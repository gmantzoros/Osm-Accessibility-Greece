package gr.mantzoros.osmaccess;

import com.wolt.osm.parallelpbf.ParallelBinaryParser;
import com.wolt.osm.parallelpbf.entity.Node;
import com.wolt.osm.parallelpbf.entity.Way;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Two-pass census over an OSM PBF extract.
 *
 * <p>Pass 1 streams ways, keeping every highway way of the frozen inventory together
 * with its tag subset and node references. Pass 2 streams nodes, resolving the
 * coordinates of referenced nodes (sorted-array + binary search: the full extract's
 * node set never needs to fit in memory as a map) and collecting crossing/signal/kerb
 * nodes. Metrics are then computed single-threaded (docs/metrics.md).
 *
 * <p>v1.0 changes the crossing model: crossings mapped as ways are collected too, and
 * a crossing node plus the crossing way(s) touching it are resolved into one physical
 * crossing feature before any attribute depth is measured.
 */
public final class PbfCensus {

    private final File pbf;
    private final StrataIndex strata;
    private final int threads;

    private final ConcurrentLinkedQueue<RoadWay> ways = new ConcurrentLinkedQueue<>();
    private final ConcurrentLinkedQueue<AccessNode> accessNodes = new ConcurrentLinkedQueue<>();
    private final ConcurrentLinkedQueue<AccessPoi> poiNodes = new ConcurrentLinkedQueue<>();

    /** A POI mapped as a way (a building, usually): tags plus one node ref for a position. */
    private record PoiWay(long id, AccessPoi.Kind kind, Map<String, String> tags, long ref) { }

    private final ConcurrentLinkedQueue<PoiWay> poiWays = new ConcurrentLinkedQueue<>();

    /** Retained only when an audit sample is requested (see AuditSampler). */
    private boolean retainFeatures;
    private List<CrossingFeature> crossingFeatures = List.of();
    private List<double[]> intersectionPoints = List.of();

    private long[] neededIds;
    private double[] lats;
    private double[] lons;
    private boolean[] resolved;
    private Set<String> configuredKeys = Set.of();
    private final Map<Long, Map<String, String>> routingNodes = new HashMap<>();
    private final Map<Long, Map<String, String>> routingCrossings = new HashMap<>();
    private String inputSha256;

    public PbfCensus(File pbf, StrataIndex strata, int threads) {
        this.pbf = pbf;
        this.strata = strata;
        this.threads = threads;
    }

    /** Keep crossing features and intersection coordinates in memory for sampling. */
    public PbfCensus retainFeatures(boolean retain) {
        this.retainFeatures = retain;
        return this;
    }

    public PbfCensus routingProfile(AccessProfile profile) {
        configuredKeys = profile == null ? Set.of() : profile.requiredKeys();
        return this;
    }

    /** Digest of the actual PBF byte stream; both parsing passes must agree. */
    public String inputSha256() {
        if (inputSha256 == null) {
            throw new IllegalStateException("PBF has not been parsed");
        }
        return inputSha256;
    }

    public List<CrossingFeature> crossingFeatures() {
        return crossingFeatures;
    }

    public List<double[]> intersectionPoints() {
        return intersectionPoints;
    }

    /** Everything the feasibility analysis needs, without re-parsing the extract. */
    public FeasibilityAnalysis feasibility(AccessProfile profile) {
        return feasibility(profile, "extended", profile.minUsableComponentKm());
    }

    RoutingGraph graph(AccessProfile profile, String scope) {
        return new RoutingGraph(profile, scope, new ArrayList<>(ways), neededIds, lats, lons,
                resolved, routingNodes, routingCrossings);
    }

    public FeasibilityAnalysis feasibility(AccessProfile profile, String scope, double threshold) {
        return new FeasibilityAnalysis(graph(profile, scope), strata, threshold);
    }

    /** Walkable ways and their midpoints, for the sidewalk arm of the audit sample. */
    public List<RoadWay> waysForSampling() {
        List<RoadWay> ordered = new ArrayList<>(ways);
        ordered.sort(java.util.Comparator.comparingLong(RoadWay::id));
        return ordered;
    }

    public List<double[]> wayMidpoints(List<RoadWay> list) {
        List<double[]> out = new ArrayList<>(list.size());
        for (RoadWay w : list) {
            out.add(midpoint(w));
        }
        return out;
    }

