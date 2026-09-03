#!/usr/bin/env bash
# Install omarchy-voice-control as an Omarchy shell plugin.
# Copies the project into the user plugin dir, registers the bar widget in
# shell.json, links the CLI on PATH, and adds the chosen push-to-talk binding.
# Never touches /usr/share/omarchy (packaged Omarchy stays read-only).
set -euo pipefail

PLUGIN_ID="max.voice-control"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.config/omarchy/plugins/$PLUGIN_ID"
SHELL_JSON="$HOME/.config/omarchy/shell.json"
CLI_SYMLINK="$HOME/.local/bin/omarchy-voice-control"
BINDINGS="$HOME/.config/hypr/bindings.lua"

echo "==> Installing $PLUGIN_ID"

# 1) Copy plugin files (runtime + QML + CLI) into the user plugin dir.
mkdir -p "$DEST"
cp -r \
  "$SRC/manifest.json" \
  "$SRC/ui" \
  "$SRC/bin" \
  "$SRC/stt" \
  "$SRC/intent" \
  "$SRC/resolver" \
  "$SRC/policy" \
  "$SRC/executor" \
  "$SRC/config" \
  "$SRC/state.py" \
  "$SRC/recorder.py" \
  "$SRC/cli.py" \
  "$DEST/"
chmod +x "$DEST/bin/voice-control"
echo "   plugin -> $DEST"

# 2) Link the CLI onto PATH so bar buttons and the Hyprland binding can call it.
mkdir -p "$HOME/.local/bin"
ln -sf "$DEST/bin/voice-control" "$CLI_SYMLINK"
echo "   cli    -> $CLI_SYMLINK"

# 3) Register the widget in the bar (right section), backing up shell.json first.
if [ -f "$SHELL_JSON" ] && command -v jq >/dev/null 2>&1; then
  cp "$SHELL_JSON" "$SHELL_JSON.bak.voice-control.$(date +%s)"
  if ! jq -e --arg id "$PLUGIN_ID" \
      '(.bar.layout.right // []) | map(.id? == $id) | any' \
      "$SHELL_JSON" >/dev/null 2>&1; then
    jq --arg id "$PLUGIN_ID" \
       '.bar.layout.right += [{"id": $id}]' \
       "$SHELL_JSON" > "$SHELL_JSON.tmp" && mv "$SHELL_JSON.tmp" "$SHELL_JSON"
    echo "   registered in bar (right): $PLUGIN_ID"
  else
    echo "   already registered in bar"
  fi
fi

# 4) Push-to-talk binding (chosen by user; SUPER+X was occupied by "Universal cut").
if [ -f "$BINDINGS" ] && ! grep -q "omarchy-voice-control begin" "$BINDINGS"; then
  cp "$BINDINGS" "$BINDINGS.bak.voice-control.$(date +%s)"
  cat >> "$BINDINGS" <<'EOF'

-- >>> omarchy-voice-control begin
-- Push-to-talk for omarchy-voice-control (selected 2026-09-03; SUPER + X was taken by "Universal cut").
o.bind("SUPER + SHIFT + V", "Voice control (push-to-talk)", "omarchy-voice-control record start")
o.bind("SUPER + SHIFT + V", "Voice control (push-to-talk release)", "omarchy-voice-control record stop", { release = true })
-- <<< omarchy-voice-control end
EOF
  echo "   added push-to-talk binding SUPER + SHIFT + V"
else
  echo "   push-to-talk binding already present"
fi

# 5) Hot-reload the shell so the new widget appears.
#    Also write an initial state.json so the UI's FileView has no missing-file warning.
"$CLI_SYMLINK" refresh-provider 2>/dev/null || true
omarchy-shell -q shell rescanPlugins 2>/dev/null \
  || omarchy restart shell 2>/dev/null \
  || echo "   (shell reload deferred — restart manually with: omarchy restart shell)"

echo "==> Done. Apply the keybinding with:  hyprctl reload && hyprctl configerrors"
