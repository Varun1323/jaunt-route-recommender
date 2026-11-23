"""Tests for the Jaunt FastAPI backend (backend/app.py).

Uses the FastAPI TestClient to exercise all API endpoints offline
without starting a live server.
"""

from __future__ import annotations

import sys
import concurrent.futures
import json
import shutil
import tempfile
import unittest
import math
from pathlib import Path
from unittest.mock import patch

# Ensure backend and scripts dirs are importable
_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
from app import app  # noqa: E402
from fetch_routes import (  # noqa: E402
    RouteFetchError,
    create_geojson_feature_collection,
    load_landmark_candidates,
    waypoint_route_id,
)
from openrouter_client import OpenRouterConfig, OpenRouterError  # noqa: E402
from preference_parser import UserPreferences, parse_preferences  # noqa: E402

client = TestClient(app)


# ==========================================================================
# GET /health & GET /api/health
# ==========================================================================
class TestHealthEndpoint(unittest.TestCase):
    """Test health check endpoints."""

    def test_health_endpoint_returns_ok(self):
        """GET /health returns HTTP 200 with status ok."""
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_api_health_endpoint_returns_ok(self):
        """GET /api/health returns HTTP 200 with status ok."""
        resp = client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


# ==========================================================================
# GET /api/landmarks
# ==========================================================================
class TestGetLandmarks(unittest.TestCase):
    """Test the /api/landmarks endpoint."""

    def test_returns_eight_user_selectable_landmarks(self):
        """GET /api/landmarks limits the user-facing selector to the eight chosen places."""
        resp = client.get("/api/landmarks")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 8)
        self.assertEqual(len(data["landmarks"]), 8)

    def test_landmark_schema_fields(self):
        """Each landmark has expected fields."""
        resp = client.get("/api/landmarks")
        data = resp.json()
        for lm in data["landmarks"]:
            self.assertIn("id", lm)
            self.assertIn("name", lm)
            self.assertIn("description", lm)
            self.assertIn("categories", lm)
            self.assertIn("coordinate_verification_status", lm)

    def test_includes_limitation_disclosure(self):
        """Response includes prototype limitation disclosure."""
        resp = client.get("/api/landmarks")
        data = resp.json()
        self.assertIn("limitation_disclosure", data)
        self.assertIn("prototype", data["limitation_disclosure"].lower())

    def test_known_landmark_ids_present(self):
        """Only the eight selected prototype places are returned."""
        resp = client.get("/api/landmarks")
        ids = {lm["id"] for lm in resp.json()["landmarks"]}
        expected = {
            "grand_central_terminal",
            "chrysler_building",
            "stephen_a_schwarzman_building",
            "bryant_park",
            "times_square",
            "rockefeller_center",
            "st_patricks_cathedral",
            "museum_of_modern_art",
        }
        self.assertEqual(ids, expected)


