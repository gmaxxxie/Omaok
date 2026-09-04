import QtQuick
import QtQuick.Effects
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

// Desktop-pet overlay (小飞马) for omarchy-voice-control.
//
// A small draggable Pegasus floating above the desktop in a layer-shell
// overlay window. It mirrors the exact phase state-machine the popover reads
// (same state.json via FileView) and drives the same CLI entry points, so it
// is never a second source of truth:
//
//   idle             -> click to start listening
//   recording        -> click to stop + recognize
//   transcribing     -> busy (no click action)
//   awaiting_confirm -> click to confirm (bubble also has ✓ / ✗)
//   executing        -> busy (no click action)
//   result           -> click to dismiss
//
// Drag = move the pet; a simple click = the phase action above. Visibility,
// position and scale live in pet.json (~/.config/omarchy/voice-control/pet.json),
// written by the CLI and watched here with FileView — the popover toggle drives
// the same file, so the pet follows the popover and vice versa.
//
// Sprites are the PNGs under ui/pet/ (transparent background). The phase ->
// file mapping is the `sprites` object below: rename the PNGs or remap here,
// no other code changes.

Item {
  id: root

  required property string bin
  required property string statePath
  readonly property string petPath: (Quickshell.env("HOME") || "") + "/.config/omarchy/voice-control/pet.json"

  // ---- mirrored runtime state (the same state.json the popover watches) ----
  property string phase: "idle"
  property string transcript: ""
  property string error: ""
  property var result: null

  // ---- pet geometry, driven by pet.json ----
  property bool petVisible: true
  property int petX: 72
  property int petY: 64
  property real petScale: 1.0
  property real petOpacity: 0.7
  property bool dragging: false
  property bool hovered: false

  readonly property bool recording: root.phase === "recording"
  readonly property bool awaiting: root.phase === "awaiting_confirm"
  readonly property bool working: root.phase === "transcribing" || root.phase === "executing"
  readonly property bool showResult: root.phase === "result"
  readonly property bool errorPhase: root.phase === "idle" && root.error !== ""
  readonly property bool cancellable: root.recording || root.working || root.awaiting
  readonly property bool bubbleShown: root.phase !== "idle" || root.errorPhase || root.hovered
  readonly property int bubbleTextW:
    root.awaiting || root.showResult || root.errorPhase ? 212 : 160
  readonly property int petSize: Math.max(48, Math.round(100 * root.petScale))

  // phase (display) -> sprite file under ui/pet/. Remap here or rename the PNGs.
  property var sprites: ({
    idle: "idle.png",
    recording: "recording.png",
    transcribing: "transcribing.png",
    awaiting_confirm: "confirm.png",
    executing: "executing.png",
    result: "done.png",
    error: "error.png"
  })

  // ---- one-shot CLI runner (mirrors the popover's `bar.run`) ----
  Process {
    id: cli
    command: [root.bin, "status"]
    running: false
  }

  function cmdCli(args) {
    cli.command = [root.bin].concat(args)
    cli.running = false
    cli.running = true
  }

  // ---- state parsing ----

  function scrub(s) { return String(s == null ? "" : s).replace(/[<>]/g, "") }

  function parseState() {
    var raw = stateFile.text() || ""
    if (!raw.trim()) return
    if (raw.length > 1024 * 1024) return
    var o
    try { o = JSON.parse(raw) } catch (e) { return }
    if (!o || typeof o !== "object" || Array.isArray(o)) return
    root.phase = String(o.phase || "idle")
    root.transcript = root.scrub(o.transcript || "")
    root.error = root.scrub(o.error || "")
    root.result = o.result || null
  }

  function parsePet() {
    var raw = petFile.text() || ""
    if (!raw.trim()) return
    var o
    try { o = JSON.parse(raw) } catch (e) { return }
    if (!o || typeof o !== "object" || Array.isArray(o)) return
    if (typeof o.visible === "boolean") root.petVisible = o.visible
    if (typeof o.x === "number") root.petX = Math.max(0, Math.round(o.x))
    if (typeof o.y === "number") root.petY = Math.max(0, Math.round(o.y))
    if (typeof o.scale === "number") root.petScale = Math.max(0.4, Math.min(2.0, o.scale))
    if (typeof o.opacity === "number") root.petOpacity = Math.max(0.3, Math.min(1.0, o.opacity))
  }

  FileView {
    id: stateFile
    path: root.statePath
    watchChanges: true
    atomicWrites: true
    onLoaded: root.parseState()
    onFileChanged: reload()
  }

  // The runtime dir/state.json does not exist when the shell first starts (the
  // CLI creates them on the first command), so FileView's initial read fails
  // and neither the file nor the directory watcher can be established — later
  // creation/rewrites would never be noticed and the UI would stay frozen.
  // Poll reload() until the file loads (which also (re)establishes the
  // watchers), then keep a slow safety-net poll in case the directory watch
  // ever goes quiet.
  // `running: true` is REQUIRED: in Qt Quick a Timer's `running` defaults to
  // false, and `repeat: true` only repeats once running — it does NOT start
  // the timer. Without it this safety-net poll never fires and the UI stays
  // frozen when the runtime dir (and thus the FileView watcher) does not
  // exist yet at shell start.
  Timer {
    interval: stateFile.loaded ? 3000 : 700
    repeat: true
    running: true
    onTriggered: stateFile.reload()
  }

  FileView {
    id: petFile
    path: root.petPath
    watchChanges: true
    atomicWrites: true
    onLoaded: root.parsePet()
    onFileChanged: reload()
  }

  // ---- sprite / bubble content ----

  function spriteUrl() {
    var key = root.errorPhase ? "error" : root.phase
    if (key === "result") key = (root.result && root.result.ok) ? "result" : "error"
    var file = root.sprites[key] || root.sprites.idle || "idle.png"
    return Qt.resolvedUrl("pet/" + file)
  }

  function bubbleText() {
    if (root.errorPhase) return "⚠ " + (root.error || "Something went wrong")
    if (root.phase === "recording") return "Listening…\ntap again to finish · right-click to cancel"
    if (root.phase === "transcribing") return "Thinking… (right-click to cancel)"
    if (root.phase === "executing") return "Working… (right-click to cancel)"
    if (root.phase === "awaiting_confirm") {
      var t = root.transcript !== "" ? "\u201C" + root.transcript + "\u201D" : ""
      return t + (t !== "" ? "\n" : "") + "Do this?"
    }
    if (root.phase === "result") {
      var ok = root.result && root.result.ok
      return (ok ? "\u2713 " : "\u2717 ") + (root.result ? root.result.message : "")
    }
    return root.hovered ? "Click to talk" : "" // idle: only on hover
  }

  // ---- click semantics (identical to the popover's activate()) ----

  function activate() {
    if (root.awaiting) { root.cmdCli(["confirm"]); return }
    if (root.recording) { root.cmdCli(["record", "stop"]); return }
    if (root.working) { return }
    if (root.phase === "result") { root.cmdCli(["cancel-action"]); return }
    root.cmdCli(["record", "start"])
  }

  // Abort whatever is in flight — never executes. Mirrors the popover's
  // cancelPending(): awaiting -> clear the pending action; recording or
  // processing -> stop and discard the audio.
  function cancelPending() {
    if (root.awaiting) { root.cmdCli(["cancel-action"]); return }
    if (root.recording || root.working) { root.cmdCli(["record", "cancel"]); return }
  }

  // Snap the bob animation back to rest before a drag, then persist on release.
  onDraggingChanged: if (root.dragging) spriteFloat.y = 0

  // ================= overlay window =================

  PanelWindow {
    id: panel
    visible: root.petVisible
    color: "transparent"
    anchors { left: true; top: true }
    margins { left: root.petX; top: root.petY }
    WlrLayershell.namespace: "omaok-voice-pet"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore

    readonly property int bubbleInset: Style.space(24)
    readonly property int bubbleH: bubble.visible ? bubble.height : 0
    implicitWidth: Math.max(root.petSize, bubble.visible ? bubble.width : root.petSize)
    implicitHeight: (bubble.visible ? bubble.height + Style.space(6) : 0) + root.petSize

    Item {
      anchors.fill: parent

      // ---- speech bubble (above the pet) ----
      // Sized by explicit content widths (no anchors.fill on the inner column)
      // so implicit sizes never form a cycle (which collapsed the window to 1px).
      Rectangle {
        id: bubble
        // Always in layout (reserved slot above the pet) so showing/hiding it
        // never moves the pet; fade controls visibility.
        opacity: root.bubbleShown ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 130 } }
        width: bubbleCol.width + Style.space(22)
        height: bubbleCol.height + Style.space(14)
        radius: Style.space(11)
        color: Qt.rgba(0.04, 0.05, 0.09, 0.86)
        border.color: Qt.rgba(1, 1, 1, 0.14)
        border.width: 1
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top

        Column {
          id: bubbleCol
          width: Math.max(root.bubbleTextW, actionRow.visible ? actionRow.width : 0)
          anchors.centerIn: parent
          spacing: Style.space(6)

          Text {
            width: root.bubbleTextW
            text: root.bubbleText()
            color: "#f2f2f2"
            font.family: Style.font.family
            font.pixelSize: Style.font.caption + 1
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
          }

          Row {
            id: actionRow
            visible: root.awaiting
            spacing: Style.space(8)
            height: Style.space(30)

            Button {
              width: (root.bubbleTextW - Style.space(8)) * 0.5
              height: Style.space(30)
              iconText: "\uF00C"          // ✓
              foreground: "#f2f2f2"
              accent: Color.accent
              onClicked: root.cmdCli(["confirm"])
            }
            Button {
              width: (root.bubbleTextW - Style.space(8)) * 0.5
              height: Style.space(30)
              iconText: "\uF00D"          // ✗
              foreground: "#f2f2f2"
              accent: Color.accent
              onClicked: root.cmdCli(["cancel-action"])
            }
          }
        }

        // tail
        Rectangle {
          width: Style.space(10)
          height: Style.space(10)
          color: bubble.color
          rotation: 45
          anchors.horizontalCenter: parent.horizontalCenter
          anchors.top: parent.bottom
          anchors.topMargin: -Style.space(5)
        }
      }

      // ---- the pet ----
      Item {
        id: spriteArea
        width: root.petSize
        height: root.petSize
        y: bubble.visible ? bubble.height + Style.space(6) : 0
        anchors.horizontalCenter: parent.horizontalCenter

        // ground shadow
        Rectangle {
          width: root.petSize * (root.phase === "executing" ? 0.46 : 0.6)
          height: root.petSize * 0.11
          radius: height / 2
          color: Qt.rgba(0, 0, 0, root.phase === "executing" ? 0.1 : 0.2)
          anchors.horizontalCenter: parent.horizontalCenter
          anchors.bottom: parent.bottom
          anchors.bottomMargin: root.petSize * 0.05
          Behavior on width { NumberAnimation { duration: 200 } }
          Behavior on color { ColorAnimation { duration: 200 } }
        }

        // float layer: NOT anchored, so y/scale animations are free; the
        // interaction area rides with it so clicks always hit the visible pet.
        Item {
          id: spriteFloat
          width: parent.width
          height: parent.height
          opacity: root.petOpacity
          Behavior on opacity { NumberAnimation { duration: 150 } }

          // idle breathing bob
          SequentialAnimation on y {
            running: root.phase === "idle" && !root.dragging && root.error === ""
            loops: Animation.Infinite
            NumberAnimation { duration: 1900; from: 0; to: -4; easing.type: Easing.InOutSine }
            NumberAnimation { duration: 1900; from: -4; to: 0; easing.type: Easing.InOutSine }
          }
          // flying bob while executing
          SequentialAnimation on y {
            running: root.phase === "executing" && !root.dragging
            loops: Animation.Infinite
            NumberAnimation { duration: 400; from: 0; to: -7; easing.type: Easing.InOutQuad }
            NumberAnimation { duration: 400; from: -7; to: 0; easing.type: Easing.InOutQuad }
          }
          // listening pulse
          SequentialAnimation on scale {
            running: root.recording
            loops: Animation.Infinite
            NumberAnimation { duration: 550; from: 1.0; to: 1.07; easing.type: Easing.InOutSine }
            NumberAnimation { duration: 550; from: 1.07; to: 1.0; easing.type: Easing.InOutSine }
          }

          Image {
            id: sprite
            anchors.fill: parent
            source: root.spriteUrl()
            sourceSize: Qt.size(256, 256)
            fillMode: Image.PreserveAspectFit
            smooth: true
          }

          // interaction: drag moves the pet, click runs the phase action
          MouseArea {
            id: hoverArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            property int sx: 0
            property int sy: 0
            property int startX: 0
            property int startY: 0
            property bool moved: false

            onPressed: function (m) {
              // Right-click aborts the in-flight phase (never executes).
              if (m.button === Qt.RightButton) {
                if (root.cancellable) root.cancelPending()
                return
              }
              // Left press: begin drag/click gesture.
              root.dragging = true
              hoverArea.sx = m.x
              hoverArea.sy = m.y
              hoverArea.startX = root.petX
              hoverArea.startY = root.petY
              hoverArea.moved = false
            }
            onPositionChanged: function (m) {
              if (!pressed) return
              var dx = m.x - hoverArea.sx
              var dy = m.y - hoverArea.sy
              if (Math.abs(dx) + Math.abs(dy) > 6) hoverArea.moved = true
              var maxX = Math.max(0, panel.screen ? panel.screen.width - panel.width : root.petX)
              var maxY = Math.max(0, panel.screen ? panel.screen.height - panel.height : root.petY)
              root.petX = Math.max(4, Math.min(maxX - 4, hoverArea.startX + dx))
              root.petY = Math.max(4, Math.min(maxY - 4, hoverArea.startY + dy))
            }
            onReleased: {
              root.dragging = false
              if (hoverArea.moved) root.cmdCli(["pet", "pos", String(root.petX), String(root.petY)])
              else root.activate()
            }
            onContainsMouseChanged: root.hovered = containsMouse
            acceptedButtons: Qt.LeftButton | Qt.RightButton
          }
        }

        // Cancel badge: a small frosted ✗ that aborts the in-flight phase.
        // Sits above the pet's interaction area so it wins clicks. No red
        // fill — translucent glass that darkens on hover.
        Rectangle {
          visible: root.cancellable
          width: Style.space(22)
          height: Style.space(22)
          radius: width / 2
          color: cancelBadgeMouse.containsMouse
            ? Qt.rgba(0, 0, 0, 0.58)
            : Qt.rgba(0, 0, 0, 0.30)
          border.color: Qt.rgba(1, 1, 1, 0.5)
          border.width: 1
          anchors.top: spriteArea.top
          anchors.right: spriteArea.right
          anchors.topMargin: -Style.space(1)
          anchors.rightMargin: -Style.space(1)
          Behavior on color { ColorAnimation { duration: 130 } }

          Text {
            anchors.centerIn: parent
            text: "\uF00D"          // ✗
            color: "#ffffff"
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          MouseArea {
            id: cancelBadgeMouse
            anchors.fill: parent
            hoverEnabled: true
            onClicked: root.cancelPending()
          }
        }
    }
  }
}
}
