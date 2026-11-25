"""Do OSM records show acoustic signals at publicly documented installations?

Junctions with acoustic pedestrian-signal devices were taken from public
announcements (Region of Western Greece, Patras, Nov 2022; Municipality of
Ilioupoli, May 2014; Municipality of Volos, Aug 2012). For each named pair of
streets the script asks the Overpass API for the OSM state on the date of the
analysed Greek extract (2026-08-26) and records whether any traffic-signal or
crossing node within 50 m of the junction carries a sound statement.

Announcements name junctions, not devices or crossing arms, and devices can be
removed or broken after installation, so a junction without an OSM sound tag
is an unrecorded documented installation, not a verified missing device.

The reported results come from the local check on the frozen extract, which
is faster and needs no network (the public Overpass instance returned repeated
502 errors for historical queries):
    python analysis/acoustic_signal_check.py --export-spec results/acoustic/junctions.json
    java -cp <classes>;census/target/census-0.1.0-shaded.jar gr.mantzoros.osmaccess.JunctionCheck         data/greece-latest.osm.pbf results/acoustic/junctions.json results/acoustic/acoustic_junctions.csv
The Overpass mode below is kept as an independent cross-check:
    python analysis/acoustic_signal_check.py results/acoustic
"""
import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

OVERPASS = "https://overpass-api.de/api/interpreter"
DATE = "2026-08-26T20:22:15Z"
RADIUS_M = 50
AFFIRMATIVE = {"yes", "walk", "always", "locate", "beep", "voice"}

SOURCES = {
    "patras": "Region of Western Greece handover of acoustic devices, 8 Nov 2022 "
              "(patrasevents.gr 9 Nov 2022; Panhellenic Federation of the Blind, 11 Nov 2022): "
              "devices 'installed and operating at 38 central junctions', 12 named.",
    "ilioupoli": "Municipality of Ilioupoli, first 19 acoustic devices (notia.gr, 5 May 2014), "
                 "19 named junctions; sound on request by remote control.",
    "volos": "Municipality of Volos, 18 new devices bringing the total to 60 (aftodioikisi.gr, "
             "9 Aug 2012), 8 named junctions.",
}

# (south, west, north, east) of the principal LAU polygon in data/boundaries.
BBOX = {
    "patras": (38.2022, 21.7099, 38.2971, 21.8748),
    "ilioupoli": (37.9178, 23.7420, 37.9494, 23.8068),
    "volos": (39.2947, 22.8986, 39.3971, 22.9757),
}

