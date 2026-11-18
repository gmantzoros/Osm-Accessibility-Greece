#!/usr/bin/env python3
"""Run one protocol sequentially over cached comparison extracts.

Definitions are held constant across the selected countries. Descriptive
correlations do not separate causal effects of population, density or mapping
activity, and countries are not a probability sample.

Covariates are GISCO POP_2021/AREA_KM2 field sums. The layer label does not
establish observation year: country reference years may differ and are unverified.
Cached input content, OSM headers, executable, profile and strata are fingerprinted.

Usage:
    python analysis/cross_country.py            # download + run everything
    python analysis/cross_country.py --report   # re-aggregate what is done
"""

import csv
import argparse
import datetime
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import zlib
from pathlib import Path

from corrected_model_impact import CORRECTED_VERSION

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAR = os.path.join(ROOT, "census", "target", "census.jar")
STRATA = os.path.join(ROOT, "data", "strata_national.json")
PROFILE = os.path.join(ROOT, "data", "profile-bvi.json")
PBF_DIR = os.path.join(ROOT, "data", "countries")
OUT = os.path.join(ROOT, "results", "countries")
LAU = os.path.join(ROOT, "data", "external", "LAU_RG_01M_2021_4326.geojson")
# Two sources, tried in order. Geofabrik is the canonical one but was returning
# HTTP 502 for every country extract when this was run (its metadata files served
# fine, so the fault was in large-file delivery, not our client). The OSM-France
# mirror carries the same Geofabrik-style extracts and is tried first when the
# canonical source is unavailable. Whichever served each file is recorded.
# Geofabrik was returning HTTP 502 for every country extract on the first run, so
# the OSM-France mirror was tried first. Geofabrik has since recovered and is the
# canonical source, so it leads again; OSM-France stays as the fallback, but note
# it carries no Balkan extracts (404 for bulgaria, croatia, romania, serbia).
MIRRORS = [
    ("geofabrik", "https://download.geofabrik.de/europe/{}-latest.osm.pbf"),
    ("osm-fr",    "https://download.openstreetmap.fr/extracts/europe/{}-latest.osm.pbf"),
]

# slug, ISO code used in the LAU file, display name, approx PBF size in GB
COUNTRIES = [
    # Ordered smallest-first so a configuration error surfaces cheaply.
    # Bulgaria, Croatia and Hungary were the original Balkan/Central peers; the
    # mirror carries no Balkan extracts, so Ireland, Slovakia and Finland stand in.
    # The substitution widens the density range (16-465/km2) and creates a second
    # near-twin population cluster at ~5.3M, which helps separate population from
    # density. It costs the study its South-East European peer group - stated as a
    # limitation rather than papered over.
    ("ireland",        "IE", "Ireland",     0.35),
    ("portugal",       "PT", "Portugal",    0.39),
    ("slovakia",       "SK", "Slovakia",    0.32),
    ("denmark",        "DK", "Denmark",     0.46),
    ("finland",        "FI", "Finland",     0.69),
    ("belgium",        "BE", "Belgium",     0.64),
    ("austria",        "AT", "Austria",     0.75),
    ("sweden",         "SE", "Sweden",      0.76),
    ("czech_republic", "CZ", "Czechia",     0.88),
    ("netherlands",    "NL", "Netherlands", 1.3),
    # South-East European peer group, added once Geofabrik recovered. Bulgaria,
    # Croatia and Hungary are the comparators originally selected for this study;
    # Romania completes a regional block spanning 4.3-22.1M population, so the
    # question of whether Greece's position is national or regional is testable.
    ("bulgaria",       "BG", "Bulgaria",    0.17),
    ("croatia",        "HR", "Croatia",     0.20),
    ("hungary",        "HU", "Hungary",     0.32),
    ("romania",        "RO", "Romania",     0.33),
    # Completing the immediate neighbourhood and the Mediterranean south, and
    # extending the population range upward. Ordered smallest-first: Italy is the
    # largest extract attempted here and the reader's memory use scales with node
    # count, so a failure surfaces after the cheap runs rather than before them.
    ("cyprus",         "CY", "Cyprus",      0.04),
    ("albania",        "AL", "Albania",     0.05),
    ("serbia",         "RS", "Serbia",      0.24),
    ("spain",          "ES", "Spain",       1.48),
    ("italy",          "IT", "Italy",       2.23),
]
GREECE = ("greece", "EL", "Greece", 0.34)
RESULT_FILES = ("summary.csv", "crossing_metrics.csv", "feasibility.csv", "feasibility_global.csv")


