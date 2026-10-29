"""One courier call derived entirely from an immutable session.

The runner validates the body and connector transport before publishing the
request. It then starts one fresh target process, publishes one final textual
answer, and exits. A failed target leaves the truthful request and no invented
response. There is no retry or implicit history.

Ordinary work has no elapsed-time deadline: the turn waits until the peer
answers, fails, or somebody stops it. A caller may instead name a bounded
wait, and that wait never kills anything - when it ends the turn is returned
to the caller as a `PendingTurn` holding the still-running peer together
with the retained session evidence, and the caller collects, re-waits,
cancels, or detaches.

A Format 3 session may be a work session: the runner hands the connector's
work builder the session's declared directories and explicit attachments,
publishes the caller's inert note reference and purpose in the request header,
and marks the response with the request sequence it answers. A caller that
asked for lifecycle events gets `started`, periodic `heartbeat` check-ins, and
one `finished` event - the last only after durable publication or an honest
failure, so a broken event pipe can never make an unfinished call look
finished.

When a target's own structured output reports which model and provider
answered, that reported identity is preserved rather than discarded after
validation: the finished event carries it as optional `model` and `provider`
fields, and a Format 3 response record carries it as `Model:` and `Provider:`
header lines. Each half appears only when the tool reported it, never as an
empty placeholder, and the published response body stays the final text alone.
A connector that reads the model and effort the tool recorded for the call
(Codex, from its own session file) adds `Effort:` beside `Model:` and the
caller's requested values as `Requested-Model:` and `Requested-Effort:`
lines, so the record shows what was asked and what the tool recorded -
including the plain fact that an unreadable value was not reported - without
either half being inferred from the other. That choices report rides in the
saved reply of either session format, so a caller reading either record shape
takes the run's requested and recorded values from it; the provider-only
attribution above stays a Format 3 record line.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import shutil
import tempfile
import time
from typing import (
    Callable,
    Dict,
    Iterator,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

from . import connectors, session as session_module
from .connectors import (
    COMMAND_LINE_BODY_LIMIT,
    PeerCommand,
    argument_space_limit,
    argument_space_used,
)
from .errors import BridgeError, Failure, guidance
from .locking import session_lock
from .peer import READINESS_TIMEOUT_SECONDS, Deadline, SignalStop, start_call

NEUTRAL_PREFIX = "agent-bridge-neutral-"

# Tests may replace the production connector builder only by calling this
# function directly. No command-line, environment, file, or configuration path
# exposes this seam.
CommandBuilder = Callable[[Deadline, str], PeerCommand]
WorkCommandBuilder = Callable[..., PeerCommand]
WarningWriter = Callable[[str], None]
#: Receives one already-serialized event line, newline included.
EventWriter = Callable[[str], None]

# Failure output is useful only when it is safe to show and small enough to
# read. Remove echoed requests and common credential shapes before shortening
# each stream's excerpt and escaping control characters. The byte count still
# describes the original output.
DIAGNOSTIC_EXCERPT_BYTES = 512
_CREDENTIAL_LINE = re.compile(
    r"(?i)(?:(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"authorization|password|secret|token)[\\\"']*\s*[:=]|bearer\s+\S+|"
    r"(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,})\b)"
)


def _redact_diagnostic(text: str, body: str) -> str:
    """Suppress raw or JSON-escaped request and credential lines before clipping."""
    fragments = []
    for line in body.splitlines():
        if not line:
            continue
        for fragment in (
            line,
            json.dumps(line, ensure_ascii=True)[1:-1],
            json.dumps(line, ensure_ascii=False)[1:-1],
        ):
            if fragment not in fragments:
                fragments.append(fragment)

    redacted_lines = []
    for line in text.splitlines(keepends=True):
        ending = "\n" if line.endswith("\n") else ""
        if any(fragment in line for fragment in fragments):
            redacted_lines.append("<request body redacted>" + ending)
        elif _CREDENTIAL_LINE.search(line):
            redacted_lines.append("<credential line redacted>" + ending)
        else:
            redacted_lines.append(line)
    return "".join(redacted_lines)


def _diagnostic_excerpt(text: str, body: str) -> str:
    """Describe one stream with a bounded, escaped, redacted excerpt."""
    encoded = text.encode("utf-8")
    redacted = _redact_diagnostic(text, body).encode("utf-8")
    clipped = redacted[:DIAGNOSTIC_EXCERPT_BYTES].decode("utf-8", "replace")
    escaped = json.dumps(clipped, ensure_ascii=True)
    suffix = ", truncated" if len(redacted) > DIAGNOSTIC_EXCERPT_BYTES else ""
    return "{0} bytes{1}, excerpt={2}".format(len(encoded), suffix, escaped)


def _peer_failure_detail(call, command: PeerCommand, body: str) -> str:
    """Keep safe evidence from both streams and any structured failure."""
    parts = ["exit {0}".format(call.returncode)]
    if command.response_parser is not None and call.stdout.strip():
        try:
            command.response_parser(call.stdout)
        except BridgeError as parsed:
            if parsed.failure == Failure.PEER_FAILURE:
                parts.append(
                    "structured stdout failure: {0}".format(
                        _structured_failure_detail(parsed, command, body)
                    )
                )
    parts.extend(
        (
            "stderr: {0}".format(_diagnostic_excerpt(call.stderr, body)),
            "stdout: {0}".format(_diagnostic_excerpt(call.stdout, body)),
        )
    )
    return "; ".join(parts)


def _structured_failure_detail(error: BridgeError, command: PeerCommand, body: str) -> str:
    """Keep the selected connector's reason policy and the shared redaction."""
    detail = error.detail or str(error)
    if command.complete_failure_reason:
        return json.dumps(_redact_diagnostic(detail, body), ensure_ascii=True)
    return _diagnostic_excerpt(detail, body)


