# Ora Agent Bridge — Courier and Work-Mode Interface

**Status:** Release 1 courier contract, approved September 3, 2026. All six targets have passed real calls on macOS 26 arm64, including Qwen's corrected stream transport. The exercised CLI versions are listed in the README. The opt-in working mode (Format 3 sessions, JSON readiness, lifecycle events, per-connector work vectors and image routes) was added September 27, 2026 under the authorized work-mode plan; its live qualification evidence is in section 8a. Qualification does not by itself complete the release finish line in section 12.

This is the controlling boundary for the shared runner, six target connectors, and thin initiating adapters. It replaces the former exactly-four-target rule and the former rule that incomplete confinement stopped release.

SPDX-License-Identifier: CC0-1.0

---

## 1. Purpose and boundary

Ora Agent Bridge is a standalone one-to-many Markdown courier. Any application or coding-agent harness may initiate a session and make one bounded call at a time to a supported target whose vendor CLI is installed, signed in, and qualified.

Ora Agent Bridge owns:

- target readiness;
- one request and one response per foreground call;
- an ordered, human-readable Markdown record;
- the selected CLI's strongest practical restriction posture and clear warnings about what it cannot guarantee;
- one lock per session, atomic publication, deadlines, and cleanup; and
- plain failures with one useful next action when a call cannot work.

It does not plan, coordinate, choose targets, combine answers, schedule work, judge a response, run a Programming Loop, approve anything, manage Git, or enforce an application workflow. It has no model API layer, database, daemon, task ledger, plugin discovery, application registry, or dynamic connector registry.

Applications own the meaning of what they send. Ora Vibe Coder or any other caller may put plans, commits, reviews, decisions, or state in the Markdown body. To Ora Agent Bridge those remain inert text.

### Same-user trust boundary

Ora Agent Bridge treats each target CLI as a trusted program running under the user's operating-system account. It does not claim to stop that program reading other files the account can read, and is not a confidentiality boundary against a malicious harness or prompt injection.

A target given a project may load its `AGENTS.md`, `CLAUDE.md`, or equivalent instructions. Ora Agent Bridge does not suppress them. Target CLIs may also keep plaintext transcripts; Ora Agent Bridge neither deletes nor hides them.

Repository instructions and message text cannot alter the connector's fixed invocation, grant Bridge authority, or become a Bridge command. Users should call only harnesses they trust and should not use Bridge when same-user read access is unacceptable.

Bridge states remaining limits as warnings during `check` and immediately before a warned `run` publishes its request. Warnings are informational: they do not stop an otherwise usable call, ask for acknowledgment, require an approval switch, or create persistent consent.

---

## 2. Runtime and safe text transport

One standard-library Python implementation serves every initiator and target. Python 3.9 is the floor; release evidence names the tested operating-system versions and architectures.

Every child starts from a fixed argument vector with no shell. A connector uses standard input when its CLI has a stable one-shot input path. Otherwise it may bind the complete body into one vendor option value only when qualification proves:

1. Leading hyphens cannot become options.
2. The body is one argument in a directly executed vector, never a shell fragment.
3. NUL input and a body too large for the platform argument block are refused before request publication, never truncated or split.
4. Documentation states that command-line text may be visible to same-user processes and system or vendor logs.

The runner never creates a private prompt file. The canonical request records the original exactly and the connector passes it unchanged.

Qwen's connector internally encodes that body as one stream-JSON user frame whose decoded content is the original string, then closes standard input. It supplies no initial query, prompt argument, control request, history, or additional frame. This avoids Qwen's text-input conversion into its sandbox shell arguments. The public body remains plain Markdown; only the connector translates framing. Qwen's stream reader has no text-mode 8 MiB cutoff; no unlimited-memory guarantee is implied.

Adapters start Bridge as a fixed vector:

```text
python3 -m ora_agent_bridge <command> ...
```

---

## 3. Initiators and targets

### Initiators

Any application or harness may initiate. It supplies one inert ASCII slug beginning with a letter or digit and continuing with letters, digits, periods, underscores, or hyphens.

The label appears in `SESSION.md` and message headers. It is not authentication, authority, routing, discovery, registration, or proof that the initiator is callable. Examples include `ora`, `gear-3`, `vibe-coder`, `codex`, and `my-app`.

Supporting another initiator never requires a Bridge code change or release.
The initiator starts the existing fixed command vectors from the checkout root,
supplies complete Markdown through standard input, keeps standard output,
standard error, and exit status distinct, and reads a response only after exit
0. For example, Vibe Coder may use `vibe-coder` as the label and pass its
prepared handoff to a user-selected existing target. Vibe's lifecycle and
approval behavior remain Vibe's; no `vibe-coder` connector belongs here.

### Callable targets

Release 1 has exactly six target identifiers:

```text
codex   claude   zcode   hermes   minimax   qwen
```

They are literal source entries resolved by a branch-local six-way switch. Each branch matches its identifier before importing that connector. There is no eager import, name-derived import, search, registry, generator, marketplace, provider fallback, or attempt to try another target.

For `check` and `run`, every unselected connector remains completely inert: no import, inspection, probe, process, project access, login, network call, or fallback. Target validation during `record` imports no connector.

An initiator label may equal a target identifier, but that neither calls anything nor grants target status. Only the immutable `Peer:` session field selects a connector.

One session has one initiator and one target. An application reaches several targets through separate sessions; Bridge does not fan out or coordinate them. Codex, Claude Code, and ZCode are project-capable. Hermes Agent, MiniMax Code, and Qwen Code are courier-only and receive a task-owned neutral directory with no project path.

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
                 [--events-jsonl]
                 [--work-permission full-access]
                 [--attachment <absolute-image-path>]...
                 [--note-ref <id>] [--purpose <label>]

python3 -m ora_agent_bridge record --session <session-directory>
                    --kind session-create
                    --initiator <label>
                    --peer <target-id>
                    [--project <project-directory>]
                    [--mode review|work]
                    [--access-path <directory>]...

python3 -m ora_agent_bridge record --session <session-directory>
                    --kind note
