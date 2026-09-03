"""omarchy-voice-control CLI.

Subcommands:
  status                 Print current state.json
  record start           Begin listening (spawn recorder)
  record stop            Stop, transcribe, parse, resolve -> awaiting_confirm
  record cancel          Stop and discard audio (never executed)
  confirm                Execute the pending confirmed Action
  cancel-action          Clear the pending Action
  refresh-provider       Re-probe Voxtype status into state.json
  catalog                Generate the machine command catalog (config/catalog.json)
  blocklist              Print the effective path blocklist
  ai-daemon              Persistent pi-RPC intent daemon (warm AI, socket server)
  check                  Print provider status as JSON
  pet [show|hide|toggle|pos <x> <y>|scale <s>|opacity <0.3-1>]
                         Desktop-mascot visibility/position (pet.json)
  test transcribe <wav>  Print what Voxtype returns for a file (no execution)
"""

from __future__ import annotations

import json
import os
import sys

# Allow imports of the plugin's top-level packages from anywhere.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config import blocklist as blocklist_mod  # noqa: E402
from config import catalog as catalog_mod  # noqa: E402
from config import pet as pet_mod  # noqa: E402
from config import settings  # noqa: E402
from executor import actions as executor  # noqa: E402
from intent import rules as intent_rules  # noqa: E402
from intent import ai as intent_ai  # noqa: E402
from policy import policy  # noqa: E402
from recorder import Recorder, raw_duration_seconds  # noqa: E402
from resolver import lookup as resolver  # noqa: E402
from state import audit, load_state, wav_path, write_state  # noqa: E402
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
        write_state(st2)
        audit("transcribe failed: %s" % exc)
        return
    st["transcript"] = text
    audit("transcribed: %r" % text)

    if not text:
        st["phase"] = "idle"
        st["error"] = "No speech recognized"
        write_state(st)
        return

    draft = intent_rules.parse(text)
    source = "rule"
    action = None
    if draft is not None:
        action = resolver.resolve_action(dict(draft), cfg, aliases)
    if action is None:
        # Rules missed or couldn't resolve (e.g. garbled transcript) -> local AI layer.
        ai_draft = intent_ai.analyze(text, cfg)
        if ai_draft is not None:
            source = "future_llm"
            draft = ai_draft
            action = resolver.resolve_action(dict(draft), cfg, aliases)
    if action is None:
        st["phase"] = "idle"
        st["error"] = "Could not understand the command"
        write_state(st)
        audit("parse failed (rules + AI) for %r" % text)
        return
    action["source"] = source

    action = policy.classify(action, cfg)
    verdict = policy.verdict(action, cfg)
    if not verdict["allowed"]:
        st["phase"] = "idle"
        st["error"] = "Blocked by policy: %s" % verdict["reason"]
        write_state(st)
        audit("policy block: %s" % verdict["reason"])
        return

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
    write_state(st)
    audit("record start")
    return 0


def cmd_record_stop() -> int:
    cfg, aliases = _load()
    st = load_state()
    recorder = _make_recorder(cfg)
    recorder.stop()  # finalize WAV, kill any lingering recorder (no-op if none)
    st["phase"] = "transcribing"
    write_state(st)

    duration = raw_duration_seconds()
    min_secs = float(cfg.get("recorder", {}).get("min_seconds", 0.4))
    if duration < min_secs:
        st["phase"] = "idle"
        st["error"] = "Recording too short (%0.1fs) — please hold a moment" % duration
        write_state(st)
        return 0

    # Run transcription + intent in a daemon thread with a hard deadline so the
    # CLI can never hang the UI (belt-and-suspenders on top of bounded WAV size
    # and non-blocking RPC reads).
    import threading
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
    write_state(st)
    audit("record cancel (audio discarded)")
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
    write_state(st)
    audit("action cancelled by user")
    return 0


def cmd_refresh_provider() -> int:
    cfg, _aliases = _load()
    st = load_state()
    st["provider"] = stt.check_status(cfg)
    write_state(st)
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
        return _err("record: unknown subcommand %r" % argv[1])
    if command == "confirm":
        return cmd_confirm()
    if command == "cancel-action":
        return cmd_cancel_action()
    if command == "refresh-provider":
        return cmd_refresh_provider()
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
