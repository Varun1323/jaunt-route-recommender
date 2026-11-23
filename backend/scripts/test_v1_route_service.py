"""Unit tests for the cache-independent Jaunt V1 Valhalla route service."""

import unittest
from unittest.mock import patch

from v1_route_service import (
    V1RouteValidationError,
    generate_v1_route,
    validate_waypoint_selection,
)


IDS = {
    "grand_central_terminal",
    "chrysler_building",
    "stephen_a_schwarzman_building",
    "bryant_park",
    "times_square",
    "rockefeller_center",
    "st_patricks_cathedral",
    "museum_of_modern_art",
}


def candidate(landmark_id, lat, lon):
    return {
        "landmark_id": landmark_id,
        "landmark_name": landmark_id.replace("_", " ").title(),
        "lat": lat,
        "lon": lon,
    }


CANDIDATES = {
    "grand_central_terminal": candidate("grand_central_terminal", 40.7521, -73.9773),
    "chrysler_building": candidate("chrysler_building", 40.7516, -73.9755),
    "bryant_park": candidate("bryant_park", 40.7536, -73.9832),
}


class TestValidateWaypointSelection(unittest.TestCase):
    def test_accepts_valid_ordered_waypoints(self):
        validate_waypoint_selection(
            "grand_central_terminal", "bryant_park", ["chrysler_building"],
            allowed_ids=IDS,
        )

    def test_rejects_unknown_waypoint(self):
        with self.assertRaises(V1RouteValidationError):
            validate_waypoint_selection(
                "grand_central_terminal", "bryant_park", ["one_vanderbilt"],
                allowed_ids=IDS,
            )

    def test_rejects_repeated_waypoint(self):
        with self.assertRaises(V1RouteValidationError):
            validate_waypoint_selection(
                "grand_central_terminal", "bryant_park",
                ["chrysler_building", "chrysler_building"], allowed_ids=IDS,
            )

    def test_rejects_endpoint_as_waypoint(self):
        with self.assertRaises(V1RouteValidationError):
            validate_waypoint_selection(
                "grand_central_terminal", "bryant_park",
                ["grand_central_terminal"], allowed_ids=IDS,
            )

    def test_loop_requires_waypoint(self):
        with self.assertRaises(V1RouteValidationError):
            validate_waypoint_selection(
                "grand_central_terminal", "grand_central_terminal", [],
                allowed_ids=IDS,
            )

    def test_loop_accepts_waypoint(self):
        validate_waypoint_selection(
            "grand_central_terminal", "grand_central_terminal",
            ["chrysler_building"], allowed_ids=IDS,
        )


class TestGenerateV1Route(unittest.TestCase):
    @patch("v1_route_service.validate_route_response")
    @patch("v1_route_service.request_json")
    @patch("v1_route_service.create_geojson_feature_collection")
    def test_builds_one_ordered_pedestrian_request(
        self, mock_geojson, mock_request, mock_validate
    ):
        mock_request.return_value = ({"trip": {}}, b"{}", 200)
        mock_validate.return_value = (
            [[-73.9773, 40.7521], [-73.9755, 40.7516], [-73.9832, 40.7536]],
            1200.0,
            900.0,
        )
        mock_geojson.return_value = {"type": "FeatureCollection", "features": []}

        result = generate_v1_route(
            "grand_central_terminal", "bryant_park", ["chrysler_building"],
            candidates=CANDIDATES,
        )

        self.assertEqual(result["type"], "FeatureCollection")
        args = mock_request.call_args.kwargs
        payload = args["payload"]
        self.assertEqual(payload["costing"], "pedestrian")
        self.assertEqual(
            [(p["lat"], p["lon"]) for p in payload["locations"]],
            [
                (40.7521, -73.9773),
                (40.7516, -73.9755),
                (40.7536, -73.9832),
            ],
        )
        mock_geojson.assert_called_once()

    @patch("v1_route_service.request_json")
    def test_does_not_call_valhalla_for_invalid_waypoint(self, mock_request):
        with self.assertRaises(V1RouteValidationError):
            generate_v1_route(
                "grand_central_terminal", "bryant_park", ["one_vanderbilt"],
                candidates=CANDIDATES,
            )
        mock_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
