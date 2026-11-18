package gr.mantzoros.osmaccess;

import com.google.protobuf.ByteString;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import crosby.binary.Fileformat;
import crosby.binary.Osmformat;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.*;

class SharedModelCorrectionsTest {
    private final List<Path> outputs = new ArrayList<>();

    private Path output() throws IOException {
        Path path = Path.of("target", "corrected-model-test-output",
                java.util.UUID.randomUUID().toString());
        Files.createDirectories(path);
        outputs.add(path);
        return path;
    }

    @AfterEach
    void cleanup() throws IOException {
        for (Path path : outputs) {
            try (var files = Files.walk(path)) {
                for (Path file : files.sorted(Comparator.reverseOrder()).toList()) {
                    Files.delete(file);
                }
            }
        }
        Path parent = Path.of("target", "corrected-model-test-output");
        if (Files.isDirectory(parent)) {
            try (var files = Files.list(parent)) {
                if (files.findAny().isEmpty()) {
                    Files.delete(parent);
                }
            }
        }
    }

    private AccessProfile profile(String name) throws IOException {
        return AccessProfile.load(Path.of("..", "data", "profile-" + name + ".json").toFile());
    }

    private StrataIndex circle() {
        return new StrataIndex(List.of(new CircleStratum("fixture", "metro", 0, 0, 2)));
    }

    private RoadWay way(long id, String highway, Map<String, String> tags, long... nodes) {
        Map<String, String> all = new HashMap<>(tags);
        all.put("highway", highway);
        return new RoadWay(id, highway, TagInventory.isDrivable(highway),
                TagInventory.isPedestrian(highway), false,
                TagInventory.isCrossingWay(highway, all), all, nodes, 0, 1);
    }

    @Test
    void crossingReconciliationKeepsOriginalObjectPermissions() throws Exception {
        Path file = output().resolve("crossing-members.osm.pbf");
        writeCrossingMembers(file);
        AccessProfile bvi = profile("bvi");
        PbfCensus census = new PbfCensus(file.toFile(), circle(), 2)
                .routingProfile(bvi).retainFeatures(true);
        PbfCensus.CensusResult result = census.run();

        assertEquals(1, result.node("NATIONAL", "crossings"));
        CrossingFeature group = census.crossingFeatures().getFirst();
        assertEquals("private", group.effectiveTags().get("access"));
        assertEquals("yes", group.effectiveTags().get("foot"));
        assertFalse(group.routingEvidence().containsKey("access"));
        assertFalse(group.routingEvidence().containsKey("foot"));
        assertEquals(EdgeState.BLOCK, group.evaluateCrossing(bvi));
        assertEquals(2, group.memberSegments().size());
        assertEquals(List.of(201L, 202L),
                census.waysForSampling().stream().map(RoadWay::id).toList());

        RoutingGraph graph = census.graph(bvi, "extended");
        Map<Long, EdgeState> states = new HashMap<>();
        graph.ways.forEach(w -> states.put(w.source().id(), w.state()));
        assertEquals(EdgeState.BLOCK, states.get(201L));
        assertEquals(EdgeState.PASS, states.get(202L));
        assertEquals(EdgeState.BLOCK, graph.transitions.get(0));
        assertFalse(graph.transitions.containsKey(1));

        Path audit = output();
        new AuditSampler(circle(), 20260722L, 1, bvi).withArms(Set.of("commission"))
                .write(census.crossingFeatures(), List.of(), audit);
        assertTrue(Files.readString(audit.resolve("audit_sample.csv")).contains(",BLOCK,"));
    }

