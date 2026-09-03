"""Unit tests: intent parsing (zh/en), resolver, policy.

Run:  python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from intent import rules
from policy import policy
from resolver import lookup

CFG = settings.load_config()
ALIASES = settings.load_aliases()


class TestIntent(unittest.TestCase):
    def test_close_window(self):
        self.assertEqual(rules.parse("关闭窗口")["type"], "close_active_window")
        self.assertEqual(rules.parse("close window")["type"], "close_active_window")

    def test_lock_screen(self):
        self.assertEqual(rules.parse("锁屏")["type"], "lock_screen")
        self.assertEqual(rules.parse("lock screen")["type"], "lock_screen")

    def test_screenshot(self):
        self.assertEqual(rules.parse("截图")["type"], "take_screenshot")
        self.assertEqual(rules.parse("take a screenshot")["type"], "take_screenshot")

    def test_fullscreen(self):
        self.assertEqual(rules.parse("全屏")["type"], "toggle_fullscreen")
        self.assertEqual(rules.parse("fullscreen")["type"], "toggle_fullscreen")

    def test_workspace(self):
        a = rules.parse("切到工作区3")
        self.assertEqual(a["type"], "switch_workspace")
        self.assertEqual(a["raw_target"], "3")
        b = rules.parse("go to workspace 4")
        self.assertEqual(b["type"], "switch_workspace")
        self.assertEqual(b["raw_target"], "4")

    def test_move_window(self):
        a = rules.parse("把当前窗口移到工作区2")
        self.assertEqual(a["type"], "move_active_window_to_workspace")
        self.assertEqual(a["raw_target"], "2")

    def test_open_folder_and_app(self):
        self.assertEqual(rules.parse("打开下载文件夹")["type"], "open_folder")
        self.assertEqual(rules.parse("open the downloads folder")["type"], "open_folder")
        self.assertEqual(rules.parse("打开浏览器")["type"], "open_app")
        self.assertEqual(rules.parse("open browser")["type"], "open_app")

    def test_focus_app(self):
        self.assertEqual(rules.parse("聚焦终端")["type"], "focus_app")
        self.assertEqual(rules.parse("focus terminal")["type"], "focus_app")

    def test_unclear(self):
        self.assertIsNone(rules.parse("今天天气怎么样"))
        self.assertIsNone(rules.parse(""))


class TestResolver(unittest.TestCase):
    def _resolve(self, phrase):
        draft = rules.parse(phrase)
        if draft is None:
            return None
        return lookup.resolve_action(dict(draft), CFG, ALIASES)

    def test_open_app_alias(self):
        a = self._resolve("打开浏览器")
        self.assertEqual(a["type"], "open_app")
        self.assertEqual(a["target"]["command"], "chromium")

    def test_open_app_named(self):
        a = self._resolve("打开 obsidian")
        self.assertEqual(a["type"], "open_app")
        self.assertEqual(a["target"]["command"], "obsidian")

    def test_folder_alias(self):
        a = self._resolve("打开下载文件夹")
        self.assertEqual(a["type"], "open_folder")
        self.assertTrue(a["target"]["path"].endswith("Downloads"))

    def test_project_folder(self):
        a = self._resolve("打开项目")
        self.assertEqual(a["type"], "open_folder")
        self.assertTrue(a["target"]["path"].endswith("项目"))

    def test_workspace_resolve(self):
        a = self._resolve("切到工作区3")
        self.assertEqual(a["type"], "switch_workspace")
        self.assertEqual(a["target"]["id"], 3)

    def test_workspace_out_of_range(self):
        a = self._resolve("切到工作区99")
        self.assertIsNone(a)

    def test_unknown_app_fails(self):
        a = self._resolve("打开不存在的应用xyz")
        self.assertIsNone(a)

    def test_path_traversal_blocked(self):
        # A literal path outside the roots must not resolve.
        draft = rules.parse("打开文件夹 /etc/passwd")
        a = lookup.resolve_action(dict(draft), CFG, ALIASES) if draft else None
        self.assertIsNone(a)


class TestPolicy(unittest.TestCase):
    def test_allowlist(self):
        for t in rules.ALLOWED_TYPES:
            self.assertTrue(policy.allowed(t))
        self.assertFalse(policy.allowed("exec_shell"))
        self.assertFalse(policy.allowed("rm_rf"))

    def test_risk_classification(self):
        self.assertEqual(policy.classify({"type": "lock_screen", "confidence": 0.9}, CFG)["risk"], "confirm_required")
        self.assertEqual(policy.classify({"type": "close_active_window", "confidence": 0.9}, CFG)["risk"], "confirm_required")
        self.assertEqual(policy.classify({"type": "toggle_fullscreen", "confidence": 0.9}, CFG)["risk"], "confirm_required")
        self.assertEqual(policy.classify({"type": "open_app", "confidence": 0.9}, CFG)["risk"], "low")
        # low confidence forces confirm_required
        self.assertEqual(policy.classify({"type": "open_app", "confidence": 0.3}, CFG)["risk"], "confirm_required")

    def test_requires_confirm_default(self):
        low = {"type": "open_app", "confidence": 0.9, "risk": "low"}
        self.assertFalse(policy.requires_confirm(low, CFG))  # low risk auto-executes
        high = {"type": "lock_screen", "confidence": 0.9, "risk": "confirm_required"}
        self.assertTrue(policy.requires_confirm(high, CFG))  # high risk still confirms

    def test_verdict_denies_unknown(self):
        v = policy.verdict({"type": "evil", "confidence": 0.9}, CFG)
        self.assertFalse(v["allowed"])
        self.assertIn("not allowed", v["reason"])


if __name__ == "__main__":
    unittest.main()
