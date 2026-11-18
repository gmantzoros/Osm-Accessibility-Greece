#!/usr/bin/env python3
"""GISCO population-field-weighted summaries of assigned LAU model outputs.

Residential location is not individual accessibility. The historical POP_2021
field is retained; its Greek total matches revised 2011, not the 2021 census.
LAUs lacking a modelled network or positive population are explicitly excluded.

Usage:  python analysis/exposure.py
        python analysis/exposure.py --versioned
"""

import argparse
import csv
import json
import os
import sys
from aggregate import reporting_paths, verify_corrected_greek, write_versioned_manifest
from cross_country import eligible_endpoints

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENSUS = os.path.join(ROOT, "results", "census")
OUT = os.path.join(ROOT, "results", "analysis")
LOOKUP = os.path.join(ROOT, "data", "boundaries", "municipalities.csv")

# Pessimistic profile-supported share of the walkable network, in bands.
BANDS = [
    (0.0, 1.0, "under 1%"),
    (1.0, 5.0, "1-5%"),
    (5.0, 10.0, "5-10%"),
    (10.0, 25.0, "10-25%"),
    (25.0, 101.0, "25% or more"),
]


def load(path):
    if not os.path.isfile(path):
        sys.exit(f"Missing {path}")
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main(*, versioned=False, root=ROOT):
    paths = reporting_paths(root, versioned)
    if versioned:
        verify_corrected_greek(paths)
    CENSUS = os.fspath(paths["census"])
    OUT = os.fspath(paths["analysis"])
    LOOKUP = os.fspath(paths["root"] / "data" / "boundaries" / "municipalities.csv")
    feas = {r["stratum"]: r for r in load(os.path.join(CENSUS, "feasibility.csv"))}
    cross = {r["stratum"]: r for r in load(os.path.join(CENSUS, "crossing_metrics.csv"))}
    lookup = load(LOOKUP)
    os.makedirs(OUT, exist_ok=True)

    rows = []
    exclusions = {"missing_feasibility": [], "nonpositive_population": [],
                  "no_network": [], "no_eligible_network": []}
    for m in lookup:
        s = m["stratum"]
        f = feas.get(s)
        pop = num(m["pop_2021"])
        if not f:
            exclusions["missing_feasibility"].append(m)
            continue
        if pop <= 0:
            exclusions["nonpositive_population"].append(m)
            continue
        walk = num(f["walkable_km"])
        if walk <= 0:
            exclusions["no_network"].append(m)
            continue
        primary = eligible_endpoints(f)
        if primary["eligible_km"] <= 0:
            exclusions["no_eligible_network"].append(m)
            continue
        c = cross.get(s, {})
        rows.append({
            "stratum": s, "lau_name": m["lau_name"], "dimos_code": m["dimos_code"],
            "degurba_label": m["degurba_label"], "pop_2021": pop,
            "walkable_km": walk, "eligible_km": primary["eligible_km"],
            "pess_usable_pct": primary["pess_usable_pct"],
            "opt_usable_pct": primary["opt_usable_pct"], "gap_pct": primary["gap_pct"],
            "all_length_pess_usable_pct": num(f["pess_usable_pct"]),
            "all_length_gap_pct": num(f["decidability_gap_pct"]),
            "unknown_pct": num(f["unknown_km"]) / walk * 100,
            "crossings": num(c.get("crossings", 0)),
            "tactile": num(c.get("crossing_tactile", 0)),
            "sound": num(c.get("signal_sound", 0)),
            "sound_affirmative": num(c.get("signal_sound_yes", 0)),
            "crossing_sound": float(c["crossing_sound"]),
            "crossing_sound_affirmative": float(c["crossing_sound_yes"]),
            "tactile_affirmative": num(c.get("crossing_tactile_yes", 0)),
        })

    total_pop = sum(r["pop_2021"] for r in rows)
    if not rows or total_pop <= 0:
        raise ValueError("No LAUs with positive population and modelled network")
    metadata = {
        "unit": "LAU/local community, not whole dimos or individual resident",
        "population_source_field": "GISCO LAU 2021 POP_2021 (CSV alias pop_2021)",
        "population_reference_year": "unverified per LAU; Greek sum and Athens match revised 2011",
        "included_laus": len(rows), "included_population_field_sum": total_pop,
        "lookup_population_field_sum": sum(num(m["pop_2021"]) for m in lookup),
        "exclusions": {k: {"laus": len(v), "population_field_sum": sum(num(m["pop_2021"]) for m in v)}
                       for k, v in exclusions.items()},
        "interpretation": "GISCO population-field-weighted mapped-evidence indicators; not current exposure",
        "primary_denominator": "eligible nonauxiliary length",
        "component_threshold_basis": "eligible component mass, not total auxiliary-inclusive mass",
        "state_percentages_denominator": "all graph way length; zero-length node uncertainty reported separately",
        "legacy_metric_labels": {
            "pop_no_crossings": "population field in included LAUs with no mapped crossing records",
            "pop_no_tactile": "population field in included LAUs without crossing tactile statements",
            "pop_no_sound": "population field in included LAUs without signal-node sound statements",
            "pop_guaranteed_under_5pct": "population field in included LAUs below 5% pessimistic profile-supported share",
            "pop_guaranteed_under_1pct": "population field in included LAUs below 1% pessimistic profile-supported share",
        },
    }
    with open(os.path.join(OUT, "exposure_metadata.json"), "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)
    print(f"{len(rows):,} LAUs; included GISCO population-field sum {total_pop:,.0f}")

    # ---------------------------------------------------------------- bands
    band_rows = []
    print("\nGISCO population field by pessimistic profile-supported share:")
    for lo, hi, label in BANDS:
        sel = [r for r in rows if lo <= r["pess_usable_pct"] < hi]
        pop = sum(r["pop_2021"] for r in sel)
        band_rows.append({
            "band": label, "lau_count": len(sel), "pop_2021": pop,
            "pop_share_pct": 100 * pop / total_pop if total_pop else 0,
            "walkable_km": sum(r["walkable_km"] for r in sel),
            "eligible_km": sum(r["eligible_km"] for r in sel),
        })
        print(f"  {label:14} {len(sel):>5} LAUs  {pop:>12,.0f} people  "
              f"({100 * pop / total_pop if total_pop else 0:5.1f}%)")

    # ------------------------------------------------- headline exposure facts
    def pop_where(pred):
        return sum(r["pop_2021"] for r in rows if pred(r))

    facts = {
        "pop_total": total_pop,
        "pop_no_crossings": pop_where(lambda r: r["crossings"] == 0),
        "pop_no_tactile": pop_where(lambda r: r["tactile"] == 0),
        "pop_no_sound": pop_where(lambda r: r["sound"] == 0),
        "pop_no_affirmative_sound": pop_where(lambda r: r["sound_affirmative"] == 0),
        "pop_no_affirmative_tactile": pop_where(lambda r: r["tactile_affirmative"] == 0),
        "pop_no_crossing_sound_statement": pop_where(lambda r: r["crossing_sound"] == 0),
        "pop_no_affirmative_crossing_sound": pop_where(lambda r: r["crossing_sound_affirmative"] == 0),
        "pop_guaranteed_under_5pct": pop_where(lambda r: r["pess_usable_pct"] < 5),
        "pop_guaranteed_under_1pct": pop_where(lambda r: r["pess_usable_pct"] < 1),
        "pop_gap_over_90": pop_where(lambda r: r["gap_pct"] > 90),
    }
    facts["pop_no_sound_statement"] = facts["pop_no_sound"]
    facts["pop_no_tactile_statement"] = facts["pop_no_tactile"]
    facts["pop_pessimistic_under_5pct"] = facts["pop_guaranteed_under_5pct"]
    facts["pop_pessimistic_under_1pct"] = facts["pop_guaranteed_under_1pct"]
    print("\nHeadline exposure:")
    for k, v in facts.items():
        if k == "pop_total":
            continue
        label = metadata["legacy_metric_labels"].get(k, k)
        print(f"  {label}: {v:>12,.0f}  ({100 * v / total_pop:5.1f}%)")

    # Legacy CSV keys are retained for downstream compatibility, not terminology.
    pw = sum(r["pess_usable_pct"] * r["pop_2021"] for r in rows) / total_pop
    aw = sum(r["pess_usable_pct"] * r["eligible_km"] for r in rows) / sum(r["eligible_km"] for r in rows)
    facts["pop_weighted_guaranteed_pct"] = pw
    facts["km_weighted_guaranteed_pct"] = aw
    facts["pop_weighted_pessimistic_pct"] = pw
    facts["km_weighted_pessimistic_pct"] = aw
    print(f"\n  GISCO-field-weighted pessimistic share: {pw:.2f}%")
    print(f"  km-weighted pessimistic share: {aw:.2f}%")

    # ------------------------------------------------------------------ write
    with open(os.path.join(OUT, "exposure_bands.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(band_rows[0].keys()))
        w.writeheader()
        w.writerows(band_rows)
    with open(os.path.join(OUT, "exposure_facts.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "value"])
        for k, v in facts.items():
            w.writerow([k, round(v, 3)])
    rows.sort(key=lambda r: -r["pop_2021"])
    with open(os.path.join(OUT, "exposure_by_lau.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote exposure_bands.csv, exposure_facts.csv, exposure_by_lau.csv to {OUT}")
    if versioned:
        write_versioned_manifest(
            paths, "exposure",
            [paths["census"] / "run_manifest.json", *(paths["census"] / name for name in
              ("feasibility.csv", "crossing_metrics.csv")),
             LOOKUP, paths["root"] / "analysis" / "exposure.py"],
            [paths["analysis"] / name for name in
             ("exposure_metadata.json", "exposure_bands.csv", "exposure_facts.csv",
              "exposure_by_lau.csv")],
            paths["analysis"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--versioned", action="store_true",
                        help="Read the verified corrected Greek census; write only isolated unified outputs")
    main(versioned=parser.parse_args().versioned)
