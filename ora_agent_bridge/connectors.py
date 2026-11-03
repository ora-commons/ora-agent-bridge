"""The six target CLIs Ora Agent Bridge can call, and how one call is described.

Looking one up is a literal six-way switch: no registry, plugin search, or
name-derived import. Each target has one hand-written module offering exactly
two operations: answer whether the target could be used right now, and
compose the one fixed argument vector a turn runs. This module also holds the
plain helpers every connector shares, and the small fixed shapes the rest of
the code passes around.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import os
import platform
import re
import shutil
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
)

from .errors import BridgeError, Failure
from .peer import CompletedCall, Deadline, run_bounded

#: The six target identifiers, fixed and ordered. Nothing else is a target.
HARNESS_IDS: Tuple[str, ...] = (
    "codex",
    "claude",
    "zcode",
    "hermes",
    "minimax",
    "qwen",
)


class Qualification(NamedTuple):
    """What one connector claims to have actually been tested against.

    A source-controlled declaration, never a record of anything observed at
    runtime. `restrictions` is the set of switches that must be present for
    the harness to be called at all. The list is not a claim of complete
    confinement: surviving limits are described in concrete warnings.
    """

    cli_identity: str
    versions: Tuple[str, ...]
    os_family: str
    os_major_versions: Tuple[str, ...]
    architectures: Tuple[str, ...]
    restrictions: Tuple[str, ...]


#: The plain wording a choices report carries for a half the connector
#: looked for in the tool's own records and could not read.
NOT_REPORTED = "not reported"


class AttributedText(str):
    """A final response text carrying the identity the tool itself reported.

    It is a `str` and publishes as that text, with the reported provider,
    model, and effort attached after the connector has validated them. Each
    attribute is the exact single-line string the tool's own output named,
    or `None` when it named none; the one wording a connector may attach
    without the tool naming a value is the plain `not reported` negative.
    """

    def __new__(
        cls,
        text: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
    ) -> "AttributedText":
        attributed = super().__new__(cls, text)
        attributed.provider = provider
        attributed.model = model
        attributed.effort = effort
        return attributed


class PeerCommand(NamedTuple):
    """One bounded call to a peer harness's program.

    `argv` is a fixed argument vector, run without a shell; `cwd` is the
    exact directory the program starts in; `env` is the complete environment
    as ordered name/value pairs. `body_argument` is `None` for every harness
    whose program reads standard input (the required transport wherever one
    exists); for a program with demonstrably no standard-input path, the
    runner appends the body to `argv` as exactly one final argument behind
    this prefix, and a body holding a NUL byte or over the command-line size
    limit is refused before any request is published. `warnings` are the
    concrete limits a successful check and run must surface.
    `response_parser` extracts final text when the fixed output is
    structured, returning `AttributedText` when that output reports the
    identity that answered. `stdin_encoder` wraps the unchanged body in a
    vendor input frame before publication. `complete_failure_reason` keeps a
    parsed failure reason complete after request and credential redaction.
    """

    argv: Tuple[str, ...]
    cwd: str
    env: Tuple[Tuple[str, str], ...]
    body_argument: Optional[str] = None
    warnings: Tuple[str, ...] = ()
    response_parser: Optional[Callable[[str], str]] = None
    stdin_encoder: Optional[Callable[[str], str]] = None
    complete_failure_reason: bool = False


class CheckResult(NamedTuple):
    """One successful readiness sentence, its facts, and its warnings.

    `qualified` is `None` when nothing established it either way.
    `selected_model_verified` is `None` unless a connector reports that fact;
    `False` means the readiness check did not establish live acceptance of
    the selected model.
    """

    message: str
    warnings: Tuple[str, ...] = ()
    executable: Optional[str] = None
    version: Optional[str] = None
    platform: Optional[str] = None
    authentication_confirmed: bool = True
    qualified: Optional[bool] = None
    selected_model_verified: Optional[bool] = None
    effective_permission: Optional[str] = None
    headless_shell: Optional[bool] = None
    shell_execution_verified: Optional[bool] = None


class Capabilities(NamedTuple):
    """What one connector offers beyond its restricted review call.

    `work` and `image` are one of `supported`, `unsupported`, or `unknown`,
    each carrying the sentence that makes the label truthful.
    """

    work: str
    work_detail: str
    image: str
    image_detail: str


def validate_work_permission(harness_id: str, mode: str, permission: Optional[str]) -> None:
    """An explicit permission choice belongs only to one ZCode work call."""
    if permission is not None and (
        harness_id != "zcode" or mode != "work" or permission != "full-access"
    ):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--work-permission full-access is available only for ZCode work calls",
        )


class ChoiceSwitches(NamedTuple):
    """The switches a caller-named model or effort choice travels on.

    Each field holds the switch exactly as this connector's qualified vector
    passes it, or `None` when the vector documents no switch for that choice,
    in which case a named choice is refused before request publication.
    """

    model: Optional[str] = None
    effort: Optional[str] = None


#: The most a body may be when it travels on the command line, in encoded
#: bytes. Fixed by the plan; half the argument space macOS allows a process.
#: A body over this limit or holding a NUL byte is refused before publication.
#: If the operating system refuses the whole argument block, the request has
#: already been saved and the launch failure reports that refusal.
COMMAND_LINE_BODY_LIMIT = 524288


# -- what all connectors do the same way ------------------------------------

#: Any dotted release number. Target CLIs surround theirs with wording of their
#: own, so only the number is read out of it.
_VERSION = re.compile(r"\d+\.\d+\.\d+")

#: The phrase every exercised-evidence warning carries. A readiness result
#: states mechanically whether the installed combination is inside the
#: connector's release evidence by looking for this marker.
EVIDENCE_MARKER = "outside the release evidence"


def environment() -> Tuple[Tuple[str, str], ...]:
    """What a peer program inherits: this process's own environment, unchanged.

    Restriction is done with the harness's own documented switches, because a
    hand-pruned environment would look like a boundary without being one.
    """
    return tuple(os.environ.items())


def executable(program: str) -> str:
    """Where a harness's program actually is, or `MISSING_CLI` if it is nowhere.

    The full path is used from then on, so the program a readiness check looked
    at is exactly the program a turn starts.
    """
    found = shutil.which(program)
    if found is None:
        raise BridgeError(Failure.MISSING_CLI, detail=program)
    return found


def probe(
    argv: Sequence[str],
    cwd: str,
    deadline: Deadline,
    env: Optional[Iterable[Tuple[str, str]]] = None,
) -> CompletedCall:
    """Ask a harness one of its own cheap questions, inside the turn's deadline.

    Started the way everything else is: a fixed argument vector, no shell,
    nothing on standard input, the same deadline, the same cleanup.
    """
    return run_bounded(
        argv=tuple(argv),
        cwd=cwd,
        env=tuple(env) if env is not None else environment(),
        stdin_text="",
        deadline=deadline,
    )


def qualified_version(
    printed: str,
    qualification: Qualification,
    warnings: List[str],
) -> str:
    """Read the installed version and warn when it is outside exercised evidence."""
    found = _VERSION.search(printed)
    if found is None:
        raise BridgeError(
            Failure.UNREPORTABLE_VERSION,
            detail="{0} printed {1!r}".format(
                qualification.cli_identity, printed.strip()[:120]
            ),
        )
    version = found.group(0)
    if version not in qualification.versions:
        warnings.append(
            "{0} reported version {1}; this connector was exercised against "
            "{2}. The required one-shot and restriction mechanics are present, "
            "but this version is {3}.".format(
                qualification.cli_identity,
                version,
                ", ".join(qualification.versions),
                EVIDENCE_MARKER,
            )
        )
    return version


def qualified_platform(
    qualification: Qualification, warnings: List[str]
) -> str:
    """Describe this computer and warn when it is outside exercised evidence.

    The family is what Python calls the operating system; the major version is
    macOS's own where there is one, and the kernel release elsewhere. The
    processor is compared only when the connector named one.
    """
    family = platform.system()
    release = platform.mac_ver()[0] or platform.release()
    major = release.split(".")[0]
    machine = platform.machine()
    described = "{0} {1} {2}".format(family, major, machine)
    untested = (
        family != qualification.os_family
        or major not in qualification.os_major_versions
        or (
            bool(qualification.architectures)
            and machine not in qualification.architectures
        )
    )
    if untested:
        qualified_platforms = "{0} major {1}".format(
            qualification.os_family,
            "/".join(qualification.os_major_versions),
        )
        if qualification.architectures:
            qualified_platforms += " on {0}".format(
                "/".join(qualification.architectures)
            )
        warnings.append(
            "{0} is running on {1}; this connector was exercised on {2}. "
            "The required one-shot and restriction mechanics are present, but "
            "this platform is {3}.".format(
                qualification.cli_identity,
                described,
                qualified_platforms,
                EVIDENCE_MARKER,
            )
        )
    return described


def qualified_restrictions(
    help_call: CompletedCall, qualification: Qualification
) -> None:
    """Prove every restriction switch this connector passes really exists.

    Reading the program's own help is how presence is established without
    spending a model turn. Only the switches are looked for, never the values
    passed with them: a switch that is gone is the quiet failure worth
    catching here; a value a version no longer accepts makes the program
    refuse loudly on its own.
    """
    if help_call.returncode != 0:
        raise BridgeError(
            Failure.RESTRICTIONS_UNAVAILABLE,
            detail="{0}'s help command exited {1}: {2}".format(
                qualification.cli_identity,
                help_call.returncode,
                (help_call.stderr or help_call.stdout).strip()[:160],
            ),
        )
    help_text = "{0}\n{1}".format(help_call.stdout, help_call.stderr)
    missing = [
        switch
        for switch in qualification.restrictions
        if switch not in help_text
    ]
    if missing:
        raise BridgeError(
            Failure.RESTRICTIONS_UNAVAILABLE,
            detail="{0} does not offer {1}".format(
                qualification.cli_identity, ", ".join(missing)
            ),
        )


def readiness(
    harness_id: str,
    program: str,
    version: str,
    described_platform: str,
    authentication: str,
    warnings: Sequence[str] = (),
    authentication_confirmed: bool = True,
    switches_confirmed: bool = True,
) -> CheckResult:
    """The truthful mechanics and authentication state printed by ``check``.

    `switches_confirmed` states whether the mode being reported on actually
    qualified a fixed-vector switch set; a work-mode check of a connector
    with no work vector qualifies nothing and its sentence claims none.
    """
    if authentication_confirmed:
        state = "{0} is ready".format(harness_id)
    else:
        state = (
            "{0} mechanics are ready, but live authentication is unconfirmed"
        ).format(harness_id)
    if switches_confirmed:
        message = (
            "{0}: version {1} at {2}, on {3}, {4}, and every "
            "required fixed-vector switch is present.".format(
                state, version, program, described_platform, authentication
            )
        )
    else:
        message = "{0}: version {1} at {2}, on {3}, {4}.".format(
            state, version, program, described_platform, authentication
        )
    return CheckResult(
        message=message,
        warnings=tuple(warnings),
        executable=program,
        version=version,
        platform=described_platform,
        authentication_confirmed=authentication_confirmed,
        qualified=not any(EVIDENCE_MARKER in warning for warning in warnings),
    )


# -- the six-way switch ----------------------------------------------------


def is_courier_only(harness_id: str) -> bool:
    """Answer from literals only, without importing or inspecting a connector."""
    return (
        harness_id == "hermes"
        or harness_id == "minimax"
        or harness_id == "qwen"
    )


def resolve(harness_id: str) -> Any:
    """Return the connector for one of the six literal targets.

    The branches are written out one by one on purpose: this is the whole
    list of programs Ora Agent Bridge is willing to start. Matching the
    branch before its import is what leaves all five unselected connectors
    inert. Raises `UNKNOWN_HARNESS` for any other name; every fixed target
    ships a connector in this build.
    """
    if harness_id == "codex":
        from . import codex

        return codex
    if harness_id == "claude":
        from . import claude

        return claude
    if harness_id == "zcode":
        from . import zcode

        return zcode
    if harness_id == "hermes":
        from . import hermes

        return hermes
    if harness_id == "minimax":
        from . import minimax

        return minimax
    if harness_id == "qwen":
        from . import qwen

        return qwen
    raise BridgeError(Failure.UNKNOWN_HARNESS, detail=harness_id)