    /**
     * Dump the OSM point features an external validator needs to join against:
     * crossings, traffic signals, and structural intersections, each with its
     * coordinates and stratum. Restricted to {@code scope} when non-empty.
     *
     * <p>This exists because the census reports aggregates, and comparing OSM against
     * an independent detector (Mapillary computer vision, imagery audit) needs the
     * individual points back out.
     */
    public void dumpPoints(Path file, java.util.Set<String> scope) throws IOException {
        java.nio.file.Files.createDirectories(file.toAbsolutePath().getParent());
        long written = 0;
        try (java.io.PrintWriter out = new java.io.PrintWriter(
                java.nio.file.Files.newBufferedWriter(file,
                        java.nio.charset.StandardCharsets.UTF_8))) {
            out.println("kind,lat,lon,stratum,detail");
            for (CrossingFeature f : crossingFeatures) {
                written += emit(out, "crossing", f.lat(), f.lon(), scope, f.schema());
            }
            for (AccessNode n : accessNodes) {
                if (n.isSignal()) {
                    String d = n.tags().containsKey("traffic_signals:sound") ? "sound" : "";
                    written += emit(out, "signal", n.lat(), n.lon(), scope, d);
                }
            }
            for (double[] p : intersectionPoints) {
                written += emit(out, "intersection", p[0], p[1], scope, "");
            }
        }
        System.err.printf(Locale.ROOT, "Dumped %,d OSM points -> %s%n", written, file);
    }

    private long emit(java.io.PrintWriter out, String kind, double lat, double lon,
                      java.util.Set<String> scope, String detail) {
        String stratum = null;
        for (String s : strata.strataFor(lat, lon)) {
            if (scope.isEmpty() ? !StrataIndex.NATIONAL.equals(s) : scope.contains(s)) {
                stratum = s;
                break;
            }
        }
        if (stratum == null) {
            return 0;
        }
        out.printf(Locale.ROOT, "%s,%.7f,%.7f,%s,%s%n", kind, lat, lon,
                stratum.indexOf(',') < 0 ? stratum : '"' + stratum + '"', detail);
        return 1;
    }

    /** Edit targeting reuses the same in-memory graph (docs/edit-targeting.md). */
    public EditTargeting targeting(AccessProfile profile) {
        return targeting(profile, "extended");
    }

    public EditTargeting targeting(AccessProfile profile, String scope) {
        return new EditTargeting(graph(profile, scope), strata);
    }

    public CensusResult run() throws IOException, InterruptedException {
        System.err.printf("Pass 1/2: scanning ways (%d threads)...%n", threads);
        parse(way -> {
            Map<String, String> allTags = way.getTags();
            String hw = allTags.get("highway");
            boolean drivable = TagInventory.isDrivable(hw);
            boolean pedestrian = TagInventory.isPedestrian(hw);
            boolean crossingWay = TagInventory.isCrossingWay(hw, allTags);
            AccessPoi.Kind poiKind = AccessPoi.classify(allTags);
            if (poiKind != null && !way.getNodes().isEmpty()) {
                Map<String, String> pt = new HashMap<>(6);
                for (Map.Entry<String, String> e : allTags.entrySet()) {
                    if (AccessPoi.KEYS.contains(e.getKey())
                            || AccessPoi.isTactileWritingKey(e.getKey())) {
                        pt.put(e.getKey(), e.getValue());
                    }
                }
                poiWays.add(new PoiWay(way.getId(), poiKind, pt, way.getNodes().get(0)));
            }
            if (!drivable && !pedestrian && !crossingWay && !TagInventory.isGraphOnly(hw)) {
                return;
            }
            Map<String, String> kept = TagInventory.retainTags(
                    allTags, TagInventory.WAY_KEYS, configuredKeys);
            boolean separateSidewalk = "footway".equals(hw) && "sidewalk".equals(kept.get("footway"));
            long[] refs = way.getNodes().stream().mapToLong(Long::longValue).toArray();
            var info = way.getInfo();
            ways.add(new RoadWay(way.getId(), hw, drivable, pedestrian, separateSidewalk,
                    crossingWay, kept, refs,
                    Geo.toEpochSeconds(info == null ? 0L : info.getTimestamp()),
                    info == null ? 0 : info.getVersion()));
        }, null);
        long crossingWays = ways.stream().filter(RoadWay::crossingWay).count();
        System.err.printf(Locale.ROOT, "  kept %,d highway ways (%,d of them crossing ways)%n",
                ways.size(), crossingWays);

        buildNeededIndex();

        System.err.println("Pass 2/2: scanning nodes...");
        AtomicLong nodeCount = new AtomicLong();
        parse(null, node -> {
            if (nodeCount.incrementAndGet() % 10_000_000 == 0) {
                System.err.printf(Locale.ROOT, "  ...%,dM nodes%n", nodeCount.get() / 1_000_000);
            }
            int idx = Arrays.binarySearch(neededIds, node.getId());
            if (idx >= 0) {
                lats[idx] = node.getLat();
                lons[idx] = node.getLon();
                resolved[idx] = true;
            }
            Map<String, String> tags = node.getTags();
            if (tags.isEmpty()) {
                return;
            }
            String hw = tags.get("highway");
            boolean relevant = "crossing".equals(hw) || "traffic_signals".equals(hw)
                    || tags.containsKey("kerb") || tags.containsKey("barrier")
                    || tags.containsKey("foot") || tags.containsKey("access")
                    || tags.containsKey("wheelchair") || tags.containsKey("foot:conditional")
                    || tags.containsKey("access:conditional")
                    || (idx >= 0 && tags.keySet().stream().anyMatch(configuredKeys::contains));
            if (relevant) {
                Map<String, String> kept = TagInventory.retainTags(
                        tags, TagInventory.NODE_KEYS, configuredKeys);
                var ni = node.getInfo();
                accessNodes.add(new AccessNode(node.getId(), node.getLat(), node.getLon(), kept,
                        Geo.toEpochSeconds(ni == null ? 0L : ni.getTimestamp()),
                        ni == null ? 0 : ni.getVersion()));
            }
            AccessPoi.Kind poiKind = AccessPoi.classify(tags);
            if (poiKind != null) {
                Map<String, String> pt = new HashMap<>(6);
                for (Map.Entry<String, String> e : tags.entrySet()) {
                    if (AccessPoi.KEYS.contains(e.getKey())
                            || AccessPoi.isTactileWritingKey(e.getKey())) {
                        pt.put(e.getKey(), e.getValue());
                    }
                }
                poiNodes.add(new AccessPoi(node.getId(), node.getLat(), node.getLon(), poiKind, pt));
            }
        });
        System.err.printf(Locale.ROOT,
                "  scanned %,d nodes, collected %,d access nodes, %,d destination features%n",
                nodeCount.get(), accessNodes.size(), poiNodes.size() + poiWays.size());

        return compute();
    }

