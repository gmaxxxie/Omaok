"""Unit tests: conversation mode (chatmode) + VAD auto-stop + streaming support.

Covers:
  - EnergyVAD: wait -> speech -> done after trailing silence
  - explicit commands: 开始对话 / 退出对话模式 / 结束对话
  - chat layer returns a chatmode on/off directive
  - cli chatmode on/off lifecycle (state flag + daemon pid)
  - record auto no-ops when chatmode is off
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import chatmem, settings
from recorder import EnergyVAD


class TestEnergyVAD(unittest.TestCase):
    def _loud(self):
        return b"\xff\x7f" * EnergyVAD.FRAME

    def _quiet(self):
        return b"\x00\x00" * EnergyVAD.FRAME

    def test_wait_then_speech_then_done(self):
        vad = EnergyVAD()
        for _ in range(10):
            vad.feed(self._quiet())          # initial silence (wait state)
        self.assertEqual(vad.state, "wait")
        for _ in range(6):
            vad.feed(self._loud())           # speech
        self.assertEqual(vad.state, "speech")
        for _ in range(vad.silence_frames + 1):
            vad.feed(self._quiet())          # trailing silence
        self.assertTrue(vad.done)

    def test_never_stops_before_speech(self):
        vad = EnergyVAD()
        for _ in range(200):
            vad.feed(self._quiet())          # long quiet, no speech yet
        self.assertFalse(vad.done)           # must keep waiting (no false auto-stop)
        self.assertEqual(vad.state, "wait")


class TestChatModeCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = settings.USER_CONFIG_DIR
        settings.USER_CONFIG_DIR = self.tmp

    def tearDown(self):
        settings.USER_CONFIG_DIR = self._orig

    def test_explicit_commands(self):
        self.assertEqual(chatmem.explicit_command("开始对话")[0], "chatmode_on")
        self.assertEqual(chatmem.explicit_command("进入对话模式")[0], "chatmode_on")
        self.assertEqual(chatmem.explicit_command("退出对话模式")[0], "chatmode_off")
        self.assertEqual(chatmem.explicit_command("结束对话")[0], "end")

    def test_chat_layer_returns_mode_directive(self):
        from intent import chat as chat_layer
        with mock.patch("intent.ai.chat_analyze") as ca:
            r = chat_layer.process("开始对话", {"ai": {"enabled": True}})
            ca.assert_not_called()
        self.assertEqual(r["kind"], "answer")
        self.assertEqual(r["mode"], "on")

    def test_cli_chatmode_on_off(self):
        import cli as cli_mod
        from state import load_state
        fake_proc = mock.Mock()
        fake_proc.pid = 424242
        with mock.patch("cli.subprocess.Popen", return_value=fake_proc):
            rc = cli_mod.cmd_chatmode(["on"])
        self.assertEqual(rc, 0)
        self.assertTrue(load_state().get("chatmode"))
        pid_file = os.path.join(self.tmp, "chatmode.pid")
        self.assertTrue(os.path.exists(pid_file))
        with mock.patch("cli.subprocess.Popen") as pop:
            rc = cli_mod.cmd_chatmode(["off"])
        self.assertEqual(rc, 0)
        self.assertFalse(load_state().get("chatmode"))
        self.assertFalse(os.path.exists(pid_file))

    def test_record_auto_noops_when_chatmode_off(self):
        import cli as cli_mod
        from state import load_state
        st = load_state()
        st["chatmode"] = False
        cli_mod.write_state(st)
        with mock.patch.object(cli_mod, "_make_auto_recorder") as ar:
            rc = cli_mod.cmd_record_auto()
        self.assertEqual(rc, 0)
        ar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
