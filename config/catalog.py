"""Machine command catalog generator.

Builds a snapshot of this machine's desktop capability surface so a local AI
agent (pi RPC mode) can ground intent understanding in what actually exists
here — instead of hallucinating commands. This is a *suggestion* catalog, not a
permission grant: the AI may only emit structured Actions that still pass the
allowlist + policy + confirmation gate.

Regenerate with:  omarchy-voice-control catalog [--refresh]
Output:           ~/.config/omarchy/voice-control/catalog.json
"""

from __future__ import annotations

import json
import os
import re
import subprocess

from config import settings


def _run(args, timeout=20):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode == 0, proc.stdout
    except Exception:
        return False, ""


ACTION_DESCRIPTIONS = {
    "open_app": "Launch (or focus) an application",
    "focus_app": "Focus an already-running application",
    "open_file": "Open a file with its default application",
    "open_folder": "Open a folder in the file manager",
    "close_active_window": "Close the currently focused window",
    "switch_workspace": "Switch to a numbered workspace",
    "move_active_window_to_workspace": "Move the active window to a numbered workspace",
    "toggle_fullscreen": "Toggle fullscreen on the active window",
    "take_screenshot": "Take a fullscreen screenshot and save it",
    "lock_screen": "Lock the screen",
    "play_pause_media": "Play or pause the current media (music)",
    "next_track": "Skip to the next track",
    "previous_track": "Go to the previous track",
    "volume_up": "Raise the output volume",
    "volume_down": "Lower the output volume",
    "toggle_mute": "Toggle output mute",
}

_HYPRLAND_DISPATCHERS = [
    "hl.dsp.exec_cmd(\"<command>\") — launch/execute a command",
    "hl.dsp.focus({ workspace = \"N\" }) — switch to workspace N",
    "hl.dsp.focus({ window = \"address:<addr>\" }) — focus a specific window",
    "hl.dsp.window.move({ workspace = \"N\" }) — move active window to workspace N",
    "hl.dsp.window.close() — close active window",
    "hl.dsp.window.fullscreen({ mode = \"fullscreen\" }) — toggle fullscreen",
]


def _omarchy_commands() -> list:
    ok, out = _run(["omarchy", "commands", "--json"])
    if not ok:
        return []
    try:
        data = json.loads(out)
        commands = data.get("commands", []) if isinstance(data, dict) else []
    except Exception:
        return []
    rows = []
    for c in commands if isinstance(commands, list) else []:
        rows.append({
            "route": c.get("route", ""),
            "binary": c.get("binary", ""),
            "summary": c.get("summary", ""),
            "args": c.get("args", ""),
            "sudo": bool(c.get("requires_sudo", False)),
        })
    return rows


def _apps() -> list:
    """Installed .desktop applications (Name + cleaned Exec + comment)."""
    apps = []
    dirs = [
        "/usr/share/applications",
        os.path.expanduser("~/.local/share/applications"),
    ]
    seen = set()
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".desktop"):
                continue
            if name in seen:
                continue
            seen.add(name)
            try:
                with open(os.path.join(directory, name), "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            entry = {"name": "", "exec": "", "comment": "", "desktop": name}
            section = None
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1]
                    continue
                if section != "Desktop Entry":
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    if key == "Name" and not entry["name"]:
                        entry["name"] = value
                    elif key == "Comment" and not entry["comment"]:
                        entry["comment"] = value
                    elif key == "Exec":
                        entry["exec"] = re.sub(r"\s*%[UuFfDdNnickvm]\s*", " ", value).strip()
            if entry["name"]:
                apps.append(entry)
    apps.sort(key=lambda a: a["name"].lower())
    return apps


def _user_bins() -> list:
    bins = []
    for directory in (os.path.expanduser("~/.local/bin"), os.path.expanduser("~/bin")):
        if os.path.isdir(directory):
            for name in sorted(os.listdir(directory)):
                path = os.path.join(directory, name)
                if os.path.isfile(path) and os.access(path, os.X_OK) and not name.startswith("."):
                    bins.append(name)
    return sorted(set(bins))


def generate_catalog() -> dict:
    return {
        "generated_at": "",
        "machine": _machine(),
        "actions": [
            {"type": t, "description": ACTION_DESCRIPTIONS[t],
             "risk": "confirm_required" if t in ("close_active_window", "toggle_fullscreen", "lock_screen") else "low"}
            for t in sorted(ACTION_DESCRIPTIONS)
        ],
        "omarchy_commands": _omarchy_commands(),
        "apps": _apps(),
        "user_bins": _user_bins(),
        "hyprland_dispatchers": _HYPRLAND_DISPATCHERS,
    }


def _machine() -> str:
    bits = []
    ok, out = _run(["hyprctl", "version"])
    if ok:
        first = (out or "").splitlines()[0]
        bits.append(first.strip())
    ok, out = _run(["omarchy", "version"])
    if ok:
        bits.append("omarchy " + (out or "").strip())
    return " / ".join(bits)


def write_catalog(cfg: dict) -> str:
    path = os.path.join(settings.USER_CONFIG_DIR, "catalog.json")
    os.makedirs(settings.USER_CONFIG_DIR, exist_ok=True)
    import time
    catalog = generate_catalog()
    catalog["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path
