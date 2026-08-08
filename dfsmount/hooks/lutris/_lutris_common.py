"""Lutris pga.db access, config parsing, and path-portability helpers.

Shared by prepack.py (captures a game's Lutris metadata before it's
archived) and install.py (restores that metadata after a mount). Not a
package - each hook script adds this file's directory to sys.path and
imports it directly, since dfsmount runs hook scripts as standalone
subprocesses.

Ported from lutris-porter.py, minus everything tarball/zstd-specific:
dfsmount's own archive.py already makes the archive, so there's no export
format to write or read here - just the Lutris side of things.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LutrisHookError(Exception):
    """Raised for expected failures; callers print these and exit 1."""


# --------------------------------------------------------------------------
# Lutris on-disk layout
# --------------------------------------------------------------------------

GAME_ROOT_PLACEHOLDER = "{{LUTRIS_GAME_ROOT}}"
ARTWORK_EXTENSIONS = ("png", "jpg")


@dataclass(frozen=True)
class LutrisPaths:
    db_path: Path
    games_config_dir: Path
    banners_dir: Path
    coverart_dir: Path
    icons_dir: Path
    system_yml_path: Path

    @staticmethod
    def for_home(home: Path) -> LutrisPaths:
        lutris_dir = home / ".local/share/lutris"
        return LutrisPaths(
            db_path=lutris_dir / "pga.db",
            games_config_dir=lutris_dir / "games",
            banners_dir=lutris_dir / "banners",
            coverart_dir=lutris_dir / "coverart",
            icons_dir=home / ".local/share/icons/hicolor/128x128/apps",
            system_yml_path=lutris_dir / "system.yml",
        )


def artwork_paths(paths: LutrisPaths, slug: str) -> dict[str, Path]:
    """Map each artwork name to its on-disk path stem (no extension).

    There are exactly three artwork files, each named after the slug; the
    actual file is whichever of stem.png / stem.jpg exists.
    """
    return {
        "banner": paths.banners_dir / slug,
        "coverart": paths.coverart_dir / slug,
        "logo": paths.icons_dir / f"lutris_{slug}",
    }


def find_artwork(stem: Path) -> Path | None:
    return next(
        (p for ext in ARTWORK_EXTENSIONS if (p := Path(f"{stem}.{ext}")).exists()),
        None,
    )


# --------------------------------------------------------------------------
# pga.db access -- plain dicts via sqlite3.Row, no ORM
# --------------------------------------------------------------------------


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def list_games(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute("SELECT * FROM games ORDER BY slug").fetchall()
    return [dict(row) for row in rows]


def find_game_by_slug(
    connection: sqlite3.Connection, slug: str
) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM games WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def insert_game(connection: sqlite3.Connection, game: dict[str, Any]) -> int:
    columns = ", ".join(game.keys())
    placeholders = ", ".join("?" for _ in game)
    cursor = connection.execute(
        f"INSERT OR REPLACE INTO games ({columns}) VALUES ({placeholders})",
        tuple(game.values()),
    )
    connection.commit()
    return cursor.lastrowid


# --------------------------------------------------------------------------
# Path portability -- swap the game's real install path for a placeholder
# --------------------------------------------------------------------------


def map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    """Recursively rebuild value, applying transform to every string leaf."""
    if isinstance(value, dict):
        return {key: map_strings(item, transform) for key, item in value.items()}
    if isinstance(value, list):
        return [map_strings(item, transform) for item in value]
    if isinstance(value, str):
        return transform(value)
    return value


def strip_game_root(path: str, game_root: Path) -> str:
    root = str(game_root)
    if not path.startswith(root):
        return path
    remainder = path[len(root) :].lstrip("/")
    return (
        f"{GAME_ROOT_PLACEHOLDER}/{remainder}" if remainder else GAME_ROOT_PLACEHOLDER
    )


def restore_game_root(path: str, new_root: str) -> str:
    return (
        path.replace(GAME_ROOT_PLACEHOLDER, new_root)
        if GAME_ROOT_PLACEHOLDER in path
        else path
    )


def strip_paths(data: Any, game_root: Path) -> Any:
    return map_strings(data, lambda value: strip_game_root(value, game_root))


def restore_paths(data: Any, new_root: str) -> Any:
    return map_strings(data, lambda value: restore_game_root(value, new_root))


# --------------------------------------------------------------------------
# Locating a game's install directory (for prepack: identifying which
# Lutris game a source_dir belongs to)
# --------------------------------------------------------------------------
# Tried in order, most explicit first:
#   1. config.yml's game.exe, if absolute and containing the slug as a
#      path segment -- everything up to and including that segment
#   2. the database's `directory` column, if present
#   3. config.yml's game.exe, if relative -- Lutris installed under its
#      default games directory (system.yml's system.game_path) / slug


def find_game_root(
    paths: LutrisPaths, config_text: str, slug: str, fallback_directory: str | None
) -> Path | None:
    root = (
        _root_from_absolute_exe(config_text, slug)
        or fallback_directory
        or _root_from_default_game_path(paths, config_text, slug)
    )
    return Path(root) if root else None


def _exe_path(config_text: str) -> str | None:
    for line in config_text.splitlines():
        if "exe:" in line:
            _, _, value = line.partition("exe:")
            value = value.strip().strip("'\"")
            if value:
                return value
    return None


def _root_from_absolute_exe(config_text: str, slug: str) -> str | None:
    exe = _exe_path(config_text)
    if not exe or not exe.startswith("/"):
        return None
    segments = exe.split("/")
    if slug not in segments:
        return None
    return "/".join(segments[: segments.index(slug) + 1])


def _root_from_default_game_path(
    paths: LutrisPaths, config_text: str, slug: str
) -> str | None:
    exe = _exe_path(config_text)
    if not exe or exe.startswith("/"):
        return None
    default_game_path = _read_default_game_path(paths)
    if not default_game_path:
        return None
    return f"{default_game_path.rstrip('/')}/{slug}"


def _read_default_game_path(paths: LutrisPaths) -> str | None:
    if not paths.system_yml_path.exists():
        return None
    for line in paths.system_yml_path.read_text(encoding="utf-8").splitlines():
        if "game_path:" in line:
            _, _, value = line.partition("game_path:")
            value = value.strip().strip("'\"")
            if value:
                return value
    return None


# --------------------------------------------------------------------------
# Fields/keys dropped from the captured metadata, and game-tree paths
# deleted outright rather than archived
# --------------------------------------------------------------------------

EXCLUDED_DATABASE_KEYS = frozenset(
    {
        "id",
        "sortname",
        "installer_slug",
        "parent_slug",
        "executable",
        "lastplayed",
        "playtime",
        "installed_at",
        "has_custom_banner",
        "has_custom_icon",
        "has_custom_coverart_big",
        "service",
        "service_id",
        "discord_id",
    }
)
EXCLUDED_CONFIG_KEYS = frozenset(
    {
        "game_slug",
        "name",
        "script",
        "service",
        "service_id",
        "slug",
    }
)
EXCLUDED_GAME_PATHS = frozenset(
    {
        "config_info",
        "lutris.json",
        "system.reg.old",
        "shadercache",
        "gstreamer-1.0",
        "drive_c/proton_shortcuts",
    }
)
DOSDEVICES_DIR = "dosdevices"


def strip_config_keys(text: str) -> str:
    """Remove specified top-level YAML keys and their indented child lines."""
    result: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip()
        if stripped and not stripped[0].isspace():
            key = stripped.split(":", 1)[0]
            skipping = key in EXCLUDED_CONFIG_KEYS
        if not skipping:
            result.append(line)
    return "".join(result)


def prepare_for_insert(
    database: dict[str, Any], existing_id: int | None
) -> dict[str, Any]:
    """Reset play stats and timestamps; reuse the existing DB id if any."""
    result = {**database, "installed_at": int(time.time())}
    if existing_id is not None:
        result["id"] = existing_id
    elif "id" in result:
        del result["id"]
    return result
