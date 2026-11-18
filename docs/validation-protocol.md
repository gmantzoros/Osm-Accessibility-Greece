# Active local-feature audit protocol

This documents the retained active sidewalk draw and **actual unchanged UI**.
The 2026-09-15 amendments are repository protocol records, not independently
timestamped preregistration. Do not backdate decisions or observations.

## Active population and fixed draw

- **500 fresh primary cases, sidewalk arm only**: ten per principal LAU across
  50 dimos groups selected using the historical GISCO population field.
- `results\urban\scope_laus.json` selects the highest-field-population LAU of
  each selected dimos, **not the entire dimos** or an official urban boundary.
- The marker frame consists of **drivable ways without sidewalk-family keys**
  (`sidewalk`, `sidewalk:left`, `sidewalk:right`, `sidewalk:both`), including
  classes/conditions that do not coincide with eligible routing-UNKNOWN edges.
  It is **not an eligible-UNKNOWN calibration sample**.
- Seed **`20260722`**, frame **`exact-radius-v2`**. Per-LAU pool/draw sizes,
  inclusion probabilities, pool hash and Greek input hash are retained in
  the existing sample/seed metadata. The radius metadata does not mean the
  sidewalk observation is a route segment or whole-way survey.
- **50 outcome-independent blinded repeats**, not 105, with separate answers.
  **Withdrawn 2026-11-18** (see Status): no repeat annotations were made.

Preserve the actual sample, seed, all annotations, existing harness/HTML and
browser storage. **Do not redraw, substitute cases or regenerate the interface
to follow this documentation.** There is no active `--sample=4` recipe.
Corrected Java source labels any **future** canonically sorted member-aware
draw `exact-radius-v3-canonical-members`; it does not retroactively change
the fixed `exact-radius-v2` cases, their probabilities or their audit interface.

The preceding 1,050-case three-arm design, its 105 repeats and
`legacy-grid-retained` interpretation are **superseded historical designs**.
Commission/omission arms are withdrawn from the active study. Retain historical
files and answers without relabelling them as the fresh draw. Historical
unknown pool sizes must not be invented from national totals.

## Estimand and observable judgments

At the marker, record sidewalk **both / left / right /
present-side-unspecified / none / unverifiable** as supported by the permitted
observation. Left/right is relative to the mapped way direction; do not guess
when that direction or the visible side cannot be established.

This estimates local visible provision within the selected marker frame.
A sidewalk may be present but narrow, obstructed, discontinuous or unusable.
A spot observation cannot establish whole-way continuity, joint edge states,
destinations, accessible routes, acoustic operation or national campaign yield.
The Greek sample does not identify mapping/infrastructure causes in the
comparator countries. No local affirmative fraction may be linearly
interpolated into graph closures: topology and threshold effects are nonlinear.

## What the current UI actually records

The interface is intentionally **unchanged**:

| Captured/retained | Scientific limit |
|---|---|
| Sidewalk judgment | Marker observation only |
| Imagery **year** | Not month/day; a year is a capture-date interval |
| Audit date and save/update timestamps | Not imagery acquisition time |
| Unverifiable reason | Preserve missingness, do not substitute another case |
| Generic location/viewer link | Not a panorama/image-specific reference |
| Default/hidden source/reference fields | Not a properly captured provider choice or proof of source inspected |
| Empty/hidden notes field | Notes are **not properly captured** |

Do not claim that image identifiers, panorama URLs, capture month/day,
provider selection or notes have been collected merely because export columns
exist. Do not manufacture them from a generic Street View link or backfill them
from recollection. Existing backend checks may validate compatible fields;
they cannot recover imagery provenance never recorded by the interface.

Confirm the marker location in a permitted viewer. Record only what can be
supported; use **unverifiable** when imagery, date, location, side, resolution
or visibility is insufficient. The displayed/default imagery year is not
evidence: verify it rather than copying the previous case's year.
Still imagery cannot establish operation of an audible signal.

The limited provenance constrains reproducibility and contemporaneous
condition claims even after a complete export. Provider terms, privacy and
reuse permissions need separate evidence. No completed field reference or
independent second-auditor accuracy study is presumed.

## Preservation and repeated annotations

1. Work in the existing HTML and original browser; export primary/repeat
   answers regularly and retain versioned durable copies privately.
2. Browser storage is not backed up by copying HTML. Do not clear it, migrate
   keys, rebuild HTML or infer progress from filesystem inspection.
3. Finish and export **all 500 primary cases**. The repeat round must unlock
   **at least 14 days after the latest primary completion**, not after the first
   case or a partial batch.
4. Complete the **50** preselected blinded repeats without consulting the
   primary exports. Keep rounds separate and retain original timestamps.
5. Report paired verifiable counts and sidewalk-outcome agreement with missing
   pairs separately. Intra-rater repeatability is not correctness.

