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
        self.assertIn("apad=pad_dur=2.000000", graph)

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

    def _job(self, clip_end: float | None) -> RenderJob:
        return RenderJob(
            media=self.media,
            events=self.events,
            output=Path("out.mp4"),
            settings=RenderSettings(),
            toolchain=self.toolchain,
            clip_end=clip_end,
        )

    def test_explicit_clip_end_used_for_to_argument(self) -> None:
        command = self._job(10.123456).command()
        index = command.index("-to")
        self.assertEqual(command[index + 1], "10.123456")

    def test_none_clip_end_preserves_average_frame_fallback(self) -> None:
        command = self._job(None).command()
        index = command.index("-to")
        expected = self.events.reached_100 + self.media.frame_duration
        self.assertEqual(command[index + 1], f"{expected:.6f}")


if __name__ == "__main__":
    unittest.main()
