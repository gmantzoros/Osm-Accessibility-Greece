package gr.mantzoros.osmaccess;

import org.locationtech.jts.geom.Envelope;
import org.locationtech.jts.index.strtree.STRtree;

import java.io.IOException;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Comparator;
import java.util.Random;
import java.util.Set;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/**
 * Draws the stratified imagery-audit sample (docs/validation-protocol.md).
 *
 * <p>Three local-feature arms, per city in scoped runs:
 * <ul>
 *   <li><b>commission</b> - random OSM crossing features. Does imagery confirm the
 *       feature and the attributes OSM asserts?
 *   <li><b>omission</b> - random structural intersections with no crossing feature
 *       within {@value #OMISSION_RADIUS_M} m. Does imagery show a crossing anyway?
 *   <li><b>sidewalk</b> - a point on a drivable way without a sidewalk-family statement.
 *       Presence at this point is not a whole-way or route-usability judgment.
 * </ul>
 *
 * <p>Sampling is seeded and the seed is written into the output, so the draw is
 * reproducible from the pinned snapshot. Cases are emitted with pre-built OSM,
 * Mapillary and Street View links so the auditor never types a coordinate.
 */
public final class AuditSampler {

    /** An intersection this far from any mapped crossing is an omission candidate. */
    public static final double OMISSION_RADIUS_M = 25.0;
    public static final String SAMPLING_FRAME = "exact-radius-v3-canonical-members";

    /** Grid cell for the proximity hash; ~28 m at Greek latitudes. */
    private static final double CELL_DEG = 0.00025;

    private final StrataIndex strata;
    private final long seed;
    private final int perGroupPerArm;
    /** Optional: when set, each case records the routing profile's verdict at that location. */
    private final AccessProfile profile;

    /**
     * Optional scope: when non-empty, only features inside one of these strata are
     * sampled, and that stratum becomes the sampling group.
     *
     * <p>The approved audit deliberately targets principal communities. This scope does
     * not establish imagery availability and cannot support rural or national inference.
     */
    private final Set<String> scope;
    private String inputSha256 = "";

    /** Supply the SHA-256 of the immutable PBF input, without changing existing callers. */
    /** Arms to serialise; empty means all three. */
    private java.util.Set<String> armFilter = java.util.Set.of();

    public AuditSampler withArms(java.util.Set<String> arms) {
        this.armFilter = arms == null ? java.util.Set.of() : arms;
        return this;
    }

    public AuditSampler withInputSha256(String sha256) {
        if (sha256 == null || !sha256.matches("[0-9a-fA-F]{64}")) {
            throw new IllegalArgumentException("Expected a SHA-256 input digest");
        }
        inputSha256 = sha256.toLowerCase(Locale.ROOT);
        return this;
    }

    public AuditSampler(StrataIndex strata, long seed, int perGroupPerArm) {
        this(strata, seed, perGroupPerArm, null, Set.of());
    }

    public AuditSampler(StrataIndex strata, long seed, int perGroupPerArm, AccessProfile profile) {
        this(strata, seed, perGroupPerArm, profile, Set.of());
    }

    public AuditSampler(StrataIndex strata, long seed, int perGroupPerArm,
                        AccessProfile profile, Set<String> scope) {
        this.strata = strata;
        this.seed = seed;
        this.perGroupPerArm = perGroupPerArm;
        this.profile = profile;
        this.scope = scope == null ? Set.of() : scope;
    }

    public AuditSampler(StrataIndex strata, long seed, int perGroupPerArm,
                        AccessProfile profile, Set<String> scope, String inputSha256) {
        this(strata, seed, perGroupPerArm, profile, scope);
        withInputSha256(inputSha256);
    }

    /** One audit case. */
    private record Case(String group, String stratum, String arm, double lat, double lon,
                        String assertion, String profileState, String featureId) {
    }

    public void write(List<CrossingFeature> crossings, List<double[]> intersections, Path outDir)
            throws IOException {
        write(crossings, intersections, List.of(), null, outDir);
    }

