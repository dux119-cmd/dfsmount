"""Minimal ctypes bindings for the fanotify permission-event API.

Lets us watch a directory for open attempts and delay (allow/deny) the calling
process's open() until we've decided how to respond - so the access that
triggers a mount can also be the access that transparently receives it.
"""

from __future__ import annotations

import ctypes
import os
import struct

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
_libc.fanotify_init.argtypes = [ctypes.c_uint, ctypes.c_uint]
_libc.fanotify_mark.argtypes = [
    ctypes.c_int,
    ctypes.c_uint,
    ctypes.c_uint64,
    ctypes.c_int,
    ctypes.c_char_p,
]

FAN_CLASS_CONTENT = 0x00000004
FAN_MARK_ADD = 0x00000001
FAN_MARK_ONLYDIR = 0x00000008
FAN_OPEN_PERM = 0x00010000
FAN_ONDIR = 0x40000000
FAN_EVENT_ON_CHILD = 0x08000000

FAN_ALLOW = 1
FAN_DENY = 2

_META_FMT = "<IBBHQii"
_META_SIZE = struct.calcsize(_META_FMT)
_RESP_FMT = "<Ii"


class FanotifyError(OSError):
    pass


class Fanotify:
    def __init__(self) -> None:
        fd = _libc.fanotify_init(FAN_CLASS_CONTENT, os.O_RDONLY)
        if fd < 0:
            errno = ctypes.get_errno()
            raise FanotifyError(
                errno, f"fanotify_init failed: {os.strerror(errno)} (are you root?)"
            )
        self.fd = fd

    def mark_dir(self, path: str) -> None:
        mask = FAN_OPEN_PERM | FAN_ONDIR | FAN_EVENT_ON_CHILD
        rc = _libc.fanotify_mark(
            self.fd, FAN_MARK_ADD | FAN_MARK_ONLYDIR, mask, -1, path.encode()
        )
        if rc < 0:
            errno = ctypes.get_errno()
            raise FanotifyError(
                errno, f"fanotify_mark failed for {path}: {os.strerror(errno)}"
            )

    def read_events(self):
        """Yield (mask, event_fd, pid) tuples. Caller must close event_fd."""
        buf = os.read(self.fd, 4096)
        pos = 0
        while pos + _META_SIZE <= len(buf):
            event_len, _vers, _reserved, _metadata_len, mask, ev_fd, pid = (
                struct.unpack_from(_META_FMT, buf, pos)
            )
            yield mask, ev_fd, pid
            pos += event_len

    def respond(self, ev_fd: int, allow: bool) -> None:
        response = FAN_ALLOW if allow else FAN_DENY
        os.write(self.fd, struct.pack(_RESP_FMT, ev_fd, response))

    def close(self) -> None:
        os.close(self.fd)
