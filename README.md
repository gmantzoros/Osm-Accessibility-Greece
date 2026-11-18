# Accessibility information and routing uncertainty in OpenStreetMap: Greece

Code and summary results for the manuscript *Accessibility Information and
Routing Uncertainty in OpenStreetMap: A National Study of Greece* by
Georgios Mantzoros and Apostolos Meliones (Department of Digital Systems,
University of Piraeus).

The project asks three questions about a fixed OpenStreetMap (OSM) extract of
Greece:

1. Which accessibility-related attributes (tactile paving, kerbs, sidewalks,
   crossing markings, acoustic signals and others) are actually recorded?
2. What do those records imply for pedestrian routing under declared profiles
   for blind and visually impaired (BVI) and wheelchair users?
3. What does street-level imagery show at sampled places where a tag is
   missing?

It measures recorded map evidence. It does not certify physical
accessibility or safe routes.

## Main results

All values come from `results/impact/unified-conditional-2026-10-09/paper_data.json`
(model version `conditional-2026-10-09`).

- **Inventory.** The extract has 250,211 km of drivable ways and 20,931
  connected crossing-feature groups. Of these, 425 carry a tactile-paving tag
  and 151 say tactile paving is present. Of 9,952 traffic-signal nodes, 412
  carry an acoustic-signal tag and 13 say one is present.
- **Routing.** Under the main BVI profile (extended graph, 1 km component
  threshold), 7.17% of eligible length is usable when unknown edges are
  treated as impassable (pessimistic) and 96.43% when they are treated as
  passable (optimistic), a gap of 89.26 percentage points. The permissive BVI
  and wheelchair profiles give 7.39% and 6.35% pessimistic shares.
- **Countries.** Run through the same code, Greece ranks 18th of 20 European
  extracts by pessimistic share.
- **Sidewalk audit.** At 500 sampled points on drivable ways with no sidewalk
  tag (ten in each of 50 principal communities), 425 had recent, judgeable
  imagery, and a sidewalk was visible at 293. The equal-community share is
  68.9% (58.6–73.6% if unresolved points are counted as all absent or all
  present): 76.6% in city-type communities, 55.2% in towns and suburbs. A
  missing sidewalk tag is weak evidence that there is no sidewalk.

## How it works

1. **Census (Java, `census/`).** Reads the `.osm.pbf` extract in two passes,
   inventories tags per spatial unit, merges node and way crossing
   representations into crossing-feature groups, builds a pedestrian graph
   whose edges are PASS, BLOCK or UNKNOWN under a profile, and computes the
   pessimistic and optimistic usable shares. It can also rank candidate
   survey questions (which unknown ways would add the most usable network if
   confirmed) and draw the audit sample.
2. **Analysis (Python, `analysis/`).** Aggregates census output by
   municipality, region and urbanisation class; computes population exposure,
   survey scenarios and the 20-country and three-profile comparisons; analyses
   the sidewalk audit and imagery checks; and assembles every number used in
   the paper into one bundle (`paper_data.py`).
3. **Validation (`validation/`).** Builders for the browser audit forms and
   regression tests for the analysis code.

## Repository layout

| Path | Contents |
|---|---|
| `census/` | Java 21 / Maven census and routing-graph code, with unit tests |
| `analysis/` | Python aggregation, comparison, audit, figure and bundle scripts |
| `data/` | Routing profiles, strata definitions, input checksums and download/verification scripts (no map data) |
| `docs/` | Metric definitions, strata, routing specification, audit protocol, data sources, licensing and reproducibility notes |
| `validation/` | Audit form builders and tests |
| `results/impact/` | Corrected summary results (see below) |

## Included results

| Path | What it is |
|---|---|
| `unified-conditional-2026-10-09/paper_data.json` | Every number reported in the paper, in one JSON bundle |
| `unified-conditional-2026-10-09/analysis/` | Geographic summaries (municipality, NUTS-3, region, urbanisation), population exposure, survey-distance comparison and the sidewalk-audit analysis (`local_audit.json`) |
| `unified-conditional-2026-10-09/campaign/` | Survey-scenario summaries by place and cost scenario |
| `unified-conditional-2026-10-09/countries/` | 20-country comparison, rank correlations and input provenance |
| `unified-conditional-2026-10-09/profiles/` | Three-profile sensitivity table |
| `unified-conditional-2026-10-09/urban/` | The 50 selected municipality groups and their principal communities |
| `greece-full-strata-conditional-2026-10-09/` | Greek census tables per spatial unit (summary, crossings, routing feasibility) and the three ranked survey worklists |

The results are derived from OSM data and are released under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/),
© OpenStreetMap contributors.

## Not included, and why

- **Map inputs.** The Greek extract is 340 MB and the comparator country
  extracts are larger; GitHub does not accept files of that size. `data/`
  holds the checksums and provenance needed to verify an exact copy.
- **Administrative boundaries.** GISCO LAU/NUTS files are downloaded and
  prepared locally (`data/prepare_boundaries.py`).
- **Sidewalk-audit judgments and imagery.** The 500 per-point judgments were
  made by inspecting Google Street View, whose terms restrict reuse; only
  aggregated results are published. No imagery is included.
- **Mapillary caches** (provider data with its own terms).
- **Per-way and per-record census dumps, per-country and per-profile run
  folders.** They are large, and the summary tables above carry the reported
  results.
- **Run logs and execution manifests.** They contain local machine paths.
- **The manuscript and its builder.**

## Requirements

- Java 21 and Maven 3.9+ for the census
- Python 3.10+; the analysis uses the standard library, and figure scripts
  also need `matplotlib`
- PowerShell 5.1+ for `data/fetch_data.ps1`

## Running

Run the tests that need no map data:

```bash
python -B -m unittest discover -s validation -p "test_*.py"
mvn -f census/pom.xml test
```

Tests that check the full local result archive skip themselves when it is
absent.

To run the census on an extract:

```bash
mvn -f census/pom.xml package
java -Xmx5g -jar census/target/census.jar data/greece-latest.osm.pbf data/strata.json out/ --profile=data/profile-bvi.json
```

Running without arguments prints all options (audit sampling, survey-question
ranking, graph scope and component thresholds). The reported results use the
Geofabrik Greece extract with OSM replication timestamp
2026-08-26T20:22:15Z; a newer download will give different numbers. Verify a
copy against the retained checksums with
`powershell -NoProfile -ExecutionPolicy Bypass -File data/fetch_data.ps1`
(its default mode only verifies; it does not download). The
reporting scripts in `analysis/` read the full local result archive, so
re-running them end to end needs the census outputs for all countries and
profiles; see `docs/reproducibility.md`.

## License

Code: MIT (`LICENSE`). Results in `results/`: ODbL 1.0, © OpenStreetMap
contributors. Boundary data (GISCO, © EuroGeographics) and imagery providers
keep their own terms; see `docs/licensing.md`.

## Citation

Mantzoros G, Meliones A. *Accessibility Information and Routing Uncertainty in
OpenStreetMap: A National Study of Greece.* Manuscript, 2026.

Related earlier work: Meliones A, Mantzoros G. Discovery of Upcoming Cross
Streets in Google Maps Blind Navigation. *Applied Sciences* 15(24):13215
(2025). https://doi.org/10.3390/app152413215
