package gr.mantzoros.osmaccess;

import java.io.IOException;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

/** Conditional component-length statistics, not physical accessibility guarantees. */
public final class FeasibilityAnalysis {
    public static final String MODEL_VERSION = "conditional-2026-10-09";
    private final RoutingGraph graph;
    private final StrataIndex strata;
    private final double threshold;
    private final Map<String, double[]> perStratum = new TreeMap<>();
    private long edges;
    private int pessComponents;
    private int optComponents;
    private double pessLargest;
    private double optLargest;

    FeasibilityAnalysis(RoutingGraph graph, StrataIndex strata, double threshold) {
        if (!Double.isFinite(threshold) || threshold <= 0) {
            throw new IllegalArgumentException("component threshold must be finite and positive");
        }
        this.graph = graph;
        this.strata = strata;
        this.threshold = threshold;
    }

    FeasibilityAnalysis(AccessProfile profile, StrataIndex strata, List<RoadWay> ways,
                        long[] ids, double[] lats, double[] lons, boolean[] resolved) {
        this(new RoutingGraph(profile, "extended", ways, ids, lats, lons, resolved,
                Map.of(), Map.of()), strata, profile.minUsableComponentKm());
    }

    public void run() {
        perStratum.clear();
        edges = 0;
        pessComponents = optComponents = 0;
        pessLargest = optLargest = 0;
        RoutingGraph.Closure pess = graph.closure(false, threshold);
        RoutingGraph.Closure opt = graph.closure(true, threshold);
        for (double km : pess.mass) {
            if (km > 0) {
                pessComponents++;
                pessLargest = Math.max(pessLargest, km);
            }
        }
        for (double km : opt.mass) {
            if (km > 0) {
                optComponents++;
                optLargest = Math.max(optLargest, km);
            }
        }
        for (RoutingGraph.Way way : graph.ways) {
            RoadWay source = way.source();
            List<String> locations = strata.strataFor(way.lat(), way.lon());
            EdgeState state = way.state();
            String reason = graph.profile.undecidedReason(source);
            boolean pedestrian = TagInventory.isPedestrian(source.highwayClass())
                    && !source.crossingWay();
            graph.edges(way, (a, b, oa, ob, km) -> {
                edges++;
                boolean pu = state == EdgeState.PASS && pess.mass[pess.find(a)] >= threshold;
                boolean ou = state != EdgeState.BLOCK && opt.mass[opt.find(a)] >= threshold;
                boolean pe = way.eligible() && state == EdgeState.PASS
                        && pess.eligibleMass[pess.find(a)] >= threshold;
                boolean oe = way.eligible() && state != EdgeState.BLOCK
                        && opt.eligibleMass[opt.find(a)] >= threshold;
                for (String location : locations) {
                    double[] v = perStratum.computeIfAbsent(location, k -> new double[24]);
                    v[0] += km;
                    v[state == EdgeState.PASS ? 1 : state == EdgeState.UNKNOWN ? 2 : 3] += km;
                    if (pu) v[4] += km;
                    if (ou) v[5] += km;
                    if (graph.profile.isDegradedSurface(source.tags())) v[6] += km;
                    if (source.crossingWay()) {
                        v[state == EdgeState.PASS ? 7 : state == EdgeState.UNKNOWN ? 8 : 9]++;
                    }
                    if (state == EdgeState.UNKNOWN) {
                        int slot = switch (reason) {
                            case "road_sidewalk" -> 10;
                            case "crossing_attributes" -> 11;
                            case "steps_handrail" -> 12;
                            case "pedestrian_other" -> 13;
                            case "graph_only_class" -> 14;
                            default -> 23;
                        };
                        v[slot] += km;
                        boolean policy = reason.equals("graph_only_class")
                                || reason.equals("separate_sidewalk_geometry")
                                || reason.equals("conditional_or_limited_access");
                        v[policy ? 22 : 21] += km;
                    }
                    if (pedestrian) {
                        v[16] += km;
                        if (source.tags().containsKey("tactile_paving")) v[15] += km;
                    }
                    v[way.eligible() ? 17 : 18] += km;
                    if (pe) v[19] += km;
                    if (oe) v[20] += km;
                }
            });
        }
    }

    private static double pct(double a, double b) {
        return b == 0 ? Double.NaN : 100 * a / b;
    }

