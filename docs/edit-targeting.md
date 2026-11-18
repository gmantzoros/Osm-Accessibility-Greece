# Conditional edit-target scenarios

**Revised 2026-09-15.** This is a modelling specification, not an externally registered
protocol or evidence of expected campaign yield. Pre-revision national results are not
results of the corrected graph.

The current corrected targeting worklists use `conditional-2026-10-09` and
remain separate from the archived `conditional-2026-09-15` worklists, whose
shared graph defects are not repaired. The scenarios below are implemented-model
results, not physical-accessibility bounds or certified improvements. The
corrected complete-prefix distance comparison is post-processing of the
newly executed saved worklists, not another census.

## Scenario and budget

Candidates are UNKNOWN, eligible ways whose unresolved primary cause is sidewalk,
crossing attributes or steps support. Auxiliary-class, separate-geometry, limited-access
and conditional-access UNKNOWN cases are not actionable candidates.

One candidate assumes the **entire selected way state becomes PASS**. Its valid consecutive
segments are added to exactly the same graph used for the baseline, including interior
junctions. Unresolved geometry gaps remain gaps. Node transition states are held fixed:
this is a conditional way-state intervention, not a simulation of every cascading change
to a unified crossing after an actual OSM tag edit. It does not override point barriers.
The corrected source derives crossing questions from the active profile's
unresolved conditions rather than always asking the BVI tactile/acoustic
questions under the wheelchair profile. The new BVI question/worklist hashes
are recorded separately from the archived versions.

All candidate outcomes are assumed positive. Negative answers improve information but do
not add PASS geometry. The local feature audit does not estimate this selected set's yield,
and no observed affirmative fraction is used to multiply the network gain.

Ways are not equal-cost tasks. Report selected geometry/survey kilometres with task count;
a marker-spot observation does not establish continuity along a long way. Way splitting
changes this budget and selection. The output is a survey-planning scenario, not an
estimate that a fixed number of clicks delivers a specified accessible network.

## Explicitly heuristic selection

The method is named `static_initial_consolidation`. Candidates are ranked once by their
initial connectivity-consolidation proxy, descending; OSM way ID breaks ties deterministically:

```
proxy(w) = own length + sum of touched passing component masses − largest touched mass
```

It is **not exact greedy**, not a submodular optimizer and has no approximation guarantee.
Component gains can increase after other edits, invalidating the stale-upper-bound premise
of the former lazy queue. The proxy is not marginal usable length. Restrictions or geometry
gaps can also prevent all touched components from coalescing; actual outcomes are always
computed from valid graph edges rather than inferred from this proxy.

An independent `length_only` ranking uses the same candidate universe and task count, with
the same deterministic tie-break and baseline graph. It reports its own selected survey
distance: equal task counts do not imply equal fieldwork effort.

An existing efficiency-oriented sequence is a further heuristic, not an
optimality claim. A distance comparison made from saved sequences must identify
the selected prefixes and distance budgets, avoid fractional/unobserved new
tasks, and remain conditional on the saved outcomes. It does not constitute
a new graph run, common-effort field experiment or measured campaign yield.

### Corrected saved complete-prefix distance comparison (Figure 9, `fig8_targeting_distance.png`)

`analysis\targeting_distance.py --versioned` reads the three **corrected ranked
worklists** from `results\impact\greece-full-strata-conditional-2026-10-09`,
checks the common corrected BVI/extended/1 km scenario and records SHA256
source identities in
`results\impact\unified-conditional-2026-10-09\analysis\targeting_distance.json`;
the companion CSV is beside it. The corrected plotted artifact was generated
at `results\impact\unified-conditional-2026-10-09\paper\figures` and copied
with its manuscript to `paper\figures\fig8_targeting_distance.png`. The
archived `results\analysis\targeting_distance.*` remains a frozen-model
comparison, not another graph run or a corrected source.

The main `edit_targets.csv` has **per-row** `survey_km`, which must be summed
over a prefix. The length-first and score-per-km worklists have **already
cumulative** `cumulative_survey_km`. For each budget, take the longest
complete ranked prefix within the recorded distance, without selecting a
fractional way, skipping an unaffordable next way, re-ranking or extending a
shorter curve past its last saved task.

At the corrected common maximum budget of **486.870 km** (the recorded endpoint
of the score-per-km sequence):