# Name stems as they appear (accents are made optional by the regex builder).
# A stem that starts with '^' or contains '.*' is used as written.
JUNCTIONS = [
    ("patras", "Αγίου Ανδρέου – Αγίου Νικολάου", "Ανδρέου", "Νικολάου"),
    ("patras", "Όθωνος Αμαλίας – Αγίου Νικολάου", "Αμαλίας", "Νικολάου"),
    ("patras", "Δ. Γούναρη – Αγ. Ανδρέου", "Γούναρη", "Ανδρέου"),
    ("patras", "Δ. Γούναρη – Κορίνθου", "Γούναρη", "Κορίνθου"),
    ("patras", "Δ. Γούναρη – Κανακάρη", "Γούναρη", "Κανακάρη"),
    ("patras", "Πατρών Κλάους – Καλαβρύτων", "Κλάους", "Καλαβρύτων"),
    ("patras", "Ηρώων Πολυτεχνείου – Πέντε Πηγαδίων", "Πολυτεχνείου", "Πηγαδίων"),
    ("patras", "Κωνσταντινουπόλεως – Φαβιέρου", "Κωνσταντινουπόλεως", "Φαβιέρου"),
    ("patras", "Κορίνθου – Ελ. Βενιζέλου", "Κορίνθου", "Βενιζέλου"),
    ("patras", "Δ. Γούναρη – Πλ. Ομονοίας", "Γούναρη", "Ομονοίας"),
    ("patras", "Πατρών Κλάους – Ρίτσου", "Κλάους", "Ρίτσου"),
    ("patras", "Ελ. Βενιζέλου – Γ. Παπανδρέου", "Βενιζέλου", "Παπανδρέου"),
    ("ilioupoli", "Αλ. Παναγούλη – Παρνασσού", "Παναγούλη", "Παρνασσού"),
    ("ilioupoli", "Μ. Αντύπα – ΙΚΑ", None, None),
    ("ilioupoli", "Κυπρίων Ηρώων – Εθν. Μακαρίου", "Κυπρίων", "Μακαρίου"),
    ("ilioupoli", "Κυπρίων Ηρώων – Μαραβελιά", "Κυπρίων", "Μαραβελιά"),
    ("ilioupoli", "Ηρως Κωνσταντοπούλου – Γαβριηλίδου", "Κωνσταντοπούλου", "Γαβριηλίδου"),
    ("ilioupoli", "Λ. Κοτζιά – Χαλκοκονδύλη – Χ. Τρικούπη", "Κοτζιά", "Χαλκοκονδύλη"),
    ("ilioupoli", "Ηρως Κωνσταντοπούλου – Εθν. Μακαρίου", "Κωνσταντοπούλου", "Μακαρίου"),
    ("ilioupoli", "Αγ. Κωνσταντίνου – Αιγίνης – Κέας", "Αιγίνης", "Κέας"),
    ("ilioupoli", "Αναστάσεως – Φαβιέρου", "Αναστάσεως", "Φαβιέρου"),
    ("ilioupoli", "Μ. Αντύπα – Τζουμαγιάς", "Αντύπα", "Τζουμαγιάς"),
    ("ilioupoli", "Πρωτοπαππά – Πλαστήρα", "Πρωτοπαπ", "Πλαστήρα"),
    ("ilioupoli", "Λ. Δημοκρατίας – Λ. Διγενή – Πατρ. Γρηγορίου", "Δημοκρατίας", "Διγενή"),
    ("ilioupoli", "Μ. Αντύπα – Αγ. Κωνσταντίνου", "Αντύπα", "Κωνσταντίνου"),
    ("ilioupoli", "Ηρώων Πολυτεχνείου – Αριστάρχου – Αθηνών", "Πολυτεχνείου", "Αριστάρχου"),
    ("ilioupoli", "Σοφ. Βενιζέλου – Ηρως Κωνσταντοπούλου", "Σοφ.*Βενιζέλου", "Κωνσταντοπούλου"),
    ("ilioupoli", "Ελ. Βενιζέλου – Αρτέμιδος – Ζήνωνος", "Ελ.*Βενιζέλου", "Αρτέμιδος"),
    ("ilioupoli", "Ελ. Βενιζέλου – Βελισσαρίου – Αναστάσεως", "Βελισσαρίου", "Αναστάσεως"),
    ("ilioupoli", "Ελ. Βενιζέλου – Παναγούλη", "Ελ.*Βενιζέλου", "Παναγούλη"),
    ("ilioupoli", "Σοφ. Βενιζέλου – Πρωτοπαππά", "Σοφ.*Βενιζέλου", "Πρωτοπαπ"),
    ("volos", "Πολυμέρη – Αθανασάκη", "Πολυμέρη", "Αθανασάκη"),
    ("volos", "Δημητριάδος – Σπυρίδη", "Δημητριάδος", "Σπυρίδη"),
    ("volos", "Δημητριάδος – Κοραή", "Δημητριάδος", "Κοραή"),
    ("volos", "Καρτάλη – Αλεξάνδρας", "Καρτάλη", "Αλεξάνδρας"),
    ("volos", "Ιάσονος – Αγ. Νικολάου", "Ιάσονος", "Νικολάου"),
    ("volos", "Ιωλκού – Ανθ. Γαζή", "Ιωλκού", "Γαζή"),
    ("volos", "Ιάσονος – Καρτάλη", "Ιάσονος", "Καρτάλη"),
    ("volos", "Λαρίσης – Νεαπόλεως", "Λαρίσης", "Νεαπόλεως"),
]

VARIANTS = {
    "α": "αάΑΆ", "ε": "εέΕΈ", "η": "ηήΗΉ", "ι": "ιίϊΐΙΊΪ", "ο": "οόΟΌ",
    "υ": "υύϋΰΥΎΫ", "ω": "ωώΩΏ",
}
PLAIN = {"ά": "α", "έ": "ε", "ή": "η", "ί": "ι", "ϊ": "ι", "ΐ": "ι", "ό": "ο", "ύ": "υ",
         "ϋ": "υ", "ΰ": "υ", "ώ": "ω"}