def _remove_neutral(path: str, during: Optional[BaseException]) -> None:
    try:
        shutil.rmtree(path)
    except OSError as failed:
        if during is not None:
            raise BridgeError(
                Failure.CLEANUP_FAILURE,
                detail="the neutral working directory {0} could not be removed "
                "after the command failed ({1}): {2}".format(
                    path, during, failed
                ),
            )
        raise BridgeError(
            Failure.CLEANUP_FAILURE,
            detail="the neutral working directory {0} could not be removed: "
            "{1}".format(path, failed),
        )


@contextlib.contextmanager
def _target_directory(project: Optional[str], retain: Optional[Dict[str, object]] = None) -> Iterator[str]:
    """Yield the immutable project, or a task-owned neutral empty directory.

    A caller that returns control while its peer still runs leaves the
    neutral directory in place, because the peer is working in it.
    """
    if project is not None:
        if not os.path.isdir(project):
            raise BridgeError(
                Failure.SESSION_INVALID,
                detail="the recorded project is not a directory: {0}".format(project),
            )
        yield project
        return
    try:
        neutral = tempfile.mkdtemp(prefix=NEUTRAL_PREFIX)
    except OSError as exc:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="no neutral working directory could be made: {0}".format(exc),
        )
    keep = retain is not None and bool(retain.get("retain_work"))
    try:
        yield neutral
    except BaseException as exc:
        if not keep:
            _remove_neutral(neutral, exc)
        raise
    if not keep:
        _remove_neutral(neutral, None)


def apply_transport(
    command: PeerCommand, body: str
) -> Tuple[Tuple[str, ...], str]:
    """Bind the body to standard input or one qualified final argument."""
    if command.body_argument is None:
        return command.argv, (
            command.stdin_encoder(body)
            if command.stdin_encoder is not None else body
        )
    if "\x00" in body:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="the message contains a NUL byte, which this peer's "
            "command-line transport cannot carry; remove it and send the "
            "message again",
        )
    size = len(body.encode("utf-8"))
    if size > COMMAND_LINE_BODY_LIMIT:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="the message is {0} bytes and this peer takes it on its "
            "command line, which Ora Agent Bridge caps at {1} bytes; send a "
            "shorter message".format(size, COMMAND_LINE_BODY_LIMIT),
        )
    argv = command.argv + (command.body_argument + body,)
    used = argument_space_used(argv, command.env)
    limit = argument_space_limit()
    if used > limit:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="the message, the command and this environment together "
            "come to {0} bytes, more than the {1} the operating system allows "
            "for starting a program once headroom is kept; send a shorter "
            "message or start Ora Agent Bridge with a smaller environment".format(
                used, limit
            ),
        )
    return argv, ""


