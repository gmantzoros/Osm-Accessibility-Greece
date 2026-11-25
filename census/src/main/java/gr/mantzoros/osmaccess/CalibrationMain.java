package gr.mantzoros.osmaccess;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.File;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.SplittableRandom;
import java.util.TreeMap;
import java.util.TreeSet;

/**
 * Audit-calibrated sidewalk scenarios for the audited principal communities.
 *
 * <p>Inside the communities listed in the model file, every eligible road that is UNKNOWN
 * only because it has no sidewalk statement receives an imputed whole-way state: PASS
 * (as if tagged sidewalk=both) with the modelled probability for its community and road
 * class group, otherwise BLOCK (as if tagged sidewalk=no). Each replicate uses one
 * bootstrap refit of the audit model and one Bernoulli draw per way (independent mode)
 * or per named street within a community and class group (street mode).
 *
 * <p>Two closures are evaluated. C1 keeps every other unresolved state pessimistic:
 * crossings, gates, steps and auxiliary classes need positive evidence. C2 admits those
 * other unresolved states as the optimistic closure does. Roads outside the audited
 * communities keep unresolved sidewalk states excluded in both, so only the audited
 * communities are reported. These are model scenarios, not measured accessibility.
 *
 * <p>Usage: java -Xmx16g -cp classes;census.jar gr.mantzoros.osmaccess.CalibrationMain
 * extract.osm.pbf strata.json profile.json sidewalk_model.json out-dir [--threads=N]
 */
public final class CalibrationMain {
    private static final long SEED = 20261125L;
    private static final String ALL = "ALL_AUDITED";

    private CalibrationMain() {
    }

    /** Union-find over graph vertices with eligible component mass. */
    private static final class Components {
        final int[] parent;
        final byte[] rank;
        final double[] eligible;

        Components(int n) {
            parent = new int[n];
            rank = new byte[n];
            eligible = new double[n];
            for (int i = 0; i < n; i++) {
                parent[i] = i;
            }
        }

        Components(Components other) {
            parent = other.parent.clone();
            rank = other.rank.clone();
            eligible = other.eligible.clone();
        }

        int find(int node) {
            while (parent[node] != node) {
                parent[node] = parent[parent[node]];
                node = parent[node];
            }
            return node;
        }

        void union(int a, int b) {
            a = find(a);
            b = find(b);
            if (a == b) {
                return;
            }
            if (rank[a] < rank[b]) {
                int swap = a;
                a = b;
                b = swap;
            }
            parent[b] = a;
            if (rank[a] == rank[b]) {
                rank[a]++;
            }
            eligible[a] += eligible[b];
            eligible[b] = 0;
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            System.err.println("Usage: CalibrationMain <pbf> <strata.json> <profile.json> "
                    + "<sidewalk_model.json> <out-dir> [--threads=N]");
            System.exit(2);
        }
        File pbf = new File(args[0]);
        File strataFile = new File(args[1]);
        AccessProfile profile = AccessProfile.load(new File(args[2]));
        JsonNode model = new ObjectMapper().readTree(new File(args[3]));
        Path out = Path.of(args[4]);
        int threads = Math.max(2, Runtime.getRuntime().availableProcessors() - 1);
        for (int i = 5; i < args.length; i++) {
            if (args[i].startsWith("--threads=")) {
                threads = Integer.parseInt(args[i].substring(10));
            }
        }
        Files.createDirectories(out);
        long start = System.currentTimeMillis();

        Set<String> scope = new TreeSet<>();
        model.path("city_groups").fieldNames().forEachRemaining(scope::add);
        StrataIndex strata = StrataConfig.load(strataFile).index(strataFile);
        PbfCensus census = new PbfCensus(pbf, strata, threads).routingProfile(profile);
        census.run();
        RoutingGraph graph = census.graph(profile, "extended");
        double tau = profile.minUsableComponentKm();
        log(start, "graph: %,d ways, %,d vertices", graph.ways.size(), graph.vertexCount);

