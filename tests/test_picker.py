import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from zero2hundred.media import Toolchain
from zero2hundred.picker import build_picker, thumbnail_indices, write_picker_html


class ThumbnailIndicesTests(unittest.TestCase):
    def test_count_under_limit_returns_every_index(self) -> None:
        self.assertEqual(thumbnail_indices(5, limit=10), [0, 1, 2, 3, 4])

    def test_count_equal_to_limit_returns_every_index(self) -> None:
        self.assertEqual(thumbnail_indices(10, limit=10), list(range(10)))

    def test_zero_count_returns_empty_list(self) -> None:
        self.assertEqual(thumbnail_indices(0, limit=10), [])

    def test_over_limit_uses_step_and_includes_zero(self) -> None:
        indices = thumbnail_indices(25, limit=10)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices, sorted(set(indices)))
        self.assertTrue(all(0 <= i < 25 for i in indices))
        # step = ceil(25/10) = 3 -> 0, 3, 6, ..., 24
        self.assertEqual(indices, [0, 3, 6, 9, 12, 15, 18, 21, 24])

    def test_over_limit_result_is_sorted_and_unique(self) -> None:
        indices = thumbnail_indices(4731, limit=1200)
        self.assertEqual(indices, sorted(set(indices)))
        self.assertLessEqual(len(indices), 1200)
        self.assertEqual(indices[0], 0)
        self.assertTrue(all(0 <= i < 4731 for i in indices))


class WritePickerHtmlTests(unittest.TestCase):
    def test_writes_html_with_times_paths_and_no_external_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            entries = [
                (0.0, "frames/000001.jpg"),
                (1.412, "frames/000002.jpg"),
                (12.375, "frames/000003.jpg"),
            ]
            html_path = write_picker_html(entries, workdir, "sample_video.mp4")

            self.assertTrue(html_path.exists())
            self.assertEqual(html_path, workdir / "picker.html")

            text = html_path.read_text(encoding="utf-8")
            self.assertIn("sample_video.mp4", text)
            self.assertIn("0.000", text)
            self.assertIn("1.412", text)
            self.assertIn("12.375", text)
            self.assertIn("frames/000001.jpg", text)
            self.assertIn("frames/000002.jpg", text)
            self.assertIn("frames/000003.jpg", text)
            self.assertNotIn("http://", text)
            self.assertNotIn("https://", text)


class BuildPickerMismatchWarningTests(unittest.TestCase):
    def test_warns_on_stderr_when_ffmpeg_produces_fewer_thumbnails_than_expected(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        times = [0.0, 1.0, 2.0, 3.0, 4.0]

        def fake_run(command, **kwargs):
            pattern = Path(command[-1])
            frames_dir = pattern.parent
            frames_dir.mkdir(parents=True, exist_ok=True)
            # Simulate ffmpeg only producing 3 of the 5 expected frames.
            for i in range(1, 4):
                (frames_dir / f"{i:06d}.jpg").write_bytes(b"\xff\xd8\xff")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            stderr = io.StringIO()
            with mock.patch("zero2hundred.picker.subprocess.run", side_effect=fake_run):
                with contextlib.redirect_stderr(stderr):
                    html_path = build_picker(Path("input.mp4"), toolchain, times, workdir)

            message = stderr.getvalue()
            self.assertIn("expected 5 thumbnails", message)
            self.assertIn("produced 3", message)

            text = html_path.read_text(encoding="utf-8")
            # Pairing must still truncate to the shorter (thumbnail) length rather than crash.
            self.assertEqual(text.count(".jpg"), 3)

    def test_no_warning_when_counts_match(self) -> None:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        times = [0.0, 1.0, 2.0]

        def fake_run(command, **kwargs):
            pattern = Path(command[-1])
            frames_dir = pattern.parent
            frames_dir.mkdir(parents=True, exist_ok=True)
            for i in range(1, 4):
                (frames_dir / f"{i:06d}.jpg").write_bytes(b"\xff\xd8\xff")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            stderr = io.StringIO()
            with mock.patch("zero2hundred.picker.subprocess.run", side_effect=fake_run):
                with contextlib.redirect_stderr(stderr):
                    build_picker(Path("input.mp4"), toolchain, times, workdir)

            self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
