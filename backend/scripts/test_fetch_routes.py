"""Offline regression and unit tests for fetch_routes.py.

All tests are completely offline (mocking network responses or using archived data).
No live API or network requests are executed during testing.
"""

from __future__ import annotations

import io
import json
import math
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "backend" / "data"
ROUTE_DIR = DATA_DIR / "routes"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_routes import (  # noqa: E402
    COORDINATE_LIMITATION_DISCLOSURE,
    MAX_ALTERNATIVES_PER_JOURNEY,
    MAX_DETOUR_RATIO,
    LEGACY_LANDMARK_IDS,
    SELECTED_LANDMARK_IDS,
    RouteFetchError,
    build_route_definitions,
    create_geojson_feature_collection,
    decode_polyline,
    extract_native_alternates_from_response,
    fetch_all_routes,
    get_candidate_location,
    haversine_distance_m,
    load_landmark_candidates,
    request_json,
    request_native_alternates,
    request_route,
    route_detour_is_excessive,
    routes_are_meaningfully_distinct,
    select_alternative_candidates,
    validate_endpoints,
    validate_cached_route,
    validate_route_response,
    generate_all_journeys,
    waypoint_route_id,
)

SAMPLE_ENCODED_POLYLINE = "y~hvlAj`fblCqCxIq@tBu@~ByNfd@_@jAq@vBcHbUkI`Xf@ZlClB_BtEuAdEaXdz@cJrYyAnFkB|G`BfAhA|@]hAyb@bsAo_@hkAf@ZJHPJ~@l@tChBXh@RU|EePRSjCfBb@`@XRfAiD"


class TestCandidateLoading(unittest.TestCase):
    """Test candidate coordinates loading and validation."""

    def test_load_landmark_candidates_success(self):
        """All original and newly selected candidate points remain available."""
        candidates = load_landmark_candidates()
        self.assertEqual(len(candidates), 13)
        self.assertTrue(LEGACY_LANDMARK_IDS.issubset(candidates))
        self.assertTrue(SELECTED_LANDMARK_IDS.issubset(candidates))
        self.assertIn("grand_central_terminal", candidates)
        self.assertIn("bryant_park", candidates)
        self.assertIn("one_vanderbilt", candidates)

        for lid, cand in candidates.items():
            with self.subTest(landmark=lid):
                self.assertTrue(math.isfinite(cand["lat"]))
                self.assertTrue(math.isfinite(cand["lon"]))
                self.assertEqual(cand["verification_status"], "candidate_unconfirmed")
                self.assertTrue(cand["is_prototype_unverified"])

    def test_load_landmark_candidates_file_not_found(self):
        """Missing candidate file raises RouteFetchError."""
        with self.assertRaises(RouteFetchError) as ctx:
            load_landmark_candidates(Path("non_existent_file.json"))
        self.assertIn("not found", str(ctx.exception))

    def test_get_candidate_location_success(self):
        """Valid landmark ID returns candidate dictionary."""
        candidates = load_landmark_candidates()
        loc = get_candidate_location(candidates, "grand_central_terminal", "origin")
        self.assertEqual(loc["landmark_id"], "grand_central_terminal")
        self.assertAlmostEqual(loc["lat"], 40.752125, places=5)
        self.assertAlmostEqual(loc["lon"], -73.977366, places=5)

    def test_get_candidate_location_unknown_id_rejected(self):
        """Unknown landmark ID raises RouteFetchError with available IDs listed."""
        candidates = load_landmark_candidates()
        with self.assertRaises(RouteFetchError) as ctx:
            get_candidate_location(candidates, "empire_state_building", "origin")
        self.assertIn("Unknown origin landmark ID 'empire_state_building'", str(ctx.exception))
        self.assertIn("grand_central_terminal", str(ctx.exception))


class TestEndpointAndRouteDefinitions(unittest.TestCase):
    """Test arbitrary endpoint validation and route alternative generation."""

    @classmethod
    def setUpClass(cls):
        cls.candidates = load_landmark_candidates()

    def test_same_origin_and_destination_rejected(self):
        """Identical origin and destination must be strictly rejected."""
        with self.assertRaises(RouteFetchError) as ctx:
            validate_endpoints("grand_central_terminal", "grand_central_terminal", self.candidates)
        self.assertIn("circular route requires", str(ctx.exception))

    def test_unknown_origin_rejected(self):
        """Unknown origin ID is rejected."""
        with self.assertRaises(RouteFetchError) as ctx:
            validate_endpoints("unknown_place", "bryant_park", self.candidates)
        self.assertIn("Unknown origin", str(ctx.exception))

    def test_unknown_destination_rejected(self):
        """Unknown destination ID is rejected."""
        with self.assertRaises(RouteFetchError) as ctx:
            validate_endpoints("grand_central_terminal", "unknown_place", self.candidates)
        self.assertIn("Unknown destination", str(ctx.exception))

    def test_build_route_definitions_core_trip(self):
        """Grand Central -> Bryant Park produces direct, via_vanderbilt, and architecture routes."""
        defs = build_route_definitions("grand_central_terminal", "bryant_park", self.candidates)
        self.assertIn("direct", defs)
        self.assertIn("via_vanderbilt", defs)
        self.assertIn("architecture", defs)

        # Direct has 2 locations [origin, destination]
        self.assertEqual(len(defs["direct"]["locations"]), 2)
        self.assertEqual(len(defs["direct"]["waypoints"]), 0)

        # via_vanderbilt has 3 locations and One Vanderbilt as waypoint
        self.assertEqual(len(defs["via_vanderbilt"]["locations"]), 3)
        self.assertEqual(len(defs["via_vanderbilt"]["waypoints"]), 1)
        self.assertEqual(defs["via_vanderbilt"]["waypoints"][0]["landmark_id"], "one_vanderbilt")

        # architecture has 4 locations and Pershing + Chrysler as waypoints
        self.assertEqual(len(defs["architecture"]["locations"]), 4)
        self.assertEqual(len(defs["architecture"]["waypoints"]), 2)
        wp_ids = [wp["landmark_id"] for wp in defs["architecture"]["waypoints"]]
        self.assertEqual(wp_ids, ["pershing_square_building", "chrysler_building"])

    def test_build_route_definitions_arbitrary_pair(self):
        """Arbitrary pair produces 2-3 genuine route alternatives with distinct waypoints."""
        defs = build_route_definitions("chrysler_building", "stephen_a_schwarzman_building", self.candidates)
        self.assertGreaterEqual(len(defs), 2)
        self.assertIn("direct", defs)

        # Check all waypoints in alternatives are distinct from endpoints
        for r_id, r_def in defs.items():
            for wp in r_def["waypoints"]:
                self.assertNotEqual(wp["landmark_id"], "chrysler_building")
                self.assertNotEqual(wp["landmark_id"], "stephen_a_schwarzman_building")

    def test_all_72_journeys_have_bounded_distinct_alternative_definitions(self):
        """Every directed journey gets a direct route plus at most two unique alternatives."""
        ids = sorted(LEGACY_LANDMARK_IDS)
        alternative_counts = {0: 0, 1: 0, 2: 0}
        for origin_id in ids:
            for destination_id in ids:
                if origin_id == destination_id:
                    continue
                with self.subTest(origin=origin_id, destination=destination_id):
                    definitions = build_route_definitions(origin_id, destination_id, self.candidates)
                    self.assertIn("direct", definitions)
                    alternative_ids = [rid for rid in definitions if rid != "direct"]
                    self.assertLessEqual(len(alternative_ids), MAX_ALTERNATIVES_PER_JOURNEY)
                    self.assertEqual(len(alternative_ids), len(set(alternative_ids)))
                    alternative_counts[len(alternative_ids)] += 1

                    waypoint_sets: set[tuple[str, ...]] = set()
                    for route_id in alternative_ids:
                        route = definitions[route_id]
                        self.assertGreaterEqual(len(route["locations"]), 3)
                        self.assertTrue(route["waypoints"])
                        waypoint_ids = tuple(wp["landmark_id"] for wp in route["waypoints"])
                        self.assertNotIn(waypoint_ids, waypoint_sets)
                        waypoint_sets.add(waypoint_ids)
                        for waypoint in route["waypoints"]:
                            self.assertNotIn(waypoint["landmark_id"], {origin_id, destination_id})

        self.assertEqual(sum(alternative_counts.values()), 72)
        self.assertLessEqual(
            sum(count * alternatives for alternatives, count in alternative_counts.items()),
            72 * MAX_ALTERNATIVES_PER_JOURNEY,
        )

    def test_candidate_routes_respect_the_alternative_cap(self):
        """Geographic candidates may produce fewer alternatives, never more than the cap."""
        for origin_id, destination_id in (
            ("chrysler_building", "pershing_square_building"),
            ("graybar_building", "one_vanderbilt"),
            ("grand_central_terminal", "graybar_building"),
        ):
            with self.subTest(origin=origin_id, destination=destination_id):
                definitions = build_route_definitions(
                    origin_id, destination_id, self.candidates
                )
                self.assertIn("direct", definitions)
                self.assertLessEqual(
                    len(definitions) - 1, MAX_ALTERNATIVES_PER_JOURNEY
                )

    def test_short_journey_does_not_force_an_alternative(self):
        """A 27 m adjacent pair has no meaningful intermediate detour."""
        definitions = build_route_definitions(
            "grand_central_terminal", "pershing_square_building", self.candidates
        )
        self.assertEqual(sorted(definitions), ["direct"])

    def test_selected_candidates_respect_detour_and_offset_policy(self):
        """Selected intermediates stay within the detour ratio and off the corridor."""
        origin_id = "bryant_park"
        destination_id = "daily_news_building"
        origin = self.candidates[origin_id]
        destination = self.candidates[destination_id]
        base = haversine_distance_m(origin["lat"], origin["lon"], destination["lat"], destination["lon"])
        selected = select_alternative_candidates(
            origin_id, destination_id, self.candidates
        )
        self.assertGreaterEqual(len(selected), 1)
        for candidate in selected:
            via = (
                haversine_distance_m(origin["lat"], origin["lon"], candidate["lat"], candidate["lon"])
                + haversine_distance_m(candidate["lat"], candidate["lon"], destination["lat"], destination["lon"])
            )
            self.assertLessEqual(via / base, MAX_DETOUR_RATIO)

    def test_build_route_definitions_custom_via(self):
        """Custom via landmarks use a stable ID bound to their ordered sequence."""
        defs = build_route_definitions(
            "grand_central_terminal",
            "bryant_park",
            self.candidates,
            via_ids=["daily_news_building", "chanin_building"],
        )
        self.assertIn("direct", defs)
        route_id = waypoint_route_id(
            "grand_central_terminal",
            "bryant_park",
            ["daily_news_building", "chanin_building"],
        )
        self.assertIn(route_id, defs)
        wp_ids = [wp["landmark_id"] for wp in defs[route_id]["waypoints"]]
        self.assertEqual(wp_ids, ["daily_news_building", "chanin_building"])

    def test_build_loop_definition_requires_and_preserves_intermediate_waypoints(self):
        route_id = waypoint_route_id(
            "grand_central_terminal", "grand_central_terminal", ["bryant_park"]
        )
        definitions = build_route_definitions(
            "grand_central_terminal",
            "grand_central_terminal",
            self.candidates,
            via_ids=["bryant_park"],
        )
        route = definitions[route_id]
        self.assertEqual(
            [location for location in route["locations"]],
            [
                {"lat": self.candidates["grand_central_terminal"]["lat"], "lon": self.candidates["grand_central_terminal"]["lon"]},
                {"lat": self.candidates["bryant_park"]["lat"], "lon": self.candidates["bryant_park"]["lon"]},
                {"lat": self.candidates["grand_central_terminal"]["lat"], "lon": self.candidates["grand_central_terminal"]["lon"]},
            ],
        )
        with self.assertRaisesRegex(RouteFetchError, "circular route requires"):
            build_route_definitions(
                "grand_central_terminal",
                "grand_central_terminal",
                self.candidates,
            )

    def test_duplicate_custom_waypoints_are_rejected(self):
        with self.assertRaisesRegex(RouteFetchError, "cannot repeat"):
            build_route_definitions(
                "grand_central_terminal",
                "bryant_park",
                self.candidates,
                via_ids=["times_square", "times_square"],
            )

    def test_via_waypoint_matching_origin_rejected(self):
        """Intermediate waypoint identical to origin must be rejected."""
        with self.assertRaises(RouteFetchError) as ctx:
            build_route_definitions(
                "grand_central_terminal",
                "bryant_park",
                self.candidates,
                via_ids=["grand_central_terminal"],
            )
        self.assertIn("cannot be identical to origin or destination", str(ctx.exception))

    def test_via_waypoint_matching_destination_rejected(self):
        """Intermediate waypoint identical to destination must be rejected."""
        with self.assertRaises(RouteFetchError) as ctx:
            build_route_definitions(
                "grand_central_terminal",
                "bryant_park",
                self.candidates,
                via_ids=["bryant_park"],
            )
        self.assertIn("cannot be identical to origin or destination", str(ctx.exception))


