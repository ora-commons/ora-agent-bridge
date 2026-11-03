# Ora Agent Bridge — Courier and Work-Mode Interface

**Status:** Release 1 courier contract. All six targets have passed real
calls on macOS 26 arm64; the exercised CLI versions are listed in the
README. This document describes the current active system; Git history
owns construction history.

SPDX-License-Identifier: CC0-1.0

---

## 1. Purpose and boundary

Ora Agent Bridge is a standalone one-to-many Markdown courier: any
application or harness may initiate a session and make one bounded call at
a time to a supported target whose vendor CLI is installed, signed in, and
qualified.

It owns: target readiness; one request and one response per foreground
call; an ordered, human-readable Markdown record; the selected CLI's
strongest practical restriction posture with clear warnings about what it
cannot guarantee; one lock per session, atomic publication, and cleanup;
and plain failures with one useful next action. It does not plan,
coordinate, choose targets, combine answers, schedule work, judge
responses, manage Git, or enforce an application workflow, and it has no
model API layer, database, daemon, registry, or plugin discovery.

Applications own the meaning of what they send. To Bridge a plan, commit,
review, or decision in the body is inert text.

**Same-user trust boundary.** Each target CLI is a trusted program running
under the user's account. Bridge does not claim to stop it reading other
files the account can read and is not a confidentiality boundary against a
malicious harness or prompt injection. A target given a project may load
its `AGENTS.md`, `CLAUDE.md`, or equivalent instructions, which Bridge does
not suppress; target CLIs may keep plaintext transcripts, which Bridge
neither deletes nor hides. Repository instructions and message text cannot
alter the connector's fixed invocation, grant Bridge authority, or become a
Bridge command. Warnings are informational: they never stop an otherwise
usable call or create persistent consent.

---

## 2. Runtime and safe text transport

One standard-library Python implementation (3.9 floor) serves every
initiator and target. Every child starts from a fixed argument vector with
no shell. A connector uses standard input when its CLI has a stable
one-shot input path; otherwise it may bind the complete body into one
vendor option value only when qualification proves leading hyphens cannot
become options, the body is one argument in a directly executed vector,
NUL input and an oversize body are refused before request publication, and
documentation states that command-line text may be visible to same-user
processes and logs. The runner never creates a private prompt file; the
canonical request records the original exactly and the connector passes it
unchanged. Qwen's connector internally encodes the body as one stream-JSON
user frame whose decoded content is the original string (its stream reader
has no text-mode 8 MiB cutoff). Adapters start Bridge as
`python3 -m ora_agent_bridge <command> ...`.

---

## 3. Initiators and targets

An initiator supplies one inert ASCII slug (letter or digit first, then
letters, digits, periods, underscores, hyphens). It appears in `SESSION.md`
and message headers and is not authentication, authority, routing, or
registration; supporting another initiator needs no Bridge change.

There are exactly six literal target identifiers — `codex`, `claude`,
`zcode`, `hermes`, `minimax`, `qwen` — resolved by a branch-local six-way
switch that matches the identifier before importing the connector: no
eager import, name-derived import, search, registry, fallback, or retry.
For `check` and `run`, every unselected connector stays completely inert.
Codex, Claude Code, and ZCode are project-capable; Hermes Agent, MiniMax
Code, and Qwen Code are courier-only and receive a task-owned neutral
directory. One session has one initiator and one target; several targets
means several sessions.

---

## 4. Commands

```text
python3 -m ora_agent_bridge check --peer <target-id>
                 [--mode review|work] [--work-permission full-access] [--json]

python3 -m ora_agent_bridge run --session <session-directory>
                 [--timeout <seconds> | --no-timeout]
                 [--max-steps <positive-integer>]
                 [--require-model <provider/model>]
                 [--model <name>] [--effort <level>]
                 [--events-jsonl] [--work-input-jsonl]
                 [--work-permission full-access]
                 [--attachment <absolute-image-path>]...
                 [--note-ref <id>] [--purpose <label>]

python3 -m ora_agent_bridge record --session <session-directory>
                    --kind session-create --initiator <label> --peer <target-id>
                    [--project <project-directory>] [--mode review|work]
                    [--access-path <directory>]...

python3 -m ora_agent_bridge record --session <session-directory> --kind note
```