def fingerprint(path):
    hashes = {"sha256": hashlib.sha256(), "md5": hashlib.md5()}
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 << 20), b""):
            for digest in hashes.values():
                digest.update(chunk)
    return {"bytes": os.path.getsize(path), **{k: v.hexdigest() for k, v in hashes.items()}}


def protobuf_fields(data):
    """Decode only protobuf wire types needed by the PBF header."""
    pos = 0
    def varint():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                raise ValueError("Truncated protobuf varint")
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise ValueError("Invalid protobuf varint")
    while pos < len(data):
        tag = varint()
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else (8 if wire == 1 else 4)
            if pos + length > len(data):
                raise ValueError("Truncated protobuf field")
            value = data[pos:pos + length]
            pos += length
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire}")
        yield field, value


def pbf_header(path):
    with open(path, "rb") as fh:
        size_bytes = fh.read(4)
        if len(size_bytes) != 4:
            raise ValueError(f"Missing PBF header: {path}")
        size = int.from_bytes(size_bytes, "big")
        if not 0 < size <= 65536:
            raise ValueError(f"Invalid PBF header length: {path}")
        header = dict(protobuf_fields(fh.read(size)))
        if header.get(1) != b"OSMHeader":
            raise ValueError(f"Not an OSM PBF header: {path}")
        blob_size = header.get(3, 0)
        if not 0 < blob_size <= 32 << 20:
            raise ValueError("Invalid PBF header blob length")
        blob = dict(protobuf_fields(fh.read(blob_size)))
    if 1 in blob:
        raw = blob[1]
    elif 3 in blob:
        raw = zlib.decompress(blob[3])
        if len(raw) != blob.get(2):
            raise ValueError("PBF header decompressed length mismatch")
    else:
        raise ValueError("Unsupported PBF header compression")
    fields = dict(protobuf_fields(raw))
    timestamp = fields.get(32)
    return {
        "replication_timestamp": timestamp,
        "replication_timestamp_utc": (
            datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).isoformat()
            if timestamp is not None else None),
        "replication_sequence": fields.get(33),
        "replication_base_url": fields.get(34, b"").decode("utf-8"),
        "writing_program": fields.get(16, b"").decode("utf-8"),
        "source": fields.get(17, b"").decode("utf-8"),
        "timestamp_note": "OSMHeader replication timestamp; missing is unknown, never inferred from filename/mtime",
    }


def input_provenance(path, source_url=None, *, persist=True):
    sidecar = path + ".provenance.json"
    prior = {}
    if os.path.isfile(sidecar):
        with open(sidecar, encoding="utf-8") as fh:
            prior = json.load(fh)
    content = fingerprint(path)
    same_content = prior.get("content") == content
    legacy_source, legacy_status = None, "not available"
    snapshot_path = os.path.join(os.path.dirname(path), "SNAPSHOT.txt")
    if os.path.isfile(snapshot_path):
        with open(snapshot_path, encoding="utf-8-sig") as fh:
            snapshot = dict(line.strip().split("=", 1) for line in fh if "=" in line)
        if snapshot.get("file") == os.path.basename(path):
            matched = any(snapshot.get(algorithm, "").lower() == content[algorithm]
                          for algorithm in ("md5", "sha256"))
            legacy_status = "content checksum matched" if matched else "checksum mismatch; source not adopted"
            if matched:
                legacy_source = snapshot.get("url")
    recorded_source = source_url or (prior.get("source_url") if same_content else None) or legacy_source
    provenance = {
        "path": os.path.relpath(path, ROOT), "content": content, "osm_header": pbf_header(path),
        "source_url": recorded_source,
        "source_status": ("recorded download URL" if recorded_source else
                          "cached content; original download source unverified"),
        "legacy_snapshot_record": legacy_status,
    }
    if source_url:
        provenance["downloaded_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    elif same_content and "downloaded_at_utc" in prior:
        provenance["downloaded_at_utc"] = prior["downloaded_at_utc"]
    if persist:
        with open(sidecar, "w", encoding="utf-8") as fh:
            json.dump(provenance, fh, indent=2)
    return provenance


def write_input_manifest(slug, provenance, *, out=OUT):
    """Create a content-addressed input record; never replace an existing record."""
    record = {"schema": "osm-pbf-input-provenance-v1", "country_slug": slug, **provenance}
    payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    directory = os.path.join(out, "input_manifests", slug)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, digest + ".json")
    try:
        with open(path, "xb") as fh:
            fh.write(payload)
    except FileExistsError:
        with open(path, "rb") as fh:
            if fh.read() != payload:
                raise ValueError(f"Immutable input manifest content mismatch: {path}")
    return {"path": os.path.relpath(path, ROOT), "sha256": digest}


