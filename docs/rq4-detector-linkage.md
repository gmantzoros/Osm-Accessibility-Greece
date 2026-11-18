# Published cross-street study and scope of this paper

Revised 2026-09-15 after checking the cited publication. This document supersedes
the earlier proposed linkage analysis; it is not a preregistration.

## Published evidence

Meliones, A.; Mantzoros, G. (2025). **Discovery of Upcoming Cross Streets in Google
Maps Blind Navigation.** Applied Sciences, 15(24), 13215.
https://doi.org/10.3390/app152413215

The study used Google Nearest Roads and Geocoding APIs, not OpenStreetMap. Its
100 coordinate-pair cases were distributed across Athens (67), Thessaloniki (22),
Patras (10) and Karystos (1). In the selected initial configuration, 67 cases
succeeded, 32 returned the current road, and one failed. Simulated forward
progression resolved 24 of the current-road cases, giving 91 final successes and
nine failures.

These are published algorithm-evaluation outcomes, not a controlled BVI field
trial. The publication does not provide the previously attributed 500-case OSM
benchmark, six-city success percentages, or 16/49 name-gap failure count. That
separate exploratory experiment is excluded from the present paper at the
author's request.

## Use in the present manuscript

The 100-case study supplies motivation and a clearly labelled description of
prior work. Its outcomes are not paired observations on the present OSM
snapshot. No correlation between those outcomes and this census is computed.
They do not validate the BVI routing profile or establish a map-completeness
effect on navigation performance.

The current empirical contributions concern recorded OSM attributes,
profile-conditioned graph connectivity, cross-region comparisons, and
conditional survey priorities. Prior work already links map quality and routing,
including PathwayBench and wheelchair-route reliability studies; novelty is not
claimed for that link in general.

## A possible future evaluation

Testing whether missing OSM information predicts system outcomes would require
a separate protocol with a specified OSM-consuming implementation, aligned map
snapshot and spatial units, independent reference observations, case-level
outcomes and a documented sampling process. Any modelling should account for
case clustering and possible selection effects. No such experiment is presented
as completed here.

The active draw is **500 fresh sidewalk-only marker observations**, ten in
each of 50 selected principal LAUs, exact-radius-v2, seed 20260722, with known
pools/probabilities. Its drivable-way sidewalk-key-absence population is not
eligible routing UNKNOWN geometry. The unchanged year/date/reason interface
does not properly capture provider, notes, capture month/day or panorama
identity. Its planned repeat round was withdrawn on 2026-11-18, so it has no
repeatability measure; it never assessed truth or performance.

This local feature audit is neither an extension of the published 100-case
experiment nor a test of routes or navigation-system performance. The older
three-arm audit/linkage proposals remain superseded. No completed case-level
OSM linkage, new benchmark, field-validation result or retrospectively
verified execution manifest is claimed.
