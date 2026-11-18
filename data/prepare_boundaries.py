#!/usr/bin/env python3
"""Build the census polygon layers from the pinned GISCO downloads.

Input   data/external/LAU_RG_01M_2021_4326.geojson          (all EU LAUs)
        data/external/NUTS_RG_01M_2021_4326_LEVL_2.geojson  (NUTS 2 = Greek regions)
        data/external/NUTS_RG_01M_2021_4326_LEVL_3.geojson  (NUTS 3 + official URBN_TYPE)
        data/external/degurba.csv                           (optional, official DEGURBA)

Output  data/boundaries/greece_municipalities.geojson
        data/boundaries/greece_regions.geojson
        data/boundaries/greece_nuts3.geojson
        data/boundaries/municipalities.csv     lookup table for analysis joins

Standard library only, deliberately: the census must reproduce on a bare Python
with no pip step. Point-in-polygon happens on the Java side (JTS); this script
only filters, classifies and rewrites.

Run:  python data/prepare_boundaries.py
"""

import csv
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
# NOT "aux": that is a reserved device name on Windows and the directory gets
# silently mangled, which breaks the fetch/prepare handoff in a way that is very
# hard to see (the files are there, under a name nothing looks for).
AUX = os.path.join(HERE, "external")
OUT = os.path.join(HERE, "boundaries")

LAU_FILE = os.path.join(AUX, "LAU_RG_01M_2021_4326.geojson")
NUTS2_FILE = os.path.join(AUX, "NUTS_RG_01M_2021_4326_LEVL_2.geojson")
NUTS3_FILE = os.path.join(AUX, "NUTS_RG_01M_2021_4326_LEVL_3.geojson")
DEGURBA_CSV = os.path.join(AUX, "degurba.csv")

# Greece is EL in NUTS/LAU coding (not GR).
CNTR = "EL"

# Greek LAU_ID is an 8-digit ELSTAT code whose first 4 digits identify the
# Kallikratis municipality (dimos): 6137 LAUs collapse to 333 dimoi, and 3 digits
# give the 78 regional-unit-level codes. Census counts and lengths are additive, so
# dimos-level results are produced by summing LAU rows in analysis rather than by
# dissolving polygons - which keeps this script dependency-free and exact.
DIMOS_CODE_LEN = 4

# Eurostat's official NUTS 3 urban-rural typology, carried in the NUTS file.
URBN_TYPE = {1: "predominantly_urban", 2: "intermediate", 3: "predominantly_rural"}


def load(path):
    if not os.path.isfile(path):
        sys.exit(
            f"Missing {path}\n"
            f"Run:  powershell -ExecutionPolicy Bypass -File data\\fetch_data.ps1 -Aux"
        )
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def degurba_proxy(pop, area_km2):
    """LAU-level approximation of the Degree of Urbanisation.

    Official DEGURBA is computed on a 1 km population grid, then assigned to LAUs.
    Without the grid we approximate it from the LAU's own population and density,
    reusing DEGURBA's own thresholds (urban centre: >=1500 inh/km2 and >=50k;
    urban cluster: >=300 inh/km2 and >=5k). This is a PROXY and is labelled as one
    everywhere it appears; drop an official per-LAU table at data/external/degurba.csv
    (columns lau_id,degurba) and it is used instead.

    The proxy systematically misclassifies large-area municipalities that contain a
    dense town surrounded by empty land - which after Kallikratis is most of rural
    Greece - so it under-reports class 1/2. Report it as a robustness layer next to
    the official NUTS 3 typology, never as the headline stratification.
    """
    if not pop or not area_km2:
        return None
    dens = pop / area_km2
    if pop >= 50_000 and dens >= 1500:
        return 1
    if pop >= 5_000 and dens >= 300:
        return 2
    return 3


DEGURBA_LABEL = {1: "cities", 2: "towns_suburbs", 3: "rural"}


