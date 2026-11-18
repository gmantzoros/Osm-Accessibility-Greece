# Conditional routing graph: specification and limitations

**Revised 2026-09-15 following scientific implementation review.**
The earlier 2026-08-28 repository notes described intended methods. They are not evidence
of an independently timestamped preregistration. Results produced before this revision
must not be presented as results of the corrected implementation.

**Current separately versioned results: `conditional-2026-10-09`. Archived
results: `conditional-2026-09-15`.** The completed corrected Greek full-strata,
20-country and three-profile runs use a separately staged JAR. Archived
country/profile outputs still represent the frozen implementation, including
its known shared graph defects; they are not relabelled. Neither
implementation measures physical routing safety, and the countries still
differ in mapping, geography and history.

## Estimand

The analysis measures component length in an abstraction of OSM under specified tag rules.
It does **not** measure physical accessibility, safe journeys, destination reachability,
or what every accessibility router can guarantee.

- `PASS`: admitted by the declared rules, including their defaults.
- `BLOCK`: excluded by an explicit rule.
- `UNKNOWN`: unresolved primary evidence **or a deliberate modelling policy**.

The pessimistic closure admits PASS ways and transitions. The optimistic closure also
admits UNKNOWN ways and transitions. Their difference is a **model-conditional closure
gap**, not a confidence interval or a bound on physical accessibility.
Decided length is PASS **plus BLOCK**, not PASS alone.

## Evidence and rules

`data/profile-*.json` supplies primary conditions and secondary thresholds. The parser
retains every key referenced by the loaded profile, in addition to the census inventory.
The highway class is also stored independently on `RoadWay`.

Primary choices:

- Crossing-designated ways use unified crossing/member evidence.
- Ordinary roads use sidewalk statements. Dedicated pedestrian ways default to PASS.
- BVI steps may PASS on handrail **or** tactile evidence; explicit blockers dominate.
- Wheelchair steps BLOCK by default; the pinned rule lets a generic `ramp=yes`
  PASS unless wheelchair-specific refusal is present. This is an unvalidated
  modelling policy, not an observed measure of wheelchair usability.
- Tracks, cycleways and bridleways that are not crossing ways are auxiliary, deliberately
  UNKNOWN unless an explicit blocker applies. Their absence from PASS is not map silence.
- `sidewalk=separate` does not create an additional passing road-centerline sidewalk.
  Such an unresolved road is classified as a separate-geometry policy case.

Explicit `foot=no|private` blocks. Explicit pedestrian permission can override a general
`access=no|private` **on the same original object** in the corrected source;
one crossing member's foot permission does not cancel another member's
restriction. Otherwise those general restrictions block. The wheelchair profile
also respects `wheelchair=no`; `wheelchair=limited` is UNKNOWN. Conditional access
expressions are not parsed and remain UNKNOWN. These are declared access policies, not
claims about legal entitlement or individual capability.

Surface, smoothness, width, incline and kerb checks can downgrade to BLOCK. Missing
secondary measurements **do not** downgrade a primary PASS. JSON secondary `onMissing`
must therefore be `pass`. Roadway width/surface/smoothness are not sidewalk measurements:
only the retained sidewalk-prefixed measurements are used for those road checks.
The surface-quality diagnostic remains a raw mapped-way attribute, not walking quality.

Conflicting member cue values are preserved in deterministic sorted form;
original-object permissions remain separate. A matching blocking value
dominates passing evidence, including compound hard-barrier tokens. Kerb
severity is resolved across direct and inherited
members: any raised value wins, unspecified evidence is retained as unknown, and a
lowered feature tag cannot hide a raised endpoint.

## Topology and point restrictions

The graph uses consecutive, resolved node pairs, not just way endpoints. Missing-coordinate
gaps are never bridged. A restricted point has separate incident ports with zero-length
transitions: a blocked/unknown transition prevents through travel in the relevant closure,
while the geometric approach segments remain represented.

Crossing-node evidence participates in those transitions, including node-only crossings.
Kerbs and explicit node access restrictions also participate. Walls/fences/blocks block;
other unmodelled barriers remain UNKNOWN unless explicit pedestrian permission resolves
the model's access condition. Wheelchair stiles/turnstiles block.

This does **not** invent left/right sidewalk geometry or distinguish approaching along a
carriageway from crossing it. Applying a crossing-node condition to incident transitions
is conservative within this undirected centreline abstraction. Side-specific transfers,
directional pedestrian restrictions, turn relations, time-dependent access and station
interiors are not represented. A count or length of separately mapped sidewalks cannot
bound the resulting connectivity error.

## Reporting universes and thresholds

The **extended** graph includes auxiliary classes. The **core** graph is rebuilt without
non-crossing track/cycleway/bridleway ways, including their blocked parts. Core sensitivity
is not subtraction of an UNKNOWN-length column.

