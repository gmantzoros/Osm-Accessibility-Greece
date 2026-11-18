"""Compare saved ranked survey scenarios at equal recorded distance budgets.

Only complete prefixes are affordable. No ways are interpolated, skipped or
re-optimised, and no curve is extended beyond its saved survey extent.

Use --versioned to read the verified corrected Greek run and write only to the
isolated unified reporting directory; the default retains the frozen worklist.
"""

import argparse
import csv
import hashlib
import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from aggregate import reporting_paths, verify_corrected_greek, write_versioned_manifest
from corrected_model_impact import CORRECTED_VERSION

ROOT = Path(__file__).resolve().parents[1]
MODEL_VERSION = "conditional-2026-09-15"
INTERVENTION_MODEL = "way_state_override_fixed_node_transitions"
ROUNDING_HALF_UNIT = Decimal("0.0005")
SOURCES = {
    "static_initial_consolidation": ("edit_targets.csv", "per_row", "Static consolidation score"),
    "length_only": ("edit_targets_length_baseline.csv", "cumulative", "Length-first ordering"),
    "consolidation_per_km": ("edit_targets_efficiency.csv", "cumulative", "Consolidation score / km"),
}


def nonnegative_decimal(value, field):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc
    if not number.is_finite() or not math.isfinite(float(number)) or number < 0:
        raise ValueError(f"{field} must be finite and nonnegative")
    return number


def positive_integer(value, field):
    text = str(value)
    if not text.isascii() or not text.isdecimal() or int(text) <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return int(text)


@dataclass(frozen=True)
class Prefix:
    tasks: int
    way_id: int | None
    survey_km: Decimal
    usable_gain_km: Decimal


@dataclass(frozen=True)
class RankedCurve:
    selection_method: str
    model_version: str
    distance_source: str
    points: tuple
    distances: tuple

    @property
    def final_survey_km(self):
        return self.points[-1].survey_km

    def prefix(self, budget_km):
        budget = nonnegative_decimal(budget_km, "budget_km")
        if budget > self.final_survey_km:
            raise ValueError("Budget exceeds recorded curve extent; no extrapolation")
        # Rightmost ties retain their rank order, including rounded zero lengths.
        count = bisect_right(self.distances, budget)
        return self.points[count - 1] if count else Prefix(0, None, Decimal(0), Decimal(0))

    def staircase(self, upper_budget_km):
        upper = nonnegative_decimal(upper_budget_km, "upper_budget_km")
        last = self.prefix(upper)
        xs, ys = [0.0], [0.0]
        for point in self.points[:last.tasks]:
            xs.append(float(point.survey_km))
            ys.append(float(point.usable_gain_km))
        if Decimal(str(xs[-1])) != upper:
            xs.append(float(upper))
            ys.append(float(last.usable_gain_km))
        return xs, ys


