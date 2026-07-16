from __future__ import annotations

import math


def parse_timecode(value: str | float | int) -> float:
    """Parse seconds, MM:SS, or HH:MM:SS into seconds."""
    if isinstance(value, (int, float)):
        seconds = float(value)
    else:
        text = value.strip()
        if not text:
            raise ValueError("time cannot be empty")

        parts = text.split(":")
        if len(parts) > 3:
            raise ValueError(f"invalid timecode: {value!r}")

        try:
            numbers = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError(f"invalid timecode: {value!r}") from exc

        if any(number < 0 for number in numbers):
            raise ValueError("time cannot be negative")
        if len(numbers) > 1 and any(number >= 60 for number in numbers[1:]):
            raise ValueError("minutes and seconds must be less than 60")

        seconds = 0.0
        for number in numbers:
            seconds = seconds * 60 + number

    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("time must be a finite, non-negative number")
    return seconds


def format_timecode(seconds: float, *, precision: int = 3) -> str:
    """Format seconds as HH:MM:SS.sss or MM:SS.sss."""
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("time must be a finite, non-negative number")

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    remaining = seconds % 60
    width = 2 + (1 if precision else 0) + precision
    tail = f"{remaining:0{width}.{precision}f}"
    if hours:
        return f"{hours:02d}:{minutes:02d}:{tail}"
    return f"{minutes:02d}:{tail}"

