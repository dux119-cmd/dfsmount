"""Write a new archive revision from a target's live (mounted) state."""

from __future__ import annotations

from pathlib import Path

from .archive import create_archive
from .models import TargetPaths
from .mount import is_mounted, mount, reset_overlay, unmount


def repack(paths: TargetPaths) -> Path:
    was_mounted = is_mounted(paths.mount_dir)
    if not was_mounted:
        mount(paths)

    output = create_archive(paths, paths.mount_dir)

    unmount(paths)
    reset_overlay(paths)
    mount(paths)

    return output
