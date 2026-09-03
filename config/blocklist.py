"""Blocklist for paths the voice assistant must never open.

Enforced in three independent layers so no source (deterministic rule or the
local AI agent) can ever make the assistant open a sensitive file/folder:

1. resolver  — blocked/hidden entries are never returned and never searched.
2. policy    — any Action whose resolved target path is blocked is denied.
3. executor  — open_file/open_folder refuse blocked paths outright.

Default policy blocks every dot-prefixed (hidden) entry plus a list of
sensitive segment/file names (ssh/gnupg/config/cache/local/pki keys, agent
auth, browser data, keyrings, ...). A user may extend/replace it with
~/.config/omarchy/voice-control/blocklist.json.
"""

from __future__ import annotations

import fnmatch
import json
import os

from config import settings

DEFAULT_BLOCKLIST = {
    "block_hidden": True,
    "segments": [],
    "file_names": [],
}


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_blocklist() -> dict:
    """Merge the plugin default with the user override (user wins)."""
    merged = dict(DEFAULT_BLOCKLIST)
    merged["segments"] = list(merged["segments"])
    merged["file_names"] = list(merged["file_names"])
    default = _load(os.path.join(settings.PLUGIN_DIR, "config", "blocklist.json"))
    user = _load(os.path.join(settings.USER_CONFIG_DIR, "blocklist.json"))
    for source in (default, user):
        if not source:
            continue
        if "block_hidden" in source:
            merged["block_hidden"] = bool(source["block_hidden"])
        for key in ("segments", "file_names"):
            values = source.get(key)
            if isinstance(values, list):
                merged[key] = list(dict.fromkeys(merged[key] + [str(v) for v in values]))
    return merged


def is_blocked_path(path: str, blocklist: dict | None = None) -> bool:
    """Return True if the path must never be opened."""
    if not path:
        return False
    blocklist = blocklist or load_blocklist()
    norm = os.path.abspath(os.path.normpath(os.path.expanduser(path)))
    parts = norm.split(os.sep)

    # Hidden entries (dot-prefixed segments) are blocked by default.
    if blocklist.get("block_hidden", True):
        for seg in parts[1:]:
            if seg.startswith("."):
                return True

    # Sensitive path segments.
    segments = set(blocklist.get("segments", []))
    for seg in parts[1:]:
        if seg in segments:
            return True

    # Sensitive file names (supports glob).
    base = os.path.basename(norm)
    for pattern in blocklist.get("file_names", []):
        if fnmatch.fnmatch(base, pattern):
            return True

    return False
