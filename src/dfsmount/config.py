"""Executable checks and YAML service config loading."""

from __future__ import annotations

import shutil
from dataclasses import fields
from functools import cache
from pathlib import Path

import yaml

from .models import LauncherConfig, LauncherHooks, ServiceConfig


@cache
def require_executable(name: str) -> None:
    if shutil.which(name) is None:
        raise FileNotFoundError(f"required executable not found on PATH: {name}")


def default_config_path() -> Path:
    return Path.home() / ".config" / "dfsmount.yaml"


def ensure_config_exists(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("launchers: []\n")


def resolve_user_path(raw: str, home: Path) -> Path:
    if raw == "~" or raw.startswith("~/"):
        raw = raw.replace("~", str(home), 1)
    path = Path(raw)
    return path if path.is_absolute() else home / path


def _parse_hooks(raw: dict) -> LauncherHooks:
    names = (f.name for f in fields(LauncherHooks))
    return LauncherHooks(**{name: raw.get(name) for name in names})


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