class TurnResult(NamedTuple):
    request_sequence: int
    response_sequence: int
    response_path: str


class TurnEvidence(NamedTuple):
    """What a bounded wait or a failure leaves behind for a person to inspect.

    The peer-session reference is the session directory with its published
    request, plus the vendor session identity when the peer's own output has
    named one. Progress is the sanitized excerpt of what the peer had said
    when the evidence was taken - partial work, never a final answer. Fields
    that are `None` were unavailable, not empty.
    """

    session_dir: str
    request_path: Optional[str] = None
    request_on_disk: Optional[bool] = None
    peer_pid: Optional[int] = None
    peer_running: Optional[bool] = None
    peer_session: Optional[str] = None
    stdout_progress: Optional[str] = None
    stderr_progress: Optional[str] = None
    elapsed_seconds: Optional[float] = None
    capture_paths: Tuple[str, ...] = ()


#: The vendor session identity a peer prints while it works, kept generic
#: because no connector may interpret another's banner: one labelled
#: session/thread/conversation id line, nothing else.
_PEER_SESSION_LINE = re.compile(
    r"(?im)\b(?:session|thread|conversation)[\s_-]?id\b\s*[:=]\s*"
    r"([A-Za-z0-9][A-Za-z0-9._-]{7,127})"
)


def _peer_session_reference(*streams: str) -> Optional[str]:
    for stream in streams:
        found = _PEER_SESSION_LINE.search(stream)
        if found is not None:
            return found.group(1)
    return None


def _evidence(
    session_dir: str,
    request_path: Optional[str],
    body: str,
    call: Optional["object"] = None,
) -> TurnEvidence:
    on_disk = None if request_path is None else os.path.isfile(request_path)
    if call is None:
        return TurnEvidence(session_dir=session_dir, request_path=request_path,
                            request_on_disk=on_disk)
    stdout, stderr = call.output()
    return TurnEvidence(
        session_dir=session_dir,
        request_path=request_path,
        request_on_disk=on_disk,
        peer_pid=call.pid,
        peer_running=call.running(),
        peer_session=_peer_session_reference(stderr, stdout),
        stdout_progress=(
            _diagnostic_excerpt(stdout, body) if stdout.strip() else None
        ),
        stderr_progress=(
            _diagnostic_excerpt(stderr, body) if stderr.strip() else None
        ),
        elapsed_seconds=round(call.elapsed(), 3),
        capture_paths=tuple(call.paths[1:]),
    )


def _evidence_fields(evidence: TurnEvidence) -> Dict[str, object]:
    fields = {
        "session_dir": evidence.session_dir,
        "request_path": evidence.request_path,
        "request_on_disk": evidence.request_on_disk,
        "peer_pid": evidence.peer_pid,
        "peer_running": evidence.peer_running,
    }
    for name in ("peer_session", "stdout_progress", "stderr_progress",
                 "elapsed_seconds"):
        value = getattr(evidence, name)
        if value is not None:
            fields[name] = value
    if evidence.capture_paths:
        fields["capture_paths"] = list(evidence.capture_paths)
    return fields


def render_pending(evidence: TurnEvidence, wait_seconds: float) -> str:
    """The report a bounded wait leaves when it returns control."""
    lines = [
        "The {0:.0f}s wait ended while the peer was still running; the work "
        "was not stopped.".format(wait_seconds),
        "Session: {0}".format(evidence.session_dir),
    ]
    if evidence.request_path is not None:
        lines.append(
            "Request: {0} ({1})".format(
                evidence.request_path,
                "on disk" if evidence.request_on_disk else "absent",
            )
        )
    if evidence.peer_pid is not None:
        lines.append("Peer process: {0}".format(evidence.peer_pid))
    if evidence.peer_session is not None:
        lines.append("Peer session: {0}".format(evidence.peer_session))
    for label, progress in (
        ("Partial stdout", evidence.stdout_progress),
        ("Partial stderr", evidence.stderr_progress),
    ):
        if progress is not None:
            lines.append("{0}: {1}".format(label, progress))
    if evidence.capture_paths:
        lines.append(
            "Captured output (retained while the peer runs): {0}".format(
                ", ".join(evidence.capture_paths)
            )
        )
    lines.append(
        "Next action: inspect the session and the peer's own record of the "
        "run, or run the turn again when you can wait for the answer."
    )
    return "\n".join(lines)


