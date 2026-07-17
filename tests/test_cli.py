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
        settings: RenderSettings | None = None,
    ) -> tuple[int, str, str, mock.Mock, mock.Mock, mock.Mock]:
        toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")
        media = SimpleNamespace(width=1080, height=1920, frame_rate=30.0, duration=2.0)
        settings = settings or RenderSettings()
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

    def test_picker_marks_skip_time_prompts(self) -> None:
        result, stdout, stderr, picker, time_value, render_job_type = self.run_main(
            ["input.mp4", "--pick", "--dry-run"],
            (0.5, 1.0),
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
        time_value.assert_not_called()
        self.assertTrue(render_job_type.call_args.kwargs["settings"].continue_after_freeze)

    def test_picker_cannot_be_combined_with_typed_times(self) -> None:
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                cli.main(["input.mp4", "--pick", "--start", "0.5"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn(
            "--pick cannot be combined with --start or --end", stderr.getvalue()
        )

    def test_typed_times_do_not_open_the_picker(self) -> None:
        result, _, stderr, picker, time_value, _ = self.run_main(
            [
                "input.mp4",
                "--start",
                "0.5",
                "--end",
                "1.0",
                "--dry-run",
            ],
            (0.0, 0.0),
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        picker.assert_not_called()
        self.assertEqual(
            [call.args for call in time_value.call_args_list],
            [("0.5", "Launch timestamp"), ("1.0", "100 km/h timestamp")],
        )

    def test_invalid_render_setting_fails_before_video_inspection(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with mock.patch("zero2hundred.cli._input_path") as input_path:
            with contextlib.redirect_stdout(stdout):
                with contextlib.redirect_stderr(stderr):
                    result = cli.main(["input.mp4", "--freeze", "-1"])

        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(), "Error: freeze_duration cannot be negative\n"
        )
        input_path.assert_not_called()

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

    def test_appearance_flags_override_configured_settings(self) -> None:
        configured = RenderSettings(
            overlay_style="type-only", timer_format="seconds", overlay_scale=1.0
        )
        _, _, stderr, _, _, render_job_type = self.run_main(
            [
                "input.mp4",
                "--pick",
                "--overlay-style",
                "compact",
                "--timer-format",
                "stopwatch",
                "--overlay-scale",
                "1.25",
                "--dry-run",
            ],
            (0.5, 1.0),
            settings=configured,
        )

        self.assertEqual(stderr, "")
        passed = render_job_type.call_args.kwargs["settings"]
        self.assertEqual(passed.overlay_style, "compact")
        self.assertEqual(passed.timer_format, "stopwatch")
        self.assertEqual(passed.overlay_scale, 1.25)

    def test_legibility_flags_override_configured_settings(self) -> None:
        _, _, stderr, _, _, render_job_type = self.run_main(
            [
                "input.mp4",
                "--pick",
                "--border-width",
                "3",
                "--text-color",
                "yellow",
                "--dry-run",
            ],
            (0.5, 1.0),
        )

        self.assertEqual(stderr, "")
        passed = render_job_type.call_args.kwargs["settings"]
        self.assertEqual(passed.border_width, 3)
        self.assertEqual(passed.text_color, "yellow")

    def test_fps_flag_overrides_the_configured_frame_rate(self) -> None:
        _, _, stderr, _, _, render_job_type = self.run_main(
            ["input.mp4", "--pick", "--fps", "60", "--dry-run"],
            (0.5, 1.0),
        )

        self.assertEqual(stderr, "")
        self.assertEqual(render_job_type.call_args.kwargs["settings"].frame_rate, 60.0)

    def test_continue_after_freeze_overrides_a_configured_short_ending(self) -> None:
        configured = RenderSettings(continue_after_freeze=False)
        result, stdout, stderr, _, _, render_job_type = self.run_main(
            [
                "input.mp4",
                "--pick",
                "--continue-after-freeze",
                "--dry-run",
            ],
            (0.5, 1.0),
            settings=configured,
        )

        self.assertEqual(result, 0)
        self.assertEqual(stderr, "")
        self.assertIn("  Ending      Continue after freeze", stdout)
        self.assertTrue(
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
