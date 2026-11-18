package gr.mantzoros.osmaccess;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * A routing profile: the rules that turn OSM tags into {@link EdgeState}.
 *
 * <p>Loaded from JSON (see {@code data/profile-bvi.json}) rather than hard-coded, because
 * the rules ARE the research decision and a reviewer must be able to read them without
 * reading Java. Evaluation has two stages:
 *
 * <ol>
 *   <li>A <b>primary determinant</b> fixes the base state from the tag family that decides
 *       usability for this edge class - the sidewalk statement on a road, the accessibility
 *       attributes on a crossing, the handrail on steps. Silence here yields UNKNOWN.
 *   <li><b>Secondary checks</b> (surface, smoothness, incline, width, kerb) may only push
 *       the state down to BLOCK. Silence on a secondary check does <em>not</em> downgrade a
 *       PASS: otherwise every footway in Greece would be UNKNOWN because nobody tags
 *       {@code width}, and the metric would carry no signal.
 * </ol>
 *
 * <p>Blocking evidence dominates within a rule. States are conditional on these declared
 * defaults and do not establish physical safety or individual route accessibility.
 */
public final class AccessProfile {

    private static final Pattern MEASUREMENT = Pattern.compile(
            "^([+-]?(?:\\d+(?:[.,]\\d+)?|[.,]\\d+))\\s*"
                    + "(mm|cm|km|m|metres?|meters?|ft|feet|foot|inches?|in|\"|')?$",
            Pattern.CASE_INSENSITIVE);
    private static final Pattern INCLINE = Pattern.compile(
            "^([+-]?(?:\\d+(?:[.,]\\d+)?|[.,]\\d+))\\s*(%|°|deg(?:rees?)?)?$",
            Pattern.CASE_INSENSITIVE);

    private final String id;
    private final String description;
    private final Set<String> excludedClasses;

    private final Set<String> sidewalkAffirmative;
    private final Set<String> sidewalkNegative;
    private final EdgeState sidewalkOnMissing;

    private final Set<String> pedestrianClasses;
    private final EdgeState pedestrianDefault;

    private final Map<String, Set<String>> crossingPass;
    private final Map<String, Set<String>> crossingBlock;
    private final EdgeState crossingOnMissing;

    private final Map<String, Set<String>> stepsPass;
    private final Map<String, Set<String>> stepsBlock;
    private final EdgeState stepsOnMissing;

    private final Set<String> kerbBlock;
    private final Set<String> kerbPass;
    private final Set<String> surfaceBlock;
    private final Set<String> surfaceDegrade;
    private final Set<String> smoothnessBlock;

    private final double maxInclinePercent;
    private final double minWidthMetres;
    private final double minUsableComponentKm;
    private final boolean wheelchair;

    private AccessProfile(JsonNode n) {
        this.id = n.path("id").asText("unnamed");
        this.description = n.path("description").asText("");
        this.wheelchair = "wheelchair".equals(n.path("mode").asText())
                || id.startsWith("wheelchair");
        this.excludedClasses = strings(n.path("excludedClasses"));

        JsonNode sw = n.path("sidewalk");
        this.sidewalkAffirmative = strings(sw.path("affirmative"));
        this.sidewalkNegative = strings(sw.path("negative"));
        this.sidewalkOnMissing = state(sw.path("onMissing").asText("unknown"));

        JsonNode pw = n.path("pedestrianWays");
        this.pedestrianClasses = strings(pw.path("classes"));
        this.pedestrianDefault = state(pw.path("default").asText("pass"));

        JsonNode cr = n.path("crossing");
        this.crossingPass = conditions(cr.path("passIf"));
        this.crossingBlock = conditions(cr.path("blockIf"));
        this.crossingOnMissing = state(cr.path("onMissing").asText("unknown"));

        JsonNode st = n.path("steps");
        this.stepsPass = conditions(st.path("passIf"));
        this.stepsBlock = conditions(st.path("blockIf"));
        this.stepsOnMissing = state(st.path("onMissing").asText("unknown"));

        this.kerbBlock = strings(n.path("kerb").path("block"));
        this.kerbPass = strings(n.path("kerb").path("pass"));
        this.surfaceBlock = strings(n.path("surface").path("block"));
        this.surfaceDegrade = strings(n.path("surface").path("degrade"));
        this.smoothnessBlock = strings(n.path("smoothness").path("block"));

        this.maxInclinePercent = n.path("maxInclinePercent").asDouble(Double.MAX_VALUE);
        this.minWidthMetres = n.path("minWidthMetres").asDouble(0);
        this.minUsableComponentKm = n.path("minUsableComponentKm").asDouble(1.0);
        for (String check : java.util.List.of("kerb", "surface", "smoothness")) {
            if (!"pass".equals(n.path(check).path("onMissing").asText("pass"))) {
                throw new IllegalArgumentException(check + ".onMissing must be pass: "
                        + "secondary silence is not a primary UNKNOWN rule");
            }
        }
    }

