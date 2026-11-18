# Frozen-input preservation and local working package

## Provenance is not a filename or a packaging date

The Greek PBF's retained OSMHeader timestamp is
**2026-08-26T20:22:15+00:00**, recorded download **2026-08-28**,
339,776,310 bytes, SHA256
`7f973ac69ac39e8a91999e49342de1d9986b70017ca41bc1f5107f573fa101b0`,
MD5 `2eaed090e58af6ad0aa664097f1b80aa`. Actual retained bytes match.
`data\SNAPSHOT.txt`, checksum sidecar and provenance JSON are preserved.
A volatile `-latest` URL cannot recover that snapshot by assumption.

Country input manifests are content-addressed records of retained bytes.
Unknown original download URLs/times remain unknown: 19 of 20 country inputs
have no verified original URL, and an OSMHeader replication feed is not a
download receipt. Archived country/profile execution manifests match the
**frozen execution** and fingerprint **both drivers plus all main Java
sources at that time**, inputs, profiles, strata/assets, JAR and outputs.
The subsequently corrected Java source (`conditional-2026-10-09`) differs;
the archived country/profile runs are not corrected-model executions. All
20 cached countries and three Greek default profiles have **separate
completed corrected execution manifests** under
`results\impact\unified-conditional-2026-10-09`, without altering the
archived results or input-provenance sidecars.
Separate native threshold/scope execution linkage remains **unverified**.
Do not create retrospective manifests or bypass fingerprints to label those
artifacts verified. Current observed hashes are not original acquisition facts.

`data\external\AUX_SNAPSHOT.txt` was created as a **2026-10-07 current-byte
inventory**. It records size/SHA256 and candidate GISCO restoration URLs; original
acquisition dates/provider responses/upstream checksums are unavailable.
It neither establishes the historic URLs actually used nor makes `POP_2021`
a verified population vintage.

## Fetch modes (PowerShell)

From the repository root:

```powershell
# Default: offline verify only. Existing records are not overwritten.
powershell -NoProfile -ExecutionPolicy Bypass -File data\fetch_data.ps1

# Explicitly verify the current auxiliary inventory; still no network.
powershell -NoProfile -ExecutionPolicy Bypass -File data\fetch_data.ps1 -Aux
```

`-Smoke` selects Malta without ever writing the Greek `SNAPSHOT.txt`.
`-All` verifies the selected PBF and auxiliary assets; it is no longer a
download-everything shortcut. Missing records, inconsistent checksum formats,
missing bytes and mismatches are errors, never success warnings. Offline Verify
rejects acquisition options rather than silently ignoring an explicit URL.
Verify/Restore use the retained auxiliary inventory; `-AuxManifestPath` is
only accepted for a separate new-snapshot Update.

### Verified restoration, not a new snapshot

```powershell
# Example: copy a trusted backup ONLY if restoration is needed and all retained
# checksum/size expectations match. A verified existing input is left intact.
powershell -NoProfile -ExecutionPolicy Bypass -File data\fetch_data.ps1 `
  -Mode Restore -RestoreFrom 'C:\trusted-backup\greece-latest.osm.pbf'
```

An explicit `-SourceUrl` may be used instead of `-RestoreFrom` when a real
historical location is known; **no historical URL is supplied or invented
here**. A present verified input is not downloaded again. Staged bytes must
match every available recorded digest/size before targeted atomic replacement;
snapshot/checksum/provenance records remain unchanged on success or failure.
SHA256 is checked where retained; actual older MD5/size snapshot records and
MD5 sidecars are also supported, with conflicts failing closed.

Auxiliary restore requires `-Aux -Mode Restore -UseRecordedAuxiliaryUrls`
and the retained checksum inventory. These URLs are declared candidates, not
proved original acquisition sources; changed upstream bytes are rejected.
`-SourceUrl`/`-RestoreFrom` are **PBF** options and cannot be passed with
`-Aux` alone. A failed checksum/download leaves the prior target and retained
records unchanged; each successful replacement is atomic per file, not an
all-files transaction.

### Intentionally different snapshot

`-Mode Update` is a separate operation. It requires
`-ConfirmNewSnapshot`, an explicitly selected **distinct empty**
`-DataDirectory`, an explicit PBF URL and externally verified
`-ExpectedSha256` (optional additional MD5/size). It **cannot overwrite the
frozen data directory or an existing snapshot destination**. Example syntax,
with deliberately invalid placeholders rather than a runnable replacement:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File data\fetch_data.ps1 `
  -Mode Update -ConfirmNewSnapshot `
  -DataDirectory 'data\new-snapshots\candidate-YYYY-MM-DD' `
  -SourceUrl 'https://example.invalid/explicit-new-extract.osm.pbf' `
  -ExpectedSha256 'REPLACE_WITH_VERIFIED_64_HEX_DIGEST'
```