Run from the absolute checkout root; there is no console executable.
Omitting every extended option keeps the original Format 2 behavior byte
for byte.

### `check` and `check --json`

`check` finds the documented CLI, reads version and platform, checks
authentication as far as the CLI safely permits, and confirms the switches
the selected mode's vector needs exist — the review set by default, the
work vector's own switches with `--mode work` (an optional switch required
only when the session or request uses it; for Hermes and Qwen, whose work
route is unsupported, no switch qualification at all). It runs in a
task-owned neutral directory, touches no real project, and writes nothing
for next time. Success prints one readiness sentence plus `Warning:` lines
and exits 0; failure prints one reason and next action to standard error
and exits nonzero.

`--json` prints exactly one JSON object (one line, no schema version);
failure still exits nonzero, with the reason inside the object:

```json
{"peer": "codex", "mode": "work", "bridge_version": "1.7.0", "ready": true,
 "command_present": true,
 "executable": "/usr/local/bin/codex", "version": "0.155.1",
 "platform": "Darwin 26 arm64", "authentication": "confirmed",
 "review": "supported", "work": "supported", "image": "supported",
 "model_switch": "-m", "effort_switch": "-c model_reasoning_effort=<effort>",
 "compatibility": "supported",
 "warnings": ["...one concrete warning per line..."]}
```

| Field | Meaning |
|---|---|
| `peer`, `mode`, `bridge_version` | Selected target, posture checked, this copy's version. |
| `command_present` | Whether the target's command was found; `false` only when absent. |
| `ready` | Whether the selected mode's call mechanics are usable now. |
| `executable`, `version`, `platform` | Observed facts, or `null` when a failure preceded them. |
| `authentication` | `confirmed`, `required`, or `unknown` (no safe no-turn check; unknown is not failure when the route is usable). |
| `review`, `work`, `image` | `supported` (implemented, switches verified — transport support, with live acceptance stated in `warnings`), `unsupported` (genuine vendor limitation, named in `warnings` with the terminal route), or `unknown`. |
| `model_switch`, `effort_switch` | The switch a caller-named choice passes through on, exactly as the connector passes it; absent when the tool documents no such choice, which is the fact that a named choice is refused. |
| `compatibility` | `supported` (inside exercised release evidence), `unknown` (outside it or nothing established it; never auto-unsupported), `unsupported` (work check of a target whose work route is unsupported), `null` (a failure prevented establishing it). |
| `warnings`, `reason`, `next_action` | The concrete non-blocking warnings, plus the failure pair when `ready` is false. |

### `run`

`run` reads the session's initiator, target, and optional project, then
one nonempty Markdown body from standard input (`--work-input-jsonl` reads
it from one leading JSON line — section 4a). It resolves only that target,
repeats cheap prerequisites, validates transport, writes any `Warning:`
lines to standard error immediately before publishing the request, starts
exactly one target CLI invocation, captures the final text, and atomically
publishes the response. Every run starts a fresh vendor context: nothing is
resumed and no earlier Bridge message is resent.

- **`--max-steps`, `--require-model`** — run-only MiniMax controls,
  refused for every other target before request publication. An explicit
  positive step bound replaces MiniMax's default of one assistant step for
  that call only (forwarded in work sessions when given, omitted
  otherwise). A required model switches MiniMax's output to JSON; Bridge
  accepts only a successful schema-version-1 `exec.result` whose
  `providerId/modelId` is byte-for-byte identical, publishes the final
  string output, and preserves the validated identity as optional `model`
  and `provider` fields on the `finished` event and `Model:`/`Provider:`
  header lines on a Format 3 response (section 7).
