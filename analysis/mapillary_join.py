#!/usr/bin/env python3
"""Cached spatial co-detection, not ground-truth validation or lower bounds.

Run mapillary_fetch.py --cached-only first, then this script. Legacy outputs are
left untouched; v2 outputs are archived by content hash before replacement.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
from collections import defaultdict, Counter

from mapillary_fetch import (OUT as MLY, ROOT, SCOPE, BOUNDS, cached, cache_path,
                             scope_features, bbox_of, tiles, short, feature_date,
                             preserve_write)

RADIUS_M = 25.0
CELL = .00025
STEP = .01


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def haversine(a_lat, a_lon, b_lat, b_lon):
    dlat, dlon = math.radians(b_lat-a_lat), math.radians(b_lon-a_lon)
    h = math.sin(dlat/2)**2 + math.cos(math.radians(a_lat))*math.cos(math.radians(b_lat))*math.sin(dlon/2)**2
    return 2*6371000*math.asin(math.sqrt(min(1., h)))


class Index:
    """Exact radius query with a latitude-dependent complete candidate window."""
    def __init__(self, points):
        self.g = defaultdict(list)
        for i, p in enumerate(points):
            lat, lon = p[:2]
            self.g[math.floor(lat/CELL), math.floor(lon/CELL)].append(
                (lat, lon, p[2] if len(p) > 2 else str(i)))

    def matches(self, lat, lon, radius=RADIUS_M):
        cy, cx = math.floor(lat/CELL), math.floor(lon/CELL)
        ry = math.ceil(radius/(111000*CELL))
        rx = math.ceil(radius/(111000*CELL*max(.001, math.cos(math.radians(lat)))))
        return {identity for dy in range(-ry, ry+1) for dx in range(-rx, rx+1)
                for a, b, identity in self.g.get((cy+dy, cx+dx), ())
                if haversine(lat, lon, a, b) <= radius}

    def near(self, lat, lon, radius=RADIUS_M):
        return bool(self.matches(lat, lon, radius))


class TileCoverage:
    """Any image in a tile is only coarse occupancy, never point visibility."""
    def __init__(self, features):
        self.grid = defaultdict(list)
        self.queried_grid = defaultdict(list)
        self.status_counts = Counter()
        self.hashes = {}
        seen = set()
        for f in features:
            for box in tiles(*bbox_of(f["geometry"])):
                if box in seen:
                    continue
                seen.add(box)
                p = cache_path("images", box)
                response = cached(p)
                if os.path.isfile(p):
                    with open(p, "rb") as fh:
                        self.hashes[os.path.relpath(p, MLY)] = hashlib.sha256(fh.read()).hexdigest()
                status = ("image_positive" if response["status"] in ("legacy", "ok") and response["data"]
                          else "empty_response" if response["status"] == "ok" else "unknown")
                self.status_counts[status] += 1
                w, s, e, n = box
                feature_ok = cached(cache_path("features", box))["status"] in ("legacy", "ok")
                # Use the real clipped east/north bounds, not an invented full-size tile.
                for cx in range(math.floor(w/STEP), math.floor(e/STEP)+1):
                    for cy in range(math.floor(s/STEP), math.floor(n/STEP)+1):
                        self.grid[cx, cy].append((box, status))
                        if feature_ok:
                            self.queried_grid[cx, cy].append(box)

    def at(self, lat, lon):
        statuses = {status for (w,s,e,n),status in self.grid.get(
            (math.floor(lon/STEP), math.floor(lat/STEP)), ())
            if w <= lon <= e and s <= lat <= n}
        if "image_positive" in statuses:
            return "image_positive"
        return "empty_response" if statuses == {"empty_response"} else "unknown"

    def feature_query_known(self, lat, lon):
        return any(w <= lon <= e and s <= lat <= n for w,s,e,n in self.queried_grid.get(
            (math.floor(lon/STEP), math.floor(lat/STEP)), ()))


def write_csv(name, rows, fields=None):
    fh = io.StringIO(newline="")
    writer = csv.DictWriter(fh, fieldnames=fields or list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    preserve_write(os.path.join(MLY, name), fh.getvalue())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-date", default=dt.date.today().isoformat())
    parser.add_argument("--max-age-years", type=int, default=5)
    args = parser.parse_args()
    reference = dt.date.fromisoformat(args.reference_date)
    if args.max_age_years < 0:
        parser.error("max-age-years must be nonnegative")
    try:
        cutoff = reference.replace(year=reference.year-args.max_age_years)
    except ValueError:
        cutoff = reference.replace(year=reference.year-args.max_age_years, day=28)
    path = os.path.join(MLY, "detections_v2.csv")
    if not os.path.isfile(path):
        parser.error("Run python -B analysis\\mapillary_fetch.py --cached-only first")
    detections = load(path)
    osm_path = os.path.join(MLY, "osm_points.csv")
    osm = load(osm_path)
    features = scope_features()
    scope = {f["properties"]["name"] for f in features}
    osm = [p for p in osm if p["stratum"] in scope]
    coverage = TileCoverage(features)
    indexes = {}
    age_counts = Counter()
    for d in detections:
        date = feature_date(d["last_seen_at"])
        age_counts["undated" if date is None else "future" if date > reference else
                   "stale" if date < cutoff else "recent"] += 1
    for group in ("crossing", "ped_signal"):
        for period in ("all_dates", "recent"):
            indexes[group, period] = Index([
                (float(d["lat"]), float(d["lon"]), d["id"]) for d in detections
                if d["group"] == group and (period == "all_dates" or
                    (feature_date(d["last_seen_at"]) is not None and
                     cutoff <= feature_date(d["last_seen_at"]) <= reference))])
    osm_cross = Index([(float(p["lat"]), float(p["lon"])) for p in osm if p["kind"] == "crossing"])
    counts = {s: Counter() for s in scope}
    flags, matched_ids = [], set()
    for p in osm:
        lat, lon = float(p["lat"]), float(p["lon"])
        s, kind = p["stratum"], p["kind"]
        c = counts[s]
        prefix = {"intersection": "intersections", "crossing": "osm_crossings",
                  "signal": "general_signal_locations"}[kind]
        c[prefix] += 1
        status = coverage.at(lat, lon)
        if status != "image_positive":
            if kind == "intersection":
                c["intersections_tile_unknown" if status == "unknown" else "intersections_tile_empty"] += 1
            continue
        tile_key = {"intersection": "intersections_image_positive_tile",
                    "crossing": "crossings_image_positive_tile",
                    "signal": "general_signal_locations_image_positive_tile"}[kind]
        c[tile_key] += 1
        if not coverage.feature_query_known(lat, lon):
            c[{"intersection": "intersections_feature_query_unknown",
               "crossing": "crossings_feature_query_unknown",
               "signal": "general_signal_locations_feature_query_unknown"}[kind]] += 1
            continue
        if kind == "intersection":
            if osm_cross.near(lat, lon):
                continue
            c["osm_negative_junctions_image_positive_tile"] += 1
            for period in ("all_dates", "recent"):
                ids = indexes["crossing", period].matches(lat, lon)
                c["cv_positive_flags_"+period] += bool(ids)
                if period == "all_dates" and ids:
                    matched_ids.update(ids)
                    flags.append({"stratum": s, "lat": lat, "lon": lon,
                                  "cv_feature_ids": ";".join(sorted(ids))})
        else:
            group = "crossing" if kind == "crossing" else "ped_signal"
            label = "crossings_codetected_" if kind == "crossing" else "ped_head_codetected_"
            for period in ("all_dates", "recent"):
                c[label+period] += indexes[group, period].near(lat, lon)
    fields = [
        "intersections", "intersections_image_positive_tile", "intersections_tile_unknown",
        "intersections_tile_empty", "osm_negative_junctions_image_positive_tile",
        "intersections_feature_query_unknown", "crossings_feature_query_unknown",
        "general_signal_locations_feature_query_unknown",
        "cv_positive_flags_all_dates", "cv_positive_flags_recent", "osm_crossings",
        "crossings_image_positive_tile", "crossings_codetected_all_dates", "crossings_codetected_recent",
        "general_signal_locations", "general_signal_locations_image_positive_tile",
        "ped_head_codetected_all_dates", "ped_head_codetected_recent"]
    rows = []
    for s, c in sorted(counts.items()):
        r = {"city": short(s), "stratum": s, **{k: c[k] for k in fields}}
        for label, num, den in [
                ("flag_rate", "cv_positive_flags", "osm_negative_junctions_image_positive_tile"),
                ("crossing_codetection", "crossings_codetected", "crossings_image_positive_tile"),
                ("ped_head_codetection", "ped_head_codetected", "general_signal_locations_image_positive_tile")]:
            for period in ("all_dates", "recent"):
                excluded = (c["crossings_feature_query_unknown"] if label == "crossing_codetection"
                            else c["general_signal_locations_feature_query_unknown"] if label == "ped_head_codetection" else 0)
                denominator = c[den]-excluded
                r[label+"_"+period+"_pct"] = round(100*c[num+"_"+period]/denominator, 4) if denominator else ""
        rows.append(r)
    inventory = defaultdict(Counter)
    inventory_path = os.path.join(MLY, "inventory_by_scope.csv")
    if not os.path.isfile(inventory_path):
        parser.error("Missing polygon inventory; run mapillary_fetch.py --cached-only first")
    for item in load(inventory_path):
        inventory[item["stratum"]][item["group"]] += int(item["count_all_dates"])
    grid_path = os.path.join(MLY, "image_coverage_v2.csv")
    if not os.path.isfile(grid_path):
        parser.error("Missing tile-coverage table; run mapillary_fetch.py --cached-only first")
    grid_rows = load(grid_path)
    grid = {r["city"]: r for r in grid_rows}
    if len(grid) != len(grid_rows) or any(r["city"] not in grid for r in rows):
        parser.error("Ambiguous/missing city tile-coverage join")
    write_csv("co_detection_by_city_periods.csv", rows)
    properties = {f["properties"]["name"]: f["properties"] for f in features}
    canonical = []
    for r in rows:
        s = r["stratum"]
        canonical.append({
            "city": r["city"], "stratum": s, "pop_2021": properties[s].get("pop_2021", ""),
            "image_positive_tile_pct":
                round(100*int(grid[r["city"]]["tiles_image_positive"])/int(grid[r["city"]]["tiles_total"]), 4)
                if int(grid[r["city"]]["tiles_total"]) else "",
            "intersections": r["intersections"],
            "junctions_in_image_positive_tiles": r["intersections_image_positive_tile"],
            "junctions_without_image_positive_tile": r["intersections_tile_unknown"]+r["intersections_tile_empty"],
            "osm_negative_junctions": r["osm_negative_junctions_image_positive_tile"],
            "cv_positive_flags": r["cv_positive_flags_all_dates"],
            "flag_rate_pct": r["flag_rate_all_dates_pct"],
            "osm_crossings": r["osm_crossings"],
            "crossings_in_image_positive_tiles": r["crossings_image_positive_tile"]-r["crossings_feature_query_unknown"],
            "crossings_codetected": r["crossings_codetected_all_dates"],
            "crossing_codetection_pct": r["crossing_codetection_all_dates_pct"],
            "general_signal_locations": r["general_signal_locations"],
            "general_signal_locations_in_image_positive_tiles":
                r["general_signal_locations_image_positive_tile"]-r["general_signal_locations_feature_query_unknown"],
            "general_signal_locations_codetected": r["ped_head_codetected_all_dates"],
            "general_signal_locations_codetection_pct": r["ped_head_codetection_all_dates_pct"],
            "det_street_light": inventory[s]["street_light"],
            "det_furniture": inventory[s]["street_furniture"],
            "det_surface_objects": inventory[s]["surface_objects"],
        })
    write_csv("co_detection_by_city.csv", canonical)
    write_csv("co_detection_flags.csv", flags, ["stratum", "lat", "lon", "cv_feature_ids"])
    sample_path = os.path.join(ROOT, "results", "census", "audit_sample.csv")
    if os.path.isfile(sample_path):
        with open(sample_path, "rb") as fh:
            sample_hash = hashlib.sha256(fh.read()).hexdigest()
        links = []
        for p in load(sample_path):
            lat, lon = float(p["lat"]), float(p["lon"])
            links.append({"case_id": p["case_id"], "sample_sha256": sample_hash, "arm": p["arm"],
                          "image_tile_status": coverage.at(lat, lon),
                          **{"cv_crossing_ids_"+period: ";".join(sorted(
                              indexes["crossing", period].matches(lat, lon)))
                             for period in ("all_dates", "recent")}})
        write_csv("local_audit_cv_links.csv", links)
    manifest = {
        "schema_version": 2, "reference_date": reference.isoformat(), "recent_cutoff": cutoff.isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "radius_m": RADIUS_M,
        "tile_status_counts": dict(coverage.status_counts), "image_cache_hashes": coverage.hashes,
        "detection_age_counts": dict(age_counts), "unique_cv_features_associated_with_flags": len(matched_ids),
        "source_hashes": {},
        "interpretation": [
            "Flags count OSM-negative junction locations, not distinct missing physical crossings.",
            "General OSM traffic-signal locations are associated with CV pedestrian heads: different object universes.",
            "Image-positive tile occupancy is not point visibility; empty legacy probes/errors are UNKNOWN.",
            "Locations with unknown/failed feature queries are separately counted and excluded from co-detection denominators.",
            "Recent means last_seen_at within the specified window; not a current-condition ground-truth claim.",
            "All-date rates include stale/undated detections. Neither period supplies accuracy or a lower bound.",
            "Proximity uses polygon-scoped exported OSM/CV points; crossings outside that export are not tested.",
            "Furniture lives in inventory_by_scope.csv, without hazard labels, population or mixed-frame km densities.",
            "Incidental audit links are for post-annotation calibration; they are not a separately sampled CV validation arm."],
    }
    acquisition_path = os.path.join(MLY, "acquisition_manifest_v2.json")
    sources = [path, osm_path, inventory_path, grid_path, SCOPE, BOUNDS, __file__,
               os.path.join(ROOT, "analysis", "mapillary_fetch.py")]
    if os.path.isfile(acquisition_path):
        sources.append(acquisition_path)
    for p in sources:
        with open(p, "rb") as fh:
            manifest["source_hashes"][os.path.relpath(p, ROOT)] = hashlib.sha256(fh.read()).hexdigest()
    preserve_write(os.path.join(MLY, "co_detection_manifest.json"),
                   json.dumps(manifest, ensure_ascii=False, indent=2))
    acquisition = {}
    if os.path.isfile(acquisition_path):
        with open(acquisition_path, encoding="utf-8") as fh:
            acquisition = json.load(fh)
    metadata = {
        "schema_version": 2, "reference_date": reference.isoformat(),
        "recent_cutoff": cutoff.isoformat(), "radius_m": RADIUS_M,
        "headline_period": "all available observation dates, including stale and undated detections",
        "population": "Principal-LAU polygon GISCO POP_2021 field; historical reference year unverified; no population normalization",
        "scope": "Selected principal-community polygons, not whole municipalities",
        "all_period_counts": {k: sum(r[k] for r in canonical) for k in (
            "intersections", "junctions_in_image_positive_tiles", "junctions_without_image_positive_tile",
            "osm_negative_junctions", "cv_positive_flags", "osm_crossings", "crossings_in_image_positive_tiles",
            "crossings_codetected", "general_signal_locations", "general_signal_locations_in_image_positive_tiles",
            "general_signal_locations_codetected", "det_street_light", "det_furniture", "det_surface_objects")},
        "recent_counts": {"cv_positive_flags": sum(r["cv_positive_flags_recent"] for r in rows),
                          "crossings_codetected": sum(r["crossings_codetected_recent"] for r in rows),
                          "general_signal_locations_codetected": sum(r["ped_head_codetected_recent"] for r in rows)},
        "query_unknown_counts": {k: sum(r[k] for r in rows) for k in (
            "intersections_feature_query_unknown", "crossings_feature_query_unknown",
            "general_signal_locations_feature_query_unknown")},
        "denominators": {
            "image_positive_tile_pct": "100 * image-positive cached tile probes / all queried 0.01-degree principal-LAU bounding-box grid tiles, clipped at bbox edges; all observation dates; not area coverage or junction coverage",
            "coverage": "junctions_in_image_positive_tiles / intersections: coarse occupancy, not visibility",
            "flags": "osm_negative_junctions: image-positive tile, successful feature query, no exported OSM crossing within 25 m",
            "crossing": "crossings_in_image_positive_tiles: image-positive AND successful feature query",
            "signals": "general_signal_locations_in_image_positive_tiles: image-positive AND successful feature query; all OSM traffic-signal nodes, not just pedestrian heads",
            "recent": "Same eligible denominators; numerator restricted by last_seen_at within the reported window",
            "aggregate": "Sum counts before division; do not average city percentages",
        },
        "detection_age_counts": dict(age_counts), "interpretation": manifest["interpretation"],
        "source_hashes": manifest["source_hashes"],
        "cache_provenance": {
            "acquisition_mode": acquisition.get("mode"),
            "feature_cache_states": acquisition.get("cache_status"),
            "image_tile_states": dict(coverage.status_counts),
            "acquisition_errors": acquisition.get("acquisition_errors"),
            "date_fields": acquisition.get("source_fields"),
            "legacy_limit": "Bare legacy arrays have no acquisition timestamp; legacy empty image probes are UNKNOWN",
            "feature_cache_hash_manifest": "acquisition_manifest_v2.json",
            "image_cache_hash_manifest": "co_detection_manifest.json",
        },
        "grid_coverage_counts": grid_rows,
        "detailed_period_file": "co_detection_by_city_periods.csv",
        "provenance_manifest": "co_detection_manifest.json",
    }
    preserve_write(os.path.join(MLY, "validation_metadata.json"),
                   json.dumps(metadata, ensure_ascii=False, indent=2))
    print("Wrote canonical/detailed co-detection tables, flags, audit links and provenance/validation metadata.")
    print("Legacy outputs unchanged. Co-detection/flags are not accuracy estimates or lower bounds.")


if __name__ == "__main__":
    main()
