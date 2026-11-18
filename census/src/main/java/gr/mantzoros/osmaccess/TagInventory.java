package gr.mantzoros.osmaccess;

import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Frozen feature/tag definitions. Must stay in sync with docs/metrics.md;
 * changes here require a changelog entry there.
 *
 * <p>v1.0 (2026-08-28) adds: crossing-as-way recognition, the legacy/modern
 * markings schema split, affirmative-vs-negative value semantics, and the
 * curated key list whose value distributions are reported.
 */
public final class TagInventory {

    /** docs/metrics.md section 1.1 - drivable network (way-level metrics denominator). */
    public static final Set<String> DRIVABLE_CLASSES = Set.of(
            "motorway", "trunk", "primary", "secondary", "tertiary",
            "residential", "unclassified", "living_street", "service",
            "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link");

    /** docs/metrics.md section 1.2 - pedestrian network. */
    public static final Set<String> PEDESTRIAN_CLASSES = Set.of(
            "pedestrian", "footway", "path", "steps", "corridor");

    /** docs/metrics.md section 2 - way-level tag families, in reporting order. */
    public static final String[] WAY_TAG_FAMILIES = {
            "name", "sidewalk", "surface", "smoothness", "lit", "maxspeed", "width", "incline"};

    // ---------------------------------------------------------------- classes

    public static boolean isDrivable(String highway) {
        return highway != null && DRIVABLE_CLASSES.contains(highway);
    }

    public static boolean isPedestrian(String highway) {
        return highway != null && PEDESTRIAN_CLASSES.contains(highway);
    }

    /**
     * Classes kept ONLY so the routing graph is connected, never counted in any census
     * denominator. Omitting them would fragment the walkable network at points where a
     * real pedestrian simply keeps walking, inflating the fragmentation metric.
     */
    public static final Set<String> GRAPH_ONLY_CLASSES = Set.of("cycleway", "track", "bridleway");

    public static boolean isGraphOnly(String highway) {
        return highway != null && GRAPH_ONLY_CLASSES.contains(highway);
    }

    /**
     * docs/metrics.md section 1.4 - a crossing mapped as a <em>way</em> (the
     * "sidewalk schema"). Covers the mainstream form ({@code highway=footway} +
     * {@code footway=crossing}), the cycle form ({@code highway=cycleway} +
     * {@code cycleway=crossing}), and the looser {@code highway=path} +
     * {@code crossing=*} form still common in Greek data.
     */
    public static boolean isCrossingWay(String highway, Map<String, String> tags) {
        if (highway == null) {
            return false;
        }
        return switch (highway) {
            case "footway" -> "crossing".equals(tags.get("footway"));
            case "cycleway" -> "crossing".equals(tags.get("cycleway"));
            case "path" -> tags.containsKey("crossing") || tags.containsKey("crossing:markings");
            default -> false;
        };
    }

    /** docs/metrics.md section 2 - the sidewalk family counts any of the four keys. */
    public static boolean hasWayTag(Map<String, String> tags, String family) {
        if ("sidewalk".equals(family)) {
            return tags.containsKey("sidewalk") || tags.containsKey("sidewalk:left")
                    || tags.containsKey("sidewalk:right") || tags.containsKey("sidewalk:both");
        }
        return tags.containsKey(family);
    }

    // ------------------------------------------------- affirmative semantics
    //
    // A completeness study measures whether the map SAYS something. A readiness
    // study needs to know whether what it says is a feature or the absence of
    // one: sidewalk=no is complete data and an inaccessible street. Conflating
    // the two is the standard failure mode of tag-presence studies.
    // docs/metrics.md section 2.1.

    private static final Set<String> NEGATIVE_SIDEWALK = Set.of("no", "none");
    private static final Set<String> NEGATIVE_GENERIC = Set.of("no", "none", "false", "0");
    private static final Set<String> NEGATIVE_TACTILE = Set.of("no", "false", "incorrect", "0");

    private static final String[] SIDEWALK_SIDE_KEYS = {"sidewalk:both", "sidewalk:left", "sidewalk:right"};

