#!/usr/bin/env python3
"""Acquire/cache Mapillary map features and export polygon-scoped inventories.

These are CV map features, not independent ground-truth observations. A one-image
tile probe does not establish street visibility. No acoustic operation, sidewalk
usability or physical hazard is inferred from these inventories.

Design notes:
  * Tiles each city's bounding box at 0.01 degrees (the API's bbox limit) and
    caches every tile response on disk, so a re-run costs nothing and an
    interrupted run resumes where it stopped. ~4,500 tiles covers all 50 cities.
  * Standard library only, like the rest of the pipeline. No pip step.
  * Polite by construction: bounded concurrency, exponential backoff, and it
    stops on repeated hard failures rather than hammering.
  * The token is read from data/secrets/ and never printed or committed.

Usage:
    python analysis/mapillary_fetch.py            # all 50 cities
    python analysis/mapillary_fetch.py Athens     # one city, substring match
    python analysis/mapillary_fetch.py --images   # also probe image coverage
    python -B analysis/mapillary_fetch.py --cached-only  # no network or secret access
"""

import io
import json
import math
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import datetime as dt
import hashlib
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(ROOT, "data", "secrets", "mapillary_token.txt")
SCOPE = os.path.join(ROOT, "results", "urban", "scope_laus.json")
BOUNDS = os.path.join(ROOT, "data", "boundaries", "greece_municipalities.geojson")
OUT = os.path.join(ROOT, "results", "mapillary")
CACHE = os.path.join(OUT, "cache")

API = "https://graph.mapillary.com/map_features"
IMG_API = "https://graph.mapillary.com/images"
STEP = 0.01          # API bbox limit
THREADS = 16         # polite; the API allows 10,000 search requests per minute
RETRIES = 4
TIMEOUT = 60

# Descriptive classes only: presence of furniture/lighting does not establish
# obstruction, safety, acoustic operation or pedestrian usability.
CLASSES = {
    "crossing": ["marking--discrete--crosswalk-zebra", "construction--flat--crosswalk-plain"],
    "ped_signal": ["object--traffic-light--pedestrians", "object--traffic-light--pedestrians-front",
                   "object--traffic-light--pedestrians-side", "object--traffic-light--pedestrians-back"],
    "street_light": ["object--street-light"],
    "street_furniture": ["object--support--pole", "object--support--utility-pole", "object--trash-can",
                 "object--bench", "object--bike-rack", "object--fire-hydrant",
                 "object--phone-booth", "object--parking-meter"],
    "surface_objects": ["object--manhole", "object--catch-basin"],
}
GROUP_OF = {v: k for k, vals in CLASSES.items() for v in vals}
ALL_VALUES = sorted(GROUP_OF)


def token():
    if not os.path.isfile(TOKEN_FILE):
        sys.exit(f"Missing {TOKEN_FILE}\nPut your Mapillary client token there (it is gitignored).")
    return io.open(TOKEN_FILE, encoding="utf-8").read().strip()


def load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def cached(path):
    """Old bare arrays remain readable, but have unknown acquisition provenance."""
    if not os.path.isfile(path):
        return {"status": "missing", "data": []}
    try:
        value = load(path)
    except (ValueError, OSError):
        return {"status": "error", "data": [], "error_type": "unreadable_cache"}
    if isinstance(value, list):
        return {"status": "legacy", "data": value, "acquired_at": None}
    if isinstance(value, dict) and value.get("status") in ("ok", "error"):
        return value
    return {"status": "error", "data": [], "error_type": "unknown_cache_schema"}


