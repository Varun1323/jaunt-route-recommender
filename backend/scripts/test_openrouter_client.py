"""Offline tests for OpenRouter configuration and API behavior."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from openrouter_client import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OpenRouterClient,
    OpenRouterConfig,
    OpenRouterError,
    get_openrouter_config,
)


class MockResponse:
    def __init__(self, value: dict):
        self.content = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.content


def completion(content: str) -> MockResponse:
    return MockResponse({
        "choices": [{"message": {"content": content}}],
    })


class TestOpenRouterConfiguration(unittest.TestCase):
    def test_reads_backend_env_file_and_keeps_api_key_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "OPENROUTER_API_KEY=\n"
                "OPENROUTER_MODEL=deepseek/deepseek-v4-flash\n"
                "OPENROUTER_BASE_URL=https://openrouter.ai/api/v1\n",
                encoding="utf-8",
            )
            config = get_openrouter_config(env_file, environ={})

        self.assertEqual(config.api_key, "")
        self.assertFalse(config.is_configured)
        self.assertEqual(config.model, DEFAULT_MODEL)
        self.assertEqual(config.base_url, DEFAULT_BASE_URL)

    def test_process_environment_takes_precedence_without_mutation(self):
        environment = {
            "OPENROUTER_API_KEY": "environment-key",
            "OPENROUTER_MODEL": "custom/model",
        }
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "OPENROUTER_API_KEY=file-key\nOPENROUTER_MODEL=file/model\n",
                encoding="utf-8",
            )
            config = get_openrouter_config(env_file, environ=environment)

        self.assertEqual(config.api_key, "environment-key")
        self.assertEqual(config.model, "custom/model")
        self.assertNotIn("OPENROUTER_BASE_URL", environment)

    def test_missing_key_fails_explicitly_without_making_a_request(self):
        client = OpenRouterClient(OpenRouterConfig(api_key=""))
        with patch("urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(OpenRouterError, "API key is not configured"):
                client.parse_preferences("quick walk")
        urlopen.assert_not_called()


class TestOpenRouterClient(unittest.TestCase):
    def setUp(self):
        self.client = OpenRouterClient(OpenRouterConfig(api_key="test-secret"))

    def test_parses_validated_natural_language_preferences(self):
        provider_response = completion(json.dumps({
            "architecture": 0.9,
            "history": 0.6,
            "shortest_distance": 0,
            "minimum_walking_time": 0.8,
            "more_landmarks": 0,
            "min_landmarks": 0,
            "max_duration_minutes": 10,
            "max_detour_ratio": 1.5,
            "excluded_categories": [],
            "is_qualitative_time_constrained": True,
            "unsupported_preferences": ["quietness"],
        }))
        with patch("urllib.request.urlopen", return_value=provider_response) as urlopen:
            preferences = self.client.parse_preferences("Architecture, but I have little time.")

        self.assertEqual(preferences.architecture, 0.9)
        self.assertEqual(preferences.history, 0.6)
        self.assertEqual(preferences.minimum_walking_time, 0.8)
        self.assertEqual(preferences.max_duration_minutes, 10)
        self.assertEqual(preferences.max_detour_ratio, 1.5)
        self.assertEqual(preferences.raw_query, "Architecture, but I have little time.")
        self.assertEqual(preferences.unsupported_preferences, ["quietness"])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f"{DEFAULT_BASE_URL}/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        body = json.loads(request.data)
        self.assertEqual(body["model"], DEFAULT_MODEL)
        self.assertEqual(body["response_format"], {"type": "json_object"})

    def test_explicit_waypoints_keep_user_order_and_use_only_catalog_ids(self):
        catalog = [
            {
                "id": "rockefeller_center",
                "name": "Rockefeller Center",
                "categories": ["architecture"],
            },
            {
                "id": "times_square",
                "name": "Times Square",
                "categories": ["entertainment"],
            },
        ]
        payload = {
            "architecture": 0.8,
            "history": 0,
            "shortest_distance": 0,
            "minimum_walking_time": 0,
            "more_landmarks": 0,
            "min_landmarks": 0,
            "max_duration_minutes": None,
            "max_distance_m": None,
            "max_detour_ratio": None,
            "excluded_categories": [],
            "preferred_categories": ["architecture"],
            "primary_interest": "architecture",
            "secondary_interests": [],
            "requested_landmark_ids": ["rockefeller_center", "times_square"],
            "preferred_landmark_ids": ["rockefeller_center"],
            "sightseeing_waypoint_ids": ["rockefeller_center"],
            "additional_sightseeing_sequences": [
                ["times_square"],
            ],
            "requested_route_count": 2,
            "route_priority": "sightseeing",
            "is_qualitative_time_constrained": False,
            "unsupported_preferences": [],
        }
        with patch(
            "urllib.request.urlopen",
            return_value=completion(json.dumps(payload)),
        ):
            preferences = self.client.parse_preferences(
                "Times Square, then Rockefeller Center",
                catalog,
                "grand_central_terminal",
                "bryant_park",
            )
        self.assertEqual(
            preferences.requested_landmark_ids,
            ["times_square", "rockefeller_center"],
        )
        self.assertEqual(preferences.preferred_landmark_ids, ["rockefeller_center"])
        self.assertEqual(
            preferences.sightseeing_waypoint_ids,
            ["rockefeller_center"],
        )
        self.assertEqual(
            preferences.additional_sightseeing_sequences,
            [["times_square"]],
        )
        self.assertEqual(preferences.requested_route_count, 2)

    def test_rejects_model_landmarks_outside_the_selected_catalog(self):
        payload = {
            "architecture": 0,
            "history": 0,
            "shortest_distance": 0,
            "minimum_walking_time": 0,
            "more_landmarks": 0,
            "min_landmarks": 0,
            "max_duration_minutes": None,
            "max_distance_m": None,
            "max_detour_ratio": None,
            "excluded_categories": [],
            "preferred_categories": [],
            "primary_interest": None,
            "secondary_interests": [],
            "requested_landmark_ids": ["invented_place"],
            "preferred_landmark_ids": [],
            "route_priority": "balanced",
            "is_qualitative_time_constrained": False,
            "unsupported_preferences": [],
        }
        with patch(
            "urllib.request.urlopen",
            return_value=completion(json.dumps(payload)),
        ):
            with self.assertRaisesRegex(OpenRouterError, "outside the approved catalog"):
                self.client.parse_preferences(
                    "visit Times Square",
                    [{"id": "times_square", "name": "Times Square", "categories": []}],
                )

    def test_rejects_out_of_range_structured_preferences(self):
        with patch(
            "urllib.request.urlopen",
            return_value=completion(json.dumps({
                "architecture": 2,
                "history": 0,
                "shortest_distance": 0,
                "minimum_walking_time": 0,
                "more_landmarks": 0,
                "min_landmarks": 0,
                "max_duration_minutes": None,
                "max_detour_ratio": None,
                "excluded_categories": [],
                "is_qualitative_time_constrained": False,
                "unsupported_preferences": [],
            })),
        ):
            with self.assertRaisesRegex(OpenRouterError, "invalid structured preferences"):
                self.client.parse_preferences("architecture")

    def test_provider_http_error_is_sanitized(self):
        error = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            401,
            "Unauthorized",
            {},
            None,
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(OpenRouterError, "HTTP 401") as raised:
                self.client.parse_preferences("quick")
        self.assertNotIn("test-secret", str(raised.exception))

    def test_timeout_is_reported(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError):
            with self.assertRaisesRegex(OpenRouterError, "timed out"):
                self.client.parse_preferences("quick")

    def test_network_error_is_reported(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(OpenRouterError, "network error"):
                self.client.parse_preferences("quick")

    def test_explanation_focus_request_contains_only_supplied_route_context(self):
        context = {
            "traveler_preference": "architecture, with limited time",
            "selected_route_id": "route-a",
            "routes": [{
                "route_id": "route-a",
                "route_name": "Route A",
                "distance_m": 500,
                "estimated_duration_minutes": 7.5,
                "landmarks": [{"name": "Supplied Landmark", "categories": ["architecture"]}],
            }],
        }
        with patch(
            "urllib.request.urlopen",
            return_value=completion(json.dumps({"focus": "balanced"})),
        ) as urlopen:
            focus = self.client.generate_explanation(context)

        self.assertEqual(focus, "balanced")
        request_body = json.loads(urlopen.call_args.args[0].data)
        prompt = request_body["messages"][1]["content"]
        self.assertEqual(json.loads(prompt), context)
        self.assertNotIn("coordinates", prompt)
        self.assertNotIn("fabricated place", prompt)

    def test_parse_preferences_supports_walking_time_budget(self):
        catalog = [{
            "id": "chrysler_building",
            "name": "Chrysler Building",
            "categories": ["architecture"],
        }]
        provider_response = completion(json.dumps({
            "architecture": 0.8,
            "history": 0.5,
            "shortest_distance": 0,
            "minimum_walking_time": 0,
            "more_landmarks": 0.5,
            "min_landmarks": 0,
            "walking_time_budget_minutes": 20.0,
            "max_duration_minutes": None,
            "max_detour_ratio": None,
            "excluded_categories": [],
            "preferred_categories": ["architecture"],
            "requested_landmark_ids": [],
            "preferred_landmark_ids": [],
            "sightseeing_waypoint_ids": [],
            "additional_sightseeing_sequences": [],
            "requested_route_count": 1,
            "route_priority": "sightseeing",
            "is_qualitative_time_constrained": False,
            "unsupported_preferences": [],
        }))
        with patch("urllib.request.urlopen", return_value=provider_response):
            prefs = self.client.parse_preferences(
                "I have 20 minutes to explore architecture.",
                available_landmarks=catalog,
            )
        self.assertEqual(prefs.walking_time_budget_minutes, 20.0)
        self.assertIsNone(prefs.max_duration_minutes)
        self.assertEqual(prefs.architecture, 0.8)

    def test_parse_preferences_passes_existing_routes_to_provider(self):
        catalog = [{"id": "chrysler_building", "name": "Chrysler Building", "categories": ["architecture"]}]
        existing_routes = [{
            "features": [{
                "properties": {
                    "route_id": "direct",
                    "route_name": "Direct Walking Route",
                    "distance_m": 320.0,
                    "estimated_duration_s": 246.0,
                    "via_waypoints": [],
                }
            }]
        }]
        provider_response = completion(json.dumps({
            "architecture": 1.0,
            "history": 0.8,
            "shortest_distance": 0,
            "minimum_walking_time": 0,
            "more_landmarks": 0.5,
            "min_landmarks": 0,
            "walking_time_budget_minutes": 20.0,
            "max_duration_minutes": None,
            "max_detour_ratio": None,
            "excluded_categories": [],
            "preferred_categories": ["architecture"],
            "requested_landmark_ids": [],
            "preferred_landmark_ids": ["chrysler_building"],
            "sightseeing_waypoint_ids": ["chrysler_building"],
            "additional_sightseeing_sequences": [],
            "requested_route_count": 1,
            "route_priority": "sightseeing",
            "is_qualitative_time_constrained": False,
            "unsupported_preferences": [],
        }))
        with patch("urllib.request.urlopen", return_value=provider_response) as urlopen:
            prefs = self.client.parse_preferences(
                "I have 20 minutes to explore architecture.",
                available_landmarks=catalog,
                origin_id="grand_central_terminal",
                destination_id="bryant_park",
                existing_routes=existing_routes,
            )
        self.assertEqual(prefs.sightseeing_waypoint_ids, ["chrysler_building"])
        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        user_msg = json.loads(body["messages"][1]["content"])
        self.assertIn("existing_validated_routes", user_msg)
        self.assertEqual(user_msg["existing_validated_routes"][0]["route_id"], "direct")

    def test_direct_route_duration_is_not_inferred_as_user_time_budget(self):
        catalog = [{
            "id": "chrysler_building",
            "name": "Chrysler Building",
            "categories": ["architecture"],
        }]
        direct_route = {
            "features": [{
                "properties": {
                    "route_id": "direct",
                    "route_name": "Direct",
                    "distance_m": 300.0,
                    "estimated_duration_s": 240.0,
                    "via_waypoints": [],
                }
            }]
        }
        response = completion(json.dumps({
            "architecture": 1.0,
            "walking_time_budget_minutes": None,
            "max_duration_minutes": None,
        }))
        with patch("urllib.request.urlopen", return_value=response):
            preferences = self.client.parse_preferences(
                "I enjoy architecture.",
                available_landmarks=catalog,
                existing_routes=[direct_route],
            )
        self.assertIsNone(preferences.walking_time_budget_minutes)
        self.assertIsNone(preferences.max_duration_minutes)



    def test_strategy_choice_must_be_in_backend_allowlist(self):
        context = completion(json.dumps({"strategy": "native"}))
        client = OpenRouterClient(OpenRouterConfig(api_key="test-secret"))
        with patch("urllib.request.urlopen", return_value=context):
            self.assertEqual(
                client.choose_route_strategy(
                    {"minimum_walking_time": 1},
                    [{"route_id": "direct", "distance_m": 500}],
                    ["none", "native"],
                ),
                "native",
            )
        with patch(
            "urllib.request.urlopen",
            return_value=completion(json.dumps({"strategy": "both"})),
        ):
            with self.assertRaisesRegex(OpenRouterError, "not approved"):
                client.choose_route_strategy(
                    {"minimum_walking_time": 1},
                    [{"route_id": "direct"}],
                    ["none", "native"],
                )


    def test_phase_6_empty_preference_plans_default_sightseeing(self):
        """Phase 6: empty query prompts default sightseeing sequence with balanced priority."""
        payload = {
            "architecture": 0,
            "history": 0,
            "shortest_distance": 0,
            "minimum_walking_time": 0,
            "more_landmarks": 0,
            "min_landmarks": 0,
            "walking_time_budget_minutes": None,
            "max_duration_minutes": None,
            "max_distance_m": None,
            "max_detour_ratio": None,
            "excluded_categories": [],
            "preferred_categories": [],
            "primary_interest": None,
            "secondary_interests": [],
            "requested_landmark_ids": [],
            "preferred_landmark_ids": [],
            "sightseeing_waypoint_ids": ["chrysler_building"],
            "additional_sightseeing_sequences": [],
            "requested_route_count": 2,
            "route_priority": "balanced",
            "is_qualitative_time_constrained": False,
            "unsupported_preferences": [],
        }
        catalog = [
            {"id": "chrysler_building", "name": "Chrysler Building", "categories": ["architecture"]},
            {"id": "bryant_park", "name": "Bryant Park", "categories": ["park"]},
        ]
        with patch("urllib.request.urlopen", return_value=completion(json.dumps(payload))) as urlopen:
            prefs = self.client.parse_preferences(
                "",
                catalog,
                "grand_central_terminal",
                "bryant_park",
            )
        self.assertEqual(prefs.sightseeing_waypoint_ids, ["chrysler_building"])
        self.assertEqual(prefs.route_priority, "balanced")
        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        system_content = body["messages"][0]["content"]
        self.assertIn("When the user query is empty or provides no specific preferences", system_content)


if __name__ == "__main__":
    unittest.main()
