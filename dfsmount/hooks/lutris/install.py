#!/usr/bin/env python3
"""dfsmount install hook: register an archived game with Lutris.

Run by dfsmount as `builtin:hooks/lutris/install.py <mount_dir>` (via
`dfsmount install`). Reads the `lutris/` metadata prepack.py captured
before archiving, restores the portable placeholder path to mount_dir, and:

1. Writes the Lutris config back to ~/.local/share/lutris/games/.
2. Inserts (or updates, if a placeholder row already exists) the game's
   pga.db row.
3. Copies banner/coverart/logo artwork back into place.

Exits 0 if mount_dir has no lutris/database.json - not every target this
launcher archives is necessarily a Lutris game - and if the game's slug is
already installed, since re-running `install` shouldn't fail loudly.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lutris_common import (  # noqa: E402
    GAME_ROOT_PLACEHOLDER,
    LutrisPaths,
    artwork_paths,
    connect,
    find_artwork,
    insert_game,
    prepare_for_insert,
    restore_paths,
)


def install_artwork(lutris_dir: Path, paths: LutrisPaths, slug: str) -> None:
    for name, stem in artwork_paths(paths, slug).items():
        source = find_artwork(lutris_dir / name)
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
    lutris_dir = mount_dir / "lutris"
    database_path = lutris_dir / "database.json"
    if not database_path.exists():
        print(
            f"install: no lutris/database.json under {mount_dir}, skipping",
            file=sys.stderr,
        )
        return 0

    paths = LutrisPaths.for_home(Path.home())
    database = json.loads(database_path.read_text(encoding="utf-8"))
    restored_database = restore_paths(database, str(mount_dir))
    slug = restored_database["slug"]

    existing_id = None
    with connect(paths.db_path) as connection:
        row = connection.execute(
            "SELECT id, installed FROM games WHERE slug = ?", (slug,)
        ).fetchone()
        if row:
            if row["installed"] == 0:
                existing_id = row["id"]
            else:
                print(
                    f"install: '{slug}' is already installed, skipping", file=sys.stderr
                )
                return 0

    config_path = lutris_dir / "config.yml"
    config_text = (
        config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    )
    restored_config = config_text.replace(GAME_ROOT_PLACEHOLDER, str(mount_dir))

    config_file_path = paths.games_config_dir / f"{restored_database['configpath']}.yml"
    config_file_path.parent.mkdir(parents=True, exist_ok=True)
    config_file_path.write_text(restored_config, encoding="utf-8")

    with connect(paths.db_path) as connection:
        insert_game(connection, prepare_for_insert(restored_database, existing_id))

    install_artwork(lutris_dir, paths, slug)

    print(f"install: registered '{slug}' with Lutris (mount: {mount_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
