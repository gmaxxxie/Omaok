"""Desktop-pet (小飞马) configuration for omarchy-voice-control.

A tiny JSON file at ~/.config/omarchy/voice-control/pet.json holds the
desktop mascot's visibility, on-screen position, and scale. Defaults ship
inside the plugin (config/pet.json). The QML pet overlay watches this file
with FileView (exactly like state.json) and the popover toggle drives it
through the CLI, so the file is the single source of truth for pet geometry.
"""

from __future__ import annotations

import json
import os
import tempfile

from config import settings

PET_DEFAULTS = {
    "visible": True,
    "x": 72,      # pixels from the top-left corner of the screen
    "y": 64,
    "scale": 1.0,
    "opacity": 0.7,    # 0.3 .. 1.0
}


def pet_path() -> str:
    return os.path.join(settings.USER_CONFIG_DIR, "pet.json")


def _read(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_pet() -> dict:
    """Merge plugin defaults + shipped config + user override, like settings."""
    out = dict(PET_DEFAULTS)
    shipped = _read(os.path.join(settings.PLUGIN_DIR, "config", "pet.json"))
    if shipped:
        out.update(shipped)
    user = _read(pet_path())
    if user:
        out.update(user)
    return out


def save_pet(updates: dict) -> dict:
    """Atomically merge `updates` into the user pet.json and return the result."""
    cur = load_pet()
    cur.update(updates or {})
    path = pet_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".pet.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return cur