New-snapshot acquisition time is recorded as acquisition time only. OSMHeader
time is **not invented** from the URL/clock; new input/header provenance needs
separate verification before any scientific recomputation.
Auxiliary updates use an explicitly supplied checksum manifest
(`-AuxManifestPath`) in a separate empty destination, with SHA256 per asset.
PBF and auxiliary updates are separate; `-All -Mode Update` is refused.
URLs with embedded credentials or fragments are refused. Incorrect supplied
hashes fail without installing any new-snapshot bytes.

Never execute these acquisition operations against the frozen project merely
to inspect results. This maintenance task uses **only mocked downloads in
project-local fixture directories**, not actual project inputs or live APIs.

## Drivers are not harmless viewing commands

The default country/profile drivers can start census jobs. Country `--report`
**never invokes census**, but excludes stale/missing manifests and rewrites
master aggregate tables. `--countries greece` restricts selected inputs, **not**
the output destination: master tables can become Greece-only. Profile report
mode also checks cached scenarios and rewrites its comparison. Fingerprint-only
mode writes input records. Do not run any of these blindly or retrofit
fingerprints. Source/JAR rebuilding, boundary preparation, new draws and
live-provider acquisition are outside the frozen maintenance workflow.
A corrected-model impact run, if performed, must build separately from the
protected JAR and write to a distinct versioned directory.

### Recorded corrected Greek run

The completed Greece-only `conditional-2026-10-09` run used the exact frozen
Greek PBF, `data\strata.json`, `data\profile-bvi.json`, an isolated Maven-built
JAR, BVI extended graph, 5,000 conditional targets, and real extended/core
reconstruction at 0.5/1/2 km. The execution record under
`results\impact\greece-full-strata-conditional-2026-10-09` captures its actual
command, UTC start/end, source, executable, input and output hashes. That
directory is distinct from immutable `results\census` and `results\countries`.
It does not establish the execution history of the **frozen** native
threshold/scope artifact. The rounded national endpoint remains
7.17%/96.43%; exact shifts are -0.0008/-0.0006 percentage points. Its
national endpoint agrees with the new corrected Greece-country and
BVI-profile executions.

`python -B analysis\corrected_model_impact.py` is a cached, source-checked
comparison: it does **not** invoke Java or fetch data. To repeat the census
deliberately in a **fresh** workspace with the pinned inputs, copy
`census\pom.xml` and `census\src` to a separate build directory; stage
`data\profile-*.json` and `data\strata*.json` in its sibling `data` directory
for the existing Maven tests. Build there with
`mvn -f <staged-pom.xml> package`, then run
`python -B analysis\run_corrected_greece.py --jar <staged-target\census.jar>`.
The runner refuses an existing
versioned output path, verifies staged-source/configuration and frozen input
hashes, and writes a complete manifest only after successful execution.
**Never** use `mvn package` in the original `census` directory: doing so would
replace the protected JAR. A source-only GitHub checkout omits the pinned PBF,
geometry and output tables and cannot run this comparison on its own.

### Completed isolated corrected country and profile runs

The country and profile drivers require **both** `--jar` and `--output` for the
corrected study. They verify that the JAR comes from a separate build of the
current Java sources and descriptor, refuse the protected JAR and any output
outside the designated `results\impact\unified-conditional-2026-10-09` tree,
and use only existing cached PBFs. In this mode they read existing provenance
sidecars without rewriting them; country input manifests and execution results
go only to the new output tree. An absent country extract is a failure, not a
reason to fetch a mutable `-latest` snapshot.

