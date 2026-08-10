"""Render and control the systemd user units for one packed target.

Both dwarfs and the overlay are plain oneshot units, started/stopped
explicitly by user_service.py, rather than kernel autofs `.automount`
units - unprivileged `systemctl --user` sessions can't set up autofs
(`CAP_SYS_ADMIN` required), so a `.automount` here fails with "Operation
not permitted" regardless of what's nested under it.

dwarfs is a oneshot `.service` (ExecStart runs dwarfs directly, ExecStop
runs umount); the overlay is a `.mount` unit that Requires=/After= it.
The dwarfs unit sets StopWhenUnneeded=yes so it tears down right behind
the overlay once nothing needs it any more.

Every generated unit carries an `X-Dfsmount-Owner=<launcher>/<target>`
marker so stale units (for launchers/targets no longer configured) can be
found and purged without touching unit files dfsmount didn't create.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import cache
from pathlib import Path

from .binaries import dwarfs_executable
from .models import TargetPaths, TargetUnits

OWNER_KEY = "X-Dfsmount-Owner"
UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


def owner_tag(launcher: str, target: str) -> str:
    return f"{launcher}/{target}"


@cache
def escape_path(path: Path) -> str:
    result = subprocess.run(
        ["systemd-escape", "--path", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def unit_names(paths: TargetPaths) -> TargetUnits:
    return TargetUnits(
        dwarfs_service=f"{escape_path(paths.ro_mount)}.service",
        overlay_mount=f"{escape_path(paths.mount_dir)}.mount",
    )


def _dwarfs_mount_command(paths: TargetPaths, pack_path: Path) -> str:
    dwarfs = dwarfs_executable("dwarfs")
    return (
        f"{dwarfs} -o workers={os.cpu_count() or 1} -o block_allocator=mmap "
        f"-o readahead=512K {pack_path} {paths.ro_mount}"
    )


def _dwarfs_service_unit(paths: TargetPaths, pack_path: Path, owner: str) -> str:
    umount = shutil.which("umount") or "umount"
    return (
        "[Unit]\n"
        f"Description=dfsmount dwarfs mount for {paths.target}\n"
        f"{OWNER_KEY}={owner}\n"
        "StopWhenUnneeded=yes\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"ExecStart={_dwarfs_mount_command(paths, pack_path)}\n"
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


def render(paths: TargetPaths, pack_path: Path, launcher: str) -> dict[str, str]:
    """unit filename -> file content, for both units of one target."""
    names = unit_names(paths)
    owner = owner_tag(launcher, paths.target)
    return {
        names.dwarfs_service: _dwarfs_service_unit(paths, pack_path, owner),
        names.overlay_mount: _overlay_mount_unit(paths, names.dwarfs_service, owner),
    }


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=False)


def daemon_reload() -> None:
    _systemctl("daemon-reload")


def write_units(units: dict[str, str]) -> None:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in units.items():
        path = UNIT_DIR / name
        if path.exists() and path.read_text() == content:
            continue
        path.write_text(content)


def remove_units(names: list[str]) -> None:
    """Stop and delete; no [Install] section exists so `disable` is a no-op."""
    if not names:
        return
    _systemctl("stop", *names)
    for name in names:
        (UNIT_DIR / name).unlink(missing_ok=True)


def owned_units() -> dict[str, str]:
    """unit filename -> owner tag, for every unit carrying our marker."""
    marker = f"{OWNER_KEY}="
    owned = {}
    if not UNIT_DIR.is_dir():
        return owned
    for path in UNIT_DIR.iterdir():
        if path.suffix not in (".service", ".mount"):
            continue
        for line in path.read_text().splitlines():
            if line.startswith(marker):
                owned[path.name] = line.removeprefix(marker)
                break
    return owned


def start_units(names: list[str]) -> None:
    if names:
        _systemctl("start", *names)


def stop_units(names: list[str]) -> None:
    if names:
        _systemctl("stop", *names)


def enable_service(name: str) -> None:
    _systemctl("enable", "--now", name)


def disable_service(name: str) -> None:
    _systemctl("disable", "--now", name)