def pattern(stem):
    """Accent- and case-insensitive alternation regex for Overpass."""
    out = []
    i = 0
    while i < len(stem):
        if stem[i:i + 2] == ".*":
            out.append(".*")
            i += 2
            continue
        ch = stem[i]
        low = PLAIN.get(ch.lower(), ch.lower())
        if low in VARIANTS:
            out.append("(" + "|".join(VARIANTS[low]) + ")")
        elif low.isalpha():
            out.append("(" + low + "|" + low.upper() + ")")
        elif ch == ".":
            out.append(".")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def query(city, a, b):
    s, w, n, e = BBOX[city]
    bb = f"{s},{w},{n},{e}"
    q = f"""[out:json][timeout:120][date:"{DATE}"];
way({bb})[highway][name~"{pattern(a)}"]->.a;
way({bb})[highway][name~"{pattern(b)}"]->.b;
node(w.a)->.na;
node(w.b)->.nb;
node.na.nb->.x;
node.na(around.b:20)->.y;
(.x; .y;)->.j;
.a out count;
.b out count;
.j out count;
.j out skel;
node(around.j:{RADIUS_M})[highway~"^(traffic_signals|crossing)$"]->.s;
.s out tags;"""
    body = urllib.parse.urlencode({"data": q}).encode()
    for attempt in range(5):
        try:
            req = urllib.request.Request(OVERPASS, data=body, headers={
                "User-Agent": "osm-accessibility-research-script"})
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:
            if attempt == 4:
                raise
            print(f"  retry ({exc})", file=sys.stderr)
            time.sleep(30 * (attempt + 1))


def export_spec(path):
    spec = {"osm_date": "2026-08-26 extract", "bbox": {k: list(v) for k, v in BBOX.items()},
            "sources": SOURCES,
            "junctions": [{"city": c, "label": l, "a": a, "b": b} for c, l, a, b in JUNCTIONS]}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv):
    if len(argv) == 3 and argv[1] == "--export-spec":
        export_spec(argv[2])
        return 0
    if len(argv) != 2:
        print(__doc__)
        return 2
    out = Path(argv[1])
    (out / "raw").mkdir(parents=True, exist_ok=True)
    rows = []
    for k, (city, label, a, b) in enumerate(JUNCTIONS):
        row = {"city": city, "junction": label, "street_a": a or "", "street_b": b or ""}
        if a is None:
            row.update(status="not matchable (landmark, not a street)", lat="", lon="", ways_a=0, ways_b=0,
                       junction_nodes=0, signal_or_crossing_nodes=0, signal_nodes=0,
                       sound_stated=0, sound_affirmative=0, sound_values="")
            rows.append(row)
            continue
        raw = out / "raw" / f"{k:02d}_{city}.json"
        if raw.exists():
            data = json.loads(raw.read_text(encoding="utf-8"))
        else:
            print(f"{city}: {label}", file=sys.stderr)
            data = query(city, a, b)
            raw.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            time.sleep(3)
        counts = [int(el["tags"]["total"]) for el in data["elements"] if el["type"] == "count"]
        all_nodes = [el for el in data["elements"] if el["type"] == "node"]
        junction = all_nodes[:counts[2]]
        nodes = all_nodes[counts[2]:]
        sound_values = []
        for node in nodes:
            for key, value in node.get("tags", {}).items():
                if "sound" in key:
                    sound_values.append(f"{key}={value}")
        affirmative = sum(any("sound" in k and set(v.split(";")) & AFFIRMATIVE
                              for k, v in node.get("tags", {}).items()) for node in nodes)
        stated = sum(any("sound" in k for k in node.get("tags", {})) for node in nodes)
        row.update(status="junction found" if counts[2] > 0 else "junction not found in OSM",
                   lat=round(junction[0]["lat"], 6) if junction else "",
                   lon=round(junction[0]["lon"], 6) if junction else "",
                   ways_a=counts[0], ways_b=counts[1], junction_nodes=counts[2],
                   signal_or_crossing_nodes=len(nodes),
                   signal_nodes=sum(n.get("tags", {}).get("highway") == "traffic_signals" for n in nodes),
                   sound_stated=stated, sound_affirmative=affirmative,
                   sound_values="; ".join(sorted(set(sound_values))))
        rows.append(row)
    with open(out / "acoustic_junctions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    summary = {}
    for city in BBOX:
        rs = [r for r in rows if r["city"] == city]
        found = [r for r in rs if r["status"] == "junction found"]
        summary[city] = {
            "documented_named_junctions": len(rs),
            "located_in_osm": len(found),
            "located_with_signal_node": sum(r["signal_nodes"] > 0 for r in found),
            "located_with_any_sound_statement": sum(r["sound_stated"] > 0 for r in found),
            "located_with_affirmative_sound": sum(r["sound_affirmative"] > 0 for r in found),
            "source": SOURCES[city],
        }
    meta = {"overpass": OVERPASS, "osm_date": DATE, "radius_m": RADIUS_M,
            "affirmative_values": sorted(AFFIRMATIVE), "summary": summary}
    (out / "acoustic_summary.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