```powershell
$jar = 'C:\path\to\separate\census\target\census.jar'
# Recheck completed manifests and refresh only the isolated aggregates; no Java execution:
python -B analysis\cross_country.py --jar $jar --output results\impact\unified-conditional-2026-10-09\countries --report --threads 2
python -B analysis\profile_sensitivity.py --jar $jar --output results\impact\unified-conditional-2026-10-09\profiles --report
```

Each completed run records the actual executable, inputs, source-driver
fingerprints, command and output hashes. A restricted `--countries` or
`--profiles` selection can prepare individual runs but does **not** replace the
full corrected comparison aggregate; all twenty countries or all three
profiles must pass before their respective aggregate is published. All
20 country runs and three Greek profiles **have completed**; the corrected
country and profile aggregates have 20 and 24 rows, respectively. A full
country rerun at 8 GB heap is **not** a reliable recipe: Spain needed a
separate 12 GB retry, Italy a separate 13 GB retry on the local machine.
Avoid concurrent large Java processes. Preserve
the old `results\countries` and `results\profile_sensitivity` trees and their
frozen manifests. Neither new execution nor copying its hashes establishes
missing historical country acquisition URLs or the frozen native
threshold/scope execution command.

### Corrected downstream reporting

After the completed corrected country/profile aggregates and the separately
recorded full-strata Greek BVI run are available, the reporting pipeline is:

```powershell
python -B analysis\aggregate.py --versioned
python -B analysis\exposure.py --versioned
python -B analysis\urban_scope.py --versioned
python -B analysis\campaign.py --versioned
python -B analysis\targeting_distance.py --versioned
python -B analysis\paper_data.py --versioned --audit-results validation\audit_results_v2.csv
python -B analysis\figures.py --versioned
python -B analysis\figures_explain.py --versioned
node paper\build_paper.js --versioned
```

These commands write under
`results\impact\unified-conditional-2026-10-09`, **not** the archived
`results\analysis`, `results\urban`, `results\campaign` or reporting bundle.
Default modes are not safe substitutes. The corrected principal-community
selection matches the frozen audit scope exactly; the independent audit,
point-pilot and imagery analyses remain labelled historical evidence. Once
the versioned Markdown, DOCX and **all matching figures** (nine with the
sidewalk-audit Figure `fig9_sidewalk_audit.png`) have been validated together,
copy them as a unit into `paper`; the versioned builder does not overwrite that
working directory. No audit observations are created by these commands; the
`--audit-results` export is the private, manually annotated file. Omitting it
reports the audit as pending.

### Cached spatial sensitivity

`python -B analysis\mapillary_radius_sensitivity.py` uses existing clipped
OSM/CV point exports, cached image/feature probes and the retained principal-LAU
polygons. It first checks exact agreement with the saved 25 m screening and its
input SHA-256 hashes. It then writes JSON/CSV at 10, 25 and 50 m on a fixed
candidate population at least 51 m inside polygon boundaries. No API, census
or manual observation is invoked. The subset cannot establish a complete
external-neighbour halo or recover original legacy-cache query provenance;
the original full-polygon screening remains separately identified.

## Package contents and exclusions

