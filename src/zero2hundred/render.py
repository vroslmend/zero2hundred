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
_MANROPE_FONT = Path(__file__).with_name("assets") / "Manrope-Medium.ttf"


@dataclass(frozen=True, slots=True)
class RenderJob:
    media: MediaInfo
    events: EventWindow
    output: Path
    settings: RenderSettings
    toolchain: Toolchain
    trim_intro: bool = False
    overwrite: bool = False
    clip_end: float | None = None

    @property
    def output_duration(self) -> float:
        freeze_at = _resolve_clip_end(self.media, self.events, self.clip_end)
        if _continues_after_freeze(self.media, self.settings, freeze_at):
            content = self.media.duration - (self.events.launch if self.trim_intro else 0.0)
        else:
            content = freeze_at - (self.events.launch if self.trim_intro else 0.0)
        return content + self.settings.freeze_duration

    def command(self) -> list[str]:
        graph = build_filter_graph(
            self.media,
            self.events,
            self.settings,
            trim_intro=self.trim_intro,
            clip_end=self.clip_end,
        )
        freeze_at = _resolve_clip_end(self.media, self.events, self.clip_end)
        input_end = (
            self.media.duration
            if _continues_after_freeze(self.media, self.settings, freeze_at)
            else freeze_at
        )
        command = [
            self.toolchain.ffmpeg,
            "-hide_banner",
            "-y" if self.overwrite else "-n",
            "-to",
            f"{input_end:.6f}",
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
    clip_end: float | None = None,
) -> str:
    clip_start = events.launch if trim_intro else 0.0
    timer_start = 0.0 if trim_intro else events.launch
    freeze_at = _resolve_clip_end(media, events, clip_end)
    output_fps = settings.frame_rate or media.frame_rate

    timer_style = settings.timer_style or settings.timer_format
    timer = _timer_text(timer_start, events.elapsed, timer_style)
    font_option = _font_option(settings)
    if timer_style == "hms":
        x, y = _position(settings.position, settings.margin_ratio)
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
        drawtext_options.append(f"enable='gte(t,{timer_start:.6f})'")
        video_filters = ["drawtext=" + ":".join(drawtext_options)]
    else:
        video_filters = _overlay_filters(settings, font_option, timer)
    timed_video = (
        f"[0:v]trim=start={clip_start:.6f}:end={freeze_at:.6f},"
        f"setpts=PTS-STARTPTS,fps=fps={output_fps:.6f},"
        f"{','.join(video_filters)},"
        f"tpad=stop_mode=clone:stop_duration={settings.freeze_duration:.6f}"
    )

    if not _continues_after_freeze(media, settings, freeze_at):
        video_chain = timed_video + "[video]"
        if not media.has_audio:
            return video_chain

        audio_chain = (
            f"[0:a]atrim=start={clip_start:.6f}:end={freeze_at:.6f},"
            "asetpts=PTS-STARTPTS,"
            f"apad=pad_dur={settings.freeze_duration:.6f}[audio]"
        )
        return f"{video_chain};{audio_chain}"

    timed_video += "[timed_video]"
    tail_video = (
        f"[0:v]trim=start={freeze_at:.6f}:end={media.duration:.6f},"
        f"setpts=PTS-STARTPTS,fps=fps={output_fps:.6f}[tail_video]"
    )
    if not media.has_audio:
        concat = "[timed_video][tail_video]concat=n=2:v=1:a=0[video]"
        return f"{timed_video};{tail_video};{concat}"

    timed_audio = (
        f"[0:a]atrim=start={clip_start:.6f}:end={freeze_at:.6f},"
        "asetpts=PTS-STARTPTS[timed_audio]"
    )
    tail_audio = (
        f"[0:a]atrim=start={freeze_at:.6f}:end={media.duration:.6f},"
        "asetpts=PTS-STARTPTS[tail_audio]"
    )
    concat = (
        "[timed_video][timed_audio][tail_video][tail_audio]"
        "concat=n=2:v=1:a=1[video][audio]"
    )
    return ";".join((timed_video, timed_audio, tail_video, tail_audio, concat))