    /**
     * @param ways      walkable ways, for the sidewalk arm; may be empty
     * @param midpoints midpoint of each way, parallel to {@code ways}; may be null when empty
     */
    public void write(List<CrossingFeature> crossings, List<double[]> intersections,
                      List<RoadWay> ways, List<double[]> midpoints, Path outDir)
            throws IOException {
        Map<String, List<Case>> commission = new HashMap<>();
        Map<String, List<Case>> omission = new HashMap<>();
        Map<String, List<Case>> sidewalk = new HashMap<>();
        Random rnd = new Random(seed);

        // Pools must be ordered independently of parallel PBF encounter order.
        List<CrossingFeature> ordered = new ArrayList<>(crossings);
        ordered.sort(Comparator.comparingDouble(CrossingFeature::lat)
                .thenComparingDouble(CrossingFeature::lon)
                .thenComparing(f -> new java.util.TreeMap<>(effectiveTags(f)).toString())
                .thenComparing(AuditSampler::featureId));

        // --- commission arm: sample from what OSM asserts exists
        List<Case> allCommission = new ArrayList<>();
        for (CrossingFeature f : ordered) {
            String stratum = namedStratumOf(f.lat(), f.lon());
            if (stratum == null) {
                continue;
            }
            allCommission.add(new Case(strata.groupOf(stratum), stratum, "commission",
                    f.lat(), f.lon(), assertionOf(f),
                    profile == null ? "" : f.evaluateCrossing(profile).name(),
                    featureId(f)));
        }

        // --- omission arm: intersections with nothing mapped nearby
        Map<Long, List<double[]>> occupied = new HashMap<>();
        STRtree crossingSegments = new STRtree();
        for (CrossingFeature f : ordered) {
            for (double[] point : f.memberPositions()) {
                occupied.computeIfAbsent(cell(point[0], point[1]), k -> new ArrayList<>())
                        .add(point);
            }
            for (CrossingFeature.Segment segment : f.memberSegments()) {
                crossingSegments.insert(new Envelope(segment.lonA(), segment.lonB(),
                        segment.latA(), segment.latB()), segment);
            }
        }
        crossingSegments.build();
        List<Case> allOmission = new ArrayList<>();
        List<double[]> orderedIntersections = new ArrayList<>(intersections);
        orderedIntersections.sort(Comparator.comparingDouble((double[] point) -> point[0])
                .thenComparingDouble(point -> point[1]));
        for (double[] ix : orderedIntersections) {
            if (nearCrossing(occupied, crossingSegments, ix[0], ix[1])) {
                continue;
            }
            String stratum = namedStratumOf(ix[0], ix[1]);
            if (stratum == null) {
                continue;
            }
            allOmission.add(new Case(strata.groupOf(stratum), stratum, "omission",
                    ix[0], ix[1], "no crossing mapped within " + (int) OMISSION_RADIUS_M + " m",
                    "", "intersection-coordinate:" + ix[0] + "," + ix[1]));
        }

        // --- sidewalk arm: the biggest single source of undecidability, and the one the
        // crossing arms do not touch. Sort the way indices, not the ways alone, so each
        // midpoint stays paired with its source even for callers outside PbfCensus.
        List<Case> allSidewalk = new ArrayList<>();
        List<Integer> wayOrder = new ArrayList<>(ways.size());
        for (int i = 0; i < ways.size(); i++) {
            wayOrder.add(i);
        }
        wayOrder.sort(Comparator.comparingLong((Integer i) -> ways.get(i).id()));
        for (int i : wayOrder) {
            RoadWay w = ways.get(i);
            if (!TagInventory.isDrivable(w.highwayClass())) {
                continue;
            }
            if (TagInventory.hasWayTag(w.tags(), "sidewalk")) {
                continue; // the map already answers this one
            }
            double[] mid = midpoints == null ? null : midpoints.get(i);
            if (mid == null) {
                continue;
            }
            String stratum = namedStratumOf(mid[0], mid[1]);
            if (stratum == null) {
                continue;
            }
            allSidewalk.add(new Case(strata.groupOf(stratum), stratum, "sidewalk",
                    mid[0], mid[1],
                    "no sidewalk statement on this " + w.highwayClass() + " (way " + w.id() + ")",
                    "", "way:" + w.id()));
        }

        drawPerGroup(allCommission, commission, rnd);
        drawPerGroup(allOmission, omission, rnd);
        drawPerGroup(allSidewalk, sidewalk, rnd);
        Map<String, Integer> pools = new java.util.TreeMap<>();
        MessageDigest immutablePool = digest();
        for (List<Case> pool : List.of(allCommission, allOmission, allSidewalk)) {
            for (Case c : pool) {
                pools.merge(samplingKey(c), 1, Integer::sum);
                immutablePool.update((c + "\n").getBytes(StandardCharsets.UTF_8));
            }
        }
        String poolSha256 = HexFormat.of().formatHex(immutablePool.digest());

        Files.createDirectories(outDir);
        Path file = outDir.resolve("audit_sample.csv");
        preserve(file);
        preserve(outDir.resolve("audit_sample_seed.txt"));
        int written = 0;
        try (PrintWriter out = new PrintWriter(
                Files.newBufferedWriter(file, StandardCharsets.UTF_8))) {
            out.println("case_id,group,stratum,arm,lat,lon,osm_assertion,mapped_feature_state,osm_link,"
                    + "streetview_link,feature_id,pool_n,draw_n,inclusion_probability,radius_m,"
                    + "sampling_frame,input_sha256,pool_sha256,seed");
            int id = 1;
            List<Map<String, List<Case>>> arms = new ArrayList<>();
            if (armFilter.isEmpty() || armFilter.contains("commission")) arms.add(commission);
            if (armFilter.isEmpty() || armFilter.contains("omission")) arms.add(omission);
            if (armFilter.isEmpty() || armFilter.contains("sidewalk")) arms.add(sidewalk);
            for (Map<String, List<Case>> arm : arms) {
                for (Map.Entry<String, List<Case>> e : new java.util.TreeMap<>(arm).entrySet()) {
                    for (Case c : e.getValue()) {
                        out.printf(Locale.ROOT,
                                "%s,%s,%s,%s,%.7f,%.7f,\"%s\",%s,%s,%s,%s,%d,%d,%.12f,%.1f,%s,%s,%s,%d%n",
                                String.format(Locale.ROOT, "C%04d", id++),
                                c.group(), quote(c.stratum()), c.arm(), c.lat(), c.lon(),
                                c.assertion().replace("\"", "'"),
                                c.profileState(),
                                quote(osmLink(c.lat(), c.lon())),
                                quote(streetViewLink(c.lat(), c.lon())),
                                quote(c.featureId()), pools.get(samplingKey(c)), e.getValue().size(),
                                (double) e.getValue().size() / pools.get(samplingKey(c)),
                                OMISSION_RADIUS_M, SAMPLING_FRAME, inputSha256, poolSha256, seed);
                        written++;
                    }
                }
            }
        }

        Path meta = outDir.resolve("audit_sample_seed.txt");
        Files.writeString(meta, String.format(Locale.ROOT,
                "seed=%d%nper_group_per_arm=%d%nomission_radius_m=%.0f%nscope_strata=%d%n"
                        + "commission_pool=%d%nomission_pool=%d%nsidewalk_pool=%d%n"
                        + "cases_written=%d%nsampling_frame=%s%ninput_sha256=%s%npool_sha256=%s%n",
                seed, perGroupPerArm, OMISSION_RADIUS_M, scope.size(),
                allCommission.size(), allOmission.size(), allSidewalk.size(),
                written, SAMPLING_FRAME, inputSha256, poolSha256), StandardCharsets.UTF_8);

        System.err.printf(Locale.ROOT,
                "Audit sample: %,d cases (pools: %,d commission, %,d omission, %,d sidewalk) -> %s%n",
                written, allCommission.size(), allOmission.size(), allSidewalk.size(), file);
    }