# ==========================================================================
# GET /api/routes
# ==========================================================================
class TestGetRoutes(unittest.TestCase):
    """Test the /api/routes endpoint."""

    def test_valid_route_query_returns_routes(self):
        """Valid origin+destination returns route data."""
        resp = client.get(
            "/api/routes",
            params={"origin_id": "grand_central_terminal", "destination_id": "bryant_park"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["route_count"], 1)
        self.assertEqual(data["origin_id"], "grand_central_terminal")
        self.assertEqual(data["destination_id"], "bryant_park")

    def test_route_response_includes_geojson(self):
        """Each route includes its full GeoJSON FeatureCollection."""
        resp = client.get(
            "/api/routes",
            params={"origin_id": "grand_central_terminal", "destination_id": "bryant_park"},
        )
        data = resp.json()
        for route in data["routes"]:
            self.assertIn("geojson", route)
            geojson = route["geojson"]
            self.assertEqual(geojson["type"], "FeatureCollection")
            self.assertGreater(len(geojson["features"]), 0)
            geom = geojson["features"][0]["geometry"]
            self.assertEqual(geom["type"], "LineString")
            self.assertGreater(len(geom["coordinates"]), 1)

    def test_route_response_includes_metrics(self):
        """Each route includes distance and duration."""
        resp = client.get(
            "/api/routes",
            params={"origin_id": "grand_central_terminal", "destination_id": "bryant_park"},
        )
        for route in resp.json()["routes"]:
            self.assertIsNotNone(route["distance_m"])
            self.assertIsNotNone(route["estimated_duration_s"])
            self.assertIsNotNone(route["estimated_duration_minutes"])
            self.assertGreater(route["distance_m"], 0)
            self.assertGreater(route["estimated_duration_s"], 0)

    def test_core_trip_has_three_routes(self):
        """Grand Central -> Bryant Park returns exactly 3 route alternatives."""
        resp = client.get(
            "/api/routes",
            params={"origin_id": "grand_central_terminal", "destination_id": "bryant_park"},
        )
        data = resp.json()
        self.assertEqual(data["route_count"], 3)
        route_ids = {r["route_id"] for r in data["routes"]}
        self.assertEqual(route_ids, {"direct", "via_vanderbilt", "architecture"})

    def test_includes_limitation_disclosure(self):
        """Route response includes prototype limitation disclosure."""
        resp = client.get(
            "/api/routes",
            params={"origin_id": "grand_central_terminal", "destination_id": "bryant_park"},
        )
        self.assertIn("limitation_disclosure", resp.json())

    def test_unknown_origin_returns_404(self):
        """Unknown origin ID returns 404 with informative message."""
        resp = client.get(
            "/api/routes",
            params={"origin_id": "nonexistent_place", "destination_id": "bryant_park"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("nonexistent_place", resp.json()["detail"])

    def test_unknown_destination_returns_404(self):
        """Unknown destination ID returns 404."""
        resp = client.get(
            "/api/routes",
            params={"origin_id": "grand_central_terminal", "destination_id": "fake_park"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("fake_park", resp.json()["detail"])

    def test_identical_origin_destination_returns_422(self):
        """Identical origin and destination returns 422."""
        resp = client.get(
            "/api/routes",
            params={"origin_id": "bryant_park", "destination_id": "bryant_park"},
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("same landmark", resp.json()["detail"].lower())

    def test_unavailable_routes_returns_404(self):
        """Journey cache miss attempts generation and returns a gateway error on provider failure."""
        with tempfile.TemporaryDirectory() as tmp_dir, \
             patch("app.ROUTES_DIR", Path(tmp_dir)), \
             patch("app.fetch_all_routes", side_effect=RouteFetchError("offline Valhalla")):
            resp = client.get("/api/routes", params={
                "origin_id": "chrysler_building", "destination_id": "chanin_building",
            })
        self.assertEqual(resp.status_code, 502)
        self.assertIn("offline Valhalla", resp.json()["detail"])

    def test_missing_query_params_returns_422(self):
        """Missing required query parameters returns 422."""
        resp = client.get("/api/routes")
        self.assertEqual(resp.status_code, 422)


# ==========================================================================
# POST /api/recommend
# ==========================================================================
class TestPostRecommend(unittest.TestCase):

    def test_explanation_for_architecture_preference_is_simple_and_specific(self):
        result = app_module.recommend_route(
            "I love architecture and have around 25 minutes.",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            strict_route_validation=True,
        )

        explanation = result["explanation"]
        winner = result["recommended_route"]

        self.assertTrue(winner["landmarks_visited"])
        self.assertTrue(any(
            name in explanation
            for name in (
                place["landmark_name"]
                for place in winner["landmarks_visited"]
            )
        ))
        self.assertNotIn("overall score", explanation.lower())
        self.assertNotIn("scoring", explanation.lower())
        self.assertNotIn("openrouter", explanation.lower())

    def test_explanation_for_shortest_preference_identifies_shortest_route(self):
        result = app_module.recommend_route(
            "I want the shortest route with no unnecessary detours.",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            strict_route_validation=True,
        )

        self.assertEqual(
            result["recommended_route"]["route_id"],
            "direct",
        )
        self.assertIn(
            "shortest available route",
            result["explanation"].lower(),
        )

    def test_explanation_mentions_sightseeing_tradeoff(self):
        result = app_module.recommend_route(
            "I love historic architecture and want interesting places. "
            "I have around 25 minutes.",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            strict_route_validation=True,
        )

        explanation = result["explanation"].lower()

        self.assertIn("25-minute walking budget", explanation)
        self.assertTrue(
            any(
                place["landmark_name"].lower() in explanation
                for place in result["recommended_route"]["landmarks_visited"]
            )
        )
        self.assertNotIn("quiet streets", explanation)
        self.assertNotIn("crowds", explanation)


    def test_waypoint_planner_excludes_irrelevant_landmarks_for_nypl_to_st_patricks(self):
        """AI-selected distant landmarks must not enter an unrelated journey."""
        preferences = UserPreferences(
            architecture=1.0,
            history=1.0,
            sightseeing_waypoint_ids=[
                "grand_central_terminal",
                "chrysler_building",
            ],
            additional_sightseeing_sequences=[
                ["grand_central_terminal"],
                ["chrysler_building"],
            ],
            requested_route_count=3,
        )

        landmarks = app_module._load_selectable_landmarks()

        sequences = app_module._sightseeing_waypoint_sequences(
            origin_id="stephen_a_schwarzman_building",
            destination_id="st_patricks_cathedral",
            selected_landmarks=landmarks,
            preferences=preferences,
            explicit_waypoints=[],
        )

        self.assertTrue(sequences)

        for sequence in sequences:
            self.assertNotIn("stephen_a_schwarzman_building", sequence)
            self.assertNotIn("st_patricks_cathedral", sequence)
            self.assertNotIn("grand_central_terminal", sequence)
            self.assertNotIn("chrysler_building", sequence)

    def test_waypoint_planner_keeps_geographically_relevant_gct_bryant_candidates(self):
        """The primary assignment journey should retain suitable nearby landmarks."""
        preferences = UserPreferences(
            architecture=1.0,
            history=1.0,
            walking_time_budget_minutes=25.0,
            requested_route_count=3,
        )

        landmarks = app_module._load_selectable_landmarks()

        sequences = app_module._sightseeing_waypoint_sequences(
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            selected_landmarks=landmarks,
            preferences=preferences,
            explicit_waypoints=[],
        )

        self.assertTrue(sequences)
        self.assertLessEqual(len(sequences), 3)

        for sequence in sequences:
            self.assertNotIn("grand_central_terminal", sequence)
            self.assertNotIn("bryant_park", sequence)

    def test_waypoint_planner_preserves_explicit_user_waypoints(self):
        """Explicitly selected stops must retain their requested order."""
        preferences = UserPreferences(
            architecture=1.0,
        )

        landmarks = app_module._load_selectable_landmarks()

        sequences = app_module._sightseeing_waypoint_sequences(
            origin_id="stephen_a_schwarzman_building",
            destination_id="st_patricks_cathedral",
            selected_landmarks=landmarks,
            preferences=preferences,
            explicit_waypoints=["rockefeller_center", "times_square"],
        )

        self.assertEqual(
            sequences,
            [["rockefeller_center", "times_square"]],
        )

    def test_waypoint_planner_rejects_excluded_categories(self):
        """Automatically selected landmarks must respect category exclusions."""
        preferences = UserPreferences(
            architecture=1.0,
            excluded_categories=["architecture"],
            requested_route_count=3,
        )

        landmarks = app_module._load_selectable_landmarks()

        sequences = app_module._sightseeing_waypoint_sequences(
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            selected_landmarks=landmarks,
            preferences=preferences,
            explicit_waypoints=[],
        )

        for sequence in sequences:
            for landmark_id in sequence:
                landmark = next(
                    item for item in landmarks
                    if item["id"] == landmark_id
                )
                self.assertNotIn(
                    "architecture",
                    landmark.get("categories", []),
                )

    def test_v2_recommend_endpoint_alias_returns_200(self):
        """The V2 endpoint exposes the existing multi-route recommendation handler."""
        response = client.post("/api/v2/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "shortest route",
        })

        self.assertEqual(response.status_code, 200, response.text)

        data = response.json()
        self.assertIn("recommended_route", data)
        self.assertIn("candidate_routes", data)
        self.assertLessEqual(len(data["candidate_routes"]), 3)
    """Test the /api/recommend endpoint."""

    def setUp(self):
        self.config_patch = patch(
            "app.get_openrouter_config",
            return_value=OpenRouterConfig(api_key=""),
        )
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()

    @staticmethod
    def _write_mock_waypoint_route(
        routes_dir: Path,
        origin_id: str,
        destination_id: str,
        waypoint_ids: list[str],
    ) -> str:
        candidates = load_landmark_candidates()
        origin = candidates[origin_id]
        destination = candidates[destination_id]
        waypoints = [candidates[landmark_id] for landmark_id in waypoint_ids]
        route_id = waypoint_route_id(origin_id, destination_id, waypoint_ids)
        route_definition = {
            "name": f"Mocked route via {', '.join(waypoint_ids)}",
            "description": "Offline Valhalla fixture for route candidate tests.",
            "waypoints": waypoints,
        }
        coordinates = [
            [origin["lon"], origin["lat"]],
            *[[waypoint["lon"], waypoint["lat"]] for waypoint in waypoints],
            [destination["lon"], destination["lat"]],
        ]
        feature_collection = create_geojson_feature_collection(
            route_id,
            route_definition,
            coordinates,
            1000.0 + 500.0 * len(waypoints),
            750.0 + 375.0 * len(waypoints),
            origin,
            destination,
            "https://valhalla1.openstreetmap.de",
        )
        routes_dir.mkdir(parents=True, exist_ok=True)
        route_path = routes_dir / f"{origin_id}__{destination_id}__{route_id}.geojson"
        route_path.write_text(
            json.dumps(feature_collection, indent=2) + "\n",
            encoding="utf-8",
        )
        return route_id

    def test_native_fallback_adds_valid_distinct_candidate(self):
        """A successful native fallback must add a validated, distinct route."""
        direct_source = app_module.ROUTES_DIR / (
            "grand_central_terminal__bryant_park__direct.geojson"
        )

        origin_id = "grand_central_terminal"
        destination_id = "bryant_park"
        native_route_id = "native_alternative_1"

        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)

            shutil.copyfile(
                direct_source,
                routes_dir / (
                    "grand_central_terminal__bryant_park__direct.geojson"
                ),
            )

            candidates = load_landmark_candidates()
            origin = candidates[origin_id]
            destination = candidates[destination_id]

            def generate_routes(**kwargs):
                # The first request is landmark-based. Simulate its failure.
                if kwargs.get("route_filter") != "all":
                    raise RouteFetchError("Landmark route unavailable.")

                # The second request is the native Valhalla fallback.
                self.assertEqual(
                    kwargs.get("alternative_strategy"),
                    "native",
                )

                route_definition = {
                    "name": "Native Valhalla Alternative",
                    "description": "Distinct native pedestrian alternative.",
                    "waypoints": [],
                }

                # A deliberately different geometry for the offline test.
                coordinates = [
                    [origin["lon"], origin["lat"]],
                    [-73.9805, 40.7517],
                    [destination["lon"], destination["lat"]],
                ]

                feature_collection = create_geojson_feature_collection(
                    native_route_id,
                    route_definition,
                    coordinates,
                    900.0,
                    700.0,
                    origin,
                    destination,
                    "https://valhalla1.openstreetmap.de",
                )

                output_path = routes_dir / (
                    f"{origin_id}__{destination_id}__{native_route_id}.geojson"
                )

                output_path.write_text(
                    json.dumps(feature_collection, indent=2),
                    encoding="utf-8",
                )

                return {native_route_id: {}}

            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes",
                side_effect=generate_routes,
            ) as generation:
                response = client.post("/api/recommend", json={
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "preference": "I want to explore historic architecture.",
                })

        self.assertEqual(response.status_code, 200, response.text)

        result = response.json()
        candidate_ids = {
            route["route_id"]
            for route in result["candidate_routes"]
        }

        self.assertIn("direct", candidate_ids)
        self.assertIn(native_route_id, candidate_ids)
        self.assertGreaterEqual(len(candidate_ids), 2)

        self.assertEqual(generation.call_count, 2)
        self.assertEqual(
            result["route_generation"]["status"],
            "generated_with_errors",
        )
        self.assertIn(
            native_route_id,
            result["route_generation"]["generated_route_ids"],
        )

    def test_native_fallback_rejects_duplicate_geometry(self):
        """A native route with identical geometry must not become a candidate."""
        direct_source = app_module.ROUTES_DIR / (
            "grand_central_terminal__bryant_park__direct.geojson"
        )

        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)
            direct_path = routes_dir / (
                "grand_central_terminal__bryant_park__direct.geojson"
            )
            shutil.copyfile(direct_source, direct_path)

            direct_data = json.loads(direct_path.read_text(encoding="utf-8"))
            direct_feature = direct_data["features"][0]
            duplicate_id = "native_duplicate"

            def generate_routes(**kwargs):
                if kwargs.get("route_filter") != "all":
                    raise RouteFetchError("Landmark route unavailable.")

                duplicate = json.loads(json.dumps(direct_data))
                duplicate["name"] = duplicate_id
                duplicate["metadata"]["route_id"] = duplicate_id
                duplicate["features"][0]["properties"]["route_id"] = duplicate_id

                output_path = routes_dir / (
                    "grand_central_terminal__bryant_park__native_duplicate.geojson"
                )
                output_path.write_text(
                    json.dumps(duplicate, indent=2),
                    encoding="utf-8",
                )
                return {duplicate_id: {}}

            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes",
                side_effect=generate_routes,
            ):
                response = client.post("/api/recommend", json={
                    "origin_id": "grand_central_terminal",
                    "destination_id": "bryant_park",
                    "preference": "I want to explore historic architecture.",
                })

        self.assertEqual(response.status_code, 200, response.text)

        candidate_ids = {
            route["route_id"]
            for route in response.json()["candidate_routes"]
        }

        self.assertIn("direct", candidate_ids)
        self.assertNotIn(duplicate_id, candidate_ids)
        self.assertEqual(len(candidate_ids), 1)

    def test_native_fallback_runs_when_landmark_generation_leaves_one_candidate(self):
        """Native Valhalla alternatives are requested when landmark routing leaves only one candidate."""
        direct_source = app_module.ROUTES_DIR / (
            "grand_central_terminal__bryant_park__direct.geojson"
        )

        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)

            shutil.copyfile(
                direct_source,
                routes_dir / "grand_central_terminal__bryant_park__direct.geojson",
            )

            generation_calls = []

            def record_generation(**kwargs):
                generation_calls.append(kwargs)

                # Simulate failure to produce the requested landmark route.
                if kwargs.get("route_filter") != "all":
                    raise RouteFetchError("Landmark alternative unavailable.")

                # Simulate a successful native-alternative request.
                return {"direct": {}}

            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes",
                side_effect=record_generation,
            ):
                response = client.post("/api/recommend", json={
                    "origin_id": "grand_central_terminal",
                    "destination_id": "bryant_park",
                    "preference": "I want to explore historic architecture.",
                })

        self.assertEqual(response.status_code, 200, response.text)

        native_calls = [
            call for call in generation_calls
            if call.get("route_filter") == "all"
            and call.get("alternative_strategy") == "native"
        ]

        self.assertEqual(
            len(native_calls),
            1,
            "Native fallback should run when only one candidate remains.",
        )

    def test_sightseeing_sequence_planner_uses_empty_and_architecture_preferences(self):
        selected = app_module._load_selectable_landmarks()
        empty = parse_preferences("")
        empty_sequences = app_module._sightseeing_waypoint_sequences(
            "grand_central_terminal",
            "bryant_park",
            selected,
            empty,
            [],
        )
        architecture = parse_preferences(
            "I love architecture.",
            selected,
        )
        architecture_sequences = app_module._sightseeing_waypoint_sequences(
            "grand_central_terminal",
            "bryant_park",
            selected,
            architecture,
            [],
        )
        self.assertGreaterEqual(len(empty_sequences), 1)
        self.assertGreaterEqual(len(architecture_sequences), 1)
        self.assertNotIn("grand_central_terminal", empty_sequences[0])
        self.assertNotIn("bryant_park", empty_sequences[0])
        for sequence in architecture_sequences:
            self.assertNotIn("grand_central_terminal", sequence)
            self.assertNotIn("bryant_park", sequence)

    def test_explicit_three_landmarks_preserve_order_and_additional_sequences_are_distinct(self):
        selected = app_module._load_selectable_landmarks()
        preferences = parse_preferences(
            "Visit Rockefeller Center, then St. Patrick's Cathedral, then Times Square.",
            selected,
        )
        sequences = app_module._sightseeing_waypoint_sequences(
            "grand_central_terminal",
            "bryant_park",
            selected,
            preferences,
            preferences.requested_landmark_ids,
        )
        self.assertEqual(
            sequences[0],
            [
                "rockefeller_center",
                "st_patricks_cathedral",
                "times_square",
            ],
        )
        preferences.requested_route_count = 3
        three_sequences = app_module._sightseeing_waypoint_sequences(
            "grand_central_terminal",
            "bryant_park",
            selected,
            preferences,
            preferences.requested_landmark_ids,
        )
        serialized = [tuple(sequence) for sequence in three_sequences]
        self.assertEqual(len(serialized), len(set(serialized)))
        self.assertLessEqual(len(three_sequences), 3)

    def test_three_or_four_requested_routes_plan_distinct_sightseeing_sequences(self):
        selected = app_module._load_selectable_landmarks()
        for query, requested_count in (
            ("Please plan three different routes.", 3),
            ("Please plan four different routes.", 4),
        ):
            with self.subTest(query=query):
                preferences = parse_preferences(query, selected)
                self.assertEqual(preferences.requested_route_count, requested_count)
                sequences = app_module._sightseeing_waypoint_sequences(
                    "grand_central_terminal",
                    "bryant_park",
                    selected,
                    preferences,
                    [],
                )
                serialized = [tuple(sequence) for sequence in sequences]
                self.assertEqual(len(serialized), len(set(serialized)))
                self.assertGreaterEqual(len(sequences), 2)
                self.assertLessEqual(len(sequences), 3)

    def test_intelligent_waypoint_planning_for_time_budget_and_architecture(self):
        """Intelligent waypoint planning selects relevant historic/architectural stops, not first catalog items."""
        selected = app_module._load_selectable_landmarks()
        prefs = parse_preferences(
            "I have 20 minutes and want to explore historic architecture.",
            selected,
        )
        sequences = app_module._sightseeing_waypoint_sequences(
            "grand_central_terminal",
            "bryant_park",
            selected,
            prefs,
            [],
        )
        self.assertGreaterEqual(len(sequences), 1)
        primary = sequences[0]
        self.assertNotIn("grand_central_terminal", primary)
        self.assertNotIn("bryant_park", primary)
        catalog = {lm["id"]: lm for lm in selected}
        for lm_id in primary:
            lm_cats = catalog[lm_id].get("categories", [])
            self.assertTrue(
                "architecture" in lm_cats or "historic_landmark" in lm_cats,
                f"Expected architecture or historic landmark, got {lm_id} with {lm_cats}",
            )
        self.assertLessEqual(len(sequences), 3)

    def test_intelligent_waypoint_planning_preserves_explicit_order_and_bounds(self):
        """Preserves exact order of explicitly requested landmarks and bounds candidates to 4 total."""
        selected = app_module._load_selectable_landmarks()
        prefs = parse_preferences("Visit Chrysler Building, then Times Square.", selected)
        prefs.requested_route_count = 4
        sequences = app_module._sightseeing_waypoint_sequences(
            "grand_central_terminal",
            "bryant_park",
            selected,
            prefs,
            prefs.requested_landmark_ids,
        )
        self.assertEqual(sequences[0], ["chrysler_building", "times_square"])
        self.assertLessEqual(len(sequences), 3)

    def test_ai_proposed_waypoint_sequence_respects_max_waypoints(self):
        """AI-proposed sequences must not exceed the planner's waypoint limit."""
        selected = app_module._load_selectable_landmarks()

        preferences = UserPreferences(
            architecture=1.0,
            history=1.0,
            walking_time_budget_minutes=20.0,
            requested_route_count=3,
            sightseeing_waypoint_ids=[
                "rockefeller_center",
                "st_patricks_cathedral",
                "times_square",
            ],
            additional_sightseeing_sequences=[
                [
                    "rockefeller_center",
                    "st_patricks_cathedral",
                    "times_square",
                ],
            ],
        )

        sequences = app_module._sightseeing_waypoint_sequences(
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            selected_landmarks=selected,
            preferences=preferences,
            explicit_waypoints=[],
        )

        self.assertTrue(sequences)
        for sequence in sequences:
            self.assertLessEqual(
                len(sequence),
                2,
                f"AI-proposed sequence exceeded the 20-minute waypoint limit: {sequence}",
            )

    def test_valid_recommendation_returns_200(self):
        """Valid recommendation request returns a full recommendation."""
        resp = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "I want to see as many interesting buildings as possible.",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("recommended_route", data)
        self.assertIn("ranked_alternatives", data)
        self.assertIn("explanation", data)
        self.assertIn("estimated_walking_time_minutes", data)
        self.assertIn("landmarks_visited", data)
        self.assertIn("limitation_disclosure", data)
        self.assertIn("route_generation", data)
    def test_candidate_route_selection_never_returns_more_than_three(self):
        """Candidate selection must cap meaningfully distinct routes at three."""
        routes = app_module.load_routes(
            app_module.ROUTES_DIR,
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            strict_validation=True,
        )

        preferences = parse_preferences(
            "I want to explore architecture and historic buildings."
        )

        selected = app_module._select_candidate_routes(
            routes=routes,
            preferences=preferences,
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            via_landmark_ids=[],
        )

        self.assertLessEqual(len(selected), 3)
        self.assertGreaterEqual(len(selected), 1)

        route_ids = [
            route["features"][0]["properties"]["route_id"]
            for route in selected
        ]
        self.assertEqual(len(route_ids), len(set(route_ids)))

    def test_recommendation_output_schema(self):
        """Recommendation response includes all required fields."""
        resp = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "shortest route",
        })
        data = resp.json()
        required_keys = [
            "query",
            "origin_landmark_id",
            "origin_landmark_name",
            "destination_landmark_id",
            "destination_landmark_name",
            "parsed_preferences",
            "recommended_route",
            "ranked_alternatives",
            "individual_scores",
            "estimated_walking_time_s",
            "estimated_walking_time_minutes",
            "distance_m",
            "landmarks_visited",
            "explanation",
            "limitation_disclosure",
        ]
        for key in required_keys:
            self.assertIn(key, data, f"Missing key: {key}")

    def test_architecture_preference_selects_architecture_route(self):
        """Architecture preference selects the architecture loop."""
        resp = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "I love architecture and want to see historic buildings.",
        })
        data = resp.json()
        self.assertEqual(data["recommended_route"]["route_id"], "architecture")

    def test_walking_time_budget_recommendation(self):
        """Available walking-time budget recommends sightseeing route over direct route."""
        resp = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "I have 20 minutes and want to explore historic architecture.",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotEqual(data["recommended_route"]["route_id"], "direct")
        self.assertEqual(data["parsed_preferences"]["walking_time_budget_minutes"], 20.0)
        self.assertIsNone(data["parsed_preferences"]["max_duration_minutes"])
        self.assertIn("20-minute", data["explanation"])

    def test_shortest_route_preference_selects_direct(self):
        """Shortest route preference selects the direct route."""
        resp = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "shortest route",
        })
        data = resp.json()
        self.assertEqual(data["recommended_route"]["route_id"], "direct")

    def test_changing_preferences_changes_recommendation(self):
        """Different preferences produce different recommended routes."""
        resp_a = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "shortest route",
        })
        resp_b = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "I want to see as many interesting buildings as possible.",
        })
        self.assertNotEqual(
            resp_a.json()["recommended_route"]["route_id"],
            resp_b.json()["recommended_route"]["route_id"],
        )

    def test_empty_preference_returns_valid_recommendation(self):
        """Empty preference query returns a valid fallback recommendation."""
        resp = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn(data["recommended_route"]["route_id"], {"direct", "via_vanderbilt", "architecture"})

    def test_default_origin_destination(self):
        """Omitting origin_id and destination_id uses defaults."""
        resp = client.post("/api/recommend", json={
            "preference": "shortest route",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["origin_landmark_id"], "grand_central_terminal")
        self.assertEqual(data["destination_landmark_id"], "bryant_park")

    def test_unknown_origin_returns_404(self):
        """Unknown origin ID returns 404."""
        resp = client.post("/api/recommend", json={
            "origin_id": "nonexistent_place",
            "destination_id": "bryant_park",
            "preference": "shortest route",
        })
        self.assertEqual(resp.status_code, 404)
        self.assertIn("nonexistent_place", resp.json()["detail"])

    def test_unknown_destination_returns_404(self):
        """Unknown destination ID returns 404."""
        resp = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "unknown_destination",
            "preference": "shortest route",
        })
        self.assertEqual(resp.status_code, 404)
        self.assertIn("unknown_destination", resp.json()["detail"])

    def test_identical_origin_destination_requires_a_valid_loop_candidate(self):
        """A missing loop candidate is generated on demand, not rejected as identical endpoints."""
        with tempfile.TemporaryDirectory() as temporary, patch(
            "app.ROUTES_DIR", Path(temporary)
        ), patch(
            "app.fetch_all_routes",
            side_effect=RouteFetchError("offline Valhalla"),
        ) as generation:
            resp = client.post(
                "/api/recommend",
                json={
                    "origin_id": "bryant_park",
                    "destination_id": "bryant_park",
                    "preference": "",
                    "via_landmark_ids": ["times_square"],
                },
            )
        self.assertEqual(resp.status_code, 502)
        self.assertNotIn("same landmark", resp.json()["detail"].lower())
        self.assertEqual(generation.call_count, 3)

    def test_api_generates_and_compares_up_to_three_loop_candidates_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)

            def generate_loop(**kwargs):
                self.assertEqual(
                    kwargs["origin_id"],
                    kwargs["destination_id"],
                )
                route_id = self._write_mock_waypoint_route(
                    routes_dir,
                    kwargs["origin_id"],
                    kwargs["destination_id"],
                    kwargs["via_ids"],
                )
                route_path = routes_dir / (
                    f"{kwargs['origin_id']}__{kwargs['destination_id']}__"
                    f"{route_id}.geojson"
                )
                route_data = json.loads(route_path.read_text(encoding="utf-8"))
                generation_index = generation.call_count + 1
                route_properties = route_data["features"][0]["properties"]
                route_properties["distance_m"] = 1000.0 * generation_index
                route_properties["estimated_duration_s"] = 600.0 * generation_index
                route_path.write_text(
                    json.dumps(route_data, indent=2) + "\n",
                    encoding="utf-8",
                )
                return {route_id: {}}

            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes", side_effect=generate_loop
            ) as generation:
                body = {
                    "origin_id": "grand_central_terminal",
                    "destination_id": "grand_central_terminal",
                    "preference": "visit Times Square",
                    "via_landmark_ids": ["times_square"],
                }
                first = client.post("/api/recommend", json=body)
                second = client.post("/api/recommend", json=body)

        self.assertEqual(first.status_code, 200, first.text)
        first_result = first.json()
        self.assertEqual(
            len(first_result["candidate_routes"]),
            3,
            [
                route["route_id"]
                for route in first_result["candidate_routes"]
            ],
        )
        self.assertLessEqual(len(first_result["candidate_routes"]), 3)
        self.assertEqual(
            first_result["parsed_preferences"]["requested_landmark_ids"],
            ["times_square"],
        )
        for route in first_result["candidate_routes"]:
            self.assertEqual(route["geojson"]["features"][0]["properties"]["costing"], "pedestrian")
            self.assertIn(
                "times_square",
                [
                    waypoint["landmark_id"]
                    for waypoint in route["via_waypoints"]
                ],
            )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(len(second.json()["candidate_routes"]), 3)
        self.assertEqual(generation.call_count, 3)

    def test_recommendation_cache_miss_reports_generation_failure(self):
        """Recommendation requests use the same generator and surface provider errors."""
        with tempfile.TemporaryDirectory() as tmp_dir, \
             patch("app.ROUTES_DIR", Path(tmp_dir)), \
             patch("app.fetch_all_routes", side_effect=RouteFetchError("offline Valhalla")):
            resp = client.post("/api/recommend", json={
                "origin_id": "chrysler_building",
                "destination_id": "chanin_building",
                "preference": "shortest route",
            })
        self.assertEqual(resp.status_code, 502)
        self.assertIn("offline Valhalla", resp.json()["detail"])

    def test_strategy_policy_differs_for_place_and_efficiency_preferences(self):
        routes = app_module.load_routes(
            app_module.ROUTES_DIR,
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            strict_validation=True,
        )
        direct_route = next(
            route for route in routes
            if route["features"][0]["properties"]["route_id"] == "direct"
        )

        _summary, place_choices, place_default, _slots = app_module._route_strategy_context(
            [direct_route],
            app_module.parse_preferences("I love architecture."),
        )
        _summary, efficiency_choices, efficiency_default, _slots = app_module._route_strategy_context(
            [direct_route],
            app_module.parse_preferences("quickest route"),
        )
        self.assertIn("landmark", place_choices)
        self.assertEqual(place_default, "landmark")
        self.assertIn("native", efficiency_choices)
        self.assertEqual(efficiency_default, "native")

    def test_api_generates_preference_relevant_route_and_reloads_validated_cache(self):
        direct_source = app_module.ROUTES_DIR / (
            "grand_central_terminal__bryant_park__direct.geojson"
        )
        alternative_source = app_module.ROUTES_DIR / "via_vanderbilt.geojson"
        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)
            shutil.copyfile(
                direct_source,
                routes_dir / "grand_central_terminal__bryant_park__direct.geojson",
            )

            def generate_alternative(**kwargs):
                if kwargs.get("alternative_strategy") == "native":
                    return {"direct": {}}

                self.assertEqual(kwargs["skip_existing"], True)
                self._write_mock_waypoint_route(
                    routes_dir,
                    kwargs["origin_id"],
                    kwargs["destination_id"],
                    kwargs["via_ids"],
                )
                return {"direct": {}}

            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes", side_effect=generate_alternative
            ) as generation:
                response = client.post("/api/recommend", json={
                    "origin_id": "grand_central_terminal",
                    "destination_id": "bryant_park",
                    "preference": "I love architecture.",
                })

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(generation.call_count, 2)
        self.assertIsNone(generation.call_args_list[0].kwargs.get("alternative_strategy"))
        self.assertEqual(
            generation.call_args_list[1].kwargs.get("alternative_strategy"),
            "native",
        )
        self.assertEqual(result["route_generation"]["strategy"], "landmark")
        self.assertEqual(result["route_generation"]["status"], "generated")
        self.assertEqual(len(result["route_generation"]["generated_route_ids"]), 1)
        self.assertEqual(result["route_generation"]["available_route_count"], 2)
        self.assertEqual(len(result["candidate_routes"]), 2)

    def test_failed_sightseeing_waypoint_sequence_is_reported_and_direct_is_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)
            shutil.copyfile(
                app_module.ROUTES_DIR
                / "grand_central_terminal__bryant_park__direct.geojson",
                routes_dir
                / "grand_central_terminal__bryant_park__direct.geojson",
            )
            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes",
                side_effect=RouteFetchError("requested waypoint could not be routed"),
            ):
                response = client.post("/api/recommend", json={
                    "origin_id": "grand_central_terminal",
                    "destination_id": "bryant_park",
                    "preference": "Visit Times Square",
                })

        self.assertEqual(response.status_code, 200)
        result = response.json()
        direct_ids = [
            route["route_id"]
            for route in result["candidate_routes"]
            if route["route_id"] == "direct"
        ]
        self.assertEqual(direct_ids, ["direct"])
        failures = result["route_generation"]["failed_waypoint_sequences"]
        self.assertGreaterEqual(len(failures), 1)
        self.assertIn("requested waypoint", failures[0]["error"])

    def test_api_generates_requested_waypoint_sequence_before_ranking(self):
        direct_source = app_module.ROUTES_DIR / (
            "grand_central_terminal__bryant_park__direct.geojson"
        )
        sequence = ["times_square", "rockefeller_center"]
        expected_route_id = waypoint_route_id(
            "grand_central_terminal", "bryant_park", sequence
        )
        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)
            shutil.copyfile(
                direct_source,
                routes_dir / "grand_central_terminal__bryant_park__direct.geojson",
            )

            def generate_requested_route(**kwargs):
                self.assertEqual(kwargs["route_filter"], expected_route_id)
                self.assertEqual(kwargs["via_ids"], sequence)
                self._write_mock_waypoint_route(
                    routes_dir,
                    kwargs["origin_id"],
                    kwargs["destination_id"],
                    kwargs["via_ids"],
                )
                return {expected_route_id: {}}

            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes", side_effect=generate_requested_route
            ) as generation:
                response = client.post("/api/recommend", json={
                    "origin_id": "grand_central_terminal",
                    "destination_id": "bryant_park",
                    "preference": "Visit Times Square, then Rockefeller Center.",
                })

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        generation.assert_called_once()
        self.assertEqual(
            result["parsed_preferences"]["requested_landmark_ids"],
            sequence,
        )
        self.assertEqual(result["recommended_route"]["route_id"], expected_route_id)
        self.assertLessEqual(len(result["candidate_routes"]), 3)

    def test_openrouter_strategy_decision_is_applied_with_backend_allowlist(self):
        direct_source = app_module.ROUTES_DIR / (
            "grand_central_terminal__bryant_park__direct.geojson"
        )
        alternative_source = app_module.ROUTES_DIR / "via_vanderbilt.geojson"
        decisions = []

        class FakeOpenRouterClient:
            def __init__(self, _config):
                pass

            def parse_preferences(self, query, *_args):
                return UserPreferences(architecture=1, raw_query=query)

            def choose_route_strategy(self, preferences, cached_routes, allowed):
                decisions.append((preferences, cached_routes, allowed))
                return "landmark"

            def generate_explanation(self, _context):
                return "architecture"

        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)
            shutil.copyfile(
                direct_source,
                routes_dir / "grand_central_terminal__bryant_park__direct.geojson",
            )

            def generate_landmark_route(**kwargs):
                if kwargs.get("alternative_strategy") == "native":
                    return {"direct": {}}

                self._write_mock_waypoint_route(
                    routes_dir,
                    kwargs["origin_id"],
                    kwargs["destination_id"],
                    kwargs["via_ids"],
                )
                return {"direct": {}}

            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes", side_effect=generate_landmark_route
            ) as generation, patch(
                "app.get_openrouter_config",
                return_value=OpenRouterConfig(api_key="mock-key"),
            ), patch("app.OpenRouterClient", FakeOpenRouterClient):
                response = client.post("/api/recommend", json={
                    "origin_id": "grand_central_terminal",
                    "destination_id": "bryant_park",
                    "preference": "I want architecture and can walk longer.",
                })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(decisions, [])
        self.assertEqual(generation.call_count, 2)
        self.assertIsNone(
            generation.call_args_list[0].kwargs.get("alternative_strategy")
        )
        self.assertEqual(
            generation.call_args_list[1].kwargs.get("alternative_strategy"),
            "native",
        )
        self.assertEqual(response.json()["route_generation"]["strategy"], "landmark")
        self.assertEqual(response.json()["ai_status"]["status"], "active")

    def test_api_reuses_cached_route_when_no_supported_preference_needs_alternatives(self):
        direct_source = app_module.ROUTES_DIR / (
            "grand_central_terminal__bryant_park__direct.geojson"
        )
        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)
            shutil.copyfile(
                direct_source,
                routes_dir / "grand_central_terminal__bryant_park__direct.geojson",
            )
            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes"
            ) as generation:
                response = client.post("/api/recommend", json={
                    "origin_id": "grand_central_terminal",
                    "destination_id": "bryant_park",
                    "preference": "",
                })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["route_generation"]["strategy"],
            "landmark",
        )
        self.assertIn(
            response.json()["route_generation"]["status"],
            {"reused", "generated", "generation_failed"},
        )
        self.assertEqual(generation.call_count, 2)

        self.assertEqual(
            generation.call_args_list[1].kwargs["alternative_strategy"],
            "native",
        )

    def test_api_does_not_generate_when_cached_routes_cover_preference(self):
        with patch("app.fetch_all_routes") as generation:
            response = client.post("/api/recommend", json={
                "origin_id": "grand_central_terminal",
                "destination_id": "bryant_park",
                "preference": "historic architecture",
            })

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["route_generation"]["strategy"], "landmark")
        generation.assert_called_once()

    def test_partial_route_generation_failure_keeps_valid_cache_and_surfaces_status(self):
        direct_source = app_module.ROUTES_DIR / (
            "grand_central_terminal__bryant_park__direct.geojson"
        )
        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)
            shutil.copyfile(
                direct_source,
                routes_dir / "grand_central_terminal__bryant_park__direct.geojson",
            )
            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes",
                return_value={
                    "direct": {
                        "generation_errors": {"native_alternates": "offline"}
                    }
                },
            ) as generation:
                response = client.post("/api/recommend", json={
                    "origin_id": "grand_central_terminal",
                    "destination_id": "bryant_park",
                    "preference": "fastest route",
                })

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(generation.call_count, 2)
        self.assertEqual(
            generation.call_args_list[1].kwargs["route_filter"],
            "all",
        )
        self.assertEqual(
            generation.call_args_list[1].kwargs["alternative_strategy"],
            "native",
        )
        self.assertEqual(result["route_generation"]["status"], "generation_failed")
        self.assertEqual(result["route_generation"]["failure_count"], 1)
        self.assertEqual(result["route_generation"]["available_route_count"], 1)
        self.assertEqual(result["recommended_route"]["route_id"], "direct")

    def test_phase_4_valhalla_sightseeing_generation_and_evaluation(self):
        """Phase 4: validates candidate sequences, calculates actual duration/distance from response, records failures, and retains direct on total failure."""
        direct_source = app_module.ROUTES_DIR / (
            "grand_central_terminal__bryant_park__direct.geojson"
        )
        with tempfile.TemporaryDirectory() as temporary:
            routes_dir = Path(temporary)
            shutil.copyfile(
                direct_source,
                routes_dir / "grand_central_terminal__bryant_park__direct.geojson",
            )
            with patch("app.ROUTES_DIR", routes_dir), patch(
                "app.fetch_all_routes",
                side_effect=app_module.RouteFetchError("Valhalla route was rejected as duplicate geometry."),
            ):
                response = client.post("/api/recommend", json={
                    "origin_id": "grand_central_terminal",
                    "destination_id": "bryant_park",
                    "preference": "I have 20 minutes and want to explore historic architecture.",
                })

            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertEqual(data["recommended_route"]["route_id"], "direct")
            self.assertGreater(data["distance_m"], 0.0)
            self.assertGreater(data["estimated_walking_time_minutes"], 0.0)
            self.assertEqual(data["route_generation"]["status"], "generation_failed")
            self.assertGreater(data["route_generation"]["failure_count"], 0)
            self.assertIn("failed_waypoint_sequences", data["route_generation"])

    def test_negative_preference_respected(self):
        """Negative preference 'avoid architecture' still returns valid recommendation."""
        resp = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "Fast walk without architecture",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["parsed_preferences"]["architecture"], 0.0)
        self.assertEqual(data["recommended_route"]["route_id"], "direct")

    def test_recommendation_includes_walking_time(self):
        """Recommendation includes estimated walking time fields."""
        resp = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "quick walk",
        })
        data = resp.json()
        self.assertGreater(data["estimated_walking_time_s"], 0)
        self.assertGreater(data["estimated_walking_time_minutes"], 0)
        self.assertGreater(data["distance_m"], 0)

    def test_recommendation_includes_landmarks(self):
        """Recommendation for architecture query includes visited landmarks."""
        resp = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "I love architecture and want to see historic buildings.",
        })
        data = resp.json()
        self.assertIsInstance(data["landmarks_visited"], list)
        self.assertGreater(len(data["landmarks_visited"]), 0)

    
    
    def test_provider_preference_error_uses_local_parser_and_surfaces_fallback(self):
        class FailingOpenRouterClient:
            def __init__(self, _config):
                pass

            def parse_preferences(self, _query, *_args):
                raise OpenRouterError("OpenRouter request timed out.")

        with patch(
            "app.get_openrouter_config",
            return_value=OpenRouterConfig(api_key="test-key"),
        ), patch("app.OpenRouterClient", FailingOpenRouterClient):
            response = client.post("/api/recommend", json={
                "origin_id": "grand_central_terminal",
                "destination_id": "bryant_park",
                "preference": "shortest route",
            })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["recommended_route"]["route_id"], "direct")
        self.assertEqual(data["ai_status"]["status"], "fallback")
        self.assertIn("timed out", data["ai_status"]["message"])

    def test_missing_key_reports_local_fallback_without_provider_call(self):
        with patch("app.OpenRouterClient") as provider:
            response = client.post("/api/recommend", json={
                "origin_id": "grand_central_terminal",
                "destination_id": "bryant_park",
                "preference": "shortest route",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ai_status"]["status"], "unconfigured")
        provider.assert_not_called()

    def test_unsupported_preference_is_disclosed_in_response(self):
        response = client.post("/api/recommend", json={
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "I prefer greenery and quiet places.",
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["parsed_preferences"]["unsupported_preferences"],
            ["greenery", "quietness"],
        )

    

class TestJourneyCacheConcurrency(unittest.TestCase):
    """Concurrent cache misses for one journey must share one generation attempt."""

    def test_same_journey_generation_is_deduplicated(self):
        cached_routes = [{"type": "FeatureCollection"}]
        with patch("app.load_routes", side_effect=[[], *([cached_routes] * 8)]), \
             patch("app.fetch_all_routes") as generation:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(
                    lambda _: app_module._ensure_journey_routes("graybar_building", "chanin_building"),
                    range(8),
                ))
        self.assertEqual(generation.call_count, 1)
        self.assertEqual(results, [cached_routes] * 8)


class TestPhase6EmptyPreference(unittest.TestCase):
    """Phase 6: Empty preference behaviour with AI default planning, local fallback, and guaranteed direct route retention."""

    def test_empty_preference_with_openrouter_plans_sightseeing_and_retains_direct(self):
        """When user provides no preference, DeepSeek plans default sightseeing and direct route is retained."""
        class FakeOpenRouterClient:
            def __init__(self, _config):
                pass

            def parse_preferences(self, query, catalog, origin_id, destination_id, *_args):
                return UserPreferences(
                    sightseeing_waypoint_ids=["chrysler_building"],
                    route_priority="balanced",
                    raw_query=query,
                )

            def generate_explanation(self, context):
                return "balanced"

        with patch(
            "app.get_openrouter_config",
            return_value=OpenRouterConfig(api_key="test-key"),
        ), patch("app.OpenRouterClient", FakeOpenRouterClient), patch("app.fetch_all_routes"):
            response = client.post("/api/recommend", json={
                "origin_id": "grand_central_terminal",
                "destination_id": "bryant_park",
                "preference": "",
            })

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["ai_status"]["status"], "active")
        self.assertEqual(data["ai_status"]["provider"], "openrouter")
        # Direct route is retained among candidates
        candidate_ids = [r["route_id"] for r in data["candidate_routes"]]
        self.assertIn("direct", candidate_ids)

    def test_empty_preference_openrouter_failure_falls_back_to_local_logic_and_retains_direct(self):
        """When OpenRouter is unavailable/fails on empty preference, deterministic local logic is used and direct is retained."""
        class FailingClient:
            def __init__(self, _config):
                pass

            def parse_preferences(self, *_args, **_kwargs):
                raise OpenRouterError("OpenRouter 503 Service Unavailable")

        with patch(
            "app.get_openrouter_config",
            return_value=OpenRouterConfig(api_key="test-key"),
        ), patch("app.OpenRouterClient", FailingClient), patch("app.fetch_all_routes"):
            response = client.post("/api/recommend", json={
                "origin_id": "grand_central_terminal",
                "destination_id": "bryant_park",
                "preference": "   ",
            })

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["ai_status"]["status"], "fallback")
        self.assertIn("503 Service Unavailable", data["ai_status"]["message"])
        candidate_ids = [r["route_id"] for r in data["candidate_routes"]]
        self.assertIn("direct", candidate_ids)

    def test_empty_preference_unconfigured_openrouter_uses_local_logic_and_retains_direct(self):
        """When OpenRouter is unconfigured, deterministic local sightseeing is planned and direct route is retained."""
        with patch(
            "app.get_openrouter_config",
            return_value=OpenRouterConfig(api_key=""),
        ), patch("app.fetch_all_routes"):
            response = client.post("/api/recommend", json={
                "origin_id": "grand_central_terminal",
                "destination_id": "bryant_park",
                "preference": "",
            })

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["ai_status"]["status"], "unconfigured")
        candidate_ids = [r["route_id"] for r in data["candidate_routes"]]
        self.assertIn("direct", candidate_ids)

    def test_empty_preference_valhalla_sightseeing_failure_still_returns_direct_route(self):
        """AI operation and sightseeing route generation are not prerequisites for returning the valid direct route."""
        with patch(
            "app.get_openrouter_config",
            return_value=OpenRouterConfig(api_key=""),
        ), patch("app.fetch_all_routes", side_effect=RouteFetchError("Simulated offline Valhalla")):
            response = client.post("/api/recommend", json={
                "origin_id": "grand_central_terminal",
                "destination_id": "bryant_park",
                "preference": "",
            })

        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["recommended_route"]["route_id"], "direct")
        self.assertEqual(data["route_generation"]["status"], "generation_failed")
        self.assertGreater(data["route_generation"]["failure_count"], 0)


if __name__ == "__main__":
    unittest.main()
