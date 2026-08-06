"""List a launcher's game names when no source directory is given to `create`.

A game is "repackable" (can be upgraded via `dfsmount repack`) when it
already has at least one archive *and* its overlay (the writable layer
holding saves/settings/updates since that archive was written) has content.
An empty or missing overlay means there's nothing new to bake in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .archive import discover_targets, latest_archive
from .config import LauncherConfig


@dataclass(frozen=True)
class GameStatus:
    name: str
    repackable: bool


def _has_overlay_content(overlay_dir: Path) -> bool:
    return overlay_dir.is_dir() and any(overlay_dir.iterdir())


def list_games(launcher: LauncherConfig) -> list[GameStatus]:
    """Game names known for `launcher`: directories under its
    target_mount_dir, plus any targets that only exist as archives so far.
    """
    from_dir = (
        {entry.name for entry in launcher.target_mount_dir.iterdir() if entry.is_dir()}
        if launcher.target_mount_dir.is_dir()
        else set()
    )
    names = sorted(from_dir | discover_targets(launcher.archives_dir))

    def repackable(name: str) -> bool:
        has_archive = latest_archive(launcher.archives_dir, name) is not None
        overlay = launcher.working_dir / name / "upper"
        return has_archive and _has_overlay_content(overlay)

    return [GameStatus(name=n, repackable=repackable(n)) for n in names]
