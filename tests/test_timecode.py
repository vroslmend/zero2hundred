import unittest

from zero2hundred.timecode import format_timecode, parse_timecode


class TimecodeTests(unittest.TestCase):
    def test_seconds(self) -> None:
        self.assertAlmostEqual(parse_timecode("4.267"), 4.267)

    def test_minutes(self) -> None:
        self.assertAlmostEqual(parse_timecode("01:04.267"), 64.267)

    def test_hours(self) -> None:
        self.assertAlmostEqual(parse_timecode("01:02:03.5"), 3723.5)

    def test_rejects_invalid_component(self) -> None:
        with self.assertRaises(ValueError):
            parse_timecode("1:75")

    def test_formats(self) -> None:
        self.assertEqual(format_timecode(64.267), "01:04.267")


if __name__ == "__main__":
    unittest.main()
