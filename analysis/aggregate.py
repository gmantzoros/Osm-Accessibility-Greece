#!/usr/bin/env python3
"""Sum assigned LAU records by dimos prefix or urbanisation class.

These rollups are not exact partitions of the national extract. Coarse polygon
layers assign representative feature points independently and can leave records unmatched.
Region and NUTS3 outputs come from their own layers, not from LAU aggregation.

Two rules this script exists to enforce:

1.  Percentages are recomputed from summed numerators and denominators, never
    averaged. The mean of per-LAU tactile percentages is not the tactile
    percentage of the area: it weights a village with three crossings the same
    as Athens with nine thousand. This is the single most common aggregation
    error in completeness papers.

2.  Units below a size threshold are reported as `thin` rather than silently
    included in rankings. 2,039 Greek LAUs have under 200 inhabitants; a LAU
    with 4 crossings can show 100% or 0% tactile coverage and neither number
    is sufficient for a stable ranking.

Usage:  python analysis/aggregate.py [results/census]
        python analysis/aggregate.py --versioned
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from corrected_model_impact import CORRECTED_VERSION, IMPACT_DIR, check_manifest, digest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOOKUP = os.path.join(ROOT, "data", "boundaries", "municipalities.csv")
UNIFIED_DIR = Path("results/impact/unified-conditional-2026-10-09")


def reporting_paths(root=ROOT, versioned=False):
    root = Path(root).resolve()
    results = root / "results"
    base = root / UNIFIED_DIR if versioned else results
    paths = {
        "root": root,
        "versioned": versioned,
        "base": base,
        "census": root / IMPACT_DIR if versioned else results / "census",
        "analysis": base / "analysis",
        "urban": base / "urban",
        "campaign": base / "campaign",
        "countries": base / "countries",
        "profiles": base / "profiles" if versioned else results / "profile_sensitivity",
        "bundle": base / "paper_data.json",
        "figures": base / "paper" / "figures" if versioned else root / "paper" / "figures",
        "paper": base / "paper" if versioned else root / "paper",
    }
    if versioned:
        for name in ("base", "census", "analysis", "urban", "campaign",
                     "countries", "profiles", "bundle", "paper", "figures"):
            path = paths[name]
            if path.resolve() != path:
                raise ValueError(f"Refusing a linked corrected reporting path: {path}")
    return paths


def verify_corrected_greek(paths):
    """Require the recorded full-strata run, its unchanged inputs and corrected rows."""
    if not paths["versioned"]:
        raise ValueError("Corrected Greece verification requires --versioned")
    census = paths["census"]
    manifest_path = census / "run_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Missing corrected Greece run manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    check_manifest(paths["root"], manifest, census)
    required = {
        "summary.csv", "crossing_metrics.csv", "way_metrics.csv", "destinations.csv",
        "provenance.csv", "tag_values.csv", "feasibility.csv", "feasibility_global.csv",
        "feasibility_sensitivity.csv", "edit_targets.csv", "edit_targets_summary.csv",
        "edit_targets_efficiency.csv", "edit_targets_length_baseline.csv", "spatial_coverage.csv",
    }
    if not required.issubset(manifest["output_sha256"]):
        raise ValueError(f"Corrected Greece run lacks downstream files: {sorted(required - set(manifest['output_sha256']))}")
    for name in ("summary.csv", "feasibility.csv", "feasibility_global.csv",
                 "edit_targets.csv", "edit_targets_summary.csv", "feasibility_sensitivity.csv"):
        with (census / name).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if not rows or any(row.get("model_version") != CORRECTED_VERSION for row in rows):
            raise ValueError(f"Corrected Greece has missing or mixed model versions: {name}")
    with (census / "feasibility.csv").open(encoding="utf-8-sig", newline="") as stream:
        national = next((r for r in csv.DictReader(stream) if r["stratum"] == "NATIONAL"), None)
    if (national is None or national.get("profile") != "bvi-v0"
            or national.get("graph_scope") != "extended"
            or float(national["component_threshold_km"]) != 1):
        raise ValueError("Corrected Greece national result has inconsistent model settings")
    return manifest


def _relative(paths, path):
    root, path = paths["root"], Path(path).resolve()
    if path == root or root not in path.parents:
        raise ValueError(f"Reporting source/output is outside the project: {path}")
    return path.relative_to(root).as_posix()


def _fingerprints(paths, files):
    return {_relative(paths, path): digest(Path(path)) for path in files}


def write_versioned_manifest(paths, producer, sources, outputs, folder):
    """Link each versioned derivative to its exact source bytes after writing."""
    if not paths["versioned"] or producer not in ("aggregate", "exposure", "urban", "campaign", "targeting_distance"):
        raise ValueError("A versioned producer and output directory are required")
    folder = Path(folder).resolve()
    if folder not in (paths["analysis"], paths["urban"], paths["campaign"]):
        raise ValueError("Refusing to write a downstream manifest outside the versioned bundle")
    if any(Path(path).resolve().parent != folder for path in outputs):
        raise ValueError("Downstream outputs must stay in their versioned directory")
    manifest = {
        "schema_version": 1,
        "model_version": CORRECTED_VERSION,
        "producer": producer,
        "source_sha256": _fingerprints(paths, sources),
        "output_sha256": _fingerprints(paths, outputs),
    }
    (folder / f"{producer}_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_versioned_manifest(paths, producer, sources, names, folder):
    folder = Path(folder).resolve()
    if not paths["versioned"] or folder not in (paths["analysis"], paths["urban"], paths["campaign"]):
        raise ValueError("Refusing to read downstream data from a legacy directory")
    path = folder / f"{producer}_manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"Missing corrected downstream manifest: {path}")
    actual = json.loads(path.read_text(encoding="utf-8-sig"))
    expected_inputs = _fingerprints(paths, sources)
    expected_outputs = _fingerprints(paths, (folder / name for name in names))
    if (actual.get("schema_version") != 1 or actual.get("model_version") != CORRECTED_VERSION
            or actual.get("producer") != producer
            or actual.get("source_sha256") != expected_inputs
            or actual.get("output_sha256") != expected_outputs):
        raise ValueError(f"Stale or mixed corrected downstream outputs: {producer}")
    return actual

# A unit below both thresholds is flagged `thin` and excluded from rankings.
MIN_DRIVABLE_KM = 20.0
MIN_INTERSECTIONS = 50

# Summable columns from the census outputs.
SUM_COLS = [
    "drivable_km", "pedestrian_km", "separate_sidewalk_km",
    "crossings", "intersections", "signals",
    "junctions_degree3",
]
CROSSING_SUM_COLS = [
    "crossings", "schema_node_only", "schema_way_only", "schema_both",
    "crossing_subtype", "markings_any", "markings_modern", "markings_legacy",
    "markings_marked", "crossing_island", "crossing_island_yes",
    "crossing_tactile", "crossing_tactile_yes", "crossing_button",
    "crossing_signalised", "crossing_kerb", "crossing_kerb_accessible",
    "crossing_kerb_barrier", "signal_nodes", "signal_sound", "signal_sound_yes",
    "signal_vibration", "signal_vibration_yes", "kerb_nodes", "intersections",
    "junctions_degree3", "crossing_conflicts",
    "crossing_sound", "crossing_sound_yes",
    "legacy_crossing_nodes", "legacy_crossing_subtype", "legacy_crossing_tactile",
]


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_lookup(path=LOOKUP):
    if not os.path.isfile(path):
        sys.exit(f"Missing {path} - run: python data/prepare_boundaries.py")
    return {r["stratum"]: r for r in read_csv(path)}


def km_pct(way_rows, stratum_filter):
    """Length-weighted tag percentages, summed then divided."""
    tot = defaultdict(float)
    for r in way_rows:
        if r["class_group"] != "drivable" or not stratum_filter(r["stratum"]):
            continue
        tot["km"] += num(r["total_km"])
        for k, v in r.items():
            if k.startswith("km_"):
                tot[k] += num(v)
    if tot["km"] == 0:
        return {}
    return {k: 100.0 * v / tot["km"] for k, v in tot.items() if k.startswith("km_")}


def aggregate(rows, cross_rows, way_rows, lookup, key_fn, label_fn):
    """Group LAU strata by key_fn and sum. Returns list of dicts."""
    groups = defaultdict(list)
    for r in rows:
        if r["layer"] != "municipality":
            continue
        meta = lookup.get(r["stratum"])
        if not meta:
            continue
        key = key_fn(meta)
        if key is not None:
            groups[key].append((r, meta))

    cross_by_stratum = {c["stratum"]: c for c in cross_rows}
    out = []
    for key, members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        names = {m[0]["stratum"] for m in members}
        agg = {"unit": label_fn(key, members), "key": key, "n_lau": len(members)}
        agg["pop_2021"] = sum(num(m[1]["pop_2021"]) for m in members)
        agg["area_km2"] = sum(num(m[1]["area_km2"]) for m in members)
        for c in SUM_COLS:
            agg[c] = sum(num(m[0].get(c)) for m in members)
        for c in CROSSING_SUM_COLS:
            agg[c] = sum(num(cross_by_stratum.get(m[0]["stratum"], {}).get(c)) for m in members)

        agg.update({k.replace("km_", "") + "_pct": v
                    for k, v in km_pct(way_rows, lambda s: s in names).items()})

        cr, sg = agg["crossings"], agg["signal_nodes"]
        ix = agg["intersections"]
        junctions = agg["junctions_degree3"]
        agg["crossings_per_100_intersections"] = 100.0 * cr / ix if ix else None
        agg["crossings_per_100_junctions"] = 100.0 * cr / junctions if junctions else None
        agg["schema_way_share_pct"] = 100.0 * (agg["schema_way_only"] + agg["schema_both"]) / cr if cr else None
        agg["tactile_pct"] = 100.0 * agg["crossing_tactile"] / cr if cr else None
        agg["tactile_yes_pct"] = 100.0 * agg["crossing_tactile_yes"] / cr if cr else None
        agg["markings_any_pct"] = 100.0 * agg["markings_any"] / cr if cr else None
        agg["kerb_pct"] = 100.0 * agg["crossing_kerb"] / cr if cr else None
        agg["sound_pct"] = 100.0 * agg["signal_sound"] / sg if sg else None
        agg["sound_yes_pct"] = 100.0 * agg["signal_sound_yes"] / sg if sg else None
        agg["crossing_sound_pct"] = 100.0 * agg["crossing_sound"] / cr if cr else None
        agg["crossing_sound_yes_pct"] = 100.0 * agg["crossing_sound_yes"] / cr if cr else None
        agg["population_source_field"] = "GISCO LAU 2021 POP_2021"
        agg["population_reference_year"] = "unverified; Greek total matches revised 2011"
        agg["crossings_per_10k_pop"] = 1e4 * cr / agg["pop_2021"] if agg["pop_2021"] else None
        agg["road_km_per_km2"] = agg["drivable_km"] / agg["area_km2"] if agg["area_km2"] else None
        agg["thin"] = agg["drivable_km"] < MIN_DRIVABLE_KM and agg["intersections"] < MIN_INTERSECTIONS
        out.append(agg)
    return out


def write(path, rows):
    if not rows:
        print(f"  (nothing to write for {os.path.basename(path)})")
        return
    cols = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    thin = sum(1 for r in rows if r.get("thin"))
    print(f"  wrote {os.path.basename(path):32s} {len(rows):5d} units ({thin} flagged thin)")


def coverage_check(rows):
    """Report assigned and unmatched national totals for each independent layer."""
    nat = next((r for r in rows if r["stratum"] == "NATIONAL"), None)
    if not nat:
        raise ValueError("summary.csv has no NATIONAL row")
    result = []
    metrics = ["drivable_km", "pedestrian_km", "crossings", "intersections", "signals"]
    if "junctions_degree3" in nat:
        metrics.append("junctions_degree3")
    for layer in ("municipality", "region", "nuts3", "circle"):
        selected = [r for r in rows if r["layer"] == layer]
        for col in metrics:
            total, part = num(nat[col]), sum(num(r[col]) for r in selected)
            result.append({"layer": layer, "metric": col, "national": total,
                           "assigned": part, "unmatched": total - part,
                           "coverage_pct": 100 * part / total if total else None})
    return result


def main(census_dir=None, *, versioned=False, root=ROOT):
    paths = reporting_paths(root, versioned)
    if versioned and census_dir is not None:
        raise ValueError("--versioned uses only the verified corrected full-strata Greece run")
    if versioned:
        verify_corrected_greek(paths)
    census_dir = os.fspath(paths["census"]) if census_dir is None else census_dir
    out_dir = os.fspath(paths["analysis"])
    lookup_path = os.fspath(paths["root"] / "data" / "boundaries" / "municipalities.csv")
    for f in ("summary.csv", "crossing_metrics.csv", "way_metrics.csv"):
        if not os.path.isfile(os.path.join(census_dir, f)):
            sys.exit(f"Missing {os.path.join(census_dir, f)} - run the census first.")
    os.makedirs(out_dir, exist_ok=True)

    rows = read_csv(os.path.join(census_dir, "summary.csv"))
    cross = read_csv(os.path.join(census_dir, "crossing_metrics.csv"))
    ways = read_csv(os.path.join(census_dir, "way_metrics.csv"))
    lookup = load_lookup(lookup_path)

    n_lau = sum(1 for r in rows if r["layer"] == "municipality")
    matched_lau = sum(1 for r in rows if r["layer"] == "municipality" and r["stratum"] in lookup)
    print(f"Census rows: {len(rows)} strata ({n_lau} LAU rows; {matched_lau} lookup matches)")
    write(os.path.join(out_dir, "spatial_coverage.csv"), coverage_check(rows))
    print()

    def largest_name(members):
        return max(members, key=lambda m: num(m[1]["pop_2021"]))[1]["lau_name"]

    write(os.path.join(out_dir, "by_dimos.csv"),
          aggregate(rows, cross, ways, lookup,
                    lambda m: m["dimos_code"],
                    lambda k, mem: f"{k} {largest_name(mem)}"))

    write(os.path.join(out_dir, "by_urbanisation.csv"),
          aggregate(rows, cross, ways, lookup,
                    lambda m: m["degurba_label"],
                    lambda k, mem: k))

    # Region and NUTS3 come straight from their own polygon layers - no rollup
    # needed, but copy them out so every reporting level sits in one folder.
    for layer, fname in (("region", "by_region.csv"), ("nuts3", "by_nuts3.csv")):
        sel = [r for r in rows if r["layer"] == layer]
        if sel:
            with open(os.path.join(out_dir, fname), "w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(sel[0].keys()))
                w.writeheader()
                w.writerows(sel)
            print(f"  wrote {fname:32s} {len(sel):5d} units")

    print(f"\nPer-unit ranking rule: a unit needs >= {MIN_DRIVABLE_KM:.0f} km of drivable "
          f"network OR >= {MIN_INTERSECTIONS} intersections; the rest are flagged `thin`.")
    if versioned:
        write_versioned_manifest(
            paths, "aggregate",
            [paths["census"] / "run_manifest.json", *(paths["census"] / name for name in
              ("summary.csv", "crossing_metrics.csv", "way_metrics.csv")),
             lookup_path, paths["root"] / "analysis" / "aggregate.py"],
            [paths["analysis"] / name for name in
             ("spatial_coverage.csv", "by_dimos.csv", "by_urbanisation.csv",
              "by_region.csv", "by_nuts3.csv")],
            paths["analysis"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("census_dir", nargs="?", help="Legacy census input (legacy output directory)")
    parser.add_argument("--versioned", action="store_true",
                        help="Use the verified corrected Greece run and isolated unified outputs")
    args = parser.parse_args()
    main(args.census_dir, versioned=args.versioned)
