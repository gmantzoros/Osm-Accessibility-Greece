package gr.mantzoros.osmaccess;

/**
 * A reporting unit. Two implementations: {@link CircleStratum} (centre + radius,
 * kept for continuity with the published detector study's city-centre sampling)
 * and {@link PolygonStratum} (an administrative boundary or DEGURBA class).
 */
public interface Stratum {

    String name();

    /** Reporting group, e.g. metro / large_city / rural, or a DEGURBA class. */
    String group();

    /** Layer this stratum belongs to: {@code circle}, or the polygon layer's id. */
    String layer();

    boolean contains(double lat, double lon);
}