def _timer_text(timer_start: float, elapsed: float, style: str) -> str:
    if style == "hms":
        # FFmpeg's pts formatter produces HH:MM:SS.mmm and accepts a timestamp offset.
        return f"%{{pts\\:hms\\:-{timer_start:.6f}}}"
    # Clamp elapsed time to [0, ELAPSED] so the frozen tail keeps showing the
    # exact result instead of drifting past it.
    v = f"min(max(t-{timer_start:.6f}\\,0)\\,{elapsed:.6f})"
    if style == "seconds":
        return (
            f"%{{eif\\:trunc({v})\\:d}}."
            f"%{{eif\\:trunc(mod({v}\\,1)*100)\\:d\\:2}}"
        )
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


def _font_option(settings: RenderSettings) -> str:
    if settings.font_file:
        path = settings.font_file
        return f"fontfile='{_escape_filter_value(path)}'"
    if settings.font.casefold() == "manrope":
        return f"fontfile='{_escape_filter_value(str(_MANROPE_FONT))}'"
    return f"font='{_escape_filter_value(settings.font)}'"


def _overlay_filters(
    settings: RenderSettings,
    font_option: str,
    timer: str,
) -> list[str]:
    main_size = settings.font_size_ratio * settings.overlay_scale
    label_size = max(0.012, main_size * 0.27)
    unit_size = max(0.012, main_size * 0.34)
    if settings.overlay_style == "quiet-plate":
        return _quiet_plate_filters(
            settings, font_option, timer, main_size, label_size, unit_size
        )
    if settings.overlay_style == "compact":
        return _compact_filters(settings, font_option, timer, main_size)
    return _type_only_filters(
        settings, font_option, timer, main_size, label_size, unit_size
    )


