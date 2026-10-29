"""Starting one program, waiting for one answer, and leaving nothing behind.

Everything Ora Agent Bridge starts is started here, the same way, and cleaned up
the same way. There is one function worth understanding for the programs a
turn asks questions of, `run_bounded`, and one construct for the AI work
itself, `start_call`: the first bounds a probe with a deadline because a
readiness question is not work, and the second starts a peer whose waits
never kill it, so that returning control and abandoning the work are two
different acts. The care in both is about two questions: what may be
started, and what must be gone afterwards.

**What may be started.** A fixed list of arguments, with no shell anywhere. The
outgoing Markdown goes down the program's standard input, or, for the one kind
of connector that has proved its harness has no standard-input path, arrives
bound to an option as a single final argument that the runner composed; either
way no shell ever sees it, so text a peer or a plan may have influenced never
becomes part of a command.

**What must be gone afterwards.** The child is started as its own session
leader, which makes it the leader of a brand new process group containing it and
anything it starts. That group is the exact set of processes this turn owns.
Cleanup signals that group and nothing else - never a name, never a scan of
unrelated processes, never a guess. Before signalling anything, the code
confirms that the group's number really is the child's own process id, which is
the check that makes it impossible to signal the group Ora Agent Bridge itself is
running in.

On Windows the same ownership is built with that platform's own primitives,
because the Unix ones do not exist there. The child is created with
`CREATE_NEW_PROCESS_GROUP` and `CREATE_SUSPENDED`: the first makes it the
leader of a brand new group numbered by its own process id - the direct twin
of the new session, and the fact the ownership check relies on rather than
reads back - and the second leaves its primary thread unstarted, so that not
one instruction of the child's own has run. The polite signal is the console
break event that group exists to receive, a courtesy a child may ignore and a
console-less caller cannot even send. The guarantee lives elsewhere, in
ownership that does not need the root alive to be exercised and that now
precedes execution outright: while the child is still suspended it is placed
in a Windows job object configured to kill on close, and only then is the
primary thread resumed, so everything the child ever starts - including
whatever it starts with its first instruction - is a member of that job with
no breakaway allowed, and the tree stays owned whichever of its processes
dies first. Closing this code's handle to the job is the forced phase - the
kernel terminates every member, root or descendant - and the same close is
what happens if Ora Agent Bridge itself is killed, because the operating system
closes a process's handles when it ends. The operating system's own
`taskkill /T /F` on the root pid remains as a belt-and-braces force and as
the confirmation, with one limit: its tree walk starts at the root, so once
the root has exited, not-found says nothing about the descendants it would
have named. Not-found is therefore read as emptiness only after the job has
been closed, and a platform that refuses the job or the resume refuses the
turn - what was just started has never run, so it is terminated outright
with nothing yet beside it, and the refusal is the answer rather than a tree
nobody owns. The Unix branches below are untouched by all of this, and no
Windows machine was available to exercise the Windows branches live - they
are covered by the unit checks with the Windows primitives simulated, and
that limit is stated rather than hidden.

**What ends the waiting.** For a bounded probe (`run_bounded`): the program
answers, the deadline passes, or somebody stops Ora Agent Bridge. For AI work
(`start_call`): the peer answers, or somebody stops the run - there is no
elapsed-time deadline at all, and a bounded wait that runs out merely returns
control with the peer still running, its result still collectable by the
process that owns it. All of these become exceptions or explicit returns, so
they all leave by routes whose cleanup runs. The signal handlers are put back
exactly as they were found on the way out, and a stop deferred while a
`PeerCall` is between waits is raised at the next one - deferral changes when
a stop is raised, never whether.

**When a stop raises, and when it waits.** Raising where it lands is right for
exactly one stretch of a turn - the wait for the program's answer - and wrong
for the rest of it.

It is wrong while the child is being created: between the operating system
making the process and this code knowing which group it belongs to, a stop that
raised would leave a running program that nothing had yet taken responsibility
for. It is wrong during cleanup: a second stop arriving while the group is being
emptied would abandon the emptying half done, which is the opposite of what the
person pressing the key wants.

So it is arranged the other way round from what might be expected. A stop is
deferred - written down rather than raised - for the whole life of the child,
and one window is opened, around the wait, where it raises immediately. That
window sits inside the cleanup that catches what it raises. There is therefore
no instruction anywhere between the child appearing and its group being empty at
which a stop can leave without cleanup having run. Once it has, the stop that
was written down is raised.

All three stops go through this, the keyboard interrupt included. Ctrl-C still
raises `KeyboardInterrupt` exactly as it always did, and is handled here for one
reason only: a handler of our own can be made to wait through those moments,
where Python's own cannot. Leaving it out would leave the commonest way of
stopping a program the one way that could still strand a peer.

Deferral changes when a stop is raised, never whether.

**What cannot be cleaned up, honestly.** Four things. Being killed outright with
`SIGKILL` cannot be caught by any program. A machine that loses power runs no
cleanup code. A child that deliberately puts itself into its own session has
left the group this turn owns and can no longer be reached by signalling that
group. And a turn run off the main thread has no handlers at all: Python only
ever delivers a signal to the main thread, and only the main thread may install
a handler, so a termination signal there does whatever the surrounding program
already arranged - which, by default, ends the process at once and leaves the
peer running. The turn still goes ahead in that case, because refusing to work
would be worse, but the tidy exit is not available and is not claimed.

None of the four can be controlled portably, so none of them is pretended about.
The consequence for connectors is concrete: a harness command-line program that
daemonizes during a turn puts its work beyond this cleanup and must therefore
fail qualification.

The deadline covers the useful work: prechecks, the call and the answer. Cleanup
afterwards gets its own separate bounded grace, because a deadline that has
already run out cannot be used to decide how long to wait for a process to die.

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
#: questions a connector asks before any work starts. It is not a deadline on
#: the work itself - ordinary AI work has none - and it is the only default
#: bound this module names.
READINESS_TIMEOUT_SECONDS = 120.0

#: How long a polite termination is given to empty the process group. Bounded
#: separately from the call deadline, which may already have run out.
CLEANUP_GRACE_SECONDS = 5.0

#: How long an unconditional kill is then given. After this the turn reports
#: `CLEANUP_FAILURE` rather than pretending the group is gone.
ESCALATION_GRACE_SECONDS = 5.0

#: Interval between the short looks that ask whether the group has emptied.
POLL_SECONDS = 0.02

#: How often a bounded call reports that it is still waiting, in seconds. The
#: report is a check-in, not progress: it says the caller's own wait is still
#: running and whether the child process is still alive, and nothing about
#: the model. A turn that wants no reports leaves the hook unset.
HEARTBEAT_SECONDS = 30.0

#: The prefix of the temporary files a started peer reads its body from and
#: captures its output streams into. Files rather than pipes, so a bounded
#: wait can read the progress a still-running peer has made without stealing
#: bytes from the final result, and so no read can ever block the wait.
PEER_FILE_PREFIX = "agent-bridge-peer-"

#: Whether the process-group twins below run their Windows branches, decided
#: from the operating system at import time. The unit checks simulate the
#: Windows branch by patching this and the taskkill and job twins, because no
#: Windows machine is part of the qualification.
WINDOWS = os.name == "nt"

#: The Windows creation flag that makes the child the leader of a brand-new
#: process group numbered by its own process id: the twin of
#: `start_new_session`. It does not exist on POSIX, where the flag is not
#: used and a missing constant reads as zero.
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

#: The Windows console signal that can be sent to a whole process group. It
#: exists only on Windows; where it does not, there is no courtesy signal to
#: send and the forced phase is the whole of termination.
_CTRL_BREAK_EVENT = getattr(signal, "CTRL_BREAK_EVENT", None)

#: The escalation signal, resolved once here rather than named per call,
#: because Windows CPython defines no `SIGKILL`: naming it inside a Windows
#: branch would raise `AttributeError` before any of that platform's cleanup
#: could run. On POSIX it is the real `SIGKILL` and the escalation below
#: behaves as it always did; on Windows the sentinel resolves to `None` and
#: no branch names the attribute, because there is no escalation signal to
#: send there - the forced phase is the kill-on-close job's release.
_SIGKILL = getattr(signal, "SIGKILL", None)

#: The exit status `taskkill` reports for a pid it could not find. Its tree
#: walk starts at the root, so once the root has exited the report names no
#: descendant and empties nothing: it is read as the emptiness answer only
#: where ownership that outlives the root - the kill-on-close job below - has
#: already been released.
_TASKKILL_NOT_FOUND = 128

#: The job-object information class of the extended-limits structure, one of
#: the plain numbers the Windows API passes beside the structure itself.
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

#: The one job limit this module sets. The kernel terminates every process in
#: the job when the last handle to it closes, which turns handle ownership
#: into tree ownership that survives any member's exit, the root's included.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

#: The process access the job assignment needs: the right to place the
#: process in a job, and the right the job's own termination uses.
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

#: The Windows creation flag that starts the child with its primary thread
#: suspended: none of the child's own instructions run until `ResumeThread`
#: is called on that thread, which is what makes job membership precede the
#: first moment a descendant could exist. The `subprocess` module names no
#: constant of its own for the flag - its creation-flag exports stop at
#: `CREATE_BREAKAWAY_FROM_JOB` - so the documented flag value is written here
#: directly, like the plain Windows numbers around it.
_CREATE_SUSPENDED = 0x00000004

#: The toolhelp snapshot flag that includes every thread in the system: the
#: documented way to find a process's threads from its pid alone, which is
#: how the suspended root's primary thread is reached for the resume.
_TH32CS_SNAPTHREAD = 0x00000004

#: The thread access the resume needs, and all of it: the one right
#: `ResumeThread` itself requires, asked of the primary thread and nothing
#: wider.
_THREAD_SUSPEND_RESUME = 0x0002

#: What `ResumeThread` returns when it fails: `(DWORD)-1`, unlike every
#: successful answer, which is a previous suspend count.
_RESUME_FAILED = 0xFFFFFFFF


class Deadline(object):
    """One bound for a turn's non-work steps, made once and passed down.

    `seconds` bounds the readiness questions a connector asks - version,
    sign-in, switch probes - which are not AI work and may be ended when they
    overrun. `work_seconds` names the bound on the work itself, and it is
    infinite in every turn the runner starts: no arbitrary elapsed-time
    cutoff may kill a healthy task, so a connector that can pass a native
    elapsed-time switch reads `work_seconds`, never `seconds`, to decide
    whether the child gets one at all.

    Created at the start of a `run` and handed to every waiting step, so
    prechecks and probes draw on one budget rather than each getting a fresh
    one. An explicit no-timeout run uses positive infinity; cleanup always
    keeps its separate finite grace.

    `heartbeat` is an optional no-result callback the bounded wait invokes
    while a program is still running, roughly once per `HEARTBEAT_SECONDS`.
    It receives the seconds elapsed since this deadline was made and whether
    the child process is still running. Because every bounded step already
    holds this one object, the callback reaches prerequisite probes and the
    peer call alike without either gaining a parameter. It is unset for every
    command that has not asked for check-ins.
    """

    def __init__(self, seconds: float, work_seconds: Optional[float] = None) -> None:
        self.seconds = float(seconds)
        self.work_seconds = float(
            seconds if work_seconds is None else work_seconds
        )
        self._started = time.monotonic()
        self.heartbeat = None  # type: Optional[Callable[[float, bool], None]]

    def remaining(self) -> float:
        """Seconds left; zero or less once the deadline has passed."""
        return self.seconds - (time.monotonic() - self._started)

    def elapsed(self) -> float:
        """Seconds spent since this deadline was made."""
        return time.monotonic() - self._started

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

    Raised from inside a signal handler so that a termination or a hangup
    leaves by the ordinary route - through the cleanup that terminates the
    process group and releases the session lock - instead of ending the process
    where it stands. It is deliberately not one of the internal failures:
    nothing went wrong with the turn, it was stopped.
    """

    def __init__(self, number: int) -> None:
        super().__init__(
            "Ora Agent Bridge was stopped by signal {0}, so the turn did not "
            "finish.".format(number)
        )
        self.number = number