    public void write(Path directory) throws IOException {
        Files.createDirectories(directory);
        try (PrintWriter out = new PrintWriter(Files.newBufferedWriter(
                directory.resolve("feasibility.csv"), StandardCharsets.UTF_8))) {
            out.println("stratum,group,layer,profile,walkable_km,pass_km,unknown_km,block_km,"
                    + "pass_pct,unknown_pct,block_pct,degraded_km,pess_usable_km,pess_usable_pct,"
                    + "opt_usable_km,opt_usable_pct,decidability_gap_pct,crossing_edges_pass,"
                    + "crossing_edges_unknown,crossing_edges_block,unknown_road_sidewalk_km,"
                    + "unknown_crossing_attrs_km,unknown_steps_km,unknown_pedestrian_other_km,"
                    + "unknown_graph_only_km,pedestrian_km,pedestrian_tactile_km,pedestrian_tactile_pct,"
                    + "decided_pct,missing_evidence_unknown_km,policy_unknown_km,eligible_km,auxiliary_km,"
                    + "pess_eligible_usable_km,opt_eligible_usable_km,eligible_decidability_gap_pct,"
                    + "graph_scope,unknown_other_km,component_threshold_km,model_version");
            for (String s : ordered()) {
                double[] v = perStratum.get(s);
                List<String> row = new ArrayList<>(List.of(q(s), q(strata.groupOf(s)),
                        q(strata.layerOf(s)), q(graph.profile.id())));
                for (double n : new double[]{v[0], v[1], v[2], v[3], pct(v[1], v[0]),
                        pct(v[2], v[0]), pct(v[3], v[0]), v[6], v[4], pct(v[4], v[0]),
                        v[5], pct(v[5], v[0]), pct(v[5] - v[4], v[0]),
                        v[7], v[8], v[9], v[10], v[11], v[12], v[13], v[14],
                        v[16], v[15], pct(v[15], v[16]), pct(v[1] + v[3], v[0]),
                        v[21], v[22], v[17], v[18], v[19], v[20], pct(v[20] - v[19], v[17])}) {
                    row.add(f(n));
                }
                row.add(graph.scope);
                row.set(17, Long.toString((long) v[7]));
                row.set(18, Long.toString((long) v[8]));
                row.set(19, Long.toString((long) v[9]));
                row.add(f(v[23]));
                row.add(f(threshold));
                row.add(MODEL_VERSION);
                out.println(String.join(",", row));
            }
        }
        try (PrintWriter out = new PrintWriter(Files.newBufferedWriter(
                directory.resolve("feasibility_global.csv"), StandardCharsets.UTF_8))) {
            out.println("profile,walkable_km,edges,pess_components,pess_largest_km,opt_components,"
                    + "opt_largest_km,min_usable_component_km,graph_scope,unknown_node_transitions,"
                    + "blocked_node_transitions,model_version");
            out.printf(Locale.ROOT, "%s,%.3f,%d,%d,%.3f,%d,%.3f,%.3f,%s,%d,%d,%s%n",
                    graph.profile.id(), national()[0], edges, pessComponents, pessLargest,
                    optComponents, optLargest, threshold, graph.scope,
                    graph.transitions.values().stream().filter(s -> s == EdgeState.UNKNOWN).count(),
                    graph.transitions.values().stream().filter(s -> s == EdgeState.BLOCK).count(),
                    MODEL_VERSION);
        }
    }

    public void appendSensitivity(Path file) throws IOException {
        boolean header = !Files.exists(file);
        try (PrintWriter out = new PrintWriter(Files.newBufferedWriter(file, StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.APPEND))) {
            if (header) {
                out.println("stratum,group,layer,profile,graph_scope,threshold_km,walkable_km,"
                        + "eligible_km,pess_usable_km,opt_usable_km,pess_usable_pct,opt_usable_pct,"
                        + "decidability_gap_pct,pess_eligible_usable_km,opt_eligible_usable_km,"
                        + "eligible_decidability_gap_pct,model_version");
            }
            for (String s : ordered()) {
                double[] v = perStratum.get(s);
                out.printf(Locale.ROOT, "%s,%s,%s,%s,%s,%.3f,%.3f,%.3f,%.3f,%.3f,"
                                + "%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%s%n",
                        q(s), q(strata.groupOf(s)), q(strata.layerOf(s)), graph.profile.id(),
                        graph.scope, threshold, v[0], v[17], v[4], v[5], pct(v[4], v[0]),
                        pct(v[5], v[0]), pct(v[5] - v[4], v[0]), v[19], v[20],
                        pct(v[20] - v[19], v[17]), MODEL_VERSION);
            }
        }
    }

    private List<String> ordered() {
        List<String> out = new ArrayList<>(perStratum.keySet());
        if (out.remove(StrataIndex.NATIONAL)) out.add(0, StrataIndex.NATIONAL);
        return out;
    }

    RoutingGraph graph() {
        return graph;
    }

    public double[] national() {
        return perStratum.getOrDefault(StrataIndex.NATIONAL, new double[24]);
    }

    public static double pessimisticPct(double[] v) { return pct(v[4], v[0]); }
    public static double optimisticPct(double[] v) { return pct(v[5], v[0]); }
    public static double unknownPct(double[] v) { return pct(v[2], v[0]); }
    public static Map<String, Double> unknownBreakdownPct(double[] v) {
        Map<String, Double> out = new java.util.LinkedHashMap<>();
        out.put("unresolved road sidewalk evidence", pct(v[10], v[0]));
        out.put("unresolved crossing evidence", pct(v[11], v[0]));
        out.put("unresolved steps evidence", pct(v[12], v[0]));
        out.put("auxiliary-class policy UNKNOWN", pct(v[14], v[0]));
        out.put("other unresolved or policy UNKNOWN", pct(v[23], v[0]));
        return out;
    }

    static String f(double value) {
        return Double.isFinite(value) ? String.format(Locale.ROOT, "%.3f", value) : "";
    }
    static String q(String value) {
        return "\"" + value.replace("\"", "\"\"") + "\"";
    }
}
