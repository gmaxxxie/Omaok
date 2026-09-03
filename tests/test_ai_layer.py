"""Tests for the AI intent layer (pi RPC, non-thinking).

The RPC subprocess is mocked: we assert the provider's prompt/validation logic
and that the CLI only ever lets a validated Action through the same gate.
"""

import os
import tempfile
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intent import ai as intent_ai
from intent import rules


class TestExtractJson(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(intent_ai._extract_json('{"type":"open_folder"}'), {"type": "open_folder"})

    def test_code_fence(self):
        obj = intent_ai._extract_json('```json\n{"type":"open_app","target":{"name":"kitty"}}\n```')
        self.assertEqual(obj["type"], "open_app")
        self.assertEqual(obj["target"]["name"], "kitty")

    def test_surrounding_prose(self):
        obj = intent_ai._extract_json('Sure! Here you go: {"type":"lock_screen","confidence":0.9}')
        self.assertEqual(obj["type"], "lock_screen")

    def test_garbage(self):
        self.assertIsNone(intent_ai._extract_json("no json here"))
        self.assertIsNone(intent_ai._extract_json(""))

    def test_nested_braces_in_string(self):
        obj = intent_ai._extract_json('{"type":"open_file","target":{"name":"a{b}.txt"}}')
        self.assertEqual(obj["target"]["name"], "a{b}.txt")


class TestToDraft(unittest.TestCase):
    def test_valid_open_folder(self):
        d = intent_ai._to_draft({"type": "open_folder", "target": {"name": "downloads"}, "confidence": 0.9})
        self.assertEqual(d["type"], "open_folder")
        self.assertEqual(d["raw_target"], "downloads")
        self.assertEqual(d["source"], "future_llm")

    def test_workspace_id(self):
        d = intent_ai._to_draft({"type": "switch_workspace", "target": {"id": 3}, "confidence": 0.95})
        self.assertEqual(d["raw_target"], "3")

    def test_rejects_unknown_type(self):
        self.assertIsNone(intent_ai._to_draft({"type": "exec_shell", "target": {}, "confidence": 0.9}))

    def test_rejects_none_type(self):
        self.assertIsNone(intent_ai._to_draft({"type": "none", "confidence": 0}))

    def test_never_accepts_shell(self):
        # Even if the model (wrongly) emits a shell field, the draft has no shell.
        d = intent_ai._to_draft({"type": "open_app", "target": {"name": "kitty"}, "confidence": 0.9})
        self.assertNotIn("shell", d)
        self.assertNotIn("command", d)

    def test_confidence_clamped(self):
        d = intent_ai._to_draft({"type": "lock_screen", "target": {}, "confidence": 99})
        self.assertEqual(d["confidence"], 1.0)


class TestAnalyze(unittest.TestCase):
    def test_too_short_skips(self):
        with mock.patch.object(intent_ai, "_rpc_prompt") as rpc:
            self.assertIsNone(intent_ai.analyze("嗯", {"ai": {"enabled": True}}))
            rpc.assert_not_called()

    def test_disabled_skips(self):
        with mock.patch.object(intent_ai, "_rpc_prompt") as rpc:
            self.assertIsNone(intent_ai.analyze("打开浏览器", {"ai": {"enabled": False}}))
            rpc.assert_not_called()

    def test_returns_valid_draft(self):
        with mock.patch.object(intent_ai, "_daemon_request", return_value=None), \
             mock.patch.object(intent_ai, "_rpc_prompt", return_value='{"type":"open_app","target":{"name":"browser"},"confidence":0.8}'):
            d = intent_ai.analyze("please open the browser", {"ai": {"enabled": True, "timeout_secs": 30}})
            self.assertEqual(d["type"], "open_app")
            self.assertEqual(d["source"], "future_llm")

    def test_model_garbage_returns_none(self):
        with mock.patch.object(intent_ai, "_daemon_request", return_value=None), \
             mock.patch.object(intent_ai, "_rpc_prompt", return_value="I don't know"):
            self.assertIsNone(intent_ai.analyze("whatever", {"ai": {"enabled": True, "timeout_secs": 30}}))

    def test_daemon_roundtrip_via_socket(self):
        """daemon_main + _daemon_request over a real Unix socket (pi stubbed)."""
        import threading
        import time
        proc_stub = mock.Mock()
        proc_stub.poll.return_value = None  # daemon thinks pi is alive
        sock_path = os.path.join(tempfile.mkdtemp(), "ai.sock")
        with mock.patch.object(intent_ai, "_spawn_pi", return_value=proc_stub), \
             mock.patch.object(intent_ai, "_send"), \
             mock.patch.object(intent_ai, "_sock_path", return_value=sock_path), \
             mock.patch.object(intent_ai, "_prompt_on", return_value='{"type":"open_folder","target":{"name":"downloads"},"confidence":0.8}'):
            cfg = {"ai": {"enabled": True, "timeout_secs": 30, "idle_secs": 30}}
            thread = threading.Thread(target=intent_ai.daemon_main, args=(cfg,), daemon=True)
            thread.start()
            for _ in range(100):
                if os.path.exists(sock_path):
                    break
                time.sleep(0.05)
            draft = intent_ai._daemon_request("打开下载文件夹", cfg)
            self.assertIsNotNone(draft)
            self.assertEqual(draft["type"], "open_folder")
            self.assertEqual(draft["source"], "future_llm")


class TestCliAiPath(unittest.TestCase):
    """CLI runs the AI layer only when the rule matcher misses, then the same gate."""

    def setUp(self):
        import tempfile
        import state as state_mod
        self.tmp = tempfile.mkdtemp()
        self._rt = state_mod._RUNTIME_BASE
        state_mod._RUNTIME_BASE = self.tmp
        import config.memory as memory_mod
        self._mem_orig = memory_mod.settings.USER_CONFIG_DIR
        memory_mod.settings.USER_CONFIG_DIR = self.tmp
        import cli as cli_mod
        self.cli = cli_mod
        self.patchers = [
            mock.patch.object(self.cli, "Recorder"),
            mock.patch.object(self.cli, "raw_duration_seconds", return_value=1.0),
            mock.patch.object(self.cli.stt, "transcribe", return_value="please open the browser"),
        ]
        for p in self.patchers:
            p.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import state as state_mod
        import config.memory as memory_mod
        for p in self.patchers:
            p.stop()
        state_mod._RUNTIME_BASE = self._rt
        memory_mod.settings.USER_CONFIG_DIR = self._mem_orig

    def test_rule_hit_does_not_call_ai(self):
        self.cli.stt.transcribe.return_value = "打开浏览器"  # rule matcher handles this
        with mock.patch.object(self.cli.intent_ai, "analyze") as ai, \
                mock.patch.object(self.cli.executor, "execute", return_value=(True, "ok")) as exec_:
            self.cli.cmd_record_start()
            self.cli.cmd_record_stop()
            ai.assert_not_called()
            exec_.assert_called_once()
            self.assertEqual(exec_.call_args[0][0]["source"], "rule")
        self.assertEqual(self.cli.load_state()["phase"], "result")

    def test_rule_miss_calls_ai_and_awaits_confirm(self):
        self.cli.stt.transcribe.return_value = "bring up the download folder"  # genuine rule miss
        with mock.patch.object(self.cli.intent_ai, "analyze", return_value={
            "type": "lock_screen", "target": {}, "confidence": 0.8,
            "source": "future_llm", "risk": "low",
        }), mock.patch.object(self.cli.executor, "execute") as exec_:
            self.cli.cmd_record_start()
            self.cli.cmd_record_stop()
            exec_.assert_not_called()  # confirm_required (lock) waits for explicit confirm
        st = self.cli.load_state()
        self.assertEqual(st["phase"], "awaiting_confirm")
        self.assertEqual(st["action"]["source"], "future_llm")
        self.assertEqual(st["action"]["type"], "lock_screen")

    def test_ai_low_risk_auto_executes(self):
        self.cli.stt.transcribe.return_value = "bring up the download folder"
        with mock.patch.object(self.cli.intent_ai, "analyze", return_value={
            "type": "open_folder", "raw_target": "downloads", "confidence": 0.8,
            "source": "future_llm", "risk": "low",
        }), mock.patch.object(self.cli.executor, "execute", return_value=(True, "ok")) as exec_:
            self.cli.cmd_record_start()
            self.cli.cmd_record_stop()
            exec_.assert_called_once()
            called = exec_.call_args[0][0]
            self.assertEqual(called["source"], "future_llm")
        st = self.cli.load_state()
        self.assertEqual(st["phase"], "result")
        self.assertTrue(st["result"]["ok"])


if __name__ == "__main__":
    unittest.main()
