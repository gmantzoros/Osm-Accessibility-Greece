package gr.mantzoros.osmaccess;

import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * A destination-side accessibility feature: a public-transport stop, or a point of
 * interest carrying accessibility information.
 *
 * <p>The crossing census measures the journey; this measures its ends. Blind-pedestrian
 * navigation in a city is transit-centric — the walk is usually to or from a stop — and
 * a route that arrives at a door the traveller cannot identify has not succeeded. Neither
 * is visible to a way-and-crossing census.
 *
 * <p>OSM distinguishes braille ({@code tactile_writing:braille:<lang>}) from
 * other tactile writing; a statement that braille is absent is still a statement.
 */
public record AccessPoi(long id, double lat, double lon, Kind kind, Map<String, String> tags) {

    public enum Kind {
        /** Bus stop, tram stop, platform, ferry terminal. */
        TRANSIT_STOP,
        /** Railway/metro station or halt — a staffed or built facility, not a roadside pole. */
        STATION,
        /** Amenity, shop or entrance carrying (or lacking) accessibility information. */
        POI
    }

    /** Node/way keys retained for the destination census. */
    public static final Set<String> KEYS = Set.of(
            "highway", "railway", "public_transport", "amenity", "shop", "entrance", "building",
            "tactile_paving", "wheelchair", "wheelchair:description", "braille",
            "shelter", "bench", "departures_board", "passenger_information_display",
            "name", "ref", "lit", "bus", "tram", "train", "subway", "ferry",
            "toilets:wheelchair", "handrail", "level", "indoor", "kerb");

    public static boolean isBrailleKey(String key) {
        return key.equals("braille") || key.equals("tactile_writing:braille")
                || key.startsWith("tactile_writing:braille:");
    }

    public static boolean isTactileWritingKey(String key) {
        return key.equals("tactile_writing") || key.startsWith("tactile_writing:");
    }

    /** Classify an element from its tags, or null when it is not a destination feature. */
    public static Kind classify(Map<String, String> t) {
        String railway = t.get("railway");
        if ("station".equals(railway) || "halt".equals(railway)
                || "station".equals(t.get("public_transport"))) {
            return Kind.STATION;
        }
        if ("bus_stop".equals(t.get("highway"))
                || "tram_stop".equals(railway)
                || "platform".equals(railway)
                || "ferry_terminal".equals(t.get("amenity"))
                || "platform".equals(t.get("public_transport"))
                || "stop_position".equals(t.get("public_transport"))) {
            return Kind.TRANSIT_STOP;
        }
        // A POI counts only when it is a real destination, not any tagged node.
        if (t.containsKey("amenity") || t.containsKey("shop") || t.containsKey("entrance")) {
            return Kind.POI;
        }
        return null;
    }

    public boolean hasBrailleStatement() {
        return tags.keySet().stream().anyMatch(AccessPoi::isBrailleKey)
                || TagInventory.values(tags.get("tactile_writing")).contains("braille");
    }

    public boolean hasBraille() {
        return tags.entrySet().stream().anyMatch(e ->
                isBrailleKey(e.getKey()) && affirmative(e.getValue()))
                || (TagInventory.values(tags.get("tactile_writing")).contains("braille")
                && affirmative(tags.get("tactile_writing")));
    }

    public boolean hasTactileWritingStatement() {
        return tags.keySet().stream().anyMatch(AccessPoi::isTactileWritingKey);
    }

    public boolean hasTactileWriting() {
        return tags.entrySet().stream().anyMatch(e ->
                isTactileWritingKey(e.getKey()) && affirmative(e.getValue()));
    }

    /** yes / limited / no / (null when unstated). */
    public String wheelchair() {
        return norm(tags.get("wheelchair"));
    }

    public boolean hasTactile() {
        return TagInventory.isAffirmative(tags, "tactile_paving");
    }

    private static String norm(String v) {
        return v == null ? null : v.trim().toLowerCase(Locale.ROOT);
    }

    private static boolean affirmative(String value) {
        Set<String> values = TagInventory.values(value);
        return !values.isEmpty() && !TagInventory.hasNegativeEvidence("braille", values);
    }
}
