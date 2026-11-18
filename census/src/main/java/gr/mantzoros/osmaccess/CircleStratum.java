package gr.mantzoros.osmaccess;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

/**
 * A centre + radius stratum. Retained because the published cross-street detector
 * study sampled the same way (city centre + radius), so census numbers and detector
 * benchmark numbers describe the same footprint. Administrative reporting uses
 * {@link PolygonStratum} instead.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record CircleStratum(String name, String group, double centerLat, double centerLon,
                            double radiusKm) implements Stratum {

    @Override
    public String layer() {
        return "circle";
    }

    @Override
    public boolean contains(double lat, double lon) {
        // Cheap rejection first: 1 degree of latitude is ~111.32 km everywhere.
        double dLat = Math.abs(lat - centerLat) * 111.32;
        if (dLat > radiusKm) {
            return false;
        }
        return Geo.haversineMeters(centerLat, centerLon, lat, lon) <= radiusKm * 1000.0;
    }
}
