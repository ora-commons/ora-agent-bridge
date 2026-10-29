"""The working mode: what it adds, and what it must never change.

A work session is an explicit alternative to the restricted review call. It
is created by naming a mode, it records its directories, its connector gets a
separate ordinary-permission work vector, and its records carry the caller's
inert metadata. These checks cover the finite list of behaviors that makes
that true without reproducing the whole original suite: legacy versus
explicit creation, the immutable Format 3 fields and request/reply pairing,
exact bodies and inert metadata, readiness certainty and honestly unsupported
routes, heartbeat and final-event ordering, separate code and document
directories, real image bytes reaching a tool, the ordinary-permission work
vectors of the four connectors that have them, a tool-reported model and
provider identity surfacing in the finished event and the Format 3 response
record while unreported identity leaves the fields absent, a failed or
stopped call retaining its records without inventing success, and optional
caller-named model and effort choices passing through each tool's own
documented switch in both vectors or being refused, before publication,
with the plain fact that the tool documents none.

The work-mode readiness checks are qualified here against each connector's
real source: a work check proves the work vector's own switches and never the
review-only switches, gates nothing on the review call's policy facts, and
for a target with no work vector performs no switch qualification at all.

Two platform twins are exercised here too, both simulated, because no Windows
machine is part of this repository's qualification: the `msvcrt.locking` twin
of the session lock, and the taskkill and kill-on-close job twins of
process-group cleanup, each driven through its Windows branch with the
platform's own primitive stood in for - including the parent-exits-first case
where the tree walk has no root to walk from and the job is the only owner
left standing, and the suspended creation - job assignment before the first
instruction can run - whose creation flags POSIX would refuse outright, so
the Windows creator itself is stood in for and what it was asked to create
with is the assertion. Event lines and the terminal response-path line being
flushed as they are written is checked as well, because a caller watching a
pipe sees nothing until a flush.

The lifecycle fixtures are the repository's own fake peer; no real target is
called here.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from ora_agent_bridge import (  # noqa: E402
    claude,
    cli,
    codex,
    connectors,
    hermes,
    locking,
    minimax,
    peer,
    qwen,
    record,
    runner,
    session,
    zcode,
)
from ora_agent_bridge.connectors import PeerCommand  # noqa: E402
from ora_agent_bridge.errors import BridgeError, Failure  # noqa: E402
from ora_agent_bridge.peer import CompletedCall  # noqa: E402
from tests.test_fake_peer import FAKE_PEER  # noqa: E402

#: How the fake peer reports the two processes a cleanup has to end.
PID_LINE = re.compile(r"^(PEER|CHILD) (\d+)$", re.MULTILINE)


def _wait_until_gone(pid, timeout=10.0):
    """Whether one process ended within the wait, asked of the pid itself."""
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def _await_reported_pids(pid_path, timeout=10.0):
    """Both process ids the fixture writes, waited for rather than guessed."""
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if os.path.exists(pid_path):
            with open(pid_path, encoding="utf-8") as stream:
                found = dict(
                    (kind, int(number))
                    for kind, number in PID_LINE.findall(stream.read())
                )
            if len(found) == 2:
                return found
        time.sleep(0.05)
    raise AssertionError("the fixture never reported both process ids")


#: A `run_bounded` caller on the simulated Windows branch, in a process of
#: its own so a check can signal the thing doing the waiting rather than the
#: check itself. It patches the platform switch and stands in for the Windows
#: creation - whose flags POSIX has no meaning for - with a wrapper that
#: records what was asked and hands the rest to the ordinary POSIX spawn,
#: for taskkill with a function that force-terminates the same tree the real
#: command's /T walk would reach, for the suspended root's resume with a
#: no-op, and for the kill-on-close job with a release that terminates the
#: same members the kernel's close would. It is stopped from outside while
#: it waits, and exits 3 when it leaves as the stop it was. Its arguments
#: are the fake peer's mode and the file the fixture writes its process ids
#: into.
WINDOWS_SIGNAL_DRIVER = (
    "import os, signal, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from ora_agent_bridge import peer\n"
    "pid_file = sys.argv[4]\n"
    "def tree_pids(pgid):\n"
    "    pids = [pgid]\n"
    "    with open(pid_file, encoding='utf-8') as stream:\n"
    "        for line in stream:\n"
    "            parts = line.split()\n"
    "            if len(parts) == 2 and parts[0] == 'CHILD':\n"
    "                pids.append(int(parts[1]))\n"
    "    return pids\n"
    "def terminate(pids):\n"
    "    found = False\n"
    "    for pid in pids:\n"
    "        try:\n"
    "            os.kill(pid, signal.SIGKILL)\n"
    "            found = True\n"
    "        except ProcessLookupError:\n"
    "            pass\n"
    "    return found\n"
    "def taskkill(pgid):\n"
    "    return not terminate(tree_pids(pgid))\n"
    "def own_tree(process):\n"
    "    return ('job', process.pid)\n"
    "def resume_root(process):\n"
    "    pass\n"
    "def release(job):\n"
    "    terminate(tree_pids(job[1]))\n"
    "real_popen = peer.subprocess.Popen\n"
    "def windows_spawn(argv, **kwargs):\n"
    "    kwargs.pop('creationflags', None)\n"
    "    return real_popen(argv, **kwargs)\n"
    "peer.WINDOWS = True\n"
    "peer.CLEANUP_GRACE_SECONDS = 0.2\n"
    "peer.ESCALATION_GRACE_SECONDS = 0.5\n"
    "peer.subprocess.Popen = windows_spawn\n"
    "peer._windows_taskkill = taskkill\n"
    "peer._windows_own_tree = own_tree\n"
    "peer._windows_resume_root = resume_root\n"
    "peer._windows_release_job = release\n"
    "try:\n"
    "    peer.run_bounded(\n"
    "        argv=(sys.executable, sys.argv[2], sys.argv[3], sys.argv[4]),\n"
    "        cwd=sys.argv[1],\n"
    "        env=tuple(os.environ.items()),\n"
    "        stdin_text='Start something and then stop answering.\\n',\n"
    "        deadline=peer.Deadline(60.0),\n"
    "    )\n"
    "except (peer.SignalStop, KeyboardInterrupt):\n"
    "    sys.exit(3)\n"
)


class WorkModeRecords(unittest.TestCase):
    """Legacy creation, explicit creation, and the strict Format 3 reader."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-work-records-")

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _read(self, path):
        with open(path, encoding="utf-8") as stream:
            return stream.read()

    def test_legacy_creation_writes_format_2_and_explicit_mode_writes_format_3(self):
        legacy = os.path.join(self.temp, "legacy")
        record.record(
            legacy,
            "session-create",
            "A legacy courier conversation.\n",
            initiator="gear-3",
            peer="claude",
            project=self.temp,
        )
        self.assertEqual(
            self._read(session.session_file(legacy)),
            "# Session\n\nBridge-Format: 2\nInitiator: gear-3\nPeer: claude\n"
            "Project: {0}\n\n## Body\n\n"
            "A legacy courier conversation.\n".format(self.temp),
        )
        legacy_record = session.read_session(legacy)
        self.assertEqual(legacy_record.bridge_format, "2")
        self.assertEqual(legacy_record.mode, "review")
        self.assertEqual(legacy_record.access_paths, ())

        code = os.path.join(self.temp, "code")
        os.mkdir(code)
        for index, mode in enumerate(("review", "work")):
            explicit = os.path.join(self.temp, "explicit-{0}".format(index))
            record.record(
                explicit,
                "session-create",
                "An explicit {0} session.\n".format(mode),
                initiator="vibe-coder",
                peer="codex",
                mode=mode,
            )
            text = self._read(session.session_file(explicit))
            self.assertIn("Bridge-Format: 3\n", text)
            self.assertIn("Mode: {0}\n".format(mode), text)
            parsed = session.read_session(explicit)
            self.assertEqual(parsed.bridge_format, "3")
            self.assertEqual(parsed.mode, mode)
            self.assertIsNone(parsed.project)

        # A work session may also name a project and access directories, and
        # an access path without an explicit mode is refused before anything
        # is written.
        docs = os.path.join(self.temp, "docs")
        os.mkdir(docs)
        working = os.path.join(self.temp, "working")
        record.record(
            working,
            "session-create",
            "Code here, documents there.\n",
            initiator="vibe-coder",
            peer="codex",
            project=code,
            mode="work",
            access_paths=[docs],
        )
        parsed = session.read_session(working)
        self.assertEqual(parsed.project, code)
        self.assertEqual(parsed.access_paths, (docs,))

        refused = os.path.join(self.temp, "refused")
        with self.assertRaises(BridgeError) as caught:
            record.record(
                refused,
                "session-create",
                "No mode, no access paths.\n",
                initiator="vibe-coder",
                peer="codex",
                access_paths=[docs],
            )
        self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
        self.assertFalse(os.path.exists(session.session_file(refused)))

        # A neutral note still works on a Format 3 session.
        note = record.record(working, "note", "Paired user answer.\n")
        self.assertTrue(note.endswith("0001-initiator-record.md"))

    def test_the_format_3_reader_is_strict_about_its_own_fields(self):
        variants = {
            "format 2 with a mode": (
                "# Session\n\nBridge-Format: 2\nInitiator: app\nPeer: claude\n"
                "Mode: work\n\n## Body\n\nMixed.\n"
            ),
            "format 3 without a mode": (
                "# Session\n\nBridge-Format: 3\nInitiator: app\nPeer: claude\n"
                "\n## Body\n\nModeless.\n"
            ),
            "format 3 with a bad mode": (
                "# Session\n\nBridge-Format: 3\nInitiator: app\nPeer: claude\n"
                "Mode: yolo\n\n## Body\n\nBad mode.\n"
            ),
            "format 2 with an access path": (
                "# Session\n\nBridge-Format: 2\nInitiator: app\nPeer: claude\n"
                "Access-Path: /tmp\n\n## Body\n\nMixed.\n"
            ),
            "repeated access path": (
                "# Session\n\nBridge-Format: 3\nInitiator: app\nPeer: claude\n"
                "Mode: work\nAccess-Path: /tmp\nAccess-Path: /tmp\n\n"
                "## Body\n\nRepeated.\n"
            ),
            "unknown field": (
                "# Session\n\nBridge-Format: 3\nInitiator: app\nPeer: claude\n"
                "Mode: work\nStatus: ready\n\n## Body\n\nUnknown.\n"
            ),
        }
        for index, (name, text) in enumerate(variants.items()):
            with self.subTest(case=name):
                directory = os.path.join(self.temp, "invalid-{0}".format(index))
                os.makedirs(session.messages_dir(directory))
                with open(
                    session.session_file(directory), "w", encoding="utf-8"
                ) as stream:
                    stream.write(text)
                with self.assertRaises(BridgeError) as caught:
                    session.read_session(directory)
                self.assertEqual(caught.exception.failure, Failure.SESSION_INVALID)


