from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile

from zero2hundred.config import RenderSettings
from zero2hundred.errors import MediaError
from zero2hundred.events import EventWindow
from zero2hundred.media import MediaInfo, Toolchain


ProgressCallback = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class RenderJob:
    media: MediaInfo
    events: EventWindow
    output: Path
    settings: RenderSettings
    toolchain: Toolchain
    trim_intro: bool = False
    overwrite: bool = False

    @property
    def output_duration(self) -> float:
        content = self.events.elapsed if self.trim_intro else self.events.reached_100
        return content + self.media.frame_duration + self.settings.freeze_duration

    def command(self) -> list[str]:
        graph = build_filter_graph(
            self.media,
            self.events,
            self.settings,
            trim_intro=self.trim_intro,
        )
        command = [
            self.toolchain.ffmpeg,
            "-hide_banner",
            "-y" if self.overwrite else "-n",
            "-to",
            f"{min(self.media.duration, self.events.reached_100 + self.media.frame_duration):.6f}",
            "-i",
            str(self.media.path),
            "-filter_complex",
            graph,
            "-map",
            "[video]",
        ]
        if self.media.has_audio:
            command.extend(["-map", "[audio]"])
        command.extend(
            [
                "-c:v",
                self.settings.video_encoder,
                "-preset",
                self.settings.preset,
                "-crf",
                str(self.settings.crf),
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if self.media.has_audio:
            command.extend(
                [
                    "-c:a",
                    "aac",
                    "-b:a",
                    self.settings.audio_bitrate,
                ]
            )
        command.extend(["-movflags", "+faststart", "-progress", "pipe:1", "-nostats"])
        command.append(str(self.output))
        return command

    def run(self, progress: ProgressCallback | None = None) -> None:
        self.events.validate(self.media.duration)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        if self.output.exists() and not self.overwrite:
            raise MediaError(f"output already exists: {self.output}")
        if self.output.resolve() == self.media.path.resolve():
            raise MediaError("output path cannot overwrite the source video")

        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                self.command(),
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                assert process.stdout is not None
                for raw_line in process.stdout:
                    key, separator, raw_value = raw_line.strip().partition("=")
                    if separator and key in {"out_time_us", "out_time_ms"}:
                        try:
                            current = int(raw_value) / 1_000_000
                        except ValueError:
                            continue
                        if progress:
                            progress(min(1.0, current / self.output_duration))
                return_code = process.wait()
            except KeyboardInterrupt:
                process.terminate()
                process.wait()
                raise

            if return_code:
                stderr.seek(0)
                detail = stderr.read().strip()
                raise MediaError(f"FFmpeg export failed:\n{detail}")
        if progress:
            progress(1.0)


def build_filter_graph(
    media: MediaInfo,
    events: EventWindow,
    settings: RenderSettings,
    *,
    trim_intro: bool,
) -> str:
    clip_start = events.launch if trim_intro else 0.0
    timer_start = 0.0 if trim_intro else events.launch
    clip_end = min(media.duration, events.reached_100 + media.frame_duration)

    x, y = _position(settings.position, settings.margin_ratio)
    timer = _timer_text(timer_start, events.elapsed, settings.timer_style)
    font_option = (
        f"fontfile='{_escape_filter_value(settings.font_file)}'"
        if settings.font_file
        else f"font='{_escape_filter_value(settings.font)}'"
    )
    drawtext_options = [
        font_option,
        f"text='{timer}'",
        f"fontsize=h*{settings.font_size_ratio:.6f}",
        f"fontcolor={settings.text_color}",
        f"bordercolor={settings.border_color}",
        f"borderw={settings.border_width}",
        f"x={x}",
        f"y={y}",
    ]
    if settings.timer_style == "hms":
        drawtext_options.append(f"enable='gte(t,{timer_start:.6f})'")
    drawtext = "drawtext=" + ":".join(drawtext_options)
    video_chain = (
        f"[0:v]trim=start={clip_start:.6f}:end={clip_end:.6f},"
        f"setpts=PTS-STARTPTS,fps=fps={media.frame_rate:.6f},{drawtext},"
        f"tpad=stop_mode=clone:stop_duration={settings.freeze_duration:.6f}[video]"
    )

    if not media.has_audio:
        return video_chain

    audio_end = min(media.duration, events.reached_100 + media.frame_duration)
    audio_chain = (
        f"[0:a]atrim=start={clip_start:.6f}:end={audio_end:.6f},"
        "asetpts=PTS-STARTPTS,"
        f"apad=pad_dur={settings.freeze_duration:.6f}[audio]"
    )
    return f"{video_chain};{audio_chain}"


def _timer_text(timer_start: float, elapsed: float, style: str) -> str:
    if style == "hms":
        # FFmpeg's pts formatter produces HH:MM:SS.mmm and accepts a timestamp offset.
        return f"%{{pts\\:hms\\:-{timer_start:.6f}}}"
    # Clamp elapsed time to [0, ELAPSED] so the frozen tail keeps showing the
    # exact result instead of drifting past it, and render as MM:SS:cc.
    v = f"min(max(t-{timer_start:.6f}\\,0)\\,{elapsed:.6f})"
    return (
        f"%{{eif\\:trunc({v}/60)\\:d\\:2}}\\:"
        f"%{{eif\\:trunc(mod({v}\\,60))\\:d\\:2}}\\:"
        f"%{{eif\\:trunc(mod({v}\\,1)*100)\\:d\\:2}}"
    )


def _position(position: str, margin: float) -> tuple[str, str]:
    left = f"w*{margin:.6f}"
    right = f"w-text_w-w*{margin:.6f}"
    center = "(w-text_w)/2"
    top = f"h*{margin:.6f}"
    bottom = f"h-text_h-h*{margin:.6f}"
    positions = {
        "top-left": (left, top),
        "top-right": (right, top),
        "top-center": (center, top),
        "bottom-left": (left, bottom),
        "bottom-right": (right, bottom),
        "bottom-center": (center, bottom),
    }
    return positions[position]


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
