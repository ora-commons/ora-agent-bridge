"""The frozen Format 2 and Format 3 command line and uniform failure rendering.

`check` asks whether one fixed target is ready without spending a model turn,
prose by default or one JSON object with `--json`. `run` reads target, mode,
and directories only from its immutable session. `record` creates that session
or writes a neutral note. Substantive Markdown always arrives on standard
input; omitting every extended option keeps the original Format 2 behavior
byte for byte.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import argparse
import json
import queue
import threading
import sys
from typing import Optional, Sequence, Tuple

from . import VERSION, connectors, record as record_module, runner
from .errors import BridgeError, Failure, guidance
from .peer import READINESS_TIMEOUT_SECONDS, Deadline, SignalStop

PROGRAM = "ora-agent-bridge"


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:  # type: ignore[override]
        raise BridgeError(Failure.USAGE_ERROR, detail=message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog=PROGRAM,
        description="Send one bounded Markdown message to a supported target.",
    )
    subcommands = parser.add_subparsers(dest="command")

    check = subcommands.add_parser(
        "check", help="report whether a target can be used right now"
    )
    check.add_argument("--peer", required=True)
    check.add_argument("--mode", choices=("review", "work"))
    check.add_argument("--work-permission", choices=("full-access",))
    check.add_argument("--json", action="store_true")

    run = subcommands.add_parser(
        "run", help="perform one call for an existing session"
    )
    run.add_argument("--session", required=True)
    timing = run.add_mutually_exclusive_group()
    timing.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="return control after this many seconds of waiting, without "
             "stopping the peer; the run reports where the work continues",
    )
    timing.add_argument("--no-timeout", action="store_true",
                        help="wait until the peer answers or the caller stops "
                             "the run (the default; kept for compatibility)")
    run.add_argument("--max-steps", type=int)
    run.add_argument("--require-model")
    run.add_argument(
        "--model",
        help="name the model for this one call; it passes through only the "
             "target's own documented switch, and a target with none refuses "
             "it before publication",
    )
    run.add_argument(
        "--effort",
        help="name the reasoning effort for this one call; it passes through "
             "only the target's own documented switch, and a target with "
             "none refuses it before publication",
    )
    run.add_argument("--events-jsonl", action="store_true")
    run.add_argument("--work-input-jsonl", action="store_true")
    run.add_argument("--work-permission", choices=("full-access",),
                     help="select ZCode Full access for this work call only; allows shell execution under your account")
    run.add_argument("--attachment", action="append")
    run.add_argument("--note-ref")
    run.add_argument("--purpose")

    record = subcommands.add_parser(
        "record", help="create a session or add one neutral note"
    )
    record.add_argument("--session", required=True)
    record.add_argument("--kind", required=True)
    record.add_argument("--initiator")
    record.add_argument("--peer")
    record.add_argument("--project")
    record.add_argument("--mode", choices=("review", "work"))
    record.add_argument("--access-path", action="append")

    return parser


def _read_body() -> str:
    return sys.stdin.read()


def _write_stdout(text: str) -> None:
    """Write one piece of standard output and flush it now.

    A caller watching a run's event stream is watching a pipe, and an
    unflushed write would sit in the buffer until the process ended - which
    for a long turn is the whole turn.
    """
    sys.stdout.write(text)
    sys.stdout.flush()


def _check(args: argparse.Namespace) -> connectors.CheckResult:
    mode = args.mode or "review"
    connectors.validate_work_permission(args.peer, mode, args.work_permission)
    connector = connectors.resolve(args.peer)
    with runner._target_directory(None) as cwd:
        check_options = {"mode": mode}
        if mode == "work" and args.work_permission is not None:
            check_options["work_permission"] = args.work_permission
        return connector.check(
            Deadline(READINESS_TIMEOUT_SECONDS), cwd, **check_options
        )


def _check_json(args: argparse.Namespace) -> int:
    """One machine-readable readiness result, success or failure alike.

    The object carries the selected peer and mode, usability, this loaded
    Bridge copy's identity and version, whether the local command is present,
    the executable/version/platform facts, tri-state authentication, the
    connector's implemented review/work/image actions, the switches a
    caller-named model or effort choice would travel on, the compatibility
    of the loaded combination, and warnings. A hard failure still exits
    nonzero, with the reason and next action inside the object.
    """
    mode = args.mode or "review"
    result = {
        "peer": args.peer,
        "mode": mode,
        "bridge_version": VERSION,
        "ready": False,
        "warnings": [],
    }  # type: dict
    checked = None  # type: Optional[connectors.CheckResult]
    known_connector = True
    try:
        checked = _check(args)
        result.update(
            {
                "ready": True,
                "command_present": True,
                "executable": checked.executable,
                "version": checked.version,
                "platform": checked.platform,
                "authentication": (
                    "confirmed"
                    if checked.authentication_confirmed
                    else "unknown"
                ),
            }
        )
        for field in ("selected_model_verified", "effective_permission", "headless_shell", "shell_execution_verified"):
            value = getattr(checked, field)
            if value is not None:
                result[field] = value
    except BridgeError as error:
        reason, next_action = guidance(error.failure)
        if error.detail:
            reason = "{0} ({1})".format(reason, error.detail)
        result["reason"] = reason
        result["next_action"] = next_action
        if error.failure == Failure.AUTHENTICATION_REQUIRED:
            result["authentication"] = "required"
        known_connector = error.failure != Failure.UNKNOWN_HARNESS
        if known_connector:
            # The command was found whenever the check got past resolving the
            # program to run; only MISSING_CLI says it was not.
            result["command_present"] = error.failure != Failure.MISSING_CLI
    if known_connector:
        connector = connectors.resolve(args.peer)
        capability = connector.CAPABILITIES
        result["review"] = "supported"
        result["work"] = capability.work
        result["image"] = capability.image
        # The choices this tool documents for a caller-named model or effort,
        # gathered from the connector's declaration without a model call.
        choices = connector.CHOICE_SWITCHES
        if choices.model is not None:
            result["model_switch"] = choices.model
        if choices.effort is not None:
            result["effort_switch"] = choices.effort
        if checked is not None:
            result["warnings"] = list(checked.warnings)
        if capability.work == "unsupported":
            result["warnings"].append(capability.work_detail)
        if capability.image == "unsupported":
            result["warnings"].append(capability.image_detail)
        if mode == "work" and capability.work == "unsupported":
            result["compatibility"] = "unsupported"
        elif checked is None:
            result["compatibility"] = None
        elif checked.qualified:
            result["compatibility"] = "supported"
        else:
            result["compatibility"] = "unknown"
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
    return 0 if result["ready"] else 1


def _run(args: argparse.Namespace) -> Tuple[int, Optional[str]]:
    """One turn. On a bounded wait's expiry, exit nonzero having returned
    control without stopping the peer: the report names the session, the
    retained request, the peer's process and any session reference, the
    sanitized partial output, and where the captured output lives on."""
    event_writer = None
    if args.events_jsonl:
        event_writer = _write_stdout
    work_input = None
    if args.work_input_jsonl:
        work_input = queue.Queue()
        body = json.loads(sys.stdin.readline())["body"]
        def read_input():
            for line in sys.stdin:
                work_input.put(json.loads(line))
        threading.Thread(target=read_input, daemon=True).start()
    else:
        body = _read_body()
    result = runner.run_turn(
        session_dir=args.session,
        body=body,
        wait_seconds=args.timeout,
        warning_writer=lambda warning: sys.stderr.write(
            "Warning: {0}\n".format(warning)
        ),
        max_steps=args.max_steps,
        required_model=args.require_model,
        model=args.model,
        effort=args.effort,
        work_permission=args.work_permission,
        attachments=tuple(args.attachment or ()),
        note_ref=args.note_ref,
        purpose=args.purpose,
        event_writer=event_writer,
        work_input=work_input,
    )
    if not isinstance(result, runner.PendingTurn):
        if args.events_jsonl:
            # The finished event already carried the response path; the line a
            # flag-free run prints would only duplicate it on the event stream.
            return 0, None
        return 0, result.response_path
    if not args.events_jsonl:
        sys.stderr.write(
            runner.render_pending(result.evidence, args.timeout) + "\n"
        )
    return 1, None


def _record(args: argparse.Namespace) -> str:
    return record_module.record(
        session_dir=args.session,
        kind=args.kind,
        body=_read_body(),
        initiator=args.initiator,
        peer=args.peer,
        project=args.project,
        mode=args.mode,
        access_paths=tuple(args.access_path) if args.access_path else None,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
        if not getattr(args, "command", None):
            raise BridgeError(
                Failure.USAGE_ERROR, detail="name a command: check, run, record"
            )
        if args.command == "check":
            if args.json:
                return _check_json(args)
            checked = _check(args)
            sys.stdout.write(checked.message + "\n")
            for warning in checked.warnings:
                sys.stdout.write("Warning: {0}\n".format(warning))
            return 0
        elif args.command == "run":
            status, written = _run(args)
        else:
            written = _record(args)
            status = 0
    except SignalStop as stopped:
        sys.stderr.write(str(stopped) + "\n")
        return 1
    except BridgeError as error:
        sys.stderr.write(str(error) + "\n")
        return 1
    if written is not None:
        _write_stdout(written + "\n")
    return status
