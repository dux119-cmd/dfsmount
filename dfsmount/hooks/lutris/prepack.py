#!/usr/bin/env python3
"""dfsmount pre_archive hook: capture a game's Lutris metadata, in place.

Run by dfsmount as `builtin:hooks/lutris/prepack.py <source_dir>` right
before source_dir is archived. It:

1. Identifies which Lutris game lives at source_dir (matching pga.db rows
   by their resolved install directory - dfsmount only gives us a path,
   not a slug).
2. Writes that game's db row, config, and artwork into a `lutris/` subdir
   inside source_dir, with the install path swapped for a portable
   placeholder - install.py reads this back after a later mount.
3. Deletes the game-tree paths Lutris/Wine/Proton regenerate on their own
   (shader caches, stale registry backups, extra dosdevices symlinks,
   ...), so they're never archived in the first place.

Exits 0 whether or not a matching Lutris game was found - a directory this
launcher archives isn't guaranteed to be a Lutris game, and dfsmount's own
create step shouldn't be blocked by that. It logs to stderr either way.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lutris_common import (  # noqa: E402
    DOSDEVICES_DIR,
    EXCLUDED_DATABASE_KEYS,
    EXCLUDED_GAME_PATHS,
    GAME_ROOT_PLACEHOLDER,
    LutrisPaths,
    artwork_paths,
    connect,
    find_artwork,
    find_game_root,
    list_games,
    strip_config_keys,
    strip_paths,
)


def find_slug_for_directory(paths: LutrisPaths, source_dir: Path) -> str | None:
    """The slug of whichever pga.db game resolves to source_dir, if any."""
    with connect(paths.db_path) as connection:
        games = list_games(connection)

    for game in games:
        config_path = paths.games_config_dir / f"{game['configpath']}.yml"
        if not config_path.exists():
            continue
        config_text = config_path.read_text(encoding="utf-8")
        game_root = find_game_root(
            paths, config_text, game["slug"], game.get("directory")
        )
        if game_root is not None and game_root.resolve() == source_dir:
            return game["slug"]
    return None


def capture_metadata(paths: LutrisPaths, slug: str, source_dir: Path) -> Path:
    with connect(paths.db_path) as connection:
        game_row = connection.execute(
            "SELECT * FROM games WHERE slug = ?", (slug,)
        ).fetchone()
    game_row = dict(game_row)

    # Strip the installed_at numeric suffix from configpath, if present.
    installed_at = game_row.get("installed_at")
    configpath = game_row.get("configpath", "")
    if installed_at and configpath:
        dash_stamp = f"-{installed_at}"
        if configpath.endswith(dash_stamp):
            game_row["configpath"] = configpath[: -len(dash_stamp)]

    for key in EXCLUDED_DATABASE_KEYS:
        game_row.pop(key, None)

    config_path = paths.games_config_dir / f"{game_row['configpath']}.yml"
    config_text = config_path.read_text(encoding="utf-8")
    stripped_config = strip_config_keys(
        config_text.replace(str(source_dir), GAME_ROOT_PLACEHOLDER)
    )

    lutris_dir = source_dir / "lutris"
    lutris_dir.mkdir(exist_ok=True)
    (lutris_dir / "database.json").write_text(
        json.dumps(strip_paths(game_row, source_dir), indent=2), encoding="utf-8"
    )
    (lutris_dir / "config.yml").write_text(stripped_config, encoding="utf-8")

    for name, stem in artwork_paths(paths, slug).items():
        source = find_artwork(stem)
        if source is not None:
            shutil.copyfile(source, lutris_dir / f"{name}{source.suffix}")

    return lutris_dir


def prune_excluded_paths(source_dir: Path) -> None:
    for excluded in EXCLUDED_GAME_PATHS:
        _remove(source_dir / excluded)

    dosdevices = source_dir / DOSDEVICES_DIR
    if dosdevices.is_dir():
        for entry in dosdevices.iterdir():
            if entry.name and "d" <= entry.name[0] <= "z":
                _remove(entry)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: prepack.py <source_dir>", file=sys.stderr)
        return 1

    source_dir = Path(sys.argv[1]).resolve()
    paths = LutrisPaths.for_home(Path.home())

    slug = find_slug_for_directory(paths, source_dir)
    if slug is None:
        print(f"prepack: no Lutris game found for {source_dir}", file=sys.stderr)
        return 0

    lutris_dir = capture_metadata(paths, slug, source_dir)
    prune_excluded_paths(source_dir)
    print(f"prepack: captured '{slug}' Lutris metadata into {lutris_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
