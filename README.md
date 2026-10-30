# Ora Agent Bridge

Get a second opinion from the AI coding tools you already use.

Send a focused request from your app or coding assistant to another signed-in
coding tool, then bring its answer back. Use it to challenge a plan, review
work, or hand off a bounded task, with a readable record of the exchange.

Ora Agent Bridge is one of the Ora tools; Ora itself is at
https://ora-ai.app. The other products' pages: Ora AI Boost
(https://github.com/ora-commons/ora-adversarial-review), Ora Programming Loop
(https://github.com/ora-commons/ora-programming-loop), and Ora Vibe Coder
(https://github.com/ora-commons/ora-vibe-coder).

Ora Agent Bridge is a standalone one-to-many Markdown courier. Any application or
harness can use one shared checkout to make a foreground call to Codex, Claude
Code, ZCode, Hermes Agent, MiniMax Code, or Qwen Code through its official
vendor CLI. Bridge does not connect model APIs; an ordinary caller need not
be an agent.

## Capabilities

Bridge owns a deliberately small surface and does it reliably:

- Target readiness checks with concrete warnings, without a model call.
- One request and one response per foreground call. Ordinary work has no
  elapsed-time deadline: Bridge waits until the target answers or the caller
  stops the run, and a caller may instead name a bounded wait that returns
  control without stopping the target. Stopping a call still cleans up its
  owned processes; Bridge never retries.
- An ordered, human-readable Markdown record of every exchange under
  `~/.agent-bridge/sessions/`, outside Git and cloud sync.
- Two calling modes on one runtime:
  - **Review courier** (the original mode): a restricted, read-only review
    call with the strongest practical vendor safeguards for that CLI.
  - **Work mode** (opt-in, Format 3 sessions): ordinary project work under
    the tool's own normal permissions, with a project directory for
    project-capable tools, optional additional access directories, optional
    image attachments, machine-readable readiness JSON, and lifecycle events
    (`--events-jsonl`) for applications that watch a turn live. One further
    Codex-only extension, `--work-input-jsonl`, lets an application send
  follow-up messages while that work turn is still running. A message
  naming the active turn's `thread_id` and `turn_id` steers that one
  turn; a stale or mismatched message is refused locally before anything
  is sent. Each message is reported `accepted`, `refused`, or `unknown`;
  `accepted` means Codex acknowledged the steering, without proof it
  complied. No other target takes live input.
- A limited identity report: where the target's own structured output names
  the model that answered (MiniMax Code with `--require-model`), Bridge
  preserves that tool-reported identity in the finished event and the
  response record. For Codex, Bridge also reads the model and effort Codex
  itself recorded for the call from Codex's own session file and reports
  them in the response record beside the values the caller requested,
  saying `not reported` when the file or the values cannot be read; they
  are never inferred from the request. Bridge never selects or changes a
  model itself.

The six target identifiers are literal: `codex`, `claude`, `zcode`,
`hermes`, `minimax`, and `qwen`. Only the selected connector is imported or
examined; the other five stay inert, with no probe, process, project access,
login, network call, or fallback.

| Target | Project in review mode | Work mode |
|---|---|---|
| Codex | Project-capable | Supported |
| Claude Code | Project-capable | Supported |
| ZCode | Project-capable | Supported |
| Hermes Agent | Courier-only | Unsupported (courier only) |
| MiniMax Code | Courier-only | Supported |
| Qwen Code | Courier-only | Unsupported (courier only) |

Courier-only targets receive a neutral directory, never a project, so a
request to them must carry all needed evidence in the message body. Where a
tool cannot do non-interactive work under ordinary permissions (Hermes
Agent, Qwen Code), Bridge reports work unsupported with the real vendor
limitation instead of quietly weakening anything.

Every call starts a fresh tool context: no vendor session is resumed and no
earlier message is resent. Include needed history in the body.

## Limits

Bridge has no coordinator, router, scheduler, database, daemon, workflow
engine, Git gate, or background service. Applications own planning, review,
target selection, combining answers, Git, and response interpretation.

Bridge is not a confidentiality boundary: each target CLI is a trusted
program running under your own operating-system account and can read other
files that account can read. Complete confinement is not claimed. See
"Safety and warnings" below.

There is no support, maintenance, or future compatibility promise. Bridge
installs no vendor program, signs in to nothing, and has no API fallback.

## Tested and untested routes

Everything below was exercised on macOS 26 on Apple silicon (arm64). No
other platform qualification is claimed, and Windows is untested (see below).

Review-courier real calls passed for all six targets with these CLI
versions:

| Target | Exercised CLI version |
|---|---|
| Codex | 0.147.0 |
| Claude Code | 2.1.251 |
| ZCode | 0.16.5 |
| Hermes Agent | 0.18.2 |
| MiniMax Code | 0.2.7 |
| Qwen Code | 0.23.0 |

Work mode was live-qualified once per supported route in disposable
fixtures (a synthetic Git repository as the code directory, an access
directory outside it, and one distinctive attachment image), with these CLI
versions:

| Target | Exercised CLI version |
|---|---|
| Codex | 0.155.1 |
| Claude Code | 2.1.278 |
| ZCode | 0.16.9 |
| MiniMax Code | 0.2.7 |

Hermes Agent 0.21.3 and Qwen Code 0.23.0 were source-verified for the work
and image findings above; no work call is claimed for them.

Windows is untested. No Windows machine was used for any check. The
Windows-specific branches — the session lock (`msvcrt.locking`) and
child-process tree cleanup through a kill-on-close job object — are
exercised only by unit checks with the platform's primitives simulated on a
Mac. All connector qualification above is macOS.

A readable CLI version outside the exercised evidence may still work: when
the required mechanics remain usable, Bridge proceeds with a warning.

## Requirements

- Git, to clone the repository and select a release tag.
- Python 3.9 or later. Bridge uses only the Python standard library; no
  Python dependency installation is needed.
- The selected target's official CLI with its own working vendor sign-in.
  The ordinary executable names are `codex`, `claude`, `hermes`, `mcode`
  (MiniMax), and `qwen`. ZCode uses `node` and the bundle at
  `/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs` instead of a
  `zcode` command on `PATH`.

One note for ZCode: Bridge passes the installed app's own built-in provider
file and ZCode's native personal provider path through its per-process
`ZCODE_BUILTIN_PROVIDER_CONFIG_FILE` and
`ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` variables. Existing path choices and
`ZCODE_DATA_BASE_DIR` are preserved. Readiness checks that the built-in file
exists and is readable, without opening either provider file. Bridge writes
no launcher or global configuration.

## Get the source

Bridge installs from a public Git checkout. There is no package to install
and no `ora-agent-bridge` console command; the checkout is the installation:

```sh
git clone https://github.com/ora-commons/ora-agent-bridge.git
cd ora-agent-bridge
git checkout v1.6.1
```

`v1.6.1` is this release's tag. Run every Bridge command from the absolute
checkout root with `python3 -m ora_agent_bridge ...`. These instructions need no
private repository and no personal settings.

## Identify your version

Open `ora_agent_bridge/__init__.py` in the checkout. The `VERSION = "1.6.1"` line
names the release you have. For a clean release checkout, the Git tag you
selected (`git checkout v1.6.1` above) and that line agree. There is no
`--version` command.

## A complete work-mode walkthrough

This example uses a disposable project, asks Codex for one file with
specified contents, then checks the file on disk and the saved answer. Run
every command from the checkout root. The `run` step is a real model call:
it can take minutes and consume the target tool's quota.

Create the disposable project and check that Codex can take work calls
right now (no model call, no project touched):

```sh
mkdir -p /tmp/agent-bridge-demo
python3 -m ora_agent_bridge check --peer codex --mode work
```

Read the readiness result and any `Warning:` lines. If it fails, resolve
the reported problem before proceeding.

Create a work session bound to one initiator label, one target, and the
project directory. The initiator is an inert record label, not
authentication; `my-app` works for any caller. These fields are immutable
once created:

```sh
python3 -m ora_agent_bridge record \
  --session "$HOME/.agent-bridge/sessions/demo-work" \
  --kind session-create --initiator my-app --peer codex \
  --mode work --project /tmp/agent-bridge-demo <<'MARKDOWN'
A disposable work session for one small file task.
MARKDOWN
```

Omit `--project` only when no code directory exists yet. Add
`--access-path` (repeatable) when the request needs additional existing
directories. Use a different session for a different target or project.

Send one self-contained request and wait in the foreground:

```sh
python3 -m ora_agent_bridge run \
  --session "$HOME/.agent-bridge/sessions/demo-work" <<'MARKDOWN'
Create the file /tmp/agent-bridge-demo/bridge-greeting.txt containing
exactly one line and nothing else:

Bridge work mode says hello.

Then answer with the file's path and the exact contents you wrote.
Create and modify no other file.
MARKDOWN
```

On success, standard output is the absolute path of the response record.
Now check the work product and the saved answer:

```sh
cat /tmp/agent-bridge-demo/bridge-greeting.txt
```

which should print exactly:

```text
Bridge work mode says hello.
```

and:

```sh
cat "$HOME/.agent-bridge/sessions/demo-work/messages/0002-peer-to-initiator.md"
```

which shows a record like this (the answer text itself varies; the file on
disk is the canonical answer):

```markdown
# Message 0002
From: codex
To: my-app
Answers: 0001

## Body

I created /tmp/agent-bridge-demo/bridge-greeting.txt with exactly the one
requested line: Bridge work mode says hello.
```

The session folder holds `SESSION.md` (the immutable binding), a numbered
request and response under `messages/`, and the lock used during the turn.
A work call runs under the tool's ordinary capabilities and its own normal
permissions. For ZCode, work defaults to Edit; an explicit
`--work-permission full-access` on `run` selects native Full access
(`--mode yolo`) for that turn only, allowing shell commands for approved
Git work and tests under your account. The same option on
`check --peer zcode --mode work --json` checks that exact launch mode and
reports `effective_permission`, `headless_shell`, and
`shell_execution_verified: false`: it runs no model turn, Git command, or test.
The option is refused for other tools and review calls and changes no global setting. If the tool cannot complete the call, the request remains as an
honest record, no response is invented, and the failure names the reason
and one next action.

To preserve information without a model call, add a neutral note:

```sh
python3 -m ora_agent_bridge record \
  --session "$HOME/.agent-bridge/sessions/demo-work" --kind note <<'MARKDOWN'
The demo finished; the disposable project can be deleted.
MARKDOWN
```

Delete `/tmp/agent-bridge-demo` and the session folder when you are done
with them.

## The review courier

The original mode asks a project-capable or courier-only target for a
restricted review answer. Create a session without `--mode` (a Format 2
session), optionally with `--project` for Codex, Claude Code, or ZCode:

```sh
python3 -m ora_agent_bridge record \
  --session "$HOME/.agent-bridge/sessions/first-look" \
  --kind session-create --initiator my-app --peer codex <<'MARKDOWN'
Trying Agent Bridge for the first time.
MARKDOWN

python3 -m ora_agent_bridge run --session "$HOME/.agent-bridge/sessions/first-look" <<'MARKDOWN'
In one sentence, what is a Markdown courier?
MARKDOWN
```

Read the answer below `## Body` in the printed response path. Omit
`--project` for Hermes, MiniMax, and Qwen and include needed evidence in
the body instead. `check --peer <target>` (without `--mode`) checks the
review vector. The complete Format 2 and Format 3 contract, including the
per-target work and image routes, is in [INTERFACE.md](INTERFACE.md).

## Output, failures, and waiting

Keep the two output streams separate; a warning is not the answer path.
`check` success writes a readiness sentence and any `Warning:` lines to
standard output; `check --json` writes one JSON readiness object instead.
`run` success writes only the response-file path to standard output
(`--events-jsonl` replaces it with one JSON event per line and a final
`finished` event carrying the path); warnings and failures go to standard
error. Any failure exits nonzero with a reason and one next action on
standard error — show all of it, not just the last line. Surface every
warning without asking for acknowledgment.

`run` has no deadline for ordinary work: it waits until the peer answers, the
peer fails, or the caller stops the run. Readiness checks keep a bounded
deadline of their own. `--timeout <seconds>` names a bounded wait instead of a
kill: when the wait ends, the command returns control, leaves the peer running,
and reports the retained evidence — the session and published request, the
peer's process and any session reference it printed, and sanitized partial
output. On Windows a retained or detached target ends when the owning Bridge
process exits; macOS and Linux are unaffected. The explicit `--no-timeout`
option is the default behavior and cannot
be combined with `--timeout`. Keep the caller attached until the answer or stop
and cleanup. A target failure
after publication leaves the truthful request and invents no response. Do
not turn an uncertain publication into success or automatically retry it.

Only a session targeting MiniMax may add `--max-steps <positive-integer>`
or `--require-model <provider/model>` to `run`. The second makes Bridge
require a byte-exact runtime provider/model identity in MiniMax's JSON
result and then publish that tool-reported identity beside the answer (the
`Model:` and `Provider:` lines described above). Either option is refused
for every other target before request publication.

## Calling Bridge from an application

An application needs no harness skill, SDK, registration, or Bridge code
change. Use a fixed argument list and the absolute checkout as working
directory, never a shell-built command string:

```python
import subprocess

checked = subprocess.run(
    ["python3", "-m", "ora_agent_bridge", "check", "--peer", user_selected_target],
    cwd="/absolute/path/to/ora-agent-bridge",
    capture_output=True, text=True,
)
```

Show both streams; honor the exit status; read a response file only on
exit 0. Supply bodies on standard input. Adding another initiating host is
documentation and a launcher, not a Bridge change; adding a target is a
bounded runtime change — both procedures are in [INTERFACE.md](INTERFACE.md).

## Optional skills for coding tools

Six optional skill sources ship in this repository:
[Codex](packages/codex/SKILL.md), [Claude](packages/claude/SKILL.md),
[ZCode](packages/zcode/SKILL.md), [Hermes](packages/hermes/SKILL.md),
[MiniMax](packages/minimax/SKILL.md), and [Qwen](packages/qwen/SKILL.md).
Each tells its host how to use the same checkout, surface warnings and
failures, send a body, read an answer, and record a note. They are
guidance, not separate runtimes, and never call each other.

To use one, copy that `SKILL.md` into your coding tool's skills folder by
hand (for example `~/.claude/skills/ora-agent-bridge/SKILL.md` for Claude
Code, or `~/.zcode/skills/ora-agent-bridge/SKILL.md` for ZCode, following your
tool's own skill conventions). Skills are copied by hand and never
synchronized: updating the checkout does not update an installed copy, and
Bridge never writes into a skills folder. A coding tool used only as a
target needs no skill at all.

## Update

Let every Bridge command using the checkout exit first, then from the
checkout root:

```sh
git fetch --tags
git checkout v1.1.1   # the release you are moving to
```

(Use the actual new tag; check the repository's tags or releases for the
latest.) Repeat `check` for your target before the next call. If you
copied a skill, replace the installed copy by hand from the same revision;
Bridge does not synchronize installed copies. Do not use Git commands to
overwrite local edits you want to keep.

## Remove

Let every Bridge command using the checkout exit, then delete the checkout
folder and any skill folders you copied from it. Removing Bridge does not
remove a vendor CLI, sign out of a vendor account, change vendor
configuration, or delete session directories elsewhere. Keep or remove
`~/.agent-bridge/sessions` separately according to whether its
human-readable records are still needed.

## Report a problem

Report problems at the repository's issue tracker:
https://github.com/ora-commons/ora-agent-bridge/issues — include the command
you ran, both output streams, the exit status, and the CLI version.
Bridge is published as-is, with no dedicated support channel, no
maintenance commitment, and no compatibility promise; issues are read on a
best-effort basis only. Warnings that name a vendor limitation usually
describe that vendor's design, not a Bridge defect.

## Corrections and withdrawals

If a release is corrected, the correction ships as a new version with a
new tag (for example v1.1.1) and release notes that say what changed; the
current README always describes the current release. A withdrawn release
is withdrawn from the releases page, but earlier versions remain reachable
at their existing Git tags, and every published release notes its
exercised platforms and tools. Check
https://github.com/ora-commons/ora-agent-bridge/releases before installing to
see the current version and any correction notes.

## Safety and warnings

Call only vendor CLIs you trust. Each is a program running under your own
account: Bridge cannot stop it reading other files that account can read.
A project target may load `AGENTS.md`, `CLAUDE.md`, or equivalent
instructions. Vendor CLIs may keep plaintext transcripts; Bridge neither
suppresses repository instructions nor deletes or hides those logs.

Review calls use each connector's strongest practical vendor safeguards
and warn about the remaining limits; work calls use the tool's ordinary
capabilities under its own normal permissions, keep every warning that
still applies, and add the ones that matter to work. ZCode Full access requires the explicit per-call option described above;
ordinary ZCode work keeps Edit and review keeps Plan. Other tools retain
their normal permissions — for Codex Bridge reports the apparent
posture read from the top of the user's config in a warning and lets
codex's own run-time enforcement govern. Warnings do not block a usable
call, require approval, or store consent.

Transport facts worth knowing: ZCode and Hermes receive the entire body as
one bound command-line option, visible to other same-user processes and
potentially to system and vendor logs; NUL and oversize bodies are refused
before publication. The other four connectors use standard input. Qwen
Code 0.23.0 alone may preprocess recognized leading `/` commands or
unescaped `@` references before the model (altering the effective prompt,
appending readable file content, failing, or handling a command itself);
its zero model-tool-call limit does not stop that preprocessing, and no
raw switch exists. Bridge records and passes the original body unchanged
and warns; never claim Qwen's model saw it unchanged. For Qwen, safe mode
still loads settings and `.env` values that can bypass its sandbox or
start a detached proxy shell; Bridge names those surviving routes as
warnings. Claude Code's exact managed MCP source is incompatible with its
strict-MCP review invocation and fails as a prerequisite, not a warning.

[INTERFACE.md](INTERFACE.md) details every connector's limits, the
per-target work and image routes, and the release criteria.

## License

SPDX-License-Identifier: CC0-1.0

First-party Ora Agent Bridge material is dedicated to the public domain under
[CC0 1.0 Universal](LICENSE). Third-party command-line programs, services,
accounts, trademarks, configurations, and transcripts are not included in
that dedication; see [NOTICE](NOTICE).