        int n = graph.ways.size();
        String[] lau = new String[n];
        String[] group = new String[n];
        String[] streetKey = new String[n];
        boolean[] candidate = new boolean[n];
        boolean[] otherUnknown = new boolean[n];
        boolean[] duplicate = new boolean[n];
        EdgeState[] imputedPresent = new EdgeState[n];
        Set<Long> seen = new HashSet<>();
        Map<String, double[]> candidateStats = new TreeMap<>();
        for (int i = 0; i < n; i++) {
            RoutingGraph.Way way = graph.ways.get(i);
            duplicate[i] = !seen.add(way.source().id());
            for (String s : strata.strataFor(way.lat(), way.lon())) {
                if (scope.contains(s)) {
                    lau[i] = s;
                }
            }
            if (way.state() == EdgeState.UNKNOWN) {
                boolean roadSidewalk = "road_sidewalk".equals(profile.undecidedReason(way.source()));
                otherUnknown[i] = !roadSidewalk;
                candidate[i] = roadSidewalk && way.eligible() && lau[i] != null;
            }
            if (candidate[i]) {
                group[i] = group(way.source().highwayClass());
                String name = way.source().tags().get("name");
                streetKey[i] = name == null || name.isBlank()
                        ? "way:" + way.source().id() : lau[i] + "|" + group[i] + "|" + name.trim();
                imputedPresent[i] = profile.evaluate(withSidewalk(way.source()));
                double[] v = candidateStats.computeIfAbsent(lau[i] + "\t" + group[i], k -> new double[2]);
                v[0]++;
                v[1] += way.km();
            }
        }
        log(start, "candidates: %,d ways", candidateStats.values().stream().mapToLong(v -> (long) v[0]).sum());
        try (PrintWriter w = writer(out.resolve("calibration_candidates.csv"))) {
            w.println("lau,group,ways,km");
            candidateStats.forEach((k, v) -> {
                String[] p = k.split("\t");
                w.printf(Locale.ROOT, "%s,%s,%d,%.3f%n", q(p[0]), p[1], (long) v[0], v[1]);
            });
        }

        // Base closures. C1 base equals the pessimistic closure.
        Components pess = build(graph, n, duplicate, i -> graph.ways.get(i).state() == EdgeState.PASS, false);
        Components opt = build(graph, n, duplicate, i -> graph.ways.get(i).state() != EdgeState.BLOCK, true);
        Components c2base = build(graph, n, duplicate, i -> graph.ways.get(i).state() == EdgeState.PASS
                || (graph.ways.get(i).state() == EdgeState.UNKNOWN && otherUnknown[i]), true);
        log(start, "base closures built");

        java.util.function.IntPredicate passWay = i -> graph.ways.get(i).state() == EdgeState.PASS;
        java.util.function.IntPredicate c2Way = i -> passWay.test(i)
                || (graph.ways.get(i).state() == EdgeState.UNKNOWN && otherUnknown[i]);

        try (PrintWriter w = writer(out.resolve("calibration_baseline.csv"))) {
            w.println("scenario,lau,usable_km,eligible_km,share_pct");
            write(w, "pessimistic", evaluate(graph, pess, lau, passWay, tau));
            write(w, "optimistic", evaluate(graph, opt, lau,
                    i -> graph.ways.get(i).state() != EdgeState.BLOCK, tau));
            boolean[] all = new boolean[n];
            for (int i = 0; i < n; i++) {
                all[i] = candidate[i] && imputedPresent[i] == EdgeState.PASS;
            }
            write(w, "C1_all_present", scenario(graph, pess, lau, all, passWay, false, tau));
            write(w, "C2_all_present", scenario(graph, c2base, lau, all, c2Way, true, tau));
            write(w, "C2_all_absent", evaluate(graph, c2base, lau, c2Way, tau));
        }
        log(start, "baselines written");

