"""Public records, exact connector commands, and real process/lock behavior.

SPDX-License-Identifier: CC0-1.0
"""

from __future__ import annotations

import io
from pathlib import Path
import json
import os
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
    minimax,
    peer,
    qwen,
    record,
    runner,
    session,
    zcode,
)
from ora_agent_bridge.connectors import COMMAND_LINE_BODY_LIMIT, PeerCommand  # noqa: E402
from ora_agent_bridge.errors import BridgeError, Failure  # noqa: E402
from ora_agent_bridge.locking import lock_path, session_lock  # noqa: E402

FAKE_PEER = os.path.join(REPO_ROOT, "tests", "fake_peer.py")

#: How long a check waits for something that must happen very soon.
SHORT_WAIT = 10.0
POLL = 0.02

#: A program that takes the session lock, says so, and holds it until
#: killed. The simplest way to have the lock held by a process this one can
#: end abruptly.
LOCK_HOLDER = (
    "import fcntl, os, sys\n"
    "handle = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
    "fcntl.flock(handle, fcntl.LOCK_EX)\n"
    "sys.stdout.write('LOCKED\\n')\n"
    "sys.stdout.flush()\n"
    "sys.stdin.readline()\n"
)

#: How the fake peer reports the processes a stop signal has to reach.
PID_LINE = re.compile(r"^(PEER|CHILD) (\d+)$", re.MULTILINE)

_DRIVER_HEAD = (
    "import os, signal, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from ora_agent_bridge import peer\n"
)

