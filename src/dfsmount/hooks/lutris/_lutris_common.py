"""Lutris pga.db access and config/path parsing, shared by the lutris hooks.

Not a package - each hook script adds this file's directory to sys.path
and imports it directly, since dfsmount runs hooks as standalone processes.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ARTWORK_EXTENSIONS = ("png", "jpg")

# Where portable per-game data lives inside a source/mount directory.
DFSMOUNT_DIR = ".dfsmount"
CONFIG_FILENAME = "config.yml"
ART_SUBDIR = "art"
ROOT_PLACEHOLDER = "{{GAME_ROOT}}"

# Fields round-tripped through config.yml's top-level "game:" section so an
# archive stays self-describing without a separate game.json.
GAME_SECTION_FIELDS = ("id", "name", "runner", "platform", "year")


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
# self-contained. Line-based, not a full YAML parse, so hooks stay
# dependency-free (they run under the system python3, not necessarily one
# with PyYAML installed).
# --------------------------------------------------------------------------


def _quote_if_needed(value: str) -> str:
    if value == "" or any(c in value for c in ":#{}[]&*!|>'\"%@`"):
        return "'" + value.replace("'", "''") + "'"
    return value


def parse_game_section(config_text: str) -> dict[str, str]:
    """Read scalar key: value pairs from the top-level "game:" section."""
    result: dict[str, str] = {}
    in_game = False
    for line in config_text.splitlines():
        if line and not line[0].isspace():
            in_game = line.rstrip().rstrip(":") == "game"
            continue
        if in_game and ":" in line:
            key, _, value = line.strip().partition(":")
            result[key.strip()] = value.strip().strip("'\"")
    return result


def set_game_section_fields(config_text: str, fields: dict[str, str]) -> str:
    """Add/replace scalar fields under the top-level "game:" section,
    creating the section if it doesn't exist."""
    remaining = dict(fields)
    out: list[str] = []
    in_game = False
    found_game = False

    def flush() -> None:
        for key, value in remaining.items():
            out.append(f"  {key}: {_quote_if_needed(value)}\n")
        remaining.clear()

    for line in config_text.splitlines(keepends=True):
        if line.strip() and not line[0].isspace():
            if in_game:
                flush()
            in_game = line.rstrip().rstrip(":") == "game"
            found_game = found_game or in_game
            out.append(line)
            continue
        if in_game and ":" in line:
            key = line.strip().split(":", 1)[0]
            if key in remaining:
                out.append(f"  {key}: {_quote_if_needed(remaining.pop(key))}\n")
                continue
        out.append(line)

    if in_game:
        flush()
    if not found_game:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append("game:\n")
        for key, value in fields.items():
            out.append(f"  {key}: {_quote_if_needed(value)}\n")
    return "".join(out)


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
    return f"{default_game_path.rstrip('/')}/{slug}" if default_game_path else None


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
# Fields/keys dropped from a captured config.yml; game-tree paths deleted
# outright (regenerated by Lutris/Wine/Proton) rather than archived
# --------------------------------------------------------------------------

EXCLUDED_CONFIG_KEYS = frozenset(
    {"game_slug", "name", "script", "service", "service_id", "slug"}
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
    """Drop specified top-level YAML keys and their indented child lines."""
    result: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip()
        if stripped and not stripped[0].isspace():
            skipping = stripped.split(":", 1)[0] in EXCLUDED_CONFIG_KEYS
        if not skipping:
            result.append(line)
    return "".join(result)