    /**
     * True when the tag family is present <em>and</em> asserts the feature exists.
     * {@code sidewalk=separate} counts as affirmative: the sidewalk exists, it is
     * mapped as its own way (and is then counted in the pedestrian universe).
     */
    public static boolean isAffirmative(Map<String, String> tags, String family) {
        switch (family) {
            case "sidewalk": {
                String v = tags.get("sidewalk");
                if (v != null && java.util.Collections.disjoint(values(v), NEGATIVE_SIDEWALK)) {
                    return true;
                }
                for (String k : SIDEWALK_SIDE_KEYS) {
                    String sv = tags.get(k);
                    if (sv != null && java.util.Collections.disjoint(values(sv), NEGATIVE_SIDEWALK)) {
                        return true;
                    }
                }
                return false;
            }
            case "lit":
            case "tactile_paving":
            case "crossing:island":
            case "button_operated":
            case "traffic_signals:sound":
            case "traffic_signals:vibration": {
                String v = tags.get(family);
                if (v == null) {
                    return false;
                }
                return !hasNegativeEvidence(family, values(v));
            }
            default:
                // name/surface/smoothness/maxspeed/width/incline have no meaningful
                // negative form - presence is affirmation.
                return hasWayTag(tags, family);
        }
    }

    /** Families for which affirmative differs from present; the rest are reported once. */
    public static boolean hasNegativeForm(String family) {
        return "sidewalk".equals(family) || "lit".equals(family);
    }

    static boolean hasNegativeEvidence(String key, Set<String> values) {
        return !java.util.Collections.disjoint(values,
                "tactile_paving".equals(key) ? NEGATIVE_TACTILE : NEGATIVE_GENERIC);
    }

    // ------------------------------------------------------- markings schema
    //
    // Greek data straddles two schemas. Counting only crossing:markings reports a
    // near-zero national markings rate; counting only the legacy crossing values
    // misses every recently mapped urban crossing. docs/metrics.md section 3.1.

    /** Legacy crossing values that assert road markings are present. */
    public static final Set<String> LEGACY_MARKED = Set.of(
            "zebra", "marked", "uncontrolled", "traffic_signals", "controlled",
            "pelican", "toucan", "pegasus", "tiger");

    /** Legacy crossing values that assert markings are absent. */
    public static final Set<String> LEGACY_UNMARKED = Set.of("unmarked", "no", "informal");
    private static final Set<String> LEGACY_SIGNALISED = Set.of(
            "traffic_signals", "pelican", "toucan", "puffin", "controlled");

    public static boolean hasModernMarkings(Map<String, String> tags) {
        return tags.containsKey("crossing:markings");
    }

    public static boolean hasLegacyMarkings(Map<String, String> tags) {
        return values(tags.get("crossing")).stream()
                .anyMatch(v -> LEGACY_MARKED.contains(v) || LEGACY_UNMARKED.contains(v));
    }

    /** Any statement about markings, in either schema. */
    public static boolean hasMarkingsStatement(Map<String, String> tags) {
        return hasModernMarkings(tags) || hasLegacyMarkings(tags);
    }

    /** The statement is affirmative (markings present). Modern schema wins when both exist. */
    public static boolean isMarked(Map<String, String> tags) {
        String modern = norm(tags.get("crossing:markings"));
        if (modern != null) {
            return java.util.Collections.disjoint(values(modern), NEGATIVE_GENERIC);
        }
        String legacy = norm(tags.get("crossing"));
        return legacy != null && values(legacy).stream().anyMatch(LEGACY_MARKED::contains)
                && java.util.Collections.disjoint(values(legacy), LEGACY_UNMARKED);
    }

    /** crossing=traffic_signals is a signalisation statement, not only a markings one. */
    public static boolean assertsSignalised(Map<String, String> tags) {
        Set<String> signals = values(tags.get("crossing:signals"));
        if (hasNegativeEvidence("crossing:signals", signals)) {
            return false;
        }
        if (signals.contains("yes")) {
            return true;
        }
        Set<String> crossing = values(tags.get("crossing"));
        return !crossing.contains("no")
                && !java.util.Collections.disjoint(crossing, LEGACY_SIGNALISED);
    }

    // ------------------------------------------------------------------ kerb
    //
    // For a blind or wheelchair pedestrian kerb=raised is a barrier and
    // kerb=flush|lowered is not. Presence alone answers the wrong question.

    public static final String KERB_ACCESSIBLE = "accessible";
    public static final String KERB_BARRIER = "barrier";
    public static final String KERB_NONE = "none";
    public static final String KERB_UNSPECIFIED = "unspecified";