- **`--model`, `--effort`** — name a model or reasoning effort for one
  call, in work and review sessions alike, passing through only the
  target's own documented switch — Codex `-m` and
  `-c model_reasoning_effort=…`, Claude Code `--model` and `--effort`,
  MiniMax `--model` — exactly as named. A target that documents no switch
  for the choice (ZCode, Hermes, Qwen for either; MiniMax for effort)
  refuses it before publication with that plain fact. Bridge chooses,
  completes, and substitutes nothing; a named model the account rejects at
  use is the target's own reported failure. For Codex the choices are
  reported back: the connector takes the thread id from `thread.started`,
  reads the `model` and `effort` from the `turn_context` line of Codex's
  own session file for that thread, and the saved reply shows requested and
  recorded values, saying `not reported` for an unreadable half.
- **Waiting** — ordinary work has no deadline. `--timeout <seconds>` is a
  bounded wait, not a kill: when it ends the command returns control with
  the target still running and reports the retained evidence (session and
  published request, the peer's process and any session reference it
  printed, sanitized partial output), exiting nonzero — on standard error,
  or as fields of the final `finished` event with `--events-jsonl`.
  `--no-timeout` names the default and cannot be combined with `--timeout`.
  Cleanup has its own bounded grace. On Windows a retained or detached
  target ends when the owning Bridge process exits. There is no retry.
- **Refusals before publication** — a courier-only session with a project;
  a work session whose target declares work unsupported (with the real
  limitation and terminal route in the message); `--note-ref`/`--purpose`
  on a Format 2 session; `--attachment` outside a Format 3 work session
  whose target has a qualified image route, or naming a missing or
  non-absolute file.
- **`--note-ref`, `--purpose`** — two inert single-line strings recorded in
  a Format 3 request header, stored without interpretation.
- **`--attachment`** — one absolute image file per use, recorded as one
  `Attachment:` header line. The request body is never modified to mention
  an attachment; where the route needs the path in the text (Claude), the
  caller's body must name it and the run warning says so.
- **Output** — success without `--events-jsonl` writes only the response
  path to standard output and exits 0. A target failure after publication
  leaves the truthful request, invents no response, and exits nonzero with
  the failure and next action on standard error.

### `record`

The only local writer besides `run`. Substantive text comes from standard
input (empty input is a usage error); success prints the canonical path.
`--mode` creates a Format 3 session; `--access-path` records additional
existing absolute directories, requires `--mode`, and is refused with
repeats; a work session may omit `--project` when no code directory exists
yet; a note accepts none of the session-creation options.

---

## 4a. Work mode, lifecycle events, and live input

A work session — `record --kind session-create --mode work` — is an
explicit alternative to the restricted review call. Its mode, target,
working directory, and access directories are immutable from creation;
message text cannot change them. Work mode is not a security override:
only `--work-permission full-access` on one ZCode `run` selects native
Full access for that call, and selecting work mode is not permission for
publication, purchases, login, or unrelated effects. Each call still
starts a fresh native context; a failed call leaves truthful records and,
if the invocation might have edited files before failing, says so.

With `--events-jsonl`, standard output carries one flushed JSON object per
line: `started`; `heartbeat` check-ins while prerequisite probes or the
peer call wait (a check-in says Bridge is alive and whether the child
runs — never progress); `wait_expired`; and exactly one `finished` event
carrying `outcome` (`success`, `failure`, `stopped`, `pending`),
`request_path`/`response_path` when published, warnings, a plain
`reason`/`next_action` on failure, the retained evidence fields on
`pending`, and optional `model`/`provider` when the target's structured
output reported the identity that answered (absent when it did not, never
empty). If the event pipe breaks the turn runs on and its records decide
the truth: a broken pipe can never turn an unfinished call into a reported
success.

