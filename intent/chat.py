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

from config import chatmem
from config import character as character_mod
from intent import ai as intent_ai

CLOSING_REPLY = "不客气～有事随时叫我。"


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
    return None


def process(text: str, cfg: dict):
    """Handle one chat utterance. Returns {kind, reply, via} or None.
    kind: answer | defer | none | (via=memory handled as answer). Never raises."""
    text = (text or "").strip()
    if not text:
        return None

    # 0) persona (offline, no model)
    pa = character_mod.match(text)
    if pa:
        _note(text, pa, cfg)
        return {"kind": "answer", "reply": pa, "via": "persona"}

    # 1) explicit memory / conversation commands (offline)
    cmd = chatmem.explicit_command(text)
    if cmd:
        return _handle_explicit(cmd, cfg)

    # 1.5) closing remark (offline, no model): reply briefly and end the session.
    if chatmem.is_closing(text):
        _note(text, CLOSING_REPLY, cfg)
        chatmem.park()
        return {"kind": "answer", "reply": CLOSING_REPLY, "via": "memory", "end": True}

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
