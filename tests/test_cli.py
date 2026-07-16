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
    ) -> tuple[int, str, str, mock.Mock, mock.Mock, mock.Mock]:
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
                                    ) as render_job_type:
                                        with contextlib.redirect_stdout(standard_out):
                                            with contextlib.redirect_stderr(standard_error):
                                                result = cli.main(arguments)

        return (
            result,
            standard_out.getvalue(),
            standard_error.getvalue(),
            picker,
            time_value,
            render_job_type,
        )

    def test_picker_marks_skip_prompts_and_explicit_start_wins(self) -> None:
        result, stdout, stderr, picker, time_value, render_job_type = self.run_main(
            ["input.mp4", "--pick", "--start", "0.5", "--dry-run"],
            (0.0, 1.0),
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("\nVideo\n", stdout)
        self.assertIn("  File        input.mp4", stdout)
        self.assertIn("  Resolution  1080 x 1920", stdout)
        self.assertIn("  Duration    00:02.000", stdout)
        self.assertIn("  Frame rate  30.000 fps", stdout)
        self.assertIn("  Frames      4", stdout)
        self.assertIn("\nPreparing frame picker...", stdout)
        self.assertIn(
            "Waiting for launch and 100 km/h marks in the browser...", stdout
        )
        self.assertIn("Marks received.", stdout)
        self.assertIn("\nResult\n", stdout)
        self.assertIn("  Launch      00:00.500", stdout)
        self.assertIn("  100 km/h    00:01.000", stdout)
        self.assertIn("  Time        0.500 seconds", stdout)
        self.assertIn("  Ending      Continue after freeze", stdout)
        self.assertIn("  Output      ", stdout)
        self.assertIn("\nFFmpeg command\n  ffmpeg -version", stdout)
        picker.assert_called_once()
        time_value.assert_called_once_with("0.5", "Launch timestamp")
        self.assertNotIn("100 km/h timestamp", [call.args[1] for call in time_value.call_args_list])
        self.assertTrue(render_job_type.call_args.kwargs["settings"].continue_after_freeze)

    def test_picker_oserror_warns_and_falls_back_to_manual_values(self) -> None:
        result, _, stderr, _, time_value, _ = self.run_main(
            ["input.mp4", "--pick", "--dry-run"], OSError("bind failed")
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "Warning: frame picker unavailable: bind failed\n")
        self.assertEqual(time_value.call_count, 2)
        self.assertEqual(time_value.call_args_list[0].args, (None, "Launch timestamp"))
        self.assertEqual(time_value.call_args_list[1].args, (None, "100 km/h timestamp"))

    def test_picker_media_error_warns_and_falls_back(self) -> None:
        result, _, stderr, _, time_value, _ = self.run_main(
            ["input.mp4", "--pick", "--dry-run"], MediaError("thumbs failed")
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "Warning: frame picker unavailable: thumbs failed\n")
        self.assertEqual(time_value.call_count, 2)

    def test_closing_picker_cancels_the_cli_cleanly(self) -> None:
        result, _, stderr, _, time_value, _ = self.run_main(
            ["input.mp4", "--pick", "--dry-run"], KeyboardInterrupt()
        )

        self.assertEqual(result, 130)
        self.assertEqual(stderr, "\nCancelled.\n")
        time_value.assert_not_called()

    def test_normal_run_separates_export_and_reports_finished_path(self) -> None:
        result, stdout, stderr, _, _, _ = self.run_main(
            ["input.mp4", "--pick"], (0.5, 1.0)
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("\nExporting input_0-100.mp4...\n", stdout)
        self.assertIn("Done: input_0-100.mp4\n", stdout)

    def test_end_after_freeze_overrides_the_configured_default(self) -> None:
        result, stdout, stderr, _, _, render_job_type = self.run_main(
            ["input.mp4", "--pick", "--end-after-freeze", "--dry-run"],
            (0.5, 1.0),
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("  Ending      End after freeze", stdout)
        self.assertFalse(
            render_job_type.call_args.kwargs["settings"].continue_after_freeze
        )


class ProgressReporterTests(unittest.TestCase):
    def test_formats_progress_as_an_indented_status_row(self) -> None:
        stdout = io.StringIO()
        reporter = cli._ProgressReporter()

        with contextlib.redirect_stdout(stdout):
            reporter(0.427)
            reporter.finish()

        self.assertEqual(stdout.getvalue(), "\r  Progress     42%\n")


if __name__ == "__main__":
    unittest.main()
