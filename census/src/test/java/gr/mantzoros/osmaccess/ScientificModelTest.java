package gr.mantzoros.osmaccess;

import com.google.protobuf.ByteString;
import crosby.binary.Fileformat;
import crosby.binary.Osmformat;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.io.DataOutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

class ScientificModelTest {
    private final List<Path> outputs = new ArrayList<>();

    private Path output() throws Exception {
        Path path = Path.of("target", "scientific-test-output", java.util.UUID.randomUUID().toString());
        Files.createDirectories(path);
        outputs.add(path);
        return path;
    }

    @AfterEach
    void cleanup() throws Exception {
        for (Path path : outputs) {
            try (var files = Files.walk(path)) {
                for (Path file : files.sorted(Comparator.reverseOrder()).toList()) {
                    Files.delete(file);
                }
            }
        }
    }

    private AccessProfile profile(String name) throws Exception {
        return AccessProfile.load(Path.of("..", "data", "profile-" + name + ".json").toFile());
    }

    private RoadWay way(long id, String highway, Map<String, String> tags, long... nodes) {
        Map<String, String> all = new HashMap<>(tags);
        all.put("highway", highway);
        return new RoadWay(id, highway, TagInventory.isDrivable(highway),
                TagInventory.isPedestrian(highway), false,
                TagInventory.isCrossingWay(highway, all), all, nodes, 0, 1);
    }

    private RoutingGraph graph(String scope, List<RoadWay> ways,
                               Map<Long, Map<String, String>> nodeTags, double... positionsKm) throws Exception {
        int n = positionsKm.length;
        long[] ids = new long[n];
        double[] lat = new double[n];
        double[] lon = new double[n];
        boolean[] resolved = new boolean[n];
        for (int i = 0; i < n; i++) {
            ids[i] = i + 1;
            lon[i] = Math.toDegrees(positionsKm[i] * 1000 / 6_371_000.0);
            resolved[i] = true;
        }
        return new RoutingGraph(profile("bvi"), scope, ways, ids, lat, lon, resolved,
                nodeTags, Map.of());
    }

    private StrataIndex strata() {
        return new StrataIndex(List.of());
    }