    public static AccessProfile load(File file) throws IOException {
        return new AccessProfile(new ObjectMapper().readTree(file));
    }

    public String id() {
        return id;
    }

    public String description() {
        return description;
    }

    public double minUsableComponentKm() {
        return minUsableComponentKm;
    }

    public Set<String> requiredKeys() {
        Set<String> keys = new HashSet<>(TagInventory.WAY_KEYS);
        keys.addAll(TagInventory.NODE_KEYS);
        for (Map<String, Set<String>> rules : java.util.List.of(
                crossingPass, crossingBlock, stepsPass, stepsBlock)) {
            keys.addAll(rules.keySet());
        }
        return Set.copyOf(keys);
    }

    /** Is this way part of the walkable graph at all? */
    public boolean inGraph(RoadWay w) {
        String hw = w.highwayClass();
        if (hw == null || excludedClasses.contains(hw)) {
            return false;
        }
        return w.crossingWay() || pedestrianClasses.contains(hw) || "steps".equals(hw)
                || TagInventory.isDrivable(hw) || TagInventory.isPedestrian(hw)
                || TagInventory.isGraphOnly(hw);
    }

    /** The state of every edge of this way. */
    public EdgeState evaluate(RoadWay w) {
        Map<String, String> t = w.tags();
        String hw = w.highwayClass();

        EdgeState base;
        if (w.crossingWay()) {
            base = match(t, crossingBlock) ? EdgeState.BLOCK
                    : matchPass(t, crossingPass) ? EdgeState.PASS : crossingOnMissing;
        } else if ("steps".equals(hw)) {
            base = match(t, stepsBlock)
                    || (wheelchair && TagInventory.hasNegativeEvidence(
                            "ramp:wheelchair", TagInventory.values(t.get("ramp:wheelchair"))))
                    ? EdgeState.BLOCK
                    : matchPass(t, stepsPass) ? EdgeState.PASS : stepsOnMissing;
        } else if (pedestrianClasses.contains(hw)) {
            base = pedestrianDefault;
        } else if (TagInventory.isDrivable(hw)) {
            base = sidewalkState(t);
        } else {
            // cycleways, tracks: kept for connectivity, never asserted usable
            base = EdgeState.UNKNOWN;
        }

        return base.and(restrictions(t)).and(secondaryChecks(t, TagInventory.isDrivable(hw)));
    }

    /**
     * A road is usable by a pedestrian if the map says it has a sidewalk. Absence of a
     * sidewalk tag is UNKNOWN rather than walkable-by-default: in Greek urban data the
     * untagged case dominates, and defaulting it to passable would make the optimistic
     * closure equal to the whole network and the decidability gap meaningless.
     */
    private EdgeState sidewalkState(Map<String, String> t) {
        boolean affirmative = false;
        boolean negative = false;
        boolean separate = false;
        for (String k : new String[]{"sidewalk", "sidewalk:both", "sidewalk:left", "sidewalk:right"}) {
            Set<String> values = TagInventory.values(t.get(k));
            boolean neg = values.stream().anyMatch(sidewalkNegative::contains);
            boolean pos = values.stream().anyMatch(sidewalkAffirmative::contains);
            if (neg && (pos || k.equals("sidewalk") || k.equals("sidewalk:both"))) {
                return EdgeState.BLOCK;
            }
            separate |= values.contains("separate");
            affirmative |= pos && !values.contains("separate");
            negative |= neg;
        }
        if (affirmative) {
            return EdgeState.PASS;
        }
        return separate ? EdgeState.UNKNOWN : negative ? EdgeState.BLOCK : sidewalkOnMissing;
    }

    /** Secondary checks may only downgrade to BLOCK; silence never downgrades. */
    private EdgeState secondaryChecks(Map<String, String> t) {
        return secondaryChecks(t, false);
    }

