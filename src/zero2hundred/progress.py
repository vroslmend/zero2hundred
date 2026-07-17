from __future__ import annotations

from collections.abc import Callable
import subprocess
import tempfile

from zero2hundred.errors import MediaError

ProgressCallback = Callable[[float], None]


class ProgressReporter:
    """Redraw a single progress line in place, once per whole percent. On a terminal
    it draws a bar; otherwise it keeps the plain '  Progress    NN%' row."""

    def __init__(self, ui: object | None = None) -> None:
        self._last_percent = -1
        self._ui = ui

    def __call__(self, fraction: float) -> None:
        percent = max(0, min(100, int(fraction * 100)))
        if percent == self._last_percent:
            return
        self._last_percent = percent
        if self._ui is not None and getattr(self._ui, "styled", False):
            print(f"\r  {self._ui.bar(fraction)}", end="", flush=True)
        else:
            print(f"\r  Progress    {percent:3d}%", end="", flush=True)

    def finish(self) -> None:
        if self._last_percent >= 0:
            print()


def stream_ffmpeg_progress(
    command: list[str],
    duration: float,
    progress: ProgressCallback | None,
    *,
    error_prefix: str,
) -> None:
    """Run an FFmpeg command that writes `-progress` to stdout, reporting fractional
    progress against `duration`.

    Raises MediaError(error_prefix + detail) if FFmpeg exits non-zero. On
    KeyboardInterrupt the child process is terminated before the exception
    propagates so no encoder is left running.
    """
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as errors:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            assert process.stdout is not None
            for line in process.stdout:
                key, separator, value = line.strip().partition("=")
                if not separator or key not in {"out_time_us", "out_time_ms"}:
                    continue
                try:
                    current = int(value) / 1_000_000
                except ValueError:
                    continue
                if progress and duration > 0:
                    progress(min(1.0, current / duration))
            return_code = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            process.wait()
            raise

        if return_code:
            errors.seek(0)
            detail = errors.read().strip() or "unknown FFmpeg error"
            raise MediaError(f"{error_prefix}{detail}")
