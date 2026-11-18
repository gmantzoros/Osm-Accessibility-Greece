# Data Sources

Retained census inputs, declared sources and known provenance/rights limits.
A declared provider URL is not proof of the actual acquisition transaction or
permission to redistribute every derived output.

## Retained inputs and declared acquisition locations

`data\fetch_data.ps1` now defaults to **offline verification**, not acquisition.
It preserves existing snapshot/provenance sidecars. Restore only verified
matching bytes from an explicit backup/URL; intentional new snapshots require
a distinct empty directory and explicit acknowledgement. The URLs below are
declared acquisition locations, not retrospective proof of every original
download.

| Dataset | Use | Source | Licence |
|---|---|---|---|
| `greece-latest.osm.pbf` | The census itself | Geofabrik | ODbL — © OpenStreetMap contributors |
| `LAU_RG_01M_2021_4326.geojson` | LAU/local-community polygons + historical `POP_2021` field + `AREA_KM2` | Eurostat GISCO | Free reuse with attribution (© EuroGeographics for administrative boundaries) |
| `NUTS_RG_01M_2021_4326_LEVL_2.geojson` | 13 regions | Eurostat GISCO | as above |
| `NUTS_RG_01M_2021_4326_LEVL_3.geojson` | 52 regional units + official `URBN_TYPE` | Eurostat GISCO | as above |

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File data\fetch_data.ps1
```

The immutable Greek PBF has OSMHeader timestamp
**2026-08-26T20:22:15+00:00**, downloaded **2026-08-28**, SHA256
`7f973ac69ac39e8a91999e49342de1d9986b70017ca41bc1f5107f573fa101b0`,
MD5 `2eaed090e58af6ad0aa664097f1b80aa`. Its current bytes match those
records. `data\SNAPSHOT.txt` and the existing provenance JSON remain unchanged.
The OSMHeader timestamp, not download time or filename, identifies extract time.

`data\external\AUX_SNAPSHOT.txt` is a **2026-10-07 current-byte inventory** with
observable SHA256/size and **candidate restoration URLs**. Original auxiliary
download dates, source responses and upstream checksum records are missing.
Its creation does not prove original acquisition from GISCO or population
reference years. See `docs\reproducibility.md` for restoration limitations.

### Population provenance

The GISCO layer's **2021 boundary label is not an established population observation
year**. The retained `POP_2021` field (CSV alias `pop_2021`) sums to **10,816,286**
for Greece and gives **664,046** for Athens. Both match ELSTAT's revised **2011**
resident census, not the official 2021 results (**10,482,487** and **643,452**).
These matches do not establish the provenance of every individual LAU value.

Sources: [ELSTAT 2011 census](https://www.statistics.gr/2011-census-pop-hous),
[revised 2011 Gazette B 698/2014](https://www.statistics.gr/documents/20181/1210503/FEK_monimos_rev.pdf/125204a0-726f-46fe-a141-302d9e7a38dc),
and [2021 Gazette B 2090/2024](https://www.statistics.gr/documents/20181/17286366/FEK-2024-Tefxos-B-02090.pdf/d4daffb1-7210-a172-9729-52d4771ae785).

Outputs therefore describe **GISCO population-field-weighted shares**, not current
or established 2021 population exposure. Country covariates have mixed/unverified
reference years. A future update requires a complete population-to-boundary
crosswalk and recomputation of spatial numerators: replacing only the national
denominator is invalid. `exposure_metadata.json` reports included population-field
sums and excluded LAUs with no modelled network, missing feasibility, or
nonpositive population. Residential location is not individual accessibility.

### Country snapshot and execution provenance

`analysis\cross_country.py` fingerprints existing cached PBF bytes (MD5 and SHA256)
and decodes the OSMHeader replication timestamp, sequence, source and writing
program. A `-latest` name or file modification time is not a snapshot date.
`*.osm.pbf.provenance.json` records the exact download URL when known; old caches
without a verified record retain **original source unverified** rather than being
attributed to a mirror retrospectively. Inputs are not redownloaded solely
because the cache lacks a manifest.
As of 9 October 2026, **19 of the 20** retained country input records still
have no verified original download URL. Their OSMHeader replication URLs identify
update feeds, not the transactions that supplied these cached PBFs.

Successful country runs record `run_manifest.json`, containing hashes of the
input, JAR, profile, strata, **all main Java sources and both
`cross_country.py` / `profile_sensitivity.py` drivers**, and outputs, plus command and
execution time. Results are reused only when these fingerprints agree.
`--report` never starts a census and excludes missing/stale manifests, but
**rewrites master aggregate outputs** from accepted selected runs. It is not
a read-only inspection command. `--countries greece` also rewrites those
master outputs and can leave a Greece-only comparison; do not run it blindly.
`--fingerprint-only` records cached inputs without running the national analysis.
Historical country runs were sequential; the isolated corrected driver accepts
a bounded `--threads` setting and records each completed run individually.
Snapshot dates can differ between countries and must be reported individually.

Cached input records are also written immutably to
`results\countries\input_manifests\<country>\<manifest-sha256>.json`.
Existing records are never overwritten; their exact bytes are verified on reuse.
The frozen `results\countries\input_provenance.json` is its historical index,
containing each manifest path/hash, actual PBF MD5/SHA256 and OSMHeader
timestamp. The corrected comparison has a separate, complete 20-country
index at `results\impact\unified-conditional-2026-10-09\countries\input_provenance.json`.
These are **input provenance**; the separate completed execution manifests
establish the corrected runs. An original download URL is adopted from a
legacy snapshot record only when its checksum matches the cached bytes;
otherwise origin remains explicitly unverified.

The profile driver can reconstruct explicit scenarios sequentially using
native `--component-thresholds` / `--core-sensitivity`; this is **not a request
to rerun the frozen study**. Retained artifacts cover three profiles plus
native 0.5/1/2 km extended/core scenarios, **not a full factorial**. Available
country/profile manifests match the frozen execution sources/JAR, **not** the
subsequently corrected `conditional-2026-10-09` Java source. The three
corrected default extended/1 km profile executions have their own complete
manifests under
`results\impact\unified-conditional-2026-10-09\profiles`; the corrected
20-country executions are under the sibling `countries` directory. Separate
**archived** native threshold/scope execution linkage remains **unverified**;
retain that status rather than writing a retrospective run manifest. Frozen
profiles, commands and available manifests remain below
`results\profile_sensitivity`.
Core scenarios reconstruct the graph without auxiliary classes; thresholded
eligible-core length is reported separately from all graph length.
**Primary usable shares** divide `pess_eligible_usable_km` and
`opt_eligible_usable_km` by `eligible_km`. The component threshold also uses
eligible/nonauxiliary mass: auxiliary edges can connect eligible segments in the
extended graph but cannot by themselves promote a short eligible stub above the
threshold. Analysis and bundle `pess/opt/gap` endpoints use this primary outcome;
legacy total-component-mass results are retained as `all_length_*` diagnostics.
Exposure and selected-dimos summaries exclude undefined zero-eligible network
shares rather than treating them as zero. Way-state percentages retain their
all-graph length denominator; zero-length node-transition uncertainty is separate.
`feasibility_sensitivity.csv` is also ingested directly into the paper bundle.
`--report` only accepts matching
cached results. Both endpoints, state composition, scope and threshold are
exported; no arithmetic pseudo-sensitivity substitutes for a missing run.

The separately staged **Greece-only** `conditional-2026-10-09` full-strata
execution has its actual command, input/JAR/Java-source SHA256 values, start/end
times and fourteen output SHA256 values in
`results\impact\greece-full-strata-conditional-2026-10-09\run_manifest.json`.
`analysis\corrected_model_impact.py` validates that record, the current
outputs and the frozen Greek baseline before producing a small comparison.
Its Greek national endpoints agree with the separately executed corrected
Greece-country and BVI-profile outputs. The other 19 cached countries and
three Greek default profiles also have separately versioned corrected
executions; none of this establishes execution details for the **older**
native threshold/scope files or repairs missing historical input URLs.

The separate `way-point-observations-v2` report records raw versus temporally
qualifying **sample-point** summaries and input/code hashes, not whole-way
continuity or population/network yield. Without a real, compatible primary
audit export, manuscript data records `manual_audit.available=false` and no
feature rates. `analysis\targeting_distance.py --versioned` post-processes
the three corrected saved worklists into complete-prefix comparisons with
SHA256-linked sources and a rounded common survey-distance range; Figure 9 (`fig8_targeting_distance.png`)
is a plot of those conditional saved outcomes, not a new census or measured
field campaign.
See `docs\validation-protocol.md` and `docs\edit-targeting.md`.

The completed corrected reporting run used `--versioned` in this order:
`aggregate.py`, `exposure.py`, `urban_scope.py`, `campaign.py`,
`targeting_distance.py`, `paper_data.py`, both figure scripts and
`paper\build_paper.js`. Default modes are **not** an instruction to replace
frozen outputs. `python analysis\validate.py --self-test` runs small
read-only numerical regressions without changing census outputs.

### Record and index definitions

Signal-node sound statements (`signal_sound`, `signal_sound_yes`) and unified
crossing-feature statements (`crossing_sound`, `crossing_sound_yes`) use different,
potentially overlapping universes. Report stated versus affirmative evidence
separately; do not add these counts into a physical acoustic-signal inventory.

BPRI v0 uses the raw node-only `legacy_crossing_nodes`, `legacy_crossing_subtype`
and `legacy_crossing_tactile` counters. BPRI v1 uses unified crossing features and
also changes components/weights; a v1−v0 difference is not a pure schema ablation.
The generated bundle supplies exact formulas, denominators and weights.
Historical v0 results must be recomputed with the corrected node-only definition.

The manuscript interface exposes `national.junctions` as an alias of
`junctions_degree3`, and `bpri_definition.weights` alongside the version-specific
weight dictionaries. `sensitivity_runs` contains national rows with `scope`,
`profile`, `threshold_km`, primary endpoints and `data_freshness`; `core_graph_run`
is the actual core-scope row matching the main profile/threshold, or null if absent.
Freshness distinguishes a hashed artifact from a verified execution manifest.
Missing execution/input linkage is explicit; no file-modification date is used as
a snapshot or execution date.

Destination metrics count OSM node/way records, not deduplicated facilities.
Station/halt records include `public_transport=station`; platforms remain in the
stop class. Direct tactile-tag absence on a station record says nothing about
platforms, entrances, the station complex or physical tactile infrastructure.

## Manual (documented, not automated)

| Dataset | Why manual | What to do |
|---|---|---|
| Official Eurostat DEGURBA per LAU | Portal download, no stable direct URL | Save as `data/external/degurba.csv` with columns `lau_id,degurba`; `prepare_boundaries.py` picks it up and flips `degurba_source` to `official`. Until then a documented density proxy is used (`docs/strata.md`). |
| Geofabrik full-history extract | Requires an OSM account | Only needed for RQ3 (edit dynamics, contributor concentration), which is explicitly severable. |
| ELSTAT 2021 census tables | Portal, per-table downloads | Required with a complete spatial crosswalk before claiming synchronized 2021 population weighting. |
| Mapillary imagery and detection metadata | API key, per-request | Automated co-detection screening uses live API queries and cached metadata; manual audit uses links. Record query time, capture/last-seen dates and licence terms separately. |

## Imagery and licensing

The local feature-audit protocol is in `docs/validation-protocol.md`:

1. **Mapillary** — imagery and metadata subject to the applicable imagery/API terms.
2. **Google Street View** — secondary, **manual in-browser inspection only**. Google's terms
   constrain scraping, derived data and redistribution. Manual research inspection does
   not itself establish permission to retain, publish or redistribute imagery or
   annotations. Check applicable terms for each use. Do not derive OSM edits from Google.
3. **Field observations**, if obtained, must be identified separately; no completed
   field-validation sample is presumed. Acoustic operation needs functional evidence.

Report actual imagery eligibility and unverifiable rates. Tile coverage does not
establish that an individual feature is visible. Co-detections and candidate
omission flags are not ground truth, mathematical lower bounds or distinct missing
crossing counts. Poles, benches and bins are object inventories, not verified hazards.

Corrected period-specific screening is exported as
`results\mapillary\co_detection_by_city.csv`, with provenance in
`co_detection_manifest.json`. Earlier v2 exports used
`co_detection_by_city_periods.csv` plus an all-date compatibility view in the
canonical filename; the loader supports that format but prefers a full temporal
canonical file over retained older exports.
legacy `validation_by_city.csv` is retained unchanged. The bundle prefers the new
artifact, exposes all-date and recent-last-seen counts separately, and explicitly
deprecates old `testable`, `confirmed` and `omission_rate` identifiers. Their
compatibility values refer to **all-date screening**, not visibility or accuracy.
Recent detections use the same image-positive-tile OSM denominators; this does
not establish recent image coverage. Crossing and pedestrian-head co-detection
denominators subtract the respective unknown feature-query counts from their
image-positive-tile populations; the OSM-negative junction denominator already
excludes unknown queries and must not be reduced twice. Missing query-status
metadata is not interpreted as zero failed/unknown queries.
The corrected coarse-grid description uses `image_coverage_v2.csv`,
not the retained legacy coverage file. `inventory_by_scope.csv` is a separate
scope-assigned object inventory, without population or network-length denominators.
`local_audit_cv_links.csv` records incidental CV-positive audit cases. Primary
local comparisons require commission/omission membership, image-positive tile
status and a CV-positive crossing association; the sample CSV hash must match
before counts are attributed to the current audit. These are not a new random
sample and cannot calibrate network connectivity.

`analysis\mapillary_radius_sensitivity.py` also uses the retained clipped
OSM/CV point exports and image/feature caches without any network request. It
verifies that their 25 m replay agrees exactly with the saved screening, then
reports 10/25/50 m comparisons on one candidate population more than 51 m
inside the selected polygons. `results\analysis\mapillary_radius_sensitivity.json`
and `.csv` link source SHA-256 hashes and give both original and common-interior
counts. Holding this interior frame fixed removes boundary-dependent missing
neighbour searches from the **subset**, not the full-polygon results: the
clipped exports cannot supply a complete external 25 m OSM/CV halo. Changing
the radius also changes which junctions are OSM-negative, so flag-rate
denominators change even though the candidate locations do not.

## Licensing of the outputs

- **OSM-derived databases**: observe ODbL attribution/share-alike requirements, ©
  OpenStreetMap contributors. Do not assign one blanket licence to all `results/`:
  Mapillary metadata, imagery and annotations require separate rights assessment.
- **Original project code**: **MIT**, as scoped in root `LICENSE`.
  Dependencies and bundled upstream code retain their own licences; no
  dependency-rich JAR is included in the default local package.
- Boundary attribution line for any map figure:
  *Administrative boundaries © EuroGeographics / Eurostat GISCO; road data © OpenStreetMap
  contributors (ODbL).*

The existing Mapillary example figure is credited **nglf, CC BY-SA 4.0**.
That specific credit does not establish rights for other provider assets,
API caches or derived annotations. See `docs\licensing.md`. The local archive
does not create a blanket redistribution licence or publication-ready dataset.
