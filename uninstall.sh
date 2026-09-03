#!/usr/bin/env bash
# Uninstall omarchy-voice-control: remove from shell.json, remove plugin dir,
# remove the CLI symlink, and strip the push-to-talk binding block.
set -euo pipefail

PLUGIN_ID="max.voice-control"
DEST="$HOME/.config/omarchy/plugins/$PLUGIN_ID"
SHELL_JSON="$HOME/.config/omarchy/shell.json"
CLI_SYMLINK="$HOME/.local/bin/omarchy-voice-control"
BINDINGS="$HOME/.config/hypr/bindings.lua"

echo "==> Uninstalling $PLUGIN_ID"

# 1) Remove from every bar section in shell.json (backup first).
if [ -f "$SHELL_JSON" ] && command -v jq >/dev/null 2>&1; then
  cp "$SHELL_JSON" "$SHELL_JSON.bak.voice-control.$(date +%s)"
  jq --arg id "$PLUGIN_ID" \
     '.bar.layout |= with_entries(.value = [.value[] | select((.id? // "") != $id)])' \
     "$SHELL_JSON" > "$SHELL_JSON.tmp" && mv "$SHELL_JSON.tmp" "$SHELL_JSON"
  echo "   removed from bar layout"
fi

# 2) Remove the plugin directory and the CLI symlink.
if [ -e "$DEST" ]; then rm -rf "$DEST"; echo "   removed $DEST"; fi
if [ -L "$CLI_SYMLINK" ]; then rm -f "$CLI_SYMLINK"; echo "   removed $CLI_SYMLINK"; fi

# 3) Strip the push-to-talk binding block from bindings.lua.
if [ -f "$BINDINGS" ] && grep -q "omarchy-voice-control begin" "$BINDINGS"; then
  cp "$BINDINGS" "$BINDINGS.bak.voice-control.$(date +%s)"
  sed -i '/-- >>> omarchy-voice-control begin/,/-- <<< omarchy-voice-control end/d' "$BINDINGS"
  echo "   removed push-to-talk binding"
fi

echo "==> Done. Reload the shell:  omarchy restart shell"
echo "     (keep user config in ~/.config/omarchy/voice-control if you plan to reinstall)"
