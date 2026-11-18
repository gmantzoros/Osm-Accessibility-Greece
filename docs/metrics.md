# Census metric definitions

**Version 2 — revised 2026-09-15 after scientific implementation review.**
Earlier repository notes recorded intended definitions; they do not establish external
preregistration or prove that every choice preceded data inspection. This revision changes
the implemented evidence handling. Historical numerical outputs require recomputation.

**Current corrected study:** `conditional-2026-10-09`, separately executed
for Greece and 19 cached comparator countries. The original
`conditional-2026-09-15` results remain frozen, with their known shared graph
defects uncorrected; their manifests are not relabelled. Graph outcomes describe
the implemented abstraction, not physical bounds. The corrected primary Greek
outcome uses eligible length and a 1 km eligible-component-mass threshold
(7.17% / 96.43%, gap 89.26 points); all-geometry state shares have a
different denominator.

## 1. Element universes

**Drivable ways**, defined by highway class, not a verified legal-access inventory:

```
motorway, trunk, primary, secondary, tertiary, residential, unclassified,
living_street, service, motorway_link, trunk_link, primary_link,
secondary_link, tertiary_link
```

**Pedestrian ways:** `pedestrian, footway, path, steps, corridor`.
`highway=footway + footway=sidewalk` additionally contributes separately mapped sidewalk
length. A `living_street` is in the drivable census even when the routing profile gives it
a dedicated-pedestrian default.

**Crossing ways:** `footway + footway=crossing`, `cycleway + cycleway=crossing`, or
`path + crossing=* / crossing:markings=*`. These are excluded from the pedestrian
way-length inventory and included in the crossing-feature inventory and routing graph.

**Crossing nodes:** `highway=crossing`. **Signal nodes:** `highway=traffic_signals`.
The latter is not restricted to pedestrian signal heads. **Kerb nodes:** `kerb=*` or
`barrier=kerb`. Other relevant restriction nodes are retained for routing without entering
these crossing/signal denominators.

**Auxiliary routing ways:** non-crossing `cycleway`, `track`, `bridleway`. They do not
enter the drivable/pedestrian census denominators. Their graph contributions are separately
reported; see `routing-feasibility.md`.

## 2. Length-weighted tag inventories

Length is the Haversine sum of consecutive resolved node pairs, radius 6,371,000 m.
Missing-coordinate segments are skipped, never interpolated. Length is mapped geometry,
not sidewalk-side distance, independently traversable facilities, or route availability.

For each stratum, class and family:

```
presence = tagged way km / all way km in that class/universe
affirmation = affirmatively tagged way km / the same denominator
```

Families: `name, sidewalk, surface, smoothness, lit, maxspeed, width, incline`.
The sidewalk family recognizes any of `sidewalk`, `sidewalk:left`, `sidewalk:right`,
`sidewalk:both`, counting each way's length once. Its canonical `sidewalk` key alone is a
different statistic.

Negative values:

| Family | Negative values |
|---|---|
| sidewalk | `no`, `none` |
| tactile_paving | `no`, `false`, `incorrect`, `0` |
| lit, crossing:island, button_operated, traffic_signals:sound/vibration | `no`, `none`, `false`, `0` |

Other supported values count as assertions, not proof of physical accessibility.
`tactile_paving=partial` can assert some tactile provision without satisfying the BVI
profile's passing condition. `sidewalk=separate` asserts existence in the census but
does not create a second passing centreline sidewalk in routing. A conflicting merged
value containing an explicit negative is not affirmative.

`tag_values.csv` preserves **feature_type and universe**. Way distributions distinguish
drivable from pedestrian geometry, and retain canonical sidewalk and side-prefixed keys
separately. Do not pool those universes and label the result drivable, or use a canonical
key distribution as the denominator of family coverage. Group duplicate values before
truncating a displayed distribution. Percentages must use numerators from the same
universe as their denominators.
The frozen value export replaces punctuation and truncates normalized values
after 40 characters; corrected source writes complete normalized values with
CSV escaping. Treat the archived distinct-value frequencies accordingly, not
as a lossless raw-tag dictionary.

## 3. Unified crossing-feature inventory