```

Run these commands from the absolute checkout root. Source installation creates no `ora-agent-bridge` console executable.

Omitting every extended option keeps the original Format 2 behavior byte for byte: the same records, the same stdout, the same review vectors. Each extension below is opt-in.

### `check`

`check` determines whether a target can be used now. Without a model call it finds the documented CLI, reads version and platform, checks authentication as far as the CLI safely permits, and confirms that the switches needed for fixed input, output, foreground control, and the connector's strongest practical posture still exist. `--mode work` qualifies the work vector's own switches instead of the review vector's — an optional switch (`--add-dir`, `--image`, `--attach`, `--file`, `--max-steps`, or a named `--model`/`--effort` choice's switch) is required only when the session or request actually uses it — keeps the same executable, version, platform, and authentication questions, and reports the connector's work and image capability facts; it changes nothing else. For a target with no work vector (Hermes, Qwen), the work-mode check performs no switch qualification at all and its readiness sentence claims none: work is reported unsupported from the source-grounded reasons in the capability fields, with the terminal route in `warnings`.

It runs in a task-owned neutral directory and never touches a real project, installs, signs in, selects a model or provider, or writes qualification state. Success writes one readiness sentence followed by any applicable `Warning:` lines to standard output, leaves standard error empty, and exits 0; warnings do not change that status. A hard failure writes one reason and next action to standard error and exits nonzero. No other connector is imported or examined.

#### `check --json`

`--json` replaces the prose with exactly one JSON object on standard output, one line, no schema version. Success and failure alike produce one object; a failure still exits nonzero.

```json
{"peer": "codex", "mode": "work", "bridge_version": "1.6.0", "ready": true,
 "command_present": true,
 "executable": "/usr/local/bin/codex", "version": "0.155.1",
 "platform": "Darwin 26 arm64",
 "authentication": "confirmed",
 "review": "supported", "work": "supported", "image": "supported",
 "model_switch": "-m", "effort_switch": "-c model_reasoning_effort=<effort>",
 "compatibility": "supported",
 "warnings": ["...one concrete warning per line..."]}