`--work-input-jsonl` adds live input to one Codex work turn. It is refused
with the usage error `work input requires an unbounded Codex work call
with lifecycle events` unless the session is a Format 3 work session whose
target is `codex`, `--events-jsonl` is on, and no `--timeout` is named.
Standard input becomes a JSON-line protocol: the first line's `body`
string is the request; each later line is one live message carrying a
nonempty `text` and the active `thread_id`/`turn_id`. Bridge starts
`codex app-server`, opens one thread, and starts exactly one turn (named
`--model` to the thread, `--effort` to the turn; server-initiated approval
requests are declined, since this foreground route has no approver;
declared access paths join the writable roots under workspace-write). One
`work_input_ready` event carries the turn's ids. Each eligible message
travels as a native `turn/steer` for exactly that turn, echoed by a
`work_input` event with status `accepted` (Codex acknowledged; not proof
of compliance), `refused` (stale or mismatched, refused locally; or Codex
answered an error or a different turn), or `unknown` (turn finished or
connection ended before acknowledgment; Bridge never resends). Every live
message is retained in order as a numbered initiator record whose body is
one `Work-Input-JSON:` line. The turn's final agent message is published
as the response and parsed like any Codex answer; an uncompleted turn or
an app-server that ends early is a peer failure carrying Codex's own
reason.

---

## 5. Neutral local records

| Kind | Required | Optional | Result |
|---|---|---|---|
| `session-create` | `--initiator <label>`, `--peer <target-id>` | `--project <dir>`, `--mode review\|work`, repeatable `--access-path` (with `--mode`) | Creates `SESSION.md`; allocates no number |
| `note` | none | none | One numbered initiator record |

The session body describes the session; a note may hold anything, and
Bridge interprets neither.

---

## 6. Session record

```text
<session>/
  SESSION.md
  messages/    0001-initiator-to-peer.md  0002-peer-to-initiator.md  ...
  .lock
```

`.lock` holds no canonical state. Sessions normally live under
`~/.agent-bridge/sessions/`, outside Git and cloud sync. `SESSION.md` is
written once:

```markdown
# Session

Bridge-Format: 2
Initiator: ora
Peer: claude
Project: /absolute/path

## Body

<application-supplied description>
```

`Project:` is omitted when absent; a project path must be absolute, exist
at creation, and never come from a peer message. An explicit `--mode`
creates Format 3, adding a required `Mode: review|work` line and optional
repeatable absolute `Access-Path:` lines; a work session may omit
`Project:`.

Reading is loose about what only a hand edit could disturb — unrecognized
header lines, repeated fields (last wins), odd spacing, either format's
fields in either file — and strict about the facts the session's safety
rests on: an unsupported `Bridge-Format`, a Format 3 file with no usable
`Mode`, a missing required field, an unknown target, and a non-absolute
recorded directory are all rejected. Nothing below the first blank line is
ever read, so body text can never change the target or mode. A Format 2
session always reads as review mode with no access paths; no file is ever
migrated. Message numbers increase under the lock and are never reused; a
failed call may leave a request as the final message — an incomplete
exchange, not corruption.

---

## 7. Envelope and Bridge-inert body

The runner writes every header; initiators and targets supply only body
text, byte-exact below `## Body`. Format 3 requests may add `Note-Ref:`,
`Purpose:`, and per-file `Attachment:` lines. A Format 3 response names
the request it answers (`Answers: 0001`) and, when the target's structured
output reported the identity that answered, carries `Model:`/`Provider:`
lines (each half only when reported, never empty). A Codex response
reports the call's choices in either format: `Model:`/`Effort:` carry what
Codex recorded, `Requested-Model:`/`Requested-Effort:` appear when the
caller named a choice. Notes carry `Record:` and `From:` headers; each
live message of a live-input turn is a note with one `Work-Input-JSON:`
body line.

