#!/usr/bin/env python3
"""Independent validity checks over the pipeline outputs.

Every check here recomputes something from a different direction than the code
that produced it, so an error in one place shows up as a disagreement rather than
being silently reproduced. Checks that cannot fail are not worth writing; each of
these could.

Run:  python analysis/validate.py
Exit code is non-zero if any check fails, so it can gate a release.
"""

import csv
import io
import json
import math
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENSUS = os.path.join(ROOT, "results", "census")
ANALYSIS = os.path.join(ROOT, "results", "analysis")
URBAN = os.path.join(ROOT, "results", "urban")
MLY = os.path.join(ROOT, "results", "mapillary")
COUNTRIES = os.path.join(ROOT, "results", "countries")

PASS, FAIL, WARN = [], [], []


def load(path):
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


def warn(name, detail):
    WARN.append(name)
    print(f"  WARN  {name}  — {detail}")


def close(a, b, tol=0.01):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def main():
    if "--self-test" in sys.argv:
        return self_test()
    S = {r["stratum"]: r for r in load(os.path.join(CENSUS, "summary.csv"))}
    C = {r["stratum"]: r for r in load(os.path.join(CENSUS, "crossing_metrics.csv"))}
    F = {r["stratum"]: r for r in load(os.path.join(CENSUS, "feasibility.csv"))}
    W = load(os.path.join(CENSUS, "way_metrics.csv"))
    rows = load(os.path.join(CENSUS, "summary.csv"))
    n, c, f = S["NATIONAL"], C["NATIONAL"], F["NATIONAL"]
    from cross_country import eligible_endpoints
    primary = eligible_endpoints(f)

    print("\n=== 1. Feasibility edge states partition the network ===")
    walk = num(f["walkable_km"])
    parts = num(f["pass_km"]) + num(f["unknown_km"]) + num(f["block_km"])
    check("pass + unknown + block == walkable_km", close(parts, walk, 1e-6),
          f"{parts:,.3f} vs {walk:,.3f}")
    check("eligible primary endpoints are bounded",
          0 <= primary["pess_usable_km"] <= primary["opt_usable_km"] + .002 <= primary["eligible_km"] + .004)

    print("\n=== 2. Undecidability decomposition sums to unknown_km ===")
    dec = sum(num(f[k]) for k in ("unknown_road_sidewalk_km", "unknown_crossing_attrs_km",
                                  "unknown_steps_km", "unknown_pedestrian_other_km",
                                  "unknown_graph_only_km"))
    dec += num(f.get("unknown_other_km"))
    check("cause breakdown == unknown_km", close(dec, num(f["unknown_km"]), 1e-6),
          f"{dec:,.3f} vs {num(f['unknown_km']):,.3f}")

    print("\n=== 3. Crossing schema split accounts for every crossing ===")
    tot = num(c["schema_node_only"]) + num(c["schema_way_only"]) + num(c["schema_both"])
    check("node_only + way_only + both == crossings", close(tot, num(c["crossings"]), 1e-9),
          f"{tot:,.0f} vs {num(c['crossings']):,.0f}")
    check("crossings <= node members + way members",
          num(c["crossings"]) <= num(c["crossing_node_members"]) + num(c["crossing_way_members"]),
          "union-find can only merge, never invent")

    print("\n=== 4. Affirmative never exceeds presence ===")
    bad = []
    for r in W:
        for fam in ("name", "sidewalk", "surface", "smoothness", "lit", "maxspeed", "width", "incline"):
            p, a = num(r.get("km_" + fam)), num(r.get("km_aff_" + fam))
            if a > p + 1e-6:
                bad.append(f"{r['stratum']}/{r['highway_class']}/{fam}")
    check("km_aff_<family> <= km_<family> everywhere", not bad,
          f"{len(bad)} violations" if bad else f"checked {len(W):,} rows")
    for stated, affirmative in (("signal_sound", "signal_sound_yes"),
                                ("crossing_sound", "crossing_sound_yes"),
                                ("crossing_tactile", "crossing_tactile_yes")):
        check(f"{affirmative} <= {stated}",
              all(num(row[affirmative]) <= num(row[stated]) for row in C.values()))

    print("\n=== 5. Tag km never exceeds total km ===")
    bad2 = [f"{r['stratum']}/{r['highway_class']}" for r in W
            if any(num(r.get("km_" + fam)) > num(r["total_km"]) + 1e-6
                   for fam in ("name", "sidewalk", "surface", "lit"))]
    check("km_<family> <= total_km everywhere", not bad2, f"{len(bad2)} violations")

    print("\n=== 6. Percentages recomputed from raw counts ===")
    cr, ix = num(c["crossings"]), num(c["intersections"])
    check("crossings_per_100_intersections", close(100 * cr / ix, num(n["crossings_per_100_intersections"])),
          f"{100*cr/ix:.4f} vs {num(n['crossings_per_100_intersections']):.4f}")
    check("tactile_pct", close(100 * num(c["crossing_tactile"]) / cr, num(n["tactile_pct"])))
    check("sound_pct", close(100 * num(c["signal_sound"]) / num(c["signal_nodes"]), num(n["sound_pct"])))
    junctions = num(c["junctions_degree3"])
    if junctions:
        check("crossings per 100 degree-three junctions",
              abs(100 * cr / junctions - num(c["crossings_per_100_junctions"])) <= .001)
    dr = [r for r in W if r["stratum"] == "NATIONAL" and r["class_group"] == "drivable"]
    tkm = sum(num(r["total_km"]) for r in dr)
    nkm = sum(num(r["km_name"]) for r in dr)
    check("name_pct from way_metrics", close(100 * nkm / tkm, num(n["name_pct"])),
          f"{100*nkm/tkm:.4f} vs {num(n['name_pct']):.4f}")
    check("drivable_km from way_metrics", close(tkm, num(n["drivable_km"])),
          f"{tkm:,.1f} vs {num(n['drivable_km']):,.1f}")
    from paper_data import bpri_from_counts
    for version in ("v0", "v1"):
        score = bpri_from_counts(n, c, version)
        check(f"BPRI {version} recomputed with its own crossing universe",
              score is not None and abs(score - num(n["bpri_" + version])) <= .001)

    print("\n=== 7. Layer sums do not exceed the national total ===")
    for layer in ("municipality", "region", "nuts3"):
        sel = [r for r in rows if r["layer"] == layer]
        if not sel:
            continue
        km = sum(num(r["drivable_km"]) for r in sel)
        nat = num(n["drivable_km"])
        ok = km <= nat * 1.001
        check(f"{layer}: sum drivable_km <= national", ok,
              f"{km:,.0f} / {nat:,.0f} = {100*km/nat:.2f}%")
        if ok and km < nat * 0.97:
            warn(f"{layer} coverage", f"only {100*km/nat:.1f}% of national km falls inside the layer")
    for r in load(os.path.join(CENSUS, "spatial_coverage.csv")):
        check(f"{r['layer']} native coverage accounts for national totals",
              abs(num(r["assigned_drivable_km"]) + num(r["unassigned_drivable_km"])
                  - num(r["national_drivable_km"])) <= .002
              and num(r["assigned_crossings"]) + num(r["unassigned_crossings"]) == num(r["national_crossings"]))

    print("\n=== 8. Urban scope is a strict subset ===")
    if os.path.isfile(os.path.join(URBAN, "top_cities.csv")):
        T = load(os.path.join(URBAN, "top_cities.csv"))
        with open(os.path.join(URBAN, "scope_laus.json"), encoding="utf-8") as fh:
            scope = json.load(fh)
        check("requested dimos groups selected", len(T) == scope["top_n"], f"{len(T)}")
        check("scope crossings <= national",
              sum(num(r["crossings"]) for r in T) <= num(c["crossings"]),
              f"{sum(num(r['crossings']) for r in T):,.0f} / {num(c['crossings']):,.0f}")
        gap_ok = all(abs((num(r["opt_usable_pct"]) - num(r["pess_usable_pct"])) - num(r["gap_pct"])) < 0.02
                     for r in T)
        check("gap == optimistic - pessimistic per city", gap_ok)
        check("pessimistic <= optimistic per city",
              all(num(r["pess_usable_pct"]) <= num(r["opt_usable_pct"]) for r in T))
        check("zero-eligible dimos groups have undefined model shares",
              all((num(r["eligible_km"]) > 0) == (r["pess_usable_pct"] != "") for r in T))

    print("\n=== 9. Exposure weighting ===")
    E = load(os.path.join(ANALYSIS, "exposure_by_lau.csv"))
    if E:
        tot_pop = sum(num(r["pop_2021"]) for r in E)
        pw = sum(num(r["pess_usable_pct"]) * num(r["pop_2021"]) for r in E) / tot_pop
        facts = {r["metric"]: num(r["value"]) for r in load(os.path.join(ANALYSIS, "exposure_facts.csv"))}
        check("population-weighted share recomputed", close(pw, facts.get("pop_weighted_guaranteed_pct", 0)),
              f"{pw:.4f} vs {facts.get('pop_weighted_guaranteed_pct', 0):.4f}")
        weighted = sum(num(r["pess_usable_pct"]) * num(r["eligible_km"]) for r in E) / sum(num(r["eligible_km"]) for r in E)
        check("network weighting uses eligible length",
              abs(weighted - facts["km_weighted_pessimistic_pct"]) <= .001)
        bands = load(os.path.join(ANALYSIS, "exposure_bands.csv"))
        band_pop = sum(num(r["pop_2021"]) for r in bands)
        check("exposure bands partition included GISCO population field", close(band_pop, tot_pop, 1e-6),
              f"{band_pop:,.0f} vs {tot_pop:,.0f}")
        meta_path = os.path.join(ANALYSIS, "exposure_metadata.json")
        check("exposure exclusion metadata exists", os.path.isfile(meta_path))
        if os.path.isfile(meta_path):
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            excluded = sum(group["population_field_sum"] for group in meta["exclusions"].values())
            check("included plus excluded population fields equal lookup",
                  close(tot_pop + excluded, meta["lookup_population_field_sum"], 1e-9))

    print("\n=== 10. Edit targeting arithmetic ===")
    ET = load(os.path.join(CENSUS, "edit_targets.csv"))
    ES = load(os.path.join(CENSUS, "edit_targets_summary.csv"))
    if ET and ES:
        s0 = ES[0]
        from campaign import validate_target_curve, intervention_metadata
        try:
            intervention_metadata(s0)
        except ValueError as exc:
            check("targeting intervention model is explicitly declared", False, str(exc))
        else:
            check("targeting intervention model is explicitly declared", True)
        try:
            validate_target_curve(ET, s0)
        except ValueError as exc:
            check("target curve equals actual cumulative usable gain", False, str(exc))
        else:
            check("target curve equals actual cumulative usable gain", True)
        check("usable_after >= usable_before",
              num(s0["usable_km_after"]) >= num(s0["usable_km_before"]))
        if "pess_eligible_usable_km" in f:
            check("targeting uses the same eligible-length baseline as feasibility",
                  close(num(s0["usable_km_before"]), num(f["pess_eligible_usable_km"]), 1e-6))
        if num(s0["usable_km_before"]) > 0:
            check("usable_multiple consistent",
                  close(num(s0["usable_km_after"]) / num(s0["usable_km_before"]), num(s0["usable_multiple"])))
        else:
            check("zero baseline has no fabricated multiplier", s0["usable_multiple"] in ("", None))
        check("edits_committed <= candidates",
              num(s0["edits_committed"]) <= num(s0["candidates"]))
        baseline_path = os.path.join(CENSUS, "edit_targets_length_baseline.csv")
        check("length-based targeting baseline exists", os.path.isfile(baseline_path))
        if os.path.isfile(baseline_path):
            from campaign import length_baseline_summary
            try:
                validate_target_curve(load(baseline_path), length_baseline_summary(s0))
            except (ValueError, KeyError) as exc:
                check("length baseline curve equals same-objective summary", False, str(exc))
            else:
                check("length baseline curve equals same-objective summary", True)

    print("\n=== 11. Mapillary join sanity ===")
    from paper_data import load_mapillary_rows
    V, mapillary_path, is_v2 = load_mapillary_rows(MLY)
    if V:
        bad3 = [r["city"] for r in V
                if num(r["junctions_in_image_positive_tiles"]) + num(r["junctions_without_image_positive_tile"])
                != num(r["intersections"])]
        check("image-positive + other-tile junctions == intersections", not bad3, f"{len(bad3)} mismatches")
        check("CV-positive flags <= eligible OSM-negative junctions",
              all(num(r["cv_positive_flags"]) <= num(r["osm_negative_junctions"]) for r in V))
        check("co-detected crossings <= crossings in image-positive tiles",
              all(num(r["crossings_codetected"]) <= num(r["crossings_in_image_positive_tiles"]) for r in V))
        check("co-detected signal locations <= signal locations in image-positive tiles",
              all(num(r["general_signal_locations_codetected"]) <=
                  num(r["general_signal_locations_in_image_positive_tiles"]) for r in V))
        if is_v2:
            check("crossing co-detections use known-query eligible denominator",
                  all(r["crossings_screening_eligible"] is not None
                      and r["crossings_codetected"] <= r["crossings_screening_eligible"] for r in V))
            check("pedestrian-head co-detections use known-query eligible denominator",
                  all(r["general_signal_locations_screening_eligible"] is not None
                      and r["general_signal_locations_codetected"] <= r["general_signal_locations_screening_eligible"] for r in V))
        check("OSM-negative eligible junctions <= junctions in image-positive tiles",
              all(num(r["osm_negative_junctions"]) <= num(r["junctions_in_image_positive_tiles"]) for r in V))
        if is_v2:
            check("Mapillary v2 has source/temporal manifest",
                  os.path.isfile(os.path.join(MLY, "co_detection_manifest.json")))
            for recent, all_dates in (("cv_positive_flags_recent", "cv_positive_flags"),
                                      ("crossings_codetected_recent", "crossings_codetected"),
                                      ("ped_head_codetected_recent", "general_signal_locations_codetected")):
                check(f"{recent} <= all-date count", all(r.get(recent) is not None and r[recent] <= r[all_dates] for r in V))
        D = load(os.path.join(MLY, "detections_v2.csv" if is_v2 else "detections.csv"))
        if D:
            lats = [num(d["lat"]) for d in D]
            lons = [num(d["lon"]) for d in D]
            check("detection coordinates inside Greece's envelope",
                  min(lats) > 34 and max(lats) < 42 and min(lons) > 19 and max(lons) < 30,
                  f"lat {min(lats):.2f}..{max(lats):.2f}, lon {min(lons):.2f}..{max(lons):.2f}")
        O = load(os.path.join(MLY, "osm_points.csv"))
        if O:
            la = [num(o["lat"]) for o in O]
            lo = [num(o["lon"]) for o in O]
            check("OSM point coordinates inside Greece's envelope",
                  min(la) > 34 and max(la) < 42 and min(lo) > 19 and max(lo) < 30,
                  f"lat {min(la):.2f}..{max(la):.2f}, lon {min(lo):.2f}..{max(lo):.2f}")

    print("\n=== 12. Cross-country table ===")
    X = load(os.path.join(COUNTRIES, "cross_country.csv"))
    from cross_country import COUNTRIES as COUNTRY_LIST, GREECE
    COUNTRY_SET = list(COUNTRY_LIST) + [GREECE]
    if X:
        # the count is a property of the country list, not a constant to assert
        expected = len(COUNTRY_SET)
        check(f"{expected} countries", len(X) == expected, f"{len(X)}")
        check("gap == optimistic - pessimistic",
              all(abs((num(r["opt_usable_pct"]) - num(r["pess_usable_pct"])) - num(r["gap_pct"])) < 0.02
                  for r in X))
        valid_cov = [r for r in X if r["pop_2021"] and r["area_km2"] and r["density_per_km2"]]
        check("density == GISCO population field / area where defined",
              all(close(num(r["pop_2021"]) / num(r["area_km2"]), num(r["density_per_km2"]), 0.02)
                  for r in valid_cov))
        check("all country records have verified execution provenance",
              all(r.get("provenance_status") == "manifest verified" for r in X))
        gr = next(r for r in X if r["code"] == "EL")
        check("Greece row matches the national census",
              close(num(gr["crossings_per_100_ix"]), num(n["crossings_per_100_intersections"]))
              and close(num(gr["tactile_pct"]), num(n["tactile_pct"]))
              and close(num(gr["pess_usable_pct"]), primary["pess_usable_pct"]),
              "national-only config reproduces the full run")
        for r in X:
            if not (0 <= num(r["pess_usable_pct"]) <= 100 and 0 <= num(r["tactile_pct"]) <= 100):
                check(f"{r['country']} percentages in range", False)

    print("\n=== 13. Paper bundle agrees with the CSVs ===")
    pj = os.path.join(ROOT, "results", "paper_data.json")
    if os.path.isfile(pj):
        d = json.load(io.open(pj, encoding="utf-8"))
        nat = d["national"]
        check("bundle crossings == crossing_metrics", nat["crossings"] == int(num(c["crossings"])))
        check("bundle intersections == summary", nat["intersections"] == int(num(n["intersections"])))
        check("bundle tactile_affirmative == crossing_tactile_yes",
              nat["tactile_affirmative"] == int(num(c["crossing_tactile_yes"])))
        check("bundle sound stated and affirmative are distinct outputs",
              nat.get("signal_sound_stated") == int(num(c["signal_sound"])) and
              nat.get("signal_sound_affirmative") == int(num(c["signal_sound_yes"])))
        check("bundle junctions aliases the degree-three count",
              nat.get("junctions") == int(c["junctions_degree3"]))
        from paper_data import value_distributions
        values = value_distributions(load(os.path.join(CENSUS, "tag_values.csv")))
        check("bundle tag values preserve universes and aggregate before top-k",
              d.get("tag_values_by_universe") == values)
        check("published detector is the 100-case Google Maps evaluation",
              d.get("published_detector", {}).get("cases") == 100 and
              d.get("published_detector", {}).get("map_provider") == "Google Maps")
        fe = d["feasibility_national"]
        check("bundle primary gap == eligible-length gap", close(fe["gap_pct"], primary["gap_pct"]))
        check("bundle all-length gap retained separately",
              close(fe["all_length_gap_pct"], num(f["decidability_gap_pct"])))

    print("\n" + "=" * 62)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed, {len(WARN)} warnings")
    if FAIL:
        print("  FAILED: " + "; ".join(FAIL))
    print("=" * 62)
    return 1 if FAIL else 0