```

| Field | Values and meaning |
|---|---|
| `peer`, `mode` | The selected target and the posture checked (`review` or `work`). |
| `bridge_version` | Version of the Bridge copy that produced this object. |
| `command_present` | Whether the target's local command was found. `false` only when the CLI is absent; any other failure implies the program was found. |
| `ready` | Whether the selected mode's call mechanics are usable now. A successful local check does not prove the selected model will answer or that every future permission is available. |
| `executable`, `version`, `platform` | Observed facts about the installed program, or `null` when a failure happened before they could be read. |
| `authentication` | `confirmed` (the CLI reported a sign-in), `required` (the CLI reported none; the object carries the reason), or `unknown` (this CLI offers no safe no-turn check; unknown is not failure when the route is usable). |
| `review` | The implemented analytical action, `supported` for every connector. Present on failure paths too, so a failed sign-in does not erase implemented capabilities. |
| `work`, `image` | `supported` (a route is implemented and its switches verified present), `unsupported` (a genuine vendor limitation, named in `warnings` with the terminal route), or `unknown`. Supported means transport support; whether the model accepted a live work call or image is stated in `warnings`, which distinguish verified evidence from unverified acceptance. |
| `model_switch`, `effort_switch` | The switch a caller-named `--model` or `--effort` choice passes through on for this tool, exactly as its connector passes it — today Codex `-m` and `-c model_reasoning_effort=<effort>`, Claude Code `--model` and `--effort`, MiniMax `--model`. Present only when the tool documents that choice; the field's absence is the fact that a named choice is refused for that target. A source declaration of the connector, read without a model call. |
| `compatibility` | `supported` (observed executable version and platform are inside the connector's exercised release evidence), `unknown` (outside that evidence, or nothing established it — a newer or legacy version is never auto-unsupported), `unsupported` (a work-mode check of a target whose work route is unsupported), or `null` when a failure prevented establishing it. |
| `warnings` | The same concrete non-blocking warnings the prose interface prints, plus the capability-detail sentence for any `unsupported` value. |
| `reason`, `next_action` | Present when `ready` is false: what happened and the one useful next action. |

### `run`

`run` reads the session's initiator, target, and optional project, then reads one nonempty Markdown body from standard input. It resolves only that target, repeats cheap prerequisites, validates transport, and determines current warnings. It writes any `Warning:` lines to standard error immediately before publishing the request, then starts exactly one target CLI invocation, captures its final text, and atomically publishes the response.

Warnings never prompt, wait for acknowledgment, read an approval flag, or persist consent. A readable version outside exercised evidence may proceed with a warning when every required switch and the fixed one-shot transport remain usable.

Every run starts a fresh vendor context. Bridge neither resumes a vendor session nor sends earlier Bridge messages. An application needing history includes it in the current body.

`--max-steps` and `--require-model` are run-only MiniMax controls and are
refused for every other recorded target before request publication. An explicit
positive step bound replaces MiniMax's default of one assistant step for that
call only. A required model changes MiniMax's internal output to JSON; Bridge
accepts only a successful schema-version-1 `exec.result` whose reported
`providerId/modelId` is byte-for-byte identical, then publishes only the final
string output. This validates runtime identity and does not select a provider or
model. The validated identity is preserved rather than discarded: the `finished`
event carries it as optional `model` and `provider` fields, and a Format 3
response record carries it as `Model:` and `Provider:` header lines (section 7).
MiniMax's JSON mode is today the only fixed output route that reports identity;
the other five connectors' outputs carry none, so this identity reaches neither
their events nor their records. Omitting both controls preserves the existing
one-step plain-text call, which reports no identity.
In a work session, `--max-steps` is forwarded when given and omitted otherwise,
so the review-only one-step assumption does not apply to work.

`--model` and `--effort` optionally name a model or reasoning effort for one
call, in work and review sessions alike. A named choice passes through only
the selected target's own documented switch — Codex `-m` and
`-c model_reasoning_effort=…`, Claude Code `--model` and `--effort`, MiniMax
`--model` — exactly as named, on that call only. A target whose connector
documents no switch for the named choice (ZCode, Hermes Agent, and Qwen Code
for either; MiniMax Code for an effort) refuses it before request publication
with that plain fact, and the tool runs with its own configured model only
when nothing is named. Bridge chooses no model or effort itself and
substitutes none: a named model the account rejects at use is the target's
own reported failure, carried out with its reason and next action, never a
fallback to another model. The switch a named choice needs is proven present
like any other switch the call uses, and the choices each tool documents are
readiness facts (the `model_switch` and `effort_switch` fields above),
gathered without a model call. For Codex, the choices are also reported back:
after the call, the connector takes the thread id from the `thread.started`
event, reads the `model` and `effort` from the `turn_context` line of Codex's
own session file for that thread (under the `sessions` directory of the Codex
home, `CODEX_HOME` when set, `~/.codex` otherwise), and the saved reply of
either session format shows the values requested, when any, and the values
Codex recorded. If the file or the values cannot be read, the reply says they
were not reported; they are never inferred from the request.

`run` has no deadline for ordinary work: it waits until the target answers, the target fails, or the caller stops the run, and readiness checks keep a bounded deadline of their own. `--timeout <seconds>` is a bounded wait, not a kill: when it ends, the command returns control with the target still running and reports the retained evidence (the session and published request, the peer's process and any session reference it printed, and sanitized partial output), exiting nonzero. In ordinary mode that report goes to standard error; with `--events-jsonl` the same evidence arrives as fields of the final `finished` event on standard output (section 4b). `--no-timeout` names the default behavior explicitly and cannot be combined with `--timeout`. Cleanup has a separate bounded grace period. On Windows a retained or detached target ends when the owning Bridge process exits; macOS and Linux are unaffected. There is no retry.

A courier-only project session cannot run in review mode: Bridge refuses before connector import or request publication and tells the application to include evidence in the body or choose a project-capable target. A work session runs against the connector's work vector (section 4a); a work session whose target declares work unsupported is refused the same way, with the real limitation and the terminal route in the message.

`--note-ref` and `--purpose` store two inert single-line strings in a Format 3 request header: an opaque pairing with a caller note and a name for the request's purpose. Bridge records them without interpreting them or using them to choose authority, files, or workflow. They are refused on a Format 2 session so legacy records never change shape.

`--attachment` names an absolute image file to deliver with the request. It is repeatable, recorded as one `Attachment:` header line per file, accepted only for a Format 3 **work** session whose target has a qualified image route, and validated to exist before anything is published. The exact request body is never modified to mention an attachment; where the route needs the path in the text (Claude), the caller's body must name it and the run warning says so. Missing files, unsupported formats, tool limitations, and model rejection are reported as real failures; the caller owns and retains the original images.

`--events-jsonl` replaces the response-path line on standard output with one JSON object per line describing the turn's lifecycle (section 4b). Standard error remains diagnostics.

Success without `--events-jsonl` writes only the response path to standard output and exits 0. With `--events-jsonl`, standard output carries only event lines and the final `finished` event carries the response path. If a target fails after request publication, the request remains as an honest record, no response is invented, and Bridge writes the failure and next action to standard error and exits nonzero.

### `record`

`record` is the only local writer besides `run`. It creates a session or adds an application-neutral note without calling a target, using the shared validation, numbering, lock, envelope, and atomic writer. ZCode's native provider paths travel in the child environment only; readiness checks built-in provider-file availability without reading its contents. Neither `check` nor the connector writes outside session records.

Its substantive text comes from standard input. Empty or whitespace-only input is a usage error. Success prints the canonical path.

`--mode review|work` creates a Format 3 session (section 6): an explicit, immutable working mode. `--access-path` records additional existing absolute directories the request needs; it is repeatable, requires an explicit `--mode`, and is refused with repeats. A work session may omit `--project` when no code directory exists yet. A note accepts none of the session-creation options.

---

## 4a. The working mode

A work session is an explicit alternative to the restricted review call, created by `record --kind session-create --mode work`. Its mode, target, working directory, and declared access directories are immutable from creation; message text cannot change them. A work request can read the supplied project material and perform the work the request authorizes through the target's ordinary capabilities, under the target's own normal permissions. The code directory and document directories may differ (`--project` plus `--access-path`); a project can be discussed before code exists (omit `--project`).

Work mode is not a security override. ZCode work defaults to `--mode edit`. Only an explicit `--work-permission full-access` on one `run` selects native Full access (`--mode yolo`), enabling shell work under the user's account without an approval connection. It changes no global setting and is refused for other tools and review calls. Other work vectors select no permission bypass or replacement posture and refuse no route on partial posture evidence. For Codex, Bridge reads the apparent posture (`sandbox_mode` at the top of `$CODEX_HOME/config.toml`, else the tool's own out-of-box default) and reports it in a warning as apparent, not effective — a trusted project's own `.codex/config.toml` and managed, cloud, or MDM policy can change what codex enforces — leaving codex's own run-time resolution and enforcement to govern, so a genuinely read-only effective sandbox fails inside codex with codex's own error, which Bridge surfaces with its reason and next action. Where a target cannot do non-interactive work under its ordinary permissions at all, Bridge reports work unsupported with the real diagnostic and the terminal route (open that tool's own terminal interface) rather than quietly weakening anything. Selecting work mode is not blanket permission for publication, purchases, login, destructive operations, or unrelated effects; normal project instructions and the user's tool configuration still apply, and the surviving-boundary warnings of section 8 continue to name what is not confined.

Each connector ships a separate work vector beside its review vector; the review vectors are unchanged and review calls made without the new options behave exactly as before. The four supported work routes and the two honest unsupported ones are in section 8a.

Each call still starts a fresh native context. A successful work call returns the complete final answer, including an AI clarification question when that is the answer; the caller supplies all continuity in a later request. A failed call leaves truthful records: the request is retained, no response is invented, and if the invocation might have edited files before failing, the failure says so.

### `run --events-jsonl`

With `--events-jsonl`, standard output carries one JSON object per line — lifecycle events, periodic check-ins, and exactly one final result — and nothing else; the response path arrives inside the final event. Every event line, like the response-path line a flag-free run prints, is flushed to standard output the moment it is written, so a consumer reading a pipe sees it during the turn rather than at exit. Standard error remains diagnostics, warnings included. Every event object has an `event` field (`started`, `heartbeat`, `wait_expired`, or `finished`) and a `phase` field.

```json
{"event": "started", "phase": "run", "peer": "codex", "mode": "work"}
{"event": "heartbeat", "phase": "prerequisites", "elapsed_seconds": 30.001, "child_running": true}
{"event": "heartbeat", "phase": "peer-call", "elapsed_seconds": 60.002, "child_running": true}
{"event": "finished", "phase": "run", "outcome": "success",
 "request_path": "/…/messages/0001-initiator-to-peer.md",
 "response_path": "/…/messages/0002-peer-to-initiator.md",
 "warnings": ["...the same warnings standard error carried..."]}