    @Test
    void hardBarrierTokensDominateAndConflictingAcousticEvidenceCannotPass() throws Exception {
        AccessProfile bvi = profile("bvi");
        assertEquals(EdgeState.BLOCK, bvi.evaluateNode(
                Map.of("barrier", "kerb;wall", "foot", "yes")));
        assertEquals(EdgeState.BLOCK, bvi.evaluateNode(Map.of(
                "highway", "crossing", "barrier", "kerb;wall",
                "traffic_signals:sound", "yes", "foot", "yes")));
        assertEquals(EdgeState.BLOCK, profile("wheelchair").evaluateNode(
                Map.of("barrier", "gate;stile", "foot", "yes")));
        assertTrue(new AccessNode(1, 0, 0, Map.of("barrier", "kerb;wall"), 0, 1).isKerb());

        Map<String, String> conflict = Map.of("traffic_signals:sound", "no;yes");
        assertFalse(TagInventory.isAffirmative(conflict, "traffic_signals:sound"));
        assertEquals(EdgeState.UNKNOWN, bvi.evaluateCrossingTags(conflict));
        assertEquals(EdgeState.UNKNOWN, bvi.evaluateNode(Map.of(
                "highway", "crossing", "traffic_signals:sound", "no;yes")));
        assertEquals(EdgeState.PASS, bvi.evaluateCrossingTags(Map.of(
                "traffic_signals:sound", "no;yes", "tactile_paving", "yes")));
        assertEquals(EdgeState.BLOCK, bvi.evaluateCrossingTags(Map.of(
                "traffic_signals:sound", "no;yes", "tactile_paving", "no")));
    }

    @Test
    void wheelchairSpecificRampRefusalDefeatsGenericRamp() throws Exception {
        AccessProfile wheelchair = profile("wheelchair");
        assertEquals(EdgeState.PASS, wheelchair.evaluate(
                way(1, "steps", Map.of("ramp", "yes"), 1, 2)));
        assertEquals(EdgeState.PASS, wheelchair.evaluate(
                way(2, "steps", Map.of("ramp:wheelchair", "yes"), 1, 2)));
        assertEquals(EdgeState.BLOCK, wheelchair.evaluate(way(3, "steps",
                Map.of("ramp", "yes", "ramp:wheelchair", "no"), 1, 2)));
        assertEquals(EdgeState.BLOCK, wheelchair.evaluate(way(4, "steps",
                Map.of("ramp", "yes", "ramp:wheelchair", "no;yes"), 1, 2)));
        assertEquals(EdgeState.PASS, profile("bvi").evaluate(way(5, "steps",
                Map.of("handrail", "yes", "ramp:wheelchair", "no"), 1, 2)));
    }

    @Test
    void compoundSignalisationIsRecognisedWithoutAffirmingContradictions() {
        assertTrue(TagInventory.assertsSignalised(Map.of("crossing", "marked;traffic_signals")));
        assertTrue(TagInventory.assertsSignalised(Map.of("crossing", "pelican;marked")));
        assertTrue(TagInventory.assertsSignalised(Map.of("crossing", "unmarked;traffic_signals")));
        assertFalse(TagInventory.assertsSignalised(Map.of("crossing", "no;traffic_signals")));
        assertFalse(TagInventory.assertsSignalised(Map.of("crossing:signals", "no;yes")));
        assertFalse(TagInventory.assertsSignalised(Map.of(
                "crossing", "marked;traffic_signals", "crossing:signals", "no")));
        assertTrue(TagInventory.assertsSignalised(Map.of("crossing:signals", "yes")));

        CrossingFeature feature = new CrossingFeature();
        feature.fromNode = true;
        feature.nodeIds.add(1L);
        feature.addPosition(0, 0);
        feature.merge(Map.of("crossing", "marked;traffic_signals"));
        PbfCensus.CensusResult result = new PbfCensus.CensusResult(circle());
        result.addCrossing(feature);
        assertEquals(1, result.node("NATIONAL", "crossing_signalised"));
    }

