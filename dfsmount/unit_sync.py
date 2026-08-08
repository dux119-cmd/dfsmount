"""Filesystem + systemctl side of managing dfsmount's generated unit files.

Pure mechanics only: writing/deleting files under the user's systemd unit
directory, and telling systemd about it. Deciding *which* units should exist
and be enabled lives in user_service.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .systemd_units import OWNER_KEY

UNIT_DIR = Path.home() / ".config" / "systemd" / "user"


def _systemctl(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=False)


def daemon_reload() -> None:
    _systemctl("daemon-reload")


def write_units(units: dict[str, str]) -> None:
    """Write each unit file, skipping ones already at the target content."""
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name, content in units.items():
        path = UNIT_DIR / name
        if path.exists() and path.read_text() == content:
            continue
        path.write_text(content)


def remove_units(names: list[str]) -> None:
    """Stop and delete unit files entirely (archive was removed).

    Only `stop` - our units carry no [Install] section (nothing is ever
    `enable`d), so calling `disable` on them is a no-op that just makes
    systemctl print "not meant to be enabled/disabled" noise."""
    if not names:
        return
    _systemctl("stop", *names)
    for name in names:
        (UNIT_DIR / name).unlink(missing_ok=True)


def owned_units() -> dict[str, str]:
    """unit filename -> owner tag, for every unit file carrying dfsmount's
    `X-Dfsmount-Owner=` marker. Units without the marker (not ours) are
    skipped, so this never touches unit files dfsmount didn't create."""
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
    if not names:
        return
    _systemctl("start", *names)


def stop_units(names: list[str]) -> None:
    """Unmount without deleting the unit files (launcher not running)."""
    if not names:
        return
    _systemctl("stop", *names)
