package gr.mantzoros.osmaccess;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * One connected group of mapped crossing elements, not a verified physical crossing.
 *
 * <p>OpenStreetMap represents a pedestrian crossing in two ways. The classic form
 * is a node on the carriageway tagged {@code highway=crossing}. The sidewalk-schema
 * form is a way tagged {@code highway=footway} + {@code footway=crossing} linking two
 * sidewalks, usually - but not always - passing through such a node. Accessibility
 * attributes ({@code tactile_paving}, {@code crossing:markings}, {@code kerb}) may sit
 * on either element.
 *
 * <p>A CrossingFeature is one connected component of {crossing nodes, crossing ways}
 * and carries the union of their tags, so that attribute depth is measured per
 * physical crossing rather than per OSM element. The {@code fromNode}/{@code fromWay}
 * flags record which schemas contributed, which is what makes the schema-split
 * result (docs/metrics.md section 3.2) computable.
 */
public final class CrossingFeature {

    record Segment(double latA, double lonA, double latB, double lonB) { }

    private static final Set<String> MEMBER_ACCESS_KEYS = Set.of(
            "foot", "access", "foot:conditional", "access:conditional");
    private final Map<String, String> tags = new HashMap<>(8);
    private final List<Map<String, String>> memberAccess = new ArrayList<>();
    private final List<double[]> memberPositions = new ArrayList<>();
    private final List<Segment> memberSegments = new ArrayList<>();
    private double latSum;
    private double lonSum;
    private int positions;
    boolean fromNode;
    boolean fromWay;
    int nodeMembers;
    int wayMembers;
    final java.util.Set<Long> nodeIds = new java.util.TreeSet<>();
    final java.util.Set<Long> wayIds = new java.util.TreeSet<>();
    final java.util.Set<String> conflictingKeys = new java.util.TreeSet<>();
    /** Kerb values contributed by kerb nodes lying on a member crossing way. */
    String kerbFromMember;
    /** Newest last-edit time across the feature's members, epoch seconds. */
    long editedAt;
    int version;

    void noteEdit(long ts, int ver) {
        if (ts > editedAt) {
            editedAt = ts;
        }
        if (ver > version) {
            version = ver;
        }
    }

    void merge(Map<String, String> other) {
        Map<String, String> local = new HashMap<>();
        for (String key : MEMBER_ACCESS_KEYS) {
            if (other.containsKey(key)) {
                local.put(key, other.get(key));
            }
        }
        if (!local.isEmpty()) {
            memberAccess.add(local);
        }
        for (Map.Entry<String, String> e : other.entrySet()) {
            String old = tags.get(e.getKey());
            if (old != null && !java.util.Set.of("highway", "name", "footway", "cycleway").contains(e.getKey())
                    && !TagInventory.values(old).equals(TagInventory.values(e.getValue()))) {
                conflictingKeys.add(e.getKey());
            }
            tags.merge(e.getKey(), e.getValue(), TagInventory::mergeValues);
        }
    }

    void addPosition(double lat, double lon) {
        latSum += lat;
        lonSum += lon;
        positions++;
        addMemberPosition(lat, lon);
    }

    void addMemberPosition(double lat, double lon) {
        memberPositions.add(new double[]{lat, lon});
    }

    void addMemberSegment(double latA, double lonA, double latB, double lonB) {
        memberSegments.add(new Segment(latA, lonA, latB, lonB));
    }

    List<double[]> memberPositions() {
        return memberPositions;
    }

    List<Segment> memberSegments() {
        return memberSegments;
    }

    public boolean hasPosition() {
        return positions > 0;
    }

    public double lat() {
        return latSum / positions;
    }

    public double lon() {
        return lonSum / positions;
    }

    public Map<String, String> tags() {
        return tags;
    }

    /** node-only / way-only / both - the schema-migration state of this crossing. */
    public String schema() {
        if (fromNode && fromWay) {
            return "both";
        }
        return fromNode ? "node_only" : "way_only";
    }

    /**
     * Worst direct or inherited kerb evidence; a feature tag cannot hide a raised member.
     */
    public String effectiveKerb() {
        return TagInventory.worstKerb(tags.get("kerb"), kerbFromMember);
    }

    /** Evidence shared by census, local feature audit, and conditional routing. */
    public Map<String, String> effectiveTags() {
        Map<String, String> out = new HashMap<>(tags);
        String kerb = effectiveKerb();
        if (kerb != null) {
            out.put("kerb", kerb);
        }
        return out;
    }

    /** Share crossing evidence, but never another object's access permission or barrier. */
    Map<String, String> routingEvidence() {
        Map<String, String> out = effectiveTags();
        out.keySet().removeAll(MEMBER_ACCESS_KEYS);
        out.remove("barrier");
        return out;
    }

    EdgeState evaluateCrossing(AccessProfile profile) {
        Map<String, String> shared = routingEvidence();
        if (memberAccess.isEmpty()) {
            return profile.evaluateCrossingTags(shared);
        }
        EdgeState state = EdgeState.PASS;
        for (Map<String, String> local : memberAccess) {
            Map<String, String> tags = new HashMap<>(shared);
            tags.putAll(local);
            state = state.and(profile.evaluateCrossingTags(tags));
        }
        return state;
    }
}
