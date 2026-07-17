from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile

from zero2hundred import __version__
from zero2hundred.config import (
    OVERLAY_STYLES,
    POSITIONS,
    TIMER_FORMATS,
    RenderSettings,
    load_settings,
)
from zero2hundred.errors import Zero2HundredError
from zero2hundred.events import EventWindow
from zero2hundred.frames import frame_after, frame_times, snap_to_frame
from zero2hundred.media import Toolchain, find_toolchain, probe_video
from zero2hundred.paths import available_output_path, default_output_path, parse_dropped_path
from zero2hundred.picker import serve_picker
from zero2hundred.render import RenderJob
from zero2hundred.timecode import format_timecode, parse_timecode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zero2hundred",
        description="Create a timed 0-100 km/h video from dashboard footage.",
        epilog=(
            "Timing examples:\n"
            "  zero2hundred run.mp4 --pick\n"
            "  zero2hundred run.mp4 --start 1.395 --end 10.982\n"
            "  zero2hundred run.mp4 --pick --dry-run\n"
            "\n"
            "Made by Ammar Hassan - https://github.com/vroslmend"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", nargs="?", metavar="VIDEO", help="input video path")

    timing = parser.add_argument_group("timing")
    timing.add_argument("--start", metavar="TIME", help="launch timestamp")
    timing.add_argument("--end", metavar="TIME", help="100 km/h timestamp")
    timing.add_argument(
        "--pick",
        action="store_true",
        help="mark both exact frames in the browser",
    )

    clip = parser.add_argument_group("clip")
    clip.add_argument(
        "--trim-intro", action="store_true", help="start output at launch"
    )
    ending = clip.add_mutually_exclusive_group()
    ending.add_argument(
        "--end-after-freeze",
        dest="continue_after_freeze",
        action="store_false",
        help="end the video after the frozen result instead of continuing",
    )
    ending.add_argument(
        "--continue-after-freeze",
        dest="continue_after_freeze",
        action="store_true",
        help="continue the video after the frozen result",
    )
    ending.set_defaults(continue_after_freeze=None)

    appearance = parser.add_argument_group("timer appearance")
    appearance.add_argument(
        "--freeze", type=float, metavar="SECONDS", help="frozen result duration"
    )
    appearance.add_argument(
        "--position",
        choices=POSITIONS,
        metavar="POSITION",
        help=(
            "timer position: top-left, top-center, top-right, bottom-left, "
            "bottom-center, or bottom-right"
        ),
    )
    appearance.add_argument("--font", metavar="NAME", help="timer font family")
    appearance.add_argument("--font-file", metavar="PATH", help="timer font file")
    appearance.add_argument(
        "--overlay-style",
        choices=OVERLAY_STYLES,
        metavar="STYLE",
        help="overlay style: type-only, quiet-plate, or compact",
    )
    appearance.add_argument(
        "--timer-format",
        choices=TIMER_FORMATS,
        metavar="FORMAT",
        help="timer format: seconds or stopwatch",
    )
    appearance.add_argument(
        "--overlay-scale",
        type=float,
        metavar="FACTOR",
        help="overlay size multiplier (0.5-2.0)",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "-o", "--output", type=Path, metavar="PATH", help="output MP4 path"
    )
    output.add_argument(
        "--overwrite", action="store_true", help="replace an existing output file"
    )
    output.add_argument(
        "--dry-run",
        action="store_true",
        help="show the FFmpeg command without exporting",
    )

    settings = parser.add_argument_group("configuration")
    settings.add_argument(
        "--config", type=Path, metavar="PATH", help="TOML settings file"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}\nMade by Ammar Hassan - https://github.com/vroslmend",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.pick and (args.start is not None or args.end is not None):
        parser.error(
            "--pick cannot be combined with --start or --end; "
            "use the browser picker or typed timestamps"
        )

    try:
        settings = _render_settings(args)
        input_path = _input_path(args.input)
        toolchain = find_toolchain()
        print(f"Inspecting {input_path.name}...")
        media = probe_video(input_path, toolchain)

        print("Reading frame timestamps...")
        times: list[float] | None
        try:
            times = frame_times(input_path, toolchain)
        except Zero2HundredError as exc:
            print(f"Warning: could not read frame timestamps: {exc}", file=sys.stderr)
            times = None

        print("\nVideo")
        print(f"  File        {input_path.name}")
        print(f"  Resolution  {media.width} x {media.height}")
        print(f"  Duration    {format_timecode(media.duration)}")
        print(f"  Frame rate  {media.frame_rate:.3f} fps")
        print(f"  Frames      {len(times) if times is not None else 'unavailable'}")

        picker_marks: tuple[float, float] | None = None
        if args.pick:
            if times is None:
                print(
                    "Warning: frame picker unavailable without frame timestamps.",
                    file=sys.stderr,
                )
            else:
                picker_marks = _pick_frames(input_path, toolchain, times)

        if args.start is not None or picker_marks is None:
            launch = _time_value(args.start, "Launch timestamp")
        else:
            launch = picker_marks[0]
        if args.end is not None or picker_marks is None:
            reached_100 = _time_value(args.end, "100 km/h timestamp")
        else:
            reached_100 = picker_marks[1]
        events = EventWindow(launch=launch, reached_100=reached_100).validate(media.duration)

        clip_end: float | None = None
        adjustments: list[tuple[str, float, float]] = []
        if times is not None:
            snapped_launch = snap_to_frame(times, events.launch)
            snapped_reached_100 = snap_to_frame(times, events.reached_100)
            if abs(snapped_launch - events.launch) > 0.0005:
                adjustments.append(("Launch", events.launch, snapped_launch))
            if abs(snapped_reached_100 - events.reached_100) > 0.0005:
                adjustments.append(
                    ("100 km/h", events.reached_100, snapped_reached_100)
                )
            events = EventWindow(
                launch=snapped_launch, reached_100=snapped_reached_100
            ).validate(media.duration)
            clip_end = frame_after(times, events.reached_100)

        preferred_output = args.output or default_output_path(input_path)
        output = preferred_output if args.overwrite or args.output else available_output_path(preferred_output)
        job = RenderJob(
            media=media,
            events=events,
            output=output,
            settings=settings,
            toolchain=toolchain,
            trim_intro=args.trim_intro,
            overwrite=args.overwrite,
            clip_end=clip_end,
        )

        if adjustments:
            print("\nAdjusted to exact frames")
            for label, original, snapped in adjustments:
                print(
                    f"  {label:<10}  {format_timecode(original)} -> "
                    f"{format_timecode(snapped)}"
                )

        print("\nResult")
        print(f"  Launch      {format_timecode(events.launch)}")
        print(f"  100 km/h    {format_timecode(events.reached_100)}")
        print(f"  Time        {events.elapsed:.3f} seconds")
        ending = (
            "Continue after freeze"
            if settings.continue_after_freeze
            else "End after freeze"
        )
        print(f"  Ending      {ending}")
        print(f"  Output      {output}")

        if args.dry_run:
            print("\nFFmpeg command")
            print(f"  {subprocess.list2cmdline(job.command())}")
            return 0

        print(f"\nExporting {output.name}...")
        reporter = _ProgressReporter()
        job.run(reporter)
        reporter.finish()
        print(f"Done: {output}")
        return 0
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (Zero2HundredError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


def _render_settings(args: argparse.Namespace) -> RenderSettings:
    settings = load_settings(args.config)
    overrides = {}
    if args.freeze is not None:
        overrides["freeze_duration"] = args.freeze
    if args.position is not None:
        overrides["position"] = args.position
    if args.font is not None:
        overrides["font"] = args.font
    if args.font_file is not None:
        overrides["font_file"] = args.font_file
    if args.overlay_style is not None:
        overrides["overlay_style"] = args.overlay_style
    if args.timer_format is not None:
        overrides["timer_format"] = args.timer_format
    if args.overlay_scale is not None:
        overrides["overlay_scale"] = args.overlay_scale
    if args.continue_after_freeze is not None:
        overrides["continue_after_freeze"] = args.continue_after_freeze
    return replace(settings, **overrides).validated()


def _input_path(argument: str | None) -> Path:
    raw = argument
    if raw is None:
        raw = input("Drop a video here and press Enter:\n> ")
    path = parse_dropped_path(raw).resolve()
    return path


def _time_value(argument: str | None, label: str) -> float:
    raw = argument
    while raw is None:
        raw = input(f"{label} (seconds or timecode): ")
        try:
            return parse_timecode(raw)
        except ValueError as exc:
            print(f"Invalid time: {exc}")
            raw = None
    return parse_timecode(raw)


def _pick_frames(
    input_path: Path, toolchain: Toolchain, times: list[float]
) -> tuple[float, float] | None:
    print("\nPreparing frame picker...")
    print("Waiting for launch and 100 km/h marks in the browser...")
    try:
        with tempfile.TemporaryDirectory(prefix="zero2hundred_pick_") as tempdir:
            result = serve_picker(input_path, toolchain, times, Path(tempdir))
        print("Marks received.")
        return result
    except (Zero2HundredError, OSError) as exc:
        print(f"Warning: frame picker unavailable: {exc}", file=sys.stderr)
        return None


class _ProgressReporter:
    def __init__(self) -> None:
        self._last_percent = -1

    def __call__(self, progress: float) -> None:
        percent = int(progress * 100)
        if percent == self._last_percent:
            return
        self._last_percent = percent
        print(f"\r  Progress    {percent:3d}%", end="", flush=True)

    def finish(self) -> None:
        if self._last_percent >= 0:
            print()


if __name__ == "__main__":
    raise SystemExit(main())
