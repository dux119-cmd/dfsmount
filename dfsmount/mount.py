"""Mount dwarfs (read-only) with fuse-overlayfs (writable) on top, at the target path."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .archive import latest_archive
from .config import require_executable
from .privsep import UserCreds, as_user


@dataclass(frozen=True)
class TargetPaths:
    target: str
    archives_dir: Path
    mount_dir: Path  # where the live, writable view appears
    ro_mount: Path  # dwarfs read-only FUSE mount (overlay lowerdir)
    upper: Path  # overlay upperdir
    work: Path  # overlay workdir


def is_mounted(path: Path) -> bool:
    return (
        subprocess.run(
            ["mountpoint", "-q", str(path)],
            check=False,
        ).returncode
        == 0
    )


def mount(paths: TargetPaths, run_as: UserCreds | None = None) -> None:
    require_executable("dwarfs")
    require_executable("fuse-overlayfs")

    if is_mounted(paths.mount_dir):
        return

    archive = latest_archive(paths.archives_dir, paths.target)
    if archive is None:
        raise FileNotFoundError(
            f"no archive found for target {paths.target!r} in {paths.archives_dir}"
        )

    with as_user(run_as):
        for directory in (paths.ro_mount, paths.upper, paths.work, paths.mount_dir):
            directory.mkdir(parents=True, exist_ok=True)

        if not is_mounted(paths.ro_mount):
            subprocess.run(
                [
                    "dwarfs",
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


def unmount(paths: TargetPaths, run_as: UserCreds | None = None) -> None:
    require_executable("umount")

    with as_user(run_as):
        if is_mounted(paths.mount_dir):
            subprocess.run(["umount", str(paths.mount_dir)], check=True)
        if is_mounted(paths.ro_mount):
            subprocess.run(["umount", str(paths.ro_mount)], check=True)


def reset_overlay(paths: TargetPaths, run_as: UserCreds | None = None) -> None:
    """Discard the writable layer. Must be called while unmounted."""
    with as_user(run_as):
        for directory in (paths.upper, paths.work):
            shutil.rmtree(directory, ignore_errors=True)
            directory.mkdir(parents=True, exist_ok=True)
