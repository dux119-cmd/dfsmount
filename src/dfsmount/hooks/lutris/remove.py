#!/usr/bin/env python3
"""dfsmount remove hook: remove a game's Lutris registration.

Run as `builtin:lutris/remove.py <mount_dir>` (via `dfsmount remove`).
Removes the pga.db row, config file, and artwork that install.py created.
Exits 0 if there's no captured metadata or the game isn't currently
registered - this only undoes `install`, it never touches the archive
itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lutris_common import (  # noqa: E402
    LutrisPaths,
    artwork_paths,
    config_path,
    connect,
    delete_game,
    dfsmount_config_path,
    parse_game_section,
)


def remove_artwork(paths: LutrisPaths, slug: str) -> None:
    for stem in artwork_paths(paths, slug).values():
        for ext in ("png", "jpg"):
            Path(f"{stem}.{ext}").unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: remove.py <mount_dir>", file=sys.stderr)
        return 1

    mount_dir = Path(sys.argv[1]).resolve()
    captured = dfsmount_config_path(mount_dir)
    if not captured.exists():
        print(
            f"remove: no Lutris metadata under {mount_dir}, skipping", file=sys.stderr
        )
        return 0

    slug = parse_game_section(captured.read_text(encoding="utf-8")).get("id")
    if not slug:
        print(f"remove: {captured} has no game.id, skipping", file=sys.stderr)
        return 0

    paths = LutrisPaths.for_home(Path.home())

    with connect(paths.db_path) as connection:
        row = connection.execute(
            "SELECT id FROM games WHERE slug = ?", (slug,)
        ).fetchone()
        if row is None:
            print(f"remove: '{slug}' is not registered, skipping", file=sys.stderr)
            return 0
        delete_game(connection, slug)

    config_path(paths, slug).unlink(missing_ok=True)
    remove_artwork(paths, slug)

    print(f"remove: removed '{slug}' from Lutris")
    return 0


if __name__ == "__main__":
    sys.exit(main())
