"""Executors for the allow-listed desktop actions.

Every executor runs a subprocess with a timeout and returns (ok, message).
Commands are passed as argument lists (no shell string interpolation of
voice-derived data).

Hyprland on this machine runs Omarchy's Lua configuration, so the classic
`hyprctl dispatch <dispatcher> <args>` syntax is NOT available — arguments are
parsed as Lua (`hl.dispatch(...)`) and every stock dispatcher errors out.
The correct, version-compatible way is the Lua dispatcher helpers Omarchy
registers:
  exec            ->  hl.dsp.exec_cmd("<command>")
  focus ws        ->  hl.dsp.focus({ workspace = "N" })
  move window     ->  hl.dsp.window.move({ workspace = "N" })
  close window    ->  hl.dsp.window.close()
  fullscreen      ->  hl.dsp.window.fullscreen({ mode = "fullscreen" })
  focus by addr   ->  hl.dsp.focus({ window = "address:<addr>" })
Verified live against Hyprland 0.56.2 + Omarchy 4.0.2 (2026-09-03).
"""

from __future__ import annotations

import json
import re
import subprocess
import time

from config import blocklist as blocklist_mod
from config import settings

# Media transport via the first-party omarchy.media service (MPRIS controller).

# Volume/mute via omarchy audio (shows the OSD).
_VOLUME_ACTIONS = {
    "volume_up": ["omarchy", "audio", "output", "volume", "raise"],
    "volume_down": ["omarchy", "audio", "output", "volume", "lower"],
    "toggle_mute": ["omarchy", "audio", "output", "volume", "mute-toggle"],
}

# Simple conventional toggles / launchers (no arguments).
_SIMPLE_TOGGLES = {
    "toggle_dnd": ["omarchy", "toggle", "notification", "silencing"],
    "toggle_mic": ["omarchy", "audio", "input", "mute"],
    "toggle_bar": ["omarchy", "toggle", "bar"],
    "open_clipboard": ["omarchy", "menu", "clipboard"],
    "open_emoji": ["omarchy", "menu", "emoji"],
}


def _launch_detached_cmd(command: str) -> None:
    """Launch a config-supplied command without waiting (never blocks).
    The command comes from user config (media.music_app), not from voice/AI.
    Split into argv via shlex — never through a shell."""
    if not command or not command.strip():
        return
    import shlex
    try:
        argv = shlex.split(command)
    except ValueError:
        return
    if not argv:
        return
    try:
        subprocess.Popen(
            argv, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _run(args, timeout=15):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, out or "ok"
    except subprocess.TimeoutExpired:
        return False, "command timed out after %ss" % timeout
    except FileNotFoundError:
        return False, "command not found: %s" % args[0]
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)


def _lua_str(value: str) -> str:
    """Encode a value as a Lua single-quoted string literal (safe for hyprctl dispatch)."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _hypr_dispatch(lua_code: str, timeout=15):
    """Run a Lua dispatcher expression via `hyprctl dispatch <lua>`."""
    ok, out = _run(["hyprctl", "dispatch", lua_code], timeout=timeout)
    if not ok:
        return ok, out
    if "error" in out.lower():
        return False, out
    return ok, out


def _window_address(class_name: str) -> str | None:
    """Resolve a running window's Hyprland address by class (for focusing)."""
    try:
        proc = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=8)
        clients = json.loads(proc.stdout or "[]")
    except Exception:
        return None
    needle = class_name.lower()
    for client in clients if isinstance(clients, list) else []:
        if str(client.get("class") or "").lower() == needle:
            return client.get("address")
    return None


def _open_path(path: str) -> tuple:
    """Open a file/folder with the desktop default.

    `xdg-open` hangs on Tracker3 on this machine, so prefer `gio open` (GLib),
    falling back to xdg-open only if gio is unavailable.
    Blocked (sensitive) paths are refused outright — final safety layer.
    """
    if blocklist_mod.is_blocked_path(path):
        return False, "blocked by policy: path is not allowed"
    import shutil
    if shutil.which("gio"):
        return _run(["gio", "open", path], timeout=15)
    return _run(["xdg-open", path], timeout=15)


