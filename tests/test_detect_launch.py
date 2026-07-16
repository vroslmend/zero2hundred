import unittest

from zero2hundred import detect
from zero2hundred.detect.launch import (
    _baseline_threshold,
    _find_launch_run,
    _moving_median,
)


HAS_CV = detect.available()


class LaunchMathTests(unittest.TestCase):
    def test_baseline_uses_quiet_half_of_first_quarter(self) -> None:
        values = [1.0, 1.1, 0.9, 5.0, 6.0] + [8.0] * 15

        baseline, mad, threshold = _baseline_threshold(
            values,
            fraction=0.25,
            k=4.0,
            minimum_mad=0.05,
        )

        self.assertAlmostEqual(baseline, 1.0)
        self.assertAlmostEqual(mad, 0.1)
        self.assertAlmostEqual(threshold, 1.4)

    def test_moving_median_removes_a_single_sample_spike(self) -> None:
        values = [1.0, 1.0, 20.0, 1.0, 1.0]

        smoothed = _moving_median(values, width=3)

        self.assertEqual(smoothed, [1.0, 1.0, 1.0, 1.0, 1.0])

    def test_launch_follows_the_longest_quiet_interval(self) -> None:
        times = [index * 0.25 for index in range(22)]
        energies = (
            [1.0] * 4
            + [5.0] * 4
            + [1.0] * 8
            + [5.0] * 6
        )

        start, end = _find_launch_run(
            times,
            energies,
            threshold=2.0,
            sustain_seconds=0.5,
        )

        self.assertEqual((start, end), (16, 21))

    def test_launch_run_requires_the_full_sustain_duration(self) -> None:
        times = [0.0, 0.25, 0.5, 0.75]
        energies = [1.0, 5.0, 5.0, 1.0]

        self.assertIsNone(
            _find_launch_run(
                times,
                energies,
                threshold=2.0,
                sustain_seconds=0.5,
            )
        )

    def test_moving_median_rejects_an_even_width(self) -> None:
        with self.assertRaisesRegex(ValueError, "positive odd number"):
            _moving_median([1.0, 2.0], width=2)


@unittest.skipUnless(HAS_CV, "OpenCV detection extra is not installed")
class MotionEnergyTests(unittest.TestCase):
    def test_motion_energy_is_mean_absolute_pixel_difference(self) -> None:
        from zero2hundred.detect import _np as np
        from zero2hundred.detect.launch import _motion_energy

        previous = np.asarray([[0, 10], [20, 30]], dtype=np.uint8)
        current = np.asarray([[10, 10], [10, 50]], dtype=np.uint8)

        self.assertEqual(_motion_energy(previous, current), 10.0)


if __name__ == "__main__":
    unittest.main()
