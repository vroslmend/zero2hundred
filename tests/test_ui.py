import io
import unittest

from zero2hundred.ui import UI, Spinner, should_style


class _Stream:
    def __init__(self, tty: bool, encoding: str = "utf-8") -> None:
        self._tty = tty
        self.encoding = encoding

    def isatty(self) -> bool:
        return self._tty


class ShouldStyleTests(unittest.TestCase):
    def test_bare_tty_enables_styling(self) -> None:
        self.assertTrue(should_style(_Stream(True), {}))

    def test_not_a_tty_disables_styling(self) -> None:
        self.assertFalse(should_style(_Stream(False), {}))

    def test_no_color_disables_even_on_a_tty(self) -> None:
        self.assertFalse(should_style(_Stream(True), {"NO_COLOR": "1"}))

    def test_empty_no_color_is_ignored(self) -> None:
        self.assertTrue(should_style(_Stream(True), {"NO_COLOR": ""}))

    def test_force_color_enables_without_a_tty(self) -> None:
        self.assertTrue(should_style(_Stream(False), {"FORCE_COLOR": "1"}))

    def test_no_color_beats_force_color(self) -> None:
        env = {"NO_COLOR": "1", "FORCE_COLOR": "1"}
        self.assertFalse(should_style(_Stream(True), env))

    def test_dumb_terminal_disables(self) -> None:
        self.assertFalse(should_style(_Stream(True), {"TERM": "dumb"}))


class PlainRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = UI(styled=False)

    def test_atoms_return_text_unchanged(self) -> None:
        for render in (self.ui.muted, self.ui.dim, self.ui.bold, self.ui.ok, self.ui.error):
            self.assertEqual(render("x"), "x")

    def test_heading_has_no_marker(self) -> None:
        self.assertEqual(self.ui.heading("Video"), "Video")

    def test_row_matches_the_legacy_aligned_format(self) -> None:
        self.assertEqual(self.ui.row("File", "run.mp4"), "  File        run.mp4")
        self.assertEqual(self.ui.row("Resolution", "1080 x 1920"), "  Resolution  1080 x 1920")

    def test_note_success_and_fail_are_plain(self) -> None:
        self.assertEqual(self.ui.note("Inspecting run.mp4..."), "Inspecting run.mp4...")
        self.assertEqual(self.ui.success("Done: out.mp4"), "Done: out.mp4")
        self.assertEqual(self.ui.fail("Error: nope"), "Error: nope")


class StyledRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = UI(styled=True)

    def test_atoms_wrap_with_escape_codes_and_reset(self) -> None:
        out = self.ui.muted("x")
        self.assertIn("\x1b[", out)
        self.assertTrue(out.endswith("\x1b[0m"))
        self.assertIn("x", out)

    def test_heading_is_bold_without_a_decorative_marker(self) -> None:
        out = self.ui.heading("Video")
        self.assertIn("\x1b[1m", out)
        self.assertIn("Video", out)
        self.assertNotIn("●", out)

    def test_success_and_fail_carry_glyphs(self) -> None:
        self.assertIn("✓", self.ui.success("Done"))
        self.assertIn("✗", self.ui.fail("Error"))

    def test_bar_shows_percent_and_stays_within_width(self) -> None:
        bar = self.ui.bar(0.5, width=10)
        self.assertIn("50%", bar)
        self.assertEqual(bar.count("━") + bar.count("─"), 10)

    def test_bar_clamps_out_of_range_fractions(self) -> None:
        self.assertIn("100%", self.ui.bar(1.4, width=8))
        self.assertIn("0%", self.ui.bar(-0.2, width=8))


class SpinnerTests(unittest.TestCase):
    def test_plain_spinner_prints_one_static_line_and_no_finished_line(self) -> None:
        stream = io.StringIO()
        spinner = Spinner(UI(styled=False), "Extracting preview frames...", stream=stream)
        with spinner:
            pass
        spinner.done("Extracted preview frames")

        self.assertEqual(stream.getvalue(), "  Extracting preview frames...\n")

    def test_styled_spinner_replaces_its_line_with_a_finished_step(self) -> None:
        stream = io.StringIO()
        spinner = Spinner(UI(styled=True), "Extracting preview frames...", stream=stream)
        with spinner:
            pass
        spinner.done("Extracted preview frames")

        output = stream.getvalue()
        self.assertIn("\x1b[2K", output)  # cleared the spinner line
        self.assertIn("✓", output)
        self.assertIn("Extracted preview frames", output)


if __name__ == "__main__":
    unittest.main()
