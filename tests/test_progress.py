import contextlib
import io
import unittest
from unittest import mock

from zero2hundred.errors import MediaError
from zero2hundred.progress import ProgressReporter, stream_ffmpeg_progress


def _fake_popen(lines, *, returncode=0, stderr_text=""):
    class _Popen:
        def __init__(self, command, **kwargs):
            if stderr_text:
                kwargs["stderr"].write(stderr_text)
            self.stdout = iter(lines)

        def wait(self):
            return returncode

    return _Popen


class ProgressReporterTests(unittest.TestCase):
    def test_formats_progress_as_an_indented_status_row(self) -> None:
        stdout = io.StringIO()
        reporter = ProgressReporter()

        with contextlib.redirect_stdout(stdout):
            reporter(0.427)
            reporter.finish()

        self.assertEqual(stdout.getvalue(), "\r  Progress     42%\n")

    def test_skips_repeated_percentages_and_clamps_range(self) -> None:
        stdout = io.StringIO()
        reporter = ProgressReporter()

        with contextlib.redirect_stdout(stdout):
            reporter(0.5)
            reporter(0.504)  # same whole percent, no reprint
            reporter(1.4)  # clamps to 100

        self.assertEqual(stdout.getvalue().count("\r"), 2)
        self.assertIn("100%", stdout.getvalue())


class StreamFfmpegProgressTests(unittest.TestCase):
    def test_reports_fractions_from_out_time(self) -> None:
        fractions: list[float] = []
        popen = _fake_popen(["out_time_us=5000000\n", "out_time_us=10000000\n"])

        with mock.patch("zero2hundred.progress.subprocess.Popen", popen):
            stream_ffmpeg_progress(["ffmpeg"], 10.0, fractions.append, error_prefix="x: ")

        self.assertEqual(fractions, [0.5, 1.0])

    def test_raises_prefixed_detail_on_failure(self) -> None:
        popen = _fake_popen([], returncode=1, stderr_text="boom")

        with mock.patch("zero2hundred.progress.subprocess.Popen", popen):
            with self.assertRaisesRegex(MediaError, "could not encode: boom"):
                stream_ffmpeg_progress(["ffmpeg"], 10.0, None, error_prefix="could not encode: ")

    def test_terminates_child_on_keyboard_interrupt(self) -> None:
        class _Popen:
            terminated = False

            def __init__(self, command, **kwargs):
                def lines():
                    raise KeyboardInterrupt
                    yield  # pragma: no cover

                self.stdout = lines()

            def wait(self):
                return 0

            def terminate(self):
                _Popen.terminated = True

        with mock.patch("zero2hundred.progress.subprocess.Popen", _Popen):
            with self.assertRaises(KeyboardInterrupt):
                stream_ffmpeg_progress(["ffmpeg"], 10.0, None, error_prefix="x: ")

        self.assertTrue(_Popen.terminated)


if __name__ == "__main__":
    unittest.main()