Header-shaped text below `## Body` remains body text: it cannot change
Bridge identity, target, project, mode, number, kind, restrictions,
authority, routing, or become a Bridge command. A caller can read
everything it needs from the retained records alone — session mode and
access paths, each request's exact body and inert metadata, each
response's body, answered sequence, and reported identity lines, and each
live message's delivery status. Bridge owns this readable surface and will
keep it readable.

---

## 8. Connectors, warnings, and qualification

Each connector is a small source-controlled translation for one official
vendor CLI, declaring its CLI identity and exact tested versions, tested
platform when relevant, no-turn authentication evidence, fixed one-shot
arguments and body transport, strongest practical restrictions with
concrete residual warnings, project-capable or courier-only posture, and
final-response extraction. A connector passes only what the caller names —
a caller-named model or effort travels on that tool's own documented
switch and nothing else does — and chooses no model, effort, provider,
endpoint, or credential; installs, updates, or signs in to nothing;
resumes no vendor session; falls back to no API, desktop endpoint, or UI.

`run` repeats cheap prerequisites. Missing software, reported missing
authentication or minimum local authentication state, unusable one-shot
input or final output, an absent required switch, or inability to control
the foreground child stops before a real call. Version or platform drift
alone warns and proceeds when the required mechanics remain usable.

Warnings state fixed limits rather than per-call lookups: Bridge does not
re-probe policy sources, plugin inventories, or posture files on every
call, and a policy combination that makes a fixed call unusable fails with
the vendor CLI's own error after the request is saved.

| Target | Posture | Strongest practical restrictions |
|---|---|---|
| Codex | Project-capable | Review: skip `$CODEX_HOME/config.toml`, disable known high-risk routes, read-only sandbox, explicit working root. Work: ordinary configured `codex exec`, no sandbox switch — codex resolves and enforces the effective posture at run time. |
| Claude Code | Project-capable | Review: restricted mode, empty strict MCP set, Read/Glob/Grep only, planning mode. Work: normal `--print`, no permission flag. |
| ZCode | Project-capable | Review: planning mode, explicit directory, dangerous tools removed by a hard-enforced deny list. Work: edit mode (or per-call Full access). |
| Hermes Agent | Courier-only | Neutral directory, safe mode, smallest harmless toolset; subscription-only (a signed-in Portal the configuration actually selects). |
| MiniMax Code | Courier-only | Neutral directory, `exec` on standard input, `--permission smart`, one assistant step by default or an explicit bound; optional exact returned-model validation. |
| Qwen Code | Courier-only | Neutral directory, safe and plan modes, pinned native macOS sandbox selection/profile, zero model tool calls, one input frame and turn, pre-model command limits. |

### Vendor findings recorded by probing

These findings, established by probing the installed CLIs rather than
reading documentation, are why the connectors are shaped as they are:

- **Codex** prints `Logged in using ChatGPT` on the error stream, so the
  sign-in answer is read from the exit status. A trusted project's own
  `.codex/config.toml` can change the posture codex enforces (observed
  flipping an empty top level from read-only to workspace-write through
  that layer alone); `codex doctor --json` reports no effective-posture
  field and probes the network, and `codex debug prompt-input` writes
  state into `CODEX_HOME` — neither is a safe pre-flight posture source. A
  legacy top-level `profile = "..."` makes codex itself refuse to run.
- **Claude Code 2.1.251** exits when `--strict-mcp-config` meets the
  administrator-managed MCP source at `/Library/Application
  Support/ClaudeCode/managed-mcp.json`; that combination, when it occurs,
  is Claude Code's own failure to report after the request is saved.