    private EdgeState secondaryChecks(Map<String, String> t, boolean road) {
        // Roadway width/surface are not measurements of a sidewalk.
        String surface = road ? sidewalkMeasurement(t, "surface") : t.get("surface");
        if (TagInventory.values(surface).stream().anyMatch(surfaceBlock::contains)) {
            return EdgeState.BLOCK;
        }
        String smooth = road ? sidewalkMeasurement(t, "smoothness") : t.get("smoothness");
        if (TagInventory.values(smooth).stream().anyMatch(smoothnessBlock::contains)) {
            return EdgeState.BLOCK;
        }
        if (TagInventory.values(t.get("kerb")).stream().anyMatch(kerbBlock::contains)) {
            return EdgeState.BLOCK;
        }
        for (String value : TagInventory.values(t.get("incline"))) {
            Double incline = percent(value);
            if (incline != null && Math.abs(incline) > maxInclinePercent) {
                return EdgeState.BLOCK;
            }
        }
        for (String value : TagInventory.values(road ? sidewalkMeasurement(t, "width") : t.get("width"))) {
            Double width = metres(value);
            if (width != null && width < minWidthMetres) {
                return EdgeState.BLOCK;
            }
        }
        return EdgeState.PASS;
    }

    private static String sidewalkMeasurement(Map<String, String> tags, String key) {
        String value = null;
        for (String prefix : new String[]{"sidewalk:", "sidewalk:left:", "sidewalk:right:", "sidewalk:both:"}) {
            String next = tags.get(prefix + key);
            if (next != null) {
                value = value == null ? next : TagInventory.mergeValues(value, next);
            }
        }
        return value;
    }

    private EdgeState restrictions(Map<String, String> t) {
        Set<String> foot = TagInventory.values(t.get("foot"));
        Set<String> access = TagInventory.values(t.get("access"));
        if (foot.contains("no") || foot.contains("private")
                || (java.util.Collections.disjoint(foot, Set.of("yes", "designated", "permissive"))
                    && (access.contains("no") || access.contains("private")))) {
            return EdgeState.BLOCK;
        }
        if (wheelchair && TagInventory.values(t.get("wheelchair")).contains("no")) {
            return EdgeState.BLOCK;
        }
        if (t.containsKey("access:conditional") || t.containsKey("foot:conditional")
                || (wheelchair && TagInventory.values(t.get("wheelchair")).contains("limited"))) {
            return EdgeState.UNKNOWN;
        }
        return EdgeState.PASS;
    }

    /** Zero-length node transition; no inferred sidewalk-side geometry. */
    public EdgeState evaluateNode(Map<String, String> t) {
        EdgeState base = TagInventory.values(t.get("highway")).contains("crossing")
                ? evaluateCrossingTags(t) : EdgeState.PASS;
        Set<String> barriers = TagInventory.values(t.get("barrier"));
        if (!java.util.Collections.disjoint(barriers, Set.of("wall", "fence", "block"))
                || (wheelchair && !java.util.Collections.disjoint(
                        barriers, Set.of("stile", "turnstile")))) {
            base = EdgeState.BLOCK;
        } else if (!barriers.isEmpty()
                && !Set.of("no", "kerb").containsAll(barriers)
                && java.util.Collections.disjoint(TagInventory.values(t.get("foot")),
                        Set.of("yes", "designated", "permissive"))) {
            base = base.and(EdgeState.UNKNOWN);
        }
        return base.and(restrictions(t)).and(secondaryChecks(t));
    }

    /**
     * Why an edge came out UNKNOWN - i.e. which question the map failed to answer.
     *
     * <p>This is the actionable half of the analysis. "77% of the network is undecidable" is
     * a diagnosis; "62 points are roads with no sidewalk statement and 11 are crossings
     * with unresolved profile-specific cues" is a work plan, and maps directly onto
     * StreetComplete quest types (docs/routing-feasibility.md section 6.4).
     */
    public String undecidedReason(RoadWay w) {
        if (restrictions(w.tags()) == EdgeState.UNKNOWN) {
            return "conditional_or_limited_access";
        }
        if (w.tags().entrySet().stream().anyMatch(e -> e.getKey().startsWith("sidewalk")
                && TagInventory.values(e.getValue()).contains("separate"))) {
            return "separate_sidewalk_geometry";
        }
        String hw = w.highwayClass();
        if (w.crossingWay()) {
            return "crossing_attributes";
        }
        if ("steps".equals(hw)) {
            return "steps_handrail";
        }
        if (pedestrianClasses.contains(hw)) {
            return "pedestrian_other";
        }
        if (TagInventory.isDrivable(hw)) {
            return "road_sidewalk";
        }
        return "graph_only_class";
    }