#: The two signals a turn turns into `SignalStop`. SIGHUP does not exist on
#: Windows, where SIGTERM is the one deliverable termination signal, so the
#: pair is named without it there rather than failing to import at all.
STOP_SIGNALS: Tuple[int, ...] = (
    (signal.SIGTERM, signal.SIGHUP)
    if hasattr(signal, "SIGHUP")
    else (signal.SIGTERM,)
)

#: An interrupt from the keyboard goes on raising `KeyboardInterrupt`, exactly
#: as it always did. It is handled here all the same, and for one reason only:
#: a handler of our own can be made to wait through the two moments below,
#: where Python's cannot. Ctrl-C is how a person ordinarily stops a program, so
#: leaving it out would leave the commonest stop able to strand a peer.
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

    A stop that raises where it lands is what makes it leave through the cleanup
    around the caller instead of ending the process where it stands. That is
    right for exactly one stretch of a turn - the wait for the program's answer
    - and wrong for the rest of it, where a running child either has nothing yet
    responsible for it or is in the middle of being cleaned up.

    So the arrangement is the other way round from what it might seem. For the
    whole life of the child a stop is deferred: written down, and raised once
    the stretch it arrived in is over. `allowing()` opens the one window where
    it raises immediately, and that window sits inside the cleanup that catches
    it. There is therefore no instruction anywhere between the child appearing
    and the group being empty at which a stop can leave without cleanup running.

    Deferral changes when a stop is raised, never whether. Only the first is
    remembered, because they all mean the same thing: the turn is being stopped,
    and it leaves once.

    The state lives on the object, not in the module, so it belongs to exactly
    one `stopped_by_signal` region. Nothing can be left behind for a later turn
    to trip over.
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

        Reopening the door is what makes a stop interrupt the wait for an
        answer, which is the whole point of handling one. The block must sit
        inside a `deferring()` block whose cleanup will catch what it raises.

        A stop that arrived before the door opened is raised as it opens, rather
        than left to be noticed later. Otherwise a turn already told to stop
        would go on and wait out its whole deadline for an answer nobody is
        waiting for any more.
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

    Installing a handler is only possible on the main thread. Somewhere else it
    is impossible rather than wrong, so the block runs without them: losing a
    tidy exit is a smaller harm than refusing to do the work at all. The watch
    is still yielded in that case and simply never fires, so callers need no
    second shape of code for it.
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


