import tempfile
from pathlib import Path
import unittest

from zero2hundred.paths import available_output_path, default_output_path, parse_dropped_path


class PathTests(unittest.TestCase):
    def test_parses_quoted_dragged_path(self) -> None:
        self.assertEqual(parse_dropped_path("'D:\\My Videos\\run.mp4'"), Path("D:/My Videos/run.mp4"))

    def test_parses_powershell_call_prefix(self) -> None:
        self.assertEqual(parse_dropped_path("& 'D:\\run.mp4'"), Path("D:/run.mp4"))

    def test_default_output(self) -> None:
        self.assertEqual(default_output_path(Path("run.mov")), Path("run_0-100.mp4"))

    def test_finds_available_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            preferred = Path(directory, "run_0-100.mp4")
            preferred.touch()
            self.assertEqual(
                available_output_path(preferred),
                Path(directory, "run_0-100_2.mp4"),
            )


if __name__ == "__main__":
    unittest.main()