    /** Local crossing-feature label only; this is not a route-calibration observation. */
    public EdgeState evaluateCrossingTags(Map<String, String> t) {
        EdgeState base = match(t, crossingBlock) ? EdgeState.BLOCK
                : matchPass(t, crossingPass) ? EdgeState.PASS : crossingOnMissing;
        return base.and(restrictions(t)).and(secondaryChecks(t));
    }

    /** Question about the affirmative crossing evidence configured for this profile. */
    String crossingQuestion() {
        java.util.List<String> cues = new ArrayList<>();
        for (Map.Entry<String, Set<String>> condition : crossingPass.entrySet()) {
            String values = String.join(" or ", condition.getValue());
            cues.add(switch (condition.getKey()) {
                case "tactile_paving" -> "tactile paving";
                case "traffic_signals:sound" -> "an acoustic signal";
                case "kerb" -> condition.getValue().equals(Set.of("flush", "lowered"))
                        ? "a flush or lowered kerb" : "a kerb tagged " + values;
                case "wheelchair" -> "a wheelchair tag of " + values;
                default -> "a " + condition.getKey() + " tag of " + values;
            });
        }
        return cues.isEmpty() ? "what crossing accessibility evidence is missing?"
                : "does this crossing have " + String.join(" or ", cues) + "?";
    }

    /** Reported separately: passable but degraded underfoot. */
    public boolean isDegradedSurface(Map<String, String> t) {
        return TagInventory.values(t.get("surface")).stream().anyMatch(surfaceDegrade::contains);
    }

    // ------------------------------------------------------------------ parsing

    private static boolean match(Map<String, String> tags, Map<String, Set<String>> conditions) {
        for (Map.Entry<String, Set<String>> c : conditions.entrySet()) {
            if (TagInventory.values(tags.get(c.getKey())).stream().anyMatch(c.getValue()::contains)) {
                return true;
            }
        }
        return false;
    }

    private static boolean matchPass(Map<String, String> tags, Map<String, Set<String>> conditions) {
        for (Map.Entry<String, Set<String>> c : conditions.entrySet()) {
            Set<String> values = TagInventory.values(tags.get(c.getKey()));
            if (!TagInventory.hasNegativeEvidence(c.getKey(), values)
                    && values.stream().anyMatch(c.getValue()::contains)) {
                return true;
            }
        }
        return false;
    }

    private static Map<String, Set<String>> conditions(JsonNode n) {
        Map<String, Set<String>> out = new LinkedHashMap<>();
        n.fields().forEachRemaining(e -> {
            if (e.getValue().isArray()) {
                out.put(e.getKey(), strings(e.getValue()));
            }
        });
        return out;
    }

    private static Set<String> strings(JsonNode n) {
        Set<String> out = new LinkedHashSet<>();
        if (n.isArray()) {
            for (JsonNode v : n) {
                out.add(v.asText().trim().toLowerCase(Locale.ROOT));
            }
        }
        return out;
    }

    private static EdgeState state(String s) {
        return switch (s.trim().toLowerCase(Locale.ROOT)) {
            case "pass" -> EdgeState.PASS;
            case "block" -> EdgeState.BLOCK;
            default -> EdgeState.UNKNOWN;
        };
    }

    /** Incline is in percent unless explicitly measured in degrees. */
    private static Double percent(String v) {
        if (v == null) {
            return null;
        }
        Matcher match = INCLINE.matcher(v.trim());
        if (!match.matches()) {
            return null;
        }
        double value = Double.parseDouble(match.group(1).replace(',', '.'));
        if (!Double.isFinite(value)) {
            return null;
        }
        String unit = match.group(2);
        return unit != null && !"%".equals(unit)
                ? 100 * Math.tan(Math.toRadians(value)) : value;
    }

    /** Widths without a unit are metres; explicit smaller or imperial units are converted. */
    private static Double metres(String v) {
        if (v == null) {
            return null;
        }
        Matcher match = MEASUREMENT.matcher(v.trim());
        if (!match.matches()) {
            return null;
        }
        double value = Double.parseDouble(match.group(1).replace(',', '.'));
        if (!Double.isFinite(value)) {
            return null;
        }
        String unit = match.group(2);
        return value * switch (unit == null ? "m" : unit.toLowerCase(Locale.ROOT)) {
            case "mm" -> 0.001;
            case "cm" -> 0.01;
            case "km" -> 1000.0;
            case "ft", "feet", "foot", "'" -> 0.3048;
            case "in", "inch", "inches", "\"" -> 0.0254;
            default -> 1.0;
        };
    }

    /** Compatibility helper. */
    static Set<String> emptySet() {
        return new HashSet<>();
    }
}
