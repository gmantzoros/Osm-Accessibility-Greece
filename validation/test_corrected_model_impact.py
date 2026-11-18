"""Verify the separately executed, source-linked Greece model comparison."""

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import corrected_model_impact as impact


@unittest.skipUnless((ROOT / "results/impact/greece-full-strata-conditional-2026-10-09/run_manifest.json").is_file(),
                     "needs the unpublished local run manifest")
class CorrectedImpactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = impact.analyze_sources(ROOT)

    def test_national_frame_and_recorded_native_sensitivity(self):
        report = self.expected
        self.assertEqual(report["strata_compared"], 6228)
        self.assertEqual(len(report["places"]), 26)
        self.assertEqual(len(report["thresholds"]), 6)
        self.assertEqual(report["places"][0]["stratum"], "NATIONAL")
        self.assertEqual(report["frozen_model_version"], impact.FROZEN_VERSION)
        self.assertEqual(report["corrected_model_version"], impact.CORRECTED_VERSION)
        self.assertEqual(report, impact.load_report(ROOT))

    def test_endpoints_use_eligible_length_and_reject_invalid_numbers(self):
        row = {"eligible_km": "20", "pess_eligible_usable_km": "2",
               "opt_eligible_usable_km": "15", "walkable_km": "100"}
        self.assertEqual(impact.endpoints(row), (10.0, 75.0))
        row["opt_eligible_usable_km"] = "nan"
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            impact.endpoints(row)

    def test_tampered_saved_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "results/analysis"
            folder.mkdir(parents=True)
            altered = json.loads(json.dumps(self.expected))
            altered["places"][0]["corrected_pess_pct"] += 1
            (folder / "corrected_model_impact.json").write_text(json.dumps(altered), encoding="utf-8")
            with patch.object(impact, "analyze_sources", return_value=self.expected):
                with self.assertRaisesRegex(ValueError, "source outputs"):
                    impact.load_report(root)

    def test_tampered_saved_csv_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "results/analysis"
            folder.mkdir(parents=True)
            (folder / "corrected_model_impact.json").write_text(
                json.dumps(self.expected), encoding="utf-8")
            with (folder / "corrected_model_impact.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=impact.PLACE_COLUMNS)
                writer.writeheader()
                rows = [{key: row[key] for key in impact.PLACE_COLUMNS} for row in self.expected["places"]]
                rows[0]["delta_pess_pp"] += 1
                writer.writerows(rows)
            with patch.object(impact, "analyze_sources", return_value=self.expected):
                with self.assertRaisesRegex(ValueError, "CSV/JSON disagree"):
                    impact.load_report(root)


if __name__ == "__main__":
    unittest.main()