    @Test
    void widthAndInclineUnitsAreConvertedBeforeBlocking() throws Exception {
        AccessProfile bvi = profile("bvi");
        assertEquals(EdgeState.BLOCK, bvi.evaluate(
                way(1, "footway", Map.of("width", "500 mm"), 1, 2)));
        assertEquals(EdgeState.BLOCK, bvi.evaluate(
                way(2, "footway", Map.of("width", "50 cm"), 1, 2)));
        assertEquals(EdgeState.BLOCK, bvi.evaluate(
                way(3, "footway", Map.of("width", "2 ft"), 1, 2)));
        assertEquals(EdgeState.PASS, bvi.evaluate(
                way(4, "footway", Map.of("width", "45 in"), 1, 2)));
        assertEquals(EdgeState.PASS, bvi.evaluate(
                way(5, "footway", Map.of("width", "1,5 m"), 1, 2)));
        assertEquals(EdgeState.BLOCK, bvi.evaluate(
                way(6, "footway", Map.of("incline", "5°"), 1, 2)));
        assertEquals(EdgeState.PASS, bvi.evaluate(
                way(7, "footway", Map.of("incline", "3°"), 1, 2)));
        assertEquals(EdgeState.PASS, bvi.evaluate(
                way(8, "footway", Map.of("width", "unmeasured"), 1, 2)));
        assertEquals(EdgeState.BLOCK, bvi.evaluate(way(9, "residential",
                Map.of("sidewalk", "yes", "sidewalk:width", "500 mm"), 1, 2)));
        assertEquals(EdgeState.PASS, bvi.evaluate(way(10, "residential",
                Map.of("sidewalk", "yes", "width", "500 mm"), 1, 2)));
    }

    @Test
    void futurePoolDrawAndHashDoNotDependOnEncounterOrder() throws Exception {
        CrossingFeature first = new CrossingFeature();
        first.fromNode = true;
        first.nodeIds.add(101L);
        first.addPosition(0, .005);
        CrossingFeature second = new CrossingFeature();
        second.fromNode = true;
        second.nodeIds.add(102L);
        second.addPosition(0, .006);
        RoadWay a = way(30, "residential", Map.of(), 1, 2);
        RoadWay b = way(10, "residential", Map.of(), 3, 4);
        RoadWay c = way(20, "residential", Map.of(), 5, 6);
        List<double[]> intersections = List.of(new double[]{0, .008}, new double[]{0, .009});
        Path firstDir = output();
        Path secondDir = output();
        long frozenSeed = 20260722L;
        new AuditSampler(circle(), frozenSeed, 2).write(
                List.of(first, second), intersections, List.of(a, b, c),
                List.of(new double[]{0, .003}, new double[]{0, .001},
                        new double[]{0, .002}), firstDir);
        new AuditSampler(circle(), frozenSeed, 2).write(
                List.of(second, first), List.of(intersections.get(1), intersections.get(0)),
                List.of(c, a, b), List.of(new double[]{0, .002},
                        new double[]{0, .003}, new double[]{0, .001}), secondDir);

        assertArrayEquals(Files.readAllBytes(firstDir.resolve("audit_sample.csv")),
                Files.readAllBytes(secondDir.resolve("audit_sample.csv")));
        assertArrayEquals(Files.readAllBytes(firstDir.resolve("audit_sample_seed.txt")),
                Files.readAllBytes(secondDir.resolve("audit_sample_seed.txt")));
        String csv = Files.readString(firstDir.resolve("audit_sample.csv"));
        assertTrue(csv.contains(AuditSampler.SAMPLING_FRAME));
        for (String row : csv.split("\\R")) {
            if (row.contains(",way:10,")) assertTrue(row.contains(",0.0000000,0.0010000,"));
            if (row.contains(",way:20,")) assertTrue(row.contains(",0.0000000,0.0020000,"));
            if (row.contains(",way:30,")) assertTrue(row.contains(",0.0000000,0.0030000,"));
        }
        assertEquals(2, csv.lines().filter(row -> row.contains(",sidewalk,")).count());
        String metadata = Files.readString(firstDir.resolve("audit_sample_seed.txt"));
        assertTrue(metadata.contains("seed=20260722"));
        assertTrue(metadata.contains("sidewalk_pool=3"));
        assertTrue(metadata.contains("sampling_frame=" + AuditSampler.SAMPLING_FRAME));
    }

