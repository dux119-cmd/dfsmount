"""Create and locate revisioned dwarfs archives: "<target>-rev<N>.dfs"."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .config import require_executable
from .privsep import UserCreds, as_user

# Edit to taste - passed as --filter=<pattern> to mkdwarfs.
MKDWARFS_EXCLUDE_FILTERS: list[str] = []

_REV_RE = re.compile(r"^(?P<target>.+)-rev(?P<rev>\d+)\.dfs$")


def parse_revision(filename: str) -> tuple[str, int] | None:
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
    parsed = (parse_revision(entry.name) for entry in archives_dir.iterdir())
    return {target for target, _rev in parsed if target}


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
    run_as: UserCreds | None = None,
) -> Path:
    """Run mkdwarfs against source_dir, writing the next revision for `target`."""
    require_executable("mkdwarfs")
    if not source_dir.is_dir():
        raise NotADirectoryError(f"{source_dir} is not a directory")

    with as_user(run_as):
        archives_dir.mkdir(parents=True, exist_ok=True)
        output = next_archive_path(archives_dir, target)

        command = ["mkdwarfs"]
        for pattern in MKDWARFS_EXCLUDE_FILTERS:
            command.append(f"--filter={pattern}")
        command += [
            "--force",
            "--progress=simple",
            "--categorize",
            "--compress-level=9",
            "--compression=zstd:level=6:long:wlog=31:strat=3",
            f"--input={source_dir}",
            f"--output={output}",
        ]
        subprocess.run(command, check=True)
    return output
