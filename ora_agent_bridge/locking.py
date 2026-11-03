"""One lock per session, held by one process, for one turn.

An advisory lock on a file, held open by the running process: `fcntl.flock`
on POSIX, `msvcrt.locking` over one byte of the same file on Windows. It
records nothing, has no lease or stale-lock rule, and is dropped by the
operating system the moment the holder ends, so a dead holder blocks nobody.
`.lock` is a pathname to lock, not a place to keep state. Contention is
`BUSY_SESSION`, immediately, having changed nothing.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import contextlib
import os
from typing import Iterator

from .errors import BridgeError, Failure

try:  # POSIX: the advisory lock is an flock on an open descriptor.
    import fcntl
except ImportError:  # Windows: msvcrt.locking is the twin there.
    fcntl = None

try:  # Windows: a byte-range lock on the same open descriptor.
    import msvcrt
except ImportError:  # POSIX: fcntl is the twin there.
    msvcrt = None

#: The file inside a session directory that is used purely as a lock target.
LOCK_FILENAME = ".lock"

#: How many bytes of the lock file the Windows twin locks.
LOCKED_BYTES = 1


def lock_path(session_dir: str) -> str:
    """Where this session's lock is taken out."""
    return os.path.join(session_dir, LOCK_FILENAME)


def _acquire(handle: int) -> None:
    """Take this session's exclusive lock without waiting, or raise OSError.

    Both dialects are held by the open descriptor, both are dropped by the
    operating system the moment it closes or the holding process ends, and
    both refuse rather than wait.
    """
    if msvcrt is not None:
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_NBLCK, LOCKED_BYTES)
    else:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(handle: int) -> None:
    """Give the lock back before the descriptor closes (Windows only)."""
    if msvcrt is None:
        return
    os.lseek(handle, 0, os.SEEK_SET)
    msvcrt.locking(handle, msvcrt.LK_UNLCK, LOCKED_BYTES)


@contextlib.contextmanager
def session_lock(session_dir: str) -> Iterator[str]:
    """Hold this session's lock for the duration of the block.

    Raises `BUSY_SESSION` at once if another process already holds it, and
    `SESSION_NOT_FOUND` when there is no such directory to lock.
    """
    if not os.path.isdir(session_dir):
        raise BridgeError(Failure.SESSION_NOT_FOUND, detail=session_dir)
    path = lock_path(session_dir)
    try:
        handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise BridgeError(Failure.SESSION_INVALID, detail=str(exc))
    try:
        try:
            _acquire(handle)
        except OSError:
            raise BridgeError(Failure.BUSY_SESSION, detail=session_dir)
        yield path
    finally:
        try:
            _release(handle)
        except OSError:
            # Closing the descriptor below releases the lock on both
            # platforms, so a failed explicit unlock cannot strand a holder.
            pass
        os.close(handle)