    @Test
    void omissionRadiusUsesActualMembersAndCrossingWaySegments() throws Exception {
        CrossingFeature crossing = new CrossingFeature();
        crossing.fromNode = true;
        crossing.nodeIds.addAll(List.of(101L, 102L));
        crossing.addPosition(0, 0);
        crossing.addPosition(0, .00054);
        crossing.addMemberSegment(0, 0, 0, .00054);
        assertTrue(Geo.haversineMeters(0, 0, crossing.lat(), crossing.lon()) > 25);
        Path dir = output();
        new AuditSampler(circle(), 42, 10).withArms(Set.of("omission")).write(
                List.of(crossing), List.of(new double[]{0, 0}, new double[]{0, .00054},
                        new double[]{0, .00027}, new double[]{.0002, .00027},
                        new double[]{.00024, .00027}, new double[]{0, .0015}), dir);
        String csv = Files.readString(dir.resolve("audit_sample.csv"));
        assertEquals(3, csv.lines().count());
        assertTrue(csv.contains(",0.0002400,0.0002700,"));
        assertTrue(csv.contains(",0.0000000,0.0015000,"));
        assertTrue(Files.readString(dir.resolve("audit_sample_seed.txt"))
                .contains("omission_pool=2"));
    }

    @Test
    void wheelchairTargetingAsksAboutItsOwnCrossingConditions() throws Exception {
        AccessProfile wheelchair = profile("wheelchair");
        assertEquals("does this crossing have tactile paving or an acoustic signal?",
                profile("bvi").crossingQuestion());
        RoadWay crossing = way(51, "footway", Map.of("footway", "crossing"), 1, 2);
        RoutingGraph graph = new RoutingGraph(wheelchair, "extended", List.of(crossing),
                new long[]{1, 2}, new double[]{0, 0}, new double[]{0, .01},
                new boolean[]{true, true}, Map.of(), Map.of());
        assertEquals(EdgeState.UNKNOWN, graph.ways.getFirst().state());
        Path dir = output();
        new EditTargeting(graph, circle()).run(dir, 1);
        String row = Files.readAllLines(dir.resolve("edit_targets.csv")).get(1);
        assertTrue(row.contains("flush or lowered kerb"));
        assertTrue(row.contains("wheelchair tag of yes or designated"));
        assertFalse(row.contains("tactile paving"));
        assertFalse(row.contains("acoustic signal"));

        ObjectMapper mapper = new ObjectMapper();
        ObjectNode custom = (ObjectNode) mapper.readTree(
                Path.of("..", "data", "profile-wheelchair.json").toFile());
        ((ObjectNode) custom.path("crossing").path("passIf")).set(
                "kerb", mapper.valueToTree(List.of("lowered")));
        Path customFile = output().resolve("custom-wheelchair.json");
        mapper.writeValue(customFile.toFile(), custom);
        assertTrue(AccessProfile.load(customFile.toFile()).crossingQuestion()
                .contains("a kerb tagged lowered"));
    }

    @Test
    void brailleStatementsAreDistinctFromOtherTactileWritingAndAffirmation() throws Exception {
        AccessPoi no = new AccessPoi(1, 0, 0, AccessPoi.Kind.POI, Map.of("braille", "no"));
        AccessPoi other = new AccessPoi(2, 0, 0, AccessPoi.Kind.POI,
                Map.of("tactile_writing:embossed_letters", "yes"));
        AccessPoi yes = new AccessPoi(3, 0, 0, AccessPoi.Kind.POI,
                Map.of("tactile_writing:braille:el", "yes"));
        AccessPoi absent = new AccessPoi(4, 0, 0, AccessPoi.Kind.POI,
                Map.of("tactile_writing", "no"));
        assertTrue(no.hasBrailleStatement());
        assertFalse(no.hasBraille());
        assertFalse(other.hasBrailleStatement());
        assertFalse(other.hasBraille());
        assertTrue(other.hasTactileWriting());
        assertTrue(yes.hasBrailleStatement());
        assertTrue(yes.hasBraille());
        assertTrue(absent.hasTactileWritingStatement());
        assertFalse(absent.hasTactileWriting());
        assertTrue(new AccessPoi(5, 0, 0, AccessPoi.Kind.POI,
                Map.of("tactile_writing", "braille")).hasBraille());
        assertFalse(AccessPoi.isBrailleKey("tactile_writing:embossed_letters"));

        PbfCensus.CensusResult result = new PbfCensus.CensusResult(circle());
        for (AccessPoi poi : List.of(no, other, yes, absent)) {
            result.addPoi(poi);
        }
        Path dir = output();
        PbfCensus.write(result, dir);
        List<String> lines = Files.readAllLines(dir.resolve("destinations.csv"));
        Map<String, String> values = columns(lines.get(0), lines.stream()
                .filter(row -> row.contains(",poi,")).findFirst().orElseThrow());
        assertEquals("4", values.get("total"));
        assertEquals("2", values.get("braille"));
        assertEquals("2", values.get("braille_stated"));
        assertEquals("1", values.get("braille_yes"));
        assertEquals("3", values.get("tactile_writing_stated"));
        assertEquals("2", values.get("tactile_writing_yes"));
    }