class PeerTimeout(BridgeError):
    """`TIMEOUT`, carrying what the program had already said.

    A timed-out call still produced evidence: whatever the program wrote before
    the deadline, and the process id of the group that was terminated. Both are
    kept on the exception because they are the only account of what happened,
    and because confirming that nothing was orphaned means naming processes by
    their own identity rather than by what they were called.
    """

    def __init__(
        self, pid: int, stdout: str, stderr: str, detail: Optional[str] = None
    ) -> None:
        super().__init__(Failure.TIMEOUT, detail=detail)
        self.pid = pid
        self.stdout = stdout
        self.stderr = stderr


def _windows_taskkill(pgid: int) -> bool:
    """Force the owned process tree to terminate; say whether it was found.

    `taskkill /T` walks the descendant tree from the root pid and `/F`
    terminates every process it names, which is the one reliable way to reach
    grandchildren on Windows. The stdlib offers nothing that does: `os.kill`
    maps every non-console signal, zero included, to `TerminateProcess` on
    the one named process - so the usual signal-zero emptiness question would
    itself be a kill - and its console events reach only processes sharing
    this console and are ignored by any child that installed a handler. But
    the walk starts at the root: once the root has exited, the not-found
    report names no descendant and empties nothing. That report is therefore
    an emptiness answer only through the job - the caller asks after the
    job's close has terminated every member, and a tree taskkill cannot then
    find is one the kernel has already emptied. Success means something was
    found and has now been terminated, and anything else is a cleanup
    failure named here.
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
    """kernel32 through ctypes, with the last error kept for the details.

    Resolved per call rather than at import, so the import surface of a POSIX
    process is exactly what it always was.
    """
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _windows_job_information():
    """The extended-limits structure for one kill-on-close job, freshly built.

    Only the ctypes shapes of the Windows API are declared here, and the one
    fact that must be right is the byte layout `SetInformationJobObject`
    expects - which is why the declaration lives in a function any platform
    can import and the unit checks can measure. Every limit stays zero but
    the one flag: the kernel terminates the job's processes when the last
    handle to it closes.
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

    The child's primary thread has not run when this is called - the creation
    left it suspended - so the assignment happens while there is nothing yet
    beside the child for any job to miss. A process handle with exactly the
    access the assignment needs is opened on the child's own pid - nothing
    private of the child's is reached for - and the child is assigned to a
    fresh job with no name and no limits but the one. Everything the child
    later starts is a member of that job too, because a member's children are
    members and the job allows no breakaway, so the handle is ownership of a
    tree that survives the root's own exit: the kernel terminates every
    member when the last handle closes, whether that close is this code's in
    cleanup or the operating system's at the end of this process.

    Raises `CLEANUP_FAILURE` naming the step that refused, so the caller can
    end what it just started - still suspended, still childless - rather
    than run a tree nobody owns.
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

    Only the ctypes shape of `THREADENTRY32` is declared here, and the one
    fact that must be right is the byte layout `Thread32First` and
    `Thread32Next` fill - which is why the declaration lives in a function
    any platform can import and the unit checks can measure. Of the seven
    fields, two are read on the Windows path: the owning process id the
    snapshot is filtered by, and the thread id the resume is asked of.
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

    The root was created suspended, so nothing of it has executed when this
    is called and no descendant exists; the kill-on-close job has already
    been assigned, so the first instruction that runs - and every process
    it goes on to start, from its first action on - runs inside an
    ownership that is already standing. That ordering is the whole of the
    correction: there is no moment at which the child can execute, or can
    have started a descendant, outside the job.

    The primary thread is found rather than remembered: `Popen` keeps the
    process handle and closes the thread handle from creation, so the
    thread is named through the toolhelp thread snapshot, which the
    platform documents for exactly this question - enumerate the threads
    and keep those whose `th32OwnerProcessID` is the process in hand. A
    process whose primary thread has never run has created no other
    threads, so the threads found are the primary thread alone, and it is
    opened with the one access right `ResumeThread` requires.

    Raises `CLEANUP_FAILURE` naming the step that refused, so the caller
    can end what it started - still suspended, still childless - and
    refuse the turn rather than run a tree whose first thread it could
    not set going.
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
        # No thread of the process was in the snapshot at all, which a
        # never-run process cannot explain by exiting - its thread cannot
        # have run to an exit. The turn is refused rather than guessed
        # about, exactly as for a step that refused.
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
    child that never ran.

    Reached on the two refusals that follow a suspended creation - the job
    could not be established, or the root could not be resumed. In both,
    nothing of the child's code has ever executed, so no descendant exists
    and the root alone is the whole of what must end. A process handle with
    exactly the right termination needs is opened on the child's own pid,
    as the assignment's was, and `TerminateProcess` is the whole of the
    ending: a suspended process can neither resist it nor postpone it, and
    there is nothing beside the root for a tree walk to be needed for.

    Raises `CLEANUP_FAILURE` naming the step that refused rather than
    pretending a survivor is gone.
    """
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
    handle closes - this code holds the only one - reaching a descendant
    that outlived its root exactly as it reaches the root itself. A close
    that fails is the cleanup failure that gets reported, not something to
    paper over: a job left open is a tree left standing by nobody's
    decision.
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

    A process that has died but not yet been collected by its parent still
    answers, so the direct child must be collected before this answer means
    anything.

    On Windows there is no signal-zero question to ask - `os.kill` there
    terminates the named process for any non-console signal, zero included -
    so the emptiness answer comes from the forced tree termination, read
    through the job: the caller asks after the job's close has terminated
    every member, so a tree taskkill cannot find is one the kernel has
    already emptied, and a tree it finds is terminated by the asking, which
    during cleanup is what the question wanted anyway.
    """
    if WINDOWS:
        return _windows_taskkill(pgid)
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
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

    On POSIX that is one signal to the group - the polite termination or the
    `SIGKILL` escalation - with the escalation named through the `_SIGKILL`
    sentinel rather than a signal attribute Windows CPython could not
    define, so no branch reaches for a missing attribute. On Windows there
    is no escalation signal to send at all: the polite signal is the console
    break event the group was created to receive - a courtesy a child may
    ignore and a console-less caller cannot send, so its failure is
    swallowed - and the guarantee lives in the forced phase, where closing
    the kill-on-close job makes the kernel terminate every member. A group
    that is already gone is left alone, either way.
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


