"""Starting one program, waiting for one answer, and leaving nothing behind.

Everything Ora Agent Bridge starts is started here the same way - a fixed
argument vector with no shell - and cleaned up the same way: the child is
started as its own session leader (POSIX) or in a kill-on-close job object
assigned before its first instruction runs (Windows), and cleanup signals
exactly that owned set. `run_bounded` bounds a probe with a deadline because
a readiness question is not work; `start_call` starts a peer whose waits
never kill it. A stop is deferred - written down rather than raised - for the
whole life of the child, with one window around the wait where it raises
immediately inside the cleanup that catches it, so no stop can leave a child
behind. What cannot be cleaned up is stated rather than pretended about:
SIGKILL, power loss, a child that escapes into its own session, and a turn
off the main thread.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import contextlib
import errno
import os
import signal
import subprocess
import tempfile
import time
from typing import (
    Callable,
    Iterable,
    Iterator,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

from .errors import BridgeError, Failure

#: The bound on a readiness check: the cheap version, sign-in and switch
#: questions a connector asks before any work starts. It is not a deadline
#: on the work itself, which has none.
READINESS_TIMEOUT_SECONDS = 120.0

#: How long a polite termination is given to empty the process group.
CLEANUP_GRACE_SECONDS = 5.0

#: How long an unconditional kill is then given, before `CLEANUP_FAILURE`.
ESCALATION_GRACE_SECONDS = 5.0

#: Interval between the short looks that ask whether the group has emptied.
POLL_SECONDS = 0.02

#: How often a bounded call reports that it is still waiting, in seconds.
#: A check-in, not progress: it says the caller's wait is still running and
#: whether the child process is still alive, and nothing about the model.
HEARTBEAT_SECONDS = 30.0

#: The prefix of the temporary files a started peer reads its body from and
#: captures its output streams into. Files rather than pipes, so a bounded
#: wait can read progress without stealing bytes from the final result.
PEER_FILE_PREFIX = "agent-bridge-peer-"

#: Whether the process-group twins below run their Windows branches, decided
#: from the operating system at import time. The unit checks simulate the
#: Windows branch by patching this and the taskkill and job twins, because no
#: Windows machine is part of the qualification.
WINDOWS = os.name == "nt"

#: The Windows creation flag that makes the child the leader of a brand-new
#: process group numbered by its own process id: the twin of
#: `start_new_session`. A missing constant reads as zero on POSIX.
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

#: The Windows console signal that can be sent to a whole process group.
_CTRL_BREAK_EVENT = getattr(signal, "CTRL_BREAK_EVENT", None)

#: The escalation signal, resolved once here because Windows CPython defines
#: no `SIGKILL`: there it resolves to None and the forced phase is the
#: kill-on-close job's release.
_SIGKILL = getattr(signal, "SIGKILL", None)

#: The exit status `taskkill` reports for a pid it could not find. Its tree
#: walk starts at the root, so it is read as the emptiness answer only after
#: the kill-on-close job has been released.
_TASKKILL_NOT_FOUND = 128

#: The job-object information class of the extended-limits structure.
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

#: The one job limit this module sets: the kernel terminates every process
#: in the job when the last handle to it closes.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

#: The process access the job assignment needs.
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

#: The Windows creation flag that starts the child with its primary thread
#: suspended, so job membership precedes the first moment a descendant could
#: exist. `subprocess` names no constant of its own for it.
_CREATE_SUSPENDED = 0x00000004

#: The toolhelp snapshot flag that includes every thread in the system.
_TH32CS_SNAPTHREAD = 0x00000004

#: The thread access the resume needs, and all of it.
_THREAD_SUSPEND_RESUME = 0x0002

#: What `ResumeThread` returns when it fails: `(DWORD)-1`.
_RESUME_FAILED = 0xFFFFFFFF


class Deadline(object):
    """One bound for a turn's non-work steps, made once and passed down.

    `seconds` bounds the readiness questions a connector asks, which are not
    AI work and may be ended when they overrun; the work itself has no
    elapsed-time deadline in any turn the runner starts. `heartbeat` is an
    optional no-result callback the bounded wait invokes roughly once per
    `HEARTBEAT_SECONDS`.
    """

    def __init__(self, seconds: float) -> None:
        self.seconds = float(seconds)
        self._started = time.monotonic()
        self.heartbeat = None  # type: Optional[Callable[[float, bool], None]]

    def remaining(self) -> float:
        """Seconds left; zero or less once the deadline has passed."""
        return self.seconds - (time.monotonic() - self._started)

    def check(self, detail: Optional[str] = None) -> None:
        """Stop now if the deadline has already passed."""
        if self.remaining() <= 0.0:
            raise BridgeError(Failure.TIMEOUT, detail=detail)


class CompletedCall(NamedTuple):
    """What one bounded call produced."""

    returncode: int
    stdout: str
    stderr: str


class SignalStop(Exception):
    """Somebody asked this turn to stop while it was under way.

    Raised from inside a signal handler so a termination or hangup leaves by
    the ordinary route - through the cleanup - instead of ending the process
    where it stands.
    """

    def __init__(self, number: int) -> None:
        super().__init__(
            "Ora Agent Bridge was stopped by signal {0}, so the turn did not "
            "finish.".format(number)
        )
        self.number = number


#: The two signals a turn turns into `SignalStop`. SIGHUP does not exist on
#: Windows, where SIGTERM is the one deliverable termination signal.
STOP_SIGNALS: Tuple[int, ...] = (
    (signal.SIGTERM, signal.SIGHUP)
    if hasattr(signal, "SIGHUP")
    else (signal.SIGTERM,)
)

#: An interrupt from the keyboard keeps raising `KeyboardInterrupt`. It is
#: handled here all the same, because a handler of our own can be made to
#: wait through the moments below, where Python's cannot.
INTERRUPT_SIGNALS: Tuple[int, ...] = (signal.SIGINT,)

#: Every signal handled here, in the order they are installed.
HANDLED_SIGNALS: Tuple[int, ...] = STOP_SIGNALS + INTERRUPT_SIGNALS


def _stop_exception(number: int) -> BaseException:
    """What a given signal leaves by. Interrupts keep their own exception."""
    if number in INTERRUPT_SIGNALS:
        return KeyboardInterrupt()
    return SignalStop(number)


class StopWatch(object):
    """When a stop raises where it lands, and when it waits its turn.

    For the whole life of the child a stop is deferred: written down, and
    raised once the stretch it arrived in is over. `allowing()` opens the one
    window where it raises immediately, and that window sits inside the
    cleanup that catches it. Deferral changes when a stop is raised, never
    whether; only the first is remembered. The state lives on the object, so
    it belongs to exactly one `stopped_by_signal` region.
    """

    def __init__(self) -> None:
        self._deferrals = 0
        self._pending = None  # type: Optional[int]

    def handle(self, number, frame) -> None:
        """The signal handler itself: raise now, or write it down for later."""
        if self._deferrals > 0:
            if self._pending is None:
                self._pending = number
            return
        raise _stop_exception(number)

    @contextlib.contextmanager
    def deferring(self) -> Iterator[None]:
        """Inside this block a stop is written down instead of being raised."""
        self._deferrals += 1
        try:
            yield
        finally:
            self._deferrals -= 1

    @contextlib.contextmanager
    def allowing(self) -> Iterator[None]:
        """Inside this block a stop raises again. Only valid inside `deferring`.

        A stop that arrived before the door opened is raised as it opens, so
        a turn already told to stop does not go on waiting out its deadline.
        """
        self._deferrals -= 1
        try:
            self.raise_if_stopped()
            yield
        finally:
            self._deferrals += 1

    def raise_if_stopped(self) -> None:
        """Raise a stop that arrived while it was being deferred, if one did."""
        number, self._pending = self._pending, None
        if number is not None:
            raise _stop_exception(number)


@contextlib.contextmanager
def stopped_by_signal() -> Iterator[StopWatch]:
    """Make termination and hangup raise, and put the handlers back after.

    Installing a handler is only possible on the main thread; elsewhere the
    block runs without them, and the watch is still yielded and never fires.
    """
    watch = StopWatch()
    installed = []
    try:
        for number in HANDLED_SIGNALS:
            installed.append((number, signal.signal(number, watch.handle)))
    except (OSError, ValueError):
        pass
    try:
        yield watch
    finally:
        for number, previous in reversed(installed):
            try:
                signal.signal(number, previous)
            except (OSError, ValueError):
                pass


def _windows_taskkill(pgid: int) -> bool:
    """Force the owned process tree to terminate; say whether it was found.

    `taskkill /T` walks the descendant tree from the root pid and `/F`
    terminates every process it names, the one reliable way to reach
    grandchildren on Windows. But the walk starts at the root: once the root
    has exited, the not-found report names no descendant and empties nothing,
    so that report is an emptiness answer only after the job's close has
    already terminated every member.
    """
    try:
        completed = subprocess.run(
            ("taskkill", "/T", "/F", "/PID", str(pgid)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=ESCALATION_GRACE_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail=(
                "taskkill could not terminate the process tree of {0}: "
                "{1}".format(pgid, exc)
            ),
        )
    if completed.returncode == _TASKKILL_NOT_FOUND:
        return True
    if completed.returncode != 0:
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail=(
                "taskkill exited {0} while terminating the process tree of "
                "{1}: {2}".format(
                    completed.returncode,
                    pgid,
                    completed.stderr.decode("utf-8", "replace").strip()[:160],
                )
            ),
        )
    return False


def _windows_kernel32():
    """kernel32 through ctypes, resolved per call so a POSIX process never
    imports it at module load."""
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _windows_job_information():
    """The extended-limits structure for one kill-on-close job, freshly built.

    Only the ctypes shapes are declared here; the one fact that must be right
    is the byte layout `SetInformationJobObject` expects, which is why the
    declaration lives in a function any platform can import and the unit
    checks can measure.
    """
    import ctypes

    class io_counters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class basic_limits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class extended_limits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", basic_limits),
            ("IoInfo", io_counters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    information = extended_limits()
    information.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    return information


def _windows_own_tree(process: "subprocess.Popen") -> int:
    """Place the suspended child in a kill-on-close job; return the job handle.

    The child's primary thread has not run when this is called, so the
    assignment happens while there is nothing yet beside the child for any
    job to miss. Everything the child later starts is a member too, and the
    kernel terminates every member when the last handle closes - this code's
    in cleanup or the operating system's at the end of this process. Raises
    `CLEANUP_FAILURE` naming the step that refused, so the caller can end
    what it just started rather than run a tree nobody owns.
    """
    import ctypes

    def refused(step, code):
        return BridgeError(
            Failure.CLEANUP_FAILURE,
            detail=(
                "{0} refused the kill-on-close job for process {1} "
                "(error {2}); the turn cannot own what it starts here"
                .format(step, process.pid, code)
            ),
        )

    kernel32 = _windows_kernel32()
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.SetInformationJobObject.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    )
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    kernel32.AssignProcessToJobObject.argtypes = (
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise refused("CreateJobObjectW", ctypes.get_last_error())
    information = _windows_job_information()
    if not kernel32.SetInformationJobObject(
        job,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        kernel32.CloseHandle(job)
        raise refused("SetInformationJobObject", ctypes.get_last_error())
    process_handle = kernel32.OpenProcess(
        _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, process.pid
    )
    if not process_handle:
        kernel32.CloseHandle(job)
        raise refused("OpenProcess", ctypes.get_last_error())
    assigned = kernel32.AssignProcessToJobObject(job, process_handle)
    code = ctypes.get_last_error()
    kernel32.CloseHandle(process_handle)
    if not assigned:
        kernel32.CloseHandle(job)
        raise refused("AssignProcessToJobObject", code)
    return job


def _windows_thread_entry():
    """The toolhelp thread record, freshly built, `dwSize` still to be set.

    Of the seven fields, two are read on the Windows path: the owning process
    id the snapshot is filtered by, and the thread id the resume is asked of.
    """
    import ctypes

    class thread_entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ThreadID", ctypes.c_uint32),
            ("th32OwnerProcessID", ctypes.c_uint32),
            ("tpBasePri", ctypes.c_int32),
            ("tpDeltaPri", ctypes.c_int32),
            ("dwFlags", ctypes.c_uint32),
        ]

    return thread_entry()


def _windows_resume_root(process: "subprocess.Popen") -> None:
    """Resume the suspended root: the step that first lets its code run.

    The kill-on-close job has already been assigned, so the first instruction
    that runs - and every process it goes on to start - runs inside an
    ownership that is already standing. The primary thread is found rather
    than remembered: `Popen` closes the thread handle from creation, so the
    thread is named through the toolhelp thread snapshot. A never-run process
    has created no other threads, so the threads found are the primary thread
    alone. Raises `CLEANUP_FAILURE` naming the step that refused.
    """
    import ctypes

    def refused(step, code):
        return BridgeError(
            Failure.CLEANUP_FAILURE,
            detail=(
                "{0} refused to resume process {1} (error {2}); "
                "the turn cannot own what it starts here".format(
                    step, process.pid, code
                )
            ),
        )

    kernel32 = _windows_kernel32()
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = (
        ctypes.c_uint32,
        ctypes.c_uint32,
    )
    kernel32.Thread32First.restype = ctypes.c_int
    kernel32.Thread32First.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    kernel32.Thread32Next.restype = ctypes.c_int
    kernel32.Thread32Next.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    kernel32.OpenThread.restype = ctypes.c_void_p
    kernel32.OpenThread.argtypes = (
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    )
    kernel32.ResumeThread.restype = ctypes.c_uint32
    kernel32.ResumeThread.argtypes = (ctypes.c_void_p,)
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise refused("CreateToolhelp32Snapshot", ctypes.get_last_error())
    entry = _windows_thread_entry()
    entry.dwSize = ctypes.sizeof(entry)
    step = None
    code = 0
    resumed = False
    more = kernel32.Thread32First(snapshot, ctypes.byref(entry))
    while more:
        if entry.th32OwnerProcessID == process.pid:
            thread = kernel32.OpenThread(
                _THREAD_SUSPEND_RESUME, False, entry.th32ThreadID
            )
            if not thread:
                step, code = "OpenThread", ctypes.get_last_error()
                break
            prior = kernel32.ResumeThread(thread)
            code = ctypes.get_last_error()
            kernel32.CloseHandle(thread)
            if prior == _RESUME_FAILED:
                step = "ResumeThread"
                break
            resumed = True
        more = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    kernel32.CloseHandle(snapshot)
    if step is not None:
        raise refused(step, code)
    if not resumed:
        # A never-run process cannot explain an absent thread by exiting.
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail=(
                "the thread snapshot named no thread of process {0} to "
                "resume; the turn cannot own what it starts here".format(
                    process.pid
                )
            ),
        )


def _windows_end_root(process: "subprocess.Popen") -> None:
    """Terminate the started root outright: the deterministic end of a
    child that never ran. Reached on the two refusals that follow a
    suspended creation; nothing beside the root can exist yet, so
    `TerminateProcess` on the child's own pid is the whole of the ending.
    Raises `CLEANUP_FAILURE` naming the step that refused."""
    import ctypes

    def refused(step, code):
        return BridgeError(
            Failure.CLEANUP_FAILURE,
            detail=(
                "{0} refused to terminate the never-run process {1} "
                "(error {2}); the turn cannot own what it starts here"
                .format(step, process.pid, code)
            ),
        )

    kernel32 = _windows_kernel32()
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    )
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, process.pid)
    if not handle:
        raise refused("OpenProcess", ctypes.get_last_error())
    terminated = kernel32.TerminateProcess(handle, 1)
    code = ctypes.get_last_error()
    kernel32.CloseHandle(handle)
    if not terminated:
        raise refused("TerminateProcess", code)


def _windows_release_job(job: int) -> None:
    """Close the job handle, which is the whole of the forced phase.

    The kernel terminates every member of a kill-on-close job when its last
    handle closes. A close that fails is the cleanup failure that gets
    reported, not something to paper over.
    """
    import ctypes

    kernel32 = _windows_kernel32()
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    if not kernel32.CloseHandle(job):
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail=(
                "closing the kill-on-close job {0:#x} failed (error {1})"
                .format(job, ctypes.get_last_error())
            ),
        )


def _group_gone(pgid: int) -> bool:
    """Is the process group empty? Signal zero asks without disturbing it.

    On Windows there is no signal-zero question to ask, so the emptiness
    answer comes from the forced tree termination, read through the job.
    """
    if WINDOWS:
        return _windows_taskkill(pgid)
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _await_group_gone(pgid: int, grace: float) -> bool:
    limit = time.monotonic() + grace
    while True:
        if _group_gone(pgid):
            return True
        if time.monotonic() >= limit:
            return False
        time.sleep(POLL_SECONDS)


def _signal_group(pgid: int, number: Optional[int]) -> None:
    """Signal exactly the group this turn started.

    On POSIX that is one signal to the group, with the escalation named
    through the `_SIGKILL` sentinel so no branch reaches for a signal
    attribute Windows CPython could not define. On Windows the polite signal
    is the console break event - a courtesy whose failure is swallowed - and
    the guarantee lives in the forced phase, the job handle's close.
    """
    if WINDOWS:
        if _CTRL_BREAK_EVENT is not None:
            try:
                os.kill(pgid, _CTRL_BREAK_EVENT)
            except OSError:
                # Courtesy only: the forced phase follows regardless.
                pass
        return
    try:
        os.killpg(pgid, number)
    except ProcessLookupError:
        return
    except OSError as exc:
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="process group {0}: {1}".format(pgid, exc),
        )


def _reap(process: "subprocess.Popen", grace: float) -> None:
    """Collect the direct child so it stops answering signals as a corpse."""
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def _close_streams(process: "subprocess.Popen") -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _cleanup_group(
    process: "subprocess.Popen", pgid: int, job: Optional[int] = None
) -> None:
    """Terminate exactly the group this turn started, and confirm it is empty.

    The child was started as its own session leader (POSIX) or with
    `CREATE_NEW_PROCESS_GROUP` (Windows), so its process id is the group
    this turn owns. Asks politely first, collects the direct child, and
    escalates against the same group only; a survivor after the escalation
    grace is `CLEANUP_FAILURE`. On Windows the forced phase is the job
    handle's close: the handle is closed first and the kernel terminates
    every member; only then does the taskkill not-found report become the
    emptiness answer, because a tree walk from a root that no longer exists
    cannot speak for the descendants it would have named. Without a job
    there is nothing honest to confirm at all, so the turn says so.
    """
    if WINDOWS:
        _signal_group(pgid, signal.SIGTERM)
        _reap(process, CLEANUP_GRACE_SECONDS)
        if job is None:
            raise BridgeError(
                Failure.CLEANUP_FAILURE,
                detail=(
                    "the process tree of {0} was never placed in a "
                    "kill-on-close job, so its descendant cleanup cannot "
                    "be confirmed".format(pgid)
                ),
            )
        _windows_release_job(job)
        _reap(process, ESCALATION_GRACE_SECONDS)
        if _await_group_gone(pgid, ESCALATION_GRACE_SECONDS):
            return
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="process tree {0} still has a member".format(pgid),
        )
    _signal_group(pgid, signal.SIGTERM)
    _reap(process, CLEANUP_GRACE_SECONDS)
    if _await_group_gone(pgid, CLEANUP_GRACE_SECONDS):
        return
    _signal_group(pgid, _SIGKILL)
    _reap(process, ESCALATION_GRACE_SECONDS)
    if _await_group_gone(pgid, ESCALATION_GRACE_SECONDS):
        return
    raise BridgeError(
        Failure.CLEANUP_FAILURE,
        detail="process group {0} still has a member".format(pgid),
    )


class PeerCall(object):
    """One started peer program, owned, whose waits never kill it.

    The body the peer reads and the streams it writes live in temporary
    files rather than pipes, so reading progress never steals bytes from
    the final result and never blocks. `wait` ends when the peer does, when
    its named bound runs out (leaving the peer running), or when somebody
    stops the run; `outcome` is the complete captured result once the peer
    has finished.
    """

    def __init__(self, process, pgid, job, watch, started, paths):
        self._process = process
        self._pgid = pgid
        self._job = job
        self._watch = watch
        self._started = started
        self.paths = paths
        self.retained = False

    @property
    def pid(self) -> int:
        return self._process.pid

    def running(self) -> bool:
        return self._process.poll() is None

    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def _read(self, index: int) -> str:
        try:
            with open(self.paths[index], "rb") as stream:
                return stream.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def output(self) -> Tuple[str, str]:
        """Everything the peer has said so far; complete once it has finished."""
        return self._read(1), self._read(2)

    def wait(
        self,
        seconds: Optional[float] = None,
        heartbeat: Optional[Callable[[float, bool], None]] = None,
    ) -> bool:
        """Wait for the peer to finish.

        True when the peer has exited. False only when a named bounded wait
        ran out first, with the peer still running and nothing cleaned up.
        A stop raises where it lands, exactly as in `run_bounded`.
        """
        limit = (
            None if seconds is None else time.monotonic() + max(0.0, float(seconds))
        )
        with self._watch.allowing():
            while True:
                if self._process.poll() is not None:
                    return True
                now = time.monotonic()
                if limit is not None and now >= limit:
                    return False
                slice_seconds = (
                    HEARTBEAT_SECONDS if heartbeat is not None else None
                )
                if limit is not None:
                    remaining = limit - now
                    slice_seconds = (
                        remaining
                        if slice_seconds is None
                        else min(slice_seconds, remaining)
                    )
                try:
                    self._process.wait(timeout=slice_seconds)
                except subprocess.TimeoutExpired:
                    pass
                if (
                    heartbeat is not None
                    and self._process.poll() is None
                    and (limit is None or time.monotonic() < limit)
                ):
                    heartbeat(self.elapsed(), self._process.poll() is None)

    def outcome(self) -> CompletedCall:
        """The return code and the complete captured output of a finished peer."""
        stdout, stderr = self.output()
        return CompletedCall(
            returncode=self._process.returncode, stdout=stdout, stderr=stderr
        )

    def retain(self) -> None:
        """Leave this peer running and its capture files in place when the
        caller returns control without stopping the work."""
        self.retained = True


def _remove_files(paths: Sequence[str]) -> None:
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


@contextlib.contextmanager
def start_call(
    argv: Sequence[str],
    cwd: str,
    env: Iterable[Tuple[str, str]],
    stdin_text: str,
    spawn_failure: Failure = Failure.MISSING_CLI,
    open_stdin: bool = False,
) -> Iterator[PeerCall]:
    """Start one peer program under this module's ownership, without a deadline.

    The spawn, group ownership and stop discipline are exactly `run_bounded`'s;
    the difference is what ends the waiting. Nothing here kills the program
    on a clock: the caller's bounded waits return control with the peer
    running, and the group is emptied only when the caller finishes with the
    call - or never, after `retain()`.
    """
    payload = stdin_text.encode("utf-8")
    paths = []  # type: List[str]
    for suffix in (".in", ".out", ".err"):
        handle, path = tempfile.mkstemp(prefix=PEER_FILE_PREFIX, suffix=suffix)
        os.close(handle)
        paths.append(path)
    process = None  # type: Optional[subprocess.Popen]
    pgid = None  # type: Optional[int]
    job = None  # type: Optional[int]
    call = None  # type: Optional[PeerCall]
    with stopped_by_signal() as watch:
        with watch.deferring():
            try:
                try:
                    with open(paths[0], "wb") as stream:
                        stream.write(payload)
                    handles = [
                        os.open(paths[0], os.O_RDONLY),
                        os.open(paths[1], os.O_WRONLY),
                        os.open(paths[2], os.O_WRONLY),
                    ]
                except OSError as exc:
                    raise BridgeError(spawn_failure, detail=str(exc))
                spawn = {
                    "stdin": subprocess.PIPE if open_stdin else handles[0],
                    "stdout": handles[1],
                    "stderr": handles[2],
                    "shell": False,
                    "close_fds": True,
                }
                if WINDOWS:
                    spawn["creationflags"] = (
                        _CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED
                    )
                else:
                    spawn["start_new_session"] = True
                try:
                    try:
                        process = subprocess.Popen(
                            list(argv),
                            cwd=cwd,
                            env=dict(env),
                            **spawn
                        )
                    except OSError as exc:
                        # The one refusal that is about the call rather than the
                        # program: the kernel would not build an argument block
                        # this large. It is the transport's failure and is named
                        # as such, not as a missing program or a failed peer.
                        if exc.errno == errno.E2BIG:
                            raise BridgeError(
                                Failure.USAGE_ERROR,
                                detail=(
                                    "the operating system refused to start {0} "
                                    "because its argument list and environment "
                                    "together were too long ({1}); send a "
                                    "shorter message, or use a peer that reads "
                                    "standard input".format(argv[0], exc)
                                ),
                            )
                        raise BridgeError(spawn_failure, detail=str(exc))
                    finally:
                        for handle in handles:
                            os.close(handle)
                except BaseException:
                    _remove_files(paths)
                    raise
                # The child was started as its own session leader (or with
                # the Windows process-group flag), so its pid is the group
                # this turn owns.
                pgid = process.pid
                if WINDOWS:
                    # Ownership is established before execution: a platform
                    # that refuses the job or the resume refuses the turn,
                    # and what was started has never run.
                    try:
                        job = _windows_own_tree(process)
                        _windows_resume_root(process)
                    except BridgeError:
                        _windows_end_root(process)
                        if job is not None:
                            _windows_release_job(job)
                        _reap(process, ESCALATION_GRACE_SECONDS)
                        pgid = None
                        job = None
                        raise
                call = PeerCall(
                    process, pgid, job, watch, time.monotonic(), tuple(paths)
                )
                yield call
            finally:
                if process is not None:
                    try:
                        if call is None or not call.retained:
                            # Still deferred, so a second stop cannot abandon
                            # this half done.
                            if pgid is not None:
                                _cleanup_group(process, pgid, job)
                    finally:
                        _close_streams(process)
                        if call is None or not call.retained:
                            _remove_files(paths)
                        else:
                            # A retained peer keeps its captured output for a
                            # person to inspect; the body file it read is the
                            # request already published in the session.
                            _remove_files(paths[:1])
        # Nothing this turn started is left, unless it was deliberately
        # retained, so a deferred stop can be raised now.
        watch.raise_if_stopped()


def run_bounded(
    argv: Sequence[str],
    cwd: str,
    env: Iterable[Tuple[str, str]],
    stdin_text: str,
    deadline: Deadline,
    spawn_failure: Failure = Failure.MISSING_CLI,
) -> CompletedCall:
    """Run one bounded program to completion, and clean up after it.

    This is the readiness shape of a call: a cheap question a connector
    asks, bounded by the deadline it was handed. Raises `TIMEOUT` when the
    deadline passes first, the given `spawn_failure` when the program could
    not be started, `CLEANUP_FAILURE` when something this turn started
    outlived it, and `SignalStop` when somebody stops Ora Agent Bridge
    while the program is running. Cleanup runs on every exit path, and on
    success too.
    """
    remaining = deadline.remaining()
    if remaining <= 0.0:
        raise BridgeError(Failure.TIMEOUT, detail=" ".join(argv[:2]))
    with start_call(argv, cwd, env, stdin_text, spawn_failure) as call:
        finished = call.wait(remaining, heartbeat=deadline.heartbeat)
        outcome = call.outcome()
    if not finished:
        raise BridgeError(
            Failure.TIMEOUT,
            detail="{0} after {1:.0f}s".format(argv[0], deadline.seconds),
        )
    return outcome
