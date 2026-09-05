"""Microphone recording for omarchy-voice-control.

Records raw s16le PCM at 16 kHz mono from the default PipeWire source via
`pw-record` (header-independent: killing the recorder at any moment still
yields a valid byte stream). The raw PCM is wrapped into a standard WAV file
locally so Voxtype's `transcribe` can read it.

Lifecycle across CLI invocations: `record start` and `record stop` are separate
processes, so the recorder's PID is persisted to a pid file. stop/cancel always
kill the recorder (by PID/process-group) so it can never keep recording
invisibly or grow the WAV unboundedly. A `timeout` wrapper caps the capture at
`max_seconds`, and the WAV wrapper truncates to the cap as a second bound.

No keystroke capture, clipboard scraping, or focus tricks are involved.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
import wave

from config import settings
from state import raw_path, recorder_pid_path, wav_path


def _read_pid() -> int | None:
    try:
        with open(recorder_pid_path(), "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _write_pid(pid: int) -> None:
    try:
        with open(recorder_pid_path(), "w", encoding="utf-8") as fh:
            fh.write(str(pid))
    except OSError:
        pass


def _clear_pid() -> None:
    try:
        os.unlink(recorder_pid_path())
    except OSError:
        pass


def _kill_group(pid: int) -> None:
    """Terminate the recorder process group; fall back to the process itself."""
    if not pid:
        return
    try:
        group = os.getpgid(pid)
        os.killpg(group, signal.SIGTERM)
        return
    except OSError:
        pass
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _wait_gone(pid: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)  # still alive?
        except OSError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


class Recorder:
    """One-shot recorder. Call start(), then stop() (or cancel())."""

    def __init__(self, device: str = "default", max_seconds: float = 30.0):
        self._proc = None
        self._fh = None
        self._device = device
        self._max_seconds = max_seconds
    def start(self) -> None:
        # Never leave a previous recorder running (e.g. from a crashed CLI).
        self._kill_existing()
        raw = raw_path()
        cmd = [
            "timeout", str(max(1, int(self._max_seconds))),
            "pw-record",
            "--rate", "16000",
            "--channels", "1",
            "--format", "s16",
            "-",
        ]
        self._fh = open(raw, "wb")
        self._proc = subprocess.Popen(
            cmd,
            stdout=self._fh,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _write_pid(self._proc.pid)

    @property
    def pid(self) -> int | None:
        """The spawned recorder's PID (None until start() is called)."""
        return self._proc.pid if self._proc is not None else None

    @staticmethod
    def _kill_existing() -> None:
        pid = _read_pid()
        if pid:
            _kill_group(pid)
            _wait_gone(pid)
            _clear_pid()

    @property
    def running(self) -> bool:
        if self._proc is not None:
            return self._proc.poll() is None
        pid = _read_pid()
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _stop_process(self) -> None:
        # In-process child, if any.
        if self._proc is not None:
            proc = self._proc
            self._proc = None
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception:
                pass
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
            _clear_pid()
            return
        # Cross-process recorder: kill by persisted pid.
        self._kill_existing()

    def stop(self) -> None:
        """Finish recording and materialize a bounded WAV file."""
        self._stop_process()
        wrap_raw_to_wav(max_seconds=self._max_seconds)

    def cancel(self) -> None:
        """Stop recording and discard all audio."""
        self._stop_process()
        for path in (raw_path(), wav_path()):
            try:
                os.unlink(path)
            except OSError:
                pass


def raw_duration_seconds() -> float:
    try:
        size = os.path.getsize(raw_path())
        return size / 2.0 / 16000.0
    except OSError:
        return 0.0


class EnergyVAD:
    """Zero-dependency energy-based voice activity detection (16 kHz mono s16).

    States: wait (no speech yet — never auto-stops) -> speech -> done (after
    `silence_ms` of trailing quiet). The noise floor adapts live so the
    threshold tracks the room.
    """

    FRAME = 320  # 20 ms at 16 kHz s16

    def __init__(self, silence_ms: int = 1200, threshold_factor: float = 4.0,
                 floor: float = 300.0):
        self.silence_frames = max(1, silence_ms // 20)
        self.factor = threshold_factor
        self.floor = float(floor)
        self.noise: float | None = None
        self.state = "wait"
        self.trail = 0

    def feed(self, data: bytes) -> None:
        if not data or self.state == "done":
            return
        try:
            import array
            samples = array.array("h", data)
        except Exception:
            return
        if not samples:
            return
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        if self.noise is None:
            self.noise = rms
        else:
            self.noise = self.noise * 0.98 + rms * 0.02
        voiced = rms > max(self.noise * self.factor, self.floor)
        if self.state == "wait":
            if voiced:
                self.state = "speech"
        elif self.state == "speech":
            if voiced:
                self.trail = 0
            else:
                self.trail += 1
                if self.trail >= self.silence_frames:
                    self.state = "done"

    @property
    def done(self) -> bool:
        return self.state == "done"


class AutoRecorder(Recorder):
    """VAD-driven recorder: pw-record streams raw PCM to a pipe, frames are
    appended to raw_path live while EnergyVAD watches for speech + trailing
    silence. Manual stop/cancel still work (they kill pw-record by pid; the
    pipe EOF is treated as a manual stop, so the caller's watchdog finalizes).
    """

    def start(self) -> None:
        self._kill_existing()
        self._vad = EnergyVAD()
        self._fh = open(raw_path(), "wb")
        self._proc = subprocess.Popen(
            [
                "pw-record", "--rate", "16000",
                "--channels", "1", "--format", "s16", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _write_pid(self._proc.pid)

    def wait_natural_end(self, max_seconds: float = 30.0) -> bool:
        """Block until trailing silence after speech (or max_seconds), appending
        frames to raw_path. Returns True on a natural end, False when pw-record
        died first (manual stop/cancel — the caller's watchdog finalizes)."""
        start = time.monotonic()
        while True:
            data = self._proc.stdout.read(EnergyVAD.FRAME * 2)
            if not data:
                return False
            self._fh.write(data)
            self._vad.feed(data)
            if self._vad.done or time.monotonic() - start > max_seconds:
                return True

    def detected_speech(self) -> bool:
        return bool(self._vad and self._vad.state in ("speech", "done"))


def wrap_raw_to_wav(max_seconds: float = 30.0) -> int:
    """Convert the raw PCM capture into a bounded 16 kHz mono WAV. Returns sample count."""
    try:
        with open(raw_path(), "rb") as fh:
            raw = fh.read()
    except OSError:
        raw = b""
    # Safety bound: cap at max_seconds even if the recorder ran long.
    cap_bytes = int(max(1.0, max_seconds) * 16000) * 2
    if len(raw) > cap_bytes:
        raw = raw[:cap_bytes]
    raw = raw[: len(raw) // 2 * 2]
    with wave.open(wav_path(), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(raw)
    return len(raw) // 2