def curve_from_rows(rows, selection_method, model_version=MODEL_VERSION,
                    distance_mode="per_row", intervention_model=INTERVENTION_MODEL,
                    usable_km_before=None, require_intervention=False):
    rows = list(rows)
    if not rows:
        raise ValueError("Target curve has no rows")
    if distance_mode not in ("per_row", "cumulative"):
        raise ValueError(f"Unsupported distance mode: {distance_mode}")
    distance_field = ("cumulative_survey_km" if distance_mode == "cumulative" else
                      ("survey_km" if "survey_km" in rows[0] else "own_km"))
    before = (None if usable_km_before is None else
              nonnegative_decimal(usable_km_before, "usable_km_before"))
    seen_ids, points = set(), []
    cumulative_distance = summed_own = summed_marginal = Decimal(0)
    previous_gain = Decimal(0)
    for rank, row in enumerate(rows, 1):
        if positive_integer(row.get("rank"), "rank") != rank:
            raise ValueError("Ranks must be consecutive from one in input order; unsorted rows are rejected")
        way_id = positive_integer(row.get("osm_way_id"), "osm_way_id")
        if way_id in seen_ids:
            raise ValueError(f"Duplicate osm_way_id: {way_id}")
        seen_ids.add(way_id)
        if row.get("model_version") != model_version:
            raise ValueError(f"Model version mismatch at rank {rank}")
        if row.get("selection_method") != selection_method:
            raise ValueError(f"Selection strategy mismatch at rank {rank}")
        if (require_intervention or "intervention_model" in row) and row.get("intervention_model") != intervention_model:
            raise ValueError(f"Intervention model mismatch at rank {rank}")
        if distance_field not in row:
            raise ValueError(f"Missing required distance field: {distance_field}")
        distance = nonnegative_decimal(row[distance_field], distance_field)
        if distance_mode == "per_row":
            cumulative_distance += distance
        else:
            if distance < cumulative_distance:
                raise ValueError(f"Cumulative survey distance decreases at rank {rank}")
            cumulative_distance = distance
        if "own_km" in row:
            summed_own += nonnegative_decimal(row["own_km"], "own_km")
            if distance_mode == "cumulative" and abs(summed_own - cumulative_distance) > (rank + 1) * ROUNDING_HALF_UNIT:
                raise ValueError(f"Own and cumulative survey distances disagree at rank {rank}")
        if "cumulative_usable_gain_km" not in row:
            raise ValueError("Missing actual cumulative_usable_gain_km; legacy proxies are not accepted")
        gain = nonnegative_decimal(row["cumulative_usable_gain_km"], "cumulative_usable_gain_km")
        if gain < previous_gain:
            raise ValueError(f"Cumulative usable gain decreases at rank {rank}")
        if "marginal_usable_km" in row:
            marginal = nonnegative_decimal(row["marginal_usable_km"], "marginal_usable_km")
            summed_marginal += marginal
            if abs(gain - summed_marginal) > (rank + 1) * ROUNDING_HALF_UNIT:
                raise ValueError(f"Marginal and cumulative usable gains disagree at rank {rank}")
            if abs(gain - previous_gain - marginal) > 3 * ROUNDING_HALF_UNIT:
                raise ValueError(f"Marginal usable gain disagrees with its step at rank {rank}")
        if "usable_km_after" in row:
            after = nonnegative_decimal(row["usable_km_after"], "usable_km_after")
            if before is not None and abs(after - before - gain) > 3 * ROUNDING_HALF_UNIT:
                raise ValueError(f"Usable endpoint and gain disagree at rank {rank}")
        points.append(Prefix(rank, way_id, cumulative_distance, gain))
        previous_gain = gain
    return RankedCurve(selection_method, model_version, distance_field, tuple(points),
                       tuple(point.survey_km for point in points))


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)):
            raise ValueError(f"Duplicate CSV column names: {Path(path).name}")
        return list(reader)


def compare_prefixes(curves, budgets):
    if not curves:
        raise ValueError("No curves to compare")
    if len({curve.model_version for curve in curves.values()}) != 1:
        raise ValueError("Model versions differ between curves")
    for key, curve in curves.items():
        if key != curve.selection_method:
            raise ValueError("Curve key and selection strategy disagree")
    upper = min(curve.final_survey_km for curve in curves.values())
    values = [nonnegative_decimal(budget, "budget_km") for budget in budgets]
    if values != sorted(set(values)):
        raise ValueError("Budgets must be unique and in increasing order")
    comparisons = []
    for budget in values:
        if budget > upper:
            raise ValueError("Budget exceeds the common recorded range; no extrapolation")
        strategies = {}
        for key, curve in curves.items():
            prefix = curve.prefix(budget)
            following = curve.points[prefix.tasks] if prefix.tasks < len(curve.points) else None
            strategies[key] = {
                "selected_tasks": prefix.tasks,
                "last_selected_rank": prefix.tasks,
                "last_selected_way_id": prefix.way_id,
                "used_survey_km": float(prefix.survey_km),
                "unused_budget_km": float(budget - prefix.survey_km),
                "cumulative_usable_gain_km": float(prefix.usable_gain_km),
                "next_rank": following.tasks if following else None,
                "next_prefix_survey_km": float(following.survey_km) if following else None,
                "survey_rounding_half_width_km": float(
                    prefix.tasks * ROUNDING_HALF_UNIT if curve.distance_source != "cumulative_survey_km"
                    else (ROUNDING_HALF_UNIT if prefix.tasks else Decimal(0))),
            }
        comparisons.append({"budget_km": float(budget), "strategies": strategies})
    return upper, comparisons


