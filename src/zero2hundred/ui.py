"""Terminal styling with no dependencies.

Colour and glyphs are added only on a real terminal; when output is piped,
redirected, or ``NO_COLOR`` is set, every helper returns plain text, so logs
and tests see exactly what they did before styling existed.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
import sys
import threading
from typing import IO

_RESET = "\x1b[0m"
# Monochrome by design: hierarchy comes from weight, not hue. Colour is reserved
# for status only (green success, red failure).
_CODES = {
    "bold": "\x1b[1m",
    "muted": "\x1b[38;5;245m",  # readable mid-grey for labels and status text
    "dim": "\x1b[38;5;240m",  # fainter grey, used only for the progress track
    "ok": "\x1b[32m",
    "error": "\x1b[31m",
}
_UNICODE = {
    "check": "✓", "arrow": "→", "cross": "✗",
    "bar_full": "━", "bar_empty": "─",  # heavy vs light line: reads even without colour
    "spin": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
}
_ASCII = {
    "check": "+", "arrow": ">", "cross": "x",
    "bar_full": "=", "bar_empty": "-",
    "spin": "|/-\\",
}


def should_style(stream: IO[str], env: Mapping[str, str]) -> bool:
    """Decide whether to emit ANSI styling, following the NO_COLOR/FORCE_COLOR standard."""
    if env.get("NO_COLOR"):
        return False
    if env.get("FORCE_COLOR") or env.get("CLICOLOR_FORCE"):
        return True
    if env.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _supports_unicode(stream: IO[str]) -> bool:
    encoding = getattr(stream, "encoding", None) or ""
    try:
        "".join(_UNICODE.values()).encode(encoding or "utf-8")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _enable_windows_vt() -> bool:
    """Turn on ANSI processing for the current Windows console; return success."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except (OSError, AttributeError):
        return False


class UI:
    """Renders styled or plain text; the same call sites work either way."""

    def __init__(self, *, styled: bool, unicode: bool = True) -> None:
        self.styled = styled
        self._glyphs = _UNICODE if (styled and unicode) else _ASCII

    def _wrap(self, code: str, text: str) -> str:
        return f"{code}{text}{_RESET}" if self.styled else text

    def muted(self, text: str) -> str:
        return self._wrap(_CODES["muted"], text)

    def dim(self, text: str) -> str:
        return self._wrap(_CODES["dim"], text)

    def bold(self, text: str) -> str:
        return self._wrap(_CODES["bold"], text)

    def ok(self, text: str) -> str:
        return self._wrap(_CODES["ok"], text)

    def error(self, text: str) -> str:
        return self._wrap(_CODES["error"], text)

    def note(self, text: str) -> str:
        """Transient status text (muted grey on a terminal)."""
        return self.muted(text)

    def success(self, text: str) -> str:
        if not self.styled:
            return text
        return f"{self.ok(self._glyphs['check'])} {text}"

    def fail(self, text: str) -> str:
        if not self.styled:
            return text
        return f"{self.error(self._glyphs['cross'])} {text}"

    def heading(self, text: str) -> str:
        return self.bold(text)

    def row(self, label: str, value: str, width: int = 12) -> str:
        return f"  {self.muted(f'{label:<{width}}')}{value}"

    def step(self, text: str, *, state: str = "done") -> str:
        glyph = {"done": self.ok(self._glyphs["check"]),
                 "active": self.muted(self._glyphs["arrow"]),
                 "fail": self.error(self._glyphs["cross"])}
        if not self.styled:
            return f"  {text}"
        return f"  {glyph[state]} {text}"

    def bar(self, fraction: float, *, width: int = 24) -> str:
        fraction = max(0.0, min(1.0, fraction))
        filled = round(fraction * width)
        percent = f"{round(fraction * 100):3d}%"
        full = self._glyphs["bar_full"] * filled
        empty = self._glyphs["bar_empty"] * (width - filled)
        if not self.styled:
            return f"{full}{empty}  {percent}"
        return f"{self.bold(full)}{self.dim(empty)}  {self.bold(percent)}"

    def spinner(self, message: str) -> "Spinner":
        return Spinner(self, message)


class Spinner:
    """A single-line spinner for an indeterminate step; a static line when unstyled."""

    def __init__(self, ui: UI, message: str, *, stream: IO[str] | None = None) -> None:
        self._ui = ui
        self._message = message
        self._stream = stream if stream is not None else sys.stdout
        self._frames = ui._glyphs["spin"]
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def __enter__(self) -> "Spinner":
        if self._ui.styled:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        else:
            # No animation off a terminal: emit the same static line as before.
            print(f"  {self._message}", file=self._stream)
        return self

    def __exit__(self, *exc: object) -> None:
        self._halt()

    def _halt(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _spin(self) -> None:
        index = 0
        while not self._stop.wait(0.08):
            frame = self._ui.accent(self._frames[index % len(self._frames)])
            self._stream.write(f"\r  {frame} {self._message}")
            self._stream.flush()
            index += 1

    def done(self, message: str) -> None:
        """On a terminal, replace the spinner line with a finished step; plain output
        already printed its single static line on entry."""
        self._halt()
        if self._ui.styled:
            self._stream.write("\r\x1b[2K")  # return to line start and clear it
            print(self._ui.step(message, state="done"), file=self._stream)


def build(stream: IO[str] | None = None, env: Mapping[str, str] | None = None) -> UI:
    """Create a UI configured for the given stream and environment."""
    stream = stream if stream is not None else sys.stdout
    env = env if env is not None else os.environ
    styled = should_style(stream, env)
    # A real Windows console needs VT turned on or ANSI shows as raw codes; if that
    # fails there, disable styling. A pipe (e.g. forced color to a file) needs no
    # console mode change, so its failure must not switch styling off.
    if styled and sys.platform == "win32" and _isatty(stream):
        styled = _enable_windows_vt()
    return UI(styled=styled, unicode=styled and _supports_unicode(stream))


def _isatty(stream: IO[str]) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False