```

A heartbeat means Bridge is alive and can report whether its child process is still running; it is not progress and carries no percentages. Heartbeats arrive while prerequisite probes or the peer call are waiting (`phase` says which), about every 30 seconds; ordinary waiting has no deadline. The final `finished` event carries `outcome` (`success`, `failure`, `stopped`, or `pending` — the last when a bounded wait returned control with the target still running), `request_path` and `response_path` when published, the warnings, and — for `failure` — a plain `reason` and `next_action`; internal exception names stay internal. Each time a bounded wait ends with the target still running, a `wait_expired` event (`phase` `peer-call`) is emitted, and the `pending` `finished` event carries the same retained evidence fields: `session_dir`, `request_on_disk`, `peer_pid`, and `peer_running`, with `peer_session`, `stdout_progress`, `stderr_progress`, `elapsed_seconds` (measured from the peer call's start), and `capture_paths` (the retained capture files) present when there is something to report. A `success` event may additionally carry `model` and `provider` when the target's own structured output reported the identity that answered — MiniMax's `--require-model` JSON mode and Codex's session-file reading are the two such routes today — and both fields are absent when it did not, never empty strings; they state what the tool reported, never a Bridge selection. For Codex the model is what its own session file recorded for the call, or the plain words `not reported` when it could not be read. A `success` event follows durable response publication. A failed call still exits nonzero. If the event pipe breaks, the turn runs on and its records decide the truth: a broken pipe can never turn an unfinished call into a reported success.

Stop signals, `--timeout` or `--no-timeout`, interruption, and cleanup behave exactly as for any other run; there is no separate cancellation daemon or command service.

---

## 5. Neutral local records

`record` accepts exactly two kinds:

| Kind | Required arguments | Optional | Result |
|---|---|---|---|
| `session-create` | `--initiator <label>`, `--peer <target-id>` | `--project <dir>`, `--mode review\|work`, repeatable `--access-path <dir>` (with `--mode`) | Creates `SESSION.md`; allocates no message number |
| `note` | none beyond session and kind | none | Creates one numbered initiator record |

The session body describes the session; a note may hold any application information. Bridge does not classify or interpret either.

`record` never invokes a target; creates, approves, replaces, or seals a plan; interprets workflow, review, repository, or Git state; changes the session's initiator; or creates application-specific headers.

Typed events belong in the application's Markdown body or its own state. Bridge does not grow an application record-kind registry.

---

## 6. Session record

```text
<session>/
  SESSION.md
  messages/
    0001-initiator-to-peer.md
    0002-peer-to-initiator.md
    0003-initiator-record.md
  .lock
```

`.lock` holds no canonical state. A session contains no `PLAN.md`, workflow status, approval state, task ledger, provider record, or application database. Sessions normally live under `~/.agent-bridge/sessions/` outside Git and cloud synchronization.

`SESSION.md` is written once:

```markdown
# Session

Bridge-Format: 2
Initiator: ora
Peer: claude
Project: /absolute/path

## Body

<application-supplied description>
```

`Project:` is omitted when absent. A project path must be absolute, exist at creation, and never come from a peer message. It is immutable with the initiator and target.

The session carries no provider or model, harness version, qualification receipt, mutable status, usage, cost, authority, or workflow field. Format 2 distinguishes this courier shape from unreleased construction sessions with workflow fields. An unsupported format is rejected, not guessed or silently migrated.

A session created with an explicit `--mode` is Format 3:

```markdown
# Session

Bridge-Format: 3
Initiator: vibe-coder
Peer: codex
Mode: work
Project: /absolute/code/path
Access-Path: /absolute/document/path

## Body

<application-supplied description>
```

`Mode:` is required and one of `review` or `work`; `Access-Path:` lines are optional, repeatable, each an absolute directory that exists at creation, and may not repeat. `Project:` may be omitted in a work session when no code directory exists yet. Both formats keep their own strict parsers: a Format 2 file carrying a Format 3 field, or the reverse, is rejected rather than reconciled, and no file is ever migrated. Format 2 sessions always read as review mode with no access paths. Successful runs never delete retained history.

Numbers increase within the session while the lock is held and are never reused. A failed target call may leave a request as the final message; that is an incomplete exchange, not corruption.

---

## 7. Envelope and Bridge-inert body

The runner writes every header. Initiators and targets supply only body text.

Request (Format 2, unchanged):

```markdown
# Message 0001
From: ora
To: claude

## Body

<request copied unchanged>
```

Response (Format 2):

```markdown
# Message 0002
From: claude
To: ora

## Body

<final answer copied unchanged>
```

A Format 3 request may additionally carry the caller's inert metadata, and a Format 3 response names the request it answers:

```markdown
# Message 0001
From: vibe-coder
To: codex
Note-Ref: note-42
Purpose: second-draft
Attachment: /absolute/path/image.png

## Body

<request copied unchanged>
```

```markdown
# Message 0002
From: codex
To: vibe-coder
Answers: 0001

## Body

<final answer copied unchanged>
```

A Format 3 response whose target's own structured output reported the identity that answered additionally carries that report in its header:

```markdown
# Message 0002
From: minimax
To: vibe-coder
Answers: 0001
Model: MiniMax-M2.5
Provider: minimax

## Body

<final answer copied unchanged>
```

`Note-Ref:` and `Purpose:` appear once each when given; `Attachment:` appears once per attached file; `Answers:` carries the request's four-digit sequence. `Model:` and `Provider:` carry the exact strings the tool reported for the call — MiniMax's `--require-model` JSON mode is the one route today — and each appears only when that half was reported, never as an empty placeholder; this provider attribution stays a Format 3 record line, so a Format 2 response never carries it and its shape is unchanged by it. A Codex response additionally reports the choices of the call, in either session format — Format 2 as Format 3: `Model:` and `Effort:` carry what Codex itself recorded for the thread the call started (read from the `turn_context` line of its session file), saying `not reported` for a half that could not be read, and `Requested-Model:` and `Requested-Effort:` appear when the caller named a choice for that call, so the record shows the values requested and the values Codex recorded without either being inferred from the other. None of it is appended to or read from the body, which stays byte-exact under `## Body`.

Neutral note:

```markdown
# Message 0003
Record: note
From: ora

## Body

<note copied unchanged>
```

Header-shaped text below `## Body` remains body text. It cannot change Bridge identity, target, project, mode, number, kind, restrictions, authority, routing, or become a Bridge command. Bridge extracts no plan, commit, approval, review result, or instruction.

Filenames describe direction rather than repeating caller labels. A response is the next message published while the same run holds the lock; in Format 3 its `Answers:` header is the correlation, read from the record itself.

### The readable Format 3 record surface

A caller may read everything it needs from the retained records alone, in plain Markdown:

- From `SESSION.md`: the session's working `Mode:` and every declared `Access-Path:` directory, beside the immutable initiator, target, and project.
- From each request file: its sequence in the title line, its exact body below `## Body` (byte-exact, never modified), and its `Note-Ref:`, `Purpose:`, and `Attachment:` header lines when the caller supplied them.
- From each response file: its path, its body below `## Body`, the request sequence it `Answers:`, the `Model:` and `Provider:` lines the tool's own output reported for that call, when it reported an identity at all, and — for Codex — the `Effort:` and `Requested-Model:`/`Requested-Effort:` lines of its choices report, which rides in the reply of either session format.

Headers are always the block between the title line and the first blank line; everything below `## Body` is inert text. Bridge owns this surface and will keep it readable.

---

## 8. Connectors, warnings, and qualification

Each target connector is a small source-controlled translation for one official vendor CLI. It declares:

- CLI identity and exact tested version or evidence-backed set;
- tested operating system, major versions, and architecture when relevant;
- authentication evidence available without a model turn, including limits when live authentication cannot be confirmed;
- fixed one-shot arguments and body transport;
- strongest practical enforced restrictions and concrete residual warnings;
- project-capable or courier-only posture; and
- final-response extraction.

