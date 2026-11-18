package gr.mantzoros.osmaccess;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/**
 * Strata configuration: the named circles plus any number of polygon layers
 * (municipalities, regions, DEGURBA classes). Polygon file paths are resolved
 * relative to the config file, so the repo stays portable.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record StrataConfig(List<CircleStratum> strata, List<PolygonLayer> polygonLayers) {

    /**
     * @param id            layer id used in the {@code layer} output column
     * @param file          GeoJSON FeatureCollection, WGS84
     * @param nameProperty  feature property holding the stratum name
     * @param groupProperty feature property holding the group (optional)
     * @param defaultGroup  group used when {@code groupProperty} is absent
     * @param enabled       set false to keep a layer configured but out of a run
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record PolygonLayer(String id, String file, String nameProperty,
                               String groupProperty, String defaultGroup, Boolean enabled) {

        public boolean isEnabled() {
            return enabled == null || enabled;
        }
    }

    public static StrataConfig load(File file) throws IOException {
        StrataConfig c = new ObjectMapper().readValue(file, StrataConfig.class);
        return new StrataConfig(
                c.strata == null ? List.of() : c.strata,
                c.polygonLayers == null ? List.of() : c.polygonLayers);
    }

    /** Materialises circles + every enabled polygon layer into one index. */
    public StrataIndex index(File configFile) throws IOException {
        List<Stratum> all = new ArrayList<>(strata);
        File baseDir = configFile.getAbsoluteFile().getParentFile();
        for (PolygonLayer layer : polygonLayers) {
            if (!layer.isEnabled()) {
                System.err.printf("  polygon layer '%s' disabled - skipping%n", layer.id());
                continue;
            }
            File f = new File(layer.file());
            if (!f.isAbsolute()) {
                f = new File(baseDir, layer.file());
            }
            if (!f.isFile()) {
                throw new IOException("Requested polygon layer '" + layer.id()
                        + "' not found: " + f + " (run data/prepare_boundaries.py)");
            }
            List<Stratum> loaded = GeoJsonPolygons.load(
                    f, layer.id(), layer.nameProperty(), layer.groupProperty(),
                    layer.defaultGroup() == null ? layer.id() : layer.defaultGroup());
            if (loaded.isEmpty()) {
                throw new IOException("Requested polygon layer '" + layer.id()
                        + "' contains no usable polygons: " + f);
            }
            System.err.printf("  polygon layer '%s': %d polygons from %s%n",
                    layer.id(), loaded.size(), f.getName());
            all.addAll(loaded);
        }
        return new StrataIndex(all);
    }
}
