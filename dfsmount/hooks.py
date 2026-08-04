"""Run optional user-configured shell hooks around archive/mount lifecycle events.

Each hook is a shell command string from config.yaml; the relevant path(s) are
appended as extra argv entries. Hooks run under the same credentials as the
operation they wrap (run_as), never as root. A hook that fails or is missing
is logged and does not abort the underlying mount/archive operation - hooks
are user extensions, not part of the core contract.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .privsep import UserCreds, as_user


def run_hook(command: str | None, *args: Path, run_as: UserCreds | None = None) -> None:
    if not command:
        return

    argv = shlex.split(command) + [str(arg) for arg in args]
    try:
        with as_user(run_as):
            result = subprocess.run(argv, check=False)
    except OSError as exc:
        print(f"[dfsmount] hook {command!r} failed to run: {exc}")
        return
    if result.returncode != 0:
        print(f"[dfsmount] hook {command!r} exited {result.returncode}")