    public static String kerbClass(String value) {
        String v = norm(value);
        if (v == null) {
            return KERB_UNSPECIFIED;
        }
        return switch (v) {
            case "flush", "lowered" -> KERB_ACCESSIBLE;
            case "raised" -> KERB_BARRIER;
            case "no" -> KERB_NONE;
            default -> KERB_UNSPECIFIED;
        };
    }

    // ------------------------------------------------- value-distribution keys

    /** Categorical way-level keys whose value distribution is reported (km-weighted). */
    public static final Set<String> WAY_VALUE_KEYS = new LinkedHashSet<>(List.of(
            "sidewalk", "sidewalk:left", "sidewalk:right", "sidewalk:both",
            "surface", "smoothness", "lit"));

    /** Categorical crossing-feature keys whose value distribution is reported (counts). */
    public static final Set<String> CROSSING_VALUE_KEYS = new LinkedHashSet<>(List.of(
            "crossing", "crossing:markings", "crossing:island", "tactile_paving",
            "kerb", "traffic_signals:sound", "traffic_signals:vibration", "button_operated",
            "surface", "wheelchair"));

    /** Node keys retained for crossing-level metrics (docs/metrics.md section 3). */
    public static final Set<String> NODE_KEYS = Set.of(
            "highway", "crossing", "crossing:markings", "crossing:island", "crossing:signals",
            "tactile_paving", "kerb", "traffic_signals:sound", "traffic_signals:vibration",
            "button_operated", "barrier", "wheelchair", "foot", "access",
            "foot:conditional", "access:conditional", "surface", "smoothness", "incline", "width");

    /** Way keys retained (frozen families + sidewalk variants + crossing-way attributes). */
    public static final Set<String> WAY_KEYS = Set.of(
            "name", "sidewalk", "sidewalk:left", "sidewalk:right", "sidewalk:both",
            "surface", "smoothness", "lit", "maxspeed", "width", "incline",
            "footway", "cycleway", "crossing", "crossing:markings", "crossing:island",
            "crossing:signals", "tactile_paving", "kerb", "wheelchair", "highway",
            "handrail", "traffic_signals:sound", "traffic_signals:vibration", "button_operated",
            "ramp", "ramp:wheelchair", "foot", "access", "barrier",
            "foot:conditional", "access:conditional", "area",
            "sidewalk:surface", "sidewalk:width", "sidewalk:smoothness",
            "sidewalk:left:surface", "sidewalk:right:surface",
            "sidewalk:left:width", "sidewalk:right:width", "sidewalk:both:surface",
            "sidewalk:both:width", "sidewalk:left:smoothness", "sidewalk:right:smoothness",
            "sidewalk:both:smoothness");

    /** Shared by the parser and regression tests; configured rule inputs cannot be dropped. */
    public static Map<String, String> retainTags(Map<String, String> tags,
                                                Set<String> inventory, Set<String> configured) {
        Map<String, String> out = new java.util.HashMap<>();
        tags.forEach((key, value) -> {
            if (inventory.contains(key) || configured.contains(key)) {
                out.put(key, value);
            }
        });
        return out;
    }

    static Set<String> values(String text) {
        if (text == null || text.isBlank()) {
            return Set.of();
        }
        if (text.indexOf(';') < 0) {
            return Set.of(norm(text));
        }
        Set<String> out = new java.util.TreeSet<>();
        if (text != null) {
            for (String value : text.split(";")) {
                if (!value.isBlank()) {
                    out.add(norm(value));
                }
            }
        }
        return out;
    }

    static String mergeValues(String a, String b) {
        Set<String> all = new java.util.TreeSet<>(values(a));
        all.addAll(values(b));
        return String.join(";", all);
    }

    static String worstKerb(String a, String b) {
        Set<String> all = values(mergeValues(a, b));
        if (all.isEmpty()) {
            return null;
        }
        if (all.contains("raised")) {
            return "raised";
        }
        if (all.stream().anyMatch(v -> KERB_UNSPECIFIED.equals(kerbClass(v)))) {
            return "unknown";
        }
        if (all.contains("lowered")) {
            return "lowered";
        }
        return all.contains("flush") ? "flush" : "no";
    }

    static String norm(String v) {
        return v == null ? null : v.trim().toLowerCase(Locale.ROOT);
    }

    private TagInventory() {
    }
}
