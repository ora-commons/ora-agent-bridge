"""Calling Codex with its strongest practical read-only posture, and its ordinary work posture.

Codex is OpenAI's own command-line program for its coding agent. This module is
the whole of what Ora Agent Bridge knows about it: which program to start, which
switches must be on it, and how to tell - without spending a model turn -
whether starting it would work at all.

There are three operations here and nothing else. `check` answers whether Codex
could be used right now. `build_command` composes the one fixed argument vector
a review turn runs. `build_work_command` composes the ordinary configured
`codex exec` a work turn runs. All of them do the same inexpensive
prerequisites first (the work ones proving the work vector's own switches
instead of the review set, which a work vector passes none of), because a turn
that skipped them would find out about a missing sign-in or a renamed switch in
the middle of real work, with the peer already running.

**How the answer comes back.** `codex exec --json` emits structured events on
standard output. Only the last completed agent message from a successfully
completed turn is an answer; tool output, partial messages, errors, and failed
or unfinished turns are never published. Errors in those events supply the
failure reason even when the error stream is full of unrelated warnings.
The prompt travels the other way, on standard input, and the
lone `-` says so out loud: with no prompt argument Codex would read standard
input anyway, and naming it is what makes the handover deliberate rather than
incidental.

**What Codex recorded, reported back.** No vector here passes `--ephemeral`,
so Codex keeps its own session file for the call, and the `turn_context` line
of that file names the model and effort the call actually ran with. After a
successful turn the connector takes the thread id from the `thread.started`
event, reads that line from the session file for that thread - under the
`sessions` directory of the same Codex home the posture reading uses - and
attaches the recorded model and an effort beside it to the answer, so the
saved reply can show the values requested and the values Codex recorded. When
the thread was not named, or the file or the values cannot be read, the reply
says they were not reported; they are never inferred from the request.

**The switches, and why none of them is decoration.**

`--ignore-user-config` skips `$CODEX_HOME/config.toml`, including any model or
effort defaults stored only there. It does not suppress trusted-project
`.codex/config.toml` files and project hooks or rules, system configuration,
`managed_config.toml`, `requirements.toml`, cloud-delivered requirements,
macOS MDM preferences, or separately sourced user/global hooks and rules. The
fixed vector therefore overrides every compatible high-risk route it can name,
and the connector reports the surviving configuration boundary rather than
claiming that the flag removes it. Signing in survives because authentication
is read from `CODEX_HOME` rather than from that file.

`--sandbox read-only` is Codex's own enforced sandbox, not a request to be
careful. A shell may still exist inside the turn; what it cannot do is write.
It is passed by the review vector only: a work turn passes no sandbox switch at
all, because the sandbox the user configured is part of the ordinary posture a
work call exists to preserve, and a switch of Bridge's choosing - widening or
narrowing - would silently replace it. What Bridge knows about that posture it
reads from the top of the user's config alone, so it is named as the apparent
posture in a warning and never treated as the effective policy: codex resolves
and enforces the actual posture itself at run time, and a work call that
posture cannot serve fails inside codex with codex's own error.
`--skip-git-repo-check` is there because
the neutral directory a turn without a project runs in is not a Git repository,
and Codex otherwise refuses to start outside one. `--cd` names the working
root, and it is given the very directory the process is started in, so the two
cannot drift apart.

Four switches are deliberately never passed, and the reason is the same each
time: every one of them hands back something the switches above have just taken
away. Three are self-evident - `--ephemeral`,
`--dangerously-bypass-approvals-and-sandbox` and
`--dangerously-bypass-hook-trust`. The fourth, `--ignore-rules`, is the subtle
one: it drops the user's *own* execution-policy rules, so despite the sound of
it, it is a loosening.

**What readiness costs.** Nothing. Four cheap questions, no model turn among
them: where the program is, `codex --version`, `codex login status`, and
`codex exec --help`. The sign-in answer is read from the exit status rather
than from the words, because Codex prints `Logged in using ChatGPT` on the error
stream along with everything else it has to say about itself. A review turn
reads the help for the review vector's restriction switches; a work turn reads
it for the work vector's own switches only, because the review-only switches
are not a prerequisite of a call that passes none of them.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, List, Optional, Sequence, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import Deadline

#: The identifier this connector answers to, out of the six.
HARNESS_ID = "codex"

#: What this connector has actually been tested against, declared in source and
#: never inferred from the machine it is running on. `restrictions` names the
#: exact switches the vector below passes to hold the boundary: skip the main
#: user config, override compatible high-risk routes, use the enforced read-only
#: sandbox, work outside a Git repository, and name the working root.
QUALIFICATION = connectors.Qualification(
    cli_identity="codex",
    versions=("0.147.0",),
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

#: What this connector offers beyond its restricted review call. The work
#: route is the ordinary configured `codex exec` with no sandbox switch: what
#: codex itself resolves from the user's configuration and enforces at run
#: time is the actual posture, and this connector's own reading of the top of
#: the user config is reported only as the apparent posture in a warning. The
#: image route is Codex's native `-i/--image` attachment.
CAPABILITIES = connectors.Capabilities(
    work="supported",
    work_detail=(
        "work uses the ordinary configured codex exec (the user's own model, "
        "effort, and configuration) with no sandbox switch of Bridge's "
        "choosing: codex itself resolves and enforces the effective sandbox "
        "at run time. Bridge reads the apparent posture from sandbox_mode at "
        "the top of $CODEX_HOME/config.toml (codex's own documented default "
        "for exec, read-only, when nothing is set there) and reports it in a "
        "warning as apparent, not effective - a trusted project's "
        ".codex/config.toml, managed_config.toml, requirements.toml, cloud "
        "or MDM policy can change what codex enforces - and never refuses on "
        "that partial reading; a genuinely read-only effective posture fails "
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
#: which this vector passes as one `-c` override beside the others.
CHOICE_SWITCHES = connectors.ChoiceSwitches(
    model="-m",
    effort="-c model_reasoning_effort=<effort>",
)

#: The switches a work turn relies on, verified against the installed
#: program's own help before any request is published. The `exec` subcommand
#: itself is proved by the help probe that carries these switches: a program
#: without it would not answer `codex exec --help`. The review-only
#: restriction set is not a work prerequisite - the work vector passes none of
#: those switches - and neither are `--add-dir` or `--image` unless the
#: session declares an access directory or sends an attachment, because a
#: plain work turn passes neither. `-m` and `-c` join the set only when the
#: caller names a model or an effort for the call.
WORK_RESTRICTIONS = ("--skip-git-repo-check", "--cd", "--json")
WORK_ACCESS_RESTRICTIONS = ("--add-dir",)
WORK_ATTACHMENT_RESTRICTIONS = ("--image",)
WORK_MODEL_RESTRICTIONS = ("-m",)
WORK_EFFORT_RESTRICTIONS = ("-c",)

#: The sandbox postures `codex exec` accepts, and the posture its own
#: documented default runs in when nothing configures another. The default is
#: the vendor's own sentence for non-interactive mode: "By default, `codex
#: exec` runs in a read-only sandbox."
SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")
DEFAULT_SANDBOX = "read-only"
DEFAULT_SANDBOX_ORIGIN = (
    "nothing at the top of the user config names a sandbox_mode; codex's own "
    "documented default for exec is read-only"
)

#: One top-level `sandbox_mode = "..."` line in codex's config.toml. Matched
#: only before the first table header, so a `[projects."..."]` table or any
#: other section cannot be misread as the user's sandbox posture.
_SANDBOX_LINE = re.compile(
    r"""sandbox_mode\s*=\s*["']([A-Za-z0-9_-]+)["']"""
)


