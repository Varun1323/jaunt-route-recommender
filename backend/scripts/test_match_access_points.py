"""Offline regression tests for match_access_points.py.

All tests use in-memory synthetic data or the archived candidate output file;
no network calls are made.
"""

from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "backend" / "data"
CANDIDATES_PATH = DATA_DIR / "pedestrian_access_point_candidates.json"

# ---------------------------------------------------------------------------
# Import the module under test (functions only — no side effects at import)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))
from match_access_points import (  # noqa: E402
    build_graph,
    find_main_pedestrian_component,
    from_projected,
    get_connected_component,
    get_connected_component_size,
    is_walkable_way,
    project_to_segment,
    to_projected,
)

STUDY_BBOX = {
    "south": 40.749,
    "north": 40.755,
    "west": -73.986,
    "east": -73.972,
}

NINE_LANDMARK_IDS = {
    "grand_central_terminal",
    "stephen_a_schwarzman_building",
    "bryant_park",
    "one_vanderbilt",
    "pershing_square_building",
    "chrysler_building",
    "graybar_building",
    "chanin_building",
    "daily_news_building",
}

ADDITIONAL_LANDMARK_IDS = {
    "times_square",
    "rockefeller_center",
    "st_patricks_cathedral",
    "museum_of_modern_art",
}

ALL_LANDMARK_IDS = NINE_LANDMARK_IDS | ADDITIONAL_LANDMARK_IDS

EXTENDED_STUDY_BBOX = {
    "south": 40.745,
    "north": 40.765,
    "west": -73.990,
    "east": -73.970,
}


# ===========================================================================
# Helper to build a minimal way dict
# ===========================================================================
def _way(tags: dict, nodes: list[int] | None = None, geometry: list[dict] | None = None) -> dict:
    return {
        "type": "way",
        "id": 999,
        "tags": tags,
        "nodes": nodes or [1, 2],
        "geometry": geometry or [{"lat": 40.752, "lon": -73.978}, {"lat": 40.753, "lon": -73.979}],
    }


