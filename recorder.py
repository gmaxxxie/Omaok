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
