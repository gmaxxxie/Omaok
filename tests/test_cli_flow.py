"""CLI state-machine + safety contract tests.

These run the real CLI modules with a mocked STT layer so no audio/network is
needed, and assert the safety invariants: nothing executes before explicit
confirmation, no raw transcript is ever used as a command, and the executor is
only ever called with a structured Action that passed policy.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import state as state_mod
import cli as cli_mod
from recorder import Recorder


class _FakeRecorder:
    """Stand-in for Recorder so tests need no mic."""

    def __init__(self, device="default", max_seconds=30.0):
        pass

    def start(self):
        pass

    def stop(self):
        pass  # transcribe is mocked; no WAV content needed

    def cancel(self):
        pass


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        # Isolate runtime state AND operation memory per test.
        self.tmp = tempfile.mkdtemp()
        self._rt = state_mod._RUNTIME_BASE
        state_mod._RUNTIME_BASE = self.tmp
        import config.memory as memory_mod
        self._mem_orig = memory_mod.settings.USER_CONFIG_DIR
        memory_mod.settings.USER_CONFIG_DIR = self.tmp
        self.patchers = [
            mock.patch.object(cli_mod, "Recorder", _FakeRecorder),
            mock.patch.object(cli_mod, "raw_duration_seconds", return_value=1.0),
            mock.patch.object(cli_mod.stt, "transcribe"),
        ]
        for p in self.patchers:
            p.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import config.memory as memory_mod
        for p in self.patchers:
            p.stop()
        state_mod._RUNTIME_BASE = self._rt
        memory_mod.settings.USER_CONFIG_DIR = self._mem_orig

    def _set_transcript(self, text):
        cli_mod.stt.transcribe.return_value = text

    def test_record_start_sets_recording_phase(self):
        cli_mod.cmd_record_start()
        self.assertEqual(state_mod.load_state()["phase"], "recording")

    def test_low_risk_auto_executes_without_confirm(self):
        self._set_transcript("打开浏览器")
        with mock.patch.object(cli_mod.executor, "execute", return_value=(True, "ok")) as exec_:
            cli_mod.cmd_record_start()
            cli_mod.cmd_record_stop()
            exec_.assert_called_once()  # low risk -> executed directly
            called_action = exec_.call_args[0][0]
            self.assertEqual(called_action["type"], "open_app")
            self.assertEqual(called_action["source"], "rule")
        st = state_mod.load_state()
        self.assertEqual(st["phase"], "result")
        self.assertTrue(st["result"]["ok"])

    def test_confirm_required_waits_for_explicit_confirm(self):
        self._set_transcript("锁屏")
        with mock.patch.object(cli_mod.executor, "execute", return_value=(True, "ok")) as exec_:
            cli_mod.cmd_record_start()
            cli_mod.cmd_record_stop()
            exec_.assert_not_called()  # high risk -> must wait for confirm
            self.assertEqual(state_mod.load_state()["phase"], "awaiting_confirm")
            cli_mod.cmd_confirm()
            exec_.assert_called_once()
            called_action = exec_.call_args[0][0]
            self.assertEqual(called_action["type"], "lock_screen")
        st = state_mod.load_state()
        self.assertEqual(st["phase"], "result")
        self.assertTrue(st["result"]["ok"])

    def test_no_execution_before_confirm(self):
        self._set_transcript("锁屏")
        cli_mod.cmd_record_start()
        cli_mod.cmd_record_stop()
        st = state_mod.load_state()
        self.assertEqual(st["phase"], "awaiting_confirm")
        self.assertEqual(st["action"]["risk"], "confirm_required")
        with mock.patch.object(cli_mod.executor, "execute") as exec_:
            cli_mod.cmd_cancel_action()  # user declines -> nothing runs
            exec_.assert_not_called()
        self.assertEqual(state_mod.load_state()["phase"], "idle")

    def test_unknown_phrase_never_executes(self):
        # The AI path is also mocked to None so this stays a pure
        # "unknown phrase must never execute" invariant test (chat handling is
        # covered separately in test_model_picker/test chat fallback).
        with mock.patch.object(cli_mod.intent_ai, "unified", return_value=None):
            self._set_transcript("今天天气怎么样")
            cli_mod.cmd_record_start()
            cli_mod.cmd_record_stop()
        st = state_mod.load_state()
        self.assertEqual(st["phase"], "idle")
        self.assertIn("understand", st["error"])
        self.assertIsNone(st["action"])

    def test_character_memory_answers_identity_offline(self):
        # "你是谁 / 你的背景" must be answered from omaok's persona WITHOUT a
        # model call (chat_analyze should never run for these).
        with mock.patch.object(cli_mod.intent_ai, "chat_analyze") as ca:
            self._set_transcript("你是谁")
            cli_mod.cmd_record_start()
            cli_mod.cmd_record_stop()
            ca.assert_not_called()
        st = state_mod.load_state()
        self.assertEqual(st["phase"], "chat_reply")
        self.assertIn("omaok", st["chat_reply"])
        self.assertIsNone(st["action"])

    def test_character_memory_background_question(self):
        self._set_transcript("你的背景是什么")
        cli_mod.cmd_record_start()
        cli_mod.cmd_record_stop()
        st = state_mod.load_state()
        self.assertEqual(st["phase"], "chat_reply")
        self.assertIn("Omarchy", st["chat_reply"])
        self.assertIn("右下角", st["chat_reply"])

    def test_general_chat_still_uses_model(self):
        # Non-identity questions must NOT be short-circuited: the model still
        # classifies them via the single unified call (answer/defer/none).
        with mock.patch.object(cli_mod.intent_ai, "unified",
                               return_value={"kind": "answer", "reply": "巴黎是法国首都"}) as uni:
            self._set_transcript("法国的首都")
            cli_mod.cmd_record_start()
            cli_mod.cmd_record_stop()
            uni.assert_called_once()
        st = state_mod.load_state()
        self.assertEqual(st["phase"], "chat_reply")
        self.assertEqual(st["chat_reply"], "巴黎是法国首都")

    def test_record_cancel_discards(self):
        cli_mod.cmd_record_start()
        cli_mod.cmd_record_cancel()
        st = state_mod.load_state()
        self.assertEqual(st["phase"], "idle")
        self.assertEqual(st["transcript"], "")
        self.assertIsNone(st["action"])

    def test_refresh_provider_updates_state(self):
        cli_mod.cmd_refresh_provider()
        self.assertIsNotNone(state_mod.load_state()["provider"])

    def test_confirm_requires_awaiting_phase(self):
        # confirm with no pending action is a no-op (never executes)
        with mock.patch.object(cli_mod.executor, "execute") as exec_:
            cli_mod.cmd_confirm()
            exec_.assert_not_called()


class TestRecorderKill(unittest.TestCase):
    """Regression: record start/stop are separate processes; the recorder PID
    must be persisted and reliably killed so it never records invisibly or grows
    the WAV unboundedly (this was the root cause of a stuck/runaway state)."""

    def test_pid_helpers_roundtrip(self):
        import recorder
        recorder._clear_pid()
        self.assertIsNone(recorder._read_pid())
        recorder._write_pid(12345)
        self.assertEqual(recorder._read_pid(), 12345)
        recorder._clear_pid()
        self.assertIsNone(recorder._read_pid())

    def test_kill_existing_kills_orphaned_recorder(self):
        import recorder
        import subprocess
        # Stand-in for an orphaned pw-record (own session so we don't nuke the test's group).
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        recorder._write_pid(proc.pid)
        self.assertEqual(recorder._read_pid(), proc.pid)
        recorder.Recorder._kill_existing()  # static method, no instance needed
        self.assertIsNone(recorder._read_pid())
        proc.wait(timeout=5)
        self.assertIsNotNone(proc.poll())  # it was killed


class TestNoInjectionContract(unittest.TestCase):
    """The integration must never drive Voxtype's injecting record path."""

    def test_provider_uses_transcribe_not_record(self):
        """The provider's only Voxtype command is `transcribe` — never `record`."""
        import stt.provider as provider
        with mock.patch.object(
            provider.subprocess, "run",
            return_value=mock.Mock(returncode=0, stdout="hello world\n", stderr=""),
        ) as run:
            text = provider.transcribe("/tmp/fake.wav", {"stt": {"engine": "auto", "language": "zh"}})
            self.assertEqual(text, "hello world")
            args = run.call_args[0][0]
            self.assertIn("transcribe", args)
            self.assertNotIn("record", args)

    def test_executor_never_concatenates_shell_strings_from_voice(self):
        import executor.actions as actions
        import inspect
        src = inspect.getsource(actions)
        # No shell=True anywhere; commands are argument lists.
        self.assertNotIn("shell=True", src)
        # Targets flow through structured args, never f-string command building.
        self.assertNotIn('"hyprctl dispatch exec " +', src)


if __name__ == "__main__":
    unittest.main()