    private void parse(java.util.function.Consumer<Way> onWay,
                       java.util.function.Consumer<Node> onNode) throws IOException, InterruptedException {
        CountDownLatch done = new CountDownLatch(1);
        java.security.MessageDigest digest;
        try {
            digest = java.security.MessageDigest.getInstance("SHA-256");
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IOException("SHA-256 digest unavailable", e);
        }
        try (InputStream in = new BufferedInputStream(
                new java.security.DigestInputStream(new FileInputStream(pbf), digest), 1 << 20)) {
            ParallelBinaryParser parser = new ParallelBinaryParser(in, threads)
                    .onComplete(done::countDown);
            if (onWay != null) {
                parser = parser.onWay(onWay);
            }
            if (onNode != null) {
                parser = parser.onNode(onNode);
            }
            parser.parse();
            done.await();
        }
        String hash = java.util.HexFormat.of().formatHex(digest.digest());
        if (inputSha256 != null && !inputSha256.equals(hash)) {
            throw new IOException("PBF bytes changed between census passes: " + pbf);
        }
        inputSha256 = hash;
    }

    private void buildNeededIndex() {
        long total = poiWays.size();
        for (RoadWay w : ways) {
            total += w.nodeRefs().length;
        }
        long[] all = new long[(int) total];
        int i = 0;
        for (RoadWay w : ways) {
            for (long ref : w.nodeRefs()) {
                all[i++] = ref;
            }
        }
        for (PoiWay pw : poiWays) {
            all[i++] = pw.ref();
        }
        Arrays.sort(all);
        int unique = 0;
        for (int j = 0; j < all.length; j++) {
            if (j == 0 || all[j] != all[j - 1]) {
                all[unique++] = all[j];
            }
        }
        neededIds = Arrays.copyOf(all, unique);
        lats = new double[unique];
        lons = new double[unique];
        resolved = new boolean[unique];
        System.err.printf(Locale.ROOT, "  need coordinates for %,d unique nodes%n", unique);
    }

