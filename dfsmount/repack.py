"""Create a new archive revision by re-packing the currently mounted (live) directory.

Reading from `paths.mount_dir` while it's mounted transparently combines the
writable upper layer with the read-only dwarfs layer beneath it - mkdwarfs
just sees the current, merged state of the files.

After the new revision is written, the mount is torn down, the writable
overlay is cleared, and the target is remounted against the new revision -
so callers immediately get a fresh overlay on top of the new archive.
"""

from __future__ import annotations

from pathlib import Path

from .archive import create_archive
from .mount import TargetPaths, is_mounted, mount, reset_overlay, unmount
from .privsep import UserCreds


def repack(paths: TargetPaths, run_as: UserCreds | None = None) -> Path:
    if not is_mounted(paths.mount_dir):
        raise RuntimeError(f"{paths.mount_dir} is not mounted; nothing to repack")

    output = create_archive(paths.mount_dir, paths.archives_dir, paths.target, run_as)

    unmount(paths, run_as)
    reset_overlay(paths, run_as)
    mount(paths, run_as)

    return output
