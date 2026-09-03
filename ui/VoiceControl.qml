import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// omarchy-voice-control — safe, local-first voice control.
// Bar icon toggles this popover. A big mic button records (16kHz mono via
// pw-record), the transcript is transcribed locally with Voxtype's
// `transcribe` (never injects text), parsed into a strict Action, resolved,
// risk-classified, and only executed after explicit confirmation.
//
// All state flows through the CLI-written runtime state.json, watched here
// with FileView (no polling). Every button just fires `omarchy-voice-control
// <cmd>` via bar.run and lets the state file drive the UI.
//
// Safety: closing the popover while recording immediately runs
// `record cancel` — the assistant never records invisibly.

Panel {
  id: root
  moduleName: "max.voice-control"
  ipcTarget: "max.voice-control"

  readonly property string homeDir: Quickshell.env("HOME") || ""
  readonly property string runtimeDir: Quickshell.env("XDG_RUNTIME_DIR") || "/tmp"
  readonly property string statePath: root.runtimeDir + "/omarchy-voice-control/state.json"
  readonly property string bin: root.setting("bin", root.homeDir + "/.local/bin/omarchy-voice-control")

  // --- state (mirrors CLI state.json) ---
  property string phase: "idle"
  property string transcript: ""
  property var action: null
  property string targetDesc: ""
  property string error: ""
  property var result: null
  property var provider: null
  property int focusIndex: 0

  readonly property bool recording: root.phase === "recording"
  readonly property bool awaiting: root.phase === "awaiting_confirm"
  readonly property bool working: root.phase === "transcribing" || root.phase === "executing"
  readonly property bool busy: root.working || root.recording

  readonly property color fg: root.bar ? root.bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(root.fg, 1.55)
  readonly property string barFont: root.bar ? root.bar.fontFamily : Style.font.family
  readonly property color hoverFill: root.bar ? Style.hoverFillFor(root.bar.foreground, Color.accent) : "transparent"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // Strip any markup from file-sourced strings: never render them as rich text.
  function scrub(s) { return String(s == null ? "" : s).replace(/[<>]/g, "") }

  function cmd(command) {
    if (root.bar) root.bar.run(root.bin + " " + command)
  }

  // ---------------- data ----------------

  function parseState() {
    var raw = stateFile.text() || ""
    if (!raw.trim()) return
    if (raw.length > 1024 * 1024) return   // defensive size cap
    var o
    try { o = JSON.parse(raw) } catch (e) { return }
    if (!o || typeof o !== "object" || Array.isArray(o)) return

    root.phase = String(o.phase || "idle")
    root.transcript = root.scrub(o.transcript || "")
    root.targetDesc = root.scrub(o.target_desc || "")
    root.error = root.scrub(o.error || "")
    root.result = o.result || null
    root.provider = o.provider || null
    root.action = o.action || null
  }

  FileView {
    id: stateFile
    path: root.statePath
    watchChanges: true
    atomicWrites: true
    onLoaded: root.parseState()
    onFileChanged: reload()
  }

  // ---------------- actions ----------------

  // Enter/Space activation: what the mic / confirm button should do now.
  function activate() {
    if (root.awaiting) { root.cmd("confirm"); return }
    if (root.recording) { root.cmd("record stop"); return }
    if (root.working) { return }
    root.cmd("record start")
  }

  function cancelPending() {
    if (root.awaiting) root.cmd("cancel-action")
    else if (root.working || root.recording) root.cmd("record cancel")
  }

  // ---------------- bar button ----------------

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "\uF036C"                       // Nerd Font microphone (verified in bar font)
    active: root.recording                // red/tinted while listening
    tooltipText: "Voice Control"
    onPressed: function (b) {
      if (b === Qt.LeftButton) root.toggle()
    }
  }

  onOpenedChanged: {
    if (root.opened) {
      root.cmd("refresh-provider")        // re-probe Voxtype status
      root.focusIndex = 0
    } else if (root.recording) {
      root.cmd("record cancel")           // never record invisibly
    }
  }

  // ---------------- popover ----------------

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(panelColumn.implicitHeight, Style.space(520))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onActivateRequested: root.activate()
      onTabRequested: function (dir) { root.moveFocus(dir) }

      ScrollView {
        id: scrollArea
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: panelColumn.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff

        Column {
          id: panelColumn
          width: scrollArea.availableWidth
          spacing: Style.space(12)

          // ---- Header ----
          Item {
            width: parent.width
            implicitHeight: Math.max(headerTitle.implicitHeight, closeBtn.height)

            Text {
              id: headerTitle
              text: "Voice Control"
              color: root.fg
              font.family: root.barFont
              font.pixelSize: Style.font.title
              font.bold: true
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
            }
            PanelActionButton {
              id: closeBtn
              width: Style.space(30)
              height: Style.space(30)
              size: Style.space(30)
              iconText: "\u00D7"
              tooltipText: "Close"
              foreground: root.fg
              hoverColor: root.fg
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              onClicked: root.close()
            }
          }

          // ---- Provider status ----
          Text {
            width: parent.width
            text: root.providerText()
            color: root.providerOk() ? root.dim : root.fg
            font.family: root.barFont
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
          }

          PanelSeparator {
            foreground: root.fg
          }

          // ---- Mic button ----
          Button {
            id: micBtn
            width: parent.width
            height: Style.space(56)
            focusable: true
            selected: root.recording || root.working
            iconText: "\uF036C"
            text: root.recording ? "Stop listening"
                : root.working ? "Cancel"
                : root.awaiting ? "Listening done"
                : "Start listening"
            iconSize: Style.font.icon
            foreground: (root.recording || root.working) ? Color.accent : root.fg
            accent: Color.accent
            onClicked: {
              // Always clickable: "Cancel" during processing is an escape hatch
              // so a stalled backend can never lock the popover.
              if (root.working) root.cancelPending()
              else root.activate()
            }

            Text {
              anchors.fill: parent
              horizontalAlignment: Text.AlignHCenter
              verticalAlignment: Text.AlignVCenter
              text: root.recording ? "● REC" : (root.working ? "…" : "")
              color: root.recording ? Color.accent : "transparent"
              font.family: root.barFont
              font.pixelSize: Style.font.caption
              font.bold: true
              visible: root.recording || root.working
            }
          }

          // ---- Transcript ----
          Text {
            width: parent.width
            text: "Transcript"
            color: root.dim
            font.family: root.barFont
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 1.2
            textFormat: Text.PlainText
          }
          Text {
            width: parent.width
            text: root.transcript !== "" ? root.transcript : "—"
            color: root.transcript !== "" ? root.fg : root.dim
            font.family: root.barFont
            font.pixelSize: Style.font.body
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
          }

          // ---- Action / target ----
          Item {
            visible: root.action !== null
            width: parent.width
            implicitHeight: Math.max(actionLabel.implicitHeight, actionValue.implicitHeight)
            Text {
              id: actionLabel
              text: root.action && root.action.source === "future_llm" ? "Action \u00B7 AI" : "Action"
              color: root.dim
              font.family: root.barFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
            }
            Text {
              id: actionValue
              text: root.targetDesc
              color: root.fg
              font.family: root.barFont
              font.pixelSize: Style.font.body
              anchors.right: parent.right
              anchors.left: actionLabel.right
              anchors.leftMargin: Style.space(12)
              anchors.verticalCenter: parent.verticalCenter
              horizontalAlignment: Text.AlignRight
              elide: Text.ElideMiddle
              textFormat: Text.PlainText
            }
          }

          // ---- Risk ----
          Item {
            visible: root.awaiting
            width: parent.width
            implicitHeight: Math.max(riskLabel.implicitHeight, riskValue.implicitHeight)
            Text {
              id: riskLabel
              text: "Risk"
              color: root.dim
              font.family: root.barFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
            }
            Text {
              id: riskValue
              text: root.action && root.action.risk === "confirm_required" ? "Confirmation required" : "Low risk"
              color: root.action && root.action.risk === "confirm_required" ? Color.accent : root.fg
              font.family: root.barFont
              font.pixelSize: Style.font.body
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
            }
          }

          // ---- Confirm / Cancel ----
          Row {
            visible: root.awaiting
            width: parent.width
            spacing: Style.space(8)
            Button {
              id: confirmBtn
              focusable: true
              text: "Confirm"
              iconText: "\uF00C"
              foreground: root.fg
              accent: Color.accent
              height: Style.space(38)
              width: (parent.width - Style.space(8)) * 0.6
              onClicked: root.cmd("confirm")
            }
            Button {
              id: cancelBtn
              focusable: true
              text: "Cancel"
              foreground: root.fg
              height: Style.space(38)
              width: (parent.width - Style.space(8)) * 0.4
              onClicked: root.cancelPending()
            }
          }

          // ---- Result ----
          Item {
            visible: root.result !== null
            width: parent.width
            implicitHeight: resultText.implicitHeight
            Text {
              id: resultText
              width: parent.width
              text: (root.result && root.result.ok ? "\u2713 " : "\u2717 ")
                  + (root.result ? root.scrub(root.result.message) : "")
              color: root.result && root.result.ok ? root.fg : Color.accent
              font.family: root.barFont
              font.pixelSize: Style.font.body
              wrapMode: Text.Wrap
              textFormat: Text.PlainText
            }
          }

          // ---- Error ----
          Text {
            visible: root.error !== "" && !root.awaiting
            width: parent.width
            text: root.error
            color: Color.accent
            font.family: root.barFont
            font.pixelSize: Style.font.body
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
          }

          // ---- Done (clear result) ----
          Button {
            visible: root.result !== null
            focusable: true
            text: "Done"
            height: Style.space(34)
            width: parent.width
            foreground: root.fg
            onClicked: root.cmd("cancel-action")
          }
        }
      }
    }
  }

  // ---------------- helpers ----------------

  function providerOk() {
    return root.provider && root.provider.available === true
  }

  function providerText() {
    if (!root.provider) return "STT provider: checking…"
    if (root.provider.available) {
      var parts = []
      if (root.provider.engine && root.provider.engine !== "auto") parts.push(root.provider.engine)
      if (root.provider.model) parts.push(root.provider.model)
      return "Voxtype ready" + (parts.length ? " · " + parts.join("/") : "")
    }
    return "Voxtype unavailable" + (root.provider.message ? ": " + root.scrub(root.provider.message) : "")
  }

  function moveFocus(dir) {
    var list = []
    if (micBtn.enabled) list.push(micBtn)
    if (root.awaiting && confirmBtn) list.push(confirmBtn)
    if (root.awaiting && cancelBtn) list.push(cancelBtn)
    if (list.length === 0) return
    root.focusIndex = (root.focusIndex + (dir > 0 ? 1 : -1) + list.length) % list.length
    list[root.focusIndex].forceActiveFocus()
  }
}