class TestPolylineAndGeometry(unittest.TestCase):
    """Test polyline6 decoding and geometric calculations."""

    def test_decode_polyline_valid(self):
        """Valid polyline6 string decodes to GeoJSON [lon, lat] coordinates."""
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        self.assertIsInstance(coords, list)
        self.assertGreaterEqual(len(coords), 2)
        for pt in coords:
            self.assertEqual(len(pt), 2)
            lon, lat = pt
            self.assertTrue(-180 <= lon <= 180)
            self.assertTrue(-90 <= lat <= 90)

    def test_decode_polyline_truncated_raises_error(self):
        """Truncated polyline string raises RouteFetchError."""
        with self.assertRaises(RouteFetchError):
            decode_polyline("y~hvlAj`fb")

    def test_decode_polyline_empty_raises_error(self):
        """Empty polyline raises RouteFetchError."""
        with self.assertRaises(RouteFetchError):
            decode_polyline("")

    def test_haversine_distance(self):
        """Haversine formula computes accurate distance in metres."""
        d = haversine_distance_m(40.752125, -73.977366, 40.754132, -73.983513)
        self.assertAlmostEqual(d, 565.0, delta=20.0)


class TestRouteResponseValidation(unittest.TestCase):
    """Test validation of raw Valhalla API responses."""

    def _make_valid_response(self):
        return {
            "trip": {
                "status": 0,
                "summary": {"length": 0.686, "time": 494.9},
                "legs": [
                    {
                        "shape": SAMPLE_ENCODED_POLYLINE,
                    }
                ],
            }
        }

    @staticmethod
    def _encode_polyline6(points: list[tuple[float, float]]) -> str:
        previous_lat = 0
        previous_lon = 0
        encoded = []
        for latitude, longitude in points:
            for axis, value in enumerate(
                (round(latitude * 1_000_000), round(longitude * 1_000_000))
            ):
                previous = previous_lat if axis == 0 else previous_lon
                delta = value - previous
                if axis == 0:
                    previous_lat = value
                else:
                    previous_lon = value
                signed = ~(delta << 1) if delta < 0 else delta << 1
                while signed >= 0x20:
                    encoded.append(chr((0x20 | (signed & 0x1F)) + 63))
                    signed >>= 5
                encoded.append(chr(signed + 63))
        return "".join(encoded)

    def test_multi_leg_response_validates_each_ordered_waypoint(self):
        origin = (40.752, -73.977)
        waypoint = (40.753, -73.979)
        end = origin
        valid = {
            "trip": {
                "status": 0,
                "summary": {"length": 1.2, "time": 900},
                "legs": [
                    {"shape": self._encode_polyline6([origin, waypoint])},
                    {"shape": self._encode_polyline6([waypoint, end])},
                ],
            }
        }
        requested = [
            {"lat": origin[0], "lon": origin[1]},
            {"lat": waypoint[0], "lon": waypoint[1]},
            {"lat": end[0], "lon": end[1]},
        ]
        coords, _distance, _duration = validate_route_response(valid, requested)
        self.assertGreaterEqual(len(coords), 3)

        invalid = {
            "trip": {
                **valid["trip"],
                "legs": [
                    {"shape": self._encode_polyline6([origin, end])},
                    {"shape": self._encode_polyline6([end, origin])},
                ],
            }
        }
        with self.assertRaisesRegex(RouteFetchError, "requested waypoint"):
            validate_route_response(invalid, requested)

    def test_valid_response_passes_validation(self):
        """Valid Valhalla response validates and returns coordinates, distance, and duration."""
        res = self._make_valid_response()
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        start_pt = {"lat": coords[0][1], "lon": coords[0][0]}
        end_pt = {"lat": coords[-1][1], "lon": coords[-1][0]}

        decoded_coords, dist_m, dur_s = validate_route_response(res, [start_pt, end_pt])
        self.assertEqual(len(decoded_coords), len(coords))
        self.assertAlmostEqual(dist_m, 686.0)
        self.assertAlmostEqual(dur_s, 494.9)

    def test_missing_trip_rejected(self):
        """Response lacking trip object raises RouteFetchError."""
        with self.assertRaises(RouteFetchError) as ctx:
            validate_route_response({"error": "something"}, [{"lat": 40.0, "lon": -74.0}, {"lat": 40.1, "lon": -74.1}])
        self.assertIn("missing top-level 'trip'", str(ctx.exception))

    def test_error_status_rejected(self):
        """Valhalla error status raises RouteFetchError."""
        bad_res = {"trip": {"status": 171, "status_message": "No route found between points"}}
        with self.assertRaises(RouteFetchError) as ctx:
            validate_route_response(bad_res, [{"lat": 40.0, "lon": -74.0}, {"lat": 40.1, "lon": -74.1}])
        self.assertIn("No route found", str(ctx.exception))

    def test_missing_legs_rejected(self):
        """Response lacking legs raises RouteFetchError."""
        bad_res = {"trip": {"status": 0, "legs": []}}
        with self.assertRaises(RouteFetchError) as ctx:
            validate_route_response(bad_res, [{"lat": 40.0, "lon": -74.0}, {"lat": 40.1, "lon": -74.1}])
        self.assertIn("no route legs", str(ctx.exception))

    def test_missing_summary_metrics_rejected(self):
        """Response lacking summary length/time raises RouteFetchError."""
        bad_res = {
            "trip": {
                "status": 0,
                "legs": [{"shape": SAMPLE_ENCODED_POLYLINE}],
                "summary": {},
            }
        }
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        start_pt = {"lat": coords[0][1], "lon": coords[0][0]}
        end_pt = {"lat": coords[-1][1], "lon": coords[-1][0]}
        with self.assertRaises(RouteFetchError) as ctx:
            validate_route_response(bad_res, [start_pt, end_pt])
        self.assertIn("missing 'length' or 'time'", str(ctx.exception))

    def test_endpoint_mismatch_rejected(self):
        """Route geometry ending far from requested destination is rejected."""
        res = self._make_valid_response()
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        start_pt = {"lat": coords[0][1], "lon": coords[0][0]}
        far_dest = {"lat": 40.9000, "lon": -74.1000}

        with self.assertRaises(RouteFetchError) as ctx:
            validate_route_response(res, [start_pt, far_dest], max_endpoint_tolerance_m=100.0)
        self.assertIn("tolerance", str(ctx.exception))


class TestGeoJsonOutput(unittest.TestCase):
    """Test GeoJSON FeatureCollection generation and provenance metadata."""

    def test_create_geojson_feature_collection(self):
        """Feature collection contains all required fields, properties, LineString, and limitation disclosure."""
        coords = [[-73.9772, 40.7523], [-73.9800, 40.7530], [-73.9835, 40.7541]]
        origin = {
            "landmark_id": "grand_central_terminal",
            "landmark_name": "Grand Central Terminal",
            "lat": 40.752125,
            "lon": -73.977366,
        }
        dest = {
            "landmark_id": "bryant_park",
            "landmark_name": "Bryant Park",
            "lat": 40.754132,
            "lon": -73.983513,
        }
        route_def = {
            "name": "Direct Test Route",
            "description": "Test description",
            "waypoints": [],
        }

        fc = create_geojson_feature_collection(
            route_id="test_direct",
            route_def=route_def,
            coordinates=coords,
            distance_m=686.0,
            duration_s=494.9,
            origin=origin,
            destination=dest,
            endpoint="https://valhalla1.openstreetmap.de",
            http_status=200,
        )

        self.assertEqual(fc["type"], "FeatureCollection")
        self.assertEqual(fc["name"], "test_direct")
        self.assertEqual(len(fc["features"]), 1)

        feat = fc["features"][0]
        self.assertEqual(feat["type"], "Feature")
        self.assertEqual(feat["geometry"]["type"], "LineString")
        self.assertEqual(feat["geometry"]["coordinates"], coords)

        props = feat["properties"]
        self.assertEqual(props["route_id"], "test_direct")
        self.assertEqual(props["origin_landmark_id"], "grand_central_terminal")
        self.assertEqual(props["destination_landmark_id"], "bryant_park")
        self.assertEqual(props["distance_m"], 686.0)
        self.assertEqual(props["estimated_duration_s"], 494.9)
        self.assertEqual(props["duration_type"], "routing_engine_estimate")
        self.assertEqual(props["costing"], "pedestrian")
        self.assertEqual(props["verification_status"], "prototype_candidate_unverified")
        self.assertEqual(props["coordinate_limitation_disclosure"], COORDINATE_LIMITATION_DISCLOSURE)
        self.assertIn("Valhalla", props["geometry_source"])


