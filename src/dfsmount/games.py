"""List a launcher's known game names and mount/repack status."""

from __future__ import annotations

from pathlib import Path

from .models import GameStatus, LauncherConfig, TargetPaths
from .mount import has_overlay_content, is_mounted
from .pack import discover_targets, latest_pack


def list_games(launcher: LauncherConfig) -> list[GameStatus]:
    from_dir = (
        {e.name for e in launcher.target_mount_dir.iterdir() if e.is_dir()}
        if launcher.target_mount_dir.is_dir()
        else set()
    )
    names = sorted(from_dir | discover_targets(launcher.archives_dir))

    def repackable(name: str) -> bool:
        paths = TargetPaths.for_target(launcher, name)
        has_pack = latest_pack(paths) is not None
        return has_pack and has_overlay_content(paths.upper)

    return [GameStatus(name=n, repackable=repackable(n)) for n in names]


def list_repackable(launcher: LauncherConfig) -> list[str]:
    return sorted(game.name for game in list_games(launcher) if game.repackable)


def list_mountable(launcher: LauncherConfig) -> list[str]:
    """Targets with a pack available to mount (reuses `mount`'s own listing)."""
    return sorted(discover_targets(launcher.archives_dir))


def list_packable(launcher: LauncherConfig) -> list[str]:
    """Game dirs eligible for `pack`: non-empty, no overlay, and not -archived"""
    if not launcher.target_mount_dir.is_dir():
        return []

    def packable(entry: Path) -> bool:
        if not entry.is_dir() or entry.name.endswith("-archived"):
            return False
        if not any(entry.iterdir()):
            return False
        paths = TargetPaths.for_target(launcher, entry.name)
        return not has_overlay_content(paths.upper)

    return sorted(e.name for e in launcher.target_mount_dir.iterdir() if packable(e))


def list_mounted(launcher: LauncherConfig) -> list[str]:
    if not launcher.target_mount_dir.is_dir():
        return []
    return sorted(
        e.name
        for e in launcher.target_mount_dir.iterdir()
        if e.is_dir() and is_mounted(e)
    )