Case identity, coordinates, sample/input hash, frame metadata and rounds must
match the retained draw. Reject incompatible exports rather than silently
migrating legacy composite or mapped-state judgments into observed truth.
The task to strengthen compatible backend validation does not authorize an
interface or case change.

## Prespecified analysis and limits

Only if an actual primary CSV export is available, run the existing backend
with a justified reference date. The path below is a placeholder, **not** an
existing project file or an instruction to synthesize observations:

```powershell
python -B analysis\local_audit.py 'path\to\actual-primary-export.csv' --reference-date YYYY-MM-DD
```

This writes a derived JSON report to stdout and never changes the interface.
If the optional repeat export is genuinely available after the prescribed
delay, pass `--repeat-csv 'path\to\actual-repeat-export.csv'`; incomplete
repeats stay separately identified. The backend checks the entire canonical
500-primary-case frame, fixed identifiers/pool probabilities and prescribed
repeat IDs; it does not renormalize a truncated export or turn unfinished rows
into completed ones. Without an explicit primary export, the reporting bundle
uses `manual_audit.available=false`, `status=not_supplied` and no invented
manual rates. Preserve any actual exports privately; their absence is not
evidence of no sidewalks.

Report:

- allocated/completed/verifiable/recent-date counts by LAU and round;
- **equal-LAU** local-feature rates, and **feature-weighted** rates only using
  the retained, internally consistent pool sizes/probabilities;
- verified-only descriptive rates separately from conservative bounds that
  retain incomplete, unverifiable, stale and undated cases;
- conservative stratified uncertainty intervals and finite-population
  calculations where valid pool sizes are known, not a pooled binomial/Wilson
  interval pretending this is a nationwide independent sample;
- year-only dates conservatively: an interval crossing a recency cutoff is
  not definitely recent;
- imagery/observation error and missing image-specific references as
  limitations not eliminated by sampling intervals.

The frame is selected principal LAUs and marker spots, not rural/national
infrastructure, routing UNKNOWN geometry, national intervention targets or
whole-way continuity. Any separate continuity analysis has its own cases,
estimand and provenance and must not be silently merged with this one.

Status (2026-11-18): the primary round is **complete** (500/500). The export
is retained privately at `validation\audit_results_v2.csv` without repeat rows,
and the paper reports the full-frame estimates. **Amendment 2026-11-18:** the
50-case repeat round is withdrawn by the auditor's decision; no repeat
annotations exist, the repeat steps above no longer apply, and the paper
states that judgments are single-pass. Separate continuity analysis uses
`way-point-observations-v2`: raw agreement, temporal-eligible agreement and
conservative **sample three-point response** bounds, not yield/scaled gain,
population rates or an `upper95` route claim. Its CLI is:

```powershell
python -B analysis\continuity_audit.py validation\continuity_results.csv --reference-date 2026-09-15
```

It writes `results\analysis\continuity_audit.json`. Explicit reference date
2026-09-15 is the reporting choice; if omitted, the default is the latest
recorded audit date, **never today/mtime**. Maximum age is five years with
the existing conservative year-only cutoff policy. These operations do not
validate physical continuity between observation points or change the UI.

The retained point artifact reports **39** raw complete three-point sets,
**38** unanimous on presence, versus **35** temporally qualifying sets,
**34** unanimous. There are **116** qualifying point responses, **51**
unverifiable and **13** stale. These are descriptive counts, not a population
rate or a continuity confidence limit. The steps mismatch **W021** remains
in raw counts and is excluded from the task-aligned sample (**59 ways**).
Raw source annotations/sample remain unchanged. The analysis validates
fixed-sample SHA and every input identity, all three points and worklist
rank/class/pool/draw/probability. January-1 qualification for year-only images
is conservative at a past cutoff, not exact contemporaneity.

## Mapillary companion: existing screening, not calibration

Retained corrected outputs distinguish inventories, image-positive **tiles**,
unknown query status, temporal `last_seen_at` groups and 25 m proximity
associations. A tile with an image does not establish marker visibility.
Co-detection fractions/flags are not detector accuracy, distinct physical
omissions or lower bounds. Pedestrian-head associations at general signal
locations are not an acoustic-operation test.

Historical commission/omission overlaps are not an active arm. Any incidental
CV-positive link to the current sidewalk sample must bind its actual sample
hash and is not a new probability-sampled calibration design.

See `docs\data-sources.md` for existing metadata and acquisition gaps. This
protocol requires **no live API acquisition or cache regeneration**. Never
publish raw provider responses or annotations under the code's MIT licence.

## Permissions

Check applicable imagery/API terms, institutional requirements and law.
Manual inspection is not blanket authorization to retain or redistribute
images or derived annotations. Google observations must not generate OSM
edits. A later mapping intervention needs independently permitted evidence,
attribution and community-compliant verification. No ethics-approval,
participant-consent, funding or conflict fact is invented here.
