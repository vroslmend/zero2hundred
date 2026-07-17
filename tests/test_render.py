from pathlib import Path
import unittest

from zero2hundred.config import RenderSettings
from zero2hundred.events import EventWindow
from zero2hundred.media import MediaInfo, Toolchain
from zero2hundred.render import RenderJob, build_filter_graph


class RenderGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.media = MediaInfo(
            path=Path("run.mp4"),
            duration=20,
            width=1920,
            height=1080,
            frame_rate=30,
            has_audio=True,
        )
        self.events = EventWindow(launch=4.0, reached_100=10.0)

    def test_type_only_default_uses_manrope_seconds_and_safe_clearance(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(),
            trim_intro=False,
        )
        self.assertIn("trim=start=0.000000", graph)
        self.assertIn("fps=fps=30.000000", graph)
        self.assertNotIn("enable=", graph)
        self.assertNotIn("drawbox=", graph)
        self.assertIn("Manrope-Medium.ttf", graph)
        self.assertIn("0–100 km/h", graph)
        self.assertIn("y=h-text_h-h*0.160000", graph)
        self.assertIn("text='s'", graph)
        self.assertIn(
            r"%{eif\:trunc(min(max(t-4.000000\,0)\,6.000000))\:d}."
            r"%{eif\:trunc(mod(min(max(t-4.000000\,0)\,6.000000)\,1)*100)\:d\:2}",
            graph,
        )
        self.assertIn("tpad=stop_mode=clone:stop_duration=2.000000", graph)
        self.assertIn("trim=start=10.033333:end=20.000000", graph)
        self.assertIn("atrim=start=10.033333:end=20.000000", graph)
        self.assertIn("concat=n=2:v=1:a=1[video][audio]", graph)
        self.assertNotIn("apad=", graph)

    def test_frame_rate_override_replaces_the_source_rate(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(frame_rate=60.0),
            trim_intro=False,
        )
        self.assertIn("fps=fps=60.000000", graph)
        self.assertNotIn("fps=fps=30.000000", graph)

    def test_frame_rate_defaults_to_the_source_rate(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(),
            trim_intro=False,
        )
        self.assertIn("fps=fps=30.000000", graph)

    def test_bundled_manrope_weights_resolve_to_their_font_files(self) -> None:
        cases = {
            "Manrope": "Manrope-Medium.ttf",
            "manrope-regular": "Manrope-Regular.ttf",
            "Manrope SemiBold": "Manrope-SemiBold.ttf",
            "manrope-bold": "Manrope-Bold.ttf",
        }
        for font, expected_file in cases.items():
            with self.subTest(font=font):
                graph = build_filter_graph(
                    self.media,
                    self.events,
                    RenderSettings(font=font),
                    trim_intro=False,
                )
                self.assertIn(expected_file, graph)

    def test_unbundled_font_family_is_passed_through_by_name(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(font="Arial"),
            trim_intro=False,
        )
        self.assertIn("font='Arial'", graph)
        self.assertNotIn(".ttf", graph)

    def test_hms_style_keeps_previous_behavior(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(timer_style="hms"),
            trim_intro=False,
        )
        self.assertIn(r"%{pts\:hms\:-4.000000}", graph)
        self.assertIn("enable='gte(t,4.000000)'", graph)
        self.assertNotIn("drawbox=", graph)

    def test_stopwatch_format_keeps_mm_ss_centiseconds(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(timer_format="stopwatch"),
            trim_intro=False,
        )
        self.assertIn(
            r"%{eif\:trunc(min(max(t-4.000000\,0)\,6.000000)/60)\:d\:2}\:"
            r"%{eif\:trunc(mod(min(max(t-4.000000\,0)\,6.000000)\,60))\:d\:2}\:",
            graph,
        )

    def test_panel_presets_add_a_neutral_drawbox(self) -> None:
        for style in ("quiet-plate", "compact"):
            with self.subTest(style=style):
                graph = build_filter_graph(
                    self.media,
                    self.events,
                    RenderSettings(overlay_style=style),
                    trim_intro=False,
                )
                self.assertIn("drawbox=", graph)
                self.assertIn("color=black@0.580000", graph)

    def test_trim_intro_resets_timer(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(),
            trim_intro=True,
        )
        self.assertIn("trim=start=4.000000", graph)
        self.assertNotIn("enable=", graph)
        self.assertIn(r"min(max(t-0.000000\,0)\,6.000000)", graph)

    def test_trim_intro_resets_timer_hms(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(timer_style="hms"),
            trim_intro=True,
        )
        self.assertIn("trim=start=4.000000", graph)
        self.assertIn("enable='gte(t,0.000000)'", graph)

    def test_explicit_clip_end_used_for_trim_and_atrim(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(),
            trim_intro=False,
            clip_end=10.123456,
        )
        self.assertIn("trim=start=0.000000:end=10.123456", graph)
        self.assertIn("atrim=start=0.000000:end=10.123456", graph)
        self.assertIn("trim=start=10.123456:end=20.000000", graph)
        self.assertIn("atrim=start=10.123456:end=20.000000", graph)

    def test_none_clip_end_preserves_average_frame_fallback(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(),
            trim_intro=False,
        )
        expected_end = self.events.reached_100 + self.media.frame_duration
        self.assertIn(f"trim=start=0.000000:end={expected_end:.6f}", graph)
        self.assertIn(f"atrim=start=0.000000:end={expected_end:.6f}", graph)

    def test_end_after_freeze_keeps_the_short_output_graph(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(continue_after_freeze=False),
            trim_intro=False,
            clip_end=10.123456,
        )

        self.assertNotIn("concat=", graph)
        self.assertNotIn("trim=start=10.123456:end=20.000000", graph)
        self.assertIn("apad=pad_dur=2.000000[audio]", graph)

    def test_zero_duration_freeze_still_continues_without_the_overlay(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(freeze_duration=0),
            trim_intro=False,
            clip_end=10.123456,
        )

        self.assertIn("tpad=stop_mode=clone:stop_duration=0.000000", graph)
        self.assertIn("concat=n=2:v=1:a=1[video][audio]", graph)

    def test_video_without_audio_uses_video_only_concat(self) -> None:
        media = MediaInfo(
            path=Path("silent.mp4"),
            duration=20,
            width=1920,
            height=1080,
            frame_rate=30,
            has_audio=False,
        )
        graph = build_filter_graph(
            media,
            self.events,
            RenderSettings(),
            trim_intro=False,
            clip_end=10.123456,
        )

        self.assertIn("concat=n=2:v=1:a=0[video]", graph)
        self.assertNotIn("[0:a]", graph)


class RenderJobClipEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.media = MediaInfo(
            path=Path("run.mp4"),
            duration=20,
            width=1920,
            height=1080,
            frame_rate=30,
            has_audio=True,
        )
        self.events = EventWindow(launch=4.0, reached_100=10.0)
        self.toolchain = Toolchain(ffmpeg="ffmpeg", ffprobe="ffprobe")

    def _job(
        self,
        clip_end: float | None,
        *,
        continue_after_freeze: bool = True,
        trim_intro: bool = False,
    ) -> RenderJob:
        return RenderJob(
            media=self.media,
            events=self.events,
            output=Path("out.mp4"),
            settings=RenderSettings(continue_after_freeze=continue_after_freeze),
            toolchain=self.toolchain,
            trim_intro=trim_intro,
            clip_end=clip_end,
        )

    def test_continuing_reads_the_full_input(self) -> None:
        command = self._job(10.123456).command()
        index = command.index("-to")
        self.assertEqual(command[index + 1], "20.000000")

    def test_end_after_freeze_stops_input_at_the_split(self) -> None:
        command = self._job(10.123456, continue_after_freeze=False).command()
        index = command.index("-to")
        self.assertEqual(command[index + 1], "10.123456")

    def test_output_duration_includes_the_tail_and_freeze(self) -> None:
        self.assertEqual(self._job(10.123456).output_duration, 22.0)
        self.assertEqual(
            self._job(10.123456, trim_intro=True).output_duration,
            18.0,
        )

    def test_short_output_duration_ends_after_the_freeze(self) -> None:
        job = self._job(10.123456, continue_after_freeze=False)
        self.assertAlmostEqual(job.output_duration, 12.123456)


if __name__ == "__main__":
    unittest.main()