A connector passes only what the caller explicitly names: a caller-named model or effort travels on that tool's own documented switch and nothing else does. A connector itself chooses no model, effort, provider, endpoint, or credential; installs, updates, or signs in to nothing; resumes no vendor session; and falls back to no API, private desktop endpoint, browser, or UI automation. A tool with no documented switch for a named choice refuses it before publication, and a model the account rejects at use is a reported failure, never a substitution.

`run` repeats cheap prerequisites. Missing software, reported missing authentication or minimum local authentication state, unusable one-shot input or final output, an absent required switch, or inability to control the foreground child stops before a real target call. Version or platform drift alone warns and proceeds when those mechanics remain usable.

### Strongest practical restrictions

Each connector applies every compatible vendor-native control available for project writes, Git changes, shell effects, browser or web access, MCP, messaging, credentials, publication, deployment, delegation, and other external effects.

A connector may remove tools, use an enforced sandbox, withhold the project, or combine them. When the vendor cannot completely guarantee confinement, Bridge names the remaining limitation and proceeds without an acknowledgment mechanism. The target's connection to its configured model provider is outside this restriction.

| Target | Project posture | Strongest practical posture | Required warning emphasis |
|---|---|---|---|
| Codex | Project-capable | Skip only `$CODEX_HOME/config.toml`, disable known high-risk routes, use the read-only sandbox, set the working directory explicitly | Skipped file's model/effort defaults; surviving project, system, cloud/managed/MDM configuration and possible hooks, MCP, or other external effects |
| Claude Code | Project-capable | Restricted mode, empty strict MCP set, Read/Glob/Grep, planning mode | Administrator-managed or remote policy can survive and add effects |
| ZCode | Project-capable | Planning mode, explicit directory, known dangerous tools removed | Plugin/direct-MCP limits, indirect OAuth evidence, visible body argument |
| Hermes Agent | Courier-only | Neutral directory, safe mode, smallest harmless toolset | Read cannot be separated from write, memory remains, visible body argument |
| MiniMax Code | Courier-only | Neutral directory, `exec` with standard input, `--permission smart`, one assistant step by default or an explicit positive bound; optional exact returned-model validation | Smart is discretionary, not a sandbox; a step bound does not disable tools; no state-free authentication check |
| Qwen Code | Courier-only | Neutral directory, safe and plan modes, pinned native macOS sandbox selection/profile, zero model tool calls, one input frame and turn, pre-model command limits | Input preprocessing below; settings/.env may bypass sandbox or launch a detached proxy; profile permits same-user reads, some writes, process launches, and network; no safe no-turn authentication confirmation |

For Codex, `--ignore-user-config` does only what its name understates: it skips `$CODEX_HOME/config.toml`. It does not suppress trusted-project `.codex/config.toml` files and project hooks or rules, system configuration, `managed_config.toml`, `requirements.toml`, cloud-delivered requirements, macOS MDM preferences, or separately sourced user/global hooks and rules. Those surviving layers can add settings the fixed vector does not override, and managed defaults or MDM can override CLI options. Hooks, MCP servers, plugins, network or telemetry settings, and other integrations from surviving configuration may therefore retain routes to external effects outside the read-only shell sandbox. Bridge names that limit in its non-blocking warning. The skipped file's model and effort defaults are also lost, and Bridge does not replace them.

**Claude Code 2.1.251 managed MCP prerequisite.** The exact source `/Library/Application Support/ClaudeCode/managed-mcp.json` is incompatible with the fixed `--strict-mcp-config` invocation: the CLI exits when they are combined. If that path is present or its absence cannot be established, `check` fails and `run` fails before request publication. Bridge observes the path without opening policy contents. This is an unusable-command prerequisite, not a refusal over incomplete confinement. Other administrator-managed endpoint and remote policy may survive restricted mode; their presence or uncertainty remains a warning, not this hard failure.

**Qwen Code 0.23.0 input exception.** Selected Qwen may interpret recognized leading `/` commands or unescaped `@` references before the model. It may alter or replace the effective prompt, read and append readable file or resource content, fail in preprocessing, or handle a command without a model call. Both supported headless input modes share this; safe mode cannot disable it and no lossless escape or raw switch exists. Bridge records and passes the original exactly, gives Qwen a task-owned neutral directory with no project, and requires `--max-tool-calls=0`: no model-initiated tool call can execute, and the first such attempt aborts the run. Input preprocessing happens before that budget, so the limit does not stop it. Bridge warns during `check` and before publication without blocking or acknowledgment. The other five prompts remain lossless and unselected Qwen inert. An official raw mode would make the exception removable after qualification.

The Qwen vector disables `/bug`, `/config`, `/update`, `/import-config`, `/language`, `/effort`, `/model`, and `/doctor` because those pre-model command families can cause external or persistent effects. Other recognized preprocessing remains. Qwen retains the user's existing authentication and provider setup; Bridge adds no selection or credential handling.

For both readiness and runs, Bridge clears inherited `QWEN_CODE_RELAUNCH_ARGS` so it cannot replace the fixed startup arguments. It pins nonempty `QWEN_SANDBOX=sandbox-exec` and `SEATBELT_PROFILE=restrictive-open`, and removes inherited `SANDBOX` and `QWEN_SANDBOX_PROXY_COMMAND`. Safe mode still loads settings and `.env` values, which can refill missing or empty variables: restored `SANDBOX` can bypass the sandbox, and a restored proxy command can launch a detached shell outside both the sandbox and Bridge's process group. Empty strings cannot disable those routes reliably. Bridge names these surviving routes as non-blocking warnings; it does not inspect or alter the user's configuration or add a confinement gate.

### Disposable qualification

Before real project use, a task-owned synthetic Git repository proves:

1. A project-capable target reads supplied evidence; a courier-only target receives no project.
2. Local create, modify, delete, Git-ref, and repository-configuration attempts occur only in that disposable repository, whose tracked content, untracked files, `HEAD`, refs, configuration, and clean status are compared afterwards.
3. No `.git` lock or task-owned child remains.
4. No prompt deliberately attempts browser or web access, messaging, MCP service calls, credential access or change, publication, deployment, login, purchase, or another real-world effect. Those routes are described from the fixed vector, safe no-turn metadata, and uninvoked tool inventory.
5. The exact record and CLI transport preserve leading hyphens, Unicode, multiline text, and the complete response. Qwen uses non-triggering content and claims no raw prompt.
6. The temporary parent is removed on every exit path.

The test uses no real project, secret, message, production service, or publication. Same-user reads outside the project are not claimed to be confined.

Qualification is source evidence, not mutable runtime state. There is no last-passed stamp, cache, receipt, database, or third connector operation. A CLI outside declared evidence warns when the required mechanics still work; qualification updates source rather than granting per-user approval.

