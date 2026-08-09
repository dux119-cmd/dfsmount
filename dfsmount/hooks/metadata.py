"""Generic ".metadata/" format written by a launcher's prepack hook and read
back by its install hook, so an archive stays portable across launchers.

Layout inside a game directory:
    .metadata/game.json      -- GameMetadata, JSON-encoded
    .metadata/config.<ext>   -- raw launcher config text, if any
    .metadata/art/<name>.<ext>

Any path under game_root in `extra`/`launch` is stored as the literal
string ROOT_PLACEHOLDER so it survives being moved or remounted elsewhere;
`resolve()` swaps it back for a real path at install/uninstall time.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

METADATA_DIR = ".metadata"
ROOT_PLACEHOLDER = "{{GAME_ROOT}}"
ART_EXTENSIONS = ("png", "jpg", "jpeg")


@dataclass(frozen=True)
class GameMetadata:
    launcher: str  # e.g. "lutris"
    id: str  # launcher-specific unique id (e.g. slug)
    name: str
    runner: str | None = None
    config_ext: str | None = None  # extension of the raw config file, if any
    config_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)  # launcher-specific fields
    art: dict[str, str] = field(default_factory=dict)  # art name -> file extension


def _map_strings(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, dict):
        return {k: _map_strings(v, transform) for k, v in value.items()}
    if isinstance(value, list):
        return [_map_strings(v, transform) for v in value]
    if isinstance(value, str):
        return transform(value)
    return value


def strip_root(value: Any, game_root: Path) -> Any:
    root = str(game_root)

    def transform(text: str) -> str:
        if not text.startswith(root):
            return text
        remainder = text[len(root) :].lstrip("/")
        return f"{ROOT_PLACEHOLDER}/{remainder}" if remainder else ROOT_PLACEHOLDER

    return _map_strings(value, transform)


def restore_root(value: Any, new_root: Path) -> Any:
    return _map_strings(
        value, lambda text: text.replace(ROOT_PLACEHOLDER, str(new_root))
    )


def write(
    game_dir: Path, meta: GameMetadata, art_files: dict[str, Path] | None = None
) -> Path:
    """Write meta and any art_files (art name -> source path) into
    game_dir/.metadata, returning that directory."""
    metadata_dir = game_dir / METADATA_DIR
    metadata_dir.mkdir(exist_ok=True)

    (metadata_dir / "game.json").write_text(
        json.dumps(asdict(meta), indent=2), encoding="utf-8"
    )
    if meta.config_ext:
        (metadata_dir / f"config.{meta.config_ext}").write_text(
            meta.config_text, encoding="utf-8"
        )

    art_dir = metadata_dir / "art"
    for name, source in (art_files or {}).items():
        art_dir.mkdir(exist_ok=True)
        (art_dir / f"{name}{source.suffix}").write_bytes(source.read_bytes())

    return metadata_dir


def read(game_dir: Path) -> GameMetadata | None:
    metadata_dir = game_dir / METADATA_DIR
    game_json = metadata_dir / "game.json"
    if not game_json.exists():
        return None

    raw = json.loads(game_json.read_text(encoding="utf-8"))
    meta = GameMetadata(**raw)
    if meta.config_ext:
        config_path = metadata_dir / f"config.{meta.config_ext}"
        if config_path.exists():
            meta = _replace(meta, config_text=config_path.read_text(encoding="utf-8"))
    return meta


def _replace(meta: GameMetadata, **changes: Any) -> GameMetadata:
    data = asdict(meta)
    data.update(changes)
    return GameMetadata(**data)


def find_art(game_dir: Path, name: str) -> Path | None:
    art_dir = game_dir / METADATA_DIR / "art"
    return next(
        (p for ext in ART_EXTENSIONS if (p := art_dir / f"{name}.{ext}").exists()),
        None,
    )
