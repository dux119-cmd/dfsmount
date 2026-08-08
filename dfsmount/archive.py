"""Create and locate revisioned dwarfs archives: "<target>-rev<N>.dfs"."""

from __future__ import annotations

import re
import subprocess
from functools import cache
from pathlib import Path

from .binaries import dwarfs_executable
from .config import LauncherHooks
from .hooks import run_hook

# Edit to taste - passed as --filter=<pattern> to mkdwarfs.
MKDWARFS_EXCLUDE_FILTERS: list[str] = []

_REV_RE = re.compile(r"^(?P<target>.+)-rev(?P<rev>\d+)\.dfs$")


@cache
def parse_revision(filename: str) -> tuple[str, int] | None:
    """Cached: called once per archive filename on every launcher-scan/reconcile
    poll (via discover_targets/revisions_for_target), and filenames are immutable
    once written."""
    match = _REV_RE.match(filename)
    if match is None:
        return None
    return match.group("target"), int(match.group("rev"))


def revisions_for_target(archives_dir: Path, target: str) -> list[tuple[int, Path]]:
    """(revision, path) pairs for a target, ascending by revision."""
    if not archives_dir.is_dir():
        return []
    found = []
    for entry in archives_dir.iterdir():
        parsed = parse_revision(entry.name)
        if parsed and parsed[0] == target:
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


def latest_archive(archives_dir: Path, target: str) -> Path | None:
    revisions = revisions_for_target(archives_dir, target)
    return revisions[-1][1] if revisions else None


def next_archive_path(archives_dir: Path, target: str) -> Path:
    revisions = revisions_for_target(archives_dir, target)
    next_rev = revisions[-1][0] + 1 if revisions else 1
    return archives_dir / f"{target}-rev{next_rev}.dfs"


def target_from_source(source_dir: Path) -> str:
    """Derive a target name from a source directory's final path component."""
    return source_dir.resolve().name


def create_archive(
    source_dir: Path,
    archives_dir: Path,
    target: str,
    hooks: LauncherHooks | None = None,
) -> Path:
    """Run mkdwarfs against source_dir, writing the next revision for `target`.

    mkdwarfs writes to a `.dfs.tmp` path and only gets renamed to the real
    `.dfs` name once it's fully written - `discover_targets`/`latest_archive`
    only recognize `.dfs`, so nothing (the service, a manual mount) can see
    or try to mount a still-being-written archive.
    """
    mkdwarfs = dwarfs_executable("mkdwarfs")
    if not source_dir.is_dir():
        raise NotADirectoryError(f"{source_dir} is not a directory")

    run_hook(hooks.pre_archive if hooks else None, source_dir)

    archives_dir.mkdir(parents=True, exist_ok=True)
    output = next_archive_path(archives_dir, target)
    temp_output = output.with_name(f"{output.name}.tmp")

    command = [mkdwarfs]
    for pattern in MKDWARFS_EXCLUDE_FILTERS:
        command.append(f"--filter={pattern}")
    command += [
        "--force",
        "--progress=simple",
        "--categorize",
        "--compress-level=9",
        "--compression=zstd:level=6:long:wlog=31:strat=3",
        f"--input={source_dir}",
        f"--output={temp_output}",
    ]
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError:
        temp_output.unlink(missing_ok=True)
        raise
    temp_output.rename(output)

    run_hook(hooks.post_archive if hooks else None, source_dir, output)
    return output


def archive_source_dir(source_dir: Path) -> Path:
    """Rename the now-archived source directory out of the way with an
    "-archived" suffix, so it no longer occupies the path the target will
    later be mounted at. A no-op if that name is already taken (e.g.
    re-archiving the same source twice)."""
    renamed = source_dir.with_name(f"{source_dir.name}-archived")
    if renamed.exists():
        return source_dir
    source_dir.rename(renamed)
    return renamed
