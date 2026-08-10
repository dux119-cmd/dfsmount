"""Mount dwarfs with a fuse-overlayfs writable layer on top."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .binaries import dwarfs_executable
from .config import require_executable
from .models import TargetPaths
from .pack import latest_pack


def is_mounted(path: Path) -> bool:
    return os.path.ismount(path)


def has_overlay_content(overlay_dir: Path) -> bool:
    return overlay_dir.is_dir() and any(overlay_dir.iterdir())


def mount(paths: TargetPaths) -> None:
    dwarfs = dwarfs_executable("dwarfs")
    require_executable("fuse-overlayfs")

    if is_mounted(paths.mount_dir):
        return

    pack_file = latest_pack(paths)
    if pack_file is None:
        raise FileNotFoundError(
            f"no pack found for target {paths.target!r} in {paths.archives_dir}"
        )

    for directory in (paths.ro_mount, paths.upper, paths.work, paths.mount_dir):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)

    if not is_mounted(paths.ro_mount):
        subprocess.run(
            [
                dwarfs,
                "-o",
                f"workers={os.cpu_count() or 1}",
                "-o",
                "block_allocator=mmap",
                "-o",
                "readahead=512K",
                str(pack_file),
                str(paths.ro_mount),
            ],
            check=True,
        )

    subprocess.run(
        [
            "fuse-overlayfs",
            "-o",
            f"lowerdir={paths.ro_mount},upperdir={paths.upper},workdir={paths.work}",
            str(paths.mount_dir),
        ],
        check=True,
    )


def unmount(paths: TargetPaths) -> None:
    require_executable("umount")

    if is_mounted(paths.mount_dir):
        subprocess.run(["umount", str(paths.mount_dir)], check=True)
    if is_mounted(paths.ro_mount):
        subprocess.run(["umount", str(paths.ro_mount)], check=True)

    cleanup_unmounted(paths)


def cleanup_unmounted(paths: TargetPaths) -> None:
    """Purge a target's empty mount point and overlay tree, once unmounted.

    Safe to call any time a target might be unmounted (e.g. after the
    service stops its systemd units) - each check confirms the relevant
    directory is both unmounted and empty before removing it.
    """
    _remove_empty_mount_dir(paths)
    _remove_overlay_tree_if_empty(paths)


def _remove_empty_mount_dir(paths: TargetPaths) -> None:
    """Drop the now-unused mount point dir, once confirmed unmounted and empty."""
    if is_mounted(paths.mount_dir) or not paths.mount_dir.is_dir():
        return
    if any(paths.mount_dir.iterdir()):
        return
    paths.mount_dir.rmdir()


def _remove_overlay_tree_if_empty(paths: TargetPaths) -> None:
    """Drop the overlay working tree (ro/upper/work) if it has no unsaved changes."""
    overlay_root = paths.upper.parent
    if is_mounted(paths.ro_mount):
        return
    if has_overlay_content(paths.upper) or not overlay_root.is_dir():
        return
    shutil.rmtree(overlay_root, ignore_errors=True)


def reset_overlay(paths: TargetPaths) -> None:
    """Discard the writable layer. Must be called while unmounted."""
    for directory in (paths.upper, paths.work):
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)
