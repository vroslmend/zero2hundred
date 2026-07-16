from __future__ import annotations

import argparse
from bisect import bisect_left
from collections.abc import Callable, Sequence
import csv
from dataclasses import dataclass, replace
import math
from pathlib import Path

from zero2hundred.errors import Zero2HundredError
from zero2hundred.frames import frame_times
from zero2hundred.media import MediaInfo, find_toolchain, probe_video


Suggestion = tuple[float, float]
Detector = Callable[[Path, MediaInfo, list[float]], Suggestion]
DETECTORS: dict[str, Detector] = {}
REQUIRED_COLUMNS = ("file", "launch", "hundred", "notes")


@dataclass(frozen=True, slots=True)
class GroundTruth:
    file: str
    launch: float | None
    hundred: float | None
    notes: str


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    file: str
    truth: float
    suggested: float
    error_s: float
    confidence: float
    hit: bool


def load_ground_truth(path: Path) -> list[GroundTruth]:
    """Load labeled and reference-only rows from a ground-truth CSV."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = set(reader.fieldnames or ())
        missing = [column for column in REQUIRED_COLUMNS if column not in columns]
        if missing:
            raise ValueError(f"ground truth is missing columns: {', '.join(missing)}")

        rows: list[GroundTruth] = []
        for line_number, values in enumerate(reader, start=2):
            file = (values.get("file") or "").strip()
            if not file:
                raise ValueError(f"ground truth line {line_number} has no file")
            rows.append(
                GroundTruth(
                    file=file,
                    launch=_optional_mark(values.get("launch"), "launch", file),
                    hundred=_optional_mark(values.get("hundred"), "hundred", file),
                    notes=(values.get("notes") or "").strip(),
                )
            )
    return rows


def score_suggestion(
    file: str,
    truth: float,
    suggestion: Suggestion,
    *,
    tolerance_s: float,
) -> EvaluationResult:
    """Score one suggestion using an inclusive tolerance in seconds."""
    suggested, confidence = map(float, suggestion)
    if not all(math.isfinite(value) for value in (truth, suggested, confidence)):
        raise ValueError("truth, suggestion, and confidence must be finite")
    if tolerance_s < 0 or not math.isfinite(tolerance_s):
        raise ValueError("tolerance_s must be a finite non-negative number")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")

    error_s = round(abs(suggested - truth), 12)
    hit = error_s <= tolerance_s or math.isclose(error_s, tolerance_s)
    return EvaluationResult(
        file=file,
        truth=float(truth),
        suggested=suggested,
        error_s=error_s,
        confidence=confidence,
        hit=hit,
    )


def frame_distance(times: Sequence[float], truth: float, suggested: float) -> int:
    """Return the distance between marks in nearest presentation-frame indices."""
    if not times:
        raise ValueError("times cannot be empty")
    return abs(_nearest_index(times, truth) - _nearest_index(times, suggested))


def score_frame_suggestion(
    file: str,
    truth: float,
    suggestion: Suggestion,
    times: Sequence[float],
    *,
    tolerance_frames: int,
) -> EvaluationResult:
    """Score one suggestion using an inclusive presentation-frame tolerance."""
    if tolerance_frames < 0:
        raise ValueError("tolerance_frames must be non-negative")
    result = score_suggestion(
        file,
        truth,
        suggestion,
        tolerance_s=0.0,
    )
    return replace(
        result,
        hit=frame_distance(times, truth, suggestion[0]) <= tolerance_frames,
    )


def hit_rate(results: Sequence[EvaluationResult]) -> tuple[float, int, int]:
    """Return rate, hit count, and evaluated count."""
    total = len(results)
    hits = sum(result.hit for result in results)
    return (hits / total if total else 0.0, hits, total)


def register_detector(name: str, detector: Detector) -> None:
    if name not in ("launch", "needle"):
        raise ValueError(f"unknown detector: {name}")
    DETECTORS[name] = detector


def run_evaluation(
    csv_path: Path,
    detector_name: str,
    detector: Detector,
) -> list[EvaluationResult]:
    """Run a registered detector against every labeled row."""
    if detector_name not in ("launch", "needle"):
        raise ValueError(f"unknown detector: {detector_name}")
    truth_field = "launch" if detector_name == "launch" else "hundred"
    toolchain = find_toolchain()
    results: list[EvaluationResult] = []
    for row in load_ground_truth(csv_path):
        truth = getattr(row, truth_field)
        if truth is None:
            continue
        video_path = csv_path.parent / row.file
        media = probe_video(video_path, toolchain)
        times = frame_times(video_path, toolchain)
        suggestion = detector(video_path, media, times)
        if detector_name == "launch":
            result = score_suggestion(
                row.file,
                truth,
                suggestion,
                tolerance_s=0.3,
            )
        else:
            result = score_frame_suggestion(
                row.file,
                truth,
                suggestion,
                times,
                tolerance_frames=2,
            )
        results.append(result)
    return results


def render_report(results: Sequence[EvaluationResult], detector_name: str) -> str:
    if detector_name not in ("launch", "needle"):
        raise ValueError(f"unknown detector: {detector_name}")
    lines = ["file | truth | suggested | error_s | confidence"]
    for result in results:
        lines.append(
            f"{result.file} | {result.truth:.3f} | {result.suggested:.3f} | "
            f"{result.error_s:.3f} | {result.confidence:.3f}"
        )
    rate, hits, total = hit_rate(results)
    threshold = "0.3s" if detector_name == "launch" else "2 frames"
    lines.append(f"Hit rate @{threshold}: {rate:.1%} ({hits}/{total})")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate automatic suggestions against labeled video frames."
    )
    parser.add_argument("ground_truth", type=Path, help="ground-truth CSV path")
    parser.add_argument(
        "--detector",
        required=True,
        choices=("launch", "needle"),
        help="detector to evaluate",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    detector = DETECTORS.get(args.detector)
    if detector is None:
        print(f"Detector not implemented yet: {args.detector}")
        return 0
    try:
        results = run_evaluation(args.ground_truth, args.detector, detector)
    except (Zero2HundredError, OSError, ValueError) as exc:
        print(f"Evaluation could not run: {exc}")
        return 0
    print(render_report(results, args.detector))
    return 0


def _optional_mark(value: str | None, label: str, file: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        mark = float(text)
    except ValueError as exc:
        raise ValueError(f"invalid {label} for {file}: {text}") from exc
    if not math.isfinite(mark) or mark < 0:
        raise ValueError(f"invalid {label} for {file}: {text}")
    return mark


def _nearest_index(times: Sequence[float], value: float) -> int:
    index = bisect_left(times, value)
    if index == 0:
        return 0
    if index == len(times):
        return len(times) - 1
    before = times[index - 1]
    after = times[index]
    return index if (after - value) < (value - before) else index - 1


if __name__ == "__main__":
    raise SystemExit(main())
