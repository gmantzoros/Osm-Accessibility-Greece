package gr.mantzoros.osmaccess;

public final class Geo {

    private static final double EARTH_RADIUS_M = 6_371_000.0;

    public static double haversineMeters(double lat1, double lon1, double lat2, double lon2) {
        double dLat = Math.toRadians(lat2 - lat1);
        double dLon = Math.toRadians(lon2 - lon1);
        double h = Math.sin(dLat / 2) * Math.sin(dLat / 2)
                + Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2))
                * Math.sin(dLon / 2) * Math.sin(dLon / 2);
        return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(h));
    }

    /**
     * Normalises an OSM PBF timestamp to epoch <em>seconds</em>.
     *
     * <p>The PBF spec stores {@code Info.timestamp} in units of {@code date_granularity}
     * milliseconds (default 1000). parallelpbf applies that granularity for dense nodes
     * but hands back the raw value for ways, so nodes arrive in milliseconds and ways in
     * seconds. Rather than depend on that asymmetry, normalise by magnitude: any value
     * past 1e11 cannot be a plausible epoch-second date (year 5138) and is milliseconds.
     */
    public static long toEpochSeconds(long raw) {
        if (raw <= 0) {
            return 0;
        }
        return raw > 100_000_000_000L ? raw / 1000L : raw;
    }

    private Geo() {
    }
}