    private String samplingKey(Case c) {
        return (scope.isEmpty() ? c.group() : c.stratum()) + "\t" + c.arm();
    }

    private static void preserve(Path file) throws IOException {
        if (Files.exists(file)) {
            String digest = sha256(Files.readString(file, StandardCharsets.UTF_8));
            Path archive = file.getParent().resolve("archive");
            Files.createDirectories(archive);
            Path target = archive.resolve(file.getFileName() + "." + digest);
            if (!Files.exists(target)) {
                Files.copy(file, target);
            }
        }
    }

    private static Map<String, String> effectiveTags(CrossingFeature f) {
        return f.effectiveTags();
    }

    private static String featureId(CrossingFeature f) {
        if (!f.nodeIds.isEmpty()) {
            return "crossing:node:" + f.nodeIds.stream().sorted().findFirst().orElseThrow();
        }
        if (!f.wayIds.isEmpty()) {
            return "crossing:way:" + f.wayIds.stream().sorted().findFirst().orElseThrow();
        }
        return "crossing-signature:" + sha256(f.lat() + "," + f.lon() + ","
                + new java.util.TreeMap<>(effectiveTags(f)));
    }

    private static String sha256(String value) {
        return HexFormat.of().formatHex(digest().digest(value.getBytes(StandardCharsets.UTF_8)));
    }