Legacy `walkable_km`, state lengths and `pess_usable_km`/`opt_usable_km` describe all geometric
length in the chosen graph. Their component threshold is total admitted component length.

The preferred eligible-length measures exclude auxiliary length from both the credited
mass and the component threshold:

| Added column | Meaning |
|---|---|
| `eligible_km`, `auxiliary_km` | Core-class and auxiliary geometry in the graph |
| `pess_eligible_usable_km`, `opt_eligible_usable_km` | Eligible length in components with eligible mass ≥ threshold |
| `eligible_decidability_gap_pct` | Difference between those quantities / eligible km |
| `decided_pct` | (PASS + BLOCK) / all graph km |
| `missing_evidence_unknown_km` | Unresolved primary evidence, including unsupported values; not necessarily absent tags |
| `policy_unknown_km` | Auxiliary classes, separate-sidewalk geometry and unparsed conditional/limited access |
| `unknown_other_km` | UNKNOWN causes outside the original four primary cause columns |
| `graph_scope`, `component_threshold_km`, `model_version` | Explicit run specification |

Auxiliary edges may connect eligible pieces in the extended optimistic closure, but their
own mass cannot alone make an eligible stub meet the eligible threshold. Zero-length
UNKNOWN/BLOCK node transitions are counted separately in `feasibility_global.csv`; they
are not additional missing kilometres. Crossing edge counts are resolved consecutive
segments of crossing-designated ways, not physical crossing counts.

Components are national; per-stratum rows attribute geometry by whole-way middle node.
An attributed segment may belong to a component extending outside its stratum. A 1 km
component is a fragmentation convention, not evidence of useful destinations.

The corrected primary Greek BVI/extended result at **1 km eligible mass** is
**7.17% pessimistic / 96.43% optimistic**, gap **89.26 percentage points**.
These shares divide eligible usable length by eligible length.
PASS/BLOCK/UNKNOWN shares divide state lengths by all graph geometry; they
are not those endpoints. The separate corrected Greek run agrees with the
new Greece-country and BVI-profile national results.

## Implemented CLI and retained outputs (not refresh instructions)

Existing positional arguments and the default extended graph / profile threshold remain:

```powershell
$jar = 'C:\path\to\separate\census\target\census.jar'
java -Xmx4g -jar $jar data\extract.osm.pbf data\strata.json results\new-run --profile=data\profile-bvi.json
```

Additional options:

- `--graph-scope=core` selects a real core graph for the main outputs.
- `--core-sensitivity` evaluates both scopes at the profile's threshold.
- `--component-thresholds=0.5,1,2` evaluates both scopes at these thresholds, also including
  the profile's default. Results go to `feasibility_sensitivity.csv`.

Each sensitivity scope is constructed from its actual ways and transitions; components
and lengths are recomputed. Existing CSV columns are retained and corrected-run metadata
is appended. Comparisons must not mix model revisions. Agreement between profiles sharing
dominant defaults is limited sensitivity evidence, not proof of model independence.

Corrected default extended/1 km executions for three profiles and the
corrected Greek BVI native 0.5/1/2 km extended/core scenarios are **not a
full factorial**. Their separately versioned manifests link the actual
corrected source, staged executable, inputs and outputs. Archived
country/profile execution manifests match only their **frozen execution**;
the archived native threshold/scope artifact's execution linkage remains
**unverified**. Do not infer its past command, execution time or model repair
from the new runs.

## Local feature audit is a different study

The active **500-case sidewalk marker** draw concerns local visibility on
drivable ways lacking sidewalk-family keys, not an eligible routing-UNKNOWN
population. Preserve the exact-radius-v2 cases, known probabilities and seed
20260722. It does not identify route accessibility, whole-way continuity or
joint edge passability. Neither a marginal affirmative rate nor a binomial
interval can be interpolated into component statistics. The unchanged UI's
year/date/reason and generic reference also limit image-level reproducibility.
There is no route calibration or calibrated physical-accessibility estimate.

Do not redraw or regenerate the harness to inspect the frozen results. The
historical three-arm draw is superseded. The separate continuity study has its
own frame and does not repair model topology or turn this marker audit into
network validation.

The published source study concerns 100 Google Maps cases. No new OSM linkage or completed
follow-up experiment is asserted here.

## Changelog

- 2026-08-28: earlier in-repository design notes; external preregistration not established.
- 2026-09-15: retained configured inputs; deterministic unified evidence; point transitions;
  explicit access policies; actual core/threshold sensitivity; eligible versus auxiliary
  accounting; conditional terminology; removed linear audit calibration and unsupported
  benchmark/preregistration claims. Earlier numerical results require recomputation.
- 2026-10-07: documented frozen-model defects, eligible-mass headline, incomplete
  sensitivity execution linkage and active marker-audit limits; no recomputation.
- 2026-10-09: separately executed and documented corrected Greek, 20-country
  and three-profile results; preserved frozen outputs and historical caveats.
