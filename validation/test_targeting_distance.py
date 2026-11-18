"""Saved-curve tests; no census, live queries or geometry reconstruction."""

import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
import targeting_distance as td


def rows(distances=("2.000", "3.000"), gains=("4.000", "7.000"),
         strategy="static_initial_consolidation", model=td.MODEL_VERSION,
         cumulative=False):
    output = []
    previous_distance = Decimal(0)
    for rank, (distance, gain) in enumerate(zip(distances, gains), 1):
        distance_value = Decimal(distance)
        own = distance_value - previous_distance if cumulative else distance_value
        output.append({
            "rank": str(rank),
            "osm_way_id": str(100 + rank),
            "own_km": str(own),
            "cumulative_survey_km" if cumulative else "survey_km": distance,
            "cumulative_usable_gain_km": gain,
            "selection_method": strategy,
            "model_version": model,
            "intervention_model": td.INTERVENTION_MODEL,
            "cumulative_km": "999999",
            "unlocked_km": "999999",
        })
        previous_distance = distance_value
    return output


class RankedDistanceTests(unittest.TestCase):
    def test_main_distance_is_cumulative_sum_and_gain_is_not_proxy(self):
        curve = td.curve_from_rows(rows(), "static_initial_consolidation")
        self.assertEqual(curve.distances, (Decimal("2.000"), Decimal("5.000")))
        self.assertEqual(curve.prefix(5).usable_gain_km, Decimal("7.000"))

    def test_missing_survey_column_uses_own_but_not_cumulative_proxy(self):
        source = rows()
        for row in source:
            del row["survey_km"]
        curve = td.curve_from_rows(source, "static_initial_consolidation")
        self.assertEqual(curve.distance_source, "own_km")
        self.assertEqual(curve.final_survey_km, Decimal(5))

    def test_unsorted_and_duplicate_ranks_rejected(self):
        source = rows()
        with self.assertRaisesRegex(ValueError, "Ranks"):
            td.curve_from_rows(source[::-1], "static_initial_consolidation")
        source[1]["rank"] = "1"
        with self.assertRaisesRegex(ValueError, "Ranks"):
            td.curve_from_rows(source, "static_initial_consolidation")

    def test_duplicate_ids_rejected(self):
        source = rows()
        source[1]["osm_way_id"] = source[0]["osm_way_id"]
        with self.assertRaisesRegex(ValueError, "Duplicate osm_way_id"):
            td.curve_from_rows(source, "static_initial_consolidation")

    def test_model_strategy_and_intervention_mismatch_rejected(self):
        for field, value, message in (
            ("model_version", "retired-model", "Model version"),
            ("selection_method", "length_only", "Selection strategy"),
            ("intervention_model", "literal_tag_edit", "Intervention model"),
        ):
            source = rows()
            source[1][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                td.curve_from_rows(source, "static_initial_consolidation")

    def test_models_must_match_between_curves(self):
        first = td.curve_from_rows(rows(), "static_initial_consolidation")
        second = td.curve_from_rows(rows(strategy="length_only", model="other"),
                                    "length_only", model_version="other")
        with self.assertRaisesRegex(ValueError, "Model versions"):
            td.compare_prefixes({"static_initial_consolidation": first, "length_only": second}, [1])

    def test_actual_gain_column_required(self):
        source = rows()
        del source[0]["cumulative_usable_gain_km"]
        with self.assertRaisesRegex(ValueError, "actual cumulative_usable_gain_km"):
            td.curve_from_rows(source, "static_initial_consolidation")

    def test_nonfinite_and_negative_distances_and_gains_rejected(self):
        for field in ("survey_km", "own_km", "cumulative_usable_gain_km", "marginal_usable_km"):
            for bad in ("NaN", "Infinity", "-0.001"):
                source = rows()
                source[0][field] = bad
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    td.curve_from_rows(source, "static_initial_consolidation")

    def test_cumulative_distances_and_gains_cannot_decrease(self):
        source = rows(cumulative=True)
        source[1]["cumulative_survey_km"] = "1.000"
        with self.assertRaisesRegex(ValueError, "distance decreases"):
            td.curve_from_rows(source, "static_initial_consolidation", distance_mode="cumulative")
        with self.assertRaisesRegex(ValueError, "gain decreases"):
            td.curve_from_rows(rows(gains=("4", "3")), "static_initial_consolidation")

    def test_steps_use_complete_prefixes_without_skipping_or_interpolation(self):
        curve = td.curve_from_rows(rows(distances=("4", "1"), gains=("8", "20")),
                                   "static_initial_consolidation")
        self.assertEqual(curve.prefix("3.999").tasks, 0)
        self.assertEqual(curve.prefix("4").usable_gain_km, Decimal(8))
        self.assertEqual(curve.prefix("4.999").usable_gain_km, Decimal(8))
        self.assertEqual(curve.prefix("5").usable_gain_km, Decimal(20))
        xs, ys = curve.staircase("4.5")
        self.assertEqual(xs, [0, 4, 4.5])
        self.assertEqual(ys, [0, 8, 8])

    def test_zero_rounded_distance_ties_select_longest_prefix(self):
        curve = td.curve_from_rows(rows(distances=("0", "0", "1"), gains=("1", "2", "5")),
                                   "static_initial_consolidation")
        self.assertEqual(curve.prefix(0).tasks, 2)
        self.assertEqual(curve.prefix("0.999").usable_gain_km, Decimal(2))
        cumulative = td.curve_from_rows(
            rows(distances=("1.000", "1.000", "2.000"), gains=("1", "2", "5"), cumulative=True),
            "static_initial_consolidation", distance_mode="cumulative")
        self.assertEqual(cumulative.prefix(1).tasks, 2)

    def test_cumulative_column_is_used_despite_independent_own_rounding(self):
        source = rows(distances=("0.001", "0.002"), gains=("1", "2"), cumulative=True)
        source[1]["own_km"] = "0.000"
        curve = td.curve_from_rows(source, "static_initial_consolidation", distance_mode="cumulative")
        self.assertEqual(curve.prefix("0.001").tasks, 1)
        self.assertEqual(curve.final_survey_km, Decimal("0.002"))

    def test_out_of_range_and_nonfinite_budgets_rejected_without_extrapolation(self):
        curve = td.curve_from_rows(rows(), "static_initial_consolidation")
        for budget in ("-1", "5.001", "NaN", "Infinity"):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                curve.prefix(budget)
            with self.subTest(staircase=budget), self.assertRaises(ValueError):
                curve.staircase(budget)
        self.assertEqual(curve.prefix(5).tasks, 2)

    def test_common_range_does_not_use_longer_curves_beyond_shortest(self):
        first = td.curve_from_rows(rows(), "static_initial_consolidation")
        second = td.curve_from_rows(rows(distances=("1", "1"), strategy="length_only"), "length_only")
        curves = {"static_initial_consolidation": first, "length_only": second}
        upper, comparison = td.compare_prefixes(curves, [0, 2])
        self.assertEqual(upper, Decimal(2))
        self.assertEqual(comparison[-1]["strategies"]["length_only"]["selected_tasks"], 2)
        with self.assertRaisesRegex(ValueError, "common recorded range"):
            td.compare_prefixes(curves, [3])
        for budgets in ([2, 0], [1, 1]):
            with self.assertRaisesRegex(ValueError, "unique"):
                td.compare_prefixes(curves, budgets)


@unittest.skipUnless((ROOT / "results/census/edit_targets_summary.csv").is_file(),
                     "needs the unpublished frozen census outputs")
class FrozenSourceTests(unittest.TestCase):
    def test_saved_sources_and_common_maximum_prefixes(self):
        report, curves = td.analyze_sources()
        self.assertEqual(report["model_version"], td.MODEL_VERSION)
        self.assertEqual(report["common_max_survey_km"], 487.539)
        self.assertEqual(report["component_threshold_km"], 1)
        self.assertTrue(report["scenario"]["all_positive"])
        expected = {
            "static_initial_consolidation": (28, 479.339, 489.634),
            "length_only": (21, 474.787, 481.094),
            "consolidation_per_km": (5000, 487.539, 1240.456),
        }
        for key, values in expected.items():
            prefix = report["comparisons"][-1]["strategies"][key]
            self.assertEqual((prefix["selected_tasks"], prefix["used_survey_km"],
                              prefix["cumulative_usable_gain_km"]), values)
            self.assertEqual(len(curves[key].points), 5000)
            self.assertEqual(len(report["source_artifacts"][key]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
