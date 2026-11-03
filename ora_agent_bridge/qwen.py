"""The hand-written Qwen Code courier connector.

Qwen Code 0.23.0 supplies a safe mode, explicit plan posture, run budgets,
zero-model-tool-call guard, and stream-JSON standard input and output. The
connector keeps the user's normal QWEN_HOME so authentication is not hidden,
puts transient output inside the task-owned neutral directory, and disables
compatible usage statistics and telemetry for this child. Qwen alone may
preprocess the unchanged body before the model - recognized leading `/`
commands and unescaped `@` references, in both text and stream-json headless
input, before the zero model-tool-call budget - and that exception is warned
about rather than hidden. There is deliberately no work builder and no image
route: `CAPABILITIES` records the source-read reasons (approval modes that
either cannot ask headlessly or auto-approve, and a stream-json reader that
stringifies non-text content as JSON text rather than pixels).

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import json
import os
from typing import List, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import Deadline

HARNESS_ID = "qwen"

QUALIFICATION = connectors.Qualification(
    cli_identity="qwen",
    versions=("0.23.0",),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--safe-mode",
        "--sandbox",
        "--chat-recording",
        "--approval-mode",
        "--disabled-slash-commands",
        "--max-tool-calls",
        "--max-session-turns",
        "--input-format",
        "--output-format",
        "--openai-logging",
    ),
)

RUNTIME_DIRECTORY = ".qwen-runtime"
CONTROLLED_ENVIRONMENT = (
    "QWEN_CODE_RELAUNCH_ARGS",
    "QWEN_RUNTIME_DIR",
    "QWEN_TELEMETRY_ENABLED",
    "QWEN_USAGE_STATISTICS_ENABLED",
    "NODE_DISABLE_COMPILE_CACHE",
    "NO_BROWSER",
    "QWEN_SANDBOX",
    "SANDBOX",
    "SEATBELT_PROFILE",
    "QWEN_SANDBOX_PROXY_COMMAND",
)

INPUT_WARNING = (
    "Qwen Code 0.23.0 may preprocess enabled recognized leading / commands and "
    "unescaped @ references before the model in both text and stream-json "
    "headless input. It may alter or replace the effective prompt, inject "
    "readable file or resource content, fail during preprocessing, or "
    "complete a command without a model call. Ora Agent Bridge disables the "
    "pre-model command families that can report externally, persist or import "
    "user configuration, update the CLI, write diagnostics, or invoke "
    "installer rollback: /bug, /config, /update, /import-config, /language, /effort, "
    "/model, and /doctor. Other recognized slash-command preprocessing "
    "remains enabled. Safe mode does not disable it and no lossless raw "
    "escape or switch exists. Ora Agent Bridge "
    "records and passes the original request unchanged, but preprocessing "
    "happens before --max-tool-calls=0, so that budget does not stop it."
)

BOUNDARY_WARNING = (
    "Qwen Code is courier-only and receives a task-owned neutral directory. "
    "--safe-mode drops ambient context, hooks, extensions, MCP servers, custom "
    "subagents, permission rules, and memory features. Ora Agent Bridge clears "
    "inherited startup-argument overrides, SANDBOX, and the Qwen sandbox proxy "
    "command, pins QWEN_SANDBOX=sandbox-exec and the restrictive-open profile, "
    "and disables browser launches. Safe mode still loads settings and .env "
    "values: those sources can restore SANDBOX and bypass the sandbox, or "
    "restore QWEN_SANDBOX_PROXY_COMMAND and launch a detached shell outside "
    "the sandbox and Bridge's process group. Missing or empty values cannot "
    "pin those routes off. When used, the native profile still permits "
    "same-user reads, process launches, "
    "writes to Qwen, cache, temporary, and task-owned paths, and outbound "
    "network access; Qwen's bundled skills still load and can shape the turn. "
    "Plan mode plus "
    "--max-tool-calls=0 prevents model-initiated tool execution and aborts "
    "the run on the first such attempt. "
    "QWEN_HOME remains visible so the user's existing authentication and "
    "provider selection can work, and live authentication cannot be confirmed "
    "without the bounded model call. The configured model-provider connection "
    "and same-user CLI read access remain outside Ora Agent Bridge confinement."
)

#: What this connector offers beyond its restricted review call: nothing,
#: and for stated reasons read out of the installed program. Qwen Code
#: 0.23.0's ordinary approval mode (`--approval-mode default`) requires
#: manual approval for file edits and shell commands, and a headless run
#: cannot ask; the modes that proceed unattended (`auto-edit`, `auto`,
#: `yolo`) are automatic approvals Bridge must not select. For images, the
#: stream-json reader turns every non-text content block into JSON text
#: rather than pixels, and there is no attachment switch.
CAPABILITIES = connectors.Capabilities(
    work="unsupported",
    work_detail=(
        "Qwen Code's ordinary approval mode (default) requires manual "
        "approval for file edits and shell commands and a headless run cannot "
        "ask, while its unattended modes (auto-edit, auto, yolo) are "
        "automatic approvals Bridge will not select; run qwen in its own "
        "terminal for interactive work, or choose a work-capable target"
    ),
    image="unsupported",
    image_detail=(
        "Qwen Code's headless stream-json input turns every non-text content "
        "block into JSON text instead of pixels and the CLI has no attachment "
        "switch, so no qualified image route exists"
    ),
)

#: The switches a caller-named model or effort choice would travel on: none.
#: This connector's fixed vector adds no model selection or credential
#: handling of any kind, and it disables the `/model` and `/effort`
#: pre-model command families, so the runner refuses a named choice before
#: publication and Qwen answers on its own configured model.
CHOICE_SWITCHES = connectors.ChoiceSwitches()


def _environment(cwd: str) -> Tuple[Tuple[str, str], ...]:
    """Keep authentication; pin compatible controls and clear inherited routes."""
    inherited = [
        (name, value)
        for name, value in connectors.environment()
        if name not in CONTROLLED_ENVIRONMENT
    ]
    inherited.extend(
        (
            ("QWEN_RUNTIME_DIR", os.path.join(cwd, RUNTIME_DIRECTORY)),
            ("QWEN_TELEMETRY_ENABLED", "0"),
            ("QWEN_USAGE_STATISTICS_ENABLED", "0"),
            ("NODE_DISABLE_COMPILE_CACHE", "1"),
            ("NO_BROWSER", "1"),
            ("QWEN_SANDBOX", "sandbox-exec"),
            ("SEATBELT_PROFILE", "restrictive-open"),
        )
    )
    return tuple(inherited)


def encode_request(body: str) -> str:
    """Send one direct-mode user frame, then let the runner close stdin.

    Qwen 0.23.0's stream reader has no text-mode 8 MiB truncation limit and
    returns string content unchanged. No prompt argument or initial query is
    supplied: either would enter its sandbox shell or create another turn.
    """
    return json.dumps(
        {"type": "user", "message": {"role": "user", "content": body}},
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"


def parse_response(output: str) -> str:
    """Extract the sole terminal result from Qwen's newline-delimited JSON."""
    try:
        messages = [json.loads(line) for line in output.split("\n") if line.strip()]
    except ValueError as exc:
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="qwen returned no readable stream-JSON messages: {0}".format(exc),
        )
    if not messages or any(not isinstance(message, dict) for message in messages):
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="qwen's stream-JSON output was not a nonempty message sequence",
        )
    result = messages[-1]
    if result.get("type") != "result" or sum(
        message.get("type") == "result" for message in messages
    ) != 1:
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="qwen's stream-JSON output lacked one sole terminal result",
        )
    if result.get("subtype") != "success" or result.get("is_error") is not False:
        error = result.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error.get("type")
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="qwen's final JSON result reported failure: {0}".format(
                # The runner removes echoed requests and credentials before
                # shortening this diagnostic; partial echoes cannot be matched.
                str(error or result.get("subtype") or "unknown")
            ),
        )
    text = result.get("result")
    if not isinstance(text, str):
        raise BridgeError(
            Failure.PEER_FAILURE,
            detail="qwen's successful JSON result contained no text",
        )
    return text


