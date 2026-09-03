# omarchy-voice-control — Build tasks

Goal: a basic, operable Omarchy voice-control extension (goal 8d7e5501).

- [x] T1 Environment audit + requirements (`docs/requirements.md`)
- [x] T2 Backend: config/state/recorder/STT/intent/resolver/policy/executor + CLI
- [x] T3 Backend tests (intent/resolver/policy/CLI state machine/no-injection) — 31 pass
- [x] T4 QML UI (`manifest.json` + `ui/VoiceControl.qml`), qmllint clean, manifest validates
- [x] T5 install.sh / uninstall.sh / README
- [x] T6 Install + register bar widget + push-to-talk binding + reload + verify
- [x] T7 End-to-end runtime verification
- [x] T8 AI intent layer via pi RPC mode (non-thinking) + machine command catalog
- [x] T9 Desktop pet (小飞马) overlay: draggable layer window, click-to-activate voice, state sprites, popover show/hide toggle

## T9 (desktop pet) evidence (2026-09-03)

- 8 sprites (base + 7 states) generated in ChatGPT, verified transparent RGBA + consistent palette (cream body / champagne mane), deduped to 8 unique files in `ui/pet/` (base.png idle.png recording.png transcribing.png confirm.png executing.png done.png error.png). Mapping is a one-line edit in `ui/PetOverlay.qml` (`sprites` object) pending user confirmation against `状态对照图.png`.
- `config/pet.py` + `cli.py pet` subcommand (status/show/hide/toggle/pos/scale) persist visibility+position atomically to `~/.config/omarchy/voice-control/pet.json`.
- `ui/PetOverlay.qml`: `PanelWindow` (WlrLayer.Overlay, keyboardFocus None, ExclusionMode.Ignore) at `margins{left:petX,top:petY}`; watches state.json + pet.json via FileView; drag updates margins live and persists on release; single click = same `activate()` semantics as the popover (idle→record start, recording→record stop, awaiting_confirm→confirm, result→cancel-action); speech bubble + ✓/✗ confirm buttons; idle bob / listening pulse / flying bob animations.
- Popover gains a 小飞马助手 Toggle (show/hide via `pet show|hide`) + reset-position button.
- qmllint clean on both QML files; 77 unit tests pass (added tests/test_pet.py).

## T8 (AI layer) evidence (2026-09-03)

- pi SDK/RPC verified: `pi --mode rpc --no-session` + `set_thinking_level off` (deepseek-v4-flash default, JSON round-trip ~5s).
- Command catalog generator (`omarchy-voice-control catalog`): 356 omarchy commands + 118 apps + 10 actions + hl.dsp.* dispatchers -> ~/.config/omarchy/voice-control/catalog.json.
- intent/ai.py: spawn pi RPC -> prompt (catalog + safety) -> agent_settled -> get_last_assistant_text -> JSON extract/validate (allowlist, confidence clamp, rejects shell). Degrades gracefully when pi missing/times out.
- CLI: rules first (fast, source=rule); AI fallback on rule-miss OR rule-hit-but-unresolvable; same resolver/policy/confirm gate. UI shows "Action · AI".
- Live: "调出下载文件夹" (rule miss) -> AI -> open_folder ~/Downloads -> confirm -> gio open -> ok, nautilus opened. "Action · AI" tag verified via OCR.
- Fix: xdg-open hung on Tracker3 -> open_file/open_folder use `gio open`.
- 48 unit tests pass.

## T7 verification evidence (2026-09-03)

- Bar icon renders (mic glyph at far right, OCR + pixel analysis).
- Popover open/close/toggle via IPC + bar click (OCR: header, provider status, mic button, transcript).
- Mic button Start listening ↔ Stop listening (recording state UI).
- Live recording (pw-record 16k mono) → Voxtype transcribe → intent parse: real audio "哎呀哎呀。" correctly rejected, nothing executed.
- Full confirm→execute chain: "截图" → take_screenshot → real screenshot created (count 19→20).
- Hide while recording → `record cancel`, phase→idle (never records invisibly).
- Push-to-talk SUPER + SHIFT + V live (press + release); SUPER + X not overwritten (was "Universal cut").
- Provider status in UI: "Voxtype ready - small-fp32".
- No text injection: provider only ever runs `voxtype transcribe` (tested).
- qmllint clean, `omarchy plugin validate` OK, no QML errors after fix + restart.
