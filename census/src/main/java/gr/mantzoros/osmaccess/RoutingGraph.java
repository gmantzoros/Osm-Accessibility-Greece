package gr.mantzoros.osmaccess;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Shared conditional graph for feasibility and intervention scenarios. Restricted nodes
 * have separate incident ports joined by zero-length transitions, so approaches remain
 * represented without permitting travel through a blocked/unknown transition.
 */
final class RoutingGraph {
    record Way(RoadWay source, EdgeState state, boolean eligible, int[] nodes, int[] ports,
               double km, double lat, double lon) { }

    interface EdgeVisitor {
        void edge(int a, int b, int originalA, int originalB, double km);
    }

    final AccessProfile profile;
    final String scope;
    final List<Way> ways = new ArrayList<>();
    final double[] lats;
    final double[] lons;
    final Map<Integer, EdgeState> transitions = new HashMap<>();
    final Map<Integer, Integer> portNode = new HashMap<>();
    int vertexCount;

    RoutingGraph(AccessProfile profile, String scope, List<RoadWay> input,
                 long[] ids, double[] lats, double[] lons, boolean[] resolved,
                 Map<Long, Map<String, String>> nodeTags,
                 Map<Long, Map<String, String>> crossingTags) {
        if (!scope.equals("core") && !scope.equals("extended")) {
            throw new IllegalArgumentException("graph scope must be core or extended");
        }
        this.profile = profile;
        this.scope = scope;
        this.lats = lats;
        this.lons = lons;
        vertexCount = ids.length;
        nodeTags.forEach((id, tags) -> {
            int index = Arrays.binarySearch(ids, id);
            if (index >= 0 && resolved[index]) {
                EdgeState state = profile.evaluateNode(tags);
                if (state != EdgeState.PASS) {
                    transitions.put(index, state);
                }
            }
        });
        List<RoadWay> sorted = new ArrayList<>(input);
        sorted.sort(Comparator.comparingLong(RoadWay::id));
        for (RoadWay raw : sorted) {
            boolean eligible = raw.crossingWay() || !TagInventory.isGraphOnly(raw.highwayClass());
            if (!profile.inGraph(raw) || (scope.equals("core") && !eligible)) {
                continue;
            }
            RoadWay source = raw;
            if (crossingTags.containsKey(raw.id())) {
                Map<String, String> tags = new HashMap<>(raw.tags());
                crossingTags.get(raw.id()).forEach((key, value) ->
                        tags.merge(key, value, TagInventory::mergeValues));
                String kerb = crossingTags.get(raw.id()).get("kerb");
                if (kerb != null) {
                    tags.put("kerb", TagInventory.worstKerb(raw.tags().get("kerb"), kerb));
                }
                source = new RoadWay(raw.id(), raw.highwayClass(), raw.drivable(), raw.pedestrian(),
                        raw.separateSidewalk(), raw.crossingWay(), tags, raw.nodeRefs(),
                        raw.editedAt(), raw.version());
            }
            int[] nodes = new int[source.nodeRefs().length];
            boolean restricted = false;
            double km = 0;
            int mid = -1;
            for (int i = 0; i < nodes.length; i++) {
                int idx = Arrays.binarySearch(ids, source.nodeRefs()[i]);
                nodes[i] = idx >= 0 && resolved[idx] ? idx : -1;
                restricted |= transitions.containsKey(nodes[i]);
                if (nodes[i] >= 0 && (mid < 0 || Math.abs(i - nodes.length / 2)
                        < Math.abs(mid - nodes.length / 2))) {
                    mid = i;
                }
                if (i > 0 && nodes[i - 1] >= 0 && nodes[i] >= 0) {
                    km += distance(nodes[i - 1], nodes[i]);
                }
            }
            if (mid < 0 || km <= 0) {
                continue;
            }
            int[] ports = restricted ? new int[Math.max(0, 2 * (nodes.length - 1))] : null;
            if (ports != null) {
                Arrays.fill(ports, -1);
                for (int i = 1; i < nodes.length; i++) {
                    if (nodes[i - 1] < 0 || nodes[i] < 0) {
                        continue;
                    }
                    for (int end = 0; end < 2; end++) {
                        int node = nodes[i - 1 + end];
                        if (transitions.containsKey(node)) {
                            int port = vertexCount++;
                            ports[2 * (i - 1) + end] = port;
                            portNode.put(port, node);
                        }
                    }
                }
            }
            ways.add(new Way(source, profile.evaluate(source), eligible, nodes, ports, km,
                    lats[nodes[mid]], lons[nodes[mid]]));
        }
        transitions.keySet().retainAll(new java.util.HashSet<>(portNode.values()));
    }

