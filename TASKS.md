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

## 明日研发任务 (2026-09-06) — 长对话 + 长短记忆 + 更多快速规则

已完成的上下文：决策漏斗五层 (L1规则→L2常用指令→L3 AI指令→L4对话→L5 AI工具,
commit 6d668c9)；人设 omaok (`config/character.py`, c3920bb)；补充 L1 规则
(把…打开/把…关了/音量/媒体/提醒/截图/锁屏变体, 134 tests)；方案文档
`docs/design-chat-memory.md`（4 个待用户确认点）。

- [x] M1 短期记忆（长对话）：`intent/chat.py` + `chat.json` —— 滑动窗口(8轮)+滚动摘要；
      会话生命周期（连续聊天同一 session，idle gap>10min 或“结束对话”→收尾开新会话）
- [x] M2 长期记忆：`config/chatmem.py` + `memory/facts.json` —— 会话收尾 consolidate
      蒸馏事实/偏好（去重/合并）；检索=关键词+recency+importance 取 top-K 注入 prompt
- [x] M3 cli.py L4 接入 chat session 层（`intent.chat.process`）；`intent/ai.py` 复用
      （persona blurb 保留；`_chat_prompt` 加 context；daemon 支持 kind=chat 热复用提速）
- [x] M4 显式记忆指令：记住…/忘掉…/我上次说到哪了/结束对话（离线，不走模型）
- [ ] M5 (v2 可选) 语义召回：SQLite+FTS5(trigram 中文)+sqlite-vec+本地 ONNX embedding(bge-small-zh)
      —— 已确认暂缓，v1 用关键词+时间+重要性已可用
- [x] M6 继续扩充 L1 规则 + 常用指令（operation memory 预热）—— 上一轮已完成一轮大扩充
- [x] M7 测试：`tests/test_chat_memory.py`（23 个）—— 窗口轮转/summary、consolidate 去重、
      retrieve 打分、显式指令、命令↔对话边界；现有 157 测试全部通过
- [x] M8 端到端验证：真实 pi 后端 3 轮长对话（上下文延续✓）、记住/忘掉/结束对话、
      persona 口吻；warm daemon 聊天 ~4-5s