    @Test
    void parserPreservesProfileKeysAndRampExceptions() throws Exception {
        Path pbf = output().resolve("fixture.osm.pbf");
        writePbf(pbf);
        AccessProfile bvi = profile("bvi");
        PbfCensus census = new PbfCensus(pbf.toFile(), strata(), 2).routingProfile(bvi).retainFeatures(true);
        PbfCensus.CensusResult result = census.run();
        assertEquals(java.util.HexFormat.of().formatHex(
                java.security.MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(pbf))),
                census.inputSha256());
        Map<Long, RoadWay> ways = new HashMap<>();
        census.waysForSampling().forEach(w -> ways.put(w.id(), w));
        assertEquals("yes", ways.get(101L).tags().get("handrail"));
        assertEquals("yes", ways.get(102L).tags().get("traffic_signals:sound"));
        assertEquals(EdgeState.PASS, bvi.evaluate(ways.get(101L)));
        assertEquals(EdgeState.PASS, bvi.evaluate(ways.get(102L)));
        assertEquals(EdgeState.PASS, profile("wheelchair").evaluate(ways.get(103L)));
        assertEquals(EdgeState.BLOCK, profile("wheelchair").evaluate(
                way(200, "steps", Map.of(), 1, 2)));
        assertEquals("steps", ways.get(103L).tags().get("highway"));
        PbfCensus.write(result, output());
    }

    @Test
    void customConfiguredKeysAreRetained() throws Exception {
        Path json = output().resolve("custom.json");
        String text = Files.readString(Path.of("..", "data", "profile-bvi.json"))
                .replace("\"traffic_signals:sound\":", "\"custom:acoustic\":");
        Files.writeString(json, text);
        AccessProfile profile = AccessProfile.load(json.toFile());
        Map<String, String> tags = TagInventory.retainTags(Map.of(
                "highway", "footway", "footway", "crossing", "custom:acoustic", "yes"),
                TagInventory.WAY_KEYS, profile.requiredKeys());
        assertEquals(EdgeState.PASS, profile.evaluate(way(1, "footway", tags, 1, 2)));
    }

    @Test
    void parsedCrossingMembersShareEffectiveKerbsWithRouting() throws Exception {
        Path pbf = output().resolve("members.osm.pbf");
        writePbf(pbf, true);
        PbfCensus census = new PbfCensus(pbf.toFile(), strata(), 2)
                .routingProfile(profile("bvi")).retainFeatures(true);
        PbfCensus.CensusResult result = census.run();
        assertEquals(1, result.node("NATIONAL", "crossings"));
        assertEquals(2, result.node("NATIONAL", "legacy_crossing_nodes"));
        assertEquals(1, result.node("NATIONAL", "crossing_kerb_barrier"));
        assertEquals("raised", census.crossingFeatures().getFirst().effectiveKerb());
        assertEquals(2, census.crossingFeatures().getFirst().wayMembers);
        RoutingGraph routing = census.graph(profile("bvi"), "extended");
        assertEquals(EdgeState.BLOCK, routing.ways.stream()
                .filter(w -> w.source().id() == 102).findFirst().orElseThrow().state());
        assertEquals(EdgeState.BLOCK, routing.transitions.get(1));
    }

    @Test
    void polygonBoundaryHasOneDeterministicOwnerPerLayer() {
        org.locationtech.jts.geom.GeometryFactory factory = new org.locationtech.jts.geom.GeometryFactory();
        var left = factory.toGeometry(new org.locationtech.jts.geom.Envelope(0, 1, 0, 1));
        var right = factory.toGeometry(new org.locationtech.jts.geom.Envelope(1, 2, 0, 1));
        PolygonStratum a = new PolygonStratum("a", "g", "municipality", left);
        PolygonStratum b = new PolygonStratum("b", "g", "municipality", right);
        assertEquals(List.of("NATIONAL", "a"), new StrataIndex(List.of(b, a)).strataFor(.5, 1));
        assertEquals(List.of("NATIONAL", "a"), new StrataIndex(List.of(a, b)).strataFor(.5, 1));
    }

    @Test
    void stationRecordsDoNotInheritPlatformProvision() {
        assertEquals(AccessPoi.Kind.STATION, AccessPoi.classify(Map.of("public_transport", "station")));
        assertEquals(AccessPoi.Kind.TRANSIT_STOP, AccessPoi.classify(
                Map.of("railway", "platform", "tactile_paving", "yes")));
        assertFalse(new AccessPoi(1, 0, 0, AccessPoi.Kind.STATION,
                Map.of("tactile_paving", "false")).hasTactile());
    }

    @Test
    void conflictsAndWorstKerbAreOrderIndependent() throws Exception {
        CrossingFeature first = new CrossingFeature();
        first.merge(Map.of("tactile_paving", "yes", "kerb", "lowered"));
        first.merge(Map.of("tactile_paving", "no"));
        first.kerbFromMember = "raised";
        CrossingFeature reverse = new CrossingFeature();
        reverse.merge(Map.of("tactile_paving", "no"));
        reverse.merge(Map.of("tactile_paving", "yes", "kerb", "lowered"));
        reverse.kerbFromMember = "raised";
        assertEquals(first.effectiveTags(), reverse.effectiveTags());
        assertEquals("raised", first.effectiveKerb());
        assertFalse(TagInventory.isAffirmative(first.effectiveTags(), "tactile_paving"));
        assertEquals(EdgeState.BLOCK, profile("bvi").evaluateCrossingTags(first.effectiveTags()));
        assertTrue(first.conflictingKeys.contains("tactile_paving"));
    }

    @Test
    void explicitRestrictionsAndRoadMeasurementScope() throws Exception {
        AccessProfile bvi = profile("bvi");
        assertEquals(EdgeState.BLOCK, bvi.evaluate(way(1, "footway", Map.of("foot", "no"), 1, 2)));
        assertEquals(EdgeState.BLOCK, bvi.evaluate(way(1, "footway", Map.of("access", "private"), 1, 2)));
        assertEquals(EdgeState.PASS, bvi.evaluate(way(1, "footway",
                Map.of("access", "private", "foot", "yes"), 1, 2)));
        assertEquals(EdgeState.BLOCK, profile("wheelchair").evaluate(way(1, "footway",
                Map.of("wheelchair", "no"), 1, 2)));
        assertEquals(EdgeState.PASS, bvi.evaluate(way(1, "residential",
                Map.of("sidewalk", "yes", "surface", "mud", "width", "0.1"), 1, 2)));
        assertEquals(EdgeState.BLOCK, bvi.evaluate(way(1, "residential",
                Map.of("sidewalk", "yes", "sidewalk:width", "0.1"), 1, 2)));
        assertEquals(EdgeState.BLOCK, bvi.evaluate(way(1, "residential",
                Map.of("sidewalk", "yes", "sidewalk:both", "no"), 1, 2)));
        assertEquals(EdgeState.UNKNOWN, bvi.evaluate(way(1, "residential",
                Map.of("sidewalk", "separate"), 1, 2)));
        assertEquals(EdgeState.BLOCK, bvi.evaluate(way(1, "steps",
                Map.of("handrail", "no", "tactile_paving", "yes"), 1, 2)));
        assertEquals(EdgeState.PASS, profile("bvi-permissive").evaluate(way(1, "footway",
                Map.of("footway", "crossing", "button_operated", "yes"), 1, 2)));
    }

    @Test
    void unknownAndBlockedPointTransitionsPreserveApproachesButPreventThroughTravel() throws Exception {
        List<RoadWay> ways = List.of(way(1, "footway", Map.of(), 1, 2, 3));
        RoutingGraph unknown = graph("extended", ways,
                Map.of(2L, Map.of("highway", "crossing")), 0, 0.6, 1.2);
        assertEquals(0, unknown.closure(false, 1).eligibleUsable, 1e-8);
        assertEquals(1.2, unknown.closure(true, 1).eligibleUsable, 1e-8);
        RoutingGraph blocked = graph("extended", ways,
                Map.of(2L, Map.of("barrier", "kerb", "kerb", "raised")), 0, 0.6, 1.2);
        assertEquals(0, blocked.closure(true, 1).eligibleUsable, 1e-8);
        assertEquals(1.2, Arrays.stream(blocked.closure(false, 1).mass).sum(), 1e-8);
    }

    @Test
    void unifiedWayEvidenceIsUsedByRouting() throws Exception {
        AccessProfile profile = profile("bvi");
        RoadWay crossing = way(1, "footway", Map.of("footway", "crossing"), 1, 2);
        RoutingGraph graph = new RoutingGraph(profile, "extended", List.of(crossing),
                new long[]{1, 2}, new double[]{0, 0}, new double[]{0, 0.01},
                new boolean[]{true, true}, Map.of(),
                Map.of(1L, Map.of("tactile_paving", "yes", "kerb", "raised")));
        assertEquals(EdgeState.BLOCK, graph.ways.getFirst().state());
    }

    @Test
    void candidateAttachesAtInteriorJunctionAndAddsNoLengthTwice() throws Exception {
        RoutingGraph graph = graph("extended", List.of(
                way(1, "footway", Map.of(), 2, 4),
                way(2, "residential", Map.of(), 1, 2, 3)),
                Map.of(), 0, 0.2, 0.4, 0.9);
        RoutingGraph.Closure state = graph.closure(false, 1);
        assertEquals(0, state.eligibleUsable, 1e-8);
        RoutingGraph.Way candidate = graph.ways.get(1);
        assertEquals(1.1, state.add(candidate), 1e-8);
        assertEquals(0, state.add(candidate), 1e-8);
        assertEquals(1.1, state.eligibleUsable, 1e-8);
    }

    @Test
    void unresolvedInteriorGeometryDoesNotCreateAnEndpointShortcut() throws Exception {
        RoadWay way = way(1, "residential", Map.of(), 1, 2, 3, 4, 5);
        RoutingGraph graph = new RoutingGraph(profile("bvi"), "extended", List.of(way),
                new long[]{1, 2, 3, 4, 5}, new double[5], new double[]{0, .005, .01, .015, .02},
                new boolean[]{true, true, false, true, true}, Map.of(), Map.of());
        RoutingGraph.Closure state = graph.closure(false, 1);
        state.add(graph.ways.getFirst());
        assertEquals(0, state.eligibleUsable, 1e-8);
        assertNotEquals(state.find(0), state.find(4));
    }

    @Test
    void coreSensitivityActuallyRebuildsTopologyAndThresholds() throws Exception {
        List<RoadWay> ways = List.of(way(1, "footway", Map.of(), 1, 2),
                way(2, "track", Map.of(), 2, 3), way(3, "footway", Map.of(), 3, 4));
        RoutingGraph extended = graph("extended", ways, Map.of(), 0, .6, .7, 1.3);
        RoutingGraph core = graph("core", ways, Map.of(), 0, .6, .7, 1.3);
        assertEquals(1.2, extended.closure(true, 1).eligibleUsable, 1e-8);
        assertEquals(0, core.closure(true, 1).eligibleUsable, 1e-8);
        assertEquals(1.2, core.closure(true, .5).eligibleUsable, 1e-8);
        assertEquals(2, core.ways.size());
        FeasibilityAnalysis analysis = new FeasibilityAnalysis(extended, strata(), 1);
        analysis.run();
        assertEquals(.1, analysis.national()[22], 1e-8);
        assertEquals(0, analysis.national()[21], 1e-8);
        Path dir = output();
        analysis.write(dir);
        analysis.appendSensitivity(dir.resolve("feasibility_sensitivity.csv"));
        assertTrue(Files.readString(dir.resolve("feasibility.csv")).contains("eligible_decidability_gap_pct"));
    }

    @Test
    void targetingReportsActualUsableDeltaAndIndependentLengthBaseline() throws Exception {
        RoutingGraph graph = graph("extended", List.of(
                way(1, "footway", Map.of(), 1, 2),
                way(2, "residential", Map.of(), 2, 3)), Map.of(), 0, .7, 1.1);
        Path dir = output();
        new EditTargeting(graph, strata()).run(dir, 1);
        List<String> lines = Files.readAllLines(dir.resolve("edit_targets.csv"));
        String[] row = lines.get(1).split(",");
        assertEquals("1.100", row[12]);
        assertEquals("1.100", row[13]);
        assertTrue(Files.exists(dir.resolve("edit_targets_length_baseline.csv")));
        assertEquals(Files.readAllLines(dir.resolve("edit_targets_summary.csv")).get(0).split(",").length,
                Files.readAllLines(dir.resolve("edit_targets_summary.csv")).get(1).split(",").length);
    }

    @Test
    void degreeThreeJunctionIsInvariantToDuplicateNeighbours() {
        int[] first = {-1};
        int[] second = {-1};
        boolean[] degree3 = {false};
        PbfCensus.addNeighbour(0, 1, first, second, degree3);
        PbfCensus.addNeighbour(0, 2, first, second, degree3);
        PbfCensus.addNeighbour(0, 1, first, second, degree3);
        assertFalse(degree3[0]);
        PbfCensus.addNeighbour(0, 3, first, second, degree3);
        assertTrue(degree3[0]);
    }

    @Test
    void recoveredNetworkSubtractsOnlySelectedLengthActuallyUsable() throws Exception {
        RoutingGraph graph = graph("extended", List.of(
                way(1, "footway", Map.of(), 1, 2),
                way(2, "residential", Map.of(), 2, 3, 4)),
                Map.of(3L, Map.of("kerb", "raised")), 0, .7, 1.1, 1.3);
        Path dir = output();
        new EditTargeting(graph, strata()).run(dir, 1);
        List<String> lines = Files.readAllLines(dir.resolve("edit_targets_summary.csv"));
        String[] keys = lines.get(0).split(",");
        String[] values = lines.get(1).split(",", -1);
        Map<String, String> summary = new HashMap<>();
        for (int i = 0; i < keys.length; i++) summary.put(keys[i], values[i]);
        assertEquals("0.600", summary.get("survey_km"));
        assertEquals("0.400", summary.get("selected_usable_km_after"));
        assertEquals("0.700", summary.get("recovered_preexisting_usable_km"));
        assertEquals("1.100", summary.get("usable_gain_km"));
        assertEquals("0.700", summary.get("length_baseline_recovered_preexisting_usable_km"));
    }

    @Test
    void cliWritesFreshCoreAndThresholdSensitivityWithoutChangingPrimaryScope() throws Exception {
        Path dir = output();
        Path pbf = dir.resolve("cli.osm.pbf");
        Path config = dir.resolve("strata.json");
        writePbf(pbf);
        Files.writeString(config, "{\"strata\":[],\"polygonLayers\":[]}");
        Main.main(new String[]{pbf.toString(), config.toString(), dir.resolve("run").toString(),
                "--profile=" + Path.of("..", "data", "profile-bvi.json"),
                "--threads=2", "--targets=1", "--component-thresholds=0.5,1,2",
                "--graph-scope=core"});
        List<String> rows = Files.readAllLines(dir.resolve("run").resolve("feasibility_sensitivity.csv"));
        assertEquals(7, rows.size());
        assertEquals(3, rows.stream().filter(row -> row.contains(",extended,")).count());
        assertEquals(3, rows.stream().filter(row -> row.contains(",core,")).count());
        assertTrue(Files.readAllLines(dir.resolve("run").resolve("feasibility.csv"))
                .get(1).contains(",core,"));
        assertTrue(Files.exists(dir.resolve("run").resolve("edit_targets_length_baseline.csv")));
    }

    @Test
    void cliPinsLocalFeatureAuditToActualInputBytes() throws Exception {
        Path dir = output();
        Path pbf = dir.resolve("audit.osm.pbf");
        Path config = dir.resolve("strata.json");
        Path scope = dir.resolve("scope.json");
        writePbf(pbf);
        Files.writeString(config, """
                {"strata":[{"name":"fixture","group":"metro","centerLat":0,
                "centerLon":0,"radiusKm":2}],"polygonLayers":[]}
                """);
        Files.writeString(scope, "{\"laus\":[\"fixture\"]}");
        Main.main(new String[]{pbf.toString(), config.toString(), dir.resolve("run").toString(),
                "--profile=" + Path.of("..", "data", "profile-bvi.json"),
                "--threads=2", "--sample=1", "--sample-scope=" + scope});
        String csv = Files.readString(dir.resolve("run").resolve("audit_sample.csv"));
        String hash = java.util.HexFormat.of().formatHex(
                java.security.MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(pbf)));
        assertTrue(csv.contains("mapped_feature_state"));
        assertTrue(csv.contains(AuditSampler.SAMPLING_FRAME));
        assertTrue(csv.contains(hash));
    }

    @Test
    void cliCannotOverwriteAnExistingPublishedAuditSample() throws Exception {
        Path dir = output();
        Path pbf = dir.resolve("fixture.osm.pbf");
        Path config = dir.resolve("strata.json");
        writePbf(pbf);
        Files.writeString(config, "{\"strata\":[],\"polygonLayers\":[]}");
        Files.writeString(dir.resolve("audit_sample.csv"), "retained original cases");
        IllegalArgumentException failure = assertThrows(IllegalArgumentException.class, () ->
                Main.main(new String[]{pbf.toString(), config.toString(), dir.toString(), "--sample=1"}));
        assertTrue(failure.getMessage().contains("will not be overwritten"));
        assertEquals("retained original cases", Files.readString(dir.resolve("audit_sample.csv")));
    }

    private void writePbf(Path file) throws Exception {
        writePbf(file, false);
    }

    private void writePbf(Path file, boolean kerbMembers) throws Exception {
        List<String> strings = List.of("", "highway", "steps", "handrail", "yes", "footway",
                "crossing", "traffic_signals:sound", "ramp:wheelchair", "kerb", "lowered",
                "raised", "tactile_paving", "track");
        Osmformat.StringTable.Builder table = Osmformat.StringTable.newBuilder();
        strings.forEach(s -> table.addS(ByteString.copyFromUtf8(s)));
        Osmformat.PrimitiveGroup.Builder group = Osmformat.PrimitiveGroup.newBuilder();
        for (int i = 1; i <= 4; i++) {
            Osmformat.Node.Builder node = Osmformat.Node.newBuilder()
                    .setId(i).setLat(0).setLon(i * 10000);
            if (kerbMembers && (i == 2 || i == 3)) {
                node.addKeys(1).addVals(6).addKeys(9).addVals(i == 2 ? 10 : 11);
                if (i == 3) node.addKeys(12).addVals(4);
            }
            group.addNodes(node);
        }
        group.addWays(Osmformat.Way.newBuilder().setId(101).addKeys(1).addVals(2)
                .addKeys(3).addVals(4).addRefs(1).addRefs(1));
        group.addWays(Osmformat.Way.newBuilder().setId(102).addKeys(1).addVals(5)
                .addKeys(5).addVals(6).addKeys(7).addVals(4).addRefs(2).addRefs(1));
        group.addWays(Osmformat.Way.newBuilder().setId(103).addKeys(1).addVals(2)
                .addKeys(8).addVals(4).addRefs(3).addRefs(1));
        group.addWays(Osmformat.Way.newBuilder().setId(105).addKeys(1).addVals(13).addRefs(1).addRefs(3));
        if (kerbMembers) {
            group.addWays(Osmformat.Way.newBuilder().setId(104).addKeys(1).addVals(5)
                    .addKeys(5).addVals(6).addKeys(9).addVals(10).addRefs(3).addRefs(1));
        }
        try (DataOutputStream out = new DataOutputStream(Files.newOutputStream(file))) {
            block(out, "OSMHeader", Osmformat.HeaderBlock.newBuilder()
                    .addRequiredFeatures("OsmSchema-V0.6").build().toByteString());
            block(out, "OSMData", Osmformat.PrimitiveBlock.newBuilder().setStringtable(table)
                    .addPrimitivegroup(group).build().toByteString());
        }
    }

    private void block(DataOutputStream out, String type, ByteString data) throws Exception {
        Fileformat.Blob blob = Fileformat.Blob.newBuilder().setRaw(data).build();
        Fileformat.BlobHeader header = Fileformat.BlobHeader.newBuilder()
                .setType(type).setDatasize(blob.getSerializedSize()).build();
        out.writeInt(header.getSerializedSize());
        header.writeTo(out);
        blob.writeTo(out);
    }
}