Union-find groups crossing-designated ways with crossing nodes they reference, and groups
crossing ways sharing a node. These are **connected mapped crossing-feature groups**, not
an independently verified one-to-one census of physical crossings. A long crossing way may
merge several streets, and disconnected representations of one crossing may remain separate.

Crossing-node and crossing-way tags are combined deterministically, preserving conflicting
normalized values in sorted semicolon form rather than encounter-order overwriting.
Kerb members on a crossing way contribute their relevant attributes, including tactile
evidence. `crossing_conflicts` records groups with differing member evidence values, excluding
representation/name keys (`highway`, `footway`, `cycleway`, `name`). Such differences may
be genuine spatial heterogeneity rather than mapping errors. The raw compound values
remain visible in the value distribution.

Effective kerb evidence considers direct and inherited members together:

- any `raised` evidence → barrier;
- otherwise unspecified/unsupported evidence → unknown;
- otherwise lowered/flush evidence → the corresponding accessible-tag category;
- otherwise `no`.

“Accessible” here names the tag category, not independently established usability. All
consumers requiring kerb evidence use `CrossingFeature.effectiveTags()`, including the
local feature audit and conditional routing.

Representative coordinates are the mean of member crossing-node coordinates, or the mean
of member-way middle-node coordinates when there are no crossing nodes. Schema counts
`node_only`, `way_only`, `both`, plus node/way member totals, expose representation choices.

### Markings and signalisation

`crossing:markings=*` is a modern markings statement. The retained legacy proxy recognizes:

- marked: `zebra, marked, uncontrolled, traffic_signals, controlled, pelican, toucan,
  pegasus, tiger`;
- unmarked: `unmarked, no, informal`.

This legacy proxy is a harmonization assumption, particularly for signalisation values:
it is not independent evidence of painted markings. Modern markings take precedence;
negative/conflicting markings are not affirmative. Report modern and legacy counts
separately, not only their union.
Frozen `crossing_signalised` counts use exact-string values; corrected source
recognizes supported semicolon-separated signalisation tokens. The archived
Greek count is 5,091 groups, versus 5,300 in the separately versioned Greek
correction; the original 20-country comparison has not been regenerated.

`crossing_tactile`/`crossing_tactile_yes` distinguish statements and affirmation.
`signal_sound`/`signal_sound_yes` use **traffic-signal nodes**. Additional
`crossing_sound`/`crossing_sound_yes` use unified crossing features. These overlapping,
different element universes must not be added as distinct physical devices.
Signal export detail `sound` denotes key presence, including negative values.

## 4. Junctions, density and spatial assignment

The historical `intersections` column counts nodes referenced by ≥2 distinct drivable
ways. This is a **shared-way proxy**, sensitive to splitting a continuous road into ways.
It is not the number of physical junctions or required pedestrian crossings.

The added `junctions_degree3` counts resolved nodes with ≥3 distinct neighbouring
drivable node IDs. Inserting degree-two geometry nodes or splitting a way does not change
this count. Grade separation still follows shared OSM node identity; it is not inferred
from coincident coordinates. `crossings_per_100_junctions` accompanies the original
`crossings_per_100_intersections`. Neither density is an omission percentage.

Ways are attributed whole to their middle list node (nearest resolved node when necessary),
not clipped at administrative boundaries. This is not a geometric length midpoint.
Nodes use their own coordinates; crossing groups use the representative point above.
Every object contributes to NATIONAL.

Polygon boundaries are included. When more than one stratum in a layer covers a point,
the lexicographically first name owns it deterministically; at most one match per layer
is counted. This tie-break is not evidence that input boundaries are error-free.

Each layer is queried independently. Coarser layers are **not guaranteed to be exact sums**
of the finest layer. `spatial_coverage.csv` reports assigned and unmatched drivable length
and crossing counts relative to NATIONAL. Missing coverage, extraction outside polygons,
and middle-node attribution require disclosure. Aggregated percentages must be recomputed
from summed numerators and denominators, never averaged across units.

The suggested minimum reporting size (≥20 drivable km or ≥50 shared-way nodes) is a
descriptive stability screen implemented by downstream reporting, not a statistical
guarantee or an exclusion from the census.

## 5. Descriptive readiness indices

These weighted tag summaries are not validated measures of pedestrian safety or individual
capability. Zero-denominator components are omitted and remaining weights renormalized.

