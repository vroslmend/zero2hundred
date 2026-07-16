import json
import math
import unittest

from zero2hundred.detect import _np as np
from zero2hundred.detect import available
from zero2hundred.detect.needle import (
    Calibration,
    _crossing_time,
    _gated_crossing_time,
    _trace_speed_path,
    angle_to_speed,
)


class CalibrationTests(unittest.TestCase):
    def test_parses_normalized_calibration_json(self) -> None:
        calibration = Calibration.from_json(
            json.dumps(
                {
                    "pivot": [0.5, 0.6],
                    "zero": [0.2, 0.8],
                    "hundred": [0.8, 0.3],
                    "frame": 1.395,
                }
            )
        )

        self.assertEqual(calibration.pivot, (0.5, 0.6))
        self.assertEqual(calibration.zero, (0.2, 0.8))
        self.assertEqual(calibration.hundred, (0.8, 0.3))
        self.assertEqual(calibration.frame, 1.395)

    def test_rejects_points_outside_the_normalized_frame(self) -> None:
        value = json.dumps(
            {
                "pivot": [0.5, 0.5],
                "zero": [-0.1, 0.8],
                "hundred": [0.8, 0.2],
                "frame": 0.0,
            }
        )

        with self.assertRaisesRegex(ValueError, "zero.*between 0 and 1"):
            Calibration.from_json(value)

    def test_rejects_identical_zero_and_hundred_angles(self) -> None:
        value = json.dumps(
            {
                "pivot": [0.5, 0.5],
                "zero": [0.5, 0.8],
                "hundred": [0.5, 0.9],
                "frame": 0.0,
            }
        )

        with self.assertRaisesRegex(ValueError, "different angles"):
            Calibration.from_json(value)


class AngleMappingTests(unittest.TestCase):
    def test_maps_halfway_angle_to_fifty(self) -> None:
        calibration = Calibration(
            pivot=(0.5, 0.5),
            zero=(0.5, 0.9),
            hundred=(0.9, 0.5),
            frame=0.0,
        )

        speed = angle_to_speed(calibration, (0.8, 0.8))

        self.assertAlmostEqual(speed, 50.0)

    def test_mapping_handles_the_angle_wrap_at_pi(self) -> None:
        def point(degrees: float) -> tuple[float, float]:
            radians = math.radians(degrees)
            return 0.5 + 0.4 * math.cos(radians), 0.5 + 0.4 * math.sin(radians)

        calibration = Calibration(
            pivot=(0.5, 0.5),
            zero=point(170),
            hundred=point(-170),
            frame=0.0,
        )

        self.assertAlmostEqual(angle_to_speed(calibration, point(180)), 50.0)

    def test_mapping_can_report_a_value_above_one_hundred(self) -> None:
        calibration = Calibration(
            pivot=(0.5, 0.5),
            zero=(0.5, 0.9),
            hundred=(0.9, 0.5),
            frame=0.0,
        )

        self.assertGreater(angle_to_speed(calibration, (0.8, 0.2)), 100.0)


class CrossingTests(unittest.TestCase):
    def test_interpolates_the_crossing_between_frames(self) -> None:
        self.assertAlmostEqual(
            _crossing_time([1.0, 2.0, 3.0], [90.0, 98.0, 102.0]),
            2.5,
        )

    def test_returns_none_when_speed_never_reaches_one_hundred(self) -> None:
        self.assertIsNone(_crossing_time([1.0, 2.0], [80.0, 99.0]))

    def test_rejects_mismatched_series(self) -> None:
        with self.assertRaisesRegex(ValueError, "equal lengths"):
            _crossing_time([1.0], [90.0, 100.0])

    def test_endpoint_crossing_ignores_a_bright_mark_before_progress(self) -> None:
        crossing = _gated_crossing_time(
            [1.0, 2.0, 3.0, 4.0],
            [100.0, 40.0, 98.0, 100.0],
            [20.0, 40.0, 91.0, 95.0],
        )

        self.assertAlmostEqual(crossing, 3.5)


class NeedlePathTests(unittest.TestCase):
    @unittest.skipUnless(available(), "OpenCV and NumPy are not installed")
    def test_path_cannot_teleport_to_an_isolated_bright_mark(self) -> None:
        speeds = np.arange(0.0, 101.0, 0.5)
        rows = [np.zeros(len(speeds)) for _ in range(4)]
        rows[0][0] = 5.0
        rows[1][200] = 100.0
        rows[1][1] = 5.0
        rows[2][2] = 5.0
        rows[3][3] = 5.0

        path = _trace_speed_path([0.0, 0.01, 0.02, 0.03], rows, speeds)

        self.assertEqual(path, [0.0, 0.5, 1.0, 1.5])


if __name__ == "__main__":
    unittest.main()
