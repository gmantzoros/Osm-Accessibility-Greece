#!/usr/bin/env python3
"""Run native profile/threshold/core scenarios sequentially, with provenance.

Similar endpoints demonstrate only limited robustness under the tested shared
assumptions. Core scenarios remove auxiliary classes before graph construction,
not by subtracting length from a previously computed denominator.

Usage:
  python analysis/profile_sensitivity.py [pbf] [strata]
  python analysis/profile_sensitivity.py --profiles bvi --include-core --thresholds 0.1,1,5
  python analysis/profile_sensitivity.py --report --profiles bvi --include-core --thresholds 0.1,1,5
"""

import argparse
import copy
import csv
import datetime
import glob
import json
import math
import os
import subprocess
import sys
import time

from cross_country import (
    CORRECTED_VERSION, fingerprint, input_provenance, manifest_inputs, current_manifest,
    require_corrected_national, versioned_model_paths,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAR = os.path.join(ROOT, "census", "target", "census.jar")
OUT = os.path.join(ROOT, "results", "profile_sensitivity")
GROUPS = ["metro", "large_city", "medium_town", "small_town", "island", "tourist_rural", "rural"]
RESULT_FILES = ("feasibility.csv", "feasibility_global.csv")
AUXILIARY_CLASSES = {"track", "cycleway", "bridleway"}


def scenario_profile(source, network_scope, threshold):
    profile = copy.deepcopy(source)
    profile["minUsableComponentKm"] = threshold
    if network_scope == "core":
        profile["excludedClasses"] = sorted(set(profile.get("excludedClasses", [])) | AUXILIARY_CLASSES)
    return profile


def native_options(thresholds, include_core):
    options = ["--graph-scope=extended"]
    if thresholds:
        options.append("--component-thresholds=" + ",".join(f"{v:g}" for v in thresholds))
    if include_core:
        options.append("--core-sensitivity")
    return options


def run(pbf, strata, source, out_dir, thresholds, include_core, provenance, report_only=False,
        heap="9g", *, jar=JAR, expected_version=None):
    with open(source, encoding="utf-8") as fh:
        expected = json.load(fh)
    options = native_options(thresholds, include_core)
    result_files = RESULT_FILES + (("feasibility_sensitivity.csv",) if thresholds or include_core else ())
    profile = os.path.join(out_dir, "profile.json")
    if report_only:
        if not os.path.isfile(profile):
            return False
        with open(profile, encoding="utf-8") as fh:
            if json.load(fh) != expected:
                return False
    else:
        os.makedirs(out_dir, exist_ok=True)
        with open(profile, "w", encoding="utf-8") as fh:
            json.dump(expected, fh, sort_keys=True, indent=2)
    inputs = manifest_inputs(pbf, profile, strata, provenance, options=options, jar=jar)
    if current_manifest(out_dir, inputs, result_files):
        if expected_version is not None:
            require_corrected_national(os.path.join(out_dir, "feasibility.csv"), expected_version)
        return True
    if report_only:
        return False
    cmd = ["java", f"-Xmx{heap}", "-jar", jar, pbf, strata, out_dir, f"--profile={profile}", *options]
    start = time.time()
    print(f"  {os.path.basename(source)} / native graph scenarios", flush=True)
    process = subprocess.run(cmd, capture_output=True, text=True)
    with open(os.path.join(out_dir, "run.log"), "w", encoding="utf-8") as fh:
        fh.write(process.stdout + "\n" + process.stderr)
    if process.returncode:
        sys.stderr.write(process.stderr[-2000:] + "\n")
        return False
    if expected_version is not None:
        require_corrected_national(os.path.join(out_dir, "feasibility.csv"), expected_version)
    outputs = {name: fingerprint(os.path.join(out_dir, name)) for name in result_files}
    with open(os.path.join(out_dir, "run_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"status": "complete", "inputs": inputs, "outputs": outputs, "command": cmd,
                   "elapsed_seconds": time.time() - start,
                   "completed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()}, fh, indent=2)
    return True


def read_feasibility(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pbf", nargs="?", default=os.path.join(ROOT, "data", "greece-latest.osm.pbf"))
    parser.add_argument("strata", nargs="?", default=os.path.join(ROOT, "data", "strata.json"))
    parser.add_argument("--profiles", help="Comma-separated profile filename suffixes (e.g. bvi,wheelchair)")
    parser.add_argument("--thresholds", help="Comma-separated component thresholds in km; default each profile's value")
    parser.add_argument("--include-core", action="store_true", help="Request native fresh core-graph reconstruction")
    parser.add_argument("--report", action="store_true", help="Verify cached scenarios only; never start Java")
    parser.add_argument("--heap", default="9g", help="Maximum JVM heap; select for available memory before running")
    parser.add_argument("--jar", help="Separately staged corrected census.jar; requires --output")
    parser.add_argument("--output", help="Versioned corrected profiles directory; requires --jar")
    args = parser.parse_args()
    versioned = bool(args.jar or args.output)
    jar, out = (versioned_model_paths(args.jar, args.output, "profiles") if versioned
                else (JAR, OUT))
    profiles = sorted(glob.glob(os.path.join(ROOT, "data", "profile-*.json")))
    if args.profiles:
        requested = set(args.profiles.split(","))
        profiles = [p for p in profiles if os.path.basename(p)[8:-5] in requested]
        found = {os.path.basename(p)[8:-5] for p in profiles}
        if found != requested:
            raise ValueError(f"Unknown profiles: {sorted(requested - found)}")
    if not profiles:
        raise ValueError("No profiles selected")
    thresholds = [float(v) for v in args.thresholds.split(",")] if args.thresholds else None
    if thresholds and any(v <= 0 or not math.isfinite(v) for v in thresholds):
        raise ValueError("Thresholds must be finite and positive")
    provenance = input_provenance(args.pbf, persist=not versioned)
    rows, failures = [], []
    for profile in profiles:
        pid = os.path.basename(profile)[8:-5]
        with open(profile, encoding="utf-8") as fh:
            source = json.load(fh)
        out_dir = os.path.join(out, pid)
        if not run(args.pbf, args.strata, profile, out_dir, thresholds, args.include_core,
                   provenance, args.report, args.heap, jar=jar,
                   expected_version=CORRECTED_VERSION if versioned else None):
            failures.append(pid)
            continue
        base = read_feasibility(os.path.join(out_dir, "feasibility.csv"))
        base_by_stratum = {r["stratum"]: r for r in base}
        if thresholds or args.include_core:
            feas = read_feasibility(os.path.join(out_dir, "feasibility_sensitivity.csv"))
        else:
            feas = [{**r, "threshold_km": source.get("minUsableComponentKm", 1.0)} for r in base]
        profile_hash = fingerprint(os.path.join(out_dir, "profile.json"))["sha256"]
        jar_hash = fingerprint(jar)["sha256"]
        cases = sorted({(r["graph_scope"], float(r["threshold_km"])) for r in feas})
        for graph_scope, threshold in cases:
            scope = "core" if graph_scope == "core" else "full"
            sid = f"{pid}-{scope}-t{threshold:g}"
            for unit in ["NATIONAL"] + GROUPS:
                records = [r for r in feas if r["graph_scope"] == graph_scope
                           and float(r["threshold_km"]) == threshold
                           and (r["stratum"] == "NATIONAL" if unit == "NATIONAL"
                                else r["layer"] == "circle" and r["group"] == unit)]
                walk = sum(float(r["walkable_km"]) for r in records)
                if not records or walk <= 0:
                    continue
                def pct(key):
                    values = []
                    for row in records:
                        value = row.get(key)
                        baseline = base_by_stratum.get(row["stratum"], {})
                        if value is None and baseline.get("graph_scope") == graph_scope:
                            value = baseline.get(key)
                        if value in ("", None):
                            return None
                        values.append(float(value))
                    return 100 * sum(values) / walk
                pess, opt = pct("pess_usable_km"), pct("opt_usable_km")
                eligible = sum(float(r["eligible_km"]) for r in records)
                pess_eligible = sum(float(r["pess_eligible_usable_km"]) for r in records)
                opt_eligible = sum(float(r["opt_eligible_usable_km"]) for r in records)
                rows.append({
                    "profile": pid, "profile_id": source.get("id", pid),
                    "unit": unit, "network_scope": scope,
                    "graph_scope": graph_scope, "run_directory": pid,
                    "execution_mode": "native-graph-sensitivity",
                    "primary_denominator": "eligible nonauxiliary length; component threshold uses eligible mass",
                    "min_usable_component_km": threshold, "scenario": sid,
                    "walkable_km": walk,
                    "pess_usable_pct": 100 * pess_eligible / eligible if eligible else None,
                    "opt_usable_pct": 100 * opt_eligible / eligible if eligible else None,
                    "decidability_gap_pct": 100 * (opt_eligible - pess_eligible) / eligible if eligible else None,
                    "all_length_pess_usable_pct": pess, "all_length_opt_usable_pct": opt,
                    "all_length_gap_pct": opt - pess, "pass_pct": pct("pass_km"),
                    "block_pct": pct("block_km"), "unknown_pct": pct("unknown_km"),
                    "unknown_graph_only_pct": pct("unknown_graph_only_km"),
                    "unknown_road_sidewalk_pct": pct("unknown_road_sidewalk_km"),
                    "eligible_km": eligible,
                    "pess_eligible_usable_km": pess_eligible, "opt_eligible_usable_km": opt_eligible,
                    "eligible_pess_pct": 100 * pess_eligible / eligible if eligible else None,
                    "eligible_opt_pct": 100 * opt_eligible / eligible if eligible else None,
                    "eligible_gap_pct": 100 * (opt_eligible - pess_eligible) / eligible if eligible else None,
                    "profile_sha256": profile_hash, "jar_sha256": jar_hash,
                })
    if failures:
        raise RuntimeError("Missing, stale or failed scenarios (aggregate not replaced): " + ", ".join(failures))
    if not rows:
        raise RuntimeError("No scenario results")
    if versioned and len(profiles) != 3:
        print("Verified selected corrected profiles; full aggregate requires all three profiles.")
        return
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "profile_sensitivity.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} verified scenario rows to {path}")
    print("Compare both endpoints and graph composition; a small gap alone is not evidence of usability.")


if __name__ == "__main__":
    main()
