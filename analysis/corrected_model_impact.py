"""Compare a separately executed corrected Greek census with frozen Greek outputs.

Only the matched BVI snapshot and full-strata frame are compared. This does not
refresh the saved country, profile, targeting or downstream geographic results.
"""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FROZEN_VERSION = "conditional-2026-09-15"
CORRECTED_VERSION = "conditional-2026-10-09"
IMPACT_DIR = Path("results/impact/greece-full-strata-conditional-2026-10-09")
INPUTS = {
    "pbf": Path("data/greece-latest.osm.pbf"),
    "strata": Path("data/strata.json"),
    "profile": Path("data/profile-bvi.json"),
}
COMPARISON_FILES = ("summary.csv", "crossing_metrics.csv", "destinations.csv", "feasibility.csv",
                    "feasibility_global.csv", "feasibility_sensitivity.csv")
PLACE_COLUMNS = ("stratum", "layer", "frozen_pess_pct", "corrected_pess_pct",
                 "delta_pess_pp", "frozen_opt_pct", "corrected_opt_pct", "delta_opt_pp")


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        result = list(csv.DictReader(stream))
    if not result:
        raise ValueError(f"Empty corrected-model comparison source: {path}")
    return result


def indexed(items, field):
    result = {}
    for row in items:
        key = row[field]
        if not key or key in result:
            raise ValueError(f"Missing or duplicate {field} in impact comparison: {key!r}")
        result[key] = row
    return result


