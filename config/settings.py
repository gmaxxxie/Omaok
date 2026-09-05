"""Settings and alias loading for omarchy-voice-control.

Configuration is local-first and never touches packaged Omarchy or Voxtype
files. Defaults ship inside the plugin (config/config.json + config/aliases.json);
a user may override either in ~/.config/omarchy/voice-control/.
"""

from __future__ import annotations

import json
import os

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_CONFIG_DIR = os.path.expanduser("~/.config/omarchy/voice-control")

DEFAULT_CONFIG = {
    "stt": {
        "engine": "auto",  # "auto" -> Voxtype's configured engine
        "model": None,     # None -> Voxtype's configured model
        "language": "zh",  # "auto" lets Voxtype decide
        "timeout_secs": 120,
    },
    "confirm_low": False,      # low-risk actions execute directly, no confirmation
    "media": {
        "music_app": "omarchy launch spotify",  # conventional Omarchy launcher for no-player fallback
    },
    "min_confidence": 0.6,     # below this the action is rejected as unclear
    "ai": {
        "enabled": True,          # local AI intent layer (pi RPC) for rule-miss phrases
        "backend": "pi-rpc",
        "thinking": "off",         # non-thinking for speed
        "model": None,             # None -> pi default (deepseek-v4-flash)
        "timeout_secs": 60,
        "idle_secs": 900,          # ai-daemon exits after this idle (auto-respawned)
    },
    "chat": {
        "enabled": True,           # non-command transcripts get a short reply / AI-tool handoff
        "tool": "chromium --app=\"https://chatgpt.com/?q={query}\"",  # complex topics -> open the AI tool, pre-filled with the user's question ({query})
        "tool_label": "ChatGPT",
    },
    "recorder": {
        "device": "default",
        "max_seconds": 30,
        "min_seconds": 0.4,    # shorter captures are treated as accidental taps
    },
    "paths": {
        "roots": ["~", "~/Downloads", "~/Documents", "~/项目", "~/Desktop"],
    },
    "screenshot": {
        "mode": "fullscreen",
        "target": "save",
    },
    "workspaces": {"min": 1, "max": 10},
    "ui": {
        "language": "zh",  # "zh" | "en" | "auto" — UI + STT + replies language switch
    },
}

def ui_language(cfg: dict | None = None) -> str:
    """Raw ui.language setting: 'zh' | 'en' | 'auto' (default 'zh').
    The user-facing switch (CLI `lang`) keeps this and stt.language in sync."""
    lang = ((cfg or {}) or {}).get("ui", {}).get("language") or "zh"
    return lang if lang in ("zh", "en", "auto") else "zh"


def effective_ui_language(cfg: dict | None = None) -> str:
    """Concrete UI language for static strings: 'zh' | 'en'.
    'auto' resolves via the system locale (zh_* -> zh, otherwise en)."""
    lang = ui_language(cfg)
    if lang in ("zh", "en"):
        return lang
    return _locale_ui_language()


def _locale_ui_language() -> str:
    """System-locale based fallback for ui.language=auto."""
    try:
        import locale
        code, _ = locale.getlocale(locale.LC_CTYPE) or ("", "")
        if code and code.lower().startswith("zh"):
            return "zh"
    except Exception:
        pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if (os.environ.get(var) or "").lower().startswith("zh"):
            return "zh"
    return "en"


DEFAULT_ALIASES = {
    "apps": {},
    "folders": {},
    "files": {},
}


def _load_json(path: str, fallback):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else fallback
    except Exception:
        return fallback


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    """Merge defaults with the user override file (~/.config/omarchy/voice-control/config.json)."""
    default_path = os.path.join(PLUGIN_DIR, "config", "config.json")
    cfg = _deep_merge(DEFAULT_CONFIG, _load_json(default_path, None))
    user = _load_json(os.path.join(USER_CONFIG_DIR, "config.json"), None)
    cfg = _deep_merge(cfg, user)
    return cfg


def user_config_path() -> str:
    """Path of the user override config file (may not exist yet)."""
    return os.path.join(USER_CONFIG_DIR, "config.json")


def save_user_config(patch: dict) -> dict:
    """Deep-merge a partial update into the user override config file and
    return the resulting merged user config. Creates the file atomically;
    never touches the plugin's shipped defaults."""
    os.makedirs(USER_CONFIG_DIR, exist_ok=True)
    path = user_config_path()
    existing = _load_json(path, None) or {}
    merged = _deep_merge(existing, patch)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return merged


def load_aliases() -> dict:
    """Merge default aliases with the user override file."""
    default_path = os.path.join(PLUGIN_DIR, "config", "aliases.json")
    aliases = _deep_merge(DEFAULT_ALIASES, _load_json(default_path, None))
    user = _load_json(os.path.join(USER_CONFIG_DIR, "aliases.json"), None)
    aliases = _deep_merge(aliases, user)
    return aliases


def expand_path(path: str) -> str:
    """Expand ~ and environment variables in a configured path."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
