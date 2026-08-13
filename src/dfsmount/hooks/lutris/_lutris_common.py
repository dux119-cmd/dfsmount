"""Lutris pga.db access and config/path parsing, shared by the lutris hooks.

Not a package - each hook script adds this file's directory to sys.path
and imports it directly, since dfsmount runs hooks as standalone processes.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ARTWORK_EXTENSIONS = ("png", "jpg")

# Where portable per-game data lives inside a source/mount directory.
DFSMOUNT_DIR = ".dfsmount"
CONFIG_FILENAME = "config.yml"
ART_SUBDIR = "art"
ROOT_PLACEHOLDER = "DFSMOUNT_GAME_ROOT"

FIELD_MAP_DIR = Path(__file__).resolve().parent
DATABASE_TO_CONFIG_FIELDS_PATH = FIELD_MAP_DIR / "database-to-config-fields.yaml"
CONFIG_TO_DATABASE_FIELDS_PATH = FIELD_MAP_DIR / "config-to-database-fields.yaml"
CONFIG_EXCLUSIONS_PATH = FIELD_MAP_DIR / "config-exclusions.yaml"

# The db column both field maps treat as a game's identity - config.yml's
# equivalent field is looked up dynamically, never assumed to be named "id".
IDENTITY_COLUMN = "slug"


def load_database_to_config_fields(
    path: Path = DATABASE_TO_CONFIG_FIELDS_PATH,
) -> dict[str, str]:
    """pga.db column -> config.yml "game:" section key, for the pack hook."""
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_config_to_database_fields(
    path: Path = CONFIG_TO_DATABASE_FIELDS_PATH,
) -> dict[str, str]:
    """config.yml "game:" section key -> pga.db column, for the install hook."""
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_config_exclusions(path: Path = CONFIG_EXCLUSIONS_PATH) -> frozenset[str]:
    """Top-level config.yml keys stripped before archiving."""
    return frozenset(yaml.safe_load(path.read_text(encoding="utf-8")) or [])


def config_identity_field(
    config_to_database_fields: dict[str, str], identity_column: str = IDENTITY_COLUMN
) -> str | None:
    """The config.yml field mapped to `identity_column` (normally "slug")."""
    return next(
        (
            config_field
            for config_field, db_column in config_to_database_fields.items()
            if db_column == identity_column
        ),
        None,
    )


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


def config_path(paths: LutrisPaths, configpath: str) -> Path:
    return paths.games_config_dir / f"{configpath}.yml"


def artwork_paths(paths: LutrisPaths, slug: str) -> dict[str, Path]:
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


def dfsmount_config_path(game_dir: Path) -> Path:
    return game_dir / DFSMOUNT_DIR / CONFIG_FILENAME


def dfsmount_art_dir(game_dir: Path) -> Path:
    return game_dir / DFSMOUNT_DIR / ART_SUBDIR


def find_dfsmount_art(game_dir: Path, name: str) -> Path | None:
    art_dir = dfsmount_art_dir(game_dir)
    return next(
        (p for ext in ARTWORK_EXTENSIONS if (p := art_dir / f"{name}.{ext}").exists()),
        None,
    )


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def list_games(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row) for row in connection.execute("SELECT * FROM games ORDER BY slug")
    ]


def insert_game(connection: sqlite3.Connection, game: dict[str, Any]) -> int:
    columns = ", ".join(game.keys())
    placeholders = ", ".join("?" for _ in game)
    cursor = connection.execute(
        f"INSERT OR REPLACE INTO games ({columns}) VALUES ({placeholders})",
        tuple(game.values()),
    )
    connection.commit()
    return cursor.lastrowid


def delete_game(connection: sqlite3.Connection, slug: str) -> None:
    connection.execute("DELETE FROM games WHERE slug = ?", (slug,))
    connection.commit()


def prepare_for_insert(
    database: dict[str, Any], existing_id: int | None
) -> dict[str, Any]:
    result = {**database, "installed_at": int(time.time())}
    if existing_id is not None:
        result["id"] = existing_id
    elif "id" in result:
        del result["id"]
    return result


# --------------------------------------------------------------------------
# "game:" section round-trip: id/name/runner/platform/year live directly in
# config.yml instead of a separate game.json, so the archive stays
# self-contained.
# --------------------------------------------------------------------------


def parse_game_section(config_text: str) -> dict[str, str]:
    """Read scalar key: value pairs from the top-level "game:" section."""
    data = yaml.safe_load(config_text) or {}
    game = data.get("game") or {}
    return {key: str(value) for key, value in game.items()}


def set_game_section_fields(config_text: str, fields: dict[str, str]) -> str:
    """Add/replace scalar fields under the top-level "game:" section,
    creating the section if it doesn't exist."""
    data = yaml.safe_load(config_text) or {}
    game = dict(data.get("game") or {})
    game.update(fields)
    data["game"] = game
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


# --------------------------------------------------------------------------
# Locating a game's install directory, for pack: identifying which
# Lutris game a source_dir belongs to. Tried in order, most explicit first:
#   1. config.yml's game.exe, if absolute and containing the slug as a
#      path segment - everything up to and including that segment
#   2. the database's `directory` column, if present
#   3. config.yml's game.exe, if relative - resolved against
#      system.yml's system.game_path / slug
# --------------------------------------------------------------------------


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
    data = yaml.safe_load(config_text) or {}
    game = data.get("game") or {}
    exe = game.get("exe")
    return str(exe) if exe else None


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
    return f"{default_game_path.rstrip('/')}/{slug}" if default_game_path else None


def _read_default_game_path(paths: LutrisPaths) -> str | None:
    if not paths.system_yml_path.exists():
        return None
    data = yaml.safe_load(paths.system_yml_path.read_text(encoding="utf-8")) or {}
    game_path = data.get("game_path") or (data.get("system") or {}).get("game_path")
    return str(game_path) if game_path else None


# --------------------------------------------------------------------------
# Fields/keys dropped from a captured config.yml; game-tree paths deleted
# outright (regenerated by Lutris/Wine/Proton) rather than archived
# --------------------------------------------------------------------------

EXCLUDED_GAME_PATHS = frozenset(
    {
        "config_info",
        "lutris.json",
        "system.reg.old",
        "shadercache",
        "gstreamer-1.0",
        "drive_c/proton_shortcuts",
        f"drive_c/users/{os.getlogin()}",
    }
)
DOSDEVICES_DIR = "dosdevices"


def strip_config_keys(text: str, excluded_keys: frozenset[str] | None = None) -> str:
    """Drop top-level YAML keys listed in config-exclusions.yaml."""
    excluded = load_config_exclusions() if excluded_keys is None else excluded_keys
    data = yaml.safe_load(text) or {}
    for key in excluded:
        data.pop(key, None)
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
