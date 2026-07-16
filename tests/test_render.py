from pathlib import Path
import unittest

from zero2hundred.config import RenderSettings
from zero2hundred.events import EventWindow
from zero2hundred.media import MediaInfo
from zero2hundred.render import build_filter_graph


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

    def test_stopwatch_default_has_no_enable_and_is_centered(self) -> None:
        graph = build_filter_graph(
            self.media,
            self.events,
            RenderSettings(),
            trim_intro=False,
        )
        self.assertIn("trim=start=0.000000", graph)
        self.assertIn("fps=fps=30.000000", graph)
        self.assertNotIn("enable=", graph)
        self.assertIn(r"x=(w-text_w)/2", graph)
        self.assertIn(
            r"%{eif\:trunc(min(max(t-4.000000\,0)\,6.000000)/60)\:d\:2}\:"
            r"%{eif\:trunc(mod(min(max(t-4.000000\,0)\,6.000000)\,60))\:d\:2}\:"
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


if __name__ == "__main__":
    unittest.main()