def _state_action(action_type: str, state: str) -> tuple:
    """Run a state-carrying toggle (on/off/toggle) via the conventional command."""
    if action_type == "set_wifi" and state == "toggle":
        ok, out = _run(["nmcli", "radio", "wifi"])
        if ok:
            state = "off" if "enabled" in out.lower() else "on"
    if action_type == "set_nightlight":
        method = {"on": "enable", "off": "disable", "toggle": "toggle"}.get(state, "toggle")
        return _run(["omarchy-shell", "nightlight", method], timeout=15)
    if action_type == "set_bluetooth":
        return _run(["omarchy", "bluetooth", "power", state], timeout=15)
    if action_type == "set_wifi":
        return _run(["nmcli", "radio", "wifi", state], timeout=15)
    if action_type == "set_touchpad":
        return _run(["omarchy", "toggle", "touchpad", state], timeout=15)
    if action_type == "set_power_mode":
        return _run(["omarchy", "powerprofiles", "set", "autodetect", state], timeout=15)
    return False, "unknown state action"


def _launch_or_focus(target: dict) -> tuple:
    running = bool(target.get("running"))
    class_name = target.get("class")
    command = target.get("command")
    # Focus an already-running instance by its window address.
    if running and class_name:
        address = _window_address(class_name)
        if address:
            ok, out = _hypr_dispatch("hl.dsp.focus({ window = %s })" % _lua_str("address:" + address))
            if ok:
                return True, "focused %s" % class_name
    # Launch (focus-on-launch is handled by the app/omarchy-launch-or-focus).
    if not command:
        return False, "no launch command resolved"
    return _hypr_dispatch("hl.dsp.exec_cmd(%s)" % _lua_str(command))


def _media_status() -> dict | None:
    """Parsed omarchy.media status (hasPlayer / canTogglePlaying / canGoNext ...)."""
    ok, out = _run(["omarchy-shell", "media", "status"], timeout=10)
    if not ok:
        return None
    try:
        data = json.loads(out)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _media_play_pause(cfg: dict) -> tuple:
    status = _media_status()
    if status and status.get("canTogglePlaying"):
        ok, out = _run(["omarchy-shell", "media", "playPause"], timeout=10)
        return (ok, out or "ok") if ok else (False, out)
    if status and status.get("hasPlayer"):
        # A player is open but has no playable track yet.
        return True, "media player is open but nothing is playing yet"
    # No player at all: launch the configured music app (conventional launcher).
    music_app = (cfg.get("media") or {}).get("music_app", "omarchy launch spotify")
    if music_app.strip():
        _launch_detached_cmd(music_app)
    deadline = time.time() + 3
    while time.time() < deadline:
        time.sleep(1.0)
        s = _media_status()
        if s and s.get("canTogglePlaying"):
            ok, out = _run(["omarchy-shell", "media", "playPause"], timeout=10)
            return True, "launched music app and started playback"
    if music_app.strip():
        return True, "launched music app (nothing playing yet)"
    return False, "no media player available"


def _media_skip(direction: str, want: str) -> tuple:
    status = _media_status()
    if status and status.get(want):
        ok, out = _run(["omarchy-shell", "media", direction], timeout=10)
        return (ok, out or "ok") if ok else (False, out)
    return False, "no active track to skip"


def _media(action_type: str, cfg: dict) -> tuple:
    """Control the active MPRIS player via omarchy.media (conventional, generic)."""
    if action_type == "play_pause_media":
        return _media_play_pause(cfg)
    if action_type == "next_track":
        return _media_skip("next", "canGoNext")
    return _media_skip("previous", "canGoPrevious")


