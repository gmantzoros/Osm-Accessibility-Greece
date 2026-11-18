package gr.mantzoros.osmaccess;

import java.util.Map;

/**
 * A highway way kept for the census: id, class, the tag subset from the frozen
 * inventory, and its node references (coordinates resolved in pass 2).
 *
 * <p>{@code crossingWay} marks a crossing mapped in the sidewalk schema
 * (docs/metrics.md section 1.4). Such ways are excluded from the pedestrian
 * length denominators and instead feed the unified crossing model, so that a
 * city mapped in the modern schema is not counted as having zero crossings.
 */
public record RoadWay(long id, String highwayClass, boolean drivable, boolean pedestrian,
                      boolean separateSidewalk, boolean crossingWay,
                      Map<String, String> tags, long[] nodeRefs,
                      long editedAt, int version) {

    /** Ways that contribute to the length-weighted way metrics. */
    public boolean countsForLength() {
        return (drivable || pedestrian) && !crossingWay;
    }
}
