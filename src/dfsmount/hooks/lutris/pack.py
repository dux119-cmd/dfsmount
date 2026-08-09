#!/usr/bin/env python3
"""dfsmount pre_archive hook: capture a game's Lutris metadata.

Run as `builtin:lutris/pack.py <source_dir>` before source_dir is archived.
Writes id/name/runner/platform/year into the "game:" section of a portable
.dfsmount/config.yml (plus .dfsmount/art/), and deletes game-tree paths
Lutris/Wine/Proton regenerate on their own. Exits 0 even if no matching
Lutris game is found - not every archived directory is necessarily a
Lutris game.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lutris_common import (  # noqa: E402
    DOSDEVICES_DIR,
    EXCLUDED_GAME_PATHS,
    GAME_SECTION_FIELDS,
    ROOT_PLACEHOLDER,
    LutrisPaths,
    artwork_paths,
    config_path,
    connect,
    dfsmount_art_dir,
    dfsmount_config_path,
    find_artwork,
    find_game_root,
    list_games,
    set_game_section_fields,
    strip_config_keys,
)


def find_slug_for_directory(paths: LutrisPaths, source_dir: Path) -> str | None:
    with connect(paths.db_path) as connection:
        games = list_games(connection)
    for game in games:
        path = config_path(paths, game["configpath"])
        if not path.exists():
            continue
        config_text = path.read_text(encoding="utf-8")
        root = find_game_root(paths, config_text, game["slug"], game.get("directory"))
        if root is not None and root.resolve() == source_dir:
            return game["slug"]
    return None


def capture_config(paths: LutrisPaths, slug: str, source_dir: Path) -> Path:
    with connect(paths.db_path) as connection:
        row = dict(
            connection.execute("SELECT * FROM games WHERE slug = ?", (slug,)).fetchone()
        )

    config_text = strip_config_keys(
        config_path(paths, row["configpath"])
        .read_text(encoding="utf-8")
        .replace(str(source_dir), ROOT_PLACEHOLDER)
    )

    fields = {
        "id": slug,
        **{k: str(row.get(k) or "") for k in GAME_SECTION_FIELDS[1:]},
    }
    config_text = set_game_section_fields(config_text, fields)

    destination = dfsmount_config_path(source_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(config_text, encoding="utf-8")
    return destination


def capture_art(paths: LutrisPaths, slug: str, source_dir: Path) -> None:
    art_dir = dfsmount_art_dir(source_dir)
    for name, stem in artwork_paths(paths, slug).items():
        source = find_artwork(stem)
        if source is None:
            continue
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / f"{name}{source.suffix}").write_bytes(source.read_bytes())


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
        print("usage: pack.py <source_dir>", file=sys.stderr)
        return 1

    source_dir = Path(sys.argv[1]).resolve()
    paths = LutrisPaths.for_home(Path.home())

    slug = find_slug_for_directory(paths, source_dir)
    if slug is None:
        print(f"pack: no Lutris game found for {source_dir}", file=sys.stderr)
        return 0

    config_file = capture_config(paths, slug, source_dir)
    capture_art(paths, slug, source_dir)
    prune_excluded_paths(source_dir)
    print(f"pack: captured '{slug}' Lutris metadata into {config_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
