"""Deterministic phrase/alias parsing for omarchy-voice-control.

Returns a strict Action draft: {type, raw_target, confidence, source: "rule"}.
The resolver later fills the structured `target`; policy sets `risk`.
Raw transcripts are never executed — only a parsed Action can reach the
executor, and only after policy + explicit confirmation.
"""

from __future__ import annotations

import re

ALLOWED_TYPES = frozenset({
    "open_app", "focus_app", "open_file", "open_folder",
    "close_active_window", "switch_workspace",
    "move_active_window_to_workspace", "toggle_fullscreen",
    "take_screenshot", "lock_screen",
    "play_pause_media", "next_track", "previous_track",
    "volume_up", "volume_down", "toggle_mute",
    # batch 1: conventional omarchy toggles (low risk)
    "set_nightlight", "set_bluetooth", "set_wifi", "set_touchpad",
    "toggle_dnd", "toggle_mic", "toggle_bar",
    "open_clipboard", "open_emoji",
    # batch 2: parameterized
    "set_reminder", "brightness_up", "brightness_down", "set_power_mode",
    # batch 3: destructive / privacy-sensitive (confirm_required)
    "shutdown", "reboot", "logout",
    "screen_record_start", "screen_record_stop",
})

_PUNCT_RE = re.compile(r"[，。！？、；：“”‘’《》【】（）…·!?.,;:()\[\]\"'<>{}|/\\]")