def analyze_sources(root=ROOT, budgets=None, *, versioned=False):
    root = Path(root)
    paths = reporting_paths(root, versioned)
    if versioned:
        verify_corrected_greek(paths)
    census = paths["census"]
    model_version = CORRECTED_VERSION if versioned else MODEL_VERSION
    summary_path = census / "edit_targets_summary.csv"
    summary_rows = read_csv(summary_path)
    if len(summary_rows) != 1:
        raise ValueError("Expected exactly one targeting summary")
    summary = summary_rows[0]
    if summary.get("model_version") != model_version:
        raise ValueError("Targeting summary model version mismatch")
    if summary.get("intervention_model") != INTERVENTION_MODEL:
        raise ValueError("Targeting summary intervention model mismatch")
    if summary.get("selection_method") != "static_initial_consolidation":
        raise ValueError("Targeting summary selection strategy mismatch")
    threshold = nonnegative_decimal(summary.get("component_threshold_km"), "component_threshold_km")
    if not threshold:
        raise ValueError("Component threshold must be positive")
    before = nonnegative_decimal(summary.get("usable_km_before"), "usable_km_before")
    curves, strategies, artifacts = {}, {}, {}
    expected_tasks = positive_integer(summary.get("edits_committed"), "edits_committed")
    for key, (filename, mode, label) in SOURCES.items():
        path = census / filename
        curve = curve_from_rows(read_csv(path), key, model_version, mode,
                                usable_km_before=before,
                                require_intervention=mode == "per_row")
        if len(curve.points) != expected_tasks:
            raise ValueError(f"Target rows and summary disagree for {key}")
        prefix = "" if key == "static_initial_consolidation" else (
            "length_baseline_" if key == "length_only" else "efficiency_")
        summary_gain = nonnegative_decimal(summary.get(prefix + "usable_gain_km"), prefix + "usable_gain_km")
        if abs(summary_gain - curve.points[-1].usable_gain_km) > 2 * ROUNDING_HALF_UNIT:
            raise ValueError(f"Final usable gain and summary disagree for {key}")
        summary_distance = nonnegative_decimal(summary.get(prefix + "survey_km"), prefix + "survey_km")
        tolerance = ((expected_tasks + 1) * ROUNDING_HALF_UNIT if mode == "per_row"
                     else 2 * ROUNDING_HALF_UNIT)
        if abs(summary_distance - curve.final_survey_km) > tolerance:
            raise ValueError(f"Final survey extent and summary disagree for {key}")
        curves[key] = curve
        strategies[key] = {
            "label": label,
            "source_file": str(path.relative_to(root)),
            "recorded_tasks": len(curve.points),
            "recorded_survey_extent_km": float(curve.final_survey_km),
            "recorded_cumulative_usable_gain_km": float(curve.points[-1].usable_gain_km),
            "distance_source": curve.distance_source,
            "distance_processing": "cumulative sum of per-row values" if mode == "per_row" else "saved cumulative distance",
            "summary_survey_extent_km": float(summary_distance),
            "distance_minus_summary_km": float(curve.final_survey_km - summary_distance),
            "rounded_zero_distance_steps": sum(
                point.survey_km == (curve.points[i - 1].survey_km if i else Decimal(0))
                for i, point in enumerate(curve.points)),
            "intervention_provenance": ("row field and shared summary" if mode == "per_row"
                                        else "shared summary; no intervention field in this curve CSV"),
        }
        artifacts[key] = {"path": str(path.relative_to(root)),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    artifacts["summary"] = {"path": str(summary_path.relative_to(root)),
                            "sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest()}
    upper = min(curve.final_survey_km for curve in curves.values())
    if budgets is None:
        budgets = sorted({Decimal(0), upper, *(Decimal(x) for x in (25, 50, 100, 250, 400)
                                              if Decimal(x) < upper)})
    upper, comparisons = compare_prefixes(curves, budgets)
    report = {
        "schema_version": 1,
        "model_version": model_version,
        "profile_id": summary["profile"],
        "graph_scope": summary["graph_scope"],
        "component_threshold_km": float(threshold),
        "intervention_model": INTERVENTION_MODEL,
        "scenario": {
            "all_positive": True,
            "selected_way_states": "PASS",
            "node_transition_gates": "fixed",
            "interpretation": "Conditional way-state overrides, not literal tag-edit effects, verified physical accessibility or predicted realised survey yield.",
        },
        "definitions": {
            "survey_distance": "Sum of selected whole-way survey extents; overlapping geometry is not deduplicated. Not travel distance, elapsed time or measured effort.",
            "usable_gain": "Saved cumulative_usable_gain_km: total increase in thresholded eligible-network usable length, including qualifying selected and pre-existing mass.",
            "prefix_rule": "Longest complete ranked prefix whose recorded cumulative survey extent is at most the budget. No fractional ways, skipped unaffordable next ways, re-optimisation or extrapolation.",
            "common_range": "Zero to the minimum of the three recorded final survey extents.",
            "fairness": "Equal recorded survey distance is not equal task count or equal survey time, and the all-positive scenario is not realised yield.",
            "recovered_preexisting_limit": "Per-prefix qualifying selected mass is not saved. Recovered-pre-existing curves cannot be obtained by subtracting selected own/survey distance from total usable gain.",
        },
        "rounding": {
            "source_decimal_places": 3,
            "per_row_distance": "The main CSV contains per-row survey_km (own_km fallback), not cumulative distance. Decimal summation avoids binary-boundary errors but accumulates the exported 3-decimal rounding.",
            "cumulative_distance": "The other CSVs supply independently rounded cumulative_survey_km; these are used directly, not reconstructed by summing own_km.",
            "budget_boundary_limit": "Prefix membership is exact only for the recorded rounded values. A boundary or zero-distance tie may move at unrounded precision; ties do not denote genuinely free surveys.",
            "reported_half_widths": "Assuming nearest rounding, main prefix distance has at most n × 0.0005 km rounding error; a saved cumulative distance or gain has at most 0.0005 km. These are rounding bounds, not confidence intervals.",
        },
        "usable_km_before": float(before),
        "common_max_survey_km": float(upper),
        "common_budgets_km": [entry["budget_km"] for entry in comparisons],
        "source_artifacts": artifacts,
        "strategies": strategies,
        "comparisons": comparisons,
    }
    return report, curves


def write_comparison(root=ROOT, *, versioned=False):
    root = Path(root)
    report, curves = analyze_sources(root, versioned=versioned)
    paths = reporting_paths(root, versioned)
    output = paths["analysis"]
    output.mkdir(parents=True, exist_ok=True)
    (output / "targeting_distance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    rows = [{"budget_km": entry["budget_km"], "selection_method": key, **prefix}
            for entry in report["comparisons"] for key, prefix in entry["strategies"].items()]
    with (output / "targeting_distance.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    if versioned:
        write_versioned_manifest(
            paths, "targeting_distance",
            [paths["census"] / "run_manifest.json",
             *(paths["census"] / name for name in
               ("edit_targets_summary.csv", *(settings[0] for settings in SOURCES.values()))),
             paths["root"] / "analysis" / "targeting_distance.py"],
            [output / "targeting_distance.json", output / "targeting_distance.csv"],
            output)
    return report, curves


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--versioned", action="store_true",
                        help="Read the verified corrected Greek census; write only isolated unified outputs")
    result, _ = write_comparison(versioned=parser.parse_args().versioned)
    print(f"Saved complete-prefix comparisons through {result['common_max_survey_km']:,.3f} survey km")
    for strategy, prefix in result["comparisons"][-1]["strategies"].items():
        print(f"  {strategy}: {prefix['selected_tasks']:,} tasks; "
              f"{prefix['used_survey_km']:,.3f} survey km; "
              f"{prefix['cumulative_usable_gain_km']:,.3f} conditional usable km gained")