- **ZCode 0.16.5** has no standard-input path for a one-shot prompt
  (`--prompt -` is a literal dash; `--attach /dev/stdin` is refused as a
  device file), so the body is bound as `--prompt=<body>`: a bare `--`
  makes the body a positional ("Unknown command"), a hyphen-leading value
  after `--prompt` is "argument is ambiguous", and the attached form comes
  back byte for byte. Four switches `--help` advertises do not exist on
  the build (`--settings`, `--max-turns`, `--allowed-tools`,
  `--permission-mode`), so readiness proves its switches by passing them
  to the no-turn `version` subcommand. A deny-list entry reduces to the
  name before any parenthesis (`Bash(git *)` removes the whole Bash tool),
  and an MCP tool is removed only by its exact `mcp__<server>__<tool>`
  name. Sign-in is a coding-plan key in the configuration file — pointing
  `ZCODE_DATA_BASE_DIR` at an empty directory left a headless turn
  working, so the credential store is not what authenticates it — and
  `zcode login` unconditionally starts a fresh OAuth flow.
- **Hermes Agent 0.18.2** switches tools in whole groups, and the group
  that reads a file is the group that writes one, so it is courier-only
  with no file tool. `-z -` hands the model a literal dash and an empty
  `-z` falls into the interactive banner, so the body travels as
  `--oneshot=<body>`, which the parser splits at the first `=`. A child
  Hermes reads `TERMINAL_CWD` in place of its own working directory, so
  that one name is dropped from the child environment.
- **MiniMax Code 0.2.7**'s source fixes an absent `--permission` at
  `smart`, and no runtime configuration can change the permission mode of
  a headless run; `ask` needs a TUI and is rejected by `mcode exec`
  itself.
- **Qwen Code 0.23.0** may preprocess recognized leading `/` commands and
  unescaped `@` references before the model in both headless input modes,
  before its zero model-tool-call budget; no lossless raw escape or switch
  exists. Safe mode still loads settings and `.env` values that can
  restore `SANDBOX` (bypassing the sandbox) or
  `QWEN_SANDBOX_PROXY_COMMAND` (launching a detached proxy shell), so
  those surviving routes are named as warnings. The `/bug`, `/config`,
  `/update`, `/import-config`, `/language`, `/effort`, `/model`, and
  `/doctor` pre-model command families are disabled; other recognized
  preprocessing remains.

### Work vectors and image routes

| Target | Work | Image route |
|---|---|---|
| Codex | `codex exec --cd <project>` (+ `--add-dir` per access path), no sandbox switch | Native `-i/--image` |
| Claude Code | Normal `claude --print --output-format text` (+ `--add-dir`), no permission flag | File-reading route: the body names the image's absolute path |
| ZCode | `--mode edit` (or per-call `--mode yolo` via `--work-permission full-access`) | Native `--attach` |
| MiniMax Code | `mcode exec --cwd <project>`, no permission policy; `--file` per attachment; `--max-steps` only when named | Native `--file` |
| Hermes Agent | Unsupported (one-shot `-z` auto-bypasses approvals by design); run `hermes` in its own terminal | Unsupported |
| Qwen Code | Unsupported (ordinary approvals cannot ask headlessly; unattended modes auto-approve); run `qwen` in its own terminal | Unsupported |

---

## 9. Locking, publication, and cleanup

One foreground turn holds a process-scoped advisory lock from sequence
allocation through publication or cleanup — `fcntl.flock` on the open
`.lock` descriptor on POSIX, `msvcrt.locking` over one byte of the same
file on Windows: one exclusive lock, held open by the running process,
refused rather than waited for, dropped by the operating system the moment
the holder ends. Contention changes no canonical file and is
`BUSY_SESSION`, immediately.

Each canonical file is completed in a temporary file beside its
destination, flushed, and atomically renamed, so a reader sees either no
file or a complete one. A stop is deferred for the whole life of the
temporary file, so no stop can end the process while a temporary file
exists with nothing arranging to clear it away; on a failure one
`PUBLICATION_FAILURE` honestly covers a failed or unknowable rename (the
message may not have arrived at its recorded name — look for the reported
file before running again), a failed removal of the leftover is
`CLEANUP_FAILURE`, and forcing the folder entry out after publication is
the one later failure, `PUBLICATION_NOT_FLUSHED`.

