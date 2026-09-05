"""omarchy-voice-control CLI.

Subcommands:
  status                 Print current state.json
  record start           Begin listening (spawn recorder)
  record stop            Stop, transcribe, parse, resolve -> awaiting_confirm
  record cancel          Stop and discard audio (never executed)
  record finalize        Detached watchdog: auto-finalize if the recorder dies
                         on its own (max_seconds cap / crash) without a stop
  confirm                Execute the pending confirmed Action
  cancel-action          Clear the pending Action
  chat-tool              Launch the configured AI tool (chat.tool) for discussion
  refresh-provider       Re-probe Voxtype status into state.json
  config get [path]      Print config (or one dotted path)
  config set <path> <v>  Persist a setting (stt.*, ai.*) to user config
  models [stt|ai]        List installed STT / available AI models as JSON
  catalog                Generate the machine command catalog (config/catalog.json)
  blocklist              Print the effective path blocklist
  ai-daemon              Persistent pi-RPC intent daemon (warm AI, socket server)
  check                  Print provider status as JSON
  pet [show|hide|toggle|pos <x> <y>|scale <s>|opacity <0.3-1>]
                         Desktop-mascot visibility/position (pet.json)
  test transcribe <wav>  Print what Voxtype returns for a file (no execution)
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time

# Allow imports of the plugin's top-level packages from anywhere.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import blocklist as blocklist_mod  # noqa: E402
from config import catalog as catalog_mod  # noqa: E402
from config import pet as pet_mod  # noqa: E402
from config import memory as memory_mod  # noqa: E402
from config import settings  # noqa: E402
from executor import actions as executor  # noqa: E402
from intent import rules as intent_rules  # noqa: E402
from intent import ai as intent_ai  # noqa: E402
from policy import policy  # noqa: E402
from recorder import AutoRecorder, Recorder, _read_pid, raw_duration_seconds  # noqa: E402
from resolver import lookup as resolver  # noqa: E402
from state import (  # noqa: E402
    audit,
    load_state,
    recorder_pid_path,
    state_path,
    wav_path,
    write_state,
)
from stt import provider as stt  # noqa: E402


def _load():
    return settings.load_config(), settings.load_aliases()


def cmd_status() -> int:
    print(json.dumps(load_state(), ensure_ascii=False, indent=2))
    return 0


def _make_recorder(cfg: dict) -> Recorder:
    rec = cfg.get("recorder") or {}
    return Recorder(rec.get("device", "default"), float(rec.get("max_seconds", 30)))


def _process_transcript(st: dict, cfg: dict, aliases: dict) -> None:
    """Transcribe + intent + resolve + policy, writing state.json as it goes.
    Called in a watchdog thread so a hung backend can never freeze the CLI."""
    try:
        text = stt.transcribe(wav_path(), cfg)
    except stt.STTError as exc:
        st2 = load_state()
        st2["phase"] = "idle"
        st2["error"] = str(exc)
        st2["transcript"] = ""
        st2["chat_reply"] = ""
        st2["chat_defer"] = ""
        write_state(st2)
        audit("transcribe failed: %s" % exc)
        return
    st["transcript"] = text
    audit("transcribed: %r" % text)

    if not text:
        st["phase"] = "idle"
        st["error"] = "No speech recognized"
        st["chat_reply"] = ""
        st["chat_defer"] = ""
        write_state(st)
        return

    # ---- Layered decision funnel (priority HIGH -> LOW) ----
    # L1 rules -> L2 common commands (operation memory) -> L3 AI-intent commands
    #   -> L4 conversation -> L5 AI-tool handoff (defer).
    # Computer-control commands are handled by the fast local layers first
    # (rules and memory are ~ms, zero network); only novel commands fall through
    # to the AI intent layer; everything else is chat, and only complex topics
    # defer to the AI tool as the last resort.
    draft = None
    source = "rule"
    action = None

    # L1) Deterministic rules — fastest, no network.
    if action is None:
        draft = intent_rules.parse(text)
        if draft is not None:
            source = "rule"
            action = resolver.resolve_action(dict(draft), cfg, aliases)

    # L2) Common commands: operation-memory replay of a previously recognized
    #     (near-identical) phrase — skips the AI intent layer. The cached draft
    #     is still re-resolved + re-validated (resolver + policy) so safety
    #     stays current (blocklist/path changes take effect immediately).
    if action is None:
        entry = memory_mod.lookup(text)
        if entry:
            cached = dict(entry.get("draft") or {})
            cached["confidence"] = float(cached.get("confidence", 0.8))
            a = resolver.resolve_action(dict(cached), cfg, aliases)
            if a is not None and a["type"] == cached.get("type"):
                action = a
                draft = cached
                source = str(cached.get("source") or "rule")
                audit("memory hit: %r -> %s" % (text, action["type"]))

    # L2.5) Offline chat precheck — persona / explicit memory commands / closing
    #       remarks / local facts (time, date, weekday, battery). Runs BEFORE the
    #       AI intent layer so these answer instantly with zero model cost (they
    #       used to spend an AI intent call first, then another in the chat layer).
    _chattext = intent_rules.normalize(text)
    if action is None and len(_chattext) >= 2 and (cfg.get("chat") or {}).get("enabled", True):
        from intent import chat as chat_layer
        pre = chat_layer.precheck(text, cfg)
        if pre is not None:
            if pre.get("kind") == "defer" and pre.get("reply"):
                st["phase"] = "chat_defer"
                st["chat_defer"] = pre["reply"]
                st["chat_reply"] = ""
                st["error"] = ""
                write_state(st)
                audit("chat defer: %r" % pre["reply"])
                return
            elif pre.get("kind") == "answer" and pre.get("reply"):
                st["phase"] = "chat_reply"
                st["chat_reply"] = pre["reply"]
                st["chat_defer"] = ""
                st["error"] = ""
                write_state(st)
                audit("chat reply (%s): %r" % (pre.get("via", "chat"), pre["reply"]))
                if pre.get("mode"):
                    cmd_chatmode([pre["mode"]])
                return
            elif pre.get("kind") == "none":
                st["phase"] = "idle"
                st["chat_reply"] = ""
                st["chat_defer"] = ""
                st["error"] = ""
                write_state(st)
                audit("chat none (ignored)")
                return

    # L3) AI-intent commands: novel phrasing the rules don't cover.
    if action is None:
        ai_draft = intent_ai.analyze(text, cfg)
        if ai_draft is not None:
            source = "future_llm"
            draft = ai_draft
            action = resolver.resolve_action(dict(draft), cfg, aliases)

    # L4/L5) Not a command: chat fallback. L4 answers inline (chat_reply); L5
    #        defers complex/open topics to the AI tool (chat_defer) — the LAST
    #        resort. Skip when the transcript is too short to be meaningful
    #        (pure noise / punctuation from a poor capture) — don't burn a
    #        model call on it. (precheck already handled persona/memory/closing/
    #        time/date/battery before L3, so process()'s offline steps are no-ops.)
    if action is None and len(_chattext) >= 2 and (cfg.get("chat") or {}).get("enabled", True):
        # L4: long conversation + short/long-term memory. The chat layer handles
        # persona offline answers, explicit memory commands (remember/forget/
        # recall/end), session rollover + lazy consolidation into long-term
        # facts, long-term retrieval, and the context-aware model reply.
        from intent import chat as chat_layer
        result = chat_layer.process(text, cfg)
        if result is None:
            pass  # model disabled/failed -> fall through to "could not understand"
        elif result.get("kind") == "defer" and result.get("reply"):
            st["phase"] = "chat_defer"
            st["chat_defer"] = result["reply"]
            st["chat_reply"] = ""
            st["error"] = ""
            write_state(st)
            audit("chat defer: %r" % result["reply"])
            return
        elif result.get("kind") == "answer" and result.get("reply"):
            st["phase"] = "chat_reply"
            st["chat_reply"] = result["reply"]
            st["chat_defer"] = ""
            st["error"] = ""
            write_state(st)
            audit("chat reply (%s): %r" % (result.get("via", "chat"), result["reply"]))
            # A conversation-mode directive (chatmode on/off) from the chat layer.
            if result.get("mode"):
                cmd_chatmode([result["mode"]])
            return
        elif result.get("kind") == "none":
            # noise / greeting — stay idle quietly, no error, no state churn.
            st["phase"] = "idle"
            st["chat_reply"] = ""
            st["chat_defer"] = ""
            st["error"] = ""
            write_state(st)
            audit("chat none (ignored)")
            return

    if action is None:
        st["phase"] = "idle"
        st["error"] = "Could not understand the command"
        st["chat_reply"] = ""
        st["chat_defer"] = ""
        write_state(st)
        audit("parse failed (rules + AI + memory) for %r" % text)
        return
    action["source"] = source

    action = policy.classify(action, cfg)
    verdict = policy.verdict(action, cfg)
    if not verdict["allowed"]:
        st["phase"] = "idle"
        st["error"] = "Blocked by policy: %s" % verdict["reason"]
        st["chat_reply"] = ""
        st["chat_defer"] = ""
        write_state(st)
        audit("policy block: %s" % verdict["reason"])
        return

    # Learn this successful interpretation (deduped by normalized text).
    memory_mod.remember(draft, text, action["confidence"])

    st["action"] = action
    st["target_desc"] = resolver.describe(action)
    if verdict["confirm"]:
        st["phase"] = "awaiting_confirm"
        write_state(st)
        audit("ready (confirm): %s %s risk=%s" % (action["type"], st["target_desc"], action["risk"]))
        return
    # Low-risk action: execute directly without confirmation.
    st["phase"] = "executing"
    write_state(st)
    audit("auto-executing (low risk): %s %s" % (action["type"], st["target_desc"]))
    ok, message = executor.execute(action, cfg)
    _record_result(resolver.describe(action), ok, message)


def _record_result(target_desc: str, ok: bool, message: str) -> None:
    """Write the terminal result state, with a human-readable message on success."""
    display = target_desc if ok else str(message)
    st = load_state()
    st["phase"] = "result"
    st["action"] = None
    st["target_desc"] = ""
    st["result"] = {"ok": bool(ok), "message": display}
    st["error"] = "" if ok else str(message)
    st["chat_reply"] = ""
    st["chat_defer"] = ""
    write_state(st)
    audit("result %s: %s" % ("ok" if ok else "fail", message))


def cmd_record_start() -> int:
    cfg, _aliases = _load()
    st = load_state()
    if st.get("phase") == "recording":
        return 0  # already listening; ignore duplicate start
    recorder = _make_recorder(cfg)
    try:
        recorder.start()
    except Exception as exc:
        st["phase"] = "result"
        st["error"] = "could not start recording: %s" % exc
        st["result"] = {"ok": False, "message": str(exc)}
        write_state(st)
        return 1
    st["phase"] = "recording"
    st["transcript"] = ""
    st["action"] = None
    st["target_desc"] = ""
    st["error"] = ""
    st["result"] = None
    st["chat_reply"] = ""
    st["chat_defer"] = ""
    write_state(st)
    audit("record start")
    _spawn_record_watchdog(getattr(recorder, "pid", None))
    return 0


@contextlib.contextmanager
def _state_lock() -> contextlib.AbstractContextManager:
    """Serialize read-modify-write of state.json between CLI processes that can
    race: a user-initiated `record stop` vs. the recorder-death watchdog."""
    import fcntl

    path = os.path.join(os.path.dirname(state_path()), "state.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _finalize_recording(cfg: dict, aliases: dict) -> int:
    """Shared record-stop path (user-initiated or watchdog): mark transcribing,
    then transcribe + parse with a hard deadline so the CLI can never hang the
    UI. Callers guard on the phase being "recording" so the same recording is
    never finalized twice."""
    st = load_state()
    st["phase"] = "transcribing"
    write_state(st)

    duration = raw_duration_seconds()
    min_secs = float(cfg.get("recorder", {}).get("min_seconds", 0.4))
    if duration < min_secs:
        st["phase"] = "idle"
        st["error"] = "Recording too short (%0.1fs) — please hold a moment" % duration
        st["chat_reply"] = ""
        st["chat_defer"] = ""
        write_state(st)
        return 0

    # Run transcription + intent in a daemon thread with a hard deadline so the
    # CLI can never hang the UI (belt-and-suspenders on top of bounded WAV size
    # and non-blocking RPC reads).
    done = threading.Event()

    def work():
        try:
            _process_transcript(st, cfg, aliases)
        finally:
            done.set()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    hard_timeout = float(cfg.get("stt", {}).get("timeout_secs", 120)) + 60
    if not done.wait(hard_timeout):
        st2 = load_state()
        st2["phase"] = "idle"
        st2["error"] = "Processing timed out"
        write_state(st2)
        audit("record stop watchdog timed out after %.0fs" % hard_timeout)
        return 1
    return 0


def _spawn_record_watchdog(pid: int | None) -> None:
    """Detached watcher for the recorder's silent `timeout` cap / crashes. When
    the recorder dies before a user stop/cancel, `record finalize` transitions
    the state machine instead of stranding it in "recording" forever."""
    try:
        cmd = [sys.executable, os.path.abspath(__file__), "record", "finalize"]
        if pid is not None:
            cmd.append(str(pid))
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass  # watchdog is a safety net; never fail record start over it


def cmd_record_stop() -> int:
    cfg, aliases = _load()
    recorder = _make_recorder(cfg)
    recorder.stop()  # finalize WAV, kill any lingering recorder (no-op if none)
    return _finalize_recording(cfg, aliases)


def cmd_record_finalize() -> int:
    """Watchdog entry: wait for the recorder we were spawned for to die, then
    run the normal record-stop finalize — but only if the state machine is
    still in "recording" AND still owned by that same recorder. A user-initiated
    stop/cancel, or a newer recording, always wins (no double-finalize)."""
    cfg, aliases = _load()
    target_pid: int | None = None
    if len(sys.argv) > 2:
        try:
            target_pid = int(sys.argv[2])
        except ValueError:
            target_pid = None
    recorder = _make_recorder(cfg)
    max_secs = float(cfg.get("recorder", {}).get("max_seconds", 30))
    deadline = time.monotonic() + max_secs + 30
    while time.monotonic() < deadline:
        if target_pid is None:
            if not recorder.running:
                break
        else:
            try:
                os.kill(target_pid, 0)
            except OSError:
                break
        time.sleep(0.2)
    else:
        return 0  # recorder outlived the cap: give up quietly

    # Give a concurrent user-initiated stop/cancel a moment to claim the phase.
    time.sleep(1.0)
    with _state_lock():
        st = load_state()
        if st.get("phase") != "recording":
            return 0  # already stopped/cancelled
        if target_pid is not None:
            try:
                with open(recorder_pid_path(), "r", encoding="utf-8") as fh:
                    current_pid = int(fh.read().strip())
            except (OSError, ValueError):
                current_pid = target_pid  # pid file gone: assume still ours
            if current_pid != target_pid:
                return 0  # a newer recording owns the state; leave it alone
        recorder.stop()  # kill any lingering recorder + materialize the WAV
        audit("record auto-finalize (recorder died without stop)")
        return _finalize_recording(cfg, aliases)
def cmd_record_cancel() -> int:
    cfg, _aliases = _load()
    _make_recorder(cfg).cancel()
    st = load_state()
    st["phase"] = "idle"
    st["transcript"] = ""
    st["action"] = None
    st["target_desc"] = ""
    st["error"] = ""
    st["result"] = None
    st["chat_reply"] = ""
    st["chat_defer"] = ""
    write_state(st)
    audit("record cancel (audio discarded)")
    return 0


def _make_auto_recorder(cfg: dict) -> AutoRecorder:
    rec = cfg.get("recorder") or {}
    return AutoRecorder(rec.get("device", "default"), float(rec.get("max_seconds", 30)))


def cmd_record_auto() -> int:
    """VAD turn: listen until speech + trailing silence (or max_seconds), then
    run the normal transcribe->parse->reply. Drives the hands-free conversation
    mode (chatmode). No-ops when chatmode is off or already listening."""
    cfg, aliases = _load()
    st = load_state()
    if not st.get("chatmode"):
        return 0
    if st.get("phase") == "recording":
        return 0
    recorder = _make_auto_recorder(cfg)
    try:
        recorder.start()
    except Exception as exc:
        st["phase"] = "result"
        st["error"] = "could not start recording: %s" % exc
        st["result"] = {"ok": False, "message": str(exc)}
        write_state(st)
        return 1
    st["phase"] = "recording"
    st["transcript"] = ""
    st["action"] = None
    st["target_desc"] = ""
    st["error"] = ""
    st["result"] = None
    st["chat_reply"] = ""
    st["chat_defer"] = ""
    write_state(st)
    audit("record auto (VAD)")
    _spawn_record_watchdog(recorder.pid)
    natural = recorder.wait_natural_end(float(cfg.get("recorder", {}).get("max_seconds", 30)))
    if natural:
        with _state_lock():
            st = load_state()
            if st.get("phase") == "recording" and _read_pid() == recorder.pid:
                recorder.stop()  # terminate pw-record + wrap WAV
                return _finalize_recording(cfg, aliases)
    return 0  # manual stop/cancel killed pw-record; that process finalized


def _chatmode_pid_path() -> str:
    return os.path.join(settings.USER_CONFIG_DIR, "chatmode.pid")


def _chatmode_daemon_pids() -> list[int]:
    try:
        with open(_chatmode_pid_path(), encoding="utf-8") as fh:
            return [int(fh.read().strip())]
    except (OSError, ValueError):
        return []


def _chatmode_kill_daemon() -> None:
    import signal
    for pid in _chatmode_daemon_pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    try:
        os.unlink(_chatmode_pid_path())
    except OSError:
        pass


def _chatmode_set(state: bool) -> None:
    st = load_state()
    st["chatmode"] = bool(state)
    write_state(st)
    audit("chatmode %s" % ("on" if state else "off"))


def _chatmode_enabled() -> bool:
    return bool(load_state().get("chatmode"))


def cmd_chatmode(argv: list) -> int:
    """Conversation mode: `chatmode on|off|status|daemon`.
    on  -> set chatmode + start a fresh conversation + spawn the hands-free daemon
    off -> stop listening, kill the daemon, clear chatmode
    daemon -> the detached loop that chains VAD turns until the conversation ends"""
    if not argv:
        return _err("usage: chatmode on|off|status|daemon")
    action = argv[0]

    if action == "status":
        enabled = _chatmode_enabled()
        pids = _chatmode_daemon_pids()
        print(json.dumps({"enabled": enabled, "daemon": pids}, ensure_ascii=False))
        return 0

    if action == "off":
        _chatmode_kill_daemon()
        _make_recorder(_load()[0]).cancel()  # stop any active VAD listen
        _chatmode_set(False)
        return 0

    if action == "on":
        if _chatmode_enabled() and _chatmode_daemon_pids():
            return 0  # already on
        _chatmode_set(True)
        from config import chatmem
        chatmem.start_session()
        try:
            proc = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "chatmode", "daemon"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            os.makedirs(os.path.dirname(_chatmode_pid_path()), exist_ok=True)
            with open(_chatmode_pid_path(), "w", encoding="utf-8") as fh:
                fh.write(str(proc.pid))
        except Exception:
            pass
        return 0

    if action == "daemon":
        return _chatmode_daemon_loop(_load())

    return _err("chatmode: unknown action %r" % action)


def _chatmode_daemon_loop(loaded) -> int:
    """Hands-free conversation loop: listen (VAD) -> turn completes -> breathing
    gap -> listen again. Ends when chatmode is turned off, the conversation
    wrapped up (session inactive), or the user went quiet for 2 listen cycles."""
    cfg, aliases = loaded
    quiet = 0
    try:
        while _chatmode_enabled():
            st = load_state()
            phase = st.get("phase")
            if phase in ("recording", "transcribing", "executing", "awaiting_confirm"):
                time.sleep(0.5)
                continue
            if phase in ("chat_reply", "chat_defer", "result"):
                # A turn just finished: check whether the conversation ended.
                try:
                    from config import chatmem as _cm
                    if not _cm.load_session().get("active", True):
                        break  # wrapped up -> leave chatmode
                except Exception:
                    pass
                time.sleep(1.0)  # breathing gap before re-arming the mic
            elif phase != "idle":
                time.sleep(0.5)
                continue
            # One hands-free turn (blocks until it completes).
            subprocess.call(
                [sys.executable, os.path.abspath(__file__), "record", "auto"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if load_state().get("phase") == "idle":
                quiet += 1
                if quiet >= 2:
                    break  # user went quiet — stop the mode
            else:
                quiet = 0
    finally:
        if _chatmode_enabled():
            _chatmode_set(False)
        _chatmode_kill_daemon()
    return 0


def cmd_confirm() -> int:
    cfg, _aliases = _load()
    st = load_state()
    action = st.get("action")
    if st.get("phase") != "awaiting_confirm" or not action:
        return 0
    if not policy.allowed(action.get("type", "")):
        st["phase"] = "idle"
        st["action"] = None
        st["error"] = "Policy denied the action"
        write_state(st)
        return 1
    st["phase"] = "executing"
    write_state(st)
    audit("executing %s %s" % (action["type"], action.get("target", {})))
    ok, message = executor.execute(action, cfg)
    _record_result(st.get("target_desc") or resolver.describe(action), ok, message)
    return 0 if ok else 1


def cmd_cancel_action() -> int:
    st = load_state()
    st["phase"] = "idle"
    st["action"] = None
    st["target_desc"] = ""
    st["error"] = ""
    st["result"] = None
    st["chat_reply"] = ""
    st["chat_defer"] = ""
    write_state(st)
    audit("action cancelled by user")
    return 0


def _chat_query() -> str:
    """URL-encode the question to hand off to the AI tool: the user's original
    transcript (the actual question), falling back to the chat_defer handoff
    text. Used to pre-fill the tool (e.g. ChatGPT's ?q= deep link) so it opens
    a chat about the topic instead of a blank page."""
    st = load_state()
    text = (st.get("transcript") or "").strip() or (st.get("chat_defer") or "").strip()
    if not text:
        return ""
    try:
        from urllib.parse import quote
        return quote(text, safe="")
    except Exception:
        return text


def cmd_chat_tool() -> int:
    """Launch the configured AI tool for complex-topic discussion (chat.tool).
    Any `{query}` placeholder in the command is replaced with the URL-encoded
    user question (original transcript, falling back to the chat_defer text) so
    the tool opens a chat about the topic. Never runs voice/AI-provided
    commands — only the user-configured command."""
    cfg, _aliases = _load()
    chat = cfg.get("chat") or {}
    command = chat.get("tool") or "chromium --app=\"https://chatgpt.com/?q={query}\""
    if not command or not command.strip():
        return 0
    command = command.replace("{query}", _chat_query())
    import shlex
    try:
        argv = shlex.split(command)
    except ValueError:
        return 1
    if not argv:
        return 1
    try:
        subprocess.Popen(
            argv, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return 1
    audit("chat tool launched: %s" % command)
    # Clear the pending chat state.
    st = load_state()
    st["phase"] = "idle"
    st["chat_reply"] = ""
    st["chat_defer"] = ""
    write_state(st)
    return 0


def cmd_refresh_provider() -> int:
    cfg, _aliases = _load()
    st = load_state()
    st["provider"] = _provider_info(cfg)
    write_state(st)
    return 0


def _provider_info(cfg: dict) -> dict:
    """Rich provider block for the popup: Voxtype availability plus the two
    pickers (STT model, AI intent model + thinking) and their options."""
    stt_status = stt.check_status(cfg)
    ai = cfg.get("ai") or {}
    # Current selections (defaults mirrored from config).
    cur_stt_engine = (cfg.get("stt") or {}).get("engine") or "auto"
    cur_stt_model = (cfg.get("stt") or {}).get("model") or "small-int8"
    stt_status["stt_models"] = [
        {
            "engine": m["engine"],
            "model": m["model"],
            "label": "%s/%s" % (m["engine"], m["model"]),
            "current": m["engine"] == cur_stt_engine and m["model"] == cur_stt_model,
        }
        for m in stt.list_installed_models()
    ]
    stt_status["stt_engine"] = cur_stt_engine
    stt_status["stt_model"] = cur_stt_model

    # AI intent layer: current model/thinking + the ready-model option list.
    ai_models = intent_ai.list_available_models(cfg)
    cur_ai_model = ai.get("model") or ""
    stt_status["ai"] = {
        "enabled": bool(ai.get("enabled", True)),
        "backend": ai.get("backend") or "pi-rpc",
        "model": cur_ai_model,
        "thinking": intent_ai._thinking_level(cfg),
        "models": ai_models,
    }
    return stt_status


def cmd_config(argv: list) -> int:
    """`config get [path]` / `config set <path> <value>` — read or persist a
    plugin setting to the user override file. Supported paths:
      stt.engine, stt.model, stt.language
      ai.enabled, ai.backend, ai.model, ai.thinking
    `config set ai.*` restarts the ai-daemon so a model/thinking change applies
    to the warm pi process."""
    if not argv or argv[0] == "get":
        cfg, _aliases = _load()
        path = argv[1] if len(argv) > 1 else None
        if path is None:
            print(json.dumps(cfg, ensure_ascii=False, indent=2))
            return 0
        node = cfg
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return _err("config: unknown path %r" % path)
            node = node[part]
        print(json.dumps(node, ensure_ascii=False, indent=2))
        return 0
    if argv[0] != "set" or len(argv) < 3:
        return _err("usage: config set <path> <value>")
    path, value = argv[1], argv[2]
    allowed = {"stt.engine", "stt.model", "stt.language", "ai.enabled", "ai.backend", "ai.model", "ai.thinking"}
    if path not in allowed:
        return _err("config set: unsupported path %r (allowed: %s)" % (path, ", ".join(sorted(allowed))))

    if path == "ai.enabled":
        patch = {"ai": {"enabled": value.lower() in ("1", "true", "yes", "on")}}
    elif path == "ai.thinking":
        level = value.lower()
        if level not in ("off", "minimal", "low", "medium", "high", "xhigh", "max"):
            return _err("config set ai.thinking: invalid level %r" % value)
        patch = {"ai": {"thinking": level}}
    elif path == "stt.engine":
        patch = {"stt": {"engine": value}}
    elif path == "stt.model":
        # Accept either "engine/model" or a bare model id (resolved against the
        # installed list to keep the engine in sync).
        engine, model = (value.split("/") + [None])[:2] if "/" in value else (None, value)
        installed = stt.list_installed_models()
        if model is None:
            return _err("config set stt.model: expected engine/model")
        if engine is None:
            match = next((m for m in installed if m["model"] == model), None)
            if match is None:
                return _err("config set stt.model: model %r not installed" % model)
            engine = match["engine"]
        elif not any(m["engine"] == engine and m["model"] == model for m in installed):
            return _err("config set stt.model: %s/%s not installed" % (engine, model))
        patch = {"stt": {"engine": engine, "model": model}}
    elif path == "stt.language":
        patch = {"stt": {"language": value}}
    elif path == "ai.backend":
        if value not in intent_ai._BACKENDS:
            return _err("config set ai.backend: unsupported %r (allowed: %s)" % (value, ", ".join(intent_ai._BACKENDS)))
        patch = {"ai": {"backend": value}}
    else:  # ai.model
        if value and value not in {"null", "none", "default"}:
            ready = {m["id"] for m in intent_ai.list_available_models(_load()[0])}
            if value not in ready:
                return _err("config set ai.model: %r not available (run `models ai`)" % value)
        patch = {"ai": {"model": None if value in ("", "null", "none", "default") else value}}

    settings.save_user_config(patch)
    audit("config set %s = %s" % (path, value))
    if path.startswith("ai.") and path != "ai.enabled":
        # pi-rpc holds a warm process that must respawn on model/thinking
        # change; opencode/codex are one-shot so nothing to restart.
        if intent_ai._backend_of(settings.load_config()) == "pi-rpc":
            intent_ai.restart_daemon()
    cmd_refresh_provider()
    return 0


def cmd_models(argv: list) -> int:
    """`models stt` / `models ai` — print the available model lists as JSON."""
    cfg, _aliases = _load()
    if argv and argv[0] == "stt":
        print(json.dumps(stt.list_installed_models(), ensure_ascii=False, indent=2))
        return 0
    if argv and argv[0] == "ai":
        refresh = len(argv) > 1 and argv[1] == "refresh"
        print(json.dumps(intent_ai.list_available_models(cfg, refresh=refresh), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps({"stt": stt.list_installed_models(), "ai": intent_ai.list_available_models(cfg)}, ensure_ascii=False, indent=2))
    return 0


def cmd_ai_daemon() -> int:
    cfg, _aliases = _load()
    return intent_ai.daemon_main(cfg)


def cmd_blocklist() -> int:
    print(json.dumps(blocklist_mod.load_blocklist(), ensure_ascii=False, indent=2))
    return 0


def cmd_catalog() -> int:
    cfg, _aliases = _load()
    try:
        path = catalog_mod.write_catalog(cfg)
    except Exception as exc:
        print("catalog generation failed: %s" % exc, file=sys.stderr)
        return 1
    print("catalog written: %s" % path)
    return 0


def cmd_check() -> int:
    cfg, _aliases = _load()
    print(json.dumps(stt.check_status(cfg), ensure_ascii=False, indent=2))
    return 0


def cmd_pet(argv: list) -> int:
    """Desktop-mascot (小飞马) visibility/position, persisted to pet.json.

    The QML pet overlay watches pet.json with FileView, so every subcommand
    here immediately moves or hides the pet on screen.
    """
    if not argv or argv[0] == "status":
        print(json.dumps(pet_mod.load_pet(), ensure_ascii=False, indent=2))
        return 0
    sub = argv[0]
    if sub in ("show", "hide"):
        pet_mod.save_pet({"visible": sub == "show"})
        audit("pet %s" % sub)
        return 0
    if sub == "toggle":
        cur = pet_mod.load_pet()
        pet_mod.save_pet({"visible": not cur.get("visible", True)})
        audit("pet toggle")
        return 0
    if sub == "pos" and len(argv) >= 3:
        try:
            x, y = int(argv[1]), int(argv[2])
        except ValueError:
            return _err("pet pos: expected two integers, got %r" % argv[1:])
        pet_mod.save_pet({"x": x, "y": y})
        audit("pet pos %d %d" % (x, y))
        return 0
    if sub == "scale" and len(argv) >= 2:
        try:
            scale = float(argv[1])
        except ValueError:
            return _err("pet scale: expected a number, got %r" % argv[1])
        pet_mod.save_pet({"scale": max(0.4, min(2.0, scale))})
        audit("pet scale %.2f" % scale)
        return 0
    if sub == "opacity" and len(argv) >= 2:
        try:
            opacity = float(argv[1])
        except ValueError:
            return _err("pet opacity: expected a number, got %r" % argv[1])
        pet_mod.save_pet({"opacity": max(0.3, min(1.0, opacity))})
        audit("pet opacity %.2f" % opacity)
        return 0
    return _err("pet: unknown subcommand %r" % sub)


def cmd_test_transcribe(wav: str) -> int:
    cfg, _aliases = _load()
    try:
        text = stt.transcribe(wav, cfg)
    except stt.STTError as exc:
        print("ERROR: %s" % exc)
        return 1
    print("TRANSCRIPT: %s" % text)
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__.strip())
        return 0
    command = argv[0]

    if command == "status":
        return cmd_status()
    if command == "record" and len(argv) > 1:
        if argv[1] == "start":
            return cmd_record_start()
        if argv[1] == "stop":
            return cmd_record_stop()
        if argv[1] == "cancel":
            return cmd_record_cancel()
        if argv[1] == "finalize":
            return cmd_record_finalize()
        if argv[1] == "auto":
            return cmd_record_auto()
        return _err("record: unknown subcommand %r" % argv[1])
    if command == "confirm":
        return cmd_confirm()
    if command == "cancel-action":
        return cmd_cancel_action()
    if command == "chat-tool":
        return cmd_chat_tool()
    if command == "refresh-provider":
        return cmd_refresh_provider()
    if command == "config":
        return cmd_config(argv[1:])
    if command == "models":
        return cmd_models(argv[1:])
    if command == "catalog":
        return cmd_catalog()
    if command == "blocklist":
        return cmd_blocklist()
    if command == "ai-daemon":
        return cmd_ai_daemon()
    if command == "check":
        return cmd_check()
    if command == "pet":
        return cmd_pet(argv[1:])
    if command == "chatmode":
        return cmd_chatmode(argv[1:])
    if command == "test" and len(argv) > 2 and argv[1] == "transcribe":
        return cmd_test_transcribe(argv[2])
    if command in ("help", "--help", "-h"):
        print(__doc__.strip())
        return 0
    return _err("unknown command %r" % command)


def _err(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
