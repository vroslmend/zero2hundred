from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from pathlib import Path
import subprocess

from zero2hundred.errors import MediaError
from zero2hundred.media import Toolchain


def frame_times(path: Path, toolchain: Toolchain) -> list[float]:
    """Return the sorted presentation timestamps of every video frame."""
    command = [
        toolchain.ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "packet=pts_time",
        "-of",
        "csv=p=0",
        str(path),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or "unknown FFprobe error"
        raise MediaError(f"could not read frame timestamps for {path.name}: {detail}")

    times = _parse_pts_lines(completed.stdout)
    if not times:
        raise MediaError(f"no frame timestamps were found in {path.name}")
    return times


def _parse_pts_lines(text: str) -> list[float]:
    times: list[float] = []
    for line in text.splitlines():
        value = line.strip()
        if not value or value.upper() == "N/A" or "side_data" in value.lower():
            continue
        try:
            times.append(float(value))
        except ValueError:
            continue
    return sorted(times)


def snap_to_frame(times: Sequence[float], t: float) -> float:
    """Return the timestamp in `times` nearest to `t`; ties prefer the earlier frame."""
    if not times:
        raise ValueError("times cannot be empty")
    index = bisect_left(times, t)
    if index == 0:
        return times[0]
    if index == len(times):
        return times[-1]
    before = times[index - 1]
    after = times[index]
    if (after - t) < (t - before):
        return after
    return before


def frame_after(times: Sequence[float], t: float) -> float | None:
    """Return the first timestamp strictly after `t`, or None if `t` is at/after the last frame."""
    index = bisect_right(times, t)
    if index == len(times):
        return None
    return times[index]