class WorkTurnBehavior(unittest.TestCase):
    """One work session, the fake peer, and the extended records."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-work-turn-")
        self.session_dir = os.path.join(self.temp, "session")
        self.code = os.path.join(self.temp, "code")
        self.docs = os.path.join(self.temp, "docs")
        os.mkdir(self.code)
        os.mkdir(self.docs)
        with open(os.path.join(self.code, "evidence.md"), "w") as stream:
            stream.write("PROJECT_READ_CANARY=work-canary\n")
        record.record(
            self.session_dir,
            "session-create",
            "Work session.\n",
            initiator="vibe-coder",
            peer="codex",
            project=self.code,
            mode="work",
            access_paths=[self.docs],
        )

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _builder(self, mode, *extra):
        argv = (sys.executable, FAKE_PEER, mode) + tuple(extra)

        def build(deadline, cwd, **kwargs):
            return PeerCommand(argv=argv, cwd=cwd, env=tuple(os.environ.items()))

        return build

    def _run(self, body="Do the work.\n", **kwargs):
        kwargs.setdefault("build_work_command", self._builder("plain"))
        return runner.run_turn(self.session_dir, body, 30.0, **kwargs)

    def test_codex_mid_turn_message_delivery(self):
        import queue
        controls, events = queue.Queue(), []
        native = os.path.join(self.temp, "native-codex")
        with open(native, "w") as stream:
            stream.write("#!" + sys.executable + "\n" + r'''import json, sys
accepted = False
def emit(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    event = json.loads(line)
    identifier, method = event.get("id"), event["method"]
    if method == "initialize":
        emit({"id": identifier, "result": {}})
    elif method == "thread/start":
        emit({"id": identifier, "result": {"thread": {"id": "owned-thread"}, "sandbox": {"type": "workspaceWrite", "writableRoots": ["/configured"]}}})
    elif method == "turn/start":
        assert event["params"]["sandboxPolicy"]["writableRoots"][0] == "/configured"
        emit({"id": identifier, "result": {"turn": {"id": "owned-turn"}}})
    elif method == "turn/steer":
        assert event["params"]["expectedTurnId"] == "owned-turn"
        if not accepted:
            accepted = True
            emit({"id": identifier, "result": {"turnId": "owned-turn"}})
        else:
            emit({"id": identifier, "error": {"message": "active turn changed"}})
            emit({"method": "item/completed", "params": {"threadId": "owned-thread", "turnId": "owned-turn", "item": {"type": "agentMessage", "text": "Correction received."}}})
            emit({"method": "turn/completed", "params": {"threadId": "owned-thread", "turn": {"id": "owned-turn", "status": "completed"}}})
''')
        os.chmod(native, 0o755)
        def report(line):
            event = json.loads(line)
            events.append(event)
            if event["event"] == "work_input_ready":
                for identifier, turn in (("accepted", "owned-turn"), ("stale", "later-turn"), ("race", "owned-turn")):
                    controls.put({"id": identifier, "thread_id": "owned-thread", "turn_id": turn,
                                  "text": "Exact correction \n" + identifier})
        result = runner.run_turn(self.session_dir, "Start work.", work_input=controls,
                                 event_writer=report, build_work_command=lambda deadline, cwd, **kwargs:
                                 PeerCommand(argv=(native,), cwd=cwd, env=tuple(os.environ.items()),
                                             response_parser=codex.parse_response))
        outcomes = {event["id"]: event["status"] for event in events if event["event"] == "work_input"}
        self.assertEqual(outcomes, {"accepted": "accepted", "stale": "refused", "race": "refused"})
        saved = []
        for path in sorted(Path(self.session_dir, "messages").glob("*-initiator-record.md")):
            body = self._read(str(path)).split("## Body\n\n", 1)[1]
            saved.append(json.loads(body.removeprefix("Work-Input-JSON: ").strip()))
        self.assertEqual([value["text"] for value in saved if value["status"] == "accepted"], ["Exact correction \naccepted"])
        self.assertIn("Correction received.", self._read(result.response_path))

    def test_the_work_round_trip_pairs_request_and_response_records(self):
        body = "Unicode caf\u00e9 \U0001f680 body.\n"
        result = self._run(
            body=body, note_ref="note-42", purpose="second-draft"
        )

        request = self._read(result_path(self.session_dir, 1))
        self.assertEqual(
            request,
            "# Message 0001\nFrom: vibe-coder\nTo: codex\n"
            "Note-Ref: note-42\nPurpose: second-draft\n\n## Body\n\n" + body,
        )
        response = self._read(result.response_path)
        self.assertEqual(
            response,
            "# Message 0002\nFrom: codex\nTo: vibe-coder\nAnswers: 0001\n\n"
            "## Body\n\n" + body,
        )

        # The metadata is inert: a body carrying header-shaped text keeps it
        # as body text, and the real header appears exactly once.
        shaped = "Note-Ref: forged\nPurpose: nope\nStill body text.\n"
        result = self._run(body=shaped, note_ref="real-ref")
        request = self._read(result_path(self.session_dir, 3))
        header, _, body_text = request.partition("\n## Body\n\n")
        self.assertEqual(header.count("Note-Ref:"), 1)
        self.assertIn("Note-Ref: real-ref\n", header)
        self.assertEqual(body_text, shaped)

    def test_zcode_permission_handoff_reaches_only_a_work_builder(self):
        for target, mode in (("zcode", "work"), ("zcode", "review"), ("codex", "work")):
            with self.subTest(target=target, mode=mode):
                directory = os.path.join(self.temp, target + "-" + mode)
                record.record(directory, "session-create", "Permission test.\n",
                              initiator="vibe-coder", peer=target, mode=mode)
                builder = mock.Mock(side_effect=self._builder("plain"))
                if (target, mode) == ("zcode", "work"):
                    runner.run_turn(directory, "Do approved work.\n",
                                    work_permission="full-access", build_work_command=builder)
                    self.assertEqual(builder.call_args.kwargs["work_permission"], "full-access")
                    # The choice belongs to one turn, not to the session.
                    builder.reset_mock()
                    runner.run_turn(directory, "Ordinary work.\n", build_work_command=builder)
                    self.assertNotIn("work_permission", builder.call_args.kwargs)
                else:
                    with self.assertRaises(BridgeError) as caught:
                        runner.run_turn(directory, "Do work.\n", work_permission="full-access",
                                        build_work_command=builder)
                    self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                    builder.assert_not_called()
                    self.assertEqual(os.listdir(os.path.join(directory, "messages")), [])

    def test_extended_run_options_stay_off_format_2_sessions(self):
        legacy = os.path.join(self.temp, "legacy")
        record.record(
            legacy, "session-create", "Legacy.\n", initiator="gear-3", peer="claude"
        )
        image = os.path.join(self.temp, "pixel.png")
        with open(image, "wb") as stream:
            stream.write(b"\x89PNG\r\n\x1a\n")
        for kwargs in (
            {"note_ref": "n"},
            {"purpose": "p"},
            {"attachments": (image,)},
        ):
            with self.subTest(options=sorted(kwargs)):
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(
                        legacy, "Body.\n", 30.0, self._builder("plain"), **kwargs
                    )
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
        self.assertEqual(os.listdir(session.messages_dir(legacy)), [])

        # On a Format 3 review session the inert metadata is welcome, but an
        # attachment still needs a work session.
        review = os.path.join(self.temp, "review")
        record.record(
            review,
            "session-create",
            "Explicit review.\n",
            initiator="gear-3",
            peer="claude",
            mode="review",
        )
        runner.run_turn(
            review,
            "Review body.\n",
            30.0,
            self._builder("plain"),
            note_ref="note-7",
            purpose="gear-3-check",
        )
        request = self._read(result_path(review, 1))
        self.assertIn("Note-Ref: note-7\nPurpose: gear-3-check\n", request)
        with self.assertRaises(BridgeError) as caught:
            runner.run_turn(
                review, "Review body.\n", 30.0, self._builder("plain"),
                attachments=(image,),
            )
        self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
        with self.assertRaises(BridgeError) as caught:
            runner.run_turn(
                self.session_dir,
                "Work body.\n",
                30.0,
                build_work_command=self._builder("plain"),
                attachments=(os.path.join(self.temp, "missing.png"),),
            )
        self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
        self.assertEqual(
            os.listdir(session.messages_dir(self.session_dir)), []
        )

    def test_the_work_builder_receives_the_code_and_document_directories(self):
        observed = {}

        def build(deadline, cwd, access_paths=(), attachments=(), **kwargs):
            observed["cwd"] = cwd
            observed["access_paths"] = access_paths
            script = (
                "import os, sys\n"
                "target = os.path.join(sys.argv[2], 'authorized.md')\n"
                "with open(target, 'w', encoding='utf-8') as stream:\n"
                "    stream.write(sys.argv[1])\n"
                "sys.stdout.write(sys.argv[1])\n"
            )
            return PeerCommand(
                argv=(sys.executable, "-c", script, "WORK_DOC: done", self.docs),
                cwd=cwd,
                env=tuple(os.environ.items()),
            )

        result = runner.run_turn(
            self.session_dir, "Edit the authorized document.\n", 30.0,
            build_work_command=build,
        )
        self.assertEqual(observed["cwd"], self.code)
        self.assertEqual(observed["access_paths"], (self.docs,))
        self.assertTrue(
            os.path.isfile(os.path.join(self.docs, "authorized.md"))
        )
        self.assertEqual(os.listdir(self.code), ["evidence.md"])
        self.assertIn("Answers: 0001", self._read(result.response_path))

    def test_actual_image_bytes_reach_the_tool(self):
        payload = bytes(bytearray((red % 251 for red in range(1024))))
        image = os.path.join(self.temp, "distinctive.bin")
        with open(image, "wb") as stream:
            stream.write(payload)
        observed = {}

        def build(deadline, cwd, access_paths=(), attachments=(), **kwargs):
            observed["attachments"] = attachments
            script = (
                "import hashlib, sys\n"
                "with open(sys.argv[1], 'rb') as stream:\n"
                "    data = stream.read()\n"
                "sys.stdout.write('SHA256 ' + hashlib.sha256(data).hexdigest())\n"
            )
            argv = [sys.executable, "-c", script]
            argv.extend(attachments)
            return PeerCommand(
                argv=tuple(argv), cwd=cwd, env=tuple(os.environ.items())
            )

        result = runner.run_turn(
            self.session_dir, "Hash the attachment.\n", 30.0,
            build_work_command=build, attachments=(image,),
        )
        self.assertEqual(observed["attachments"], (image,))
        request = self._read(result_path(self.session_dir, 1))
        self.assertIn("Attachment: {0}\n".format(image), request)
        response = self._read(result.response_path)
        self.assertIn(
            "SHA256 {0}".format(hashlib.sha256(payload).hexdigest()),
            response,
        )

    def test_no_timeout_waits_for_a_reply_past_a_short_named_budget(self):
        class SlowWorkConnector:
            CAPABILITIES = connectors.Capabilities("supported", "fake", "unsupported", "no images")

            @staticmethod
            def build_work_command(deadline, cwd, **kwargs):
                peer.run_bounded(
                    (sys.executable, FAKE_PEER, "delay-echo", "0.15"),
                    cwd, tuple(os.environ.items()), "Prerequisite.\n", deadline,
                )
                return PeerCommand(
                    argv=(sys.executable, FAKE_PEER, "delay-echo", "0.15"),
                    cwd=cwd, env=tuple(os.environ.items()),
                )

        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(runner.connectors, "resolve", return_value=SlowWorkConnector), \
                mock.patch.object(peer, "HEARTBEAT_SECONDS", 0.05), \
                mock.patch("sys.stdin", io.StringIO("Wait for the answer.\n")), \
                mock.patch("sys.stdout", output), mock.patch("sys.stderr", errors):
            status = cli.main([
                "run", "--session", self.session_dir, "--no-timeout", "--events-jsonl",
            ])
        self.assertEqual(status, 0, errors.getvalue())
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([e["event"] for e in events].count("finished"), 1)
        self.assertEqual(events[-1]["outcome"], "success")
        self.assertEqual(
            {e["phase"] for e in events if e["event"] == "heartbeat"},
            {"prerequisites", "peer-call"},
        )
        response = self._read(events[-1]["response_path"])
        self.assertEqual(response.split("\n## Body\n\n", 1)[1], "Wait for the answer.\n")
        self.assertEqual(sorted(os.listdir(session.messages_dir(self.session_dir))), [
            "0001-initiator-to-peer.md", "0002-peer-to-initiator.md",
        ])

    def test_cli_timeout_choices_keep_finite_defaults_and_are_exclusive(self):
        class HangingWorkConnector:
            CAPABILITIES = connectors.Capabilities("supported", "fake", "unsupported", "no images")

            @staticmethod
            def build_work_command(deadline, cwd, **kwargs):
                return PeerCommand(
                    argv=(sys.executable, FAKE_PEER, "hang", "1"),
                    cwd=cwd, env=tuple(os.environ.items()),
                )

        # A named bounded wait returns control without stopping the peer:
        # nonzero exit, a pending finished event carrying the retained
        # evidence, and no response - then this check kills the peer itself.
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(runner.connectors, "resolve", return_value=HangingWorkConnector), \
                mock.patch("sys.stdin", io.StringIO("Do not wait forever.\n")), \
                mock.patch("sys.stdout", output), mock.patch("sys.stderr", errors):
            status = cli.main([
                "run", "--session", self.session_dir, "--events-jsonl",
                "--timeout", "0.15",
            ])
        self.assertEqual(status, 1)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events[-1]["outcome"], "pending")
        self.assertTrue(events[-1]["request_path"].endswith(
            "0001-initiator-to-peer.md"
        ))
        self.assertEqual(events[-1]["peer_running"], True)
        self.assertNotIn("response_path", events[-1])
        self.assertEqual(events[-1]["session_dir"], self.session_dir)
        self.assertTrue(events[-1]["request_on_disk"])
        pid = events[-1]["peer_pid"]
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
        # Detach retained the capture files; this check now removes them.
        retained = events[-1].get("capture_paths", [])
        self.assertTrue(retained)
        for path in retained:
            self.assertTrue(os.path.exists(path))
            os.unlink(path)

        # The default is deadline-free: a peer that answers slowly is waited
        # for and its response published.
        class SlowWorkConnector:
            CAPABILITIES = connectors.Capabilities("supported", "fake", "unsupported", "no images")

            @staticmethod
            def build_work_command(deadline, cwd, **kwargs):
                return PeerCommand(
                    argv=(sys.executable, FAKE_PEER, "delay-echo", "0.5"),
                    cwd=cwd, env=tuple(os.environ.items()),
                )

        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(runner.connectors, "resolve", return_value=SlowWorkConnector), \
                mock.patch("sys.stdin", io.StringIO("Wait as long as it takes.\n")), \
                mock.patch("sys.stdout", output), mock.patch("sys.stderr", errors):
            status = cli.main(["run", "--session", self.session_dir, "--events-jsonl"])
        self.assertEqual(status, 0, errors.getvalue())
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events[-1]["outcome"], "success")

        before = sorted(os.listdir(session.messages_dir(self.session_dir)))
        self.assertEqual(before, [
            "0001-initiator-to-peer.md", "0002-initiator-to-peer.md",
            "0003-peer-to-initiator.md",
        ])
        with mock.patch("sys.stderr", io.StringIO()) as errors:
            status = cli.main([
                "run", "--session", self.session_dir,
                "--timeout", "1", "--no-timeout",
            ])
        self.assertEqual(status, 1)
        self.assertIn("not allowed with argument", errors.getvalue())
        self.assertEqual(sorted(os.listdir(session.messages_dir(self.session_dir))), before)

    @unittest.skipUnless(os.name == "posix", "Uses POSIX process-group stop")
    def test_no_timeout_stop_ends_owned_descendants_without_retry_or_answer(self):
        # The real CLI runs in its own process, with only its connector replaced
        # by the repository fixture. A stop during a probe precedes publication;
        # a stop during work retains exactly one request and no invented reply.
        source = """
import io, os, sys
sys.path.insert(0, sys.argv[1])
from ora_agent_bridge import cli, connectors, peer, runner
from ora_agent_bridge.connectors import PeerCommand
class FakeConnector:
    CAPABILITIES = connectors.Capabilities('supported', 'fake', 'unsupported', 'no images')
    @staticmethod
    def build_work_command(deadline, cwd, **kwargs):
        argv = (sys.executable, sys.argv[2], 'write-pids-then-hang', sys.argv[4])
        if sys.argv[5] == 'prerequisites':
            peer.run_bounded(argv, cwd, tuple(os.environ.items()), '', deadline)
        return PeerCommand(argv=argv, cwd=cwd, env=tuple(os.environ.items()))
