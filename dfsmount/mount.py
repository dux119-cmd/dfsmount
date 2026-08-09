"""Mount dwarfs with a fuse-overlayfs writable layer on top."""

from __future__ import annotations

import os
import shutil
import subprocess

from .archive import latest_archive
from .binaries import dwarfs_executable
from .config import require_executable
from .models import TargetPaths


def is_mounted(path) -> bool:
    return os.path.ismount(path)


def mount(paths: TargetPaths) -> None:
    dwarfs = dwarfs_executable("dwarfs")
    require_executable("fuse-overlayfs")

    if is_mounted(paths.mount_dir):
        return

    archive = latest_archive(paths.archives_dir, paths.target)
    if archive is None:
        raise FileNotFoundError(
            f"no archive found for target {paths.target!r} in {paths.archives_dir}"
        )

    for directory in (paths.ro_mount, paths.upper, paths.work, paths.mount_dir):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o755)

    if not is_mounted(paths.ro_mount):
        subprocess.run(
            [
                dwarfs,
                "-o",
                f"uid={os.getuid()}",
                "-o",
                f"gid={os.getgid()}",
                "-o",
                f"workers={os.cpu_count() or 1}",
                "-o",
                "block_allocator=mmap",
                "-o",
                "cachesize=2048M",
                "-o",
                "readahead=512K",
                str(archive),
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

    if not is_mounted(paths.mount_dir) and not is_mounted(paths.ro_mount):
        return

    if is_mounted(paths.mount_dir):
        subprocess.run(["umount", str(paths.mount_dir)], check=True)
    if is_mounted(paths.ro_mount):
        subprocess.run(["umount", str(paths.ro_mount)], check=True)


def reset_overlay(paths: TargetPaths) -> None:
    """Discard the writable layer. Must be called while unmounted."""
    for directory in (paths.upper, paths.work):
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)