    private double distance(int a, int b) {
        return Geo.haversineMeters(lats[a], lons[a], lats[b], lons[b]) / 1000.0;
    }

    void edges(Way way, EdgeVisitor visitor) {
        for (int i = 1; i < way.nodes().length; i++) {
            int a = way.nodes()[i - 1];
            int b = way.nodes()[i];
            if (a < 0 || b < 0) {
                continue;
            }
            int pa = way.ports() == null ? -1 : way.ports()[2 * (i - 1)];
            int pb = way.ports() == null ? -1 : way.ports()[2 * (i - 1) + 1];
            visitor.edge(pa < 0 ? a : pa, pb < 0 ? b : pb, a, b, distance(a, b));
        }
    }

    Closure closure(boolean optimistic, double threshold) {
        Closure closure = new Closure(optimistic, threshold);
        for (Way way : ways) {
            if (way.state() == EdgeState.PASS
                    || (optimistic && way.state() == EdgeState.UNKNOWN)) {
                closure.add(way);
            }
        }
        return closure;
    }

    final class Closure {
        final int[] parent = new int[vertexCount];
        final byte[] rank = new byte[vertexCount];
        final double[] mass = new double[vertexCount];
        final double[] eligibleMass = new double[vertexCount];
        final boolean optimistic;
        final double threshold;
        double usable;
        double eligibleUsable;
        final java.util.Set<Long> added = new java.util.HashSet<>();

        Closure(boolean optimistic, double threshold) {
            this.optimistic = optimistic;
            this.threshold = threshold;
            for (int i = 0; i < parent.length; i++) {
                parent[i] = i;
            }
        }

        int find(int node) {
            while (parent[node] != node) {
                parent[node] = parent[parent[node]];
                node = parent[node];
            }
            return node;
        }

        private double credited(double km) {
            return km >= threshold ? km : 0;
        }

        private void union(int a, int b) {
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
            usable -= credited(mass[a]) + credited(mass[b]);
            eligibleUsable -= credited(eligibleMass[a]) + credited(eligibleMass[b]);
            parent[b] = a;
            if (rank[a] == rank[b]) {
                rank[a]++;
            }
            mass[a] += mass[b];
            eligibleMass[a] += eligibleMass[b];
            mass[b] = 0;
            eligibleMass[b] = 0;
            usable += credited(mass[a]);
            eligibleUsable += credited(eligibleMass[a]);
        }

        private void transition(int port, int original) {
            if (port == original) {
                return;
            }
            EdgeState state = transitions.getOrDefault(original, EdgeState.PASS);
            if (state == EdgeState.PASS || (optimistic && state == EdgeState.UNKNOWN)) {
                union(port, original);
            }
        }

        /** Adds each valid consecutive edge once, including all interior attachment points. */
        double add(Way way) {
            if (!added.add(way.source().id())) {
                return 0;
            }
            double before = eligibleUsable;
            edges(way, (a, b, oa, ob, km) -> {
                transition(a, oa);
                transition(b, ob);
                union(a, b);
                int root = find(a);
                usable -= credited(mass[root]);
                eligibleUsable -= credited(eligibleMass[root]);
                mass[root] += km;
                if (way.eligible()) {
                    eligibleMass[root] += km;
                }
                usable += credited(mass[root]);
                eligibleUsable += credited(eligibleMass[root]);
            });
            return eligibleUsable - before;
        }

        double usableLength(Way way) {
            if (!way.eligible() || !added.contains(way.source().id())) {
                return 0;
            }
            double[] km = {0};
            edges(way, (a, b, oa, ob, length) -> {
                if (eligibleMass[find(a)] >= threshold) {
                    km[0] += length;
                }
            });
            return km[0];
        }

        /** Static ranking diagnostic, not marginal usable gain or a global objective. */
        double consolidationScore(Way way) {
            java.util.Set<Integer> roots = new java.util.HashSet<>();
            edges(way, (a, b, oa, ob, km) -> {
                int ra = find(a);
                int rb = find(b);
                if (eligibleMass[ra] > 0) roots.add(ra);
                if (eligibleMass[rb] > 0) roots.add(rb);
            });
            double sum = way.km();
            double largest = 0;
            for (int root : roots) {
                sum += eligibleMass[root];
                largest = Math.max(largest, eligibleMass[root]);
            }
            return sum - largest;
        }
    }
}
