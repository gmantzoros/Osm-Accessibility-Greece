# Spatial Frame and Strata

The census always reports `NATIONAL`. Everything else lives in a **layer**; a feature is
assigned by its representative point to at most one stratum per layer. Layers are
independent and must not be summed together. Definitions live in `data/strata.json`.

| Layer | Units | Source | Purpose |
|---|---|---|---|
| `circle` | 25 named places | `data/strata.json` | Descriptive study footprints, not a published detector sampling frame |
| `municipality` | 6,137 Greek LAUs | GISCO LAU 2021 | Finest official unit; carries population + area |
| `region` | 13 περιφέρειες | GISCO NUTS 2021 level 2 | Regional reporting |
| `nuts3` | 52 περιφερειακές ενότητες | GISCO NUTS 2021 level 3 | Carries the **official** Eurostat urban–rural typology |

Polygon layers are built by `python data/prepare_boundaries.py` from the pinned GISCO
downloads and are gitignored; the script is the reproducible artefact.

## Why polygons replaced circles as the reportable frame

A 12 km circle centred on Syntagma contains open sea and much of Piraeus. It is a fine
*descriptive* footprint, but it cannot carry a claim about "the Municipality of
Athens". The published 100-case Google Maps detector evaluation does not validate
these circles or establish them as its sampling frame.

## Vintage

**Boundary-layer vintage: GISCO 2021** (LAU and NUTS). The `POP_2021` field does
not establish a synchronized population vintage: the Greek sum and Athens value
match revised 2011 ELSTAT figures, while per-LAU provenance remains unverified.
Retain this historical field explicitly; see `docs/data-sources.md`. Current observable checksums are recorded in
`data\external\AUX_SNAPSHOT.txt`. This is a current-byte inventory, **not an
original acquisition record or proof that an upstream URL served these bytes**.

The 1:1M generalized polygon layers assign ways by their middle stored node and point features
independently. Their coastlines and borders need not nest exactly. National
features can be unmatched, and a way crossing a boundary is not length-clipped.
Points on boundaries are included with one deterministic owner within each layer.
`results/analysis/spatial_coverage.csv` reports assigned and unmatched national
totals for each layer. LAU rollups sum assigned records; they are **not exact
hierarchical partitions of the national network**. NUTS and region totals are
computed from their own polygons, not derived by summing LAUs.
The native `results\census\spatial_coverage.csv` separately records assigned,
unassigned and national drivable-length/crossing totals, assignment method and
model version; the bundle exposes this as `native_spatial_coverage`.

## The Greek LAU hierarchy

Greek LAU is the **κοινότητα** (local community), not the Kallikratis **δήμος**. The 8-digit
ELSTAT `LAU_ID` encodes the hierarchy, so coarser levels come from prefix grouping — and
because census counts and lengths are additive, `analysis/aggregate.py` produces them by
summing rows rather than dissolving polygons:

| Prefix length | Distinct values | Level |
|---|---|---|
| 3 | 78 | regional-unit-ish grouping |
| **4** | **333** | **δήμος (Kallikratis municipality)** |
| 8 | 6,137 | κοινότητα (the polygon layer) |

Eurostat represents a δήμος with no community subdivision as a single
*ψευδοκοινότητα* — e.g. `4501 Ψευδοκοινότητα Αθηναίων`; its GISCO
population-field value 664,046 matches revised 2011, not 2021.

**Consequence for reporting:** 2,039 LAUs have under 200 inhabitants. Per-unit rankings
apply the minimum-size rule (`docs\metrics.md` §4); everything below it is reported as a
group, never as a rank.

## Urban–rural classification

Two classifications are carried, deliberately:

1. **Official — Eurostat NUTS 3 urban–rural typology** (`URBN_TYPE`: predominantly urban /
   intermediate / predominantly rural), shipped inside the NUTS file. This is the headline
   stratification. A common classification alone does not establish direct
   comparability with studies using different feature universes, dates or
   mapping/geographic contexts.
2. **Proxy — LAU-level degree of urbanisation**, computed from the historical `POP_2021` field and `AREA_KM2`
   using DEGURBA's own thresholds (≥1,500 inh/km² and ≥50k → cities; ≥300 inh/km² and ≥5k →
   towns/suburbs; else rural). Labelled `proxy_*` everywhere it appears.

