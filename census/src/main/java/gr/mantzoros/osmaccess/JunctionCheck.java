package gr.mantzoros.osmaccess;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.wolt.osm.parallelpbf.ParallelBinaryParser;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.regex.Pattern;

/**
 * Locates named street junctions in the PBF and reports sound statements on nearby
 * traffic-signal and crossing nodes. Junction nodes are nodes shared by a way whose name
 * matches street A and a way whose name matches street B inside the city box; when they
 * share none (dual carriageways, squares), nodes of A within 25 m of a node of B are used.
 * Names are compared after removing Greek accents and case.
 *
 * <p>Usage: JunctionCheck extract.osm.pbf junctions.json out.csv [--threads=N]
 */
public final class JunctionCheck {
    private static final double JUNCTION_M = 25;
    private static final double RADIUS_M = 50;
    private static final Set<String> AFFIRMATIVE = Set.of("yes", "walk", "always", "locate", "beep", "voice");

    private record Junction(String city, String label, Pattern a, Pattern b, double[] box) { }

    private JunctionCheck() {
    }

    static String norm(String s) {
        return Normalizer.normalize(s, Normalizer.Form.NFD).replaceAll("\\p{M}", "")
                .toLowerCase(Locale.ROOT).replace('ς', 'σ');
    }

    public static void main(String[] args) throws Exception {
        File pbf = new File(args[0]);
        JsonNode spec = new ObjectMapper().readTree(new File(args[1]));
        int threads = 4;
        for (int i = 3; i < args.length; i++) {
            if (args[i].startsWith("--threads=")) {
                threads = Integer.parseInt(args[i].substring(10));
            }
        }
        List<Junction> junctions = new ArrayList<>();
        List<String[]> unmatchable = new ArrayList<>();
        for (JsonNode j : spec.path("junctions")) {
            JsonNode box = spec.path("bbox").path(j.path("city").asText());
            double[] b = {box.get(0).asDouble(), box.get(1).asDouble(), box.get(2).asDouble(), box.get(3).asDouble()};
            if (j.path("a").isNull() || j.path("a").isMissingNode()) {
                unmatchable.add(new String[]{j.path("city").asText(), j.path("label").asText()});
                junctions.add(null);
                continue;
            }
            junctions.add(new Junction(j.path("city").asText(), j.path("label").asText(),
                    Pattern.compile(norm(j.path("a").asText())), Pattern.compile(norm(j.path("b").asText())), b));
        }
        int n = junctions.size();
        List<Set<Long>> aNodes = new ArrayList<>();
        List<Set<Long>> bNodes = new ArrayList<>();
        int[] aWays = new int[n];
        int[] bWays = new int[n];
        for (int i = 0; i < n; i++) {
            aNodes.add(ConcurrentHashMap.newKeySet());
            bNodes.add(ConcurrentHashMap.newKeySet());
        }
        // Pass 1: named highway ways.
        parse(pbf, threads, way -> {
            Map<String, String> t = way.getTags();
            String name = t.get("name");
            if (name == null || !t.containsKey("highway")) {
                return;
            }
            String nn = norm(name);
            for (int i = 0; i < n; i++) {
                Junction j = junctions.get(i);
                if (j == null) {
                    continue;
                }
                if (j.a().matcher(nn).find()) {
                    aNodes.get(i).addAll(way.getNodes());
                    synchronized (aWays) { aWays[i]++; }
                }
                if (j.b().matcher(nn).find()) {
                    bNodes.get(i).addAll(way.getNodes());
                    synchronized (bWays) { bWays[i]++; }
                }
            }
        }, null);
        Set<Long> wanted = new HashSet<>();
        for (int i = 0; i < n; i++) {
            wanted.addAll(aNodes.get(i));
            wanted.addAll(bNodes.get(i));
        }
        Map<Long, double[]> coords = new ConcurrentHashMap<>();
        Map<Long, Map<String, String>> features = new ConcurrentHashMap<>();
        Map<Long, double[]> featureCoords = new ConcurrentHashMap<>();
        double[][] boxes = spec.path("bbox").properties().stream().map(e -> new double[]{
                e.getValue().get(0).asDouble(), e.getValue().get(1).asDouble(),
                e.getValue().get(2).asDouble(), e.getValue().get(3).asDouble()}).toArray(double[][]::new);
        // Pass 2: coordinates and signal/crossing nodes inside the city boxes.
        parse(pbf, threads, null, node -> {
            if (wanted.contains(node.getId())) {
                coords.put(node.getId(), new double[]{node.getLat(), node.getLon()});
            }
            String hw = node.getTags().get("highway");
            if (hw != null && (hw.equals("traffic_signals") || hw.equals("crossing"))) {
                for (double[] b : boxes) {
                    if (node.getLat() >= b[0] && node.getLat() <= b[2] && node.getLon() >= b[1] && node.getLon() <= b[3]) {
                        features.put(node.getId(), new HashMap<>(node.getTags()));
                        featureCoords.put(node.getId(), new double[]{node.getLat(), node.getLon()});
                    }
                }
            }
        });
        try (PrintWriter w = new PrintWriter(Files.newBufferedWriter(new File(args[2]).toPath(), StandardCharsets.UTF_8))) {
            w.println("city,junction,status,lat,lon,ways_a,ways_b,junction_nodes,signal_or_crossing_nodes,"
                    + "signal_nodes,sound_stated,sound_affirmative,sound_values");
            for (int i = 0; i < n; i++) {
                Junction j = junctions.get(i);
                if (j == null) {
                    String[] u = unmatchable.remove(0);
                    w.printf("%s,%s,not matchable (landmark),,,0,0,0,0,0,0,0,%n", u[0], q(u[1]));
                    continue;
                }
                List<double[]> a = inBox(aNodes.get(i), coords, j.box());
                List<double[]> b = inBox(bNodes.get(i), coords, j.box());
                Set<Long> shared = new HashSet<>(aNodes.get(i));
                shared.retainAll(bNodes.get(i));
                List<double[]> junction = new ArrayList<>(inBox(shared, coords, j.box()));
                if (junction.isEmpty()) {
                    for (double[] p : a) {
                        for (double[] r : b) {
                            if (Geo.haversineMeters(p[0], p[1], r[0], r[1]) <= JUNCTION_M) {
                                junction.add(p);
                                break;
                            }
                        }
                    }
                }
                Map<Long, Map<String, String>> near = new TreeMap<>();
                for (Map.Entry<Long, double[]> e : featureCoords.entrySet()) {
                    for (double[] p : junction) {
                        if (Geo.haversineMeters(p[0], p[1], e.getValue()[0], e.getValue()[1]) <= RADIUS_M) {
                            near.put(e.getKey(), features.get(e.getKey()));
                            break;
                        }
                    }
                }
                int signals = 0, stated = 0, affirmative = 0;
                Set<String> values = new java.util.TreeSet<>();
                for (Map<String, String> t : near.values()) {
                    if ("traffic_signals".equals(t.get("highway"))) signals++;
                    boolean s = false, yes = false;
                    for (Map.Entry<String, String> e : t.entrySet()) {
                        if (e.getKey().contains("sound")) {
                            s = true;
                            values.add(e.getKey() + "=" + e.getValue());
                            for (String v : e.getValue().split(";")) {
                                yes |= AFFIRMATIVE.contains(v.trim());
                            }
                        }
                    }
                    if (s) stated++;
                    if (yes) affirmative++;
                }
                double[] first = junction.isEmpty() ? null : junction.get(0);
                w.printf(Locale.ROOT, "%s,%s,%s,%s,%s,%d,%d,%d,%d,%d,%d,%d,%s%n", j.city(), q(j.label()),
                        junction.isEmpty() ? "junction not found" : "junction found",
                        first == null ? "" : String.format(Locale.ROOT, "%.6f", first[0]),
                        first == null ? "" : String.format(Locale.ROOT, "%.6f", first[1]),
                        aWays[i], bWays[i], junction.size(), near.size(), signals, stated, affirmative,
                        q(String.join("; ", values)));
            }
        }
    }

    private static List<double[]> inBox(Set<Long> ids, Map<Long, double[]> coords, double[] box) {
        List<double[]> out = new ArrayList<>();
        for (long id : ids) {
            double[] p = coords.get(id);
            if (p != null && p[0] >= box[0] && p[0] <= box[2] && p[1] >= box[1] && p[1] <= box[3]) {
                out.add(p);
            }
        }
        return out;
    }

    private static void parse(File pbf, int threads,
                              java.util.function.Consumer<com.wolt.osm.parallelpbf.entity.Way> onWay,
                              java.util.function.Consumer<com.wolt.osm.parallelpbf.entity.Node> onNode)
            throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        try (InputStream in = new BufferedInputStream(new FileInputStream(pbf), 1 << 20)) {
            ParallelBinaryParser parser = new ParallelBinaryParser(in, threads).onComplete(done::countDown);
            if (onWay != null) parser = parser.onWay(onWay);
            if (onNode != null) parser = parser.onNode(onNode);
            parser.parse();
            done.await();
        }
    }

    private static String q(String value) {
        return "\"" + value.replace("\"", "\"\"") + "\"";
    }
}
