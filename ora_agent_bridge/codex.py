"""Calling Codex with its strongest practical read-only posture, and its ordinary work posture.

This module is the whole of what Ora Agent Bridge knows about Codex: which
program to start, which switches must be on it, and how to tell - without
spending a model turn - whether starting it would work at all. `check`
answers readiness; `build_command` composes the review vector; and
`build_work_command` composes the ordinary configured `codex exec` a work
turn runs. The answer comes back as `codex exec --json` structured events on
standard output, where only the last completed agent message from a
successfully completed turn is an answer; the prompt travels on standard
input. No vector passes `--ephemeral`, so Codex keeps a session file whose
`turn_context` line names the model and effort the call actually ran with;
after a successful turn that recorded pair is attached to the answer.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import json
import os
from typing import Any, List, Optional, Sequence, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import Deadline

#: The identifier this connector answers to, out of the six.
HARNESS_ID = "codex"

#: What this connector has actually been tested against, declared in source:
#: the review route at 0.147.0 and the work route at 0.155.1.
#: `restrictions` names the exact switches the review vector passes.
QUALIFICATION = connectors.Qualification(
    cli_identity="codex",
    versions=("0.147.0", "0.155.1"),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--ignore-user-config",
        "-c",
        "--disable",
        "--sandbox",
        "--skip-git-repo-check",
        "--cd",
        "--json",
    ),
)

WARNING = (
    "--ignore-user-config skips only $CODEX_HOME/config.toml, including any "
    "model and effort defaults stored there; Ora Agent Bridge supplies no "
    "replacement. Trusted-project .codex/config.toml files and project hooks "
    "or rules, system configuration, managed_config.toml, requirements.toml, "
    "cloud-delivered requirements, macOS MDM preferences, and separately "
    "sourced user/global hooks or rules can still apply. Those layers can add "
    "settings the fixed vector does not override, while managed defaults or "
    "MDM can override CLI options. The fixed scalar controls disable known "
    "web search, notify, "
    "hooks, apps, plugins, Codex Apps MCP, and agent routes, but named MCP "
    "servers, plugins, hooks, network, telemetry, or other integrations from "
    "surviving configuration may still retain external-effect routes outside "
    "the read-only shell sandbox."
)

#: What this connector offers beyond its restricted review call: the ordinary
#: configured `codex exec` with no sandbox switch, and the native
#: `-i/--image` attachment route.
CAPABILITIES = connectors.Capabilities(
    work="supported",
    work_detail=(
        "work uses the ordinary configured codex exec (the user's own model, "
        "effort, and configuration) with no sandbox switch of Bridge's "
        "choosing: codex itself resolves and enforces the effective sandbox "
        "at run time, and a genuinely read-only effective posture fails "
        "inside codex with codex's own error, which Bridge surfaces with its "
        "reason and next action; --add-dir adds writable roots under a "
        "writable posture"
    ),
    image="supported",
    image_detail=(
        "images travel as native codex exec -i/--image attachments"
    ),
)

#: The switches a caller-named model or effort choice travels on: Codex
#: documents `-m/--model` and the `model_reasoning_effort` configuration key,
#: passed as one `-c` override.
CHOICE_SWITCHES = connectors.ChoiceSwitches(
    model="-m",
    effort="-c model_reasoning_effort=<effort>",
)

#: The switches a work turn relies on, verified against the installed
#: program's own help before any request is published. The `exec` subcommand
#: itself is proved by the help probe that carries these switches.
#: `--add-dir`/`--image` join only when the session declares them, and
#: `-m`/`-c` only when the caller names a model or effort.
WORK_RESTRICTIONS = ("--skip-git-repo-check", "--cd", "--json")
WORK_ACCESS_RESTRICTIONS = ("--add-dir",)
WORK_ATTACHMENT_RESTRICTIONS = ("--image",)
WORK_MODEL_RESTRICTIONS = ("-m",)
WORK_EFFORT_RESTRICTIONS = ("-c",)

#: The work-call boundary stated as a fixed sentence. The posture the user
#: configured - or codex's own documented read-only default for exec - is
#: not read per call: it is apparent, not effective (a trusted project's
#: own .codex/config.toml, managed_config.toml, requirements.toml, cloud or
#: MDM policy can change what codex enforces, and no side-effect-free
#: authoritative source for the effective policy exists in the installed
#: program), so codex's own run-time resolution and enforcement govern.
WORK_WARNING = (
    "Codex work runs with the user's ordinary configuration: model, effort, "
    "hooks, MCP servers, plugins, and network settings the user or "
    "surviving policy layers configure all apply, and none of the review "
    "call's feature disables are inherited. No sandbox switch is passed; "
    "codex itself resolves and enforces the effective sandbox at run time "
    "from configuration this connector does not read per call. codex exec "
    "cannot ask for an interactive approval, so a call the enforced posture "
    "cannot serve - a genuinely read-only sandbox, under which headless "
    "work cannot write - fails inside codex with codex's own error, which "
    "Bridge surfaces with its reason and next action; run codex in its own "
    "terminal for work its approval flow must ask about, or set "
    "sandbox_mode in codex's own configuration to a posture that permits "
    "the authorized work. External effects that configuration makes "
    "available (web search, notify, network, telemetry) remain possible "
    "and are the caller's responsibility to authorize."
)


def _codex_home() -> str:
    """The Codex home this connector already reads: `CODEX_HOME`, else `~/.codex`."""
    return os.environ.get("CODEX_HOME") or os.path.expanduser(
        os.path.join("~", ".codex")
    )


def _turn_context_choices(path: str) -> Tuple[Optional[str], Optional[str]]:
    """The model and effort of the first `turn_context` line, or nothing.

    Codex writes the values a call ran with on the `turn_context` line of the
    rollout file, either at the top level or inside its `payload` object.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if (
                    not isinstance(event, dict)
                    or event.get("type") != "turn_context"
                ):
                    continue
                model = None  # type: Optional[str]
                effort = None  # type: Optional[str]
                for holder in (event, event.get("payload")):
                    if not isinstance(holder, dict):
                        continue
                    for name in ("model", "effort"):
                        value = holder.get(name)
                        if not isinstance(value, str) or not value:
                            continue
                        if name == "model":
                            model = model or value
                        else:
                            effort = effort or value
                return model, effort
    except OSError:
        pass
    return None, None


