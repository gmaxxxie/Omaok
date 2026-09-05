"""omaok chat memory: conversation thread (short-term) + durable facts (long-term).

Short-term memory — the active conversation thread (chat.json):
  rolling window of recent turns + a regenerated summary of older turns
  (sliding-window + rolling-summary pattern).

Long-term memory — durable facts/preferences (memory/facts.json):
  distilled from finished conversations, retrieved per-turn by
  keyword overlap + recency + importance.

Everything is local JSON written atomically. This module has NO model calls —
the orchestrator (intent/chat.py) supplies the model for summarization and
consolidation, so this stays fast and testable.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid

from config import settings

# An idle gap this long ends a conversation session (default 10 min).
GAP_SECONDS = 600
# Rolling window of recent turns kept verbatim (older turns go into `summary`).
MAX_TURNS = 8
# How many long-term facts to retrieve per turn.
RETRIEVE_TOP_K = 5

DEFAULT_CATEGORY = "fact"
CATEGORIES = ("preference", "fact", "identity", "todo", "other")


# ---------------------------------------------------------------------------
# paths + atomic persistence
# ---------------------------------------------------------------------------

def session_path() -> str:
    return os.path.join(settings.USER_CONFIG_DIR, "chat.json")


def facts_dir() -> str:
    p = os.path.join(settings.USER_CONFIG_DIR, "memory")
    os.makedirs(p, exist_ok=True)
    return p


def facts_path() -> str:
    return os.path.join(facts_dir(), "facts.json")


def _atomic_write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
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


# ---------------------------------------------------------------------------
# short-term: conversation thread
# ---------------------------------------------------------------------------

def empty_session() -> dict:
    now = time.time()
    return {
        "session_id": uuid.uuid4().hex[:12],
        "active": False,
        "started_at": now,
        "last_turn_at": now,
        "turns": [],          # rolling window (<= MAX_TURNS)
        "summary": "",        # rolling summary of older turns
        "pending": [],        # turns of a finished thread awaiting consolidation
    }


def load_session() -> dict:
    try:
        with open(session_path(), encoding="utf-8") as fh:
            s = json.load(fh)
        if isinstance(s, dict) and "turns" in s:
            s.setdefault("pending", [])
            s.setdefault("summary", "")
            return s
    except (OSError, ValueError):
        pass
    return empty_session()


def save_session(s: dict) -> None:
    _atomic_write(session_path(), s)


def start_session() -> dict:
    s = empty_session()
    s["active"] = True
    save_session(s)
    return s


def should_rollover(s: dict, now: float | None = None) -> bool:
    """True when the next chat must start a fresh thread (inactive or idle too long)."""
    now = now if now is not None else time.time()
    if not s.get("active"):
        return True
    return (now - float(s.get("last_turn_at", now))) > GAP_SECONDS


def park() -> dict:
    """End the conversational context (used at session end). Cheap: stashes the
    turns for later consolidation and clears the thread. The model-based
    consolidation runs lazily at the next chat start so nothing blocks here."""
    s = load_session()
    if s.get("turns"):
        s["pending"] = list(s.get("pending", [])) + list(s["turns"])
    s["turns"] = []
    s["summary"] = ""
    s["active"] = False
    save_session(s)
    return s


def append_turn(s: dict, role: str, text: str) -> None:
    s.setdefault("turns", []).append({"role": role, "text": text, "ts": time.time()})
    s["active"] = True
    s["last_turn_at"] = time.time()


def trimmed(s: dict, max_turns: int = MAX_TURNS):
    """(keep, overflow) — overflow turns are candidates for summarization."""
    turns = list(s.get("turns", []))
    if len(turns) <= max_turns:
        return turns, []
    return turns[-max_turns:], turns[:-max_turns]


def recent_turns(s: dict, n: int = MAX_TURNS) -> list:
    return list(s.get("turns", []))[-n:]


def set_summary(s: dict, text: str) -> None:
    s["summary"] = (text or "").strip()


def history_text(s: dict, n: int = MAX_TURNS) -> str:
    lines = []
    for t in recent_turns(s, n):
        lines.append("%s: %s" % ("user" if t.get("role") == "user" else "omaok", t.get("text", "")))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# long-term: durable facts
# ---------------------------------------------------------------------------

def load_facts() -> list:
    try:
        with open(facts_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (OSError, ValueError):
        pass
    return []


def save_facts(facts: list) -> None:
    _atomic_write(facts_path(), facts)


def add_fact(facts: list, text: str, category: str = DEFAULT_CATEGORY,
             source: str = "", ts: float | None = None) -> dict:
    f = {
        "id": uuid.uuid4().hex[:8],
        "text": (text or "").strip()[:300],
        "category": category if category in CATEGORIES else DEFAULT_CATEGORY,
        "ts": ts if ts is not None else time.time(),
        "source_turn": (source or "")[:200],
        "hits": 0,
        "last_used": 0.0,
    }
    facts.append(f)
    return f


def _tokens(text: str) -> set:
    """Word / CJK-bigram tokenization for zh + en keyword matching."""
    t = (text or "").lower()
    out = set()
    for run in re.findall(r"[\u4e00-\u9fff]+|[a-z0-9]+", t):
        if re.fullmatch(r"[\u4e00-\u9fff]+", run):
            if len(run) == 1:
                out.add(run)
            else:
                for i in range(len(run) - 1):
                    out.add(run[i:i + 2])
        elif len(run) >= 2:
            out.add(run)
    return out


def score_fact(text: str, fact: dict, now: float | None = None) -> float:
    """keyword overlap (dominant) + recency + importance."""
    now = now if now is not None else time.time()
    q = _tokens(text)
    f = _tokens(fact.get("text", ""))
    if not q or not f:
        return 0.0
    overlap = len(q & f)
    if overlap == 0:
        return 0.0
    kw = overlap / (len(q) ** 0.5 + 1)                      # 0..~1, len-normalized
    age_days = max(0.0, (now - float(fact.get("ts", now))) / 86400.0)
    recency = 1.0 / (1.0 + age_days / 7.0)                  # ~half-life a week
    importance = min(1.0, float(fact.get("hits", 0)) / 10.0)
    return kw * 2.0 + recency * 0.5 + importance * 0.5


def retrieve(text: str, facts: list, k: int = RETRIEVE_TOP_K,
             now: float | None = None) -> list:
    """Top-K relevant facts for a turn (keyword+recency+importance). Bumps hits."""
    now = now if now is not None else time.time()
    scored = [(score_fact(text, f, now), f) for f in facts if score_fact(text, f, now) > 0]
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [f for _, f in scored[:k]]
    for f in top:
        f["hits"] = int(f.get("hits", 0)) + 1
        f["last_used"] = now
    if top:
        save_facts(facts)
    return top


def _find_fact(facts: list, text: str) -> dict | None:
    """Near-duplicate fact lookup (>=70% token overlap)."""
    toks = _tokens(text)
    if not toks:
        return None
    for f in facts:
        inter = len(toks & _tokens(f.get("text", "")))
        denom = max(1, min(len(toks), len(_tokens(f.get("text", "")))))
        if inter > 0 and inter / denom >= 0.7:
            return f
    return None


def merge_consolidated(facts: list, new_items: list) -> dict:
    """Merge distilled items into facts with dedupe. Returns count summary."""
    res = {"added": 0, "merged": 0, "skipped": 0}
    for it in new_items or []:
        text = str(it.get("text") or "").strip() if isinstance(it, dict) else ""
        if len(text) < 3:
            res["skipped"] += 1
            continue
        existing = _find_fact(facts, text)
        if existing is not None:
            existing["hits"] = int(existing.get("hits", 0)) + 1
            existing["last_used"] = time.time()
            res["merged"] += 1
            continue
        add_fact(facts, text, str(it.get("category") or DEFAULT_CATEGORY))
        res["added"] += 1
    if res["added"] or res["merged"]:
        save_facts(facts)
    return res


def forget(text: str, facts: list) -> int:
    """Remove facts matching the topic (token overlap OR substring).
    Returns removed count."""
    q = _tokens(text)
    qsub = (text or "").strip()
    before = len(facts)
    keep = []
    for f in facts:
        ft = str(f.get("text", ""))
        hit = False
        if q and (q & _tokens(ft)):
            hit = True
        elif qsub and (qsub in ft or ft in qsub):
            hit = True
        if not hit:
            keep.append(f)
    facts[:] = keep
    if len(facts) != before:
        save_facts(facts)
    return before - len(facts)


# ---------------------------------------------------------------------------
# explicit memory / conversation commands (offline)
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    t = re.sub(r"[，。！？、；：“”‘’《》【】（）…·!?.,;:()\[\]\"'<>{}|/\\]", " ", text or "")
    return re.sub(r"\s+", " ", t).strip()


_REMEMBER_RE = re.compile(
    r"^(?:记住|记下|帮我记住|别忘了|请记住|remember)\s*(?:一下|一点)?\s*(.+)$", re.I)
_FORGET_RE = re.compile(
    r"^(?:忘掉|忘记|删掉|清除|忘了|不记得)\s*(?:关于|有关|我)?\s*(.+?)(?:的)?(?:记忆|事情|信息|事)?\s*$"
    r"|^forget\s*(?:about\s*)?(.+)$", re.I)
_RECALL_RE = re.compile(
    r"上次聊到哪|聊到哪了|我们聊到哪|刚才聊到哪|说到哪了|说到哪|上次聊了什么|我们之前聊了什么"
    r"|what were we (?:talking|chatting|discussing) about|where did we leave off|what did we (?:talk|chat) about",
    re.I)
_END_RE = re.compile(
    r"结束对话|退出对话|结束聊天|不聊了|先聊到这|聊完了|再见|拜拜|今天就到这|到此为止"
    r"|end (?:the )?conversation|stop chatting|exit chat|bye for now|that's all for now",
    re.I)
_CHATMODE_ON_RE = re.compile(
    r"开始对话|进入对话模式|开启对话|开启对话模式|开始聊天|免提对话", re.I)
_CHATMODE_OFF_RE = re.compile(
    r"退出对话模式|关闭对话模式|停止对话模式|退出对话|免提关闭|关掉对话模式", re.I)

# Natural closing remarks that wrap up a conversation (offline, conservative:
# FULL-match on a short utterance only, so a politeness prefix in a real
# follow-up like "好的谢谢，那预算呢" is NOT treated as a closer).
_CLOSING_RE = re.compile(
    r"^(?:"
    r"好的(?:吧|呢|啦|嘞)?|好嘞|好哒|好呀|明白了|懂了|知道了|嗯好|可以了|行吧|"
    r"没事了|没别的事了|没有了|就这(?:样|么)吧|先这样|就到这(?:里|儿)?|"
    r"谢谢|多谢|辛苦(?:了|啦)?|感谢|麻烦你了|"
    r"thanks|thank you|got it|ok(?:ay)?|that\s*s\s*all|all good|fine,? thanks|sounds good|no problem"
    r")"
    r"(?:\s*(?:谢谢|多谢|辛苦(?:了|啦)?|感谢|ok(?:ay)?|thanks|thank you|got it))?\s*$",
    re.I,
)


def is_closing(text: str) -> bool:
    """True when the utterance is a pure closing remark (offline). Conservative:
    only short full matches, so real follow-ups never get cut off."""
    if not text or not text.strip():
        return False
    t = normalize(text)
    if not t or len(t) > 16:
        return False
    return bool(_CLOSING_RE.search(t))


def explicit_command(text: str):
    """Detect an explicit memory/conversation command. Returns
    ("remember"|"forget"|"recall"|"end", arg) or None."""
    if not text or not text.strip():
        return None
    t = normalize(text)
    m = _REMEMBER_RE.search(t)
    if m:
        return ("remember", (m.group(1) or "").strip())
    m = _FORGET_RE.search(t)
    if m:
        arg = next((g for g in m.groups() if g and g.strip()), None)
        return ("forget", (arg or "").strip())
    if _CHATMODE_ON_RE.search(t):
        return ("chatmode_on", "")
    if _CHATMODE_OFF_RE.search(t):
        return ("chatmode_off", "")
    if _RECALL_RE.search(t):
        return ("recall", "")
    if _END_RE.search(t):
        return ("end", "")
    return None
