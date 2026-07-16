from pathlib import Path
import contextlib
import io
import tempfile
import unittest
from unittest import mock

from zero2hundred.detect.evaluate import (
    DETECTORS,
    EvaluationResult,
    frame_distance,
    hit_rate,
    load_ground_truth,
    main,
    render_report,
    score_frame_suggestion,
    score_suggestion,
)


class GroundTruthParsingTests(unittest.TestCase):
    def test_loads_labeled_and_reference_rows(self) -> None:
        text = (
            "file,launch,hundred,notes\n"
            "run.mp4,1.395,10.982,night run\n"
            "edited.mp4,,,reference edit of run.mp4\n"
        )
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "ground_truth.csv"
            path.write_text(text, encoding="utf-8")

            rows = load_ground_truth(path)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].file, "run.mp4")
        self.assertEqual(rows[0].launch, 1.395)
        self.assertEqual(rows[0].hundred, 10.982)
        self.assertEqual(rows[0].notes, "night run")
        self.assertIsNone(rows[1].launch)
        self.assertIsNone(rows[1].hundred)
        self.assertEqual(rows[1].notes, "reference edit of run.mp4")

    def test_rejects_a_missing_required_column(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "ground_truth.csv"
            path.write_text("file,launch\nrun.mp4,1.0\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing columns: hundred, notes"):
                load_ground_truth(path)

    def test_rejects_a_non_numeric_mark(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "ground_truth.csv"
            path.write_text(
                "file,launch,hundred,notes\nrun.mp4,soon,10.0,\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "invalid launch.*run.mp4"):
                load_ground_truth(path)


class EvaluationScoringTests(unittest.TestCase):
    def test_scores_absolute_error_confidence_and_inclusive_tolerance(self) -> None:
        result = score_suggestion(
            "run.mp4",
            truth=1.0,
            suggestion=(1.3, 0.75),
            tolerance_s=0.3,
        )

        self.assertEqual(
            result,
            EvaluationResult(
                file="run.mp4",
                truth=1.0,
                suggested=1.3,
                error_s=0.3,
                confidence=0.75,
                hit=True,
            ),
        )

    def test_hit_rate_reports_hits_and_total(self) -> None:
        results = [
            score_suggestion("a.mp4", 1.0, (1.1, 0.9), tolerance_s=0.3),
            score_suggestion("b.mp4", 2.0, (2.5, 0.4), tolerance_s=0.3),
            score_suggestion("c.mp4", 3.0, (2.8, 0.7), tolerance_s=0.3),
        ]

        rate, hits, total = hit_rate(results)

        self.assertAlmostEqual(rate, 2 / 3)
        self.assertEqual((hits, total), (2, 3))

    def test_empty_results_have_a_zero_hit_rate(self) -> None:
        self.assertEqual(hit_rate([]), (0.0, 0, 0))

    def test_frame_distance_uses_nearest_presentation_indices(self) -> None:
        times = [0.0, 0.04, 0.09, 0.15, 0.22]

        self.assertEqual(frame_distance(times, 0.041, 0.149), 2)
        self.assertEqual(frame_distance(times, 0.0, 0.22), 4)

    def test_frame_scoring_uses_an_inclusive_frame_tolerance(self) -> None:
        times = [0.0, 0.04, 0.09, 0.15, 0.22]

        hit = score_frame_suggestion(
            "hit.mp4", 0.04, (0.15, 0.8), times, tolerance_frames=2
        )
        miss = score_frame_suggestion(
            "miss.mp4", 0.0, (0.15, 0.6), times, tolerance_frames=2
        )

        self.assertTrue(hit.hit)
        self.assertFalse(miss.hit)


class EvaluationReportingTests(unittest.TestCase):
    def test_report_has_required_columns_and_summary(self) -> None:
        results = [
            EvaluationResult("run.mp4", 1.0, 1.1, 0.1, 0.8, True),
        ]

        report = render_report(results, "launch")

        self.assertIn("file | truth | suggested | error_s | confidence", report)
        self.assertIn("run.mp4 | 1.000 | 1.100 | 0.100 | 0.800", report)
        self.assertIn("Hit rate @0.3s: 100.0% (1/1)", report)

    def test_main_selects_the_registered_detector(self) -> None:
        detector = mock.Mock(return_value=(1.0, 0.5))
        results = [EvaluationResult("run.mp4", 1.0, 1.0, 0.0, 0.5, True)]
        stdout = io.StringIO()

        with mock.patch.dict(DETECTORS, {"launch": detector}, clear=True):
            with mock.patch(
                "zero2hundred.detect.evaluate.run_evaluation",
                return_value=results,
            ) as run:
                with contextlib.redirect_stdout(stdout):
                    result = main(["labels.csv", "--detector", "launch"])

        self.assertEqual(result, 0)
        run.assert_called_once_with(Path("labels.csv"), "launch", detector)
        self.assertIn("Hit rate @0.3s", stdout.getvalue())

    def test_main_reports_an_unimplemented_detector_without_failing(self) -> None:
        stdout = io.StringIO()

        with mock.patch.dict(DETECTORS, {}, clear=True):
            with contextlib.redirect_stdout(stdout):
                result = main(["labels.csv", "--detector", "needle"])

        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "Detector not implemented yet: needle\n")


if __name__ == "__main__":
    unittest.main()
