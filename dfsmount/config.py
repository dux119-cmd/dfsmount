"""Executable checks and the YAML service configuration.

Config lives at ~/.config/dfsmount.yaml for the invoking user (override with
-c/--config). Paths inside it may use "~" or be given relative to that
user's home (e.g. "Games" -> "~/Games"). Everything - CLI and service -
always acts as whoever invokes it; there's no privilege split.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, fields
from functools import cache
from pathlib import Path

import yaml


@cache
def require_executable(name: str) -> None:
    """Raise if `name` isn't on PATH. Cached: PATH is stable for a run, and this
    is called on every mount/unmount/archive-create - shutil.which() re-scans PATH
    each time otherwise. Failures aren't cached (lru_cache only caches successful
    returns), so a missing executable is re-checked, not permanently poisoned."""
    if shutil.which(name) is None:
        raise FileNotFoundError(f"required executable not found on PATH: {name}")


def default_config_path() -> Path:
    return Path.home() / ".config" / "dfsmount.yaml"


def ensure_config_exists(path: Path) -> None:
    """Write an empty config skeleton if `path` doesn't exist yet."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("launchers: []\n")


def resolve_user_path(raw: str, home: Path) -> Path:
    """Expand "~" and resolve bare-relative paths against `home`."""
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


def load_config(path: Path | None = None) -> ServiceConfig:
    path = path or default_config_path()
    raw = yaml.safe_load(path.read_text()) or {}
    home = Path.home()

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
    )


def find_launcher(config: ServiceConfig, name: str) -> LauncherConfig:
    for launcher in config.launchers:
        if launcher.name == name:
            return launcher
    raise SystemExit(f"no launcher named {name!r} in config")
