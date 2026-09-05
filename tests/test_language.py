"""Unit tests: 中英文版本切换 (zh/en language switch).

Covers:
  - settings.ui_language / effective_ui_language resolution (zh|en|auto)
  - `lang` CLI command keeps ui.language + stt.language in sync
  - state.write_state injects ui_lang (effective) + ui_lang_setting (raw)
  - English rule coverage additions (volume max/min, gaps, brightness, open the X)
  - resolver.describe bilingual (en default / zh)
  - chat replies: auto-detect per utterance + forced zh/en by ui.language
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from intent import chat as chat_layer
from intent import rules
from resolver import lookup


class TestUiLanguageSetting(unittest.TestCase):
    def test_default_zh(self):
        self.assertEqual(settings.ui_language(settings.load_config()), "zh")
        self.assertEqual(settings.effective_ui_language(settings.load_config()), "zh")

    def test_forced_values(self):
        self.assertEqual(settings.ui_language({"ui": {"language": "en"}}), "en")
        self.assertEqual(settings.effective_ui_language({"ui": {"language": "en"}}), "en")
        self.assertEqual(settings.effective_ui_language({"ui": {"language": "zh"}}), "zh")
        self.assertEqual(settings.ui_language({"ui": {"language": "garbage"}}), "zh")

    def test_auto_resolves_via_locale(self):
        lang = settings.effective_ui_language({"ui": {"language": "auto"}})
        self.assertIn(lang, ("zh", "en"))
        self.assertEqual(settings.ui_language({"ui": {"language": "auto"}}), "auto")


class TestLangCli(unittest.TestCase):
    """`lang zh|en|auto` persists BOTH ui.language and stt.language."""

    def setUp(self):
        from cli import cmd_lang
        self.cmd_lang = cmd_lang
        self.tmp = tempfile.mkdtemp()
        self._orig = settings.USER_CONFIG_DIR
        settings.USER_CONFIG_DIR = self.tmp

    def tearDown(self):
        settings.USER_CONFIG_DIR = self._orig

    def test_lang_syncs_ui_and_stt(self):
        self.assertEqual(self.cmd_lang(["en"]), 0)
        cfg = settings.load_config()
        self.assertEqual(cfg["ui"]["language"], "en")
        self.assertEqual(cfg["stt"]["language"], "en")

        self.assertEqual(self.cmd_lang(["auto"]), 0)
        cfg = settings.load_config()
        self.assertEqual(cfg["ui"]["language"], "auto")
        self.assertEqual(cfg["stt"]["language"], "auto")

    def test_lang_rejects_unknown(self):
        from cli import _err
        # _err returns 2 for unknown; the subcommand itself rejects
        self.assertEqual(self.cmd_lang(["fr"]), 2)


class TestStateWritesUiLang(unittest.TestCase):
    def setUp(self):
        import state as state_mod
        self.state = state_mod
        self.tmp = tempfile.mkdtemp()
        self._orig = settings.USER_CONFIG_DIR
        settings.USER_CONFIG_DIR = self.tmp

    def tearDown(self):
        settings.USER_CONFIG_DIR = self._orig

    def test_state_carries_effective_and_raw(self):
        st = self.state.load_state()
        self.state.write_state(st)
        st2 = self.state.load_state()
        self.assertIn(st2.get("ui_lang"), ("zh", "en"))
        self.assertIn(st2.get("ui_lang_setting"), ("zh", "en", "auto"))


class TestEnglishRuleCoverage(unittest.TestCase):
    """English phrases for actions that only had zh (or weak en) rules."""

    def test_volume_extremes_en(self):
        self.assertEqual(rules.parse("set volume to maximum")["type"], "volume_up")
        self.assertEqual(rules.parse("volume to max")["type"], "volume_up")
        self.assertEqual(rules.parse("turn the volume down all the way")["type"], "volume_down")
        self.assertEqual(rules.parse("volume to minimum")["type"], "volume_down")

    def test_gaps_en(self):
        self.assertEqual(rules.parse("toggle window gaps")["type"], "toggle_window_gaps")
        self.assertEqual(rules.parse("turn off window gaps")["type"], "toggle_window_gaps")

    def test_brightness_en(self):
        self.assertEqual(rules.parse("increase brightness")["type"], "brightness_up")
        self.assertEqual(rules.parse("make the screen brighter")["type"], "brightness_up")
        self.assertEqual(rules.parse("decrease brightness")["type"], "brightness_down")
        self.assertEqual(rules.parse("dim the screen")["type"], "brightness_down")

    def test_open_the_en(self):
        self.assertEqual(rules.parse("open the browser")["type"], "open_app")

    def test_existing_en_still_works(self):
        self.assertEqual(rules.parse("close window")["type"], "close_active_window")
        self.assertEqual(rules.parse("lock screen")["type"], "lock_screen")
        self.assertEqual(rules.parse("take a screenshot")["type"], "take_screenshot")
        self.assertEqual(rules.parse("go to workspace 4")["type"], "switch_workspace")


class TestDescribeBilingual(unittest.TestCase):
    def test_en_default(self):
        a = {"type": "open_app", "target": {"name": "browser"}}
        self.assertEqual(lookup.describe(a), "Open app: browser")

    def test_zh(self):
        a = {"type": "open_app", "target": {"name": "browser"}}
        self.assertEqual(lookup.describe(a, lang="zh"), "打开应用: browser")
        a2 = {"type": "switch_workspace", "target": {"id": 3}}
        self.assertEqual(lookup.describe(a2, lang="zh"), "切换到工作区 3")
        a3 = {"type": "set_reminder", "target": {"minutes": 10, "message": "喝水"}}
        self.assertEqual(lookup.describe(a3, lang="zh"), "设置提醒: 10 分钟 (喝水)")


class TestChatLanguageSwitch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = settings.USER_CONFIG_DIR
        settings.USER_CONFIG_DIR = self.tmp

    def tearDown(self):
        settings.USER_CONFIG_DIR = self._orig

    def _cfg(self, lang):
        cfg = settings.load_config()
        cfg["ui"] = {"language": lang}
        return cfg

    def test_auto_detect_per_utterance(self):
        r = chat_layer.precheck("what time is it", self._cfg("auto"))
        self.assertTrue(r["reply"].startswith("It's "))
        r = chat_layer.precheck("现在几点", self._cfg("auto"))
        self.assertTrue(r["reply"].startswith("现在是"))

    def test_forced_en_replies_english(self):
        r = chat_layer.precheck("记住我喜欢喝咖啡", self._cfg("en"))
        self.assertTrue(r["reply"].startswith("Got it"))
        r = chat_layer.precheck("好的谢谢", self._cfg("en"))
        self.assertTrue(r["reply"].startswith("Anytime"))

    def test_forced_zh_replies_chinese(self):
        r = chat_layer.precheck("remember I like coffee", self._cfg("zh"))
        self.assertTrue(r["reply"].startswith("记住了"))
        r = chat_layer.precheck("thanks", self._cfg("zh"))
        self.assertTrue(r["reply"].startswith("不客气"))


if __name__ == "__main__":
    unittest.main()
