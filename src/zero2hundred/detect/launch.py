from __future__ import annotations

import math
from pathlib import Path
from statistics import median
from collections.abc import Sequence

from zero2hundred.detect import _np as np
from zero2hundred.detect import require
from zero2hundred.detect.video import iter_frames
from zero2hundred.errors import MediaError
from zero2hundred.media import MediaInfo


FRAME_STEP = 2
MAX_HEIGHT = 240
BASELINE_FRACTION = 0.25
THRESHOLD_K = 4.0
MINIMUM_MAD = 0.05
SMOOTHING_WIDTH = 3
SUSTAIN_SECONDS = 0.5


def suggest_launch(
    path: Path,
    media: MediaInfo,
    times: list[float],
) -> tuple[float, float]:
    """Suggest a launch PTS and confidence from sustained frame motion."""
    require()
    sample_times: list[float] = []
    energies: list[float] = []
    previous = None
    for frame_index, gray in iter_frames(
        path,
        media,
        step=FRAME_STEP,
        max_height=MAX_HEIGHT,
    ):
        if frame_index >= len(times):
            raise MediaError(f"frame timestamps ended early for {path.name}")
        if previous is not None:
            sample_times.append(times[frame_index])
            energies.append(_motion_energy(previous, gray))
        previous = gray

    if not energies:
        raise MediaError(f"not enough video frames to suggest a launch for {path.name}")

    smoothed = _moving_median(energies, width=SMOOTHING_WIDTH)
    _, _, threshold = _baseline_threshold(
        smoothed,
        fraction=BASELINE_FRACTION,
        k=THRESHOLD_K,
        minimum_mad=MINIMUM_MAD,
    )
    run = _find_launch_run(
        sample_times,
        smoothed,
        threshold=threshold,
        sustain_seconds=SUSTAIN_SECONDS,
    )
    if run is None:
        raise MediaError(f"could not find sustained launch motion in {path.name}")

    start, end = run
    margin = max(0.0, median(smoothed[start : end + 1]) - threshold)
    confidence = 1.0 - math.exp(-margin / max(threshold, MINIMUM_MAD))
    return sample_times[start], min(1.0, max(0.0, confidence))


def _motion_energy(previous: "np.ndarray", current: "np.ndarray") -> float:
    difference = np.abs(current.astype(np.int16) - previous.astype(np.int16))
    return float(np.mean(difference))


def _moving_median(values: Sequence[float], *, width: int) -> list[float]:
    if width < 1 or width % 2 == 0:
        raise ValueError("moving median width must be a positive odd number")
    radius = width // 2
    return [
        float(median(values[max(0, index - radius) : index + radius + 1]))
        for index in range(len(values))
    ]


def _baseline_threshold(
    values: Sequence[float],
    *,
    fraction: float,
    k: float,
    minimum_mad: float,
) -> tuple[float, float, float]:
    if not values:
        raise ValueError("motion values cannot be empty")
    if not 0 < fraction <= 1:
        raise ValueError("baseline fraction must be between 0 and 1")
    count = max(1, int(len(values) * fraction))
    initial = list(values[:count])
    cutoff = median(initial)
    # Some clips include a short creep before the measured run. The quieter
    # half of the opening window isolates stopped-camera noise from that motion.
    quiet = [value for value in initial if value <= cutoff]
    baseline = float(median(quiet))
    mad = float(median(abs(value - baseline) for value in quiet))
    threshold = baseline + k * max(mad, minimum_mad)
    return baseline, mad, threshold


def _find_launch_run(
    times: Sequence[float],
    energies: Sequence[float],
    *,
    threshold: float,
    sustain_seconds: float,
) -> tuple[int, int] | None:
    if len(times) != len(energies):
        raise ValueError("motion times and energies must have equal lengths")
    if sustain_seconds < 0:
        raise ValueError("sustain_seconds must be non-negative")
    if not energies:
        return None

    candidates: list[tuple[float, float, int, int]] = []
    quiet_start = 0
    index = 0
    while index < len(energies):
        if energies[index] <= threshold:
            if index == 0 or energies[index - 1] > threshold:
                quiet_start = index
            index += 1
            continue

        high_start = index
        quiet_end = index - 1
        while index < len(energies) and energies[index] > threshold:
            index += 1
        high_end = index - 1
        high_duration = times[high_end] - times[high_start]
        if high_duration < sustain_seconds:
            continue
        quiet_duration = (
            times[quiet_end] - times[quiet_start]
            if quiet_end >= quiet_start
            else 0.0
        )
        candidates.append(
            (quiet_duration, times[high_start], high_start, high_end)
        )

    if not candidates:
        return None
    # A driver can reposition and stop again before launching. The measured
    # run follows the longest stopped interval, not necessarily the first burst.
    _, _, start, end = max(candidates)
    return start, end
