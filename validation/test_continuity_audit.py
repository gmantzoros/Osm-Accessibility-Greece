"""Tests for sample-point evidence; no census or real annotation files are written."""
import copy
import datetime as dt
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import continuity_audit as audit


def fixture(responses):
    sample, results, targets = [], [], []
    for index, values in enumerate(responses, 1):
        group = f"W{index:03d}"
        targets.append({"osm_way_id": str(index), "rank": str(index), "own_km": "1",
                        "highway": "residential", "missing": "does this street have a sidewalk?"})
        for point, (fraction, value) in enumerate(zip((".15", ".5", ".85"), values), 1):
            identity = {field: "" for field in audit.IDENTITY}
            identity.update(case_id=f"{group}P{point}",
                            way_group=group, osm_way_id=str(index), point_index=str(point),
                            point_fraction=fraction, band="short", own_km="1",
                            lat="38", lon="23",
                            highway="residential", worklist_rank=str(index),
                            pool_n=str(len(responses)), draw_n=str(len(responses)),
                            inclusion_probability="1")
            sample.append(identity)
            results.append({**identity, "schema_version": "way-continuity-v1",
                            "sample_sha256": "a"*64, "sidewalk": value,
                            "completed": "true", "capture_year": "2026",
                            "unverifiable_reason": "no imagery" if value == "unverifiable" else "",
                            "audit_date": "2026-09-15"})
    return sample, results, targets


class PointObservationTests(unittest.TestCase):
    reference = dt.date(2026, 9, 15)

    def analyse(self, responses):
        sample, results, targets = fixture(responses)
        return audit.analyse(results, sample, "a"*64, targets, self.reference)

    def test_partial_negative_remains_unknown(self):
        report = self.analyse([("none", "unverifiable", "none")])
        bounds = report["task_aligned_eligible_point_bounds"]
        self.assertEqual(bounds["any_sampled_point_positive"], [0, 1])
        self.assertEqual(bounds["all_sampled_points_positive"], [0, 0])

    def test_positive_point_does_not_establish_all_points(self):
        report = self.analyse([("none", "none", "right")])
        bounds = report["task_aligned_eligible_point_bounds"]
        self.assertEqual(bounds["any_sampled_point_positive"], [1, 1])
        self.assertEqual(bounds["all_sampled_points_positive"], [0, 0])
        self.assertFalse(report["inference"]["whole_way_continuity_established"])

    def test_old_negative_becomes_temporally_unresolved(self):
        sample, results, targets = fixture([("none", "none", "none")])
        results[1]["capture_year"] = "2014"
        report = audit.analyse(results, sample, "a"*64, targets, self.reference)
        self.assertEqual(report["temporal_status_counts"]["stale"], 1)
        self.assertEqual(report["raw_point_agreement"]["ways_with_all_points_observed"], 1)
        self.assertEqual(report["eligible_point_agreement"]["ways_with_all_points_observed"], 0)
        self.assertEqual(report["task_aligned_eligible_point_bounds"]["any_sampled_point_positive"], [0, 1])

    def test_old_positive_does_not_establish_eligible_presence(self):
        sample, results, targets = fixture([("both", "unverifiable", "unverifiable")])
        results[0]["capture_year"] = "2011"
        report = audit.analyse(results, sample, "a"*64, targets, self.reference)
        self.assertEqual(report["task_aligned_raw_point_bounds"]["any_sampled_point_positive"], [1, 1])
        self.assertEqual(report["task_aligned_eligible_point_bounds"]["any_sampled_point_positive"], [0, 1])

    def test_future_and_ambiguous_years_are_not_eligible(self):
        for value, status in [("2027", "future_capture_year"), ("2021", "year_overlaps_age_cutoff"),
                              ("", "undated"), ("bad", "invalid_capture_year")]:
            self.assertEqual(audit.eligibility({"sidewalk": "none", "capture_year": value},
                                             self.reference, 5), status)

    def test_wrong_task_not_used_for_task_aligned_bounds(self):
        sample, results, targets = fixture([("unverifiable",)*3])
        targets[0]["missing"] = "does this stairway have a handrail?"
        report = audit.analyse(results, sample, "a"*64, targets, self.reference)
        self.assertEqual(report["task_aligned_sampled_ways"], 0)
        self.assertIsNone(report["task_aligned_eligible_point_bounds"])
        self.assertEqual(len(report["task_scope_mismatches"]), 1)

    def test_no_network_scaling_or_population_intervals(self):
        report = self.analyse([("both",)*3, ("none",)*3])
        self.assertNotIn("yield", report)
        self.assertNotIn("scaled_gain_km", str(report))
        self.assertNotIn("upper95_pct", str(report))
        self.assertFalse(report["inference"]["population_prevalence_estimated"])

    def test_duplicate_missing_or_tampered_rows_rejected(self):
        sample, results, targets = fixture([("none",)*3])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            audit.analyse(results + [results[0]], sample, "a"*64, targets, self.reference)
        with self.assertRaisesRegex(ValueError, "complete fixed sample"):
            audit.analyse(results[:-1], sample, "a"*64, targets, self.reference)
        changed = copy.deepcopy(results)
        changed[0]["lat"] = "39"
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            audit.analyse(changed, sample, "a"*64, targets, self.reference)

    def test_fingerprint_and_probability_rejected(self):
        sample, results, targets = fixture([("none",)*3])
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            audit.analyse(results, sample, "b"*64, targets, self.reference)
        for collection in (sample, results):
            for row in collection:
                row["inclusion_probability"] = ".5"
        with self.assertRaisesRegex(ValueError, "probability"):
            audit.analyse(results, sample, "a"*64, targets, self.reference)

    def test_incomplete_and_invalid_responses_rejected(self):
        sample, results, targets = fixture([("none",)*3])
        results[0]["completed"] = "false"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            audit.analyse(results, sample, "a"*64, targets, self.reference)
        results[0]["completed"] = "true"
        results[0]["sidewalk"] = "confirmed"
        with self.assertRaisesRegex(ValueError, "Invalid sidewalk"):
            audit.analyse(results, sample, "a"*64, targets, self.reference)

    def test_nonfinite_geometry_rejected_even_if_identity_matches(self):
        sample, results, targets = fixture([("none",)*3])
        sample[0]["lat"] = results[0]["lat"] = "nan"
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            audit.analyse(results, sample, "a"*64, targets, self.reference)

    def test_nonfinite_probability_rejected(self):
        sample, results, targets = fixture([("none",)*3])
        for row in sample + results:
            row["inclusion_probability"] = "nan"
        with self.assertRaisesRegex(ValueError, "probability"):
            audit.analyse(results, sample, "a"*64, targets, self.reference)


if __name__ == "__main__":
    unittest.main()
