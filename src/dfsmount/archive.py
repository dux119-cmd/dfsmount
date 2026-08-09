"""Create and locate revisioned dwarfs archives: "<target>-rev<N>.dfs"."""

from __future__ import annotations

import re
import subprocess
from functools import cache
from pathlib import Path

from .binaries import dwarfs_executable
from .hooks import run_hook
from .models import TargetPaths

MKDWARFS_EXCLUDE_FILTERS: list[str] = []  # passed as --filter=<pattern> to mkdwarfs

_REV_RE = re.compile(r"^(?P<target>.+)-rev(?P<rev>\d+)\.dfs$")


@cache
def parse_revision(filename: str) -> tuple[str, int] | None:
    match = _REV_RE.match(filename)
    if match is None:
        return None
    return match.group("target"), int(match.group("rev"))


def revisions_for_target(paths: TargetPaths) -> list[tuple[int, Path]]:
    if not paths.archives_dir.is_dir():
        return []
    found = []
    for entry in paths.archives_dir.iterdir():
        parsed = parse_revision(entry.name)
        if parsed and parsed[0] == paths.target:
            found.append((parsed[1], entry))
    return sorted(found)


def discover_targets(archives_dir: Path) -> set[str]:
    if not archives_dir.is_dir():
        return set()
    return {
        parsed[0]
        for entry in archives_dir.iterdir()
        if (parsed := parse_revision(entry.name)) is not None
    }


def latest_archive(paths: TargetPaths) -> Path | None:
    revisions = revisions_for_target(paths)
    return revisions[-1][1] if revisions else None


def next_archive_path(paths: TargetPaths) -> Path:
    revisions = revisions_for_target(paths)
    next_rev = revisions[-1][0] + 1 if revisions else 1
    return paths.archives_dir / f"{paths.target}-rev{next_rev}.dfs"


def target_from_source(source_dir: Path) -> str:
    return source_dir.resolve().name


def create_archive(paths: TargetPaths, source_dir: Path) -> Path:
    """Run mkdwarfs against source_dir, writing the next revision for paths.target."""
    mkdwarfs = dwarfs_executable("mkdwarfs")
    if not source_dir.is_dir():
        raise NotADirectoryError(f"{source_dir} is not a directory")

    run_hook(paths.hooks.pack, source_dir)

    paths.archives_dir.mkdir(parents=True, exist_ok=True)
    output = next_archive_path(paths)
    temp_output = output.with_name(f"{output.name}.tmp")

    command = [mkdwarfs]
    for pattern in MKDWARFS_EXCLUDE_FILTERS:
        command.append(f"--filter={pattern}")
    command += [
        "--force",
        "--progress=simple",
        "--categorize",
        "--compress-level=9",
        "--compression=zstd:level=6:long:wlog=30:strat=3",
        f"--input={source_dir}",
        f"--output={temp_output}",
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError:
        temp_output.unlink(missing_ok=True)
        raise
    temp_output.rename(output)

    return output


def archive_source_dir(source_dir: Path) -> Path:
    """Rename the archived source directory aside with an "-archived" suffix."""
    renamed = source_dir.with_name(f"{source_dir.name}-archived")
    if renamed.exists():
        return source_dir
    source_dir.rename(renamed)
    return renamed
