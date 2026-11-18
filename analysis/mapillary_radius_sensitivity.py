#!/usr/bin/env python3
"""Compare cached co-detections at several radii on a common boundary-safe frame."""

import csv
import datetime as dt
import hashlib
import io
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from mapillary_fetch import OUT, feature_date, load as load_json, preserve_write, scope_features
from mapillary_join import Index, RADIUS_M, TileCoverage, load


ROOT = Path(__file__).resolve().parents[1]
RADII_M = (10.0, 25.0, 50.0)
BOUNDARY_MARGIN_M = 1.0
OUTPUT = ROOT / "results" / "analysis"
COUNTS = (
    "intersection_total", "intersection_image_positive", "intersection_query_unknown",
    "intersection_eligible", "osm_negative_junctions", "cv_positive_flags_all_dates",
    "cv_positive_flags_recent", "crossing_total", "crossing_image_positive",
    "crossing_query_unknown", "crossing_eligible", "crossings_codetected_all_dates",
    "crossings_codetected_recent", "signal_total", "signal_image_positive",
    "signal_query_unknown", "signal_eligible", "signals_codetected_all_dates",
    "signals_codetected_recent",
)


def boundary_segments(geometry):
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return [(a, b) for polygon in polygons for ring in polygon
            for a, b in zip(ring, ring[1:] + ring[:1])]


def near_boundary(lat, lon, segments, distance_m):
    lat_limit = distance_m / 110000
    lon_limit = distance_m / (110000 * math.cos(math.radians(lat)))
    lat_scale = 111195.08
    lon_scale = lat_scale * math.cos(math.radians(lat))
    for (ax, ay), (bx, by) in segments:
        if (max(ay, by) < lat - lat_limit or min(ay, by) > lat + lat_limit
                or max(ax, bx) < lon - lon_limit or min(ax, bx) > lon + lon_limit):
            continue
        x1, y1 = (ax - lon) * lon_scale, (ay - lat) * lat_scale
        dx, dy = (bx - ax) * lon_scale, (by - ay) * lat_scale
        length_sq = dx * dx + dy * dy
        t = min(1.0, max(0.0, -(x1 * dx + y1 * dy) / length_sq)) if length_sq else 0.0
        if math.hypot(x1 + t * dx, y1 + t * dy) <= distance_m:
            return True
    return False


def screening_counts(osm, detections, features, coverage, reference, cutoff):
    segments = {f["properties"]["name"]: boundary_segments(f["geometry"]) for f in features}
    crosses = Index([(float(p["lat"]), float(p["lon"]))
                     for p in osm if p["kind"] == "crossing"])
    indexes = {}
    for group in ("crossing", "ped_signal"):
        relevant = [d for d in detections if d["group"] == group]
        for period in ("all_dates", "recent"):
            indexes[group, period] = Index([
                (float(d["lat"]), float(d["lon"]), d["id"]) for d in relevant
                if period == "all_dates" or (
                    (date := feature_date(d["last_seen_at"])) is not None
                    and cutoff <= date <= reference)])
    counts = defaultdict(Counter)
    excluded = Counter()
    for p in osm:
        name, kind = p["stratum"], p["kind"]
        if name not in segments or kind not in ("intersection", "crossing", "signal"):
            raise ValueError(f"Unexpected point scope or kind: {name}/{kind}")
        lat, lon = float(p["lat"]), float(p["lon"])
        interior = not near_boundary(lat, lon, segments[name], max(RADII_M) + BOUNDARY_MARGIN_M)
        if not interior:
            excluded[kind] += 1
        status = coverage.at(lat, lon)
        known = coverage.feature_query_known(lat, lon) if status == "image_positive" else False
        settings = [("original_clipped", RADIUS_M)]
        if interior:
            settings.extend(("common_interior", radius) for radius in RADII_M)
        for population, radius in settings:
            c = counts[name, population, radius]
            c[kind + "_total"] += 1
            if status != "image_positive":
                continue
            c[kind + "_image_positive"] += 1
            if not known:
                c[kind + "_query_unknown"] += 1
                continue
            c[kind + "_eligible"] += 1
            if kind == "intersection":
                if crosses.near(lat, lon, radius):
                    continue
                c["osm_negative_junctions"] += 1
                for period in ("all_dates", "recent"):
                    c["cv_positive_flags_" + period] += indexes["crossing", period].near(lat, lon, radius)
            else:
                group = "crossing" if kind == "crossing" else "ped_signal"
                prefix = "crossings_codetected_" if kind == "crossing" else "signals_codetected_"
                for period in ("all_dates", "recent"):
                    c[prefix + period] += indexes[group, period].near(lat, lon, radius)
    return counts, excluded


def make_row(name, population, radius, counts):
    row = {"stratum": name, "population": population, "radius_m": radius,
           **{key: counts[key] for key in COUNTS}}
    for key, numerator, denominator in (
        ("flag_rate_pct", "cv_positive_flags_all_dates", "osm_negative_junctions"),
        ("crossing_codetection_pct", "crossings_codetected_all_dates", "crossing_eligible"),
        ("signal_codetection_pct", "signals_codetected_all_dates", "signal_eligible"),
    ):
        row[key] = round(100 * counts[numerator] / counts[denominator], 4) if counts[denominator] else None
    return row