    private CensusResult compute() {
        System.err.println("Computing metrics...");
        CensusResult result = new CensusResult(strata);

        // Intersection detection: nodes used by >= 2 distinct drivable ways
        // (docs/metrics.md section 4.3). Counted single-threaded after parsing.
        short[] usage = new short[neededIds.length];
        int[] neighbour1 = new int[neededIds.length];
        int[] neighbour2 = new int[neededIds.length];
        boolean[] junction = new boolean[neededIds.length];
        Arrays.fill(neighbour1, -1);
        Arrays.fill(neighbour2, -1);
        for (RoadWay w : ways) {
            if (!w.drivable()) {
                continue;
            }
            Set<Long> distinct = new HashSet<>();
            for (long ref : w.nodeRefs()) {
                distinct.add(ref);
            }
            for (long ref : distinct) {
                int idx = Arrays.binarySearch(neededIds, ref);
                if (idx >= 0 && usage[idx] < Short.MAX_VALUE) {
                    usage[idx]++;
                }
            }
            long[] refs = w.nodeRefs();
            for (int j = 1; j < refs.length; j++) {
                int a = Arrays.binarySearch(neededIds, refs[j - 1]);
                int b = Arrays.binarySearch(neededIds, refs[j]);
                if (a >= 0 && b >= 0 && resolved[a] && resolved[b] && a != b) {
                    addNeighbour(a, b, neighbour1, neighbour2, junction);
                    addNeighbour(b, a, neighbour1, neighbour2, junction);
                }
            }
        }
        List<double[]> intersections = retainFeatures ? new ArrayList<>() : null;
        for (int i = 0; i < usage.length; i++) {
            if (junction[i] && resolved[i]) {
                result.addJunction(lats[i], lons[i]);
            }
            if (usage[i] >= 2 && resolved[i]) {
                result.addIntersection(lats[i], lons[i]);
                if (intersections != null) {
                    intersections.add(new double[]{lats[i], lons[i]});
                }
            }
        }
        if (intersections != null) {
            intersectionPoints = intersections;
        }

        long unresolvedWays = 0;
        for (RoadWay w : ways) {
            if (!w.countsForLength()) {
                continue;
            }
            double km = wayLengthKm(w);
            double[] mid = midpoint(w);
            if (km <= 0 || mid == null) {
                unresolvedWays++;
                continue;
            }
            result.addWay(w, km, mid[0], mid[1]);
        }
        if (unresolvedWays > 0) {
            System.err.printf(Locale.ROOT, "  %,d ways skipped (no resolvable geometry - extract boundary clipping)%n",
                    unresolvedWays);
        }

        long newest = 0;
        for (RoadWay w : ways) {
            if (w.editedAt() > newest) {
                newest = w.editedAt();
            }
        }
        for (AccessNode n : accessNodes) {
            if (n.editedAt() > newest) {
                newest = n.editedAt();
            }
        }
        result.snapshotEpochSec = newest;

        List<CrossingFeature> features = buildCrossingFeatures();
        for (AccessNode node : accessNodes) {
            routingNodes.put(node.id(), node.tags());
        }
        for (CrossingFeature f : features) {
            result.addCrossing(f);
            Map<String, String> evidence = f.routingEvidence();
            for (long id : f.wayIds) {
                routingCrossings.put(id, evidence);
            }
            for (long id : f.nodeIds) {
                Map<String, String> tags = new HashMap<>(routingNodes.getOrDefault(id, Map.of()));
                evidence.forEach((key, value) -> {
                    if ("kerb".equals(key)) {
                        tags.put(key, TagInventory.worstKerb(tags.get(key), value));
                    } else {
                        tags.merge(key, value, TagInventory::mergeValues);
                    }
                });
                routingNodes.put(id, tags);
            }
        }
        if (retainFeatures) {
            crossingFeatures = features;
        }
        for (AccessPoi poi : poiNodes) {
            result.addPoi(poi);
        }
        int poiWaysPlaced = 0;
        for (PoiWay pw : poiWays) {
            int idx = Arrays.binarySearch(neededIds, pw.ref());
            if (idx >= 0 && resolved[idx]) {
                result.addPoi(new AccessPoi(pw.id(), lats[idx], lons[idx], pw.kind(), pw.tags()));
                poiWaysPlaced++;
            }
        }
        if (!poiWays.isEmpty()) {
            System.err.printf(Locale.ROOT, "  destinations: %,d nodes + %,d ways (%,d placed)%n",
                    poiNodes.size(), poiWays.size(), poiWaysPlaced);
        }

        for (AccessNode n : accessNodes) {
            if (n.isSignal()) {
                result.addSignal(n);
            }
            if (n.isCrossing()) {
                result.addLegacyCrossingNode(n);
            }
            if (n.isKerb()) {
                result.addKerbNode(n);
            }
        }
        return result;
    }

    static void addNeighbour(int node, int neighbour, int[] first, int[] second, boolean[] junction) {
        if (first[node] == neighbour || second[node] == neighbour || junction[node]) {
            return;
        }
        if (first[node] < 0) {
            first[node] = neighbour;
        } else if (second[node] < 0) {
            second[node] = neighbour;
        } else {
            junction[node] = true;
        }
    }

    // ------------------------------------------------- unified crossing model