def _type_only_filters(
    settings: RenderSettings,
    font_option: str,
    timer: str,
    main_size: float,
    label_size: float,
    unit_size: float,
) -> list[str]:
    label_x = _aligned_text_x(settings.position, settings.margin_ratio)
    if settings.position.endswith("left"):
        timer_x = f"w*{settings.margin_ratio:.6f}"
        unit_x = f"w*{settings.margin_ratio:.6f}+h*{main_size * 2.55:.6f}"
    elif settings.position.endswith("right"):
        timer_x = (
            f"w-text_w-w*{settings.margin_ratio:.6f}-h*{unit_size * 0.85:.6f}"
        )
        unit_x = f"w-text_w-w*{settings.margin_ratio:.6f}"
    else:
        timer_x = f"(w-text_w)/2-h*{unit_size * 0.18:.6f}"
        unit_x = f"w/2+h*{main_size * 1.28:.6f}"

    if settings.position.startswith("bottom"):
        timer_y = f"h-text_h-h*{settings.bottom_clearance_ratio:.6f}"
        label_y = (
            f"h-h*{settings.bottom_clearance_ratio:.6f}"
            f"-h*{main_size + label_size + 0.010:.6f}"
        )
        unit_y = f"h-text_h-h*{settings.bottom_clearance_ratio + 0.003:.6f}"
    else:
        label_y = f"h*{settings.margin_ratio:.6f}"
        timer_y = f"h*{settings.margin_ratio + label_size + 0.008:.6f}"
        unit_y = (
            f"h*{settings.margin_ratio + label_size + main_size - unit_size + 0.004:.6f}"
        )

    return [
        _drawtext_filter(
            font_option,
            _escape_filter_value(settings.timer_label),
            label_size,
            settings.text_color,
            label_x,
            label_y,
            settings,
            max(1, settings.border_width // 2),
        ),
        _drawtext_filter(
            font_option,
            timer,
            main_size,
            settings.text_color,
            timer_x,
            timer_y,
            settings,
            settings.border_width,
        ),
        _drawtext_filter(
            font_option,
            "s",
            unit_size,
            settings.text_color,
            unit_x,
            unit_y,
            settings,
            max(1, settings.border_width // 2),
        ),
    ]


def _quiet_plate_filters(
    settings: RenderSettings,
    font_option: str,
    timer: str,
    main_size: float,
    label_size: float,
    unit_size: float,
) -> list[str]:
    panel_width = main_size * 4.0 + 0.06
    panel_height = main_size + label_size + 0.045
    panel_x, panel_y = _box_position(
        settings.position,
        settings.margin_ratio,
        settings.bottom_clearance_ratio,
        panel_width,
        panel_height,
    )
    drawbox_x, drawbox_y = _drawbox_position(
        settings.position,
        settings.margin_ratio,
        settings.bottom_clearance_ratio,
        panel_width,
        panel_height,
    )
    label_x = f"{panel_x}+h*0.018000"
    label_y = f"{panel_y}+h*0.013000"
    timer_x = f"{panel_x}+h*0.018000"
    timer_y = f"{panel_y}+h*{label_size + 0.025:.6f}"
    unit_x = f"{panel_x}+h*{0.018 + main_size * 2.55:.6f}"
    unit_y = f"{timer_y}+h*{(main_size - unit_size) * 0.70:.6f}"
    return [
        _drawbox_filter(
            drawbox_x,
            drawbox_y,
            panel_width,
            panel_height,
            settings.panel_color,
        ),
        _drawtext_filter(
            font_option,
            _escape_filter_value(settings.timer_label),
            label_size,
            settings.text_color,
            label_x,
            label_y,
            settings,
            max(1, settings.border_width // 2),
        ),
        _drawtext_filter(
            font_option,
            timer,
            main_size,
            settings.text_color,
            timer_x,
            timer_y,
            settings,
            settings.border_width,
        ),
        _drawtext_filter(
            font_option,
            "s",
            unit_size,
            settings.text_color,
            unit_x,
            unit_y,
            settings,
            max(1, settings.border_width // 2),
        ),
    ]


def _compact_filters(
    settings: RenderSettings,
    font_option: str,
    timer: str,
    main_size: float,
) -> list[str]:
    value_size = main_size * 0.72
    label_size = max(0.012, main_size * 0.23)
    unit_size = max(0.012, value_size * 0.34)
    panel_width = value_size * 4.25 + label_size * 6.0 + 0.07
    panel_height = value_size + 0.032
    panel_x, panel_y = _box_position(
        settings.position,
        settings.margin_ratio,
        settings.bottom_clearance_ratio,
        panel_width,
        panel_height,
    )
    drawbox_x, drawbox_y = _drawbox_position(
        settings.position,
        settings.margin_ratio,
        settings.bottom_clearance_ratio,
        panel_width,
        panel_height,
    )
    divider_offset = label_size * 6.0 + 0.035
    value_offset = label_size * 6.0 + 0.052
    label_y = f"{panel_y}+h*{(panel_height - label_size) / 2 - 0.002:.6f}"
    value_y = f"{panel_y}+h*{(panel_height - value_size) / 2 - 0.002:.6f}"
    unit_y = f"{panel_y}+h*{(panel_height - unit_size) / 2:.6f}"
    return [
        _drawbox_filter(
            drawbox_x,
            drawbox_y,
            panel_width,
            panel_height,
            settings.panel_color,
        ),
        (
            f"drawbox=x={drawbox_x}+ih*{divider_offset:.6f}:"
            f"y={drawbox_y}+ih*0.012000:w=ih*0.001000:"
            f"h=ih*{panel_height - 0.024:.6f}:"
            f"color={_normalized_color(settings.accent_color)}:t=fill"
        ),
        _drawtext_filter(
            font_option,
            _escape_filter_value(settings.timer_label),
            label_size,
            settings.text_color,
            f"{panel_x}+h*0.018000",
            label_y,
            settings,
            max(1, settings.border_width // 2),
        ),
        _drawtext_filter(
            font_option,
            timer,
            value_size,
            settings.text_color,
            f"{panel_x}+h*{value_offset:.6f}",
            value_y,
            settings,
            settings.border_width,
        ),
        _drawtext_filter(
            font_option,
            "s",
            unit_size,
            settings.text_color,
            f"{panel_x}+h*{value_offset + value_size * 2.55:.6f}",
            unit_y,
            settings,
            max(1, settings.border_width // 2),
        ),
    ]


def _drawtext_filter(
    font_option: str,
    text: str,
    size: float,
    color: str,
    x: str,
    y: str,
    settings: RenderSettings,
    border_width: int,
) -> str:
    options = [
        font_option,
        f"text='{text}'",
        f"fontsize=h*{size:.6f}",
        f"fontcolor={_normalized_color(color)}",
        f"bordercolor={_normalized_color(settings.border_color)}",
        f"borderw={border_width}",
        "shadowcolor=black@0.650000",
        "shadowx=1",
        "shadowy=2",
        f"x={x}",
        f"y={y}",
    ]
    return "drawtext=" + ":".join(options)


def _drawbox_filter(
    x: str,
    y: str,
    width: float,
    height: float,
    color: str,
) -> str:
    return (
        f"drawbox=x={x}:y={y}:w=ih*{width:.6f}:h=ih*{height:.6f}:"
        f"color={_normalized_color(color)}:t=fill"
    )


def _aligned_text_x(position: str, margin: float) -> str:
    if position.endswith("left"):
        return f"w*{margin:.6f}"
    if position.endswith("right"):
        return f"w-text_w-w*{margin:.6f}"
    return "(w-text_w)/2"


def _box_position(
    position: str,
    margin: float,
    bottom_clearance: float,
    width: float,
    height: float,
) -> tuple[str, str]:
    left = f"w*{margin:.6f}"
    right = f"w-h*{width:.6f}-w*{margin:.6f}"
    center = f"(w-h*{width:.6f})/2"
    top = f"h*{margin:.6f}"
    bottom = f"h-h*{height:.6f}-h*{bottom_clearance:.6f}"
    positions = {
        "top-left": (left, top),
        "top-right": (right, top),
        "top-center": (center, top),
        "bottom-left": (left, bottom),
        "bottom-right": (right, bottom),
        "bottom-center": (center, bottom),
    }
    return positions[position]


def _drawbox_position(
    position: str,
    margin: float,
    bottom_clearance: float,
    width: float,
    height: float,
) -> tuple[str, str]:
    left = f"iw*{margin:.6f}"
    right = f"iw-ih*{width:.6f}-iw*{margin:.6f}"
    center = f"(iw-ih*{width:.6f})/2"
    top = f"ih*{margin:.6f}"
    bottom = f"ih-ih*{height:.6f}-ih*{bottom_clearance:.6f}"
    positions = {
        "top-left": (left, top),
        "top-right": (right, top),
        "top-center": (center, top),
        "bottom-left": (left, bottom),
        "bottom-right": (right, bottom),
        "bottom-center": (center, bottom),
    }
    return positions[position]


def _normalized_color(value: str) -> str:
    color, separator, alpha = value.partition("@")
    if not separator:
        return value
    try:
        return f"{color}@{float(alpha):.6f}"
    except ValueError:
        return value


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _continues_after_freeze(
    media: MediaInfo, settings: RenderSettings, freeze_at: float
) -> bool:
    return settings.continue_after_freeze and freeze_at < media.duration


def _resolve_clip_end(media: MediaInfo, events: EventWindow, clip_end: float | None) -> float:
    if clip_end is not None:
        return min(media.duration, clip_end)
    return min(media.duration, events.reached_100 + media.frame_duration)