        List<Integer> candidates = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            if (candidate[i]) {
                candidates.add(i);
            }
        }
        SplittableRandom rng = new SplittableRandom(SEED);
        try (PrintWriter w = writer(out.resolve("calibration_replicates.csv"))) {
            w.println("variant,mode,scenario,replicate,lau,usable_km,eligible_km,share_pct,present_km,candidate_km");
            JsonNode variants = model.path("variants");
            for (String variant : List.of("mar", "unresolved_absent", "unresolved_present")) {
                JsonNode reps = variants.path(variant).path("replicates");
                List<String> modes = variant.equals("mar")
                        ? List.of("independent", "street") : List.of("independent");
                for (String mode : modes) {
                    for (int r = 0; r < reps.size(); r++) {
                        JsonNode table = reps.get(r);
                        boolean[] present = new boolean[n];
                        Map<String, Boolean> shared = new HashMap<>();
                        Map<String, double[]> presentKm = new HashMap<>();
                        for (int i : candidates) {
                            double p = table.path(lau[i]).path(group[i]).asDouble(Double.NaN);
                            if (!Double.isFinite(p)) {
                                throw new IllegalStateException("No probability for " + lau[i] + "/" + group[i]);
                            }
                            boolean draw = mode.equals("street")
                                    ? shared.computeIfAbsent(streetKey[i], k -> rng.nextDouble() < p)
                                    : rng.nextDouble() < p;
                            present[i] = draw && imputedPresent[i] == EdgeState.PASS;
                            double km = graph.ways.get(i).km();
                            for (String key : List.of(lau[i], ALL)) {
                                double[] v = presentKm.computeIfAbsent(key, k -> new double[2]);
                                v[1] += km;
                                if (present[i]) {
                                    v[0] += km;
                                }
                            }
                        }
                        for (String sc : List.of("C1", "C2")) {
                            Map<String, double[]> result = sc.equals("C1")
                                    ? scenario(graph, pess, lau, present, passWay, false, tau)
                                    : scenario(graph, c2base, lau, present, c2Way, true, tau);
                            for (Map.Entry<String, double[]> e : result.entrySet()) {
                                double[] v = e.getValue();
                                double[] pk = presentKm.getOrDefault(e.getKey(), new double[2]);
                                w.printf(Locale.ROOT, "%s,%s,%s,%d,%s,%.3f,%.3f,%.4f,%.3f,%.3f%n",
                                        variant, mode, sc, r, q(e.getKey()), v[0], v[1],
                                        100 * v[0] / v[1], pk[0], pk[1]);
                            }
                        }
                    }
                    log(start, "variant %s/%s: %d replicates", variant, mode, reps.size());
                }
            }
        }
        log(start, "done");
    }

    private static Map<String, double[]> scenario(RoutingGraph graph, Components base, String[] lau,
                                                  boolean[] present, java.util.function.IntPredicate admitted,
                                                  boolean optimisticGates, double tau) {
        Components c = new Components(base);
        for (int i = 0; i < present.length; i++) {
            if (present[i]) {
                add(graph, c, graph.ways.get(i), optimisticGates);
            }
        }
        return evaluate(graph, c, lau, i -> admitted.test(i) || present[i], tau);
    }

    private static Components build(RoutingGraph graph, int n, boolean[] duplicate,
                                    java.util.function.IntPredicate admit, boolean optimisticGates) {
        Components c = new Components(graph.vertexCount);
        for (int i = 0; i < n; i++) {
            if (!duplicate[i] && admit.test(i)) {
                add(graph, c, graph.ways.get(i), optimisticGates);
            }
        }
        return c;
    }

    private static void add(RoutingGraph graph, Components c, RoutingGraph.Way way, boolean optimisticGates) {
        graph.edges(way, (a, b, oa, ob, km) -> {
            transition(graph, c, a, oa, optimisticGates);
            transition(graph, c, b, ob, optimisticGates);
            c.union(a, b);
            if (way.eligible()) {
                c.eligible[c.find(a)] += km;
            }
        });
    }

    private static void transition(RoutingGraph graph, Components c, int port, int original,
                                   boolean optimisticGates) {
        if (port == original) {
            return;
        }
        EdgeState state = graph.transitions.getOrDefault(original, EdgeState.PASS);
        if (state == EdgeState.PASS || (optimisticGates && state == EdgeState.UNKNOWN)) {
            c.union(port, original);
        }
    }

    /** Usable and total eligible km per audited community and in total. */
    private static Map<String, double[]> evaluate(RoutingGraph graph, Components c, String[] lau,
                                                  java.util.function.IntPredicate admitted, double tau) {
        Map<String, double[]> out = new LinkedHashMap<>();
        out.put(ALL, new double[2]);
        for (int i = 0; i < lau.length; i++) {
            RoutingGraph.Way way = graph.ways.get(i);
            if (lau[i] == null || !way.eligible()) {
                continue;
            }
            boolean ok = admitted.test(i);
            double[] local = out.computeIfAbsent(lau[i], k -> new double[2]);
            double[] total = out.get(ALL);
            graph.edges(way, (a, b, oa, ob, km) -> {
                local[1] += km;
                total[1] += km;
                if (ok && c.eligible[c.find(a)] >= tau) {
                    local[0] += km;
                    total[0] += km;
                }
            });
        }
        return out;
    }

    private static RoadWay withSidewalk(RoadWay w) {
        Map<String, String> tags = new HashMap<>(w.tags());
        tags.put("sidewalk", "both");
        return new RoadWay(w.id(), w.highwayClass(), w.drivable(), w.pedestrian(), w.separateSidewalk(),
                w.crossingWay(), tags, w.nodeRefs(), w.editedAt(), w.version());
    }

    static String group(String highway) {
        if (highway == null) {
            return "service_unclassified";
        }
        return switch (highway) {
            case "primary", "primary_link" -> "primary";
            case "secondary", "secondary_link", "tertiary", "tertiary_link" -> "secondary_tertiary";
            case "residential", "living_street", "road" -> "residential";
            default -> "service_unclassified";
        };
    }

    private static void write(PrintWriter w, String scenario, Map<String, double[]> values) {
        values.forEach((k, v) -> w.printf(Locale.ROOT, "%s,%s,%.3f,%.3f,%.4f%n",
                scenario, q(k), v[0], v[1], 100 * v[0] / v[1]));
    }

    private static PrintWriter writer(Path path) throws java.io.IOException {
        return new PrintWriter(Files.newBufferedWriter(path, StandardCharsets.UTF_8));
    }

    private static String q(String value) {
        return "\"" + value.replace("\"", "\"\"") + "\"";
    }

    private static void log(long start, String fmt, Object... args) {
        System.err.printf(Locale.ROOT, "[%6.1f s] " + fmt + "%n",
                java.util.stream.Stream.concat(
                        java.util.stream.Stream.of((System.currentTimeMillis() - start) / 1000.0),
                        java.util.Arrays.stream(args)).toArray());
    }
}