def _own_group(process: "subprocess.Popen") -> int:
    """The process group this turn owns, confirmed to be the child's own.

    Read once, immediately after the child has been started. The child was
    asked to become a session leader, so its group number must equal its own
    process id. If it does not, this turn does not own a group it can safely
    signal, and it says so instead of signalling anything.

    On Windows the same fact comes from the spawn flag instead of a read-back:
    `CREATE_NEW_PROCESS_GROUP` makes the child the leader of a brand-new group
    numbered by its own process id, so the flag is the confirmation and there
    is nothing to consult.
    """
    if WINDOWS:
        return process.pid
    try:
        pgid = os.getpgid(process.pid)
    except OSError as exc:
        _close_streams(process)
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="cannot read the process group of {0}: {1}".format(
                process.pid, exc
            ),
        )
    if pgid != process.pid:
        _close_streams(process)
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail=(
                "process {0} is in group {1}, which this turn does not own, "
                "so nothing was signalled".format(process.pid, pgid)
            ),
        )
    return pgid


def _cleanup_group(
    process: "subprocess.Popen", pgid: int, job: Optional[int] = None
) -> None:
    """Terminate exactly the group this turn started, and confirm it is empty.

    Asks politely first, collects the direct child so a corpse cannot be
    mistaken for a survivor, and escalates against the same group only. On
    POSIX the escalation is the `_SIGKILL` sentinel, not a named attribute,
    because Windows CPython defines no such signal; nothing about the POSIX
    sequence below changes with the Windows addition. If anything in the
    group is still there after the escalation grace, that is
    `CLEANUP_FAILURE`: an unreported survivor would be worse than a visible
    failure.

    On Windows the forced phase is the job handle's close, and the order of
    the two steps is the correction. taskkill's tree walk starts at the
    root, so a root that has already exited leaves its not-found report
    saying nothing about the descendants it would have named - exactly the
    moment a surviving orphan is on its own - and the kill-on-close job is
    the owner that is still standing in it. The handle is closed first and
    the kernel terminates every member for having been in the job; the root
    is collected again after the close so its corpse cannot be mistaken for
    a survivor; only then does the not-found report become the emptiness
    answer. Without a job there is nothing honest to confirm at all -
    unreachable while a Windows spawn refuses to run without one - so the
    turn says so instead of guessing.
    """
    if pgid != process.pid:
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="refusing to signal group {0}".format(pgid),
        )
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
    files rather than pipes, so reading the progress a still-running peer
    has made - which is the only account of what a bounded wait returned
    control from - never steals bytes from the final result and never
    blocks. `wait` ends when the peer does, when its named bound runs out
    (leaving the peer running), or when somebody stops the run; `outcome` is
    the complete captured result once the peer has finished.
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
        ran out first, with the peer still running and nothing cleaned up:
        the caller keeps the means to wait again, collect the outcome, or
        stop the run. A stop raises where it lands, exactly as in
        `run_bounded`.
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
    the difference is what ends the waiting. Nothing here kills the program on
    a clock: the caller's bounded waits return control with the peer running,
    and the group is emptied only when the caller finishes with the call -
    normally, by cancellation, or by a stop - or never, after `retain()`, which
    is the deliberate release of a peer a caller chose to leave working.
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
                pgid = _own_group(process)
                if WINDOWS:
                    # Ownership is established before execution, as in
                    # `run_bounded`; a platform that refuses the job or the
                    # resume refuses the turn, and what was started has never
                    # run.
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
                            # this half done, and the ownership checks below
                            # are the same ones `run_bounded` makes.
                            if pgid is not None:
                                _cleanup_group(process, pgid, job)
                    finally:
                        _close_streams(process)
                        if call is None or not call.retained:
                            _remove_files(paths)
                        else:
                            # A retained peer keeps its captured output for a
                            # person to inspect; the body file it read is the
                            # request already published in the session, so it
                            # goes.
                            _remove_files(paths[:1])
        # Nothing this turn started is left, unless it was deliberately
        # retained, so a stop that arrived while it was being deferred can be
        # raised now without leaving anything behind.
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

    This is the readiness shape of a call: a cheap question a connector asks,
    bounded by the deadline it was handed, because a question that will not
    answer is not work worth waiting on. The work shape is `start_call`,
    which nothing kills on a clock.

    `argv` is a fixed argument vector run without a shell. `stdin_text` is
    written to the program's standard input and the input is then closed, so a
    program that reads to end-of-file gets everything and then stops waiting.

    Raises `PeerTimeout` (a `TIMEOUT`) when the deadline passes first, the given
    `spawn_failure` when the program could not be started at all, and
    `CLEANUP_FAILURE` when something this turn started outlived it, or when
    the platform would not let this turn own what it started. Raises
    `SignalStop` when somebody terminates or hangs up Ora Agent Bridge while the
    program is running. Cleanup runs on every one of those exit paths, and on
    success too.
    """
    remaining = deadline.remaining()
    if remaining <= 0.0:
        raise BridgeError(Failure.TIMEOUT, detail=" ".join(argv[:2]))
    with start_call(argv, cwd, env, stdin_text, spawn_failure) as call:
        finished = call.wait(remaining, heartbeat=deadline.heartbeat)
        outcome = call.outcome()
        pid = call.pid
    if not finished:
        raise PeerTimeout(
            pid=pid,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            detail="{0} after {1:.0f}s".format(argv[0], deadline.seconds),
        )
    return outcome
