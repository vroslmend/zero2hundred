from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess

from zero2hundred.errors import DependencyError, MediaError


@dataclass(frozen=True, slots=True)
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    frame_rate: float
    has_audio: bool
    video_codec: str | None = None
    audio_codec: str | None = None

    @property
    def frame_duration(self) -> float:
        return 1 / self.frame_rate if self.frame_rate > 0 else 1 / 30


@dataclass(frozen=True, slots=True)
class Toolchain:
    ffmpeg: str
    ffprobe: str


def find_toolchain() -> Toolchain:
    ffmpeg = os.environ.get("ZERO2HUNDRED_FFMPEG") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("ZERO2HUNDRED_FFPROBE") or shutil.which("ffprobe")
    if not ffmpeg:
        raise DependencyError("FFmpeg was not found on PATH")
    if not ffprobe:
        raise DependencyError("FFprobe was not found on PATH")
    return Toolchain(ffmpeg=ffmpeg, ffprobe=ffprobe)


def probe_video(path: Path, toolchain: Toolchain) -> MediaInfo:
    if not path.is_file():
        raise MediaError(f"input video does not exist: {path}")

    command = [
        toolchain.ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
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
        raise MediaError(f"could not inspect {path.name}: {detail}")

    try:
        payload = json.loads(completed.stdout)
        streams = payload.get("streams", [])
        video = next(stream for stream in streams if stream.get("codec_type") == "video")
    except (json.JSONDecodeError, StopIteration, TypeError) as exc:
        raise MediaError(f"no readable video stream found in {path.name}") from exc

    audio = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        None,
    )
    duration = _duration(payload, video)
    frame_rate = _frame_rate(video)

    try:
        width = int(video["width"])
        height = int(video["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MediaError(f"could not determine video dimensions for {path.name}") from exc

    return MediaInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        frame_rate=frame_rate,
        has_audio=audio is not None,
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else None,
    )


def _duration(payload: dict, video: dict) -> float:
    candidates = [
        payload.get("format", {}).get("duration"),
        video.get("duration"),
    ]
    for candidate in candidates:
        try:
            duration = float(candidate)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    raise MediaError("could not determine video duration")


def _frame_rate(video: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = video.get(key)
        if not value or value == "0/0":
            continue
        try:
            rate = float(Fraction(value))
        except (ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            return rate
    return 30.0

