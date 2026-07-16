import unittest

from zero2hundred.frames import _parse_pts_lines, frame_after, snap_to_frame


class SnapToFrameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.times = [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_below_first_snaps_to_first(self) -> None:
        self.assertEqual(snap_to_frame(self.times, -5.0), 0.0)

    def test_above_last_snaps_to_last(self) -> None:
        self.assertEqual(snap_to_frame(self.times, 99.0), 4.0)

    def test_midpoint_tie_prefers_earlier_frame(self) -> None:
        self.assertEqual(snap_to_frame(self.times, 1.5), 1.0)

    def test_exact_hit_returns_same_value(self) -> None:
        self.assertEqual(snap_to_frame(self.times, 2.0), 2.0)

    def test_nearest_below_midpoint(self) -> None:
        self.assertEqual(snap_to_frame(self.times, 1.4), 1.0)

    def test_nearest_above_midpoint(self) -> None:
        self.assertEqual(snap_to_frame(self.times, 1.6), 2.0)

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            snap_to_frame([], 1.0)


class FrameAfterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.times = [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_mid_value_returns_next_frame(self) -> None:
        self.assertEqual(frame_after(self.times, 1.5), 2.0)

    def test_at_last_returns_none(self) -> None:
        self.assertIsNone(frame_after(self.times, 4.0))

    def test_before_first_returns_first(self) -> None:
        self.assertEqual(frame_after(self.times, -1.0), 0.0)

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(frame_after([], 1.0))


class ParsePtsLinesTests(unittest.TestCase):
    def test_sorts_and_skips_noise(self) -> None:
        text = "\n".join(
            [
                "2.500000",
                "N/A",
                "",
                "0.000000",
                "side_data",
                "1.250000",
            ]
        )
        self.assertEqual(_parse_pts_lines(text), [0.0, 1.25, 2.5])

    def test_empty_text_returns_empty_list(self) -> None:
        self.assertEqual(_parse_pts_lines(""), [])


if __name__ == "__main__":
    unittest.main()
