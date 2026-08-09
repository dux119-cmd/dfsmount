#!/usr/bin/env python3
"""dfsmount install hook: register an archived game with Lutris.

Run as `builtin:hooks/lutris/install.py <mount_dir>` (via `dfsmount install`).
Reads .metadata/ (see hooks/metadata.py), restores paths to mount_dir, and
writes the Lutris config, pga.db row, and artwork. Exits 0 if there's no
Lutris metadata, or if the game is already installed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lutris_common import (  # noqa: E402
    LutrisPaths,
    artwork_paths,
    connect,
    insert_game,
    prepare_for_insert,
)
from metadata import find_art, read, restore_root  # noqa: E402


def install_artwork(mount_dir: Path, paths: LutrisPaths, slug: str) -> None:
    for name, stem in artwork_paths(paths, slug).items():
        source = find_art(mount_dir, name)
        if source is None:
            continue
        destination = Path(f"{stem}{source.suffix}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: install.py <mount_dir>", file=sys.stderr)
        return 1

    mount_dir = Path(sys.argv[1]).resolve()
    meta = read(mount_dir)
    if meta is None or meta.launcher != "lutris":
        print(
            f"install: no Lutris metadata under {mount_dir}, skipping", file=sys.stderr
        )
        return 0

    paths = LutrisPaths.for_home(Path.home())
    slug = meta.id

    existing_id = None
    with connect(paths.db_path) as connection:
        row = connection.execute(
            "SELECT id, installed FROM games WHERE slug = ?", (slug,)
        ).fetchone()
        if row:
            if row["installed"] != 0:
                print(
                    f"install: '{slug}' is already installed, skipping", file=sys.stderr
                )
                return 0
            existing_id = row["id"]

    extra = restore_root(meta.extra, mount_dir)
    database_row = {
        **extra,
        "slug": slug,
        "name": meta.name,
        "runner": meta.runner,
    }

    config_text = meta.config_text.replace("{{GAME_ROOT}}", str(mount_dir))
    config_file_path = paths.games_config_dir / f"{database_row['configpath']}.yml"
    config_file_path.parent.mkdir(parents=True, exist_ok=True)
    config_file_path.write_text(config_text, encoding="utf-8")

    with connect(paths.db_path) as connection:
        insert_game(connection, prepare_for_insert(database_row, existing_id))

    install_artwork(mount_dir, paths, slug)

    print(f"install: registered '{slug}' with Lutris (mount: {mount_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