def manifest_inputs(pbf, profile=PROFILE, strata=STRATA, provenance=None, options=None, *, jar=JAR):
    provenance = provenance or input_provenance(pbf)
    sources = {}
    source_root = os.path.join(ROOT, "census", "src", "main", "java")
    for directory, _, names in os.walk(source_root):
        for name in sorted(names):
            if name.endswith(".java"):
                path = os.path.join(directory, name)
                sources[os.path.relpath(path, source_root)] = fingerprint(path)["sha256"]
    with open(strata, encoding="utf-8") as fh:
        config = json.load(fh)
    assets = {}
    for layer in config.get("polygonLayers", []):
        if layer.get("enabled", True):
            path = os.path.join(os.path.dirname(strata), layer["file"].replace("/", os.sep))
            assets[layer["id"]] = fingerprint(path)
    return {"pbf": provenance["content"], "osm_header": provenance["osm_header"],
            "jar": fingerprint(jar), "profile": fingerprint(profile),
            "strata": fingerprint(strata), "strata_assets": assets,
            "java_sources": sources, "options": options or [],
            "analysis_drivers": {name: fingerprint(os.path.join(ROOT, "analysis", name))["sha256"]
                                 for name in ("cross_country.py", "profile_sensitivity.py")}}


def versioned_model_paths(jar_path, output_path, kind):
    """Allow the corrected JAR to write only to its dedicated versioned tree."""
    from run_corrected_greece import FROZEN_JAR_SHA256, digest, source_hashes

    if not jar_path or not output_path:
        raise ValueError("A corrected rerun requires both --jar and --output")
    root = Path(ROOT).resolve()
    target = root / "results" / "impact" / f"unified-{CORRECTED_VERSION}" / kind
    if target.resolve() != target or Path(output_path).resolve() != target:
        raise ValueError(f"Corrected {kind} output must be an unlinked {target}")
    jar = Path(jar_path).resolve(strict=True)
    frozen = root / "census" / "target" / "census.jar"
    if jar == frozen or digest(frozen) != FROZEN_JAR_SHA256 or digest(jar) == FROZEN_JAR_SHA256:
        raise ValueError("Refusing the protected or changed frozen census JAR")
    stage = jar.parent.parent
    if (jar.name != "census.jar" or jar.parent.name != "target"
            or stage.is_relative_to(root)
            or digest(stage / "pom.xml") != digest(root / "census" / "pom.xml")):
        raise ValueError("Corrected JAR must be built from a separate copy of the current Maven source")
    source_hashes(root, stage)
    return str(jar), str(target)


def current_manifest(out_dir, inputs, result_files=RESULT_FILES):
    path = os.path.join(out_dir, "run_manifest.json")
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    return (saved.get("inputs") == inputs and saved.get("status") == "complete"
            and all(os.path.isfile(os.path.join(out_dir, name)) and
                    saved.get("outputs", {}).get(name) == fingerprint(os.path.join(out_dir, name))
                    for name in result_files))


