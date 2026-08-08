"""Mount dwarfs with fuse-overlayfs (writable) at the target path.

Always runs as whichever user invokes it - the CLI directly, or the
unprivileged user-mode service. Neither needs root.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .archive import latest_archive
from .binaries import dwarfs_executable
from .config import LauncherHooks, require_executable


@dataclass(frozen=True)
class TargetPaths:
    target: str
    archives_dir: Path
    mount_dir: Path  # where the live, writable view appears
    ro_mount: Path  # dwarfs read-only FUSE mount (overlay lowerdir)
    upper: Path  # overlay upperdir
    work: Path  # overlay workdir
    hooks: LauncherHooks = field(default_factory=LauncherHooks)


def is_mounted(path: Path) -> bool:
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
        # Guarantee traversal/access for the invoking user regardless of
        # the process's current umask.
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