---

### 8a. Work vectors and image routes

Each connector's work vector is a separate builder beside its review vector, reusing the same fixed-vector transport, output parsing, execution, cancellation, and cleanup. Work vectors preserve ordinary user model/configuration choices — a caller-named model or effort passes through on the tool's own documented switch (section 4) — and the tool's normal permission policy, except for the caller's explicit per-call ZCode Full access choice described in section 4. Access directories are recorded in the session and reach the vector where the tool has a matching switch; otherwise they remain ordinary same-user paths, which is a declaration of need, not an isolation boundary.

| Target | Work | Image route | Basis |
|---|---|---|---|
| Codex | Supported: `codex exec --cd <project>` (+ `--add-dir` per access path) with **no sandbox switch** — codex itself resolves and enforces the effective sandbox at run time; Bridge reads the **apparent** posture (`sandbox_mode` at the top of `$CODEX_HOME/config.toml`, else codex's own documented read-only default for exec) and warns with it named as apparent, never refusing on that partial reading; no `--ignore-user-config` and no review feature disables, so the user's ordinary configured model and effort apply unless the caller names one, which then passes through on `-m` / `-c model_reasoning_effort=…`; headless exec cannot ask for approval, so what the sandbox refuses fails: a genuinely read-only effective posture fails inside codex with codex's own error, surfaced by Bridge with its reason and next action | Native `-i/--image` attachment | Switches verified on the installed 0.155.1 `codex exec --help`; posture evidence established with `codex debug prompt-input` under controlled `CODEX_HOME` fixtures (read-only by default; each `sandbox_mode` value governs; a trusted-project `.codex/config.toml` — with the project marked trusted in the user config — flips the enforced posture, observed read-only → workspace-write through that layer alone) and the vendor's non-interactive documentation; no cheap, side-effect-free authoritative effective-policy source exists (`codex doctor --json` reports no effective-posture field and probes the network and auth; `codex debug prompt-input` writes state into `CODEX_HOME`), which is why the apparent reading warns instead of refusing; re-live-qualified under the corrected resolution (see the re-qualification notes below) |
| Claude Code | Supported: normal `claude --print --output-format text` (+ `--add-dir` per access path) with no permission flag — the user's settings and the ordinary default headless permission behavior decide; the review-only managed-MCP gate does not apply because work passes no `--strict-mcp-config` | File-reading route: Claude Code's own read tool presents the image when the request body names its absolute path | Live probe on 2.1.278 confirmed default-mode headless file creation; live-qualified |
| ZCode | Supported: `--mode edit` with the real tool set (no review deny list) — edit mode allows workspace file edits; every other approval-needing tool is denied by the deny broker a headless prompt uses; an explicit per-call `--work-permission full-access` instead selects native `--mode yolo`, enabling shell commands for Git and tests under the user's account | Native `--attach`, which the bundle reads and hands to the model as inline image content | Mode and deny-broker behavior read from the installed 0.16.9 bundle source; `--attach` pipeline verified in source; live-qualified |
| MiniMax Code | Supported: `mcode exec --cwd <project>` with **no permission policy selected** — the program's own headless default governs: its source fixes an absent `--permission` at `smart`, and no runtime configuration can change a headless run's permission mode (`ask` needs a TUI and is rejected by `mcode exec` itself; `full`/`off` are approval bypasses never selected); `--max-steps` only when the caller names one, `--file` per attachment | Native `--file` attachment, classified by MIME type into model image content (≤10 files, ≤100 MB) | Read from the installed 0.2.7 source; re-live-qualified under the corrected vector (see the re-qualification note below) |
| Hermes Agent | Unsupported: one-shot `-z` auto-bypasses approvals by design (its own help says "approvals are auto-bypassed") and no other non-interactive route offers ordinary approvals | Unsupported: no attachment switch; the one-shot body is command-line text | Read from the installed 0.21.3 `hermes --help`; terminal route: run `hermes` in its own terminal |
| Qwen Code | Unsupported: the ordinary approval mode (`default`) requires manual approval for file edits and shell commands and a headless run cannot ask (text mode cancels the call; stream-json would ask the calling host to approve, making Bridge the permission authority), while `auto-edit`/`auto`/`yolo` are automatic approvals Bridge will not select | Unsupported: the stream-json reader stringifies every non-text content block as JSON text rather than pixels, and there is no attachment switch | Read from the installed 0.23.0 bundle source; terminal route: run `qwen` in its own terminal |

The `/` and `@` preprocessing exception of section 8 applies to every Qwen input, work included; no work route exists to carry it, and the finding above is the whole of Qwen's work story. Capability facts in `check --json` state these same truths, and a work session for an unsupported target is refused before request publication with the limitation and terminal route in the message.

**Work-mode prerequisites.** A work-mode check or work run proves the work vector's own switches on the installed program and never the review-only restriction switches, which a work vector passes none of. Concretely: Codex proves the `exec` subcommand (by the help probe that carries the switches), `--skip-git-repo-check`, and `--cd`, plus `--add-dir`/`--image` when the session declares them and `-m`/`-c` when the caller names a model or effort; Claude proves `--print` and `--output-format`, plus `--add-dir` when an access directory is declared and `--model`/`--effort` when the caller names them, and asks neither the endpoint-policy readings nor the `doctor` remote-policy fact, both of which describe the review call's boundary and are discarded by work mode; ZCode proves `--prompt`, `--mode`, and `--cwd` in the help and passes the selected `--mode edit` or `--mode yolo`, `--cwd`, and `--output-format` through the same no-turn parser probe the review call uses — without the review deny-list switch the work command never carries — plus `--attach` when an attachment travels; MiniMax proves `--input`, `--input-format`, `--cwd`, and `--output-format`, plus `--timeout`/`--file`/`--max-steps`/`--model` only when the vector emits them, and never the review-only `--permission`. Hermes and Qwen perform no work-mode switch qualification at all. An optional switch's absence degrades the request that needs it, honestly and by name; it never blocks an otherwise supported plain-text work turn. Executable discovery, version and platform reporting, and genuine authentication checks remain prerequisites everywhere.

`check --peer zcode --mode work --work-permission full-access --json` qualifies that exact permission mode. ZCode readiness reports `effective_permission` (`edit`, `full-access`, or review `plan`), whether headless shell execution is enabled (`headless_shell`), and `shell_execution_verified: false`. The no-model parser check does not prove a model can execute Git or tests, and live authentication remains unconfirmed.

**Live work qualification, September 27, 2026, macOS 26 arm64.** One `qualify --mode work` run per supported route, each in a disposable fixture: a synthetic Git repository as the code directory, an `authorized-docs` access directory outside it, and one distinctive attachment image whose content appeared nowhere in the request text. Every run proved the production work vector, code reading from the code directory, a reversible document edit written inside the access directory only, the synthetic repository unchanged afterwards (tracked hashes, untracked set, HEAD, refs, config, clean status, no Git locks), released session locks, and full cleanup. Codex 0.155.1 identified a solid-magenta attachment from its pixels (`--image`); Claude Code 2.1.278 identified the same magenta image through its file-reading tool with the path named only in the request body (`--add-dir` widened access); ZCode 0.16.9 read block digits "3174" from a `--attach` image; MiniMax Code 0.2.7 read the same digits from a `--file` image. No work qualification is claimed for Hermes Agent or Qwen Code.

**Re-qualification after the permission-posture correction, September 28, 2026.** The Codex and MiniMax Code work vectors were corrected to stop selecting a permission posture of Bridge's own (Codex no longer passes `--sandbox workspace-write`; MiniMax no longer passes `--permission smart`), because a selected policy silently replaced the user's effective configured posture — widening a configured read-only Codex to writable. Each corrected vector was re-live-qualified once in a fresh disposable fixture with the same checks as above. The Codex run proceeded under this machine's effective configured posture (`sandbox_mode = "danger-full-access"` read from `~/.codex/config.toml`; the read-only handling of that round was itself corrected later — see the next note), and the MiniMax run under the program's own headless default (`smart`). Claude Code (no permission flag) and ZCode (`--mode edit`, which narrows the `yolo` default and replaces no user-configurable posture) were audited for the same problem and found clean.

**Correction of the read-only refusal, September 28, 2026, second round.** The same round's refusal of an apparent read-only Codex posture was removed, because its evidence — the top of the user config — is partial where codex's own resolution is not: on the installed codex 0.155.1, a project the user config marks trusted can carry its own `.codex/config.toml` that changes the enforced posture (verified locally: an empty top level that reads as read-only flipped to workspace-write through that layer alone), so a validly configured writable work route existed that Bridge wrongly rejected before publication. No authoritative pre-flight replacement exists in the installed program: `codex doctor --json` reports no effective-posture field and performs network and auth probes, and `codex debug prompt-input` renders codex's own resolution but writes state into `CODEX_HOME`, which Bridge never modifies. The work vector therefore always proceeds without a posture flag, warns that the top-of-config posture is apparent rather than effective and that codex's own run-time enforcement governs, and lets a genuinely read-only effective call fail inside codex with codex's own error, which Bridge surfaces with its reason and next action. The vector was re-live-qualified once more under this machine's actual `danger-full-access` configuration; the warn-and-proceed path under an apparent read-only reading is exercised by the unit checks, not by a live model call.

---

## 9. Locking, publication, and cleanup

One foreground turn holds a process-scoped advisory lock from sequence allocation through response publication or cleanup. Contention changes no canonical file. There is no lease, heartbeat, stale-lock service, or lock stealing. On POSIX the lock is `fcntl.flock` on the open `.lock` descriptor; on Windows it is `msvcrt.locking` over one byte of the same file. The contract is identical on both platforms: one exclusive advisory lock, held open by the running process, refused rather than waited for, and dropped by the operating system the moment the holder ends.

Each canonical file is completed in a temporary file in its destination directory, flushed, and atomically renamed. A partial file is never published. An uncertain rename reports the exact canonical path and requires inspection before another run.

Each child belongs to the turn's process group. Timeout, interrupt, termination, and hangup terminate and reap it, remove task-owned temporary files, and release the lock. Cleanup failure visibly names what remains. On POSIX the child is started as its own session leader and cleanup signals its process group. On Windows the child is created with `CREATE_NEW_PROCESS_GROUP` and `CREATE_SUSPENDED` — the first gives the group numbered by its own process id, which is the ownership fact, and the second leaves the child's primary thread unstarted — and the kill-on-close job object is created and assigned while the child has not yet executed one instruction, with the thread resumed only afterwards: everything the child starts, from its first action on, is a job member, so ownership precedes execution and survives the root's own exit. The polite phase sends the console break event that group exists to receive (a courtesy a child may ignore and a console-less caller cannot send). The forced phase closes that job handle and the kernel terminates every member; the operating system's `taskkill /T /F` remains as a belt-and-braces force and as the confirmation, whose not-found report counts as emptiness only after the job has been closed — a tree walk from a root that no longer exists cannot speak for the descendants it would have named. A platform that refuses the job or the resume refuses the run: what was started has never run and is terminated outright, any job already taken is released, and the refusal is reported. No Windows machine is part of this repository's qualification: the Windows branches of the lock and the cleanup are covered by unit checks with the platform's primitives simulated, and no live Windows claim is made.

`SIGKILL` and power loss cannot clean up; on Windows the kill-on-close job means the operating system itself ends the owned tree when Bridge dies abruptly, because a process's end closes its handles. A child that escapes its process group fails qualification if observed. Outside the main thread, the surrounding program's signal behavior applies because handlers cannot be installed. Storage failure can still make publication uncertain.

There is no automatic retry. Retrying an uncertain provider call is an application decision.

---

## 10. Internal failures

The core owns this internal list; connectors map vendor behavior into it. The names are not a public compatibility surface.

| Failure | Meaning and next action |
|---|---|
| `MISSING_CLI` | Target CLI absent; install its official program and check again. |
| `AUTHENTICATION_REQUIRED` | Supported sign-in not observed; sign in through the harness. |
| `UNREPORTABLE_VERSION` | Version unreadable; inspect the vendor command. |
| `UNQUALIFIED_VERSION` | Retained internal diagnostic; current connectors warn on readable version drift when required mechanics remain usable. |
| `UNQUALIFIED_PLATFORM` | Retained internal diagnostic; current connectors warn on platform drift when required mechanics remain usable. |
| `RESTRICTIONS_UNAVAILABLE` | A required fixed-vector, input, output, or foreground switch is absent; the promised call cannot be made. |
| `QUALIFICATION_UNSAFE_OR_INCONCLUSIVE` | Retained internal diagnostic, not a current runtime confinement gate; residual limits are warnings. |
| `BUSY_SESSION` | Another turn owns the lock; wait. |
| `TIMEOUT` | Deadline expired; inspect visible state before deciding whether to retry. |
| `PEER_FAILURE` | Vendor CLI failed; correct the harness-side problem. |
| `EMPTY_RESPONSE` | No final text; check the target directly. |
| `CLEANUP_FAILURE` | A task-owned process or path remains; remove the named item. |
| `USAGE_ERROR` | Argument, label, body, target/project combination, or transport invalid; correct it. |
| `UNKNOWN_HARNESS` | Target is not one of the six fixed identifiers; name one. |
| `CONNECTOR_UNAVAILABLE` | Incomplete build lacks a fixed target's connector; use complete source. All six ship in Release 1. |
| `UNKNOWN_RECORD_KIND` | Kind is not `session-create` or `note`; use one of them. |
| `SESSION_NOT_FOUND` | Session absent; create it or correct the path. |
| `SESSION_INVALID` | Session unreadable, inconsistent, or unsupported; inspect it or start again. |
| `SESSION_EXISTS` | Session already exists; continue it or choose an empty path. |
| `PUBLICATION_FAILURE` | Nothing published; correct storage and retry only when safe. |
| `PUBLICATION_NOT_FLUSHED` | File exists but directory entry was not forced to disk; treat as unfinished. |
| `PUBLICATION_UNCERTAIN` | Rename outcome unknown; inspect the exact path before anything else. |

Every command reports failures on standard error and exits nonzero. A failure avoids false success, cleans what the turn owns when possible, and supplies one next action.

---

## 11. Initiating adapters

An adapter may be a harness skill, local application wrapper, or server-side integration running where target CLIs and vendor sign-ins exist. It:

1. Supplies an inert initiator label and fixed target.
2. Creates or reuses the one-initiator, one-target session.
3. Hands one complete Markdown body to `run`.
4. Reads the returned response path.
5. Adds a neutral note when needed.
6. Reports readiness, warnings, and failures without hiding them.

The adapter owns its UI, target-selection policy, multi-target work, context assembly, plans, approvals, reviews, corrections, Git behavior, retry decisions, and response interpretation. Those remain application responsibilities even inside a harness package.

The six harness packages expose courier, readiness, and neutral-record entry points through host conventions. They contain no planning, Programming Loop, or review product, never find or call each other, and invoke the one shared Bridge.

### Adding an initiating host

To be a target, a harness itself needs no Bridge adapter package. Its official
CLI and vendor sign-in must exist where Bridge runs, and Bridge must have a
qualified connector. A host that will initiate Bridge needs only documentation,
a skill, or a launcher implementing the six adapter steps above. It must use
the source checkout as the runtime, leave target choice with the caller, pass
the complete body, surface warnings and failures, retain the saved response,
and wait for cleanup. It must not add a target branch, register itself, or make
its application state part of Format 2.

Focused initiating-host evidence uses a disposable session and fake target. It
shows exact body preservation, one selected target, readiness and warning
presentation, nonzero failure, complete response retrieval, neutral notes, and
no surviving process. This does not qualify a vendor CLI or model.

### Adding a target

A new target requires one hand-written connector module and literal edits to
`HARNESS_IDS` and `_switch`; add it to `is_courier_only` when it cannot receive
a project. The connector implements the existing `check` and `build_command`
surface with an official stable CLI, a fixed no-shell vector, standard-input
transport where the CLI supports it, exact final-response extraction, the
strongest practical restrictions, and specific residual warnings. It neither
selects a model/provider nor installs, signs in, retries, or falls back.

Focused target evidence must cover the fixed Format 2 request, response, and
note records; immutable session identity; literal dispatch; every unselected
connector remaining inert; warning and failure reporting; timeout,
publication, and process cleanup; and every initiating adapter that offers the
new identifier. Qualification declarations name only the CLI versions and
platforms actually exercised. Without a separately authorized disposable real
call, the connector may be described as implemented and untested, never tested.

This procedure is intentionally not a registry, generator, SDK, marketplace,
discovery engine, provider fallback, automatic target selector, or all-pairs
test requirement.

---

## 12. Release 1 conformance

Release 1 conforms only when evidence proves:

1. Targets are exactly `codex`, `claude`, `zcode`, `hermes`, `minimax`, and `qwen`; another target fails, new valid initiator labels work without registration, and all five unselected connectors stay inert.
2. Each connector reports accurate readiness and concrete non-blocking warnings without a model turn, then completes one distinctive Markdown round trip.
3. Safe disposable qualification proves declared posture, transport, local state, and cleanup without deliberately attempting a real-world effect; courier-only targets refuse projects before connector import and publication.
4. A fake target proves both record kinds, inert bodies, immutable sessions, numbering, atomic publication, contention, timeout, failures, interruption, child termination, and no orphan.
5. Each of six harness adapters and one arbitrary application adapter can create, check, send, receive, record a note, and report warnings and failures.
6. Inspection finds no model API, dynamic registry, pair bridge, coordinator, router, scheduler, database, daemon, workflow engine, Git gate, plan store, approval or acknowledgment mechanism, review protocol, or Programming Loop.
7. Documentation matches the tested implementation, platform, and versions.

The complete authorized commands are:

```text
/usr/bin/python3 -m unittest -v tests.test_fake_peer
python3 -m unittest -v tests.test_work_mode
python3 -m tests.release_conformance inspect
python3 -m tests.release_conformance adapters
python3 -m tests.release_conformance qualify --peer codex
python3 -m tests.release_conformance qualify --peer claude
python3 -m tests.release_conformance qualify --peer zcode
python3 -m tests.release_conformance qualify --peer hermes
python3 -m tests.release_conformance qualify --peer minimax
python3 -m tests.release_conformance qualify --peer qwen
```

The work-mode extension added one focused selection over the unchanged
compatibility classes (`tests.test_fake_peer.FormatTwoRecords`,
`.SixTargetConnectorBehavior`, `.CommandLineBody`, and the eleven listed
`TurnBehavior` methods), the `tests.test_work_mode` module, and one live work
qualification per route declared supported:

```text
python3 -m tests.release_conformance qualify --peer codex  --mode work
python3 -m tests.release_conformance qualify --peer claude --mode work
python3 -m tests.release_conformance qualify --peer zcode  --mode work
python3 -m tests.release_conformance qualify --peer minimax --mode work
```

Hermes and Qwen declare no supported work route, so no work qualification is
run or claimed for them.

The September 28, 2026 correction batch — work-mode prerequisite
qualifications, event flushing, and the simulated Windows lock and cleanup
twins — extended `tests.test_work_mode` in place. The compatibility
selection, the adapter conformance run, and the live qualification count are
unchanged by it: no connector's work vector changed, only its prerequisite
checks, so the live work qualifications above were not re-run.

Each qualification includes readiness and one distinctive real model call. The approved transport correction's one additional Qwen-only qualification passed after local checks and independent review. No full suite, build, benchmark, all-pairs test, other repeated qualification, deliberate external-effect attempt, or duplicate reassurance pass is part of the ceiling. Application behavior is outside this boundary.

Release finishes only after accepted source is committed, pushed, reviewed in a pull request, merged to `main`, and the existing repository is public. The installed Claude adapter is updated from merged source, anonymous repository installation is verified, task-owned resources are removed, and the Ora task receives the actual merged commit and invocation path. Bridge itself installs no target CLI and signs no user in.
