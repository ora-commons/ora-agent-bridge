#!/usr/bin/env python3
"""A stand-in for a real coding-agent harness.

Started exactly as a real peer is — `[sys.executable, "<path>/fake_peer.py",
"<mode>", ...]` — it reads the whole of standard input and then behaves
according to its mode. Two families of modes exist: what a peer says (an
echo of the body, the final argument echoed for a command-line transport,
an empty or whitespace answer, a deliberate failure, a delayed echo) and
what a peer leaves behind (a program that will not stop, and fixtures that
report their process ids into a named file so a check can watch those
exact processes). `noisy-echo` also prints a vendor-style session identity,
a credential line, and a progress line, so a check can prove what a
returned-control turn retained and what was sanitized out of it;
`stop-parent-then-hang` terminates the process that started it, so a check
can be interrupted mid-wait without a driver of its own.

It imports nothing outside the standard library, touches nothing except
its input, the file it is told to write into, and its output streams, and
never sleeps without a bound.

SPDX-License-Identifier: CC0-1.0
"""

import os
import signal
import subprocess
import sys
import time

#: Long enough to outlast any real deadline, short enough that a stray copy
#: cannot survive the day, in short naps so a signal is acted on at once.
MAX_SLEEP_SECONDS = 3600.0
NAP_SECONDS = 0.05

THIS_FILE = os.path.abspath(__file__)


def _read_all_stdin() -> str:
    data = sys.stdin.buffer.read()
    return data.decode("utf-8", "replace")


def _emit(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def _echoed(body: str) -> str:
    if not body:
        return ""
    return body if body.endswith("\n") else body + "\n"


def _sleep_bounded(seconds: float) -> None:
    deadline = time.time() + min(seconds, MAX_SLEEP_SECONDS)
    while time.time() < deadline:
        time.sleep(NAP_SECONDS)


def _spawn_child() -> int:
    child = subprocess.Popen(
        [sys.executable, THIS_FILE, "hang"], stdin=subprocess.DEVNULL,
    )
    return child.pid


def _write_pids(path: str, child_pid: int) -> None:
    with open(path, "w", encoding="utf-8") as stream:
        stream.write("PEER {0}\nCHILD {1}\n".format(os.getpid(), child_pid))
        stream.flush()
        os.fsync(stream.fileno())


def _seconds_from(extra: list, default: float = 0.0) -> float:
    if not extra:
        return default
    return float(extra[0])


def _run(mode: str, extra: list) -> int:
    body = _read_all_stdin()
    echoed = _echoed(body)

    if mode == "plain":
        _emit(echoed)
        return 0

    if mode == "last-argument":
        # The body travelled as the final argument; hand that back exactly,
        # and complain where a check will see it if standard input was not
        # empty.
        _emit(_echoed(extra[-1]) if extra else "")
        if body:
            _emit("STDIN WAS NOT EMPTY\n")
        return 0

    if mode == "empty":
        return 0

    if mode == "whitespace":
        _emit("\n   \n\n  \n")
        return 0

    if mode == "fail":
        sys.stderr.write("fake peer: deliberate failure\n")
        sys.stderr.flush()
        return 3

    if mode == "hang":
        _sleep_bounded(_seconds_from(extra, MAX_SLEEP_SECONDS))
        return 0

    if mode == "delay-echo":
        _sleep_bounded(_seconds_from(extra))
        _emit(echoed)
        return 0

    if mode in ("noisy-echo", "stop-parent-then-hang"):
        seconds = _seconds_from(extra) if mode == "noisy-echo" else float(extra[0])
        sys.stderr.write("session id: fake-peer-session-0001\n")
        sys.stderr.write("OPENAI_API_KEY=opaque-value\n")
        sys.stderr.flush()
        _emit("PROGRESS still working\n")
        if mode == "stop-parent-then-hang":
            with open(extra[1], "w", encoding="utf-8") as stream:
                stream.write("PEER {0}\n".format(os.getpid()))
                stream.flush()
                os.fsync(stream.fileno())
            os.kill(os.getppid(), signal.SIGTERM)
        _sleep_bounded(seconds)
        if mode == "noisy-echo":
            _emit(echoed)
        return 0

    if mode == "write-pids-then-hang":
        # The child stays in the group the runner created, so terminating
        # that group must reach it; a runner that kills only its direct
        # child leaves this process behind.
        _write_pids(extra[0], _spawn_child())
        _sleep_bounded(MAX_SLEEP_SECONDS)
        return 0

    return 2


def main(argv: list) -> int:
    arguments = argv[1:]
    return _run(arguments[0], arguments[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