def verify_baseline(counts, metadata):
    expected = metadata["all_period_counts"]
    fields = {
        "intersections": "intersection_total",
        "junctions_in_image_positive_tiles": "intersection_image_positive",
        "osm_negative_junctions": "osm_negative_junctions",
        "cv_positive_flags": "cv_positive_flags_all_dates",
        "osm_crossings": "crossing_total",
        "crossings_in_image_positive_tiles": "crossing_eligible",
        "crossings_codetected": "crossings_codetected_all_dates",
        "general_signal_locations": "signal_total",
        "general_signal_locations_in_image_positive_tiles": "signal_eligible",
        "general_signal_locations_codetected": "signals_codetected_all_dates",
    }
    for saved, computed in fields.items():
        if counts[computed] != expected[saved]:
            raise ValueError(f"Saved 25 m screening disagrees on {saved}: {counts[computed]} != {expected[saved]}")
    for saved, computed in (
        ("cv_positive_flags", "cv_positive_flags_recent"),
        ("crossings_codetected", "crossings_codetected_recent"),
        ("general_signal_locations_codetected", "signals_codetected_recent"),
    ):
        if counts[computed] != metadata["recent_counts"][saved]:
            raise ValueError(f"Saved recent screening disagrees on {saved}")
    for saved, computed in (
        ("intersections_feature_query_unknown", "intersection_query_unknown"),
        ("crossings_feature_query_unknown", "crossing_query_unknown"),
        ("general_signal_locations_feature_query_unknown", "signal_query_unknown"),
    ):
        if counts[computed] != metadata["query_unknown_counts"][saved]:
            raise ValueError(f"Saved query exclusions disagree on {saved}")


def verify_sources(manifest):
    required = (
        "results/mapillary/detections_v2.csv", "results/mapillary/osm_points.csv",
        "results/urban/scope_laus.json", "data/boundaries/greece_municipalities.geojson",
        "analysis/mapillary_join.py", "analysis/mapillary_fetch.py",
    )
    recorded = {key.replace("\\", "/"): digest for key, digest in manifest["source_hashes"].items()}
    for name in required:
        path = ROOT / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != recorded.get(name):
            raise ValueError(f"Saved screening source hash disagrees: {name}")
    return {name: recorded[name] for name in required}


def build_report():
    metadata = load_json(OUT + "/validation_metadata.json")
    manifest = load_json(OUT + "/co_detection_manifest.json")
    if metadata["radius_m"] != RADIUS_M or metadata["reference_date"] != manifest["reference_date"]:
        raise ValueError("Saved Mapillary screening settings disagree")
    sources = verify_sources(manifest)
    reference = dt.date.fromisoformat(manifest["reference_date"])
    cutoff = dt.date.fromisoformat(manifest["recent_cutoff"])
    features = scope_features()
    scope = {f["properties"]["name"] for f in features}
    osm = [p for p in load(OUT + "/osm_points.csv") if p["stratum"] in scope]
    detections = load(OUT + "/detections_v2.csv")
    counts, excluded = screening_counts(osm, detections, features, TileCoverage(features), reference, cutoff)
    baseline = sum((counts[name, "original_clipped", RADIUS_M] for name in scope), Counter())
    verify_baseline(baseline, metadata)
    settings = [("original_clipped", RADIUS_M)] + [("common_interior", radius) for radius in RADII_M]
    rows = [make_row(name, population, radius, counts[name, population, radius])
            for population, radius in settings for name in sorted(scope)]
    national = [make_row("TOTAL", population, radius,
                         sum((counts[name, population, radius] for name in scope), Counter()))
                for population, radius in settings]
    for row in national[2:]:
        fixed = national[1]
        if any(row[key] != fixed[key] for key in ("intersection_total", "crossing_total", "signal_total",
                                                "intersection_eligible", "crossing_eligible", "signal_eligible")):
            raise ValueError("Common-interior comparison changed its candidate population")
    return {
        "schema_version": 1, "reference_date": reference.isoformat(), "recent_cutoff": cutoff.isoformat(),
        "model": "cached point co-detection; no census or network analysis",
        "radii_m": list(RADII_M), "common_interior_exclusion_m": max(RADII_M) + BOUNDARY_MARGIN_M,
        "boundary_excluded_points": dict(excluded), "baseline_verified": True,
        "source_hashes": {**sources, "analysis/mapillary_radius_sensitivity.py":
                          hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "national": national, "by_city": rows,
        "limitations": [
            "The original clipped 25 m screening is reproduced, not silently overwritten.",
            "Every comparison radius uses the same candidate locations more than 51 m from the reporting polygon boundary.",
            "This common-interior check removes boundary-dependent false-negative neighbour searches; it is not a complete external halo or a full-polygon corrected estimate.",
            "Clipped OSM/CV exports do not establish neighbours outside the original polygons; outside-bounding-box feature caches are not available.",
            "Image-positive tiles are not point visibility; feature-query acquisition of legacy caches remains unverifiable.",
            "Matches are co-detections, not independent ground truth or physical route accessibility.",
        ],
    }


def main():
    report = build_report()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    preserve_write(str(OUTPUT / "mapillary_radius_sensitivity.json"),
                   json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    rows = report["national"] + report["by_city"]
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    preserve_write(str(OUTPUT / "mapillary_radius_sensitivity.csv"), out.getvalue())
    print("Saved 25 m counts verified; wrote boundary-safe 10/25/50 m screening comparison.")


if __name__ == "__main__":
    main()
