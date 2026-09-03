"""Speech-to-text via the locally installed Voxtype.

Integration contract (verified 2026-09-03 against Voxtype 1.0.0):
- `voxtype transcribe <wav>` prints the final transcript to STDOUT and never
  injects text into the focused application. This is the ONLY transcript
  channel we use.
- The daemon's `voxtype record start/stop` path always applies `output.mode`
  (types into the focused app / copies to clipboard), so it is deliberately
  NOT used. We record our own 16 kHz mono WAV and hand it to `transcribe`.
- We never modify the user's ~/.config/voxtype/config.toml; engine/model/
  language are passed per-invocation only when configured.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

VOXTYPE = shutil.which("voxtype")
RECORDER_BIN = shutil.which("pw-record")
MODELS_DIR = os.path.expanduser("~/.local/share/voxtype/models")

_ASSISTANT_CFG_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "omarchy-voice-control", "voxtype-assistant.toml")


def _write_assistant_config(cfg: dict) -> str:
    """Write the assistant's own voxtype config (fast int8 engine) so the user's
    dictation config (~/.config/voxtype/config.toml) is never touched.
    Returns the config path."""
    stt = cfg.get("stt") or {}
    engine = stt.get("engine") or "sensevoice"
    if engine == "auto":
        engine = "sensevoice"
    model = stt.get("model") or "small-int8"  # 2x faster load/infer than fp32
    language = stt.get("language") or "zh"
    content = (
        'engine = "%s"\n'
        '[sensevoice]\n'
        'model = "%s"\n'
        'language = "%s"\n'
        'use_itn = true\n'
        'on_demand_loading = false\n'
        '[output]\n'
        'mode = "file"\n'  # irrelevant for transcribe (stdout), kept harmless
    ) % (engine, model, language)
    os.makedirs(os.path.dirname(_ASSISTANT_CFG_PATH), exist_ok=True)
    with open(_ASSISTANT_CFG_PATH, "w", encoding="utf-8") as fh:
        fh.write(content)
    return _ASSISTANT_CFG_PATH


class STTError(RuntimeError):
    """Raised when transcription cannot be produced safely."""


def transcribe(wav_path: str, cfg: dict) -> str:
    """Return the final transcript text. Never injects input."""
    if not VOXTYPE:
        raise STTError("voxtype binary not found on PATH")
    _write_assistant_config(cfg)
    cmd = [VOXTYPE, "-q", "-c", _ASSISTANT_CFG_PATH, "transcribe", wav_path]
    timeout = int((cfg.get("stt") or {}).get("timeout_secs", 120))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise STTError("voxtype transcribe timed out after %ss" % timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise STTError("voxtype transcribe failed: %s" % (detail[-1][:300] if detail else proc.returncode))
    text = (proc.stdout or "").strip()
    # Voxtype occasionally emits a leading log line on stdout for the file path.
    if "\n" in text:
        text = text.splitlines()[-1].strip()
    return text


def check_status(cfg: dict) -> dict:
    """Probe Voxtype + recorder availability for the UI provider status line."""
    stt = cfg.get("stt") or {}
    info = {
        "available": False,
        "voxtype": False,
        "daemon": False,
        "engine": stt.get("engine") or "auto",
        "model": stt.get("model"),
        "backend": None,
        "recorder": False,
        "model_installed": False,
        "state": "unknown",
        "message": "",
    }
    if not VOXTYPE:
        info["message"] = "voxtype binary not found"
        return info
    info["voxtype"] = True

    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", "voxtype"],
            capture_output=True, text=True, timeout=10,
        )
        info["daemon"] = proc.stdout.strip() == "active"
    except Exception:
        pass

    if os.path.isdir(MODELS_DIR):
        try:
            installed = {n for n in os.listdir(MODELS_DIR) if os.path.isdir(os.path.join(MODELS_DIR, n))}
            info["model_installed"] = bool(installed)
            if info["model"] and info["model"] in installed:
                info["model_installed"] = True
        except OSError:
            pass

    info["recorder"] = RECORDER_BIN is not None

    try:
        proc = subprocess.run(
            [VOXTYPE, "status", "--format", "json", "--extended"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            line = (proc.stdout or "").strip().splitlines()[-1]
            obj = json.loads(line)
            info["state"] = obj.get("alt", "unknown")
            info["backend"] = obj.get("backend")
            if not info["model"]:
                info["model"] = obj.get("model")
            if obj.get("model"):
                info["model_installed"] = True
    except Exception:
        pass

    info["available"] = bool(info["voxtype"] and info["recorder"] and info["model_installed"])
    if not info["available"]:
        missing = []
        if not info["voxtype"]:
            missing.append("voxtype")
        if not info["recorder"]:
            missing.append("pw-record")
        if not info["model_installed"]:
            missing.append("model")
        info["message"] = "missing: %s" % ", ".join(missing)
    return info
