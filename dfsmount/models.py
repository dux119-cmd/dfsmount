"""Dataclasses shared across dfsmount modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

HookCommands = str | list[str] | None


@dataclass(frozen=True)
class LauncherHooks:
    pre_archive: HookCommands = None  # given: source_dir
    post_archive: HookCommands = None  # given: source_dir, archive_path
    install: HookCommands = None  # given: mount_dir
    uninstall: HookCommands = None  # given: mount_dir


@dataclass(frozen=True)
class LauncherConfig:
    name: str  # matched against /proc/<pid>/comm
    archives_dir: Path
    working_dir: Path
    target_mount_dir: Path
    hooks: LauncherHooks = field(default_factory=LauncherHooks)


@dataclass(frozen=True)
class ServiceConfig:
    poll_interval: float
    launchers: tuple[LauncherConfig, ...]


@dataclass(frozen=True)
class TargetPaths:
    target: str
    archives_dir: Path
    mount_dir: Path  # live, writable view
    ro_mount: Path  # dwarfs read-only mount (overlay lowerdir)
    upper: Path  # overlay upperdir
    work: Path  # overlay workdir
    hooks: LauncherHooks = field(default_factory=LauncherHooks)


@dataclass(frozen=True)
class GameStatus:
    name: str
    repackable: bool


@dataclass(frozen=True)
class TargetUnits:
    dwarfs_service: str
    overlay_mount: str

    def all_names(self) -> list[str]:
        return [self.overlay_mount, self.dwarfs_service]