**BPRI v0**, presence-based, now correctly uses separately counted crossing-node elements
and their own subtype/tactile attributes:

| Component | Weight |
|---|---:|
| min(1, crossing nodes / shared-way nodes) | .20 |
| crossing-node subtype | .15 |
| crossing-node tactile statement | .15 |
| signal-node sound statement | .10 |
| sidewalk-family statement | .15 |
| lit statement | .10 |
| surface statement | .05 |
| name statement | .10 |

**BPRI v1** uses unified crossing groups:

| Component | Weight |
|---|---:|
| min(1, crossing groups / shared-way nodes) | .20 |
| crossing subtype | .10 |
| markings statement proxy | .05 |
| tactile statement | .15 |
| signal-node sound statement | .10 |
| kerb statement | .05 |
| affirmative sidewalk family | .15 |
| lit statement | .10 |
| surface statement | .05 |
| name statement | .05 |

Legacy crossing-node counts are emitted alongside unified counts so v0 can be reproduced.
Pre-revision code incorrectly used unified crossing evidence for v0 too; those historical
v0 numbers are not an unchanged node-only baseline.

## 6. Destination records

The destination census counts individual OSM **node/way records**, not station complexes:

- station: `railway=station|halt` or `public_transport=station`;
- stop: bus stops, tram stops, railway/public-transport platforms, stop positions,
  ferry terminals;
- POI: amenity, shop or entrance records not assigned to a preceding class.

Way records retain one node for placement. Highway ways with destination tags participate
in both applicable inventories. Relations and station/site hierarchy are not expanded.
Platforms are in the stop category, not inherited by a station parent. The station category
may include modes other than rail. Consequently zero tactile statements on station records
does **not** mean no tactile paving at railway/metro facilities.

Reported attributes include tactile statements/affirmation, wheelchair statements and
values, names, departures information and shelter. The frozen `braille` column is a
legacy non-negative **braille-or-other-tactile-writing indicator**, not braille-key
statement coverage: it excludes explicit `no`. Corrected-source exports add
`braille_stated`/`braille_yes` and separate `tactile_writing_stated`/
`tactile_writing_yes`; statement counts include negative values and affirmative
counts do not. Do not reinterpret the archived legacy column as either new
metric. Missing parent-record tags are database observations, not absence of
physical provision or proof about child features.

## 7. Population and local audit interpretation

`provenance.csv` summarizes last-edit timestamps of retained OSM objects, not the dates
individual accessibility tags were added or verified. Crossing-group dates use contributing
members' most recent object edit. Calendar-year bins are not rolling-age intervals, and
the latest retained object timestamp is only a snapshot-time proxy, not extract provenance.

Population joined to administrative tag/graph summaries describes contextual exposure to
mapped data. It is not a count of individuals with accessible routes; boundary assignment,
within-area heterogeneity and profile assumptions remain.

The active **500-case sidewalk-only marker audit**, ten cases in each of 50
population-field-selected principal LAUs, estimates local visible provision
within a **drivable-way sidewalk-key-absence** frame. It is not a sample of
eligible routing UNKNOWN edges, route accessibility validation, a national
omission census, or calibration of nonlinear graph closures. The exact-radius-v2
draw retains seed 20260722 and known per-LAU pools/probabilities. Preserve it.
Static images do not establish acoustic operation. The unchanged UI records
year/date/reason and a generic location reference, not properly captured
provider, notes, month/day or panorama identity. See the validation protocol
for these limits; the planned repeat round was withdrawn on 2026-11-18.

## Changelog

- 2026-07-22: initial in-repository metric draft.
- 2026-08-28: repository revisions introduced crossing groups, schema harmonization,
  affirmation, destination records and population summaries. External preregistration is
  not established by these dated notes.
- 2026-09-15: corrected deterministic/member evidence, effective kerbs, true node-based v0,
  crossing acoustic inventory, degree-three junctions, spatial tie-breaking and unmatched
  coverage. Clarified denominator universes, mapped-feature versus physical interpretations,
  destination hierarchy, and the local-only audit estimand. All affected results must carry
  the corrected model revision and be recomputed before use.
- 2026-10-07: documentation aligned with the retained 500-case marker frame and
  eligible-mass headline; no model, input, sample or computation changed.
