"""Unit tests: intent parsing (zh/en), resolver, policy.

Run:  python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from unittest import mock

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

    def test_media_actions(self):
        for phrase in ("播放音乐", "放首歌", "开始播放", "暂停音乐", "暂停", "play music", "pause"):
            self.assertEqual(rules.parse(phrase)["type"], "play_pause_media", phrase)
        self.assertEqual(rules.parse("下一首")["type"], "next_track")
        self.assertEqual(rules.parse("切歌")["type"], "next_track")
        self.assertEqual(rules.parse("next")["type"], "next_track")
        self.assertEqual(rules.parse("上一首")["type"], "previous_track")
        self.assertEqual(rules.parse("previous")["type"], "previous_track")
        self.assertEqual(rules.parse("调大音量")["type"], "volume_up")
        self.assertEqual(rules.parse("大声点")["type"], "volume_up")
        self.assertEqual(rules.parse("volume up")["type"], "volume_up")
        self.assertEqual(rules.parse("调小音量")["type"], "volume_down")
        self.assertEqual(rules.parse("小声点")["type"], "volume_down")
        self.assertEqual(rules.parse("静音")["type"], "toggle_mute")
        self.assertEqual(rules.parse("mute")["type"], "toggle_mute")

    def test_unclear(self):
        self.assertIsNone(rules.parse("今天天气怎么样"))
        self.assertIsNone(rules.parse(""))

    def test_batch1_toggles(self):
        self.assertEqual(rules.parse("开启护眼模式")["type"], "set_nightlight")
        self.assertEqual(rules.parse("关闭夜灯")["raw_target"], "off")
        self.assertEqual(rules.parse("打开蓝牙")["type"], "set_bluetooth")
        self.assertEqual(rules.parse("关闭wifi")["type"], "set_wifi")
        self.assertEqual(rules.parse("关闭触摸板")["type"], "set_touchpad")
        self.assertEqual(rules.parse("省电模式")["type"], "set_power_mode")
        self.assertEqual(rules.parse("静音麦克风")["type"], "toggle_mic")
        self.assertEqual(rules.parse("勿扰")["type"], "toggle_dnd")
        self.assertEqual(rules.parse("隐藏顶栏")["type"], "toggle_bar")
        self.assertEqual(rules.parse("剪贴板")["type"], "open_clipboard")
        self.assertEqual(rules.parse("表情")["type"], "open_emoji")

    def test_batch2_parameterized(self):
        d = rules.parse("提醒我15分钟后喝水")
        self.assertEqual(d["type"], "set_reminder")
        self.assertEqual(d["minutes"], 15)
        self.assertEqual(d["raw_target"], "喝水")
        e = rules.parse("remind me in 30 minutes to check email")
        self.assertEqual(e["minutes"], 30)
        self.assertEqual(e["raw_target"], "check email")
        self.assertEqual(rules.parse("调亮屏幕")["type"], "brightness_up")
        self.assertEqual(rules.parse("调暗屏幕")["type"], "brightness_down")

    def test_batch3_destructive(self):
        self.assertEqual(rules.parse("关机")["type"], "shutdown")
        self.assertEqual(rules.parse("重启")["type"], "reboot")
        self.assertEqual(rules.parse("注销")["type"], "logout")
        self.assertEqual(rules.parse("开始录屏")["type"], "screen_record_start")
        self.assertEqual(rules.parse("停止录屏")["type"], "screen_record_stop")


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

    def test_state_action_resolve(self):
        draft = rules.parse("关闭蓝牙")
        a = lookup.resolve_action(dict(draft), CFG, ALIASES)
        self.assertEqual(a["target"]["state"], "off")
        draft = rules.parse("省电模式")
        a = lookup.resolve_action(dict(draft), CFG, ALIASES)
        self.assertEqual(a["target"]["state"], "power-saver")

    def test_reminder_resolve(self):
        draft = rules.parse("提醒我25分钟后开会")
        a = lookup.resolve_action(dict(draft), CFG, ALIASES)
        self.assertEqual(a["target"]["kind"], "reminder")
        self.assertEqual(a["target"]["minutes"], 25)
        self.assertEqual(a["target"]["message"], "开会")

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
        # media/volume are low risk -> auto-execute
        self.assertEqual(policy.classify({"type": "play_pause_media", "confidence": 0.9}, CFG)["risk"], "low")
        self.assertEqual(policy.classify({"type": "volume_up", "confidence": 0.9}, CFG)["risk"], "low")
        # toggles low, destructive confirm_required
        self.assertEqual(policy.classify({"type": "set_nightlight", "confidence": 0.9}, CFG)["risk"], "low")
        self.assertEqual(policy.classify({"type": "set_reminder", "confidence": 0.9}, CFG)["risk"], "low")
        for t in ("shutdown", "reboot", "logout", "screen_record_start", "screen_record_stop"):
            self.assertEqual(policy.classify({"type": t, "confidence": 0.9}, CFG)["risk"], "confirm_required", t)
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


class TestMediaExecutor(unittest.TestCase):
    def test_media_play_pause_uses_status_gate(self):
        from executor import actions
        with mock.patch.object(actions, "_media_status", return_value={"canTogglePlaying": True, "hasPlayer": True}), \
             mock.patch.object(actions, "_run", return_value=(True, "ok")) as run:
            ok, msg = actions.execute({"type": "play_pause_media", "target": {}}, CFG)
            self.assertTrue(ok)
            self.assertEqual(run.call_args_list[-1][0][0], ["omarchy-shell", "media", "playPause"])

    def test_media_skip_needs_can_go(self):
        from executor import actions
        with mock.patch.object(actions, "_media_status", return_value={"canGoNext": False, "hasPlayer": True}), \
             mock.patch.object(actions, "_run") as run:
            ok, msg = actions.execute({"type": "next_track", "target": {}}, CFG)
            self.assertFalse(ok)
            run.assert_not_called()

    def test_volume_maps_to_omarchy_audio(self):
        from executor import actions
        with mock.patch.object(actions, "_run", return_value=(True, "ok")) as run:
            ok, msg = actions.execute({"type": "volume_up", "target": {}}, CFG)
            self.assertTrue(ok)
            self.assertEqual(run.call_args[0][0][:2], ["omarchy", "audio"])

    def test_play_pause_launches_music_app_when_no_player(self):
        from executor import actions
        def fake_status():
            fake_status.calls += 1
            return {"canTogglePlaying": False, "hasPlayer": False}  # never becomes playable
        fake_status.calls = 0
        with mock.patch.object(actions, "_media_status", side_effect=fake_status), \
             mock.patch.object(actions, "_launch_detached_cmd") as launch, \
             mock.patch.object(actions.time, "sleep"):
            ok, msg = actions.execute({"type": "play_pause_media", "target": {}}, CFG)
            self.assertTrue(ok)
            launch.assert_called_once_with("omarchy launch spotify")
            self.assertIn("launched music app", msg)

    def test_play_pause_reports_idle_player_honestly(self):
        from executor import actions
        with mock.patch.object(actions, "_media_status", return_value={"hasPlayer": True, "canTogglePlaying": False}), \
             mock.patch.object(actions, "_launch_detached_cmd") as launch, \
             mock.patch.object(actions, "_run") as run:
            ok, msg = actions.execute({"type": "play_pause_media", "target": {}}, CFG)
            self.assertTrue(ok)
            self.assertIn("nothing is playing", msg)
            launch.assert_not_called()
            run.assert_not_called()

    def test_state_actions_map_to_omarchy_commands(self):
        from executor import actions
        with mock.patch.object(actions, "_run", return_value=(True, "ok")) as run:
            ok, _ = actions.execute({"type": "set_nightlight", "target": {"state": "on"}}, CFG)
            self.assertEqual(run.call_args[0][0], ["omarchy-shell", "nightlight", "enable"])
            ok, _ = actions.execute({"type": "set_bluetooth", "target": {"state": "toggle"}}, CFG)
            self.assertEqual(run.call_args[0][0], ["omarchy", "bluetooth", "power", "toggle"])
            ok, _ = actions.execute({"type": "set_power_mode", "target": {"state": "performance"}}, CFG)
            self.assertEqual(run.call_args[0][0][:4], ["omarchy", "powerprofiles", "set", "autodetect"])

    def test_reminder_and_destructive_mapping(self):
        from executor import actions
        with mock.patch.object(actions, "_run", return_value=(True, "ok")) as run:
            ok, _ = actions.execute({"type": "set_reminder", "target": {"minutes": 15, "message": "喝水"}}, CFG)
            self.assertEqual(run.call_args[0][0], ["omarchy", "reminder", "15", "喝水"])
            ok, _ = actions.execute({"type": "shutdown", "target": {}}, CFG)
            self.assertEqual(run.call_args[0][0], ["omarchy", "system", "shutdown"])
            ok, _ = actions.execute({"type": "brightness_up", "target": {}}, CFG)
            self.assertEqual(run.call_args[0][0], ["omarchy", "brightness", "display", "+10%"])


class TestSupplementaryRules(unittest.TestCase):
    """L1 fast rules: polite/request phrasing + common variants must NOT fall
    through to the AI layer (speed for computer-control commands)."""

    def test_open_app_variants(self):
        cases = [
            ("把微信打开", "微信"), ("帮我把微信打开", "微信"), ("我想把浏览器打开", "浏览器"),
            ("我要用微信", "微信"), ("帮我用一下计算器", "计算器"), ("我想用浏览器", "浏览器"),
            ("点开终端", "终端"), ("呼出设置", "设置"),
        ]
        for phrase, target in cases:
            d = rules.parse(phrase)
            self.assertEqual(d["type"], "open_app", phrase)
            self.assertEqual(d["raw_target"], target, phrase)

    def test_open_folder_file_with_ba(self):
        self.assertEqual(rules.parse("把下载文件夹打开")["type"], "open_folder")
        self.assertEqual(rules.parse("把报告文件打开")["type"], "open_file")

    def test_toggle_with_ba(self):
        self.assertEqual(rules.parse("把wifi关了")["raw_target"], "off")
        self.assertEqual(rules.parse("把wifi打开")["raw_target"], "on")
        self.assertEqual(rules.parse("把蓝牙关了")["raw_target"], "off")
        self.assertEqual(rules.parse("把蓝牙开开")["raw_target"], "on")
        self.assertEqual(rules.parse("把夜灯关了")["raw_target"], "off")
        self.assertEqual(rules.parse("把触摸板关了")["raw_target"], "off")

    def test_volume_variants(self):
        self.assertEqual(rules.parse("把声音调大")["type"], "volume_up")
        self.assertEqual(rules.parse("把声音调小")["type"], "volume_down")
        self.assertEqual(rules.parse("声音开到最大")["type"], "volume_up")
        self.assertEqual(rules.parse("声音开到最小")["type"], "volume_down")
        self.assertEqual(rules.parse("把声音关了")["type"], "toggle_mute")

    def test_media_variants(self):
        for phrase in ["暂停一下", "先暂停", "放首歌吧", "放首歌听", "换一首", "换首歌", "换回上一首"]:
            self.assertIsNotNone(rules.parse(phrase), phrase)

    def test_screenshot_lock_close_variants(self):
        self.assertEqual(rules.parse("截个图")["type"], "take_screenshot")
        self.assertEqual(rules.parse("帮我锁屏")["type"], "lock_screen")
        self.assertEqual(rules.parse("锁一下屏")["type"], "lock_screen")
        self.assertEqual(rules.parse("把当前窗口关上")["type"], "close_active_window")

    def test_reminder_variants(self):
        a = rules.parse("5分钟后提醒我喝水")
        self.assertEqual(a["type"], "set_reminder")
        self.assertEqual(a["minutes"], 5)
        self.assertEqual(a["raw_target"], "喝水")
        b = rules.parse("五分钟后提醒我喝水")
        self.assertEqual(b["minutes"], 5)
        c = rules.parse("提醒我在10分钟后休息")
        self.assertEqual(c["minutes"], 10)
        d = rules.parse("提醒我喝水")
        self.assertEqual(d["type"], "set_reminder")
        self.assertEqual(d["minutes"], 10)  # default when no time given

    def test_move_window_variants(self):
        self.assertEqual(rules.parse("把窗口切到工作区3")["type"], "move_active_window_to_workspace")
        self.assertEqual(rules.parse("把窗口挪到工作区五")["raw_target"], "5")

    def test_non_commands_stay_unparsed(self):
        # Chat/persona/noise must NOT be hijacked by the new open rules.
        for phrase in ["今天天气怎么样", "你是谁", "帮我写首诗", "什么是量子计算", "谢谢你", "讲个笑话"]:
            self.assertIsNone(rules.parse(phrase), phrase)


if __name__ == "__main__":
    unittest.main()
