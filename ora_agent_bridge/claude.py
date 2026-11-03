"""Calling Claude Code with its strongest practical read-only posture, and its normal work posture.

This module is the whole of what Ora Agent Bridge knows about Claude Code:
which program to start, which switches must be on it, and how to tell -
without spending a model turn - whether starting it would work at all.
`--print` with no prompt argument reads the prompt from standard input and
`--output-format text` puts the final answer, and only it, on standard
output. `--restricted` carries most of the review weight (no command-running
or web tools, settings files ignored, file tools confined to the working
directory); `--strict-mcp-config` with no `--mcp-config` leaves an empty MCP
set; `--tools Read,Glob,Grep` keeps only read-only tools; and
`--permission-mode plan` is the harness's own enforced read-only posture on
top. Five switches are deliberately never passed: the two
dangerous-permission bypasses, `--continue`/`--resume` (every call is
fresh), and `--bare` (it abandons subscription sign-in for an API key).
Claude Code works in the directory its process was started in; there is no
working-directory switch.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import json
from typing import List, Optional, Sequence, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import CompletedCall, Deadline

#: The identifier this connector answers to, out of the six.
HARNESS_ID = "claude"

#: What this connector has actually been tested against, declared in source:
#: the review route at 2.1.251 and the work route at 2.1.278.
#: `restrictions` names the exact switches the review vector passes.
QUALIFICATION = connectors.Qualification(
    cli_identity="claude",
    versions=("2.1.251", "2.1.278"),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--restricted",
        "--strict-mcp-config",
        "--tools",
        "--permission-mode",
    ),
)

#: The built-in tools a peer keeps: read a file, find files by name, search
#: inside them. One comma-separated value so the option cannot go on
#: swallowing the switches that follow it.
READ_ONLY_TOOLS = "Read,Glob,Grep"

#: What this connector offers beyond its restricted review call. Work is the
#: normal `claude --print` route with no permission flag at all; images have
#: no attachment switch on this CLI, so the one qualified route is Claude
#: Code's own file-reading tool with the path named in the request body.
CAPABILITIES = connectors.Capabilities(
    work="supported",
    work_detail=(
        "work uses the normal claude --print route with no permission flag, "
        "so the user's settings and Claude Code's ordinary default permission "
        "behavior decide what the call may do; --add-dir widens tool access "
        "to declared access directories"
    ),
    image="supported",
    image_detail=(
        "images travel by Claude Code's own file-reading tool: the request "
        "body must name the attached image's absolute path, and the model "
        "reads the pixels with it"
    ),
)

#: The switches a caller-named model or effort choice travels on; both
#: vectors pass them through unchanged when the caller names one.
CHOICE_SWITCHES = connectors.ChoiceSwitches(
    model="--model",
    effort="--effort",
)

#: The switches a work turn relies on, verified against the installed
#: program's own help before any request is published. `--add-dir` and the
#: named-choice switches join only when the call actually uses them.
WORK_RESTRICTIONS = ("--print", "--output-format")
WORK_ACCESS_RESTRICTIONS = ("--add-dir",)
WORK_MODEL_RESTRICTIONS = ("--model",)
WORK_EFFORT_RESTRICTIONS = ("--effort",)

WORK_WARNING = (
    "Claude Code work runs on its normal route: user, project, and local "
    "settings files apply (the review call's --restricted ignored them), "
    "administrator-managed and remote policy can still add hooks or other "
    "external effects, and no permission flag is passed, so the permission "
    "behavior is whatever the user's own configuration and Claude Code's "
    "default headless policy make it. In a --print call nobody can answer an "
    "interactive permission prompt, so a tool the configuration does not "
    "allow is denied rather than asked about."
)

#: The review-call boundary stated as a fixed sentence. Claude Code 2.1.251
#: exits when `--strict-mcp-config` meets the administrator-managed MCP
#: source at /Library/Application Support/ClaudeCode/managed-mcp.json; that
#: combination, when it occurs, is Claude Code's own failure to report after
#: the request is saved, not a Bridge refusal.
WARNING = (
    "Claude Code's --restricted, strict empty MCP, read-only tool, and "
    "planning posture still keeps administrator-managed endpoint and remote "
    "policy, which can add hooks or other external effects; Ora Agent Bridge "
    "does not inspect those sources per call. If policy layers make the "
    "fixed call unusable - as Claude Code 2.1.251 does when "
    "--strict-mcp-config meets a managed MCP source - Claude Code itself "
    "exits and the failure is reported after the request is saved."
)

IMAGE_ROUTE_WARNING = (
    "This attachment reaches the model only through Claude Code's "
    "file-reading tool, so the request body must name the image's absolute "
    "path; recording the attachment alone does not deliver pixels."
)


def _signed_in(status: CompletedCall) -> str:
    """Read Claude Code's own answer about who is signed in, and say it plainly.

    Being signed in is the requirement, taken from the harness's own JSON;
    how the sign-in was made is reported alongside it and decides nothing.
    An answer that cannot be read at all is treated as not being signed in:
    the one useful next action is the same.
    """
    if status.returncode != 0:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="claude auth status exited {0}".format(status.returncode),
        )
    try:
        answer = json.loads(status.stdout)
    except ValueError as exc:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="claude auth status printed no readable JSON: {0}".format(
                exc
            ),
        )
    if not isinstance(answer, dict) or answer.get("loggedIn") is not True:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="claude auth status does not report being logged in",
        )
    described = "signed in through {0} on {1}".format(
        answer.get("authMethod") or "an unnamed method",
        answer.get("apiProvider") or "an unnamed provider",
    )
    subscription = answer.get("subscriptionType")
    if subscription:
        described += " with a {0} subscription".format(subscription)
    return described


def _prerequisites(
    deadline: Deadline,
    cwd: str,
    work: bool = False,
    access_paths: Sequence[str] = (),
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> Tuple[str, str, str, str, Tuple[str, ...]]:
    """Everything that has to be true before starting Claude Code is worth doing.

    Each question is cheap and none is a model turn: is the program here,
    is the version tested, is this computer tested, is somebody signed in,
    and does the installed version still have every switch the turn relies
    on - the review set for a review turn, the work vector's own switches
    for a work turn. The surviving policy boundary is stated as a fixed
    warning, not looked up per call. Returns the four facts a readiness
    report needs and a turn uses.
    """
    warnings = []  # type: List[str]
    program = connectors.executable(QUALIFICATION.cli_identity)
    version = connectors.qualified_version(
        connectors.probe((program, "--version"), cwd, deadline).stdout,
        QUALIFICATION,
        warnings,
    )
    described = connectors.qualified_platform(QUALIFICATION, warnings)

    account = _signed_in(
        connectors.probe(
            (program, "auth", "status", "--json"), cwd, deadline
        )
    )

    help_call = connectors.probe((program, "--help"), cwd, deadline)
    choice_switches = WORK_MODEL_RESTRICTIONS if model is not None else ()
    choice_switches += WORK_EFFORT_RESTRICTIONS if effort is not None else ()
    if work:
        switches = WORK_RESTRICTIONS
        if access_paths:
            switches += WORK_ACCESS_RESTRICTIONS
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
    return (
        program,
        version,
        described,
        account,
        tuple(warnings),
    )


def check(
    deadline: Deadline, cwd: str, mode: str = "review"
) -> connectors.CheckResult:
    """Report whether Claude Code could be used right now, spending no turn.

    A work-mode check proves the work vector's own switches only.
    """
    program, version, described, account, warnings = _prerequisites(
        deadline, cwd, work=(mode == "work")
    )
    return connectors.readiness(
        HARNESS_ID, program, version, described, account, warnings
    )


def build_command(
    deadline: Deadline,
    cwd: str,
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> connectors.PeerCommand:
    """The fixed argument vector for one review turn, prerequisites confirmed first.

    `cwd` is the directory the peer may read - from the command line only -
    and, because of `--restricted`, the limit of what its file tools can
    reach. A caller-named model or effort passes through on Claude Code's
    own documented switches, exactly as named; this connector chooses
    neither itself.
    """
    program, _version, _described, _account, warnings = _prerequisites(
        deadline, cwd, model=model, effort=effort
    )
    argv = [program, "--print"]
    if model is not None:
        argv.extend(("--model", model))
    if effort is not None:
        argv.extend(("--effort", effort))
    argv.extend(
        (
            "--restricted",
            "--strict-mcp-config",
            "--tools",
            READ_ONLY_TOOLS,
            "--permission-mode",
            "plan",
            "--output-format",
            "text",
        )
    )
    return connectors.PeerCommand(
        argv=tuple(argv),
        cwd=cwd,
        env=connectors.environment(),
        warnings=warnings,
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
    """The normal `claude --print` route, with no permission flag passed.

    None of the review call's restrictions travel into a work turn; what the
    call may do is exactly what the user's own settings and Claude Code's
    ordinary default headless permission behavior allow. `--add-dir` widens
    tool access to each declared access directory. An attached image reaches
    the model through Claude Code's file-reading tool when the request body
    names its path, so that truth is warned about whenever one is attached.
    A caller-named model or effort passes through on the same documented
    switches, exactly as named.
    """
    program, _version, _described, _account, warnings = _prerequisites(
        deadline, cwd, work=True, access_paths=access_paths,
        model=model, effort=effort,
    )
    argv = [program, "--print"]
    if model is not None:
        argv.extend(("--model", model))
    if effort is not None:
        argv.extend(("--effort", effort))
    argv.extend(("--output-format", "text"))
    for path in access_paths:
        argv.extend(("--add-dir", path))
    if attachments:
        warnings = tuple(warnings) + (IMAGE_ROUTE_WARNING,)
    return connectors.PeerCommand(
        argv=tuple(argv),
        cwd=cwd,
        env=connectors.environment(),
        warnings=warnings,
    )
