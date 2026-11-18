package gr.mantzoros.osmaccess;

import gr.mantzoros.osmaccess.PbfCensus.CensusResult;

import java.io.IOException;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeMap;

/** Writes the census CSVs (see README) from a completed CensusResult. */
public final class MetricsWriter {

    private final CensusResult r;

    public MetricsWriter(CensusResult result) {
        this.r = result;
    }

    public void writeAll(Path outDir) throws IOException {
        Files.createDirectories(outDir);
        writeWayMetrics(outDir.resolve("way_metrics.csv"));
        writeCrossingMetrics(outDir.resolve("crossing_metrics.csv"));
        writeSummary(outDir.resolve("summary.csv"));
        writeTagValues(outDir.resolve("tag_values.csv"));
        writeProvenance(outDir.resolve("provenance.csv"));
        writeDestinations(outDir.resolve("destinations.csv"));
        writeSpatialCoverage(outDir.resolve("spatial_coverage.csv"));
        System.err.println("Wrote way_metrics.csv, crossing_metrics.csv, summary.csv, "
                + "tag_values.csv, provenance.csv, destinations.csv to " + outDir);
    }

    private List<String> orderedStrata() {
        return r.orderedStrata();
    }

    private void writeWayMetrics(Path file) throws IOException {
        String[] families = TagInventory.WAY_TAG_FAMILIES;
        int f = families.length;
        try (PrintWriter out = writer(file)) {
            StringBuilder header =
                    new StringBuilder("stratum,group,layer,highway_class,class_group,ways,total_km");
            for (String fam : families) {
                header.append(",km_").append(fam);
            }
            for (String fam : families) {
                header.append(",km_aff_").append(fam);
            }
            out.println(header);
            for (String stratum : orderedStrata()) {
                Map<String, double[]> byClass = r.wayMetrics.get(stratum);
                if (byClass == null) {
                    continue;
                }
                for (Map.Entry<String, double[]> e : new TreeMap<>(byClass).entrySet()) {
                    String cls = e.getKey();
                    double[] acc = e.getValue();
                    StringBuilder row = new StringBuilder();
                    row.append(q(stratum)).append(',').append(q(r.groupOf(stratum))).append(',')
                            .append(r.layerOf(stratum)).append(',')
                            .append(cls).append(',')
                            .append(TagInventory.isDrivable(cls) ? "drivable" : "pedestrian").append(',')
                            .append((long) acc[0]).append(',').append(fmt(acc[1]));
                    for (int i = 0; i < 2 * f; i++) {
                        row.append(',').append(fmt(acc[2 + i]));
                    }
                    out.println(row);
                }
            }
        }
    }

