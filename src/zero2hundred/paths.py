from __future__ import annotations

from pathlib import Path


def parse_dropped_path(value: str) -> Path:
    """Normalize paths pasted or dragged into a Windows terminal."""
    text = value.strip()
    if text.startswith("&"):
        text = text[1:].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    return Path(text).expanduser()


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_0-100.mp4")


def available_output_path(preferred: Path) -> Path:
    if not preferred.exists():
        return preferred
    for index in range(2, 10_000):
        candidate = preferred.with_name(f"{preferred.stem}_{index}{preferred.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("could not find an available output filename")