    @Test
    void pbfRetainsBrailleAndOtherTactileWritingOnNodesAndWays() throws Exception {
        Path file = output().resolve("destinations.osm.pbf");
        writeDestinations(file);
        PbfCensus.CensusResult result = new PbfCensus(file.toFile(), circle(), 2).run();
        assertEquals(5, result.node("NATIONAL", "poi_total"));
        assertEquals(3, result.node("NATIONAL", "poi_braille"));
        assertEquals(2, result.node("NATIONAL", "poi_braille_yes"));
        assertEquals(4, result.node("NATIONAL", "poi_tactile_writing_stated"));
        assertEquals(3, result.node("NATIONAL", "poi_tactile_writing_yes"));
    }

    @Test
    void tagValueCsvPreservesDistinctLongValuesAndEscapesPunctuation() throws Exception {
        PbfCensus.CensusResult result = new PbfCensus.CensusResult(circle());
        String prefix = "a".repeat(45);
        String first = "  " + prefix + ", \"first\"\r\nnext  ";
        String second = "  " + prefix + ", \"second\"\r\nnext  ";
        result.addWay(way(1, "residential", Map.of("surface", first), 1, 2), .4, 0, 0);
        result.addWay(way(2, "residential", Map.of("surface", second), 3, 4), .6, 0, 0);
        CrossingFeature crossing = new CrossingFeature();
        crossing.fromNode = true;
        crossing.nodeIds.add(5L);
        crossing.addPosition(0, 0);
        crossing.merge(Map.of("surface", "ROUGH, \"BRICK\";smooth"));
        result.addCrossing(crossing);
        assertEquals(2, result.wayValues.get("NATIONAL")
                .get("drivable|surface").size());
        Path dir = output();
        PbfCensus.write(result, dir);
        String csv = Files.readString(dir.resolve("tag_values.csv"));
        for (String value : List.of(first, second)) {
            String normalised = value.trim().toLowerCase(Locale.ROOT);
            assertTrue(csv.contains('"' + normalised.replace("\"", "\"\"") + '"'));
        }
        String crossingValue = crossing.tags().get("surface").toLowerCase(Locale.ROOT);
        assertTrue(csv.contains('"' + crossingValue.replace("\"", "\"\"") + '"'));
    }

    @Test
    void requestedSamplingAndPolygonFramesFailBeforeCensusWrites() throws Exception {
        Path dir = output();
        Path pbf = dir.resolve("fixture.osm.pbf");
        Path config = dir.resolve("strata.json");
        Path run = dir.resolve("run");
        Files.writeString(pbf, "not parsed on a preflight failure");
        Files.writeString(config, """
                {"strata":[{"name":"fixture","group":"metro","centerLat":0,
                "centerLon":0,"radiusKm":2}],"polygonLayers":[]}
                """);
        Path scope = dir.resolve("missing-frame.json");
        IllegalArgumentException missing = assertThrows(IllegalArgumentException.class,
                () -> Main.main(new String[]{pbf.toString(), config.toString(), run.toString(),
                        "--sample=1", "--sample-scope=" + scope}));
        assertTrue(missing.getMessage().contains("not found"));
        assertFalse(Files.exists(run));

        Files.writeString(scope, "{\"laus\":[\"unknown-city\"]}");
        IllegalArgumentException unknown = assertThrows(IllegalArgumentException.class,
                () -> Main.main(new String[]{pbf.toString(), config.toString(), run.toString(),
                        "--sample=1", "--sample-scope=" + scope}));
        assertTrue(unknown.getMessage().contains("Unknown stratum"));
        Files.writeString(scope, "{\"laus\":[]}");
        assertThrows(IllegalArgumentException.class,
                () -> Main.main(new String[]{pbf.toString(), config.toString(), run.toString(),
                        "--sample=1", "--sample-scope=" + scope}));
        assertFalse(Files.exists(run));

        Files.writeString(config, """
                {"strata":[],"polygonLayers":[{"id":"required",
                "file":"missing.geojson","nameProperty":"name"}]}
                """);
        IOException missingPolygon = assertThrows(IOException.class,
                () -> StrataConfig.load(config.toFile()).index(config.toFile()));
        assertTrue(missingPolygon.getMessage().contains("Requested polygon layer 'required'"));
        Files.writeString(dir.resolve("missing.geojson"),
                "{\"type\":\"FeatureCollection\",\"features\":[]}");
        IOException emptyPolygon = assertThrows(IOException.class,
                () -> StrataConfig.load(config.toFile()).index(config.toFile()));
        assertTrue(emptyPolygon.getMessage().contains("contains no usable polygons"));
    }