class PendingTurn(object):
    """A turn whose bounded wait ended with its peer still running.

    The caller holds the turn: `collect` waits for the answer with no bound,
    `wait` bounds that wait again, `cancel` stops the run and cleans up the
    processes it owns, and `detach` returns control for good, leaving the
    peer running with its captured output retained. An abandoned pending
    turn is closed silently and cleans up.
    """

    def __init__(self, generator, state: Dict[str, object]) -> None:
        self._generator = generator
        self._state = state
        self._live = True

    @property
    def session_dir(self) -> str:
        return self._state["session_dir"]  # type: ignore[return-value]

    @property
    def request_path(self) -> Optional[str]:
        return self._state["request_path"]  # type: ignore[return-value]

    @property
    def peer_pid(self) -> Optional[int]:
        call = self._state.get("call")
        return call.pid if call is not None else None

    def evidence(self) -> TurnEvidence:
        return _evidence(
            self.session_dir, self.request_path,
            self._state["body"],  # type: ignore[arg-type]
            self._state.get("call"),
        )

    def collect(self) -> TurnResult:
        return self._resume(None)

    def wait(self, seconds: float):
        return self._resume(seconds)

    def cancel(self) -> TurnEvidence:
        evidence = self.evidence()
        self._state["finish"](
            "stopped",
            reason="the run was cancelled by its caller",
            evidence=evidence,
        )
        self._close()
        return evidence

    def detach(self) -> TurnEvidence:
        evidence = self.evidence()
        self._state["retain_work"] = True
        call = self._state.get("call")
        if call is not None:
            call.retain()
        self._state["finish"](
            "pending",
            reason="the bounded wait ended while the peer was still running",
            evidence=evidence,
        )
        self._close()
        return evidence

    def _resume(self, seconds: Optional[float]):
        try:
            self._generator.send(seconds)
        except StopIteration as finished:
            self._live = False
            return finished.value
        return self

    def _close(self) -> None:
        if not self._live:
            return
        self._live = False
        try:
            self._generator.close()
        except (SignalStop, KeyboardInterrupt):
            pass

    def __del__(self) -> None:
        if self._live:
            self._live = False
            try:
                self._generator.close()
            except BaseException:
                pass


class _EventReporter(object):
    """Write lifecycle events as one JSON object per line, surviving a break.

    The events are a view of the turn, not a part of it: if the pipe they are
    written to goes away, the turn runs on and its records decide what is
    true. A write that fails is remembered and nothing further is written, so
    a broken event pipe can neither kill an otherwise healthy call nor - the
    thing that matters - turn an unfinished one into a reported success.
    """

    def __init__(self, writer: Callable[[str], None]) -> None:
        self._writer = writer
        self._broken = False

    def emit(self, event: Dict[str, object]) -> None:
        if self._broken:
            return
        try:
            self._writer(json.dumps(event, ensure_ascii=True) + "\n")
        except OSError:
            self._broken = True


def _attachments_error(path: str, option: str) -> BridgeError:
    if not os.path.isabs(path):
        return BridgeError(
            Failure.USAGE_ERROR,
            detail="{0} must be an absolute path: {1}".format(option, path),
        )
    return BridgeError(
        Failure.USAGE_ERROR,
        detail="{0} is not an existing file: {1}".format(option, path),
    )


def _validate_attachments(
    attachments: Sequence[str],
) -> Tuple[str, ...]:
    """Every attachment must be an absolute existing file, checked in order."""
    checked = []
    for path in attachments:
        if not os.path.isabs(path) or not os.path.isfile(path):
            raise _attachments_error(path, "--attachment")
        checked.append(path)
    return tuple(checked)


