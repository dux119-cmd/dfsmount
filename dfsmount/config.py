"""Executable checks and the YAML service configuration.

Config lives at ~/.config/dfsmount.yaml for the user who owns the games
(override with -c/--config). Paths inside it may use "~" or be given
relative to that user's home (e.g. "Games" -> "~/Games").
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, fields
from functools import cache
from pathlib import Path

import yaml

from .privsep import UserCreds, lookup_user


@cache
def require_executable(name: str) -> None:
    """Raise if `name` isn't on PATH. Cached: PATH is stable for a run, and this
    is called on every mount/unmount/archive-create - shutil.which() re-scans PATH
    each time otherwise. Failures aren't cached (lru_cache only caches successful
    returns), so a missing executable is re-checked, not permanently poisoned."""
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


HookCommands = str | list[str] | None  # one command, or several run in sequence


@dataclass(frozen=True)
class LauncherHooks:
    """Optional shell commands run around a launcher's archive/mount lifecycle.

    Each is a shell command string (parsed with shlex), or a list of them to
    run in sequence. The relevant path(s) are appended as extra arguments
    when a hook runs. A path may start with "builtin:" to reference a hook
    script bundled with dfsmount, e.g. "builtin:hooks/lutris/prepack.sh".
    """

    pre_archive: HookCommands = None  # given: source_dir
    post_archive: HookCommands = None  # given: source_dir, archive_path
    pre_mount: HookCommands = None  # given: mount_dir
    post_mount: HookCommands = None  # given: mount_dir
    pre_unmount: HookCommands = None  # given: mount_dir
    post_unmount: HookCommands = None  # given: mount_dir
    install: HookCommands = None  # given: mount_dir
    uninstall: HookCommands = None  # given: mount_dir


def _parse_hooks(raw: dict) -> LauncherHooks:
    names = (f.name for f in fields(LauncherHooks))
    return LauncherHooks(**{name: raw.get(name) for name in names})


@dataclass(frozen=True)
class LauncherConfig:
    name: str  # launcher process name to watch for, matched against /proc/<pid>/comm
    archives_dir: Path  # holds "<target>-rev<N>.dfs" files, one or more targets
    working_dir: Path  # per-target ro/upper/work dirs live under here
    target_mount_dir: Path  # each target is mounted at target_mount_dir/<target>
    hooks: LauncherHooks = LauncherHooks()


@dataclass(frozen=True)
class ServiceConfig:
    poll_interval: float
    launchers: tuple[LauncherConfig, ...]
    run_as: str | None  # username that owns mounts/archives; required for `service`


def load_config(path: Path | None = None) -> ServiceConfig:
    path = path or default_config_path()
    raw = yaml.safe_load(path.read_text()) or {}

    run_as = raw.get("run_as")
    home = Path(lookup_user(run_as).home) if run_as else _invoking_home()

    launchers = tuple(
        LauncherConfig(
            name=entry["name"],
            archives_dir=resolve_user_path(entry["archives_dir"], home),
            working_dir=resolve_user_path(entry["working_dir"], home),
            target_mount_dir=resolve_user_path(entry["target_mount_dir"], home),
            hooks=_parse_hooks(entry.get("hooks") or {}),
        )
        for entry in raw.get("launchers", [])
    )
    return ServiceConfig(
        poll_interval=float(raw.get("poll_interval", 2)),
        launchers=launchers,
        run_as=run_as,
    )


def find_launcher(config: ServiceConfig, name: str) -> LauncherConfig:
    for launcher in config.launchers:
        if launcher.name == name:
            return launcher
    raise SystemExit(f"no launcher named {name!r} in config")


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
