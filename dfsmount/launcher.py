"""Cheap /proc-based launcher process-name lookup.

Note: /proc/<pid>/comm is truncated to 15 characters (TASK_COMM_LEN - 1), same
as `ps -C` / `pgrep -x`. Use the truncated name in config if a launcher name is
longer than that.

Interpreted launchers (e.g. a Lutris installed as a Python entry point) show
up in comm as the interpreter itself - "python3" - not "lutris". For those,
fall back to matching the script/module basename in argv, e.g.
"python3 /usr/bin/lutris" -> "lutris".
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
            continue  # process exited between listing and read, or no permission
        if comm == name:
            return True
        if comm.startswith("python") and _matches_cmdline(entry, name):
            return True
    return False
