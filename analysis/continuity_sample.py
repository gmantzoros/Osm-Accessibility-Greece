#!/usr/bin/env python3
"""Draw a whole-way continuity sample for the survey-priority worklist.

The intervention model assumes a selected way becomes passable along its entire
length. The selected ways have a median length of about five kilometres, while
every observation in the local audit judges a single marker. Nothing in the
study tests the step between the two, so this draws ways from the committed
worklist and places three observation points along each.

Geometry comes from the OpenStreetMap API rather than the pinned extract,
because the extract reader is a separate executable and re-running it would
invalidate the country provenance. The fetch date and the way's current tag
state are recorded with each case: a way tagged since the snapshot is still a
valid continuity observation, but it is no longer an untagged candidate.

    python analysis/continuity_sample.py [n_ways]      (default 60)
"""
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import random
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = os.path.join(ROOT, "results", "census", "edit_targets.csv")
OUT = os.path.join(ROOT, "results", "census", "continuity_sample.csv")
SEED = 20260915
FRACTIONS = (0.15, 0.50, 0.85)
# Long ways are where the assumption is strained, so they are deliberately
# over-represented; the inclusion probability is recorded so the analysis can
# weight back to the worklist.
BANDS = [("short", 0.0, 2.0, 15), ("medium", 2.0, 10.0, 30), ("long", 10.0, 1e9, 15)]
UA = {"User-Agent": "osm-accessibility-greece/1.0 (research; whole-way continuity audit)"}


def haversine_km(a, b):
    dy = (b[0] - a[0]) * 111.32
    dx = (b[1] - a[1]) * 111.32 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def fetch(way_id):
    url = f"https://api.openstreetmap.org/api/0.6/way/{way_id}/full.json"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        doc = json.load(r)
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in doc["elements"] if e["type"] == "node"}
    way = next(e for e in doc["elements"] if e["type"] == "way")
    pts = [nodes[n] for n in way["nodes"] if n in nodes]
    return pts, way.get("tags", {})


def points_along(pts, fractions):
    """Interpolate at the given fractions of cumulative length."""
    seg = [haversine_km(a, b) for a, b in zip(pts, pts[1:])]
    total = sum(seg)
    if total <= 0:
        return [], 0.0
    out = []
    for f in fractions:
        want, run = f * total, 0.0
        for i, d in enumerate(seg):
            if run + d >= want or i == len(seg) - 1:
                t = 0.0 if d == 0 else (want - run) / d
                a, b = pts[i], pts[i + 1]
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                break
            run += d
    return out, total


def main():
    n_target = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    rows = list(csv.DictReader(io.open(TARGETS, encoding="utf-8")))
    scale = n_target / sum(b[3] for b in BANDS)
    rng = random.Random(SEED)
    picked = []
    for name, lo, hi, want in BANDS:
        pool = sorted((r for r in rows if lo <= float(r["own_km"]) < hi),
                      key=lambda r: int(r["osm_way_id"]))
        take = min(len(pool), max(1, round(want * scale)))
        for r in rng.sample(pool, take):
            picked.append((name, len(pool), take, r))
    picked.sort(key=lambda p: int(p[3]["rank"]))

    fetched_on = dt.date.today().isoformat()
    cases, failures = [], 0
    for idx, (band, pool_n, draw_n, r) in enumerate(picked, 1):
        wid = r["osm_way_id"]
        try:
            pts, tags = fetch(wid)
            coords, total_km = points_along(pts, FRACTIONS)
        except Exception as exc:                     # noqa: BLE001 - reported, not raised
            print(f"  way {wid}: fetch failed ({type(exc).__name__}); excluded")
            failures += 1
            continue
        if len(coords) != len(FRACTIONS):
            print(f"  way {wid}: degenerate geometry; excluded")
            failures += 1
            continue
        retagged = next((tags[k] for k in ("sidewalk", "sidewalk:both", "sidewalk:left",
                                           "sidewalk:right") if k in tags), "")
        for pos, (frac, (lat, lon)) in enumerate(zip(FRACTIONS, coords), 1):
            cases.append({
                "case_id": f"W{idx:03d}P{pos}", "way_group": f"W{idx:03d}",
                "osm_way_id": wid, "point_index": pos, "point_fraction": f"{frac:.2f}",
                "band": band, "pool_n": pool_n, "draw_n": draw_n,
                "inclusion_probability": f"{draw_n / pool_n:.12f}",
                "worklist_rank": r["rank"], "highway": r["highway"],
                "stratum": r["stratum"], "own_km": f"{total_km:.3f}",
                "lat": f"{lat:.7f}", "lon": f"{lon:.7f}",
                "geometry_source": "osm-api-0.6", "geometry_fetched_on": fetched_on,
                "current_sidewalk_tag": retagged,
                "osm_link": f"https://www.openstreetmap.org/way/{wid}",
                "streetview_link":
                    f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat:.7f},{lon:.7f}",
            })
        time.sleep(0.4)                              # courtesy pacing for a public API

    if not cases:
        sys.exit("No continuity cases drawn")
    with io.open(OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cases[0]))
        w.writeheader()
        w.writerows(cases)
    digest = hashlib.sha256(io.open(OUT, "rb").read()).hexdigest()
    ways = len({c["way_group"] for c in cases})
    print(f"\nWrote {OUT}")
    print(f"  {ways} ways x {len(FRACTIONS)} points = {len(cases)} observations "
          f"({failures} ways excluded)")
    print(f"  bands: " + ", ".join(
        f"{b}={len({c['way_group'] for c in cases if c['band'] == b})}" for b, *_ in BANDS))
    print(f"  retagged since snapshot: "
          f"{len({c['way_group'] for c in cases if c['current_sidewalk_tag']})} ways")
    print(f"  sha256 {digest}")


if __name__ == "__main__":
    main()