The proxy is **not** official DEGURBA, which is computed on a 1 km population grid. It
systematically misclassifies large-area municipalities containing a dense town surrounded by
empty land — which after Kallikratis is most of rural Greece — so it under-reports classes 1
and 2. In the retained GISCO layer it yields 34 cities, 143 towns/suburbs, 5,951 rural, 9 unknown.
Report it as a robustness layer beside the official NUTS 3 typology, never as the headline.

In a separately documented future update, `prepare_boundaries.py` can consume
an official DEGURBA CSV with columns `lau_id,degurba` at
`data\external\degurba.csv`; the `degurba_source` column then changes from
`proxy` to `official`. This is **not an
instruction to change frozen assets**; acquisition and crosswalk provenance
must first be established.

## Selected dimos groups and principal-LAU audit frame

`analysis\urban_scope.py [top_n]` ranks dimos-prefix groups by the retained GISCO
population field. `top_cities.csv` describes **all assigned LAUs** in those groups;
`scope_laus.json` selects **only the highest-field-population LAU in each group**
for the local feature audit. Neither is an official urban delineation. State the
frame explicitly instead of treating all dimos land as urban or claiming that
zero-heavy rural outcomes cannot be analysed.

The active draw is 500 sidewalk-only marker cases, ten per principal LAU,
seed 20260722, exact-radius-v2, with known per-LAU pools/probabilities. It samples
drivable ways lacking sidewalk-family keys, not all eligible routing UNKNOWN
ways. Its planned repeat round was withdrawn on 2026-11-18 (single-pass judgments).
No reselection, sample replacement or harness change is prescribed here.

`urban_summary.json` reports descriptive population/density rank correlations
with endpoints, gap, crossing representation and naming. These associations
neither demonstrate contributor residence nor distinguish mapping activity from
infrastructure, network composition and tagging practices.

The national zero-share statistic uses eligible **LAUs with positive GISCO
population, positive eligible/nonauxiliary network length, and at least 20
all-graph walkable km**, not every LAU in the boundary
file. Its numerator, denominator and threshold are exported in `national_spread`.
LAUs excluded from population weighting are recorded in `exposure_metadata.json`.
Usable percentages themselves use eligible length, with the threshold applied to
eligible component mass. The 20 km reporting-size screen is a distinct all-graph
length criterion and must not be confused with that denominator.

Both shared-way-node `intersections` (sensitive to ordinary OSM way splits) and
`junctions_degree3` are retained. The latter counts degree-three-or-more drivable
network nodes, with its own `crossings_per_100_junctions` rate. Neither is a
field-verified physical-junction inventory. `crossing_conflicts` records conflicting
merged crossing evidence rather than silently treating it as missing.

Campaign exports use the **national ranking**, not this selected audit frame.
Named campaign place labels must not be counted as dimoi. Selected-way length is
survey extent. The recorded intervention model,
`way_state_override_fixed_node_transitions`, sets selected way states to PASS
while retaining existing point-transition states. It does not simulate literal
tag edits or their cascading effects on merged crossing/node evidence.
Calculated graph gains and assumed desk minutes are not realised accessibility
improvements or measured whole-way survey costs.

## Named circles

Retained for descriptive comparisons. These are not the sampling frame of the
published Google Maps detector evaluation.

| Group | Stratum | Rationale |
|---|---|---|
| metro | Athens, Thessaloniki | Largest metropolitan areas |
| large_city | Patras, Heraklion, Larissa, Volos | Larger-city descriptive group |
| medium_town | Ioannina, Chania, Kalamata, Trikala, Serres, Xanthi | 20–60k, spread across Epirus, Crete, Peloponnese, Thessaly, Macedonia, Thrace |
| small_town | Nafplio, Karystos, Edessa, Sparti, Igoumenitsa | Smaller-town descriptive group |
| island | Ermoupoli, Rhodes town, Mytilene, Naxos town | Island morphology + tourism-mapping effects |
| tourist_rural | Santorini | Tourism-hypothesis arm |
| rural | Grevena, Karpenisi, Vytina areas | Expected completeness floor |

**Status: retained descriptive circles, not an independently verified urban
delineation.** Do not adjust frozen centres/radii during documentation repair.
They are not the active principal-LAU audit frame.

## Smoke-test config

`data/strata_test.json` runs the Malta extract against two Malta circles plus two synthetic
boxes (`census/src/test/resources/test_boxes.geojson`). The box covering the whole extract
must reproduce the `NATIONAL` row exactly — that is the regression test for the
point-in-polygon path.
