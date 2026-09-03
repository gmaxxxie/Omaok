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
    "min_confidence": 0.6,     # below this the action is rejected as unclear
    "ai": {
        "enabled": True,          # local AI intent layer (pi RPC) for rule-miss phrases
        "backend": "pi-rpc",
        "thinking": "off",         # non-thinking for speed
        "model": None,             # None -> pi default (deepseek-v4-flash)
        "timeout_secs": 60,
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
}

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