| Saved ordering | Tasks in prefix | Used survey distance (km) | Conditional eligible usable gain (km) |
|---|---:|---:|---:|
| Static initial consolidation | 28 | 479.339 | 489.634 |
| Length only | 21 | 474.787 | 481.094 |
| Consolidation score per km | 5,000 | 486.870 | 1,237.995 |

The first two 5,000-way endpoints use **30,907.910 km** (sum of individually
rounded main rows; the summary gives 30,907.915) and **31,273.003 km**,
respectively, and are **outside** this common distance range. Equal budget
does not mean equal tasks, exactly equal used kilometres, equal time or equal
fieldwork difficulty. Source distances/gains are rounded to three decimals;
zero-rounded distance steps are not free surveys, and boundary membership may
change at unrounded precision. A prefix's recovered pre-existing usable mass
cannot be computed by subtracting selected/survey length from its gain because
selected length may not itself meet the eligible-component threshold.

## Outcome

The primary scenario outcome is eligible PASS length in components whose **eligible**
length is at least the profile threshold. For every selected way the code measures
`U_after − U_before`; these increments telescope to the final minus initial outcome.
Existing passing component length is never counted as newly usable simply because two
already-usable components merge.

`unlocked_km` and `cumulative_km` are retained compatibility columns for a consolidation
diagnostic. Their cumulative sum can re-credit component mass and depend on selection
order; it is **not distinct kilometres recovered** and must not substitute for the
measured usable delta.

## Files

`edit_targets.csv` retains rank, way ID/class, question, stratum, coordinates, own length,
diagnostic score, diagnostic cumulative score and OSM link. Added fields:

- `eligible_own_km`, `survey_km`: selected whole-way geometry, not an effort estimate;
- `marginal_usable_km`, `cumulative_usable_gain_km`, `usable_km_after`: measured outcomes;
- `selection_method`, `model_version`, `intervention_model`: explicit provenance; the last
  is `way_state_override_fixed_node_transitions`.

`edit_targets_length_baseline.csv` reports the independent length-only sequence, actual
marginal/cumulative usable change and cumulative survey distance.

`edit_targets_summary.csv` retains prior columns but uses corrected graph evaluation.
Added columns identify the method, selected/survey distance, length-baseline before/after/
gain/distance, graph scope, threshold and model revision. Legacy `guaranteed_*` column names
mean model-admitted PASS length, **not physical guarantees**. A zero baseline produces an
undefined/blank multiple, not a zero or infinite claim.

`selected_usable_km_after` traverses selected edges in the final components;
`recovered_preexisting_usable_km` is actual usable gain minus that credited selected length.
The analogous `length_baseline_selected_usable_km_after` and
`length_baseline_recovered_preexisting_usable_km` describe the comparator. Do **not**
subtract raw selected/survey length to infer recovered pre-existing network: portions of
selected ways can remain below the component threshold.

The implemented CLI accepts `--targets=N` and a profile;
`--graph-scope=core` selects that universe. Targets use the profile's default
component threshold. These are option definitions, **not instructions to
replace the frozen Greek results**. Use isolated, versioned output paths for
any intentional recomputation.

## Scientific limitations

- No actual-safety, expected-yield, optimality, population-demand or destination-utility claim.
- Whole-way segmentation and survey distance matter; costs are not calibrated.
- Point-transition evidence is fixed in the intervention scenario.
- Profile defaults and unknown auxiliary connectivity remain modelling choices.
- The active 500-case sidewalk marker audit has a drivable-way key-absence
  frame, not the actionable routing-UNKNOWN universe. It cannot calibrate
  national, predominantly long-way interventions or the nonlinear objective.
- The unchanged audit UI does not properly capture provider, notes or
  image-specific references; no image provenance or whole-way cost is inferred.
- Neither assumed desk minutes nor this all-positive scenario supports a
  person-month or guaranteed safe-network claim.

## Changelog

- 2026-08-28: initial design and first national exploratory run, recorded on the same date.
- 2026-09-15: replaced unsupported lazy-greedy interpretation; fixed interior attachments
  and geometry gaps; added actual marginal outcomes, same-universe length baseline and
  survey-distance reporting. Removed expected-yield and prior numerical campaign claims.
- 2026-10-07: clarified frozen-model/saved-comparator limits and active marker
  frame; no source, scenario result or model revision changed.
- 2026-10-09: reported the separate corrected Greek targeting worklists and
  complete-prefix comparison; preserved the frozen comparison unchanged.
