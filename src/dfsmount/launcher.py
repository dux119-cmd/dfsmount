"""Cheap /proc-based launcher process-name lookup.

/proc/<pid>/comm is truncated to 15 chars, same as `pgrep -x`. Interpreted
launchers (e.g. a Python entry point) show up in comm as the interpreter,
so those are matched against argv's script/module basename instead.
"""

from __future__ import annotations

from pathlib import Path


def _matches_cmdline(pid_dir: Path, name: str) -> bool:
    try:
        raw = (pid_dir / "cmdline").read_bytes()
    except OSError:
        return False
    args = raw.split(b"\0")
    return any(Path(arg.decode(errors="replace")).name == name for arg in args if arg)


def is_launcher_running(name: str) -> bool:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
        except OSError:
            continue
        if comm == name:
            return True
        if comm.startswith("python") and _matches_cmdline(entry, name):
            return True
    return False
