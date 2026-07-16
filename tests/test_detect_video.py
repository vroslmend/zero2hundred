from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from zero2hundred import detect
from zero2hundred.errors import DetectionUnavailable
from zero2hundred.media import MediaInfo


HAS_CV = detect.available()


class DetectionAvailabilityTests(unittest.TestCase):
    def test_available_returns_the_import_guard_state(self) -> None:
        self.assertIsInstance(detect.available(), bool)
        self.assertEqual(detect.available(), detect._HAS_DETECTION)

    def test_require_raises_a_clear_error_without_the_extra(self) -> None:
        with mock.patch.object(detect, "_HAS_DETECTION", False):
            with self.assertRaisesRegex(
                DetectionUnavailable,
                r"^automatic detection needs the detect extra: "
                r"pip install -e \.\[detect\]$",
            ):
                detect.require()

    @unittest.skipUnless(HAS_CV, "OpenCV detection extra is not installed")
    def test_require_returns_normally_with_the_extra(self) -> None:
        self.assertIsNone(detect.require())


@unittest.skipUnless(HAS_CV, "OpenCV detection extra is not installed")
class FrameIterationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise unittest.SkipTest("FFmpeg is not installed")
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tempdir.name) / "clip.mp4"
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=duration=2:size=320x240:rate=30",
                "-pix_fmt",
                "yuv420p",
                str(cls.path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            cls.tempdir.cleanup()
            raise RuntimeError(completed.stderr.strip())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tempdir.cleanup()

    def media(self, *, rotation: int = 0) -> MediaInfo:
        width, height = (240, 320) if rotation in (90, 270) else (320, 240)
        return MediaInfo(
            path=self.path,
            duration=2.0,
            width=width,
            height=height,
            frame_rate=30.0,
            has_audio=False,
            rotation=rotation,
            video_codec="h264",
        )

    def test_iterates_all_frames_as_downscaled_grayscale(self) -> None:
        from zero2hundred.detect.video import iter_frames

        frames = list(iter_frames(self.path, self.media(), max_height=120))

        self.assertEqual(len(frames), 60)
        self.assertEqual([index for index, _ in frames], list(range(60)))
        for _, frame in frames:
            self.assertEqual(frame.shape, (120, 160))
            self.assertEqual(frame.dtype.name, "uint8")

    def test_step_preserves_source_frame_indices(self) -> None:
        from zero2hundred.detect.video import iter_frames

        frames = list(iter_frames(self.path, self.media(), step=7))

        self.assertEqual([index for index, _ in frames], list(range(0, 60, 7)))

    def test_applies_media_rotation_before_downscaling(self) -> None:
        from zero2hundred.detect.video import iter_frames

        _, frame = next(iter_frames(self.path, self.media(rotation=90)))

        self.assertEqual(frame.shape, (320, 240))


if __name__ == "__main__":
    unittest.main()