    @Test
    void linearNeighbourScanPreservesDistinctWayAndJunctionCounts() throws Exception {
        Path file = output().resolve("junctions.osm.pbf");
        writeRoadJunctions(file);
        PbfCensus.CensusResult result = new PbfCensus(file.toFile(), circle(), 2).run();
        assertEquals(1, result.node("NATIONAL", "intersections"));
        assertEquals(1, result.node("NATIONAL", "junctions_degree3"));
    }

    @Test
    void correctedVersionLabelsEveryVersionedJavaOutput() throws Exception {
        String version = "conditional-2026-10-09";
        assertEquals(version, FeasibilityAnalysis.MODEL_VERSION);
        AccessProfile wheelchair = profile("wheelchair");
        RoadWay crossing = way(52, "footway", Map.of("footway", "crossing"), 1, 2);
        RoutingGraph graph = new RoutingGraph(wheelchair, "extended", List.of(crossing),
                new long[]{1, 2}, new double[]{0, 0}, new double[]{0, .01},
                new boolean[]{true, true}, Map.of(), Map.of());
        Path dir = output();
        FeasibilityAnalysis analysis = new FeasibilityAnalysis(graph, circle(), 1);
        analysis.run();
        analysis.write(dir);
        analysis.appendSensitivity(dir.resolve("feasibility_sensitivity.csv"));
        new EditTargeting(graph, circle()).run(dir, 1);
        PbfCensus.CensusResult result = new PbfCensus.CensusResult(circle());
        result.addWay(way(1, "residential", Map.of("sidewalk", "yes"), 1, 2),
                1, 0, 0);
        PbfCensus.write(result, dir);

        for (String file : List.of("summary.csv", "spatial_coverage.csv",
                "feasibility.csv", "feasibility_global.csv", "feasibility_sensitivity.csv",
                "edit_targets.csv", "edit_targets_summary.csv",
                "edit_targets_length_baseline.csv", "edit_targets_efficiency.csv")) {
            assertTrue(Files.readString(dir.resolve(file)).contains(version), file);
        }
    }

    private static Map<String, String> columns(String header, String row) {
        String[] names = header.split(",", -1);
        String[] values = row.split(",", -1);
        assertEquals(names.length, values.length);
        Map<String, String> out = new HashMap<>();
        for (int i = 0; i < names.length; i++) {
            out.put(names[i], values[i]);
        }
        return out;
    }