    private static MessageDigest digest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    /**
     * Reservoir sampling per group: one pass, no shuffle of the full pool, and the
     * draw depends only on the seed and the pool order (which the pinned snapshot
     * fixes).
     */
    private void drawPerGroup(List<Case> pool, Map<String, List<Case>> out, Random rnd) {
        Map<String, Integer> seen = new HashMap<>();
        for (Case c : pool) {
            // Scoped runs group by city so every city gets cases; unscoped runs group by
            // stratum group (metro / rural / ...) as the protocol specifies.
            String key = scope.isEmpty() ? c.group() : c.stratum();
            List<Case> reservoir = out.computeIfAbsent(key, k -> new ArrayList<>());
            int n = seen.merge(key, 1, Integer::sum);
            if (reservoir.size() < perGroupPerArm) {
                reservoir.add(c);
            } else {
                int j = rnd.nextInt(n);
                if (j < perGroupPerArm) {
                    reservoir.set(j, c);
                }
            }
        }
    }

    /**
     * Which stratum this case is labelled with, and whether it is auditable at all.
     * With a scope set, only in-scope strata qualify and the stratum is the sampling
     * group, so cases spread evenly across the cities rather than piling into Athens.
     * Without one, the named circles are used - imagery links need a real place.
     */
    private String namedStratumOf(double lat, double lon) {
        List<String> in = strata.strataFor(lat, lon);
        if (!scope.isEmpty()) {
            for (String s : in) {
                if (scope.contains(s)) {
                    return s;
                }
            }
            return null;
        }
        for (String s : in) {
            if ("circle".equals(strata.layerOf(s))) {
                return s;
            }
        }
        return null;
    }

    private static String assertionOf(CrossingFeature f) {
        StringBuilder sb = new StringBuilder("crossing mapped (").append(f.schema()).append(")");
        Map<String, String> t = effectiveTags(f);
        if (t.containsKey("crossing")) {
            sb.append("; crossing=").append(t.get("crossing"));
        }
        if (t.containsKey("crossing:markings")) {
            sb.append("; markings=").append(t.get("crossing:markings"));
        }
        if (t.containsKey("tactile_paving")) {
            sb.append("; tactile=").append(t.get("tactile_paving"));
        }
        String kerb = t.get("kerb");
        if (kerb != null) {
            sb.append("; kerb=").append(kerb);
        }
        if (t.containsKey("crossing:island")) {
            sb.append("; island=").append(t.get("crossing:island"));
        }
        return sb.toString();
    }

