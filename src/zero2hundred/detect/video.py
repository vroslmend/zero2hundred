from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from zero2hundred.detect import _cv2 as cv2
from zero2hundred.detect import _np as np
from zero2hundred.detect import require
from zero2hundred.errors import MediaError
from zero2hundred.media import MediaInfo


def iter_frames(
    path: Path,
    media: MediaInfo,
    *,
    step: int = 1,
    max_height: int = 360,
) -> Iterator[tuple[int, "np.ndarray"]]:
    """Yield source frame indices with rotated, downscaled grayscale frames."""
    require()
    if step < 1:
        raise ValueError("step must be at least 1")
    if max_height < 1:
        raise ValueError("max_height must be at least 1")

    capture = cv2.VideoCapture(str(path))
    if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
        capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
    if not capture.isOpened():
        capture.release()
        raise MediaError(f"could not open video frames from {path.name}")

    frame_index = 0
    try:
        while True:
            readable, frame = capture.read()
            if not readable:
                break
            if frame_index % step == 0:
                rotated = _apply_rotation(frame, media.rotation)
                gray = cv2.cvtColor(rotated, cv2.COLOR_BGR2GRAY)
                yield frame_index, _downscale(gray, max_height)
            frame_index += 1
    finally:
        capture.release()


def _apply_rotation(frame: "np.ndarray", rotation: int) -> "np.ndarray":
    if rotation == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if rotation == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    return frame


def _downscale(frame: "np.ndarray", max_height: int) -> "np.ndarray":
    height, width = frame.shape[:2]
    if height <= max_height:
        return frame
    scale = max_height / height
    resized_width = max(1, round(width * scale))
    return cv2.resize(
        frame,
        (resized_width, max_height),
        interpolation=cv2.INTER_AREA,
    )
