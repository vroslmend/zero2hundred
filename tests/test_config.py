import tempfile
from pathlib import Path
import unittest

from zero2hundred.config import RenderSettings, load_settings
from zero2hundred.errors import ConfigurationError


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        self.assertEqual(load_settings(None), RenderSettings())
        self.assertEqual(RenderSettings().timer_label, "0-100 KM/H")
        self.assertEqual(RenderSettings().panel_color, "black@0.62")
        self.assertEqual(RenderSettings().accent_color, "0xFF6B4A@0.95")

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

    def test_accepts_valid_timer_styles(self) -> None:
        for style in ("stopwatch", "hms"):
            settings = RenderSettings(timer_style=style).validated()
            self.assertEqual(settings.timer_style, style)

    def test_rejects_bad_timer_style(self) -> None:
        with self.assertRaises(ConfigurationError):
            RenderSettings(timer_style="digital").validated()


if __name__ == "__main__":
    unittest.main()
