"""Tests for the response-speed work (2026-09-06):

- _OPEN_V2_RE fixes: polite prefixes / verb-object splits must no longer fall
  through to the slow AI layer (帮我打开X / 打开一下X / 能不能打开X ...).
- New fast L1 window/system rules (maximize / close all / tiled fullscreen /
  gaps / transparency / wake / weather / OCR / QR) resolve at ~ms.
- Offline local-fact answers (time / date / weekday / battery) and the chat
  precheck that runs BEFORE the AI intent layer so persona / explicit memory /
  closing remarks / local facts never spend a model call.

Run:  python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from intent import rules
from intent import chat as chat_layer
from executor import actions


CFG = settings.load_config()


class TestOpenV2Fixes(unittest.TestCase):
    """Polite-prefix / verb-object phrasing must resolve to the right app name."""

    def test_prefix_never_captured_as_app(self):
        cases = [
            ("帮我打开计算器", "计算器"),
            ("帮我打开微信", "微信"),
            ("帮我打开浏览器", "浏览器"),
            ("请打开计算器", "计算器"),
            ("能不能打开浏览器", "浏览器"),
            ("可以打开浏览器吗", "浏览器"),
            ("麻烦打开一下浏览器", "浏览器"),
            ("帮我打开微信吧", "微信"),
        ]
        for phrase, target in cases:
            d = rules.parse(phrase)
            self.assertIsNotNone(d, phrase)
            self.assertEqual(d["type"], "open_app", phrase)
            self.assertEqual(d["raw_target"], target, phrase)

    def test_verb_object_not_mis_split(self):
        # "打开一下浏览器" must be verb+object -> 浏览器, never "打".
        for phrase in ["打开一下浏览器", "打开一下微信", "我想打开微信", "打开微信吧"]:
            d = rules.parse(phrase)
            self.assertEqual(d["type"], "open_app", phrase)
            self.assertIn(d["raw_target"], ("浏览器", "微信"), phrase)

    def test_ba_name_verb_still_works(self):
        for phrase, target in [("把微信打开", "微信"), ("帮我把微信打开", "微信"),
                               ("我想把浏览器打开", "浏览器"), ("微信打开一下", "微信"),
                               ("把微信打开一下", "微信"), ("开个浏览器", "浏览器")]:
            d = rules.parse(phrase)
            self.assertEqual(d["type"], "open_app", phrase)
            self.assertEqual(d["raw_target"], target, phrase)

    def test_yong_variants(self):
        for phrase, target in [("我想用微信", "微信"), ("我要用微信", "微信"),
                               ("我想用一下微信", "微信"), ("帮我用一下计算器", "计算器")]:
            d = rules.parse(phrase)
            self.assertEqual(d["type"], "open_app", phrase)
            self.assertEqual(d["raw_target"], target, phrase)

    def test_chat_phrases_still_unparsed(self):
        for phrase in ["帮我写首诗", "讲个笑话", "今天天气怎么样", "你是谁", "谢谢你"]:
            self.assertIsNone(rules.parse(phrase), phrase)


class TestNewWindowSystemRules(unittest.TestCase):
    def test_maximize(self):
        for phrase in ["最大化窗口", "窗口最大化", "放大窗口", "把窗口最大化"]:
            d = rules.parse(phrase)
            self.assertEqual(d["type"], "maximize_window", phrase)

    def test_close_all(self):
        for phrase in ["关闭所有窗口", "把所有窗口关了", "全部窗口都关掉", "close all windows"]:
            d = rules.parse(phrase)
            self.assertEqual(d["type"], "close_all_windows", phrase)

    def test_window_helpers(self):
        self.assertEqual(rules.parse("平铺全屏")["type"], "toggle_tiled_fullscreen")
        self.assertEqual(rules.parse("窗口间距调一下")["type"], "toggle_window_gaps")
        self.assertEqual(rules.parse("透明窗口")["type"], "toggle_window_transparency")
        self.assertEqual(rules.parse("把窗口变透明")["type"], "toggle_window_transparency")

    def test_system_helpers(self):
        self.assertEqual(rules.parse("唤醒屏幕")["type"], "wake_screen")
        self.assertEqual(rules.parse("打开天气面板")["type"], "toggle_weather")
        self.assertEqual(rules.parse("提取屏幕文字")["type"], "extract_screen_text")
        self.assertEqual(rules.parse("扫码")["type"], "scan_qr")
        self.assertEqual(rules.parse("扫个二维码")["type"], "scan_qr")

    def test_new_variants(self):
        self.assertEqual(rules.parse("把音量调到最大")["type"], "volume_up")
        self.assertEqual(rules.parse("把音量调到最小")["type"], "volume_down")
        self.assertEqual(rules.parse("把音乐关了")["type"], "play_pause_media")
        self.assertEqual(rules.parse("别吵了")["type"], "toggle_dnd")
        self.assertEqual(rules.parse("安静一下")["type"], "toggle_dnd")

    def test_executors(self):
        with mock.patch.object(actions, "_hypr_dispatch", return_value=(True, "ok")) as hy:
            ok, _ = actions.execute({"type": "maximize_window", "target": {}}, CFG)
            self.assertIn("maximized", hy.call_args[0][0])
        for t, argv in [
            ("close_all_windows", ["omarchy", "hyprland", "window", "close", "all"]),
            ("toggle_tiled_fullscreen", ["omarchy", "hyprland", "window", "tiled", "fullscreen", "toggle"]),
            ("toggle_window_gaps", ["omarchy", "hyprland", "window", "gaps", "toggle"]),
            ("toggle_window_transparency", ["omarchy", "hyprland", "window", "transparency", "toggle"]),
            ("wake_screen", ["omarchy", "system", "wake"]),
            ("toggle_weather", ["omarchy", "notification", "weather"]),
            ("extract_screen_text", ["omarchy", "capture", "text"]),
            ("scan_qr", ["omarchy", "capture", "qr"]),
        ]:
            with mock.patch.object(actions, "_run", return_value=(True, "ok")) as run:
                ok, _ = actions.execute({"type": t, "target": {}}, CFG)
                self.assertEqual(run.call_args[0][0], argv, t)

    def test_policy_close_all_confirm(self):
        from policy import policy
        a = policy.classify({"type": "close_all_windows", "confidence": 0.9}, CFG)
        self.assertEqual(a["risk"], "confirm_required")


class TestLocalFactAnswers(unittest.TestCase):
    def test_time(self):
        r = chat_layer.local_fact_answer("现在几点")
        self.assertIsNotNone(r)
        self.assertEqual(r[1], "local")
        self.assertIn("点", r[0])
        r = chat_layer.local_fact_answer("what time is it")
        self.assertIn(":", r[0])

    def test_date_weekday(self):
        r = chat_layer.local_fact_answer("今天几号")
        self.assertIn("月", r[0])
        r = chat_layer.local_fact_answer("今天星期几")
        self.assertIn("星期", r[0])
        self.assertEqual(chat_layer.local_fact_answer("今天是星期几")[1], "local")

    @mock.patch("intent.chat.subprocess.run")
    def test_battery(self, run):
        proc = mock.MagicMock()
        proc.stdout = "Battery 80%  ·  Holding at 75-80%  ·  0W / 49Wh"
        run.return_value = proc
        r = chat_layer.local_fact_answer("电量多少")
        self.assertEqual(r, ("电量还有 80%。", "local"))
        run.assert_called_once_with(["omarchy", "battery", "status"],
                                    capture_output=True, text=True, timeout=5)

    def test_non_fact_returns_none(self):
        for p in ["打开浏览器", "你是谁", "帮我写首诗", "今天天气怎么样", ""]:
            self.assertIsNone(chat_layer.local_fact_answer(p), p)

    def test_command_phrases_not_answered_as_facts(self):
        # "打开设置" is a command, not a time/date question -> no local fact.
        for p in ["打开设置", "打开日期"]:
            self.assertIsNone(chat_layer.local_fact_answer(p), p)


class TestUnifiedSinglePass(unittest.TestCase):
    """L3+L4 merged into one model call: command / answer / defer / none."""

    def _cfg(self, **ai):
        return {"ai": {"enabled": True, **ai}, "chat": {"enabled": True}}

    def test_parse_command(self):
        from intent import ai
        r = ai._unified_parse('{"kind": "command", "action": {"type": "open_app", "target": {"name": "微信"}, "confidence": 0.9}}')
        self.assertEqual(r["kind"], "command")
        self.assertEqual(r["draft"]["type"], "open_app")
        self.assertEqual(r["draft"]["raw_target"], "微信")

    def test_parse_answer_defer_none(self):
        from intent import ai
        r = ai._unified_parse('{"kind": "answer", "reply": "巴黎。", "end": false}')
        self.assertEqual((r["kind"], r["reply"], r["end"]), ("answer", "巴黎。", False))
        r = ai._unified_parse('{"kind": "defer", "reply": "深入研究量子计算", "end": true}')
        self.assertEqual((r["kind"], r["reply"], r["end"]), ("defer", "深入研究量子计算", True))
        r = ai._unified_parse('{"kind": "none"}')
        self.assertEqual(r["kind"], "none")

    def test_parse_rejects_unknown_or_invalid(self):
        from intent import ai
        self.assertIsNone(ai._unified_parse("not json"))
        self.assertIsNone(ai._unified_parse('{"kind": "command", "action": {"type": "nope"}}'))
        self.assertIsNone(ai._unified_parse('{"kind": "command"}'))
        self.assertIsNone(ai._unified_parse('{"kind": "answer", "reply": ""}'))
        self.assertIsNone(ai._unified_parse('{"kind": "bogus"}'))

    def test_unified_entry_mocks_model(self):
        from intent import ai
        with mock.patch.object(ai, "_backend_text", return_value='{"kind": "command", "action": {"type": "volume_up", "target": {}, "confidence": 0.9}}') as bt:
            r = ai.unified("把声音调大", self._cfg())
        self.assertEqual(r["kind"], "command")
        self.assertEqual(r["draft"]["type"], "volume_up")
        self.assertIn("把声音调大", bt.call_args[0][0])  # prompt carries the transcript

    def test_unified_short_or_disabled(self):
        from intent import ai
        self.assertIsNone(ai.unified(" ", self._cfg()))
        self.assertIsNone(ai.unified("hi", self._cfg(enabled=False)))

    def test_unified_model_failure_is_none(self):
        from intent import ai
        with mock.patch.object(ai, "_backend_text", side_effect=ai.AIError("boom")):
            self.assertIsNone(ai.unified("hi there", self._cfg()))

    def test_cli_command_branch_awaits_confirm(self):
        # A unified command draft still passes the full resolver + policy gate.
        import cli as cli_mod
        import state as state_mod
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}, clear=False):
                with mock.patch.object(cli_mod, "raw_duration_seconds", return_value=1.0):
                    with mock.patch.object(cli_mod.stt, "transcribe", return_value="please lock the workstation now"):
                        with mock.patch.object(cli_mod.intent_rules, "parse", return_value=None):
                            with mock.patch.object(cli_mod.intent_ai, "unified", return_value={
                                "kind": "command", "draft": {
                                    "type": "lock_screen", "target": {}, "confidence": 0.8,
                                    "source": "future_llm", "risk": "low",
                                }}), mock.patch.object(cli_mod.executor, "execute") as exec_:
                                cli_mod.cmd_record_start()
                                cli_mod.cmd_record_stop()
                                exec_.assert_not_called()
                            st = state_mod.load_state()
        self.assertEqual(st["phase"], "awaiting_confirm")
        self.assertEqual(st["action"]["type"], "lock_screen")

    def test_cli_answer_branch_never_executes(self):
        import cli as cli_mod
        import state as state_mod
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}, clear=False):
                with mock.patch.object(cli_mod, "raw_duration_seconds", return_value=1.0):
                    with mock.patch.object(cli_mod.stt, "transcribe", return_value="法国的首都"):
                        with mock.patch.object(cli_mod.intent_rules, "parse", return_value=None):
                            with mock.patch.object(cli_mod.intent_ai, "unified", return_value={
                                "kind": "answer", "reply": "巴黎。", "end": False}), \
                                    mock.patch.object(cli_mod.executor, "execute") as exec_:
                                cli_mod.cmd_record_start()
                                cli_mod.cmd_record_stop()
                                exec_.assert_not_called()
                            st = state_mod.load_state()
        self.assertEqual(st["phase"], "chat_reply")
        self.assertEqual(st["chat_reply"], "巴黎。")
        self.assertIsNone(st["action"])


class TestPrecheckOffline(unittest.TestCase):
    def test_persona_offline(self):
        with mock.patch.object(chat_layer, "_note") as note:
            r = chat_layer.precheck("你是谁", CFG)
        self.assertEqual(r["via"], "persona")
        note.assert_called_once()

    def test_explicit_memory_offline(self):
        r = chat_layer.precheck("记住我喜欢喝咖啡", CFG)
        self.assertEqual(r["via"], "memory")
        self.assertIn("记住了", r["reply"])

    def test_local_fact_offline(self):
        with mock.patch("intent.chat.local_fact_answer", return_value=("现在中午12点。", "local")):
            r = chat_layer.precheck("现在几点", CFG)
        self.assertEqual(r["via"], "local")

    def test_not_offline(self):
        for p in ["讲个笑话", "帮我写首诗", "今天天气怎么样"]:
            self.assertIsNone(chat_layer.precheck(p, CFG), p)


if __name__ == "__main__":
    unittest.main()
