"""Experimental semi-automatic tracking of the 100 km/h needle crossing.

The tracking constants below were tuned against a small private set of
labeled runs and have not been validated more widely. Suggestions from
find_hundred pre-fill the picker for the user to confirm; they are never
rendered directly.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from statistics import median
from collections.abc import Sequence

from zero2hundred.detect import _cv2 as cv2
from zero2hundred.detect import _np as np
from zero2hundred.detect import require
from zero2hundred.detect.video import iter_frames
from zero2hundred.errors import MediaError
from zero2hundred.media import MediaInfo


Point = tuple[float, float]
# Sampling at native resolution (2160) was measured on the labeled runs and
# changed nothing on the failing video while costing 4x the runtime, so the
# bottleneck is dial-feature confusion, not resolution.
MAX_HEIGHT = 960
MINIMUM_FEATURE_MATCHES = 8
MINIMUM_HALF_SIZE = 60
REFERENCE_RADIUS_MULTIPLIER = 2.0
SEARCH_MARGIN_FRACTION = 0.14
SPEED_MINIMUM = -10.0
SPEED_MAXIMUM = 140.0
SPEED_STEP = 0.5
RADIAL_SAMPLE_COUNT = 75
SMOOTHING_WIDTH = 5
INITIAL_MAXIMUM_SPEED = 20.0
MAXIMUM_ACCELERATION = 80.0
MAXIMUM_DECELERATION = 30.0
TRANSITION_PENALTY = 0.04
ENDPOINT_SPEED = 99.0
ENDPOINT_GATE_SPEED = 90.0


@dataclass(frozen=True, slots=True)
class Calibration:
    pivot: Point
    zero: Point
    hundred: Point
    frame: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "pivot", _validated_point(self.pivot, "pivot"))
        object.__setattr__(self, "zero", _validated_point(self.zero, "zero"))
        object.__setattr__(
            self,
            "hundred",
            _validated_point(self.hundred, "hundred"),
        )
        frame = _finite_number(self.frame, "frame")
        if frame < 0:
            raise ValueError("frame must be non-negative")
        object.__setattr__(self, "frame", frame)

        zero_angle = _point_angle(self.pivot, self.zero, "zero")
        hundred_angle = _point_angle(self.pivot, self.hundred, "hundred")
        if math.isclose(_angle_delta(zero_angle, hundred_angle), 0.0, abs_tol=1e-9):
            raise ValueError("zero and hundred must have different angles")

    @classmethod
    def from_json(cls, value: str) -> "Calibration":
        try:
            data = json.loads(value)
            return cls(
                pivot=data["pivot"],
                zero=data["zero"],
                hundred=data["hundred"],
                frame=data["frame"],
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid calibration JSON") from exc


def angle_to_speed(calibration: Calibration, needle: Point) -> float:
    """Map a needle point to speed along the calibrated zero-to-hundred arc."""
    needle = _validated_point(needle, "needle")
    zero_angle = _point_angle(calibration.pivot, calibration.zero, "zero")
    hundred_angle = _point_angle(
        calibration.pivot,
        calibration.hundred,
        "hundred",
    )
    needle_angle = _point_angle(calibration.pivot, needle, "needle")
    calibrated_sweep = _angle_delta(zero_angle, hundred_angle)
    needle_sweep = _angle_delta(zero_angle, needle_angle)
    return 100.0 * needle_sweep / calibrated_sweep


def find_hundred(
    path: Path,
    media: MediaInfo,
    times: list[float],
    calibration: Calibration,
    search_start_pts: float,
) -> tuple[float, float]:
    """Suggest the PTS where a calibrated speedometer needle reaches 100."""
    require()
    if not times:
        raise MediaError(f"no frame timestamps available for {path.name}")
    if not math.isfinite(search_start_pts) or search_start_pts < 0:
        raise ValueError("search_start_pts must be finite and non-negative")

    reference_index = _nearest_index(times, calibration.frame)
    reference = _read_frame(path, media, reference_index)
    tracker = _GaugeTracker(reference, calibration)

    sample_times: list[float] = []
    score_rows: list["np.ndarray"] = []
    registration_scores: list[float] = []
    needle_scores: list[float] = []
    for frame_index, gray in iter_frames(
        path,
        media,
        max_height=MAX_HEIGHT,
    ):
        if frame_index >= len(times):
            raise MediaError(f"frame timestamps ended early for {path.name}")
        if times[frame_index] < search_start_pts:
            continue
        geometry, registration_score = tracker.locate(gray)
        registration_scores.append(registration_score)
        if registration_score == 0.0:
            # An unregistered frame would be measured with stale geometry.
            # A gap in the series costs less than feeding the temporal path
            # a needle angle taken from the wrong place.
            continue
        scores, needle_score = _needle_scores(gray, geometry)
        sample_times.append(times[frame_index])
        score_rows.append(scores)
        needle_scores.append(needle_score)

    if not registration_scores:
        raise MediaError(f"no video frames remain after the search start in {path.name}")
    if not score_rows:
        raise MediaError(f"could not register the gauge in {path.name}")
    speed_axis = _speed_axis()
    tracked = _moving_median(
        _trace_speed_path(sample_times, score_rows, speed_axis),
        width=SMOOTHING_WIDTH,
    )
    independent = _moving_median(
        [float(speed_axis[int(np.argmax(row))]) for row in score_rows],
        width=SMOOTHING_WIDTH,
    )
    crossing = _gated_crossing_time(sample_times, independent, tracked)
    if crossing is None:
        raise MediaError(f"could not find the 100 km/h crossing in {path.name}")

    registration_confidence = median(registration_scores)
    needle_confidence = median(needle_scores)
    confidence = max(
        0.0,
        min(1.0, 0.55 * registration_confidence + 0.45 * needle_confidence),
    )
    return crossing, confidence


@dataclass(frozen=True, slots=True)
class _GaugeGeometry:
    pivot: "np.ndarray"
    zero: "np.ndarray"
    hundred: "np.ndarray"


class _GaugeTracker:
    def __init__(self, reference: "np.ndarray", calibration: Calibration) -> None:
        height, width = reference.shape
        self._height = height
        self._width = width
        self._pivot = _pixel_point(calibration.pivot, width, height)
        self._zero = _pixel_point(calibration.zero, width, height)
        self._hundred = _pixel_point(calibration.hundred, width, height)
        radius = float(np.linalg.norm(self._hundred - self._pivot))
        self._half_size = max(
            MINIMUM_HALF_SIZE,
            round(REFERENCE_RADIUS_MULTIPLIER * radius),
        )
        self._margin = round(SEARCH_MARGIN_FRACTION * height)
        self._orb = cv2.ORB_create(nfeatures=1200, fastThreshold=5)
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        self._matrix = np.eye(3, dtype=np.float64)

        x0, y0, x1, y1 = self._bounds(self._pivot, self._half_size)
        keypoints, descriptors = self._orb.detectAndCompute(
            reference[y0:y1, x0:x1],
            None,
        )
        if descriptors is None or len(keypoints) < MINIMUM_FEATURE_MATCHES:
            raise MediaError("could not find enough gauge detail in the calibration frame")
        self._descriptors = descriptors
        self._source_points = np.float32(
            [(point.pt[0] + x0, point.pt[1] + y0) for point in keypoints]
        )

    def locate(self, gray: "np.ndarray") -> tuple[_GaugeGeometry, float]:
        prior_pivot = _transform_point(self._matrix, self._pivot)
        extent = self._half_size + self._margin
        x0, y0, x1, y1 = self._bounds(prior_pivot, extent)
        keypoints, descriptors = self._orb.detectAndCompute(
            gray[y0:y1, x0:x1],
            None,
        )
        score = 0.0
        if descriptors is not None and len(keypoints) >= 3:
            target_points = np.float32(
                [(point.pt[0] + x0, point.pt[1] + y0) for point in keypoints]
            )
            matches = []
            for pair in self._matcher.knnMatch(
                self._descriptors,
                descriptors,
                k=2,
            ):
                if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance:
                    matches.append(pair[0])
            if len(matches) >= MINIMUM_FEATURE_MATCHES:
                source = np.float32(
                    [self._source_points[match.queryIdx] for match in matches]
                )
                target = np.float32(
                    [target_points[match.trainIdx] for match in matches]
                )
                # A similarity transform was measured against homography on
                # the labeled runs: it made one moving-dashboard video worse
                # and helped nothing, so the perspective terms earn their
                # keep here.
                matrix, inliers = cv2.findHomography(
                    source,
                    target,
                    cv2.RANSAC,
                    3.0,
                )
                if matrix is not None and inliers is not None:
                    inlier_count = int(inliers.sum())
                    candidate_pivot = _transform_point(matrix, self._pivot)
                    candidate_hundred = _transform_point(matrix, self._hundred)
                    original_radius = float(
                        np.linalg.norm(self._hundred - self._pivot)
                    )
                    scale = float(
                        np.linalg.norm(candidate_hundred - candidate_pivot)
                    ) / original_radius
                    if (
                        inlier_count >= 6
                        and 0.75 < scale < 1.3
                        and float(np.linalg.norm(candidate_pivot - self._pivot))
                        < 0.3 * self._height
                    ):
                        self._matrix = matrix
                        score = min(1.0, inlier_count / max(12, len(matches)))

        return (
            _GaugeGeometry(
                pivot=_transform_point(self._matrix, self._pivot),
                zero=_transform_point(self._matrix, self._zero),
                hundred=_transform_point(self._matrix, self._hundred),
            ),
            score,
        )

    def _bounds(self, center: "np.ndarray", extent: int) -> tuple[int, int, int, int]:
        x, y = np.rint(center).astype(int)
        return (
            max(0, x - extent),
            max(0, y - extent),
            min(self._width, x + extent),
            min(self._height, y + extent),
        )


def _needle_scores(
    gray: "np.ndarray",
    geometry: _GaugeGeometry,
) -> tuple["np.ndarray", float]:
    zero_angle = _array_angle(geometry.pivot, geometry.zero)
    hundred_angle = _array_angle(geometry.pivot, geometry.hundred)
    sweep = _angle_delta(zero_angle, hundred_angle)
    radius = float(np.linalg.norm(geometry.hundred - geometry.pivot))
    # The pivot cap and outer tick labels are both brighter than some needles.
    # The middle needle body is the stable radial feature across the sample dials.
    radii = np.linspace(0.30 * radius, 0.95 * radius, RADIAL_SAMPLE_COUNT)
    speeds = _speed_axis()
    angles = zero_angle + sweep * speeds / 100.0
    side_offset = abs(sweep) * 5.0 / 100.0
    center = _sample_rays(gray, geometry.pivot, angles, radii)
    left = _sample_rays(gray, geometry.pivot, angles - side_offset, radii)
    right = _sample_rays(gray, geometry.pivot, angles + side_offset, radii)
    scores = np.mean(center - (left + right) / 2.0, axis=1)
    best_index = int(np.argmax(scores))
    spread = float(np.std(scores))
    signal = float(scores[best_index] - np.median(scores))
    confidence = 1.0 - math.exp(-max(0.0, signal) / max(1.0, spread))
    return scores, confidence


def _speed_axis() -> "np.ndarray":
    return np.arange(
        SPEED_MINIMUM,
        SPEED_MAXIMUM + SPEED_STEP / 2,
        SPEED_STEP,
    )


def _trace_speed_path(
    times: Sequence[float],
    score_rows: Sequence["np.ndarray"],
    speeds: "np.ndarray",
) -> list[float]:
    if len(times) != len(score_rows):
        raise ValueError("needle times and score rows must have equal lengths")
    if not score_rows:
        return []
    state_count = len(speeds)
    normalized = []
    for row in score_rows:
        if len(row) != state_count:
            raise ValueError("needle score rows must match the speed axis")
        scale = max(1.0, float(np.std(row)))
        normalized.append((row - np.median(row)) / scale)

    previous = np.full(state_count, -np.inf, dtype=np.float64)
    initial = speeds <= INITIAL_MAXIMUM_SPEED
    previous[initial] = normalized[0][initial] - 0.08 * np.abs(speeds[initial])
    backpointers = np.zeros((len(score_rows), state_count), dtype=np.int16)

    for frame_index in range(1, len(score_rows)):
        elapsed = max(0.0, float(times[frame_index] - times[frame_index - 1]))
        maximum_increase = max(SPEED_STEP, MAXIMUM_ACCELERATION * elapsed)
        maximum_decrease = max(SPEED_STEP, MAXIMUM_DECELERATION * elapsed)
        current = np.full(state_count, -np.inf, dtype=np.float64)
        for state, speed in enumerate(speeds):
            allowed = np.flatnonzero(
                (speeds >= speed - maximum_increase)
                & (speeds <= speed + maximum_decrease)
            )
            transitions = previous[allowed] - TRANSITION_PENALTY * np.abs(
                speed - speeds[allowed]
            )
            best_offset = int(np.argmax(transitions))
            best_previous = int(allowed[best_offset])
            current[state] = normalized[frame_index][state] + transitions[best_offset]
            backpointers[frame_index, state] = best_previous
        previous = current

    state = int(np.argmax(previous))
    path = [0.0] * len(score_rows)
    for frame_index in range(len(score_rows) - 1, -1, -1):
        path[frame_index] = float(speeds[state])
        if frame_index:
            state = int(backpointers[frame_index, state])
    return path


def _sample_rays(
    gray: "np.ndarray",
    pivot: "np.ndarray",
    angles: "np.ndarray",
    radii: "np.ndarray",
) -> "np.ndarray":
    x_map = (
        pivot[0] + np.cos(angles)[:, np.newaxis] * radii[np.newaxis, :]
    ).astype(np.float32)
    y_map = (
        pivot[1] + np.sin(angles)[:, np.newaxis] * radii[np.newaxis, :]
    ).astype(np.float32)
    return cv2.remap(
        gray,
        x_map,
        y_map,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    ).astype(np.float32)


def _read_frame(
    path: Path,
    media: MediaInfo,
    target_index: int,
) -> "np.ndarray":
    for frame_index, gray in iter_frames(path, media, max_height=MAX_HEIGHT):
        if frame_index == target_index:
            return gray
    raise MediaError(f"could not read calibration frame from {path.name}")


def _crossing_time(times: Sequence[float], speeds: Sequence[float]) -> float | None:
    if len(times) != len(speeds):
        raise ValueError("times and speeds must have equal lengths")
    if not times:
        return None
    if speeds[0] >= 100:
        return float(times[0])
    for index in range(1, len(speeds)):
        before = float(speeds[index - 1])
        after = float(speeds[index])
        if before < 100 <= after:
            fraction = (100 - before) / (after - before)
            return float(times[index - 1]) + fraction * (
                float(times[index]) - float(times[index - 1])
            )
    return None


def _gated_crossing_time(
    times: Sequence[float],
    independent: Sequence[float],
    tracked: Sequence[float],
) -> float | None:
    if not (len(times) == len(independent) == len(tracked)):
        raise ValueError("needle crossing series must have equal lengths")
    for index, (measured, progress) in enumerate(zip(independent, tracked)):
        if measured >= ENDPOINT_SPEED and progress >= ENDPOINT_GATE_SPEED:
            if index == 0 or independent[index - 1] >= ENDPOINT_SPEED:
                return float(times[index])
            before = float(independent[index - 1])
            fraction = (ENDPOINT_SPEED - before) / (measured - before)
            return float(times[index - 1]) + fraction * (
                float(times[index]) - float(times[index - 1])
            )
    return None


def _validated_point(value: object, label: str) -> Point:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must contain two normalized coordinates")
    x = _finite_number(value[0], label)
    y = _finite_number(value[1], label)
    if not 0 <= x <= 1 or not 0 <= y <= 1:
        raise ValueError(f"{label} coordinates must be between 0 and 1")
    return x, y


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _point_angle(pivot: Point, point: Point, label: str) -> float:
    dx = point[0] - pivot[0]
    dy = point[1] - pivot[1]
    if math.isclose(dx, 0.0, abs_tol=1e-12) and math.isclose(
        dy, 0.0, abs_tol=1e-12
    ):
        raise ValueError(f"{label} must differ from the pivot")
    return math.atan2(dy, dx)


def _angle_delta(start: float, end: float) -> float:
    return (end - start + math.pi) % (2 * math.pi) - math.pi


def _pixel_point(point: Point, width: int, height: int) -> "np.ndarray":
    # OpenCV sampling puts integer coordinates at pixel centers, so the
    # exact conversion would subtract half a pixel. The tracking thresholds
    # below were tuned with this uncorrected form and a lone half-pixel
    # shift measurably moves crossings, so keep the convention until the
    # thresholds are re-derived.
    return np.array((point[0] * width, point[1] * height), dtype=np.float64)


def _transform_point(matrix: "np.ndarray", point: "np.ndarray") -> "np.ndarray":
    if matrix.shape == (2, 3):
        return matrix[:, :2] @ point + matrix[:, 2]
    homogeneous = matrix @ np.array((point[0], point[1], 1.0))
    return homogeneous[:2] / homogeneous[2]


def _array_angle(pivot: "np.ndarray", point: "np.ndarray") -> float:
    return math.atan2(float(point[1] - pivot[1]), float(point[0] - pivot[0]))


def _nearest_index(times: Sequence[float], value: float) -> int:
    return min(range(len(times)), key=lambda index: abs(times[index] - value))


def _moving_median(values: Sequence[float], *, width: int) -> list[float]:
    if width < 1 or width % 2 == 0:
        raise ValueError("moving median width must be a positive odd number")
    radius = width // 2
    return [
        float(median(values[max(0, index - radius) : index + radius + 1]))
        for index in range(len(values))
    ]
