"""Run filesystem/subprocess operations as a specific unprivileged user.

The service needs root only for fanotify (init/mark/respond). Everything it
does on our behalf - mkdir, mkdwarfs, dwarfs, fuse-overlayfs, umount - should
run as the configured user, since that's what fuse-overlayfs is for. This
module switches the process's effective uid/gid for the duration of a `with`
block, then restores root.
"""

from __future__ import annotations

import grp
import os
import pwd
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class UserCreds:
    name: str
    uid: int
    gid: int
    groups: tuple[int, ...]
    home: str


def lookup_user(name: str) -> UserCreds:
    try:
        pw = pwd.getpwnam(name)
    except KeyError:
        raise SystemExit(f"no such user: {name!r}") from None
    groups = tuple(g.gr_gid for g in grp.getgrall() if name in g.gr_mem)
    groups = tuple({pw.pw_gid, *groups})
    return UserCreds(
        name=name, uid=pw.pw_uid, gid=pw.pw_gid, groups=groups, home=pw.pw_dir
    )


@contextmanager
def as_user(creds: UserCreds | None):
    """Temporarily drop from root to `creds` for filesystem/subprocess calls.

    No-op if creds is None or we're not root (CLI commands run directly as
    the invoking user and never need this).
    """
    if creds is None or os.geteuid() != 0:
        yield
        return

    os.setegid(creds.gid)
    os.setgroups(creds.groups)
    os.seteuid(creds.uid)
    try:
        yield
    finally:
        os.seteuid(0)
        os.setgroups([0])
        os.setegid(0)