def require_corrected_national(path, expected_version):
    national = [row for row in load_csv(path) if row.get("stratum") == "NATIONAL"]
    if (len(national) != 1 or national[0].get("model_version") != expected_version
            or national[0].get("graph_scope") != "extended"):
        raise ValueError(f"Corrected national model version or graph scope disagrees: {path}")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def eligible_endpoints(row):
    """Primary outcome: actual eligible-mass-thresholded length / eligible length."""
    try:
        eligible = float(row["eligible_km"])
        pess = float(row["pess_eligible_usable_km"])
        opt = float(row["opt_eligible_usable_km"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Corrected eligible-length feasibility fields are required") from exc
    if not all(math.isfinite(v) and v >= 0 for v in (eligible, pess, opt)):
        raise ValueError("Eligible-length endpoints must be finite and nonnegative")
    if pess > opt + .002 or opt > eligible + .002:
        raise ValueError("Eligible-length endpoints violate pessimistic <= optimistic <= denominator")
    return {
        "eligible_km": eligible, "pess_usable_km": pess, "opt_usable_km": opt,
        "pess_usable_pct": 100 * pess / eligible if eligible else None,
        "opt_usable_pct": 100 * opt / eligible if eligible else None,
        "gap_pct": 100 * (opt - pess) / eligible if eligible else None,
    }


def load_csv(path):
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def covariates():
    """Historical field sums; layer vintage is not population observation year."""
    if not os.path.isfile(LAU):
        sys.exit(f"Missing {LAU} — run data/fetch_data.ps1 -Aux")
    with io.open(LAU, encoding="utf-8") as fh:
        gj = json.load(fh)
    agg = {}
    for f in gj["features"]:
        p = f["properties"]
        c = p.get("CNTR_CODE")
        if not c:
            continue
        a = agg.setdefault(c, [0.0, 0.0])
        a[0] += p.get("POP_2021") or 0
        a[1] += p.get("AREA_KM2") or 0
    return {c: {"pop": v[0], "area_km2": v[1],
                "density": v[0] / v[1] if v[1] else 0} for c, v in agg.items()}


def download(slug, gb):
    """Try each mirror in turn. Returns (path, source) or raises."""
    os.makedirs(PBF_DIR, exist_ok=True)
    dest = os.path.join(PBF_DIR, f"{slug}-latest.osm.pbf")
    if os.path.isfile(dest):
        pbf_header(dest)
        return dest, None
    last = None
    for source, template in MIRRORS:
        url = template.format(slug)
        print(f"  downloading {slug} (~{gb:.2f} GB) from {source} ...", end="", flush=True)
        t0 = time.time()
        tmp = dest + ".part"
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "osm-accessibility-greece/1.0 (research)"})
            with urllib.request.urlopen(req, timeout=120) as r, io.open(tmp, "wb") as fh:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
            pbf_header(tmp)
            os.replace(tmp, dest)
            print(f" {os.path.getsize(dest)/1e9:.2f} GB in {time.time()-t0:.0f}s")
            return dest, url
        except (OSError, ValueError, urllib.error.URLError) as ex:
            last = ex
            print(f" failed ({type(ex).__name__})")
            if os.path.isfile(tmp):
                os.remove(tmp)
    raise RuntimeError(f"all mirrors failed for {slug}: {last}")


def heap_for(path):
    """Coordinate arrays dominate; scale the heap with the extract, within reason."""
    gb = os.path.getsize(path) / 1e9
    return f"-Xmx{min(13, max(5, int(gb * 7) + 3))}g"


