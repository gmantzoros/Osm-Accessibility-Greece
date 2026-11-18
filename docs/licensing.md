# Licensing and third-party rights

The root `LICENSE` resolves the repository's earlier MIT code claim.
**MIT covers original project code and its associated code documentation
only**, using project contributors as the copyright attribution. It does not
relicense all repository contents or establish authority to distribute every
retained input/output.

## Data and media

| Material | Applicable status / obligation |
|---|---|
| OSM extracts and OSM-derived databases | ODbL; attribute **© OpenStreetMap contributors**, https://www.openstreetmap.org/copyright ; assess database/share-alike obligations for distribution |
| GISCO LAU/NUTS assets and boundary-derived material | Eurostat GISCO / EuroGeographics reuse terms and attribution; not MIT, not established as ODbL merely by proximity to road data |
| Mapillary inventories, metadata and observations | Applicable imagery/API terms and actual acquisition/use evidence; no blanket relicensing of `results` |
| Existing Mapillary example in `fig6_imagery_validation.jpg` | Retained credit: **nglf, CC BY-SA 4.0**, https://creativecommons.org/licenses/by-sa/4.0/ ; preserve credit and indicate any modifications when reused |
| Other provider imagery/annotations | Rights require actual evidence; a generic viewer link or research purpose is not permission to scrape, publish or redistribute |
| Original manuscript/figures | No new blanket media/manuscript licence is assigned by the code MIT grant |
| Corrected survey-distance figure (Figure 9, `fig8_targeting_distance.png`), country/profile/geographic summaries, saved-distance/point reports and reporting bundle | Derived OSM-related analysis and possibly identifiable way-level judgments; assess data rights and observation permissions separately before distribution |

Administrative map credit:

> Administrative boundaries © EuroGeographics / Eurostat GISCO; road data
> © OpenStreetMap contributors (ODbL).

The Mapillary example's specific credit is **not** proof of rights for other
images or caches. Google-derived observations must not be converted to OSM
edits. The unchanged audit UI does not properly capture provider/panorama
identity, so it cannot establish per-image permission or acquisition provenance.
No ethics, consent, funding, conflict or author-role declaration is invented.

## Dependencies and executable distribution

`census\pom.xml` and `paper\package-lock.json` retain declared versions and
dependency relationships. Direct Java dependencies are:

- `com.wolt.osm:parallelpbf:0.2.0` (the existing POM identifies GPLv3);
- `org.openstreetmap.pbf:osmpbf:1.4.0`;
- `com.fasterxml.jackson.core:jackson-databind:2.17.2`;
- `org.locationtech.jts:jts-core:1.19.0`;
- `org.slf4j:slf4j-simple:1.7.36`;
- `org.junit.jupiter:junit-jupiter:5.11.0` (test scope).

Node DOCX generation declares `docx:9.7.1`, with transitive versions in the
lockfile. Existing figure code also uses its separately installed scientific
plotting stack; source inclusion does not bundle or license those libraries.
Python release/fetch fixture tests use the standard library and PowerShell.

This list is **not a completed transitive licence audit**. Actual upstream
licence/NOTICE texts, version-specific terms, copyleft compatibility and
source-offer obligations must be retained/assessed before distributing a
dependency-rich executable. The repository's MIT grant cannot override them.
The protected frozen JAR was not replaced. The separately staged corrected-code
build used for the local Greek, 20-country and profile reruns is not packaged
or cleared for executable distribution.

The default archive **omits all JARs and installed dependencies**. Its metadata
records exact expected executable SHA256 values from retained execution
manifests, rather than claiming source inclusion is executable reproduction.
The observed retained `census\target\census.jar` SHA256 on 2026-10-07 is
`cdc1480ca05ee45d73f484974e1031e6838c8e7bc45c4435204a10f1b732c059`.
Matching this hash does not complete the dependency-rights audit or prove the
native sensitivity artifact's execution linkage.

## Conservative GitHub source repository

The proposed Git allowlist is **code-first**: original source, tests, build
manifests, profiles/strata configuration, documentation, licensing text and
small checksum/provenance records. Generated census/country/imagery results,
audit samples or responses, raw OSM/GISCO inputs, cached API responses,
compiled dependencies, the local ZIP and manuscript/figure outputs remain
local. This is a source repository, **not** an independently restorable study
deposit or permission to publish excluded data. The manuscript can still be
built locally from retained inputs and outputs; a public code checkout alone
cannot reproduce its numerical findings.

Before pushing, confirm authority to publish the original code and its stated
MIT terms. `.gitignore` cannot remove files already tracked by Git or protect
against `git add -f`; inspect the staged file list and remove any excluded
tracked files from the index without deleting the working copies. Rights and
attribution for a later public paper/data deposit, and version-specific
licence/NOTICE obligations for any distributed JAR, must be assessed
separately. Do not treat a source-only GitHub URL as a data DOI.

## Local archive is not permission to publish

The existing `tools\build_release.py` allowlist produced a **historical
frozen-model local working package**, with rights/provisional-model notes.
It refuses the corrected manuscript with its old reporting bundle and does
not produce a corrected-study package.
It excludes raw provider/API caches, credentials, private annotations,
browser storage, audit exports/sample draws, raw point observations, huge raw
inputs and compiled dependencies. Its derived point summaries can still
identify individual way-level exceptions, and its paper includes the credited
Mapillary image (Figure 2, `fig6_imagery_validation.jpg`); these are **not** a blanket release of audit or imagery rights.
Even included summaries/media require the above rights assessment before
public distribution. No upload, DOI registration or publication is performed.