def preserve_write(path, text):
    """Keep content-addressed copies of replaced derived artifacts."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            old = fh.read()
        if old == text.encode("utf-8"):
            return
        archive = os.path.join(os.path.dirname(path), "archive",
                               os.path.basename(path) + "." + hashlib.sha256(old).hexdigest())
        os.makedirs(os.path.dirname(archive), exist_ok=True)
        if not os.path.isfile(archive):
            shutil.copy2(path, archive)
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def cache_response(path, box, endpoint, data=None, error=None):
    value = {"schema_version": 2, "status": "error" if error else "ok",
             "endpoint": endpoint, "bbox": list(box),
             "acquired_at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "data": data or []}
    if error:
        value["error_type"] = type(error).__name__
    preserve_write(path, json.dumps(value, ensure_ascii=False))


def inside_ring(lon, lat, ring):
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if (a[1] > lat) != (b[1] > lat):
            x = (b[0]-a[0]) * (lat-a[1]) / (b[1]-a[1]) + a[0]
            if lon < x:
                inside = not inside
    return inside


def inside_geometry(lon, lat, geometry):
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return any(inside_ring(lon, lat, p[0]) and not any(inside_ring(lon, lat, h) for h in p[1:])
               for p in polygons)


def scope_features():
    scope = set(load(SCOPE)["laus"])
    return [f for f in load(BOUNDS)["features"] if f["properties"]["name"] in scope]


def short(name):
    return (name.replace("DIMOS:", "").replace("Ψευδοκοινοτητα ", "")
            .replace("Ψευδοκοινότητα ", "").replace("Κοινότητα ", ""))


def feature_date(value):
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return dt.datetime.fromtimestamp(float(value)/1000, dt.timezone.utc).date()
        return dt.date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError, OverflowError):
        return None


def fetch_pages(url):
    result, seen = [], set()
    while url:
        parsed = urllib.parse.urlparse(url)
        if url in seen or parsed.scheme != "https" or parsed.hostname != "graph.mapillary.com":
            raise RuntimeError("Invalid/repeated pagination link")
        seen.add(url)
        page = fetch(url)
        result.extend(page_data(page))
        url = (page.get("paging") or {}).get("next")
    return result


def page_data(page):
    if not isinstance(page, dict) or not isinstance(page.get("data"), list):
        raise RuntimeError("Invalid API response: data array required")
    return page["data"]


def bbox_of(geom):
    xs, ys = [], []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
            return
        for p in c:
            walk(p)

    walk(geom["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


def tiles(w, s, e, n):
    nx = max(1, math.ceil((e - w) / STEP))
    ny = max(1, math.ceil((n - s) / STEP))
    for i in range(nx):
        for j in range(ny):
            yield (round(w + i * STEP, 4), round(s + j * STEP, 4),
                   round(min(w + (i + 1) * STEP, e), 4), round(min(s + (j + 1) * STEP, n), 4))


def cache_path(kind, box):
    return os.path.join(CACHE, kind, f"{box[0]}_{box[1]}.json")


def fetch(url):
    """One request with backoff. Returns parsed JSON or raises."""
    last = None
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "osm-accessibility-greece/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            last = ex
            if ex.code in (400, 401, 403):          # not transient - surface it
                raise RuntimeError(f"HTTP {ex.code}")
            time.sleep(2 ** attempt)
        except Exception as ex:                      # timeouts, resets
            last = ex
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed after {RETRIES} attempts: {last}")


def sweep(city_boxes, tok, want_images):
    """Fetch every tile for every city, cached. Returns counts."""
    jobs = queue.Queue()
    for city, boxes in city_boxes:
        for b in boxes:
            jobs.put((city, b))
    total = jobs.qsize()
    state = {"done": 0, "hit": 0, "new": 0, "features": 0, "err": 0}
    lock = threading.Lock()
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                city, box = jobs.get_nowait()
            except queue.Empty:
                return
            try:
                p = cache_path("features", box)
                if cached(p)["status"] in ("ok", "legacy"):
                    with lock:
                        state["hit"] += 1
                else:
                    url = (f"{API}?access_token={urllib.parse.quote(tok)}"
                           f"&fields=id,object_value,geometry,first_seen_at,last_seen_at"
                           f"&bbox={box[0]},{box[1]},{box[2]},{box[3]}"
                           f"&object_values={','.join(ALL_VALUES)}&limit=2000")
                    try:
                        data = fetch_pages(url)
                        cache_response(p, box, API, data=data)
                    except Exception as ex:
                        cache_response(p, box, API, error=ex)
                        raise
                    with lock:
                        state["new"] += 1
                if want_images:
                    ip = cache_path("images", box)
                    if cached(ip)["status"] not in ("ok", "legacy"):
                        # One cheap probe: does ANY imagery exist in this tile?
                        url = (f"{IMG_API}?access_token={urllib.parse.quote(tok)}"
                               f"&fields=id,captured_at&bbox={box[0]},{box[1]},{box[2]},{box[3]}&limit=1")
                        try:
                            d = page_data(fetch(url))
                            cache_response(ip, box, IMG_API, data=d)
                        except Exception as ex:
                            cache_response(ip, box, IMG_API, error=ex)
                            with lock:
                                state["err"] += 1
                                if state["err"] > 40:
                                    stop.set()
                            sys.stderr.write("\nImage probe failed; recorded UNKNOWN, not no imagery.\n")
            except Exception as ex:
                with lock:
                    state["err"] += 1
                    if state["err"] <= 3:
                        sys.stderr.write(f"\n  {city} {box}: {type(ex).__name__}; see explicit error cache\n")
                    if state["err"] > 40:
                        stop.set()
            finally:
                with lock:
                    state["done"] += 1
                    if state["done"] % 50 == 0 or state["done"] == total:
                        pct = 100 * state["done"] / total
                        sys.stderr.write(f"\r  {state['done']:,}/{total:,} tiles ({pct:5.1f}%) "
                                         f"cached={state['hit']:,} fetched={state['new']:,} "
                                         f"errors={state['err']}   ")
                        sys.stderr.flush()
                jobs.task_done()

    ts = [threading.Thread(target=worker, daemon=True) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    sys.stderr.write("\n")
    return state


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    want_images = "--images" in sys.argv
    only = args[0].lower() if args else None

    cached_only = "--cached-only" in sys.argv
    tok = None if cached_only else token()
    scope = set(load(SCOPE)["laus"])
    gj = load(BOUNDS)
    os.makedirs(CACHE, exist_ok=True)

    city_boxes = []
    all_city_boxes = []
    for f in gj["features"]:
        name = f["properties"]["name"]
        if name not in scope:
            continue
        city_name = (name.replace("DIMOS:", "").replace("Ψευδοκοινοτητα ", "")
                     .replace("Ψευδοκοινότητα ", "").replace("Κοινότητα ", ""))
        entry = (city_name, list(tiles(*bbox_of(f["geometry"]))))
        all_city_boxes.append(entry)
        if only and only not in city_name.lower() and only not in name.lower():
            continue
        city_boxes.append(entry)

    if not city_boxes:
        sys.exit("No cities matched.")
    n_tiles = sum(len(b) for _, b in city_boxes)
    print(f"Mapillary sweep: {len(city_boxes)} cities, {n_tiles:,} tiles "
          f"({len(ALL_VALUES)} object classes){' + image probe' if want_images else ''}")

    t0 = time.time()
    state = ({"new": 0, "hit": sum(os.path.isfile(cache_path("features", b))
                                 for _, boxes in city_boxes for b in boxes), "err": 0} if cached_only
             else sweep(city_boxes, tok, want_images))
    print(f"  {state['new']:,} fetched, {state['hit']:,} from cache, "
          f"{state['err']} errors in {time.time()-t0:.0f}s")

    # ---- flatten cache into one CSV
    # A city-specific acquisition must not replace the national-scope export with
    # only that city's detections. Re-export every available in-scope cache.
    city_boxes = all_city_boxes
    import csv
    JOIN_GROUPS = {"crossing", "ped_signal"}   # need point-level joins
    rows = []
    agg = {}
    agg_recent = {}
    agg_undated = {}
    reference = dt.date.today()
    try:
        cutoff = reference.replace(year=reference.year-5)
    except ValueError:
        cutoff = reference.replace(year=reference.year-5, day=28)
    polygons = []
    for f in sorted(gj["features"], key=lambda f: f["properties"]["name"]):
        if f["properties"]["name"] in scope:
            polygons.append((f["properties"]["name"], bbox_of(f["geometry"]), f["geometry"]))
    skipped_outside = 0
    cache_status = {}
    input_hashes = {}
    seen = set()
    for city, boxes in city_boxes:
        for b in boxes:
            p = cache_path("features", b)
            if not os.path.isfile(p):
                cache_status["missing"] = cache_status.get("missing", 0) + 1
                continue
            response = cached(p)
            cache_status[response["status"]] = cache_status.get(response["status"], 0) + 1
            with open(p, "rb") as fh:
                input_hashes[os.path.relpath(p, OUT)] = hashlib.sha256(fh.read()).hexdigest()
            for d in response["data"]:
                fid = d.get("id")
                if fid in seen:
                    continue
                seen.add(fid)
                g = d.get("geometry") or {}
                c = g.get("coordinates")
                if not c or g.get("type") != "Point":
                    continue
                assigned = next((name for name, (w, s, e, n), geom in polygons
                                 if w <= c[0] <= e and s <= c[1] <= n
                                 and inside_geometry(c[0], c[1], geom)), None)
                if assigned is None:
                    skipped_outside += 1
                    continue
                assigned_city = short(assigned)
                ov = d.get("object_value", "")
                grp = GROUP_OF.get(ov, "other")
                key = (assigned_city, assigned, grp, ov)
                agg[key] = agg.get(key, 0) + 1
                date = feature_date(d.get("last_seen_at"))
                if date is None:
                    agg_undated[key] = agg_undated.get(key, 0) + 1
                elif cutoff <= date <= reference:
                    agg_recent[key] = agg_recent.get(key, 0) + 1
                if grp in JOIN_GROUPS:
                    rows.append({"id": fid, "city": assigned_city, "stratum": assigned, "group": grp,
                                 "object_value": ov, "lon": c[0], "lat": c[1],
                                 "first_seen_at": d.get("first_seen_at", ""),
                                 "last_seen_at": d.get("last_seen_at", ""),
                                 "acquired_at": response.get("acquired_at", ""),
                                 "cache_provenance": response["status"]})
    out_csv = os.path.join(OUT, "detections_v2.csv")
    fh = io.StringIO(newline="")
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else
                      ["id", "city", "stratum", "group", "object_value", "lon", "lat",
                       "first_seen_at", "last_seen_at", "acquired_at", "cache_provenance"])
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: (r["stratum"], str(r["id"]))))
    preserve_write(out_csv, fh.getvalue())

    # Furniture/surface objects are aggregated polygon inventories, without
    # hazard labels or unrelated municipality-length denominators.
    counts_csv = os.path.join(OUT, "inventory_by_scope.csv")
    fh = io.StringIO(newline="")
    w = csv.writer(fh)
    w.writerow(["city", "stratum", "group", "object_value", "count_all_dates",
                "count_recent", "last_seen_missing"])
    for key, c in sorted(agg.items()):
        w.writerow([*key, c, agg_recent.get(key, 0), agg_undated.get(key, 0)])
    preserve_write(counts_csv, fh.getvalue())

    from collections import Counter
    byg = Counter()
    for (city, stratum, grp, ov), c in agg.items():
        byg[grp] += c
    print(f"\nWrote {out_csv}  ({len(rows):,} point features for spatial joins)")
    print(f"Wrote {counts_csv}  ({len(agg):,} city x class rows, "
          f"{sum(byg.values()):,} detections total)")
    for g, c in byg.most_common():
        print(f"  {g:16} {c:>9,}")

    if want_images or cached_only:
        cov = {}
        for city, boxes in city_boxes:
            responses = [cached(cache_path("images", b)) for b in boxes]
            have = sum(bool(x["data"]) and x["status"] in ("ok", "legacy") for x in responses)
            empty = sum(x["status"] == "ok" and not x["data"] for x in responses)
            cov[city] = (have, empty, len(boxes)-have-empty, len(boxes))
        cov_csv = os.path.join(OUT, "image_coverage_v2.csv")
        fh = io.StringIO(newline="")
        w = csv.writer(fh)
        w.writerow(["city", "tiles_image_positive", "tiles_empty_response",
                    "tiles_unknown", "tiles_total"])
        for c, values in sorted(cov.items()):
            w.writerow([c, *values])
        preserve_write(cov_csv, fh.getvalue())
        print(f"Wrote {cov_csv}")
    manifest = {
        "schema_version": 2, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": "cached-only" if cached_only else "api-and-cache", "endpoint": API,
        "reference_date": reference.isoformat(), "recent_cutoff": cutoff.isoformat(),
        "source_fields": "first_seen_at,last_seen_at; acquisition date is not capture date",
        "scope_sha256": hashlib.sha256(open(SCOPE, "rb").read()).hexdigest(),
        "boundary_sha256": hashlib.sha256(open(BOUNDS, "rb").read()).hexdigest(),
        "code_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest(),
        "cache_status": cache_status, "cache_hashes": input_hashes,
        "acquisition_errors": state["err"],
        "outside_scope_discarded": skipped_outside,
        "interpretation": "Polygon-clipped CV inventories, not hazards or physical-infrastructure counts. "
                          "Image-positive tiles do not establish point visibility. Legacy empty caches are UNKNOWN.",
    }
    preserve_write(os.path.join(OUT, "acquisition_manifest_v2.json"),
                   json.dumps(manifest, ensure_ascii=False, indent=2))
    if state["err"] and not cached_only:
        sys.exit("Acquisition incomplete; errors are explicit in caches. No claim of complete coverage.")


if __name__ == "__main__":
    main()