    /**
     * Resolves crossing nodes and crossing ways into physical crossing features
     * (docs/metrics.md section 3.2) with a union-find over the two element sets:
     * a crossing way and every crossing node lying on it belong to one component.
     * A dual-carriageway crossing mapped as one footway through two carriageway
     * nodes therefore counts once, not twice.
     */
    private List<CrossingFeature> buildCrossingFeatures() {
        List<AccessNode> crossingNodes = new ArrayList<>();
        for (AccessNode n : accessNodes) {
            if (n.isCrossing()) {
                crossingNodes.add(n);
            }
        }
        List<RoadWay> crossingWays = new ArrayList<>();
        for (RoadWay w : ways) {
            if (w.crossingWay()) {
                crossingWays.add(w);
            }
        }
        crossingNodes.sort(java.util.Comparator.comparingLong(AccessNode::id));
        crossingWays.sort(java.util.Comparator.comparingLong(RoadWay::id));
        Map<Long, Integer> nodeIndex = new HashMap<>(crossingNodes.size() * 2);
        for (int i = 0; i < crossingNodes.size(); i++) {
            nodeIndex.put(crossingNodes.get(i).id(), i);
        }
        Map<Long, String> kerbById = new HashMap<>();
        Map<Long, AccessNode> memberById = new HashMap<>();
        for (AccessNode n : accessNodes) {
            memberById.put(n.id(), n);
            if (n.isKerb()) {
                kerbById.put(n.id(), n.kerbValue());
            }
        }

        int n = crossingNodes.size();
        int m = crossingWays.size();
        int[] parent = new int[n + m];
        for (int i = 0; i < parent.length; i++) {
            parent[i] = i;
        }
        for (int j = 0; j < m; j++) {
            for (long ref : crossingWays.get(j).nodeRefs()) {
                Integer ni = nodeIndex.get(ref);
                if (ni != null) {
                    union(parent, n + j, ni);
                }
            }
        }
        Map<Long, Integer> touchingWay = new HashMap<>();
        for (int j = 0; j < m; j++) {
            for (long ref : crossingWays.get(j).nodeRefs()) {
                Integer other = touchingWay.putIfAbsent(ref, n + j);
                if (other != null) {
                    union(parent, n + j, other);
                }
            }
        }

        Map<Integer, CrossingFeature> features = new HashMap<>();
        for (int i = 0; i < n; i++) {
            AccessNode node = crossingNodes.get(i);
            CrossingFeature f = features.computeIfAbsent(find(parent, i), k -> new CrossingFeature());
            f.fromNode = true;
            f.nodeMembers++;
            f.nodeIds.add(node.id());
            f.noteEdit(node.editedAt(), node.version());
            f.merge(node.tags());
            f.addPosition(node.lat(), node.lon());
        }
        for (int j = 0; j < m; j++) {
            RoadWay w = crossingWays.get(j);
            CrossingFeature f = features.computeIfAbsent(find(parent, n + j), k -> new CrossingFeature());
            f.fromWay = true;
            f.wayMembers++;
            f.wayIds.add(w.id());
            f.noteEdit(w.editedAt(), w.version());
            f.merge(w.tags());
            if (f.nodeMembers == 0) {
                double[] mid = midpoint(w);
                if (mid != null) {
                    f.addPosition(mid[0], mid[1]);
                }
            }
            if (retainFeatures) {
                double[] previous = null;
                for (long ref : w.nodeRefs()) {
                    int idx = Arrays.binarySearch(neededIds, ref);
                    if (idx < 0 || !resolved[idx]) {
                        previous = null;
                        continue;
                    }
                    double lat = lats[idx];
                    double lon = lons[idx];
                    f.addMemberPosition(lat, lon);
                    if (previous != null) {
                        f.addMemberSegment(previous[0], previous[1], lat, lon);
                    }
                    previous = new double[]{lat, lon};
                }
            }
            for (long ref : w.nodeRefs()) {
                String kerb = kerbById.get(ref);
                if (kerb != null) {
                    f.kerbFromMember = TagInventory.worstKerb(f.kerbFromMember, kerb);
                    AccessNode member = memberById.get(ref);
                    if (member != null) {
                        f.merge(member.tags());
                        f.noteEdit(member.editedAt(), member.version());
                    }
                }
            }
        }

        List<CrossingFeature> out = new ArrayList<>(features.size());
        int positionless = 0;
        for (CrossingFeature f : features.values()) {
            if (f.hasPosition()) {
                out.add(f);
            } else {
                positionless++;
            }
        }
        out.sort(java.util.Comparator.comparingLong(f ->
                f.nodeIds.isEmpty() ? f.wayIds.iterator().next() : f.nodeIds.iterator().next()));
        System.err.printf(Locale.ROOT, "  crossings: %,d nodes + %,d ways -> %,d mapped feature groups%s%n",
                n, m, out.size(),
                positionless > 0 ? " (" + positionless + " dropped, no geometry)" : "");
        return out;
    }

