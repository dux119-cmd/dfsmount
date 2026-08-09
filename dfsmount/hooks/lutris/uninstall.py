#!/usr/bin/env python3
"""dfsmount uninstall hook: remove a game's Lutris registration.

Run as `builtin:hooks/lutris/uninstall.py <mount_dir>` (via `dfsmount
uninstall`). Removes the pga.db row, config file, and artwork that
install.py created. Exits 0 if there's no Lutris metadata or the game
isn't currently registered - this only undoes `install`, it never touches
the archive itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _lutris_common import (  # noqa: E402
    LutrisPaths,
    artwork_paths,
    connect,
    delete_game,
)
from metadata import read  # noqa: E402


def remove_artwork(paths: LutrisPaths, slug: str) -> None:
    for stem in artwork_paths(paths, slug).values():
        for ext in ("png", "jpg"):
            Path(f"{stem}.{ext}").unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: uninstall.py <mount_dir>", file=sys.stderr)
        return 1

    mount_dir = Path(sys.argv[1]).resolve()
    meta = read(mount_dir)
    if meta is None or meta.launcher != "lutris":
        print(
            f"uninstall: no Lutris metadata under {mount_dir}, skipping",
            file=sys.stderr,
        )
        return 0

    paths = LutrisPaths.for_home(Path.home())
    slug = meta.id

    with connect(paths.db_path) as connection:
        row = connection.execute(
            "SELECT id FROM games WHERE slug = ?", (slug,)
        ).fetchone()
        if row is None:
            print(f"uninstall: '{slug}' is not registered, skipping", file=sys.stderr)
            return 0
        delete_game(connection, slug)

    config_path = paths.games_config_dir / f"{meta.extra.get('configpath', slug)}.yml"
    config_path.unlink(missing_ok=True)
    remove_artwork(paths, slug)

    print(f"uninstall: removed '{slug}' from Lutris")
    return 0


if __name__ == "__main__":
    sys.exit(main())