def number(row, field):
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid impact source field {field!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Nonfinite or negative impact source field {field!r}")
    return value


def count(row, field):
    value = number(row, field)
    if not value.is_integer():
        raise ValueError(f"Noninteger impact inventory count: {field}")
    return int(value)


def endpoints(row):
    eligible = number(row, "eligible_km")
    if not eligible:
        return None, None
    pess = number(row, "pess_eligible_usable_km")
    opt = number(row, "opt_eligible_usable_km")
    if pess > opt + 0.005 or opt > eligible + 0.005:
        raise ValueError("Invalid corrected-model eligible-length endpoints")
    return round(100 * pess / eligible, 4), round(100 * opt / eligible, 4)


def check_manifest(root, manifest, corrected_dir):
    if (manifest.get("schema_version") != 1 or manifest.get("status") != "complete"
            or manifest.get("model_version") != CORRECTED_VERSION):
        raise ValueError("No completed, versioned corrected Greece execution")
    command = manifest.get("command")
    if (not isinstance(command, list) or len(command) < 11 or command[:3] != ["java", "-Xmx5g", "-jar"]
            or not isinstance(command[3], str) or not isinstance(command[4], str)
            or not isinstance(command[5], str) or not isinstance(command[6], str)
            or not isinstance(command[7], str)):
        raise ValueError("Invalid corrected Greece execution command")
    for index, path in ((4, INPUTS["pbf"]), (5, INPUTS["strata"]), (7, INPUTS["profile"])):
        recorded = command[index].removeprefix("--profile=") if index == 7 else command[index]
        if Path(recorded).resolve() != (root / path).resolve():
            raise ValueError(f"Corrected run used a different {path}")
    if Path(command[6]).resolve() != corrected_dir.resolve():
        raise ValueError("Corrected run used a different output directory")
    if (Path(command[3]).resolve() == (root / "census/target/census.jar").resolve()
            or not {"--targets=5000", "--graph-scope=extended", "--core-sensitivity",
                    "--component-thresholds=0.5,1,2"}.issubset(command)):
        raise ValueError("Corrected run used unexpected model options or the frozen JAR")
    fingerprints = manifest.get("input_sha256")
    if not isinstance(fingerprints, dict):
        raise ValueError("Corrected run lacks input fingerprints")
    for key, relative in INPUTS.items():
        if fingerprints.get(key) != digest(root / relative):
            raise ValueError(f"Corrected run input changed: {relative}")
    if fingerprints.get("jar") != digest(Path(command[3])):
        raise ValueError("Corrected executable changed after the run")
    if fingerprints.get("pom") != digest(root / "census/pom.xml"):
        raise ValueError("Corrected Maven build descriptor changed")
    source_dir = root / "census/src/main/java"
    recorded_sources = manifest.get("source_sha256")
    if not isinstance(recorded_sources, dict):
        raise ValueError("Corrected run lacks Java source fingerprints")
    current_sources = {
        str(path.relative_to(source_dir)).replace("\\", "/"): digest(path)
        for path in source_dir.rglob("*.java")
    }
    normalized_sources = {name.replace("\\", "/"): sha for name, sha in recorded_sources.items()}
    if normalized_sources != current_sources:
        raise ValueError("Corrected Java source has changed since the run")
    recorded_outputs = manifest.get("output_sha256")
    if not isinstance(recorded_outputs, dict) or not set(COMPARISON_FILES).issubset(recorded_outputs):
        raise ValueError("Corrected execution lacks required outputs")
    actual_files = {
        path.name for path in corrected_dir.iterdir()
        if path.is_file() and path.name != "run_manifest.json" and path.suffix in (".csv", ".json", ".txt")
    }
    if actual_files != set(recorded_outputs):
        raise ValueError("Corrected execution output inventory changed")
    for name, saved_hash in recorded_outputs.items():
        if Path(name).name != name or digest(corrected_dir / name) != saved_hash:
            raise ValueError(f"Corrected execution output changed: {name}")
    if (not manifest.get("started_at_utc") or not manifest.get("completed_at_utc")
            or manifest["started_at_utc"] > manifest["completed_at_utc"]):
        raise ValueError("Corrected execution times are missing or reversed")


def analyze_sources(root=ROOT):
    root = Path(root)
    corrected_dir = root / IMPACT_DIR
    manifest_path = corrected_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    check_manifest(root, manifest, corrected_dir)
    frozen_dir = root / "results/census"
    old = indexed(rows(frozen_dir / "feasibility.csv"), "stratum")
    new = indexed(rows(corrected_dir / "feasibility.csv"), "stratum")
    if set(old) != set(new):
        raise ValueError("Greek reporting strata differ between model versions")
    for name in old:
        if ((old[name]["layer"], old[name]["group"]) != (new[name]["layer"], new[name]["group"])
                or abs(number(old[name], "eligible_km") - number(new[name], "eligible_km")) > 0.005):
            raise ValueError(f"Greek stratum eligibility or assignment changed: {name}")
    if (old["NATIONAL"].get("model_version") != FROZEN_VERSION
            or new["NATIONAL"].get("model_version") != CORRECTED_VERSION):
        raise ValueError("Greece impact compared the wrong model versions")
    places = []
    for stratum in ["NATIONAL"] + sorted(
            name for name, row in old.items() if row.get("layer") == "circle"):
        before, after = old[stratum], new[stratum]
        if ((before["layer"], before["group"]) != (after["layer"], after["group"])
                or before["profile"] != after["profile"] or before["profile"] != "bvi-v0"
                or before["graph_scope"] != after["graph_scope"] or before["graph_scope"] != "extended"
                or number(before, "component_threshold_km") != 1
                or number(after, "component_threshold_km") != 1
                or abs(number(before, "eligible_km") - number(after, "eligible_km")) > 0.005):
            raise ValueError(f"Different Greece impact frame or settings: {stratum}")
        old_pess, old_opt = endpoints(before)
        new_pess, new_opt = endpoints(after)
        places.append({
            "stratum": stratum, "layer": before["layer"],
            "frozen_pess_pct": old_pess, "corrected_pess_pct": new_pess,
            "delta_pess_pp": round(new_pess - old_pess, 4) if old_pess is not None else None,
            "frozen_opt_pct": old_opt, "corrected_opt_pct": new_opt,
            "delta_opt_pp": round(new_opt - old_opt, 4) if old_opt is not None else None,
        })
    old_country = next(row for row in rows(root / "results/countries/greece/feasibility.csv")
                       if row["stratum"] == "NATIONAL")
    if (old_country["model_version"] != FROZEN_VERSION
            or any(abs(number(old["NATIONAL"], key) - number(old_country, key)) > 0.005
                   for key in ("eligible_km", "pess_eligible_usable_km", "opt_eligible_usable_km"))):
        raise ValueError("Saved Greek country baseline disagrees with the full-strata baseline")

    def sensitivity(folder, version):
        result = {}
        for row in rows(folder / "feasibility_sensitivity.csv"):
            if row["stratum"] != "NATIONAL":
                continue
            key = (row["graph_scope"], number(row, "threshold_km"))
            if key in result or row["model_version"] != version or row["profile"] != "bvi-v0":
                raise ValueError("Invalid or duplicate national threshold/scope comparison")
            result[key] = endpoints(row)
        return result

    old_sensitivity = sensitivity(frozen_dir, FROZEN_VERSION)
    new_sensitivity = sensitivity(corrected_dir, CORRECTED_VERSION)
    expected = {(scope, threshold) for scope in ("extended", "core") for threshold in (0.5, 1.0, 2.0)}
    if set(old_sensitivity) != expected or set(new_sensitivity) != expected:
        raise ValueError("Corrected native threshold/scope coverage differs from frozen coverage")
    if (max(abs(a - b) for a, b in zip(old_sensitivity["extended", 1.0],
                                      (places[0]["frozen_pess_pct"], places[0]["frozen_opt_pct"]))) > 0.01
            or max(abs(a - b) for a, b in zip(new_sensitivity["extended", 1.0],
                                              (places[0]["corrected_pess_pct"], places[0]["corrected_opt_pct"]))) > 0.01):
        raise ValueError("Native 1 km result disagrees with the main Greek result")
    threshold_rows = []
    for scope, threshold in sorted(expected, key=lambda key: (key[0] != "extended", key[1])):
        prior_pess, prior_opt = old_sensitivity[scope, threshold]
        current_pess, current_opt = new_sensitivity[scope, threshold]
        threshold_rows.append({
            "scope": scope, "threshold_km": threshold,
            "frozen_pess_pct": prior_pess, "corrected_pess_pct": current_pess,
            "frozen_opt_pct": prior_opt, "corrected_opt_pct": current_opt,
        })
    prior_crossings = indexed(rows(frozen_dir / "crossing_metrics.csv"), "stratum")["NATIONAL"]
    new_crossings = indexed(rows(corrected_dir / "crossing_metrics.csv"), "stratum")["NATIONAL"]
    inventory = {}
    for key in ("crossings", "crossing_signalised", "signal_sound_yes"):
        before, after = count(prior_crossings, key), count(new_crossings, key)
        inventory[key] = {"frozen": before, "corrected": after, "delta": after - before}
    corrected_destinations = [
        row for row in rows(corrected_dir / "destinations.csv") if row["stratum"] == "NATIONAL"]
    if {row["kind"] for row in corrected_destinations} != {"stop", "station", "poi"}:
        raise ValueError("Corrected Greek destination inventories are incomplete")
    destination_statements = {
        field: sum(count(row, field) for row in corrected_destinations)
        for field in ("braille_stated", "braille_yes", "tactile_writing_stated", "tactile_writing_yes")
    }
    source_paths = (
        Path("analysis/corrected_model_impact.py"),
        Path("census/target/census.jar"),
        Path("results/countries/greece/feasibility.csv"),
        Path("data/strata.json"), Path("data/profile-bvi.json"),
        Path("results/census/feasibility.csv"), Path("results/census/feasibility_sensitivity.csv"),
        Path("results/census/crossing_metrics.csv"),
        IMPACT_DIR / "run_manifest.json",
        *(IMPACT_DIR / name for name in COMPARISON_FILES),
    )
    sources = {str(path).replace("\\", "/"): digest(root / path) for path in source_paths}
    return {
        "schema_version": 1,
        "frozen_model_version": FROZEN_VERSION,
        "corrected_model_version": CORRECTED_VERSION,
        "snapshot_sha256": manifest["input_sha256"]["pbf"],
        "corrected_jar_sha256": manifest["input_sha256"]["jar"],
        "execution_started_utc": manifest["started_at_utc"],
        "execution_completed_at_utc": manifest["completed_at_utc"],
        "profile": "bvi-v0", "graph_scope": "extended", "component_threshold_km": 1.0,
        "strata_compared": len(old), "source_sha256": sources,
        "places": places, "thresholds": threshold_rows,
        "inventory_national": inventory,
        "corrected_destination_statements": destination_statements,
        "limitations": (
            "Greece only: international country/profile results and downstream exposure, "
            "campaign, targeting and geographic analyses still use the frozen model. "
            "Snapshot, configuration and eligible-length frame are held fixed; old and "
            "corrected percentages are model-conditional, not physical accessibility. "
            "Frozen native threshold/scope artifact execution linkage remains unverified; "
            "the new run record does not establish its past command or time."
        ),
    }


def load_report(root=ROOT):
    root = Path(root)
    expected = analyze_sources(root)
    json_path = root / "results/analysis/corrected_model_impact.json"
    csv_path = root / "results/analysis/corrected_model_impact.csv"
    actual = json.loads(json_path.read_text(encoding="utf-8-sig"))
    if actual != expected:
        raise ValueError("Saved corrected-model impact disagrees with its source outputs")
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    if csv_rows != [
            {key: "" if row[key] is None else str(row[key]) for key in PLACE_COLUMNS}
            for row in expected["places"]]:
        raise ValueError("Corrected-model impact CSV/JSON disagree")
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    report = analyze_sources(args.root)
    output = args.root / "results/analysis"
    output.mkdir(parents=True, exist_ok=True)
    (output / "corrected_model_impact.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (output / "corrected_model_impact.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=PLACE_COLUMNS)
        writer.writeheader()
        writer.writerows({key: row[key] for key in PLACE_COLUMNS} for row in report["places"])
    print(f"Wrote corrected Greece impact for {len(report['places'])} national/circle units")


if __name__ == "__main__":
    main()
