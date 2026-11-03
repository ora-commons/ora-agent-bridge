"""Work records, images, Codex protocol, readiness and simulated Windows behavior.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
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
from tests.test_fake_peer import FAKE_PEER, _fake_connector  # noqa: E402

def result_path(session_dir, sequence):
    return session.message_path(
        session_dir, sequence, session.INITIATOR_TO_PEER_SUFFIX
    )


class WorkModeRecords(unittest.TestCase):
    def test_session_formats_modes_and_access_paths(self):
        with tempfile.TemporaryDirectory() as root:
            for name, mode, fmt in (("legacy", None, "2"),
                                    ("review", "review", "3"), ("work", "work", "3")):
                directory = os.path.join(root, name)
                access = (root,) if mode == "work" else ()
                record.record(directory, "session-create", "Session.\n",
                              initiator="app", peer="codex", mode=mode,
                              project=root, access_paths=access)
                parsed = session.read_session(directory)
                self.assertEqual((parsed.bridge_format, parsed.mode,
                                  parsed.project, parsed.access_paths),
                                 (fmt, mode or "review", root, access))
                self.assertTrue(record.record(directory, "note", "Note.\n")
                                .endswith("0001-initiator-record.md"))
            refused = os.path.join(root, "refused")
            with self.assertRaises(BridgeError) as caught:
                record.record(refused, "session-create", "Body.\n",
                              initiator="app", peer="codex", access_paths=(root,))
            self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
            self.assertFalse(os.path.exists(session.session_file(refused)))


class WorkTurnBehavior(unittest.TestCase):

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
            self.session_dir, "session-create", "Work session.\n",
            initiator="vibe-coder", peer="codex",
            project=self.code, mode="work", access_paths=[self.docs],
        )

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _run(self, body="Do the work.\n", connector=None, **kwargs):
        connector = connector or _fake_connector(work=True)
        with mock.patch.object(
            runner.connectors, "resolve", return_value=connector
        ):
            return runner.run_turn(self.session_dir, body, 30.0, **kwargs)

    def _run_cli(self, arguments, body, connector=None):
        connector = connector or _fake_connector(work=True)
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(
            runner.connectors, "resolve", return_value=connector
        ), mock.patch(
            "sys.stdin", io.StringIO(body)
        ), mock.patch(
            "sys.stdout", output
        ), mock.patch(
            "sys.stderr", errors
        ):
            status = cli.main(arguments)
        return status, output.getvalue(), errors.getvalue()

    def _read(self, path):
        with open(path, encoding="utf-8") as stream:
            return stream.read()

    def test_the_work_round_trip_pairs_request_and_response_records(self):
        result = self._run()
        self.assertEqual(
            self._read(result_path(self.session_dir, 1)),
            "# Message 0001\nFrom: vibe-coder\nTo: codex\n\n## Body\n\n"
            "Do the work.\n",
        )
        self.assertEqual(
            self._read(result.response_path),
            "# Message 0002\nFrom: codex\nTo: vibe-coder\nAnswers: 0001\n\n"
            "## Body\n\nDo the work.\n",
        )

        # The inert metadata is welcome on a Format 3 session, stays above
        # `## Body`, and header-shaped body text stays body text.
        shaped = "Note-Ref: forged\nPurpose: nope\nStill body text.\n"
        with mock.patch.object(
            runner.connectors, "resolve", return_value=_fake_connector(work=True)
        ):
            result = runner.run_turn(
                self.session_dir, shaped, 30.0,
                note_ref="real-ref", purpose="second-draft",
            )
        request = self._read(result_path(self.session_dir, 3))
        header, _, body_text = request.partition("\n## Body\n\n")
        self.assertEqual(header.count("Note-Ref:"), 1)
        self.assertIn("Note-Ref: real-ref\nPurpose: second-draft\n", header)
        self.assertEqual(body_text, shaped)

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
                for identifier, turn in (("accepted", "owned-turn"),
                                         ("stale", "later-turn"),
                                         ("race", "owned-turn")):
                    controls.put({
                        "id": identifier, "thread_id": "owned-thread",
                        "turn_id": turn,
                        "text": "Exact correction \n" + identifier,
                    })

        def build(deadline, cwd, **kwargs):
            return PeerCommand(
                argv=(native,), cwd=cwd, env=tuple(os.environ.items()),
                response_parser=codex.parse_response,
            )

        connector = _fake_connector(work=True)
        connector.build_work_command = staticmethod(build)
        connector.run_work_input = staticmethod(codex.run_work_input)
        with mock.patch.object(
            runner.connectors, "resolve", return_value=connector
        ):
            result = runner.run_turn(
                self.session_dir, "Start work.", work_input=controls,
                event_writer=report,
            )
        outcomes = {
            event["id"]: event["status"]
            for event in events if event["event"] == "work_input"
        }
        self.assertEqual(outcomes,
                         {"accepted": "accepted", "stale": "refused",
                          "race": "refused"})
        saved = []
        for path in sorted(Path(self.session_dir, "messages")
                           .glob("*-initiator-record.md")):
            body = self._read(str(path)).split("## Body\n\n", 1)[1]
            saved.append(json.loads(body.removeprefix("Work-Input-JSON: ").strip()))
        self.assertEqual(
            [value["text"] for value in saved if value["status"] == "accepted"],
            ["Exact correction \naccepted"],
        )
        self.assertIn("Correction received.", self._read(result.response_path))

    def test_no_timeout_waits_for_a_reply_past_a_short_named_budget(self):
        argv = (sys.executable, FAKE_PEER, "delay-echo", "0.15")
        with mock.patch.object(peer, "HEARTBEAT_SECONDS", 0.05):
            status, output, errors = self._run_cli(
                ["run", "--session", self.session_dir, "--no-timeout",
                 "--events-jsonl"],
                "Wait for the answer.\n",
                connector=_fake_connector(argv, work=True),
            )
        self.assertEqual(status, 0, errors)
        events = [json.loads(line) for line in output.splitlines()]
        self.assertEqual([e["event"] for e in events].count("finished"), 1)
        self.assertEqual(events[-1]["outcome"], "success")
        self.assertEqual(
            {e["phase"] for e in events if e["event"] == "heartbeat"},
            {"peer-call"},
        )
        response = self._read(events[-1]["response_path"])
        self.assertEqual(
            response.split("\n## Body\n\n", 1)[1], "Wait for the answer.\n"
        )
        status, output, errors = self._run_cli(
            ["run", "--session", self.session_dir], "Plain call.\n",
            connector=_fake_connector(argv, work=True),
        )
        self.assertEqual(status, 0, errors)
        self.assertEqual(
            self._read(output.strip()).split("\n## Body\n\n", 1)[1], "Plain call.\n"
        )

    def test_a_failed_work_call_keeps_its_records_without_false_success(self):
        connector = _fake_connector((sys.executable, FAKE_PEER, "fail"), work=True)
        status, output, stderr = self._run_cli(
            ["run", "--session", self.session_dir,
             "--events-jsonl", "--note-ref", "note-9"],
            "Please fail.\n",
            connector=connector,
        )
        self.assertEqual(status, 1)
        self.assertIn("deliberate failure", stderr)
        parsed = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(
            [event["event"] for event in parsed], ["started", "finished"]
        )
        self.assertEqual(parsed[-1]["outcome"], "failure")
        self.assertIn(
            "peer harness's program exited with a failure", parsed[-1]["reason"]
        )

        # A broken event pipe can mask the reports, never the truth: the
        # call fails, the request stays, and no success exists anywhere.
        def broken_writer(line):
            raise OSError("broken pipe")

        with self.assertRaises(BridgeError) as caught:
            self._run(
                connector=connector, body="Fail again.\n",
                event_writer=broken_writer,
            )
        self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)


    def test_image_bytes_and_document_directory_reach_the_work_tool(self):
        payload = bytes(range(256)) * 4
        image = Path(self.docs, "image.bin")
        image.write_bytes(payload)
        observed = {}
        def build(deadline, cwd, access_paths=(), attachments=(), **kwargs):
            observed.update(cwd=cwd, access=access_paths, images=attachments)
            script = ("import hashlib,pathlib,sys\n"
                      "data=pathlib.Path(sys.argv[1]).read_bytes()\n"
                      "pathlib.Path(sys.argv[2],'authorized.md').write_text('Done.\\n')\n"
                      "print(hashlib.sha256(data).hexdigest())\n")
            return PeerCommand((sys.executable, "-c", script, attachments[0], access_paths[0]),
                               cwd=cwd, env=tuple(os.environ.items()))
        connector = _fake_connector(work=True)
        connector.build_work_command = staticmethod(build)
        result = self._run(connector=connector, attachments=(str(image),))
        self.assertEqual(observed, dict(cwd=self.code, access=(self.docs,), images=(str(image),)))
        self.assertEqual(Path(self.docs, "authorized.md").read_text(), "Done.\n")
        self.assertEqual(os.listdir(self.code), ["evidence.md"])
        self.assertIn("Attachment: " + str(image), self._read(result_path(self.session_dir, 1)))
        self.assertIn(hashlib.sha256(payload).hexdigest(), self._read(result.response_path))

    def test_bounded_wait_retains_a_neutral_directory_and_honest_evidence(self):
        self.session_dir = os.path.join(self.temp, "neutral")
        record.record(self.session_dir, "session-create", "Neutral work.\n",
                      initiator="app", peer="codex", mode="work")
        observed = []
        def build(deadline, cwd, **kwargs):
            observed.append(cwd)
            return PeerCommand((sys.executable, FAKE_PEER, "noisy-echo", "5"),
                               cwd=cwd, env=tuple(os.environ.items()))
        connector = _fake_connector(work=True)
        connector.build_work_command = staticmethod(build)
        status, output, errors = self._run_cli(
            ["run", "--session", self.session_dir, "--events-jsonl", "--timeout", "0.4"],
            "Private body.\n", connector=connector)
        events = [json.loads(line) for line in output.splitlines()]
        final = events[-1]
        pid, captures = final["peer_pid"], final["capture_paths"]
        try:
            self.assertEqual(status, 1, errors)
            self.assertEqual(final["outcome"], "pending")
            self.assertTrue(final["peer_running"] and final["request_on_disk"])
            self.assertEqual(final["peer_session"], "fake-peer-session-0001")
            self.assertIn("PROGRESS still working", final["stdout_progress"])
            self.assertIn("credential line redacted", final["stderr_progress"])
            self.assertNotIn("opaque-value", output)
            self.assertNotIn("Private body", output)
            self.assertNotIn("response_path", final)
            self.assertEqual(sum(e["event"] == "wait_expired" for e in events), 1)
            self.assertTrue(os.path.isdir(observed[0]))
            self.assertNotEqual(observed[0], self.code)
            self.assertTrue(captures and all(os.path.isfile(f) for f in captures))
            self.assertEqual(os.listdir(session.messages_dir(self.session_dir)),
                             ["0001-initiator-to-peer.md"])
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except ProcessLookupError:
                pass
            for path in captures:
                os.unlink(path)
            shutil.rmtree(observed[0])


class ReportedIdentityAttribution(unittest.TestCase):
    def test_reported_identity_or_absent_fields_in_events_and_records(self):
        with tempfile.TemporaryDirectory() as root:
            result_json = json.dumps(dict(schemaVersion=1, type="exec.result", status="succeeded",
                                          output="Answer.\n", model=dict(providerId="minimax", modelId="MiniMax-M3")))
            for name, mode, reported in (("work", "work", True), ("legacy", None, True),
                                         ("plain", "work", False)):
                directory = os.path.join(root, name)
                record.record(directory, "session-create", "Identity.\n",
                              initiator="app", peer="minimax", mode=mode)
                connector = _fake_connector(work=True)
                def build(deadline, cwd, **kwargs):
                    return PeerCommand((sys.executable, "-c", "print(" + repr(result_json if reported else "Answer.") + ")"),
                                       cwd=cwd, env=tuple(os.environ.items()),
                                       response_parser=(lambda text: minimax.parse_response(text, "minimax/MiniMax-M3")) if reported else None)
                connector.build_command = connector.build_work_command = staticmethod(build)
                events = []
                with mock.patch.object(connectors, "resolve", return_value=connector):
                    result = runner.run_turn(directory, "Body.\n", event_writer=events.append)
                final = json.loads(events[-1])
                reply = Path(result.response_path).read_text()
                self.assertEqual(reply.split("\n## Body\n\n")[1], "Answer.\n")
                self.assertEqual(final.get("model"), "MiniMax-M3" if reported else None)
                self.assertEqual(final.get("provider"), "minimax" if reported else None)
                self.assertEqual("Model:" in reply, reported and mode == "work")
                self.assertEqual("Provider:" in reply, reported and mode == "work")
            with self.assertRaises(BridgeError):
                minimax.parse_response(result_json, "minimax/other")


class ReadinessAndSupport(unittest.TestCase):
    def test_json_readiness_ready_missing_signed_out_and_unsupported(self):
        cases = (
            (None, True, False, "supported", "unknown", "unknown"),
            (None, True, True, "supported", "confirmed", "supported"),
            (Failure.AUTHENTICATION_REQUIRED, True, False, "supported", "required", None),
            (Failure.MISSING_CLI, False, False, "supported", None, None),
            (None, True, True, "unsupported", "confirmed", "unsupported"),
        )
        for failure, present, confirmed, work, auth, compatibility in cases:
            with self.subTest(failure=failure, work=work):
                connector = _fake_connector(work=True)
                connector.CAPABILITIES = connectors.Capabilities(work, "terminal route", "unknown", "unverified")
                connector.CHOICE_SWITCHES = codex.CHOICE_SWITCHES
                facts = connectors.CheckResult("ready", ("boundary warning",), executable="/fake/peer",
                                               version="9.9.9", platform="Darwin 26 arm64",
                                               authentication_confirmed=confirmed, qualified=True if confirmed else None)
                connector.check = mock.Mock(side_effect=BridgeError(failure, detail="fixture reason") if failure else None,
                                            return_value=facts)
                output = io.StringIO()
                with mock.patch.object(connectors, "resolve", return_value=connector), mock.patch("sys.stdout", output):
                    status = cli.main(["check", "--peer", "codex", "--mode", "work", "--json"])
                result = json.loads(output.getvalue())
                self.assertEqual((status, result["ready"], result["command_present"], result.get("authentication"), result["compatibility"]),
                                 (int(failure is not None), failure is None, present, auth, compatibility))
                self.assertEqual((result["peer"], result["mode"], result["bridge_version"], result["review"], result["work"], result["image"]),
                                 ("codex", "work", cli.VERSION, "supported", work, "unknown"))
                self.assertEqual((result["model_switch"], result["effort_switch"]), tuple(codex.CHOICE_SWITCHES))
                if failure:
                    self.assertIn("fixture reason", result["reason"])
                    self.assertTrue(result["next_action"])
                else:
                    self.assertEqual((result["executable"], result["version"], result["platform"]),
                                     ("/fake/peer", "9.9.9", "Darwin 26 arm64"))
                    self.assertIn("boundary warning", result["warnings"])
        with tempfile.TemporaryDirectory() as root:
            for module in (hermes, qwen):
                directory = os.path.join(root, module.HARNESS_ID)
                record.record(directory, "session-create", "Unsupported.\n", initiator="app", peer=module.HARNESS_ID, mode="work")
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(directory, "Body.\n")
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                self.assertIn("terminal", caught.exception.detail)
                self.assertEqual(os.listdir(session.messages_dir(directory)), [])
            with mock.patch.object(zcode, "CONFIG_FILE", os.path.join(root, "absent.json")):
                with self.assertRaises(BridgeError) as caught:
                    zcode._sign_in_facts()
                self.assertEqual(caught.exception.failure, Failure.AUTHENTICATION_REQUIRED)


class CodexStructuredDispatch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="bridge-codex-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.codex_home = os.path.join(self.temp, "home")
        self.program = os.path.join(self.temp, "codex")
        self.log = os.path.join(self.temp, "launches.jsonl")
        self.events = os.path.join(self.temp, "events.jsonl")
        Path(self.program).write_text("#!" + sys.executable + "\n" + r'''import json, os, pathlib, sys
body = sys.stdin.read()
with open(os.environ["BRIDGE_FAKE_CODEX_LOG"], "a") as out:
    out.write(json.dumps({"argv": sys.argv[1:], "body": body}) + "\n")
rollout = os.environ.get("BRIDGE_FAKE_CODEX_ROLLOUT")
if rollout:
    pathlib.Path(rollout).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(rollout).write_text(json.dumps({"type": "turn_context", "payload": {"model": "recorded-model", "effort": "recorded-effort"}}) + "\n")
sys.stderr.write("startup warning\n" * 2000)
sys.stdout.write(pathlib.Path(os.environ["BRIDGE_FAKE_CODEX_EVENTS"]).read_text())
sys.exit(int(os.environ.get("BRIDGE_FAKE_CODEX_EXIT", "0")))
''')
        os.chmod(self.program, 0o755)
        patcher = mock.patch.dict(os.environ, {"CODEX_HOME": self.codex_home,
                                 "BRIDGE_FAKE_CODEX_LOG": self.log, "BRIDGE_FAKE_CODEX_EVENTS": self.events,
                                 "BRIDGE_FAKE_CODEX_ROLLOUT": "", "BRIDGE_FAKE_CODEX_EXIT": "0"})
        patcher.start()
        self.addCleanup(patcher.stop)
        prerequisite = mock.patch.object(codex, "_prerequisites", return_value=(self.program, "0.155.1", "Darwin 26 arm64", ()))
        prerequisite.start()
        self.addCleanup(prerequisite.stop)


    def _session(self, name, mode="work"):
        path = os.path.join(self.temp, name)
        record.record(
            path, "session-create", "A disposable Codex dispatch.\n",
            initiator="vibe-coder", peer="codex", project=self.temp, mode=mode,
        )
        return path


    def _stream(self, events, exit_code=0):
        with open(self.events, "w", encoding="utf-8") as stream:
            stream.write(
                "\n".join(
                    json.dumps(event, ensure_ascii=False) for event in events
                ) + "\n"
            )
        os.environ["BRIDGE_FAKE_CODEX_EVENTS"] = self.events
        os.environ["BRIDGE_FAKE_CODEX_EXIT"] = str(exit_code)


    def _body(self, result):
        with open(result.response_path, encoding="utf-8") as stream:
            return stream.read().split("## Body\n\n", 1)[1]


    def test_last_finished_message_wins_and_nonzero_exit_publishes_no_reply(self):
        final = "Final café.\n" + "Complete answer. " * 200
        stream = [{"type": "thread.started", "thread_id": "thread-fixture"},
                  {"type": "error", "message": "recoverable retry"},
                  {"type": "item.completed", "item": {"id": "comment", "type": "agent_message", "text": "Commentary"}},
                  {"type": "item.completed", "item": {"id": "final", "type": "agent_message", "text": final}},
                  {"type": "turn.completed"}]
        for mode in ("work", "review"):
            self._stream(stream)
            result = runner.run_turn(self._session("ok-" + mode, mode), "Body.\n")
            self.assertEqual(self._body(result), final)
            self._stream(stream, exit_code=1)
            directory = self._session("nonzero-" + mode, mode)
            with self.assertRaises(BridgeError) as caught:
                runner.run_turn(directory, "Body.\n")
            self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
            self.assertEqual(os.listdir(session.messages_dir(directory)), ["0001-initiator-to-peer.md"])

    def test_rejection_survives_warning_flood_and_redacts_private_content(self):
        body = "PRIVATE REQUEST café\nSECOND PRIVATE LINE\n"
        reason = "Requested model unsupported. " * 40 + "ACTIONABLE END"
        message = reason + "\n" + body + "api_key=opaque-private-key\n"
        for mode in ("work", "review"):
            self._stream([{"type": "turn.failed", "error": {"message": message}}], exit_code=1)
            directory = self._session("rejected-" + mode, mode)
            with self.assertRaises(BridgeError) as caught:
                runner.run_turn(directory, body, model="chosen-model")
            detail = caught.exception.detail
            decoded, _ = json.JSONDecoder().raw_decode(detail.split("structured stdout failure: ", 1)[1])
            self.assertIn(reason, decoded)
            self.assertIn("request body redacted", detail)
            self.assertIn("credential line redacted", detail)
            for private in ("PRIVATE REQUEST", "SECOND PRIVATE LINE", "opaque-private-key"):
                self.assertNotIn(private, detail)
            self.assertEqual(os.listdir(session.messages_dir(directory)), ["0001-initiator-to-peer.md"])
        launches = [json.loads(line) for line in Path(self.log).read_text().splitlines()]
        self.assertEqual(len(launches), 2)
        self.assertTrue(all(("-m", "chosen-model") in list(zip(x["argv"], x["argv"][1:])) for x in launches))

    def test_failed_or_unfinished_turns_never_publish_candidates(self):
        answer = {"type": "item.completed", "item": {"id": "answer", "type": "agent_message", "text": "Candidate"}}
        streams = ([answer], [answer, {"type": "turn.failed", "error": {"message": "Actual failure"}}],
                   [answer, {"type": "item.started", "item": {"type": "agent_message"}}, {"type": "turn.completed"}])
        for index, stream in enumerate(streams):
            self._stream(stream)
            directory = self._session("unfinished-" + str(index))
            with self.assertRaises(BridgeError) as caught:
                runner.run_turn(directory, "Body.\n")
            self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
            self.assertEqual(os.listdir(session.messages_dir(directory)), ["0001-initiator-to-peer.md"])

    def test_reply_reports_requested_and_codex_recorded_choices_in_both_formats(self):
        os.environ["BRIDGE_FAKE_CODEX_ROLLOUT"] = os.path.join(self.codex_home, "sessions", "rollout-thread-choices.jsonl")
        self._stream([{"type": "thread.started", "thread_id": "thread-choices"},
                      {"type": "item.completed", "item": {"id": "final", "type": "agent_message", "text": "Answer.\n"}},
                      {"type": "turn.completed"}])
        for name, mode in (("format-three", "work"), ("format-two", None)):
            result = runner.run_turn(self._session(name, mode), "Choices.\n",
                                     model="requested-model", effort="requested-effort")
            reply = Path(result.response_path).read_text()
            prefix = "# Message 0002\nFrom: codex\nTo: vibe-coder\n"
            if mode:
                prefix += "Answers: 0001\n"
            self.assertEqual(reply, prefix + "Model: recorded-model\nEffort: recorded-effort\n"
                             "Requested-Model: requested-model\nRequested-Effort: requested-effort\n\n## Body\n\nAnswer.\n")
        launches = [json.loads(line) for line in Path(self.log).read_text().splitlines()]
        self.assertEqual(len(launches), 2)
        for launch in launches:
            pairs = list(zip(launch["argv"], launch["argv"][1:]))
            self.assertIn(("-m", "requested-model"), pairs)
            self.assertIn(("-c", "model_reasoning_effort=requested-effort"), pairs)


class CallerNamedModelAndEffortChoices(unittest.TestCase):
    def test_named_choices_pass_native_switches_or_are_refused_unsent(self):
        with tempfile.TemporaryDirectory() as root:
            rows = ((codex, "-m", "-c", "model_reasoning_effort=xhigh"),
                    (claude, "--model", "--effort", "xhigh"),
                    (minimax, "--model", None, None))
            for module, model_switch, effort_switch, effort_value in rows:
                program = "/fake/" + module.HARNESS_ID
                facts = (program, "1.2.3", "Darwin 26 arm64", ())
                if module is not codex:
                    facts = facts[:3] + ("authenticated",) + facts[3:]
                for builder in ("build_command", "build_work_command"):
                    named = dict(model="chosen-model")
                    if effort_switch:
                        named["effort"] = "xhigh"
                    with mock.patch.object(module, "_prerequisites", return_value=facts):
                        command = getattr(module, builder)(peer.Deadline(30), root, **named)
                    pairs = list(zip(command.argv, command.argv[1:]))
                    self.assertIn((model_switch, "chosen-model"), pairs)
                    if effort_switch:
                        self.assertIn((effort_switch, effort_value), pairs)
            for index, (target, named) in enumerate((
                ("zcode", dict(model="chosen")), ("zcode", dict(effort="high")),
                ("hermes", dict(model="chosen")), ("qwen", dict(effort="high")),
                ("minimax", dict(effort="high")), ("codex", dict(model=" spaced ")),
                ("codex", dict(effort="two\nlines")),
            )):
                directory = os.path.join(root, str(index))
                record.record(directory, "session-create", "Choice.\n", initiator="app", peer=target)
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(directory, "Body.\n", **named)
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                self.assertEqual(os.listdir(session.messages_dir(directory)), [])


class WorkModeCheckQualifications(unittest.TestCase):
    def test_work_help_and_optional_switches(self):
        with tempfile.TemporaryDirectory() as root:
            for module in (codex, claude, zcode, minimax):
                version = module.QUALIFICATION.versions[-1]
                help_text = " ".join(module.WORK_RESTRICTIONS)
                def probe(argv, cwd, deadline, env=None):
                    if argv[-1] == "--version" or (module is zcode and argv[2] == "version"):
                        return CompletedCall(0, version, "")
                    if tuple(argv[1:]) == ("login", "status"):
                        return CompletedCall(0, "", "Logged in using ChatGPT")
                    if tuple(argv[1:]) == ("auth", "status", "--json"):
                        return CompletedCall(0, '{"loggedIn":true}', "")
                    return CompletedCall(0, help_text, "")
                with mock.patch.object(connectors, "probe", side_effect=probe), \
                        mock.patch.object(connectors, "executable", return_value="/fake/peer"), \
                        mock.patch.object(connectors, "qualified_platform", return_value="Darwin 26 arm64"), \
                        mock.patch.object(zcode, "_program", return_value=("/fake/node", "/fake/zcode.cjs")), \
                        mock.patch.object(zcode, "_environment", return_value=()), \
                        mock.patch.object(zcode, "_sign_in_facts", return_value="config present"):
                    checked = module.check(peer.Deadline(30), root, mode="work")
                    self.assertIn(module.WORK_WARNING, checked.warnings)
                    with self.assertRaises(BridgeError) as caught:
                        module.check(peer.Deadline(30), root, mode="review")
                    self.assertEqual(caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE)
                    option = dict(attachments=("/image.png",)) if module is not claude else dict(access_paths=("/docs",))
                    with self.assertRaises(BridgeError) as caught:
                        module.build_work_command(peer.Deadline(30), root, **option)
                    self.assertEqual(caught.exception.failure, Failure.RESTRICTIONS_UNAVAILABLE)
                    if module is zcode:
                        facts = module.check(peer.Deadline(30), root, mode="work", work_permission="full-access")
                        self.assertEqual((facts.effective_permission, facts.headless_shell, facts.shell_execution_verified),
                                         ("full-access", True, False))
                        command = module.build_work_command(peer.Deadline(30), root, work_permission="full-access")
                        self.assertEqual(command.argv, ("/fake/node", "/fake/zcode.cjs", "--mode", "yolo", "--cwd", root, "--output-format", "text"))
            for target, mode in (("codex", "work"), ("zcode", "review")):
                with mock.patch.object(connectors, "resolve") as resolve, mock.patch("sys.stderr", io.StringIO()):
                    self.assertEqual(cli.main(["check", "--peer", target, "--mode", mode,
                                               "--work-permission", "full-access"]), 1)
                    resolve.assert_not_called()


class WindowsLockingTwins(unittest.TestCase):
    def test_simulated_windows_lock_refuses_second_holder_and_unlocks(self):
        held, calls = set(), []
        def lock(fd, mode, length):
            key = os.fstat(fd).st_ino
            calls.append((mode, length, os.lseek(fd, 0, os.SEEK_CUR)))
            if mode == 2:
                if key in held:
                    raise OSError(13, "busy")
                held.add(key)
            else:
                held.discard(key)
        fake = mock.Mock(LK_NBLCK=2, LK_UNLCK=0, locking=lock)
        with tempfile.TemporaryDirectory() as root, mock.patch.object(locking, "msvcrt", fake):
            with locking.session_lock(root):
                with self.assertRaises(BridgeError) as caught:
                    with locking.session_lock(root):
                        self.fail("lock granted twice")
                self.assertEqual(caught.exception.failure, Failure.BUSY_SESSION)
            self.assertEqual(held, set())
            with locking.session_lock(root):
                self.assertEqual(len(held), 1)
        self.assertEqual(calls, [(2, 1, 0), (2, 1, 0), (0, 1, 0), (0, 1, 0), (2, 1, 0), (0, 1, 0)])


class WindowsProcessCleanup(unittest.TestCase):
    def test_ownership_precedes_resume_and_release_precedes_tree_confirmation(self):
        order = []
        process = mock.Mock(pid=4321, stdin=None, stdout=None, stderr=None)
        def mark(name, result=None):
            def call(*args):
                order.append(name)
                return result
            return call
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(peer, "WINDOWS", True), \
                mock.patch.object(peer.subprocess, "Popen", return_value=process) as spawn, \
                mock.patch.object(peer, "_windows_own_tree", side_effect=mark("own", "job")), \
                mock.patch.object(peer, "_windows_resume_root", side_effect=mark("resume")), \
                mock.patch.object(peer, "_windows_release_job", side_effect=mark("release")), \
                mock.patch.object(peer, "_windows_taskkill", side_effect=mark("confirm", True)), \
                mock.patch.object(peer, "_reap"), mock.patch.object(peer.os, "kill"):
            with peer.start_call(("fixture",), root, (), ""):
                self.assertEqual(order, ["own", "resume"])
            self.assertEqual(order, ["own", "resume", "release", "confirm"])
            self.assertEqual(spawn.call_args.kwargs["creationflags"], peer._CREATE_NEW_PROCESS_GROUP | peer._CREATE_SUSPENDED)

    def test_both_windows_structure_layouts(self):
        information = peer._windows_job_information()
        pointer = ctypes.sizeof(ctypes.c_void_p)
        basic = 64 if pointer == 8 else 48
        self.assertEqual(information.BasicLimitInformation.LimitFlags, peer._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
        self.assertEqual(ctypes.sizeof(type(information.IoInfo)), 48)
        self.assertEqual(ctypes.sizeof(information.BasicLimitInformation), basic)
        self.assertEqual(ctypes.sizeof(information), basic + 48 + 4 * pointer)
        self.assertEqual(type(information).IoInfo.offset, basic)
        entry = peer._windows_thread_entry()
        self.assertEqual(ctypes.sizeof(entry), 28)
        self.assertEqual(entry.dwSize, 0)
        self.assertEqual([n for n, _ in type(entry)._fields_],
                         ["dwSize", "cntUsage", "th32ThreadID", "th32OwnerProcessID", "tpBasePri", "tpDeltaPri", "dwFlags"])
        self.assertEqual((type(entry).th32ThreadID.offset, type(entry).th32OwnerProcessID.offset), (8, 12))

    def test_the_windows_cleanup_never_names_a_signal_windows_does_not_have(self):
        windows = type("WindowsSignals", (), dict(SIGINT=2, SIGTERM=15, CTRL_C_EVENT=0, CTRL_BREAK_EVENT=1))()
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
            peer, "_SIGKILL", None
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

        self.assertEqual(kills, [(4321, windows.CTRL_BREAK_EVENT)])
        self.assertEqual(releases, [job])
        self.assertEqual(taskkills, [])

        # Without the job no missing root is an emptiness answer at all.
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


if __name__ == "__main__":
    unittest.main()