    private static long cell(double lat, double lon) {
        long y = (long) Math.floor(lat / CELL_DEG);
        long x = (long) Math.floor(lon / CELL_DEG);
        return y * 10_000_000L + x;
    }

    static boolean nearCrossing(Map<Long, List<double[]>> occupied, double lat, double lon) {
        return nearCrossing(occupied, new STRtree(), lat, lon);
    }

    static boolean nearCrossing(Map<Long, List<double[]>> occupied, STRtree segments,
                                double lat, double lon) {
        int ry = (int) Math.ceil(OMISSION_RADIUS_M / (111_000 * CELL_DEG));
        int rx = (int) Math.ceil(OMISSION_RADIUS_M
                / (111_000 * CELL_DEG * Math.max(0.000001, Math.cos(Math.toRadians(lat)))));
        long y = (long) Math.floor(lat / CELL_DEG);
        long x = (long) Math.floor(lon / CELL_DEG);
        for (int dy = -ry; dy <= ry; dy++) {
            for (int dx = -rx; dx <= rx; dx++) {
                for (double[] p : occupied.getOrDefault(
                        (y + dy) * 10_000_000L + x + dx, List.of())) {
                    double dlat = Math.toRadians(p[0] - lat);
                    double dlon = Math.toRadians(p[1] - lon);
                    double h = Math.pow(Math.sin(dlat / 2), 2)
                            + Math.cos(Math.toRadians(lat)) * Math.cos(Math.toRadians(p[0]))
                            * Math.pow(Math.sin(dlon / 2), 2);
                    if (2 * 6_371_000 * Math.asin(Math.sqrt(Math.min(1, h))) <= OMISSION_RADIUS_M) {
                        return true;
                    }
                }
            }
        }
        double latRadius = OMISSION_RADIUS_M / 110_000;
        double lonRadius = latRadius / Math.max(0.000001, Math.cos(Math.toRadians(lat)));
        for (Object candidate : segments.query(new Envelope(lon - lonRadius, lon + lonRadius,
                lat - latRadius, lat + latRadius))) {
            if (distanceToSegment(lat, lon, (CrossingFeature.Segment) candidate)
                    <= OMISSION_RADIUS_M) {
                return true;
            }
        }
        return false;
    }

    private static double distanceToSegment(double lat, double lon, CrossingFeature.Segment s) {
        double cos = Math.cos(Math.toRadians(lat));
        double ax = (s.lonA() - lon) * cos;
        double ay = s.latA() - lat;
        double dx = (s.lonB() - s.lonA()) * cos;
        double dy = s.latB() - s.latA();
        double lengthSquared = dx * dx + dy * dy;
        double t = lengthSquared == 0 ? 0
                : Math.max(0, Math.min(1, -(ax * dx + ay * dy) / lengthSquared));
        return Geo.haversineMeters(lat, lon,
                s.latA() + t * (s.latB() - s.latA()),
                s.lonA() + t * (s.lonB() - s.lonA()));
    }

    private static String osmLink(double lat, double lon) {
        return String.format(Locale.ROOT,
                "https://www.openstreetmap.org/?mlat=%.6f&mlon=%.6f#map=19/%.6f/%.6f",
                lat, lon, lat, lon);
    }

    private static String mapillaryLink(double lat, double lon) {
        return String.format(Locale.ROOT,
                "https://www.mapillary.com/app/?lat=%.6f&lng=%.6f&z=18", lat, lon);
    }

    private static String streetViewLink(double lat, double lon) {
        return String.format(Locale.ROOT,
                "https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=%.6f,%.6f",
                lat, lon);
    }

    /**
     * CSV-quote when needed. This matters more than it looks: the Street View deep link
     * embeds the coordinate as {@code viewpoint=lat,lon}, so an unquoted link splits the
     * field and shifts every column after it - silently, and only for that one column.
     */
    private static String quote(String s) {
        if (s == null) {
            return "";
        }
        return s.indexOf(',') < 0 && s.indexOf('"') < 0 ? s : '"' + s.replace("\"", "\"\"") + '"';
    }
}