def _apparent_sandbox() -> Tuple[str, str]:
    """The posture the top of the user config indicates - apparent, not effective.

    A work vector passes no `--sandbox` switch, no `-c` override, and no
    `--profile`, so what codex itself resolves starts from `sandbox_mode` at
    the top of `$CODEX_HOME/config.toml` (the same file
    `--ignore-user-config` names, whose default home is `~/.codex`) and falls
    back to its own built-in default, read-only for exec. Only that one
    scalar is read here, and anything that cannot be resolved - an unreadable
    file, an unrecognized value - is returned unresolved rather than guessed
    at, because a wrong guess is how a posture comes to be silently replaced.

    The result is evidence for a warning and never a basis for refusal,
    because the reading is partial where codex's resolution is not. Verified
    on the installed program (0.155.1): a work route's profiles apply only
    through `--profile`/`-p`, which this vector never passes, and a legacy
    top-level `profile = "..."` makes codex itself refuse to run; but a
    project the user config marks trusted can carry its own
    `.codex/config.toml` that changes the posture codex enforces (observed
    flipping an empty top level from read-only to workspace-write through
    that layer alone), and `managed_config.toml`, `requirements.toml`,
    cloud or MDM policy sit outside anything Bridge reads. No
    side-effect-free authoritative source for the effective policy exists
    either: `codex doctor --json` reports no effective-posture field and
    probes the network, and `codex debug prompt-input` renders codex's own
    resolution but writes state into `CODEX_HOME` (`installation_id`,
    `shell_snapshots`, skills), so neither may run as a pre-flight against
    the user's real home. Codex therefore resolves and enforces the actual
    posture itself at run time, whatever this returns.

    Returns the apparent posture (empty when unresolved) and where it came
    from, in words, for the warning that names it.
    """
    home = os.environ.get("CODEX_HOME") or os.path.expanduser(
        os.path.join("~", ".codex")
    )
    path = os.path.join(home, "config.toml")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            lines = stream.readlines()
    except FileNotFoundError:
        return DEFAULT_SANDBOX, DEFAULT_SANDBOX_ORIGIN
    except OSError as error:
        return "", "{0} could not be read ({1})".format(path, error)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            break
        if stripped.startswith("#"):
            continue
        matched = _SANDBOX_LINE.match(stripped)
        if matched is not None:
            value = matched.group(1)
            if value in SANDBOX_MODES:
                return value, "sandbox_mode set at the top of {0}".format(path)
            return "", (
                "{0} sets a sandbox_mode this connector does not recognize "
                "({1!r})".format(path, value)
            )
    return DEFAULT_SANDBOX, DEFAULT_SANDBOX_ORIGIN