def self_test():
    """Read-only numerical regressions; no census or output regeneration."""
    from unittest.mock import patch, mock_open
    from paper_data import (value_distributions, normalize_mapillary_row,
                            MAPILLARY_LEGACY_ALIASES, native_sensitivity_rows,
                            MAPILLARY_V2_FIELDS, MAPILLARY_RECENT_COUNTS,
                            mapillary_inventory_summary, mapillary_audit_links, audit_sample_summary,
                            bpri_from_counts, BPRI_WEIGHTS, sensitivity_freshness, mapillary_screening_denominators,
                            load_mapillary_rows)
    from cross_country import (spearman, pbf_header, fingerprint, current_manifest,
                               input_provenance, eligible_endpoints)
    from campaign import validate_target_curve, objective_target_rows, length_baseline_summary, intervention_metadata
    from profile_sensitivity import scenario_profile, native_options
    from aggregate import coverage_check
    fixture = [
        {"stratum": "NATIONAL", "feature_type": "way", "universe": universe,
         "key": "sidewalk", "value": value, "km": str(km), "count": ""}
        for universe, value, km in [
            ("drivable", "no", 3), ("drivable", "yes", 5), ("drivable", "no", 4),
            ("pedestrian", "no", 90), ("drivable", "both", 2)]
    ]
    values = value_distributions(fixture, top_k=1)
    drv = values["way/drivable/sidewalk"]
    check("top-k follows complete value merge", drv["top"][0]["value"] == "no" and
          drv["top"][0]["amount"] == 7 and drv["total"] == 14 and drv["other_amount"] == 7)
    check("different feature universes never pool", values["way/pedestrian/sidewalk"]["total"] == 90)
    clean = {key: "1" for key in MAPILLARY_LEGACY_ALIASES}
    clean.update({"intersections": "2", "osm_crossings": "1", "det_street_light": "0"})
    legacy = {old: clean[new] for new, old in MAPILLARY_LEGACY_ALIASES.items()}
    legacy.update({key: clean[key] for key in ("intersections", "osm_crossings", "det_street_light")})
    new_row, old_row = normalize_mapillary_row(clean), normalize_mapillary_row(legacy)
    check("clean and legacy Mapillary counts map identically",
          all(new_row[key] == old_row[key] for key in MAPILLARY_LEGACY_ALIASES))
    v2 = {key: "1" for key in MAPILLARY_V2_FIELDS.values()}
    v2.update({key: "0" for key in MAPILLARY_RECENT_COUNTS})
    v2.update({"intersections": "2", "osm_crossings": "1", "city": "fixture", "stratum": "LAU:fixture"})
    v2_row = normalize_mapillary_row(v2)
    check("v2 separates all-date and recent detection counts",
          v2_row["cv_positive_flags"] == 1 and v2_row["cv_positive_flags_recent"] == 0
          and v2_row["general_signal_locations_codetected"] == 1)
    check("v2 missing inventory is unavailable, not zero", v2_row["det_furniture"] is None)
    with patch("os.path.isfile", return_value=True), patch("paper_data.load", return_value=[v2]):
        loaded, source, _ = load_mapillary_rows("fixture")
    check("full temporal canonical Mapillary file takes precedence",
          source.endswith("co_detection_by_city.csv") and loaded[0]["cv_positive_flags_recent"] == 0)
    compatibility = {**clean, "city": "fixture", "stratum": "LAU:fixture"}
    with patch("os.path.isfile", return_value=True), patch(
            "paper_data.load", side_effect=lambda path: [compatibility] if path.endswith("co_detection_by_city.csv") else [v2]):
        loaded, source, _ = load_mapillary_rows("fixture")
    check("earlier all-date compatibility file uses explicit periods artifact",
          source.endswith("co_detection_by_city_periods.csv") and loaded[0]["cv_positive_flags_recent"] == 0)
    denominators = mapillary_screening_denominators({
        "crossings_in_image_positive_tiles": 5, "crossings_feature_query_unknown": 2,
        "general_signal_locations_in_image_positive_tiles": 7, "general_signal_locations_feature_query_unknown": 1})
    check("unknown feature queries are removed from crossing and signal denominators",
          denominators == {"crossings_screening_eligible": 3, "general_signal_locations_screening_eligible": 6})
    check("missing query-status metadata does not imply zero unknown queries",
          mapillary_screening_denominators({"crossings_in_image_positive_tiles": 5,
                                           "general_signal_locations_in_image_positive_tiles": 7})["crossings_screening_eligible"] is None)
    inventory = mapillary_inventory_summary([
        {"stratum": "A", "group": "street_light", "count_all_dates": "10", "count_recent": "6", "last_seen_missing": "1"},
        {"stratum": "B", "group": "street_furniture", "count_all_dates": "3", "count_recent": "0", "last_seen_missing": "0"}])
    check("scope inventory preserves temporal and object-group totals",
          inventory["count_all_dates"] == 13 and inventory["count_recent"] == 6
          and inventory["by_group"]["street_furniture"]["count_all_dates"] == 3)
    link_rows = [
        {"sample_sha256": "sample", "arm": arm, "image_tile_status": tile,
         "cv_crossing_ids_all_dates": all_dates, "cv_crossing_ids_recent": recent}
        for arm, tile, all_dates, recent in [
            ("commission", "image_positive", "1;2", "2"),
            ("omission", "image_positive", "3", ""),
            ("sidewalk", "image_positive", "4", "4"),
            ("commission", "unknown", "5", "5"),
            ("omission", "image_positive", "", "")]]
    links = mapillary_audit_links(link_rows, "sample")
    check("incidental primary links exclude sidewalk, unknown tiles and CV-negative cases",
          links["primary_cv_positive_n"] == 2 and links["recent_primary_cv_positive_n"] == 1)
    check("stale incidental links are not attributed to current audit",
          mapillary_audit_links(link_rows, "changed")["current_sample_primary_cv_positive_n"] is None)
    audit = audit_sample_summary([
        {"arm": "commission", "group": "city", "mapped_feature_state": "PASS", "profile_state": "UNKNOWN"},
        {"arm": "sidewalk", "group": "city", "mapped_feature_state": "", "profile_state": "UNKNOWN"}])
    check("audit prefers local mapped-feature state to legacy labels",
          audit["by_arm_state"]["commission/PASS"] == 1 and audit["state_sources"] == {"mapped_feature_state": 2})
    check("blank local audit state is not silently UNKNOWN",
          audit["by_arm_state"]["sidewalk/not_evaluated"] == 1)
    old_audit = audit_sample_summary([{"arm": "omission", "group": "city", "profile_state": "UNKNOWN"}])
    check("legacy audit state fallback is explicit and not relabelled as mapped truth",
          old_audit["state_sources"] == {"legacy_profile_state": 1}
          and old_audit["by_arm_mapped_feature_state"] is None)
    score_summary = {key: "0" for key in ("sidewalk_pct", "sidewalk_aff_pct", "lit_pct", "surface_pct", "name_pct")}
    score_crossing = {
        "legacy_crossing_nodes": "2", "legacy_crossing_subtype": "1", "legacy_crossing_tactile": "0",
        "intersections": "4", "crossings": "4", "crossing_subtype": "4", "crossing_tactile": "4",
        "markings_any": "4", "crossing_kerb": "4", "signal_nodes": "1", "signal_sound": "0"}
    check("BPRI v0 is genuinely node-only", abs(bpri_from_counts(score_summary, score_crossing, "v0") - .175) < 1e-12)
    check("BPRI v1 uses unified crossing features", abs(bpri_from_counts(score_summary, score_crossing, "v1") - .55) < 1e-12
          and all(abs(sum(weights.values()) - 1) < 1e-12 for weights in BPRI_WEIGHTS.values()))
    try:
        normalize_mapillary_row({k: v for k, v in clean.items() if k != "cv_positive_flags"})
    except ValueError:
        check("missing Mapillary count fails rather than becoming zero", True)
    else:
        check("missing Mapillary count fails rather than becoming zero", False)
    try:
        normalize_mapillary_row({**clean, "omission_found": "2"})
    except ValueError:
        check("conflicting Mapillary aliases rejected", True)
    else:
        check("conflicting Mapillary aliases rejected", False)
    base = {"pedestrian_km": "0", "crossings": "0", "intersections": "0", "signals": "0"}
    coverage = coverage_check([
        {**base, "stratum": "NATIONAL", "layer": "national", "drivable_km": "100"},
        {**base, "stratum": "LAU:a", "layer": "municipality", "drivable_km": "90"},
        {**base, "stratum": "REG:b", "layer": "region", "drivable_km": "95"}])
    residuals = {r["layer"]: r["unmatched"] for r in coverage if r["metric"] == "drivable_km"}
    check("independent layers report separate unmatched totals",
          residuals["municipality"] == 10 and residuals["region"] == 5)
    check("tie-aware Spearman", spearman([1, 1, 2], [1, 2, 3]) == .866)
    check("constant Spearman is undefined", spearman([1, 1, 1], [1, 2, 3]) is None)
    original_profile = {"id": "test", "excludedClasses": ["motorway"], "minUsableComponentKm": 1}
    core_profile = scenario_profile(original_profile, "core", 5)
    check("core sensitivity removes auxiliary classes before execution",
          set(core_profile["excludedClasses"]) == {"motorway", "track", "cycleway", "bridleway"}
          and core_profile["minUsableComponentKm"] == 5)
    check("sensitivity preserves original profile", original_profile["excludedClasses"] == ["motorway"]
          and original_profile["minUsableComponentKm"] == 1)
    check("native sensitivity runs thresholds and core in one invocation",
          native_options([.5, 1, 2], True) ==
          ["--graph-scope=extended", "--component-thresholds=0.5,1,2", "--core-sensitivity"])
    native = native_sensitivity_rows([{
        "stratum": "NATIONAL", "profile": "test", "graph_scope": "extended",
        "threshold_km": "1", "walkable_km": "100", "eligible_km": "50",
        "pess_usable_km": "10", "opt_usable_km": "80",
        "pess_eligible_usable_km": "10", "opt_eligible_usable_km": "40"}])[0]
    check("native sensitivity distinguishes graph and eligible denominators",
          native["pess_pct"] == 20 and native["gap_pct"] == 60
          and native["all_length_pess_pct"] == 10 and native["all_length_gap_pct"] == 70)
    stub = eligible_endpoints({"eligible_km": ".1", "pess_eligible_usable_km": "0",
                               "opt_eligible_usable_km": "0", "walkable_km": "100", "opt_usable_km": "100"})
    check("auxiliary-inclusive usable mass cannot replace eligible-threshold output",
          stub["opt_usable_pct"] == 0 and stub["eligible_km"] == .1)
    targets = [{"rank": "1", "own_km": ".041", "unlocked_km": ".041", "cumulative_km": ".041"}]
    summary = {"edits_committed": "1", "usable_km_before": "100", "usable_km_after": "100.041"}
    validate_target_curve(targets, summary)
    check("joining already usable components gains own 41 metres", True)
    mixed = [{**targets[0], "unlocked_km": "33.9", "cumulative_km": "33.9",
              "marginal_usable_km": ".041", "cumulative_usable_gain_km": ".041",
              "usable_km_after": "100.041"}]
    validate_target_curve(mixed, summary)
    mapped = objective_target_rows(mixed)[0]
    check("actual target gain takes precedence over legacy consolidation score",
          mapped["unlocked_km"] == ".041" and mapped["legacy_unlocked_km"] == "33.9")
    baseline = [{k: v for k, v in mixed[0].items() if k not in ("unlocked_km", "cumulative_km")}]
    bs = length_baseline_summary({
        "length_baseline_usable_before": "100", "length_baseline_usable_after": "100.041",
        "length_baseline_usable_gain_km": ".041", "length_baseline_survey_km": ".041", "edits_committed": "1"})
    validate_target_curve(baseline, bs)
    check("native length baseline needs no legacy consolidation columns", bs["usable_gain_km"] == .041)
    intervention = intervention_metadata({"intervention_model": "way_state_override_fixed_node_transitions"})
    check("target gains identify fixed-node state overrides, not literal tag edits",
          "node transitions remain fixed" in intervention["assumption"]
          and "not literal tag edits" in intervention["interpretation"])
    try:
        intervention_metadata({})
    except ValueError:
        check("missing intervention model is not silently relabelled", True)
    else:
        check("missing intervention model is not silently relabelled", False)
    try:
        validate_target_curve([{**targets[0], "unlocked_km": "33.9", "cumulative_km": "33.9"}], summary)
    except ValueError:
        check("old consolidation scores rejected", True)
    else:
        check("old consolidation scores rejected", False)
    def varint(n):
        b = bytearray()
        while n > 127:
            b.append((n & 127) | 128)
            n >>= 7
        return bytes(b + bytes([n]))
    timestamp = 1700000000
    raw = varint(32 << 3) + varint(timestamp)
    blob = b"\x0a" + varint(len(raw)) + raw
    header = b"\x0a\x09OSMHeader\x18" + varint(len(blob))
    pbf = len(header).to_bytes(4, "big") + header + blob
    with patch("builtins.open", return_value=io.BytesIO(pbf)):
        result = pbf_header("fixture.osm.pbf")
    check("PBF timestamp comes from header", result["replication_timestamp"] == timestamp)
    with patch("builtins.open", return_value=io.BytesIO(b"abc")), patch("os.path.getsize", return_value=3):
        fp = fingerprint("fixture")
    check("content fingerprints computed", fp["sha256"] ==
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" and
          fp["md5"] == "900150983cd24fb0d6963f7d28e17f72")
    manifest = {"status": "complete", "inputs": {"profile": "old"}, "outputs": {}}
    with patch("os.path.isfile", return_value=True), patch("builtins.open", return_value=io.StringIO(json.dumps(manifest))):
        check("changed profile invalidates run cache", not current_manifest("fixture", {"profile": "new"}, ()))
    manifest["outputs"]["summary.csv"] = {"sha256": "old"}
    with patch("os.path.isfile", return_value=True), patch("builtins.open", mock_open(read_data=json.dumps(manifest))), \
            patch("cross_country.fingerprint", return_value={"sha256": "changed"}):
        check("changed output invalidates run cache",
              not current_manifest("fixture", {"profile": "old"}, ("summary.csv",)))
    with patch("os.path.isfile", return_value=False), patch("builtins.open", mock_open()), \
            patch("cross_country.fingerprint", return_value=fp), patch("cross_country.pbf_header", return_value={}):
        cached = input_provenance("cached.osm.pbf")
    check("unknown cached source is not attributed to a mirror",
          cached["source_url"] is None and "unverified" in cached["source_status"])
    for digest, expected_source in ((fp["md5"], "https://example.invalid/extract"), ("stale", None)):
        record = f"file=cached.osm.pbf\nmd5={digest}\nurl=https://example.invalid/extract\n"
        with patch("os.path.isfile", side_effect=lambda p: str(p).endswith("SNAPSHOT.txt")), \
                patch("builtins.open", mock_open(read_data=record)), \
                patch("cross_country.fingerprint", return_value=fp), patch("cross_country.pbf_header", return_value={}):
            cached = input_provenance("cached.osm.pbf")
        check("legacy source requires matching content checksum " + ("(match)" if expected_source else "(mismatch)"),
              cached["source_url"] == expected_source)
    artifact = {"sha256": "fixture", "md5": "fixture", "bytes": 0}
    with patch("cross_country.fingerprint", return_value=artifact), patch("os.path.isfile", return_value=False), \
            patch("builtins.open", return_value=io.StringIO("model_version\nconditional\n")):
        freshness = sensitivity_freshness("fixture.csv")
    check("missing sensitivity execution link is explicit, not a fabricated date",
          freshness["status"] == "artifact_hashed_execution_link_unverified"
          and freshness["completed_at_utc"] is None and freshness["input_pbf_sha256"] is None)
    execution = {
        "status": "complete", "outputs": {"fixture.csv": artifact},
        "completed_at_utc": "2026-09-15T00:00:00+00:00",
        "inputs": {"pbf": {"sha256": "pbf"}, "jar": artifact, "profile": {"sha256": "profile"},
                   "osm_header": {"replication_timestamp_utc": "2026-08-26T00:00:00+00:00"}}}
    with patch("cross_country.fingerprint", return_value=artifact), patch("os.path.isfile", return_value=True), \
            patch("builtins.open", side_effect=[io.StringIO("model_version\nconditional\n"), io.StringIO(json.dumps(execution))]):
        freshness = sensitivity_freshness("fixture.csv")
    check("verified sensitivity preserves distinct snapshot and execution timestamps",
          freshness["status"] == "execution_manifest_verified"
          and freshness["completed_at_utc"] == execution["completed_at_utc"]
          and freshness["osm_replication_timestamp_utc"] == "2026-08-26T00:00:00+00:00")
    execution["outputs"]["fixture.csv"] = {"sha256": "changed"}
    with patch("cross_country.fingerprint", return_value=artifact), patch("os.path.isfile", return_value=True), \
            patch("builtins.open", side_effect=[io.StringIO("model_version\nconditional\n"), io.StringIO(json.dumps(execution))]):
        try:
            sensitivity_freshness("fixture.csv")
        except ValueError:
            check("changed sensitivity artifact fails freshness verification", True)
        else:
            check("changed sensitivity artifact fails freshness verification", False)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
