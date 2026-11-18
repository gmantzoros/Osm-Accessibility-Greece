package gr.mantzoros.osmaccess;

import java.io.File;
import java.nio.file.Path;
import java.util.Locale;

public final class Main {

    /** Fixed default so the audit draw is reproducible without extra bookkeeping. */
    private static final long DEFAULT_SEED = 20260722L;

    public static void main(String[] args) throws Exception {
        if (args.length < 3) {
            System.err.println("""
                    Usage: java -Xmx4g -jar census.jar <extract.osm.pbf> <strata.json> <output-dir> [options]

                      --threads=N     parser threads (default: cores - 1)
                      --sample=N      also draw N audit cases per stratum group per arm
                                      (docs/validation-protocol.md); writes audit_sample.csv
                      --seed=N        sampling seed (default 20260722, recorded in the output)
                      --sample-arms=A,B  restrict the audit draw to these arms
                                      (sidewalk, commission, omission); default all three
                      --sample-scope=F  restrict the audit sample to the strata listed in this
                                      JSON file (results/urban/scope_laus.json); adds the
                                      sidewalk arm and groups the draw by city
                      --profile=FILE  routing profile JSON; enables the feasibility analysis
                                      (docs/routing-feasibility.md); writes feasibility.csv
                      --targets=N     rank the N highest-leverage edits under that profile
                                      (docs/edit-targeting.md); writes edit_targets.csv
                      --graph-scope=extended|core  graph universe (default extended)
                      --core-sensitivity  additionally rebuild core and extended graphs
                      --component-thresholds=0.5,1,2  recompute both graph scopes at these km cuts
                      --dump-points=F write crossings, signals and intersections with their
                                      coordinates to F, for joining against external detectors
                                      (obeys --sample-scope); implies --sample retention

                    Example:
                      java -Xmx4g -jar census.jar data/greece-latest.osm.pbf data/strata.json \\
                           results/census --sample=50
                    """);
            System.exit(2);
        }
        File pbf = new File(args[0]);
        File strataFile = new File(args[1]);
        Path outDir = Path.of(args[2]);

        int threads = Math.max(2, Runtime.getRuntime().availableProcessors() - 1);
        int sample = 0;
        java.util.Set<String> sampleArms = java.util.Set.of();
        long seed = DEFAULT_SEED;
        File profileFile = null;
        File scopeFile = null;
        File dumpFile = null;
        int targets = 0;
        String graphScope = "extended";
        boolean coreSensitivity = false;
        java.util.Set<Double> thresholds = new java.util.TreeSet<>();
        for (int i = 3; i < args.length; i++) {
            String a = args[i];
            if (a.startsWith("--threads=")) {
                threads = Integer.parseInt(a.substring(10));
            } else if (a.startsWith("--sample=")) {
                sample = Integer.parseInt(a.substring(9));
            } else if (a.startsWith("--seed=")) {
                seed = Long.parseLong(a.substring(7));
            } else if (a.startsWith("--profile=")) {
                profileFile = new File(a.substring(10));
            } else if (a.startsWith("--targets=")) {
                targets = Integer.parseInt(a.substring(10));
            } else if (a.startsWith("--graph-scope=")) {
                graphScope = a.substring("--graph-scope=".length());
                if (!java.util.Set.of("core", "extended").contains(graphScope)) {
                    throw new IllegalArgumentException("graph scope must be core or extended");
                }
            } else if (a.equals("--core-sensitivity")) {
                coreSensitivity = true;
            } else if (a.startsWith("--component-thresholds=")) {
                for (String value : a.substring("--component-thresholds=".length()).split(",")) {
                    double threshold = Double.parseDouble(value);
                    if (!Double.isFinite(threshold) || threshold <= 0) {
                        throw new IllegalArgumentException("thresholds must be finite and positive");
                    }
                    thresholds.add(threshold);
                }
            } else if (a.startsWith("--sample-arms=")) {
                sampleArms = new java.util.LinkedHashSet<>(
                        java.util.List.of(a.substring(14).split(",")));
            } else if (a.startsWith("--sample-scope=")) {
                scopeFile = new File(a.substring(15));
            } else if (a.startsWith("--dump-points=")) {
                dumpFile = new File(a.substring(14));
            } else if (a.matches("\\d+")) {
                threads = Integer.parseInt(a); // backwards compatible positional threads
            } else {
                System.err.println("Unknown option: " + a);
                System.exit(2);
            }
        }

        if (!pbf.isFile()) {
            System.err.println("PBF not found: " + pbf + " - run data/fetch_data.ps1 first.");
            System.exit(1);
        }
        if (!strataFile.isFile()) {
            System.err.println("Strata config not found: " + strataFile);
            System.exit(1);
        }
        if (profileFile != null && !profileFile.isFile()) {
            System.err.println("Routing profile not found: " + profileFile);
            System.exit(1);
        }
        if (scopeFile != null && !scopeFile.isFile()) {
            throw new IllegalArgumentException("Requested sample scope frame not found: " + scopeFile);
        }
        if (sample > 0 && java.nio.file.Files.exists(outDir.resolve("audit_sample.csv"))) {
            throw new IllegalArgumentException("Existing audit_sample.csv will not be overwritten. "
                    + "Use a new output directory for a separately reviewed new sampling frame.");
        }

        long start = System.currentTimeMillis();
        StrataConfig config = StrataConfig.load(strataFile);
        StrataIndex strata = config.index(strataFile);
        java.util.Set<String> sampleScope = scopeFile == null
                ? java.util.Set.of() : readScope(scopeFile, strata);
        System.err.printf(Locale.ROOT, "Census of %s with %d circles + %d polygons + NATIONAL%n",
                pbf.getName(), strata.circleCount(), strata.polygonCount());

        AccessProfile profile = profileFile == null ? null : AccessProfile.load(profileFile);
        if (targets < 0 || (profile == null && (targets > 0 || coreSensitivity
                || !thresholds.isEmpty() || graphScope.equals("core")))) {
            throw new IllegalArgumentException("nonnegative targets and a profile are required for graph options");
        }
        PbfCensus census = new PbfCensus(pbf, strata, threads)
                .retainFeatures(sample > 0 || dumpFile != null).routingProfile(profile);
        PbfCensus.CensusResult result = census.run();
        PbfCensus.write(result, outDir);

        if (dumpFile != null) {
            census.dumpPoints(dumpFile.toPath(), sampleScope);
        }

        if (sample > 0) {
            if (scopeFile != null) {
                System.err.printf(Locale.ROOT, "Audit scope: %,d strata from %s%n",
                        sampleScope.size(), scopeFile.getName());
            }
            var ways = census.waysForSampling();
            new AuditSampler(strata, seed, sample, profile, sampleScope).withArms(sampleArms)
                    .withInputSha256(census.inputSha256())
                    .write(census.crossingFeatures(), census.intersectionPoints(),
                            ways, census.wayMidpoints(ways), outDir);
        }

        FeasibilityAnalysis feasibility = null;
        if (profile != null) {
            feasibility = census.feasibility(profile, graphScope, profile.minUsableComponentKm());
            feasibility.run();
            feasibility.write(outDir);
            if (targets > 0) {
                new EditTargeting(feasibility.graph(), strata).run(outDir, targets);
            }
            if (coreSensitivity || !thresholds.isEmpty()) {
                thresholds.add(profile.minUsableComponentKm());
                Path sensitivity = outDir.resolve("feasibility_sensitivity.csv");
                java.nio.file.Files.deleteIfExists(sensitivity);
                for (String scope : java.util.List.of("extended", "core")) {
                    RoutingGraph graph = scope.equals(graphScope)
                            ? feasibility.graph() : census.graph(profile, scope);
                    for (double threshold : thresholds) {
                        FeasibilityAnalysis variant = new FeasibilityAnalysis(graph, strata, threshold);
                        variant.run();
                        variant.appendSensitivity(sensitivity);
                    }
                }
            }
        }

        System.err.printf(Locale.ROOT, "Done in %.1f s%n", (System.currentTimeMillis() - start) / 1000.0);

        // Headline numbers to stdout - the "first result slide" (plan section 11.4)
        String n = PbfCensus.CensusResult.NATIONAL;
        p("National crossings (unified):     %,d", result.node(n, "crossings"));
        p("  mapped as node only:            %,d", result.node(n, "crossing_schema_node_only"));
        p("  mapped as way only:             %,d", result.node(n, "crossing_schema_way_only"));
        p("  both node and way:              %,d", result.node(n, "crossing_schema_both"));
        p("  tactile_paving stated:          %,d", result.node(n, "crossing_tactile"));
        p("    of which affirmative:         %,d", result.node(n, "crossing_tactile_yes"));
        p("  markings stated (any schema):   %,d", result.node(n, "markings_any"));
        p("    modern crossing:markings:     %,d", result.node(n, "markings_modern"));
        p("    legacy crossing=* value:      %,d", result.node(n, "markings_legacy"));
        p("  kerb stated:                    %,d", result.node(n, "crossing_kerb"));
        p("    of which flush/lowered:       %,d", result.node(n, "crossing_kerb_accessible"));
        p("National traffic signals tagged:  %,d", result.node(n, "signal_nodes"));
        p("  with sound:                     %,d", result.node(n, "signal_sound"));
        p("National structural intersections:%,d", result.node(n, "intersections"));

        if (feasibility != null) {
            double[] nat = feasibility.national();
            System.out.println();
            System.out.printf(Locale.ROOT, "Routing feasibility, walkable network:%n");
            System.out.printf(Locale.ROOT, "  conditional usable (pessimistic): %.1f%%%n",
                    FeasibilityAnalysis.pessimisticPct(nat));
            System.out.printf(Locale.ROOT, "  possibly usable (optimistic):     %.1f%%%n",
                    FeasibilityAnalysis.optimisticPct(nat));
            System.out.printf(Locale.ROOT, "  DECIDABILITY GAP:                 %.1f points%n",
                    FeasibilityAnalysis.optimisticPct(nat) - FeasibilityAnalysis.pessimisticPct(nat));
            System.out.printf(Locale.ROOT, "  UNKNOWN including class policy:   %.1f%%%n",
                    FeasibilityAnalysis.unknownPct(nat));
            System.out.println("  undecidable because of:");
            FeasibilityAnalysis.unknownBreakdownPct(nat).forEach((k, v) ->
                    System.out.printf(Locale.ROOT, "    %-44s %5.1f points%n", k, v));
        }
    }

    private static java.util.Set<String> readScope(File file, StrataIndex strata) throws Exception {
        var root = new com.fasterxml.jackson.databind.ObjectMapper().readTree(file);
        var laus = root == null ? null : root.get("laus");
        if (laus == null || !laus.isArray() || laus.isEmpty()) {
            throw new IllegalArgumentException("Sample scope frame must contain a nonempty laus array: " + file);
        }
        java.util.Set<String> names = new java.util.HashSet<>();
        for (var name : laus) {
            if (!name.isTextual() || name.asText().isBlank()) {
                throw new IllegalArgumentException("Invalid stratum in sample scope frame: " + file);
            }
            names.add(name.asText());
        }
        java.util.Set<String> available = new java.util.HashSet<>();
        for (Stratum s : strata.all()) {
            available.add(s.name());
        }
        for (String name : names) {
            if (!available.contains(name)) {
                throw new IllegalArgumentException("Unknown stratum '" + name
                        + "' in sample scope frame: " + file);
            }
        }
        return java.util.Set.copyOf(names);
    }

    private static void p(String fmt, long v) {
        System.out.printf(Locale.ROOT, fmt + "%n", v);
    }

    private Main() {
    }
}
