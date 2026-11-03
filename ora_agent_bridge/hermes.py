"""Calling Hermes Agent with its strongest practical courier posture.

Hermes Agent is Nous Research's command-line coding agent. Two vendor
findings shape this connector, both established by probing 0.18.2 and
recorded in full in INTERFACE.md: its file toolset cannot separate reads
from writes, so it is courier-only (no project, no file tool), and it has
no standard-input path for a one-shot prompt, so the body travels as
`--oneshot=<body>`. A child Hermes also reads `TERMINAL_CWD` in place of
its own working directory, so that one name is dropped from the child
environment. `--toolsets todo` is the whole restriction surface,
`--safe-mode` sheds plugins, MCP servers and shell hooks while leaving the
configured provider alone, and readiness requires a signed-in Nous Portal
that the configuration actually selects - this connector declines to run
through anything but the subscription.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

from typing import List, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import CompletedCall, Deadline

#: The identifier this connector answers to, out of the six.
HARNESS_ID = "hermes"

#: What this connector has actually been tested against, declared in source.
#: `restrictions` names the exact switches the vector below passes.
QUALIFICATION = connectors.Qualification(
    cli_identity="hermes",
    versions=("0.18.2",),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--oneshot",
        "--toolsets",
        "--safe-mode",
    ),
)

#: The one toolset a peer keeps: a planner that touches nothing.
TOOLSET = "todo"

#: How the body is attached: as the one-shot option's own value, which
#: Hermes's parser splits at the first `=`, so a body beginning with a
#: hyphen is still the body.
BODY_ARGUMENT = "--oneshot="

#: The one environment name dropped before the child is started, because a
#: child Hermes reads it in place of its own working directory.
DROPPED_FROM_ENVIRONMENT = ("TERMINAL_CWD",)

#: What `hermes portal info` says when the Portal is signed in, and when the
#: configuration selects it as the inference provider. Both are required.
SIGNED_IN_LINE = "logged in"
NOT_SIGNED_IN_LINE = "not logged in"
PORTAL_SELECTED_LINE = "using Nous as inference provider"

WARNING = (
    "Hermes is courier-only because its file toolset cannot separate reads "
    "from writes, so it receives only a neutral directory and no file tool. "
    "--safe-mode skips plugins, MCP servers, and shell hooks, but user memory "
    "can remain. The complete message is carried in one --oneshot argument "
    "and may be visible to other processes under the same account or to logs."
)

#: What this connector offers beyond its restricted review call: nothing,
#: and for a stated reason read from the installed 0.21.3 help ("approvals
#: are auto-bypassed" on the one-shot route) rather than untested.
CAPABILITIES = connectors.Capabilities(
    work="unsupported",
    work_detail=(
        "Hermes Agent's one-shot -z route auto-bypasses approvals by design "
        "(its own help: \"approvals are auto-bypassed\"), no other "
        "non-interactive route offers ordinary approvals, and Bridge will "
        "not select a bypassing mode; run hermes in its own terminal for "
        "interactive work, or choose a work-capable target"
    ),
    image="unsupported",
    image_detail=(
        "Hermes Agent has no image attachment switch and its one-shot body "
        "travels as command-line text, so no qualified image route exists"
    ),
)

#: The switches a caller-named model or effort choice would travel on: none.
#: This connector's courier route is qualified without any model or provider
#: selection, and it declines to run through anything but the subscription.
CHOICE_SWITCHES = connectors.ChoiceSwitches()


def _environment() -> Tuple[Tuple[str, str], ...]:
    """This process's environment with the one redirecting name taken out."""
    return tuple(
        (name, value)
        for name, value in connectors.environment()
        if name not in DROPPED_FROM_ENVIRONMENT
    )


def _signed_in(status: CompletedCall) -> str:
    """Read Hermes's own answer about the Portal, and say it plainly.

    Two things have to be true and both are on the one page: somebody is
    signed in to the Nous Portal, and the configuration names it as the
    provider a turn would use (Hermes prefers other API keys when the
    configuration is silent).
    """
    if status.returncode != 0:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="hermes portal info exited {0}".format(status.returncode),
        )
    text = status.stdout
    if NOT_SIGNED_IN_LINE in text or SIGNED_IN_LINE not in text:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="hermes portal info does not report being logged in to "
            "the Nous Portal",
        )
    if PORTAL_SELECTED_LINE not in text:
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="hermes is signed in to the Nous Portal but its "
            "configuration selects another inference provider; Ora Agent Bridge "
            "runs a peer only on its subscription",
        )
    return "signed in to the Nous Portal, which is the configured provider"


def _prerequisites(
    deadline: Deadline, cwd: str, work: bool = False
) -> Tuple[str, str, str, str, Tuple[str, ...]]:
    """Everything that has to be true before starting Hermes is worth doing.

    Four questions, each one cheap and none a model turn: is the program
    here, is its version tested, is this computer tested, and is the
    subscription signed in and selected. A review turn then asks a fifth -
    does the installed version still have every review switch - which a
    work-mode check does not ask at all, because work is unsupported for
    reasons recorded in `CAPABILITIES`.

    Returns the four facts a readiness report needs and a turn uses.
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
        connectors.probe((program, "portal", "info"), cwd, deadline)
    )

    if not work:
        connectors.qualified_restrictions(
            connectors.probe((program, "--help"), cwd, deadline),
            QUALIFICATION,
        )
    warnings.append(WARNING)
    return program, version, described, account, tuple(warnings)


def check(
    deadline: Deadline, cwd: str, mode: str = "review"
) -> connectors.CheckResult:
    """Report whether Hermes could be used right now, spending no model turn.

    A work-mode check answers the same readiness questions with no switch
    qualification at all; the connector's declared capabilities carry the
    fact that work mode itself is unsupported.
    """
    program, version, described, account, warnings = _prerequisites(
        deadline, cwd, work=(mode == "work")
    )
    return connectors.readiness(
        HARNESS_ID,
        program,
        version,
        described,
        account,
        warnings,
        switches_confirmed=(mode != "work"),
    )


def build_command(deadline: Deadline, cwd: str) -> connectors.PeerCommand:
    """The fixed argument vector for one review turn, prerequisites confirmed first.

    `cwd` is always the neutral empty directory a turn without a project
    gets, because this connector is courier-only. The body is not in the
    vector returned here: the runner appends it as the final argument,
    behind `--oneshot=`, once it has checked the body will fit on a command
    line at all.
    """
    program, _version, _described, _account, warnings = _prerequisites(
        deadline, cwd
    )
    return connectors.PeerCommand(
        argv=(
            program,
            "--safe-mode",
            "--toolsets",
            TOOLSET,
        ),
        cwd=cwd,
        env=_environment(),
        body_argument=BODY_ARGUMENT,
        warnings=warnings,
    )