def _recorded_choices(thread_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Read the model and effort Codex itself recorded for one thread.

    Codex keeps one rollout file per call, named
    `rollout-<time>-<thread id>.jsonl` under date directories of the
    `sessions` directory in the Codex home. The sessions tree - and nothing
    outside it - is walked for a rollout whose name ends in that thread id;
    a thread id names one call, so at most one rollout matches for real.
    """
    sessions = os.path.join(_codex_home(), "sessions")
    suffix = "-" + thread_id + ".jsonl"
    for directory, subdirectories, names in os.walk(sessions):
        subdirectories.sort()
        for name in sorted(names):
            if not (name.startswith("rollout-") and name.endswith(suffix)):
                continue
            model, effort = _turn_context_choices(
                os.path.join(directory, name)
            )
            if model is not None or effort is not None:
                return model, effort
    return None, None


def _attributed(answer: str, events: Sequence[Any]) -> str:
    """The final text with the model and effort Codex itself recorded.

    The thread id comes from the `thread.started` event of the call's own
    event stream - never from anything the caller sent - and the recorded
    values come from Codex's session file for that thread alone. An
    unreadable half carries the plain words `not reported`; no half is ever
    filled from the request.
    """
    thread_id = None  # type: Optional[str]
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "thread.started":
            continue
        named = event.get("thread_id")
        if isinstance(named, str) and named:
            thread_id = named
        break
    model = None  # type: Optional[str]
    effort = None  # type: Optional[str]
    if thread_id is not None:
        model, effort = _recorded_choices(thread_id)
    return connectors.AttributedText(
        answer,
        model=model if model is not None else connectors.NOT_REPORTED,
        effort=effort if effort is not None else connectors.NOT_REPORTED,
    )


def parse_response(output: str) -> str:
    """Read one Codex JSONL turn without treating intermediate work as a reply.

    Unknown event and item types are harmless. A failed turn is refused, a
    completed turn is required, and only the last completed agent message
    before the one successful terminal event is the answer, returned with
    the model and effort Codex recorded for the call's thread attached.
    """
    try:
        events = [json.loads(line) for line in output.split("\n") if line.strip()]
    except ValueError:
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="codex returned unreadable JSON events",
        ) from None
    if not events or any(not isinstance(event, dict) for event in events):
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="codex returned no readable JSON event sequence",
        )

    failures = []  # type: List[str]
    turn_failed = False
    for event in events:
        if event.get("type") not in ("turn.failed", "error"):
            continue
        turn_failed = turn_failed or event.get("type") == "turn.failed"
        error = event.get("error")
        if isinstance(error, dict):
            message = error.get("message")
        elif isinstance(error, str):
            message = error
        else:
            message = event.get("message")
        detail = message if isinstance(message, str) and message.strip() else (
            "codex reported {0} without a readable reason".format(event["type"])
        )
        if detail not in failures:
            failures.append(detail)
    if turn_failed:
        # Preserve the complete vendor reason. The runner removes request and
        # credential lines after JSON decoding, before showing this detail.
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="codex reported failure: " + "\n".join(failures),
        )

    answer = None  # type: Optional[str]
    completed = False
    for event in events:
        kind = event.get("type")
        if kind == "turn.completed":
            completed = True
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                # A later partial agent message invalidates an earlier
                # completed commentary message as the final answer.
                answer = item.get("text") if kind == "item.completed" else None
    if completed and isinstance(answer, str) and answer.strip():
        # Codex also emits top-level error notices for recoverable retries,
        # without exposing will_retry in JSONL. The completed turn and the
        # runner's zero-exit check, not an earlier notice, establish success.
        return _attributed(answer, events)
    detail = (
        "codex did not report a completed turn; no answer was published"
        if not completed else "codex completed without a final agent message"
    )
    if failures:
        detail += "; codex reported: " + "\n".join(failures)
    raise BridgeError(Failure.PEER_FAILURE, detail=detail)


def _prerequisites(
    deadline: Deadline,
    cwd: str,
    work: bool = False,
    access_paths: Sequence[str] = (),
    attachments: Sequence[str] = (),
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> Tuple[str, str, str, Tuple[str, ...]]:
    """Everything that has to be true before starting Codex is worth doing.

    Five cheap questions, none a model turn: is the program here, is its
    version one this connector was tested against, is this computer one it
    was tested on, is somebody signed in, and does the installed version
    still have every switch the turn relies on - the review set for a review
    turn, and the work vector's own switches for a work turn. Returns the
    three facts a readiness report needs and a turn uses.
    """
    warnings = []  # type: List[str]
    program = os.path.realpath(connectors.executable(QUALIFICATION.cli_identity))
    version = connectors.qualified_version(
        connectors.probe((program, "--version"), cwd, deadline).stdout,
        QUALIFICATION,
        warnings,
    )
    described = connectors.qualified_platform(QUALIFICATION, warnings)

    # The sign-in answer is read from the exit status rather than the words,
    # because Codex prints `Logged in using ChatGPT` on the error stream.
    signed_in = connectors.probe((program, "login", "status"), cwd, deadline)
    if signed_in.returncode != 0:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="codex login status exited {0}".format(
                signed_in.returncode
            ),
        )

    help_call = connectors.probe((program, "exec", "--help"), cwd, deadline)
    choice_switches = WORK_MODEL_RESTRICTIONS if model is not None else ()
    choice_switches += WORK_EFFORT_RESTRICTIONS if effort is not None else ()
    if work:
        switches = WORK_RESTRICTIONS
        if access_paths:
            switches += WORK_ACCESS_RESTRICTIONS
        if attachments:
            switches += WORK_ATTACHMENT_RESTRICTIONS
        switches += choice_switches
        connectors.qualified_restrictions(
            help_call, QUALIFICATION._replace(restrictions=switches)
        )
        warnings.append(WORK_WARNING)
    else:
        connectors.qualified_restrictions(
            help_call,
            QUALIFICATION._replace(
                restrictions=QUALIFICATION.restrictions + choice_switches
            ),
        )
        warnings.append(WARNING)
    return program, version, described, tuple(warnings)


def check(
    deadline: Deadline, cwd: str, mode: str = "review"
) -> connectors.CheckResult:
    """Report whether Codex could be used right now, spending no model turn.

    `cwd` is a neutral directory made for this command; no real project is
    touched and nothing is written down for next time. A work-mode check
    also proves the work vector's own switches exist on the installed
    version.
    """
    program, version, described, warnings = _prerequisites(
        deadline, cwd, work=(mode == "work")
    )
    checked = connectors.readiness(
        HARNESS_ID, program, version, described, "signed in", warnings
    )
    return checked._replace(
        message=checked.message.replace("codex is ready", "codex transport is ready", 1)
        + " Selected model acceptance is unverified; no model call was made.",
        selected_model_verified=False,
    )


def build_command(
    deadline: Deadline,
    cwd: str,
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> connectors.PeerCommand:
    """The fixed argument vector for one review turn, prerequisites confirmed first.

    `cwd` is the directory the peer may read, from the command line only;
    nothing under a message's `## Body` heading is read anywhere in Ora
    Agent Bridge. A caller-named model or effort passes through on Codex's
    own documented switches exactly as named; this connector chooses none
    itself, and a named model the account rejects at use fails inside Codex
    with Codex's own error.
    """
    program, _version, _described, warnings = _prerequisites(
        deadline, cwd, model=model, effort=effort
    )
    argv = [program, "exec", "--json"]
    if model is not None:
        argv.extend(("-m", model))
    if effort is not None:
        argv.extend(("-c", "model_reasoning_effort=" + effort))
    argv.extend(
        (
            "--ignore-user-config",
            "-c",
            "web_search=disabled",
            "-c",
            "notify=[]",
            "--disable",
            "hooks",
            "--disable",
            "apps",
            "--disable",
            "plugins",
            "-c",
            "orchestrator.mcp.enabled=false",
            "-c",
            "agents.enabled=false",
            "--disable",
            "multi_agent_v2",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--cd",
            cwd,
            "-",
        )
    )
    return connectors.PeerCommand(
        argv=tuple(argv),
        cwd=cwd,
        env=connectors.environment(),
        warnings=warnings,
        response_parser=parse_response,
        complete_failure_reason=True,
    )


def build_work_command(
    deadline: Deadline,
    cwd: str,
    access_paths: Sequence[str] = (),
    attachments: Sequence[str] = (),
    max_steps: Optional[int] = None,
    required_model: Optional[str] = None,
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> connectors.PeerCommand:
    """The ordinary configured `codex exec`, under the user's own sandbox.

    Everything the review vector passes to keep the target read-only is left
    out here: no `--ignore-user-config`, no review feature disables, and no
    sandbox switch - the sandbox the user configured (or codex's own default)
    is part of the ordinary posture a work call exists to preserve, and any
    switch of Bridge's choosing would silently replace it. `--add-dir` names
    each access directory and `-i` carries each attached image. The builder
    proceeds with no posture flag and lets codex's own configuration
    resolution govern; a call the effective posture cannot serve fails
    inside codex with codex's own error. A caller-named model or effort
    passes through on the same documented switches the review vector uses,
    exactly as named.
    """
    program, _version, _described, warnings = _prerequisites(
        deadline, cwd, work=True, access_paths=access_paths,
        attachments=attachments, model=model, effort=effort,
    )
    argv = [
        program,
        "exec",
        "--json",
    ]
    if model is not None:
        argv.extend(("-m", model))
    if effort is not None:
        argv.extend(("-c", "model_reasoning_effort=" + effort))
    argv.extend(
        (
            "--skip-git-repo-check",
            "--cd",
            cwd,
        )
    )
    for path in access_paths:
        argv.extend(("--add-dir", path))
    for attachment in attachments:
        argv.extend(("--image", attachment))
    argv.append("-")
    return connectors.PeerCommand(
        argv=tuple(argv),
        cwd=cwd,
        env=connectors.environment(),
        warnings=warnings,
        response_parser=parse_response,
        complete_failure_reason=True,
    )


def run_work_input(command, body, controls, report, record_input, access_paths, attachments, model, effort):
    """One owned native turn; steer only its matching active turn, never queue."""
    import queue
    from .peer import start_call, CompletedCall, HEARTBEAT_SECONDS

    argv = [command.argv[0], "app-server"]
    with start_call(argv, command.cwd, command.env, "", open_stdin=True) as call:
        def send(identifier, method, params):
            message = {"method": method, "params": params}
            if identifier is not None:
                message["id"] = identifier
            call._process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            call._process.stdin.flush()

        pending, events = {}, []
        thread, turn, answer, completed = None, None, None, False
        fragment, next_id, next_heartbeat = b"", 4, HEARTBEAT_SECONDS
        output_stream = open(call.paths[1], "rb")
        send(1, "initialize", {"clientInfo": {"name": "ora_agent_bridge", "version": "1"}})
        def acknowledge(control, status, reason=""):
            value = {**control, "status": status, "reason": reason}
            record_input(value)
            report({"event": "work_input", **value})

        try:
            while True:
                fragment += output_stream.read()
                lines = fragment.split(b"\n")
                fragment = lines.pop()
                for line in lines:
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    identifier, params = event.get("id"), event.get("params", {})
                    result, error = event.get("result", {}), event.get("error")
                    if identifier in pending:
                        control = pending.pop(identifier)
                        accepted = not error and result.get("turnId") == control["turn_id"]
                        acknowledge(control, "accepted" if accepted else "refused",
                                    error.get("message", "Codex refused the message.") if error else "" if accepted else "Codex did not acknowledge the expected turn.")
                    elif identifier in (1, 2, 3) and error:
                        raise BridgeError(Failure.PEER_FAILURE, detail=error.get("message", str(error)))
                    elif identifier == 1:
                        send(None, "initialized", {})
                        options = {"cwd": command.cwd}
                        if model is not None:
                            options["model"] = model
                        send(2, "thread/start", options)
                    elif identifier == 2:
                        thread = result["thread"]["id"]
                        events.append({"type": "thread.started", "thread_id": thread})
                        options = {"threadId": thread, "input": [{"type": "text", "text": body}] +
                                   [{"type": "localImage", "path": path} for path in attachments]}
                        # Match exec: ordinary approvals cannot prompt; configured
                        # native auto-review keeps its own approval policy.
                        if result.get("approvalsReviewer") != "auto_review":
                            options["approvalPolicy"] = "never"
                        if effort is not None:
                            options["effort"] = effort
                        policy = result.get("sandbox", {})
                        if access_paths and policy.get("type") == "workspaceWrite":
                            options["sandboxPolicy"] = {**policy, "writableRoots": list(dict.fromkeys(policy.get("writableRoots", []) + list(access_paths)))}
                        send(3, "turn/start", options)
                    elif identifier == 3:
                        turn = result["turn"]["id"]
                        report({"event": "work_input_ready", "thread_id": thread, "turn_id": turn})
                    elif "method" in event and identifier is not None:
                        # Like exec, this foreground route has no interactive approver.
                        call._process.stdin.write((json.dumps({"id": identifier, "error": {"code": -32601, "message": "No interactive approval connection; use Codex in its terminal."}}) + "\n").encode("utf-8"))
                        call._process.stdin.flush()
                    elif params.get("threadId") == thread and params.get("turnId", params.get("turn", {}).get("id")) == turn:
                        if event.get("method") == "item/completed" and params.get("item", {}).get("type") == "agentMessage":
                            answer = params["item"].get("text")
                        elif event.get("method") == "turn/completed":
                            completed = True
                            report({"event": "work_input_closed"})
                            if params["turn"].get("status") != "completed":
                                raise BridgeError(Failure.PEER_FAILURE, detail=str(params["turn"].get("error") or params["turn"]["status"]))
                while True:
                    try:
                        control = controls.get_nowait()
                    except queue.Empty:
                        break
                    if not isinstance(control, dict) or not isinstance(control.get("text"), str) or not control["text"].strip():
                        raise BridgeError(Failure.USAGE_ERROR, detail="work input needs a nonempty text message")
                    if completed or not turn or control.get("thread_id") != thread or control.get("turn_id") != turn:
                        acknowledge(control, "refused", "The originating Codex turn is no longer active.")
                        continue
                    record_input({**control, "status": "pending"})
                    pending[next_id] = control
                    send(next_id, "turn/steer", {"threadId": thread, "expectedTurnId": turn,
                                               "input": [{"type": "text", "text": control["text"]}]})
                    next_id += 1
                if call.elapsed() >= next_heartbeat:
                    report({"event": "heartbeat", "phase": "peer-call", "elapsed_seconds": call.elapsed(), "child_running": call.running()})
                    next_heartbeat = call.elapsed() + HEARTBEAT_SECONDS
                if completed:
                    for control in pending.values():
                        acknowledge(control, "unknown", "Codex finished before delivery was acknowledged; inspect the retained turn before resending.")
                    pending.clear()
                    if not isinstance(answer, str) or not answer.strip():
                        raise BridgeError(Failure.PEER_FAILURE, detail="Codex completed without a final agent message")
                    events += [{"type": "item.completed", "item": {"type": "agent_message", "text": answer}}, {"type": "turn.completed"}]
                    return CompletedCall(0, "\n".join(json.dumps(item) for item in events), "")
                if call.wait(0.05):
                    raise BridgeError(Failure.PEER_FAILURE, detail="Codex app-server ended before the turn completed: " + call.output()[1])
        finally:
            output_stream.close()
            for control in pending.values():
                acknowledge(control, "unknown", "The connection ended before Codex acknowledged delivery; inspect the retained turn before resending.")