Each child belongs to the turn that started it: on POSIX it is its own
session leader and cleanup signals its group; on Windows it is created
suspended in a kill-on-close job object before its first instruction, so
everything it ever starts is owned and ends with the turn (the exact
phases are in `peer.py`). Timeout, interrupt, termination, and hangup
terminate and reap the owned set, remove task-owned temporary files, and
release the lock; cleanup failure visibly names what remains. `SIGKILL`
and power loss cannot clean up; on Windows the kill-on-close job means
the operating system itself ends the owned tree when Bridge dies
abruptly. No Windows machine is part of this qualification; the Windows
branches are covered by unit checks with the platform's primitives
simulated. A child that escapes its process group fails qualification if
observed. Off the main thread no handlers can be installed, so the
surrounding program's signal behavior applies. Retrying, where it happens
at all, is an application decision.

---

## 10. Internal failures

The core owns this internal list; connectors map vendor behavior into it.
The names are not a public compatibility surface. Every command reports
failures on standard error and exits nonzero, avoids false success, cleans
what the turn owns when possible, and supplies one next action; each
failure's exact wording lives in `errors.py`.

---

## 11. Initiating adapters

An adapter — harness skill, application wrapper, or server-side
integration running where target CLIs and sign-ins exist — supplies an
inert initiator label and fixed target; creates or reuses the
one-initiator, one-target session; hands one complete Markdown body to
`run`; reads the returned response path; adds a neutral note when needed;
and reports readiness, warnings, and failures without hiding them. The
adapter owns its UI, target-selection policy, multi-target work, context
assembly, plans, approvals, reviews, corrections, Git behavior, retry
decisions, and response interpretation. The six harness packages expose
courier, readiness, and neutral-record entry points through host
conventions, contain no planning or review product, never find or call
each other, and invoke the one shared Bridge.

**Adding an initiating host** needs only documentation, a skill, or a
launcher implementing those steps: use the source checkout as the runtime,
leave target choice with the caller, pass the complete body, surface
warnings and failures, retain the saved response, wait for cleanup, add no
target branch, register nothing, and keep application state out of Format
2.

**Adding a target** requires one hand-written connector module and literal
edits to `HARNESS_IDS` and `resolve` (plus `is_courier_only` when it
cannot receive a project), implementing the existing `check` and
`build_command` surface with an official stable CLI, a fixed no-shell
vector, standard-input transport where the CLI supports it, exact
final-response extraction, the strongest practical restrictions, and
specific residual warnings. Qualification declarations name only the CLI
versions and platforms actually exercised; without a separately authorized
disposable real call, the connector is implemented and untested, never
tested.

---

## 12. Checks

The ordinary checks are the two unit modules, run by hand from the
checkout root:

```text
/usr/bin/python3 -m unittest -v tests.test_fake_peer
python3 -m unittest -v tests.test_work_mode
```

The one live qualification per supported route is hand-run and spends real
model quota, so it is not part of ordinary test runs, and uses
subscription tools only:

```text
python3 -m tests.release_conformance qualify --peer <target> [--mode work]
```

It makes one real attached call in disposable fixtures — a synthetic Git
repository, an access directory outside it, and one distinctive attachment
image whose content appears nowhere in the request — waits for the answer
under Bridge's own owned cleanup, prints any model identity Bridge itself
reported in the reply header, and proves the repository byte-for-byte
unchanged afterwards on the review route, or holding exactly the one
authorized tracked-file edit on the work route. No real project, secret,
message, or publication is touched, and qualification leaves no runtime
state: no last-passed stamp, cache, or receipt exists, and a CLI outside
the declared evidence warns when the required mechanics still work. The
exact command vectors are pinned by the ordinary unit checks. Hermes and
Qwen declare no supported work route, so no work qualification is run or
claimed for them.
