"""Run user-configured shell hooks. Failures are logged, never fatal."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

_BUILTIN_PREFIX = "builtin:"
_PACKAGE_ROOT = Path(__file__).resolve().parent


def _resolve_builtin(token: str) -> str:
    if not token.startswith(_BUILTIN_PREFIX):
        return token
    return str(_PACKAGE_ROOT / token.removeprefix(_BUILTIN_PREFIX))


def run_hook(commands: str | list[str] | None, *args: Path) -> None:
    if not commands:
        return
    for command in [commands] if isinstance(commands, str) else commands:
        _run_one(command, *args)


def _run_one(command: str, *args: Path) -> None:
    argv = shlex.split(command)
    if not argv:
        return
    argv[0] = _resolve_builtin(argv[0])
    argv += [str(arg) for arg in args]

    try:
        result = subprocess.run(argv, check=False)
    except OSError as exc:
        print(f"[dfsmount] hook {command!r} failed to run: {exc}")
        return
    if result.returncode != 0:
        print(f"[dfsmount] hook {command!r} exited {result.returncode}")
