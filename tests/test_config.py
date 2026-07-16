import tempfile
from pathlib import Path
import unittest

from zero2hundred.config import RenderSettings, load_settings
from zero2hundred.errors import ConfigurationError


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self) -> None:
        self.assertEqual(load_settings(None), RenderSettings())

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


if __name__ == "__main__":
    unittest.main()

