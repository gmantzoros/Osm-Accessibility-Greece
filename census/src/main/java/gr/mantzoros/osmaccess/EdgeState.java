package gr.mantzoros.osmaccess;

/**
 * Three-valued edge passability under a routing profile.
 *
 * <p>The third value is the point of the whole exercise. A boolean router forced to
 * decide on silent data invents an answer: treat unknown as impassable and you measure
 * mapping effort, treat it as passable and you measure nothing at all. Keeping UNKNOWN
 * distinct lets the analysis report what the map can actually decide
 * (docs/routing-feasibility.md).
 */
public enum EdgeState {

    /** Excluded by an explicit profile rule. */
    BLOCK,
    /** Unresolved evidence or a declared unmodelled-class/access policy. */
    UNKNOWN,
    /** Admitted by the profile, including its primary and secondary defaults. */
    PASS;

    /** Worst-wins combination: BLOCK &lt; UNKNOWN &lt; PASS. */
    public EdgeState and(EdgeState other) {
        return this.ordinal() <= other.ordinal() ? this : other;
    }

    /** Included in the pessimistic closure (unknown treated as impassable). */
    public boolean inPessimistic() {
        return this == PASS;
    }

    /** Included in the optimistic closure (unknown treated as passable). */
    public boolean inOptimistic() {
        return this != BLOCK;
    }
}
