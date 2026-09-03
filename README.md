# omarchy-voice-control

Safe, local-first desktop voice control for [Omarchy](https://omarchy.org/).
A compact bar icon opens a popover with a big microphone button: speak a
Chinese or English command, review the recognized action, **explicitly
confirm**, then a safe allow-listed desktop action runs.

This is **not** a generic shell assistant: raw transcripts and STT output are
never executed. Only rule-parsed, strictly-typed Actions that pass policy and
explicit confirmation reach the executor.

## Highlights

- **Local-first**: speech-to-text uses the locally installed
  [Voxtype](https://voxtype.io) model (SenseVoice small-fp32 zh here); no cloud.
- **No text injection**: we record our own 16 kHz mono WAV and feed it to
  `voxtype transcribe`, which prints the transcript to stdout and never types
  into the focused app. The daemon's `record start/stop` path (which injects)
  is deliberately not used.
- **Confirm before acting**: every action is reviewed in the popover
  (transcript → action → target → risk) and requires an explicit Confirm.
- **Never records invisibly**: closing the popover while listening immediately
  discards the audio.
- **Strict Action schema**: 10 allow-listed action types with
  `type/target/confidence/risk/source`; policy classifies risk and forces
  confirmation for destructive/global actions.

## Supported commands (deterministic, zh/en)

`open_app` · `focus_app` · `open_file` · `open_folder` ·
`close_active_window` · `switch_workspace` ·
`move_active_window_to_workspace` · `toggle_fullscreen` ·
`take_screenshot` · `lock_screen`

Examples: 打开浏览器 / open browser · 打开下载文件夹 / open the downloads
folder · 切到工作区3 / go to workspace 3 · 关闭窗口 / close window · 锁屏 /
lock screen.

## Install

```bash
./install.sh          # copies plugin, registers bar widget, adds push-to-talk
hyprctl reload && hyprctl configerrors   # apply the keybinding
```

Uninstall: `./uninstall.sh`

## Layout

```
docs/requirements.md   # frozen requirements + environment audit + ADRs
stt/                   # Voxtype provider (transcribe-only, no injection)
intent/                # deterministic zh/en phrase rules -> strict Actions
resolver/              # app/file/folder/workspace resolution (allow-listed roots)
policy/                # allowlist, risk classification, confirmation
executor/              # Hyprland 0.56.x / XDG / omarchy system actions
config/                # defaults + user overrides (aliases, STT, roots)
ui/VoiceControl.qml    # bar icon + popover (KeyboardPanel, FileView-driven)
tests/                 # unittest suite (31 tests)
```

## User configuration

Override defaults at `~/.config/omarchy/voice-control/`:
- `config.json`  — STT engine/model/language, confirm policy, search roots, …
- `aliases.json` — `apps` / `folders` / `files` aliases

Runtime state/audio live in `$XDG_RUNTIME_DIR/omarchy-voice-control/`;
audit log at `~/.local/state/omarchy-voice-control/audit.log`.

## Push-to-talk

`SUPER + SHIFT + V` (hold to talk). `SUPER + X` was already bound to
"Universal cut", so it was **not** overwritten — see `docs/requirements.md`
ADR-004.