def run_turn(
    session_dir: str,
    body: str,
    wait_seconds: Optional[float] = None,
    build_command: Optional[CommandBuilder] = None,
    warning_writer: Optional[WarningWriter] = None,
    max_steps: Optional[int] = None,
    required_model: Optional[str] = None,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    attachments: Sequence[str] = (),
    note_ref: Optional[str] = None,
    purpose: Optional[str] = None,
    event_writer: Optional[EventWriter] = None,
    build_work_command: Optional[WorkCommandBuilder] = None,
    no_timeout: bool = False,
    work_permission: Optional[str] = None,
    work_input=None,
):
    """Publish one request and one response for the session's fixed target.

    Ordinary work has no elapsed-time deadline: the turn waits until the peer
    answers, fails, or somebody stops it. `wait_seconds`, when named, is a
    bounded wait and never a kill - on expiry the call returns a `PendingTurn`
    holding the still-running peer, the published request, and the evidence
    collected so far, and the caller may keep waiting, collect the answer,
    cancel the run, or detach from it. This return contract replaces the
    `TIMEOUT` exception a caller of the old `timeout_seconds` parameter saw:
    that parameter no longer exists, so an unupdated caller fails loudly
    rather than silently changing meaning.

    `model` and `effort` are the caller's named choices for this one call.
    Each passes through only the target's own documented switch; a target
    whose connector documents no switch for a named choice is refused before
    anything is published, and no model or effort is ever chosen, completed,
    or substituted here.
    """
    try:
        wait = None if wait_seconds is None else float(wait_seconds)
    except (TypeError, ValueError):
        raise BridgeError(Failure.USAGE_ERROR, detail="--timeout must be a number")
    if wait is not None and (not math.isfinite(wait) or wait <= 0.0):
        raise BridgeError(
            Failure.USAGE_ERROR, detail="--timeout must be greater than zero"
        )
    if no_timeout and wait is not None:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--no-timeout cannot be combined with --timeout",
        )
    if not body or not body.strip():
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="there was no message to send on standard input",
        )
    generator = _turn(
        session_dir, body, wait, build_command, warning_writer, max_steps,
        required_model, model, effort, attachments, note_ref, purpose,
        event_writer, build_work_command,
        work_permission, work_input,
    )
    try:
        outcome = generator.send(None)
    except StopIteration as finished:
        return finished.value
    return PendingTurn(generator, outcome)