def load_official_degurba():
    if not os.path.isfile(DEGURBA_CSV):
        return {}
    out = {}
    with open(DEGURBA_CSV, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            keys = {k.lower(): v for k, v in row.items()}
            lau = keys.get("lau_id") or keys.get("lau") or keys.get("gisco_id", "").split("_")[-1]
            val = keys.get("degurba") or keys.get("dgurba") or keys.get("class")
            if lau and val:
                try:
                    out[str(lau).strip()] = int(float(val))
                except ValueError:
                    pass
    return out


def bbox_centre(geom):
    xs, ys = [], []

    def walk(c):
        if isinstance(c, (int, float)):
            return
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
            return
        for part in c:
            walk(part)

    walk(geom.get("coordinates", []))
    if not xs:
        return None, None
    return (min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2


def unique_namer():
    seen = Counter()

    def make(prefix, base, suffix_id):
        base = (base or "").strip() or suffix_id
        name = f"{prefix}:{base}"
        seen[name] += 1
        if seen[name] > 1:
            name = f"{prefix}:{base} [{suffix_id}]"
        return name

    return make


def write_geojson(path, features):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh, ensure_ascii=False)
    size = os.path.getsize(path) / 1e6
    print(f"  wrote {os.path.basename(path):38s} {len(features):5d} polygons  {size:6.1f} MB")


def main():
    os.makedirs(OUT, exist_ok=True)
    official = load_official_degurba()
    source = "official" if official else "proxy"
    if official:
        print(f"Using official DEGURBA for {len(official)} LAUs from {DEGURBA_CSV}")
    else:
        print("No data/external/degurba.csv found - using the documented density proxy.")

    name_of = unique_namer()

    # ------------------------------------------------------------ municipalities
    lau = load(LAU_FILE)
    muni, rows = [], []
    for f in lau["features"]:
        p = f["properties"]
        if p.get("CNTR_CODE") != CNTR:
            continue
        lau_id = str(p.get("LAU_ID", "")).strip()
        pop = p.get("POP_2021")
        area = p.get("AREA_KM2")
        cls = official.get(lau_id) or degurba_proxy(pop, area)
        label = DEGURBA_LABEL.get(cls, "unknown")
        group = label if source == "official" else f"proxy_{label}"
        name = name_of("DIMOS", p.get("LAU_NAME"), lau_id)
        lat, lon = bbox_centre(f["geometry"])
        muni.append({
            "type": "Feature",
            "properties": {
                "name": name, "group": group, "lau_id": lau_id,
                "gisco_id": p.get("GISCO_ID"), "lau_name": p.get("LAU_NAME"),
                "pop_2021": pop, "area_km2": area, "pop_dens_2021": p.get("POP_DENS_2021"),
                "degurba": cls, "degurba_source": source,
                "dimos_code": lau_id[:DIMOS_CODE_LEN],
            },
            "geometry": f["geometry"],
        })
        rows.append({
            "stratum": name, "lau_id": lau_id, "gisco_id": p.get("GISCO_ID"),
            "lau_name": p.get("LAU_NAME"), "pop_2021": pop, "area_km2": area,
            "pop_dens_2021": p.get("POP_DENS_2021"), "degurba": cls,
            "degurba_source": source, "degurba_label": label,
            "dimos_code": lau_id[:DIMOS_CODE_LEN],
            "centroid_lat": lat, "centroid_lon": lon,
        })
    write_geojson(os.path.join(OUT, "greece_municipalities.geojson"), muni)

    # ------------------------------------------------------------------ regions
    n2 = load(NUTS2_FILE)
    regions = []
    for f in n2["features"]:
        p = f["properties"]
        if p.get("CNTR_CODE") != CNTR:
            continue
        regions.append({
            "type": "Feature",
            "properties": {
                "name": name_of("PERIF", p.get("NAME_LATN"), p.get("NUTS_ID")),
                "group": "region", "nuts_id": p.get("NUTS_ID"),
            },
            "geometry": f["geometry"],
        })
    write_geojson(os.path.join(OUT, "greece_regions.geojson"), regions)

    # ------------------------- NUTS 3 with the OFFICIAL urban-rural typology
    n3 = load(NUTS3_FILE)
    nuts3 = []
    for f in n3["features"]:
        p = f["properties"]
        if p.get("CNTR_CODE") != CNTR:
            continue
        nuts3.append({
            "type": "Feature",
            "properties": {
                "name": name_of("NUTS3", p.get("NAME_LATN"), p.get("NUTS_ID")),
                "group": URBN_TYPE.get(p.get("URBN_TYPE"), "unknown"),
                "nuts_id": p.get("NUTS_ID"), "urbn_type": p.get("URBN_TYPE"),
            },
            "geometry": f["geometry"],
        })
    write_geojson(os.path.join(OUT, "greece_nuts3.geojson"), nuts3)

    # ------------------------------------------------------------------ lookup
    csv_path = os.path.join(OUT, "municipalities.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {'municipalities.csv':38s} {len(rows):5d} rows")

    dist = Counter(r["degurba_label"] for r in rows)
    pop = sum(r["pop_2021"] or 0 for r in rows)
    print(f"\nGreece: {len(rows)} municipalities, {pop:,.0f} inhabitants (LAU 2021)")
    print(f"  urbanisation ({source}): " + ", ".join(f"{k}={v}" for k, v in sorted(dist.items())))
    print(f"  regions: {len(regions)}   NUTS 3 units: {len(nuts3)}   "
          f"dimoi (by code): {len({r['dimos_code'] for r in rows})}")
    thin = sum(1 for r in rows if (r["pop_2021"] or 0) < 200)
    print(f"  {thin} LAUs under 200 inhabitants - per-unit results need a "
          f"minimum-size rule (see analysis/aggregate.py)")
    print("\nNext: java -Xmx4g -jar census/target/census.jar "
          "data/greece-latest.osm.pbf data/strata.json results/census")


if __name__ == "__main__":
    main()
