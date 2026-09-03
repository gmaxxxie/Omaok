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

from config import blocklist as blocklist_mod
from config import settings


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


def execute(action: dict, cfg: dict) -> tuple:
    """Run a fully parsed + confirmed Action. Returns (ok, message)."""
    action_type = action.get("type", "")
    target = action.get("target") or {}

    if action_type in ("open_app", "focus_app"):
        return _launch_or_focus(target)

    if action_type == "open_file":
        return _open_path(target.get("path", ""))

    if action_type == "open_folder":
        return _open_path(target.get("path", ""))

    if action_type == "close_active_window":
        return _hypr_dispatch("hl.dsp.window.close()")

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

    return False, "unknown action type: %s" % action_type
