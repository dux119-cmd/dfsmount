"""Symlink the source checkout's `dfsmount` launcher into ~/.local/bin.

Lets a self-contained checkout (curl install or manual clone) put `dfsmount`
on PATH without a `pip install`.
"""

from __future__ import annotations

import os
from pathlib import Path

BIN_DIR = Path.home() / ".local" / "bin"
LINK_NAME = "dfsmount"


def wrapper_script() -> Path:
    """The `dfsmount` launcher script beside this checkout's src/ tree."""
    return Path(__file__).resolve().parent.parent.parent / "dfsmount"


def install_symlink() -> Path:
    wrapper = wrapper_script()
    if not wrapper.is_file():
        raise FileNotFoundError(
            f"launcher script not found at {wrapper}; "
            "run this from a dfsmount source checkout"
        )

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    link = BIN_DIR / LINK_NAME
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(wrapper)
    return link


def bin_dir_on_path() -> bool:
    dirs = os.environ.get("PATH", "").split(os.pathsep)
    resolved = {Path(d).resolve() for d in dirs if d}
    return BIN_DIR.resolve() in resolved
