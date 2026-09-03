"""Shared runtime state for omarchy-voice-control.

Single JSON file is the source of truth that the QML shell watches via
FileView. Every CLI subcommand reads, mutates, and atomically rewrites it.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

_RUNTIME_BASE = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"


def runtime_dir() -> str:
    path = os.path.join(_RUNTIME_BASE, "omarchy-voice-control")
    os.makedirs(path, exist_ok=True)
    return path


def state_path() -> str:
    return os.path.join(runtime_dir(), "state.json")


def raw_path() -> str:
    return os.path.join(runtime_dir(), "recording.raw")


def wav_path() -> str:
    return os.path.join(runtime_dir(), "recording.wav")


def recorder_pid_path() -> str:
    """Persists the active pw-record PID so record start/stop/cancel (separate
    CLI processes) can reliably find and kill the recorder across calls."""
    return os.path.join(runtime_dir(), "recorder.pid")


def audit_path() -> str:
    path = os.path.expanduser("~/.local/state/omarchy-voice-control/audit.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


DEFAULT_STATE = {
    "schema": 1,
    "phase": "idle",  # idle | recording | transcribing | awaiting_confirm | executing | result
    "transcript": "",
    "action": None,
    "target_desc": "",
    "error": "",
    "result": None,
    "provider": None,
    "updated_at": "",
}


def load_state() -> dict:
    try:
        with open(state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return dict(DEFAULT_STATE)
        merged = dict(DEFAULT_STATE)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_STATE)


def write_state(data: dict) -> dict:
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path = state_path()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return data


def audit(message: str) -> None:
    try:
        with open(audit_path(), "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), message))
    except Exception:
        pass