# ===========================================================================
# 1: is_walkable_way exclusion tests
# ===========================================================================
class TestIsWalkableWayExclusions(unittest.TestCase):

    def test_excludes_tunnel_yes(self):
        """Ways tagged tunnel=yes must be excluded."""
        way = _way({"highway": "footway", "tunnel": "yes", "layer": "-1"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_tunnel_yes_no_layer(self):
        """tunnel=yes without layer tag must still be excluded."""
        way = _way({"highway": "footway", "tunnel": "yes"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_negative_layer(self):
        """Ways with layer=-1 (even without tunnel tag) must be excluded."""
        way = _way({"highway": "footway", "footway": "sidewalk", "layer": "-1"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_layer_minus_3(self):
        """Ways with layer=-3 must be excluded."""
        way = _way({"highway": "footway", "layer": "-3"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_indoor_yes(self):
        """Ways tagged indoor=yes must be excluded."""
        way = _way({"highway": "footway", "indoor": "yes"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_private_access(self):
        """Ways with access=private must be excluded."""
        way = _way({"highway": "footway", "access": "private"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_no_access(self):
        """Ways with access=no must be excluded."""
        way = _way({"highway": "pedestrian", "access": "no"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_customers_access(self):
        """Ways with access=customers must be excluded."""
        way = _way({"highway": "footway", "access": "customers"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_foot_no(self):
        """Ways with foot=no must be excluded."""
        way = _way({"highway": "primary", "foot": "no"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_railway(self):
        """Ways with railway tag must be excluded."""
        way = _way({"railway": "subway", "highway": "footway"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_subway_yes(self):
        """Ways tagged subway=yes must be excluded."""
        way = _way({"highway": "footway", "subway": "yes"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_area_yes(self):
        """Ways tagged area=yes (plazas) must be excluded from linear routing."""
        way = _way({"highway": "pedestrian", "area": "yes"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_service_highway(self):
        """Service ways must be excluded."""
        way = _way({"highway": "service"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_public_transport_platform(self):
        """Platform ways must be excluded."""
        way = _way({"highway": "footway", "public_transport": "platform"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_missing_highway_tag(self):
        """Ways with no highway tag must be excluded."""
        way = _way({"name": "Some Way"})
        self.assertFalse(is_walkable_way(way))

    def test_excludes_cycleway_without_foot(self):
        """highway=cycleway without foot tags must be excluded."""
        way = _way({"highway": "cycleway"})
        self.assertFalse(is_walkable_way(way))


# ===========================================================================
# 2: Road carriageway and sidewalk filtering regression tests
# ===========================================================================
class TestRoadCarriagewaySidewalkFiltering(unittest.TestCase):
    """Regression tests ensuring ordinary roads without integrated sidewalks are excluded."""

    def test_road_without_sidewalk_tag_is_excluded(self):
        """Ordinary primary road with no sidewalk tag must NOT be walkable."""
        way = _way({"highway": "primary", "name": "5th Avenue"})
        self.assertFalse(is_walkable_way(way))

    def test_residential_road_without_sidewalk_tag_is_excluded(self):
        """Residential street with no sidewalk tag must NOT be walkable."""
        way = _way({"highway": "residential", "name": "East 43rd Street"})
        self.assertFalse(is_walkable_way(way))

    def test_road_with_sidewalk_no_is_excluded(self):
        """Primary road with sidewalk=no must be excluded."""
        way = _way({"highway": "primary", "name": "Park Avenue", "sidewalk": "no"})
        self.assertFalse(is_walkable_way(way))

    def test_road_with_sidewalk_none_is_excluded(self):
        """Road with sidewalk=none must be excluded."""
        way = _way({"highway": "primary", "sidewalk": "none"})
        self.assertFalse(is_walkable_way(way))

    def test_road_with_sidewalk_separate_is_excluded(self):
        """Road with sidewalk=separate has separately mapped footways; carriageway itself is not walkable."""
        way = _way({"highway": "residential", "name": "East 43rd Street", "sidewalk": "separate"})
        self.assertFalse(is_walkable_way(way))

    def test_road_with_sidewalk_yes_is_included(self):
        """Road carriageway with integrated sidewalk=yes is permitted."""
        way = _way({"highway": "residential", "name": "Local Street", "sidewalk": "yes"})
        self.assertTrue(is_walkable_way(way))

    def test_road_with_sidewalk_both_is_included(self):
        """Road carriageway with integrated sidewalk=both is permitted."""
        way = _way({"highway": "primary", "name": "Main Street", "sidewalk": "both"})
        self.assertTrue(is_walkable_way(way))

    def test_road_with_sidewalk_left_is_included(self):
        """Road carriageway with integrated sidewalk=left is permitted."""
        way = _way({"highway": "tertiary", "sidewalk": "left"})
        self.assertTrue(is_walkable_way(way))

    def test_road_with_sidewalk_right_is_included(self):
        """Road carriageway with integrated sidewalk=right is permitted."""
        way = _way({"highway": "secondary", "sidewalk": "right"})
        self.assertTrue(is_walkable_way(way))


# ===========================================================================
# 3: is_walkable_way inclusion tests
# ===========================================================================
class TestIsWalkableWayInclusions(unittest.TestCase):

    def test_includes_footway_sidewalk(self):
        """Outdoor footway=sidewalk ways must be included."""
        way = _way({"highway": "footway", "footway": "sidewalk", "surface": "concrete"})
        self.assertTrue(is_walkable_way(way))

    def test_includes_pedestrian(self):
        """highway=pedestrian ways (without area=yes) must be included."""
        way = _way({"highway": "pedestrian", "surface": "asphalt"})
        self.assertTrue(is_walkable_way(way))

    def test_includes_path(self):
        """highway=path ways must be included."""
        way = _way({"highway": "path"})
        self.assertTrue(is_walkable_way(way))

    def test_includes_steps(self):
        """Outdoor steps must be included."""
        way = _way({"highway": "steps"})
        self.assertTrue(is_walkable_way(way))

    def test_includes_layer_zero(self):
        """Ways with layer=0 (explicit) must be included."""
        way = _way({"highway": "footway", "footway": "sidewalk", "layer": "0"})
        self.assertTrue(is_walkable_way(way))

    def test_includes_layer_1(self):
        """Ways with layer=1 (elevated bridge) must still be included."""
        way = _way({"highway": "footway", "layer": "1"})
        self.assertTrue(is_walkable_way(way))

    def test_includes_no_layer_tag(self):
        """Ways with no layer tag default to surface level and must be included."""
        way = _way({"highway": "footway", "footway": "sidewalk"})
        self.assertTrue(is_walkable_way(way))

    def test_does_not_exclude_tunnel_no(self):
        """tunnel=no (explicitly not a tunnel) must not be excluded."""
        way = _way({"highway": "footway", "tunnel": "no"})
        self.assertTrue(is_walkable_way(way))


# ===========================================================================
# 4: project_to_segment geometry tests
# ===========================================================================
class TestProjectToSegment(unittest.TestCase):

    def test_project_midpoint(self):
        """Point directly above segment midpoint projects to that midpoint."""
        px, py, dist = project_to_segment(5.0, 3.0, 0.0, 0.0, 10.0, 0.0)
        self.assertAlmostEqual(px, 5.0, places=6)
        self.assertAlmostEqual(py, 0.0, places=6)
        self.assertAlmostEqual(dist, 3.0, places=6)

    def test_project_beyond_endpoint_clamps(self):
        """Point past the end of segment must clamp to the endpoint."""
        px, py, dist = project_to_segment(10.0, 0.0, 0.0, 0.0, 5.0, 0.0)
        self.assertAlmostEqual(px, 5.0, places=6)
        self.assertAlmostEqual(py, 0.0, places=6)
        self.assertAlmostEqual(dist, 5.0, places=6)

    def test_project_before_start_clamps(self):
        """Point before the start of segment must clamp to the start."""
        px, py, dist = project_to_segment(-3.0, 0.0, 0.0, 0.0, 10.0, 0.0)
        self.assertAlmostEqual(px, 0.0, places=6)
        self.assertAlmostEqual(py, 0.0, places=6)
        self.assertAlmostEqual(dist, 3.0, places=6)

    def test_project_zero_length_segment(self):
        """Zero-length segment returns distance to that point."""
        px, py, dist = project_to_segment(3.0, 4.0, 0.0, 0.0, 0.0, 0.0)
        self.assertAlmostEqual(px, 0.0, places=6)
        self.assertAlmostEqual(py, 0.0, places=6)
        self.assertAlmostEqual(dist, 5.0, places=6)

    def test_project_on_segment(self):
        """Point exactly on the segment must return dist=0."""
        px, py, dist = project_to_segment(3.0, 0.0, 0.0, 0.0, 10.0, 0.0)
        self.assertAlmostEqual(dist, 0.0, places=6)


# ===========================================================================
# 5: round-trip coordinate projection
# ===========================================================================
class TestCoordinateProjection(unittest.TestCase):

    def test_round_trip_lon_lat(self):
        """to_projected then from_projected must recover original lon/lat."""
        lon0, lat0 = -73.978, 40.752
        lon_in, lat_in = -73.980, 40.754
        x, y = to_projected(lon_in, lat_in, lon0, lat0)
        lon_out, lat_out = from_projected(x, y, lon0, lat0)
        self.assertAlmostEqual(lon_out, lon_in, places=8)
        self.assertAlmostEqual(lat_out, lat_in, places=8)


# ===========================================================================
# 6: Graph connectivity and main component identification
# ===========================================================================
class TestGraphConnectivity(unittest.TestCase):

    def _make_way(self, nodes: list[int], tags: dict | None = None) -> dict:
        geom = [{"lat": 40.752 + 0.001 * i, "lon": -73.978} for i in range(len(nodes))]
        t = tags or {"highway": "footway", "footway": "sidewalk"}
        return _way(t, nodes=nodes, geometry=geom)

    def test_connected_two_ways_sharing_node(self):
        """Two walkable ways sharing a node form a connected component."""
        ways = [self._make_way([1, 2, 3]), self._make_way([3, 4, 5])]
        graph = build_graph(ways)
        size = get_connected_component_size(1, graph)
        self.assertEqual(size, 5)

    def test_disconnected_subgraph(self):
        """Two disjoint chains form separate components."""
        ways = [self._make_way([10, 11, 12]), self._make_way([20, 21, 22])]
        graph = build_graph(ways)
        self.assertEqual(get_connected_component_size(10, graph), 3)
        self.assertEqual(get_connected_component_size(20, graph), 3)

    def test_isolated_node_not_in_graph(self):
        """A node that appears in no way returns component size 0."""
        ways = [self._make_way([1, 2, 3])]
        graph = build_graph(ways)
        self.assertEqual(get_connected_component_size(999, graph), 0)

    def test_bidirectional_edges(self):
        """Graph edges must be bidirectional (undirected)."""
        ways = [self._make_way([100, 101])]
        graph = build_graph(ways)
        self.assertIn(101, graph[100])
        self.assertIn(100, graph[101])

    def test_find_main_pedestrian_component_selects_largest(self):
        """find_main_pedestrian_component selects the largest component."""
        # Component A: 60 nodes (would pass naive >= 50 check!)
        comp_a_nodes = list(range(1, 61))
        ways_a = [self._make_way(comp_a_nodes)]

        # Component B: 200 nodes (the true main network)
        comp_b_nodes = list(range(100, 301))
        ways_b = [self._make_way(comp_b_nodes)]

        graph = build_graph(ways_a + ways_b)
        main_comp = find_main_pedestrian_component(graph)

        self.assertEqual(len(main_comp), 201)
        self.assertIn(100, main_comp)
        self.assertNotIn(1, main_comp)

    def test_sub_50_and_isolated_components_rejected_from_main(self):
        """Nodes in smaller components are not in main_component."""
        small_comp_nodes = [10, 11, 12, 13]
        big_comp_nodes = list(range(100, 250))
        graph = build_graph([self._make_way(small_comp_nodes), self._make_way(big_comp_nodes)])
        main_comp = find_main_pedestrian_component(graph)
        self.assertFalse(any(n in main_comp for n in small_comp_nodes))
        self.assertTrue(all(n in main_comp for n in big_comp_nodes))

    def test_empty_graph_returns_empty_component(self):
        """Empty graph returns empty frozenset."""
        self.assertEqual(find_main_pedestrian_component({}), frozenset())

    def test_tunnel_and_indoor_ways_do_not_bridge_components(self):
        """Underground/tunnel/indoor ways filtered out do not connect disjoint surface ways."""
        surface_way_1 = self._make_way([1, 2], {"highway": "footway"})
        tunnel_connector = self._make_way([2, 3], {"highway": "footway", "tunnel": "yes", "layer": "-1"})
        surface_way_2 = self._make_way([3, 4], {"highway": "footway"})

        # Filtering with is_walkable_way excludes tunnel_connector
        walkable = [w for w in [surface_way_1, tunnel_connector, surface_way_2] if is_walkable_way(w)]
        self.assertEqual(len(walkable), 2)

        graph = build_graph(walkable)
        # Node 1 and Node 4 must NOT be in the same component
        comp_1 = get_connected_component(1, graph)
        self.assertNotIn(4, comp_1)
        self.assertEqual(len(comp_1), 2)


# ===========================================================================
# 7: Candidate output file integrity and schema validation tests
# ===========================================================================
class TestCandidateOutputFile(unittest.TestCase):
    """Tests against the archived pedestrian_access_point_candidates.json."""

    @classmethod
    def setUpClass(cls):
        with open(CANDIDATES_PATH, encoding="utf-8") as f:
            cls.data = json.load(f)
        cls.landmarks = {lm["landmark_id"]: lm for lm in cls.data["landmarks"]}

    def test_all_nine_landmarks_present(self):
        """All 9 core landmark IDs plus 4 additional catalog landmarks must appear in output."""
        self.assertEqual(set(self.landmarks.keys()), ALL_LANDMARK_IDS)
        self.assertTrue(NINE_LANDMARK_IDS.issubset(set(self.landmarks.keys())))

    def test_all_nine_have_routing_candidates(self):
        """Every landmark must have a non-null routing_candidate."""
        for lid, lm in self.landmarks.items():
            with self.subTest(landmark=lid):
                self.assertIsNotNone(
                    lm["routing_candidate"],
                    msg=f"{lid} has no routing_candidate",
                )

    def test_main_pedestrian_component_size_recorded(self):
        """main_pedestrian_component_size must be recorded at top-level."""
        self.assertIn("main_pedestrian_component_size", self.data)
        self.assertIsInstance(self.data["main_pedestrian_component_size"], int)
        self.assertGreater(self.data["main_pedestrian_component_size"], 1000)

    def test_all_candidates_connected_to_main_network(self):
        """Every core 9 landmark candidate must report is_connected_to_main_network=True."""
        for lid in NINE_LANDMARK_IDS:
            lm = self.landmarks.get(lid)
            if not lm:
                continue
            rc = lm["routing_candidate"]
            if rc is None:
                continue
            with self.subTest(landmark=lid):
                self.assertTrue(
                    rc["is_connected_to_main_network"],
                    msg=f"{lid} candidate is NOT connected to main network",
                )

    def test_all_candidates_are_unconfirmed(self):
        """Every landmark must be verification_status=candidate_unconfirmed."""
        for lid, lm in self.landmarks.items():
            with self.subTest(landmark=lid):
                self.assertEqual(lm["verification_status"], "candidate_unconfirmed")

    def test_all_candidates_manual_review_required(self):
        """Every landmark must have manual_review_required=True."""
        for lid, lm in self.landmarks.items():
            with self.subTest(landmark=lid):
                self.assertTrue(
                    lm["manual_review_required"],
                    msg=f"{lid} has manual_review_required != True",
                )

    def test_candidate_coordinates_in_study_area(self):
        """All candidate lat/lon must fall within the relevant study bounding box."""
        for lid, lm in self.landmarks.items():
            rc = lm["routing_candidate"]
            if rc is None:
                continue
            lat = rc["candidate_latitude"]
            lon = rc["candidate_longitude"]
            bbox = STUDY_BBOX if lid in NINE_LANDMARK_IDS else EXTENDED_STUDY_BBOX
            with self.subTest(landmark=lid):
                self.assertGreaterEqual(lat, bbox["south"], msg=f"{lid} lat too far south")
                self.assertLessEqual(lat, bbox["north"], msg=f"{lid} lat too far north")
                self.assertGreaterEqual(lon, bbox["west"], msg=f"{lid} lon too far west")
                self.assertLessEqual(lon, bbox["east"], msg=f"{lid} lon too far east")

    def test_physical_evidence_separate_from_routing_coordinates(self):
        """reference_target_coordinates must differ from candidate routing point."""
        for lid, lm in self.landmarks.items():
            rc = lm["routing_candidate"]
            if rc is None:
                continue
            ref = lm["documented_access"]["reference_target_coordinates"]
            cand_lat = rc["candidate_latitude"]
            cand_lon = rc["candidate_longitude"]
            dist = rc.get("projection_distance_m")
            if dist is not None and dist > 0.01:
                with self.subTest(landmark=lid):
                    same_lat = abs(ref["latitude"] - cand_lat) < 1e-9
                    same_lon = abs(ref["longitude"] - cand_lon) < 1e-9
                    self.assertFalse(
                        same_lat and same_lon,
                        msg=(
                            f"{lid} reference_target_coordinates identical to "
                            f"candidate coordinates but projection_distance_m={dist}"
                        ),
                    )

    def test_reference_coordinates_explicitly_unverified(self):
        """reference_target_coordinates must state coordinate_status is unverified."""
        for lid, lm in self.landmarks.items():
            ref = lm["documented_access"]["reference_target_coordinates"]
            expected_status = (
                "research_derived_unverified"
                if lid in NINE_LANDMARK_IDS
                else "nominatim_place_point_unverified"
            )
            with self.subTest(landmark=lid):
                self.assertEqual(
                    ref.get("coordinate_status"),
                    expected_status,
                    msg=f"{lid} coordinate_status must be '{expected_status}'",
                )

    def test_review_notes_do_not_claim_independently_verified_coordinates(self):
        """Review notes must not claim coordinates or physical frontage are verified."""
        forbidden_phrases = [
            "verified physical approach frontage",
            "verified connected sidewalk",
            "verified coordinates",
        ]
        for lid, lm in self.landmarks.items():
            note = lm.get("review_notes", "")
            with self.subTest(landmark=lid):
                for phrase in forbidden_phrases:
                    self.assertNotIn(
                        phrase,
                        note.lower(),
                        msg=f"{lid} review_notes contains forbidden claim: '{phrase}'",
                    )
                self.assertIn("unverified", note.lower(), msg=f"{lid} review_notes must explicitly note unverified status")

    def test_nypl_confidence_is_high_and_consistent(self):
        """NYPL candidate with projection distance 16.54 m (< 20 m) must have confidence=high."""
        lm = self.landmarks.get("stephen_a_schwarzman_building")
        self.assertIsNotNone(lm)
        rc = lm["routing_candidate"]
        self.assertIsNotNone(rc)
        self.assertEqual(rc["confidence"], "high")
        self.assertLess(rc["projection_distance_m"], 20.0)

    def test_nypl_candidate_not_on_tunnel_way(self):
        """NYPL (stephen_a_schwarzman_building) candidate must not be on a tunnel way."""
        lm = self.landmarks.get("stephen_a_schwarzman_building")
        self.assertIsNotNone(lm, "NYPL landmark not found in output")
        rc = lm["routing_candidate"]
        self.assertIsNotNone(rc, "NYPL has no routing_candidate")
        way_tags = rc.get("way_tags", {})
        self.assertNotEqual(
            way_tags.get("tunnel"),
            "yes",
            msg=f"NYPL candidate is on a tunnel way: {way_tags}",
        )

    def test_nypl_candidate_on_surface_sidewalk(self):
        """NYPL candidate should be on a surface sidewalk (footway=sidewalk)."""
        lm = self.landmarks.get("stephen_a_schwarzman_building")
        self.assertIsNotNone(lm)
        rc = lm["routing_candidate"]
        self.assertIsNotNone(rc)
        way_tags = rc.get("way_tags", {})
        self.assertEqual(
            way_tags.get("footway"),
            "sidewalk",
            msg=f"NYPL candidate way_tags={way_tags} — expected footway=sidewalk",
        )

    def test_required_candidate_fields_present(self):
        """Every routing_candidate must contain all required schema fields."""
        required_fields = {
            "osm_way_id",
            "candidate_latitude",
            "candidate_longitude",
            "projection_distance_m",
            "connected_component_size",
            "is_connected_to_main_network",
            "confidence",
            "way_tags",
            "segment_nodes",
        }
        for lid, lm in self.landmarks.items():
            rc = lm["routing_candidate"]
            if rc is None:
                continue
            with self.subTest(landmark=lid):
                missing = required_fields - set(rc.keys())
                self.assertFalse(missing, msg=f"{lid} routing_candidate missing fields: {missing}")

    def test_required_documented_access_fields_present(self):
        """Every documented_access entry must contain required fields."""
        required_fields = {
            "access_type",
            "access_name",
            "street_address",
            "approach_description",
            "reference_target_coordinates",
        }
        for lid, lm in self.landmarks.items():
            da = lm.get("documented_access", {})
            with self.subTest(landmark=lid):
                missing = required_fields - set(da.keys())
                self.assertFalse(
                    missing, msg=f"{lid} documented_access missing fields: {missing}"
                )

    def test_reference_target_coordinates_in_study_area(self):
        """All reference_target_coordinates must be within the study bounding box."""
        for lid, lm in self.landmarks.items():
            ref = lm.get("documented_access", {}).get("reference_target_coordinates", {})
            lat = ref.get("latitude")
            lon = ref.get("longitude")
            if lat is None or lon is None:
                continue
            bbox = STUDY_BBOX if lid in NINE_LANDMARK_IDS else EXTENDED_STUDY_BBOX
            with self.subTest(landmark=lid):
                self.assertGreaterEqual(lat, bbox["south"])
                self.assertLessEqual(lat, bbox["north"])
                self.assertGreaterEqual(lon, bbox["west"])
                self.assertLessEqual(lon, bbox["east"])

    def test_component_sizes_match_main_component(self):
        """All candidates connected to main network must report the main component size."""
        expected_size = self.data["main_pedestrian_component_size"]
        for lid in NINE_LANDMARK_IDS:
            lm = self.landmarks.get(lid)
            if not lm:
                continue
            rc = lm["routing_candidate"]
            if rc is None:
                continue
            with self.subTest(landmark=lid):
                self.assertEqual(rc["connected_component_size"], expected_size)

    def test_projection_distances_non_negative(self):
        """All projection_distance_m values must be non-negative when present."""
        for lid, lm in self.landmarks.items():
            rc = lm["routing_candidate"]
            if rc is None or rc.get("projection_distance_m") is None:
                continue
            with self.subTest(landmark=lid):
                self.assertGreaterEqual(rc["projection_distance_m"], 0.0)


# ===========================================================================
# 8: Regression tests for specific known ways from raw data
# ===========================================================================
class TestKnownWayFiltering(unittest.TestCase):
    """Regression tests for specific OSM ways in the study area."""

    def test_way_388734718_tunnel_is_excluded(self):
        """OSM way 388734718 (tunnel=yes, layer=-1) must be excluded."""
        way = {
            "type": "way",
            "id": 388734718,
            "tags": {
                "highway": "footway",
                "layer": "-1",
                "lit": "yes",
                "smoothness": "good",
                "surface": "concrete",
                "tunnel": "yes",
            },
            "nodes": [1, 2],
            "geometry": [{"lat": 40.7537, "lon": -73.9822}, {"lat": 40.7536, "lon": -73.9821}],
        }
        self.assertFalse(is_walkable_way(way))

    def test_way_1219118698_underground_sidewalk_is_excluded(self):
        """OSM way 1219118698 (footway=sidewalk, layer=-1) must be excluded."""
        way = {
            "type": "way",
            "id": 1219118698,
            "tags": {
                "footway": "sidewalk",
                "highway": "footway",
                "layer": "-1",
                "surface": "concrete",
            },
            "nodes": [1, 2],
            "geometry": [{"lat": 40.752, "lon": -73.978}, {"lat": 40.753, "lon": -73.979}],
        }
        self.assertFalse(is_walkable_way(way))

    def test_way_44165030_park_ave_primary_road_is_excluded(self):
        """OSM way 44165030 (highway=primary, sidewalk=no) must be excluded."""
        way = {
            "type": "way",
            "id": 44165030,
            "tags": {
                "highway": "primary",
                "name": "Park Avenue",
                "sidewalk": "no",
                "surface": "asphalt",
            },
            "nodes": [1, 2],
        }
        self.assertFalse(is_walkable_way(way))

    def test_way_46469727_e_43rd_st_residential_separate_sidewalk_is_excluded(self):
        """OSM way 46469727 (highway=residential, sidewalk=separate) must be excluded."""
        way = {
            "type": "way",
            "id": 46469727,
            "tags": {
                "highway": "residential",
                "name": "East 43rd Street",
                "sidewalk": "separate",
                "surface": "asphalt",
            },
            "nodes": [1, 2],
        }
        self.assertFalse(is_walkable_way(way))


if __name__ == "__main__":
    unittest.main()