    private void writeCrossingMetrics(Path file) throws IOException {
        try (PrintWriter out = writer(file)) {
            out.println("stratum,group,layer,crossings,schema_node_only,schema_way_only,schema_both,"
                    + "crossing_node_members,crossing_way_members,"
                    + "crossing_subtype,markings_any,markings_modern,markings_legacy,markings_marked,"
                    + "crossing_island,crossing_island_yes,crossing_tactile,crossing_tactile_yes,"
                    + "crossing_button,crossing_signalised,"
                    + "crossing_kerb,crossing_kerb_accessible,crossing_kerb_barrier,"
                    + "signal_nodes,signal_sound,signal_sound_yes,signal_vibration,signal_vibration_yes,"
                    + "kerb_nodes,kerb_accessible,kerb_barrier,kerb_none,kerb_unspecified,"
                    + "intersections,crossings_per_100_intersections,junctions_degree3,"
                    + "crossings_per_100_junctions,crossing_conflicts,crossing_sound,"
                    + "crossing_sound_yes,legacy_crossing_nodes,legacy_crossing_subtype,legacy_crossing_tactile");
            for (String s : orderedStrata()) {
                long crossings = r.node(s, "crossings");
                long intersections = r.node(s, "intersections");
                double per100 = intersections == 0 ? Double.NaN : 100.0 * crossings / intersections;
                out.printf(Locale.ROOT,
                        "%s,%s,%s,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,"
                                + "%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%s,%d,%s,%d,%d,%d,%d,%d,%d%n",
                        q(s), q(r.groupOf(s)), r.layerOf(s),
                        crossings,
                        r.node(s, "crossing_schema_node_only"),
                        r.node(s, "crossing_schema_way_only"),
                        r.node(s, "crossing_schema_both"),
                        r.node(s, "crossing_node_members"),
                        r.node(s, "crossing_way_members"),
                        r.node(s, "crossing_subtype"),
                        r.node(s, "markings_any"),
                        r.node(s, "markings_modern"),
                        r.node(s, "markings_legacy"),
                        r.node(s, "markings_marked"),
                        r.node(s, "crossing_island"),
                        r.node(s, "crossing_island_yes"),
                        r.node(s, "crossing_tactile"),
                        r.node(s, "crossing_tactile_yes"),
                        r.node(s, "crossing_button"),
                        r.node(s, "crossing_signalised"),
                        r.node(s, "crossing_kerb"),
                        r.node(s, "crossing_kerb_accessible"),
                        r.node(s, "crossing_kerb_barrier"),
                        r.node(s, "signal_nodes"),
                        r.node(s, "signal_sound"),
                        r.node(s, "signal_sound_yes"),
                        r.node(s, "signal_vibration"),
                        r.node(s, "signal_vibration_yes"),
                        r.node(s, "kerb_nodes"),
                        r.node(s, "kerb_" + TagInventory.KERB_ACCESSIBLE),
                        r.node(s, "kerb_" + TagInventory.KERB_BARRIER),
                        r.node(s, "kerb_" + TagInventory.KERB_NONE),
                        r.node(s, "kerb_" + TagInventory.KERB_UNSPECIFIED),
                        intersections,
                        fmt(per100), r.node(s, "junctions_degree3"),
                        fmt(ratio(crossings, r.node(s, "junctions_degree3"))),
                        r.node(s, "crossing_conflicts"), r.node(s, "crossing_sound"),
                        r.node(s, "crossing_sound_yes"), r.node(s, "legacy_crossing_nodes"),
                        r.node(s, "legacy_crossing_subtype"), r.node(s, "legacy_crossing_tactile"));
            }
        }
    }

