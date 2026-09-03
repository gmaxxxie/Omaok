"""Operation memory: learned voice->action cache.

Stores recent successful interpretations (transcript + resolved Action draft)
so a repeated or near-identical command can skip the AI intent layer and replay
the known draft instantly. Read *before* the AI path; the cached draft is still
re-resolved and re-validated (resolver + policy) on every hit so safety stays
current (e.g. blocklist/path changes take effect immediately).

This only speeds up the AI path (~2.5s -> ~0s for repeats); STT and rules are
unaffected (already fast / unavoidable).
"""

from __future__ import annotations

import difflib
import json
import os
import tempfile
import time

from config import settings

MAX_ENTRIES = 200
FUZZY_THRESHOLD = 0.92  # conservative: only near-identical phrasing replays


def memory_path() -> str:
    return os.path.join(settings.USER_CONFIG_DIR, "memory.json")


def load_memory() -> list:
    try:
        with open(memory_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data if isinstance(data, list) else []
        return [e for e in entries if isinstance(e, dict)][:MAX_ENTRIES]
    except Exception:
        return []


def save_memory(entries: list) -> None:
    os.makedirs(settings.USER_CONFIG_DIR, exist_ok=True)
    path = memory_path()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".memory.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entries[:MAX_ENTRIES], fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def remember(draft: dict, transcript: str, confidence: float) -> None:
    """Add/refresh an entry for a successful interpretation (dedupe by normalized text)."""
    norm = _norm(transcript)
    if not norm or not draft or not draft.get("type"):
        return
    from intent import rules
    if draft["type"] not in rules.ALLOWED_TYPES:
        return
    entries = load_memory()
    entry = {
        "norm": norm,
        "text": transcript,
        "draft": {
            "type": draft["type"],
            "raw_target": str(draft.get("raw_target") or ""),
            "source": str(draft.get("source") or "rule"),
            "confidence": float(confidence),
        },
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    entries = [e for e in entries if e.get("norm") != norm]
    entries.insert(0, entry)
    save_memory(entries)


def _norm(text: str) -> str:
    from intent import rules
    return rules.normalize(text or "")


def lookup(transcript: str, entries: list | None = None) -> dict | None:
    """Best matching cached entry (exact first, then conservative fuzzy)."""
    norm = _norm(transcript)
    if not norm:
        return None
    entries = entries if entries is not None else load_memory()
    exact = None
    best = None
    best_ratio = 0.0
    for e in entries:
        e_norm = str(e.get("norm") or "")
        if not e_norm:
            continue
        if e_norm == norm:
            exact = e
            break
        ratio = difflib.SequenceMatcher(None, norm, e_norm).ratio()
        if ratio > best_ratio:
            best, best_ratio = e, ratio
    if exact is not None:
        return exact
    if best is not None and best_ratio >= FUZZY_THRESHOLD and len(norm) >= 2:
        return best
    return None
