from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile

from zero2hundred import __version__
from zero2hundred.config import POSITIONS, load_settings
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
    )
    parser.add_argument("input", nargs="?", help="input video path")
    parser.add_argument("--start", metavar="TIME", help="launch timestamp")
    parser.add_argument("--end", metavar="TIME", help="100 km/h timestamp")
    parser.add_argument("-o", "--output", type=Path, help="output MP4 path")
    parser.add_argument("--freeze", type=float, help="freeze duration in seconds")
    parser.add_argument("--position", choices=POSITIONS, help="timer position")
    parser.add_argument("--font", help="timer font family")
    parser.add_argument("--font-file", help="path to a timer font file")
    parser.add_argument("--trim-intro", action="store_true", help="start output at launch")
    parser.add_argument(
        "--pick",
        action="store_true",
        help="open a frame picker in the browser to find exact times",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace existing output")
    parser.add_argument("--config", type=Path, help="TOML configuration file")
    parser.add_argument("--dry-run", action="store_true", help="print FFmpeg command")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        input_path = _input_path(args.input)
        toolchain = find_toolchain()
        print(f"Inspecting {input_path.name}...")
        media = probe_video(input_path, toolchain)
        print(
            f"Video: {media.width}x{media.height}, {media.frame_rate:.3f} fps, "
            f"{format_timecode(media.duration)}"
        )

        print("Reading frame timestamps...")
        times: list[float] | None
        try:
            times = frame_times(input_path, toolchain)
        except Zero2HundredError as exc:
            print(f"Warning: could not read frame timestamps: {exc}", file=sys.stderr)
            times = None

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
        if times is not None:
            snapped_launch = snap_to_frame(times, events.launch)
            snapped_reached_100 = snap_to_frame(times, events.reached_100)
            if abs(snapped_launch - events.launch) > 0.0005:
                print(
                    "Snapped launch to the nearest frame: "
                    f"{format_timecode(events.launch)} -> {format_timecode(snapped_launch)}"
                )
            if abs(snapped_reached_100 - events.reached_100) > 0.0005:
                print(
                    "Snapped 100 km/h to the nearest frame: "
                    f"{format_timecode(events.reached_100)} -> {format_timecode(snapped_reached_100)}"
                )
            events = EventWindow(
                launch=snapped_launch, reached_100=snapped_reached_100
            ).validate(media.duration)
            clip_end = frame_after(times, events.reached_100)

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
        settings = replace(settings, **overrides).validated()

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

        print(f"Launch:  {format_timecode(events.launch)}")
        print(f"100 km/h: {format_timecode(events.reached_100)}")
        print(f"0-100:   {events.elapsed:.3f} seconds")

        if args.dry_run:
            print(subprocess.list2cmdline(job.command()))
            return 0

        print(f"Exporting {output.name}...")
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
    print(
        "Pick the frames in your browser. "
        "The run continues when you press Finish."
    )
    try:
        with tempfile.TemporaryDirectory(prefix="zero2hundred_pick_") as tempdir:
            return serve_picker(input_path, toolchain, times, Path(tempdir))
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
        print(f"\rProgress: {percent:3d}%", end="", flush=True)

    def finish(self) -> None:
        if self._last_percent >= 0:
            print()


if __name__ == "__main__":
    raise SystemExit(main())
