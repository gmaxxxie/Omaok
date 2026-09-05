"""omaok chat layer (L4): long conversation + short/long-term memory.

For each non-command utterance it orchestrates, high->low:
  0) persona offline answers (config.character)           — no model call
  1) explicit memory commands (config.chatmem): remember / forget / recall / end
  2) session rollover (short-term memory) + lazy consolidation of the previous
     thread into long-term facts                          — at the next chat start
  3) long-term fact retrieval (keyword+recency+importance)
  4) model reply with context (intent.ai.chat_analyze)
  5) persist the turn + rolling summary (sliding window)

Returns {"kind", "reply", "via"} for the CLI to map to chat_reply / chat_defer.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime

from config import chatmem
from config import character as character_mod
from intent import ai as intent_ai

CLOSING_REPLY = "不客气～有事随时叫我。"


# ---------------------------------------------------------------------------
# Offline local-facts answers (time / date / weekday / battery).
# These are deterministic, zero-model, ~instant — the pet answers from the
# machine itself, so "现在几点 / 今天几号 / 电量多少" never touch the AI layer.
# ---------------------------------------------------------------------------

_LOCAL_FACT_PATTERNS = [
    # (regex, kind) — checked in order; weekday before date before time.
    (re.compile(r"今天星期几|今天是星期几|今天周几|今天是周几|今天礼拜几|星期几|周几|礼拜几"
                r"|what day is (?:it|today)|what day of the week", re.I), "weekday"),
    (re.compile(r"今天几号|今天是几号|今天几月几日|今天日期|现在几号|几月几日"
                r"|today'?s date|what('| i)s the date|what date(?: is today)?", re.I), "date"),
    (re.compile(r"现在几点(?:钟|了|啦)?|现在什么时间|现在时间|几点钟了|几点了|几点啦|几点钟"
                r"|what time is it|current time|what('| i)s the time", re.I), "time"),
    (re.compile(r"电量多少|电量还有多少|电量还剩|电量是多少|现在电量|还有多少电|还有电吗"
                r"|电池(?:还有|剩|多少|快没电|还有多少)|快没电了吗|电量"
                r"|battery(?: level| left| percent| percentage)?|how much battery|charge(?: left)?", re.I), "battery"),
]

_WEEKDAYS_ZH = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
_WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _has_cjk(text: str) -> bool:
    return re.search(r"[\u4e00-\u9fff]", text) is not None


def _zh_period(hour: int) -> str:
    if hour < 5:
        return "凌晨"
    if hour < 9:
        return "早上"
    if hour < 12:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    return "晚上"


def local_fact_answer(text: str) -> tuple | None:
    """Return (reply, kind) for an offline local-fact question, or None."""
    if not text or not text.strip():
        return None
    t = re.sub(r"[，。！？、；：“”‘’《》【】（）…·!?.,;:()\[\]\"'<>{}|/\\]", " ", text)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    zh = _has_cjk(text)
    now = datetime.now()
    for rx, kind in _LOCAL_FACT_PATTERNS:
        if not rx.search(t):
            continue
        if kind == "weekday":
            wd = now.weekday()
            return (("今天是%s。" % _WEEKDAYS_ZH[wd]) if zh else
                    ("It's %s." % _WEEKDAYS_EN[wd])), "local"
        if kind == "date":
            if zh:
                return ("今天是%d年%d月%d日。" % (now.year, now.month, now.day)), "local"
            return ("Today is %s %d, %d." % (now.strftime("%B"), now.day, now.year)), "local"
        if kind == "time":
            if zh:
                return ("现在是%s%d点%d分。" % (_zh_period(now.hour), now.hour % 12 or 12, now.minute)), "local"
            return ("It's %s." % now.strftime("%I:%M %p").lstrip("0")), "local"
        if kind == "battery":
            loc = _battery_reply(zh)
            if loc:
                return loc
            # battery command unavailable -> let the model path handle it
            return None
    return None


def _battery_reply(zh: bool) -> tuple | None:
    """Local battery percentage via `omarchy battery status` (conventional)."""
    try:
        proc = subprocess.run(["omarchy", "battery", "status"], capture_output=True,
                              text=True, timeout=5)
        out = (proc.stdout or "").strip()
    except Exception:
        return None
    m = re.search(r"(\d+)\s*%", out)
    if not m:
        return None
    pct = int(m.group(1))
    if zh:
        return ("电量还有 %d%%。" % pct), "local"
    return ("Battery is at %d%%." % pct), "local"


# ---------------------------------------------------------------------------
# Offline precheck — runs BEFORE the AI intent layer (cli step) so every
# offline-answerable utterance skips L3 and answers instantly.
# ---------------------------------------------------------------------------

def precheck(text: str, cfg):
    """Offline answers: persona, explicit memory commands, closing remarks, and
    local facts (time/date/weekday/battery). Returns {kind, reply, via} or None.
    Called from the CLI before the AI intent layer AND at the top of process()."""
    text = (text or "").strip()
    if not text:
        return None

    pa = character_mod.match(text)
    if pa:
        _note(text, pa, cfg)
        return {"kind": "answer", "reply": pa, "via": "persona"}

    cmd = chatmem.explicit_command(text)
    if cmd:
        return _handle_explicit(cmd, cfg)

    if chatmem.is_closing(text):
        _note(text, CLOSING_REPLY, cfg)
        chatmem.park()
        return {"kind": "answer", "reply": CLOSING_REPLY, "via": "memory", "end": True}

    loc = local_fact_answer(text)
    if loc:
        reply, via = loc
        return {"kind": "answer", "reply": reply, "via": via}

    return None


def _turns_text(turns: list) -> str:
    return "\n".join("%s: %s" % (t.get("role"), t.get("text", "")) for t in turns)


def _rollover(cfg) -> dict:
    """Consolidate the previous thread's turns into long-term facts (lazily),
    then start a fresh conversation thread. Fast: consolidation is one model
    call, only when there are pending turns."""
    s = chatmem.load_session()
    pending = list(s.get("pending", [])) + list(s.get("turns", []))
    if len(pending) >= 2:
        existing = chatmem.load_facts()
        new_items = intent_ai.consolidate_facts(_turns_text(pending), existing, cfg)
        if new_items:
            chatmem.merge_consolidated(existing, new_items)
    return chatmem.start_session()


def _maybe_summarize(cfg) -> None:
    """Fold overflow turns into the rolling summary (rolling-summary pattern)."""
    s = chatmem.load_session()
    keep, overflow = chatmem.trimmed(s)
    if not overflow:
        return
    new_summary = intent_ai.summarize_turns(_turns_text(overflow), s.get("summary", ""), cfg)
    s["turns"] = keep
    chatmem.set_summary(s, new_summary)
    chatmem.save_session(s)


def _note(text: str, reply: str, cfg) -> None:
    """Persist a persona/offline reply as part of the ongoing conversation."""
    if not reply:
        return
    s = chatmem.load_session()
    if not s.get("active"):
        s = chatmem.start_session()
    chatmem.append_turn(s, "user", text)
    chatmem.append_turn(s, "assistant", reply)
    chatmem.save_session(s)
    _maybe_summarize(cfg)


def _handle_explicit(cmd, cfg):
    kind, arg = cmd
    if kind == "remember":
        facts = chatmem.load_facts()
        chatmem.add_fact(facts, arg, "fact", source="user request")
        chatmem.save_facts(facts)
        return {"kind": "answer", "reply": "记住了：%s" % arg, "via": "memory"}
    if kind == "forget":
        facts = chatmem.load_facts()
        removed = chatmem.forget(arg, facts)
        reply = "好，相关的事情已经忘掉啦" if removed else "我好像没有这方面的记忆哦"
        return {"kind": "answer", "reply": reply, "via": "memory"}
    if kind == "recall":
        s = chatmem.load_session()
        summary = s.get("summary", "")
        turns = chatmem.recent_turns(s, 4)
        if summary or turns:
            bits = []
            if summary:
                bits.append("之前聊到：" + summary)
            if turns:
                bits.append("刚说到：" + turns[-1].get("text", ""))
            reply = "；".join(bits)[:300]
        else:
            facts = chatmem.load_facts()
            if facts:
                reply = "我记得一些关于你的事：" + "；".join(f["text"] for f in facts[-3:])
            else:
                reply = "我们好像还没好好聊过呢～"
        return {"kind": "answer", "reply": reply, "via": "memory"}
    if kind == "end":
        chatmem.park()  # stash turns for lazy consolidation at next chat
        return {"kind": "answer", "reply": "好，先聊到这儿～ 想我的时候随时叫我。", "via": "memory"}
    if kind == "chatmode_on":
        return {"kind": "answer", "reply": "好，对话模式已开启～ 说完话停一下就行，想退出就说“结束对话”。",
                "via": "memory", "mode": "on"}
    if kind == "chatmode_off":
        return {"kind": "answer", "reply": "好，已退出对话模式。", "via": "memory", "mode": "off"}
    return None


def process(text: str, cfg: dict):
    """Handle one chat utterance. Returns {kind, reply, via} or None.
    kind: answer | defer | none | (via=memory handled as answer). Never raises."""
    text = (text or "").strip()
    if not text:
        return None

    # Offline precheck first (persona / explicit memory / closing / local facts)
    # — no model call for anything answerable offline.
    pre = precheck(text, cfg)
    if pre is not None:
        return pre

    # 2) session rollover + lazy consolidation, ensure an active thread
    s = chatmem.load_session()
    if chatmem.should_rollover(s):
        _rollover(cfg)
        s = chatmem.load_session()
    if not s.get("active"):
        s = chatmem.start_session()

    # 3) long-term retrieval
    facts = chatmem.load_facts()
    relevant = chatmem.retrieve(text, facts) if facts else []

    # 4) model reply with context
    context = {
        "summary": s.get("summary", ""),
        "turns": chatmem.recent_turns(s),
        "facts": [f["text"] for f in relevant],
    }
    result = intent_ai.chat_analyze(text, cfg, context=context)
    if result is None:
        return None
    kind = result.get("kind")
    reply = (result.get("reply") or "").strip()

    # 5) persist real turns; skip none/noise
    if kind in ("answer", "defer") and reply:
        s = chatmem.load_session()
        chatmem.append_turn(s, "user", text)
        chatmem.append_turn(s, "assistant", reply)
        chatmem.save_session(s)
        _maybe_summarize(cfg)
        # The model judged this utterance wraps up the conversation — end the
        # session (stash turns for lazy consolidation; next chat starts fresh).
        if result.get("end"):
            chatmem.park()

    return {"kind": kind, "reply": reply, "via": "chat", "end": bool(result.get("end"))}
