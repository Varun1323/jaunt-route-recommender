"""Unit tests for Jaunt V1 DeepSeek intent contract."""

import unittest

from v1_preference_service import (
    V1PreferenceError,
    interpret_v1_preference,
    validate_v1_preference,
)


LANDMARKS = [
    {"id": "grand_central_terminal", "name": "Grand Central Terminal", "prototype_selection": True},
    {"id": "chrysler_building", "name": "Chrysler Building", "prototype_selection": True},
    {"id": "bryant_park", "name": "Bryant Park", "prototype_selection": True},
    {"id": "times_square", "name": "Times Square", "prototype_selection": True},
    {"id": "rockefeller_center", "name": "Rockefeller Center", "prototype_selection": True},
    {"id": "stephen_a_schwarzman_building", "name": "NYPL", "prototype_selection": True},
    {"id": "st_patricks_cathedral", "name": "St. Patrick's Cathedral", "prototype_selection": True},
    {"id": "museum_of_modern_art", "name": "MoMA", "prototype_selection": True},
    {"id": "one_vanderbilt", "name": "One Vanderbilt", "prototype_selection": False},
]


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.messages = None

    def _chat_json(self, messages):
        self.messages = messages
        return self.response


class TestValidateV1Preference(unittest.TestCase):
    def valid(self):
        return {
            "waypoint_ids": ["chrysler_building", "rockefeller_center"],
            "primary_preference": "architecture",
            "detour_tolerance": "moderate",
            "reasoning": "You prefer architecture, so this route includes selected architectural landmarks.",
        }

        def test_accepts_valid_ordered_waypoints(self):
            result = validate_v1_preference(
                self.valid(),
                origin_id="grand_central_terminal",
                destination_id="bryant_park",
                available_landmarks=LANDMARKS,
            )
            self.assertEqual(
                result.waypoint_ids,
                ["chrysler_building", "rockefeller_center"],
            )
            self.assertEqual(result.primary_preference, "architecture")

    def test_accepts_direct_route_without_waypoints(self):
        payload = self.valid()
        payload["waypoint_ids"] = []
        result = validate_v1_preference(
            payload,
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            available_landmarks=LANDMARKS,
        )
        self.assertEqual(result.waypoint_ids, [])

    def test_rejects_unknown_or_nonselectable_id(self):
        for landmark_id in ("not_real", "one_vanderbilt"):
            payload = self.valid()
            payload["waypoint_ids"] = [landmark_id]
            with self.subTest(landmark_id=landmark_id):
                with self.assertRaises(V1PreferenceError):
                    validate_v1_preference(
                        payload,
                        origin_id="grand_central_terminal",
                        destination_id="bryant_park",
                        available_landmarks=LANDMARKS,
                    )

    def test_rejects_duplicate_waypoints(self):
        payload = self.valid()
        payload["waypoint_ids"] = ["chrysler_building", "chrysler_building"]
        with self.assertRaises(V1PreferenceError):
            validate_v1_preference(
                payload, origin_id="grand_central_terminal",
                destination_id="bryant_park", available_landmarks=LANDMARKS,
            )

    def test_rejects_endpoint_as_waypoint(self):
        payload = self.valid()
        payload["waypoint_ids"] = ["grand_central_terminal"]
        with self.assertRaises(V1PreferenceError):
            validate_v1_preference(
                payload, origin_id="grand_central_terminal",
                destination_id="bryant_park", available_landmarks=LANDMARKS,
            )

    def test_requires_waypoint_for_loop(self):
        payload = self.valid()
        payload["waypoint_ids"] = []
        with self.assertRaises(V1PreferenceError):
            validate_v1_preference(
                payload, origin_id="bryant_park",
                destination_id="bryant_park", available_landmarks=LANDMARKS,
            )

    def test_rejects_extra_or_missing_fields(self):
        payload = self.valid()
        payload["distance_m"] = 100
        with self.assertRaises(V1PreferenceError):
            validate_v1_preference(
                payload, origin_id="grand_central_terminal",
                destination_id="bryant_park", available_landmarks=LANDMARKS,
            )

    def test_rejects_invalid_preference_enums(self):
        payload = self.valid()
        payload["detour_tolerance"] = "huge"
        with self.assertRaises(V1PreferenceError):
            validate_v1_preference(
                payload, origin_id="grand_central_terminal",
                destination_id="bryant_park", available_landmarks=LANDMARKS,
            )

    def test_accepts_valid_reasoning(self):
        result = validate_v1_preference(
            self.valid(),
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            available_landmarks=LANDMARKS,
        )
        self.assertTrue(result.reasoning)
        self.assertLessEqual(len(result.reasoning), 500)

    def test_rejects_missing_reasoning(self):
        payload = self.valid()
        del payload["reasoning"]

        with self.assertRaises(V1PreferenceError):
            validate_v1_preference(
                payload,
                origin_id="grand_central_terminal",
                destination_id="bryant_park",
                available_landmarks=LANDMARKS,
            )

    def test_rejects_empty_reasoning(self):
        payload = self.valid()
        payload["reasoning"] = "   "

        with self.assertRaises(V1PreferenceError):
            validate_v1_preference(
                payload,
                origin_id="grand_central_terminal",
                destination_id="bryant_park",
                available_landmarks=LANDMARKS,
            )

    def test_rejects_reasoning_over_500_characters(self):
        payload = self.valid()
        payload["reasoning"] = "x" * 501

        with self.assertRaises(V1PreferenceError):
            validate_v1_preference(
                payload,
                origin_id="grand_central_terminal",
                destination_id="bryant_park",
                available_landmarks=LANDMARKS,
            )
class TestInterpretV1Preference(unittest.TestCase):
    def test_sends_catalogue_and_returns_validated_result(self):
        response = {
            "waypoint_ids": ["chrysler_building"],
            "primary_preference": "architecture",
            "detour_tolerance": "moderate",
            "reasoning": "You prefer architecture, so this route includes selected architectural landmarks.",
        }
        client = FakeClient(response)
        result = interpret_v1_preference(
            "I want to see Art Deco architecture",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            available_landmarks=LANDMARKS,
            client=client,
        )
        self.assertEqual(result.waypoint_ids, ["chrysler_building"])
        self.assertIn("selectable_landmarks", client.messages[1]["content"])
        self.assertNotIn("one_vanderbilt", client.messages[1]["content"])

    def test_provider_failure_is_wrapped(self):
        class BrokenClient:
            def _chat_json(self, messages):
                raise RuntimeError("provider unavailable")

        with self.assertRaises(V1PreferenceError):
            interpret_v1_preference(
                "architecture",
                origin_id="grand_central_terminal",
                destination_id="bryant_park",
                available_landmarks=LANDMARKS,
                client=BrokenClient(),
            )


if __name__ == "__main__":
    unittest.main()