#: A `run_bounded` caller in its own process, stopped from outside while it
#: waits; it exits 3 when it leaves as the stop it was.
SIGNAL_DRIVER = (
    _DRIVER_HEAD
    + "try:\n"
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

#: Stopped a second time while the first stop's cleanup is under way: the
#: second signal is delivered from in front of the cleanup step.
SECOND_STOP_DRIVER = (
    _DRIVER_HEAD
    + "empty_group = peer._cleanup_group\n"
    "def cleanup(process, pgid, job=None):\n"
    "    os.kill(os.getpid(), signal.SIGTERM)\n"
    "    return empty_group(process, pgid, job)\n"
    "peer._cleanup_group = cleanup\n"
    + "try:\n"
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


def _wait_for(condition, timeout=SHORT_WAIT):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if condition():
            return True
        time.sleep(POLL)
    return condition()


def _process_gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _fake_connector(command_argv=None, warnings=(), work=False):
    argv = command_argv or (sys.executable, FAKE_PEER, "plain")

    class FakeConnector:
        CAPABILITIES = connectors.Capabilities(
            work="supported" if work else "unsupported",
            work_detail="fake",
            image="supported" if work else "unsupported",
            image_detail="fake image route" if work else "no images",
        )
        CHOICE_SWITCHES = connectors.ChoiceSwitches()

        @staticmethod
        def check(deadline, cwd, **kwargs):
            return connectors.CheckResult(
                message="test target is ready without a model call",
                warnings=warnings,
            )

        @staticmethod
        def build_command(deadline, cwd, **kwargs):
            return PeerCommand(
                argv=argv, cwd=cwd, env=tuple(os.environ.items()),
                warnings=warnings,
            )

        @staticmethod
        def build_work_command(deadline, cwd, **kwargs):
            return PeerCommand(
                argv=argv, cwd=cwd, env=tuple(os.environ.items()),
                warnings=warnings,
            )

    return FakeConnector


class TurnBehavior(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="agent-bridge-check-")
        self.session_dir = os.path.join(self.temp, "session")
        record.record(
            self.session_dir,
            "session-create",
            "Prove the runner without spending a real harness call.\n",
            initiator="sample-app",
            peer="claude",
        )
        self.holders = []
        self.drivers = []
        self.strays = []

    def tearDown(self):
        for holder in self.holders:
            if holder.poll() is None:
                holder.kill()
            holder.wait()
            for stream in (holder.stdin, holder.stdout):
                if stream is not None:
                    stream.close()
        for driver in self.drivers:
            if driver.poll() is None:
                driver.kill()
            driver.wait()
            for stream in (driver.stdout, driver.stderr):
                if stream is not None:
                    stream.close()
        # A check that found a process still running has already failed;
        # this makes sure the failure does not also leave the process about.
        for pid in self.strays:
            if not _process_gone(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        shutil.rmtree(self.temp, ignore_errors=True)


    def _failing_connector(self):
        connector = _fake_connector()

        def build(deadline, cwd, **kwargs):
            raise AssertionError("invalid input reached the connector")

        connector.build_command = staticmethod(build)
        return connector

    def _turn(self, mode="plain", body="Please answer this.\n", **kwargs):
        connector = _fake_connector((sys.executable, FAKE_PEER, mode))
        with mock.patch.object(
            runner.connectors, "resolve", return_value=connector
        ):
            return runner.run_turn(self.session_dir, body, 30.0, **kwargs)

    def _expect(self, failure, mode="plain", body="Please answer this.\n"):
        with self.assertRaises(BridgeError) as caught:
            self._turn(mode, body=body)
        self.assertEqual(caught.exception.failure, failure)
        return caught.exception

    def _start_driver(self, source, *args):
        driver = subprocess.Popen(
            [sys.executable, "-c", source, REPO_ROOT, FAKE_PEER] + list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        self.drivers.append(driver)
        return driver

    def _await_reported(self, pid_path, names, running=True):
        reported = {}

        def all_reported():
            try:
                with open(pid_path, encoding="utf-8") as stream:
                    text = stream.read()
            except OSError:
                return False
            found = dict(
                (name, int(number))
                for name, number in PID_LINE.findall(text)
            )
            if len(found) != len(names):
                return False
            reported.update(found)
            return True

        self.assertTrue(
            _wait_for(all_reported),
            "the processes to watch were never reported",
        )
        pids = []
        for name in names:
            pid = reported[name]
            self.strays.append(pid)
            if running:
                self.assertTrue(
                    _wait_for(lambda: not _process_gone(pid)),
                    "{0} never started".format(name),
                )
            pids.append(pid)
        return tuple(pids)

    def _messages(self):
        return sorted(os.listdir(session.messages_dir(self.session_dir)))

    def _leftover_temporaries(self):
        return [
            name
            for name in os.listdir(session.messages_dir(self.session_dir))
            if name.startswith(session.TEMP_PREFIX)
        ]

    def _start_lock_holder(self):
        holder = subprocess.Popen(
            [sys.executable, "-c", LOCK_HOLDER, lock_path(self.session_dir)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            shell=False,
        )
        self.holders.append(holder)
        self.assertEqual(holder.stdout.readline(), b"LOCKED\n")
        return holder


    def test_round_trip_publishes_request_and_response(self):
        result = self._turn(body="Consider the question.\n")

        self.assertEqual(result.request_sequence, 1)
        self.assertEqual(result.response_sequence, 2)
        self.assertEqual(
            self._messages(),
            ["0001-initiator-to-peer.md", "0002-peer-to-initiator.md"],
        )
        with open(
            session.message_path(
                self.session_dir, 1, session.INITIATOR_TO_PEER_SUFFIX
            ),
            encoding="utf-8",
        ) as stream:
            self.assertEqual(
                stream.read(),
                "# Message 0001\nFrom: sample-app\nTo: claude\n\n## Body\n\n"
                "Consider the question.\n",
            )
        with open(result.response_path, encoding="utf-8") as stream:
            self.assertEqual(
                stream.read(),
                "# Message 0002\nFrom: claude\nTo: sample-app\n\n## Body\n\n"
                "Consider the question.\n",
            )
        self.assertEqual(self._leftover_temporaries(), [])

    def test_failure_during_publication_leaves_nothing_behind(self):
        target = session.message_path(
            self.session_dir, 1, session.INITIATOR_RECORD_SUFFIX
        )
        with mock.patch("os.replace", side_effect=OSError("forced failure")):
            with self.assertRaises(BridgeError) as caught:
                session.publish(target, "# Message 0001\nnever arrives\n")
        self.assertEqual(caught.exception.failure, Failure.PUBLICATION_FAILURE)
        self.assertIn("Look in the session's messages folder",
                      str(caught.exception))
        self.assertFalse(os.path.exists(target))
        self.assertEqual(self._messages(), [])
        self.assertEqual(self._leftover_temporaries(), [])

    def test_each_call_starts_fresh_and_sends_only_its_current_body(self):
        first = self._turn(body="First body with caf\u00e9.\n\n")
        second = self._turn(body="Second body only.\n")
        with open(first.response_path, encoding="utf-8") as stream:
            first_text = stream.read()
        with open(second.response_path, encoding="utf-8") as stream:
            second_text = stream.read()
        self.assertIn("First body with caf\u00e9.\n\n", first_text)
        self.assertNotIn("First body", second_text)
        self.assertTrue(second_text.endswith("Second body only.\n"))

    def test_runner_uses_the_session_project_and_removes_a_neutral_directory(self):
        project_session = os.path.join(self.temp, "project-session")
        record.record(
            project_session,
            "session-create",
            "Project-bound call.\n",
            initiator="gear-3",
            peer="zcode",
            project=self.temp,
        )
        observed = []

        def project_builder(deadline, cwd, **kwargs):
            observed.append(cwd)
            return PeerCommand(
                argv=(sys.executable, FAKE_PEER, "plain"),
                cwd=cwd,
                env=tuple(os.environ.items()),
            )

        connector = _fake_connector()
        connector.build_command = staticmethod(project_builder)
        with mock.patch.object(
            runner.connectors, "resolve", return_value=connector
        ):
            result = runner.run_turn(
                project_session, "Use the recorded project.\n", 30.0
            )
        self.assertEqual(observed, [self.temp])

        neutral = []

        def neutral_builder(deadline, cwd, **kwargs):
            neutral.append(cwd)
            self.assertTrue(os.path.isdir(cwd))
            self.assertEqual(os.listdir(cwd), [])
            return PeerCommand(
                argv=(sys.executable, FAKE_PEER, "plain"),
                cwd=cwd,
                env=tuple(os.environ.items()),
            )

        connector.build_command = staticmethod(neutral_builder)
        with mock.patch.object(
            runner.connectors, "resolve", return_value=connector
        ):
            runner.run_turn(self.session_dir, "Neutral call.\n", 30.0)
        self.assertEqual(len(neutral), 1)
        self.assertFalse(os.path.exists(neutral[0]))

    def test_invalid_body_and_timeout_fail_before_a_connector_or_publication(self):
        for body, wait in (("   \n", 30.0), ("Body.\n", 0), ("Body.\n", -1)):
            with self.subTest(body=body, wait=wait):
                with mock.patch.object(
                    runner.connectors,
                    "resolve",
                    return_value=self._failing_connector(),
                ):
                    with self.assertRaises(BridgeError) as caught:
                        runner.run_turn(self.session_dir, body, wait)
                    self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
        self.assertEqual(self._messages(), [])


    def test_a_second_acquirer_is_busy_and_changes_nothing(self):
        self._turn()
        before = self._messages()
        self._start_lock_holder()

        with self.assertRaises(BridgeError) as caught:
            with session_lock(self.session_dir):
                self.fail("the lock was granted twice")
        self.assertEqual(caught.exception.failure, Failure.BUSY_SESSION)

        with self.assertRaises(BridgeError) as caught:
            record.record(self.session_dir, "note", "Blocked.\n")
        self.assertEqual(caught.exception.failure, Failure.BUSY_SESSION)
        self.assertEqual(self._messages(), before)

    def test_a_lock_survives_nothing_when_its_owner_is_killed(self):
        holder = self._start_lock_holder()

        with self.assertRaises(BridgeError) as caught:
            with session_lock(self.session_dir):
                self.fail("the lock was granted twice")
        self.assertEqual(caught.exception.failure, Failure.BUSY_SESSION)

        os.kill(holder.pid, signal.SIGKILL)
        holder.wait()
        self.assertTrue(_wait_for(lambda: _process_gone(holder.pid)))

        path = record.record(
            self.session_dir, "note", "After the abrupt end.\n"
        )
        self.assertTrue(os.path.exists(path))


    def test_nonzero_exit_publishes_no_response_and_redacts(self):
        body = (
            'PRIVATE REQUEST "confidential caf\u00e9" \\ details '
            + "request-content " * 20
            + "PRIVATE END\n"
        )
        script = (
            "import sys\n"
            "body = sys.stdin.read()\n"
            "sys.stdout.write('vendor wrapper ' + body + 'x' * 600 "
            "+ 'UNBOUNDED TAIL')\n"
            "sys.stderr.write('fake peer: deliberate failure\\n')\n"
            "sys.stderr.write('OPENAI_API_KEY=opaqueApiValue\\n')\n"
            "sys.stderr.write('Bearer bearer-secret-value\\n')\n"
            "sys.exit(3)\n"
        )

        def build(deadline, cwd, **kwargs):
            return PeerCommand(
                argv=(sys.executable, "-c", script),
                cwd=cwd,
                env=tuple(os.environ.items()),
            )

        connector = _fake_connector()
        connector.build_command = staticmethod(build)
        with mock.patch.object(
            runner.connectors, "resolve", return_value=connector
        ):
            with self.assertRaises(BridgeError) as caught:
                runner.run_turn(self.session_dir, body, 30.0)
        rendered = str(caught.exception)
        self.assertEqual(caught.exception.failure, Failure.PEER_FAILURE)
        self.assertIn("deliberate failure", rendered)
        self.assertIn("credential line redacted", rendered)
        self.assertIn("request body redacted", rendered)
        self.assertNotIn("confidential", rendered)
        self.assertNotIn("opaqueApiValue", rendered)
        self.assertNotIn("bearer-secret-value", rendered)
        self.assertNotIn("UNBOUNDED TAIL", rendered)
        self.assertEqual(self._messages(), ["0001-initiator-to-peer.md"])

    def test_no_output_at_all_publishes_no_response(self):
        self._expect(Failure.EMPTY_RESPONSE, "empty", body="Say nothing.\n")
        self._expect(Failure.EMPTY_RESPONSE, "whitespace", body="Say air.\n")
        # Each refusal keeps its own truthful request; neither gets a reply.
        self.assertEqual(
            self._messages(),
            ["0001-initiator-to-peer.md", "0002-initiator-to-peer.md"],
        )


    def test_a_hyphen_led_body_travels_as_one_argument_and_comes_back_whole(self):
        body = (
            "---\ntitle: bound\n--mode yolo\n-p not an option\n"
            "--disallowed-tools=Edit is text\n---\n\nAnswer this.\n"
        )
        argv = (sys.executable, FAKE_PEER, "last-argument")
        connector = _fake_connector()
        connector.build_command = staticmethod(
            lambda deadline, cwd, **kwargs: PeerCommand(
                argv=argv, cwd=cwd, env=tuple(os.environ.items()),
                body_argument="--prompt=",
            )
        )
        with mock.patch.object(
            runner.connectors, "resolve", return_value=connector
        ):
            result = runner.run_turn(self.session_dir, body, 30.0)
        with open(result.response_path, encoding="utf-8") as stream:
            response = stream.read()
        self.assertIn("--prompt=" + body, response)
        self.assertNotIn("STDIN WAS NOT EMPTY", response)

    def test_a_body_the_command_line_cannot_carry_is_refused_before_anything(self):
        for name, body in (
            ("one byte over the limit", "x" * (COMMAND_LINE_BODY_LIMIT + 1)),
            ("a NUL byte inside", "before\x00after\n"),
        ):
            with self.subTest(body=name):
                connector = _fake_connector()
                connector.build_command = staticmethod(
                    lambda deadline, cwd, **kwargs: PeerCommand(
                        argv=(sys.executable, FAKE_PEER, "last-argument"),
                        cwd=cwd,
                        env=tuple(os.environ.items()),
                        body_argument="--prompt=",
                    )
                )
                with mock.patch.object(
                    runner.connectors, "resolve", return_value=connector
                ):
                    with self.assertRaises(BridgeError) as caught:
                        runner.run_turn(self.session_dir, body, 30.0)
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                self.assertEqual(self._messages(), [])
        # And exactly at the limit the body is carried, not refused.
        connector = _fake_connector()
        connector.build_command = staticmethod(
            lambda deadline, cwd, **kwargs: PeerCommand(
                argv=(sys.executable, FAKE_PEER, "last-argument"),
                cwd=cwd,
                env=tuple(os.environ.items()),
                body_argument="",
            )
        )
        with mock.patch.object(
            runner.connectors, "resolve", return_value=connector
        ):
            result = runner.run_turn(
                self.session_dir, "y" * COMMAND_LINE_BODY_LIMIT, 30.0
            )
        self.assertEqual(result.response_sequence, 2)

    def test_a_courier_only_peer_is_refused_a_project_before_anything_starts(self):
        for peer_id in ("hermes", "minimax", "qwen"):
            with self.subTest(peer=peer_id):
                project_session = os.path.join(
                    self.temp, "project-session-{0}".format(peer_id)
                )
                record.record(
                    project_session,
                    "session-create",
                    "This target cannot receive a project.\n",
                    initiator="sample-app",
                    peer=peer_id,
                    project=self.temp,
                )
                captured = io.StringIO()
                with mock.patch("sys.stderr", captured), mock.patch(
                    "sys.stdin", io.StringIO("A question.\n")
                ):
                    status = cli.main(
                        ["run", "--session", project_session]
                    )
                self.assertEqual(status, 1)
                self.assertIn(
                    "project for {0}".format(peer_id), captured.getvalue()
                )
                self.assertEqual(
                    sorted(os.listdir(session.messages_dir(project_session))),
                    [],
                )


    def test_being_told_to_stop_cleans_up_the_peer_and_its_child(self):
        for number in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=number):
                pid_path = os.path.join(
                    self.temp, "pids-{0}.txt".format(number)
                )
                driver = self._start_driver(
                    SIGNAL_DRIVER, "write-pids-then-hang", pid_path
                )
                peer_pid, child_pid = self._await_reported(pid_path, ("PEER", "CHILD"))

                os.kill(driver.pid, number)
                driver.wait(timeout=SHORT_WAIT)
                self.assertEqual(driver.returncode, 3)
                self.assertTrue(_wait_for(lambda: _process_gone(peer_pid)))
                self.assertTrue(_wait_for(lambda: _process_gone(child_pid)))

    def test_a_second_stop_does_not_abandon_the_cleanup(self):
        pid_path = os.path.join(self.temp, "second-stop-pids.txt")
        driver = self._start_driver(
            SECOND_STOP_DRIVER, "write-pids-then-hang", pid_path
        )
        peer_pid, child_pid = self._await_reported(pid_path, ("PEER", "CHILD"))

        os.kill(driver.pid, signal.SIGTERM)
        driver.wait(timeout=SHORT_WAIT)
        self.assertEqual(driver.returncode, 3)
        self.assertTrue(
            _wait_for(lambda: _process_gone(peer_pid)),
            "the second stop abandoned the cleanup and left the peer",
        )
        self.assertTrue(
            _wait_for(lambda: _process_gone(child_pid)),
            "the second stop abandoned the cleanup and left the child",
        )

    def test_an_interrupted_turn_keeps_its_request_and_evidence(self):
        pid_path = os.path.join(self.temp, "stopped-pids.txt")
        events = []
        connector = _fake_connector(
            (sys.executable, FAKE_PEER, "stop-parent-then-hang", "60", pid_path)
        )
        with mock.patch.object(
            runner.connectors, "resolve", return_value=connector
        ):
            with self.assertRaises(peer.SignalStop):
                runner.run_turn(
                    self.session_dir, "Stop me mid-wait.\n", 30.0,
                    event_writer=events.append,
                )
        (peer_pid,) = self._await_reported(pid_path, ("PEER",), running=False)
        self.assertTrue(_wait_for(lambda: _process_gone(peer_pid)))
        self.assertEqual(self._messages(), ["0001-initiator-to-peer.md"])
        finished = json.loads(events[-1])
        self.assertEqual(finished["event"], "finished")
        self.assertEqual(finished["outcome"], "stopped")
        self.assertTrue(
            finished["request_path"].endswith("0001-initiator-to-peer.md")
        )
        self.assertNotIn("response_path", finished)


class SixTargetConnectorBehavior(unittest.TestCase):
    TARGETS = ("codex", "claude", "zcode", "hermes", "minimax", "qwen")

    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="bridge-connectors-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)

    def test_exact_targets_resolve_with_only_the_selected_module_imported(self):
        self.assertEqual(connectors.HARNESS_IDS, self.TARGETS)
        source = (
            "import sys\n"
            "from ora_agent_bridge import connectors\n"
            "selected = sys.argv[1]\n"
            "connector = connectors.resolve(selected)\n"
            "targets = set(connectors.HARNESS_IDS)\n"
            "loaded = sorted(name.rsplit('.', 1)[1] for name in sys.modules "
            "if name.startswith('ora_agent_bridge.') and name.rsplit('.', 1)[1] in targets)\n"
            "print(connector.HARNESS_ID + ':' + ','.join(loaded))\n"
        )
        for selected in self.TARGETS:
            with self.subTest(selected=selected):
                completed = subprocess.run(
                    (sys.executable, "-c", source, selected),
                    cwd=REPO_ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    timeout=SHORT_WAIT,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(
                    completed.stdout.strip(), "{0}:{0}".format(selected)
                )

        with self.assertRaises(BridgeError) as caught:
            connectors.resolve("other")
        self.assertEqual(caught.exception.failure, Failure.UNKNOWN_HARNESS)


    def test_the_claude_connector_loads_without_unix_only_modules(self):
        source = (
            "import sys\n"
            "sys.modules['pwd'] = None\n"
            "sys.modules['grp'] = None\n"
            "from ora_agent_bridge import claude\n"
            "print(claude.HARNESS_ID)\n"
        )
        completed = subprocess.run(
            (sys.executable, "-c", source),
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=SHORT_WAIT,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "claude")


    def _build_vector(self, module, builder, **kwargs):
        # Prerequisite mechanics have their own table; use the real builders here.
        program = "/fake/mcode" if module is minimax else "/fake/" + module.HARNESS_ID
        facts = (program, "1.2.3", "Darwin 26 arm64", ("boundary warning",))
        if module is not codex:
            facts = facts[:3] + ("sign-in observed",) + facts[3:]
        with mock.patch.object(module, "_prerequisites", return_value=facts), \
                mock.patch.object(connectors, "executable", return_value=program), \
                mock.patch.object(zcode, "_program", return_value=("/fake/node", "/fake/zcode.cjs")), \
                mock.patch.object(zcode, "_environment", return_value=tuple(os.environ.items())):
            return getattr(module, builder)(peer.Deadline(60), self.temp, **kwargs)

    def test_one_table_of_the_exact_review_command_for_all_six_targets(self):
        expected = {
            "codex": (
                "/fake/codex", "exec", "--json",
                "--ignore-user-config", "-c", "web_search=disabled",
                "-c", "notify=[]",
                "--disable", "hooks", "--disable", "apps", "--disable", "plugins",
                "-c", "orchestrator.mcp.enabled=false", "-c", "agents.enabled=false",
                "--disable", "multi_agent_v2",
                "--sandbox", "read-only",
                "--skip-git-repo-check", "--cd", self.temp, "-",
            ),
            "claude": (
                "/fake/claude", "--print", "--restricted", "--strict-mcp-config",
                "--tools", "Read,Glob,Grep", "--permission-mode", "plan",
                "--output-format", "text",
            ),
            "zcode": (
                "/fake/node", "/fake/zcode.cjs", "--mode", "plan",
                "--cwd", self.temp,
                "--disallowed-tools", zcode.DENIED_TOOLS,
                "--output-format", "text",
            ),
            "hermes": ("/fake/hermes", "--safe-mode", "--toolsets", "todo"),
            "minimax": (
                "/fake/mcode", "exec", "--input", "-", "--input-format", "text",
                "--cwd", self.temp, "--permission", "smart",
                "--max-steps", "1", "--output-format", "text",
            ),
            "qwen": (
                "/fake/qwen", "--safe-mode", "--sandbox=sandbox-exec",
                "--chat-recording=false", "--approval-mode=plan",
                "--disabled-slash-commands="
                "bug,config,update,import-config,language,effort,model,doctor",
                "--max-tool-calls=0", "--max-session-turns=1",
                "--input-format=stream-json", "--output-format=stream-json",
                "--openai-logging=false",
            ),
        }
        body_arguments = {"zcode": zcode.BODY_ARGUMENT,
                          "hermes": hermes.BODY_ARGUMENT}
        bypass = (
            "--dangerously-skip-permissions",
            "--dangerously-bypass-approvals-and-sandbox",
            "--allow-dangerously-skip-permissions",
            "--dangerously-bypass-hook-trust",
            "--yolo", "--accept-hooks", "--resume", "--continue", "--bare",
        )

        for module in (codex, claude, zcode, hermes, minimax, qwen):
            with self.subTest(peer=module.HARNESS_ID):
                command = self._build_vector(
                    module,
                    "build_command",
                )
                self.assertEqual(command.argv, expected[module.HARNESS_ID])
                self.assertEqual(
                    command.body_argument, body_arguments.get(module.HARNESS_ID)
                )
                joined = " ".join(command.argv + (
                    (command.body_argument + "<BODY>",)
                    if command.body_argument is not None else ()
                ))
                for switch in bypass:
                    self.assertNotIn(switch, joined)
                for switch in module.QUALIFICATION.restrictions:
                    self.assertIn(switch, joined)
                self.assertTrue(command.warnings)
                if module is hermes:
                    self.assertNotIn("TERMINAL_CWD", dict(command.env))
                if module is qwen:
                    env = dict(command.env)
                    self.assertEqual((env["QWEN_SANDBOX"], env["SEATBELT_PROFILE"], env["NO_BROWSER"]),
                                     ("sandbox-exec", "restrictive-open", "1"))


    def test_one_table_of_the_exact_work_command_for_the_supported_routes(self):
        docs = "/access/docs"
        expected = {
            "codex": (
                "/fake/codex", "exec", "--json", "--skip-git-repo-check",
                "--cd", self.temp, "--add-dir", docs, "--image", "/img/a.png", "-",
            ),
            "claude": (
                "/fake/claude", "--print", "--output-format", "text",
                "--add-dir", docs,
            ),
            "zcode": (
                "/fake/node", "/fake/zcode.cjs", "--mode", "edit",
                "--cwd", self.temp,
                "--attach", "/img/a.png", "--output-format", "text",
            ),
            "minimax": (
                "/fake/mcode", "exec", "--input", "-", "--input-format", "text",
                "--cwd", self.temp, "--file", "/img/a.png", "--output-format", "text",
            ),
        }
        review_only = {
            codex: ("--ignore-user-config", "--disable", "--sandbox"),
            claude: ("--restricted", "--strict-mcp-config", "--tools",
                     "--permission-mode"),
            minimax: ("--permission",),
            zcode: ("--disallowed-tools",),
        }
        for module in (codex, claude, zcode, minimax):
            with self.subTest(peer=module.HARNESS_ID):
                command = self._build_vector(
                    module, "build_work_command",
                    access_paths=(docs,), attachments=("/img/a.png",),
                )
                self.assertEqual(command.argv, expected[module.HARNESS_ID])
                for switch in review_only[module]:
                    self.assertNotIn(switch, " ".join(command.argv))
                self.assertTrue(command.warnings)


    def test_minimax_options_and_other_targets_refuse_them_unsent(self):
        for builder in ("build_command", "build_work_command"):
            command = self._build_vector(minimax, builder,
                                         max_steps=2, required_model="minimax/MiniMax-M3")
            pairs = list(zip(command.argv, command.argv[1:]))
            self.assertIn(("--max-steps", "2"), pairs)
            self.assertIn(("--output-format", "json"), pairs)
            self.assertIsNotNone(command.response_parser)
        for target in ("codex", "claude", "zcode", "hermes", "qwen"):
            directory = os.path.join(self.temp, target)
            record.record(directory, "session-create", "Options.\n",
                          initiator="app", peer=target)
            with mock.patch.object(connectors, "resolve") as resolve:
                with self.assertRaises(BridgeError) as caught:
                    runner.run_turn(directory, "Body.\n", max_steps=2,
                                    required_model="minimax/MiniMax-M3")
                self.assertEqual(caught.exception.failure, Failure.USAGE_ERROR)
                resolve.assert_not_called()
            self.assertEqual(os.listdir(session.messages_dir(directory)), [])


class FormatTwoRecords(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="bridge-records-")
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.session_dir = os.path.join(self.temp, "session")

    def _create(self, **kwargs):
        return record.record(self.session_dir, "session-create", "Session body.\n",
                             initiator="my-app", peer="claude", **kwargs)

    def test_session_and_note_records_keep_body_inert(self):
        path = self._create(project=self.temp)
        self.assertEqual(Path(path).read_text(),
                         "# Session\n\nBridge-Format: 2\nInitiator: my-app\nPeer: claude\n"
                         f"Project: {self.temp}\n\n## Body\n\nSession body.\n")
        self.assertEqual(os.listdir(session.messages_dir(self.session_dir)), [])
        body = "Peer: codex\nProject: /elsewhere\nMode: work\n\nStill body.\n"
        note = record.record(self.session_dir, "note", body)
        self.assertEqual(Path(note).name, "0001-initiator-record.md")
        self.assertEqual(Path(note).read_text(),
                         "# Message 0001\nRecord: note\nFrom: my-app\n\n## Body\n\n" + body)
        parsed = session.read_session(self.session_dir)
        self.assertEqual((parsed.peer, parsed.project, parsed.mode),
                         ("claude", self.temp, "review"))

    def test_reader_accepts_edit_noise_but_rejects_invalid_bindings(self):
        header = "# Session\n\nBridge-Format: 2\nInitiator: app\nPeer: claude\n"
        tail = "\n## Body\n\nPeer: codex\nProject: /elsewhere\nMode: work\n"
        accepted = (
            (header, "app", "review", ()),
            (header + "Initiator: app2\nStatus: ready\n", "app2", "review", ()),
            (header + "Mode: work\nAccess-Path: /tmp\n", "app", "review", ()),
            (header.replace("Format: 2", "Format: 3") +
             "Mode: work\nAccess-Path: /tmp\nAccess-Path: /tmp\n", "app", "work", ("/tmp", "/tmp")),
        )
        os.makedirs(session.messages_dir(self.session_dir))
        for text, initiator, mode, access in accepted:
            Path(session.session_file(self.session_dir)).write_text(text + tail)
            parsed = session.read_session(self.session_dir)
            self.assertEqual((parsed.initiator, parsed.peer, parsed.project,
                              parsed.mode, parsed.access_paths),
                             (initiator, "claude", None, mode, access))
        refused = (
            header.replace("Format: 2", "Format: 1") + tail,
            header.replace("Peer: claude", "Peer: other") + tail,
            header.replace("Initiator: app\n", "") + tail,
            header + "Project: relative\n" + tail,
            header.replace("Format: 2", "Format: 3") + tail,
            header.replace("Format: 2", "Format: 3") + "Mode: yolo\n" + tail,
            header + "\n## Body\n\n   \n",
            header + "\nnot the body heading\n\nBody.\n",
            header + " \nPeer: zcode\n\n## Body\n\nRedirected.\n",
        )
        for text in refused:
            with self.subTest(text=text):
                Path(session.session_file(self.session_dir)).write_text(text)
                with self.assertRaises(BridgeError) as caught:
                    session.read_session(self.session_dir)
                self.assertEqual(caught.exception.failure, Failure.SESSION_INVALID)

    def test_invalid_utf8_session_is_reported_as_session_invalid_by_the_cli(self):
        os.makedirs(session.messages_dir(self.session_dir))
        with open(session.session_file(self.session_dir), "wb") as stream:
            stream.write(b"# Session\n\nBridge-Format: 2\nInitiator: app\xff\n")

        with self.assertRaises(BridgeError) as caught:
            session.read_session(self.session_dir)
        self.assertEqual(caught.exception.failure, Failure.SESSION_INVALID)

        captured = io.StringIO()
        with mock.patch("sys.stderr", captured), mock.patch(
            "sys.stdin", io.StringIO("Neutral note.\n")
        ):
            status = cli.main(
                ["record", "--session", self.session_dir, "--kind", "note"]
            )
        self.assertEqual(status, 1)
        self.assertIn(
            "record could not be read as a valid session", captured.getvalue()
        )
        self.assertIn("Next action:", captured.getvalue())
        self.assertNotIn("Traceback", captured.getvalue())
        self.assertEqual(sorted(os.listdir(session.messages_dir(self.session_dir))), [])


if __name__ == "__main__":
    unittest.main()