    private void writeSummary(Path file) throws IOException {
        try (PrintWriter out = writer(file)) {
            out.println("stratum,group,layer,drivable_km,pedestrian_km,separate_sidewalk_km,"
                    + "name_pct,sidewalk_pct,sidewalk_aff_pct,surface_pct,lit_pct,lit_aff_pct,"
                    + "maxspeed_pct,smoothness_pct,"
                    + "crossings,intersections,crossings_per_100_intersections,"
                    + "schema_way_share_pct,subtype_pct,markings_any_pct,markings_modern_share_pct,"
                    + "tactile_pct,tactile_yes_pct,kerb_pct,kerb_accessible_pct,"
                    + "signals,sound_pct,sound_yes_pct,bpri_v0,bpri_v1,junctions_degree3,"
                    + "crossings_per_100_junctions,model_version");
            for (String s : orderedStrata()) {
                double[] dr = sumUniverse(s, true);
                double[] pe = sumUniverse(s, false);
                double km = dr[1];

                double namePct = pct(dr, "name", false);
                double sidewalkPct = pct(dr, "sidewalk", false);
                double sidewalkAff = pct(dr, "sidewalk", true);
                double surfacePct = pct(dr, "surface", false);
                double litPct = pct(dr, "lit", false);
                double litAff = pct(dr, "lit", true);
                double maxspeedPct = pct(dr, "maxspeed", false);
                double smoothPct = pct(dr, "smoothness", false);

                long crossings = r.node(s, "crossings");
                long intersections = r.node(s, "intersections");
                long signals = r.node(s, "signal_nodes");
                double per100 = intersections == 0 ? Double.NaN : 100.0 * crossings / intersections;

                long wayOnly = r.node(s, "crossing_schema_way_only");
                long both = r.node(s, "crossing_schema_both");
                double waySharePct = ratio(wayOnly + both, crossings);
                double subtypePct = ratio(r.node(s, "crossing_subtype"), crossings);
                double markingsAnyPct = ratio(r.node(s, "markings_any"), crossings);
                double modernSharePct = ratio(r.node(s, "markings_modern"), r.node(s, "markings_any"));
                double tactilePct = ratio(r.node(s, "crossing_tactile"), crossings);
                double tactileYesPct = ratio(r.node(s, "crossing_tactile_yes"), crossings);
                double kerbPct = ratio(r.node(s, "crossing_kerb"), crossings);
                double kerbAccPct = ratio(r.node(s, "crossing_kerb_accessible"), crossings);
                double soundPct = ratio(r.node(s, "signal_sound"), signals);
                double soundYesPct = ratio(r.node(s, "signal_sound_yes"), signals);

                double coverage = intersections == 0 ? Double.NaN
                        : Math.min(1.0, (double) crossings / intersections);

                double v0 = weighted(
                        new double[]{intersections == 0 ? Double.NaN
                                : Math.min(1.0, (double) r.node(s, "legacy_crossing_nodes") / intersections),
                                ratio(r.node(s, "legacy_crossing_subtype"),
                                        r.node(s, "legacy_crossing_nodes")) / 100,
                                ratio(r.node(s, "legacy_crossing_tactile"),
                                        r.node(s, "legacy_crossing_nodes")) / 100, soundPct / 100,
                                sidewalkPct / 100, litPct / 100, surfacePct / 100, namePct / 100},
                        new double[]{0.20, 0.15, 0.15, 0.10, 0.15, 0.10, 0.05, 0.10});

                double v1 = weighted(
                        new double[]{coverage, subtypePct / 100, markingsAnyPct / 100,
                                tactilePct / 100, soundPct / 100, kerbPct / 100,
                                sidewalkAff / 100, litPct / 100, surfacePct / 100, namePct / 100},
                        new double[]{0.20, 0.10, 0.05, 0.15, 0.10, 0.05, 0.15, 0.10, 0.05, 0.05});

                out.printf(Locale.ROOT,
                        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%d,%d,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                                + "%d,%s,%s,%s,%s,%d,%s,%s%n",
                        q(s), q(r.groupOf(s)), r.layerOf(s),
                        fmt(km), fmt(pe[1]), fmt(r.separateSidewalkKm.getOrDefault(s, 0.0)),
                        fmt(namePct), fmt(sidewalkPct), fmt(sidewalkAff), fmt(surfacePct),
                        fmt(litPct), fmt(litAff), fmt(maxspeedPct), fmt(smoothPct),
                        crossings, intersections, fmt(per100),
                        fmt(waySharePct), fmt(subtypePct), fmt(markingsAnyPct), fmt(modernSharePct),
                        fmt(tactilePct), fmt(tactileYesPct), fmt(kerbPct), fmt(kerbAccPct),
                        signals, fmt(soundPct), fmt(soundYesPct), fmt(v0), fmt(v1),
                        r.node(s, "junctions_degree3"),
                        fmt(ratio(crossings, r.node(s, "junctions_degree3"))),
                        FeasibilityAnalysis.MODEL_VERSION);
            }
        }
    }

    /**
     * Long-format value distributions. This is what separates "the map says
     * something about sidewalks" from "the map says there IS a sidewalk" - the
     * distinction a tag-presence percentage cannot express.
     */
    private void writeTagValues(Path file) throws IOException {
        try (PrintWriter out = writer(file)) {
            out.println("stratum,group,layer,feature_type,universe,key,value,count,km");
            for (String s : orderedStrata()) {
                Map<String, Map<String, Double>> wv = r.wayValues.get(s);
                if (wv != null) {
                    for (Map.Entry<String, Map<String, Double>> e : new TreeMap<>(wv).entrySet()) {
                        String[] parts = e.getKey().split("\\|", 2);
                        for (Map.Entry<String, Double> v : new TreeMap<>(e.getValue()).entrySet()) {
                            out.printf(Locale.ROOT, "%s,%s,%s,way,%s,%s,%s,,%s%n",
                                    q(s), q(r.groupOf(s)), r.layerOf(s), parts[0], parts[1],
                                    q(v.getKey()), fmt(v.getValue()));
                        }
                    }
                }
                Map<String, Map<String, Long>> cv = r.crossingValues.get(s);
                if (cv != null) {
                    for (Map.Entry<String, Map<String, Long>> e : new TreeMap<>(cv).entrySet()) {
                        for (Map.Entry<String, Long> v : new TreeMap<>(e.getValue()).entrySet()) {
                            out.printf(Locale.ROOT, "%s,%s,%s,crossing,crossing,%s,%s,%d,%n",
                                    q(s), q(r.groupOf(s)), r.layerOf(s), e.getKey(),
                                    q(v.getKey()), v.getValue());
                        }
                    }
                }
            }
        }
    }