def normalize(text: str) -> str:
    t = _PUNCT_RE.sub(" ", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Ordered rules: first match wins. Higher-priority specific phrases first.
_CLOSE_RE = re.compile(r"关闭\s*(?:当前\s*)?窗口|关掉窗口|close\s*(?:the\s*)?(?:active\s*)?window", re.I)
_LOCK_RE = re.compile(r"锁屏|锁定屏幕|锁定|lock\s*(?:the\s*)?screen|lock\s*(?:the\s*)?computer", re.I)
_SCREENSHOT_RE = re.compile(r"截图|截屏|屏幕截图|screenshot|take\s*a\s*screenshot|screen\s*shot", re.I)
_FULLSCREEN_RE = re.compile(r"全屏|全屏幕|fullscreen|full\s*screen|toggle\s*fullscreen", re.I)

_MOVE_WS_RE = re.compile(
    r"(?:把\s*)?(?:当前\s*)?(?:这个\s*)?窗口\s*(?:移到|移动到|放到|移去)\s*工作区\s*([0-9]+|[一二两三四五六七八九十]+)"
    r"|(?:把|将)\s*[^切移放\s]+\s*(?:切到|切换到|移到|移动到|放到)\s*工作区\s*([0-9]+|[一二两三四五六七八九十]+)"
    r"|(?:move|put)\s*(?:the\s*)?(?:active\s*)?(?:current\s*)?window\s*(?:to|into)\s*workspace\s*([0-9]+)"
    r"|move\s*(?:to|into)\s*workspace\s*([0-9]+)",
    re.I,
)

_SWITCH_WS_RE = re.compile(
    r"(?:切换到|切到|到|前往)\s*工作区\s*([0-9]+|[一二两三四五六七八九十]+)"
    r"|工作区\s*([0-9]+|[一二两三四五六七八九十]+)"
    r"|go\s*(?:to)?\s*(?:workspace)?\s*([0-9]+)"
    r"|switch\s*(?:to)?\s*(?:workspace)?\s*([0-9]+)"
    r"|workspace\s*([0-9]+)",
    re.I,
)

_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _to_arabic(s: str | None) -> str | None:
    """工作区五 -> 5; 十二 -> 12. Returns None when not a valid number."""
    if not s:
        return None
    if s.isdigit():
        return s
    if s in _CN_DIGITS:
        return str(_CN_DIGITS[s])
    if s == "十":
        return "10"
    if len(s) == 2 and s[0] in _CN_DIGITS and s[1] == "十":
        return str(_CN_DIGITS[s[0]] * 10)
    if len(s) == 2 and s[0] == "十" and s[1] in _CN_DIGITS:
        return str(10 + _CN_DIGITS[s[1]])
    if len(s) == 3 and s[0] in _CN_DIGITS and s[1] == "十" and s[2] in _CN_DIGITS:
        return str(_CN_DIGITS[s[0]] * 10 + _CN_DIGITS[s[2]])
    return None

_FOLDER_RE = re.compile(
    r"(?:打开|调出|唤出|显示)\s*(?:一下\s*)?(.+?(?:文件夹|目录))"
    r"|(?:open|bring up|pull up|show)\s*(?:the\s*)?(.+?(?:folder|directory))",
    re.I,
)

_FILE_RE = re.compile(
    r"打开\s*(?:一下\s*)?(.+?文件)"
    r"|open\s*(?:the\s*)?(.+?file)",
    re.I,
)

_OPEN_RE = re.compile(
    r"(?:打开|启动|开启|运行|调出|唤出)\s*(?:一下\s*)?(.+)"
    r"|(?:open|launch|start|run|bring up)\s+(.+)",
    re.I,
)

_FOCUS_RE = re.compile(
    r"(?:聚焦|切到|切换到)\s*(?:一下\s*)?(.+)"
    r"|(?:focus\s+(?:on\s+)?)(.+)",
    re.I,
)


_PLAY_PAUSE_RE = re.compile(
    r"播放\s*(?:音乐|歌曲|歌|首歌|音乐吧)?"
    r"|放歌|放首歌|放音乐|放点音乐|放点歌|来点音乐|来首歌|来一曲|来段音乐|整点音乐"
    r"|开始播放|继续播放|接着放|接着播|接着放歌"
    r"|暂停\s*(?:音乐|播放)?|停止播放|停歌|别放了"
    r"|(?:想|要|给我)?(?:听|放)(?:个|首|点)?(?:音乐|歌|歌曲)"
    r"|播\S*音乐\S*|音乐\S*(?:听|放|播)"
    r"|play\s*(?:music|some music|a song)?|pause\s*(?:music|the music)?|resume|stop playing",
    re.I,
)
_NEXT_RE = re.compile(r"下一首|下一曲|切歌|换歌|跳过|next track|next song|next", re.I)
_PREV_RE = re.compile(r"上一首|上一曲|切回上一首|previous track|previous song|previous", re.I)
_VOLUME_UP_RE = re.compile(r"调大音量|音量调大|音量加|增大音量|调高音量|声音大点|大声点|声音大一点|volume up|turn up|louder", re.I)
_VOLUME_DOWN_RE = re.compile(r"调小音量|音量调小|音量减|减小音量|调低音量|声音小点|小声点|声音小一点|volume down|turn down|quieter", re.I)
_MUTE_RE = re.compile(r"静音|关闭声音|取消静音|打开声音|mute|unmute|silence", re.I)

# ---- conventional omarchy toggles (batch 1) ----
_NL_ON_RE = re.compile(r"开启夜灯|打开夜灯|开启护眼|打开护眼|nightlight on|turn on nightlight", re.I)
_NL_OFF_RE = re.compile(r"关闭夜灯|关闭护眼|关掉夜灯|nightlight off|turn off nightlight", re.I)
_NL_RE = re.compile(r"夜灯|护眼模式|nightlight", re.I)

_BT_ON_RE = re.compile(r"开启蓝牙|打开蓝牙|开蓝牙|bluetooth on|turn on bluetooth", re.I)
_BT_OFF_RE = re.compile(r"关闭蓝牙|关掉蓝牙|关蓝牙|bluetooth off|turn off bluetooth", re.I)
_BT_RE = re.compile(r"蓝牙|bluetooth", re.I)

_WIFI_ON_RE = re.compile(r"开启wifi|打开wifi|开启无线|打开无线|wifi on|turn on wifi|开启无线网络", re.I)
_WIFI_OFF_RE = re.compile(r"关闭wifi|关掉wifi|关闭无线|wifi off|turn off wifi|关闭无线网络", re.I)
_WIFI_RE = re.compile(r"wifi|wi-?fi|无线网|无线网络", re.I)

_TP_ON_RE = re.compile(r"开启触摸板|打开触摸板|touchpad on|enable touchpad", re.I)
_TP_OFF_RE = re.compile(r"关闭触摸板|关掉触摸板|touchpad off|disable touchpad", re.I)
_TP_RE = re.compile(r"触摸板|触控板|touchpad", re.I)

_PM_SAVER_RE = re.compile(r"省电模式|开启省电|省电|power saver|battery saver|save power", re.I)
_PM_PERF_RE = re.compile(r"性能模式|开启性能|性能优先|performance mode|max performance", re.I)
_PM_BAL_RE = re.compile(r"平衡模式|均衡模式|balanced mode", re.I)

_MIC_TOGGLE_RE = re.compile(r"静音麦克风|麦克风静音|关闭麦克风|打开麦克风|mute microphone|unmute microphone|microphone mute", re.I)
_DND_RE = re.compile(r"勿扰|免打扰|勿扰模式|do not disturb|dnd", re.I)
_BAR_RE = re.compile(r"隐藏顶栏|显示顶栏|顶栏|任务栏|hide bar|show bar|toggle bar", re.I)
_CLIPBOARD_RE = re.compile(r"打开剪贴板|剪贴板|clipboard", re.I)
_EMOJI_RE = re.compile(r"打开表情|表情符号|表情|emoji", re.I)

# ---- parameterized (batch 2) ----
_REMINDER_RE = re.compile(
    r"提醒(?:我)?\s*(?:在|过)?\s*(\d+)\s*(分钟|分|秒|小时|minute|hour|min)s?\s*(?:后|之后)?\s*(.*)"
    r"|remind me(?: in)?\s*(\d+)\s*(minute|hour|min)s?\s*(?:to|about)?\s*(.*)",
    re.I,
)
_REMINDER_HALF = re.compile(r"提醒(?:我)?(半小时)(?:后|之后)?(.*)", re.I)
_BRIGHT_UP_RE = re.compile(r"调亮屏幕|屏幕调亮|亮度调高|调高亮度|增亮|亮一点|screen brighter|brightness up", re.I)
_BRIGHT_DOWN_RE = re.compile(r"调暗屏幕|屏幕调暗|亮度调低|调低亮度|变暗|暗一点|screen darker|brightness down", re.I)

# ---- destructive / privacy-sensitive (batch 3, confirm_required) ----
_SHUTDOWN_RE = re.compile(r"关机|shutdown|power off|poweroff", re.I)
_REBOOT_RE = re.compile(r"重启|重新启动|reboot|restart computer", re.I)
_LOGOUT_RE = re.compile(r"注销|退出登录|logout|log out", re.I)
_REC_ON_RE = re.compile(r"开始录屏|开始录制|开始录像|录屏|屏幕录制|start recording|start screen recording|screenrecord", re.I)
_REC_OFF_RE = re.compile(r"停止录屏|停止录制|结束录屏|停止录像|stop recording|stop screen recording|end recording", re.I)


def parse(text: str) -> dict | None:
    """Parse a transcript into an Action draft, or None if unclear."""
    t = normalize(text)
    if not t:
        return None

    m = _CLOSE_RE.search(t)
    if m:
        return _draft("close_active_window")
    m = _LOCK_RE.search(t)
    if m:
        return _draft("lock_screen")
    m = _SCREENSHOT_RE.search(t)
    if m:
        return _draft("take_screenshot")
    m = _FULLSCREEN_RE.search(t)
    if m:
        return _draft("toggle_fullscreen")

    m = _PLAY_PAUSE_RE.search(t)
    if m:
        return _draft("play_pause_media")
    m = _NEXT_RE.search(t)
    if m:
        return _draft("next_track")
    m = _PREV_RE.search(t)
    if m:
        return _draft("previous_track")
    m = _VOLUME_UP_RE.search(t)
    if m:
        return _draft("volume_up")
    m = _VOLUME_DOWN_RE.search(t)
    if m:
        return _draft("volume_down")
    m = _MIC_TOGGLE_RE.search(t)  # more specific than generic mute
    if m:
        return _draft("toggle_mic")
    m = _MUTE_RE.search(t)
    if m:
        return _draft("toggle_mute")

    # ---- batch 1 toggles ----
    m = _NL_ON_RE.search(t)
    if m:
        return _draft("set_nightlight", raw_target="on")
    m = _NL_OFF_RE.search(t)
    if m:
        return _draft("set_nightlight", raw_target="off")
    m = _NL_RE.search(t)
    if m:
        return _draft("set_nightlight", raw_target="toggle")

    m = _BT_ON_RE.search(t)
    if m:
        return _draft("set_bluetooth", raw_target="on")
    m = _BT_OFF_RE.search(t)
    if m:
        return _draft("set_bluetooth", raw_target="off")
    m = _BT_RE.search(t)
    if m:
        return _draft("set_bluetooth", raw_target="toggle")

    m = _WIFI_ON_RE.search(t)
    if m:
        return _draft("set_wifi", raw_target="on")
    m = _WIFI_OFF_RE.search(t)
    if m:
        return _draft("set_wifi", raw_target="off")
    m = _WIFI_RE.search(t)
    if m:
        return _draft("set_wifi", raw_target="toggle")

    m = _TP_ON_RE.search(t)
    if m:
        return _draft("set_touchpad", raw_target="on")
    m = _TP_OFF_RE.search(t)
    if m:
        return _draft("set_touchpad", raw_target="off")
    m = _TP_RE.search(t)
    if m:
        return _draft("set_touchpad", raw_target="toggle")

    m = _PM_SAVER_RE.search(t)
    if m:
        return _draft("set_power_mode", raw_target="power-saver")
    m = _PM_PERF_RE.search(t)
    if m:
        return _draft("set_power_mode", raw_target="performance")
    m = _PM_BAL_RE.search(t)
    if m:
        return _draft("set_power_mode", raw_target="balanced")

    m = _MIC_TOGGLE_RE.search(t)
    if m:
        return _draft("toggle_mic")
    m = _DND_RE.search(t)
    if m:
        return _draft("toggle_dnd")
    m = _BAR_RE.search(t)
    if m:
        return _draft("toggle_bar")
    m = _CLIPBOARD_RE.search(t)
    if m:
        return _draft("open_clipboard")
    m = _EMOJI_RE.search(t)
    if m:
        return _draft("open_emoji")

    # ---- batch 2 parameterized ----
    m = _REMINDER_RE.search(t)
    if m:
        if m.group(1):
            num, unit, msg = int(m.group(1)), m.group(2), (m.group(3) or "").strip()
        else:
            num, unit, msg = int(m.group(4)), m.group(5), (m.group(6) or "").strip()
        if unit in ("小时", "hour"):
            num *= 60
        elif unit in ("秒",):
            num = 1
        return _draft("set_reminder", raw_target=msg, minutes=max(1, num))
    m = _REMINDER_HALF.search(t)
    if m:
        return _draft("set_reminder", raw_target=(m.group(2) or "").strip(), minutes=30)

    m = _BRIGHT_UP_RE.search(t)
    if m:
        return _draft("brightness_up")
    m = _BRIGHT_DOWN_RE.search(t)
    if m:
        return _draft("brightness_down")

    # ---- batch 3 destructive ----
    m = _SHUTDOWN_RE.search(t)
    if m:
        return _draft("shutdown")
    m = _REBOOT_RE.search(t)
    if m:
        return _draft("reboot")
    m = _LOGOUT_RE.search(t)
    if m:
        return _draft("logout")
    m = _REC_OFF_RE.search(t)
    if m:
        return _draft("screen_record_stop")
    m = _REC_ON_RE.search(t)
    if m:
        return _draft("screen_record_start")

    m = _MOVE_WS_RE.search(t)
    if m:
        ws = _to_arabic(next((g for g in m.groups() if g is not None), None))
        if ws:
            return _draft("move_active_window_to_workspace", raw_target=ws)

    m = _SWITCH_WS_RE.search(t)
    if m:
        ws = _to_arabic(next((g for g in m.groups() if g is not None), None))
        if ws:
            return _draft("switch_workspace", raw_target=ws)

    m = _FOLDER_RE.search(t)
    if m:
        name = next((g for g in m.groups() if g and g.strip()), None)
        return _draft("open_folder", raw_target=(name or "").strip())

    m = _FILE_RE.search(t)
    if m:
        name = next((g for g in m.groups() if g and g.strip()), None)
        return _draft("open_file", raw_target=(name or "").strip())

    m = _FOCUS_RE.search(t)
    if m:
        name = next((g for g in m.groups() if g and g.strip()), None)
        return _draft("focus_app", raw_target=(name or "").strip())

    m = _OPEN_RE.search(t)
    if m:
        name = next((g for g in m.groups() if g and g.strip()), None)
        if name:
            # Strip launch-package suffixes so "微信软件/程序" resolves to 微信.
            name = re.sub(r"(软件|程序|应用)\s*$", "", name.strip())
        return _draft("open_app", raw_target=(name or "").strip())

    return None


def _draft(action_type: str, raw_target: str | None = None, **extra) -> dict:
    draft = {
        "type": action_type,
        "raw_target": raw_target or "",
        "confidence": 0.9,
        "source": "rule",
        "risk": "low",
    }
    draft.update(extra)
    return draft
