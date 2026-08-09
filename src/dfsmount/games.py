"""List a launcher's known game names and mount/repack status."""

from __future__ import annotations

from pathlib import Path

from .archive import discover_targets, latest_archive
from .models import GameStatus, LauncherConfig, TargetPaths
from .mount import is_mounted


def _has_overlay_content(overlay_dir: Path) -> bool:
    return overlay_dir.is_dir() and any(overlay_dir.iterdir())


def list_games(launcher: LauncherConfig) -> list[GameStatus]:
    from_dir = (
        {e.name for e in launcher.target_mount_dir.iterdir() if e.is_dir()}
        if launcher.target_mount_dir.is_dir()
        else set()
    )
    names = sorted(from_dir | discover_targets(launcher.archives_dir))

    def repackable(name: str) -> bool:
        paths = TargetPaths.for_target(launcher, name)
        has_archive = latest_archive(paths) is not None
        return has_archive and _has_overlay_content(paths.upper)

    return [GameStatus(name=n, repackable=repackable(n)) for n in names]


def list_mounted(launcher: LauncherConfig) -> list[str]:
    if not launcher.target_mount_dir.is_dir():
        return []
    return sorted(
        e.name
        for e in launcher.target_mount_dir.iterdir()
        if e.is_dir() and is_mounted(e)
    )