def _sandbox_limits(posture: str) -> str:
    """What the named posture confines, in the posture's own words."""
    if posture == "workspace-write":
        return (
            "Codex's workspace-write sandbox confines writes to the working "
            "directory and any declared --add-dir directory; codex exec "
            "cannot ask for an interactive approval, so a command the "
            "sandbox refuses fails rather than prompts."
        )
    if posture == "danger-full-access":
        return (
            "Under danger-full-access codex itself reports no filesystem "
            "sandboxing and network access enabled: writes are not "
            "confined. Passing no switch is what preserves that configured "
            "choice rather than silently narrowing it."
        )
    return (
        "codex exec cannot write the files authorized work needs under a "
        "read-only sandbox and has no interactive approver to escalate to, "
        "so if the posture codex actually enforces is read-only the call "
        "fails inside codex with codex's own error, which Bridge surfaces "
        "with its reason and next action; run codex in its own terminal for "
        "this work, where its approval flow can ask you directly, or set "
        "sandbox_mode in codex's own configuration to a posture that "
        "permits the authorized work."
    )


def _work_warning(posture: str, origin: str) -> str:
    """The concrete boundary of one work call under the apparent posture."""
    if posture in SANDBOX_MODES:
        return (
            "Codex work runs with the user's ordinary configuration: model, "
            "effort, hooks, MCP servers, plugins, and network settings the "
            "user or surviving policy layers configure all apply, and none "
            "of the review call's feature disables are inherited. No "
            "sandbox switch is passed: the top of the user config indicates "
            "{0} ({1}), which is the apparent posture, not the effective "
            "one - a trusted project's own .codex/config.toml, "
            "managed_config.toml, requirements.toml, cloud or MDM policy, "
            "or another codex mechanism this connector does not read can "
            "change it, and codex itself resolves and enforces the actual "
            "posture at run time. {2} External effects that configuration "
            "makes available (web search, notify, network, telemetry) "
            "remain possible and are the caller's responsibility to "
            "authorize.".format(posture, origin, _sandbox_limits(posture))
        )
    return (
        "Codex work runs with the user's ordinary configuration and no "
        "sandbox switch of Bridge's choosing, but the apparent sandbox "
        "posture could not be read: {0}. Codex's own enforced posture "
        "at run time governs; its built-in default is read-only, under "
        "which headless work cannot write, so the call may fail inside "
        "codex if nothing wider is configured, and that failure is "
        "surfaced with codex's own error. External effects that "
        "configuration makes available (web search, notify, network, "
        "telemetry) remain possible and are the caller's responsibility "
        "to authorize.".format(origin)
    )


