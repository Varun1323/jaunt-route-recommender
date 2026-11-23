
"""Integration tests for the Jaunt V1 FastAPI endpoint."""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import v1_api


LANDMARKS = [
    {"id": "grand_central_terminal", "name": "Grand Central Terminal", "prototype_selection": True},
    {"id": "chrysler_building", "name": "Chrysler Building", "prototype_selection": True},
    {"id": "stephen_a_schwarzman_building", "name": "New York Public Library", "prototype_selection": True},
    {"id": "bryant_park", "name": "Bryant Park", "prototype_selection": True},
    {"id": "times_square", "name": "Times Square", "prototype_selection": True},
    {"id": "rockefeller_center", "name": "Rockefeller Center", "prototype_selection": True},
    {"id": "st_patricks_cathedral", "name": "St. Patrick's Cathedral", "prototype_selection": True},
    {"id": "museum_of_modern_art", "name": "Museum of Modern Art", "prototype_selection": True},
]


class FakeConfig:
    is_configured = True
    model = "test-model"


class FakeClient:
    pass


def valid_preference():
    class Preference:
        waypoint_ids = ["chrysler_building"]
        primary_preference = "architecture"
        detour_tolerance = "moderate"
        reasoning = "You prefer architecture, so this route includes an architectural stop."

        def to_dict(self):
            return {
                "waypoint_ids": self.waypoint_ids,
                "primary_preference": self.primary_preference,
                "detour_tolerance": self.detour_tolerance,
                "reasoning": self.reasoning,
            }

    return Preference()


def valid_route():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-73.9773, 40.7521],
                        [-73.9755, 40.7516],
                        [-73.9832, 40.7536],
                    ],
                },
                "properties": {
                    "route_id": "jaunt_v1_test",
                    "distance_m": 1200.0,
                    "estimated_duration_s": 900.0,
                    "via_waypoints": [
                        {
                            "landmark_id": "chrysler_building",
                            "landmark_name": "Chrysler Building",
                        }
                    ],
                    "costing": "pedestrian",
                    "verification_status": "prototype_candidate_unverified",
                    "coordinate_limitation_disclosure": "Test disclosure",
                },
            }
        ],
    }


class TestV1RecommendEndpoint(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(v1_api.router)
        self.client = TestClient(app)

        self.landmarks_patch = patch.object(
            v1_api,
            "_load_selectable_landmarks",
            return_value=LANDMARKS,
        )
        self.config_patch = patch.object(
            v1_api,
            "get_openrouter_config",
            return_value=FakeConfig(),
        )
        self.client_patch = patch.object(
            v1_api,
            "OpenRouterClient",
            return_value=FakeClient(),
        )
        self.preference_patch = patch.object(
            v1_api,
            "interpret_v1_preference",
            return_value=valid_preference(),
        )
        self.route_patch = patch.object(
            v1_api,
            "generate_v1_route",
            return_value=valid_route(),
        )

        for patcher in (
            self.landmarks_patch,
            self.config_patch,
            self.client_patch,
            self.preference_patch,
            self.route_patch,
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def request_body(self):
        return {
            "origin_id": "grand_central_terminal",
            "destination_id": "bryant_park",
            "preference": "Show me interesting architecture",
        }

    def test_returns_exactly_one_route(self):
        response = self.client.post(
            "/api/v1/recommend",
            json=self.request_body(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertEqual(payload["route_count"], 1)
        self.assertEqual(payload["route"]["type"], "FeatureCollection")
        self.assertEqual(
            payload["preference"]["waypoint_ids"],
            ["chrysler_building"],
        )
        self.assertIn("reasoning", payload["preference"])
        self.assertTrue(payload["preference"]["reasoning"])
        self.assertEqual(
            payload["route_summary"]["costing"],
            "pedestrian",
        )

    def test_rejects_unknown_origin(self):
        body = self.request_body()
        body["origin_id"] = "one_vanderbilt"

        response = self.client.post("/api/v1/recommend", json=body)

        self.assertEqual(response.status_code, 422)

    def test_rejects_unknown_destination(self):
        body = self.request_body()
        body["destination_id"] = "not_a_landmark"

        response = self.client.post("/api/v1/recommend", json=body)

        self.assertEqual(response.status_code, 422)

    def test_rejects_missing_ai_configuration(self):
        with patch.object(
            v1_api,
            "get_openrouter_config",
            return_value=type(
                "Unconfigured",
                (),
                {"is_configured": False, "model": "test-model"},
            )(),
        ):
            response = self.client.post(
                "/api/v1/recommend",
                json=self.request_body(),
            )

        self.assertEqual(response.status_code, 503)

    def test_returns_error_when_ai_interpretation_fails(self):
        from v1_preference_service import V1PreferenceError

        with patch.object(
            v1_api,
            "interpret_v1_preference",
            side_effect=V1PreferenceError("Invalid model response"),
        ):
            response = self.client.post(
                "/api/v1/recommend",
                json=self.request_body(),
            )

        self.assertEqual(response.status_code, 502)

    def test_returns_error_when_route_generation_fails(self):
        from v1_route_service import V1RouteValidationError

        with patch.object(
            v1_api,
            "generate_v1_route",
            side_effect=V1RouteValidationError("Invalid waypoint"),
        ):
            response = self.client.post(
                "/api/v1/recommend",
                json=self.request_body(),
            )

        self.assertEqual(response.status_code, 422)

    def test_rejects_invalid_route_response(self):
        with patch.object(
            v1_api,
            "generate_v1_route",
            return_value={"type": "FeatureCollection", "features": []},
        ):
            response = self.client.post(
                "/api/v1/recommend",
                json=self.request_body(),
            )

        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