runner.connectors.resolve = lambda target: FakeConnector
peer.HEARTBEAT_SECONDS = 0.05
sys.stdin = io.StringIO('Start once; preserve partial work if stopped.\\n')
sys.exit(cli.main(['run', '--session', sys.argv[3], '--no-timeout', '--events-jsonl']))
"""
        for phase in ("prerequisites", "peer-call"):
            with self.subTest(phase=phase):
                pid_path = os.path.join(self.temp, phase + "-pids.txt")
                driver = subprocess.Popen(
                    [sys.executable, "-c", source, REPO_ROOT, FAKE_PEER,
                     self.session_dir, pid_path, phase],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True,
                )
                try:
                    pids = _await_reported_pids(pid_path)
                    driver.terminate()
                    output, errors = driver.communicate(timeout=10.0)
                    self.assertEqual(driver.returncode, 1, errors)
                    events = [json.loads(line) for line in output.splitlines()]
                    self.assertEqual([e["event"] for e in events].count("finished"), 1)
                    self.assertEqual(events[-1]["outcome"], "stopped")
                    self.assertNotIn("response_path", events[-1])
                    for pid in pids.values():
                        self.assertTrue(_wait_until_gone(pid), "Owned process survived Stop")
                    self.assertEqual(sorted(os.listdir(session.messages_dir(self.session_dir))),
                                     [] if phase == "prerequisites" else ["0001-initiator-to-peer.md"])
                    with locking.session_lock(self.session_dir):
                        pass
                finally:
                    # Even an assertion failure must clean up this fixture's
                    # exact processes; no background test worker survives.
                    if driver.poll() is None:
                        driver.terminate()
                        try:
                            driver.wait(timeout=10.0)
                        except subprocess.TimeoutExpired:
                            driver.kill()
                            driver.wait(timeout=10.0)
                    if os.path.exists(pid_path):
                        with open(pid_path, encoding="utf-8") as stream:
                            owned = dict((name, int(pid)) for name, pid in PID_LINE.findall(stream.read()))
                        if "PEER" in owned:
                            try:
                                os.killpg(owned["PEER"], signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    driver.stdout.close()
                    driver.stderr.close()

    def test_lifecycle_events_order_heartbeats_before_one_final_result(self):
        events = []
        with mock.patch.object(peer, "HEARTBEAT_SECONDS", 0.05):
            result = runner.run_turn(
                self.session_dir,
                "Answer after a short wait.\n",
                30.0,
                build_work_command=self._builder("delay-echo", "0.35"),
                event_writer=events.append,
            )
        parsed = [json.loads(line) for line in events]
        kinds = [event["event"] for event in parsed]
        self.assertEqual(kinds[0], "started")
        self.assertEqual(kinds[-1], "finished")
        self.assertEqual(kinds.count("finished"), 1)
        self.assertIn("heartbeat", kinds)
        first_heartbeat = kinds.index("heartbeat")
        self.assertLess(first_heartbeat, kinds.index("finished"))
        self.assertTrue(
            all(
                event["phase"] == "peer-call"
                for event in parsed
                if event["event"] == "heartbeat"
            )
        )
        finished = parsed[-1]
        self.assertEqual(finished["outcome"], "success")
        self.assertEqual(finished["response_path"], result.response_path)
        self.assertTrue(finished["request_path"].endswith("0001-initiator-to-peer.md"))

        # A bounded work wait returns control without stopping the peer: the
        # request is retained, no response exists, and cancelling cleans up.
        events = []
        pending = runner.run_turn(
            self.session_dir,
            "Never answer in time.\n",
            0.6,
            build_work_command=self._builder("hang"),
            event_writer=events.append,
        )
        self.assertIsInstance(pending, runner.PendingTurn)
        parsed = [json.loads(line) for line in events]
        self.assertEqual(
            [event["event"] for event in parsed][-1], "wait_expired"
        )
        self.assertNotIn(
            "finished", [event["event"] for event in parsed]
        )
        pending.cancel()
        parsed = [json.loads(line) for line in events]
        finished = parsed[-1]
        self.assertEqual(finished["outcome"], "stopped")
        self.assertNotIn("response_path", finished)
        self.assertTrue(finished["request_path"].endswith("0003-initiator-to-peer.md"))
        self.assertEqual(
            sorted(os.listdir(session.messages_dir(self.session_dir))),
            ["0001-initiator-to-peer.md", "0002-peer-to-initiator.md",
             "0003-initiator-to-peer.md"],
        )

    def test_a_failed_work_call_keeps_its_records_without_false_success(self):
        class FailingWorkConnector:
            CAPABILITIES = connectors.Capabilities(
                work="supported",
                work_detail="fake",
                image="supported",
                image_detail="fake",
            )

            @staticmethod
            def build_work_command(
                deadline, cwd, access_paths=(), attachments=(), **kwargs
            ):
                return PeerCommand(
                    argv=(sys.executable, FAKE_PEER, "fail"),
                    cwd=cwd,
                    env=tuple(os.environ.items()),
                )

        stderr = io.StringIO()
        output = io.StringIO()
        with mock.patch.object(
            runner.connectors, "resolve", return_value=FailingWorkConnector
        ):
            with mock.patch("sys.stderr", stderr), mock.patch("sys.stdout", output):
                with mock.patch("sys.stdin", io.StringIO("Please fail.\n")):
                    status = cli.main(
                        [
                            "run",
                            "--session",
                            self.session_dir,
                            "--events-jsonl",
                            "--note-ref",
                            "note-9",
                        ]
                    )
        self.assertEqual(status, 1)
        self.assertIn("deliberate failure", stderr.getvalue())
        parsed = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(
            [event["event"] for event in parsed], ["started", "finished"]
        )
        self.assertEqual(parsed[-1]["outcome"], "failure")
        self.assertTrue(
            parsed[-1]["request_path"].endswith("0001-initiator-to-peer.md")
        )
        self.assertIn(
            "peer harness's program exited with a failure", parsed[-1]["reason"]
        )
        self.assertEqual(
            os.listdir(session.messages_dir(self.session_dir)),
            ["0001-initiator-to-peer.md"],
        )

        # A broken event pipe can mask the reports, never the truth: the call
        # fails, the request stays, and no success exists anywhere.
        def broken_writer(line):
            raise OSError("broken pipe")

        with mock.patch.object(
            runner.connectors, "resolve", return_value=FailingWorkConnector
        ):
            with self.assertRaises(BridgeError) as caught:
                runner.run_turn(
                    self.session_dir,
                    "Fail again.\n",
                    30.0,
                    event_writer=broken_writer,
                )
        self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
        self.assertEqual(
            sorted(os.listdir(session.messages_dir(self.session_dir))),
            ["0001-initiator-to-peer.md", "0002-initiator-to-peer.md"],
        )

    def test_event_lines_and_the_response_path_line_are_flushed_as_written(self):
        """A caller watching a pipe sees nothing until a flush.

        Python buffers standard output when it is a pipe, so an unflushed
        event line would sit in the buffer until the process ended - which
        for a long turn is the whole turn. Every lifecycle event, and the
        one response-path line a flag-free run prints, must be pushed out at
        the moment it is written.
        """

        class _CountingFlush(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flushes = 0

            def flush(self):
                self.flushes += 1
                return super().flush()

        class _EchoWorkConnector:
            CAPABILITIES = connectors.Capabilities(
                work="supported",
                work_detail="fake",
                image="supported",
                image_detail="fake",
            )

            @staticmethod
            def build_work_command(
                deadline, cwd, access_paths=(), attachments=(), **kwargs
            ):
                return PeerCommand(
                    argv=(sys.executable, FAKE_PEER, "plain"),
                    cwd=cwd,
                    env=tuple(os.environ.items()),
                )

        with mock.patch.object(
            runner.connectors, "resolve", return_value=_EchoWorkConnector
        ):
            events = _CountingFlush()
            with mock.patch("sys.stdout", events), mock.patch(
                "sys.stdin", io.StringIO("Answer me.\n")
            ):
                status = cli.main(
                    ["run", "--session", self.session_dir, "--events-jsonl"]
                )
            self.assertEqual(status, 0)
            kinds = [
                json.loads(line)["event"]
                for line in events.getvalue().splitlines()
            ]
            self.assertEqual(kinds, ["started", "finished"])
            self.assertEqual(
                events.flushes,
                len(kinds),
                "each event line must be flushed as it is written",
            )

            plain = _CountingFlush()
            with mock.patch("sys.stdout", plain), mock.patch(
                "sys.stdin", io.StringIO("Answer again.\n")
            ):
                status = cli.main(["run", "--session", self.session_dir])
            self.assertEqual(status, 0)
            self.assertTrue(
                plain.getvalue().strip().endswith("0004-peer-to-initiator.md")
            )
            self.assertEqual(plain.flushes, 1)

    def _read(self, path):
        with open(path, encoding="utf-8") as stream:
            return stream.read()


def result_path(session_dir, sequence):
    return session.message_path(
        session_dir, sequence, session.INITIATOR_TO_PEER_SUFFIX
    )


class ReportedIdentityAttribution(unittest.TestCase):
    """The identity a tool's own output reported survives the run.

    MiniMax's JSON result mode - the one fixed output route today that
    reports which provider and model answered - is driven through its real
    parser, builder, and runner: the identity the parser validated must be
    the identity that surfaces, in the finished event's optional fields and
    in a Format 3 response record's `Model:`/`Provider:` header lines,
    while the published body stays the final answer text alone. A response
    no identity traveled with leaves both surfaces without the fields -
    never empty strings - and a reported identity that does not match the
    required model still fails the call before any response exists.
    """

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-work-attribution-")
        self.session_dir = os.path.join(self.temp, "session")
        record.record(
            self.session_dir,
            "session-create",
            "A MiniMax work session.\n",
            initiator="vibe-coder",
            peer="minimax",
            mode="work",
        )

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    @staticmethod
    def _result(provider_id="minimax", model_id="MiniMax-M2.5"):
        """One MiniMax exec.result JSON, as the installed program prints it."""
        return json.dumps(
            {
                "schemaVersion": 1,
                "type": "exec.result",
                "status": "succeeded",
                "output": "The attributed answer.\n",
                "model": {"providerId": provider_id, "modelId": model_id},
            }
        )

    def _fake_mcode(self, result_json):
        """A stand-in mcode that prints one fixed JSON result, args ignored."""
        payload = os.path.join(self.temp, "exec-result.json")
        with open(payload, "w", encoding="utf-8") as stream:
            stream.write(result_json)
        program = os.path.join(self.temp, "mcode")
        with open(program, "w", encoding="utf-8") as stream:
            stream.write('#!/bin/sh\ncat "{0}"\n'.format(payload))
        os.chmod(program, 0o755)
        return (
            program,
            "0.2.7",
            "Darwin 26 arm64",
            "no state-free noninteractive authentication-status command "
            "is available",
            (minimax.WORK_WARNING,),
        )

    def _read(self, path):
        with open(path, encoding="utf-8") as stream:
            return stream.read()

    def test_a_reported_identity_surfaces_in_the_event_and_the_record(self):
        parsed = minimax.parse_response(
            self._result(), "minimax/MiniMax-M2.5"
        )
        self.assertEqual(parsed, "The attributed answer.\n")
        self.assertEqual(parsed.provider, "minimax")
        self.assertEqual(parsed.model, "MiniMax-M2.5")

        events = []
        with mock.patch.object(
            minimax,
            "_prerequisites",
            return_value=self._fake_mcode(self._result()),
        ):
            result = runner.run_turn(
                self.session_dir,
                "Answer as yourself.\n",
                30.0,
                required_model="minimax/MiniMax-M2.5",
                event_writer=events.append,
            )
        finished = json.loads(events[-1])
        self.assertEqual(finished["outcome"], "success")
        # The surfaced identity is the one the parser validated: the tool's
        # own reported halves, exactly as they were printed.
        self.assertEqual(finished["model"], "MiniMax-M2.5")
        self.assertEqual(finished["provider"], "minimax")
        self.assertEqual(
            self._read(result.response_path),
            "# Message 0002\nFrom: minimax\nTo: vibe-coder\nAnswers: 0001\n"
            "Model: MiniMax-M2.5\nProvider: minimax\n\n## Body\n\n"
            "The attributed answer.\n",
        )

    def test_unreported_identity_leaves_the_fields_absent_never_empty(self):
        def plain_builder(response_parser):
            def build(deadline, cwd, **kwargs):
                return PeerCommand(
                    argv=(sys.executable, FAKE_PEER, "plain"),
                    cwd=cwd,
                    env=tuple(os.environ.items()),
                    response_parser=response_parser,
                )

            return build

        routes = (
            ("no structured output at all", None),
            ("structured output reporting no identity", lambda output: output),
        )
        for name, parser in routes:
            with self.subTest(route=name):
                events = []
                result = runner.run_turn(
                    self.session_dir,
                    "Plain answer.\n",
                    30.0,
                    build_work_command=plain_builder(parser),
                    event_writer=events.append,
                )
                finished = json.loads(events[-1])
                self.assertEqual(finished["outcome"], "success")
                self.assertNotIn("model", finished)
                self.assertNotIn("provider", finished)
                header = self._read(result.response_path).partition(
                    "\n## Body"
                )[0]
                self.assertNotIn("Model:", header)
                self.assertNotIn("Provider:", header)

    def test_a_mismatched_reported_identity_fails_before_any_response(self):
        events = []
        with mock.patch.object(
            minimax,
            "_prerequisites",
            return_value=self._fake_mcode(
                self._result(model_id="MiniMax-M1.5")
            ),
        ):
            with self.assertRaises(BridgeError) as caught:
                runner.run_turn(
                    self.session_dir,
                    "Answer as an older model.\n",
                    30.0,
                    required_model="minimax/MiniMax-M2.5",
                    event_writer=events.append,
                )
        self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
        self.assertIn("did not exactly match", caught.exception.detail)
        finished = json.loads(events[-1])
        self.assertEqual(finished["outcome"], "failure")
        self.assertNotIn("model", finished)
        self.assertNotIn("provider", finished)
        self.assertEqual(
            os.listdir(session.messages_dir(self.session_dir)),
            ["0001-initiator-to-peer.md"],
        )

    def test_a_format_two_record_never_grows_attribution_headers(self):
        legacy = os.path.join(self.temp, "legacy")
        record.record(
            legacy,
            "session-create",
            "A legacy MiniMax session.\n",
            initiator="gear-3",
            peer="minimax",
        )
        events = []
        with mock.patch.object(
            minimax,
            "_prerequisites",
            return_value=self._fake_mcode(self._result()),
        ):
            result = runner.run_turn(
                legacy,
                "Legacy bounded call.\n",
                30.0,
                required_model="minimax/MiniMax-M2.5",
                event_writer=events.append,
            )
        # The legacy record keeps its exact shape; the opt-in event stream,
        # not the record contract, is where the identity also surfaces.
        self.assertEqual(
            self._read(result.response_path),
            "# Message 0002\nFrom: minimax\nTo: gear-3\n\n## Body\n\n"
            "The attributed answer.\n",
        )
        finished = json.loads(events[-1])
        self.assertEqual(finished["model"], "MiniMax-M2.5")
        self.assertEqual(finished["provider"], "minimax")


class ReadinessAndSupport(unittest.TestCase):
    """The JSON readiness result and honestly unsupported work routes."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-work-readiness-")

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _check_json(self, peer, mode, connector):
        output = io.StringIO()
        with mock.patch.object(connectors, "resolve", return_value=connector):
            with mock.patch("sys.stdout", output):
                status = cli.main(
                    ["check", "--peer", peer, "--mode", mode, "--json"]
                )
        return status, json.loads(output.getvalue())

    def test_json_readiness_reports_facts_and_capability_states(self):
        class UnknownAuthConnector:
            CAPABILITIES = connectors.Capabilities(
                work="supported",
                work_detail="route: work vector",
                image="unknown",
                image_detail="no evidence either way",
            )
            CHOICE_SWITCHES = connectors.ChoiceSwitches()

            @staticmethod
            def check(deadline, cwd, mode="review"):
                return connectors.CheckResult(
                    "unconfirmed",
                    ("one warning",),
                    executable="/fake/peer",
                    version="9.9.9",
                    platform="Darwin 26 arm64",
                    authentication_confirmed=False,
                )

        status, result = self._check_json(
            "codex", "work", UnknownAuthConnector
        )
        self.assertEqual(status, 0)
        self.assertEqual(
            result,
            {
                "peer": "codex",
                "mode": "work",
                "bridge_version": cli.VERSION,
                "ready": True,
                "command_present": True,
                "executable": "/fake/peer",
                "version": "9.9.9",
                "platform": "Darwin 26 arm64",
                "authentication": "unknown",
                "review": "supported",
                "work": "supported",
                "image": "unknown",
                "compatibility": "unknown",
                "warnings": ["one warning"],
            },
        )

        class ConfirmedConnector(UnknownAuthConnector):
            @staticmethod
            def check(deadline, cwd, mode="review"):
                return connectors.CheckResult(
                    "confirmed",
                    (),
                    executable="/fake/peer",
                    version="9.9.9",
                    platform="Darwin 26 arm64",
                    authentication_confirmed=True,
                )

        _status, result = self._check_json("codex", "review", ConfirmedConnector)
        self.assertEqual(result["authentication"], "confirmed")
        self.assertEqual(result["mode"], "review")

        class QualifiedConnector(ConfirmedConnector):
            @staticmethod
            def check(deadline, cwd, mode="review"):
                return connectors.CheckResult(
                    "confirmed",
                    (),
                    executable="/fake/peer",
                    version="9.9.9",
                    platform="Darwin 26 arm64",
                    authentication_confirmed=True,
                    qualified=True,
                )

        _status, result = self._check_json("codex", "review", QualifiedConnector)
        self.assertEqual(result["compatibility"], "supported")

        class UnauthenticatedConnector:
            CAPABILITIES = UnknownAuthConnector.CAPABILITIES
            CHOICE_SWITCHES = UnknownAuthConnector.CHOICE_SWITCHES

            @staticmethod
            def check(deadline, cwd, mode="review"):
                raise BridgeError(
                    Failure.AUTHENTICATION_REQUIRED, detail="login status exited 1"
                )

        status, result = self._check_json("codex", "work", UnauthenticatedConnector)
        self.assertEqual(status, 1)
        self.assertEqual(result["ready"], False)
        self.assertEqual(result["authentication"], "required")
        self.assertIn("login status exited 1", result["reason"])
        self.assertTrue(result["next_action"])
        # A failed login erases neither the command nor the implemented
        # capabilities; only the compatibility of this combination, which the
        # failed check never established, stays unset (null).
        self.assertEqual(result["command_present"], True)
        self.assertEqual(result["review"], "supported")
        self.assertEqual(result["work"], "supported")
        self.assertEqual(result["image"], "unknown")
        self.assertIsNone(result["compatibility"])

        class MissingConnector:
            CAPABILITIES = UnknownAuthConnector.CAPABILITIES
            CHOICE_SWITCHES = UnknownAuthConnector.CHOICE_SWITCHES

            @staticmethod
            def check(deadline, cwd, mode="review"):
                raise BridgeError(Failure.MISSING_CLI, detail="codex")

        status, result = self._check_json("codex", "review", MissingConnector)
        self.assertEqual(status, 1)
        self.assertEqual(result["ready"], False)
        self.assertEqual(result["command_present"], False)
        self.assertEqual(result["review"], "supported")
        self.assertEqual(result["work"], "supported")

        class UnsupportedWorkConnector:
            CAPABILITIES = connectors.Capabilities(
                work="unsupported",
                work_detail="terminal route: run it yourself",
                image="unknown",
                image_detail="no evidence either way",
            )
            CHOICE_SWITCHES = connectors.ChoiceSwitches()

            @staticmethod
            def check(deadline, cwd, mode="review"):
                return connectors.CheckResult(
                    "unconfirmed",
                    (),
                    executable="/fake/peer",
                    version="9.9.9",
                    platform="Darwin 26 arm64",
                    authentication_confirmed=False,
                    qualified=True,
                )

        status, result = self._check_json(
            "hermes", "work", UnsupportedWorkConnector
        )
        self.assertEqual(status, 0)
        self.assertEqual(result["compatibility"], "unsupported")
        self.assertIn("terminal route", result["warnings"][-1])

    def test_unsupported_work_routes_are_refused_with_the_real_reason(self):
        for connector, peer_id in ((hermes, "hermes"), (qwen, "qwen")):
            with self.subTest(peer=peer_id):
                self.assertEqual(connector.CAPABILITIES.work, "unsupported")
                self.assertIn("terminal", connector.CAPABILITIES.work_detail)
                session_dir = os.path.join(self.temp, peer_id)
                record.record(
                    session_dir,
                    "session-create",
                    "A work session this target cannot serve.\n",
                    initiator="vibe-coder",
                    peer=peer_id,
                    mode="work",
                )
                captured = io.StringIO()
                with mock.patch.object(
                    runner.connectors,
                    "resolve",
                    return_value=connector,
                ):
                    with mock.patch("sys.stderr", captured), mock.patch(
                        "sys.stdin", io.StringIO("Please work.\n")
                    ):
                        status = cli.main(["run", "--session", session_dir])
                self.assertEqual(status, 1)
                self.assertIn("work mode is unsupported", captured.getvalue())
                self.assertIn("terminal", captured.getvalue())
                self.assertEqual(
                    os.listdir(session.messages_dir(session_dir)), []
                )


