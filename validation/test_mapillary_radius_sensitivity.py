"""Small cached-screening fixtures; never query imagery or run the census."""

import datetime as dt
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))
from mapillary_radius_sensitivity import (RADII_M, boundary_segments, make_row, near_boundary,
                                         screening_counts, verify_baseline)


class PositiveCoverage:
    def at(self, lat, lon):
        return "image_positive"

    def feature_query_known(self, lat, lon):
        return True


class RadiusSensitivityTests(unittest.TestCase):
    def setUp(self):
        self.feature = {"properties": {"name": "fixture"}, "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]],
                            [[.7, .7], [.8, .7], [.8, .8], [.7, .8], [.7, .7]]]}}

    def test_boundary_excludes_outer_edges_and_holes(self):
        segments = boundary_segments(self.feature["geometry"])
        self.assertTrue(near_boundary(.5, .0002, segments, 51))
        self.assertTrue(near_boundary(.75, .7002, segments, 51))
        self.assertFalse(near_boundary(.5, .5, segments, 51))

    def test_common_interior_radii_share_locations_but_not_matches(self):
        osm = [
            {"stratum": "fixture", "kind": "intersection", "lat": ".5", "lon": ".5"},
            {"stratum": "fixture", "kind": "crossing", "lat": ".5", "lon": ".5003"},
            {"stratum": "fixture", "kind": "intersection", "lat": ".5", "lon": ".0002"},
        ]
        detections = [
            {"group": "crossing", "id": "cv1", "lat": ".5", "lon": ".50015",
             "last_seen_at": "2025-01-01"},
        ]
        counts, excluded = screening_counts(
            osm, detections, [self.feature], PositiveCoverage(),
            dt.date(2026, 9, 15), dt.date(2021, 9, 15))
        self.assertEqual(excluded, Counter({"intersection": 1}))
        at_10 = counts["fixture", "common_interior", RADII_M[0]]
        at_25 = counts["fixture", "common_interior", RADII_M[1]]
        at_50 = counts["fixture", "common_interior", RADII_M[2]]
        self.assertEqual([row["intersection_total"] for row in (at_10, at_25, at_50)], [1, 1, 1])
        self.assertEqual([row["cv_positive_flags_all_dates"] for row in (at_10, at_25, at_50)], [0, 1, 0])
        self.assertEqual([row["crossings_codetected_recent"] for row in (at_10, at_25, at_50)], [0, 1, 1])
        self.assertEqual(make_row("TOTAL", "common_interior", 25, at_25)["flag_rate_pct"], 100)

    def test_baseline_disagreement_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "Saved 25 m screening disagrees"):
            verify_baseline(Counter(), {"all_period_counts": {"intersections": 1}})


if __name__ == "__main__":
    unittest.main()