    private static int find(int[] parent, int x) {
        while (parent[x] != x) {
            parent[x] = parent[parent[x]];
            x = parent[x];
        }
        return x;
    }

    private static void union(int[] parent, int a, int b) {
        int ra = find(parent, a);
        int rb = find(parent, b);
        if (ra != rb) {
            parent[rb] = ra;
        }
    }

    // ------------------------------------------------------------- geometry

    private double wayLengthKm(RoadWay w) {
        double meters = 0;
        int prev = -1;
        for (long ref : w.nodeRefs()) {
            int idx = Arrays.binarySearch(neededIds, ref);
            if (idx < 0 || !resolved[idx]) {
                prev = -1; // gap: skip segment rather than interpolate (docs/metrics.md section 4.1)
                continue;
            }
            if (prev >= 0) {
                meters += Geo.haversineMeters(lats[prev], lons[prev], lats[idx], lons[idx]);
            }
            prev = idx;
        }
        return meters / 1000.0;
    }

    private double[] midpoint(RoadWay w) {
        long[] refs = w.nodeRefs();
        // middle node outward search for the first resolvable coordinate
        int mid = refs.length / 2;
        for (int off = 0; off <= refs.length; off++) {
            for (int sign = -1; sign <= 1; sign += 2) {
                int i = mid + sign * off;
                if (i < 0 || i >= refs.length) {
                    continue;
                }
                int idx = Arrays.binarySearch(neededIds, refs[i]);
                if (idx >= 0 && resolved[idx]) {
                    return new double[]{lats[idx], lons[idx]};
                }
            }
        }
        return null;
    }

    // ------------------------------------------------------------ aggregation

    /** Aggregation container; one instance per census run. */
    public static final class CensusResult {

        public static final String NATIONAL = StrataIndex.NATIONAL;

        private final StrataIndex strata;

        /** stratum -&gt; highway class -&gt; [ways, km, kmPresent[F], kmAffirmative[F]]. */
        final Map<String, Map<String, double[]>> wayMetrics = new HashMap<>();
        /** stratum -&gt; named counter -&gt; count. */
        final Map<String, Map<String, Long>> nodeMetrics = new HashMap<>();
        /** stratum -&gt; separately mapped sidewalk km. */
        final Map<String, Double> separateSidewalkKm = new HashMap<>();
        /** stratum -&gt; "universe|key" -&gt; value -&gt; km (way tags). */
        final Map<String, Map<String, Map<String, Double>>> wayValues = new HashMap<>();
        /** stratum -&gt; key -&gt; value -&gt; count (crossing-feature tags). */
        final Map<String, Map<String, Map<String, Long>>> crossingValues = new HashMap<>();
        /**
         * stratum -&gt; feature type -&gt; edit year -&gt; count (or km for ways).
         * Recency is measurable from a single snapshot because public extracts keep
         * per-object timestamps; contributor concentration is not, because they zero
         * out uid/username (docs/data-sources.md).
         */
        final Map<String, Map<String, Map<Integer, Double>>> editYears = new HashMap<>();
        /** Newest edit seen anywhere in the extract - the de facto snapshot instant. */
        long snapshotEpochSec;

        CensusResult(StrataIndex strata) {
            this.strata = strata;
        }

        public StrataIndex strata() {
            return strata;
        }

        public String groupOf(String stratumName) {
            return strata.groupOf(stratumName);
        }

        public String layerOf(String stratumName) {
            return strata.layerOf(stratumName);
        }

        private static int yearOf(long epochSec) {
            return epochSec <= 0 ? 0
                    : java.time.Instant.ofEpochSecond(epochSec)
                            .atZone(java.time.ZoneOffset.UTC).getYear();
        }

        void noteEditYear(String stratum, String type, long epochSec, double weight) {
            int y = yearOf(epochSec);
            if (y == 0) {
                return;
            }
            editYears.computeIfAbsent(stratum, k -> new HashMap<>())
                    .computeIfAbsent(type, k -> new HashMap<>())
                    .merge(y, weight, Double::sum);
        }

