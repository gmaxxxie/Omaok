"""Target resolution for parsed Actions.

Resolves a raw target string into a structured, verified `target` object and
adjusts confidence. All lookups are local: installed .desktop entries, running
Hyprland clients, configured aliases, and allow-listed directory roots.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess

from config import settings

_DESKTOP_DIRS = [
    "/usr/share/applications",
    os.path.expanduser("~/.local/share/applications"),
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
]


def _run(args, timeout=8):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.stdout
    except Exception:
        return ""


def _clients() -> list:
    """Parsed `hyprctl clients -j` list, or [] on failure."""
    out = _run(["hyprctl", "clients", "-j"])
    try:
        import json
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def active_window() -> dict | None:
    """Parsed `hyprctl activewindow -j`, or None."""
    out = _run(["hyprctl", "activewindow", "-j"])
    try:
        import json
        data = json.loads(out)
        return data if isinstance(data, dict) and data.get("mapped") else None
    except Exception:
        return None


def _desktop_entries():
    entries = []
    for directory in _DESKTOP_DIRS:
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".desktop"):
                continue
            path = os.path.join(directory, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            desktop = _parse_desktop(text)
            desktop["file"] = name
            desktop["stem"] = name[:-len(".desktop")]
            entries.append(desktop)
    return entries


def _parse_desktop(text: str) -> dict:
    info = {"Name": None, "Exec": None, "Type": None}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            continue
        if current != "Desktop Entry":
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            if key in info and info[key] is None:
                info[key] = value.strip()
    return info


def _cmd_exists(command: str) -> bool:
    if not command:
        return False
    first = command.split()[0]
    return any(
        os.path.isfile(os.path.join(p, first)) and os.access(os.path.join(p, first), os.X_OK)
        for p in os.environ.get("PATH", "").split(":")
    )


def _match_app(name: str, aliases: dict) -> dict | None:
    """Resolve an app alias/name to a launch command + WM class."""
    lowered = name.strip().lower()
    if not lowered:
        return None

    # 1) explicit alias
    alias = (aliases.get("apps") or {}).get(lowered)
    if alias:
        return _candidate(alias, name)

    # 2) running clients (class or title match)
    needle = re.escape(lowered)
    clients = _clients()
    for client in clients:
        cls = str(client.get("class") or "")
        title = str(client.get("title") or "")
        if cls and lowered in cls.lower():
            return _candidate(cls, name, class_name=cls, running=True)
        if title and lowered in title.lower() and len(lowered) >= 3:
            return _candidate(cls or lowered, name, class_name=cls, running=True)

    # 3) installed .desktop entries
    entries = _desktop_entries()
    exact = None
    for entry in entries:
        entry_name = (entry.get("Name") or "").lower()
        stem = entry.get("stem") or ""
        if entry_name == lowered or stem == lowered:
            exact = entry
            break
    if exact is None:
        for entry in entries:
            entry_name = (entry.get("Name") or "").lower()
            if entry_name and lowered in entry_name:
                exact = entry
                break
    if exact:
        command = _clean_exec((exact.get("Exec") or "").strip())
        return _candidate(command or exact.get("stem") or lowered, name, class_name=exact.get("stem"))

    # 4) on PATH
    if _cmd_exists(lowered):
        return _candidate(lowered, name)

    return None


def _clean_exec(command: str) -> str:
    """Strip .desktop Exec field codes (%U, %u, %F, %f, ...) so the result is a runnable command."""
    return re.sub(r"\s*%[UuFfDdNnickvm]\s*", " ", command).strip()


def _candidate(command: str, name: str, class_name: str | None = None, running: bool = False) -> dict:
    return {
        "kind": "app",
        "name": name,
        "command": command,
        "class": class_name,
        "running": running,
    }


def _search_roots(roots: list) -> list:
    expanded = []
    for root in roots:
        path = settings.expand_path(root)
        if os.path.isdir(path):
            expanded.append(path)
    return expanded


def _clean_target_name(name: str) -> str:
    """Strip folder/file words so aliases match: 下载文件夹 -> 下载, downloads folder -> downloads."""
    n = (name or "").strip().lower()
    n = re.sub(r"(文件夹|目录|文件)\s*$", "", n)          # Chinese suffix
    n = re.sub(r"^(the\s+)?(.+?)\s*(folder|directory|file)s?$", r"\2", n)  # English suffix
    n = re.sub(r"\s+", " ", n).strip()
    return n


def _resolve_folder(name: str, aliases: dict, cfg: dict, strong_only: bool = False) -> dict | None:
    """Resolve a folder alias or a path under the allow-listed roots."""
    lowered = (name or "").strip()
    if not lowered:
        return None
    cleaned = _clean_target_name(lowered)
    # Alias match (original and cleaned)
    for key in (lowered.lower(), cleaned):
        alias = (aliases.get("folders") or {}).get(key)
        if alias:
            path = settings.expand_path(alias)
            if os.path.isdir(path):
                return {"kind": "folder", "path": path, "name": name}
    # Literal path (relative to HOME) that exists
    candidate = settings.expand_path(lowered)
    if os.path.isdir(candidate) and _under_roots(candidate, cfg):
        return {"kind": "folder", "path": os.path.normpath(candidate), "name": name}
    # Substring search over roots; only strong (exact/prefix) matches when asked
    needle = cleaned
    best = None
    best_score = None
    for root in _search_roots(cfg.get("paths", {}).get("roots", ["~"])):
        for base, dirs, _files in os.walk(root):
            for d in dirs:
                if not needle or needle in d.lower():
                    full = os.path.join(base, d)
                    score = _path_score(d, needle)
                    if best_score is None or score < best_score:
                        best, best_score = full, score
            if len(dirs) > 200:
                dirs[:] = []  # avoid deep traversal cost
    if best and (not strong_only or best_score <= 1):
        return {"kind": "folder", "path": best, "name": name}
    return None


def _resolve_file(name: str, aliases: dict, cfg: dict, strong_only: bool = False) -> dict | None:
    lowered = (name or "").strip()
    if not lowered:
        return None
    cleaned = _clean_target_name(lowered)
    for key in (lowered.lower(), cleaned):
        alias = (aliases.get("files") or {}).get(key)
        if alias:
            path = settings.expand_path(alias)
            if os.path.isfile(path):
                return {"kind": "file", "path": path, "name": name}
    candidate = settings.expand_path(lowered)
    if os.path.isfile(candidate) and _under_roots(candidate, cfg):
        return {"kind": "file", "path": os.path.normpath(candidate), "name": name}
    needle = cleaned
    best = None
    best_score = None
    for root in _search_roots(cfg.get("paths", {}).get("roots", ["~"])):
        for base, dirs, files in os.walk(root):
            for f in files:
                if not needle or needle in f.lower():
                    full = os.path.join(base, f)
                    score = _path_score(f, needle)
                    if best_score is None or score < best_score:
                        best, best_score = full, score
            if len(dirs) > 200:
                dirs[:] = []
    if best:
        return {"kind": "file", "path": best, "name": name}
    return None


def _path_score(basename: str, needle: str) -> int:
    """Lower score = better. Exact match beats prefix beats substring; shorter wins."""
    base = basename.lower()
    if needle == base:
        return 0
    if base.startswith(needle):
        return 1
    return 10 + len(base)


def _under_roots(path: str, cfg: dict) -> bool:
    norm = os.path.normpath(path)
    for root in _search_roots(cfg.get("paths", {}).get("roots", ["~"])):
        if norm == root or norm.startswith(root + os.sep):
            return True
    return False


def resolve_action(action: dict, cfg: dict, aliases: dict) -> dict | None:
    """Fill `target` + adjust confidence. Returns updated action or None."""
    action_type = action["type"]
    raw = (action.get("raw_target") or "").strip()
    target = None
    confidence = action.get("confidence", 0.9)

    if action_type in ("open_app", "focus_app"):
        if not raw:
            return None
        # "打开 X" with no matching app: fall back to a folder/file alias or path.
        target = _match_app(raw, aliases)
        if target is None and action_type == "open_app":
            folder = _resolve_folder(raw, aliases, cfg, strong_only=True)
            if folder:
                action["type"] = "open_folder"
                return _finish(action, folder, confidence)
            file_target = _resolve_file(raw, aliases, cfg, strong_only=True)
            if file_target:
                action["type"] = "open_file"
                return _finish(action, file_target, confidence)
        if target is None:
            return None
        if action_type == "focus_app" and not target["running"]:
            # focus on a not-running app still launches it (no focus target exists)
            pass

    elif action_type == "open_folder":
        target = _resolve_folder(raw, aliases, cfg) if raw else None
        if target is None:
            return None

    elif action_type == "open_file":
        target = _resolve_file(raw, aliases, cfg) if raw else None
        if target is None:
            return None

    elif action_type == "switch_workspace":
        try:
            ws = int(raw)
        except (TypeError, ValueError):
            return None
        lo = int(cfg.get("workspaces", {}).get("min", 1))
        hi = int(cfg.get("workspaces", {}).get("max", 10))
        if not (lo <= ws <= hi):
            return None
        target = {"kind": "workspace", "id": ws}

    elif action_type == "move_active_window_to_workspace":
        try:
            ws = int(raw)
        except (TypeError, ValueError):
            return None
        lo = int(cfg.get("workspaces", {}).get("min", 1))
        hi = int(cfg.get("workspaces", {}).get("max", 10))
        if not (lo <= ws <= hi):
            return None
        target = {"kind": "workspace", "id": ws}

    else:
        target = {}

    action["target"] = target
    return _finish(action, target, confidence)


def _finish(action: dict, target: dict, confidence: float) -> dict:
    action["target"] = target
    if action.get("type") in ("open_app", "focus_app") and target and not target.get("running"):
        confidence = min(confidence, 0.75)  # launching unverified binary -> slightly lower
    action["confidence"] = confidence
    return action


def describe(action: dict) -> str:
    """Human-readable one-line description of the resolved action for the UI."""
    t = action.get("type", "")
    target = action.get("target") or {}
    labels = {
        "open_app": "Open app",
        "focus_app": "Focus app",
        "open_file": "Open file",
        "open_folder": "Open folder",
        "close_active_window": "Close active window",
        "switch_workspace": "Switch workspace",
        "move_active_window_to_workspace": "Move active window",
        "toggle_fullscreen": "Toggle fullscreen",
        "take_screenshot": "Take screenshot",
        "lock_screen": "Lock screen",
    }
    base = labels.get(t, t)
    if t in ("open_app", "focus_app"):
        return "%s: %s" % (base, target.get("name") or target.get("command") or "")
    if t in ("open_file", "open_folder"):
        return "%s: %s" % (base, target.get("path") or "")
    if t in ("switch_workspace", "move_active_window_to_workspace"):
        return "%s: workspace %s" % (base, target.get("id"))
    return base
