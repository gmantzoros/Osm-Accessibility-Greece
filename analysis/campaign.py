#!/usr/bin/env python3
"""Export a national, all-positive survey scenario; no measured cost is inferred.

Usage: python analysis/campaign.py [top_n]
       python analysis/campaign.py --versioned [top_n]
Targets retain their national ordering. Each answer concerns the entire OSM way,
not merely the displayed midpoint. Desk-time assumptions exclude travel, imagery
availability, failed inspection, functionality tests and quality assurance.
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter
from statistics import median

from aggregate import reporting_paths, verify_corrected_greek, write_versioned_manifest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENSUS = os.path.join(ROOT, "results", "census")
TARGETS = os.path.join(CENSUS, "edit_targets.csv")
SUMMARY = os.path.join(CENSUS, "edit_targets_summary.csv")
OUT = os.path.join(ROOT, "results", "campaign")
DESK_MINUTES_SCENARIOS = (2, 10, 30)

QUEST = {
    "does this street have a sidewalk?": ("sidewalk", "both / left / right / no / separate"),
    "does this crossing have tactile paving or an acoustic signal?":
        ("tactile_paving + traffic_signals:sound", "yes / no; sound operation requires field evidence"),
    "do these steps have a handrail?": ("handrail", "yes / no"),
}

INSTRUCTIONS = """\
**Survey the entire OSM way before adding an accessibility assertion.**

{{missing}}

Suggested tag: `{{tag}}`; values: {{values}}.
The midpoint is a locator, not the extent of the task. The survey extent is {{survey_km}} km
long. Inspect its full relevant extent, split it where conditions change, and
skip uncertain or unobservable questions. Static imagery cannot verify sound
operation or safe passage.

National rank: {{rank}}. If this way and all earlier selected ways resolve
to PASS in the idealised way-state scenario, its marginal eligible-network
change is {{unlocked_km}} km. Existing node transitions remain fixed. This is
not a simulation of a literal tag edit or its cascading effects, and is not
verified accessibility or a recommendation to assert a favourable value.

