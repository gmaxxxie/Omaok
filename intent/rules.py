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
    r"(?:把\s*)?(?:当前\s*)?(?:这个\s*)?窗口\s*(?:移到|移动到|放到|移去)\s*工作区\s*(\d+)"
    r"|(?:move|put)\s*(?:the\s*)?(?:active\s*)?(?:current\s*)?window\s*(?:to|into)\s*workspace\s*(\d+)"
    r"|move\s*(?:to|into)\s*workspace\s*(\d+)",
    re.I,
)

_SWITCH_WS_RE = re.compile(
    r"(?:切换到|切到|到|前往)\s*工作区\s*(\d+)"
    r"|工作区\s*(\d+)"
    r"|go\s*(?:to)?\s*(?:workspace)?\s*(\d+)"
    r"|switch\s*(?:to)?\s*(?:workspace)?\s*(\d+)"
    r"|workspace\s*(\d+)",
    re.I,
)

_FOLDER_RE = re.compile(
    r"打开\s*(?:一下\s*)?(.+?(?:文件夹|目录))"
    r"|open\s*(?:the\s*)?(.+?(?:folder|directory))",
    re.I,
)

_FILE_RE = re.compile(
    r"打开\s*(?:一下\s*)?(.+?文件)"
    r"|open\s*(?:the\s*)?(.+?file)",
    re.I,
)

_OPEN_RE = re.compile(
    r"(?:打开|启动|开启|运行)\s*(?:一下\s*)?(.+)"
    r"|(?:open|launch|start|run)\s+(.+)",
    re.I,
)

_FOCUS_RE = re.compile(
    r"(?:聚焦|切到|切换到)\s*(?:一下\s*)?(.+)"
    r"|(?:focus\s+(?:on\s+)?)(.+)",
    re.I,
)


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

    m = _MOVE_WS_RE.search(t)
    if m:
        ws = next((g for g in m.groups() if g is not None), None)
        return _draft("move_active_window_to_workspace", raw_target=ws)

    m = _SWITCH_WS_RE.search(t)
    if m:
        ws = next((g for g in m.groups() if g is not None), None)
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
        return _draft("open_app", raw_target=(name or "").strip())

    return None


def _draft(action_type: str, raw_target: str | None = None) -> dict:
    return {
        "type": action_type,
        "raw_target": raw_target or "",
        "confidence": 0.9,
        "source": "rule",
        "risk": "low",
    }
