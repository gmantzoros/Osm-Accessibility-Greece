"""Tests of saved-artifact reporting assembly; no census execution."""
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import mock_open, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import paper_data
import targeting_distance


def scenario(scope, threshold=1, status="artifact_hashed_execution_link_unverified",
             profile="bvi-v0", pess=7.17):
    return {"profile_id": profile, "profile": profile, "unit": "NATIONAL",
            "network_scope": scope, "graph_scope": scope,
            "min_usable_component_km": threshold, "model_version": "test-model",
            "pess_pct": pess, "opt_pct": 96.43, "gap_pct": 96.43-pess,
            "data_freshness": {"status": status}}


class ReportingAssemblyTests(unittest.TestCase):
    def test_pending_audit_does_not_invent_results_or_load_inputs(self):
        with patch("local_audit.load_expected_sample") as loader:
            result = paper_data.local_audit_result()
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "not_supplied")
        self.assertNotIn("rates", result)
        loader.assert_not_called()

    def test_orphan_repeat_and_reference_options_rejected(self):
        for option in ({"repeat_path": "repeat.csv"}, {"reference_date": "2026-09-15"}):
            with self.assertRaisesRegex(ValueError, "require --audit-results"):
                paper_data.local_audit_result(**option)

    def test_verified_full_profiles_do_not_drop_native_core_or_thresholds(self):
        native = [scenario(scope, threshold)
                  for scope in ("full", "core") for threshold in (.5, 1, 2)]
        external = [scenario("full", status="execution_manifest_verified"),
                    scenario("full", status="execution_manifest_verified", profile="wheelchair")]
        merged = paper_data.merge_sensitivity_rows(native, external)
        self.assertEqual(len(merged), 7)
        self.assertEqual(sum(r["network_scope"] == "core" for r in merged), 3)
        self.assertEqual(sum(r["min_usable_component_km"] == .5 for r in merged), 2)
        main = next(r for r in merged if r["profile_id"] == "bvi-v0"
                    and r["network_scope"] == "full" and r["min_usable_component_km"] == 1)
        self.assertEqual(main["data_freshness"]["status"], "execution_manifest_verified")
        self.assertEqual(native[1]["data_freshness"]["status"], "artifact_hashed_execution_link_unverified")

    def test_scope_aliases_are_one_scenario(self):
        self.assertEqual(len(paper_data.merge_sensitivity_rows(
            [scenario("extended")], [scenario("full")])), 1)

    def test_disagreement_is_not_silently_overwritten(self):
        with self.assertRaisesRegex(ValueError, "outcomes disagree"):
            paper_data.merge_sensitivity_rows([scenario("full")], [scenario("full", pess=8)])

    def test_different_models_are_not_merged(self):
        external = scenario("full")
        external["model_version"] = "another-model"
        with self.assertRaisesRegex(ValueError, "versions disagree"):
            paper_data.merge_sensitivity_rows([scenario("full")], [external])

    def test_unverified_duplicate_does_not_replace_verified_run(self):
        rows = paper_data.merge_sensitivity_rows(
            [scenario("full", status="execution_manifest_verified")], [scenario("full")])
        self.assertEqual(rows[0]["data_freshness"]["status"], "execution_manifest_verified")

    def test_unknown_scope_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            paper_data.merge_sensitivity_rows([scenario("fake")], [])

    def test_nonfinite_outcomes_do_not_pass_duplicate_comparison(self):
        invalid = scenario("full", pess=float("nan"))
        with self.assertRaisesRegex(ValueError, "Non-finite"):
            paper_data.merge_sensitivity_rows([scenario("full")], [invalid])


@unittest.skipUnless((ROOT / "results/census/edit_targets_summary.csv").is_file(),
                     "needs the unpublished frozen census outputs")
class TargetingDistanceAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected, _ = targeting_distance.analyze_sources(ROOT)
        report = cls.expected
        cls.feasibility = {
            "model_version": report["model_version"], "profile": report["profile_id"],
            "graph_scope": report["graph_scope"],
            "fragmentation": {
                "model_version": report["model_version"], "graph_scope": report["graph_scope"],
                "min_usable_component_km": report["component_threshold_km"],
            },
        }
        cls.targeting = {
            "model_version": report["model_version"], "profile": report["profile_id"],
            "graph_scope": report["graph_scope"],
            "component_threshold_km": report["component_threshold_km"],
            "intervention_model": report["intervention_model"],
            "usable_km_before": report["usable_km_before"],
        }

    def check_fixture(self, report, feasibility=None, targeting=None):
        with (patch("paper_data.open", mock_open(read_data=json.dumps(report))),
              patch("targeting_distance.analyze_sources", return_value=(self.expected, {}))):
            return paper_data.load_targeting_distance(
                feasibility if feasibility is not None else self.feasibility,
                targeting if targeting is not None else self.targeting, ROOT)

    def test_saved_comparison_matches_fixed_worklists(self):
        result = paper_data.load_targeting_distance(
            self.feasibility, self.targeting, ROOT)
        self.assertEqual(result["common_max_survey_km"], 487.539)
        self.assertEqual(result["comparisons"][-1]["strategies"]["static_initial_consolidation"]
                         ["selected_tasks"], 28)
        self.assertEqual(result["comparisons"][-1]["strategies"]["consolidation_per_km"]
                         ["cumulative_usable_gain_km"], 1240.456)

    def test_worklist_hash_and_path_must_match(self):
        for field, value in (("sha256", "0" * 64), ("path", "results\\census\\other.csv")):
            changed = deepcopy(self.expected)
            changed["source_artifacts"]["length_only"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "source artifact: length_only"):
                self.check_fixture(changed)

    def test_saved_prefix_and_scenario_cannot_disagree_with_sources(self):
        for section in ("prefix", "scenario"):
            changed = deepcopy(self.expected)
            if section == "prefix":
                changed["comparisons"][-1]["strategies"]["static_initial_consolidation"]["selected_tasks"] = 29
            else:
                changed["scenario"]["all_positive"] = False
            with self.subTest(section=section), self.assertRaisesRegex(ValueError, "differ from the fixed"):
                self.check_fixture(changed)

    def test_feasibility_and_targeting_settings_must_agree(self):
        for field, value in (("model_version", "other"), ("profile", "other"),
                             ("graph_scope", "core")):
            feasibility = deepcopy(self.feasibility)
            feasibility[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "manuscript model"):
                self.check_fixture(self.expected, feasibility=feasibility)
        targeting = deepcopy(self.targeting)
        targeting["component_threshold_km"] = 2
        with self.assertRaisesRegex(ValueError, "Targeting summary component_threshold_km"):
            self.check_fixture(self.expected, targeting=targeting)

    def test_source_paths_are_portable_across_platforms(self):
        saved = deepcopy(self.expected)
        for source in saved["source_artifacts"].values():
            source["path"] = source["path"].replace("\\", "/")
        for strategy in saved["strategies"].values():
            strategy["source_file"] = strategy["source_file"].replace("\\", "/")
        self.assertEqual(self.check_fixture(saved), saved)


@unittest.skipUnless((ROOT / "results/analysis/mapillary_radius_sensitivity.json").is_file(),
                     "needs the unpublished archived imagery analysis")
class MapillaryRadiusAssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(
            (ROOT / "results" / "analysis" / "mapillary_radius_sensitivity.json").read_text(encoding="utf-8"))
        cls.mapillary = json.loads((ROOT / "results" / "paper_data.json").read_text(encoding="utf-8"))["mapillary"]

    def test_saved_sensitivity_matches_screener_and_csv(self):
        report = paper_data.load_mapillary_radius_sensitivity(self.mapillary)
        self.assertEqual(report["national"], self.report["national"])
        self.assertEqual([row["radius_m"] for row in report["national"][1:]], [10.0, 25.0, 50.0])

    def test_rejects_modified_source_hash(self):
        report = deepcopy(self.report)
        report["source_hashes"]["analysis/mapillary_join.py"] = "0" * 64
        with patch("paper_data.json.load", return_value=report):
            with self.assertRaisesRegex(ValueError, "Stale Mapillary radius comparison source"):
                paper_data.load_mapillary_radius_sensitivity(self.mapillary)

    def test_rejects_modified_city_count(self):
        report = deepcopy(self.report)
        report["by_city"][0]["crossings_codetected_all_dates"] += 1
        with patch("paper_data.json.load", return_value=report):
            with self.assertRaisesRegex(ValueError, "rate|totals disagree"):
                paper_data.load_mapillary_radius_sensitivity(self.mapillary)


if __name__ == "__main__":
    unittest.main()