def _prerequisites(
    deadline: Deadline, cwd: str, work: bool = False
) -> Tuple[str, str, str, str, Tuple[str, ...]]:
    """Readiness facts, with the review switch set qualified for review alone.

    A review turn proves the review vector's switches exist on the installed
    program. A work-mode check performs no switch qualification at all: work
    is unsupported for reasons recorded in `CAPABILITIES`, and no review
    switch is a prerequisite of reporting that.
    """
    warnings = []  # type: List[str]
    program = connectors.executable(QUALIFICATION.cli_identity)
    environment = _environment(cwd)
    version = connectors.qualified_version(
        connectors.probe(
            (program, "--version"), cwd, deadline, environment
        ).stdout,
        QUALIFICATION,
        warnings,
    )
    described = connectors.qualified_platform(QUALIFICATION, warnings)
    if not work:
        connectors.qualified_restrictions(
            connectors.probe((program, "--help"), cwd, deadline, environment),
            QUALIFICATION,
        )
    warnings.extend((INPUT_WARNING, BOUNDARY_WARNING))
    return (
        program,
        version,
        described,
        "no safe noninteractive authentication-status command is available",
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
        switches_confirmed=(mode != "work"),
    )


def build_command(deadline: Deadline, cwd: str) -> connectors.PeerCommand:
    program, _version, _described, _account, warnings = _prerequisites(
        deadline, cwd
    )
    return connectors.PeerCommand(
        argv=(
            program,
            "--safe-mode",
            "--sandbox=sandbox-exec",
            "--chat-recording=false",
            "--approval-mode=plan",
            "--disabled-slash-commands="
            "bug,config,update,import-config,language,effort,model,doctor",
            "--max-tool-calls=0",
            "--max-session-turns=1",
            "--input-format=stream-json",
            "--output-format=stream-json",
            "--openai-logging=false",
        ),
        cwd=cwd,
        env=_environment(cwd),
        warnings=warnings,
        response_parser=parse_response,
        stdin_encoder=encode_request,
    )