class TestSavedRouteArtifacts(unittest.TestCase):
    """Validate saved GeoJSON route files in backend/data/routes/."""

    def test_saved_routes_exist_and_are_valid(self):
        """Verify direct, via_vanderbilt, and architecture route files exist and are valid GeoJSON."""
        expected_routes = ["direct", "via_vanderbilt", "architecture"]
        for r_id in expected_routes:
            route_path = ROUTE_DIR / f"{r_id}.geojson"
            with self.subTest(route=r_id):
                self.assertTrue(route_path.exists(), msg=f"Missing saved route file {route_path}")
                data = json.loads(route_path.read_text(encoding="utf-8"))
                self.assertEqual(data.get("type"), "FeatureCollection")
                self.assertEqual(data.get("name"), r_id)
                self.assertEqual(len(data.get("features", [])), 1)

                feat = data["features"][0]
                self.assertEqual(feat["geometry"]["type"], "LineString")
                coords = feat["geometry"]["coordinates"]
                # Must not be a straight line: genuine pedestrian paths have many vertices
                self.assertGreater(len(coords), 10, msg=f"{r_id} geometry has too few vertices (straight-line?)")

                props = feat["properties"]
                self.assertGreater(props["distance_m"], 0)
                self.assertGreater(props["estimated_duration_s"], 0)
                self.assertEqual(props["origin_landmark_id"], "grand_central_terminal")
                self.assertEqual(props["destination_landmark_id"], "bryant_park")
                self.assertEqual(props["verification_status"], "prototype_candidate_unverified")
                self.assertIn("Valhalla", props["geometry_source"])


