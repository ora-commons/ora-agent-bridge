"""The hand-written MiniMax Code courier connector.

MiniMax Code 0.2.7 has a stable headless ``mcode exec`` transport, but no
headless permission mode that confines its tools. Ora Agent Bridge therefore
gives its review calls only a task-owned neutral directory and reports the
remaining tool and configuration authority instead of presenting ``smart``
as a sandbox. Work calls use the same ``exec`` under the program's own
ordinary defaults and with no permission policy selected by Bridge: the
workspace comes from ``--cwd``, and the review-only one-step bound is not
imposed.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import json
from typing import List, Optional, Sequence, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import Deadline

HARNESS_ID = "minimax"

QUALIFICATION = connectors.Qualification(
    cli_identity="mcode",
    versions=("0.2.7",),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--input",
        "--input-format",
        "--cwd",
        "--permission",
        "--max-steps",
        "--output-format",
    ),
)

#: What this connector offers beyond its restricted review call. Work is the
#: ordinary `mcode exec` invocation with no permission policy of Bridge's
#: choosing: mcode's source fixes an absent `--permission` at `smart`, and
#: no runtime configuration can change the permission mode of a headless
#: run; `ask` needs a TUI and is rejected by `mcode exec` itself, while
#: `full` and `off` are approval bypasses Bridge never selects. Images have
#: a native `--file` attachment switch.
CAPABILITIES = connectors.Capabilities(
    work="supported",
    work_detail=(
        "work uses mcode exec with the caller's project as --cwd and no "
        "permission policy selected by Bridge: the program's own headless "
        "default (smart) governs, ask cannot run headless and is rejected "
        "by mcode exec itself, and full and off are approval bypasses "
        "Bridge never selects; no assistant-step bound is imposed unless "
        "--max-steps names one"
    ),
    image="supported",
    image_detail=(
        "images travel as native mcode exec --file attachments, which mcode "
        "classifies by MIME type and hands to the model as image content "
        "(at most 10 files and 100 MB per run)"
    ),
)

#: The switch a caller-named model choice travels on. MiniMax Code documents
#: `--model <provider/model>` for one run; it documents no effort switch.
CHOICE_SWITCHES = connectors.ChoiceSwitches(model="--model")

#: The switches a work turn relies on, verified against the installed
#: program's own help before any request is published. The review-only
#: `--permission` switch is not a work prerequisite, and the optional
#: switches are required only when the work vector actually emits them.
WORK_RESTRICTIONS = ("--input", "--input-format", "--cwd", "--output-format")
WORK_ATTACHMENT_RESTRICTIONS = ("--file",)
WORK_STEPS_RESTRICTIONS = ("--max-steps",)
WORK_MODEL_RESTRICTIONS = ("--model",)

WORK_WARNING = (
    "MiniMax Code work runs with the program's ordinary headless defaults "
    "and no permission policy selected by this vector: mcode exec's own "
    "default governs, which its source fixes at smart when --permission is "
    "absent, and no runtime configuration can change the permission mode of "
    "a headless run. smart is a discretionary permission mode, not a "
    "sandbox, so the model's tool use is governed by MiniMax's own policy "
    "engine and by surviving user/provider configuration; ask is interactive "
    "and rejected by mcode exec, which names the TUI or ACP as its route, "
    "and full and off are approval bypasses Bridge never selects. No "
    "assistant-step bound is imposed unless --max-steps names one; the "
    "caller's stop and cancellation are the only outer bound. Writes are confined by nothing "
    "except that policy; treat the working and access directories as "
    "writable."
)

WARNING = (
    "MiniMax Code is courier-only and receives a task-owned neutral directory. "
    "--permission smart is a discretionary permission mode, not a sandbox: "
    "ask is interactive and rejected by mcode exec, while full bypasses and "
    "off disables permission checks, so the fixed vector uses none of those "
    "modes. By default, --max-steps=1 limits assistant steps; an explicitly "
    "requested positive bound may permit more. Neither form creates confinement, "
    "and a step bound does not disable tools within any permitted step. Smart "
    "does not categorically "
    "confine file writes, shell or Git "
    "commands, MCP tools, network access, or surviving user/provider "
    "configuration."
)

def _invalid_result(detail: str) -> BridgeError:
    return BridgeError(Failure.PEER_FAILURE, detail=detail)


def validate_run_options(
    max_steps: Optional[int], required_model: Optional[str]
) -> None:
    """Validate the two MiniMax-only per-run controls before prerequisites."""
    if max_steps is not None and (type(max_steps) is not int or max_steps <= 0):
        raise BridgeError(
            Failure.USAGE_ERROR,
            detail="--max-steps must be a positive integer",
        )
    if required_model is not None:
        valid_model = isinstance(required_model, str) and (
            required_model == required_model.strip()
            and len(required_model) <= 256
            and required_model.count("/") == 1
            and all(required_model.split("/", 1))
            and not any(ord(character) < 32 for character in required_model)
        )
        if not valid_model:
            raise BridgeError(
                Failure.USAGE_ERROR,
                detail="--require-model must name one exact nonempty provider/model",
            )


def parse_response(output: str, required_model: str) -> str:
    """Final text, carrying the reported identity, after strict validation.

    A schema-version-1 successful `exec.result` whose `providerId/modelId`
    is byte-for-byte the required model. The
    return is that validated identity's own final output text, returned as
    `AttributedText` so the provider and model MiniMax itself reported
    travel with the answer instead of being discarded.
    """
    try:
        result = json.loads(output)
    except (TypeError, ValueError) as exc:
        raise _invalid_result("minimax returned invalid JSON: {0}".format(exc))
    if not isinstance(result, dict):
        raise _invalid_result("minimax JSON output was not an object")
    schema_is_valid = type(result.get("schemaVersion")) is int and result["schemaVersion"] == 1
    if not schema_is_valid or result.get("type") != "exec.result" or result.get("status") != "succeeded":
        raise _invalid_result(
            "minimax JSON output did not report a successful schema-version-1 "
            "exec.result"
        )
    text = result.get("output")
    if not isinstance(text, str):
        raise _invalid_result("minimax successful JSON result contained no string output")
    model = result.get("model")
    provider_id = model.get("providerId") if isinstance(model, dict) else None
    model_id = model.get("modelId") if isinstance(model, dict) else None
    if not all(isinstance(value, str) and value for value in (provider_id, model_id)):
        raise _invalid_result(
            "minimax successful JSON result contained no runtime model identity"
        )
    actual_model = "{0}/{1}".format(provider_id, model_id)
    if actual_model.encode("utf-8") != required_model.encode("utf-8"):
        detail = "minimax runtime model {0!r} did not exactly match required model {1!r}"
        raise _invalid_result(detail.format(actual_model, required_model))
    return connectors.AttributedText(
        text, provider=provider_id, model=model_id
    )


def _prerequisites(
    deadline: Deadline,
    cwd: str,
    work: bool = False,
    attachments: Sequence[str] = (),
    max_steps: Optional[int] = None,
    model: Optional[str] = None,
) -> Tuple[str, str, str, str, Tuple[str, ...]]:
    """Readiness facts and the switches the selected mode's vector relies on.

    A review turn proves the review vector's whole switch set, `--permission`
    included; a work turn proves only what the work vector passes.
    Authentication stays honestly unconfirmed in both modes: MiniMax has no
    state-free noninteractive authentication check, and none is invented
    here.
    """
    warnings = []  # type: List[str]
    program = connectors.executable(QUALIFICATION.cli_identity)
    version = connectors.qualified_version(
        connectors.probe((program, "--version"), cwd, deadline).stdout,
        QUALIFICATION,
        warnings,
    )
    described = connectors.qualified_platform(QUALIFICATION, warnings)
    help_call = connectors.probe((program, "exec", "--help"), cwd, deadline)
    if work:
        switches = WORK_RESTRICTIONS
        if attachments:
            switches += WORK_ATTACHMENT_RESTRICTIONS
        if max_steps is not None:
            switches += WORK_STEPS_RESTRICTIONS
        if model is not None:
            switches += WORK_MODEL_RESTRICTIONS
        connectors.qualified_restrictions(
            help_call, QUALIFICATION._replace(restrictions=switches)
        )
        warnings.append(WORK_WARNING)
    else:
        connectors.qualified_restrictions(
            help_call,
            QUALIFICATION._replace(
                restrictions=QUALIFICATION.restrictions
                + (WORK_MODEL_RESTRICTIONS if model is not None else ())
            ),
        )
        warnings.append(WARNING)
    warnings.append(
        "MiniMax has no state-free noninteractive authentication check: "
        "provider list can initialize its runtime and refresh or invalidate "
        "OAuth state, so Ora Agent Bridge does not run it. Live authentication "
        "remains unconfirmed until the selected bounded call."
    )
    return (
        program,
        version,
        described,
        "no state-free noninteractive authentication-status command is available",
        tuple(warnings),
    )


def check(
    deadline: Deadline, cwd: str, mode: str = "review"
) -> connectors.CheckResult:
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
        authentication_confirmed=False,
    )


def build_command(
    deadline: Deadline,
    cwd: str,
    max_steps: Optional[int] = None,
    required_model: Optional[str] = None,
    model: Optional[str] = None,
) -> connectors.PeerCommand:
    program, _version, _described, _account, warnings = _prerequisites(
        deadline, cwd, model=model
    )
    return connectors.PeerCommand(
        argv=(
            program,
            "exec",
            "--input",
            "-",
            "--input-format",
            "text",
            "--cwd",
            cwd,
            "--permission",
            "smart",
        )
        + (
            ("--model", model) if model is not None else ()
        )
        + (
            "--max-steps",
            str(max_steps if max_steps is not None else 1),
            "--output-format",
            "json" if required_model is not None else "text",
        ),
        cwd=cwd,
        env=connectors.environment(),
        warnings=warnings,
        response_parser=(
            (lambda output: parse_response(output, required_model))
            if required_model is not None
            else None
        ),
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
    """The ordinary `mcode exec` work invocation, review-only bounds removed.

    The workspace is the session's working directory, named by `--cwd`. No
    permission policy is selected: the effective policy is mcode exec's own,
    which its source fixes at `smart` when the switch is absent, with no
    runtime configuration able to change the permission mode of a headless
    run. The review call's one-assistant-step bound is not imposed:
    `--max-steps` is passed only when the caller names a bound. Each
    attachment becomes one `--file`. Declared access directories stay
    reachable as ordinary same-user paths; mcode has no per-call switch that
    widens its workspace. A caller-named model passes through on MiniMax's
    own documented `--model` switch, exactly as named; MiniMax Code
    documents no effort switch, so the parameter is accepted and never used.
    """
    program, _version, _described, _account, warnings = _prerequisites(
        deadline, cwd, work=True, attachments=attachments,
        max_steps=max_steps, model=model,
    )
    argv = [
        program,
        "exec",
        "--input",
        "-",
        "--input-format",
        "text",
        "--cwd",
        cwd,
    ]
    for attachment in attachments:
        argv.extend(("--file", attachment))
    if model is not None:
        argv.extend(("--model", model))
    if max_steps is not None:
        argv.extend(("--max-steps", str(max_steps)))
    argv.extend(
        ("--output-format", "json" if required_model is not None else "text")
    )
    return connectors.PeerCommand(
        argv=tuple(argv),
        cwd=cwd,
        env=connectors.environment(),
        warnings=warnings,
        response_parser=(
            (lambda output: parse_response(output, required_model))
            if required_model is not None
            else None
        ),
    )
