package gr.mantzoros.osmaccess;

import java.io.IOException;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** All-positive, whole-way survey scenarios; not expected yield or optimal selection. */
public final class EditTargeting {
    private static final Set<String> ACTIONABLE = Set.of(
            "road_sidewalk", "crossing_attributes", "steps_handrail");
    public static final String METHOD = "static_initial_consolidation";
    public static final String SCENARIO = "way_state_override_fixed_node_transitions";
    private final RoutingGraph graph;
    private final StrataIndex strata;
    record Candidate(RoutingGraph.Way way, String reason, double priority) { }

    EditTargeting(RoutingGraph graph, StrataIndex strata) {
        this.graph = graph;
        this.strata = strata;
    }

    public void run(Path directory, int topN) throws IOException {
        if (topN < 0) throw new IllegalArgumentException("target count must be nonnegative");
        Files.createDirectories(directory);
        double threshold = graph.profile.minUsableComponentKm();
        RoutingGraph.Closure current = graph.closure(false, threshold);
        double before = current.eligibleUsable;
        double totalBefore = java.util.Arrays.stream(current.mass).sum();
        List<Candidate> candidates = new ArrayList<>();
        for (RoutingGraph.Way way : graph.ways) {
            String reason = graph.profile.undecidedReason(way.source());
            if (way.state() == EdgeState.UNKNOWN && way.eligible() && ACTIONABLE.contains(reason)) {
                candidates.add(new Candidate(way, reason, current.consolidationScore(way)));
            }
        }
        candidates.sort(Comparator.comparingDouble(Candidate::priority).reversed()
                .thenComparingLong(c -> c.way().source().id()));
        int count = Math.min(topN, candidates.size());
        double consolidation = 0;
        double surveyKm = 0;
        try (PrintWriter out = writer(directory.resolve("edit_targets.csv"))) {
            out.println("rank,osm_way_id,highway,missing,stratum,lat,lon,own_km,unlocked_km,"
                    + "cumulative_km,osm_link,eligible_own_km,marginal_usable_km,"
                    + "cumulative_usable_gain_km,usable_km_after,selection_method,survey_km,model_version,"
                    + "intervention_model");
            for (int i = 0; i < count; i++) {
                Candidate candidate = candidates.get(i);
                RoutingGraph.Way way = candidate.way();
                double score = current.consolidationScore(way);
                double marginal = current.add(way);
                consolidation += score;
                surveyKm += way.km();
                out.printf(Locale.ROOT, "%d,%d,%s,%s,%s,%.7f,%.7f,%.3f,%.3f,%.3f,%s,"
                                + "%.3f,%.3f,%.3f,%.3f,%s,%.3f,%s,%s%n",
                        i + 1, way.source().id(), way.source().highwayClass(),
                        FeasibilityAnalysis.q(question(candidate.reason())),
                        FeasibilityAnalysis.q(namedStratum(way.lat(), way.lon())),
                        way.lat(), way.lon(), way.km(), score, consolidation,
                        "https://www.openstreetmap.org/way/" + way.source().id(),
                        way.km(), marginal, current.eligibleUsable - before, current.eligibleUsable,
                        METHOD, way.km(), FeasibilityAnalysis.MODEL_VERSION, SCENARIO);
            }
        }
        double after = current.eligibleUsable;
        double totalAfter = java.util.Arrays.stream(current.mass).sum();
        double selectedUsable = 0;
        for (int i = 0; i < count; i++) {
            selectedUsable += current.usableLength(candidates.get(i).way());
        }
        // Release the national closure before constructing the independent comparator.
        current = null;
        candidates.sort(Comparator.comparingDouble((Candidate c) -> c.way().km()).reversed()
                .thenComparingLong(c -> c.way().source().id()));
        RoutingGraph.Closure length = graph.closure(false, threshold);
        double lengthSurvey = 0;
        try (PrintWriter out = writer(directory.resolve("edit_targets_length_baseline.csv"))) {
            out.println("rank,osm_way_id,own_km,marginal_usable_km,cumulative_usable_gain_km,"
                    + "usable_km_after,selection_method,cumulative_survey_km,model_version");
            for (int i = 0; i < count; i++) {
                RoutingGraph.Way way = candidates.get(i).way();
                double marginal = length.add(way);
                lengthSurvey += way.km();
                out.printf(Locale.ROOT, "%d,%d,%.3f,%.3f,%.3f,%.3f,length_only,%.3f,%s%n",
                        i + 1, way.source().id(), way.km(), marginal,
                        length.eligibleUsable - before, length.eligibleUsable,
                        lengthSurvey, FeasibilityAnalysis.MODEL_VERSION);
            }
        }
        double lengthSelectedUsable = 0;
        for (int i = 0; i < count; i++) {
            lengthSelectedUsable += length.usableLength(candidates.get(i).way());
        }
        double lengthUsable = length.eligibleUsable;
        length = null;

        // Consolidation per surveyed kilometre. The dominant term in the outcome is a
        // selected way's own length, so ranking on the raw proxy prefers long ways; this
        // divides it out and prefers short ways that bridge large components instead.
        candidates.sort(Comparator.comparingDouble((Candidate c) ->
                        c.way().km() <= 0 ? 0 : c.priority() / c.way().km()).reversed()
                .thenComparingLong(c -> c.way().source().id()));
        RoutingGraph.Closure eff = graph.closure(false, threshold);
        double effSurvey = 0;
        try (PrintWriter out = writer(directory.resolve("edit_targets_efficiency.csv"))) {
            out.println("rank,osm_way_id,own_km,marginal_usable_km,cumulative_usable_gain_km,"
                    + "usable_km_after,selection_method,cumulative_survey_km,model_version");
            for (int i = 0; i < count; i++) {
                RoutingGraph.Way way = candidates.get(i).way();
                double marginal = eff.add(way);
                effSurvey += way.km();
                out.printf(Locale.ROOT, "%d,%d,%.3f,%.3f,%.3f,%.3f,consolidation_per_km,%.3f,%s%n",
                        i + 1, way.source().id(), way.km(), marginal,
                        eff.eligibleUsable - before, eff.eligibleUsable,
                        effSurvey, FeasibilityAnalysis.MODEL_VERSION);
            }
        }
        double effSelectedUsable = 0;
        for (int i = 0; i < count; i++) {
            effSelectedUsable += eff.usableLength(candidates.get(i).way());
        }
        double effUsable = eff.eligibleUsable;
        try (PrintWriter out = writer(directory.resolve("edit_targets_summary.csv"))) {
            out.println("profile,candidates,edits_committed,km_unlocked,km_per_edit,"
                    + "guaranteed_km_before,guaranteed_km_after,usable_km_before,usable_km_after,"
                    + "usable_gain_km,usable_multiple,selection_method,survey_km,eligible_own_km,"
                    + "length_baseline_usable_before,length_baseline_usable_after,"
                    + "length_baseline_usable_gain_km,length_baseline_survey_km,"
                    + "graph_scope,component_threshold_km,model_version,intervention_model,"
                    + "selected_usable_km_after,recovered_preexisting_usable_km,"
                    + "length_baseline_selected_usable_km_after,length_baseline_recovered_preexisting_usable_km,"
                    + "efficiency_usable_after,efficiency_usable_gain_km,efficiency_survey_km,"
                    + "efficiency_selected_usable_km_after,efficiency_recovered_preexisting_usable_km");
            out.printf(Locale.ROOT, "%s,%d,%d,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%s,"
                            + "%s,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%s,%.3f,%s,%s,%.3f,%.3f,%.3f,%.3f,"
                            + "%.3f,%.3f,%.3f,%.3f,%.3f%n",
                    graph.profile.id(), candidates.size(), count, consolidation,
                    count == 0 ? 0 : consolidation / count, totalBefore, totalAfter,
                    before, after, after - before,
                    before == 0 ? "" : FeasibilityAnalysis.f(after / before),
                    METHOD, surveyKm, surveyKm, before, lengthUsable,
                    lengthUsable - before, lengthSurvey, graph.scope, threshold,
                    FeasibilityAnalysis.MODEL_VERSION, SCENARIO,
                    selectedUsable, Math.max(0, after - before - selectedUsable),
                    lengthSelectedUsable, Math.max(0, lengthUsable - before - lengthSelectedUsable),
                    effUsable, effUsable - before, effSurvey,
                    effSelectedUsable, Math.max(0, effUsable - before - effSelectedUsable));
        }
    }

    private static PrintWriter writer(Path file) throws IOException {
        return new PrintWriter(Files.newBufferedWriter(file, StandardCharsets.UTF_8));
    }

    private String question(String reason) {
        return switch (reason) {
            case "road_sidewalk" -> "does this street have a sidewalk?";
            case "crossing_attributes" -> graph.profile.crossingQuestion();
            case "steps_handrail" -> "do these steps have a handrail?";
            default -> reason;
        };
    }

    private String namedStratum(double lat, double lon) {
        String fallback = "";
        for (String name : strata.strataFor(lat, lon)) {
            if (strata.layerOf(name).equals("municipality")) return name;
            if (strata.layerOf(name).equals("circle")) fallback = name;
        }
        return fallback;
    }
}