class TestReliabilityAndCaching(unittest.TestCase):
    """Offline unit tests for artifact namespacing, cache awareness, partial availability, and bounded retry."""

    def _sample_valhalla_response(self):
        return {
            "trip": {
                "status": 0,
                "summary": {"length": 0.5, "time": 360.0},
                "legs": [{"shape": SAMPLE_ENCODED_POLYLINE}],
            }
        }

    def test_raw_artifacts_are_namespaced_by_journey_and_route(self):
        """Raw request and response files must be namespaced by origin, destination, and route ID."""
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        origin = {
            "landmark_id": "chrysler_building",
            "landmark_name": "Chrysler Building",
            "lat": coords[0][1],
            "lon": coords[0][0],
        }
        dest = {
            "landmark_id": "daily_news_building",
            "landmark_name": "Daily News Building",
            "lat": coords[-1][1],
            "lon": coords[-1][0],
        }
        route_def = {
            "name": "Direct Chrysler to Daily News",
            "description": "Test description",
            "locations": [{"lat": origin["lat"], "lon": origin["lon"]}, {"lat": dest["lat"], "lon": dest["lon"]}],
            "waypoints": [],
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir = Path(tmp_routes)
            raw_dir = Path(tmp_raw)

            sample_resp = self._sample_valhalla_response()
            raw_bytes = json.dumps(sample_resp).encode("utf-8")

            with patch("fetch_routes.request_json", return_value=(sample_resp, raw_bytes, 200)), \
                 patch("fetch_routes.time.sleep", return_value=None):
                res = request_route(
                    endpoint="https://mock-valhalla.local",
                    route_id="direct",
                    route_def=route_def,
                    origin=origin,
                    destination=dest,
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                )

            # Check namespaced raw files exist
            expected_req = raw_dir / "chrysler_building__daily_news_building__direct_request.json"
            expected_resp = raw_dir / "chrysler_building__daily_news_building__direct_response.json"
            self.assertTrue(expected_req.exists(), "Namespaced request file missing")
            self.assertTrue(expected_resp.exists(), "Namespaced response file missing")

            # Check un-prefixed flat file was NOT written for non-core journey
            self.assertFalse((raw_dir / "direct_request.json").exists())

            # Check GeoJSON is namespaced
            expected_geojson = routes_dir / "chrysler_building__daily_news_building__direct.geojson"
            self.assertTrue(expected_geojson.exists(), "Namespaced GeoJSON missing")
            self.assertEqual(res["cached"], False)

    def test_cache_aware_fetching_reuses_existing_valid_geojson(self):
        """When a valid GeoJSON route exists, request_route reuses it without invoking request_json."""
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        origin = {
            "landmark_id": "chrysler_building",
            "landmark_name": "Chrysler Building",
            "lat": coords[0][1],
            "lon": coords[0][0],
        }
        dest = {
            "landmark_id": "daily_news_building",
            "landmark_name": "Daily News Building",
            "lat": coords[-1][1],
            "lon": coords[-1][0],
        }
        route_def = {
            "name": "Direct Chrysler to Daily News",
            "description": "Test description",
            "locations": [{"lat": origin["lat"], "lon": origin["lon"]}, {"lat": dest["lat"], "lon": dest["lon"]}],
            "waypoints": [],
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir = Path(tmp_routes)
            raw_dir = Path(tmp_raw)

            # Pre-seed cached GeoJSON
            cached_geojson = {
                "type": "FeatureCollection",
                "name": "direct",
                "metadata": {
                    "origin_landmark_id": "chrysler_building",
                    "destination_landmark_id": "daily_news_building",
                    "route_id": "direct",
                    "costing": "pedestrian",
                    "provider": "Valhalla public demo",
                    "geometry_source": "Valhalla response only",
                },
                "features": [{
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "route_id": "direct",
                        "distance_m": 450.0,
                        "estimated_duration_s": 320.0,
                        "origin_landmark_id": "chrysler_building",
                        "destination_landmark_id": "daily_news_building",
                        "route_id": "direct",
                        "costing": "pedestrian",
                        "provider": "Valhalla public demo",
                        "geometry_source": "Valhalla response only",
                    },
                }],
            }
            cached_file = routes_dir / "chrysler_building__daily_news_building__direct.geojson"
            cached_file.write_text(json.dumps(cached_geojson), encoding="utf-8")

            # Call request_route with mock that fails if called
            with patch("fetch_routes.request_json", side_effect=AssertionError("Network called despite cache")):
                res = request_route(
                    endpoint="https://mock-valhalla.local",
                    route_id="direct",
                    route_def=route_def,
                    origin=origin,
                    destination=dest,
                    skip_existing=True,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                )

            self.assertTrue(res["cached"])
            self.assertEqual(res["distance_m"], 450.0)
            self.assertEqual(res["duration_s"], 320.0)

    def test_force_bypasses_cache(self):
        """When skip_existing is False, request_route queries network even if file exists."""
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        origin = {
            "landmark_id": "chrysler_building",
            "landmark_name": "Chrysler Building",
            "lat": coords[0][1],
            "lon": coords[0][0],
        }
        dest = {
            "landmark_id": "daily_news_building",
            "landmark_name": "Daily News Building",
            "lat": coords[-1][1],
            "lon": coords[-1][0],
        }
        route_def = {
            "name": "Direct Chrysler to Daily News",
            "description": "Test description",
            "locations": [{"lat": origin["lat"], "lon": origin["lon"]}, {"lat": dest["lat"], "lon": dest["lon"]}],
            "waypoints": [],
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir = Path(tmp_routes)
            raw_dir = Path(tmp_raw)

            cached_file = routes_dir / "chrysler_building__daily_news_building__direct.geojson"
            cached_file.write_text(json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8")

            sample_resp = self._sample_valhalla_response()
            raw_bytes = json.dumps(sample_resp).encode("utf-8")

            with patch("fetch_routes.request_json", return_value=(sample_resp, raw_bytes, 200)) as mock_net, \
                 patch("fetch_routes.time.sleep", return_value=None):
                res = request_route(
                    endpoint="https://mock-valhalla.local",
                    route_id="direct",
                    route_def=route_def,
                    origin=origin,
                    destination=dest,
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                )

            mock_net.assert_called_once()
            self.assertFalse(res["cached"])

    def test_partial_availability_when_optional_alternative_fails(self):
        """When an alternative route fails, fetch_all_routes preserves successful routes and records error."""
        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir = Path(tmp_routes)
            raw_dir = Path(tmp_raw)

            # Mock request_route directly: succeed for "direct", fail for alternatives.
            def mock_request_route(endpoint, route_id, route_def, origin, destination,
                                   skip_existing=True, routes_dir=None, raw_dir=None, **kwargs):
                if route_id == "direct":
                    return {
                        "feature_collection": {},
                        "output_path": str(routes_dir / "direct.geojson"),
                        "raw_response_path": None,
                        "distance_m": 500.0,
                        "duration_s": 360.0,
                        "cached": False,
                    }
                raise RouteFetchError(f"Simulated failure for {route_id}")

            with patch("fetch_routes.request_route", side_effect=mock_request_route):
                results = fetch_all_routes(
                    origin_id="grand_central_terminal",
                    destination_id="bryant_park",
                    endpoint="https://mock-valhalla.local",
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                )

            # Direct route succeeded and is in results
            self.assertIn("direct", results)
            # Alternatives failed and are not in results
            self.assertNotIn("via_vanderbilt", results)

            # Error artifact for failed alternative must exist and be namespaced
            err_file = raw_dir / "grand_central_terminal__bryant_park__via_vanderbilt__error.json"
            self.assertTrue(err_file.exists(), "Expected namespaced error artifact for via_vanderbilt")
            err_data = json.loads(err_file.read_text(encoding="utf-8"))
            self.assertEqual(err_data["route_id"], "via_vanderbilt")
            self.assertIn("Simulated failure", err_data["error"])

    def test_all_routes_failing_raises_route_fetch_error_and_records_journey_error(self):
        """When all routes fail, fetch_all_routes raises RouteFetchError and writes journey error artifact."""
        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir = Path(tmp_routes)
            raw_dir = Path(tmp_raw)

            with patch("fetch_routes.request_json", side_effect=RouteFetchError("Simulated total outage")), \
                 patch("fetch_routes.time.sleep", return_value=None):
                with self.assertRaises(RouteFetchError) as ctx:
                    fetch_all_routes(
                        origin_id="chrysler_building",
                        destination_id="daily_news_building",
                        endpoint="https://mock-valhalla.local",
                        skip_existing=False,
                        routes_dir=routes_dir,
                        raw_dir=raw_dir,
                    )
                self.assertIn("Failed to fetch any routes", str(ctx.exception))

            journey_err = raw_dir / "chrysler_building__daily_news_building__route_error.json"
            self.assertTrue(journey_err.exists(), "Expected namespaced journey error artifact")

    def test_transient_http_429_retry_with_backoff(self):
        """Transient HTTP 429 retries with backoff and succeeds on subsequent attempt."""
        sample_resp = self._sample_valhalla_response()
        raw_bytes = json.dumps(sample_resp).encode("utf-8")

        mock_resp_obj = MagicMock()
        mock_resp_obj.status = 200
        mock_resp_obj.read.return_value = raw_bytes
        mock_resp_obj.__enter__.return_value = mock_resp_obj

        # First call raises HTTP 429, second call succeeds
        err_429 = urllib.error.HTTPError(
            url="https://valhalla.local/route",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "0.01"},
            fp=io.BytesIO(b"rate limit exceeded"),
        )

        with patch("urllib.request.urlopen", side_effect=[err_429, mock_resp_obj]) as mock_open, \
             patch("fetch_routes.time.sleep", return_value=None) as mock_sleep:
            parsed, raw, status = request_json(
                "https://valhalla.local/route",
                payload={"test": 1},
                max_retries=3,
                backoff_base_s=0.01,
            )

        self.assertEqual(status, 200)
        self.assertEqual(parsed, sample_resp)
        self.assertEqual(mock_open.call_count, 2)
        mock_sleep.assert_called_once()

    def test_transient_http_503_retry_with_backoff(self):
        """Transient HTTP 503 retries with backoff and succeeds on subsequent attempt."""
        sample_resp = self._sample_valhalla_response()
        raw_bytes = json.dumps(sample_resp).encode("utf-8")

        mock_resp_obj = MagicMock()
        mock_resp_obj.status = 200
        mock_resp_obj.read.return_value = raw_bytes
        mock_resp_obj.__enter__.return_value = mock_resp_obj

        err_503 = urllib.error.HTTPError(
            url="https://valhalla.local/route",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=io.BytesIO(b"service temporarily unavailable"),
        )

        with patch("urllib.request.urlopen", side_effect=[err_503, mock_resp_obj]) as mock_open, \
             patch("fetch_routes.time.sleep", return_value=None) as mock_sleep:
            parsed, raw, status = request_json(
                "https://valhalla.local/route",
                payload={"test": 1},
                max_retries=3,
                backoff_base_s=0.01,
            )

        self.assertEqual(status, 200)
        self.assertEqual(mock_open.call_count, 2)
        mock_sleep.assert_called_once()

    def test_retry_exhausted_raises_route_fetch_error(self):
        """When retries are exhausted on 503, RouteFetchError is raised."""
        err_503 = urllib.error.HTTPError(
            url="https://valhalla.local/route",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=io.BytesIO(b"service unavailable"),
        )

        with patch("urllib.request.urlopen", side_effect=err_503) as mock_open, \
             patch("fetch_routes.time.sleep", return_value=None):
            with self.assertRaises(RouteFetchError) as ctx:
                request_json(
                    "https://valhalla.local/route",
                    payload={"test": 1},
                    max_retries=3,
                    backoff_base_s=0.01,
                )
            self.assertIn("exhausted 3 retries", str(ctx.exception))
        self.assertEqual(mock_open.call_count, 3)

    def test_strict_cache_validation_rejects_mismatched_or_non_pedestrian_route(self):
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        fc = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "route_id": "direct",
                    "origin_landmark_id": "chrysler_building",
                    "destination_landmark_id": "daily_news_building",
                    "distance_m": 500,
                    "estimated_duration_s": 360,
                    "costing": "pedestrian",
                    "provider": "Valhalla public demo",
                    "geometry_source": "Valhalla response only",
                },
            }],
        }
        self.assertEqual(
            validate_cached_route(fc, "chrysler_building", "daily_news_building", "direct"),
            (500.0, 360.0),
        )
        with self.assertRaises(RouteFetchError):
            validate_cached_route(fc, "grand_central_terminal", "daily_news_building", "direct")
        fc["features"][0]["geometry"]["type"] = "Polygon"
        with self.assertRaises(RouteFetchError):
            validate_cached_route(fc, "chrysler_building", "daily_news_building", "direct")
        fc["features"][0]["geometry"] = {"type": "LineString", "coordinates": [[181, 40], [182, 41]]}
        with self.assertRaises(RouteFetchError):
            validate_cached_route(fc, "chrysler_building", "daily_news_building", "direct")

    def test_route_response_rejects_non_finite_metrics(self):
        response = self._sample_valhalla_response()
        response["trip"]["summary"]["length"] = float("nan")
        coordinates = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        endpoints = [
            {"lat": coordinates[0][1], "lon": coordinates[0][0]},
            {"lat": coordinates[-1][1], "lon": coordinates[-1][0]},
        ]
        with self.assertRaises(RouteFetchError):
            validate_route_response(response, endpoints)

    def test_duplicate_geometry_is_not_a_meaningful_alternative(self):
        """A different waypoint label on the same corridor is a duplicate, not an alternative."""
        base_feature = {
            "geometry": {"type": "LineString", "coordinates": [[-73.98, 40.75], [-73.97, 40.76]]},
            "properties": {"distance_m": 500, "via_waypoints": []},
        }
        stop_feature = {
            "geometry": {"type": "LineString", "coordinates": [[-73.98, 40.75], [-73.97, 40.76]]},
            "properties": {
                "distance_m": 500,
                "via_waypoints": [{"landmark_id": "one_vanderbilt"}],
            },
        }
        self.assertFalse(routes_are_meaningfully_distinct(
            {"features": [base_feature]}, {"features": [stop_feature]}
        ))

    def test_distance_difference_counts_as_meaningful_alternative(self):
        """A materially longer route is meaningful even when its geometry overlaps."""
        base_feature = {
            "geometry": {"type": "LineString", "coordinates": [[-73.98, 40.75], [-73.97, 40.76]]},
            "properties": {"distance_m": 500, "via_waypoints": []},
        }
        longer_feature = {
            "geometry": {"type": "LineString", "coordinates": [[-73.98, 40.75], [-73.97, 40.76]]},
            "properties": {"distance_m": 700, "via_waypoints": []},
        }
        self.assertTrue(routes_are_meaningfully_distinct(
            {"features": [base_feature]}, {"features": [longer_feature]}
        ))

    def test_geometry_divergence_counts_as_meaningful_alternative(self):
        """Substantial path separation is meaningful even when distances match."""
        base_line = [[-73.990 + i * 0.001, 40.750] for i in range(11)]
        shifted_line = [[-73.990 + i * 0.001, 40.7505] for i in range(11)]
        base_feature = {
            "geometry": {"type": "LineString", "coordinates": base_line},
            "properties": {"distance_m": 500, "via_waypoints": []},
        }
        shifted_feature = {
            "geometry": {"type": "LineString", "coordinates": shifted_line},
            "properties": {"distance_m": 500, "via_waypoints": []},
        }
        self.assertTrue(routes_are_meaningfully_distinct(
            {"features": [base_feature]}, {"features": [shifted_feature]}
        ))

    def test_excessive_detour_is_rejected(self):
        """Actual alternative length beyond the detour policy is rejected."""
        direct = {"features": [{"properties": {"distance_m": 500}}]}
        acceptable = {"features": [{"properties": {"distance_m": 700}}]}
        excessive = {"features": [{"properties": {"distance_m": 1500}}]}
        self.assertFalse(route_detour_is_excessive(acceptable, direct))
        self.assertTrue(route_detour_is_excessive(excessive, direct))

    def _feature_collection(self, route_id, coordinates, distance_m, duration_s, origin_id, destination_id):
        return create_geojson_feature_collection(
            route_id=route_id,
            route_def={"name": route_id, "description": "test", "waypoints": []},
            coordinates=coordinates,
            distance_m=distance_m,
            duration_s=duration_s,
            origin={"landmark_id": origin_id, "landmark_name": origin_id, "lat": coordinates[0][1], "lon": coordinates[0][0]},
            destination={"landmark_id": destination_id, "landmark_name": destination_id, "lat": coordinates[-1][1], "lon": coordinates[-1][0]},
            endpoint="https://mock-valhalla.local",
        )

    def test_new_duplicate_alternative_is_removed_but_direct_preserved(self):
        """A newly generated duplicate alternative is deleted; the direct baseline survives."""
        origin_id, destination_id = "chrysler_building", "daily_news_building"
        line = [[-73.9757, 40.7516], [-73.9734, 40.7501]]
        direct_fc = self._feature_collection("direct", line, 300.0, 220.0, origin_id, destination_id)
        duplicate_fc = self._feature_collection("alternative_1", line, 300.0, 220.0, origin_id, destination_id)
        definitions = {
            "direct": {"route_id": "direct", "name": "direct", "description": "", "locations": [], "waypoints": []},
            "alternative_1": {"route_id": "alternative_1", "name": "alt", "description": "", "locations": [], "waypoints": []},
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)

            def fake_request_route(endpoint, route_id, route_def, origin, destination, **kwargs):
                fc = direct_fc if route_id == "direct" else duplicate_fc
                path = routes_dir / f"{origin_id}__{destination_id}__{route_id}.geojson"
                path.write_text(json.dumps(fc), encoding="utf-8")
                return {
                    "feature_collection": fc,
                    "output_path": str(path),
                    "distance_m": 300.0,
                    "duration_s": 220.0,
                    "cached": False,
                }

            with patch("fetch_routes.build_route_definitions", return_value=definitions), \
                 patch("fetch_routes.request_route", side_effect=fake_request_route):
                results = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    endpoint="https://mock-valhalla.local",
                    route_filter="all",
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertEqual(sorted(results), ["direct"])
            self.assertTrue((routes_dir / f"{origin_id}__{destination_id}__direct.geojson").exists())
            self.assertFalse((routes_dir / f"{origin_id}__{destination_id}__alternative_1.geojson").exists())
            rejection = raw_dir / f"{origin_id}__{destination_id}__alternative_1__rejected.json"
            self.assertTrue(rejection.exists())
            self.assertEqual(json.loads(rejection.read_text(encoding="utf-8"))["reason"], "duplicate_path")

    def test_alternative_selection_failure_preserves_direct_baseline(self):
        """A selector error after a successful direct fetch must not discard the direct route."""
        origin_id, destination_id = "chrysler_building", "daily_news_building"
        line = [[-73.9757, 40.7516], [-73.9734, 40.7501]]
        direct_fc = self._feature_collection("direct", line, 300.0, 220.0, origin_id, destination_id)
        direct_definition = {
            "route_id": "direct",
            "name": "direct",
            "description": "",
            "locations": [],
            "waypoints": [],
        }

        def fake_build(*args, **kwargs):
            if kwargs.get("include_alternatives") is False:
                return {"direct": direct_definition}
            raise RuntimeError("simulated selector failure")

        def fake_request_route(endpoint, route_id, route_def, origin, destination, **kwargs):
            path = routes_dir / f"{origin_id}__{destination_id}__{route_id}.geojson"
            path.write_text(json.dumps(direct_fc), encoding="utf-8")
            return {
                "feature_collection": direct_fc,
                "output_path": str(path),
                "distance_m": 300.0,
                "duration_s": 220.0,
                "cached": False,
            }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            with patch("fetch_routes.build_route_definitions", side_effect=fake_build), \
                 patch("fetch_routes.request_route", side_effect=fake_request_route):
                results = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    endpoint="https://mock-valhalla.local",
                    route_filter="all",
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertEqual(sorted(results), ["direct"])
            self.assertTrue((routes_dir / f"{origin_id}__{destination_id}__direct.geojson").exists())

    def test_cached_duplicate_alternative_is_preserved_and_flagged(self):
        """A pre-existing cached duplicate is preserved for compatibility but not counted as meaningful."""
        origin_id, destination_id = "chrysler_building", "daily_news_building"
        line = [[-73.9757, 40.7516], [-73.9734, 40.7501]]
        direct_fc = self._feature_collection("direct", line, 300.0, 220.0, origin_id, destination_id)
        duplicate_fc = self._feature_collection("alternative_1", line, 300.0, 220.0, origin_id, destination_id)
        definitions = {
            "direct": {"route_id": "direct", "name": "direct", "description": "", "locations": [], "waypoints": []},
            "alternative_1": {"route_id": "alternative_1", "name": "alt", "description": "", "locations": [], "waypoints": []},
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)

            def fake_request_route(endpoint, route_id, route_def, origin, destination, **kwargs):
                fc = direct_fc if route_id == "direct" else duplicate_fc
                path = routes_dir / f"{origin_id}__{destination_id}__{route_id}.geojson"
                path.write_text(json.dumps(fc), encoding="utf-8")
                return {
                    "feature_collection": fc,
                    "output_path": str(path),
                    "distance_m": 300.0,
                    "duration_s": 220.0,
                    "cached": True,
                }

            with patch("fetch_routes.build_route_definitions", return_value=definitions), \
                 patch("fetch_routes.request_route", side_effect=fake_request_route):
                results = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    endpoint="https://mock-valhalla.local",
                    route_filter="all",
                    skip_existing=True,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertEqual(sorted(results), ["alternative_1", "direct"])
            self.assertTrue((routes_dir / f"{origin_id}__{destination_id}__alternative_1.geojson").exists())
            self.assertFalse(results["alternative_1"]["meaningful"])
            self.assertEqual(results["alternative_1"]["rejection_reason"], "duplicate_path")

    def test_new_excessive_detour_alternative_is_removed(self):
        """A newly generated alternative beyond the actual detour limit is rejected."""
        origin_id, destination_id = "chrysler_building", "daily_news_building"
        direct_line = [[-73.9757, 40.7516], [-73.9734, 40.7501]]
        detour_line = [[-73.9757, 40.7516], [-73.9900, 40.7600], [-73.9734, 40.7501]]
        direct_fc = self._feature_collection("direct", direct_line, 300.0, 220.0, origin_id, destination_id)
        detour_fc = self._feature_collection("alternative_1", detour_line, 1200.0, 900.0, origin_id, destination_id)
        definitions = {
            "direct": {"route_id": "direct", "name": "direct", "description": "", "locations": [], "waypoints": []},
            "alternative_1": {"route_id": "alternative_1", "name": "alt", "description": "", "locations": [], "waypoints": []},
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)

            def fake_request_route(endpoint, route_id, route_def, origin, destination, **kwargs):
                fc = direct_fc if route_id == "direct" else detour_fc
                path = routes_dir / f"{origin_id}__{destination_id}__{route_id}.geojson"
                path.write_text(json.dumps(fc), encoding="utf-8")
                return {
                    "feature_collection": fc,
                    "output_path": str(path),
                    "distance_m": 300.0 if route_id == "direct" else 1200.0,
                    "duration_s": 220.0 if route_id == "direct" else 900.0,
                    "cached": False,
                }

            with patch("fetch_routes.build_route_definitions", return_value=definitions), \
                 patch("fetch_routes.request_route", side_effect=fake_request_route):
                results = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    endpoint="https://mock-valhalla.local",
                    route_filter="all",
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertEqual(sorted(results), ["direct"])
            rejection = raw_dir / f"{origin_id}__{destination_id}__alternative_1__rejected.json"
            self.assertTrue(rejection.exists())
            self.assertEqual(json.loads(rejection.read_text(encoding="utf-8"))["reason"], "excessive_detour")

    def test_rejected_route_marker_reused_without_network_request(self):
        """When skip_existing is True, request_route reuses __rejected.json without calling network."""
        origin = {"landmark_id": "chrysler_building", "landmark_name": "Chrysler", "lat": 40.7516, "lon": -73.9757}
        dest = {"landmark_id": "daily_news_building", "landmark_name": "Daily News", "lat": 40.7501, "lon": -73.9734}
        route_def = {"name": "alt", "description": "", "locations": [{"lat": 40.7516, "lon": -73.9757}, {"lat": 40.7501, "lon": -73.9734}], "waypoints": []}

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            rejection_marker = raw_dir / "chrysler_building__daily_news_building__alternative_1__rejected.json"
            rejection_marker.write_text(json.dumps({
                "rejected_at": "2026-11-21T00:00:00Z",
                "origin": "chrysler_building",
                "destination": "daily_news_building",
                "route_id": "alternative_1",
                "reason": "duplicate_path",
            }), encoding="utf-8")

            with patch("fetch_routes.request_json", side_effect=AssertionError("Network called despite rejected cache")):
                res = request_route(
                    endpoint="https://mock-valhalla.local",
                    route_id="alternative_1",
                    route_def=route_def,
                    origin=origin,
                    destination=dest,
                    skip_existing=True,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                )

            self.assertTrue(res["cached"])
            self.assertTrue(res["rejected"])
            self.assertEqual(res["rejection_reason"], "duplicate_path")
            self.assertIsNone(res["feature_collection"])

    def test_rejected_route_marker_bypassed_when_skip_existing_false(self):
        """When skip_existing is False, request_route ignores __rejected.json and calls network."""
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        origin = {"landmark_id": "chrysler_building", "landmark_name": "Chrysler", "lat": coords[0][1], "lon": coords[0][0]}
        dest = {"landmark_id": "daily_news_building", "landmark_name": "Daily News", "lat": coords[-1][1], "lon": coords[-1][0]}
        route_def = {"name": "alt", "description": "", "locations": [{"lat": origin["lat"], "lon": origin["lon"]}, {"lat": dest["lat"], "lon": dest["lon"]}], "waypoints": []}

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            rejection_marker = raw_dir / "chrysler_building__daily_news_building__alternative_1__rejected.json"
            rejection_marker.write_text(json.dumps({
                "rejected_at": "2026-11-21T00:00:00Z",
                "origin": "chrysler_building",
                "destination": "daily_news_building",
                "route_id": "alternative_1",
                "reason": "duplicate_path",
            }), encoding="utf-8")

            sample_resp = self._sample_valhalla_response()
            raw_bytes = json.dumps(sample_resp).encode("utf-8")

            with patch("fetch_routes.request_json", return_value=(sample_resp, raw_bytes, 200)) as mock_req, \
                 patch("fetch_routes.time.sleep", return_value=None):
                res = request_route(
                    endpoint="https://mock-valhalla.local",
                    route_id="alternative_1",
                    route_def=route_def,
                    origin=origin,
                    destination=dest,
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                )

            self.assertTrue(mock_req.called)
            self.assertFalse(res["cached"])
            self.assertIsNotNone(res["feature_collection"])

    def test_fetch_all_routes_idempotent_with_previously_rejected_alternative(self):
        """fetch_all_routes with skip_existing=True does not call network for previously rejected alternatives."""
        origin_id, destination_id = "chrysler_building", "daily_news_building"
        direct_line = [[-73.9757, 40.7516], [-73.9734, 40.7501]]
        direct_fc = self._feature_collection("direct", direct_line, 300.0, 220.0, origin_id, destination_id)
        locs = [{"lat": direct_line[0][1], "lon": direct_line[0][0]}, {"lat": direct_line[-1][1], "lon": direct_line[-1][0]}]
        definitions = {
            "direct": {"route_id": "direct", "name": "direct", "description": "", "locations": locs, "waypoints": []},
            "alternative_1": {"route_id": "alternative_1", "name": "alt", "description": "", "locations": locs, "waypoints": []},
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            # Pre-seed cached direct route
            (routes_dir / f"{origin_id}__{destination_id}__direct.geojson").write_text(
                json.dumps(direct_fc), encoding="utf-8"
            )
            # Pre-seed rejected alternative marker
            (raw_dir / f"{origin_id}__{destination_id}__alternative_1__rejected.json").write_text(
                json.dumps({
                    "rejected_at": "2026-11-21T00:00:00Z",
                    "origin": origin_id,
                    "destination": destination_id,
                    "route_id": "alternative_1",
                    "reason": "duplicate_path",
                }),
                encoding="utf-8",
            )

            with patch("fetch_routes.build_route_definitions", return_value=definitions), \
                 patch("fetch_routes.request_json", side_effect=AssertionError("Network called despite cache")):
                results = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    endpoint="https://mock-valhalla.local",
                    route_filter="all",
                    skip_existing=True,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            # Direct route is preserved from cache, alternative_1 was skipped idempotently
            self.assertEqual(sorted(results), ["direct"])
            self.assertTrue(results["direct"]["cached"])

            # An explicitly selected strategy may request a new alternative from
            # the cached baseline; implicit cache validation remains network-free.
            with patch("fetch_routes.request_native_alternates", return_value=[]) as mock_native:
                fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    endpoint="https://mock-valhalla.local",
                    route_filter="all",
                    alternative_strategy="native",
                    skip_existing=True,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            mock_native.assert_called_once()

    def test_alternative_raw_artifacts_are_namespaced(self):
        """Alternative route requests retain journey- and route-specific raw artifacts."""
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        origin = {
            "landmark_id": "chrysler_building",
            "landmark_name": "Chrysler Building",
            "lat": coords[0][1],
            "lon": coords[0][0],
        }
        dest = {
            "landmark_id": "daily_news_building",
            "landmark_name": "Daily News Building",
            "lat": coords[-1][1],
            "lon": coords[-1][0],
        }
        route_def = {
            "name": "Via Test Landmark",
            "description": "Test description",
            "locations": [{"lat": origin["lat"], "lon": origin["lon"]}, {"lat": dest["lat"], "lon": dest["lon"]}],
            "waypoints": [],
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            sample_resp = self._sample_valhalla_response()
            raw_bytes = json.dumps(sample_resp).encode("utf-8")

            with patch("fetch_routes.request_json", return_value=(sample_resp, raw_bytes, 200)), \
                 patch("fetch_routes.time.sleep", return_value=None):
                request_route(
                    endpoint="https://mock-valhalla.local",
                    route_id="alternative_1",
                    route_def=route_def,
                    origin=origin,
                    destination=dest,
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                )

            self.assertTrue((raw_dir / "chrysler_building__daily_news_building__alternative_1_request.json").exists())
            self.assertTrue((raw_dir / "chrysler_building__daily_news_building__alternative_1_response.json").exists())
            self.assertTrue((routes_dir / "chrysler_building__daily_news_building__alternative_1.geojson").exists())

    def test_batch_report_counts_alternatives_and_meaningful_routes(self):
        """The batch report records valid, meaningful, and alternative route counts."""
        origin_id, destination_id = "chrysler_building", "daily_news_building"
        direct_fc = {
            "features": [{
                "geometry": {"type": "LineString", "coordinates": [[-73.9757, 40.7516], [-73.9734, 40.7501]]},
                "properties": {"distance_m": 400.0, "via_waypoints": []},
            }]
        }
        alternative_fc = {
            "features": [{
                "geometry": {"type": "LineString", "coordinates": [[-73.9757, 40.7516], [-73.9800, 40.7550], [-73.9734, 40.7501]]},
                "properties": {"distance_m": 700.0, "via_waypoints": [{"landmark_id": "one_vanderbilt"}]},
            }]
        }

        def fake_fetch(**kwargs):
            return {
                "direct": {
                    "feature_collection": direct_fc,
                    "output_path": "direct.geojson",
                    "distance_m": 400.0,
                    "duration_s": 300.0,
                    "cached": False,
                },
                "alternative_1": {
                    "feature_collection": alternative_fc,
                    "output_path": "alternative_1.geojson",
                    "distance_m": 700.0,
                    "duration_s": 500.0,
                    "cached": False,
                },
            }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            with patch("fetch_routes.fetch_all_routes", side_effect=fake_fetch), \
                 patch("fetch_routes.load_valid_journey_cache", return_value={
                     "direct": direct_fc,
                     "alternative_1": alternative_fc,
                 }):
                report = generate_all_journeys(
                    endpoint="https://mock-valhalla.local",
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertEqual(report["valid_route_count"], 144)
            self.assertEqual(len(report["journeys_with_two_or_more_routes"]), 72)
            self.assertEqual(len(report["journeys_with_meaningful_alternatives"]), 72)
            sample = report["journeys"][f"{origin_id}__{destination_id}"]
            self.assertEqual(sample["valid_route_count"], 2)
            self.assertEqual(sample["meaningful_route_count"], 2)
            self.assertEqual(sample["alternative_route_ids"], ["alternative_1"])
            self.assertEqual(sample["distinct_alternative_ids"], ["alternative_1"])

    def test_batch_marks_partial_when_direct_is_missing(self):
        """An alternative-only result is reported as partial, not as a validated baseline."""
        alternative_fc = {
            "features": [{
                "geometry": {"type": "LineString", "coordinates": [[-73.9757, 40.7516], [-73.9734, 40.7501]]},
                "properties": {"distance_m": 400.0, "via_waypoints": []},
            }]
        }

        def fake_fetch(**kwargs):
            return {
                "alternative_1": {
                    "feature_collection": alternative_fc,
                    "output_path": "alternative_1.geojson",
                    "distance_m": 400.0,
                    "duration_s": 300.0,
                    "cached": False,
                },
            }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            with patch("fetch_routes.fetch_all_routes", side_effect=fake_fetch), \
                 patch("fetch_routes.load_valid_journey_cache", return_value={
                     "alternative_1": alternative_fc,
                 }):
                report = generate_all_journeys(
                    endpoint="https://mock-valhalla.local",
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertEqual(report["validated_route_journey_count"], 0)
            self.assertEqual(report["partial_journey_count"], 72)
            self.assertEqual(report["failed_journey_count"], 0)
            sample = next(iter(report["journeys"].values()))
            self.assertEqual(sample["status"], "partial_alternative_only")
            self.assertEqual(sample["route_validation_status"], "partial")

    def test_batch_generation_attempts_each_of_72_journeys_and_persists_report(self):
        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)

            def fake_fetch(**kwargs):
                origin = kwargs["origin_id"]
                destination = kwargs["destination_id"]
                return {"direct": {
                    "feature_collection": {},
                    "output_path": str(routes_dir / f"{origin}__{destination}__direct.geojson"),
                    "distance_m": 400.0,
                    "duration_s": 300.0,
                    "cached": False,
                }}

            with patch("fetch_routes.fetch_all_routes", side_effect=fake_fetch) as mocked, \
                 patch("fetch_routes.load_valid_journey_cache", return_value={"direct": {}}):
                report = generate_all_journeys(
                    endpoint="https://mock-valhalla.local",
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertEqual(mocked.call_count, 72)
            self.assertEqual(report["validated_route_journey_count"], 72)
            self.assertEqual(report["failed_journey_count"], 0)
            self.assertEqual(report["valid_route_count"], 72)
            self.assertTrue((raw_dir / "route_coverage_report.json").exists())
            persisted = json.loads((raw_dir / "route_coverage_report.json").read_text(encoding="utf-8"))
            self.assertEqual(len(persisted["journeys"]), 72)
            self.assertTrue(all(
                journey["status"] == "route_generated_candidate_unverified"
                and journey["endpoint_access_verification_status"] == "candidate_unverified"
                for journey in persisted["journeys"].values()
            ))


def _encode_polyline(points: list[list[float]], precision: int = 6) -> str:
    """Encode GeoJSON positions [longitude, latitude] to Valhalla polyline6 string."""
    factor = 10**precision
    result = []
    prev_lat = prev_lon = 0
    for lon, lat in points:
        lat_i = round(lat * factor)
        lon_i = round(lon * factor)
        d_lat = lat_i - prev_lat
        d_lon = lon_i - prev_lon
        prev_lat = lat_i
        prev_lon = lon_i
        for val in (d_lat, d_lon):
            val = ~(val << 1) if val < 0 else (val << 1)
            while val >= 0x20:
                result.append(chr((0x20 | (val & 0x1f)) + 63))
                val >>= 5
            result.append(chr(val + 63))
    return "".join(result)


class TestNativeValhallaAlternatives(unittest.TestCase):
    """Offline unit and integration tests for Phase 8.3 native Valhalla pedestrian alternatives."""

    def test_extract_native_alternates_zero_or_empty(self):
        """Returns empty list when response has no alternates, empty list, or non-list."""
        req_locs = [{"lat": 40.7527, "lon": -73.9772}, {"lat": 40.7536, "lon": -73.9832}]
        res = {"trip": {"status": 0, "summary": {"length": 0.5, "time": 300}, "legs": [{"shape": SAMPLE_ENCODED_POLYLINE}]}}
        self.assertEqual(extract_native_alternates_from_response(res, req_locs), [])

        res["alternates"] = []
        self.assertEqual(extract_native_alternates_from_response(res, req_locs), [])

        res["alternates"] = "invalid"
        self.assertEqual(extract_native_alternates_from_response(res, req_locs), [])

    def test_extract_native_alternates_malformed(self):
        """Malformed or incomplete alternate objects are skipped without raising errors."""
        req_locs = [{"lat": 40.7527, "lon": -73.9772}, {"lat": 40.7536, "lon": -73.9832}]
        res = {
            "trip": {"status": 0, "summary": {"length": 0.5, "time": 300}, "legs": [{"shape": SAMPLE_ENCODED_POLYLINE}]},
            "alternates": [
                {},  # Missing trip
                {"trip": {}},  # Empty trip
                {"trip": {"status": 1}},  # Non-zero status
                {"trip": {"status": 0, "summary": {}, "legs": []}},  # Missing legs
                {"trip": {"status": 0, "summary": {"length": 0.5, "time": 300}, "legs": [{"shape": "???"}]}},  # Bad polyline
            ],
        }
        alts = extract_native_alternates_from_response(res, req_locs)
        self.assertEqual(len(alts), 0)

    def test_extract_native_alternates_one_and_two_valid(self):
        """Successfully decodes 1 or 2 valid alternate trips."""
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        req_locs = [{"lat": coords[0][1], "lon": coords[0][0]}, {"lat": coords[-1][1], "lon": coords[-1][0]}]
        alt_trip_1 = {
            "status": 0,
            "summary": {"length": 0.6, "time": 360.0},
            "legs": [{"shape": SAMPLE_ENCODED_POLYLINE}],
        }
        alt_trip_2 = {
            "status": 0,
            "summary": {"length": 0.7, "time": 420.0},
            "legs": [{"shape": SAMPLE_ENCODED_POLYLINE}],
        }
        res = {
            "trip": {"status": 0, "summary": {"length": 0.5, "time": 300}, "legs": [{"shape": SAMPLE_ENCODED_POLYLINE}]},
            "alternates": [{"trip": alt_trip_1}, {"trip": alt_trip_2}],
        }
        alts = extract_native_alternates_from_response(res, req_locs)
        self.assertEqual(len(alts), 2)
        coords0, dist0, dur0 = alts[0]
        self.assertEqual(dist0, 600.0)
        self.assertEqual(dur0, 360.0)
        self.assertEqual(len(coords0), len(coords))
        self.assertEqual(alts[1][1], 700.0)

    def test_request_native_alternates_preserves_baseline_and_archives_response(self):
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        origin = {
            "landmark_id": "grand_central_terminal",
            "lat": coords[0][1],
            "lon": coords[0][0],
        }
        destination = {
            "landmark_id": "bryant_park",
            "lat": coords[-1][1],
            "lon": coords[-1][0],
        }
        trip = {
            "status": 0,
            "summary": {"length": 0.6, "time": 360},
            "legs": [{"shape": SAMPLE_ENCODED_POLYLINE}],
        }
        response = {
            "trip": trip,
            "alternates": [{"trip": trip}],
        }
        captured = []

        def fake_request_json(url, payload=None, **_kwargs):
            captured.append((url, payload))
            return response, json.dumps(response).encode("utf-8"), 200

        with tempfile.TemporaryDirectory() as temporary, patch(
            "fetch_routes.request_json", side_effect=fake_request_json
        ):
            alternatives = request_native_alternates(
                "https://mock-valhalla.local",
                origin,
                destination,
                num_alternates=1,
                raw_dir=Path(temporary),
                request_delay_s=0,
            )
            raw_files = list(Path(temporary).glob("*"))

        self.assertEqual(len(alternatives), 1)
        self.assertEqual(captured[0][0], "https://mock-valhalla.local/route")
        self.assertEqual(captured[0][1]["costing"], "pedestrian")
        self.assertEqual(captured[0][1]["alternates"], 1)
        self.assertEqual(len(raw_files), 2)
        self.assertFalse(any(path.suffix == ".geojson" for path in raw_files))

    def test_request_route_sends_alternates_for_direct_pair(self):
        """When num_alternates > 0 and locations count is 2, alternates is added to payload."""
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        origin = {"landmark_id": "grand_central_terminal", "landmark_name": "GCT", "lat": coords[0][1], "lon": coords[0][0]}
        dest = {"landmark_id": "bryant_park", "landmark_name": "BP", "lat": coords[-1][1], "lon": coords[-1][0]}
        route_def = {
            "name": "Direct GCT to BP",
            "description": "Direct",
            "locations": [{"lat": origin["lat"], "lon": origin["lon"]}, {"lat": dest["lat"], "lon": dest["lon"]}],
            "waypoints": [],
        }

        captured_payloads = []
        def fake_request_json(url, payload=None, *args, **kwargs):
            if payload is None and "payload" in kwargs:
                payload = kwargs["payload"]
            captured_payloads.append(dict(payload))
            resp = {
                "trip": {"status": 0, "summary": {"length": 0.5, "time": 300}, "legs": [{"shape": SAMPLE_ENCODED_POLYLINE}]},
                "alternates": [{
                    "trip": {"status": 0, "summary": {"length": 0.55, "time": 330}, "legs": [{"shape": SAMPLE_ENCODED_POLYLINE}]}
                }],
            }
            return resp, json.dumps(resp).encode("utf-8"), 200

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            with patch("fetch_routes.request_json", side_effect=fake_request_json):
                result = request_route(
                    endpoint="https://mock-valhalla.local",
                    route_id="direct",
                    route_def=route_def,
                    origin=origin,
                    destination=dest,
                    num_alternates=2,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                )
            self.assertEqual(len(captured_payloads), 1)
            self.assertEqual(captured_payloads[0].get("alternates"), 2)
            self.assertEqual(len(result.get("native_alternates", [])), 1)

    def test_request_route_omits_alternates_when_via_waypoints_present(self):
        """When locations count > 2, alternates parameter is omitted even if num_alternates > 0."""
        coords = decode_polyline(SAMPLE_ENCODED_POLYLINE)
        origin = {"landmark_id": "grand_central_terminal", "landmark_name": "GCT", "lat": coords[0][1], "lon": coords[0][0]}
        dest = {"landmark_id": "bryant_park", "landmark_name": "BP", "lat": coords[-1][1], "lon": coords[-1][0]}
        route_def = {
            "name": "Via Landmark",
            "description": "Via",
            "locations": [
                {"lat": origin["lat"], "lon": origin["lon"]},
                {"lat": 40.7525, "lon": -73.9780},
                {"lat": dest["lat"], "lon": dest["lon"]},
            ],
            "waypoints": [{
                "landmark_id": "one_vanderbilt",
                "landmark_name": "One Vanderbilt",
                "lat": 40.7525,
                "lon": -73.9780,
            }],
        }

        captured_payloads = []
        def fake_request_json(url, payload=None, *args, **kwargs):
            if payload is None and "payload" in kwargs:
                payload = kwargs["payload"]
            captured_payloads.append(dict(payload))
            resp = {
                "trip": {
                    "status": 0,
                    "summary": {"length": 0.6, "time": 360},
                    "legs": [
                        {
                            "shape": _encode_polyline(
                                [
                                    [origin["lon"], origin["lat"]],
                                    [-73.9780, 40.7525],
                                ]
                            )
                        },
                        {
                            "shape": _encode_polyline(
                                [
                                    [-73.9780, 40.7525],
                                    [dest["lon"], dest["lat"]],
                                ]
                            )
                        },
                    ],
                }
            }
            return resp, json.dumps(resp).encode("utf-8"), 200

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            with patch("fetch_routes.request_json", side_effect=fake_request_json):
                result = request_route(
                    endpoint="https://mock-valhalla.local",
                    route_id="alternative_1",
                    route_def=route_def,
                    origin=origin,
                    destination=dest,
                    num_alternates=2,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                )
            self.assertEqual(len(captured_payloads), 1)
            self.assertNotIn("alternates", captured_payloads[0])
            self.assertEqual(result.get("native_alternates", []), [])

    def test_fetch_all_routes_zero_landmark_adopts_two_native_alternatives(self):
        """A journey with zero landmark alternatives requests and adopts 2 native alternatives."""
        candidates = load_landmark_candidates()
        origin_id = "bryant_park"
        dest_id = "stephen_a_schwarzman_building"
        o = candidates[origin_id]
        d = candidates[dest_id]

        direct_coords = [
            [o["lon"], o["lat"]],
            [o["lon"], d["lat"]],
            [d["lon"], d["lat"]],
        ]
        # Alt 1 detours East
        alt1_coords = [
            [o["lon"], o["lat"]],
            [o["lon"] + 0.0015, o["lat"]],
            [o["lon"] + 0.0015, d["lat"]],
            [d["lon"], d["lat"]],
        ]
        # Alt 2 detours West
        alt2_coords = [
            [o["lon"], o["lat"]],
            [o["lon"] - 0.0015, o["lat"]],
            [o["lon"] - 0.0015, d["lat"]],
            [d["lon"], d["lat"]],
        ]

        direct_trip = {
            "status": 0,
            "summary": {"length": 0.25, "time": 180.0},
            "legs": [{"shape": _encode_polyline(direct_coords)}],
        }
        alt1_trip = {
            "status": 0,
            "summary": {"length": 0.35, "time": 250.0},
            "legs": [{"shape": _encode_polyline(alt1_coords)}],
        }
        alt2_trip = {
            "status": 0,
            "summary": {"length": 0.38, "time": 270.0},
            "legs": [{"shape": _encode_polyline(alt2_coords)}],
        }
        valhalla_resp = {
            "trip": direct_trip,
            "alternates": [{"trip": alt1_trip}, {"trip": alt2_trip}],
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            with patch("fetch_routes.request_json", return_value=(valhalla_resp, json.dumps(valhalla_resp).encode("utf-8"), 200)), \
                 patch("fetch_routes.time.sleep", return_value=None):
                results = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=dest_id,
                    endpoint="https://mock-valhalla.local",
                    alternative_strategy="native",
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertIn("direct", results)
            self.assertIn("alternative_1", results)
            self.assertIn("alternative_2", results)
            self.assertEqual(len(results), 3)

            # Check alternative properties
            alt1_props = results["alternative_1"]["feature_collection"]["features"][0]["properties"]
            self.assertEqual(alt1_props["route_id"], "alternative_1")
            self.assertEqual(len(alt1_props["via_waypoints"]), 0)
            self.assertIn("Valhalla", alt1_props["geometry_source"])
            self.assertEqual(alt1_props["distance_m"], 350.0)

            alt2_props = results["alternative_2"]["feature_collection"]["features"][0]["properties"]
            self.assertEqual(alt2_props["route_id"], "alternative_2")
            self.assertEqual(len(alt2_props["via_waypoints"]), 0)
            self.assertIn("Valhalla", alt2_props["geometry_source"])
            self.assertEqual(alt2_props["distance_m"], 380.0)

    def test_fetch_all_routes_preserves_curated_and_slots_native_into_alternative_2(self):
        """Preserves existing curated landmark alternative and assigns native alternate to alternative_2."""
        candidates = load_landmark_candidates()
        # Use a pair with more distance so curated geometry is clearly distinct.
        origin_id = "bryant_park"
        dest_id = "grand_central_terminal"
        o = candidates[origin_id]
        d = candidates[dest_id]

        direct_coords = [
            [o["lon"], o["lat"]],
            [o["lon"] + 0.001, (o["lat"] + d["lat"]) / 2],
            [d["lon"], d["lat"]],
        ]
        # Curated via waypoint clearly North of direct line (~200m offset)
        via_lat = max(o["lat"], d["lat"]) + 0.002
        via_lon = (o["lon"] + d["lon"]) / 2
        curated_coords = [
            [o["lon"], o["lat"]],
            [o["lon"] + 0.0005, o["lat"] + 0.0005],
            [via_lon, via_lat],
            [d["lon"] + 0.0005, d["lat"] + 0.0005],
            [d["lon"], d["lat"]],
        ]
        # Native alt detours South (clearly different from both direct and curated)
        native_coords = [
            [o["lon"], o["lat"]],
            [o["lon"], o["lat"] - 0.002],
            [d["lon"], d["lat"] - 0.002],
            [d["lon"], d["lat"]],
        ]

        # Synthetic via landmark for the curated route definition
        via_lm = {
            "landmark_id": "one_vanderbilt",
            "landmark_name": "One Vanderbilt",
            "lat": via_lat,
            "lon": via_lon,
        }

        direct_trip = {
            "status": 0,
            "summary": {"length": 0.50, "time": 360.0},
            "legs": [{"shape": _encode_polyline(direct_coords)}],
        }
        native_trip = {
            "status": 0,
            "summary": {"length": 0.70, "time": 500.0},
            "legs": [{"shape": _encode_polyline(native_coords)}],
        }
        curated_trip = {
            "status": 0,
            "summary": {"length": 0.80, "time": 580.0},
            "legs": [
                {"shape": _encode_polyline(curated_coords[:3])},
                {"shape": _encode_polyline(curated_coords[2:])},
            ],
        }

        direct_def = {
            "name": "Direct Route",
            "description": "Direct",
            "locations": [
                {"lat": o["lat"], "lon": o["lon"]},
                {"lat": d["lat"], "lon": d["lon"]},
            ],
            "waypoints": [],
        }
        curated_def = {
            "name": "Via One Vanderbilt",
            "description": "Walking route via One Vanderbilt.",
            "locations": [
                {"lat": o["lat"], "lon": o["lon"]},
                {"lat": via_lat, "lon": via_lon},
                {"lat": d["lat"], "lon": d["lon"]},
            ],
            "waypoints": [via_lm],
        }

        def fake_build(orig_id_arg, dest_id_arg, cands, **kwargs):
            if kwargs.get("include_alternatives") is False:
                return {"direct": direct_def}
            return {"direct": direct_def, "alternative_1": curated_def}

        def fake_request_json(url, payload=None, *args, **kwargs):
            if payload is None and "payload" in kwargs:
                payload = kwargs["payload"]
            if len(payload["locations"]) == 2:
                resp = {"trip": direct_trip, "alternates": [{"trip": native_trip}]}
            else:
                resp = {"trip": curated_trip}
            return resp, json.dumps(resp).encode("utf-8"), 200

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            with patch("fetch_routes.build_route_definitions", side_effect=fake_build), \
                 patch("fetch_routes.request_json", side_effect=fake_request_json), \
                 patch("fetch_routes.time.sleep", return_value=None):
                results = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=dest_id,
                    endpoint="https://mock-valhalla.local",
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertIn("direct", results)
            self.assertIn("alternative_1", results)
            self.assertIn("alternative_2", results)

            # alternative_1 must be the curated landmark route
            alt1_props = results["alternative_1"]["feature_collection"]["features"][0]["properties"]
            self.assertEqual(len(alt1_props["via_waypoints"]), 1)
            self.assertEqual(alt1_props["via_waypoints"][0]["landmark_id"], "one_vanderbilt")

            # alternative_2 must be the native alternate
            alt2_props = results["alternative_2"]["feature_collection"]["features"][0]["properties"]
            self.assertEqual(len(alt2_props["via_waypoints"]), 0)
            self.assertIn("Valhalla", alt2_props["geometry_source"])
            self.assertEqual(alt2_props["distance_m"], 700.0)

    def test_native_alternative_rejected_for_duplicate_geometry(self):
        """Native alternate with geometry nearly identical to direct is rejected."""
        candidates = load_landmark_candidates()
        origin_id = "bryant_park"
        dest_id = "stephen_a_schwarzman_building"
        o = candidates[origin_id]
        d = candidates[dest_id]

        direct_coords = [
            [o["lon"], o["lat"]],
            [o["lon"], d["lat"]],
            [d["lon"], d["lat"]],
        ]
        # Duplicate coords (divergence < 35m)
        dup_coords = [
            [o["lon"], o["lat"]],
            [o["lon"] + 0.00005, d["lat"]],
            [d["lon"], d["lat"]],
        ]

        direct_trip = {
            "status": 0,
            "summary": {"length": 0.25, "time": 180.0},
            "legs": [{"shape": _encode_polyline(direct_coords)}],
        }
        dup_trip = {
            "status": 0,
            "summary": {"length": 0.26, "time": 185.0},
            "legs": [{"shape": _encode_polyline(dup_coords)}],
        }
        valhalla_resp = {
            "trip": direct_trip,
            "alternates": [{"trip": dup_trip}],
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            with patch("fetch_routes.request_json", return_value=(valhalla_resp, json.dumps(valhalla_resp).encode("utf-8"), 200)), \
                 patch("fetch_routes.time.sleep", return_value=None):
                results = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=dest_id,
                    endpoint="https://mock-valhalla.local",
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            # Native alternate rejected, only direct accepted
            self.assertIn("direct", results)
            self.assertNotIn("alternative_1", results)

            # Rejection record written
            rejection_file = raw_dir / f"{origin_id}__{dest_id}__alternative_1__rejected.json"
            self.assertTrue(rejection_file.exists())
            rej_data = json.loads(rejection_file.read_text(encoding="utf-8"))
            self.assertEqual(rej_data["reason"], "duplicate_path")

    def test_native_alternative_rejected_for_excessive_detour(self):
        """Native alternate exceeding MAX_ACTUAL_DETOUR_RATIO is rejected."""
        candidates = load_landmark_candidates()
        origin_id = "bryant_park"
        dest_id = "stephen_a_schwarzman_building"
        o = candidates[origin_id]
        d = candidates[dest_id]

        direct_coords = [
            [o["lon"], o["lat"]],
            [o["lon"], d["lat"]],
            [d["lon"], d["lat"]],
        ]
        detour_coords = [
            [o["lon"], o["lat"]],
            [o["lon"] + 0.005, o["lat"]],
            [o["lon"] + 0.005, d["lat"]],
            [d["lon"], d["lat"]],
        ]

        direct_trip = {
            "status": 0,
            "summary": {"length": 0.20, "time": 150.0},
            "legs": [{"shape": _encode_polyline(direct_coords)}],
        }
        detour_trip = {
            "status": 0,
            "summary": {"length": 0.80, "time": 600.0},  # 4x direct distance
            "legs": [{"shape": _encode_polyline(detour_coords)}],
        }
        valhalla_resp = {
            "trip": direct_trip,
            "alternates": [{"trip": detour_trip}],
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            with patch("fetch_routes.request_json", return_value=(valhalla_resp, json.dumps(valhalla_resp).encode("utf-8"), 200)), \
                 patch("fetch_routes.time.sleep", return_value=None):
                results = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=dest_id,
                    endpoint="https://mock-valhalla.local",
                    skip_existing=False,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            # Detour alternate rejected, only direct accepted
            self.assertIn("direct", results)
            self.assertNotIn("alternative_1", results)

            # Rejection record written
            rejection_file = raw_dir / f"{origin_id}__{dest_id}__alternative_1__rejected.json"
            self.assertTrue(rejection_file.exists())
            rej_data = json.loads(rejection_file.read_text(encoding="utf-8"))
            self.assertEqual(rej_data["reason"], "excessive_detour")

    def test_native_alternative_cached_and_idempotent(self):
        """Native alternate GeoJSON is reused from cache, and skip_existing avoids re-fetching."""
        candidates = load_landmark_candidates()
        origin_id = "bryant_park"
        dest_id = "stephen_a_schwarzman_building"
        o = candidates[origin_id]
        d = candidates[dest_id]

        direct_coords = [
            [o["lon"], o["lat"]],
            [o["lon"], d["lat"]],
            [d["lon"], d["lat"]],
        ]
        alt1_coords = [
            [o["lon"], o["lat"]],
            [o["lon"] + 0.0015, o["lat"]],
            [o["lon"] + 0.0015, d["lat"]],
            [d["lon"], d["lat"]],
        ]

        direct_trip = {
            "status": 0,
            "summary": {"length": 0.25, "time": 180.0},
            "legs": [{"shape": _encode_polyline(direct_coords)}],
        }
        alt1_trip = {
            "status": 0,
            "summary": {"length": 0.35, "time": 250.0},
            "legs": [{"shape": _encode_polyline(alt1_coords)}],
        }
        valhalla_resp = {
            "trip": direct_trip,
            "alternates": [{"trip": alt1_trip}],
        }

        with tempfile.TemporaryDirectory() as tmp_routes, tempfile.TemporaryDirectory() as tmp_raw:
            routes_dir, raw_dir = Path(tmp_routes), Path(tmp_raw)
            # Pass 1: Generate route
            with patch("fetch_routes.request_json", return_value=(valhalla_resp, json.dumps(valhalla_resp).encode("utf-8"), 200)), \
                 patch("fetch_routes.time.sleep", return_value=None):
                results1 = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=dest_id,
                    endpoint="https://mock-valhalla.local",
                    skip_existing=True,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertIn("direct", results1)
            self.assertIn("alternative_1", results1)
            self.assertFalse(results1["direct"]["cached"])
            self.assertFalse(results1["alternative_1"]["cached"])

            # Pass 2: skip_existing=True -> must NOT make network requests
            with patch("fetch_routes.request_json", side_effect=AssertionError("Network called when routes cached")):
                results2 = fetch_all_routes(
                    origin_id=origin_id,
                    destination_id=dest_id,
                    endpoint="https://mock-valhalla.local",
                    alternative_strategy="native",
                    skip_existing=True,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    request_delay_s=0,
                )

            self.assertIn("direct", results2)
            self.assertIn("alternative_1", results2)
            self.assertTrue(results2["direct"]["cached"])
            self.assertTrue(results2["alternative_1"]["cached"])

    def test_fetch_all_routes_rejects_unbounded_or_unknown_strategy(self):
        for options in (
            {"alternative_strategy": "arbitrary"},
            {"max_alternatives": MAX_ALTERNATIVES_PER_JOURNEY + 1},
            {"max_alternatives": 0},
            {"max_alternatives": True},
        ):
            with self.subTest(options=options):
                with self.assertRaises(RouteFetchError):
                    fetch_all_routes(
                        origin_id="bryant_park",
                        destination_id="stephen_a_schwarzman_building",
                        **options,
                    )


if __name__ == "__main__":
    unittest.main()