def run_census(slug, pbf, provenance=None, report_only=False, heap=None, *,
               jar=JAR, out=OUT, threads=None, expected_version=None):
    out_dir = os.path.join(out, slug)
    marker = os.path.join(out_dir, "feasibility.csv")
    options = [f"--threads={threads}"] if threads is not None else []
    inputs = manifest_inputs(pbf, provenance=provenance, options=options, jar=jar)
    if current_manifest(out_dir, inputs):
        if expected_version is not None:
            require_corrected_national(marker, expected_version)
        return out_dir, True
    if report_only:
        print(f"  {slug}: missing/stale run manifest; excluded (no census started)")
        return out_dir, False
    os.makedirs(out_dir, exist_ok=True)
    heap_flag = f"-Xmx{heap}" if heap else heap_for(pbf)
    cmd = ["java", heap_flag, "-jar", jar, pbf, STRATA, out_dir, f"--profile={PROFILE}", *options]
    print(f"  census {slug} ({heap_flag}) ...", end="", flush=True)
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    with open(os.path.join(out_dir, "run.log"), "w", encoding="utf-8") as fh:
        fh.write(p.stdout + "\n" + p.stderr)
    if p.returncode != 0 or not os.path.isfile(marker):
        print(" FAILED")
        sys.stderr.write(p.stderr[-1500:] + "\n")
        return out_dir, False
    if expected_version is not None:
        require_corrected_national(marker, expected_version)
    manifest = {"status": "complete", "inputs": inputs, "command": cmd,
                "completed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "elapsed_seconds": time.time() - t0,
                "outputs": {name: fingerprint(os.path.join(out_dir, name)) for name in RESULT_FILES}}
    with open(os.path.join(out_dir, "run_manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f" {time.time()-t0:.0f}s")
    return out_dir, True


def collect(slug, code, name, cov, *, out=OUT):
    d = os.path.join(out, slug)
    S = {r["stratum"]: r for r in load_csv(os.path.join(d, "summary.csv"))}
    C = {r["stratum"]: r for r in load_csv(os.path.join(d, "crossing_metrics.csv"))}
    F = {r["stratum"]: r for r in load_csv(os.path.join(d, "feasibility.csv"))}
    if "NATIONAL" not in S or "NATIONAL" not in F:
        return None
    s, c, f = S["NATIONAL"], C["NATIONAL"], F["NATIONAL"]
    k = cov.get(code, {})
    walk = num(f["walkable_km"])
    primary = eligible_endpoints(f)
    eligible = primary["eligible_km"]
    if eligible <= 0:
        print(f"  {name}: no eligible network; excluded from country comparisons")
        return None
    drv = num(s["drivable_km"])
    return {
        "country": name, "code": code,
        "pop_2021": round(k["pop"]) if k.get("pop", 0) > 0 else "",
        "population_source_field": "GISCO LAU 2021 POP_2021",
        "population_reference_year": "mixed/reference year unverified",
        "area_km2": round(k["area_km2"]) if k.get("area_km2", 0) > 0 else "",
        "density_per_km2": round(k["density"], 1) if k.get("pop", 0) > 0 and k.get("area_km2", 0) > 0 else "",
        "drivable_km": round(drv),
        "road_km_per_1000_pop": round(1000 * drv / k["pop"], 2) if k.get("pop") else "",
        "intersections": int(num(s["intersections"])),
        "crossings": int(num(s["crossings"])),
        "crossings_per_100_ix": num(s["crossings_per_100_intersections"]),
        "junctions_degree3": int(c["junctions_degree3"]),
        "crossings_per_100_junctions": float(c["crossings_per_100_junctions"]) if c["crossings_per_100_junctions"] else "",
        "crossing_conflicts": int(c["crossing_conflicts"]),
        "name_pct": num(s["name_pct"]),
        "sidewalk_pct": num(s["sidewalk_pct"]),
        "sidewalk_aff_pct": num(s["sidewalk_aff_pct"]),
        "lit_pct": num(s["lit_pct"]),
        "tactile_stated": int(num(c["crossing_tactile"])),
        "tactile_pct": num(s["tactile_pct"]),
        "signals": int(num(s["signals"])),
        "sound_pct": num(s["sound_pct"]),
        "signal_sound_stated": int(num(c["signal_sound"])),
        "signal_sound_affirmative": int(num(c["signal_sound_yes"])),
        "sound_yes_pct": num(s["sound_yes_pct"]),
        "crossing_sound_stated": int(c["crossing_sound"]),
        "crossing_sound_affirmative": int(c["crossing_sound_yes"]),
        "legacy_crossing_nodes": int(c["legacy_crossing_nodes"]),
        "markings_modern_share_pct": num(s["markings_modern_share_pct"]),
        "schema_way_share_pct": num(s["schema_way_share_pct"]),
        "bpri_v1": num(s["bpri_v1"]),
        "bpri_v0": num(s["bpri_v0"]),
        "model_version": s["model_version"],
        "walkable_km": round(walk),
        "eligible_km": eligible if eligible is not None else "",
        "graph_scope": f.get("graph_scope", "legacy unspecified"),
        "primary_denominator": "eligible nonauxiliary length; component threshold uses eligible mass",
        "eligible_pess_usable_pct": 100 * float(f["pess_eligible_usable_km"]) / eligible if eligible else "",
        "eligible_opt_usable_pct": 100 * float(f["opt_eligible_usable_km"]) / eligible if eligible else "",
        "eligible_gap_pct": float(f["eligible_decidability_gap_pct"]) if "eligible_decidability_gap_pct" in f else "",
        "decided_pct": 100 * (num(f["pass_km"]) + num(f["block_km"])) / walk if walk else "",
        "missing_evidence_unknown_km": float(f["missing_evidence_unknown_km"]) if "missing_evidence_unknown_km" in f else "",
        "policy_unknown_km": float(f["policy_unknown_km"]) if "policy_unknown_km" in f else "",
        "core_km": "",
        "pess_usable_pct": primary["pess_usable_pct"],
        "opt_usable_pct": primary["opt_usable_pct"],
        "gap_pct": primary["gap_pct"],
        "all_length_pess_usable_pct": num(f["pess_usable_pct"]),
        "all_length_opt_usable_pct": num(f["opt_usable_pct"]),
        "all_length_gap_pct": num(f["decidability_gap_pct"]),
        "unknown_pct": 100 * num(f["unknown_km"]) / walk if walk else 0,
        "core_pess_pct": "",
    }


def spearman(a, b):
    """Rank correlation with proper average ranks for ties."""
    n = len(a)
    if n < 3:
        return None

    def ranks(x):
        order = sorted(range(n), key=lambda i: x[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and x[order[j + 1]] == x[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    num_ = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((x - mb) ** 2 for x in rb))
    return round(num_ / den, 3) if den else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="Verify and aggregate only; never run a census")
    parser.add_argument("--countries", help="Comma-separated extract slugs; default all configured countries")
    parser.add_argument("--fingerprint-only", action="store_true", help="Fingerprint existing inputs without census runs")
    parser.add_argument("--heap", help="Maximum JVM heap, e.g. 6g; select for available memory before starting a census")
    parser.add_argument("--threads", type=int, help="Parser threads for a new census")
    parser.add_argument("--jar", help="Separately staged corrected census.jar; requires --output")
    parser.add_argument("--output", help="Versioned corrected countries directory; requires --jar")
    args = parser.parse_args()
    versioned = bool(args.jar or args.output)
    jar, out = (versioned_model_paths(args.jar, args.output, "countries") if versioned
                else (JAR, OUT))
    if args.threads is not None and args.threads < 1:
        parser.error("--threads must be positive")
    report_only = args.report
    if args.countries:
        missing = set(args.countries.split(",")) - {c[0] for c in [GREECE] + COUNTRIES}
        if missing:
            raise ValueError(f"Unknown country slugs: {sorted(missing)}")
    if not args.fingerprint_only and not os.path.isfile(jar):
        sys.exit(f"Missing {jar} — build in a separate Maven directory")
    os.makedirs(out, exist_ok=True)
    cov = {} if args.fingerprint_only else covariates()

    rows, failures, provenance_rows = [], [], []
    # Greece re-uses the national extract already on disk
    for slug, code, name, gb in [GREECE] + COUNTRIES:
        if args.countries and slug not in args.countries.split(","):
            continue
        source = None
        if slug == "greece":
            pbf = os.path.join(ROOT, "data", "greece-latest.osm.pbf")
            if not os.path.isfile(pbf):
                print("  greece extract missing, skipping")
                failures.append(slug)
                continue
        elif report_only or args.fingerprint_only or versioned:
            pbf = os.path.join(PBF_DIR, f"{slug}-latest.osm.pbf")
            if not os.path.isfile(pbf):
                failures.append(slug)
                continue
        else:
            try:
                pbf, source = download(slug, gb)
            except (OSError, RuntimeError, ValueError) as ex:
                print(f"  {slug}: {ex}")
                failures.append(slug)
                continue
        print(f"  fingerprint {slug} ({os.path.getsize(pbf) / 1e9:.3f} GB)", flush=True)
        provenance = input_provenance(pbf, source, persist=not versioned)
        immutable = write_input_manifest(slug, provenance, out=out)
        provenance_rows.append({"country": name, "immutable_manifest": immutable, **provenance})
        print(f"    OSMHeader date: {provenance['osm_header']['replication_timestamp_utc'] or 'unknown'}", flush=True)
        if args.fingerprint_only:
            continue
        _, ok = run_census(
            slug, pbf, provenance, report_only, args.heap,
            jar=jar, out=out, threads=args.threads,
            expected_version=CORRECTED_VERSION if versioned else None,
        )
        if not ok:
            failures.append(slug)
            continue
        r = collect(slug, code, name, cov, out=out)
        if r:
            r.update({"snapshot_timestamp": provenance["osm_header"]["replication_timestamp_utc"] or "",
                      "source": provenance["source_url"] or provenance["source_status"],
                      "pbf_sha256": provenance["content"]["sha256"], "pbf_md5": provenance["content"]["md5"],
                      "jar_sha256": fingerprint(jar)["sha256"], "profile_sha256": fingerprint(PROFILE)["sha256"],
                      "input_manifest_path": immutable["path"], "input_manifest_sha256": immutable["sha256"],
                      "provenance_status": "manifest verified"})
            rows.append(r)
        else:
            failures.append(slug)

    with open(os.path.join(out, "input_provenance.json"), "w", encoding="utf-8") as fh:
        json.dump({"inputs": provenance_rows, "excluded_or_failed": failures,
                   "requested_slugs": args.countries.split(",") if args.countries else [c[0] for c in [GREECE] + COUNTRIES],
                   "covariates": "GISCO population fields; reference years unverified"}, fh, indent=2)
    if args.fingerprint_only:
        return 1 if failures else 0
    if versioned and failures:
        raise RuntimeError("Corrected country runs incomplete; no master aggregate: " + ", ".join(failures))
    if versioned and args.countries:
        print("Verified selected corrected countries; full aggregate requires all 20 countries.")
        return 0
    if versioned and len(rows) != len(COUNTRIES) + 1:
        raise RuntimeError(f"Corrected aggregate requires 20 countries; found {len(rows)}")

    if not rows:
        sys.exit("No countries completed.")
    rows.sort(key=lambda r: -num(r["pop_2021"]))
    path = os.path.join(out, "cross_country.csv")
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- correlations
    covs = [("pop_2021", "population"), ("area_km2", "area"),
            ("density_per_km2", "density"), ("road_km_per_1000_pop", "road km / 1000 pop")]
    mets = [("pess_usable_pct", "pessimistic usable %"), ("gap_pct", "decidability gap"),
            ("crossings_per_100_ix", "crossings /100 ix"), ("tactile_pct", "tactile % of crossings"),
            ("crossings_per_100_junctions", "crossings /100 degree-three junctions"),
            ("sound_pct", "signals with sound statement %"), ("name_pct", "name completeness %"),
            ("sidewalk_pct", "sidewalk tag %"), ("bpri_v1", "BPRI v1")]
    corr = []
    for ck, cn in covs:
        for mk, mn in mets:
            pairs = [r for r in rows if r[ck] not in ("", None) and r[mk] not in ("", None)]
            a = [num(r[ck]) for r in pairs]
            b = [num(r[mk]) for r in pairs]
            rho = spearman(a, b)
            if rho is not None:
                corr.append({"covariate": cn, "metric": mn, "spearman_rho": rho, "n": len(pairs)})
    cpath = os.path.join(out, "cross_country_correlations.csv")
    with io.open(cpath, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["covariate", "metric", "spearman_rho", "n"])
        w.writeheader()
        w.writerows(corr)

    # ---- report
    print(f"\nWrote {path} ({len(rows)} countries) and {cpath}\n")
    print(f"{'country':14}{'pop':>11}{'dens':>7}{'guar%':>7}{'gap':>7}{'cr/100ix':>9}"
          f"{'tactile%':>9}{'sound%':>8}{'name%':>7}{'BPRI':>7}")
    print("-" * 86)
    for r in rows:
        mark = " <" if r["code"] == "EL" else ""
        population = f"{r['pop_2021']:,}" if r["pop_2021"] != "" else "n/a"
        density = f"{r['density_per_km2']:.0f}" if r["density_per_km2"] != "" else "n/a"
        print(f"{r['country']:14}{population:>11}{density:>7}"
              f"{r['pess_usable_pct']:>7.1f}{r['gap_pct']:>7.1f}{r['crossings_per_100_ix']:>9.2f}"
              f"{r['tactile_pct']:>9.2f}{r['sound_pct']:>8.2f}{r['name_pct']:>7.1f}"
              f"{r['bpri_v1']:>7.3f}{mark}")

    print(f"\nSpearman rank correlations (n={len(rows)}):")
    print(f"  {'metric':26}" + "".join(f"{cn:>22}" for _, cn in covs))
    print("  " + "-" * (26 + 22 * len(covs)))
    for mk, mn in mets:
        line = f"  {mn:26}"
        for ck, cn in covs:
            rho = next((c["spearman_rho"] for c in corr if c["covariate"] == cn and c["metric"] == mn), None)
            line += f"{rho if rho is not None else '—':>22}"
        print(line)
    print(f"\n  n={len(rows)} is small: read these as descriptive, not inferential.")
    print("  No informal significance threshold is applied; covariate reference years are unverified.")
    if failures:
        print("  Excluded/failed: " + ", ".join(failures), file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