def _turn(
    session_dir: str,
    body: str,
    wait: Optional[float],
    build_command: Optional[CommandBuilder],
    warning_writer: Optional[WarningWriter],
    max_steps: Optional[int],
    required_model: Optional[str],
    model: Optional[str],
    effort: Optional[str],
    attachments: Sequence[str],
    note_ref: Optional[str],
    purpose: Optional[str],
    event_writer: Optional[EventWriter],
    build_work_command: Optional[WorkCommandBuilder],
    work_permission: Optional[str],
    work_input,
):
    """One whole turn as a generator: a bounded wait suspends it mid-turn."""
    record = session_module.read_session(session_dir)
    if work_input is not None and (record.peer != "codex" or record.mode != "work" or wait is not None or event_writer is None):
        raise BridgeError(Failure.USAGE_ERROR, detail="work input requires an unbounded Codex work call with lifecycle events")
    connectors.validate_work_permission(record.peer, record.mode, work_permission)
    has_minimax_options = max_steps is not None or required_model is not None
    if has_minimax_options and record.peer != "minimax":
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--max-steps and --require-model are available only for a "
            "session whose recorded target is minimax",
        )

    has_named_choice = model is not None or effort is not None
    if has_named_choice:
        # A named model or effort passes through only the target's own
        # documented switch. A target that documents none is refused here,
        # before connector transport or request publication, and nothing is
        # chosen or substituted in its place.
        choices = connectors.resolve(record.peer).CHOICE_SWITCHES
        for option, named, switch, article, kind in (
            ("--model", model, choices.model, "a model", "model"),
            ("--effort", effort, choices.effort, "an effort", "effort"),
        ):
            if named is None:
                continue
            if switch is None:
                raise BridgeError(
                    Failure.USAGE_ERROR,
                    detail="{0} is refused for {1}: this connector's fixed "
                    "vector documents no switch for naming {2}, Agent "
                    "Bridge chooses and substitutes none, and the tool "
                    "runs with its own configured {3}".format(
                        option, record.peer, article, kind
                    ),
                )
            if (
                not named
                or named != named.strip()
                or "\n" in named
                or "\r" in named
                or len(named) > 256
            ):
                raise BridgeError(
                    Failure.USAGE_ERROR,
                    detail="{0} must be one nonempty line of at most 256 "
                    "characters without surrounding whitespace".format(
                        option
                    ),
                )

    extended = bool(attachments) or note_ref is not None or purpose is not None
    if extended and record.bridge_format != session_module.FORMAT_3:
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--attachment, --note-ref, and --purpose require a Format 3 "
            "session created with an explicit --mode",
        )
    if note_ref is not None:
        session_module.validate_inline_value("--note-ref", note_ref)
    if purpose is not None:
        session_module.validate_inline_value("--purpose", purpose)
    if attachments and record.mode != "work":
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--attachment is available only in a work session whose "
            "target has a qualified image route",
        )
    declared_attachments = _validate_attachments(attachments)

    if record.mode == "work":
        connector = connectors.resolve(record.peer)
        capability = connector.CAPABILITIES
        if capability.work != "supported":
            raise BridgeError(
                Failure.USAGE_ERROR,
                detail="work mode is unsupported for {0}: {1}".format(
                    record.peer, capability.work_detail
                ),
            )
        if declared_attachments and capability.image != "supported":
            raise BridgeError(
                Failure.USAGE_ERROR,
                detail="{0} has no qualified image route: {1}".format(
                    record.peer, capability.image_detail
                ),
            )
        if has_minimax_options:
            connector.validate_run_options(max_steps, required_model)
    else:
        if record.project is not None and connectors.is_courier_only(record.peer):
            raise BridgeError(
                Failure.USAGE_ERROR,
                detail="the session records a project for {0}, which is "
                "courier-only; include the needed evidence in the body or "
                "choose a project-capable target".format(record.peer),
            )
        connector = connectors.resolve(record.peer)
        if has_minimax_options:
            connector.validate_run_options(max_steps, required_model)

    events = (
        _EventReporter(event_writer) if event_writer is not None else None
    )
    published_request = None  # type: Optional[str]
    turn_warnings = []  # type: List[str]
    # The mutable state a suspended turn shares with its PendingTurn.
    state = {
        "session_dir": session_dir,
        "request_path": None,
        "body": body,
        "call": None,
        "retain_work": False,
        "failure_evidence": None,
        "finish": None,
    }
    turn_started = time.monotonic()

    def report(event: Dict[str, object]) -> None:
        if events is not None:
            events.emit(event)

    def heartbeat(phase: str):
        def beat(elapsed: float, child_running: bool) -> None:
            report(
                {
                    "event": "heartbeat",
                    "phase": phase,
                    "elapsed_seconds": round(elapsed, 3),
                    "child_running": bool(child_running),
                }
            )

        return beat

    finish_state = {"emitted": False}

    def emit_finished(
        outcome: str,
        reason: Optional[str] = None,
        next_action: Optional[str] = None,
        evidence: Optional[TurnEvidence] = None,
    ) -> None:
        if finish_state["emitted"]:
            return
        finish_state["emitted"] = True
        event = {
            "event": "finished",
            "phase": "run",
            "outcome": outcome,
            "warnings": list(turn_warnings),
        }
        if reason is not None:
            event["reason"] = reason
            if next_action is not None:
                event["next_action"] = next_action
        if published_request is not None:
            event["request_path"] = published_request
        if evidence is not None:
            event.update(_evidence_fields(evidence))
        report(event)

    state["finish"] = emit_finished

    def finished_failure(exc: BaseException) -> None:
        evidence = state["failure_evidence"] or _evidence(
            session_dir, published_request, body
        )
        if isinstance(exc, (SignalStop, KeyboardInterrupt, GeneratorExit)):
            # GeneratorExit is an abandoned pending turn being closed: its
            # cleanup ran, so it left as a stop, not a failure.
            emit_finished(
                "stopped",
                reason=str(exc) or exc.__class__.__name__,
                evidence=evidence,
            )
            return
        if isinstance(exc, BridgeError):
            reason, next_action = guidance(exc.failure)
            if exc.detail:
                reason = "{0} ({1})".format(reason, exc.detail)
        else:
            reason = str(exc) or exc.__class__.__name__
            next_action = (
                "Inspect the session's visible state before deciding "
                "whether to run the command again."
            )
        emit_finished("failure", reason, next_action, evidence)

    try:
        report(
            {
                "event": "started",
                "phase": "run",
                "peer": record.peer,
                "mode": record.mode,
            }
        )
        with session_lock(session_dir):
            with _target_directory(record.project, state) as cwd:
                # Readiness keeps a bounded deadline of its own; the work
                # itself gets none. A named wait also bounds readiness, so a
                # hanging precheck cannot outlive the caller's patience.
                deadline = Deadline(
                    READINESS_TIMEOUT_SECONDS
                    if wait is None
                    else min(wait, READINESS_TIMEOUT_SECONDS),
                    work_seconds=math.inf,
                )
                deadline.check("composing the peer command")
                deadline.heartbeat = heartbeat("prerequisites")
                if record.mode == "work":
                    builder = (
                        build_work_command
                        if build_work_command is not None
                        else connector.build_work_command
                    )
                    work_options = {}
                    if work_permission is not None:
                        work_options["work_permission"] = work_permission
                    command = builder(
                        deadline,
                        cwd,
                        access_paths=record.access_paths,
                        attachments=declared_attachments,
                        max_steps=max_steps,
                        required_model=required_model,
                        model=model,
                        effort=effort,
                        **work_options,
                    )
                elif has_minimax_options or has_named_choice:
                    builder = build_command or connector.build_command
                    # A review builder receives only the options this call
                    # actually names: the MiniMax controls, a caller-named
                    # model or effort, or both together.
                    review_options = {}  # type: Dict[str, object]
                    if has_minimax_options:
                        review_options.update(
                            max_steps=max_steps,
                            required_model=required_model,
                        )
                    if model is not None:
                        review_options["model"] = model
                    if effort is not None:
                        review_options["effort"] = effort
                    command = builder(deadline, cwd, **review_options)
                elif build_command is None:
                    command = connector.build_command(deadline, cwd)
                else:
                    command = build_command(deadline, cwd)
                deadline.check("composing the peer command")
                if os.path.abspath(command.cwd) != os.path.abspath(cwd):
                    raise BridgeError(
                        Failure.SESSION_INVALID,
                        detail="the connector did not use the session-derived directory",
                    )
                argv, stdin_text = apply_transport(command, body)

                request_sequence = session_module.next_sequence(session_dir)
                if warning_writer is not None:
                    for warning in command.warnings:
                        warning_writer(warning)
                turn_warnings.extend(command.warnings)
                published_request = session_module.publish(
                    session_module.message_path(
                        session_dir,
                        request_sequence,
                        session_module.INITIATOR_TO_PEER_SUFFIX,
                    ),
                    session_module.initiator_to_peer_text(
                        request_sequence,
                        record.initiator,
                        record.peer,
                        body,
                        note_ref=note_ref,
                        purpose=purpose,
                        attachments=declared_attachments,
                    ),
                )
                state["request_path"] = published_request

                deadline.heartbeat = heartbeat("peer-call")
                if work_input is not None:
                    from .record import _publish_note
                    def record_input(value):
                        _publish_note(session_dir, record, "Work-Input-JSON: " + json.dumps(value, ensure_ascii=True) + "\n")
                    peer_call = connector.run_work_input(
                        command, body, work_input, report, record_input,
                        record.access_paths, declared_attachments, model, effort,
                    )
                else:
                    with start_call(
                        argv=argv,
                        cwd=command.cwd,
                        env=command.env,
                        stdin_text=stdin_text,
                    ) as call:
                        state["call"] = call
                        try:
                            next_wait = (
                                None
                                if wait is None
                                else max(
                                    0.0, wait - (time.monotonic() - turn_started)
                                )
                            )
                            while True:
                                if call.wait(next_wait, heartbeat=deadline.heartbeat):
                                    break
                                expiry = _evidence(
                                    session_dir, published_request, body, call
                                )
                                event = {"event": "wait_expired", "phase": "peer-call"}
                                event.update(_evidence_fields(expiry))
                                report(event)
                                next_wait = yield state
                            peer_call = call.outcome()
                        except BaseException:
                            state["failure_evidence"] = _evidence(
                                session_dir, published_request, body, call
                            )
                            raise
                if peer_call.returncode != 0:
                    raise BridgeError(
                        Failure.PEER_FAILURE,
                        detail=_peer_failure_detail(peer_call, command, body),
                    )
                response = peer_call.stdout
                # The caller's named choices for this one call, kept before
                # the reported identity shadows those names below, so a
                # reply that shows what the tool recorded can show what was
                # requested beside it.
                requested_model = model  # type: Optional[str]
                requested_effort = effort  # type: Optional[str]
                provider = None  # type: Optional[str]
                model = None  # type: Optional[str]
                effort = None  # type: Optional[str]
                if command.response_parser is not None:
                    try:
                        response = command.response_parser(response)
                    except BridgeError as parsed:
                        raise BridgeError(
                            parsed.failure,
                            detail=_structured_failure_detail(parsed, command, body),
                        ) from None
                    if isinstance(response, connectors.AttributedText):
                        # The tool's own structured output named which
                        # provider and model answered; keep that reported
                        # identity beside the text instead of discarding it
                        # after validation.
                        provider = response.provider
                        model = response.model
                        effort = response.effort
                if not response.strip():
                    raise BridgeError(
                        Failure.EMPTY_RESPONSE, detail=command.argv[0]
                    )

                format_three = (
                    record.bridge_format == session_module.FORMAT_3
                )
                # A choices report - today Codex's model-and-effort reading -
                # names what the tool recorded, and the same report names
                # what the caller requested, so the record shows both halves
                # without either being inferred from the other. It rides in
                # the saved reply of either format, because a caller reading
                # either record shape owns the run's requested and recorded
                # values. A tool that reports no choices keeps its reply
                # unchanged even when the caller named a choice, and a
                # provider-only attribution keeps its `Model:`/`Provider:`
                # record lines Format 3 only, so a Format 2 reply from such
                # a tool stays byte-unchanged.
                reported_choices = effort is not None
                carries_choices = format_three or reported_choices
                response_sequence = session_module.next_sequence(session_dir)
                response_path = session_module.publish(
                    session_module.message_path(
                        session_dir,
                        response_sequence,
                        session_module.PEER_TO_INITIATOR_SUFFIX,
                    ),
                    session_module.peer_to_initiator_text(
                        response_sequence,
                        record.peer,
                        record.initiator,
                        response,
                        answers=(
                            request_sequence if format_three else None
                        ),
                        model=model if carries_choices else None,
                        provider=provider if format_three else None,
                        effort=effort,
                        requested_model=(
                            requested_model
                            if reported_choices
                            else None
                        ),
                        requested_effort=(
                            requested_effort
                            if reported_choices
                            else None
                        ),
                    ),
                )
    except SignalStop as stopped:
        finished_failure(stopped)
        raise
    except KeyboardInterrupt as interrupted:
        finished_failure(interrupted)
        raise
    except BaseException as failed:
        finished_failure(failed)
        raise

    finished = {
        "event": "finished",
        "phase": "run",
        "outcome": "success",
        "request_path": published_request,
        "response_path": response_path,
        "warnings": list(turn_warnings),
    }
    if model is not None:
        finished["model"] = model
    if provider is not None:
        finished["provider"] = provider
    report(finished)
    return TurnResult(
        request_sequence=request_sequence,
        response_sequence=response_sequence,
        response_path=response_path,
    )