def _codex_home() -> str:
    """The Codex home this connector already reads: `CODEX_HOME`, else `~/.codex`."""
    return os.environ.get("CODEX_HOME") or os.path.expanduser(
        os.path.join("~", ".codex")
    )


def _turn_context_choices(path: str) -> Tuple[Optional[str], Optional[str]]:
    """The model and effort of the first `turn_context` line, or nothing.

    Codex writes the values a call ran with on the `turn_context` line of the
    rollout file, either at the top level or inside its `payload` object;
    both shapes are read, and a line that names neither half contributes
    nothing. Reading stops at the first `turn_context` line found.
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
    `sessions` directory in the Codex home; current Codex nests those
    date directories year, month, and day deep, and older Codex kept
    them one level below `sessions`. The date and the timestamp are
    Codex's, not this connector's, so the sessions tree - and nothing
    outside it - is walked for a rollout whose name ends in that thread
    id, and the first one that supplies a `turn_context` line answers.
    A thread id names one call, so at most one rollout matches for real.
    Any failure to list, open, or read is left as an unreadable lookup
    for the caller to report.
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
    values come from Codex's session file for that thread alone. When the
    thread was not named, or the file or the values cannot be read, each
    unreadable half carries the plain words `not reported`, so the saved
    reply says so; no half is ever filled from the request.
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

    Unknown event and item types are harmless additions to the stream. The
    known completion and failure events still decide whether any final answer
    may be published. Only an agent message completed before the one successful
    terminal event can supply that answer, which is returned with the model
    and effort Codex recorded for the call's thread attached - or the plain
    fact that they were not reported.
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
    started = False
    completed = False
    for event in events:
        kind = event.get("type")
        if kind == "turn.completed":
            if completed:
                raise BridgeError(
                    Failure.PEER_FAILURE,
                    detail="codex returned more than one completed turn",
                )
            completed = True
        elif kind in ("turn.started", "item.started", "item.updated", "item.completed"):
            if completed:
                raise BridgeError(
                    Failure.PEER_FAILURE,
                    detail="codex returned turn activity after completion",
                )
            if kind == "turn.started":
                if started or answer is not None:
                    raise BridgeError(
                        Failure.PEER_FAILURE,
                        detail="codex returned more than one turn or a late turn start",
                    )
                started = True
            else:
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

    Five questions in order, each one cheap and none of them a model turn: is
    the program here, is its version one this connector was tested against, is
    this computer one it was tested on, is somebody signed in, and does the
    installed version still have every switch the turn relies on - the review
    set for a review turn, and for a work turn the work vector's own switches:
    the working-root and no-repository switches always, `--add-dir` when an
    access directory is declared, `--image` when an attachment travels, and
    `-m` or `-c` when the caller names a model or an effort the vector then
    passes. The review-only switches are not a work prerequisite, because a
    work vector passes none of them. Any failing question raises, so nothing
    further happens.

    Returns the three facts a readiness report needs and a turn uses: where
    the program is, which version answered, and how this computer describes itself.
    """
    warnings = []  # type: List[str]
    program = os.path.realpath(connectors.executable(QUALIFICATION.cli_identity))
    version = connectors.qualified_version(
        connectors.probe((program, "--version"), cwd, deadline).stdout,
        QUALIFICATION,
        warnings,
    )
    described = connectors.qualified_platform(QUALIFICATION, warnings)

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
            help_call,
            connectors.Qualification(
                cli_identity=QUALIFICATION.cli_identity,
                versions=QUALIFICATION.versions,
                os_family=QUALIFICATION.os_family,
                os_major_versions=QUALIFICATION.os_major_versions,
                architectures=QUALIFICATION.architectures,
                restrictions=switches,
            ),
        )
        posture, origin = _apparent_sandbox()
        warnings.append(_work_warning(posture, origin))
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

    `cwd` is a neutral directory made for this command, so the questions below
    are asked somewhere with nothing in it. No real project is touched, nothing
    is installed, nobody is logged in, no model or provider is chosen, and
    nothing is written down for next time. A work-mode check also proves the
    work vector's own switches exist on the installed version.
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
    """The fixed argument vector for one turn, prerequisites confirmed first.

    The runner calls this inside the turn's readiness bound, which is why the
    prerequisites are repeated here rather than trusted from an earlier
    readiness check: readiness may have been established days ago, or never.

    `cwd` is the directory the peer may read - the project named on the command
    line, or the neutral empty directory a turn without a project gets. It comes
    from the command line only. Nothing under a message's `## Body` heading is
    read anywhere in Ora Agent Bridge, so no text a peer or a plan wrote can name a
    directory here.

    A caller-named model or effort passes through on Codex's own documented
    switches, `-m` and one `-c model_reasoning_effort=` override, exactly as
    named. This connector chooses no model or effort itself: unnamed, the call
    runs on Codex's own configured or built-in default, and a named model the
    account rejects at use fails inside Codex with Codex's own error, which the
    runner surfaces rather than substituting anything.
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
    out here: no `--ignore-user-config`, so the user's own model, effort, and
    configuration apply exactly as an interactive Codex session would read
    them, and none of the review feature disables are inherited. No sandbox
    switch is passed either, for the same reason: the sandbox the user
    configured - or codex's own default when they have not - is part of the
    ordinary posture a work call exists to preserve, and any switch of
    Bridge's choosing would silently replace it, widening a read-only
    configuration or narrowing a full-access one. `--add-dir` still names
    each access directory, which codex makes writable under a
    workspace-confining posture, and `-i` is how an attached image reaches
    the model as real input.

    Nothing here refuses a configured work route. The posture this connector
    can read - `sandbox_mode` at the top of the user config, or codex's own
    documented default - is apparent, not effective: a trusted project's own
    `.codex/config.toml`, managed, cloud or MDM policy, or another codex
    mechanism can change what codex actually enforces, and no cheap,
    side-effect-free, authoritative reading of the effective policy exists
    in the installed program. So the builder proceeds with no posture flag
    and lets codex's own configuration resolution govern, warning about the
    apparent posture it read. A work call the effective posture cannot serve
    - a genuinely read-only sandbox, which cannot write what authorized work
    needs and has no interactive approver to escalate to - fails inside
    codex with codex's own error, which the runner surfaces with its reason
    and next action.

    A caller-named model or effort passes through on the same documented
    switches the review vector uses, `-m` and one `-c
    model_reasoning_effort=` override, exactly as named; unnamed, the user's
    ordinary configured model and effort apply, and no substitution of any
    kind is made for a named choice the account rejects at use.
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
