"""Render systemd user units for one archived target.

Neither unit is a kernel autofs mount. `mount(2)` on an `autofs`
filesystem requires `CAP_SYS_ADMIN` in the initial user namespace - autofs
isn't among the filesystem types the kernel allows an unprivileged user
namespace to mount (unlike fuse, which is why the dwarfs/fuse-overlayfs
mounts below work unprivileged). A plain `systemctl --user` session is
always unprivileged, so a `.automount` unit here fails every time with
"Operation not permitted" setting up the autofs superblock - regardless of
how many mounts are nested under it.

So both dwarfs and the overlay are started/stopped explicitly instead of
being triggered on first access: dwarfs is a oneshot `.service`
(ExecStart runs the dwarfs binary directly, the same invocation
`mount.py` uses for manual mounts; ExecStop runs `umount`), and the
overlay is a `.mount` unit that `Requires=`/`After=` it. `user_service.py`
starts the overlay `.mount` while a target's launcher is running and stops
it when the launcher exits.

Stopping the overlay `.mount` does not, by itself, stop the dwarfs
`.service` it Requires= - that dependency only propagates the other way
(dwarfs stopping takes the overlay down with it, not vice versa). The
dwarfs unit sets `StopWhenUnneeded=yes` (a [Unit]-section directive) so
that once nothing requires it any more - i.e. right after the overlay
unmounts - systemd stops it too, instead of leaving the read-only dwarfs
mount behind.

Both unit files also carry an `X-Dfsmount-Owner=` marker (an
`X-`-prefixed key, which systemd always ignores) recording the
`<launcher>/<target>` that unit belongs to, so `user_service.py` can find
and purge stale units it created for launchers/targets that no longer
exist, without touching unit files it doesn't own.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .binaries import dwarfs_executable
from .mount import TargetPaths

OWNER_KEY = "X-Dfsmount-Owner"


def owner_tag(launcher: str, target: str) -> str:
    return f"{launcher}/{target}"


@cache
def escape_path(path: Path) -> str:
    """systemd-escape --path: the mandatory unit-name form of a mount path.

    Cached: a target's ro_mount/mount_dir paths are fixed for the process's
    lifetime, but escape_path is called on every poll for every tracked
    target (via unit_names) - caching avoids re-forking systemd-escape for
    a result that can't change.
    """
    result = subprocess.run(
        ["systemd-escape", "--path", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class TargetUnits:
    dwarfs_service: str
    overlay_mount: str

    def all_names(self) -> list[str]:
        """Mount before service, so `remove_units` can stop cleanly."""
        return [self.overlay_mount, self.dwarfs_service]


def unit_names(paths: TargetPaths) -> TargetUnits:
    dwarfs_base = escape_path(paths.ro_mount)
    overlay_base = escape_path(paths.mount_dir)
    return TargetUnits(
        dwarfs_service=f"{dwarfs_base}.service",
        overlay_mount=f"{overlay_base}.mount",
    )


def _dwarfs_mount_command(paths: TargetPaths, archive: Path) -> str:
    """Same dwarfs invocation as mount.py's manual mount, as a single
    command line for ExecStart= - dwarfs forks to background once mounted,
    so this returns (and the oneshot service becomes active) right when
    the mount is actually ready."""
    dwarfs = dwarfs_executable("dwarfs")
    return (
        f"{dwarfs} -o uid={os.getuid()} -o gid={os.getgid()} "
        f"-o workers={os.cpu_count() or 1} -o block_allocator=mmap "
        f"-o cachesize=2048M -o readahead=512K {archive} {paths.ro_mount}"
    )


def _dwarfs_service_unit(paths: TargetPaths, archive: Path, owner: str) -> str:
    umount = shutil.which("umount") or "umount"
    return (
        "[Unit]\n"
        f"Description=dfsmount dwarfs mount for {paths.target}\n"
        f"{OWNER_KEY}={owner}\n"
        "StopWhenUnneeded=yes\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"ExecStart={_dwarfs_mount_command(paths, archive)}\n"
        f"ExecStop={umount} {paths.ro_mount}\n"
    )


def _overlay_mount_unit(paths: TargetPaths, dwarfs_service: str, owner: str) -> str:
    return (
        "[Unit]\n"
        f"Description=dfsmount overlay mount for {paths.target}\n"
        f"{OWNER_KEY}={owner}\n"
        f"Requires={dwarfs_service}\n"
        f"After={dwarfs_service}\n\n"
        "[Mount]\n"
        "What=fuse-overlayfs\n"
        f"Where={paths.mount_dir}\n"
        "Type=fuse\n"
        f"Options=lowerdir={paths.ro_mount},upperdir={paths.upper},"
        f"workdir={paths.work}\n"
    )


def render(paths: TargetPaths, archive: Path, launcher: str) -> dict[str, str]:
    """unit filename -> file content, for both units of one target."""
    names = unit_names(paths)
    owner = owner_tag(launcher, paths.target)
    return {
        names.dwarfs_service: _dwarfs_service_unit(paths, archive, owner),
        names.overlay_mount: _overlay_mount_unit(paths, names.dwarfs_service, owner),
    }