    private static void writeCrossingMembers(Path file) throws IOException {
        List<String> strings = List.of("", "highway", "crossing", "footway", "foot",
                "yes", "access", "private", "tactile_paving");
        Osmformat.StringTable.Builder table = Osmformat.StringTable.newBuilder();
        strings.forEach(s -> table.addS(ByteString.copyFromUtf8(s)));
        Osmformat.PrimitiveGroup.Builder group = Osmformat.PrimitiveGroup.newBuilder();
        for (int id = 1; id <= 3; id++) {
            Osmformat.Node.Builder node = Osmformat.Node.newBuilder()
                    .setId(id).setLat(0).setLon(id * 10_000);
            if (id == 1) {
                node.addKeys(1).addVals(2).addKeys(6).addVals(7);
            } else if (id == 2) {
                node.addKeys(1).addVals(2).addKeys(4).addVals(5);
            }
            group.addNodes(node);
        }
        group.addWays(Osmformat.Way.newBuilder().setId(201)
                .addKeys(1).addVals(3).addKeys(3).addVals(2)
                .addKeys(8).addVals(5).addKeys(6).addVals(7)
                .addRefs(1).addRefs(1));
        group.addWays(Osmformat.Way.newBuilder().setId(202)
                .addKeys(1).addVals(3).addKeys(3).addVals(2)
                .addKeys(4).addVals(5).addRefs(2).addRefs(1));
        writePbf(file, table, group);
    }

    private static void writeDestinations(Path file) throws IOException {
        List<String> strings = List.of("", "amenity", "cafe", "tactile_writing:embossed_letters",
                "yes", "tactile_writing:braille:el", "no", "braille", "tactile_writing");
        Osmformat.StringTable.Builder table = Osmformat.StringTable.newBuilder();
        strings.forEach(s -> table.addS(ByteString.copyFromUtf8(s)));
        Osmformat.PrimitiveGroup.Builder group = Osmformat.PrimitiveGroup.newBuilder();
        for (int id = 1; id <= 4; id++) {
            Osmformat.Node.Builder node = Osmformat.Node.newBuilder()
                    .setId(id).setLat(0).setLon(id * 10_000).addKeys(1).addVals(2);
            switch (id) {
                case 1 -> node.addKeys(3).addVals(4);
                case 2 -> node.addKeys(5).addVals(6);
                case 3 -> node.addKeys(7).addVals(4);
                case 4 -> node.addKeys(8).addVals(7);
                default -> throw new IllegalStateException();
            }
            group.addNodes(node);
        }
        group.addWays(Osmformat.Way.newBuilder().setId(101)
                .addKeys(1).addVals(2).addKeys(3).addVals(4).addRefs(1));
        writePbf(file, table, group);
    }

    private static void writeRoadJunctions(Path file) throws IOException {
        Osmformat.StringTable.Builder table = Osmformat.StringTable.newBuilder();
        for (String value : List.of("", "highway", "residential")) {
            table.addS(ByteString.copyFromUtf8(value));
        }
        Osmformat.PrimitiveGroup.Builder group = Osmformat.PrimitiveGroup.newBuilder();
        for (int id = 1; id <= 4; id++) {
            group.addNodes(Osmformat.Node.newBuilder()
                    .setId(id).setLat(0).setLon(id * 10_000));
        }
        group.addWays(Osmformat.Way.newBuilder().setId(101)
                .addKeys(1).addVals(2).addRefs(1).addRefs(1).addRefs(-1).addRefs(1));
        group.addWays(Osmformat.Way.newBuilder().setId(102)
                .addKeys(1).addVals(2).addRefs(1).addRefs(2));
        group.addWays(Osmformat.Way.newBuilder().setId(103)
                .addKeys(1).addVals(2).addRefs(1).addRefs(3));
        writePbf(file, table, group);
    }

    private static void writePbf(Path file, Osmformat.StringTable.Builder table,
                                 Osmformat.PrimitiveGroup.Builder group) throws IOException {
        try (DataOutputStream out = new DataOutputStream(Files.newOutputStream(file))) {
            block(out, "OSMHeader", Osmformat.HeaderBlock.newBuilder()
                    .addRequiredFeatures("OsmSchema-V0.6").build().toByteString());
            block(out, "OSMData", Osmformat.PrimitiveBlock.newBuilder().setStringtable(table)
                    .addPrimitivegroup(group).build().toByteString());
        }
    }

    private static void block(DataOutputStream out, String type, ByteString data)
            throws IOException {
        Fileformat.Blob blob = Fileformat.Blob.newBuilder().setRaw(data).build();
        Fileformat.BlobHeader header = Fileformat.BlobHeader.newBuilder()
                .setType(type).setDatasize(blob.getSerializedSize()).build();
        out.writeInt(header.getSerializedSize());
        header.writeTo(out);
        blob.writeTo(out);
    }
}
