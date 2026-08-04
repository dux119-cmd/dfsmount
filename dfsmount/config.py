"""Executable checks and the YAML service configuration.

Config lives at ~/.config/dfsmount.yaml for the user who owns the games
(override with -c/--config). Paths inside it may use "~" or be given
relative to that user's home (e.g. "Games" -> "~/Games").
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from .privsep import UserCreds, lookup_user


def require_executable(name: str) -> None:
    if shutil.which(name) is None:
        raise FileNotFoundError(f"required executable not found on PATH: {name}")


def _invoking_home() -> Path:
    """Home directory of the human behind this process.

    Under plain `dfsmount ...` this is just the current user. Under
    `sudo dfsmount service`, sudo commonly resets $HOME to root's, so prefer
    $SUDO_USER's home when we're root and it's set.
    """
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and os.geteuid() == 0:
        try:
            return Path(lookup_user(sudo_user).home)
        except SystemExit:
            pass
    return Path.home()


def default_config_path() -> Path:
    return _invoking_home() / ".config" / "dfsmount.yaml"


def resolve_user_path(raw: str, home: Path) -> Path:
    """Expand "~" and resolve bare-relative paths against `home`.

    Expands "~" against `home` explicitly rather than via Path.expanduser(),
    since expanduser() uses this process's own home (root, when running as
    the service) - not necessarily the target user's.
    """
    if raw == "~" or raw.startswith("~/"):
        raw = raw.replace("~", str(home), 1)
    path = Path(raw)
    if not path.is_absolute():
        path = home / path
    return path


@dataclass(frozen=True)
class ProcessConfig:
    name: str  # process name to watch for, matched against /proc/<pid>/comm
    archives_dir: Path  # holds "<target>-rev<N>.dfs" files, one or more targets
    working_dir: Path  # per-target ro/upper/work dirs live under here
    target_mount_dir: Path  # each target is mounted at target_mount_dir/<target>


@dataclass(frozen=True)
class ServiceConfig:
    poll_interval: float
    processes: tuple[ProcessConfig, ...]
    run_as: str | None  # username that owns mounts/archives; required for `service`


def load_config(path: Path | None = None) -> ServiceConfig:
    path = path or default_config_path()
    raw = yaml.safe_load(path.read_text()) or {}

    run_as = raw.get("run_as")
    home = Path(lookup_user(run_as).home) if run_as else _invoking_home()

    processes = tuple(
        ProcessConfig(
            name=entry["name"],
            archives_dir=resolve_user_path(entry["archives_dir"], home),
            working_dir=resolve_user_path(entry["working_dir"], home),
            target_mount_dir=resolve_user_path(entry["target_mount_dir"], home),
        )
        for entry in raw.get("processes", [])
    )
    return ServiceConfig(
        poll_interval=float(raw.get("poll_interval", 2)),
        processes=processes,
        run_as=run_as,
    )


def find_process(config: ServiceConfig, name: str) -> ProcessConfig:
    for proc in config.processes:
        if proc.name == name:
            return proc
    raise SystemExit(f"no process named {name!r} in config")


def resolve_run_as(config: ServiceConfig, override: str | None) -> UserCreds:
    """The user whose credentials mounts/archives/creates should run under.

    Priority: --user flag > config's run_as > $SUDO_USER (sudo dfsmount ...).
    """
    name = override or config.run_as or os.environ.get("SUDO_USER")
    if not name:
        raise SystemExit(
            "dfsmount: no target user - set run_as in config.yaml, "
            "pass -u/--user, or run via `sudo dfsmount ...`"
        )
    return lookup_user(name)
