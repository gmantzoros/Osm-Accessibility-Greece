#!/usr/bin/env python3
"""Assemble manuscript reporting from existing saved artifacts, never run a census.

python -B analysis\\paper_data.py  ->  results\\paper_data.json
python -B analysis\\paper_data.py --versioned
    -> results\\impact\\unified-conditional-2026-10-09\\paper_data.json

Changing the model or its pinned inputs is a separate, explicit operation.
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from statistics import median

from aggregate import (coverage_check, reporting_paths, verify_corrected_greek,
                       verify_versioned_manifest)
from corrected_model_impact import CORRECTED_VERSION, digest, load_report as load_corrected_impact
from cross_country import eligible_endpoints

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENSUS = os.path.join(ROOT, "results", "census")
ANALYSIS = os.path.join(ROOT, "results", "analysis")
SENS = os.path.join(ROOT, "results", "profile_sensitivity", "profile_sensitivity.csv")

CIRCLE_GROUPS = ["metro", "large_city", "medium_town", "small_town",
                 "island", "tourist_rural", "rural"]
PUBLISHED_DETECTOR = {
    "doi": "10.3390/app152413215",
    "map_provider": "Google Maps",
    "cases": 100,
    "cities": {"Athens": 67, "Thessaloniki": 22, "Patras": 10, "Karystos": 1},
    "initial_success": 67,
    "initial_current_road": 32,
    "initial_failure": 1,
    "resolved_after_movement": 24,
    "final_success": 91,
    "final_failure": 9,
}
BPRI_WEIGHTS = {
    "v0": dict(zip(("legacy_node_coverage", "legacy_node_subtype", "legacy_node_tactile", "sound",
                    "sidewalk", "lit", "surface", "name"), (.20, .15, .15, .10, .15, .10, .05, .10))),
    "v1": dict(zip(("crossing_coverage", "subtype", "markings", "tactile", "sound", "kerb",
                    "sidewalk_affirmative", "lit", "surface", "name"),
                   (.20, .10, .05, .15, .10, .05, .15, .10, .05, .05))),
}
MAPILLARY_LEGACY_ALIASES = {
    "junctions_in_image_positive_tiles": "intersections_testable",
    "junctions_without_image_positive_tile": "intersections_no_imagery",
    "osm_negative_junctions": "omission_candidates",
    "cv_positive_flags": "omission_found",
    "crossings_in_image_positive_tiles": "crossings_testable",
    "crossings_codetected": "crossings_confirmed",
    "general_signal_locations": "osm_signals",
    "general_signal_locations_in_image_positive_tiles": "signals_testable",
    "general_signal_locations_codetected": "signals_confirmed",
    "det_furniture": "det_obstacle",
    "det_surface_objects": "det_surface_hazard",
}
MAPILLARY_V2_FIELDS = {
    "junctions_in_image_positive_tiles": "intersections_image_positive_tile",
    "junctions_without_image_positive_tile": "intersections_tile_unknown",
    "osm_negative_junctions": "osm_negative_junctions_image_positive_tile",
    "cv_positive_flags": "cv_positive_flags_all_dates",
    "crossings_in_image_positive_tiles": "crossings_image_positive_tile",
    "crossings_codetected": "crossings_codetected_all_dates",
    "general_signal_locations": "general_signal_locations",
    "general_signal_locations_in_image_positive_tiles": "general_signal_locations_image_positive_tile",
    "general_signal_locations_codetected": "ped_head_codetected_all_dates",
}
MAPILLARY_RECENT_COUNTS = (
    "cv_positive_flags_recent", "crossings_codetected_recent", "ped_head_codetected_recent")
MAPILLARY_INVENTORY_COUNTS = ("det_street_light", "det_furniture", "det_surface_objects")
MAPILLARY_STATUS_COUNTS = (
    "intersections_tile_empty", "intersections_feature_query_unknown",
    "crossings_feature_query_unknown", "general_signal_locations_feature_query_unknown")
MAPILLARY_SCREENING_COUNTS = ("crossings_screening_eligible", "general_signal_locations_screening_eligible")


def mapillary_screening_denominators(row):
    result = {}
    for key, covered, unknown in (
        ("crossings_screening_eligible", "crossings_in_image_positive_tiles", "crossings_feature_query_unknown"),
        ("general_signal_locations_screening_eligible", "general_signal_locations_in_image_positive_tiles",
         "general_signal_locations_feature_query_unknown"),
    ):
        if row.get(unknown) is None:
            result[key] = None
            continue
        eligible = float(row[covered]) - float(row[unknown])
        if eligible < 0:
            raise ValueError(f"Unknown feature queries exceed image-positive population: {unknown}")
        result[key] = eligible
    return result


def normalize_mapillary_row(row):
    """Translate explicit legacy column names, never missing counts to zero."""
    result = dict(row)
    if "cv_positive_flags_all_dates" in row:
        result["source_schema"] = "co-detection-v2"
        required = ("intersections", "osm_crossings", *MAPILLARY_V2_FIELDS.values(), *MAPILLARY_RECENT_COUNTS)
        for key in required:
            if key not in row or row[key] in ("", None):
                raise ValueError(f"Missing Mapillary v2 count: {key}")
            result[key] = float(row[key])
            if result[key] < 0:
                raise ValueError(f"Negative Mapillary v2 count: {key}")
        for canonical, source in MAPILLARY_V2_FIELDS.items():
            result[canonical] = result[source]
        for key in MAPILLARY_STATUS_COUNTS:
            result[key] = float(row[key]) if key in row else None
        result["junctions_without_image_positive_tile"] += result.get("intersections_tile_empty") or 0
        for key in MAPILLARY_INVENTORY_COUNTS:
            result[key] = None
        result.update(mapillary_screening_denominators(result))
        return result
    result["source_schema"] = ("co-detection" if "cv_positive_flags" in row else "legacy aliases")
    for canonical, legacy in MAPILLARY_LEGACY_ALIASES.items():
        if canonical not in result:
            if legacy not in row:
                raise ValueError(f"Missing Mapillary count {canonical} (legacy: {legacy})")
            result[canonical] = row[legacy]
        if legacy in row and float(row[legacy]) != float(result[canonical]):
            raise ValueError(f"Conflicting Mapillary aliases: {canonical}/{legacy}")
    for key in ("intersections", "osm_crossings", "det_street_light", *MAPILLARY_LEGACY_ALIASES):
        result[key] = float(result[key])
        if result[key] < 0:
            raise ValueError(f"Negative Mapillary count: {key}")
    for key in MAPILLARY_STATUS_COUNTS:
        result[key] = float(row[key]) if key in row else None
    result.update(mapillary_screening_denominators(result))
    return result


def load(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def csv_value(value):
    if value in ("", None):
        return None
    try:
        return float(value)
    except ValueError:
        return value


def sensitivity_freshness(source_path, root=ROOT, jar=None):
    """Report artifact identity and verified execution links, never filesystem dates."""
    from cross_country import fingerprint, JAR
    jar = jar or JAR
    source_hash = fingerprint(source_path)
    with open(source_path, encoding="utf-8", newline="") as fh:
        first = next(csv.DictReader(fh), None)
    if first is None:
        raise ValueError(f"Empty sensitivity source: {source_path}")
    result = {
        "source_file": os.path.relpath(source_path, root), "source_sha256": source_hash["sha256"],
        "model_version": first.get("model_version"),
        "status": "artifact_hashed_execution_link_unverified",
        "completed_at_utc": None, "input_pbf_sha256": None,
        "osm_replication_timestamp_utc": None, "jar_sha256": None, "profile_sha256": None,
        "note": "File identity/model label alone do not establish which input or executable produced it.",
    }
    manifest_path = os.path.join(os.path.dirname(source_path), "run_manifest.json")
    if not os.path.isfile(manifest_path):
        return result
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    expected = manifest.get("outputs", {}).get(os.path.basename(source_path))
    if expected is None:
        result["status"] = "artifact_not_covered_by_execution_manifest"
        return result
    if manifest.get("status") != "complete" or expected != source_hash:
        raise ValueError(f"Incomplete or stale sensitivity execution manifest: {source_path}")
    inputs = manifest["inputs"]
    if inputs["jar"] != fingerprint(jar):
        raise ValueError(f"Sensitivity executable differs from the current JAR: {source_path}")
    for name, digest in inputs.get("analysis_drivers", {}).items():
        if fingerprint(os.path.join(root, "analysis", name))["sha256"] != digest:
            raise ValueError(f"Sensitivity producer changed; regenerate its table: {name}")
    result.update({
        "status": "execution_manifest_verified", "completed_at_utc": manifest.get("completed_at_utc"),
        "input_pbf_sha256": inputs["pbf"]["sha256"], "jar_sha256": inputs["jar"]["sha256"],
        "profile_sha256": inputs["profile"]["sha256"],
        "osm_replication_timestamp_utc": inputs.get("osm_header", {}).get("replication_timestamp_utc"),
        "manifest_file": os.path.relpath(manifest_path, root),
        "manifest_sha256": fingerprint(manifest_path)["sha256"],
        "note": "Snapshot observation time and recorded execution time are distinct; neither is an accessibility inspection date.",
    })
    return result


def _json_file(path):
    if not Path(path).is_file():
        raise FileNotFoundError(f"Missing corrected reporting input: {path}")
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _verified_fingerprint(record, path):
    path = Path(path)
    if (not isinstance(record, dict) or record.get("sha256") != digest(path)
            or record.get("bytes") != path.stat().st_size):
        raise ValueError(f"Run/input fingerprint differs from current file: {path}")


def _verified_run(paths, directory, pbf, profile, strata, greek, outputs):
    """Verify a corrected country/profile run independently of the frozen JAR."""
    root, directory = paths["root"], Path(directory)
    if directory.resolve() != directory:
        raise ValueError(f"Refusing a linked corrected run directory: {directory}")
    manifest = _json_file(directory / "run_manifest.json")
    if manifest.get("status") != "complete":
        raise ValueError(f"Corrected run is not complete: {directory}")
    inputs, command = manifest.get("inputs"), manifest.get("command")
    if (not isinstance(inputs, dict) or not isinstance(command, list) or len(command) < 8
            or command[0] != "java" or command[2] != "-jar"
            or Path(command[3]).resolve() != Path(greek["command"][3]).resolve()
            or Path(command[4]).resolve() != pbf.resolve()
            or Path(command[5]).resolve() != strata.resolve()
            or Path(command[6]).resolve() != directory.resolve()
            or not command[7].startswith("--profile=")
            or Path(command[7].removeprefix("--profile=")).resolve() != profile.resolve()):
        raise ValueError(f"Corrected execution command uses inconsistent inputs: {directory}")
    for key, path in (("pbf", pbf), ("jar", Path(command[3])),
                      ("profile", profile), ("strata", strata)):
        _verified_fingerprint(inputs.get(key), path)
    assets = inputs.get("strata_assets")
    strata_config = _json_file(strata)
    active_layers = [layer for layer in strata_config.get("polygonLayers", [])
                     if layer.get("enabled", True)]
    if not isinstance(assets, dict) or set(assets) != {layer["id"] for layer in active_layers}:
        raise ValueError(f"Corrected run strata assets are incomplete: {directory}")
    for layer in active_layers:
        asset = strata.parent / layer["file"].replace("\\", "/")
        if not asset.is_relative_to(root / "data") or asset.resolve() != asset:
            raise ValueError(f"Corrected strata asset path is unsafe: {asset}")
        _verified_fingerprint(assets[layer["id"]], asset)
    if (inputs["jar"]["sha256"] != greek["input_sha256"]["jar"]
            or {name.replace("\\", "/"): value for name, value in
                inputs.get("java_sources", {}).items()} != greek["source_sha256"]):
        raise ValueError(f"Corrected run uses a different executable or Java source: {directory}")
    for name, expected in inputs.get("analysis_drivers", {}).items():
        if name not in ("cross_country.py", "profile_sensitivity.py") or digest(root / "analysis" / name) != expected:
            raise ValueError(f"Corrected run analysis driver changed: {name}")
    saved_outputs = manifest.get("outputs")
    if not isinstance(saved_outputs, dict) or not set(outputs).issubset(saved_outputs):
        raise ValueError(f"Missing corrected run outputs: {directory}")
    for name, identity in saved_outputs.items():
        if Path(name).name != name:
            raise ValueError(f"Invalid corrected run output name: {name}")
        _verified_fingerprint(identity, directory / name)
    for name in ("summary.csv", "feasibility.csv", "feasibility_global.csv"):
        if name not in saved_outputs:
            continue
        rows = load(directory / name)
        if not rows or any(row.get("model_version") != CORRECTED_VERSION for row in rows):
            raise ValueError(f"Missing or mixed corrected model versions: {directory / name}")
    if "summary.csv" in outputs and "NATIONAL" not in {r["stratum"] for r in load(directory / "summary.csv")}:
        raise ValueError(f"Corrected national inventory missing: {directory}")
    if "NATIONAL" not in {r["stratum"] for r in load(directory / "feasibility.csv")}:
        raise ValueError(f"Corrected national feasibility missing: {directory}")
    return manifest


def verify_unified_inputs(paths, greek):
    """Reject partial/mixed 20-country and three-profile reruns before any report is written."""
    from cross_country import COUNTRIES, GREECE, eligible_endpoints

    root, countries, profiles = paths["root"], paths["countries"], paths["profiles"]
    expected = {slug: (code, name) for slug, code, name, _ in [GREECE, *COUNTRIES]}
    if len(expected) != 20:
        raise ValueError("Expected exactly 20 distinct configured country extracts")
    country_rows = load(countries / "cross_country.csv")
    if (len(country_rows) != len(expected) or len({r.get("code") for r in country_rows}) != len(expected)
            or {r.get("code") for r in country_rows} != {code for code, _ in expected.values()}
            or any(r.get("model_version") != CORRECTED_VERSION
                   or r.get("provenance_status") != "manifest verified"
                   or r.get("jar_sha256") != greek["input_sha256"]["jar"]
                   or r.get("profile_sha256") != greek["input_sha256"]["profile"]
                   for r in country_rows)):
        raise ValueError("Corrected cross-country table is partial or has mixed models/identities")
    provenance = _json_file(countries / "input_provenance.json")
    if (provenance.get("excluded_or_failed") != []
            or set(provenance.get("requested_slugs", [])) != set(expected)
            or len(provenance.get("inputs", [])) != len(expected)):
        raise ValueError("Corrected 20-country provenance is incomplete")
    by_country = {entry.get("country"): entry for entry in provenance["inputs"]}
    if len(by_country) != len(expected):
        raise ValueError("Corrected country provenance has missing or duplicate inputs")
    country_strata = root / "data" / "strata_national.json"
    bvi = root / "data" / "profile-bvi.json"
    for slug, (code, name) in expected.items():
        directory = countries / slug
        pbf = root / "data" / (
            "greece-latest.osm.pbf" if slug == "greece" else f"countries/{slug}-latest.osm.pbf")
        manifest = _verified_run(paths, directory, pbf, bvi, country_strata, greek,
                                 ("summary.csv", "crossing_metrics.csv",
                                  "feasibility.csv", "feasibility_global.csv"))
        row = next(r for r in country_rows if r["code"] == code)
        if (row["country"] != name or row["pbf_sha256"] != manifest["inputs"]["pbf"]["sha256"]
                or row.get("graph_scope") != "extended"):
            raise ValueError(f"Corrected country summary differs from its run: {slug}")
        ref = by_country.get(name, {}).get("immutable_manifest", {})
        rel = ref.get("path", "").replace("\\", "/")
        immutable = root / rel
        if (immutable.resolve() != immutable
                or not immutable.is_relative_to(countries / "input_manifests" / slug)
                or digest(immutable) != ref.get("sha256")
                or rel != row.get("input_manifest_path", "").replace("\\", "/")
                or ref.get("sha256") != row.get("input_manifest_sha256")
                or _json_file(immutable).get("content", {}).get("sha256") != row["pbf_sha256"]):
            raise ValueError(f"Corrected country input record is missing or inconsistent: {slug}")
        national = next(r for r in load(directory / "feasibility.csv") if r["stratum"] == "NATIONAL")
        if (national.get("profile") != "bvi-v0" or national.get("graph_scope") != "extended"
                or float(national["component_threshold_km"]) != 1
                or abs(float(row["pess_usable_pct"]) - eligible_endpoints(national)["pess_usable_pct"]) > .01
                or abs(float(row["opt_usable_pct"]) - eligible_endpoints(national)["opt_usable_pct"]) > .01):
            raise ValueError(f"Corrected country table and feasibility disagree: {slug}")
        inventory = next(r for r in load(directory / "summary.csv") if r["stratum"] == "NATIONAL")
        crossings = next(r for r in load(directory / "crossing_metrics.csv")
                         if r["stratum"] == "NATIONAL")
        for saved_field, source, field in (
            ("drivable_km", inventory, "drivable_km"),
            ("intersections", inventory, "intersections"),
            ("crossings", inventory, "crossings"),
            ("tactile_pct", inventory, "tactile_pct"),
            ("name_pct", inventory, "name_pct"),
            ("sidewalk_pct", inventory, "sidewalk_pct"),
            ("bpri_v1", inventory, "bpri_v1"),
            ("signal_sound_stated", crossings, "signal_sound"),
            ("signal_sound_affirmative", crossings, "signal_sound_yes"),
            ("crossing_sound_stated", crossings, "crossing_sound"),
            ("crossing_conflicts", crossings, "crossing_conflicts"),
        ):
            if abs(float(row[saved_field]) - float(source[field])) > (
                    .51 if saved_field == "drivable_km" else .001):
                raise ValueError(f"Corrected country aggregate differs from census: {slug}/{saved_field}")
    corr = load(countries / "cross_country_correlations.csv")
    if not corr or any(not 2 <= int(r["n"]) <= 20 for r in corr):
        raise ValueError("Corrected country correlations are missing or partial")

    profile_rows = load(profiles / "profile_sensitivity.csv")
    expected_profiles = {"bvi", "bvi-permissive", "wheelchair"}
    scenarios = {r.get("profile") for r in profile_rows}
    if (scenarios != expected_profiles
            or len({(r.get("scenario"), r.get("unit")) for r in profile_rows}) != len(profile_rows)
            or any(r.get("jar_sha256") != greek["input_sha256"]["jar"]
                   or r.get("model_version", CORRECTED_VERSION) != CORRECTED_VERSION
                   for r in profile_rows)):
        raise ValueError("Corrected profile table is partial or has mixed model identities")
    for pid in expected_profiles:
        directory = profiles / pid
        profile = directory / "profile.json"
        original = root / "data" / f"profile-{pid}.json"
        if _json_file(profile) != _json_file(original):
            raise ValueError(f"Corrected profile configuration differs from the pinned source: {pid}")
        manifest = _verified_run(paths, directory, root / "data" / "greece-latest.osm.pbf",
                                 profile, root / "data" / "strata.json", greek,
                                 ("feasibility.csv", "feasibility_global.csv"))
        records = [r for r in profile_rows if r["profile"] == pid]
        if (not records or any(r.get("run_directory") != pid
                               or r.get("profile_sha256") != manifest["inputs"]["profile"]["sha256"]
                               for r in records)):
            raise ValueError(f"Corrected profile table and run disagree: {pid}")
        nominal = [r for r in records if r["unit"] == "NATIONAL"
                   and r["network_scope"] == "full" and r["graph_scope"] == "extended"
                   and float(r["min_usable_component_km"]) == 1]
        if len(nominal) != 1:
            raise ValueError(f"Missing unique corrected national full-graph 1 km profile: {pid}")
        baseline = next(r for r in load(directory / "feasibility.csv") if r["stratum"] == "NATIONAL")
        if (baseline.get("model_version") != CORRECTED_VERSION
                or baseline.get("profile") != nominal[0]["profile_id"]
                or abs(float(nominal[0]["pess_usable_pct"]) - eligible_endpoints(baseline)["pess_usable_pct"]) > .01
                or abs(float(nominal[0]["opt_usable_pct"]) - eligible_endpoints(baseline)["opt_usable_pct"]) > .01):
            raise ValueError(f"Corrected profile table and national result disagree: {pid}")
        native = (load(directory / "feasibility_sensitivity.csv")
                  if "feasibility_sensitivity.csv" in manifest["outputs"]
                  else [{**r, "threshold_km": "1.000"}
                        for r in load(directory / "feasibility.csv")])
        if any(r.get("model_version") != CORRECTED_VERSION for r in native):
            raise ValueError(f"Mixed corrected profile scenario models: {pid}")
        groups = {r["group"] for r in native if r["layer"] == "circle"}
        expected_cases = set()
        for scope, threshold in {(r["graph_scope"], float(r["threshold_km"])) for r in native}:
            for unit in ("NATIONAL", *groups):
                selected = [r for r in native if r["graph_scope"] == scope
                            and float(r["threshold_km"]) == threshold
                            and (r["stratum"] == "NATIONAL" if unit == "NATIONAL"
                                 else r["layer"] == "circle" and r["group"] == unit)]
                if not selected or sum(float(r["walkable_km"]) for r in selected) <= 0:
                    continue
                key = (unit, scope, threshold)
                expected_cases.add(key)
                match = [r for r in records if r["unit"] == unit and r["graph_scope"] == scope
                         and float(r["min_usable_component_km"]) == threshold]
                eligible = sum(float(r["eligible_km"]) for r in selected)
                pess = sum(float(r["pess_eligible_usable_km"]) for r in selected)
                opt = sum(float(r["opt_eligible_usable_km"]) for r in selected)
                if (len(match) != 1 or match[0]["profile_id"] != baseline["profile"]
                        or match[0]["network_scope"] != ("core" if scope == "core" else "full")
                        or abs(float(match[0]["eligible_km"]) - eligible) > .005
                        or abs(float(match[0]["pess_eligible_usable_km"]) - pess) > .005
                        or abs(float(match[0]["opt_eligible_usable_km"]) - opt) > .005
                        or (eligible > 0 and (
                            abs(float(match[0]["pess_usable_pct"]) - 100 * pess / eligible) > .01
                            or abs(float(match[0]["opt_usable_pct"]) - 100 * opt / eligible) > .01))):
                    raise ValueError(f"Corrected profile scenario differs from census: {pid}/{key}")
        if {(r["unit"], r["graph_scope"], float(r["min_usable_component_km"]))
                for r in records} != expected_cases:
            raise ValueError(f"Corrected profile scenario coverage is incomplete: {pid}")

    corrected = next(r for r in load(paths["census"] / "feasibility.csv")
                     if r["stratum"] == "NATIONAL")
    country_greek = next(r for r in load(countries / "greece" / "feasibility.csv")
                         if r["stratum"] == "NATIONAL")
    for name in ("summary.csv", "crossing_metrics.csv", "feasibility.csv"):
        full = next(r for r in load(paths["census"] / name) if r["stratum"] == "NATIONAL")
        national = next(r for r in load(countries / "greece" / name) if r["stratum"] == "NATIONAL")
        if full != national:
            raise ValueError(f"Corrected Greek country and full-strata national rows differ: {name}")
    if digest(paths["census"] / "feasibility_global.csv") != digest(
            countries / "greece" / "feasibility_global.csv"):
        raise ValueError("Corrected Greek country and full-strata global results differ")
    for row in (country_greek, next(r for r in load(profiles / "bvi" / "feasibility.csv")
                                    if r["stratum"] == "NATIONAL")):
        if any(abs(float(corrected[field]) - float(row[field])) > .005
               for field in ("eligible_km", "pess_eligible_usable_km", "opt_eligible_usable_km")):
            raise ValueError("Corrected Greek full-strata, country and BVI profile endpoints disagree")
    return country_rows, profile_rows


def verify_unified_derivatives(paths):
    root, census, analysis = paths["root"], paths["census"], paths["analysis"]
    lookup = root / "data" / "boundaries" / "municipalities.csv"
    manifest = census / "run_manifest.json"
    products = (
        ("aggregate", (manifest, *(census / name for name in
          ("summary.csv", "crossing_metrics.csv", "way_metrics.csv")),
          lookup, root / "analysis" / "aggregate.py"),
         ("spatial_coverage.csv", "by_dimos.csv", "by_urbanisation.csv",
          "by_region.csv", "by_nuts3.csv"), analysis),
        ("exposure", (manifest, census / "feasibility.csv", census / "crossing_metrics.csv",
          lookup, root / "analysis" / "exposure.py"),
         ("exposure_metadata.json", "exposure_bands.csv", "exposure_facts.csv",
          "exposure_by_lau.csv"), analysis),
        ("targeting_distance", (manifest, census / "edit_targets_summary.csv",
          *(census / name for name in
            ("edit_targets.csv", "edit_targets_length_baseline.csv", "edit_targets_efficiency.csv")),
          root / "analysis" / "targeting_distance.py"),
         ("targeting_distance.json", "targeting_distance.csv"), analysis),
        ("urban", (manifest, *(census / name for name in
          ("feasibility.csv", "crossing_metrics.csv", "summary.csv", "destinations.csv")),
          lookup, root / "analysis" / "urban_scope.py"),
         ("top_cities.csv", "scope_laus.json", "urban_summary.json"), paths["urban"]),
        ("campaign", (manifest, census / "edit_targets.csv", census / "edit_targets_summary.csv",
          root / "analysis" / "campaign.py"),
         ("campaign_cost.csv", "campaign_by_place.csv", "campaign_summary.json",
          "maproulette_challenge.json", "maproulette_tasks.geojson"), paths["campaign"]),
    )
    for name, inputs, outputs, directory in products:
        verify_versioned_manifest(paths, name, inputs, outputs, directory)
    archived_scope = _json_file(root / "results" / "urban" / "scope_laus.json")
    corrected_scope = _json_file(paths["urban"] / "scope_laus.json")
    if (corrected_scope.get("top_n") != 50
            or any(corrected_scope.get(key) != archived_scope.get(key)
                   for key in ("top_n", "dimos_codes", "laus"))):
        raise ValueError("Corrected urban scope differs from frozen audit/Mapillary scope")


def _manual_input_paths(paths, names):
    if not isinstance(names, (list, tuple)) or any(not isinstance(name, (str, os.PathLike))
                                                   for name in names):
        raise ValueError("Manual audit source paths must be a list of project files")
    sources = []
    for name in names:
        path = Path(name)
        path = path if path.is_absolute() else paths["root"] / path
        if path.resolve() != path or not path.is_file() or not path.is_relative_to(paths["root"]):
            raise ValueError(f"Manual audit input must be an unlinked project file: {path}")
        sources.append(path)
    return sources


def reporting_source_hashes(paths, manual_inputs=()):
    """Pin corrected outputs, reporting code and archived evidence for rendering."""
    from cross_country import COUNTRIES, GREECE

    root = paths["root"]
    country_slugs = [slug for slug, _, _, _ in [GREECE, *COUNTRIES]]
    profile_ids = ("bvi", "bvi-permissive", "wheelchair")
    greek_outputs = _json_file(paths["census"] / "run_manifest.json")["output_sha256"]
    if any(Path(name).name != name for name in greek_outputs):
        raise ValueError("Unsafe corrected Greek output in execution manifest")
    files = [
        paths["census"] / "run_manifest.json",
        *(paths["census"] / name for name in greek_outputs),
        *(paths["countries"] / name for name in
          ("cross_country.csv", "cross_country_correlations.csv", "input_provenance.json")),
        paths["profiles"] / "profile_sensitivity.csv",
        *(paths["countries"] / slug / name for slug in country_slugs
          for name in ("run_manifest.json", "summary.csv", "crossing_metrics.csv",
                       "feasibility.csv", "feasibility_global.csv")),
        *(paths["profiles"] / pid / name for pid in profile_ids
          for name in ("run_manifest.json", "profile.json", "feasibility.csv",
                       "feasibility_global.csv")),
        *(paths["countries"] / "input_manifests" / slug / name
          for slug in country_slugs
          for name in os.listdir(paths["countries"] / "input_manifests" / slug)
          if name.endswith(".json")),
        *(directory / f"{name}_manifest.json" for name, directory in
          (("aggregate", paths["analysis"]), ("exposure", paths["analysis"]),
           ("targeting_distance", paths["analysis"]), ("urban", paths["urban"]),
           ("campaign", paths["campaign"]))),
        *(paths["analysis"] / name for name in
          ("spatial_coverage.csv", "by_dimos.csv", "by_nuts3.csv", "by_region.csv",
           "by_urbanisation.csv", "exposure_metadata.json", "exposure_bands.csv",
           "exposure_facts.csv", "exposure_by_lau.csv", "targeting_distance.json",
           "targeting_distance.csv")),
        *(paths["urban"] / name for name in
          ("top_cities.csv", "scope_laus.json", "urban_summary.json")),
        *(paths["campaign"] / name for name in
          ("campaign_cost.csv", "campaign_by_place.csv", "campaign_summary.json",
           "maproulette_challenge.json", "maproulette_tasks.geojson")),
        root / "results" / "analysis" / "corrected_model_impact.json",
        root / "results" / "analysis" / "corrected_model_impact.csv",
        root / "results" / "census" / "audit_sample.csv",
        root / "results" / "census" / "continuity_sample.csv",
        root / "results" / "census" / "edit_targets.csv",
        root / "validation" / "continuity_results.csv",
        root / "results" / "urban" / "scope_laus.json",
        root / "results" / "analysis" / "continuity_audit.json",
        root / "results" / "analysis" / "mapillary_radius_sensitivity.json",
        root / "results" / "analysis" / "mapillary_radius_sensitivity.csv",
        root / "results" / "mapillary" / "detections_v2.csv",
        root / "results" / "mapillary" / "osm_points.csv",
        root / "results" / "mapillary" / "co_detection_manifest.json",
        root / "data" / "boundaries" / "greece_municipalities.geojson",
        root / "paper" / "figures" / "src" / "pick_933896235308839.json",
        root / "data" / "SNAPSHOT.txt",
        root / "data" / "boundaries" / "municipalities.csv",
        root / "analysis" / "paper_data.py",
        *(root / "analysis" / f"{name}.py" for name in
          ("aggregate", "exposure", "urban_scope", "campaign", "targeting_distance",
           "corrected_model_impact", "continuity_audit", "local_audit",
           "mapillary_join", "mapillary_fetch", "mapillary_radius_sensitivity")),
        *_manual_input_paths(paths, manual_inputs),
    ]
    files.extend(paths["profiles"] / pid / "feasibility_sensitivity.csv"
                 for pid in profile_ids
                 if (paths["profiles"] / pid / "feasibility_sensitivity.csv").is_file())
    files.extend(root / "paper" / "figures" / "src" / name
                 for name in os.listdir(root / "paper" / "figures" / "src")
                 if "933896235308839" in name and name.lower().endswith((".jpg", ".png", ".jpeg")))
    return {path.relative_to(root).as_posix(): digest(path) for path in files}


def verify_reporting_bundle_sources(paths, bundle):
    if (not paths["versioned"] or bundle.get("reporting", {}).get("mode") != "versioned-corrected"
            or bundle["reporting"].get("model_version") != CORRECTED_VERSION
            or bundle.get("feasibility_national", {}).get("model_version") != CORRECTED_VERSION
            or bundle.get("national", {}).get("model_version") != CORRECTED_VERSION):
        raise ValueError("Not a unified corrected reporting bundle")
    recorded = bundle["reporting"].get("source_sha256")
    manual_inputs = bundle["reporting"].get("manual_inputs", [])
    if (bool(bundle.get("manual_audit", {}).get("available")) != bool(manual_inputs)
            or not isinstance(recorded, dict)
            or recorded != reporting_source_hashes(paths, manual_inputs)):
        raise ValueError("Unified reporting bundle sources changed; rebuild paper_data.py --versioned")
    return bundle


def bpri_from_counts(summary, crossing, version):
    def ratio(numerator, denominator):
        den = float(crossing[denominator])
        return float(crossing[numerator]) / den if den else None
    components = {
        "legacy_node_coverage": ratio("legacy_crossing_nodes", "intersections"),
        "legacy_node_subtype": ratio("legacy_crossing_subtype", "legacy_crossing_nodes"),
        "legacy_node_tactile": ratio("legacy_crossing_tactile", "legacy_crossing_nodes"),
        "crossing_coverage": ratio("crossings", "intersections"),
        "subtype": ratio("crossing_subtype", "crossings"),
        "markings": ratio("markings_any", "crossings"),
        "tactile": ratio("crossing_tactile", "crossings"),
        "sound": ratio("signal_sound", "signal_nodes"),
        "kerb": ratio("crossing_kerb", "crossings"),
        **{component: float(summary[key]) / 100 if summary[key] != "" else None
           for component, key in (("sidewalk", "sidewalk_pct"), ("sidewalk_affirmative", "sidewalk_aff_pct"),
                                  ("lit", "lit_pct"), ("surface", "surface_pct"), ("name", "name_pct"))},
    }
    for key in ("legacy_node_coverage", "crossing_coverage"):
        if components[key] is not None:
            components[key] = min(1, components[key])
    terms = [(weight, components[key]) for key, weight in BPRI_WEIGHTS[version].items()
             if components[key] is not None]
    return sum(weight * value for weight, value in terms) / sum(weight for weight, _ in terms) if terms else None


def load_mapillary_rows(directory):
    paths = [os.path.join(directory, name) for name in
             ("co_detection_by_city_periods.csv", "co_detection_by_city.csv", "validation_by_city.csv")]
    canonical = load(paths[1]) if os.path.isfile(paths[1]) else None
    if canonical is not None:
        if not canonical:
            raise ValueError("Canonical Mapillary co-detection artifact is empty")
        path = (paths[1] if "cv_positive_flags_all_dates" in canonical[0] or not os.path.isfile(paths[0])
                else paths[0])
    else:
        path = next((p for p in (paths[0], paths[2]) if os.path.isfile(p)), None)
    if path is None:
        return [], None, False
    is_v2 = path != paths[2]
    if is_v2 and not os.path.isfile(os.path.join(directory, "co_detection_manifest.json")):
        raise ValueError("Mapillary v2 artifact requires co_detection_manifest.json")
    rows = [normalize_mapillary_row(r) for r in (canonical if path == paths[1] else load(path))]
    if path == paths[0] and canonical is not None:
        compatibility = {r["stratum"]: normalize_mapillary_row(r) for r in canonical}
        if len(compatibility) != len(rows) or {r["stratum"] for r in rows} != set(compatibility):
            raise ValueError("Mapillary period and compatibility artifacts have different scopes")
        for row in rows:
            other = compatibility[row["stratum"]]
            if any(row[key] != other[key] for key in ("intersections", "osm_crossings", *MAPILLARY_V2_FIELDS)):
                raise ValueError(f"Mapillary all-date counts disagree across artifacts: {row['stratum']}")
            if any(row.get(key) is not None and other.get(key) is not None and row[key] != other[key]
                   for key in MAPILLARY_STATUS_COUNTS):
                raise ValueError(f"Mapillary query/tile exclusions disagree across artifacts: {row['stratum']}")
            for key in ("pop_2021", "image_positive_tile_pct"):
                if key in other:
                    row[key] = other[key]
    return rows, path, is_v2


def load_mapillary_radius_sensitivity(mapillary, root=ROOT):
    """Check the saved common-interior comparison against its inputs and main screening."""
    folder = os.path.join(root, "results", "analysis")
    with open(os.path.join(folder, "mapillary_radius_sensitivity.json"), encoding="utf-8") as stream:
        report = json.load(stream)
    if not isinstance(report, dict):
        raise ValueError("Invalid Mapillary radius comparison artifact")
    settings = [("original_clipped", 25.0)] + [("common_interior", r) for r in (10.0, 25.0, 50.0)]
    if (report.get("schema_version") != 1 or report.get("radii_m") != [10.0, 25.0, 50.0]
            or report.get("common_interior_exclusion_m") != 51.0
            or report.get("baseline_verified") is not True
            or report.get("reference_date") != mapillary["reference_date"]
            or report.get("recent_cutoff") != mapillary["recent_cutoff"]):
        raise ValueError("Mapillary radius comparison settings disagree with saved screening")
    expected_sources = {
        "results/mapillary/detections_v2.csv", "results/mapillary/osm_points.csv",
        "results/urban/scope_laus.json", "data/boundaries/greece_municipalities.geojson",
        "analysis/mapillary_join.py", "analysis/mapillary_fetch.py",
        "analysis/mapillary_radius_sensitivity.py",
    }
    sources = report.get("source_hashes")
    if (not isinstance(sources, dict) or len(sources) != len(expected_sources)
            or {k.replace("\\", "/") for k in sources} != expected_sources):
        raise ValueError("Mapillary radius comparison source inventory is incomplete")
    for name, expected in sources.items():
        path = os.path.join(root, *name.replace("\\", "/").split("/"))
        with open(path, "rb") as stream:
            observed = hashlib.sha256(stream.read()).hexdigest()
        if expected != observed:
            raise ValueError(f"Stale Mapillary radius comparison source: {name}")
    national, cities = report.get("national"), report.get("by_city")
    if (not isinstance(national, list) or not isinstance(cities, list)
            or len(national) != len(settings) or any(not isinstance(r, dict) for r in cities)):
        raise ValueError("Invalid Mapillary radius comparison rows")
    scopes = {r["stratum"] for r in mapillary["by_city"]}
    from mapillary_radius_sensitivity import COUNTS
    def check_row(row, name, setting):
        if not isinstance(row, dict) or (row.get("stratum"), row.get("population"), row.get("radius_m")) != (
                name, *setting):
            raise ValueError("Invalid Mapillary radius comparison row identity")
        for field in COUNTS:
            if type(row.get(field)) is not int or row[field] < 0:
                raise ValueError(f"Invalid Mapillary radius comparison count: {field}")
        for rate, numerator, denominator in (
            ("flag_rate_pct", "cv_positive_flags_all_dates", "osm_negative_junctions"),
            ("crossing_codetection_pct", "crossings_codetected_all_dates", "crossing_eligible"),
            ("signal_codetection_pct", "signals_codetected_all_dates", "signal_eligible"),
        ):
            value = round(100 * row[numerator] / row[denominator], 4) if row[denominator] else None
            if row.get(rate) != value:
                raise ValueError(f"Invalid Mapillary radius comparison rate: {rate}")
    for setting, total in zip(settings, national):
        check_row(total, "TOTAL", setting)
        selected = [r for r in cities if (r.get("population"), r.get("radius_m")) == setting]
        if len(selected) != len(scopes) or {r.get("stratum") for r in selected} != scopes:
            raise ValueError("Mapillary radius comparison has missing or duplicate city rows")
        for row in selected:
            check_row(row, row["stratum"], setting)
        if any(sum(r[field] for r in selected) != total[field] for field in COUNTS):
            raise ValueError("Mapillary radius comparison city and national totals disagree")
    if len(cities) != len(scopes) * len(settings):
        raise ValueError("Mapillary radius comparison contains extra city rows")
    for saved, computed in (
        ("intersections", "intersection_total"),
        ("junctions_in_image_positive_tiles", "intersection_image_positive"),
        ("osm_negative_junctions", "osm_negative_junctions"),
        ("cv_positive_flags", "cv_positive_flags_all_dates"),
        ("osm_crossings", "crossing_total"),
        ("crossings_in_image_positive_tiles", "crossing_eligible"),
        ("crossings_codetected", "crossings_codetected_all_dates"),
        ("general_signal_locations", "signal_total"),
        ("general_signal_locations_in_image_positive_tiles", "signal_eligible"),
        ("general_signal_locations_codetected", "signals_codetected_all_dates"),
    ):
        if national[0][computed] != mapillary[saved]:
            raise ValueError(f"Mapillary radius comparison baseline differs on {saved}")
    for field in ("intersection_total", "crossing_total", "signal_total",
                  "intersection_eligible", "crossing_eligible", "signal_eligible"):
        if len({row[field] for row in national[1:]}) != 1:
            raise ValueError(f"Mapillary radius comparison does not use a common frame: {field}")
    for kind in ("intersection", "crossing", "signal"):
        if report.get("boundary_excluded_points", {}).get(kind) != (
                national[0][kind + "_total"] - national[1][kind + "_total"]):
            raise ValueError(f"Mapillary boundary-exclusion count disagrees: {kind}")
    with open(os.path.join(folder, "mapillary_radius_sensitivity.csv"), encoding="utf-8", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    expected_rows = national + cities
    if len(csv_rows) != len(expected_rows) or any(
            {k: "" if v is None else str(v) for k, v in expected.items()} != actual
            for actual, expected in zip(csv_rows, expected_rows)):
        raise ValueError("Mapillary radius JSON and CSV disagree")
    return report


def mapillary_inventory_summary(rows):
    groups = defaultdict(lambda: {"count_all_dates": 0, "count_recent": 0, "last_seen_missing": 0})
    strata = defaultdict(lambda: defaultdict(lambda: {"count_all_dates": 0, "count_recent": 0, "last_seen_missing": 0}))
    for row in rows:
        amounts = {key: int(row[key]) for key in ("count_all_dates", "count_recent", "last_seen_missing")}
        if any(v < 0 for v in amounts.values()) or amounts["count_recent"] + amounts["last_seen_missing"] > amounts["count_all_dates"]:
            raise ValueError("Invalid temporal inventory counts")
        for key, value in amounts.items():
            groups[row["group"]][key] += value
            strata[row["stratum"]][row["group"]][key] += value
    return {
        "count_all_dates": sum(r["count_all_dates"] for r in groups.values()),
        "count_recent": sum(r["count_recent"] for r in groups.values()),
        "last_seen_missing": sum(r["last_seen_missing"] for r in groups.values()),
        "by_group": dict(groups), "by_stratum": {key: dict(value) for key, value in strata.items()},
        "unit": "exported CV object records assigned to scope polygons; not verified physical objects or hazards",
    }


def mapillary_audit_links(rows, current_sample_hash):
    hashes = sorted({r["sample_sha256"] for r in rows})
    matches = bool(rows) and hashes == [current_sample_hash]
    selected = [r for r in rows if r["arm"] in ("commission", "omission")
                and r["image_tile_status"] == "image_positive"
                and r["cv_crossing_ids_all_dates"].strip()]
    by_arm = {arm: sum(r["arm"] == arm for r in selected) for arm in ("commission", "omission")}
    return {
        "rows": len(rows), "sample_sha256": hashes, "matches_current_sample": matches,
        "image_positive_cases": sum(r["image_tile_status"] == "image_positive" for r in rows),
        "primary_cv_positive_by_arm": by_arm, "primary_cv_positive_n": len(selected),
        "current_sample_primary_cv_positive_n": len(selected) if matches else None,
        "recent_primary_cv_positive_n": sum(bool(r["cv_crossing_ids_recent"].strip()) for r in selected),
        "interpretation": "Incidental CV-positive commission/omission cases for optional post-annotation local-feature comparisons; not a separate probability sample or network calibration.",
    }


def audit_sample_summary(rows):
    by_state, mapped_states, sources = defaultdict(int), defaultdict(int), defaultdict(int)
    for row in rows:
        if "mapped_feature_state" in row:
            source = "mapped_feature_state"
        elif "profile_state" in row:
            source = "legacy_profile_state"
        else:
            raise ValueError("Audit sample lacks mapped_feature_state or the legacy profile_state column")
        state = row["mapped_feature_state"] if source == "mapped_feature_state" else row["profile_state"]
        key = f"{row['arm']}/{state.strip() if state and state.strip() else 'not_evaluated'}"
        by_state[key] += 1
        sources[source] += 1
        if source == "mapped_feature_state":
            mapped_states[key] += 1
    return {
        "n": len(rows), "groups": len({r["group"] for r in rows}),
        "by_arm_state": dict(by_state),
        "by_arm_mapped_feature_state": dict(mapped_states) if mapped_states else None,
        "state_sources": dict(sources),
        "state_interpretation": "Commission mapped-feature tag state only; blank sidewalk/omission states are not evaluated. Legacy profile_state labels are retained only as explicitly unvalidated compatibility data.",
        "annotation_scope": "Independent local-feature observations, not graph-state truth or network-band calibration",
    }


def native_sensitivity_rows(rows):
    result = []
    for row in rows:
        if row["stratum"] != "NATIONAL":
            continue
        scope = row["graph_scope"]
        if scope not in ("extended", "core"):
            raise ValueError(f"Unknown graph scope: {scope}")
        walk, eligible = float(row["walkable_km"]), float(row["eligible_km"])
        pess, opt = float(row["pess_usable_km"]), float(row["opt_usable_km"])
        pe, oe = float(row["pess_eligible_usable_km"]), float(row["opt_eligible_usable_km"])
        primary = eligible_endpoints(row)
        threshold = float(row["threshold_km"])
        result.append({
            "profile": row["profile"], "profile_id": row["profile"], "unit": "NATIONAL",
            "graph_scope": scope, "network_scope": "core" if scope == "core" else "full",
            "min_usable_component_km": threshold,
            "scenario": f"{row['profile']}-{scope}-t{threshold:g}",
            "execution_mode": "native-graph-sensitivity",
            "model_version": row.get("model_version", "unspecified"),
            "walkable_km": walk, "eligible_km": eligible,
            "pess_pct": primary["pess_usable_pct"], "opt_pct": primary["opt_usable_pct"],
            "gap_pct": primary["gap_pct"],
            "all_length_pess_pct": 100 * pess / walk if walk else None,
            "all_length_opt_pct": 100 * opt / walk if walk else None,
            "all_length_gap_pct": 100 * (opt - pess) / walk if walk else None,
            "primary_denominator": "eligible nonauxiliary length; threshold uses eligible mass",
            "pess_eligible_usable_km": pe, "opt_eligible_usable_km": oe,
            "eligible_pess_pct": 100 * pe / eligible if eligible else None,
            "eligible_opt_pct": 100 * oe / eligible if eligible else None,
            "eligible_gap_pct": 100 * (oe - pe) / eligible if eligible else None,
        })
    return result


def merge_sensitivity_rows(native, external):
    """Keep native core/threshold rows when verified profile rows are appended."""
    merged = {}
    for row in [*native, *external]:
        scope = row["network_scope"]
        if scope not in ("full", "extended", "core"):
            raise ValueError(f"Unknown sensitivity scope: {scope}")
        threshold = float(row["min_usable_component_km"])
        if not math.isfinite(threshold) or threshold <= 0:
            raise ValueError("Sensitivity thresholds must be finite and positive")
        if any(not math.isfinite(float(row[metric])) for metric in ("pess_pct", "opt_pct", "gap_pct")):
            raise ValueError("Non-finite sensitivity outcome")
        key = (row["profile_id"], row["unit"],
               "full" if scope == "extended" else scope,
               round(threshold, 12))
        current = merged.get(key)
        if current is not None:
            if current.get("model_version") != row.get("model_version"):
                raise ValueError(f"Sensitivity model versions disagree: {key}")
            for metric in ("pess_pct", "opt_pct", "gap_pct"):
                if abs(float(current[metric]) - float(row[metric])) > .01:
                    raise ValueError(f"Equivalent sensitivity outcomes disagree: {key}, {metric}")
            current_verified = current.get("data_freshness", {}).get("status") == "execution_manifest_verified"
            new_verified = row.get("data_freshness", {}).get("status") == "execution_manifest_verified"
            if current_verified and not new_verified:
                continue
        merged[key] = dict(row)
    return list(merged.values())


# English labels for the 50 principal communities of the frozen audit draw.
AUDIT_COMMUNITY_NAMES = dict(zip(
    ["Κοινότητα Αγίων Αναργύρων", "Κοινότητα Αγρινίου", "Κοινότητα Αλεξανδρούπολης",
     "Κοινότητα Αχαρνών", "Κοινότητα Βέροιας", "Κοινότητα Βόλου", "Κοινότητα Γιαννιτσών",
     "Κοινότητα Ευόσμου", "Κοινότητα Ιωαννιτών", "Κοινότητα Καβάλας", "Κοινότητα Καλαμάτας",
     "Κοινότητα Κατερίνης", "Κοινότητα Κερατσινίου", "Κοινότητα Κερκυραίων", "Κοινότητα Κηφισιάς",
     "Κοινότητα Κοζάνης", "Κοινότητα Κομοτηνής", "Κοινότητα Λαμιέων", "Κοινότητα Νικαίας",
     "Κοινότητα Ξάνθης", "Κοινότητα Πυλαίας", "Κοινότητα Ρόδου", "Κοινότητα Σερρών",
     "Κοινότητα Σταυρουπόλεως", "Κοινότητα Συκεών", "Κοινότητα Τρικκαίων", "Κοινότητα Χαλκιδέων",
     "Κοινότητα Χανίων", "Ψευδοκοινοτητα Αγιου Δημητριου", "Ψευδοκοινοτητα Αθηναιων",
     "Ψευδοκοινοτητα Αιγαλεω", "Ψευδοκοινοτητα Αμαρουσιου", "Ψευδοκοινοτητα Βυρωνος",
     "Ψευδοκοινοτητα Γλυφαδας", "Ψευδοκοινοτητα Ζωγραφου", "Ψευδοκοινοτητα Ηλιουπολεως",
     "Ψευδοκοινοτητα Ηρακλειου", "Ψευδοκοινοτητα Θεσσαλονικης", "Ψευδοκοινοτητα Ιλιου",
     "Ψευδοκοινοτητα Καλαμαριας", "Ψευδοκοινοτητα Καλλιθεας", "Ψευδοκοινοτητα Κορυδαλλου",
     "Ψευδοκοινοτητα Λαρισαιων", "Ψευδοκοινοτητα Νεας Ιωνιας", "Ψευδοκοινοτητα Νεας Σμυρνης",
     "Ψευδοκοινοτητα Παλαιου Φαληρου", "Ψευδοκοινοτητα Πατρεων", "Ψευδοκοινοτητα Πειραιως",
     "Ψευδοκοινοτητα Περιστεριου", "Ψευδοκοινοτητα Χαλανδριου"],
    ["Agioi Anargyroi", "Agrinio", "Alexandroupoli", "Acharnes", "Veroia", "Volos", "Giannitsa",
     "Evosmos", "Ioannina", "Kavala", "Kalamata", "Katerini", "Keratsini", "Corfu", "Kifisia",
     "Kozani", "Komotini", "Lamia", "Nikaia", "Xanthi", "Pylaia", "Rhodes", "Serres",
     "Stavroupoli", "Sykies", "Trikala", "Chalkida", "Chania", "Agios Dimitrios", "Athens",
     "Aigaleo", "Marousi", "Vyronas", "Glyfada", "Zografou", "Ilioupoli", "Heraklion",
     "Thessaloniki", "Ilion", "Kalamaria", "Kallithea", "Korydallos", "Larissa", "Nea Ionia",
     "Nea Smyrni", "Palaio Faliro", "Patras", "Piraeus", "Peristeri", "Chalandri"], strict=True))


def audit_domains(primary, reference, pools):
    """Equal-community sidewalk estimates per density-proxy class; unweighted road-class counts."""
    from local_audit import estimate, outcome
    ys, groups, roads = defaultdict(list), {}, defaultdict(lambda: [0, 0, 0])
    for row in primary:
        if row["arm"] != "sidewalk":
            continue
        y = outcome(row, "sidewalk_present", reference)
        ys[row["stratum"]].append(y)
        groups[row["stratum"]] = row["group"]
        road = re.fullmatch(r"no sidewalk statement on this (\S+) \(way \d+\)", row["osm_assertion"])
        if not road:
            raise ValueError("Unexpected sidewalk assertion: " + row["case_id"])
        roads[road.group(1)][2 if y is None else 1 - y] += 1
    density = {}
    for group in sorted(set(groups.values())):
        cities = {c: v for c, v in ys.items() if groups[c] == group}
        values = [y for v in cities.values() for y in v]
        density[group] = {
            "cities": len(cities), "n": len(values),
            "positive": values.count(1), "negative": values.count(0),
            "equal_city": estimate(cities, {c: 1 / len(cities) for c in cities},
                                   {c: pools[c] for c in cities})}
    return {"density_proxy": density,
            "road_class": {road: dict(zip(("positive", "negative", "unresolved"), counts))
                           for road, counts in sorted(roads.items())},
            "road_class_note": "Unweighted sample counts; road classes are not sampling strata."}


def local_audit_result(primary_path=None, repeat_path=None, reference_date=None,
                       sample_path=None, input_path=None):
    """Analyse only explicitly supplied exports, never manufacture pending results."""
    if primary_path is None:
        if repeat_path is not None or reference_date is not None:
            raise ValueError("Audit repeat/reference options require --audit-results")
        return {"available": False, "status": "not_supplied",
                "note": "No local-audit export supplied; no manual outcomes inferred."}
    from local_audit import analyse, digest_file, load_expected_sample
    sample_path = sample_path or os.path.join(CENSUS, "audit_sample.csv")
    input_path = input_path or os.path.join(ROOT, "data", "greece-latest.osm.pbf")
    cases, manifest = load_expected_sample(sample_path, input_path)
    with open(primary_path, encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if repeat_path is not None:
        with open(repeat_path, encoding="utf-8-sig", newline="") as stream:
            repeated = list(csv.DictReader(stream))
        if any(row.get("round") != "repeat" for row in repeated):
            raise ValueError("--audit-repeat-results must contain only repeat rows")
        rows.extend(repeated)
    result = analyse(rows, reference_date, expected_cases=cases, manifest=manifest)
    result["city_labels"] = {
        row["city"]: AUDIT_COMMUNITY_NAMES.get(row["city"].removeprefix("DIMOS:"),
                                               row["city"].removeprefix("DIMOS:"))
        for row in result["city_arm_counts"]}
    result["city_groups"] = {case["stratum"]: case["group"] for case in cases}
    primary = [r for r in rows if r["round"] == "primary"]
    dates = sorted(r["audit_date"] for r in primary if r.get("completed") == "true")
    result["primary_audit_dates"] = [dates[0], dates[-1]] if dates else None
    result["domains"] = audit_domains(
        primary, dt.date.fromisoformat(result["reference_date"]),
        {row["city"]: row["pool_n"] for row in result["city_arm_counts"]})
    result["available"] = True
    # The repeat round was withdrawn (protocol amendment 2026-11-18): no repeat rows = primary_complete.
    result["status"] = ("complete" if result["full_primary"] and result["full_repeat"]
                        else "primary_complete" if result["full_primary"]
                        and result["repeat_status"] == "not_supplied"
                        else "primary_complete_repeat_pending" if result["full_primary"]
                        else "in_progress")
    result["sources"] = {
        "primary_export": {"file": os.path.basename(primary_path), "sha256": digest_file(primary_path)},
        "sample": {"file": os.path.basename(sample_path), "sha256": manifest["sample_sha256"]},
        "input": {"file": os.path.basename(input_path), "sha256": manifest["input_sha256"]},
        "analysis_code": {"file": "analysis\\local_audit.py",
                          "sha256": digest_file(os.path.join(ROOT, "analysis", "local_audit.py"))},
    }
    if repeat_path is not None:
        result["sources"]["repeat_export"] = {
            "file": os.path.basename(repeat_path), "sha256": digest_file(repeat_path)}
    return result


def load_targeting_distance(feasibility, targeting, root=ROOT, *, versioned=False):
    """Verify saved complete-prefix comparisons against the fixed worklists."""
    from targeting_distance import analyze_sources

    path = reporting_paths(root, versioned)["analysis"] / "targeting_distance.json"
    with open(path, encoding="utf-8") as stream:
        saved = json.load(stream)
    if not isinstance(saved, dict):
        raise ValueError("Invalid targeting-distance artifact")
    expected, _ = analyze_sources(root, versioned=versioned)
    settings = {
        "model_version": feasibility["model_version"],
        "profile_id": feasibility["profile"],
        "graph_scope": feasibility["graph_scope"],
        "component_threshold_km": feasibility["fragmentation"]["min_usable_component_km"],
        "intervention_model": targeting["intervention_model"],
        "usable_km_before": targeting["usable_km_before"],
    }
    for field, value in settings.items():
        if saved.get(field) != value or expected.get(field) != value:
            raise ValueError(f"Targeting-distance {field} differs from the manuscript model")
    for field, report_field in (("model_version", "model_version"),
                                ("profile", "profile_id"),
                                ("graph_scope", "graph_scope"),
                                ("component_threshold_km", "component_threshold_km")):
        if targeting.get(field) != settings[report_field]:
            raise ValueError(f"Targeting summary {field} differs from national feasibility")
    if (feasibility["fragmentation"]["model_version"] != settings["model_version"]
            or feasibility["fragmentation"]["graph_scope"] != settings["graph_scope"]):
        raise ValueError("National fragmentation settings differ from targeting model")

    sources = saved.get("source_artifacts")
    if not isinstance(sources, dict):
        raise ValueError("Missing targeting-distance source artifacts")
    for name, source in expected["source_artifacts"].items():
        recorded = sources.get(name)
        if (not isinstance(recorded, dict)
                or not isinstance(recorded.get("path"), str)
                or recorded["path"].replace("\\", "/") != source["path"].replace("\\", "/")
                or recorded.get("sha256") != source["sha256"]):
            raise ValueError(f"Stale targeting-distance source artifact: {name}")

    def canonical_paths(report):
        normalized = deepcopy(report)
        for section, field in (("source_artifacts", "path"), ("strategies", "source_file")):
            entries = normalized.get(section)
            if not isinstance(entries, dict):
                raise ValueError(f"Invalid targeting-distance {section}")
            for entry in entries.values():
                if not isinstance(entry, dict) or not isinstance(entry.get(field), str):
                    raise ValueError(f"Invalid targeting-distance {section} path")
                entry[field] = entry[field].replace("\\", "/")
        return normalized

    if canonical_paths(saved) != canonical_paths(expected):
        raise ValueError("Saved targeting-distance comparisons differ from the fixed ranked worklists")
    return saved


def value_distributions(rows, top_k=8):
    """Merge by feature universe and exact value before ranking or truncation."""
    grouped = defaultdict(lambda: defaultdict(float))
    for row in rows:
        if row["stratum"] != "NATIONAL":
            continue
        kind, universe, key = row["feature_type"], row["universe"], row["key"]
        grouped[(kind, universe, key)][row["value"]] += num(
            row["km"] if kind == "way" else row["count"])
    output = {}
    for (kind, universe, key), values in sorted(grouped.items()):
        total = sum(values.values())
        entries = [{"value": value, "amount": amount, "pct": 100 * amount / total if total else 0}
                   for value, amount in sorted(values.items(), key=lambda item: (-item[1], item[0]))]
        output[f"{kind}/{universe}/{key}"] = {
            "key": key, "feature_type": kind, "universe": universe,
            "unit": "km" if kind == "way" else "features",
            "denominator": f"{universe} {kind} records carrying canonical {key}",
            "tag_scope": "canonical key only; not the wider tag family",
            "total": total, "values": entries, "top": entries[:top_k],
            "other_amount": sum(e["amount"] for e in entries[top_k:]),
        }
    return output


def population_provenance(root=ROOT):
    lookup = load(os.path.join(root, "data", "boundaries", "municipalities.csv"))
    return {
        "source": "Eurostat GISCO LAU_RG_01M_2021_4326.geojson",
        "source_field": "POP_2021", "csv_alias": "pop_2021",
        "interpretation": "historical GISCO population-field weights, not established 2021/current population",
        "reference_year": "unverified per LAU; Greek total and Athens match revised 2011",
        "field_sum": sum(num(r["pop_2021"]) for r in lookup),
        "laus": len(lookup), "dimos_prefix_groups": len({r["dimos_code"] for r in lookup}),
        "elstat_2011_revised_total": 10816286, "elstat_2021_total": 10482487,
        "elstat_2011_athens": 664046, "elstat_2021_athens": 643452,
        "sources": ["https://www.statistics.gr/2011-census-pop-hous",
                    "https://www.statistics.gr/documents/20181/17286366/FEK-2024-Tefxos-B-02090.pdf/d4daffb1-7210-a172-9729-52d4771ae785"],
        "caution": "No denominator-only census substitution; spatial numerator requires a complete crosswalk.",
    }


def main(audit_results=None, audit_repeat_results=None, audit_reference_date=None,
         *, versioned=False, root=ROOT):
    paths = reporting_paths(root, versioned)
    manual_inputs = (_manual_input_paths(paths, [
        name for name in (audit_results, audit_repeat_results) if name is not None])
        if versioned else [])
    greek = verify_corrected_greek(paths) if versioned else None
    if versioned:
        verify_unified_inputs(paths, greek)
        verify_unified_derivatives(paths)
    ROOT = os.fspath(paths["root"])
    CENSUS = os.fspath(paths["census"])
    ANALYSIS = os.fspath(paths["analysis"])
    SENS = os.fspath(paths["profiles"] / "profile_sensitivity.csv")
    for f in ("summary.csv", "crossing_metrics.csv", "feasibility.csv",
              "provenance.csv", "tag_values.csv"):
        if not os.path.isfile(os.path.join(CENSUS, f)):
            sys.exit(f"Missing {os.path.join(CENSUS, f)} - run the census first.")

    S = {r["stratum"]: r for r in load(os.path.join(CENSUS, "summary.csv"))}
    C = {r["stratum"]: r for r in load(os.path.join(CENSUS, "crossing_metrics.csv"))}
    F = {r["stratum"]: r for r in load(os.path.join(CENSUS, "feasibility.csv"))}
    P = load(os.path.join(CENSUS, "provenance.csv"))
    V = load(os.path.join(CENSUS, "tag_values.csv"))
    summary_rows = load(os.path.join(CENSUS, "summary.csv"))

    d = {}
    d["population_provenance"] = population_provenance(ROOT)
    d["bpri_definition"] = {
        "formula": "sum(w_j * x_j for defined components) / sum(w_j for defined components)",
        "range": [0, 1], "zero_denominator": "component omitted; weights renormalised",
        "rationale": "Author-specified descriptive evidence index; weights are not empirically calibrated.",
        "v0_schema": "raw node-only crossing inventory before node/way unification",
        "v1_schema": "unified crossing-feature inventory",
        "comparison_caution": "Versions differ in crossing universe, components and weights; their difference is not a pure schema ablation.",
        "components": {
            "legacy_node_coverage": "min(1, legacy_crossing_nodes / shared-way structural intersections)",
            "legacy_node_subtype": "legacy crossing nodes with subtype / legacy_crossing_nodes",
            "legacy_node_tactile": "legacy crossing nodes with tactile statement / legacy_crossing_nodes",
            "crossing_coverage": "min(1, unified crossings / structural intersections)",
            "subtype": "crossings with subtype / unified crossings",
            "markings": "crossings with modern or legacy markings / unified crossings",
            "tactile": "crossings with any tactile statement / unified crossings",
            "sound": "signal nodes with any sound statement / signal nodes",
            "kerb": "crossings with any kerb statement / unified crossings",
            "sidewalk": "drivable km with any sidewalk-family statement / drivable km",
            "sidewalk_affirmative": "drivable km with affirmative sidewalk-family evidence / drivable km",
            "lit": "drivable km with lit-family statement / drivable km",
            "surface": "drivable km with surface-family statement / drivable km",
            "name": "drivable km with name-family statement / drivable km"},
        "v0_weights": BPRI_WEIGHTS["v0"], "v1_weights": BPRI_WEIGHTS["v1"],
        "weights": BPRI_WEIGHTS,
    }

    # ---------------------------------------------------------------- snapshot
    snap = {}
    snap_file = os.path.join(ROOT, "data", "SNAPSHOT.txt")
    if os.path.isfile(snap_file):
        for line in open(snap_file, encoding="utf-8-sig"):
            if "=" in line:
                k, v = line.strip().split("=", 1)
                snap[k] = v
    d["snapshot"] = snap
    from cross_country import input_provenance
    pbf_path = os.path.join(ROOT, "data", "greece-latest.osm.pbf")
    if os.path.isfile(pbf_path):
        provenance = input_provenance(pbf_path)
        d["snapshot"]["legacy_record"] = dict(snap)
        d["snapshot"].update({
            "osm_header": provenance["osm_header"],
            "md5": provenance["content"]["md5"], "sha256": provenance["content"]["sha256"],
            "size_bytes": provenance["content"]["bytes"],
            "url": provenance["source_url"], "source_status": provenance["source_status"],
            "legacy_snapshot_record": provenance["legacy_snapshot_record"],
        })

    # ---------------------------------------------------------------- national
    n, cn, fn = S["NATIONAL"], C["NATIONAL"], F["NATIONAL"]
    d["national"] = {
        "drivable_km": num(n["drivable_km"]), "pedestrian_km": num(n["pedestrian_km"]),
        "separate_sidewalk_km": num(n["separate_sidewalk_km"]),
        "intersections": int(num(n["intersections"])), "crossings": int(num(n["crossings"])),
        "crossings_per_100_intersections": num(n["crossings_per_100_intersections"]),
        "junctions_degree3": int(cn["junctions_degree3"]),
        "junctions": int(cn["junctions_degree3"]),
        "crossings_per_100_junctions": csv_value(cn["crossings_per_100_junctions"]),
        "crossing_conflicts": int(cn["crossing_conflicts"]),
        "crossing_signalised": int(cn["crossing_signalised"]),
        "intersection_definition": "shared nodes of distinct drivable ways; sensitive to way segmentation",
        "junction_definition": "degree-three-or-more drivable-network nodes",
        "signals": int(num(n["signals"])), "signal_sound": int(num(cn["signal_sound"])),
        "signal_sound_stated": int(num(cn["signal_sound"])),
        "signal_sound_affirmative": int(num(cn["signal_sound_yes"])),
        "sound_yes_pct": num(n["sound_yes_pct"]),
        "crossing_sound_stated": int(cn["crossing_sound"]),
        "crossing_sound_affirmative": int(cn["crossing_sound_yes"]),
        "crossing_sound_universe": "unified crossing features; separate from traffic-signal nodes",
        "sound_counts_not_additive": "Signal-node and crossing-feature universes can overlap; neither is a physical inventory.",
        "legacy_crossing_nodes": int(cn["legacy_crossing_nodes"]),
        "legacy_crossing_subtype": int(cn["legacy_crossing_subtype"]),
        "legacy_crossing_tactile": int(cn["legacy_crossing_tactile"]),
        "sound_universe": "highway=traffic_signals nodes; statements are not physical signal counts",
        "road_completeness_universe": "drivable way length, tag-family presence",
        "sound_pct": num(n["sound_pct"]),
        "name_pct": num(n["name_pct"]),
        "sidewalk_pct": num(n["sidewalk_pct"]), "sidewalk_aff_pct": num(n["sidewalk_aff_pct"]),
        "surface_pct": num(n["surface_pct"]),
        "lit_pct": num(n["lit_pct"]), "lit_aff_pct": num(n["lit_aff_pct"]),
        "maxspeed_pct": num(n["maxspeed_pct"]), "smoothness_pct": num(n["smoothness_pct"]),
        "subtype_pct": num(n["subtype_pct"]), "markings_any_pct": num(n["markings_any_pct"]),
        "markings_modern_share_pct": num(n["markings_modern_share_pct"]),
        "tactile_stated": int(num(cn["crossing_tactile"])),
        "tactile_affirmative": int(num(cn["crossing_tactile_yes"])),
        "tactile_pct": num(n["tactile_pct"]), "tactile_yes_pct": num(n["tactile_yes_pct"]),
        "kerb_stated": int(num(cn["crossing_kerb"])),
        "kerb_accessible": int(num(cn["crossing_kerb_accessible"])),
        "kerb_barrier": int(num(cn["crossing_kerb_barrier"])),
        "kerb_pct": num(n["kerb_pct"]),
        "schema_node_only": int(num(cn["schema_node_only"])),
        "schema_way_only": int(num(cn["schema_way_only"])),
        "schema_both": int(num(cn["schema_both"])),
        "markings_modern": int(num(cn["markings_modern"])),
        "markings_legacy": int(num(cn["markings_legacy"])),
        "markings_any": int(num(cn["markings_any"])),
        "bpri_v0": num(n["bpri_v0"]), "bpri_v1": num(n["bpri_v1"]),
        "model_version": n["model_version"],
    }

    # ------------------------------------------------------------- feasibility
    def feas(rows):
        w = sum(num(F[r]["walkable_km"]) for r in rows if r in F)
        go = sum(num(F[r]["unknown_graph_only_km"]) for r in rows if r in F)
        p = sum(num(F[r]["pess_usable_km"]) for r in rows if r in F)
        o = sum(num(F[r]["opt_usable_km"]) for r in rows if r in F)
        result = {
            "walkable_km": w, "core_km": None, "graph_only_unknown_km": go,
            "all_length_pess_pct": 100 * p / w if w else None,
            "all_length_opt_pct": 100 * o / w if w else None,
            "all_length_gap_pct": 100 * (o - p) / w if w else None,
            "primary_denominator": "eligible nonauxiliary length; threshold uses eligible component mass",
            "state_denominator": "all graph way length; node transitions have zero length",
            "core_pess_pct": None, "core_opt_pct": None, "core_gap_pct": None,
            "core_status": "requires reconstructed core-only graph; not arithmetic denominator adjustment",
            "pass_pct": sum(num(F[r]["pass_km"]) for r in rows if r in F) / w * 100 if w else 0,
            "decidable_pct": sum(num(F[r]["pass_km"]) + num(F[r]["block_km"])
                                 for r in rows if r in F) / w * 100 if w else 0,
            "unknown_pct": sum(num(F[r]["unknown_km"]) for r in rows if r in F) / w * 100 if w else 0,
            "block_pct": sum(num(F[r]["block_km"]) for r in rows if r in F) / w * 100 if w else 0,
        }
        selected = [F[r] for r in rows if r in F]
        for key in ("eligible_km", "auxiliary_km", "pess_eligible_usable_km",
                    "opt_eligible_usable_km", "missing_evidence_unknown_km", "policy_unknown_km"):
            result[key] = sum(float(r[key]) for r in selected) if selected and all(key in r for r in selected) else None
        eligible = result["eligible_km"]
        if selected:
            primary = eligible_endpoints(result)
            result.update({"pess_pct": primary["pess_usable_pct"], "opt_pct": primary["opt_usable_pct"],
                           "gap_pct": primary["gap_pct"], "pess_usable_km": primary["pess_usable_km"],
                           "opt_usable_km": primary["opt_usable_km"]})
        else:
            result.update({"pess_pct": None, "opt_pct": None, "gap_pct": None,
                           "pess_usable_km": None, "opt_usable_km": None})
        for target, source in (("eligible_pess_pct", "pess_eligible_usable_km"),
                               ("eligible_opt_pct", "opt_eligible_usable_km")):
            result[target] = 100 * result[source] / eligible if eligible else None
        result["eligible_gap_pct"] = (
            result["eligible_opt_pct"] - result["eligible_pess_pct"] if eligible else None)
        result["graph_scopes"] = sorted({r.get("graph_scope", "legacy unspecified") for r in selected})
        result["graph_scope"] = result["graph_scopes"][0] if len(result["graph_scopes"]) == 1 else "mixed"
        result["decided_pct"] = result["decidable_pct"]
        result["eligible_decidability_gap_pct"] = result["eligible_gap_pct"]
        return result

    d["feasibility_national"] = feas(["NATIONAL"])
    d["feasibility_national"]["profile"] = fn["profile"]
    d["feasibility_national"]["model_version"] = fn.get("model_version", "legacy unspecified")
    d["feasibility_national"]["undecidable"] = {
        "road_sidewalk_km": num(fn["unknown_road_sidewalk_km"]),
        "crossing_attrs_km": num(fn["unknown_crossing_attrs_km"]),
        "steps_km": num(fn["unknown_steps_km"]),
        "pedestrian_other_km": num(fn["unknown_pedestrian_other_km"]),
        "graph_only_km": num(fn["unknown_graph_only_km"]),
        "other_km": num(fn.get("unknown_other_km")),
    }
    d["feasibility_national"]["crossing_edges"] = {
        "pass": int(num(fn["crossing_edges_pass"])),
        "unknown": int(num(fn["crossing_edges_unknown"])),
        "block": int(num(fn["crossing_edges_block"])),
    }
    glob_path = os.path.join(CENSUS, "feasibility_global.csv")
    if os.path.isfile(glob_path):
        g = load(glob_path)[0]
        d["feasibility_national"]["fragmentation"] = {
            "edges": int(num(g["edges"])),
            "pess_components": int(num(g["pess_components"])),
            "pess_largest_km": num(g["pess_largest_km"]),
            "opt_components": int(num(g["opt_components"])),
            "opt_largest_km": num(g["opt_largest_km"]),
            "min_usable_component_km": num(g["min_usable_component_km"]),
            "graph_scope": g.get("graph_scope", "legacy unspecified"),
            "unknown_node_transitions": csv_value(g.get("unknown_node_transitions")),
            "blocked_node_transitions": csv_value(g.get("blocked_node_transitions")),
            "model_version": g.get("model_version", "legacy unspecified"),
        }
    d["feasibility_national"]["pedestrian_tactile_km"] = num(fn["pedestrian_tactile_km"])
    d["feasibility_national"]["pedestrian_tactile_pct"] = num(fn["pedestrian_tactile_pct"])

    # ------------------------------------------------- circle-stratum gradient
    groups = defaultdict(list)
    for r in summary_rows:
        if r["layer"] == "circle":
            groups[r["group"]].append(r["stratum"])
    grad = []
    for g in CIRCLE_GROUPS:
        names = groups.get(g, [])
        if not names:
            continue
        km = sum(num(S[x]["drivable_km"]) for x in names)
        ix = sum(num(C[x]["intersections"]) for x in names)
        junctions = sum(float(C[x]["junctions_degree3"]) for x in names)
        cr = sum(num(C[x]["crossings"]) for x in names)
        tac = sum(num(C[x]["crossing_tactile"]) for x in names)
        sg = sum(num(C[x]["signal_nodes"]) for x in names)
        snd = sum(num(C[x]["signal_sound"]) for x in names)
        fe = feas(names)
        grad.append({
            "group": g, "n_strata": len(names), "drivable_km": km,
            "name_pct": sum(num(S[x]["name_pct"]) / 100 * num(S[x]["drivable_km"]) for x in names) / km * 100 if km else 0,
            "sidewalk_pct": sum(num(S[x]["sidewalk_pct"]) / 100 * num(S[x]["drivable_km"]) for x in names) / km * 100 if km else 0,
            "crossings": int(cr), "crossings_per_100_intersections": 100 * cr / ix if ix else 0,
            "junctions_degree3": int(junctions),
            "crossings_per_100_junctions": 100 * cr / junctions if junctions else None,
            "tactile_pct": 100 * tac / cr if cr else 0, "sound_pct": 100 * snd / sg if sg else 0,
            "bpri_v1": sum(num(S[x]["bpri_v1"]) * num(S[x]["drivable_km"]) for x in names) / km if km else 0,
            **{k: v for k, v in fe.items() if k in ("walkable_km", "eligible_km", "core_km", "pess_pct", "opt_pct",
                                                   "gap_pct", "core_pess_pct", "all_length_pess_pct",
                                                   "all_length_opt_pct", "all_length_gap_pct")},
        })
    d["gradient"] = grad

    # ------------------------------------- official NUTS-3 urban-rural typology
    if os.path.isfile(os.path.join(ANALYSIS, "by_nuts3.csv")):
        N = load(os.path.join(ANALYSIS, "by_nuts3.csv"))
        byt = defaultdict(list)
        for r in N:
            byt[r["group"]].append(r)
        typ = []
        for k in ["predominantly_urban", "intermediate", "predominantly_rural"]:
            rs = byt.get(k, [])
            if not rs:
                continue
            km = sum(num(r["drivable_km"]) for r in rs)
            names = [r["stratum"] for r in rs]
            ix = sum(num(C[x]["intersections"]) for x in names)
            junctions = sum(float(C[x]["junctions_degree3"]) for x in names)
            cr = sum(num(C[x]["crossings"]) for x in names)
            tac = sum(num(C[x]["crossing_tactile"]) for x in names)
            sg = sum(num(C[x]["signal_nodes"]) for x in names)
            snd = sum(num(C[x]["signal_sound"]) for x in names)
            fe = feas(names)
            typ.append({
                "typology": k, "units": len(rs), "drivable_km": km,
                "name_pct": sum(num(r["name_pct"]) / 100 * num(r["drivable_km"]) for r in rs) / km * 100 if km else 0,
                "crossings_per_100_intersections": 100 * cr / ix if ix else 0,
                "junctions_degree3": int(junctions),
                "crossings_per_100_junctions": 100 * cr / junctions if junctions else None,
                "tactile_pct": 100 * tac / cr if cr else 0,
                "sound_pct": 100 * snd / sg if sg else 0,
                "bpri_v1": sum(num(r["bpri_v1"]) * num(r["drivable_km"]) for r in rs) / km if km else 0,
                "eligible_km": fe["eligible_km"],
                "pess_pct": fe["pess_pct"], "opt_pct": fe["opt_pct"], "gap_pct": fe["gap_pct"],
            })
        d["nuts3_typology"] = typ

    # ------------------------------------------------------------ urbanisation
    if os.path.isfile(os.path.join(ANALYSIS, "by_urbanisation.csv")):
        d["urbanisation"] = [{
            "class": r["unit"], "n_lau": int(num(r["n_lau"])), "pop": num(r["pop_2021"]),
            "drivable_km": num(r["drivable_km"]), "name_pct": num(r["name_pct"]),
            "sidewalk_pct": num(r["sidewalk_pct"]),
            "crossings_per_100_intersections": num(r["crossings_per_100_intersections"]),
            "junctions_degree3": int(float(r["junctions_degree3"])),
            "crossings_per_100_junctions": csv_value(r["crossings_per_100_junctions"]),
            "tactile_pct": num(r["tactile_pct"]), "sound_pct": num(r["sound_pct"]),
        } for r in load(os.path.join(ANALYSIS, "by_urbanisation.csv"))]

    # ----------------------------------------------------------------- regions
    if os.path.isfile(os.path.join(ANALYSIS, "by_region.csv")):
        R = load(os.path.join(ANALYSIS, "by_region.csv"))
        R.sort(key=lambda r: -num(r["crossings_per_100_intersections"]))
        d["regions"] = [{
            "region": r["stratum"].replace("PERIF:", ""),
            "drivable_km": num(r["drivable_km"]), "name_pct": num(r["name_pct"]),
            "crossings_per_100_intersections": num(r["crossings_per_100_intersections"]),
            "junctions_degree3": int(float(r["junctions_degree3"])),
            "crossings_per_100_junctions": csv_value(r["crossings_per_100_junctions"]),
            "tactile": int(num(C.get(r["stratum"], {}).get("crossing_tactile", 0))),
            "sound": int(num(C.get(r["stratum"], {}).get("signal_sound", 0))),
            "bpri_v1": num(r["bpri_v1"]),
        } for r in R]

    # --------------------------------------------------------------- dimoi
    if os.path.isfile(os.path.join(ANALYSIS, "by_dimos.csv")):
        D = [r for r in load(os.path.join(ANALYSIS, "by_dimos.csv"))
             if num(r["intersections"]) >= 200]
        D.sort(key=lambda r: -num(r["crossings_per_100_intersections"]))
        d["dimoi"] = {
            "n_eligible": len(D),
            "eligibility": "at least 200 shared-way structural intersections, not degree-three junctions",
            "zero_crossings": sum(1 for r in D if num(r["crossings"]) == 0),
            "zero_tactile": sum(1 for r in D if num(r["crossing_tactile"]) == 0),
            "zero_sound": sum(1 for r in D if num(r["signal_sound"]) == 0),
            "top": [{"unit": r["unit"], "pop": num(r["pop_2021"]),
                     "cr100": num(r["crossings_per_100_intersections"]),
                     "cr100_junctions": csv_value(r["crossings_per_100_junctions"]),
                     "junctions_degree3": int(float(r["junctions_degree3"])),
                     "tactile": int(num(r["crossing_tactile"])),
                     "sound": int(num(r["signal_sound"]))} for r in D[:10]],
        }

    # -------------------------------------------------------------- provenance
    d["provenance"] = [{
        "feature_type": r["feature_type"], "n": num(r["n"]),
        "median_edit_year": r["median_edit_year"],
        "p25": r["p25_edit_year"], "p75": r["p75_edit_year"],
        "pct_edited_last_2y": num(r["pct_edited_last_2y"]),
        "pct_older_than_5y": num(r["pct_older_than_5y"]),
        "snapshot_year": r["snapshot_year"],
    } for r in P if r["stratum"] == "NATIONAL"]

    # -------------------------------------------------------- value breakdowns
    d["tag_values_by_universe"] = value_distributions(V)
    d["value_distributions"] = {
        key: d["tag_values_by_universe"][path]
        for key, path in {
            "sidewalk": "way/drivable/sidewalk", "lit": "way/drivable/lit",
            "surface": "way/drivable/surface", "tactile_paving": "crossing/crossing/tactile_paving",
            "kerb": "crossing/crossing/kerb", "crossing": "crossing/crossing/crossing",
        }.items() if path in d["tag_values_by_universe"]
    }
    d["schema_ablation"] = {
        "unit": "unified crossing inventory; not a routing sensitivity",
        "unified_crossings": int(num(cn["crossings"])),
        "node_schema_features": int(num(cn["schema_node_only"]) + num(cn["schema_both"])),
        "way_schema_features": int(num(cn["schema_way_only"]) + num(cn["schema_both"])),
        "node_only_omits_way_only": int(num(cn["schema_way_only"])),
        "way_only_omits_node_only": int(num(cn["schema_node_only"])),
        "modern_markings_only_omits": int(num(cn["markings_any"]) - num(cn["markings_modern"])),
        "legacy_crossing_nodes": int(cn["legacy_crossing_nodes"]),
        "legacy_crossing_subtype": int(cn["legacy_crossing_subtype"]),
        "legacy_crossing_tactile": int(cn["legacy_crossing_tactile"]),
        "legacy_node_caution": "Raw node inventory is not the count of unified features with a node member.",
    }

    # ------------------------------------ graph scope x component threshold (review item 5)
    # Each variant rebuilds its connected components; nothing here is the headline
    # figure minus a subtracted length. Shares use each scope's own eligible length,
    # the same denominator as the headline.
    sens = os.path.join(CENSUS, "feasibility_sensitivity.csv")
    if os.path.isfile(sens):
        rows, seen = [], set()
        for r in load(sens):
            if r["stratum"] != "NATIONAL":
                continue
            key = (r["graph_scope"], round(float(r["threshold_km"]), 3))
            if key in seen:           # the main threshold is appended once per scope
                continue
            seen.add(key)
            elig = float(r["eligible_km"])
            rows.append({
                "scope": r["graph_scope"], "threshold_km": key[1], "eligible_km": elig,
                "pess_pct": 100 * float(r["pess_eligible_usable_km"]) / elig,
                "opt_pct": 100 * float(r["opt_eligible_usable_km"]) / elig,
                "gap_pct": float(r["eligible_decidability_gap_pct"]),
            })
        rows.sort(key=lambda x: (x["scope"] != "extended", x["threshold_km"]))
        # The extended graph at the main threshold is the headline run recomputed;
        # if the two disagree, one of them is wrong and nothing should be bundled.
        fe_ = d["feasibility_national"]
        main = next((x for x in rows if x["scope"] == fe_.get("graph_scope", "extended")
                     and abs(x["threshold_km"] - fe_["fragmentation"]["min_usable_component_km"]) < 1e-9), None)
        if main is None or abs(main["pess_pct"] - fe_["pess_pct"]) > 0.01                 or abs(main["gap_pct"] - fe_["gap_pct"]) > 0.01:
            raise ValueError(f"Sensitivity main row {main} does not reproduce the headline "
                             f"({fe_['pess_pct']:.3f} / {fe_['gap_pct']:.3f})")
        d["network_sensitivity"] = rows

    # -------------------------------------------------- three-point observations
    archived_census = os.path.join(ROOT, "results", "census")
    cont = os.path.join(ROOT, "results", "analysis", "continuity_audit.json")
    if os.path.isfile(cont):
        with open(cont, encoding="utf-8") as fh:
            d["continuity"] = json.load(fh)
        if d["continuity"].get("schema") != "way-point-observations-v2":
            raise ValueError("Legacy point-audit extrapolation artifact: regenerate continuity_audit.py")
        from local_audit import digest_file
        source_paths = {
            "sample": os.path.join(archived_census, "continuity_sample.csv"),
            "worklist": os.path.join(archived_census, "edit_targets.csv"),
            "observations": os.path.join(ROOT, "validation", "continuity_results.csv"),
            "analysis_code": os.path.join(ROOT, "analysis", "continuity_audit.py"),
            "temporal_rules": os.path.join(ROOT, "analysis", "local_audit.py"),
        }
        for name, path in source_paths.items():
            if d["continuity"].get("sources", {}).get(name, {}).get("sha256") != digest_file(path):
                raise ValueError(f"Stale point-observation artifact: {name}")

    # ------------------------------------------------------------ audit sample
    audit_path = os.path.join(archived_census, "audit_sample.csv")
    if os.path.isfile(audit_path):
        A = load(audit_path)
        from cross_country import fingerprint
        d["audit"] = audit_sample_summary(A)
        d["audit"]["source_csv_sha256"] = fingerprint(audit_path)["sha256"]
        d["audit"]["cities"] = len({row["stratum"] for row in A})
        if versioned:
            d["audit"]["source_model_version"] = "conditional-2026-09-15"
            d["audit"]["note"] = "Frozen audit draw; it was not regenerated from corrected rankings."
    d["manual_audit"] = local_audit_result(
        audit_results, audit_repeat_results, audit_reference_date,
        sample_path=audit_path, input_path=pbf_path)
    if d["manual_audit"]["available"]:
        if (d["manual_audit"]["sample_sha256"] != d["audit"]["source_csv_sha256"]
                or d["manual_audit"]["input_sha256"] != d["snapshot"]["sha256"]):
            raise ValueError("Local-audit analysis is not linked to the manuscript's fixed input/sample")
        if versioned:
            for name, source in zip(("primary_export", "repeat_export"), manual_inputs):
                if digest(source) != d["manual_audit"]["sources"][name]["sha256"]:
                    raise ValueError(f"Manual audit export changed during reporting: {name}")
        local_path = os.path.join(ANALYSIS, "local_audit.json")
        with open(local_path, "w", encoding="utf-8") as stream:
            json.dump(d["manual_audit"], stream, ensure_ascii=False, indent=2)
            stream.write("\n")

    d["published_detector"] = PUBLISHED_DETECTOR

    # ------------------------------------------------------------ destinations
    dest_path = os.path.join(CENSUS, "destinations.csv")
    if os.path.isfile(dest_path):
        DE = load(dest_path)
        d["destination_definition"] = {
            "unit": "OSM node/way records, not deduplicated facilities or station complexes",
            "stations": "railway station/halt and public_transport=station records",
            "platforms": "retained in the stop class",
            "tag_scope": "direct record tags only; platform, entrance and complex evidence is not inferred",
        }
        d["destinations"] = {
            r["kind"]: {
                "total": int(num(r["total"])), "named": int(num(r["named"])),
                "named_pct": num(r["named_pct"]),
                "tactile_stated": int(num(r["tactile_stated"])),
                "tactile_yes": int(num(r["tactile_yes"])),
                "tactile_pct": num(r["tactile_pct"]),
                "wheelchair_stated": int(num(r["wheelchair_stated"])),
                "wheelchair_yes": int(num(r["wheelchair_yes"])),
                "wheelchair_limited": int(num(r["wheelchair_limited"])),
                "wheelchair_no": int(num(r["wheelchair_no"])),
                "wheelchair_pct": num(r["wheelchair_pct"]),
                "braille": int(num(r["braille"])), "braille_pct": num(r["braille_pct"]),
                "braille_stated": int(num(r.get("braille_stated"))),
                "braille_yes": int(num(r.get("braille_yes"))),
                "tactile_writing_stated": int(num(r.get("tactile_writing_stated"))),
                "tactile_writing_yes": int(num(r.get("tactile_writing_yes"))),
                "departures": int(num(r["departures"])), "shelter": int(num(r["shelter"])),
            } for r in DE if r["stratum"] == "NATIONAL"
        }
        by = defaultdict(lambda: defaultdict(float))
        for r in DE:
            if r["layer"] != "circle":
                continue
            for k in ("total", "named", "tactile_stated", "wheelchair_stated", "braille"):
                by[(r["group"], r["kind"])][k] += num(r[k])
        d["destinations_by_group"] = [{
            "group": g, "kind": k, "total": v["total"],
            "named_pct": 100 * v["named"] / v["total"] if v["total"] else 0,
            "tactile_pct": 100 * v["tactile_stated"] / v["total"] if v["total"] else 0,
            "wheelchair_pct": 100 * v["wheelchair_stated"] / v["total"] if v["total"] else 0,
            "braille": int(v["braille"]),
        } for (g, k), v in sorted(by.items()) if v["total"] > 0]
        d["destinations_total"] = sum(v["total"] for v in d["destinations"].values())
        if versioned and any(r["braille"] != r["braille_stated"]
                             for r in d["destinations"].values()):
            raise ValueError("Corrected destination braille indicator and key statements disagree")

    # ---------------------------------------------------------------- exposure
    ex_facts = os.path.join(ANALYSIS, "exposure_facts.csv")
    if os.path.isfile(ex_facts):
        d["exposure"] = {r["metric"]: num(r["value"]) for r in load(ex_facts)}
    ex_meta = os.path.join(ANALYSIS, "exposure_metadata.json")
    if os.path.isfile(ex_meta):
        with open(ex_meta, encoding="utf-8") as fh:
            d["exposure_metadata"] = json.load(fh)
    ex_bands = os.path.join(ANALYSIS, "exposure_bands.csv")
    if os.path.isfile(ex_bands):
        d["exposure_bands"] = [{
            "band": r["band"], "lau_count": int(num(r["lau_count"])),
            "pop": num(r["pop_2021"]), "pop_share_pct": num(r["pop_share_pct"]),
            "walkable_km": num(r["walkable_km"]),
            "eligible_km": num(r["eligible_km"]),
        } for r in load(ex_bands)]

    # ----------------------------------------------------------- edit targeting
    et_sum = os.path.join(CENSUS, "edit_targets_summary.csv")
    if os.path.isfile(et_sum):
        g = load(et_sum)[0]
        d["targeting"] = {k: (v if k in ("profile", "method", "objective", "scope", "strategy")
                              else csv_value(v)) for k, v in g.items()}
        from campaign import intervention_metadata
        d["targeting"].update(intervention_metadata(g))
        d["targeting"]["legacy_km_unlocked"] = d["targeting"].get("km_unlocked")
        d["targeting"]["km_unlocked"] = d["targeting"]["usable_gain_km"]
        committed = d["targeting"]["edits_committed"]
        d["targeting"]["km_per_edit"] = d["targeting"]["usable_gain_km"] / committed if committed else None
        # Three orderings over the same candidates, graph and task count. The last
        # column is what separates them: total gain is dominated by the selected
        # ways' own length, so only recovered pre-existing network distinguishes
        # a connectivity-aware ordering from a length sort.
        t = d["targeting"]
        rows = [("consolidation proxy", "survey_km", "usable_gain_km",
                 "recovered_preexisting_usable_km"),
                ("length only", "length_baseline_survey_km", "length_baseline_usable_gain_km",
                 "length_baseline_recovered_preexisting_usable_km"),
                ("consolidation per km", "efficiency_survey_km", "efficiency_usable_gain_km",
                 "efficiency_recovered_preexisting_usable_km")]
        comparison = []
        for name, own, gain, rec in rows:
            if t.get(gain) is None:
                continue
            comparison.append({"strategy": name, "edits": committed,
                               "own_km": t.get(own), "usable_gain_km": t.get(gain),
                               "recovered_km": t.get(rec)})
        if len(comparison) > 1:
            d["targeting_comparison"] = comparison
    et = os.path.join(CENSUS, "edit_targets.csv")
    if os.path.isfile(et):
        T = load(et)
        from campaign import validate_target_curve, objective_target_rows
        validate_target_curve(T, load(et_sum)[0])
        T = objective_target_rows(T)
        qmix = defaultdict(int)
        cmix = defaultdict(int)
        for r in T:
            qmix[r["missing"]] += 1
            cmix[r["highway"]] += 1
        unlocks = sorted(num(r["unlocked_km"]) for r in T)
        d["targeting"]["question_mix"] = dict(sorted(qmix.items(), key=lambda x: -x[1]))
        d["targeting"]["class_mix"] = dict(sorted(cmix.items(), key=lambda x: -x[1])[:6])
        d["targeting"]["median_unlock_km"] = median(unlocks) if unlocks else 0
        d["targeting"]["median_own_km"] = median(num(r["own_km"]) for r in T) if T else 0
        d["targeting"]["selected_own_km"] = sum(num(r["own_km"]) for r in T)
        d["targeting"]["selected_survey_km"] = sum(num(r.get("survey_km", r["own_km"])) for r in T)
        d["targeting"]["selected_eligible_own_km"] = (
            sum(float(r["eligible_own_km"]) for r in T) if T and all("eligible_own_km" in r for r in T) else None)
        d["targeting"]["gain_minus_selected_own_km"] = (
            num(d["targeting"]["usable_gain_km"]) - d["targeting"]["selected_own_km"])
        d["targeting"]["gain_decomposition_note"] = (
            "Selected own length is survey extent. Its difference from usable gain is not, "
            "without graph attribution, an exact measure of recovered pre-existing PASS length.")
        d["targeting"]["scope_note"] = "national ranking; place labels are not municipalities"
        d["targeting"]["objective"] = "thresholded eligible-core usable length under the configured graph scope"
        d["targeting"]["selection_method"] = T[0].get("selection_method", "unspecified") if T else "no targets"
        d["targeting"]["top"] = [{
            "rank": int(num(r["rank"])), "highway": r["highway"],
            "own_m": num(r["own_km"]) * 1000, "unlocked_km": num(r["unlocked_km"]),
            "eligible_own_km": csv_value(r.get("eligible_own_km")),
            "survey_km": num(r.get("survey_km", r["own_km"])),
            "marginal_usable_km": num(r["unlocked_km"]),
            "stratum": r["stratum"], "missing": r["missing"],
        } for r in T[:8]]
        if "length_baseline_usable_before" in d["targeting"]:
            from campaign import length_baseline_summary
            baseline = length_baseline_summary(d["targeting"])
            baseline_path = os.path.join(CENSUS, baseline["curve_file"])
            validate_target_curve(load(baseline_path), baseline)
            d["targeting"]["length_baseline"] = baseline
    if "targeting" in d:
        d["targeting_distance"] = load_targeting_distance(
            d["feasibility_national"], d["targeting"], ROOT, versioned=versioned)

    # ------------------------------------------------------------------- urban
    urban_path = os.fspath(paths["urban"] / "urban_summary.json")
    if os.path.isfile(urban_path):
        with open(urban_path, encoding="utf-8") as fh:
            d["urban"] = json.load(fh)
        if versioned:
            scope = _json_file(paths["urban"] / "scope_laus.json")
            selected = scope["laus"]
            lookup = {row["stratum"]: row for row in load(
                paths["root"] / "data" / "boundaries" / "municipalities.csv")}
            cities = load(paths["urban"] / "top_cities.csv")
            if (len(cities) != d["urban"]["top_n"] or len(selected) != len(cities)
                    or len(set(selected)) != len(selected)
                    or {row["principal_lau"] for row in cities} != set(selected)
                    or any(lau not in lookup or lau not in F or F[lau]["layer"] != "municipality"
                           for lau in selected)):
                raise ValueError("Corrected urban principal-community frame is incomplete")
            principal_pop = sum(num(lookup[lau]["pop_2021"]) for lau in selected)
            d["urban"]["principal_communities"] = {
                "laus": len(selected),
                "population": principal_pop,
                "population_pct": 100 * principal_pop / d["population_provenance"]["field_sum"],
                "eligible_km": sum(float(F[lau]["eligible_km"]) for lau in selected),
            }
            d["urban"]["selected_laus"] = sum(int(row["n_lau"]) for row in cities)
    # national spread, for the contrast that justifies the scope
    ex_lau = os.path.join(ANALYSIS, "exposure_by_lau.csv")
    if os.path.isfile(ex_lau):
        rows = [r for r in load(ex_lau) if num(r["walkable_km"]) >= 20]
        v = sorted(num(r["pess_usable_pct"]) for r in rows)
        d["national_spread"] = {
            "n": len(v), "median": median(v) if v else None,
            "unit": "LAU/local community",
            "eligibility": "positive GISCO population field, positive eligible network, and at least 20 all-graph walkable km",
            "primary_denominator": "eligible nonauxiliary network length",
            "min_walkable_km": 20,
            "p75": v[int(0.75 * (len(v) - 1))] if v else None,
            "zeros": sum(1 for x in v if x == 0),
            "zeros_pct": 100 * sum(1 for x in v if x == 0) / len(v) if v else None,
        }

    # ------------------------------------------------------- cross-country
    country_provenance_path = os.fspath(paths["countries"] / "input_provenance.json")
    if os.path.isfile(country_provenance_path):
        from cross_country import fingerprint
        with open(country_provenance_path, encoding="utf-8") as fh:
            country_provenance = json.load(fh)
        for record in country_provenance["inputs"]:
            reference = record["immutable_manifest"]
            if fingerprint(os.path.join(ROOT, reference["path"]))["sha256"] != reference["sha256"]:
                raise ValueError(f"Country input manifest hash mismatch: {record['country']}")
        d["country_input_provenance"] = country_provenance
    xc_path = os.fspath(paths["countries"] / "cross_country.csv")
    if os.path.isfile(xc_path):
        X = load(xc_path)
        if any(r.get("provenance_status") != "manifest verified" for r in X):
            raise ValueError("Cross-country table lacks verified execution manifests; rerun cross_country.py")
        from cross_country import fingerprint, JAR, PROFILE
        current_jar = fingerprint(greek["command"][3] if versioned else JAR)["sha256"]
        current_profile = fingerprint(PROFILE)["sha256"]
        if any(r.get("jar_sha256") != current_jar or r.get("profile_sha256") != current_profile for r in X):
            raise ValueError("Cross-country results use a different executable/profile; rerun before bundling")
        d["cross_country"] = {
            "n": len(X),
            "model_version": d["feasibility_national"]["model_version"],
            "covariate_provenance": "GISCO POP_2021/AREA_KM2 fields; mixed/reference years unverified",
            "comparison_scope": "country-specific extract snapshots; not synchronized observations",
            "rows": [{k: (v if k in ("country", "code", "population_reference_year",
                                    "population_source_field", "snapshot_timestamp", "source",
                                    "pbf_sha256", "pbf_md5", "jar_sha256", "profile_sha256",
                                    "provenance_status") else csv_value(v))
                      for k, v in r.items()} for r in X],
        }
        gr = next((r for r in d["cross_country"]["rows"] if r["code"] == "EL"), None)
        if gr:
            def rank_of(key, reverse=True):
                vals = sorted((r[key] for r in d["cross_country"]["rows"] if r[key] is not None), reverse=reverse)
                return vals.index(gr[key]) + 1 if gr[key] is not None else None
            d["cross_country"]["greece"] = gr
            d["cross_country"]["greece_rank"] = {
                k: rank_of(k) for k in ("pess_usable_pct", "crossings_per_100_ix",
                                        "crossings_per_100_junctions", "tactile_pct", "sound_pct", "name_pct", "bpri_v1")
            }
            d["cross_country"]["greece_rank"]["gap_pct"] = rank_of("gap_pct", reverse=False)
            for key in ("pess_usable_pct", "gap_pct", "crossings_per_100_ix", "tactile_pct",
                        "crossings_per_100_junctions", "sound_pct", "name_pct", "bpri_v1"):
                vals = [r[key] for r in d["cross_country"]["rows"] if r[key] is not None]
                if not vals:
                    continue
                d["cross_country"].setdefault("range", {})[key] = {
                    "min": min(vals), "max": max(vals),
                    "median": sorted(vals)[len(vals) // 2], "greece": gr[key],
                }
    xcorr = os.fspath(paths["countries"] / "cross_country_correlations.csv")
    if os.path.isfile(xcorr):
        d["cross_country_corr"] = [{"covariate": r["covariate"], "metric": r["metric"],
                                    "rho": float(r["spearman_rho"]) if r["spearman_rho"] else None,
                                    "n": int(num(r["n"]))}
                                   for r in load(xcorr)]

    # --------------------------------------------------- automated validation
    mly_dir = os.path.join(ROOT, "results", "mapillary")
    V, val_path, is_v2 = load_mapillary_rows(mly_dir)
    if val_path:
        tot = defaultdict(float)
        for r in V:
            for k in ("intersections", "osm_crossings", *MAPILLARY_V2_FIELDS):
                tot[k] += r[k]
        for key in (*MAPILLARY_INVENTORY_COUNTS, *MAPILLARY_RECENT_COUNTS, *MAPILLARY_STATUS_COUNTS,
                    *MAPILLARY_SCREENING_COUNTS):
            tot[key] = sum(r[key] for r in V) if V and all(r.get(key) is not None for r in V) else None
        def rate(row, numerator, denominator):
            return 100 * row[numerator] / row[denominator] if row[denominator] else None
        d["mapillary"] = {
            "cities": len(V),
            "source_file": os.path.basename(val_path),
            "spatial_scope": "selected principal-LAU polygons" if is_v2 else "legacy artifact scope; not reinterpreted as v2",
            "source_schemas": sorted({r["source_schema"] for r in V}),
            "interpretation": "Spatial co-detection screening and candidate omission flags, not ground truth",
            "eligibility_caution": "Image/tile coverage is not proof that a feature was visible",
            "temporal_caution": "OSM snapshot and imagery observations can differ in date",
            "unit_caution": "Junction flags need not denote distinct missing crossings; signal universes differ",
            "object_inventory_caution": "Detected furniture is not a verified obstruction/hazard; no network-density normalization",
            "period_note": "Unqualified compatibility rates refer to all dates; recent refers to detection last_seen, not inspection date.",
            "feature_query_exclusions": {key: tot[key] for key in MAPILLARY_STATUS_COUNTS if "query" in key},
            **{k: v for k, v in tot.items()},
            "junction_image_positive_tile_pct": rate(tot, "junctions_in_image_positive_tiles", "intersections"),
            "candidate_omission_flag_pct": rate(tot, "cv_positive_flags", "osm_negative_junctions"),
            "crossing_codetection_pct": rate(tot, "crossings_codetected", "crossings_screening_eligible"),
            "signal_codetection_pct": rate(tot, "general_signal_locations_codetected",
                                            "general_signal_locations_screening_eligible"),
            "screening_denominator_note": "Crossing/signal image-positive populations minus respective unknown feature-query counts; OSM-negative junction denominator already excludes unknown queries.",
            "by_city": [{
                "city": r["city"], "stratum": r.get("stratum"), "pop": csv_value(r.get("pop_2021")),
                "imagery_pct": csv_value(r.get("image_positive_tile_pct", r.get("imagery_coverage_pct"))),
                "junction_image_positive_tile_pct": rate(r, "junctions_in_image_positive_tiles", "intersections"),
                "candidate_omission_flag_pct": rate(r, "cv_positive_flags", "osm_negative_junctions"),
                "crossing_codetection_pct": rate(r, "crossings_codetected", "crossings_screening_eligible"),
                "omission_rate_pct": rate(r, "cv_positive_flags", "osm_negative_junctions"),
                "commission_confirm_pct": rate(r, "crossings_codetected", "crossings_screening_eligible"),
                "crossings_screening_eligible": r["crossings_screening_eligible"],
                "general_signal_locations_screening_eligible": r["general_signal_locations_screening_eligible"],
                "det_street_light": r["det_street_light"],
                "det_furniture": r["det_furniture"], "det_surface_objects": r["det_surface_objects"],
                "cv_positive_flags_recent": r.get("cv_positive_flags_recent"),
                "crossings_codetected_recent": r.get("crossings_codetected_recent"),
                "ped_head_codetected_recent": r.get("ped_head_codetected_recent"),
            } for r in V],
        }
        d["mapillary"].update({source: tot[canonical] for canonical, source in MAPILLARY_V2_FIELDS.items()})
        d["mapillary"]["all_dates"] = {
            "cv_positive_flags": tot["cv_positive_flags"],
            "crossings_codetected": tot["crossings_codetected"],
            "ped_head_codetected": tot["general_signal_locations_codetected"],
            "flag_rate_pct": d["mapillary"]["candidate_omission_flag_pct"],
            "crossing_codetection_pct": d["mapillary"]["crossing_codetection_pct"],
            "ped_head_codetection_pct": d["mapillary"]["signal_codetection_pct"],
        }
        d["mapillary"]["recent"] = {
            "cv_positive_flags": tot["cv_positive_flags_recent"],
            "crossings_codetected": tot["crossings_codetected_recent"],
            "ped_head_codetected": tot["ped_head_codetected_recent"],
            "denominator_note": "Same image-positive, known-feature-query OSM populations as all-date screening; not recent-image visibility.",
            **{key: rate(tot, numerator, denominator) if tot[numerator] is not None else None
               for key, numerator, denominator in (
                   ("flag_rate_pct", "cv_positive_flags_recent", "osm_negative_junctions"),
                   ("crossing_codetection_pct", "crossings_codetected_recent", "crossings_screening_eligible"),
                   ("ped_head_codetection_pct", "ped_head_codetected_recent",
                    "general_signal_locations_screening_eligible"))},
        }
        deprecated = {legacy: canonical for canonical, legacy in MAPILLARY_LEGACY_ALIASES.items()}
        deprecated.update({
            "crossings_testable": "crossings_screening_eligible",
            "signals_testable": "general_signal_locations_screening_eligible",
            "testable_pct": "junction_image_positive_tile_pct",
            "omission_rate_pct": "candidate_omission_flag_pct",
            "commission_confirm_pct": "crossing_codetection_pct",
            "signal_confirm_pct": "signal_codetection_pct",
        })
        d["mapillary"].update({old: d["mapillary"][new] for old, new in deprecated.items()})
        d["mapillary"]["deprecated_aliases"] = deprecated
        d["mapillary"]["deprecated_alias_note"] = (
            "Compatibility identifiers only: testable does not establish visibility, "
            "confirmed denotes co-detection, omission denotes a candidate junction flag, "
            "and obstacle/hazard denotes an object inventory.")
        for metadata_file in ("co_detection_manifest.json", "validation_metadata.json", "join_metadata.json"):
            metadata_path = os.path.join(mly_dir, metadata_file)
            if os.path.isfile(metadata_path):
                with open(metadata_path, encoding="utf-8") as fh:
                    d["mapillary"][metadata_file[:-5]] = json.load(fh)
        if "co_detection_manifest" in d["mapillary"]:
            manifest = d["mapillary"]["co_detection_manifest"]
            for key in ("reference_date", "recent_cutoff", "detection_age_counts",
                        "unique_cv_features_associated_with_flags"):
                if key in manifest:
                    d["mapillary"][key] = manifest[key]
            d["mapillary"]["detection_age_universe"] = "crossing and pedestrian-head point records in detections_v2.csv"
        if is_v2:
            d["mapillary"]["radius_sensitivity"] = load_mapillary_radius_sensitivity(d["mapillary"], ROOT)
        links_path = os.path.join(mly_dir, "local_audit_cv_links.csv")
        sample_path = os.path.join(archived_census, "audit_sample.csv")
        if os.path.isfile(links_path):
            from cross_country import fingerprint
            sample_hash = fingerprint(sample_path)["sha256"] if os.path.isfile(sample_path) else None
            d["mapillary"]["incidental_audit_links"] = mapillary_audit_links(load(links_path), sample_hash)
            if versioned:
                d["mapillary"]["incidental_audit_links"]["sample_scope"] = (
                    "Original frozen audit draw, not a corrected-model sample.")
        inventory_path = os.path.join(mly_dir, "inventory_by_scope.csv")
        if os.path.isfile(inventory_path):
            inventory_rows = load(inventory_path)
            inventory = mapillary_inventory_summary(inventory_rows)
            d["mapillary"]["inventory_by_scope"] = inventory_rows
            d["mapillary"]["inventory"] = inventory
            d["mapillary"]["inventory_source_file"] = "inventory_by_scope.csv"
            d["mapillary"]["inventory_note"] = "Separate spatially assigned object inventory; retain scope and units, no network-length normalisation."
            if is_v2:
                d["mapillary"]["detections"] = {key: value["count_all_dates"] for key, value in inventory["by_group"].items()}
                for key, group in (("det_street_light", "street_light"), ("det_furniture", "street_furniture"),
                                   ("det_surface_objects", "surface_objects")):
                    d["mapillary"][key] = inventory["by_group"].get(group, {}).get("count_all_dates")
                    for row in d["mapillary"]["by_city"]:
                        row[key] = inventory["by_stratum"].get(row["stratum"], {}).get(group, {}).get("count_all_dates")
                for old, new in deprecated.items():
                    d["mapillary"][old] = d["mapillary"][new]
        cov_path = os.path.join(mly_dir, "image_coverage_v2.csv" if is_v2 else "image_coverage.csv")
        if os.path.isfile(cov_path):
            C2 = load(cov_path)
            cov = ([100 * float(r["tiles_image_positive"]) / float(r["tiles_total"])
                    for r in C2 if float(r["tiles_total"]) > 0] if is_v2
                   else [num(r["coverage_pct"]) for r in C2])
            cov.sort()
            d["mapillary"]["coverage"] = {
                "source_file": os.path.basename(cov_path),
                "interpretation": "Coarse image-positive grid-cell coverage; not feature visibility or a co-detection denominator",
                "cities": len(cov), "undefined_cities": len(C2) - len(cov), "min": cov[0] if cov else None,
                "median": median(cov) if cov else None,
                "max": cov[-1] if cov else None,
                "under10": sum(1 for c in cov if c < 10),
            }
        det_path = os.path.join(mly_dir, "detection_counts.csv")
        if os.path.isfile(det_path) and not is_v2:
            D2 = load(det_path)
            byg = defaultdict(float)
            for r in D2:
                byg[r["group"]] += num(r["count"])
            d["mapillary"]["detections"] = dict(byg)
            d["mapillary"]["detections_source_note"] = "Legacy cached detection counts; not scope-matched hazard densities."

    # ---------------------------------------------------------------- campaign
    cost_path = os.fspath(paths["campaign"] / "campaign_cost.csv")
    if os.path.isfile(cost_path):
        d["campaign"] = {
            "cost": [{
                "edits": int(num(r["edits"])), "km_unlocked": num(r["km_unlocked"]),
                "selected_ways": int(r["selected_ways"]),
                "person_hours": num(r["person_hours"]), "working_days": num(r["working_days"]),
                "person_months": num(r["person_months"]), "km_per_hour": num(r["km_per_hour"]),
                "desk_minutes_per_task": num(r["desk_minutes_per_task"]),
                "selected_own_km": num(r["selected_own_km"]),
                "selected_survey_km": num(r["selected_survey_km"]),
                "usable_km_before": num(r["usable_km_before"]),
                "usable_km_after": num(r["usable_km_after"]),
                "scenario": r["scenario"],
                "intervention_model": r["intervention_model"],
            } for r in load(cost_path)],
        }
        place_path = os.fspath(paths["campaign"] / "campaign_by_place.csv")
        if os.path.isfile(place_path):
            pl = load(place_path)
            d["campaign"]["places"] = len(pl)
        metadata_path = os.fspath(paths["campaign"] / "campaign_summary.json")
        with open(metadata_path, encoding="utf-8") as fh:
            d["campaign"]["metadata"] = json.load(fh)

    # ----------------------------------------------------- profile sensitivity
    d["sensitivity_runs"] = []
    d["core_graph_run"] = None
    native_rows = []
    native_path = os.path.join(CENSUS, "feasibility_sensitivity.csv")
    if os.path.isfile(native_path):
        native_rows = native_sensitivity_rows(load(native_path))
        freshness = (
            {
                "source_file": os.path.relpath(native_path, ROOT),
                "source_sha256": digest(Path(native_path)),
                "model_version": CORRECTED_VERSION,
                "status": "execution_manifest_verified",
                "completed_at_utc": greek["completed_at_utc"],
                "input_pbf_sha256": greek["input_sha256"]["pbf"],
                "jar_sha256": greek["input_sha256"]["jar"],
                "profile_sha256": greek["input_sha256"]["profile"],
                "manifest_file": os.path.relpath(paths["census"] / "run_manifest.json", ROOT),
                "manifest_sha256": digest(paths["census"] / "run_manifest.json"),
                "osm_replication_timestamp_utc": d["snapshot"]["osm_header"]["replication_timestamp_utc"],
                "note": "Verified corrected Greece execution; observation and run times are distinct.",
            } if versioned else sensitivity_freshness(native_path))
        if freshness["input_pbf_sha256"] is not None and freshness["input_pbf_sha256"] != d["snapshot"].get("sha256"):
            raise ValueError("Native sensitivity input differs from the national snapshot")
        for row in native_rows:
            row["data_freshness"] = freshness
        d["native_sensitivity"] = native_rows
        d["component_sensitivity"] = native_rows
        d["core_sensitivity"] = [r for r in native_rows if r["network_scope"] == "core"]
        d["profile_sensitivity"] = []
    sensitivity_rows = load(SENS) if os.path.isfile(SENS) else []
    if any("network_scope" not in r or "min_usable_component_km" not in r for r in sensitivity_rows):
        if not d.get("native_sensitivity"):
            raise ValueError("Legacy sensitivity table lacks reconstructed graph provenance; rerun profile_sensitivity.py")
        d["external_sensitivity_status"] = "Legacy external table excluded; only native actual-run scenarios reported."
        sensitivity_rows = []
    if sensitivity_rows:
        from cross_country import fingerprint, JAR
        from profile_sensitivity import scenario_profile
        jar_hash = fingerprint(greek["command"][3] if versioned else JAR)["sha256"]
        aggregate_hash = fingerprint(SENS)["sha256"]
        freshness_cache, freshness_by_scenario = {}, {}
        for r in {r["scenario"]: r for r in sensitivity_rows}.values():
            run_directory = os.path.join(os.path.dirname(SENS), r.get("run_directory", r["scenario"]))
            frozen_path = os.path.join(run_directory, "profile.json")
            with open(os.path.join(ROOT, "data", f"profile-{r['profile']}.json"), encoding="utf-8") as fh:
                source_profile = json.load(fh)
                expected = (source_profile if r.get("execution_mode") == "native-graph-sensitivity" else
                            scenario_profile(source_profile, r["network_scope"], float(r["min_usable_component_km"])))
            with open(frozen_path, encoding="utf-8") as fh:
                frozen = json.load(fh)
            if (frozen != expected or fingerprint(frozen_path)["sha256"] != r["profile_sha256"]
                    or r.get("jar_sha256") != jar_hash):
                raise ValueError(f"Stale profile sensitivity scenario: {r['scenario']}")
            with open(os.path.join(run_directory, "run_manifest.json"), encoding="utf-8") as fh:
                manifest = json.load(fh)
            source_name = ("feasibility_sensitivity.csv" if "feasibility_sensitivity.csv" in manifest.get("outputs", {})
                           else "feasibility.csv")
            source_path = os.path.join(run_directory, source_name)
            if source_path not in freshness_cache:
                freshness_cache[source_path] = sensitivity_freshness(
                    source_path, ROOT, greek["command"][3] if versioned else None)
            freshness = freshness_cache[source_path]
            if freshness["status"] != "execution_manifest_verified":
                raise ValueError(f"Sensitivity execution linkage is unverified: {r['scenario']}")
            if freshness["input_pbf_sha256"] != d["snapshot"].get("sha256"):
                raise ValueError(f"Sensitivity input differs from the national snapshot: {r['scenario']}")
            freshness_by_scenario[r["scenario"]] = {
                **freshness, "aggregate_file": os.path.relpath(SENS, ROOT), "aggregate_sha256": aggregate_hash}
        scenarios = [{
            "profile": r["profile"], "unit": r["unit"],
            "profile_id": r["profile_id"], "network_scope": r["network_scope"],
            "min_usable_component_km": float(r["min_usable_component_km"]),
            "scenario": r["scenario"],
            "execution_mode": r.get("execution_mode", "profile-reconstruction"),
            "graph_scope": r.get("graph_scope", r["network_scope"]),
            "primary_denominator": "eligible nonauxiliary length; threshold uses eligible mass",
            "walkable_km": num(r["walkable_km"]),
            "pess_pct": num(r["pess_usable_pct"]), "opt_pct": num(r["opt_usable_pct"]),
            "gap_pct": num(r["decidability_gap_pct"]),
            "all_length_pess_pct": csv_value(r.get("all_length_pess_usable_pct")),
            "all_length_opt_pct": csv_value(r.get("all_length_opt_usable_pct")),
            "all_length_gap_pct": csv_value(r.get("all_length_gap_pct")),
            "pass_pct": csv_value(r["pass_pct"]), "block_pct": csv_value(r["block_pct"]),
            "unknown_pct": csv_value(r["unknown_pct"]),
            "unknown_graph_only_pct": csv_value(r["unknown_graph_only_pct"]),
            "unknown_road_sidewalk_pct": csv_value(r["unknown_road_sidewalk_pct"]),
            "eligible_km": csv_value(r.get("eligible_km")),
            "pess_eligible_usable_km": csv_value(r.get("pess_eligible_usable_km")),
            "opt_eligible_usable_km": csv_value(r.get("opt_eligible_usable_km")),
            "eligible_pess_pct": csv_value(r.get("eligible_pess_pct")),
            "eligible_opt_pct": csv_value(r.get("eligible_opt_pct")),
            "eligible_gap_pct": csv_value(r.get("eligible_gap_pct")),
            "profile_sha256": r["profile_sha256"],
            "model_version": freshness_by_scenario[r["scenario"]]["model_version"],
            "data_freshness": freshness_by_scenario[r["scenario"]],
        } for r in sensitivity_rows]
        threshold = d["feasibility_national"]["fragmentation"]["min_usable_component_km"]
        d["component_sensitivity"] = merge_sensitivity_rows(native_rows, scenarios)
        d["profile_sensitivity"] = [
            r for r in d["component_sensitivity"]
            if r["network_scope"] in ("full", "extended") and r["min_usable_component_km"] == threshold]
        d["core_sensitivity"] = [
            r for r in d["component_sensitivity"] if r["network_scope"] == "core"]
    if d.get("component_sensitivity"):
        d["sensitivity_runs"] = [
            {**row, "scope": row["graph_scope"], "profile": row["profile_id"],
             "threshold_km": row["min_usable_component_km"]}
            for row in d["component_sensitivity"] if row["unit"] == "NATIONAL"]
        threshold = d["feasibility_national"]["fragmentation"]["min_usable_component_km"]
        core = next((r for r in d["component_sensitivity"] if r["network_scope"] == "core" and r["unit"] == "NATIONAL"
                     and r["profile_id"] == fn["profile"] and r["min_usable_component_km"] == threshold), None)
        if core:
            d["core_graph_run"] = {**core, "scope": core["graph_scope"], "profile": core["profile_id"],
                                   "threshold_km": core["min_usable_component_km"]}
            d["feasibility_national"].update({
                "core_km": core["walkable_km"], "core_pess_pct": core["pess_pct"],
                "core_opt_pct": core["opt_pct"], "core_gap_pct": core["gap_pct"],
                "core_eligible_pess_pct": core.get("eligible_pess_pct"),
                "core_eligible_opt_pct": core.get("eligible_opt_pct"),
                "core_eligible_gap_pct": core.get("eligible_gap_pct"),
                "core_status": ("saved reconstructed core graph; "
                                + core["data_freshness"]["status"]),
            })

    # --------------------------------------------------------- coverage check
    lau = [r for r in summary_rows if r["layer"] == "municipality"]
    d["coverage"] = {
        col: {"national": num(S["NATIONAL"][col]),
              "in_lau": sum(num(r[col]) for r in lau),
              "pct": 100 * sum(num(r[col]) for r in lau) / num(S["NATIONAL"][col])
              if num(S["NATIONAL"][col]) else 0}
        for col in ("drivable_km", "crossings", "intersections")
    }
    if versioned:
        assigned = [r for r in F.values() if r["layer"] == "municipality"]
        d["eligible_lau_coverage"] = {
            "eligible_pct": 100 * sum(float(r["eligible_km"]) for r in assigned)
                            / float(fn["eligible_km"]),
            "pess_usable_pct": 100 * sum(float(r["pess_eligible_usable_km"]) for r in assigned)
                               / float(fn["pess_eligible_usable_km"]),
            "definition": "Independent municipality-layer assignments divided by corrected national eligible lengths",
        }
    d["spatial_coverage"] = coverage_check(summary_rows)
    native_coverage_path = os.path.join(CENSUS, "spatial_coverage.csv")
    if os.path.isfile(native_coverage_path):
        d["native_spatial_coverage"] = [
            {key: (value if key in ("layer", "assignment", "model_version") else csv_value(value))
             for key, value in row.items()} for row in load(native_coverage_path)]
    d["spatial_assignment"] = (
        "Independent representative-point assignment per layer; whole-way lengths use the middle stored way node, "
        "without boundary clipping. Boundary points are included with one deterministic owner per layer. "
        "Coarse polygons are not an exact hierarchical partition; unmatched totals are reported.")
    d["strata_counts"] = {
        "municipality": len(lau),
        "region": sum(1 for r in summary_rows if r["layer"] == "region"),
        "nuts3": sum(1 for r in summary_rows if r["layer"] == "nuts3"),
        "circle": sum(1 for r in summary_rows if r["layer"] == "circle"),
    }
    impact = load_corrected_impact(ROOT)
    comparison = impact["places"][0]
    side = "corrected" if versioned else "frozen"
    if (d["feasibility_national"]["model_version"] != impact[f"{side}_model_version"]
            or abs(d["feasibility_national"]["pess_pct"] - comparison[f"{side}_pess_pct"]) > 0.01
            or abs(d["feasibility_national"]["opt_pct"] - comparison[f"{side}_opt_pct"]) > 0.01):
        raise ValueError(f"Corrected-model comparison disagrees with the {side} manuscript baseline")
    d["corrected_model_impact"] = impact
    if versioned:
        d["reporting"] = {
            "mode": "versioned-corrected",
            "model_version": CORRECTED_VERSION,
            "greek_census": paths["census"].relative_to(paths["root"]).as_posix(),
            "country_runs": paths["countries"].relative_to(paths["root"]).as_posix(),
            "profile_runs": paths["profiles"].relative_to(paths["root"]).as_posix(),
            "source_sha256": reporting_source_hashes(paths, manual_inputs),
            "manual_inputs": [path.relative_to(paths["root"]).as_posix()
                              for path in manual_inputs],
            "frozen_evidence": {
                "audit_draw": "results/census/audit_sample.csv (frozen original draw, not corrected sampler output)",
                "point_pilot": "results/analysis/continuity_audit.json (old ranked ways, not corrected worklist)",
                "imagery": "results/mapillary and results/analysis/mapillary_radius_sensitivity.* "
                           "(original geometry/scope; not regenerated by the corrected census)",
                "historical_comparator": "results/analysis/corrected_model_impact.* "
                                         "(frozen Greek baseline against the corrected Greek run)",
            },
        }

    out = os.fspath(paths["bundle"])
    if versioned:
        paths["base"].mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1,
                  **({"allow_nan": False} if versioned else {}))
    print(f"Wrote {out}  ({os.path.getsize(out) / 1024:.0f} KB)")
    print(f"  sections: {', '.join(sorted(d.keys()))}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-results", help="Explicit primary v2 export; omitted means pending")
    parser.add_argument("--audit-repeat-results", help="Optional repeat-only v2 export")
    parser.add_argument("--audit-reference-date", help="YYYY-MM-DD; otherwise latest completed primary audit date")
    parser.add_argument("--versioned", action="store_true",
                        help="Require complete corrected Greece/20-country/three-profile runs and isolated downstream data")
    arguments = parser.parse_args()
    main(arguments.audit_results, arguments.audit_repeat_results, arguments.audit_reference_date,
         versioned=arguments.versioned)
