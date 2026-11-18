package gr.mantzoros.osmaccess;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.Geometry;
import org.locationtech.jts.geom.GeometryFactory;
import org.locationtech.jts.geom.LinearRing;
import org.locationtech.jts.geom.Polygon;

import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/**
 * Minimal GeoJSON FeatureCollection reader producing {@link PolygonStratum}s.
 *
 * <p>Deliberately hand-rolled rather than pulling in GeoTools: the census only needs
 * Polygon/MultiPolygon in WGS84 with two string properties, and GeoTools would add
 * ~40 MB of transitive dependencies to a shaded jar for that.
 */
public final class GeoJsonPolygons {

    private static final GeometryFactory GF = new GeometryFactory();

    /**
     * @param nameProperty  feature property holding the stratum name
     * @param groupProperty feature property holding the reporting group; when null or
     *                      absent on a feature, {@code defaultGroup} is used
     */
    public static List<Stratum> load(File file, String layer, String nameProperty,
                                     String groupProperty, String defaultGroup) throws IOException {
        JsonNode root = new ObjectMapper().readTree(file);
        JsonNode features = root.path("features");
        if (!features.isArray()) {
            throw new IOException("Not a GeoJSON FeatureCollection: " + file);
        }
        List<Stratum> out = new ArrayList<>(features.size());
        int skipped = 0;
        for (JsonNode f : features) {
            JsonNode props = f.path("properties");
            JsonNode geom = f.path("geometry");
            String name = text(props, nameProperty);
            if (name == null) {
                skipped++;
                continue;
            }
            String group = groupProperty == null ? defaultGroup : text(props, groupProperty);
            if (group == null) {
                group = defaultGroup;
            }
            Geometry g = geometry(geom);
            if (g == null || g.isEmpty()) {
                skipped++;
                continue;
            }
            out.add(new PolygonStratum(name, group, layer, g));
        }
        if (skipped > 0) {
            System.err.printf("  %s: skipped %d feature(s) without a usable name/geometry%n",
                    file.getName(), skipped);
        }
        return out;
    }

    private static String text(JsonNode props, String key) {
        if (key == null) {
            return null;
        }
        JsonNode n = props.path(key);
        return n.isMissingNode() || n.isNull() ? null : n.asText();
    }

    private static Geometry geometry(JsonNode geom) {
        String type = geom.path("type").asText("");
        JsonNode coords = geom.path("coordinates");
        return switch (type) {
            case "Polygon" -> polygon(coords);
            case "MultiPolygon" -> {
                List<Polygon> parts = new ArrayList<>();
                for (JsonNode p : coords) {
                    Polygon poly = polygon(p);
                    if (poly != null) {
                        parts.add(poly);
                    }
                }
                yield parts.isEmpty() ? null : GF.createMultiPolygon(parts.toArray(new Polygon[0]));
            }
            default -> null;
        };
    }

    /** rings[0] is the shell; the rest are holes. */
    private static Polygon polygon(JsonNode rings) {
        if (!rings.isArray() || rings.isEmpty()) {
            return null;
        }
        LinearRing shell = ring(rings.get(0));
        if (shell == null) {
            return null;
        }
        List<LinearRing> holes = new ArrayList<>();
        for (int i = 1; i < rings.size(); i++) {
            LinearRing h = ring(rings.get(i));
            if (h != null) {
                holes.add(h);
            }
        }
        return GF.createPolygon(shell, holes.toArray(new LinearRing[0]));
    }

    private static LinearRing ring(JsonNode coords) {
        if (!coords.isArray() || coords.size() < 4) {
            return null;
        }
        List<Coordinate> cs = new ArrayList<>(coords.size());
        for (JsonNode c : coords) {
            if (c.isArray() && c.size() >= 2) {
                cs.add(new Coordinate(c.get(0).asDouble(), c.get(1).asDouble()));
            }
        }
        if (cs.size() < 4) {
            return null;
        }
        // GeoJSON requires closure but not every producer honours it.
        Coordinate first = cs.get(0);
        Coordinate last = cs.get(cs.size() - 1);
        if (!first.equals2D(last)) {
            cs.add(new Coordinate(first));
        }
        return GF.createLinearRing(cs.toArray(new Coordinate[0]));
    }

    private GeoJsonPolygons() {
    }
}
