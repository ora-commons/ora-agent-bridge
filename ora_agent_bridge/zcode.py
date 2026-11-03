"""Calling ZCode with its strongest practical read-only posture.

ZCode's command-line program is a JavaScript bundle inside the desktop
application, run by a Node runtime and started directly. `check` answers
readiness, `build_command` composes the review vector, and
`build_work_command` composes the per-call permission prompt a work turn
runs. The connector's shape rests on vendor findings established by probing
the installed bundle - no standard-input prompt path, `--prompt=<body>` as
the only safe binding, a help text that lists switches its parser rejects,
and a configuration-file sign-in test - recorded in full in INTERFACE.md.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence, Tuple

from . import connectors
from .errors import BridgeError, Failure
from .peer import Deadline

#: The identifier this connector answers to, out of the six.
HARNESS_ID = "zcode"

#: The runtime the bundle needs, found on PATH like any other program.
RUNTIME = "node"

#: Where the desktop application keeps the command-line program. There is no
#: `zcode` on PATH to find; this is the documented location of the bundle.
SCRIPT = "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"

#: The desktop app's built-in provider file, passed through its native
#: per-process path support rather than inferred from the bundle's location.
BUILTIN_PROVIDER = (
    "/Applications/ZCode.app/Contents/Resources/config/provider/"
    "zcode-builtin.json"
)
BUILTIN_PROVIDER_VARIABLE = "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE"
PERSONAL_PROVIDER_VARIABLE = "ZCODE_PERSONAL_PROVIDER_CONFIG_FILE"
PERSONAL_PROVIDER_FILE = os.path.join(".zcode", "v2", "provider_config.json")

#: What this connector has actually been tested against, declared in source:
#: the review route at 0.16.5 and the work route at 0.16.9.
#: `restrictions` names the exact switches the review vector passes.
QUALIFICATION = connectors.Qualification(
    cli_identity="zcode",
    versions=("0.16.5", "0.16.9"),
    os_family="Darwin",
    os_major_versions=("26",),
    architectures=("arm64",),
    restrictions=(
        "--disallowed-tools",
        "--mode",
        "--cwd",
    ),
)

#: What this connector offers beyond its restricted review call: ZCode's own
#: `edit` permission mode by default, or explicitly requested Full access
#: (`yolo`) for one work call, and the native `--attach` image route.
CAPABILITIES = connectors.Capabilities(
    work="supported",
    work_detail=(
        "work uses zcode --mode edit, ZCode's own permission mode in which "
        "workspace file edits are allowed and every other approval-needing "
        "tool is denied by the deny broker a headless prompt uses; an explicit "
        "--work-permission full-access selects native --mode yolo for that call"
    ),
    image="supported",
    image_detail=(
        "images travel as native --prompt attachments via --attach, which "
        "ZCode reads and hands to the model as inline image content"
    ),
)

#: The switches a caller-named model or effort choice would travel on: none.
#: The installed bundle's headless `--prompt` route documents no switch for
#: naming either, so the runner refuses a named choice before publication.
CHOICE_SWITCHES = connectors.ChoiceSwitches()

#: The switches a work turn relies on, looked for in the help text this
#: build prints reliably for them and proven together by the no-turn parser
#: probe below. `--attach` is required only when an attachment travels.
WORK_RESTRICTIONS = ("--prompt", "--mode", "--cwd")
WORK_ATTACHMENT_RESTRICTIONS = ("--attach",)

WORK_WARNING = (
    "ZCode work runs in edit mode: workspace file edits are allowed "
    "automatically, and any other tool that would need approval is denied, "
    "because a headless prompt has no approver and ZCode's default broker "
    "denies rather than asks. The review call's tool deny list is not "
    "applied, so the ordinary tool set is available under those rules, and "
    "enabled plugins and directly configured MCP servers keep whatever routes "
    "they already had. The prompt default for --prompt is yolo; this vector "
    "overrides it with edit unless this call explicitly requests Full access."
)

FULL_ACCESS_WARNING = (
    "ZCode Full access is selected for this work call only (--mode yolo): "
    "tools, including shell commands for Git and tests, run without an approval "
    "connection under your operating-system account. This does not change "
    "global settings or authorize work beyond the user's request and project instructions."
)

#: The prefix the body is bound to as the final argument. One argument, so
#: no byte of the body can begin another option; the probe findings behind
#: this shape are recorded in INTERFACE.md.
BODY_ARGUMENT = "--prompt="

#: How the answer is asked for: the final response alone, on standard
#: output. Not printed by `--help`, so it is proven by the no-turn probe.
OUTPUT_FORMAT = ("--output-format", "text")

#: The built-in tools removed from the peer, as one comma-separated value so
#: the option cannot go on swallowing the switches that follow it.
#: Everything that writes, runs, reaches out, delegates, schedules, changes
#: mode, asks a person or reads other sessions. Read, Glob and Grep stay,
#: because a peer that cannot read the project cannot answer about it.
DENIED_TOOLS = ",".join(
    (
        "Write",
        "Edit",
        "ApplyPatch",
        "NotebookEdit",
        "Bash",
        "WebFetch",
        "WebSearch",
        "Agent",
        "Task",
        "TaskOutput",
        "TaskStop",
        "Skill",
        "Workflow",
        "SendMessage",
        "RespondToCoordinator",
        "AskUserQuestion",
        "EnterPlanMode",
        "ExitPlanMode",
        "TodoWrite",
        "ReadSessionContext",
        "CronCreate",
        "CronList",
        "CronUpdate",
        "CronDelete",
        "js",
        "js_reset",
        "js_add_node_module_dir",
        "mcp__node_repl__js",
        "mcp__node_repl__js_reset",
        "mcp__node_repl__js_add_node_module_dir",
    )
)

#: The program's own configuration file - the place its sign-in test looks.
CONFIG_FILE = os.path.join("~", ".zcode", "cli", "config.json")

DATA_BASE_DIR_VARIABLE = "ZCODE_DATA_BASE_DIR"


def _program() -> Tuple[str, str]:
    """Where the runtime is and what path the bundle is started under.

    The runtime is looked up on PATH; the bundle is looked for at its one
    documented place. Either missing is `MISSING_CLI` naming which.
    """
    runtime = connectors.executable(RUNTIME)
    if not os.path.isfile(SCRIPT):
        raise BridgeError(
            Failure.MISSING_CLI,
            detail="the ZCode desktop application's command-line bundle is "
            "not at {0}".format(SCRIPT),
        )
    return runtime, SCRIPT


def _environment(cwd: str) -> Tuple[Tuple[str, str], ...]:
    """Keep ZCode's provider identity while repairing its per-process paths.

    ZCode requires both path variables together. A supplied path wins; the
    absent personal path uses exactly ZCode's native data-base/home location.
    The built-in file must be readable before a no-model check can claim
    usable launch mechanics. Its contents and the personal store are never
    opened here, and no parent environment or persistent setting is changed.
    """
    env = dict(connectors.environment())
    if not env.get(BUILTIN_PROVIDER_VARIABLE, "").strip():
        env[BUILTIN_PROVIDER_VARIABLE] = BUILTIN_PROVIDER
    if not env.get(PERSONAL_PROVIDER_VARIABLE, "").strip():
        base = env.get(DATA_BASE_DIR_VARIABLE)
        base = base.strip() if base is not None else os.path.expanduser("~")
        env[PERSONAL_PROVIDER_VARIABLE] = os.path.join(base, PERSONAL_PROVIDER_FILE)
    builtin = env[BUILTIN_PROVIDER_VARIABLE].strip()
    checked_path = os.path.join(cwd, builtin)
    if not os.path.isfile(checked_path) or not os.access(checked_path, os.R_OK):
        raise BridgeError(
            Failure.MISSING_CLI,
            detail="ZCode's built-in provider configuration is not a readable "
            "file at {0}, selected by {1}; restore that file or correct the "
            "supplied path before checking again".format(
                builtin, BUILTIN_PROVIDER_VARIABLE,
            ),
        )
    return tuple(env.items())


def _sign_in_facts() -> str:
    """What can be observed about sign-in without a turn and without reading.

    ZCode's own sign-in test is a coding-plan key inside its configuration
    file, so a missing configuration file is not signed in by the program's
    own rule and is reported as `AUTHENTICATION_REQUIRED`. Presence is
    established by metadata only; the file is never opened, and sign-in
    itself is not confirmed, because ZCode offers no command that reports
    it without spending a model turn.
    """
    config = os.path.expanduser(CONFIG_FILE)
    if not os.path.isfile(config):
        raise BridgeError(
            Failure.AUTHENTICATION_REQUIRED,
            detail="ZCode's own sign-in test is a coding-plan key in {0}, "
            "which is absent".format(config),
        )
    return (
        "its configuration file is present at {0}; sign-in itself is not "
        "confirmed, because ZCode offers no command that reports it without "
        "spending a model turn".format(config)
    )


def _prerequisites(
    deadline: Deadline,
    cwd: str,
    work: bool = False,
    attachments: Sequence[str] = (),
    work_permission: Optional[str] = None,
) -> Tuple[str, str, str, str, Tuple[str, ...]]:
    """Everything that has to be true before starting ZCode is worth doing.

    Each question is cheap and none spends a model turn: is the runtime
    here, is the bundle here, is its built-in provider path readable, is its
    version one this connector was tested against, is this computer one it
    was tested on, is the configuration file present, and are the switches
    the turn relies on really accepted - proven by passing them to the
    `version` subcommand, because this program's help text lists switches
    its parser rejects. Missing software, minimum local sign-in state, or
    required mechanics raises; the surviving plugin boundary and the lack
    of live OAuth evidence are returned as fixed warnings.

    Returns the four facts a readiness report needs and a turn uses.
    """
    mode = ("yolo" if work_permission == "full-access" else "edit") if work else "plan"
    warnings = []  # type: List[str]
    runtime, script = _program()
    env = _environment(cwd)
    version = connectors.qualified_version(
        connectors.probe((runtime, script, "--version"), cwd, deadline, env=env).stdout,
        QUALIFICATION,
        warnings,
    )
    described = connectors.qualified_platform(QUALIFICATION, warnings)
    sign_in = _sign_in_facts()

    help_call = connectors.probe((runtime, script, "--help"), cwd, deadline, env=env)
    if work:
        switches = WORK_RESTRICTIONS
        if attachments:
            switches += WORK_ATTACHMENT_RESTRICTIONS
        connectors.qualified_restrictions(
            help_call, QUALIFICATION._replace(restrictions=switches)
        )
        parser_switches = ("--mode", mode, "--cwd", cwd)
        parser_names = ("--mode", "--cwd") + OUTPUT_FORMAT[:1]
    else:
        connectors.qualified_restrictions(help_call, QUALIFICATION)
        parser_switches = ("--disallowed-tools", "Edit", "--mode", "plan", "--cwd", cwd)
        parser_names = QUALIFICATION.restrictions + OUTPUT_FORMAT[:1]
    accepted = connectors.probe(
        (runtime, script, "version") + parser_switches + OUTPUT_FORMAT,
        cwd,
        deadline,
        env=env,
    )
    if accepted.returncode != 0 or version not in accepted.stdout:
        raise BridgeError(
            Failure.RESTRICTIONS_UNAVAILABLE,
            detail="zcode rejected {0} on a subcommand that spends no turn: "
            "{1}".format(
                ", ".join(parser_names),
                (accepted.stderr or accepted.stdout).strip()[:160],
            ),
        )
    if work:
        warnings.append(FULL_ACCESS_WARNING if work_permission == "full-access" else WORK_WARNING)
    warnings.append(
        "ZCode cannot shed enabled plugins or directly configured MCP "
        "servers per call, and {0} mode leaves their own permission routes "
        "available; {1}.".format(mode, sign_in)
    )
    warnings.append(
        "ZCode receives the complete message in one --prompt argument, which "
        "may be visible to other processes under the same account or to logs."
    )
    return (
        "{0} {1}".format(runtime, script),
        version,
        described,
        "minimum local configuration needed to attempt a call is present",
        tuple(warnings),
    )


def check(
    deadline: Deadline, cwd: str, mode: str = "review",
    work_permission: Optional[str] = None,
) -> connectors.CheckResult:
    """Report whether ZCode could be used right now, spending no model turn.

    Provider paths are checked by metadata only and passed in this
    process's environment. A work-mode check proves the work vector's own
    switches, passing no review deny-list switch the work command never
    carries.
    """
    program, version, described, account, warnings = _prerequisites(
        deadline, cwd, work=(mode == "work"), work_permission=work_permission,
    )
    permission = (work_permission or "edit") if mode == "work" else "plan"
    shell = permission == "full-access"
    checked = connectors.readiness(
        HARNESS_ID,
        program,
        version,
        described,
        account,
        warnings,
        authentication_confirmed=False,
    )
    return checked._replace(
        message=checked.message + " Effective permission: {0}; headless shell {1}. "
        "This parser check ran no model turn, Git command, or test.".format(
            "Full access under your account" if shell else permission,
            "enabled" if shell else "not enabled",
        ),
        effective_permission=permission,
        headless_shell=shell,
        shell_execution_verified=False,
        selected_model_verified=False,
    )


def build_command(deadline: Deadline, cwd: str) -> connectors.PeerCommand:
    """The fixed argument vector for one review turn, prerequisites confirmed first.

    `cwd` is the directory the peer may read, from the command line only,
    and both where the program is started and what `--cwd` names. The body
    is not here: the runner binds it to `BODY_ARGUMENT` as the final
    argument, after its own refusals, and sends nothing on standard input.
    """
    runtime, script = _program()
    _program_name, _version, _described, _account, warnings = _prerequisites(
        deadline, cwd
    )
    return connectors.PeerCommand(
        argv=(
            runtime,
            script,
            "--mode",
            "plan",
            "--cwd",
            cwd,
            "--disallowed-tools",
            DENIED_TOOLS,
        )
        + OUTPUT_FORMAT,
        cwd=cwd,
        env=_environment(cwd),
        body_argument=BODY_ARGUMENT,
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
    work_permission: Optional[str] = None,
) -> connectors.PeerCommand:
    """The ZCode prompt with one explicit, per-call permission handoff.

    Two things from the review vector are deliberately absent: the tool deny
    list, because work needs the ordinary tool set, and `plan` mode. The mode
    a bare `--prompt` would get is `yolo`, which auto-approves everything,
    so the vector defaults to `edit`; an explicit `full-access` choice
    instead selects native `yolo` for this call. No global setting is
    changed. Declared access directories stay reachable as ordinary
    same-user paths; ZCode has no per-call switch that widens its working
    root. Each attachment becomes one `--attach`.
    """
    runtime, script = _program()
    _program_name, _version, _described, _account, warnings = _prerequisites(
        deadline, cwd, work=True, attachments=attachments, work_permission=work_permission,
    )
    argv = [runtime, script, "--mode", "yolo" if work_permission == "full-access" else "edit", "--cwd", cwd]
    for attachment in attachments:
        argv.extend(("--attach", attachment))
    argv.extend(OUTPUT_FORMAT)
    return connectors.PeerCommand(
        argv=tuple(argv),
        cwd=cwd,
        env=_environment(cwd),
        body_argument=BODY_ARGUMENT,
        warnings=warnings,
    )