        void addWay(RoadWay w, double km, double midLat, double midLon) {
            String[] families = TagInventory.WAY_TAG_FAMILIES;
            int f = families.length;
            String universe = w.drivable() ? "drivable" : "pedestrian";
            for (String stratum : strata.strataFor(midLat, midLon)) {
                double[] acc = wayMetrics
                        .computeIfAbsent(stratum, k -> new HashMap<>())
                        .computeIfAbsent(w.highwayClass(), k -> new double[2 + 2 * f]);
                acc[0] += 1;
                acc[1] += km;
                for (int i = 0; i < f; i++) {
                    if (TagInventory.hasWayTag(w.tags(), families[i])) {
                        acc[2 + i] += km;
                    }
                    if (TagInventory.isAffirmative(w.tags(), families[i])) {
                        acc[2 + f + i] += km;
                    }
                }
                if (w.separateSidewalk()) {
                    separateSidewalkKm.merge(stratum, km, Double::sum);
                }
                if (w.drivable()) {
                    noteEditYear(stratum, "drivable_km", w.editedAt(), km);
                }
                Map<String, Map<String, Double>> vals =
                        wayValues.computeIfAbsent(stratum, k -> new HashMap<>());
                for (String key : TagInventory.WAY_VALUE_KEYS) {
                    String v = w.tags().get(key);
                    if (v != null) {
                        vals.computeIfAbsent(universe + "|" + key, k -> new HashMap<>())
                                .merge(clean(v), km, Double::sum);
                    }
                }
            }
        }

        void addCrossing(CrossingFeature f) {
            Map<String, String> t = f.effectiveTags();
            for (String stratum : strata.strataFor(f.lat(), f.lon())) {
                Map<String, Long> m = nodeMetrics.computeIfAbsent(stratum, k -> new HashMap<>());
                inc(m, "crossings");
                if (!f.conflictingKeys.isEmpty()) {
                    inc(m, "crossing_conflicts");
                }
                noteEditYear(stratum, "crossings", f.editedAt, 1.0);
                inc(m, "crossing_schema_" + f.schema());
                add(m, "crossing_node_members", f.nodeMembers);
                add(m, "crossing_way_members", f.wayMembers);

                if (t.containsKey("crossing")) {
                    inc(m, "crossing_subtype");
                }
                if (TagInventory.hasModernMarkings(t)) {
                    inc(m, "markings_modern");
                }
                if (TagInventory.hasLegacyMarkings(t)) {
                    inc(m, "markings_legacy");
                }
                if (TagInventory.hasMarkingsStatement(t)) {
                    inc(m, "markings_any");
                    if (TagInventory.isMarked(t)) {
                        inc(m, "markings_marked");
                    }
                }
                if (t.containsKey("crossing:island")) {
                    inc(m, "crossing_island");
                    if (TagInventory.isAffirmative(t, "crossing:island")) {
                        inc(m, "crossing_island_yes");
                    }
                }
                if (t.containsKey("tactile_paving")) {
                    inc(m, "crossing_tactile");
                    if (TagInventory.isAffirmative(t, "tactile_paving")) {
                        inc(m, "crossing_tactile_yes");
                    }
                }
                if (t.containsKey("button_operated")) {
                    inc(m, "crossing_button");
                }
                if (t.containsKey("traffic_signals:sound")) {
                    inc(m, "crossing_sound");
                    if (TagInventory.isAffirmative(t, "traffic_signals:sound")) {
                        inc(m, "crossing_sound_yes");
                    }
                }
                if (TagInventory.assertsSignalised(t)) {
                    inc(m, "crossing_signalised");
                }
                String kerb = f.effectiveKerb();
                if (kerb != null) {
                    inc(m, "crossing_kerb");
                    String kc = TagInventory.kerbClass(kerb);
                    if (TagInventory.KERB_ACCESSIBLE.equals(kc)) {
                        inc(m, "crossing_kerb_accessible");
                    } else if (TagInventory.KERB_BARRIER.equals(kc)) {
                        inc(m, "crossing_kerb_barrier");
                    }
                }

                Map<String, Map<String, Long>> vals =
                        crossingValues.computeIfAbsent(stratum, k -> new HashMap<>());
                for (String key : TagInventory.CROSSING_VALUE_KEYS) {
                    String v = "kerb".equals(key) ? kerb : t.get(key);
                    if (v != null) {
                        vals.computeIfAbsent(key, k -> new HashMap<>())
                                .merge(clean(v), 1L, Long::sum);
                    }
                }
            }
        }

