#!/usr/bin/env python3
"""Describe three-point observations, not whole-way passability or network yield.

python -B analysis\\continuity_audit.py --reference-date 2026-09-15

Bounds concern responses at the three sampled locations in the inspected sample.
They are neither population confidence intervals nor physical-accessibility bounds.
"""
import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from local_audit import recent

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "validation" / "continuity_results.csv"
SAMPLE = ROOT / "results" / "census" / "continuity_sample.csv"
TARGETS = ROOT / "results" / "census" / "edit_targets.csv"
OUT = ROOT / "results" / "analysis" / "continuity_audit.json"
PRESENT = {"both", "left", "right", "present_side_unspecified"}
OUTCOMES = PRESENT | {"none", "unverifiable"}
BANDS = {"short": (0.0, 2.0), "medium": (2.0, 10.0), "long": (10.0, float("inf"))}
IDENTITY = (
    "case_id", "way_group", "osm_way_id", "point_index",
    "point_fraction", "band", "pool_n", "draw_n", "inclusion_probability",
    "worklist_rank", "highway", "stratum", "own_km", "lat", "lon",
    "geometry_source", "geometry_fetched_on", "current_sidewalk_tag",
)


def load(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate(results, sample, sample_sha256, targets):
    if not sample or not results or not targets:
        raise ValueError("Observations, the fixed sample and the worklist are required")
    if any(not set(IDENTITY).issubset(row) for row in sample):
        raise ValueError("Incomplete fixed-sample identity schema")
    expected = {row["case_id"]: row for row in sample}
    if len(expected) != len(sample):
        raise ValueError("Duplicate fixed-sample case identities")
    seen = set()
    for row in results:
        case_id = row.get("case_id")
        if case_id in seen:
            raise ValueError(f"Duplicate observation: {case_id}")
        seen.add(case_id)
        if case_id not in expected:
            raise ValueError(f"Observation is outside the fixed sample: {case_id}")
        if row.get("sample_sha256") != sample_sha256:
            raise ValueError(f"Sample fingerprint mismatch: {case_id}")
        if any(row.get(key) != expected[case_id].get(key) for key in IDENTITY):
            raise ValueError(f"Immutable observation identity mismatch: {case_id}")
        if row.get("schema_version") != "way-continuity-v1":
            raise ValueError(f"Unsupported observation schema: {case_id}")
        if row.get("completed") != "true":
            raise ValueError(f"Observation is incomplete: {case_id}")
        if row.get("sidewalk") not in OUTCOMES:
            raise ValueError(f"Invalid sidewalk response: {case_id}")
        for key in ("lat", "lon", "own_km", "point_fraction"):
            if not math.isfinite(float(row[key])):
                raise ValueError(f"Non-finite observation identity: {case_id}, {key}")
        if not (-90 <= float(row["lat"]) <= 90 and -180 <= float(row["lon"]) <= 180
                and float(row["own_km"]) >= 0):
            raise ValueError(f"Invalid observation geometry: {case_id}")
        if row["sidewalk"] == "unverifiable" and not row.get("unverifiable_reason"):
            raise ValueError(f"Unverifiable observation lacks a reason: {case_id}")
        dt.date.fromisoformat(row["audit_date"])
    if seen != set(expected):
        raise ValueError("Observations do not cover the complete fixed sample")

    worklist = {row["osm_way_id"]: row for row in targets}
    if len(worklist) != len(targets):
        raise ValueError("Duplicate ways in the source worklist")
    if (len({row["rank"] for row in targets}) != len(targets)
            or any(not math.isfinite(float(row["own_km"])) or float(row["own_km"]) < 0
                   for row in targets)):
        raise ValueError("Invalid worklist ranks or distances")
    ways = collections.defaultdict(list)
    for row in results:
        ways[row["way_group"]].append(row)
    if len({rows[0]["osm_way_id"] for rows in ways.values()}) != len(ways):
        raise ValueError("A sampled way occurs in multiple groups")
    populations = {
        band: sum(lo <= float(row["own_km"]) < hi for row in targets)
        for band, (lo, hi) in BANDS.items()
    }
    for group, rows in ways.items():
        rows.sort(key=lambda row: int(row["point_index"]))
        if [int(row["point_index"]) for row in rows] != [1, 2, 3]:
            raise ValueError(f"A way must have exactly three distinct points: {group}")
        if [float(row["point_fraction"]) for row in rows] != [.15, .5, .85]:
            raise ValueError(f"Unexpected observation locations: {group}")
        shared = ("osm_way_id", "band", "pool_n", "draw_n",
                  "inclusion_probability", "worklist_rank", "highway")
        if any(len({row[key] for row in rows}) != 1 for key in shared):
            raise ValueError(f"Inconsistent sampled-way identity: {group}")
        first = rows[0]
        target = worklist.get(first["osm_way_id"])
        if target is None or first["worklist_rank"] != target["rank"]:
            raise ValueError(f"Sampled way/rank is absent from the worklist: {group}")
        if first["highway"] != target["highway"]:
            raise ValueError(f"Worklist class differs from the sample: {group}")
        if first["band"] not in BANDS:
            raise ValueError(f"Invalid length band: {group}")
        pool = populations[first["band"]]
        draw = sum(rs[0]["band"] == first["band"] for rs in ways.values())
        probability = float(first["inclusion_probability"])
        if (int(first["pool_n"]) != pool or int(first["draw_n"]) != draw
                or pool < draw or draw < 1
                or not math.isfinite(probability)
                or abs(probability - draw / pool) > 1e-9):
            raise ValueError(f"Sampling allocation/probability mismatch: {group}")
    return ways, worklist, populations


def eligibility(row, reference, years):
    if row["sidewalk"] == "unverifiable":
        return "unverifiable"
    value = row.get("capture_year", "")
    if not value:
        return "undated"
    if len(value) != 4 or not value.isdigit() or not 1 <= int(value) <= 9999:
        return "invalid_capture_year"
    year = int(value)
    if year > reference.year:
        return "future_capture_year"
    if year < reference.year - years:
        return "stale"
    if not recent({"capture_date": value}, reference, years):
        return "year_overlaps_age_cutoff"
    return "eligible"


def response(row, reference=None, years=5):
    if row["sidewalk"] == "unverifiable":
        return None
    if reference is not None and eligibility(row, reference, years) != "eligible":
        return None
    return int(row["sidewalk"] in PRESENT)


def agreement(groups, reference=None, years=5):
    complete = [(group, rows) for group, rows in groups.items()
                if all(response(row, reference, years) is not None for row in rows)]
    unanimous = [(group, rows) for group, rows in complete
                 if len({response(row, reference, years) for row in rows}) == 1]
    first_positive = [(group, rows) for group, rows in complete
                      if response(rows[0], reference, years) == 1]
    all_positive = [(group, rows) for group, rows in first_positive
                    if all(response(row, reference, years) == 1 for row in rows)]
    return {
        "ways_with_all_points_observed": len(complete),
        "unanimous_presence_ways": len(unanimous),
        "unanimous_presence_pct": 100 * len(unanimous) / len(complete) if complete else None,
        "first_point_positive_ways": len(first_positive),
        "all_sampled_points_positive_ways": len(all_positive),
        "mixed_ways": [
            {"way_group": group, "osm_way_id": rows[0]["osm_way_id"],
             "own_km": float(rows[0]["own_km"]), "band": rows[0]["band"],
             "points": [row["sidewalk"] for row in rows]}
            for group, rows in complete if (group, rows) not in unanimous
        ],
    }


def point_bounds(groups, reference=None, years=5):
    if not groups:
        return None
    any_lower = any_upper = all_lower = all_upper = 0
    for rows in groups:
        values = [response(row, reference, years) for row in rows]
        any_lower += 1 in values
        any_upper += 1 in values or None in values
        all_lower += all(value == 1 for value in values)
        all_upper += 0 not in values
    n = len(groups)
    return {
        "sampled_ways": n,
        "any_sampled_point_positive": [any_lower / n, any_upper / n],
        "all_sampled_points_positive": [all_lower / n, all_upper / n],
        "interpretation": "Missing-response bounds within this inspected sample; "
                          "not population confidence limits or whole-way continuity.",
    }


def analyse(results, sample, sample_sha256, targets, reference, years=5):
    if not isinstance(years, int) or not 0 <= years < reference.year:
        raise ValueError("Maximum imagery age must be a nonnegative valid calendar interval")
    ways, worklist, populations = validate(results, sample, sample_sha256, targets)
    mismatches = [
        {"way_group": group, "osm_way_id": rows[0]["osm_way_id"],
         "question": worklist[rows[0]["osm_way_id"]]["missing"]}
        for group, rows in ways.items()
        if "sidewalk" not in worklist[rows[0]["osm_way_id"]]["missing"].lower()
    ]
    mismatch_ids = {row["way_group"] for row in mismatches}
    aligned = {group: rows for group, rows in ways.items() if group not in mismatch_ids}
    statuses = collections.Counter(eligibility(row, reference, years) for row in results)
    outcomes = collections.Counter(row["sidewalk"] for row in results)
    per_band, no_imagery = {}, {}
    for band in BANDS:
        groups = [rows for rows in ways.values() if rows[0]["band"] == band]
        points = [row for row in results if row["band"] == band]
        unobserved = sum(row["sidewalk"] == "unverifiable" for row in points)
        partial = sum(any(row["sidewalk"] == "unverifiable" for row in rows)
                      and any(row["sidewalk"] != "unverifiable" for row in rows)
                      for rows in groups)
        per_band[band] = {
            "worklist_ways": populations[band],
            "worklist_share_pct": 100 * populations[band] / len(targets),
            "sampled_ways": len(groups),
            "ways_with_any_observed_point": sum(any(row["sidewalk"] != "unverifiable"
                                                   for row in rows) for rows in groups),
            "ways_positive_at_any_observed_point": sum(any(row["sidewalk"] in PRESENT
                                                           for row in rows) for rows in groups),
            "partly_observed_ways": partial,
            "raw_sample_point_bounds": point_bounds(groups),
            "eligible_sample_point_bounds": point_bounds(groups, reference, years),
        }
        no_imagery[band] = {
            "points": len(points), "unverifiable": unobserved,
            "pct": 100 * unobserved / len(points) if points else None,
        }
    return {
        "schema": "way-point-observations-v2",
        "observation_schema": "way-continuity-v1",
        "sample_sha256": sample_sha256,
        "reference_date": reference.isoformat(),
        "max_image_age_years": years,
        "temporal_rule": "Exact five-year cutoff for the declared reference date; "
                         "a year-only record qualifies only when January 1 is not before "
                         "the cutoff and the year is not future. Year precision does not "
                         "establish contemporaneous conditions.",
        "ways_sampled": len(ways), "observations": len(results),
        "outcomes": dict(outcomes),
        "no_imagery_points": outcomes.get("unverifiable", 0),
        "no_imagery_pct": 100 * outcomes.get("unverifiable", 0) / len(results),
        "no_imagery_by_band": no_imagery,
        "raw_point_agreement": agreement(ways),
        "eligible_point_agreement": agreement(ways, reference, years),
        "sampling": {"by_band": per_band, "worklist_ways": len(targets)},
        "temporal_status_counts": dict(statuses),
        "task_scope_mismatches": mismatches,
        "task_aligned_sampled_ways": len(aligned),
        "task_aligned_raw_point_bounds": point_bounds(list(aligned.values())),
        "task_aligned_eligible_point_bounds": point_bounds(list(aligned.values()), reference, years),
        "inference": {
            "unit": "one way with three inspected locations",
            "whole_way_continuity_established": False,
            "population_prevalence_estimated": False,
            "network_gain_estimated": False,
            "note": "Sample point-response agreement and bounds cannot be multiplied "
                    "by network gain. The three points are not independent ways; "
                    "a positive point need not resolve the worklist's actual task.",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="?", type=Path, default=RESULTS)
    parser.add_argument("--sample", type=Path, default=SAMPLE)
    parser.add_argument("--targets", type=Path, default=TARGETS)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--reference-date", help="Defaults to the latest recorded audit date, not today")
    parser.add_argument("--max-image-age-years", type=int, default=5)
    args = parser.parse_args()
    results = load(args.csv)
    if not results:
        raise ValueError("No recorded observations")
    reference = (dt.date.fromisoformat(args.reference_date) if args.reference_date else
                 max(dt.date.fromisoformat(row["audit_date"]) for row in results))
    report = analyse(results, load(args.sample), digest(args.sample), load(args.targets),
                     reference, args.max_image_age_years)
    report["sources"] = {}
    for name, path in (("observations", args.csv), ("sample", args.sample),
                       ("worklist", args.targets), ("analysis_code", Path(__file__)),
                       ("temporal_rules", ROOT / "analysis" / "local_audit.py")):
        resolved = path.resolve()
        location = "project_relative"
        try:
            label = str(resolved.relative_to(ROOT))
        except ValueError:
            label, location = resolved.name, "external_input_basename"
        report["sources"][name] = {"file": label, "location": location, "sha256": digest(path)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}: {report['ways_sampled']} ways, {report['observations']} points; no network-yield estimate.")


if __name__ == "__main__":
    main()
