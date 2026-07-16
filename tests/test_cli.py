import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from zero2hundred import cli
from zero2hundred.config import RenderSettings
from zero2hundred.errors import MediaError
from zero2hundred.media import Toolchain


class PickerCliTests(unittest.TestCase):
    def run_main(
        self,
        arguments: list[str],
        picker_result: tuple[float, float] | BaseException,
    ) -> tuple[int, str, str, mock.Mock, mock.Mock]:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        media = SimpleNamespace(width=1080, height=1920, frame_rate=30.0, duration=2.0)
        settings = RenderSettings()
        render_job = mock.Mock()
        render_job.command.return_value = ["ffmpeg", "-version"]
        time_value = mock.Mock(
            side_effect=lambda value, label: (
                float(value)
                if value is not None
                else (0.5 if label == "Launch timestamp" else 1.0)
            )
        )
        standard_out = io.StringIO()
        standard_error = io.StringIO()

        if isinstance(picker_result, BaseException):
            picker_side_effect = picker_result
            picker_return_value = None
        else:
            picker_side_effect = None
            picker_return_value = picker_result

        with mock.patch("zero2hundred.cli._input_path", return_value=Path("input.mp4")):
            with mock.patch("zero2hundred.cli.find_toolchain", return_value=toolchain):
                with mock.patch("zero2hundred.cli.probe_video", return_value=media):
                    with mock.patch(
                        "zero2hundred.cli.frame_times",
                        return_value=[0.0, 0.5, 1.0, 1.5],
                    ):
                        with mock.patch(
                            "zero2hundred.cli.serve_picker",
                            return_value=picker_return_value,
                            side_effect=picker_side_effect,
                        ) as picker:
                            with mock.patch(
                                "zero2hundred.cli._time_value", time_value
                            ):
                                with mock.patch(
                                    "zero2hundred.cli.load_settings", return_value=settings
                                ):
                                    with mock.patch(
                                        "zero2hundred.cli.RenderJob", return_value=render_job
                                    ):
                                        with contextlib.redirect_stdout(standard_out):
                                            with contextlib.redirect_stderr(standard_error):
                                                result = cli.main(arguments)

        return result, standard_out.getvalue(), standard_error.getvalue(), picker, time_value

    def test_picker_marks_skip_prompts_and_explicit_start_wins(self) -> None:
        result, stdout, stderr, picker, time_value = self.run_main(
            ["input.mp4", "--pick", "--start", "0.5", "--dry-run"],
            (0.0, 1.0),
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn(
            "Pick the frames in your browser. The run continues when you press Finish.",
            stdout,
        )
        picker.assert_called_once()
        time_value.assert_called_once_with("0.5", "Launch timestamp")
        self.assertNotIn("100 km/h timestamp", [call.args[1] for call in time_value.call_args_list])

    def test_picker_oserror_warns_and_falls_back_to_manual_values(self) -> None:
        result, _, stderr, _, time_value = self.run_main(
            ["input.mp4", "--pick", "--dry-run"], OSError("bind failed")
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "Warning: frame picker unavailable: bind failed\n")
        self.assertEqual(time_value.call_count, 2)
        self.assertEqual(time_value.call_args_list[0].args, (None, "Launch timestamp"))
        self.assertEqual(time_value.call_args_list[1].args, (None, "100 km/h timestamp"))

    def test_picker_media_error_warns_and_falls_back(self) -> None:
        result, _, stderr, _, time_value = self.run_main(
            ["input.mp4", "--pick", "--dry-run"], MediaError("thumbs failed")
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "Warning: frame picker unavailable: thumbs failed\n")
        self.assertEqual(time_value.call_count, 2)


if __name__ == "__main__":
    unittest.main()
