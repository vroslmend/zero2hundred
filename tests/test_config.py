import tempfile
from pathlib import Path
import unittest

from zero2hundred.config import RenderSettings, load_settings
from zero2hundred.errors import ConfigurationError


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        self.assertEqual(load_settings(None), RenderSettings())
        self.assertEqual(RenderSettings().overlay_style, "type-only")
        self.assertTrue(RenderSettings().continue_after_freeze)
        self.assertEqual(RenderSettings().bottom_clearance_ratio, 0.16)
        self.assertEqual(RenderSettings().overlay_scale, 1.0)
        self.assertEqual(RenderSettings().timer_format, "seconds")
        self.assertEqual(RenderSettings().timer_label, "0–100 km/h")
        self.assertEqual(RenderSettings().font, "Manrope")

    def test_loads_render_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.toml")
            path.write_text('[render]\nposition = "top-left"\ncrf = 21\n', encoding="utf-8")
            settings = load_settings(path)
        self.assertEqual(settings.position, "top-left")
        self.assertEqual(settings.crf, 21)

    def test_rejects_unknown_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "config.toml")
            path.write_text("mystery = true\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_settings(path)

    def test_accepts_new_positions(self) -> None:
        for position in ("top-center", "bottom-center"):
            settings = RenderSettings(position=position).validated()
            self.assertEqual(settings.position, position)

    def test_rejects_bad_position(self) -> None:
        with self.assertRaises(ConfigurationError):
            RenderSettings(position="middle").validated()

    def test_accepts_overlay_styles_and_timer_formats(self) -> None:
        for style in ("type-only", "quiet-plate", "compact"):
            self.assertEqual(RenderSettings(overlay_style=style).validated().overlay_style, style)
        for timer_format in ("seconds", "stopwatch"):
            self.assertEqual(
                RenderSettings(timer_format=timer_format).validated().timer_format,
                timer_format,
            )

    def test_accepts_legacy_timer_styles(self) -> None:
        for style in ("stopwatch", "hms"):
            settings = RenderSettings(timer_style=style).validated()
            self.assertEqual(settings.timer_style, style)

    def test_rejects_bad_timer_style(self) -> None:
        with self.assertRaises(ConfigurationError):
            RenderSettings(timer_style="digital").validated()

    def test_frame_rate_defaults_to_none_and_accepts_a_positive_rate(self) -> None:
        self.assertIsNone(RenderSettings().frame_rate)
        self.assertEqual(RenderSettings(frame_rate=60.0).validated().frame_rate, 60.0)

    def test_rejects_non_positive_or_non_numeric_frame_rate(self) -> None:
        for value in (0, -30.0, float("nan"), float("inf"), True):
            with self.assertRaises(ConfigurationError):
                RenderSettings(frame_rate=value).validated()  # type: ignore[arg-type]

    def test_rejects_bad_overlay_configuration(self) -> None:
        with self.assertRaises(ConfigurationError):
            RenderSettings(overlay_style="neon").validated()
        with self.assertRaises(ConfigurationError):
            RenderSettings(timer_format="frames").validated()
        with self.assertRaises(ConfigurationError):
            RenderSettings(bottom_clearance_ratio=0.6).validated()
        with self.assertRaises(ConfigurationError):
            RenderSettings(overlay_scale=0.2).validated()
        with self.assertRaises(ConfigurationError):
            RenderSettings(continue_after_freeze="yes").validated()  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
