#!/usr/bin/env python3
"""Describe the top N dimos-prefix groups by historical GISCO population field.

This selected urban-oriented frame is not an official urban classification and
changes the research population. The national census retains rural observations.
Principal-LAU audit scope and whole-dimos descriptive totals are separate frames.

The unit is the Kallikratis dimos (4-char LAU_ID prefix), which is the
administrative "city" and correctly aggregates municipalities split into several
local communities.

Usage:  python analysis/urban_scope.py [top_n]      (default 50)
Output: results/urban/
        python analysis/urban_scope.py --versioned [top_n]
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from statistics import median

from aggregate import reporting_paths, verify_corrected_greek, write_versioned_manifest
from cross_country import spearman

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENSUS = os.path.join(ROOT, "results", "census")
ANALYSIS = os.path.join(ROOT, "results", "analysis")
LOOKUP = os.path.join(ROOT, "data", "boundaries", "municipalities.csv")
OUT = os.path.join(ROOT, "results", "urban")


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


def pctl(vals, p):
    v = sorted(vals)
    return v[int(p * (len(v) - 1))] if v else 0.0


def main(top_n=50, *, versioned=False, root=ROOT):
    top_n = int(top_n)
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    paths = reporting_paths(root, versioned)
    if versioned:
        verify_corrected_greek(paths)
    CENSUS = os.fspath(paths["census"])
    OUT = os.fspath(paths["urban"])
    LOOKUP = os.fspath(paths["root"] / "data" / "boundaries" / "municipalities.csv")
    os.makedirs(OUT, exist_ok=True)

    lookup = load(LOOKUP)
    feas = {r["stratum"]: r for r in load(os.path.join(CENSUS, "feasibility.csv"))}
    cross = {r["stratum"]: r for r in load(os.path.join(CENSUS, "crossing_metrics.csv"))}
    summ = {r["stratum"]: r for r in load(os.path.join(CENSUS, "summary.csv"))}
    dest = defaultdict(lambda: defaultdict(float))
    for r in load(os.path.join(CENSUS, "destinations.csv")):
        if r["layer"] == "municipality":
            for k in ("total", "tactile_stated", "wheelchair_stated", "braille", "named"):
                dest[(r["stratum"], r["kind"])][k] += num(r[k])

    # ---- group LAUs into dimoi
    dimoi = defaultdict(lambda: {"laus": [], "pop": 0.0, "name": "", "best_pop": -1})
    for m in lookup:
        code = m["dimos_code"]
        d = dimoi[code]
        d["laus"].append(m["stratum"])
        p = num(m["pop_2021"])
        d["pop"] += p
        if p > d["best_pop"]:
            d["best_pop"] = p
            d["name"] = m["lau_name"]

    ranked = sorted(dimoi.items(), key=lambda kv: -kv[1]["pop"])[:top_n]

    # The audit frame selects the principal LAU, not the entire dimos group.
    by_lau = {m["stratum"]: m for m in lookup}
    scope_laus = []
    for code, d in ranked:
        core = max(d["laus"], key=lambda l: num(by_lau[l]["pop_2021"]))
        scope_laus.append(core)

    # ---- aggregate the scope
    def agg(laus, col, src):
        return sum(num(src[l][col]) for l in laus if l in src)

    rows = []
    for code, d in ranked:
        laus = d["laus"]
        walk = agg(laus, "walkable_km", feas)
        eligible = agg(laus, "eligible_km", feas)
        pess = agg(laus, "pess_eligible_usable_km", feas)
        opt = agg(laus, "opt_eligible_usable_km", feas)
        all_pess = agg(laus, "pess_usable_km", feas)
        all_opt = agg(laus, "opt_usable_km", feas)
        unk = agg(laus, "unknown_km", feas)
        drv = agg(laus, "drivable_km", summ)
        ix = agg(laus, "intersections", cross)
        junctions = agg(laus, "junctions_degree3", cross)
        cr = agg(laus, "crossings", cross)
        tac = agg(laus, "crossing_tactile", cross)
        sg = agg(laus, "signal_nodes", cross)
        snd = agg(laus, "signal_sound", cross)
        snd_yes = agg(laus, "signal_sound_yes", cross)
        area = sum(num(by_lau[l]["area_km2"]) for l in laus)
        stops = sum(dest[(l, "stop")]["total"] for l in laus)
        stops_tac = sum(dest[(l, "stop")]["tactile_stated"] for l in laus)
        name_km = sum(num(summ[l]["name_pct"]) / 100 * num(summ[l]["drivable_km"])
                      for l in laus if l in summ)
        rows.append({
            "dimos_code": code, "name": d["name"], "pop_2021": round(d["pop"]),
            "population_source_field": "GISCO POP_2021; reference year unverified",
            "area_km2": area, "density_per_km2": d["pop"] / area if area else None,
            "principal_lau": max(laus, key=lambda l: num(by_lau[l]["pop_2021"])),
            "laus_with_network": sum(1 for l in laus if l in feas and num(feas[l]["walkable_km"]) > 0),
            "laus_with_eligible_network": sum(1 for l in laus if l in feas and num(feas[l]["eligible_km"]) > 0),
            "laus_missing_feasibility": sum(1 for l in laus if l not in feas),
            "population_no_network": sum(num(by_lau[l]["pop_2021"]) for l in laus
                                         if l not in feas or num(feas[l]["walkable_km"]) <= 0),
            "population_no_eligible_network": sum(num(by_lau[l]["pop_2021"]) for l in laus
                                                  if l not in feas or num(feas[l]["eligible_km"]) <= 0),
            "n_lau": len(laus), "drivable_km": round(drv, 1), "walkable_km": round(walk, 1),
            "eligible_km": eligible, "primary_denominator": "eligible nonauxiliary length",
            "intersections": int(ix), "crossings": int(cr),
            "junctions_degree3": int(junctions),
            "crossings_per_100_junctions": 100 * cr / junctions if junctions else None,
            "crossings_per_100_ix": round(100 * cr / ix, 3) if ix else 0,
            "name_pct": round(100 * name_km / drv, 2) if drv else 0,
            "tactile_crossings": int(tac),
            "tactile_pct": round(100 * tac / cr, 3) if cr else 0,
            "signals": int(sg), "signals_sound": int(snd),
            "signals_sound_affirmative": int(snd_yes),
            "sound_yes_pct": round(100 * snd_yes / sg, 3) if sg else 0,
            "sound_pct": round(100 * snd / sg, 2) if sg else 0,
            "transit_stops": int(stops), "stops_tactile": int(stops_tac),
            "pess_usable_pct": round(100 * pess / eligible, 3) if eligible else None,
            "opt_usable_pct": round(100 * opt / eligible, 3) if eligible else None,
            "gap_pct": round(100 * (opt - pess) / eligible, 3) if eligible else None,
            "all_length_pess_usable_pct": 100 * all_pess / walk if walk else None,
            "all_length_opt_usable_pct": 100 * all_opt / walk if walk else None,
            "all_length_gap_pct": 100 * (all_opt - all_pess) / walk if walk else None,
            "unknown_pct": round(100 * unk / walk, 3) if walk else 0,
        })

    with open(os.path.join(OUT, "top_cities.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT, "scope_laus.json"), "w", encoding="utf-8") as fh:
        json.dump({"top_n": top_n, "dimos_codes": [c for c, _ in ranked],
                   "laus": sorted(scope_laus),
                   "selection": "top dimos-prefix groups by historical GISCO POP_2021 field",
                   "note": "principal LAU per dimos group; not official urban delineation or whole-dimos audit"},
                  fh, ensure_ascii=False)

    # ---- national vs scope
    natf, natc, nats = feas["NATIONAL"], cross["NATIONAL"], summ["NATIONAL"]
    tot_pop = sum(num(m["pop_2021"]) for m in lookup)
    s_pop = sum(r["pop_2021"] for r in rows)
    s_walk = sum(r["walkable_km"] for r in rows)
    s_eligible = sum(r["eligible_km"] for r in rows)
    eligible_rows = [r for r in rows if r["eligible_km"] > 0]
    if not eligible_rows:
        raise ValueError("Selected scope has no eligible network")
    s_cr = sum(r["crossings"] for r in rows)
    s_tac = sum(r["tactile_crossings"] for r in rows)
    s_snd = sum(r["signals_sound"] for r in rows)
    s_sg = sum(r["signals"] for r in rows)

    print(f"=== SCOPE: top {len(rows)} dimos-prefix groups by GISCO population field ===")
    print(f"  population field {s_pop:>12,.0f}  ({100*s_pop/tot_pop:5.1f}% of Greek field sum)")
    print(f"  walkable km     {s_walk:>12,.0f}  ({100*s_walk/num(natf['walkable_km']):5.1f}% of national)")
    print(f"  crossings       {s_cr:>12,}  ({100*s_cr/num(natc['crossings']):5.1f}% of national)")
    print(f"  tactile         {s_tac:>12,}  ({100*s_tac/max(num(natc['crossing_tactile']),1):5.1f}% of national)")
    print(f"  sound statements {s_snd:>12,}  ({100*s_snd/max(num(natc['signal_sound']),1):5.1f}% of national statements)")
    print()
    print("=== SELECTED-SCOPE DISTRIBUTIONS ===")
    for col, label in [("pess_usable_pct", "pessimistic usable %"),
                       ("gap_pct", "decidability gap"),
                       ("crossings_per_100_ix", "crossings/100 ix"),
                       ("tactile_pct", "tactile % of crossings"),
                       ("sound_pct", "sound statements %"),
                       ("name_pct", "name completeness %")]:
        v = [r[col] for r in rows if r[col] is not None]
        zeros = sum(1 for x in v if x == 0)
        print(f"  {label:24} min {min(v):6.2f}  p25 {pctl(v,.25):6.2f}  median {pctl(v,.5):6.2f}  "
              f"p75 {pctl(v,.75):6.2f}  max {max(v):7.2f}   zeros {zeros}/{len(v)}")

    print()
    print("=== TOP 12 BY POPULATION ===")
    print(f"  {'city':30}{'pop':>9}{'guar%':>7}{'gap':>7}{'cr/100ix':>9}{'tactile':>8}{'sound%':>7}")
    for r in eligible_rows[:12]:
        print(f"  {r['name'][:29]:30}{r['pop_2021']:>9,}{r['pess_usable_pct']:>7.1f}"
              f"{r['gap_pct']:>7.1f}{r['crossings_per_100_ix']:>9.2f}"
              f"{r['tactile_crossings']:>8}{r['sound_pct']:>7.1f}")

    print()
    print("=== EXTREMES OF PESSIMISTIC PROFILE-SUPPORTED SHARE ===")
    by = sorted(eligible_rows, key=lambda r: -r["pess_usable_pct"])
    for r in by[:5]:
        print(f"  + {r['name'][:34]:36} {r['pess_usable_pct']:6.2f}%  pop {r['pop_2021']:>8,}")
    for r in by[-5:]:
        print(f"  - {r['name'][:34]:36} {r['pess_usable_pct']:6.2f}%  pop {r['pop_2021']:>8,}")
    # ---- Descriptive Attica/rest comparison; no causal interpretation.
    att_prefixes = {"45", "46", "47", "48", "49", "50", "51"}
    def is_attica(code):
        laus = dimoi[code]["laus"]
        return by_lau[laus[0]]["lau_id"][:2] in att_prefixes
    groups = {"attica": [r for r in rows if is_attica(r["dimos_code"])],
              "rest": [r for r in rows if not is_attica(r["dimos_code"])]}

    def med(v):
        values = [x for x in v if x is not None]
        return median(values) if values else None

    summary = {
        "top_n": top_n,
        "selected_dimoi": len(rows),
        "dimoi_with_eligible_network": len(eligible_rows),
        "primary_denominator": "eligible nonauxiliary length; threshold uses eligible component mass",
        "population_provenance": "GISCO POP_2021 historical weights; per-LAU year unverified",
        "descriptive_scope": "all assigned LAUs in selected dimos-prefix groups",
        "audit_scope": "one principal LAU per selected dimos-prefix group",
        "selection_limitation": "Population-selected frame, not all urban Greece; rural outcomes remain meaningful.",
        "coverage": {
            "population": s_pop, "population_pct": 100 * s_pop / tot_pop,
            "walkable_km": s_walk,
            "eligible_km": s_eligible,
            "eligible_pct": 100 * s_eligible / num(natf["eligible_km"]) if num(natf["eligible_km"]) else None,
            "walkable_pct": 100 * s_walk / num(natf["walkable_km"]),
            "crossings": s_cr, "crossings_pct": 100 * s_cr / num(natc["crossings"]),
            "tactile": s_tac,
            "tactile_pct": 100 * s_tac / max(num(natc["crossing_tactile"]), 1),
            "sound": s_snd,
            "sound_pct": 100 * s_snd / max(num(natc["signal_sound"]), 1),
        },
        "variance": [
            {"metric": label,
             "min": min(v), "p25": pctl(v, .25), "median": pctl(v, .5),
             "p75": pctl(v, .75), "max": max(v),
             "zeros": sum(1 for x in v if x == 0), "n": len(v)}
            for col, label in [("pess_usable_pct", "Pessimistic usable %"),
                               ("gap_pct", "Decidability gap (pts)"),
                               ("crossings_per_100_ix", "Crossings per 100 intersections"),
                               ("name_pct", "Name completeness %"),
                               ("tactile_pct", "Tactile % of crossings"),
                               ("sound_pct", "Signals with sound statement %")]
            for v in [[r[col] for r in rows if r[col] is not None]]
        ],
        "attica": {
            k: {"n": len(g),
                "n_eligible": sum(r["eligible_km"] > 0 for r in g),
                "guaranteed": med([r["pess_usable_pct"] for r in g]),
                "pessimistic_usable_pct": med([r["pess_usable_pct"] for r in g]),
                "gap": med([r["gap_pct"] for r in g]),
                "crossings_per_100_ix": med([r["crossings_per_100_ix"] for r in g]),
                "name_pct": med([r["name_pct"] for r in g]),
                "pop": sum(r["pop_2021"] for r in g)}
            for k, g in groups.items() if g
        },
        "cities": [{k: r[k] for k in ("name", "pop_2021", "pess_usable_pct", "gap_pct",
                                      "crossings_per_100_ix", "name_pct",
                                      "tactile_crossings", "sound_pct")} for r in rows],
    }
    summary["relationships"] = [
        {"covariate": cov, "metric": metric, "n": len(pairs),
         "spearman_rho": spearman([r[cov] for r in pairs], [r[metric] for r in pairs]),
         "interpretation": "descriptive association in selected dimos groups; no causal inference"}
        for cov in ("pop_2021", "density_per_km2")
        for metric in ("pess_usable_pct", "gap_pct", "crossings_per_100_ix", "name_pct")
        for pairs in [[r for r in rows if r[cov] is not None and r[metric] is not None]]
    ]
    with open(os.path.join(OUT, "urban_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)

    print()
    print("=== ATTICA vs REST (medians) ===")
    for k, g in summary["attica"].items():
        if g["pessimistic_usable_pct"] is None:
            print(f"  {k}: no eligible network")
            continue
        print(f"  {k:8} n={g['n']:>3}  pessimistic {g['guaranteed']:6.2f}%  "
              f"gap {g['gap']:6.2f}  cr/100ix {g['crossings_per_100_ix']:6.2f}  "
              f"name {g['name_pct']:5.1f}%")
    print(f"\nWrote top_cities.csv, scope_laus.json, urban_summary.json to {OUT}")
    if versioned:
        write_versioned_manifest(
            paths, "urban",
            [paths["census"] / "run_manifest.json", *(paths["census"] / name for name in
              ("feasibility.csv", "crossing_metrics.csv", "summary.csv", "destinations.csv")),
             LOOKUP, paths["root"] / "analysis" / "urban_scope.py"],
            [paths["urban"] / name for name in
             ("top_cities.csv", "scope_laus.json", "urban_summary.json")],
            paths["urban"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("top_n", nargs="?", type=int, default=50)
    parser.add_argument("--versioned", action="store_true",
                        help="Read the verified corrected Greek census; write only isolated unified outputs")
    args = parser.parse_args()
    main(args.top_n, versioned=args.versioned)
