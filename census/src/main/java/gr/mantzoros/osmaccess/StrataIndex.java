package gr.mantzoros.osmaccess;

import org.locationtech.jts.geom.Envelope;
import org.locationtech.jts.index.strtree.STRtree;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Resolves a coordinate to the strata containing it.
 *
 * <p>Called once per way, per access node and per structural intersection, so on a
 * national run this executes several million times against a few hundred polygons.
 * Circles are scanned linearly (there are ~25 and the latitude pre-check rejects
 * almost all of them); polygons go through an STRtree envelope query first.
 */
public final class StrataIndex {

    public static final String NATIONAL = "NATIONAL";

    private final List<CircleStratum> circles = new ArrayList<>();
    private final STRtree tree = new STRtree();
    private final Map<String, Stratum> byName = new HashMap<>();
    private final List<Stratum> all = new ArrayList<>();
    private boolean built;

    public StrataIndex(List<Stratum> strata) {
        for (Stratum s : strata) {
            Stratum clash = byName.putIfAbsent(s.name(), s);
            if (clash != null) {
                System.err.printf("  WARNING: duplicate stratum name '%s' (layers %s and %s) - "
                                + "features will be pooled under one row%n",
                        s.name(), clash.layer(), s.layer());
                continue;
            }
            all.add(s);
            if (s instanceof CircleStratum c) {
                circles.add(c);
            } else if (s instanceof PolygonStratum p) {
                Envelope e = p.envelope();
                tree.insert(e, p);
            }
        }
        tree.build();
        built = true;
    }

    public List<Stratum> all() {
        return all;
    }

    public int polygonCount() {
        return all.size() - circles.size();
    }

    public int circleCount() {
        return circles.size();
    }

    /**
     * Every feature always counts toward NATIONAL, plus each stratum containing it.
     * A feature can land in several layers at once (e.g. NATIONAL + the Athens circle
     * + the Municipality of Athens polygon + its DEGURBA class); layers are reported
     * separately so nothing is double counted within a layer.
     */
    public List<String> strataFor(double lat, double lon) {
        List<String> names = new ArrayList<>(4);
        names.add(NATIONAL);
        Map<String, String> owner = new java.util.TreeMap<>();
        for (CircleStratum c : circles) {
            if (c.contains(lat, lon)) {
                owner.merge(c.layer(), c.name(), (a, b) -> a.compareTo(b) <= 0 ? a : b);
            }
        }
        if (built && polygonCount() > 0) {
            @SuppressWarnings("unchecked")
            List<PolygonStratum> candidates = tree.query(new Envelope(lon, lon, lat, lat));
            for (PolygonStratum p : candidates) {
                if (p.contains(lat, lon)) {
                    owner.merge(p.layer(), p.name(), (a, b) -> a.compareTo(b) <= 0 ? a : b);
                }
            }
        }
        names.addAll(owner.values());
        return names;
    }

    public String groupOf(String name) {
        if (NATIONAL.equals(name)) {
            return "national";
        }
        Stratum s = byName.get(name);
        return s == null ? "?" : s.group();
    }

    public String layerOf(String name) {
        if (NATIONAL.equals(name)) {
            return "national";
        }
        Stratum s = byName.get(name);
        return s == null ? "?" : s.layer();
    }
}
