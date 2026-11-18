package gr.mantzoros.osmaccess;

import org.locationtech.jts.geom.Coordinate;
import org.locationtech.jts.geom.Envelope;
import org.locationtech.jts.geom.Geometry;
import org.locationtech.jts.geom.GeometryFactory;
import org.locationtech.jts.geom.Point;
import org.locationtech.jts.geom.prep.PreparedGeometry;
import org.locationtech.jts.geom.prep.PreparedGeometryFactory;

/**
 * A stratum defined by an administrative (or classification) polygon in WGS84.
 *
 * <p>This is what replaces circles for the reportable spatial frame: a 12 km circle
 * on Syntagma contains open sea and half of Piraeus, which is indefensible as a
 * "Municipality of Athens" number. Geometry is prepared once and reused, because
 * point-in-polygon runs several million times per national census.
 */
public final class PolygonStratum implements Stratum {

    private static final GeometryFactory GF = new GeometryFactory();

    private final String name;
    private final String group;
    private final String layer;
    private final PreparedGeometry prepared;
    private final Envelope envelope;

    public PolygonStratum(String name, String group, String layer, Geometry geometry) {
        this.name = name;
        this.group = group;
        this.layer = layer;
        this.prepared = PreparedGeometryFactory.prepare(geometry);
        this.envelope = geometry.getEnvelopeInternal();
    }

    @Override
    public String name() {
        return name;
    }

    @Override
    public String group() {
        return group;
    }

    @Override
    public String layer() {
        return layer;
    }

    public Envelope envelope() {
        return envelope;
    }

    @Override
    public boolean contains(double lat, double lon) {
        if (!envelope.contains(lon, lat)) {
            return false;
        }
        Point p = GF.createPoint(new Coordinate(lon, lat));
        return prepared.covers(p);
    }
}