    /**
     * Recency of the accessibility data, from the extract's per-object timestamps.
     *
     * <p>This answers "how stale is the map here" from a single pinned snapshot, with no
     * full-history extract: median and quartile edit year, plus the share edited recently
     * and the share untouched for over five years. It does NOT answer "who mapped it" -
     * public Geofabrik extracts zero out uid/username, so contributor concentration still
     * needs the authenticated full-history extract (docs/data-sources.md).
     */
    private void writeProvenance(Path file) throws IOException {
        int snapshotYear = r.snapshotEpochSec <= 0 ? 0
                : java.time.Instant.ofEpochSecond(r.snapshotEpochSec)
                        .atZone(java.time.ZoneOffset.UTC).getYear();
        try (PrintWriter out = writer(file)) {
            out.println("stratum,group,layer,feature_type,n,median_edit_year,p25_edit_year,"
                    + "p75_edit_year,pct_edited_last_2y,pct_older_than_5y,snapshot_year");
            for (String s : orderedStrata()) {
                Map<String, Map<Integer, Double>> byType = r.editYears.get(s);
                if (byType == null) {
                    continue;
                }
                for (Map.Entry<String, Map<Integer, Double>> e : new TreeMap<>(byType).entrySet()) {
                    Map<Integer, Double> hist = e.getValue();
                    double n = hist.values().stream().mapToDouble(Double::doubleValue).sum();
                    if (n <= 0) {
                        continue;
                    }
                    double recent = 0;
                    double old = 0;
                    for (Map.Entry<Integer, Double> b : hist.entrySet()) {
                        if (snapshotYear - b.getKey() <= 2) {
                            recent += b.getValue();
                        }
                        if (snapshotYear - b.getKey() > 5) {
                            old += b.getValue();
                        }
                    }
                    out.printf(Locale.ROOT, "%s,%s,%s,%s,%s,%d,%d,%d,%s,%s,%d%n",
                            q(s), q(r.groupOf(s)), r.layerOf(s), e.getKey(), fmt(n),
                            quantileYear(hist, n, 0.50), quantileYear(hist, n, 0.25),
                            quantileYear(hist, n, 0.75),
                            fmt(100.0 * recent / n), fmt(100.0 * old / n), snapshotYear);
                }
            }
        }
    }

    /** Quantile over a year histogram (weights are counts for nodes, km for ways). */
    private static int quantileYear(Map<Integer, Double> hist, double total, double p) {
        double target = total * p;
        double acc = 0;
        for (Map.Entry<Integer, Double> e : new TreeMap<>(hist).entrySet()) {
            acc += e.getValue();
            if (acc >= target) {
                return e.getKey();
            }
        }
        return 0;
    }

    /**
     * Destination-side accessibility: transit stops, stations and POIs, with the
     * attributes that decide whether a blind traveller can use them.
     *
     * <p>Reported separately from the network census because it answers a different
     * question. A perfectly routable pavement that ends at an unidentifiable door has
     * not delivered the traveller anywhere.
     */
    private void writeDestinations(Path file) throws IOException {
        String[] kinds = {"stop", "station", "poi"};
        try (PrintWriter out = writer(file)) {
            // Keep braille as key presence for existing readers; braille_yes is affirmation.
            out.println("stratum,group,layer,kind,total,named,tactile_stated,tactile_yes,"
                    + "wheelchair_stated,wheelchair_yes,wheelchair_limited,wheelchair_no,"
                    + "braille,departures,shelter,"
                    + "named_pct,tactile_pct,wheelchair_pct,braille_pct,"
                    + "braille_stated,braille_yes,tactile_writing_stated,tactile_writing_yes");
            for (String s : orderedStrata()) {
                for (String k : kinds) {
                    long total = r.node(s, k + "_total");
                    if (total == 0) {
                        continue;
                    }
                    long named = r.node(s, k + "_named");
                    long tac = r.node(s, k + "_tactile_stated");
                    long wcs = r.node(s, k + "_wheelchair_stated");
                    long br = r.node(s, k + "_braille");
                    long brYes = r.node(s, k + "_braille_yes");
                    out.printf(Locale.ROOT,
                            "%s,%s,%s,%s,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%s,%s,%s,%s,%d,%d,%d,%d%n",
                            q(s), q(r.groupOf(s)), r.layerOf(s), k,
                            total, named, tac, r.node(s, k + "_tactile_yes"),
                            wcs, r.node(s, k + "_wheelchair_yes"),
                            r.node(s, k + "_wheelchair_limited"), r.node(s, k + "_wheelchair_no"),
                            br, r.node(s, k + "_departures"), r.node(s, k + "_shelter"),
                            fmt(ratio(named, total)), fmt(ratio(tac, total)),
                            fmt(ratio(wcs, total)), fmt(ratio(br, total)),
                            br, brYes,
                            r.node(s, k + "_tactile_writing_stated"),
                            r.node(s, k + "_tactile_writing_yes"));
                }
            }
        }
    }