def execute(action: dict, cfg: dict) -> tuple:
    """Run a fully parsed + confirmed Action. Returns (ok, message)."""
    action_type = action.get("type", "")
    target = action.get("target") or {}

    if action_type in ("open_app", "focus_app"):
        return _launch_or_focus(target)

    if action_type in ("play_pause_media", "next_track", "previous_track"):
        return _media(action_type, cfg)

    if action_type in _VOLUME_ACTIONS:
        return _run(_VOLUME_ACTIONS[action_type], timeout=15)

    if action_type == "open_file":
        return _open_path(target.get("path", ""))

    if action_type == "open_folder":
        return _open_path(target.get("path", ""))

    if action_type == "close_active_window":
        return _hypr_dispatch("hl.dsp.window.close()")

    if action_type == "close_all_windows":
        return _run(["omarchy", "hyprland", "window", "close", "all"], timeout=20)

    if action_type == "maximize_window":
        return _hypr_dispatch('hl.dsp.window.fullscreen({ mode = "maximized" })')

    if action_type == "toggle_tiled_fullscreen":
        return _run(["omarchy", "hyprland", "window", "tiled", "fullscreen", "toggle"], timeout=15)

    if action_type == "toggle_window_gaps":
        return _run(["omarchy", "hyprland", "window", "gaps", "toggle"], timeout=15)

    if action_type == "toggle_window_transparency":
        return _run(["omarchy", "hyprland", "window", "transparency", "toggle"], timeout=15)

    if action_type == "wake_screen":
        return _run(["omarchy", "system", "wake"], timeout=15)

    if action_type == "toggle_weather":
        return _run(["omarchy", "notification", "weather"], timeout=15)

    if action_type == "extract_screen_text":
        return _run(["omarchy", "capture", "text"], timeout=30)

    if action_type == "scan_qr":
        return _run(["omarchy", "capture", "qr"], timeout=30)

    if action_type == "switch_workspace":
        return _hypr_dispatch("hl.dsp.focus({ workspace = %s })" % _lua_str(str(target.get("id"))))

    if action_type == "move_active_window_to_workspace":
        return _hypr_dispatch("hl.dsp.window.move({ workspace = %s })" % _lua_str(str(target.get("id"))))

    if action_type == "toggle_fullscreen":
        return _hypr_dispatch('hl.dsp.window.fullscreen({ mode = "fullscreen" })')

    if action_type == "take_screenshot":
        mode = cfg.get("screenshot", {}).get("mode", "fullscreen")
        target_kind = cfg.get("screenshot", {}).get("target", "save")
        return _run(["omarchy", "capture", "screenshot", mode, target_kind], timeout=30)

    if action_type == "lock_screen":
        return _run(["omarchy", "system", "lock"], timeout=20)

    # ---- batch 1: conventional toggles ----
    if action_type in ("toggle_dnd", "toggle_mic", "toggle_bar"):
        return _run(_SIMPLE_TOGGLES[action_type], timeout=15)

    if action_type in ("open_clipboard", "open_emoji"):
        return _run(_SIMPLE_TOGGLES[action_type], timeout=15)

    if action_type in ("set_nightlight", "set_bluetooth", "set_wifi", "set_touchpad", "set_power_mode"):
        return _state_action(action_type, target.get("state", "toggle"))

    # ---- batch 2: parameterized ----
    if action_type == "set_reminder":
        minutes = int(target.get("minutes", 1))
        message = str(target.get("message") or "")
        return _run(["omarchy", "reminder", str(minutes), message], timeout=15)

    if action_type == "brightness_up":
        return _run(["omarchy", "brightness", "display", "+10%"], timeout=15)

    if action_type == "brightness_down":
        return _run(["omarchy", "brightness", "display", "10%-"], timeout=15)

    # ---- batch 3: destructive / privacy-sensitive ----
    if action_type in ("shutdown", "reboot", "logout"):
        return _run(["omarchy", "system", action_type], timeout=30)

    if action_type == "screen_record_start":
        return _run(["omarchy", "capture", "screenrecording", "--fullscreen"], timeout=20)

    if action_type == "screen_record_stop":
        return _run(["omarchy", "capture", "screenrecording", "--stop-recording"], timeout=20)

    return False, "unknown action type: %s" % action_type
