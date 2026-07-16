import unittest

from zero2hundred.errors import MediaError
from zero2hundred.events import EventWindow


class EventWindowTests(unittest.TestCase):
    def test_elapsed(self) -> None:
        events = EventWindow(launch=4.267, reached_100=10.833)
        self.assertAlmostEqual(events.elapsed, 6.566)

    def test_end_must_follow_launch(self) -> None:
        with self.assertRaises(MediaError):
            EventWindow(launch=5, reached_100=5).validate(10)

    def test_end_must_be_inside_video(self) -> None:
        with self.assertRaises(MediaError):
            EventWindow(launch=2, reached_100=10).validate(10)


if __name__ == "__main__":
    unittest.main()