The existing local working ZIP is a **historical frozen-model package**.
`tools\build_release.py` deliberately refuses a corrected manuscript paired
with the frozen `results\paper_data.json` and has no corrected-release mode;
do not use it to build a new ZIP from the current paper. In its historical
mode the helper has a fixed, explicit allowlist: original source/build descriptors,
profiles/strata configuration (not geometry assets), relevant documentation,
final `paper\paper.md`, the generated
`paper\Greece-OSM-Accessibility-Routing-Feasibility.docx`, all eight
explicitly named `paper\figures` assets (Figures 1–8, including
`fig8_targeting_distance.png`), `results\paper_data.json`, bounded
census/analysis/country/profile summaries
and retained run/input manifests. Each selected file is read unchanged, and
the archive has a **SHA256 payload manifest**, release metadata and limitations.
Its assembly time is not a result-computation or image-acquisition timestamp.
Verification checks the complete member inventory and payload hashes without
extracting files or trusting archive paths. The saved-distance JSON/CSV and
Figure 8 (`paper\figures\fig8_targeting_distance.png`) must occur together:
the helper checks all four source-worklist/summary SHA256 values and CSV/JSON
prefix concordance on creation **and** verification. It rejects the old
point-observation schema or a reporting bundle without an explicit manual-audit
availability/status. The bundled saved comparison and point report must match
their standalone artifacts; when an optional manual result is reported, its
derived report must match, and an orphaned report is refused when no export
was supplied. Raw manual exports are never included or independently validated
by packaging. The cached Mapillary radius JSON/CSV must also match each other
and the reporting bundle; included source-code and LAU-scope hashes are checked.
Its raw detection/OSM points and boundary geometry are intentionally omitted,
so archive verification **cannot reproduce** the comparison or establish
original provider-query completeness. The Greece-only corrected-model impact
JSON/CSV must likewise match each other and the reporting bundle; included
frozen baseline, configuration and analysis-source hashes are checked. The
separate corrected output tables, build-stage JAR and raw PBF are omitted:
that historical aggregate is an inspectable **comparison**, not an independently
re-executable study deposit. This allowlist does **not** include the new
corrected 20-country, profile or geographic outputs.

Defaults limit each file to **16 MiB** and the total payload to **256 MiB**.
An allowlisted oversized file is explicitly omitted in metadata; limits are
not a licence to include unlisted data. Raw PBFs, boundary geometry, all JARs,
`.git`, installed dependencies, environment/API credentials, raw imagery/API
caches, audit HTML/exports and sample draws, raw point-response CSV, browser
storage, private notes and million-row cache families are never included.
Derived point reports and the paper bundle can still include **identifiable
way-level exception summaries**; inspect those before any external sharing.
The credited Mapillary image (Figure 2, `fig6_imagery_validation.jpg`) remains in the working paper; its terms are
separate from the code licence. There is no unrestricted inclusion flag.
Links/junctions, paths escaping scope, credential-bearing allowed text and
raw private audit records fail closed. Outputs are staged beside the target
and atomically finalized without replacing an existing archive.

The package includes no executable dependencies and is **not self-contained**.
Exact expected PBF/JAR hashes come from retained records; the omitted executable
still needs version-specific third-party licences/notices before distribution.
Auxiliary restoration depends on possessing hash-matching original bytes;
declared current URLs might no longer serve them. API caches and imagery
provenance cannot be reconstructed from aggregate tables. Because the point
observations/sample are withheld, the included point counts cannot be
recomputed from **only** the package; the package likewise omits raw manual
exports and cannot independently reproduce any later optional audit result.
The saved Figure 8 is a rendered artifact, not a verified historical plotting
execution; matching worklist hashes do not prove pixel-generation provenance.

Frozen `conditional-2026-09-15` results remain preserved implemented-model
outcomes, with shared graph defects uncorrected. The separate corrected
`conditional-2026-10-09` study is also conditional, not a guarantee of physical
accessibility. Three default profiles and Greek BVI native scope/threshold
artifacts are not a full factorial; **archived** native execution linkage is
not repaired by new execution or packaging. The unchanged marker audit does
not supply image-specific provenance or route calibration.
Data/media/dependency rights are separate from MIT code; see
`docs\licensing.md`.

### Isolated validation

```powershell
python -B -m unittest validation.test_fetch_data validation.test_release_package
```

Tests use existing `unittest`, PowerShell AST parsing and small mocked
downloads/fixture archives below `validation`, with targeted cleanup.
No real census, Maven build, input preparation or network call is made.

## Missing release facts

- Original auxiliary acquisition dates, source responses and upstream checksum
  evidence; per-LAU population reference provenance/crosswalk.
- Retrospectively unverifiable original sources for some country caches.
- Execution linkage for the native threshold/scope artifact.
- Properly captured per-image/provider/panorama evidence for the existing UI.
- Complete transitive executable licence/notice/source-offer audit.
- Any author roles, funding/conflicts, ethics/consent or publication identifiers
  not independently supplied and supported.

The archive is a **local working package**, not an overall submission-ready or
publicly licensed deposit. No DOI, commit, upload or publication is created.
