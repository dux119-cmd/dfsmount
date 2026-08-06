"""Run optional user-configured shell hooks around archive/mount lifecycle events.

Each hook is a shell command string from config.yaml, or a list of them to run
in sequence; the relevant path(s) are appended as extra argv entries. A
command may start with "builtin:" to reference a hook script bundled with
dfsmount, relative to the package root (e.g. "builtin:hooks/lutris/prepack.sh").
Hooks run under the same credentials as the operation they wrap (run_as),
never as root. A hook that fails or is missing is logged and does not abort
the underlying mount/archive operation - hooks are user extensions, not part
of the core contract.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .privsep import UserCreds, as_user

_BUILTIN_PREFIX = "builtin:"
_PACKAGE_ROOT = Path(__file__).resolve().parent


def _resolve_builtin(token: str) -> str:
    if not token.startswith(_BUILTIN_PREFIX):
        return token
    relative = token.removeprefix(_BUILTIN_PREFIX)
    return str(_PACKAGE_ROOT / relative)


def run_hook(
    commands: str | list[str] | None,
    *args: Path,
    run_as: UserCreds | None = None,
) -> None:
    if not commands:
        return
    for command in [commands] if isinstance(commands, str) else commands:
        _run_one(command, *args, run_as=run_as)


def _run_one(command: str, *args: Path, run_as: UserCreds | None = None) -> None:
    argv = shlex.split(command)
    if not argv:
        return
    argv[0] = _resolve_builtin(argv[0])
    argv += [str(arg) for arg in args]

    try:
        with as_user(run_as):
            result = subprocess.run(argv, check=False)
    except OSError as exc:
        print(f"[dfsmount] hook {command!r} failed to run: {exc}")
        return
    if result.returncode != 0:
        print(f"[dfsmount] hook {command!r} exited {result.returncode}")
