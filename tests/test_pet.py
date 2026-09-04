"""Tests for the desktop-pet (小飞马) config + CLI subcommand.

The pet is pure state persistence (pet.json) — no audio, no execution — so
these tests cover the merge/save semantics and the CLI surface the popover
and the pet overlay call.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import pet as pet_mod
import cli as cli_mod


class TestPetConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Redirect the user config dir (pet.json lives there) into a temp dir.
        self._cfg_dir = pet_mod.settings.USER_CONFIG_DIR
        pet_mod.settings.USER_CONFIG_DIR = self.tmp

    def tearDown(self):
        pet_mod.settings.USER_CONFIG_DIR = self._cfg_dir

    def test_defaults_when_no_user_file(self):
        p = pet_mod.load_pet()
        self.assertTrue(p["visible"])
        self.assertEqual(p["x"], 72)
        self.assertEqual(p["y"], 64)
        self.assertEqual(p["scale"], 1.0)

    def test_save_merges_and_persists(self):
        saved = pet_mod.save_pet({"x": 333, "y": 222})
        self.assertEqual(saved["x"], 333)
        self.assertEqual(saved["y"], 222)
        self.assertEqual(saved["visible"], True)  # untouched default kept
        # Reload from disk reflects the merge.
        again = pet_mod.load_pet()
        self.assertEqual(again["x"], 333)
        self.assertEqual(again["y"], 222)

    def test_toggle_flips_visible(self):
        pet_mod.save_pet({"visible": True})
        pet_mod.save_pet({"visible": not pet_mod.load_pet()["visible"]})
        self.assertFalse(pet_mod.load_pet()["visible"])

    def test_bad_user_file_falls_back(self):
        with open(os.path.join(self.tmp, "pet.json"), "w", encoding="utf-8") as fh:
            fh.write("not json{{")
        p = pet_mod.load_pet()
        self.assertEqual(p["x"], 72)


class TestPetCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._cfg_dir = pet_mod.settings.USER_CONFIG_DIR
        pet_mod.settings.USER_CONFIG_DIR = self.tmp

    def tearDown(self):
        pet_mod.settings.USER_CONFIG_DIR = self._cfg_dir

    def test_status(self):
        self.assertEqual(cli_mod.cmd_pet([]), 0)
        self.assertEqual(cli_mod.cmd_pet(["status"]), 0)

    def test_show_hide_toggle(self):
        self.assertEqual(cli_mod.cmd_pet(["hide"]), 0)
        self.assertFalse(pet_mod.load_pet()["visible"])
        self.assertEqual(cli_mod.cmd_pet(["show"]), 0)
        self.assertTrue(pet_mod.load_pet()["visible"])
        self.assertEqual(cli_mod.cmd_pet(["toggle"]), 0)
        self.assertFalse(pet_mod.load_pet()["visible"])

    def test_pos(self):
        self.assertEqual(cli_mod.cmd_pet(["pos", "100", "200"]), 0)
        self.assertEqual(pet_mod.load_pet()["x"], 100)
        self.assertEqual(pet_mod.load_pet()["y"], 200)

    def test_pos_rejects_non_int(self):
        self.assertNotEqual(cli_mod.cmd_pet(["pos", "abc", "200"]), 0)

    def test_scale_clamped(self):
        self.assertEqual(cli_mod.cmd_pet(["scale", "9"]), 0)
        self.assertLessEqual(pet_mod.load_pet()["scale"], 2.0)
        self.assertEqual(cli_mod.cmd_pet(["scale", "0.1"]), 0)
        self.assertGreaterEqual(pet_mod.load_pet()["scale"], 0.4)

    def test_opacity_clamped(self):
        self.assertEqual(cli_mod.cmd_pet(["opacity", "0.5"]), 0)
        self.assertAlmostEqual(pet_mod.load_pet()["opacity"], 0.5)
        self.assertEqual(cli_mod.cmd_pet(["opacity", "9"]), 0)
        self.assertLessEqual(pet_mod.load_pet()["opacity"], 1.0)
        self.assertEqual(cli_mod.cmd_pet(["opacity", "-1"]), 0)
        self.assertGreaterEqual(pet_mod.load_pet()["opacity"], 0.3)

    def test_unknown_subcommand(self):
        self.assertNotEqual(cli_mod.cmd_pet(["teleport"]), 0)

    def test_pet_command_dispatches_via_main(self):
        with mock.patch.object(cli_mod, "audit"):
            rc = cli_mod.main(["pet", "hide"])
        self.assertEqual(rc, 0)
        self.assertFalse(pet_mod.load_pet()["visible"])


if __name__ == "__main__":
    unittest.main()


class TestCharacterMemory(unittest.TestCase):
    """omaok's persona: identity/background questions answered from character memory."""

    def setUp(self):
        from config import character as ch
        self.ch = ch

    def test_who_answers_name_and_role(self):
        zh = self.ch.match("你是谁")
        self.assertIsNotNone(zh)
        self.assertIn("omaok", zh)
        self.assertIn("本地语音管家", zh)
        en = self.ch.match("who are you")
        self.assertIsNotNone(en)
        self.assertIn("omaok", en)

    def test_name_mention_introduces(self):
        self.assertIn("omaok", self.ch.match("小飞马"))
        self.assertIn("omaok", self.ch.match("omaok"))

    def test_background_answers_origin_and_home(self):
        ans = self.ch.match("你的背景是什么")
        self.assertIsNotNone(ans)
        self.assertIn("Omarchy", ans)
        self.assertIn("右下角", ans)

    def test_ability_answers_capabilities(self):
        self.assertIn("Voxtype", self.ch.match("你会什么"))

    def test_personality_answers(self):
        self.assertIn("傲娇", self.ch.match("你的性格怎么样"))

    def test_non_persona_returns_none(self):
        self.assertIsNone(self.ch.match("帮我打开浏览器"))
        self.assertIsNone(self.ch.match("今天天气怎么样"))
        self.assertIsNone(self.ch.match(""))
