"""Execute one isolated corrected Greece census and record its actual inputs/outputs.

Build a corrected JAR from a copy of census/pom.xml and census/src outside the
repository first. Never replace the frozen JAR or the archived census results.
"""

import argparse
import csv
import datetime as dt
import json
import subprocess
from pathlib import Path

from corrected_model_impact import (
    COMPARISON_FILES, CORRECTED_VERSION, IMPACT_DIR, INPUTS, ROOT, digest,
)


FROZEN_JAR_SHA256 = "cdc1480ca05ee45d73f484974e1031e6838c8e7bc45c4435204a10f1b732c059"
GREEK_PBF_SHA256 = "7f973ac69ac39e8a91999e49342de1d9986b70017ca41bc1f5107f573fa101b0"


def source_hashes(root, staged):
    main = root / "census/src/main/java"
    copied = staged / "src/main/java"
    original = {
        str(path.relative_to(main)).replace("\\", "/"): digest(path)
        for path in main.rglob("*.java")
    }
    staged_sources = {
        str(path.relative_to(copied)).replace("\\", "/"): digest(path)
        for path in copied.rglob("*.java")
    }
    if len(original) < 21 or original != staged_sources:
        raise ValueError("Staged corrected Java sources differ from the repository")
    return staged_sources


def run(root, jar):
    root, jar = Path(root).resolve(), Path(jar).resolve()
    output = root / IMPACT_DIR
    frozen_jar = root / "census/target/census.jar"
    if output.exists():
        raise FileExistsError(f"Corrected output already exists; refusing replacement: {output}")
    if jar == frozen_jar or digest(frozen_jar) != FROZEN_JAR_SHA256:
        raise ValueError("Refusing to replace or use the frozen census JAR")
    if jar.name != "census.jar" or jar.parent.name != "target":
        raise ValueError("Supply a separately staged Maven target/census.jar")
    stage = jar.parent.parent
    if (stage == root / "census"
            or digest(stage / "pom.xml") != digest(root / "census/pom.xml")):
        raise ValueError("Corrected build must use a separate copy of the current Maven descriptor")
    sources = source_hashes(root, stage)
    inputs = {key: digest(root / path) for key, path in INPUTS.items()}
    if inputs["pbf"] != GREEK_PBF_SHA256:
        raise ValueError("Greek PBF does not match the frozen snapshot")
    inputs["jar"] = digest(jar)
    inputs["pom"] = digest(stage / "pom.xml")
    pbf, strata, profile = (root / INPUTS[key] for key in ("pbf", "strata", "profile"))
    command = [
        "java", "-Xmx5g", "-jar", str(jar), str(pbf), str(strata), str(output),
        f"--profile={profile}", "--threads=2", "--targets=5000",
        "--graph-scope=extended", "--core-sensitivity",
        "--component-thresholds=0.5,1,2",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    subprocess.run(command, cwd=root, check=True)
    completed = dt.datetime.now(dt.timezone.utc).isoformat()
    for key, path in INPUTS.items():
        if digest(root / path) != inputs[key]:
            raise ValueError(f"Corrected census input changed during execution: {path}")
    if digest(jar) != inputs["jar"] or source_hashes(root, stage) != sources:
        raise ValueError("Corrected executable or source changed during execution")
    files = {
        path.name: digest(path) for path in output.iterdir()
        if path.is_file() and path.suffix in (".csv", ".json", ".txt")
    }
    if not set(COMPARISON_FILES).issubset(files):
        raise ValueError("Corrected Greek run lacks required comparison outputs")
    with (output / "feasibility.csv").open(encoding="utf-8-sig", newline="") as stream:
        national = next((row for row in csv.DictReader(stream) if row["stratum"] == "NATIONAL"), None)
    if (national is None or national["model_version"] != CORRECTED_VERSION
            or national["profile"] != "bvi-v0" or national["graph_scope"] != "extended"
            or national["component_threshold_km"] != "1.000"):
        raise ValueError("Corrected national feasibility output has unexpected model settings")
    manifest = {
        "schema_version": 1, "status": "complete", "model_version": CORRECTED_VERSION,
        "started_at_utc": started, "completed_at_utc": completed,
        "command": command, "input_sha256": inputs, "source_sha256": sources,
        "output_sha256": files,
        "scope": "Greece BVI full strata; isolated corrected impact assessment, not a 20-country regeneration",
    }
    with (output / "run_manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--jar", type=Path, required=True,
                        help="Staged corrected census/target/census.jar outside the repository census tree")
    args = parser.parse_args()
    result = run(args.root, args.jar)
    print(f"Recorded {len(result['output_sha256'])} isolated corrected Greece outputs")


if __name__ == "__main__":
    main()
