#!/usr/bin/env python3
"""dfsmount install hook: register an archived game with Lutris.

Run as `builtin:lutris/install.py <mount_dir>` (via `dfsmount install`).
Reads .dfsmount/config.yml's "game:" section (fields listed in
config-to-database-fields.yaml), restores it to mount_dir, and writes the
Lutris config, pga.db row, and artwork. `directory` is set from mount_dir
and `configpath` from the identity field's value (normally the slug);
whatever art files are present under .dfsmount/art/ are installed as-is.
Exits 0 if there's no captured metadata, or if the game is already
installed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _lutris_common import (  # noqa: E402
    ROOT_PLACEHOLDER,
    LutrisPaths,
    artwork_paths,
    config_identity_field,
    config_path,
    connect,
    dfsmount_config_path,
    find_dfsmount_art,
    insert_game,
    load_config_to_database_fields,
    parse_game_section,
    prepare_for_insert,
)


def install_artwork(mount_dir: Path, paths: LutrisPaths, slug: str) -> None:
    for name, stem in artwork_paths(paths, slug).items():
        source = find_dfsmount_art(mount_dir, name)
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
    captured = dfsmount_config_path(mount_dir)
    if not captured.exists():
        print(
            f"install: no Lutris metadata under {mount_dir}, skipping", file=sys.stderr
        )
        return 0

    config_text = captured.read_text(encoding="utf-8")
    fields = parse_game_section(config_text)
    field_map = load_config_to_database_fields()
    identity_field = config_identity_field(field_map)
    slug = fields.get(identity_field) if identity_field else None
    if not slug:
        print(f"install: {captured} has no identity field, skipping", file=sys.stderr)
        return 0

    paths = LutrisPaths.for_home(Path.home())

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

    config_text = config_text.replace(ROOT_PLACEHOLDER, str(mount_dir))
    config_file_path = config_path(paths, slug)
    config_file_path.parent.mkdir(parents=True, exist_ok=True)
    config_file_path.write_text(config_text, encoding="utf-8")

    database_row = {
        db_column: fields.get(yaml_field) or None
        for yaml_field, db_column in field_map.items()
    }
    database_row["name"] = database_row.get("name") or slug
    # Not mapped: computed from the install itself, always hard-coded.
    database_row["directory"] = str(mount_dir)
    database_row["configpath"] = slug
    database_row["installed"] = 1

    with connect(paths.db_path) as connection:
        insert_game(connection, prepare_for_insert(database_row, existing_id))

    install_artwork(mount_dir, paths, slug)

    print(f"install: registered '{slug}' with Lutris (mount: {mount_dir})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