        /**
         * Destination-side accessibility (docs/metrics.md section 7). The crossing census
         * measures the journey; this measures its ends. Blind-pedestrian navigation is
         * transit-centric, and a route that arrives at a door the traveller cannot identify
         * has not succeeded - neither is visible to a way-and-crossing census.
         */
        void addPoi(AccessPoi p) {
            String prefix = switch (p.kind()) {
                case TRANSIT_STOP -> "stop";
                case STATION -> "station";
                case POI -> "poi";
            };
            for (String stratum : strata.strataFor(p.lat(), p.lon())) {
                Map<String, Long> m = nodeMetrics.computeIfAbsent(stratum, k -> new HashMap<>());
                inc(m, prefix + "_total");
                if (p.tags().containsKey("tactile_paving")) {
                    inc(m, prefix + "_tactile_stated");
                    if (p.hasTactile()) {
                        inc(m, prefix + "_tactile_yes");
                    }
                }
                String wc = p.wheelchair();
                if (wc != null) {
                    inc(m, prefix + "_wheelchair_stated");
                    if ("yes".equals(wc) || "designated".equals(wc)) {
                        inc(m, prefix + "_wheelchair_yes");
                    } else if ("limited".equals(wc)) {
                        inc(m, prefix + "_wheelchair_limited");
                    } else if ("no".equals(wc)) {
                        inc(m, prefix + "_wheelchair_no");
                    }
                }
                if (p.hasBrailleStatement()) {
                    inc(m, prefix + "_braille");
                }
                if (p.hasBraille()) {
                    inc(m, prefix + "_braille_yes");
                }
                if (p.hasTactileWritingStatement()) {
                    inc(m, prefix + "_tactile_writing_stated");
                    if (p.hasTactileWriting()) {
                        inc(m, prefix + "_tactile_writing_yes");
                    }
                }
                if (p.tags().containsKey("name")) {
                    inc(m, prefix + "_named");
                }
                if (p.tags().containsKey("departures_board")
                        || p.tags().containsKey("passenger_information_display")) {
                    inc(m, prefix + "_departures");
                }
                if (p.tags().containsKey("shelter")) {
                    inc(m, prefix + "_shelter");
                }
            }
        }

        void addSignal(AccessNode n) {
            Map<String, String> t = n.tags();
            for (String stratum : strata.strataFor(n.lat(), n.lon())) {
                Map<String, Long> m = nodeMetrics.computeIfAbsent(stratum, k -> new HashMap<>());
                inc(m, "signal_nodes");
                if (t.containsKey("traffic_signals:sound")) {
                    inc(m, "signal_sound");
                    if (TagInventory.isAffirmative(t, "traffic_signals:sound")) {
                        inc(m, "signal_sound_yes");
                    }

                }
                if (t.containsKey("traffic_signals:vibration")) {
                    inc(m, "signal_vibration");
                    if (TagInventory.isAffirmative(t, "traffic_signals:vibration")) {
                        inc(m, "signal_vibration_yes");
                    }
                }
            }
        }

        void addLegacyCrossingNode(AccessNode n) {
            for (String stratum : strata.strataFor(n.lat(), n.lon())) {
                Map<String, Long> m = nodeMetrics.computeIfAbsent(stratum, k -> new HashMap<>());
                inc(m, "legacy_crossing_nodes");
                if (n.tags().containsKey("crossing")) inc(m, "legacy_crossing_subtype");
                if (n.tags().containsKey("tactile_paving")) inc(m, "legacy_crossing_tactile");
            }
        }

        void addKerbNode(AccessNode n) {
            String kc = TagInventory.kerbClass(n.kerbValue());
            for (String stratum : strata.strataFor(n.lat(), n.lon())) {
                Map<String, Long> m = nodeMetrics.computeIfAbsent(stratum, k -> new HashMap<>());
                inc(m, "kerb_nodes");
                inc(m, "kerb_" + kc);
            }
        }

        void addIntersection(double lat, double lon) {
            for (String stratum : strata.strataFor(lat, lon)) {
                inc(nodeMetrics.computeIfAbsent(stratum, k -> new HashMap<>()), "intersections");
            }
        }

        void addJunction(double lat, double lon) {
            for (String stratum : strata.strataFor(lat, lon)) {
                inc(nodeMetrics.computeIfAbsent(stratum, k -> new HashMap<>()), "junctions_degree3");
            }
        }

        private static void inc(Map<String, Long> m, String key) {
            m.merge(key, 1L, Long::sum);
        }

        private static void add(Map<String, Long> m, String key, long v) {
            m.merge(key, v, Long::sum);
        }

        private static String clean(String v) {
            return v.trim().toLowerCase(Locale.ROOT);
        }

        public long node(String stratum, String key) {
            return nodeMetrics.getOrDefault(stratum, Map.of()).getOrDefault(key, 0L);
        }

        /** All strata that received at least one feature, NATIONAL first then alphabetical. */
        public List<String> orderedStrata() {
            Set<String> names = new HashSet<>(wayMetrics.keySet());
            names.addAll(nodeMetrics.keySet());
            List<String> out = new ArrayList<>(new TreeMap<>(
                    names.stream().collect(java.util.stream.Collectors.toMap(s -> s, s -> s))).keySet());
            out.remove(NATIONAL);
            out.add(0, NATIONAL);
            return out;
        }
    }

    public static void write(CensusResult result, Path outDir) throws IOException {
        new MetricsWriter(result).writeAll(outDir);
    }
}
