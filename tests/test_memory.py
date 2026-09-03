"""Operation-memory tests: learned voice->action cache speeds up repeats."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import memory as memory_mod


def _isolated_memory(self):
    """Point memory at a temp dir so real user data is untouched."""
    self.tmp = tempfile.mkdtemp()
    self._orig = memory_mod.settings.USER_CONFIG_DIR
    memory_mod.settings.USER_CONFIG_DIR = self.tmp
    self.addCleanup(_restore, self)

def _restore(self):
    memory_mod.settings.USER_CONFIG_DIR = self._orig


class TestMemory(unittest.TestCase):
    def setUp(self):
        _isolated_memory(self)

    def test_remember_and_exact_lookup(self):
        memory_mod.remember({"type": "open_folder", "raw_target": "下载", "source": "future_llm"}, "调出下载文件夹", 0.95)
        hit = memory_mod.lookup("调出下载文件夹")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["draft"]["type"], "open_folder")

    def test_fuzzy_lookup_close_phrasing(self):
        memory_mod.remember({"type": "open_folder", "raw_target": "下载", "source": "future_llm"}, "调出下载文件夹", 0.95)
        # near-identical phrasing replays; unrelated text does not
        self.assertIsNotNone(memory_mod.lookup("调出下载的文件夹"))
        self.assertIsNone(memory_mod.lookup("今天天气怎么样"))

    def test_dedupe_by_norm(self):
        memory_mod.remember({"type": "open_folder", "raw_target": "a"}, "打开下载文件夹", 0.9)
        memory_mod.remember({"type": "open_folder", "raw_target": "b"}, "打开下载文件夹", 0.9)
        entries = memory_mod.load_memory()
        self.assertEqual(len([e for e in entries if e["norm"] == memory_mod._norm("打开下载文件夹")]), 1)

    def test_rejects_non_allowed_type(self):
        memory_mod.remember({"type": "exec_shell"}, "do anything", 0.9)
        self.assertEqual(memory_mod.load_memory(), [])


class TestCliMemory(unittest.TestCase):
    """Memory hit skips the AI layer; successful AI interpretations are learned."""

    def setUp(self):
        _isolated_memory(self)
        import tempfile as _tf
        import state as state_mod
        self._rt = state_mod._RUNTIME_BASE
        state_mod._RUNTIME_BASE = _tf.mkdtemp()
        import cli as cli_mod
        self.cli = cli_mod
        self.patchers = [
            mock.patch.object(self.cli, "Recorder"),
            mock.patch.object(self.cli, "raw_duration_seconds", return_value=1.0),
            mock.patch.object(self.cli.stt, "transcribe", return_value="调出下载文件夹"),
            mock.patch.object(self.cli.executor, "execute", return_value=(True, "ok")),
        ]
        for p in self.patchers:
            p.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        import state as state_mod
        for p in self.patchers:
            p.stop()
        state_mod._RUNTIME_BASE = self._rt

    def test_memory_hit_skips_ai(self):
        # seed memory with the interpretation, then the same phrase must not call AI
        memory_mod.remember({"type": "open_folder", "raw_target": "下载", "source": "future_llm"}, "调出下载文件夹", 0.95)
        with mock.patch.object(self.cli.intent_ai, "analyze") as ai, \
             mock.patch.object(self.cli.intent_rules, "parse", return_value=None):  # rules would miss anyway
            self.cli.cmd_record_start()
            self.cli.cmd_record_stop()
            ai.assert_not_called()
        st = self.cli.load_state()
        self.assertEqual(st["phase"], "result")
        self.assertTrue(st["result"]["ok"])

    def test_ai_success_is_remembered(self):
        # rule miss + AI success -> interpretation is written to memory
        self.cli.stt.transcribe.return_value = "bring up the download folder"  # genuine rule miss
        with mock.patch.object(self.cli.intent_rules, "parse", return_value=None), \
             mock.patch.object(self.cli.intent_ai, "analyze", return_value={
                 "type": "open_folder", "raw_target": "下载", "confidence": 0.9, "source": "future_llm", "risk": "low",
             }):
            self.cli.cmd_record_start()
            self.cli.cmd_record_stop()
        self.assertEqual(len(memory_mod.load_memory()), 1)
        self.assertEqual(memory_mod.load_memory()[0]["draft"]["type"], "open_folder")


if __name__ == "__main__":
    unittest.main()