class CodexStructuredDispatch(unittest.TestCase):
    """The launched Codex owns model discovery; structured results own success."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-codex-dispatch-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.codex_home = os.path.join(self.temp, "codex-home")
        self.bin = os.path.join(self.temp, "bin")
        os.makedirs(self.codex_home)
        os.makedirs(self.bin)
        self.program = os.path.join(self.temp, "installed-codex")
        self.log = os.path.join(self.temp, "launches.jsonl")
        self.events = os.path.join(self.temp, "events.jsonl")
        # The help the fake program answers with names the work vector's
        # own switches, including `-m` for a caller-named model choice.
        help_text = "\n".join(codex.QUALIFICATION.restrictions + ("-m",))
        with open(self.program, "w", encoding="utf-8") as stream:
            stream.write(
                "#!" + sys.executable + "\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "if args == ['--version']:\n"
                "    print('codex-cli 0.155.1')\n"
                "elif args == ['login', 'status']:\n"
                "    print('Logged in using ChatGPT', file=sys.stderr)\n"
                "elif args == ['exec', '--help']:\n"
                "    print(" + repr(help_text) + ")\n"
                "else:\n"
                "    assert args[0] == 'exec' and '--json' in args\n"
                "    body = sys.stdin.read()\n"
                "    with open(os.environ['BRIDGE_FAKE_CODEX_LOG'], 'a') as out:\n"
                "        out.write(json.dumps({'argv': args, 'body': body}) + '\\n')\n"
                "    rollout = os.environ.get('BRIDGE_FAKE_CODEX_ROLLOUT')\n"
                "    if rollout:\n"
                "        os.makedirs(os.path.dirname(rollout), exist_ok=True)\n"
                "        with open(rollout, 'w', encoding='utf-8') as stream:\n"
                "            stream.write(json.dumps({'type': 'turn_context', 'payload': {'model': os.environ['BRIDGE_FAKE_CODEX_MODEL'], 'effort': os.environ['BRIDGE_FAKE_CODEX_EFFORT']}}) + '\\n')\n"
                "    if os.environ.get('BRIDGE_FAKE_CODEX_WARNINGS'):\n"
                "        sys.stderr.write('unrelated startup warning\\n' * 2000)\n"
                "    source = os.environ.get('BRIDGE_FAKE_CODEX_EVENTS')\n"
                "    if source:\n"
                "        with open(source, encoding='utf-8') as data:\n"
                "            sys.stdout.write(data.read())\n"
                "    else:\n"
                "        with open(os.path.join(os.environ['CODEX_HOME'], 'config.toml')) as config:\n"
                "            model = json.loads(config.read().split('=', 1)[1].strip())\n"
                "        print(json.dumps({'type': 'item.completed', 'item': {\n"
                "            'id': 'final', 'type': 'agent_message', 'text': model}}))\n"
                "        print(json.dumps({'type': 'turn.completed'}))\n"
                "    sys.exit(int(os.environ.get('BRIDGE_FAKE_CODEX_EXIT', '0')))\n"
            )
        os.chmod(self.program, 0o755)
        os.symlink(self.program, os.path.join(self.bin, "codex"))
        patcher = mock.patch.dict(
            os.environ,
            {
                "PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
                "CODEX_HOME": self.codex_home,
                "BRIDGE_FAKE_CODEX_LOG": self.log,
                "BRIDGE_FAKE_CODEX_EVENTS": "",
                "BRIDGE_FAKE_CODEX_WARNINGS": "",
                "BRIDGE_FAKE_CODEX_EXIT": "0",
                "BRIDGE_FAKE_CODEX_ROLLOUT": "",
                "BRIDGE_FAKE_CODEX_MODEL": "",
                "BRIDGE_FAKE_CODEX_EFFORT": "",
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _session(self, name, mode="work"):
        path = os.path.join(self.temp, name)
        record.record(
            path, "session-create", "A disposable Codex dispatch.\n",
            initiator="vibe-coder", peer="codex", project=self.temp, mode=mode,
        )
        return path

    def _stream(self, events, exit_code=0):
        with open(self.events, "w", encoding="utf-8") as stream:
            stream.write("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n")
        os.environ["BRIDGE_FAKE_CODEX_EVENTS"] = self.events
        os.environ["BRIDGE_FAKE_CODEX_EXIT"] = str(exit_code)

    def _body(self, result):
        with open(result.response_path, encoding="utf-8") as stream:
            return stream.read().split("## Body\n\n", 1)[1]

    def test_each_dispatch_uses_codex_current_model_without_an_allowlist_or_override(self):
        session_dir = self._session("models")
        for model in ("gpt-6.1-sol", "future-user-selected-model"):
            with self.subTest(model=model):
                with open(os.path.join(self.codex_home, "config.toml"), "w") as config:
                    config.write('model = "' + model + '"\n')
                result = runner.run_turn(session_dir, "Use the current choice.\n")
                self.assertEqual(self._body(result), model)
        with open(self.log, encoding="utf-8") as stream:
            launches = [json.loads(line) for line in stream]
        self.assertEqual(len(launches), 2)
        for launch in launches:
            self.assertNotIn("--model", launch["argv"])
            self.assertNotIn("-m", launch["argv"])
            self.assertNotIn("--ignore-user-config", launch["argv"])
            self.assertFalse(any(arg.startswith("model=") for arg in launch["argv"]))
            self.assertEqual(launch["body"], "Use the current choice.\n")

    def test_readiness_identifies_the_resolved_installation_without_claiming_acceptance(self):
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            status = cli.main(["check", "--peer", "codex", "--mode", "work", "--json"])
        checked = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertTrue(checked["ready"])
        self.assertEqual(checked["executable"], os.path.realpath(self.program))
        self.assertEqual(checked["version"], "0.155.1")
        self.assertIs(checked["selected_model_verified"], False)
        self.assertFalse(os.path.exists(self.log))
        for mode in ("work", "review"):
            with self.subTest(mode=mode):
                result = codex.check(peer.Deadline(30.0), self.temp, mode=mode)
                self.assertIn("codex transport is ready", result.message)
                self.assertIn("model acceptance is unverified", result.message)
                self.assertIn(os.path.realpath(self.program), result.message)
                self.assertIn("0.155.1", result.message)
                self.assertIs(result.selected_model_verified, False)

    def test_both_modes_publish_only_complete_final_text_despite_warning_floods(self):
        final = "The final answer — café.\u0085Next line.\u2028Line separator.\u2029Paragraph separator.\n" + "Full answer content. " * 200
        self._stream([
            {"type": "thread.started", "thread_id": "thread-fixture"},
            {"type": "turn.started"},
            {"type": "error", "message": "Reconnecting after a recoverable stream error"},
            {"type": "future.progress", "text": "future output is not the answer"},
            {"type": "item.completed", "item": {
                "id": "commentary", "type": "agent_message", "text": "Intermediate commentary"}},
            {"type": "item.completed", "item": {
                "id": "tool", "type": "command_execution", "aggregated_output": "TOOL OUTPUT"}},
            {"type": "item.completed", "item": {
                "id": "future", "type": "future_item", "text": "UNKNOWN ITEM OUTPUT"}},
            {"type": "item.completed", "item": {
                "id": "final", "type": "agent_message", "text": final}},
            {"type": "turn.completed", "usage": {"input_tokens": 1}},
            {"type": "future.summary", "detail": "future terminal addition"},
        ])
        os.environ["BRIDGE_FAKE_CODEX_WARNINGS"] = "yes"
        for mode in ("work", "review"):
            with self.subTest(mode=mode):
                result = runner.run_turn(self._session("success-" + mode, mode), "Answer once.\n")
                self.assertEqual(self._body(result), final)
        os.environ["BRIDGE_FAKE_CODEX_EXIT"] = "1"
        for mode in ("work", "review"):
            with self.subTest(mode=mode, exit_code=1):
                session_dir = self._session("nonzero-" + mode, mode)
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(session_dir, "Answer once.\n")
                self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
                self.assertIn("exit 1", caught.exception.detail)
                self.assertEqual(os.listdir(session.messages_dir(session_dir)), ["0001-initiator-to-peer.md"])

    def test_actual_rejection_survives_warning_floods_without_replay_or_private_echoes(self):
        body = "PRIVATE REQUEST café\nSECOND PRIVATE LINE\n"
        refusal = (
            "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account. "
            + "\u0085Next line.\u2028Line separator.\u2029Paragraph separator. "
            + "Vendor explanation. " * 80 + "ACTIONABLE END"
        )
        message = refusal + "\n" + body + "api_key=opaque-private-key\n"
        self._stream([
            {"type": "thread.started", "thread_id": "thread-fixture"},
            {"type": "error", "message": message},
            {"type": "turn.failed", "error": {"message": message}},
        ], exit_code=1)
        os.environ["BRIDGE_FAKE_CODEX_WARNINGS"] = "yes"
        for mode in ("work", "review"):
            with self.subTest(mode=mode):
                events = []
                session_dir = self._session("rejected-" + mode, mode)
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(session_dir, body, event_writer=lambda line: events.append(json.loads(line)))
                detail = caught.exception.detail
                self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
                decoded_detail, _ = json.JSONDecoder().raw_decode(
                    detail.split("structured stdout failure: ", 1)[1]
                )
                self.assertIn(refusal, decoded_detail)
                self.assertIn("request body redacted", detail)
                self.assertIn("credential line redacted", detail)
                for private in ("PRIVATE REQUEST", "SECOND PRIVATE LINE", "opaque-private-key"):
                    self.assertNotIn(private, detail)
                    self.assertNotIn(private, events[-1]["reason"])
                self.assertEqual(events[-1]["outcome"], "failure")
                decoded_event_reason, _ = json.JSONDecoder().raw_decode(
                    events[-1]["reason"].split("structured stdout failure: ", 1)[1]
                )
                self.assertIn(refusal, decoded_event_reason)
                self.assertEqual(os.listdir(session.messages_dir(session_dir)), ["0001-initiator-to-peer.md"])
        with open(self.log, encoding="utf-8") as stream:
            self.assertEqual(len(stream.readlines()), 2)

    def test_zero_exit_still_refuses_failed_or_unfinished_turns(self):
        answer = {"type": "item.completed", "item": {
            "id": "answer", "type": "agent_message", "text": "Unpublishable candidate"}}
        completed = {"type": "turn.completed"}
        streams = {
            "partial": [{"type": "item.updated", "item": {
                "id": "answer", "type": "agent_message", "text": "Partial text"}}],
            "unfinished": [answer],
            "unfinished-after-commentary": [answer, {"type": "item.started", "item": {
                "id": "later", "type": "agent_message", "text": "Partial later message"}}, completed],
            "tool-only": [{"type": "item.completed", "item": {
                "id": "tool", "type": "command_execution", "aggregated_output": "Tool output"}}, completed],
            "failed": [answer, {"type": "turn.failed", "error": {"message": "Actual failure"}}],
            "error-unfinished": [answer, {"type": "error", "message": "Actual failure"}],
            "duplicate-completion": [answer, completed, completed],
            "duplicate-start": [{"type": "turn.started"}, {"type": "turn.started"}, answer, completed],
            "late-start": [answer, {"type": "turn.started"}, completed],
            "answer-after-completion": [completed, answer],
            "empty-final": [answer, {"type": "item.completed", "item": {
                "id": "empty", "type": "agent_message", "text": " "}}, completed],
        }
        for name, stream in streams.items():
            with self.subTest(stream=name):
                self._stream(stream)
                session_dir = self._session(name)
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(session_dir, "Do not invent success.\n")
                self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
                self.assertEqual(os.listdir(session.messages_dir(session_dir)), ["0001-initiator-to-peer.md"])

    def test_zero_exit_failure_reason_is_complete_and_redacted_after_json_decode(self):
        body = "PRIVATE REQUEST\n秘密の依頼\n"
        reason = "Detailed failure. " * 100 + "\u0085Next line.\u2028Line separator.\u2029Paragraph separator. REASON END"
        message = reason + "\n" + body + "Authorization: Bearer opaque-private-key\n"
        streams = {
            "failed": [{"type": "turn.failed", "error": {"message": message}}],
            "unfinished": [{"type": "error", "message": message}],
            "no-answer": [{"type": "error", "message": message}, {"type": "turn.completed"}],
        }
        for name, events in streams.items():
            self._stream(events)
            for mode in ("work", "review"):
                with self.subTest(stream=name, mode=mode):
                    with self.assertRaises(BridgeError) as caught:
                        runner.run_turn(self._session("decoded-error-" + name + "-" + mode, mode), body)
                    detail = json.loads(caught.exception.detail)
                    self.assertIn(reason, detail)
                    self.assertIn("request body redacted", detail)
                    self.assertIn("credential line redacted", detail)
                    for private in ("PRIVATE REQUEST", "秘密の依頼", "opaque-private-key"):
                        self.assertNotIn(private, detail)

    def test_malformed_stream_does_not_publish_a_candidate_answer(self):
        self._stream([
            {"type": "item.completed", "item": {
                "id": "final", "type": "agent_message", "text": "Candidate answer"}},
            {"type": "turn.completed"},
        ])
        with open(self.events, "a", encoding="utf-8") as stream:
            stream.write("not JSON: PRIVATE REQUEST\n")
        session_dir = self._session("malformed")
        with self.assertRaises(BridgeError) as caught:
            runner.run_turn(session_dir, "PRIVATE REQUEST\n")
        self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
        self.assertNotIn("PRIVATE REQUEST", caught.exception.detail)
        self.assertEqual(os.listdir(session.messages_dir(session_dir)), ["0001-initiator-to-peer.md"])

    def test_the_saved_reply_reports_requested_and_recorded_model_and_effort(self):
        """A named model and effort, and Codex's own session file, in one reply.

        The fake codex receives the caller's named choices on `-m` and one
        `-c model_reasoning_effort=` override exactly as named, emits
        `thread.started` with the thread id, and writes the rollout session
        file whose `turn_context` names the model and effort Codex actually
        recorded. The saved reply must show the requested values and the
        recorded ones beside each other, and the recorded halves must come
        from the session file alone: they differ from the request here, and
        neither half of the report is inferred from it. The choices report
        rides in the reply of either session format - Format 2 is the
        original courier shape, still the default - and the Format 2 reply
        grows nothing beyond it: no `Answers:` line, and no attribution
        lines a choices report does not carry.
        """
        rollout = os.path.join(
            self.codex_home, "sessions", "2026", "10", "19",
            "rollout-2026-10-19T09-00-00-thread-choices.jsonl",
        )
        os.environ["BRIDGE_FAKE_CODEX_ROLLOUT"] = rollout
        os.environ["BRIDGE_FAKE_CODEX_MODEL"] = "recorded-model"
        os.environ["BRIDGE_FAKE_CODEX_EFFORT"] = "recorded-effort"
        self._stream([
            {"type": "thread.started", "thread_id": "thread-choices"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {
                "id": "final", "type": "agent_message",
                "text": "The reported answer.\n"}},
            {"type": "turn.completed"},
        ])
        for name, mode in (("format-three", "work"), ("format-two", None)):
            with self.subTest(session=name):
                result = runner.run_turn(
                    self._session("choices-report-" + name, mode),
                    "Name one model and one effort.\n",
                    model="requested-model",
                    effort="requested-effort",
                )
                with open(result.response_path, encoding="utf-8") as stream:
                    reply = stream.read()
                header = reply.partition("\n## Body")[0]
                self.assertIn("Model: recorded-model\n", header)
                self.assertIn("Effort: recorded-effort\n", header)
                self.assertIn("Requested-Model: requested-model\n", header)
                self.assertIn("Requested-Effort: requested-effort\n", header)
                self.assertNotIn("\nModel: requested-model\n", header)
                self.assertNotIn("\nEffort: requested-effort\n", header)
                self.assertEqual(self._body(result), "The reported answer.\n")
                if mode is None:
                    self.assertEqual(
                        reply,
                        "# Message 0002\nFrom: codex\nTo: vibe-coder\n"
                        "Model: recorded-model\nEffort: recorded-effort\n"
                        "Requested-Model: requested-model\n"
                        "Requested-Effort: requested-effort\n"
                        "\n## Body\n\nThe reported answer.\n",
                    )

        # Each turn launched codex once, with the named choices on the
        # target's own switches exactly as named.
        with open(self.log, encoding="utf-8") as stream:
            launches = [json.loads(line) for line in stream]
        self.assertEqual(len(launches), 2)
        for launch in launches:
            pairs = list(zip(launch["argv"], launch["argv"][1:]))
            self.assertIn(("-m", "requested-model"), pairs)
            self.assertIn(
                ("-c", "model_reasoning_effort=requested-effort"), pairs
            )


class OrdinaryPermissionWorkVectors(unittest.TestCase):
    """The four work vectors, composed against their real connector source."""

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-work-vectors-")
        # A hermetic codex home, so the posture the work builder resolves is
        # the fixture's and never this machine's real configuration.
        self.codex_home = os.path.join(self.temp, "codex-home")
        os.makedirs(self.codex_home)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _codex_config(self, text=None):
        """Write an optional fixture config and patch CODEX_HOME to it."""
        if text is not None:
            with open(
                os.path.join(self.codex_home, "config.toml"),
                "w",
                encoding="utf-8",
            ) as stream:
                stream.write(text)
        return mock.patch.dict(os.environ, {"CODEX_HOME": self.codex_home})

    def test_codex_work_preserves_the_configured_sandbox_posture(self):
        prerequisite = (
            "/fake/codex",
            "0.147.0",
            "Darwin 26 arm64",
            (codex._work_warning("workspace-write", "fixture"),),
        )
        with self._codex_config('sandbox_mode = "workspace-write"\n'):
            with mock.patch.object(
                codex, "_prerequisites", return_value=prerequisite
            ):
                command = codex.build_work_command(
                    peer.Deadline(60.0),
                    self.temp,
                    access_paths=("/access/one",),
                    attachments=("/img/a.png", "/img/b.png"),
                )
        self.assertEqual(
            command.argv,
            (
                "/fake/codex",
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--cd",
                self.temp,
                "--add-dir",
                "/access/one",
                "--image",
                "/img/a.png",
                "--image",
                "/img/b.png",
                "-",
            ),
        )
        self.assertIsNone(command.body_argument)
        joined = " ".join(command.argv)
        self.assertNotIn("--ignore-user-config", joined)
        self.assertNotIn("--disable", joined)
        self.assertNotIn("--sandbox", joined)
        warning = " ".join(command.warnings)
        self.assertIn("sandbox switch is passed", warning)
        self.assertIn("workspace-write", warning)

    def test_codex_work_proceeds_with_a_warning_under_an_apparent_read_only_posture(self):
        """A partial posture reading never rejects a configured work route.

        Both origins of an apparent read-only posture - the user's own
        configuration naming read-only, and codex's out-of-box default when
        nothing configures a wider sandbox - proceed the same way: no
        sandbox switch, a warning that names the posture as apparent rather
        than effective, and codex's own run-time enforcement left to govern,
        because layers this connector does not read can make the effective
        posture writable. A call the enforced posture cannot serve fails
        inside codex with codex's own error, which the runner surfaces with
        its reason and next action after the request is published.
        """
        postures = {
            "configured read-only": 'sandbox_mode = "read-only"\n',
            "codex default read-only": "# no sandbox_mode configured\n",
        }
        for name, config in postures.items():
            with self.subTest(origin=name):
                with self._codex_config(config):
                    apparent, origin = codex._apparent_sandbox()
                    self.assertEqual(apparent, "read-only")
                    self.assertTrue(origin)
                    prerequisite = (
                        "/fake/codex",
                        "0.147.0",
                        "Darwin 26 arm64",
                        (codex._work_warning(apparent, origin),),
                    )
                    with mock.patch.object(
                        codex, "_prerequisites", return_value=prerequisite
                    ):
                        command = codex.build_work_command(
                            peer.Deadline(60.0), self.temp
                        )
                self.assertEqual(
                    command.argv,
                    (
                        "/fake/codex",
                        "exec",
                        "--json",
                        "--skip-git-repo-check",
                        "--cd",
                        self.temp,
                        "-",
                    ),
                )
                self.assertNotIn("--sandbox", command.argv)
                warning = " ".join(command.warnings)
                self.assertIn("apparent posture", warning)
                self.assertIn("read-only", warning)
                self.assertIn(
                    "codex itself resolves and enforces", warning
                )
                self.assertIn("trusted project", warning)
                self.assertIn("managed_config.toml", warning)
                self.assertIn("fails inside codex", warning)

        # The same posture through a real work session: the call is not
        # refused before publication. It runs under no posture flag, and a
        # genuinely read-only effective posture fails inside codex with
        # codex's own error, surfaced with its reason and next action, while
        # the record keeps the request that was published.
        refusing = os.path.join(self.temp, "codex-refuses-headless-work")
        with open(refusing, "w", encoding="utf-8") as stream:
            stream.write(
                "#!/bin/sh\n"
                "echo 'codex exec: the sandbox is read-only; work that "
                "writes cannot run headlessly' >&2\n"
                "exit 1\n"
            )
        os.chmod(refusing, 0o755)
        session_dir = os.path.join(self.temp, "apparent-readonly-session")
        record.record(
            session_dir,
            "session-create",
            "A work session under an apparent read-only posture.\n",
            initiator="vibe-coder",
            peer="codex",
            project=self.temp,
            mode="work",
        )
        with self._codex_config('sandbox_mode = "read-only"\n'):
            with mock.patch.object(
                codex,
                "_prerequisites",
                return_value=(
                    refusing,
                    "0.147.0",
                    "Darwin 26 arm64",
                    (codex._work_warning("read-only", "fixture"),),
                ),
            ):
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(session_dir, "Please work.\n", 30.0)
        self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
        self.assertIn("sandbox is read-only", caught.exception.detail)
        self.assertIn("Next action:", str(caught.exception))
        self.assertEqual(
            os.listdir(session.messages_dir(session_dir)),
            ["0001-initiator-to-peer.md"],
        )

    def test_claude_work_carries_no_review_only_restriction_or_flag(self):
        prerequisite = (
            "/fake/claude",
            "2.1.251",
            "Darwin 26 arm64",
            "signed in through claude.ai",
            (claude.WORK_WARNING,),
        )
        with mock.patch.object(claude, "_prerequisites", return_value=prerequisite):
            command = claude.build_work_command(
                peer.Deadline(60.0), self.temp, access_paths=("/access/docs",)
            )
        self.assertEqual(
            command.argv,
            ("/fake/claude", "--print", "--output-format", "text",
             "--add-dir", "/access/docs"),
        )
        joined = " ".join(command.argv)
        for review_only in (
            "--restricted",
            "--strict-mcp-config",
            "--tools",
            "--permission-mode",
        ):
            self.assertNotIn(review_only, joined)
        with mock.patch.object(claude, "_prerequisites", return_value=prerequisite):
            with_image = claude.build_work_command(
                peer.Deadline(60.0), self.temp, attachments=("/img/a.png",)
            )
        self.assertTrue(
            any("file-reading tool" in warning for warning in with_image.warnings)
        )

    def test_zcode_work_is_edit_mode_with_its_real_tool_set(self):
        prerequisite = (
            "/fake/node /fake/zcode.cjs",
            "0.16.5",
            "Darwin 26 arm64",
            "minimum local configuration is present",
            (zcode.WORK_WARNING,),
        )
        with mock.patch.object(zcode, "_program", return_value=("/n", "/z")), mock.patch.object(
            zcode, "_environment", return_value=()
        ):
            with mock.patch.object(
                zcode, "_prerequisites", return_value=prerequisite
            ):
                command = zcode.build_work_command(
                    peer.Deadline(60.0),
                    self.temp,
                    attachments=("/img/a.png",),
                )
        self.assertEqual(
            command.argv,
            ("/n", "/z", "--mode", "edit", "--cwd", self.temp,
             "--attach", "/img/a.png", "--output-format", "text"),
        )
        self.assertEqual(command.body_argument, zcode.BODY_ARGUMENT)
        self.assertNotIn("--disallowed-tools", command.argv)
        self.assertIn(zcode.WORK_WARNING, command.warnings)

    def test_minimax_work_drops_the_one_step_assumption_and_attaches_files(self):
        prerequisite = (
            "/fake/mcode",
            "0.2.7",
            "Darwin 26 arm64",
            "authentication not confirmed",
            (minimax.WORK_WARNING,),
        )
        with mock.patch.object(minimax, "_prerequisites", return_value=prerequisite):
            command = minimax.build_work_command(
                peer.Deadline(60.0, work_seconds=float("inf")),
                self.temp,
                attachments=("/img/a.png",),
            )
            bounded = minimax.build_work_command(
                peer.Deadline(60.0), self.temp, max_steps=4
            )
        self.assertEqual(
            command.argv,
            (
                "/fake/mcode",
                "exec",
                "--input",
                "-",
                "--input-format",
                "text",
                "--cwd",
                self.temp,
                "--file",
                "/img/a.png",
                "--output-format",
                "text",
            ),
        )
        self.assertNotIn("--max-steps", command.argv)
        self.assertEqual(
            bounded.argv[bounded.argv.index("--max-steps") + 1], "4"
        )
        self.assertNotIn("--permission", " ".join(command.argv))
        self.assertIn(minimax.WORK_WARNING, command.warnings)

    def test_review_vectors_are_unchanged_by_the_work_additions(self):
        codex_prerequisite = ("/fake/codex", "0.147.0", "Darwin 26 arm64", ())
        with mock.patch.object(codex, "_prerequisites", return_value=codex_prerequisite):
            review = codex.build_command(peer.Deadline(60.0), self.temp)
        self.assertIn("read-only", review.argv)
        self.assertIn("--ignore-user-config", review.argv)
        self.assertIn("--json", review.argv)
        self.assertIs(review.response_parser, codex.parse_response)


class CallerNamedModelAndEffortChoices(unittest.TestCase):
    """Optional caller-named model and effort choices, work and review alike.

    A named choice travels only on the selected tool's own documented switch,
    in either vector, and the switch it needs is qualified like any other the
    call uses. A tool whose connector documents no switch refuses the named
    choice before request publication with that plain fact, and Bridge
    chooses, completes, and substitutes nothing - a choice the tool rejects
    at use is the tool's own reported failure. Readiness reports the same
    switches as facts, without a model call.
    """

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-named-choices-")

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _session(self, name, peer_id, mode=None):
        path = os.path.join(self.temp, name)
        record.record(
            path,
            "session-create",
            "A named-choice session.\n",
            initiator="vibe-coder",
            peer=peer_id,
            mode=mode,
        )
        return path

    @staticmethod
    def _pairs(argv):
        return list(zip(argv, argv[1:]))

    def test_each_connector_declares_exactly_the_switches_it_passes(self):
        declared = (
            (codex, "-m", "-c model_reasoning_effort=<effort>"),
            (claude, "--model", "--effort"),
            (minimax, "--model", None),
            (zcode, None, None),
            (hermes, None, None),
            (qwen, None, None),
        )
        for connector, model, effort in declared:
            with self.subTest(peer=connector.HARNESS_ID):
                self.assertEqual(
                    connector.CHOICE_SWITCHES,
                    connectors.ChoiceSwitches(model=model, effort=effort),
                )

    def test_codex_and_claude_pass_named_choices_in_both_vectors(self):
        codex_prerequisite = ("/fake/codex", "0.147.0", "Darwin 26 arm64", ())
        claude_prerequisite = (
            "/fake/claude",
            "2.1.251",
            "Darwin 26 arm64",
            "signed in through claude.ai",
            (claude.WORK_WARNING,),
        )
        named = {"model": "chosen-model", "effort": "xhigh"}
        with mock.patch.object(
            codex, "_prerequisites", return_value=codex_prerequisite
        ) as codex_prereq:
            codex_work = codex.build_work_command(
                peer.Deadline(60.0), self.temp, **named
            )
            codex_review = codex.build_command(
                peer.Deadline(60.0), self.temp, **named
            )
        with mock.patch.object(
            claude, "_prerequisites", return_value=claude_prerequisite
        ) as claude_prereq:
            claude_work = claude.build_work_command(
                peer.Deadline(60.0), self.temp, **named
            )
            claude_review = claude.build_command(
                peer.Deadline(60.0), self.temp, **named
            )
        for command in (codex_work, codex_review):
            self.assertIn(("-m", "chosen-model"), self._pairs(command.argv))
            self.assertIn(
                ("-c", "model_reasoning_effort=xhigh"), self._pairs(command.argv)
            )
            self.assertNotIn("--model", command.argv)
        for command in (claude_work, claude_review):
            self.assertIn(("--model", "chosen-model"), self._pairs(command.argv))
            self.assertIn(("--effort", "xhigh"), self._pairs(command.argv))
        # The named choices reach the prerequisites, which qualify the
        # switches they travel on, in both modes.
        for prerequisite in (codex_prereq, claude_prereq):
            self.assertEqual(len(prerequisite.call_args_list), 2)
            for call in prerequisite.call_args_list:
                self.assertEqual(call[1]["model"], "chosen-model")
                self.assertEqual(call[1]["effort"], "xhigh")

    def test_minimax_passes_a_named_model_and_documents_no_effort(self):
        prerequisite = (
            "/fake/mcode",
            "0.2.7",
            "Darwin 26 arm64",
            "authentication not confirmed",
            (minimax.WORK_WARNING,),
        )
        with mock.patch.object(minimax, "_prerequisites", return_value=prerequisite):
            work = minimax.build_work_command(
                peer.Deadline(60.0, work_seconds=float("inf")),
                self.temp,
                model="minimax/MiniMax-M2.5",
            )
            review = minimax.build_command(
                peer.Deadline(60.0), self.temp, model="minimax/MiniMax-M2.5"
            )
        for command in (work, review):
            self.assertIn(
                ("--model", "minimax/MiniMax-M2.5"), self._pairs(command.argv)
            )
        self.assertIsNone(minimax.CHOICE_SWITCHES.effort)

    def test_a_named_choice_switch_is_qualified_against_the_installed_help(self):
        def probe_from(help_text):
            def probe(argv, cwd, deadline, env=None):
                argv = tuple(argv)
                if argv[-1] == "--version":
                    return CompletedCall(0, "codex-cli 0.147.0\n", "")
                if argv[1:] == ("login", "status"):
                    return CompletedCall(0, "", "Logged in using ChatGPT")
                return CompletedCall(0, help_text, "")

            return probe

        help_without = (
            "Usage: codex exec\n  --skip-git-repo-check\n  --cd <DIR>\n"
            "  --json\n  -c, --config <key=value>\n"
        )
        with mock.patch.object(
            codex.connectors, "executable", return_value="/fake/codex"
        ), mock.patch.object(
            codex.connectors, "qualified_platform", return_value="Darwin 26 arm64"
        ), mock.patch.object(
            codex.connectors, "probe", side_effect=probe_from(help_without)
        ), mock.patch.dict(os.environ, {"CODEX_HOME": self.temp}):
            with self.assertRaises(BridgeError) as caught:
                codex.build_work_command(
                    peer.Deadline(60.0), self.temp, model="chosen-model"
                )
        self.assertEqual(caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE)
        self.assertIn("-m", caught.exception.detail)

        help_with = help_without + "  -m, --model <MODEL>\n"
        with mock.patch.object(
            codex.connectors, "executable", return_value="/fake/codex"
        ), mock.patch.object(
            codex.connectors, "qualified_platform", return_value="Darwin 26 arm64"
        ), mock.patch.object(
            codex.connectors, "probe", side_effect=probe_from(help_with)
        ), mock.patch.dict(os.environ, {"CODEX_HOME": self.temp}):
            command = codex.build_work_command(
                peer.Deadline(60.0), self.temp,
                model="chosen-model", effort="xhigh",
            )
        self.assertIn(("-m", "chosen-model"), self._pairs(command.argv))
        self.assertIn(
            ("-c", "model_reasoning_effort=xhigh"), self._pairs(command.argv)
        )

    def test_targets_without_a_documented_switch_refuse_named_choices(self):
        refusals = (
            ("zcode", {"model": "chosen-model"}, None),
            ("zcode", {"effort": "high"}, None),
            ("zcode", {"model": "chosen-model"}, "work"),
            ("hermes", {"model": "chosen-model"}, None),
            ("qwen", {"effort": "high"}, None),
            ("minimax", {"effort": "high"}, None),
        )
        for index, (peer_id, named, mode) in enumerate(refusals):
            with self.subTest(peer=peer_id, named=sorted(named), mode=mode):
                session_dir = self._session(
                    "refuse-{0}".format(index), peer_id, mode=mode
                )
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(
                        session_dir, "Please answer.\n", 30.0, **named
                    )
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                self.assertIn(peer_id, caught.exception.detail)
                self.assertIn("no switch for naming", caught.exception.detail)
                self.assertIn("chooses and substitutes none", caught.exception.detail)
                self.assertEqual(
                    os.listdir(session.messages_dir(session_dir)), []
                )

    def test_named_choice_values_are_validated_before_publication(self):
        session_dir = self._session("codex-values", "codex")
        invalid = (
            ("--model", ""),
            ("--model", " spaced "),
            ("--effort", "two\nlines"),
        )
        for option, value in invalid:
            with self.subTest(option=option, value=value):
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(
                        session_dir,
                        "Please answer.\n",
                        30.0,
                        **{option[2:]: value}
                    )
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                self.assertIn(option, caught.exception.detail)
                self.assertEqual(
                    os.listdir(session.messages_dir(session_dir)), []
                )

    def test_a_review_turn_hands_the_named_choices_to_the_review_builder(self):
        observed = []

        class ReviewConnector:
            CHOICE_SWITCHES = claude.CHOICE_SWITCHES

            @staticmethod
            def build_command(deadline, cwd, model=None, effort=None):
                observed.append((model, effort))
                return PeerCommand(
                    argv=(sys.executable, FAKE_PEER, "plain"),
                    cwd=cwd,
                    env=tuple(os.environ.items()),
                )

        session_dir = self._session("claude-review", "claude")
        with mock.patch.object(
            runner.connectors, "resolve", return_value=ReviewConnector
        ):
            result = runner.run_turn(
                session_dir,
                "Please answer.\n",
                30.0,
                model="chosen-model",
                effort="high",
            )
        self.assertEqual(observed, [("chosen-model", "high")])
        self.assertTrue(result.response_path.endswith("0002-peer-to-initiator.md"))

    def test_a_rejected_named_model_is_a_failure_never_a_substitution(self):
        launches = []

        class NamedChoiceConnector:
            CAPABILITIES = connectors.Capabilities(
                work="supported",
                work_detail="fake",
                image="supported",
                image_detail="fake",
            )
            CHOICE_SWITCHES = codex.CHOICE_SWITCHES

            @staticmethod
            def build_work_command(
                deadline, cwd, access_paths=(), attachments=(), **kwargs
            ):
                launches.append(kwargs.get("model"))
                return PeerCommand(
                    argv=(sys.executable, FAKE_PEER, "fail"),
                    cwd=cwd,
                    env=tuple(os.environ.items()),
                )

        session_dir = self._session("codex-rejected", "codex", mode="work")
        with mock.patch.object(
            runner.connectors, "resolve", return_value=NamedChoiceConnector
        ):
            with self.assertRaises(BridgeError) as caught:
                runner.run_turn(
                    session_dir, "Please answer.\n", 30.0, model="chosen-model"
                )
        self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
        self.assertIn("deliberate failure", caught.exception.detail)
        # One launch, on the named choice: no fallback model, no retry.
        self.assertEqual(launches, ["chosen-model"])
        self.assertEqual(
            os.listdir(session.messages_dir(session_dir)),
            ["0001-initiator-to-peer.md"],
        )

    def test_readiness_reports_the_documented_choice_switches(self):
        class ChoiceConnector:
            CAPABILITIES = connectors.Capabilities(
                work="supported",
                work_detail="fake",
                image="supported",
                image_detail="fake",
            )
            CHOICE_SWITCHES = codex.CHOICE_SWITCHES

            @staticmethod
            def check(deadline, cwd, mode="review"):
                return connectors.CheckResult("confirmed")

        output = io.StringIO()
        with mock.patch.object(
            cli.connectors, "resolve", return_value=ChoiceConnector
        ):
            with mock.patch("sys.stdout", output):
                status = cli.main(["check", "--peer", "codex", "--json"])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["model_switch"], "-m")
        self.assertEqual(
            result["effort_switch"], "-c model_reasoning_effort=<effort>"
        )

        class NoChoiceConnector(ChoiceConnector):
            CHOICE_SWITCHES = connectors.ChoiceSwitches()

        output = io.StringIO()
        with mock.patch.object(
            cli.connectors, "resolve", return_value=NoChoiceConnector
        ):
            with mock.patch("sys.stdout", output):
                status = cli.main(["check", "--peer", "codex", "--json"])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        # Absence is the fact that a named choice is refused for this tool.
        self.assertNotIn("model_switch", result)
        self.assertNotIn("effort_switch", result)

    def test_the_run_command_line_forwards_named_choices(self):
        turn = mock.Mock(response_path="/response.md")
        stdout = io.StringIO()
        with mock.patch.object(
            cli.runner, "run_turn", return_value=turn
        ) as called, mock.patch(
            "sys.stdout", stdout
        ), mock.patch("sys.stdin", io.StringIO("Please answer.\n")):
            self.assertEqual(
                cli.main(
                    [
                        "run",
                        "--session",
                        "/session",
                        "--model",
                        "chosen-model",
                        "--effort",
                        "xhigh",
                        "--work-permission",
                        "full-access",
                    ]
                ),
                0,
            )
        self.assertEqual(stdout.getvalue(), "/response.md\n")
        called.assert_called_once_with(
            session_dir="/session",
            body="Please answer.\n",
            wait_seconds=None,
            no_timeout=False,
            warning_writer=mock.ANY,
            max_steps=None,
            required_model=None,
            model="chosen-model",
            effort="xhigh",
            work_permission="full-access",
            attachments=(),
            note_ref=None,
            purpose=None,
            event_writer=None,
        )


class WorkModeCheckQualifications(unittest.TestCase):
    """A work-mode check qualifies the work vector, never the review vector.

    Each check below drives one connector's real prerequisite code with a
    probe stand-in. The help text the stand-in returns names only the work
    vector's own switches - a program that had dropped every review-only
    switch - and the work-mode check must succeed on it, while the review
    check on the same program must still refuse. Authentication and the
    executable, version, and platform facts stay prerequisites in both modes.
    """

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-work-checks-")

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    @staticmethod
    def _probed(fake, recording):
        def probe(argv, cwd, deadline, env=None):
            argv = tuple(argv)
            recording.append(argv)
            return fake(argv)

        return probe

    def test_claude_work_check_skips_the_review_gates_and_policy_facts(self):
        auth = (
            '{"loggedIn": true, "authMethod": "claude.ai", '
            '"apiProvider": "firstParty"}'
        )
        work_help = (
            "Usage: claude\n  -p, --print\n  --output-format <format>\n"
            "  --add-dir <directories...>\n"
        )
        probed = []

        def fake(argv):
            if argv[-1] == "--version":
                return CompletedCall(0, "claude 2.1.251\n", "")
            if argv[-1] == "--json":
                return CompletedCall(0, auth, "")
            return CompletedCall(0, work_help, "")

        with mock.patch.object(
            claude.connectors, "executable", return_value="/fake/claude"
        ), mock.patch.object(
            claude.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            claude.connectors, "probe", side_effect=self._probed(fake, probed)
        ):
            checked = claude.check(peer.Deadline(30.0), self.temp, mode="work")
            command = claude.build_work_command(peer.Deadline(60.0), self.temp)

        # No doctor question and no policy fact: the boundary they describe
        # is the review call's, and work mode discards both readings.
        self.assertEqual(
            probed[:3],
            [
                ("/fake/claude", "--version"),
                ("/fake/claude", "auth", "status", "--json"),
                ("/fake/claude", "--help"),
            ],
        )
        self.assertNotIn("/fake/claude doctor", " ".join(map(" ".join, probed)))
        self.assertEqual(checked.warnings, (claude.WORK_WARNING,))
        self.assertEqual(
            command.argv, ("/fake/claude", "--print", "--output-format", "text")
        )

        # The help names no review switch, and the review check refuses it.
        with mock.patch.object(
            claude.connectors, "executable", return_value="/fake/claude"
        ), mock.patch.object(
            claude.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            claude.connectors, "probe", side_effect=self._probed(fake, [])
        ):
            with self.assertRaises(BridgeError) as caught:
                claude.check(peer.Deadline(30.0), self.temp)
        self.assertEqual(caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE)

        # --add-dir is a prerequisite only when an access directory exists.
        no_add_dir = work_help.replace("  --add-dir <directories...>\n", "")

        def fake_without(argv):
            if argv[-1] == "--version":
                return CompletedCall(0, "claude 2.1.251\n", "")
            if argv[-1] == "--json":
                return CompletedCall(0, auth, "")
            return CompletedCall(0, no_add_dir, "")

        with mock.patch.object(
            claude.connectors, "executable", return_value="/fake/claude"
        ), mock.patch.object(
            claude.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            claude.connectors, "probe", side_effect=self._probed(fake_without, [])
        ):
            claude.build_work_command(peer.Deadline(60.0), self.temp)
            with self.assertRaises(BridgeError) as caught:
                claude.build_work_command(
                    peer.Deadline(60.0), self.temp, access_paths=("/access/docs",)
                )
        self.assertEqual(caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE)

    def test_codex_work_check_qualifies_the_work_switches_only(self):
        work_help = (
            "Usage: codex exec\n  --skip-git-repo-check\n  --cd <DIR>\n  --json\n"
        )
        probed = []

        def fake(argv):
            if argv[-1] == "--version":
                return CompletedCall(0, "codex-cli 0.147.0\n", "")
            if tuple(argv)[1:] == ("login", "status"):
                return CompletedCall(0, "", "Logged in using ChatGPT")
            return CompletedCall(0, work_help, "")

        with mock.patch.object(
            codex.connectors, "executable", return_value="/fake/codex"
        ), mock.patch.object(
            codex.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            codex.connectors, "probe", side_effect=self._probed(fake, probed)
        ), mock.patch.dict(os.environ, {"CODEX_HOME": self.temp}):
            checked = codex.check(peer.Deadline(30.0), self.temp, mode="work")
            codex.build_work_command(peer.Deadline(60.0), self.temp)

        # The exec subcommand is proved by the help probe that carries the
        # switches; the login status is still a prerequisite; the help names
        # no review-only switch and the work check proceeds on it.
        self.assertEqual(
            probed[:3],
            [
                ("/fake/codex", "--version"),
                ("/fake/codex", "login", "status"),
                ("/fake/codex", "exec", "--help"),
            ],
        )
        self.assertEqual(len(checked.warnings), 1)
        self.assertIn("apparent", checked.warnings[0])

        with mock.patch.object(
            codex.connectors, "executable", return_value="/fake/codex"
        ), mock.patch.object(
            codex.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            codex.connectors, "probe", side_effect=self._probed(fake, [])
        ):
            with self.assertRaises(BridgeError) as caught:
                codex.check(peer.Deadline(30.0), self.temp)
        self.assertEqual(caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE)

        # A refused sign-in still stops a work turn before anything starts.
        def signed_out(argv):
            if argv[-1] == "--version":
                return CompletedCall(0, "codex-cli 0.147.0\n", "")
            return CompletedCall(1, "", "not logged in")

        with mock.patch.object(
            codex.connectors, "executable", return_value="/fake/codex"
        ), mock.patch.object(
            codex.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            codex.connectors, "probe", side_effect=self._probed(signed_out, [])
        ):
            with self.assertRaises(BridgeError) as caught:
                codex.check(peer.Deadline(30.0), self.temp, mode="work")
        self.assertEqual(caught.exception.failure, Failure.AUTHENTICATION_REQUIRED)

        # --add-dir and --image are prerequisites only when they travel.
        def fake_without(argv):
            if argv[-1] == "--version":
                return CompletedCall(0, "codex-cli 0.147.0\n", "")
            if tuple(argv)[1:] == ("login", "status"):
                return CompletedCall(0, "", "Logged in using ChatGPT")
            return CompletedCall(0, work_help, "")

        with mock.patch.object(
            codex.connectors, "executable", return_value="/fake/codex"
        ), mock.patch.object(
            codex.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            codex.connectors, "probe",
            side_effect=self._probed(fake_without, []),
        ), mock.patch.dict(os.environ, {"CODEX_HOME": self.temp}):
            codex.build_work_command(peer.Deadline(60.0), self.temp)
            for requested in ({"access_paths": ("/access",)},
                              {"attachments": ("/img/a.png",)}):
                with self.subTest(requested=sorted(requested)):
                    with self.assertRaises(BridgeError) as caught:
                        codex.build_work_command(
                            peer.Deadline(60.0), self.temp, **requested
                        )
                    self.assertEqual(
                        caught.exception.failure,
                        Failure.RESTRICTIONS_UNAVAILABLE,
                    )

    def test_minimax_work_check_requires_no_review_permission_switch(self):
        work_help = (
            "Usage: mcode exec\n  --input <text>\n  --input-format <format>\n"
            "  --cwd <dir>\n  --output-format <format>\n  --timeout <ms>\n"
            "  --file <path>\n  --max-steps <n>\n"
        )
        probed = []

        def fake(argv):
            if argv[-1] == "--version":
                return CompletedCall(0, "mcode 0.2.7\n", "")
            return CompletedCall(0, work_help, "")

        with mock.patch.object(
            minimax.connectors, "executable", return_value="/fake/mcode"
        ), mock.patch.object(
            minimax.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            minimax.connectors, "probe", side_effect=self._probed(fake, probed)
        ):
            checked = minimax.check(peer.Deadline(30.0), self.temp, mode="work")
            command = minimax.build_work_command(
                peer.Deadline(30.0), self.temp, attachments=("/img/a.png",)
            )

        # Two probes only, no provider probe: authentication stays honestly
        # unconfirmed, and the help names no --permission switch.
        self.assertEqual(
            probed[:2],
            [("/fake/mcode", "--version"), ("/fake/mcode", "exec", "--help")],
        )
        self.assertTrue(
            all("provider" not in " ".join(call) for call in probed)
        )
        self.assertIn(minimax.WORK_WARNING, checked.warnings)
        self.assertTrue(
            any("unconfirmed" in warning for warning in checked.warnings)
        )
        self.assertIn(("--file", "/img/a.png"), tuple(zip(command.argv, command.argv[1:])))

        # The review check still requires the review set, --permission
        # included, from the same switchless help.
        with mock.patch.object(
            minimax.connectors, "executable", return_value="/fake/mcode"
        ), mock.patch.object(
            minimax.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            minimax.connectors, "probe", side_effect=self._probed(fake, [])
        ):
            with self.assertRaises(BridgeError) as caught:
                minimax.check(peer.Deadline(30.0), self.temp)
        self.assertEqual(caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE)

        # The optional switches are prerequisites only when the vector
        # emits them; the native timeout stops being one when the deadline
        # is past what the program's own timer domain accepts.
        optional_free = work_help.replace("  --file <path>\n", "").replace(
            "  --max-steps <n>\n", ""
        )

        def fake_without(argv):
            if argv[-1] == "--version":
                return CompletedCall(0, "mcode 0.2.7\n", "")
            return CompletedCall(0, optional_free, "")

        with mock.patch.object(
            minimax.connectors, "executable", return_value="/fake/mcode"
        ), mock.patch.object(
            minimax.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            minimax.connectors, "probe", side_effect=self._probed(fake_without, [])
        ):
            minimax.build_work_command(peer.Deadline(30.0), self.temp)
            for requested in ({"attachments": ("/img/a.png",)},
                              {"max_steps": 4}):
                with self.subTest(requested=sorted(requested)):
                    with self.assertRaises(BridgeError) as caught:
                        minimax.build_work_command(
                            peer.Deadline(30.0), self.temp, **requested
                        )
                    self.assertEqual(
                        caught.exception.failure,
                        Failure.RESTRICTIONS_UNAVAILABLE,
                    )

            no_timeout = optional_free.replace("  --timeout <ms>\n", "")

            def fake_untimed(argv):
                if argv[-1] == "--version":
                    return CompletedCall(0, "mcode 0.2.7\n", "")
                return CompletedCall(0, no_timeout, "")

            with mock.patch.object(
                minimax.connectors, "probe",
                side_effect=self._probed(fake_untimed, []),
            ):
                astronomical = minimax.build_work_command(
                    peer.Deadline(1e308), self.temp
                )
            self.assertNotIn("--timeout", astronomical.argv)

    def test_zcode_full_access_permission_is_qualified_and_reported_without_execution(self):
        probed = []

        def fake(argv):
            if argv[2] in ("--version", "version"):
                return CompletedCall(0, "zcode 0.16.5\n", "")
            if argv[2] == "plugins":
                return CompletedCall(0, "[]", "")
            return CompletedCall(0, "--prompt <text>\n--mode <mode>\n--cwd <path>\n", "")

        with mock.patch.object(zcode, "_environment", return_value=()), \
                mock.patch.object(zcode, "_program", return_value=("/fake/node", "/fake/zcode.cjs")), \
                mock.patch.object(zcode.connectors, "qualified_platform", return_value="Darwin 26 arm64"), \
                mock.patch.object(zcode, "_sign_in_facts", return_value="sign-in observed"), \
                mock.patch.object(zcode.connectors, "probe", side_effect=self._probed(fake, probed)):
            stdout = io.StringIO()
            with mock.patch("sys.stdout", stdout):
                status = cli.main(["check", "--peer", "zcode", "--mode", "work",
                                   "--work-permission", "full-access", "--json"])
            command = zcode.build_work_command(peer.Deadline(30), self.temp,
                                               work_permission="full-access")
        self.assertEqual(status, 0)
        facts = json.loads(stdout.getvalue())
        self.assertEqual(facts["effective_permission"], "full-access")
        self.assertTrue(facts["headless_shell"])
        self.assertFalse(facts["shell_execution_verified"])
        self.assertFalse(facts["selected_model_verified"])
        self.assertEqual(facts["authentication"], "unknown")
        self.assertIn("under your account", facts["warnings"][0].replace("operating-system ", ""))
        self.assertEqual(command.argv, ("/fake/node", "/fake/zcode.cjs", "--mode", "yolo",
                                       "--cwd", self.temp, "--output-format", "text"))
        self.assertIn(zcode.FULL_ACCESS_WARNING, command.warnings)
        for call in (call for call in probed if call[2] == "version"):
            self.assertEqual(call[3:5], ("--mode", "yolo"))
            self.assertNotIn("--disallowed-tools", call)

        def rejects_full_access(argv):
            if argv[2] == "version" and "yolo" in argv:
                return CompletedCall(1, "", "unsupported mode yolo")
            return fake(argv)

        with mock.patch.object(zcode, "_environment", return_value=()), \
                mock.patch.object(zcode, "_program", return_value=("/fake/node", "/fake/zcode.cjs")), \
                mock.patch.object(zcode.connectors, "qualified_platform", return_value="Darwin 26 arm64"), \
                mock.patch.object(zcode, "_sign_in_facts", return_value="sign-in observed"), \
                mock.patch.object(zcode.connectors, "probe", side_effect=self._probed(rejects_full_access, [])):
            ordinary = zcode.check(peer.Deadline(30), self.temp, mode="work")
            self.assertEqual(ordinary.effective_permission, "edit")
            self.assertFalse(ordinary.headless_shell)
            with self.assertRaises(BridgeError) as caught:
                zcode.check(peer.Deadline(30), self.temp, mode="work", work_permission="full-access")
            self.assertEqual(caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE)

        for target, mode in (("codex", "work"), ("zcode", "review")):
            with mock.patch.object(cli.connectors, "resolve") as resolve, \
                    mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(cli.main(["check", "--peer", target, "--mode", mode,
                                           "--work-permission", "full-access"]), 1)
                resolve.assert_not_called()

    def test_zcode_work_check_drops_the_review_deny_list_requirement(self):
        help_text = (
            "  -p, --prompt <text>\n  --mode <mode>\n  --cwd <path>\n"
            "  --attach <path>\n  --disallowed-tools <tools...>\n"
        )
        probed = []

        def fake(argv):
            if argv[2] == "--version":
                return CompletedCall(0, "zcode 0.16.5\n", "")
            if argv[2] == "plugins":
                return CompletedCall(0, "[]", "")
            if argv[2] == "version":
                return CompletedCall(0, "zcode 0.16.5\n", "")
            return CompletedCall(0, help_text, "")

        with mock.patch.object(zcode, "_environment", return_value=()), mock.patch.object(
            zcode, "_program", return_value=("/fake/node", "/fake/zcode.cjs")
        ), mock.patch.object(
            zcode.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            zcode, "_sign_in_facts", return_value="sign-in observed"
        ), mock.patch.object(
            zcode.connectors, "probe", side_effect=self._probed(fake, probed)
        ):
            checked = zcode.check(peer.Deadline(30.0), self.temp, mode="work")
            command = zcode.build_work_command(peer.Deadline(60.0), self.temp)

        # The no-model parser check passes the work switches and no
        # --disallowed-tools, because the work vector never carries one.
        parser_probes = [call for call in probed if call[2] == "version"]
        self.assertEqual(len(parser_probes), 2)
        for parser_probe in parser_probes:
            self.assertEqual(
                parser_probe,
                (
                    "/fake/node", "/fake/zcode.cjs", "version",
                    "--mode", "edit", "--cwd", self.temp,
                    "--output-format", "text",
                ),
            )
        self.assertIn(zcode.WORK_WARNING, checked.warnings)
        self.assertEqual(
            command.argv,
            (
                "/fake/node", "/fake/zcode.cjs",
                "--mode", "edit", "--cwd", self.temp,
                "--output-format", "text",
            ),
        )

        # A help text without the review deny-list switch serves a work turn
        # and refuses a review one; the review parser check still carries
        # the deny switch the review vector passes.
        no_deny_list = help_text.replace(
            "  --disallowed-tools <tools...>\n", ""
        )

        def fake_without(argv):
            if argv[2] == "--version":
                return CompletedCall(0, "zcode 0.16.5\n", "")
            if argv[2] == "plugins":
                return CompletedCall(0, "[]", "")
            if argv[2] == "version":
                return CompletedCall(0, "zcode 0.16.5\n", "")
            return CompletedCall(0, no_deny_list, "")

        with mock.patch.object(zcode, "_environment", return_value=()), mock.patch.object(
            zcode, "_program", return_value=("/fake/node", "/fake/zcode.cjs")
        ), mock.patch.object(
            zcode.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            zcode, "_sign_in_facts", return_value="sign-in observed"
        ), mock.patch.object(
            zcode.connectors, "probe", side_effect=self._probed(fake_without, [])
        ):
            zcode.check(peer.Deadline(30.0), self.temp, mode="work")
            with self.assertRaises(BridgeError) as caught:
                zcode.check(peer.Deadline(30.0), self.temp)
        self.assertEqual(caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE)

        review_probes = []
        with mock.patch.object(zcode, "_environment", return_value=()), mock.patch.object(
            zcode, "_program", return_value=("/fake/node", "/fake/zcode.cjs")
        ), mock.patch.object(
            zcode.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            zcode, "_sign_in_facts", return_value="sign-in observed"
        ), mock.patch.object(
            zcode.connectors, "probe",
            side_effect=self._probed(fake, review_probes),
        ):
            zcode.check(peer.Deadline(30.0), self.temp)
        self.assertEqual(
            [call for call in review_probes if call[2] == "version"],
            [
                (
                    "/fake/node", "/fake/zcode.cjs", "version",
                    "--disallowed-tools", "Edit", "--mode", "plan",
                    "--cwd", self.temp, "--output-format", "text",
                )
            ],
        )

        # --attach is a prerequisite only when an attachment travels.
        no_attach = help_text.replace("  --attach <path>\n", "")

        def fake_unattached(argv):
            if argv[2] == "--version":
                return CompletedCall(0, "zcode 0.16.5\n", "")
            if argv[2] == "plugins":
                return CompletedCall(0, "[]", "")
            if argv[2] == "version":
                return CompletedCall(0, "zcode 0.16.5\n", "")
            return CompletedCall(0, no_attach, "")

        with mock.patch.object(zcode, "_environment", return_value=()), mock.patch.object(
            zcode, "_program", return_value=("/fake/node", "/fake/zcode.cjs")
        ), mock.patch.object(
            zcode.connectors, "qualified_platform",
            return_value="Darwin 26 arm64",
        ), mock.patch.object(
            zcode, "_sign_in_facts", return_value="sign-in observed"
        ), mock.patch.object(
            zcode.connectors, "probe",
            side_effect=self._probed(fake_unattached, []),
        ):
            zcode.build_work_command(peer.Deadline(60.0), self.temp)
            with self.assertRaises(BridgeError) as caught:
                zcode.build_work_command(
                    peer.Deadline(60.0), self.temp, attachments=("/img/a.png",)
                )
        self.assertEqual(caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE)

    def test_unsupported_work_needs_no_switch_qualification(self):
        """Hermes and Qwen: work is reported unsupported from source-grounded
        reasons, and no review switch is a prerequisite of that report."""
        for connector, peer_id in ((hermes, "hermes"), (qwen, "qwen")):
            with self.subTest(peer=peer_id):
                probed = []

                def fake(argv):
                    argv = tuple(argv)
                    if argv[-1] == "--version":
                        version = {
                            "hermes": "hermes 0.18.2\n",
                            "qwen": "qwen 0.23.0\n",
                        }[peer_id]
                        return CompletedCall(0, version, "")
                    if peer_id == "hermes":
                        return CompletedCall(
                            0,
                            "logged in\nusing Nous as inference provider\n",
                            "",
                        )
                    return CompletedCall(0, "", "")

                with mock.patch.object(
                    connector.connectors, "executable",
                    return_value="/fake/{0}".format(peer_id),
                ), mock.patch.object(
                    connector.connectors, "qualified_platform",
                    return_value="Darwin 26 arm64",
                ), mock.patch.object(
                    connector.connectors, "probe",
                    side_effect=self._probed(fake, probed),
                ):
                    checked = connector.check(
                        peer.Deadline(30.0), self.temp, mode="work"
                    )
                    # The work-mode check asked its version and
                    # authentication questions and no switch question at
                    # all; its sentence claims no switch verification
                    # nobody performed.
                    expected = [("/fake/{0}".format(peer_id), "--version")]
                    if peer_id == "hermes":
                        expected.append(("/fake/hermes", "portal", "info"))
                    self.assertEqual(probed, expected)
                    self.assertIn("{0} ".format(peer_id), checked.message)
                    self.assertNotIn("fixed-vector switch", checked.message)

                    # The same switchless program still fails a review
                    # check, which alone qualifies the review switches.
                    with self.assertRaises(BridgeError) as caught:
                        connector.check(peer.Deadline(30.0), self.temp)
                self.assertEqual(
                    caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE
                )
                self.assertEqual(
                    probed[-1], ("/fake/{0}".format(peer_id), "--help")
                )


class WindowsLockingTwins(unittest.TestCase):
    """The msvcrt lock twin: the same one-holder contract, simulated.

    No Windows machine is part of this repository's qualification, so the
    Windows branch of the session lock is driven by standing in for the
    `msvcrt` module itself, with the lock file, the descriptors, and the
    contention all real. The POSIX twin is checked on this platform directly,
    and the cross-process flock behavior it keeps is covered by the
    compatibility selection's own contention checks.
    """

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-work-winlock-")

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    class _FakeMsvcrt(object):
        """A stand-in for the Windows lock module.

        The real `msvcrt.locking` refuses a second `LK_NBLCK` on the same
        byte range and refuses to unlock a range the caller does not hold;
        the stand-in does both, keyed on the file the descriptor names, and
        records every call's mode, length, and the position it was made at.
        """

        LK_NBLCK = 2
        LK_UNLCK = 0

        def __init__(self):
            self.locked = set()
            self.calls = []

        def locking(self, fd, mode, nbytes):
            status = os.fstat(fd)
            key = (status.st_dev, status.st_ino)
            self.calls.append(
                (fd, mode, nbytes, os.lseek(fd, 0, os.SEEK_CUR))
            )
            if mode == self.LK_NBLCK:
                if key in self.locked:
                    raise OSError(13, "Permission denied")
                self.locked.add(key)
            elif mode == self.LK_UNLCK:
                if key not in self.locked:
                    raise OSError(22, "unlock of an unheld region")
                self.locked.discard(key)

    def test_the_windows_twin_locks_unlocks_and_refuses_a_second_holder(self):
        fake = self._FakeMsvcrt()
        with mock.patch.object(locking, "msvcrt", fake):
            with locking.session_lock(self.temp) as path:
                self.assertEqual(path, locking.lock_path(self.temp))
                self.assertEqual(len(fake.locked), 1)
                # A second acquirer is refused at once, and nothing is ever
                # written into the lock file.
                with self.assertRaises(BridgeError) as caught:
                    with locking.session_lock(self.temp):
                        self.fail("the lock was granted twice")
                self.assertEqual(caught.exception.failure, Failure.BUSY_SESSION)
                with open(path, "rb") as stream:
                    self.assertEqual(stream.read(), b"")
            # The holder's exit gives the lock back.
            self.assertEqual(fake.locked, set())

        # Every call locked or unlocked one byte, at the start of the file:
        # the take, the refused second take, the refused second take's stray
        # release, and the holder's release.
        self.assertEqual(
            [(mode, nbytes, position) for _fd, mode, nbytes, position in fake.calls],
            [
                (fake.LK_NBLCK, 1, 0),
                (fake.LK_NBLCK, 1, 0),
                (fake.LK_UNLCK, 1, 0),
                (fake.LK_UNLCK, 1, 0),
            ],
        )

    def test_the_posix_twin_is_flock_and_its_release_is_the_close(self):
        with mock.patch.object(locking, "msvcrt", None):
            handle = os.open(
                locking.lock_path(self.temp), os.O_RDWR | os.O_CREAT, 0o600
            )
            second = os.open(locking.lock_path(self.temp), os.O_RDWR)
            try:
                # Closing the descriptor has always been the release on
                # POSIX, so the release twin does nothing there.
                self.assertIsNone(locking._release(handle))
                locking._acquire(handle)
                with self.assertRaises(OSError):
                    locking._acquire(second)
            finally:
                os.close(handle)
                os.close(second)


class WindowsProcessCleanup(unittest.TestCase):
    """The taskkill and job cleanup branch, simulated: the owned tree ends.

    No Windows machine is part of this repository's qualification, so the
    Windows branch of process cleanup is driven by patching the platform
    switch and standing in for `taskkill /T /F` with a function that
    force-terminates the same processes the real command's own tree walk
    would reach, and for the kill-on-close job with a release that
    terminates the same members the kernel's close would. Everything else in
    each check is real: the spawn, the deadline, the signal delivery, the
    reaping, and the processes that must be gone afterwards - including the
    one case where the root exits before cleanup and the tree walk has
    nothing to walk from. The Unix branch of the same cleanup is the
    compatibility selection's own orphan and stop checks.
    """

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-work-winclean-")

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _child_pids(self, pid_path):
        """The descendant pids the fixture reported, read from its file."""
        with open(pid_path, encoding="utf-8") as stream:
            return [
                int(number)
                for kind, number in PID_LINE.findall(stream.read())
                if kind == "CHILD"
            ]

    def _tree_killer(self, pid_path):
        """A stand-in for `taskkill /T /F` on one root pid.

        The real command walks the descendant tree and force-terminates each
        process it names; the stand-in terminates the root and the descendant
        the fixture reported, which is the same set, and answers the
        emptiness question the same way: nothing found means already gone.
        """

        def taskkill(pgid):
            pids = [pgid] + self._child_pids(pid_path)
            found = False
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    found = True
                except ProcessLookupError:
                    pass
            return not found

        return taskkill

    def _walk_from_root_killer(self, pid_path):
        """A stand-in for `taskkill /T /F` faithful to where the walk starts.

        The real command's `/T` walk begins at the root pid: a root that has
        already exited turns the whole command into one not-found report that
        names no descendant and empties nothing, and a live root names the
        whole tree. The stand-in does exactly that and nothing more, which is
        what makes it the right simulation for the parent-exits-first case:
        it cannot reach an orphan even by accident.
        """

        def taskkill(pgid):
            try:
                os.kill(pgid, signal.SIGKILL)
            except ProcessLookupError:
                return True
            for pid in self._child_pids(pid_path):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            return False

        return taskkill

    def _owned_job(self, pid_path, order=None):
        """Stand-ins for the kill-on-close job's assignment and release.

        The real release is the kernel's: closing the last handle to the job
        terminates every member, root or descendant, alive at the close. The
        stand-in terminates the same set the tree killer would, which is what
        makes it faithful to the one case the correction is about - the root
        already gone and a descendant still running.

        The assignment stand-in also stands in for the suspension discipline
        when an order list is given: it records itself before the resume, so
        a check can assert the assignment happened first.
        """

        def own_tree(process):
            if order is not None:
                order.append(("own", process.pid))
            return ("job", process.pid)

        def release(job):
            if order is not None:
                order.append(("release", job[1]))
            for pid in [job[1]] + self._child_pids(pid_path):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        return own_tree, release

    def _windows_creation(self, record):
        """A stand-in for the Windows creation, recording its flags.

        The real creation passes `CREATE_NEW_PROCESS_GROUP` and
        `CREATE_SUSPENDED` to `CreateProcess`; the POSIX `Popen` has no
        meaning for creation flags and refuses a nonzero value outright, so
        the stand-in records exactly what the Windows branch asked to create
        with and hands the rest to the ordinary POSIX spawn. The record is
        what the ordering checks assert: the suspended bit is the one that
        makes assignment-before-resume sufficient on Windows, because a
        child created with it has not run one instruction of its own.
        """
        real_popen = subprocess.Popen

        def windows_spawn(argv, **kwargs):
            record.append(("spawn", kwargs.get("creationflags", 0)))
            kwargs.pop("creationflags", None)
            return real_popen(argv, **kwargs)

        return windows_spawn

    def _resumed_root(self, order=None, pid_path=None):
        """A stand-in for the suspended root's resume, recording its place.

        The real resume runs the primary thread through `ResumeThread`; the
        stand-in does nothing but record that the step ran, so a check can
        assert it ran after the job assignment and - when a fixture pid file
        is named - that the fixture had still done nothing at that point,
        because its input is only supplied after this step returns.
        """

        def resume_root(process):
            if order is not None:
                order.append(("resume", process.pid))
            if pid_path is not None:
                if os.path.exists(pid_path):
                    raise AssertionError(
                        "the fixture had already acted before the resume"
                    )

        return resume_root

    def test_a_windows_timeout_terminates_the_whole_owned_tree(self):
        pid_path = os.path.join(self.temp, "timeout-pids.txt")
        own_tree, release = self._owned_job(pid_path)
        creation = []
        with mock.patch.object(peer, "WINDOWS", True), mock.patch.object(
            peer, "CLEANUP_GRACE_SECONDS", 0.2
        ), mock.patch.object(
            peer, "ESCALATION_GRACE_SECONDS", 0.5
        ), mock.patch.object(
            peer.subprocess, "Popen", self._windows_creation(creation)
        ), mock.patch.object(
            peer, "_windows_taskkill", side_effect=self._tree_killer(pid_path)
        ), mock.patch.object(
            peer, "_windows_own_tree", side_effect=own_tree
        ), mock.patch.object(
            peer, "_windows_resume_root", side_effect=self._resumed_root()
        ), mock.patch.object(
            peer, "_windows_release_job", side_effect=release
        ):
            with self.assertRaises(peer.PeerTimeout) as caught:
                peer.run_bounded(
                    argv=(
                        sys.executable, FAKE_PEER,
                        "write-pids-then-hang", pid_path,
                    ),
                    cwd=self.temp,
                    env=tuple(os.environ.items()),
                    stdin_text="Start something and then stop answering.\n",
                    deadline=peer.Deadline(0.8),
                )
        pids = _await_reported_pids(pid_path)
        self.assertEqual(caught.exception.pid, pids["PEER"])
        self.assertTrue(
            _wait_until_gone(pids["PEER"]),
            "the peer is still there after the Windows cleanup",
        )
        self.assertTrue(
            _wait_until_gone(pids["CHILD"]),
            "the process the peer started is still there",
        )

    def test_a_windows_stop_terminates_the_whole_owned_tree(self):
        pid_path = os.path.join(self.temp, "stop-pids.txt")
        driver = subprocess.Popen(
            (
                sys.executable, "-c", WINDOWS_SIGNAL_DRIVER,
                REPO_ROOT, FAKE_PEER, "write-pids-then-hang", pid_path,
            ),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def end_driver():
            driver.stdin.close()
            if driver.poll() is None:
                driver.kill()
            driver.wait(timeout=10.0)
            driver.stdout.close()
            driver.stderr.close()

        self.addCleanup(end_driver)
        pids = _await_reported_pids(pid_path)

        os.kill(driver.pid, signal.SIGTERM)
        driver.wait(timeout=10.0)
        self.assertEqual(driver.returncode, 3, driver.stderr.read())
        self.assertTrue(
            _wait_until_gone(pids["PEER"]),
            "the peer is still there after the Windows stop",
        )
        self.assertTrue(
            _wait_until_gone(pids["CHILD"]),
            "the process the peer started is still there after the stop",
        )

    def test_a_windows_root_that_exits_first_still_ends_its_descendants(self):
        """The parent-exits-first case: the root is gone before cleanup, a
        descendant is alive, and the corrected ownership still ends it.

        A tree walk that starts at the root can confirm nothing once the root
        has exited - `taskkill`'s not-found report names no descendant - so
        the ownership is the kill-on-close job, whose release terminates
        every member, root or no root. The two stand-ins are faithful to
        exactly that split: this taskkill walks only from a live root and
        kills nothing when the root is gone, so the release is the only
        thing that can reach the orphan, and the run must finish clean.

        The descendant here is the immediate kind: the root starts it as
        its first action, the shape that escapes an ownership established
        only after the child is already running. The correction's answer
        to that shape is asserted directly - the creation carried
        `CREATE_SUSPENDED` beside the process-group flag, the job was
        assigned before the resume, and the fixture had still done nothing
        at the resume - because that is what makes an immediate descendant
        a member from its creation rather than the first one a race could
        miss.
        """
        pid_path = os.path.join(self.temp, "orphan-pids.txt")
        order = []
        creation = []
        own_tree, release = self._owned_job(pid_path, order)
        walk = self._walk_from_root_killer(pid_path)

        def taskkill(pgid):
            order.append(("taskkill", pgid))
            return walk(pgid)

        with mock.patch.object(peer, "WINDOWS", True), mock.patch.object(
            peer, "_CREATE_NEW_PROCESS_GROUP", 0x200
        ), mock.patch.object(
            peer, "CLEANUP_GRACE_SECONDS", 0.2
        ), mock.patch.object(
            peer, "ESCALATION_GRACE_SECONDS", 0.5
        ), mock.patch.object(
            peer.subprocess, "Popen", self._windows_creation(creation)
        ), mock.patch.object(
            peer, "_windows_taskkill", side_effect=taskkill
        ), mock.patch.object(
            peer, "_windows_own_tree", side_effect=own_tree
        ), mock.patch.object(
            peer, "_windows_resume_root",
            side_effect=self._resumed_root(order, pid_path),
        ), mock.patch.object(
            peer, "_windows_release_job", side_effect=release
        ):
            completed = peer.run_bounded(
                argv=(
                    sys.executable, FAKE_PEER,
                    "write-pids-then-exit", pid_path,
                ),
                cwd=self.temp,
                env=tuple(os.environ.items()),
                stdin_text="Start something, leave it behind, and go.\n",
                deadline=peer.Deadline(30.0),
            )
        pids = _await_reported_pids(pid_path)
        self.assertEqual(completed.returncode, 0)
        # The creation asked for the new process group and the suspended
        # start together: 0x200 beside 0x4, the pair that leaves the child
        # running nothing until the code above resumes it.
        self.assertEqual(
            creation,
            [("spawn", 0x200 | peer._CREATE_SUSPENDED)],
            "the Windows creation must start the child suspended",
        )
        # The job was assigned to the still-suspended root, the root was
        # resumed only afterwards - so the descendant the root started with
        # its first action began inside a job that already owned it - and
        # the job was closed before any confirming walk, which is the order
        # that makes a not-found report an honest emptiness answer for the
        # descendants the walk never reached.
        self.assertEqual(
            order,
            [
                ("own", pids["PEER"]),
                ("resume", pids["PEER"]),
                ("release", pids["PEER"]),
                ("taskkill", pids["PEER"]),
            ],
        )
        self.assertTrue(
            _wait_until_gone(pids["PEER"]),
            "the peer is still there after the Windows cleanup",
        )
        self.assertTrue(
            _wait_until_gone(pids["CHILD"]),
            "the descendant that outlived its root is still there",
        )

    def test_a_windows_spawn_refused_ownership_ends_what_it_started(self):
        """A platform that refuses the kill-on-close job does not get to run.

        The refusal is the answer rather than a degraded run: what was just
        started has never run - it was created suspended and the assignment
        came before the resume - so it is terminated outright, with nothing
        yet beside it for a tree walk to be needed for, and no cleanup runs
        afterwards because the responsibility has already been taken.
        """
        order = []

        def refusing_own_tree(process):
            order.append(("own", process.pid))
            raise BridgeError(
                Failure.CLEANUP_FAILURE,
                detail="the platform refused the kill-on-close job",
            )

        def end_root(process):
            order.append(("end", process.pid))
            process.kill()

        def release(job):
            order.append(("release", job))

        def taskkill(pgid):
            order.append(("taskkill", pgid))
            return False

        with mock.patch.object(peer, "WINDOWS", True), mock.patch.object(
            peer.subprocess, "Popen", self._windows_creation([])
        ), mock.patch.object(
            peer, "_windows_own_tree", side_effect=refusing_own_tree
        ), mock.patch.object(
            peer, "_windows_resume_root", side_effect=self._resumed_root()
        ), mock.patch.object(
            peer, "_windows_end_root", side_effect=end_root
        ), mock.patch.object(
            peer, "_windows_release_job", side_effect=release
        ), mock.patch.object(
            peer, "_windows_taskkill", side_effect=taskkill
        ):
            with self.assertRaises(BridgeError) as caught:
                peer.run_bounded(
                    argv=(sys.executable, FAKE_PEER, "hang"),
                    cwd=self.temp,
                    env=tuple(os.environ.items()),
                    stdin_text="Start something and then stop answering.\n",
                    deadline=peer.Deadline(30.0),
                )
        self.assertEqual(caught.exception.failure, Failure.CLEANUP_FAILURE)
        self.assertIn(
            "refused the kill-on-close job", caught.exception.detail
        )
        # The refused run ended what it had started outright - the child
        # was still suspended, so it had run nothing and there was nothing
        # beside it to walk - and no job was ever released because none was
        # taken, and no tree walk or group cleanup ran afterwards because
        # the responsibility had already been taken.
        root = order[0][1]
        self.assertEqual(
            order,
            [("own", root), ("end", root)],
            "the never-run program must be terminated outright on refusal",
        )
        self.assertTrue(
            _wait_until_gone(root),
            "the refused program is still running",
        )

    def test_a_windows_resume_refusal_releases_the_job_and_ends_the_child(self):
        """A platform that resumes the suspended root but is refused does
        not get to run either.

        The job has been taken by then, so the refusal path has more to put
        back than the assignment refusal: the never-run child is ended
        outright, the job that was taken is released - which is itself the
        kernel's termination of every member, none of which can have run -
        and the refusal is the answer, with no group cleanup afterwards
        because the responsibility has already been taken.
        """
        order = []

        def own_tree(process):
            order.append(("own", process.pid))
            return ("job", process.pid)

        def refusing_resume(process):
            order.append(("resume", process.pid))
            raise BridgeError(
                Failure.CLEANUP_FAILURE,
                detail="the platform refused to resume the suspended root",
            )

        def end_root(process):
            order.append(("end", process.pid))
            process.kill()

        def release(job):
            order.append(("release", job[1]))

        def taskkill(pgid):
            order.append(("taskkill", pgid))
            return False

        with mock.patch.object(peer, "WINDOWS", True), mock.patch.object(
            peer.subprocess, "Popen", self._windows_creation([])
        ), mock.patch.object(
            peer, "_windows_own_tree", side_effect=own_tree
        ), mock.patch.object(
            peer, "_windows_resume_root", side_effect=refusing_resume
        ), mock.patch.object(
            peer, "_windows_end_root", side_effect=end_root
        ), mock.patch.object(
            peer, "_windows_release_job", side_effect=release
        ), mock.patch.object(
            peer, "_windows_taskkill", side_effect=taskkill
        ):
            with self.assertRaises(BridgeError) as caught:
                peer.run_bounded(
                    argv=(sys.executable, FAKE_PEER, "hang"),
                    cwd=self.temp,
                    env=tuple(os.environ.items()),
                    stdin_text="Start something and then stop answering.\n",
                    deadline=peer.Deadline(30.0),
                )
        self.assertEqual(caught.exception.failure, Failure.CLEANUP_FAILURE)
        self.assertIn(
            "refused to resume the suspended root", caught.exception.detail
        )
        # The job was taken, the resume was refused, the never-run child
        # was ended outright, and the taken job was released - in that
        # order - and nothing ran afterwards: no tree walk, no group
        # cleanup, because the responsibility had already been taken.
        root = order[0][1]
        self.assertEqual(
            order,
            [
                ("own", root),
                ("resume", root),
                ("end", root),
                ("release", root),
            ],
            "the resume refusal must end the child and release the job",
        )
        self.assertTrue(
            _wait_until_gone(root),
            "the refused program is still running",
        )

    def test_the_kill_on_close_job_structure_matches_the_api_layout(self):
        """The ctypes declaration is the layout `SetInformationJobObject`
        expects, measured rather than trusted, carrying the one limit flag
        and nothing else. The measurement runs on this platform because a
        byte layout is a fact about the declaration, not the machine; the
        only platform-dependent part is the pointer width, which the Windows
        build shares with the one running here.
        """
        information = peer._windows_job_information()
        self.assertEqual(
            information.BasicLimitInformation.LimitFlags,
            peer._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        )
        counters = type(information.IoInfo)
        self.assertEqual(
            ctypes.sizeof(counters), 48, "IO_COUNTERS is six quadwords"
        )
        # JOBOBJECT_BASIC_LIMIT_INFORMATION: two quadwords, a dword, two
        # pointer-sized fields, a dword, a pointer-sized affinity, and two
        # dwords, with the platform's own alignment.
        pointer = ctypes.sizeof(ctypes.c_void_p)
        expected_basic = 64 if pointer == 8 else 48
        self.assertEqual(
            ctypes.sizeof(information.BasicLimitInformation), expected_basic
        )
        # The extended structure is the basic one, the counters exactly
        # where the basic one ends, and four pointer-sized limits.
        self.assertEqual(
            ctypes.sizeof(information),
            expected_basic + 48 + 4 * pointer,
        )
        self.assertEqual(type(information).IoInfo.offset, expected_basic)

    def test_the_toolhelp_thread_entry_structure_matches_the_api_layout(self):
        """The ctypes declaration is the layout `Thread32First` and
        `Thread32Next` fill, measured rather than trusted: seven dword
        fields and nothing else, so no padding and no platform-dependent
        part at all. The two fields the resume path reads are where the
        API puts them.
        """
        entry = peer._windows_thread_entry()
        self.assertEqual(
            ctypes.sizeof(entry), 28, "THREADENTRY32 is seven dwords"
        )
        self.assertEqual(entry.dwSize, 0)
        names = [name for name, _ in type(entry)._fields_]
        self.assertEqual(
            names,
            [
                "dwSize",
                "cntUsage",
                "th32ThreadID",
                "th32OwnerProcessID",
                "tpBasePri",
                "tpDeltaPri",
                "dwFlags",
            ],
        )
        self.assertEqual(type(entry).th32ThreadID.offset, 8)
        self.assertEqual(type(entry).th32OwnerProcessID.offset, 12)

    class _WindowsSignals(object):
        """A stand-in for the Windows signal module.

        Windows CPython defines no `SIGKILL`, so neither does the stand-in:
        any code on the Windows path that names `signal.SIGKILL` fails the
        check below exactly the way it would fail on Windows itself, with an
        `AttributeError` raised before any cleanup could run. The signals
        that platform really defines and the Windows branches read are kept,
        with their real Windows values.
        """

        SIGINT = 2
        SIGTERM = 15
        CTRL_C_EVENT = 0
        CTRL_BREAK_EVENT = 1

    def test_the_windows_cleanup_never_names_a_signal_windows_does_not_have(self):
        """The whole Windows cleanup runs against a signal module without
        SIGKILL, so a missing attribute is a failure here rather than one
        discovered only on a Windows machine nobody has.

        The escalation sentinel is re-resolved from the stand-in exactly as
        the module resolves it at import, the escalation phase - the job
        handle's close - is reached with every platform primitive the branch
        touches stood in for, so the check is about the one thing that
        broke: the resolved values, and a code path that never reaches for
        an attribute the platform's own signal module does not define.
        """
        windows = self._WindowsSignals()
        resolved = getattr(windows, "SIGKILL", None)
        self.assertIsNone(
            resolved,
            "the escalation sentinel must resolve to None on a signal "
            "module without SIGKILL",
        )
        process = mock.Mock(pid=4321)
        job = ("job", 4321)
        kills = []
        releases = []
        taskkills = []

        def fake_kill(pid, number):
            kills.append((pid, number))

        def fake_release(handle):
            releases.append(handle)

        def fake_taskkill(pgid):
            taskkills.append(pgid)
            return True

        with mock.patch.object(peer, "WINDOWS", True), mock.patch.object(
            peer, "signal", windows
        ), mock.patch.object(
            peer, "_SIGKILL", resolved
        ), mock.patch.object(
            peer, "_CTRL_BREAK_EVENT", windows.CTRL_BREAK_EVENT
        ), mock.patch.object(
            peer.os, "kill", side_effect=fake_kill
        ), mock.patch.object(
            peer, "_windows_taskkill", side_effect=fake_taskkill
        ), mock.patch.object(
            peer, "_windows_release_job", side_effect=fake_release
        ), mock.patch.object(
            peer, "_reap", lambda waited, grace: None
        ), mock.patch.object(
            peer, "_await_group_gone", side_effect=[True]
        ):
            # Reaching the end at all is the first assertion: naming
            # signal.SIGKILL anywhere on this path raises AttributeError
            # against the stand-in, as it would against the real module.
            peer._cleanup_group(process, 4321, job)

        # The polite phase was the console break alone and the forced phase
        # was the job handle's close: no signal Windows does not have was
        # named, sent, or needed to empty the owned tree.
        self.assertEqual(
            kills, [(4321, windows.CTRL_BREAK_EVENT)]
        )
        self.assertEqual(releases, [job])
        self.assertEqual(taskkills, [])

        # A tree that will not confirm emptiness fails visibly rather than
        # pretending, still without naming any signal Windows lacks.
        with mock.patch.object(peer, "WINDOWS", True), mock.patch.object(
            peer, "signal", windows
        ), mock.patch.object(
            peer, "_SIGKILL", resolved
        ), mock.patch.object(
            peer, "_CTRL_BREAK_EVENT", windows.CTRL_BREAK_EVENT
        ), mock.patch.object(
            peer.os, "kill", side_effect=fake_kill
        ), mock.patch.object(
            peer, "_windows_taskkill", side_effect=fake_taskkill
        ), mock.patch.object(
            peer, "_windows_release_job", side_effect=fake_release
        ), mock.patch.object(
            peer, "_reap", lambda waited, grace: None
        ), mock.patch.object(
            peer, "_await_group_gone", side_effect=[False]
        ):
            with self.assertRaises(BridgeError) as caught:
                peer._cleanup_group(process, 4321, job)
        self.assertEqual(caught.exception.failure, Failure.CLEANUP_FAILURE)

        # Without the job, no missing root is an emptiness answer: the
        # cleanup says it cannot confirm descendants instead of returning a
        # clean verdict on a report that named nothing.
        with mock.patch.object(peer, "WINDOWS", True), mock.patch.object(
            peer, "signal", windows
        ), mock.patch.object(
            peer, "_CTRL_BREAK_EVENT", windows.CTRL_BREAK_EVENT
        ), mock.patch.object(
            peer.os, "kill", side_effect=fake_kill
        ), mock.patch.object(
            peer, "_reap", lambda waited, grace: None
        ):
            with self.assertRaises(BridgeError) as caught:
                peer._cleanup_group(process, 4321, None)
        self.assertEqual(caught.exception.failure, Failure.CLEANUP_FAILURE)
        self.assertIn("kill-on-close job", caught.exception.detail)

    def test_taskkill_is_one_fixed_vector_with_three_honest_answers(self):
        run = mock.Mock(
            return_value=subprocess.CompletedProcess((), 0, b"", b"SUCCESS")
        )
        with mock.patch.object(peer.subprocess, "run", run):
            self.assertFalse(
                peer._windows_taskkill(4321),
                "a terminated tree is not an already-empty one",
            )
        self.assertEqual(
            run.call_args[0][0], ("taskkill", "/T", "/F", "/PID", "4321")
        )

        with mock.patch.object(
            peer.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                (), 128, b"", b'ERROR: The process "4321" not found.'
            ),
        ):
            self.assertTrue(
                peer._windows_taskkill(4321),
                "the not-found report, read after the job's close has "
                "terminated every member, is the emptiness answer",
            )

        with mock.patch.object(
            peer.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                (), 1, b"", b"ERROR: Access is denied."
            ),
        ):
            with self.assertRaises(BridgeError) as caught:
                peer._windows_taskkill(4321)
        self.assertEqual(caught.exception.failure, Failure.CLEANUP_FAILURE)
        self.assertIn("Access is denied", caught.exception.detail)
        self.assertIn("4321", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
