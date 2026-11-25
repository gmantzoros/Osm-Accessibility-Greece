"""Growth of accessibility-related OSM tagging in Greece, 2012-2026, via the ohsome API.

Counts raw OSM records (nodes) and mapped lengths (ways) at half-yearly
snapshots inside the 13 Greek regions (GISCO NUTS-2 polygons supplied with the
repository). Counts are of individual records, not the merged crossing-feature
groups of the census, so they describe the trend, not the census inventory.

The script only sends the filter definitions and the public region polygons to
the ohsome API (https://api.ohsome.org), which is operated by HeiGIT.

Usage:
    python analysis/tagging_trend.py results/trend
"""
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.ohsome.org/v1"
TIME = "2012-01-01/2026-07-01/P6M"
DRIVABLE = ("motorway, trunk, primary, secondary, tertiary, residential, unclassified, "
            "living_street, service, motorway_link, trunk_link, primary_link, "
            "secondary_link, tertiary_link")
SIDEWALK_ANY = "(sidewalk=* or sidewalk:both=* or sidewalk:left=* or sidewalk:right=*)"

MEASURES = {
    # name: (aggregation, filter)
    "crossing_nodes": ("count", "type:node and highway=crossing"),
    "crossing_nodes_tactile_stated": ("count", "type:node and highway=crossing and tactile_paving=*"),
    "crossing_nodes_tactile_yes": ("count", "type:node and highway=crossing and "
                                            "tactile_paving in (yes, contrasted, primitive)"),
    "crossing_ways": ("count", "type:way and (footway=crossing or cycleway=crossing)"),
    "signal_nodes": ("count", "type:node and highway=traffic_signals"),
    "signal_nodes_sound_stated": ("count", "type:node and highway=traffic_signals and "
                                           "traffic_signals:sound=*"),
    "signal_nodes_sound_yes": ("count", "type:node and highway=traffic_signals and "
                                        "traffic_signals:sound in (yes, walk, always)"),
    "drivable_km": ("length", f"type:way and highway in ({DRIVABLE})"),
    "drivable_km_sidewalk_stated": ("length", f"type:way and highway in ({DRIVABLE}) and {SIDEWALK_ANY}"),
    "separate_sidewalk_km": ("length", "type:way and highway=footway and footway=sidewalk"),
    "footway_km": ("length", "type:way and highway in (footway, pedestrian, path)"),
}


def regions():
    g = json.loads((ROOT / "data/boundaries/greece_regions.geojson").read_text(encoding="utf-8"))
    features = [{"type": "Feature", "id": f["properties"]["nuts_id"],
                 "properties": {"id": f["properties"]["nuts_id"]}, "geometry": f["geometry"]}
                for f in g["features"]]
    return json.dumps({"type": "FeatureCollection", "features": features})


def query(aggregation, flt, bpolys):
    url = f"{API}/elements/{aggregation}/groupBy/boundary"
    body = urllib.parse.urlencode({"bpolys": bpolys, "filter": flt, "time": TIME}).encode()
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "osm-accessibility-research-script"})
            with urllib.request.urlopen(req, timeout=900) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # network or 5xx: retry with backoff
            if attempt == 3:
                raise
            print(f"  retry after error: {exc}", file=sys.stderr)
            time.sleep(20 * (attempt + 1))


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    out = Path(argv[1])
    (out / "raw").mkdir(parents=True, exist_ok=True)
    bpolys = regions()
    series = {}
    for name, (aggregation, flt) in MEASURES.items():
        raw_file = out / "raw" / f"{name}.json"
        if raw_file.exists():
            data = json.loads(raw_file.read_text(encoding="utf-8"))
        else:
            print(f"querying {name} ...", file=sys.stderr)
            data = query(aggregation, flt, bpolys)
            raw_file.write_text(json.dumps(data, indent=1), encoding="utf-8")
        totals = {}
        for group in data["groupByResult"]:
            for item in group["result"]:
                totals[item["timestamp"][:10]] = totals.get(item["timestamp"][:10], 0) + item["value"]
        series[name] = totals
    stamps = sorted(next(iter(series.values())))
    with open(out / "tagging_trend.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date"] + list(MEASURES))
        for s in stamps:
            w.writerow([s] + [round(series[m][s] / (1000 if MEASURES[m][0] == "length" else 1), 3)
                              for m in MEASURES])
    meta = {"api": API, "time": TIME, "filters": {k: v[1] for k, v in MEASURES.items()},
            "units": {k: ("km" if v[0] == "length" else "records") for k, v in MEASURES.items()},
            "regions": "data/boundaries/greece_regions.geojson (13 NUTS-2 regions, summed)",
            "data_timestamp_note": "ohsome history database; latest snapshot 2026-07-01"}
    (out / "tagging_trend_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print((out / "tagging_trend.csv").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
