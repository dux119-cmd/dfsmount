"""Create a new archive revision by re-packing the currently mounted (live) directory.

Reading from `paths.mount_dir` while it's mounted transparently combines the
writable upper layer with the read-only dwarfs layer beneath it - mkdwarfs
just sees the current, merged state of the files.

After the new revision is written, the mount is torn down and the writable
overlay is cleared. If the target was already mounted before repacking
started (e.g. its launcher has it open), it's remounted fresh against the
new revision. If repack had to mount it itself just to read it, it's left
unmounted afterward - repack shouldn't leave behind a mount nobody asked for.
"""

from __future__ import annotations

from pathlib import Path

from .archive import create_archive
from .mount import TargetPaths, is_mounted, mount, reset_overlay, unmount


def repack(paths: TargetPaths) -> Path:
    was_mounted = is_mounted(paths.mount_dir)
    if not was_mounted:
        mount(paths)

    output = create_archive(
        paths.mount_dir, paths.archives_dir, paths.target, hooks=paths.hooks
    )

    unmount(paths)
    reset_overlay(paths)
    if was_mounted:
        mount(paths)

    return output
