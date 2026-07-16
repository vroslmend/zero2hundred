from __future__ import annotations

from zero2hundred.errors import DetectionUnavailable


_cv2 = None
_np = None
_IMPORT_ERROR: ImportError | OSError | None = None

try:
    import cv2 as _cv2
    import numpy as _np
except (ImportError, OSError) as exc:
    _IMPORT_ERROR = exc

_HAS_DETECTION = _cv2 is not None and _np is not None


def available() -> bool:
    """Return whether the optional detection dependencies can be imported."""
    return bool(_HAS_DETECTION)


def require() -> None:
    """Raise a clear application error when the detection extra is unavailable."""
    if not available():
        raise DetectionUnavailable(
            "automatic detection needs the detect extra: pip install -e .[detect]"
        ) from _IMPORT_ERROR
