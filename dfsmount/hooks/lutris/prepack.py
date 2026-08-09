#!/usr/bin/env python3
"""dfsmount pre_archive hook: capture a game's Lutris metadata.

Run as `builtin:hooks/lutris/prepack.py <source_dir>` before source_dir is
archived. Writes a generic .metadata/ dir (see hooks/metadata.py) inside
source_dir and deletes game-tree paths Lutris/Wine/Proton regenerate on
their own. Exits 0 even if no matching Lutris game is found - not every
archived directory is necessarily a Lutris game.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lutris_common import (  # noqa: E402
    DOSDEVICES_DIR,
    EXCLUDED_DATABASE_KEYS,
    EXCLUDED_GAME_PATHS,
    LutrisPaths,
    artwork_paths,
    connect,
    find_artwork,
    find_game_root,
    list_games,
    strip_config_keys,
)
from metadata import GameMetadata, strip_root, write  # noqa: E402


def find_slug_for_directory(paths: LutrisPaths, source_dir: Path) -> str | None:
    with connect(paths.db_path) as connection:
        games = list_games(connection)
    for game in games:
        config_path = paths.games_config_dir / f"{game['configpath']}.yml"
        if not config_path.exists():
            continue
        config_text = config_path.read_text(encoding="utf-8")
        root = find_game_root(paths, config_text, game["slug"], game.get("directory"))
        if root is not None and root.resolve() == source_dir:
            return game["slug"]
    return None


def capture_metadata(paths: LutrisPaths, slug: str, source_dir: Path) -> Path:
    with connect(paths.db_path) as connection:
        row = dict(
            connection.execute("SELECT * FROM games WHERE slug = ?", (slug,)).fetchone()
        )

    config_path = paths.games_config_dir / f"{row['configpath']}.yml"

    # Drop the "-<installed_at>" suffix Lutris appends to configpath, so the
    # stored value is portable (a fresh install gets its own timestamp).
    installed_at, configpath = row.get("installed_at"), row.get("configpath", "")
    if installed_at and configpath.endswith(f"-{installed_at}"):
        row["configpath"] = configpath[: -len(f"-{installed_at}")]

    config_text = strip_config_keys(
        config_path.read_text(encoding="utf-8").replace(
            str(source_dir), "{{GAME_ROOT}}"
        )
    )

    name = row.pop("name", slug)
    runner = row.pop("runner", None)
    for key in EXCLUDED_DATABASE_KEYS | {"slug"}:
        row.pop(key, None)

    art_files = {
        name: source
        for name, stem in artwork_paths(paths, slug).items()
        if (source := find_artwork(stem)) is not None
    }

    meta = GameMetadata(
        launcher="lutris",
        id=slug,
        name=name,
        runner=runner,
        config_ext="yml",
        config_text=config_text,
        extra=strip_root(row, source_dir),
        art={name: source.suffix.lstrip(".") for name, source in art_files.items()},
    )
    return write(source_dir, meta, art_files)


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

    metadata_dir = capture_metadata(paths, slug, source_dir)
    prune_excluded_paths(source_dir)
    print(f"prepack: captured '{slug}' Lutris metadata into {metadata_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
