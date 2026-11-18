package gr.mantzoros.osmaccess;

import java.util.Map;

/** A crossing / traffic-signal / kerb node with its accessibility attributes. */
public record AccessNode(long id, double lat, double lon, Map<String, String> tags,
                         long editedAt, int version) {

    public boolean isCrossing() {
        return "crossing".equals(tags.get("highway"));
    }

    public boolean isSignal() {
        return "traffic_signals".equals(tags.get("highway"));
    }

    /**
     * A kerb node. Both the node form ({@code kerb=lowered} on a barrier node) and
     * the {@code barrier=kerb} form are counted; the value carries the accessibility
     * meaning (see {@link TagInventory#kerbClass(String)}).
     */
    public boolean isKerb() {
        return tags.containsKey("kerb")
                || TagInventory.values(tags.get("barrier")).contains("kerb");
    }

    public String kerbValue() {
        String v = tags.get("kerb");
        return v != null ? v : (isKerb() ? "unspecified" : null);
    }
}