    // --------------------------------------------------------------- helpers

    /** Weighted mean over the non-NaN components, weights renormalised. */
    private static double weighted(double[] values, double[] weights) {
        double sum = 0;
        double weightSum = 0;
        for (int i = 0; i < values.length; i++) {
            if (!Double.isNaN(values[i])) {
                sum += weights[i] * values[i];
                weightSum += weights[i];
            }

        }
        return weightSum == 0 ? Double.NaN : sum / weightSum;
    }

    private void writeSpatialCoverage(Path file) throws IOException {
        Map<String, double[]> layers = new TreeMap<>();
        for (String stratum : orderedStrata()) {
            if (stratum.equals(StrataIndex.NATIONAL)) continue;
            double[] acc = layers.computeIfAbsent(r.layerOf(stratum), k -> new double[2]);
            acc[0] += sumUniverse(stratum, true)[1];
            acc[1] += r.node(stratum, "crossings");
        }
        double nationalKm = sumUniverse(StrataIndex.NATIONAL, true)[1];
        long nationalCrossings = r.node(StrataIndex.NATIONAL, "crossings");
        try (PrintWriter out = writer(file)) {
            out.println("layer,assigned_drivable_km,national_drivable_km,unassigned_drivable_km,"
                    + "assigned_crossings,national_crossings,unassigned_crossings,assignment,model_version");
            for (Map.Entry<String, double[]> layer : layers.entrySet()) {
                double[] a = layer.getValue();
                out.printf(Locale.ROOT, "%s,%.3f,%.3f,%.3f,%.0f,%d,%.0f,whole_way_middle_node,%s%n",
                        q(layer.getKey()), a[0], nationalKm, nationalKm - a[0],
                        a[1], nationalCrossings, nationalCrossings - a[1],
                        FeasibilityAnalysis.MODEL_VERSION);
            }
        }
    }

    /** Sums the per-class way metrics over the drivable (or pedestrian) classes. */
    private double[] sumUniverse(String stratum, boolean drivable) {
        int f = TagInventory.WAY_TAG_FAMILIES.length;
        double[] total = new double[2 + 2 * f];
        Map<String, double[]> byClass = r.wayMetrics.get(stratum);
        if (byClass == null) {
            return total;
        }
        for (Map.Entry<String, double[]> e : byClass.entrySet()) {
            if (TagInventory.isDrivable(e.getKey()) != drivable) {
                continue;
            }
            for (int i = 0; i < total.length; i++) {
                total[i] += e.getValue()[i];
            }
        }
        return total;
    }

    private static double pct(double[] acc, String family, boolean affirmative) {
        double totalKm = acc[1];
        if (totalKm == 0) {
            return Double.NaN;
        }
        String[] families = TagInventory.WAY_TAG_FAMILIES;
        int f = families.length;
        for (int i = 0; i < f; i++) {
            if (families[i].equals(family)) {
                return 100.0 * acc[2 + (affirmative ? f : 0) + i] / totalKm;
            }
        }
        throw new IllegalArgumentException("unknown family " + family);
    }

    private static double ratio(long numerator, long denominator) {
        return denominator == 0 ? Double.NaN : 100.0 * numerator / denominator;
    }

    private static String fmt(double v) {
        if (Double.isNaN(v)) {
            return "";
        }
        return String.format(Locale.ROOT, "%.3f", v);
    }

    /** Municipality names contain commas and Greek text; quote defensively. */
    private static String q(String s) {
        if (s == null) {
            return "";
        }
        if (s.indexOf(',') < 0 && s.indexOf('"') < 0
                && s.indexOf('\n') < 0 && s.indexOf('\r') < 0) {
            return s;
        }
        return '"' + s.replace("\"", "\"\"") + '"';
    }

    private static PrintWriter writer(Path file) throws IOException {
        return new PrintWriter(Files.newBufferedWriter(file, StandardCharsets.UTF_8));
    }
}