Use a ground survey or imagery whose current terms permit deriving OSM edits.
Do not derive OSM data from Google Maps, Street View or Google Earth.
"""


def load(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def marginal_gain(row):
    return float(row["marginal_usable_km"] if "marginal_usable_km" in row else row["unlocked_km"])


def cumulative_gain(row):
    return float(row["cumulative_usable_gain_km"] if "cumulative_usable_gain_km" in row else row["cumulative_km"])


def intervention_metadata(summary):
    model = summary.get("intervention_model")
    if model != "way_state_override_fixed_node_transitions":
        raise ValueError(f"Missing or unsupported targeting intervention model: {model}")
    return {
        "intervention_model": model,
        "assumption": "Selected way states are set to PASS while existing node transitions remain fixed.",
        "interpretation": "Idealised positive way-state scenario, not literal tag edits, cascading evidence propagation, or predicted realised accessibility.",
    }


def length_baseline_summary(summary):
    return {
        "usable_km_before": float(summary["length_baseline_usable_before"]),
        "usable_km_after": float(summary["length_baseline_usable_after"]),
        "usable_gain_km": float(summary["length_baseline_usable_gain_km"]),
        "survey_km": float(summary["length_baseline_survey_km"]),
        "edits_committed": int(summary["edits_committed"]),
        "selection_method": "length_only",
        "intervention_model": summary.get("intervention_model"),
        "curve_file": "edit_targets_length_baseline.csv",
    }


def validate_target_curve(targets, summary):
    """Reject old component-consolidation scores masquerading as usable gain."""
    cumulative = 0.0
    for i, row in enumerate(targets, 1):
        if int(row["rank"]) != i:
            raise ValueError("Target ranking must be consecutive from one")
        gain = marginal_gain(row)
        if gain < 0:
            raise ValueError("Negative all-positive usable gain")
        cumulative += gain
        # CSV values are rounded independently to three decimals.
        if abs(cumulative - cumulative_gain(row)) > i * 0.00051 + 0.001:
            raise ValueError("Target marginal and cumulative gains disagree")
    actual = float(summary["usable_km_after"]) - float(summary["usable_km_before"])
    exported = cumulative_gain(targets[-1]) if targets else 0.0
    if abs(exported - actual) > 0.005:
        raise ValueError("Target curve is not actual usable gain; rerun corrected targeting")
    if int(summary["edits_committed"]) != len(targets):
        raise ValueError("Target rows and summary belong to different runs")
    if targets and "usable_km_after" in targets[-1]:
        if abs(float(targets[-1]["usable_km_after"]) - float(summary["usable_km_after"])) > 0.005:
            raise ValueError("Per-target and summary usable endpoints use different outcomes")


def objective_target_rows(rows):
    """Expose actual objective gain under compatibility keys; preserve raw scores."""
    output = []
    for row in rows:
        mapped = dict(row)
        mapped.setdefault("survey_km", row["own_km"])
        if "marginal_usable_km" in row:
            mapped["legacy_unlocked_km"] = row.get("unlocked_km")
            mapped["legacy_cumulative_km"] = row.get("cumulative_km")
            mapped["unlocked_km"] = row["marginal_usable_km"]
            mapped["cumulative_km"] = row["cumulative_usable_gain_km"]
        output.append(mapped)
    return output


def main(top_n=5000, *, versioned=False, root=ROOT):
    top_n = int(top_n)
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    paths = reporting_paths(root, versioned)
    if versioned:
        verify_corrected_greek(paths)
    CENSUS = os.fspath(paths["census"])
    TARGETS = os.path.join(CENSUS, "edit_targets.csv")
    SUMMARY = os.path.join(CENSUS, "edit_targets_summary.csv")
    OUT = os.fspath(paths["campaign"])
    all_targets, summary = load(TARGETS), load(SUMMARY)[0]
    validate_target_curve(all_targets, summary)
    intervention = intervention_metadata(summary)
    all_targets = objective_target_rows(all_targets)
    targets = all_targets[:top_n]
    if not targets:
        raise ValueError("No target candidates selected")
    os.makedirs(OUT, exist_ok=True)
    before = float(summary["usable_km_before"])

    with open(os.path.join(OUT, "maproulette_tasks.geojson"), "w", encoding="utf-8") as fh:
        for row in targets:
            tag, values = QUEST.get(row["missing"], ("inspect profile-specific evidence", "do not guess"))
            properties = {**row, "tag": tag, "values": values, "scope": "national",
                          "gain_interpretation": "conditional marginal profile-supported usable km"}
            feature = {"type": "FeatureCollection", "features": [{
                "type": "Feature", "geometry": {"type": "Point",
                    "coordinates": [float(row["lon"]), float(row["lat"])]},
                "properties": properties}]}
            fh.write(json.dumps(feature, ensure_ascii=False) + "\n")
    config = {
        "name": "Greek accessibility: national survey candidates",
        "description": "Whole-way survey candidates ranked by an idealised positive way-state scenario with fixed node transitions; not literal tag-edit impacts.",
        "instruction": INSTRUCTIONS, "checkinComment": "Survey pedestrian accessibility evidence",
        "checkinSource": "survey / appropriately licensed imagery", "difficulty": 3,
        "defaultZoom": 18, "minZoom": 16, "maxZoom": 19,
        "tags": "accessibility,sidewalk,pedestrian,greece", "updateTasks": False,
    }
    with open(os.path.join(OUT, "maproulette_challenge.json"), "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)

    costs = []
    budgets = sorted({n for n in (100, 250, 500, 1000, 2500, 5000, len(targets)) if n <= len(targets)})
    for n in budgets:
        gain = float(targets[n - 1]["cumulative_km"])
        own_km = sum(float(r["own_km"]) for r in targets[:n])
        survey_km = sum(float(r.get("survey_km", r["own_km"])) for r in targets[:n])
        for minutes in DESK_MINUTES_SCENARIOS:
            hours = n * minutes / 60
            costs.append({
                "edits": n, "km_unlocked": gain, "usable_km_before": before,
                "selected_ways": n,
                "usable_km_after": before + gain, "selected_own_km": own_km,
                "selected_survey_km": survey_km,
                "desk_minutes_per_task": minutes, "person_hours": round(hours, 2),
                "working_days": round(hours / 8, 2), "person_months": round(hours / 160, 3),
                "km_per_hour": round(gain / hours, 3),
                "intervention_model": intervention["intervention_model"],
                "scenario": "positive way-state overrides; fixed node transitions; assumed desk time, not measured cost",
            })
    write_csv(os.path.join(OUT, "campaign_cost.csv"), costs, list(costs[0]))

    place_rows = {}
    for row in targets:
        label = row["stratum"] or "(unmatched)"
        item = place_rows.setdefault(label, {"place": label, "tasks": 0, "km_unlocked": 0.0,
                                             "selected_own_km": 0.0})
        item["tasks"] += 1
        item["km_unlocked"] += float(row["unlocked_km"])
        item["selected_own_km"] += float(row["own_km"])
    write_csv(os.path.join(OUT, "campaign_by_place.csv"),
              sorted(place_rows.values(), key=lambda r: -r["km_unlocked"]),
              ["place", "tasks", "km_unlocked", "selected_own_km"])
    lau_ids = {r.get("lau_stratum", "") for r in targets} - {""}
    lengths = [float(r["own_km"]) for r in targets]
    gain = float(targets[-1]["cumulative_km"])
    metadata = {
        "scope": "national", "urban_intersection_applied": False,
        "tasks": len(targets), "named_place_labels": len(place_rows),
        "unique_laus": len(lau_ids) if "lau_stratum" in targets[0] else None,
        "place_note": "Place labels can mix named circles and LAUs; not counts of municipalities.",
        "objective": "marginal thresholded eligible-core usable km under the configured graph scope",
        "selection_method": targets[0].get("selection_method", summary.get("selection_method", "unspecified")),
        **intervention,
        "usable_km_before": before, "usable_km_after": before + gain, "usable_gain_km": gain,
        "selected_own_km": sum(lengths), "median_own_km": median(lengths), "max_own_km": max(lengths),
        "selected_survey_km": sum(float(r.get("survey_km", r["own_km"])) for r in targets),
        "selected_eligible_own_km": (sum(float(r["eligible_own_km"]) for r in targets)
                                    if all("eligible_own_km" in r for r in targets) else None),
        "survey_distance_note": "Sum of selected way lengths, not a travel itinerary or measured effort.",
        "desk_minutes_scenarios": list(DESK_MINUTES_SCENARIOS),
        "cost_excludes": ["travel", "failed inspections", "imagery availability", "whole-way survey time",
                          "quality assurance", "functional acoustic testing"],
        "question_mix": dict(Counter(r["missing"] for r in targets)),
    }
    with open(os.path.join(OUT, "campaign_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2)
    print(f"Wrote national scenario: {len(targets):,} tasks; {gain:,.3f} conditional usable km gained.")
    print(f"Selected ways total {sum(lengths):,.3f} km; desk-time scenarios are assumptions, not measured costs.")
    if versioned:
        write_versioned_manifest(
            paths, "campaign",
            [paths["census"] / "run_manifest.json", TARGETS, SUMMARY,
             paths["root"] / "analysis" / "campaign.py"],
            [paths["campaign"] / name for name in
             ("campaign_cost.csv", "campaign_by_place.csv", "campaign_summary.json",
              "maproulette_challenge.json", "maproulette_tasks.geojson")],
            paths["campaign"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("top_n", nargs="?", type=int, default=5000)
    parser.add_argument("--versioned", action="store_true",
                        help="Read the verified corrected Greek census; write only isolated unified outputs")
    args = parser.parse_args()
    main(args.top_n, versioned=args.versioned)
