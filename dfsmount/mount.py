"""Mount dwarfs with fuse-overlayfs (writable) at the target path."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .archive import latest_archive
from .config import ProcessHooks, require_executable
from .hooks import run_hook
from .privsep import UserCreds, as_user


@dataclass(frozen=True)
class TargetPaths:
    target: str
    archives_dir: Path
    mount_dir: Path  # where the live, writable view appears
    ro_mount: Path  # dwarfs read-only FUSE mount (overlay lowerdir)
    upper: Path  # overlay upperdir
    work: Path  # overlay workdir
    hooks: ProcessHooks = field(default_factory=ProcessHooks)


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

    run_hook(paths.hooks.pre_mount, paths.mount_dir, run_as=run_as)

    with as_user(run_as):
        for directory in (
            paths.ro_mount,
            paths.upper,
            paths.work,
            paths.mount_dir,
        ):
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

    run_hook(paths.hooks.post_mount, paths.mount_dir, run_as=run_as)


def unmount(paths: TargetPaths, run_as: UserCreds | None = None) -> None:
    require_executable("umount")

    if not is_mounted(paths.mount_dir) and not is_mounted(paths.ro_mount):
        return

    run_hook(paths.hooks.pre_unmount, paths.mount_dir, run_as=run_as)

    with as_user(run_as):
        if is_mounted(paths.mount_dir):
            subprocess.run(["umount", str(paths.mount_dir)], check=True)
        if is_mounted(paths.ro_mount):
            subprocess.run(["umount", str(paths.ro_mount)], check=True)

    run_hook(paths.hooks.post_unmount, paths.mount_dir, run_as=run_as)


def reset_overlay(paths: TargetPaths, run_as: UserCreds | None = None) -> None:
    """Discard the writable layer. Must be called while unmounted."""
    with as_user(run_as):
        for directory in (paths.upper, paths.work):
            shutil.rmtree(directory, ignore_errors=True)
            directory.mkdir(parents=True, exist_ok=True)
