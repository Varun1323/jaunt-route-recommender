"""Offline unit tests for Jaunt recommendation engine and preference parser.

Tests cover:
1. Architecture preference.
2. Strict time limit.
3. Shortest-route preference.
4. Preference for more landmarks.
5. Changing preferences changes the recommendation.
6. Missing or invalid preference input.
7. No feasible route within the requested time limit.
8. Support for all 72 directed journeys between the 9 landmarks.
9. Strict journey scoping (evaluates ONLY routes belonging to the selected journey).
10. Identical origin and destination rejection.
11. Journeys with fewer than three alternatives (1 or 2 routes).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "backend" / "data"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_routes import (
    LEGACY_LANDMARK_IDS,
    build_route_definitions,
    load_landmark_candidates,
)  # noqa: E402
from preference_parser import (  # noqa: E402
    PreferenceValidationError,
    UserPreferences,
    parse_preferences,
    validate_structured_preferences,
)
from recommendation_engine import (  # noqa: E402
    DEFAULT_NEARBY_THRESHOLD_M,
    compute_route_landmark_relevance,
    find_nearby_landmarks,
    load_candidate_coordinates,
    load_landmark_metadata,
    load_routes,
    recommend_route,
    score_routes,
)


class TestRecommendationEngine(unittest.TestCase):
    """Test recommendation engine ranking, constraints, and explanations."""

    @classmethod
    def setUpClass(cls):
        cls.routes = load_routes()
        cls.landmarks_meta = load_landmark_metadata()

    @staticmethod
    def _route_candidate(
        route_id: str,
        distance_m: float,
        duration_s: float,
        waypoint_ids: list[str] | None = None,
    ) -> dict:
        waypoints = waypoint_ids or []
        candidates = load_landmark_candidates()
        return {
            "type": "FeatureCollection",
            "name": route_id,
            "features": [{
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [candidates["grand_central_terminal"]["lon"],
                         candidates["grand_central_terminal"]["lat"]],
                        [candidates["bryant_park"]["lon"],
                         candidates["bryant_park"]["lat"]],
                    ],
                },
                "properties": {
                    "route_id": route_id,
                    "route_name": route_id,
                    "origin_landmark_id": "grand_central_terminal",
                    "destination_landmark_id": "bryant_park",
                    "distance_m": distance_m,
                    "estimated_duration_s": duration_s,
                    "via_waypoints": [
                        {
                            "landmark_id": landmark_id,
                            "landmark_name": landmark_id,
                        }
                        for landmark_id in waypoints
                    ],
                },
            }],
        }
    def test_detour_value_decreases_as_extra_distance_increases(self):
        """The same relevant sightseeing stop earns less value when its detour is longer."""
        routes = [
            self._route_candidate(
                "short_detour",
                400.0,
                300.0,
                ["chrysler_building"],
            ),
            self._route_candidate(
                "long_detour",
                900.0,
                600.0,
                ["chrysler_building"],
            ),
        ]

        ranked = score_routes(
            routes,
            UserPreferences(architecture=1.0),
            landmarks_meta=self.landmarks_meta,
            candidate_coords={},
        )

        by_id = {route.route_id: route for route in ranked}

        self.assertGreater(
            by_id["short_detour"].score_breakdown["detour_value"],
            by_id["long_detour"].score_breakdown["detour_value"],
        )

        self.assertEqual(
            by_id["short_detour"].score_breakdown["detour_extra_distance_m"],
            0.0,
        )

        self.assertEqual(
            by_id["long_detour"].score_breakdown["detour_extra_distance_m"],
            500.0,
        )

        self.assertEqual(
            by_id["short_detour"].score_breakdown["detour_value_method"],
            "preference_value_per_extra_distance",
        )
    def test_four_minute_direct_and_20_minute_budget_select_relevant_sightseeing(self):
        routes = [
            self._route_candidate("direct", 300.0, 240.0),
            self._route_candidate(
                "sightseeing", 900.0, 1080.0, ["chrysler_building"]
            ),
        ]
        result = recommend_route(
            query="I have 20 minutes to explore architecture.",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            preferences=UserPreferences(
                architecture=1.0,
                walking_time_budget_minutes=20.0,
                raw_query="I have 20 minutes to explore architecture.",
            ),
            route_candidates=routes,
        )
        self.assertEqual(result["recommended_route"]["route_id"], "sightseeing")
        self.assertEqual(
            result["recommended_route"]["estimated_duration_s"], 1080.0
        )
        self.assertEqual(result["parsed_preferences"]["walking_time_budget_minutes"], 20.0)

    def test_actual_route_duration_drives_fastest_comparison(self):
        routes = [
            self._route_candidate("fast", 600.0, 240.0),
            self._route_candidate("slow", 600.0, 900.0, ["chrysler_building"]),
        ]
        result = recommend_route(
            query="quickest route",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            preferences=UserPreferences(
                minimum_walking_time=1.0,
                raw_query="quickest route",
            ),
            route_candidates=routes,
        )
        self.assertEqual(result["recommended_route"]["route_id"], "fast")
        self.assertEqual(result["recommended_route"]["estimated_duration_s"], 240.0)
        self.assertEqual(
            result["ranked_alternatives"][0]["estimated_duration_s"], 900.0
        )

    def test_neutral_preference_does_not_reward_detour_without_relevant_value(self):
        routes = [
            self._route_candidate("direct", 300.0, 240.0),
            self._route_candidate("detour", 900.0, 1080.0, ["times_square"]),
        ]
        result = recommend_route(
            query="",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
            preferences=UserPreferences(),
            route_candidates=routes,
        )
        self.assertEqual(result["recommended_route"]["route_id"], "direct")

    def test_hard_maximum_marks_only_over_limit_route_infeasible(self):
        routes = [
            self._route_candidate("within_limit", 300.0, 240.0),
            self._route_candidate(
                "over_limit", 900.0, 1080.0, ["chrysler_building"]
            ),
        ]
        ranked = score_routes(
            routes,
            UserPreferences(
                architecture=1.0,
                max_duration_minutes=10.0,
            ),
            landmarks_meta=self.landmarks_meta,
            candidate_coords={},
        )
        by_id = {route.route_id: route for route in ranked}
        self.assertTrue(by_id["within_limit"].is_feasible)
        self.assertFalse(by_id["over_limit"].is_feasible)

    # -----------------------------------------------------------------------
    # 1. Architecture preference
    # -----------------------------------------------------------------------
    def test_architecture_preference_selects_architecture_route(self):
        """User seeking architecture without time constraints receives the architecture loop."""
        res = recommend_route("I love historic architecture and historic buildings.")
        self.assertEqual(res["recommended_route"]["route_id"], "architecture")
        self.assertGreater(res["recommended_route"]["landmark_count"], 0)
        self.assertIn("Pershing Square Building", res["landmarks_visited"])
        self.assertIn("Chrysler Building", res["landmarks_visited"])
        self.assertIn("architecture", res["explanation"].lower())

    # -----------------------------------------------------------------------
    # 2. Strict time limit
    # -----------------------------------------------------------------------
    def test_strict_time_limit_penalizes_long_route(self):
        """A 10-minute time limit rules out the 13.6-minute architecture route."""
        res = recommend_route("I love architecture but need to arrive within 10 minutes.")
        self.assertEqual(res["recommended_route"]["route_id"], "via_vanderbilt")
        self.assertTrue(res["recommended_route"]["is_feasible"])
        self.assertLessEqual(res["recommended_route"]["estimated_duration_minutes"], 10.0)

        # Check that architecture route is penalized and ranked behind feasible alternatives
        ranked_ids = [r["route_id"] for r in res["ranked_alternatives"]]
        self.assertEqual(ranked_ids[-1], "architecture")
        self.assertIn("exceed", res["explanation"].lower())

    # -----------------------------------------------------------------------
    # 3. Shortest-route preference
    # -----------------------------------------------------------------------
    def test_shortest_route_preference_selects_direct(self):
        """User requesting the shortest distance receives the direct 42nd St route."""
        res = recommend_route("I want the shortest possible walk.")
        self.assertEqual(res["recommended_route"]["route_id"], "direct")
        self.assertEqual(res["recommended_route"]["distance_m"], 686.0)
        self.assertEqual(res["recommended_route"]["score_breakdown"]["distance_efficiency"], 1.0)
        self.assertIn("shortest", res["explanation"].lower())

    def test_exclusions_and_detour_limits_precede_soft_architecture_weight(self):
        def route(route_id, distance, duration, waypoint_ids):
            return {
                "type": "FeatureCollection",
                "name": route_id,
                "features": [{
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-73.98, 40.75], [-73.97, 40.75]],
                    },
                    "properties": {
                        "route_id": route_id,
                        "route_name": route_id,
                        "origin_landmark_id": "grand_central_terminal",
                        "destination_landmark_id": "bryant_park",
                        "distance_m": distance,
                        "estimated_duration_s": duration,
                        "via_waypoints": [
                            {"landmark_id": waypoint, "landmark_name": waypoint}
                            for waypoint in waypoint_ids
                        ],
                    },
                }],
            }

        direct = route("direct", 500, 500, [])
        architecture = route("architecture", 700, 400, ["one_vanderbilt"])
        excluded = score_routes(
            [architecture, direct],
            UserPreferences(
                architecture=1.0,
                excluded_categories=["architecture"],
            ),
            landmarks_meta=self.landmarks_meta,
            candidate_coords={},
        )
        self.assertEqual(excluded[0].route_id, "direct")
        self.assertFalse(excluded[1].constraint_status["exclusions"])

        detour_limited = score_routes(
            [architecture, direct],
            UserPreferences(
                architecture=1.0,
                max_detour_ratio=1.1,
            ),
            landmarks_meta=self.landmarks_meta,
            candidate_coords={},
        )
        self.assertEqual(detour_limited[0].route_id, "direct")
        self.assertFalse(detour_limited[1].constraint_status["detour_limit"])

        landmark_required = score_routes(
            [direct, architecture],
            UserPreferences(
                shortest_distance=1.0,
                min_landmarks=1,
            ),
            landmarks_meta=self.landmarks_meta,
            candidate_coords={},
        )
        self.assertEqual(landmark_required[0].route_id, "architecture")
        self.assertTrue(landmark_required[0].constraint_status["min_landmarks"])

    # -----------------------------------------------------------------------
    # 4. Preference for more landmarks
    # -----------------------------------------------------------------------
    def test_more_landmarks_preference_selects_architecture_loop(self):
        """User wanting to see as many interesting buildings as possible gets the architecture route with most landmarks."""
        res = recommend_route("I want to see as many interesting buildings as possible.")
        self.assertEqual(res["recommended_route"]["route_id"], "architecture")
        self.assertEqual(res["recommended_route"]["landmark_count"], len(res["recommended_route"]["landmarks_visited"]))
        self.assertEqual(res["recommended_route"]["landmark_count"], 6)
        self.assertEqual(res["recommended_route"]["score_breakdown"]["landmarks_quantity"], 1.0)

    # -----------------------------------------------------------------------
    # 5. Changing preferences changes the recommendation
    # -----------------------------------------------------------------------
    def test_changing_preferences_changes_recommendation(self):
        """Varying preference inputs deterministically alters the top recommendation."""
        # Preference A: Shortest distance -> direct
        res_a = recommend_route("shortest route")
        self.assertEqual(res_a["recommended_route"]["route_id"], "direct")

        # Preference B: Maximum sights / architecture -> architecture loop
        res_b = recommend_route("see as many historic landmarks as possible")
        self.assertEqual(res_b["recommended_route"]["route_id"], "architecture")

        # Preference C: Quick walk with an architectural landmark -> via_vanderbilt
        res_c = recommend_route("quick walk with an architectural landmark")
        self.assertEqual(res_c["recommended_route"]["route_id"], "via_vanderbilt")

        # Verify all 3 produced different recommended route IDs
        winner_ids = {
            res_a["recommended_route"]["route_id"],
            res_b["recommended_route"]["route_id"],
            res_c["recommended_route"]["route_id"],
        }
        self.assertEqual(len(winner_ids), 3)

    # -----------------------------------------------------------------------
    # 6. Missing or invalid preference input
    # -----------------------------------------------------------------------
    def test_none_preference_input(self):
        """None query produces valid fallback recommendation without error."""
        res = recommend_route(None)
        self.assertIn("recommended_route", res)
        self.assertIn(res["recommended_route"]["route_id"], {"direct", "via_vanderbilt", "architecture"})
        self.assertIsInstance(res["individual_scores"], dict)
        self.assertIn("limitation_disclosure", res)

    def test_empty_string_preference_input(self):
        """Empty query string produces valid fallback recommendation."""
        res = recommend_route("   ")
        self.assertIn("recommended_route", res)
        self.assertGreater(len(res["ranked_alternatives"]), 0)

    def test_unrecognized_query_input(self):
        """Gibberish or non-matching query produces valid default scoring without crashing."""
        res = recommend_route("xyz 12345 foobar unrelated words")
        self.assertIn("recommended_route", res)
        self.assertIn("explanation", res)

    # -----------------------------------------------------------------------
    # 7. No feasible route within requested time limit
    # -----------------------------------------------------------------------
    def test_no_feasible_route_within_time_limit(self):
        """When time limit is tighter than any available route, engine recommends fastest and warns."""
        res = recommend_route("I need a route under 5 minutes.")
        self.assertEqual(res["recommended_route"]["route_id"], "direct")
        self.assertFalse(res["recommended_route"]["is_feasible"])
        self.assertIn("all available routes exceed your requested time limit", res["explanation"].lower())
        self.assertIn("fastest available", res["explanation"].lower())

    def test_feasible_route_outranks_higher_scoring_infeasible_route(self):
        """Feasibility is the primary ranking key even when an infeasible route scores higher."""
        direct = {
            "type": "FeatureCollection",
            "name": "direct",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[-74.0, 40.0], [-73.999, 40.001]]},
                "properties": {
                    "route_id": "direct",
                    "route_name": "Direct",
                    "origin_landmark_id": "chrysler_building",
                    "destination_landmark_id": "daily_news_building",
                    "distance_m": 300.0,
                    "estimated_duration_s": 240.0,
                    "via_waypoints": [],
                },
            }],
        }
        architecture = {
            "type": "FeatureCollection",
            "name": "architecture",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[-74.0, 40.0], [-73.998, 40.002], [-73.999, 40.001]]},
                "properties": {
                    "route_id": "architecture",
                    "route_name": "Architecture",
                    "origin_landmark_id": "chrysler_building",
                    "destination_landmark_id": "daily_news_building",
                    "distance_m": 1500.0,
                    "estimated_duration_s": 360.0,
                    "via_waypoints": [{"landmark_id": "pershing_square_building", "landmark_name": "Pershing Square Building"}],
                },
            }],
        }
        preferences = UserPreferences(
            architecture=1.0,
            max_duration_minutes=5.0,
            raw_query="architecture under 5 minutes",
        )
        scored = score_routes([direct, architecture], preferences)
        self.assertTrue(scored[0].is_feasible)
        self.assertEqual(scored[0].route_id, "direct")
        self.assertGreater(scored[1].final_score, scored[0].final_score)

    # -----------------------------------------------------------------------
    # 8. Output schema verification
    # -----------------------------------------------------------------------
    def test_recommendation_output_schema(self):
        """Output dictionary must contain all required fields from user specification."""
        res = recommend_route("I love architecture but don't have much time.")
        required_keys = {
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
        }
        self.assertTrue(required_keys.issubset(set(res.keys())))
        self.assertIsInstance(res["ranked_alternatives"], list)
        # The cached route set grows as alternatives are generated, so assert
        # the invariant (alternatives exclude the recommendation) rather than
        # a fixed count.
        self.assertGreaterEqual(len(res["ranked_alternatives"]), 2)
        self.assertIsInstance(res["individual_scores"], dict)
        self.assertEqual(
            len(res["individual_scores"]), len(res["ranked_alternatives"]) + 1
        )
    def test_landmark_quantity_uses_diminishing_returns(self):
        """The marginal quantity reward decreases as more landmarks are added."""
        routes = [
            self._route_candidate(
                "one_landmark", 400.0, 300.0, ["chrysler_building"]
            ),
            self._route_candidate(
                "two_landmarks",
                450.0,
                330.0,
                ["chrysler_building", "pershing_square_building"],
            ),
        ]

        ranked = score_routes(
            routes,
            UserPreferences(more_landmarks=1.0),
            landmarks_meta=self.landmarks_meta,
            candidate_coords={},
        )

        scores = {
            route.route_id: route.score_breakdown["landmarks_quantity"]
            for route in ranked
        }

        first_gain = scores["one_landmark"]
        second_gain = scores["two_landmarks"] - scores["one_landmark"]

        self.assertAlmostEqual(scores["two_landmarks"], 1.0)
        self.assertGreater(first_gain, second_gain)
        self.assertEqual(
            ranked[0].score_breakdown["landmarks_quantity_method"],
            "logarithmic_diminishing_returns",
        )

    def test_relevant_landmark_beats_larger_irrelevant_collection(self):
        """A highly relevant architectural stop should beat several unrelated stops."""
        routes = [
            self._route_candidate(
                "many_unrelated_places",
                650.0,
                500.0,
                [
                    "times_square",
                    "bryant_park",
                    "museum_of_modern_art",
                    "st_patricks_cathedral",
                ],
            ),
            self._route_candidate(
                "architecture_focus",
                500.0,
                390.0,
                ["chrysler_building"],
            ),
        ]

        ranked = score_routes(
            routes,
            UserPreferences(
                architecture=1.0,
                more_landmarks=1.0,
            ),
            landmarks_meta=self.landmarks_meta,
            candidate_coords={},
        )

        self.assertEqual(ranked[0].route_id, "architecture_focus")


class TestJourneyScopingAndArbitraryJourneys(unittest.TestCase):
    """Test journey scoping across all 72 directed pairs and multi-journey route isolation."""

    @classmethod
    def setUpClass(cls):
        cls.candidates = load_landmark_candidates()
        cls.all_landmark_ids = sorted(LEGACY_LANDMARK_IDS)

    # -----------------------------------------------------------------------
    # 9. All 72 directed journeys genuinely supported
    # -----------------------------------------------------------------------
    def test_all_72_directed_journeys_supported_in_definitions(self):
        """All 9*8 = 72 directed journeys between the 9 landmarks must generate valid route definitions."""
        self.assertEqual(len(self.all_landmark_ids), 9)
        journey_count = 0

        for origin_id in self.all_landmark_ids:
            for dest_id in self.all_landmark_ids:
                if origin_id == dest_id:
                    continue

                journey_count += 1
                with self.subTest(origin=origin_id, destination=dest_id):
                    defs = build_route_definitions(origin_id, dest_id, self.candidates)
                    self.assertGreaterEqual(len(defs), 1, f"No route definitions for {origin_id} -> {dest_id}")
                    self.assertIn("direct", defs)

                    for r_id, r_def in defs.items():
                        # First location must match origin
                        self.assertEqual(r_def["locations"][0]["lat"], self.candidates[origin_id]["lat"])
                        self.assertEqual(r_def["locations"][0]["lon"], self.candidates[origin_id]["lon"])
                        # Last location must match destination
                        self.assertEqual(r_def["locations"][-1]["lat"], self.candidates[dest_id]["lat"])
                        self.assertEqual(r_def["locations"][-1]["lon"], self.candidates[dest_id]["lon"])

                        # Intermediate waypoints must never match origin or destination
                        for wp in r_def.get("waypoints", []):
                            self.assertNotEqual(wp["landmark_id"], origin_id)
                            self.assertNotEqual(wp["landmark_id"], dest_id)

        self.assertEqual(journey_count, 72)

    # -----------------------------------------------------------------------
    # 10. Rejection of identical origin and destination
    # -----------------------------------------------------------------------
    def test_identical_origin_and_destination_rejected_in_recommender(self):
        """A loop without any cached, validated waypoint route is unavailable."""
        with self.assertRaisesRegex(ValueError, "No routes found"):
            recommend_route(
                origin_id="chrysler_building",
                destination_id="chrysler_building",
            )

    def test_unknown_origin_or_destination_rejected_in_recommender(self):
        """Unknown landmark IDs are rejected with informative error."""
        with self.assertRaises(ValueError) as ctx:
            recommend_route(origin_id="fake_place", destination_id="bryant_park")
        self.assertIn("Unknown origin", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            recommend_route(origin_id="grand_central_terminal", destination_id="fake_place")
        self.assertIn("Unknown destination", str(ctx.exception))

    # -----------------------------------------------------------------------
    # 11. Strict journey isolation (evaluates ONLY routes for requested journey)
    # -----------------------------------------------------------------------
    def test_recommendation_engine_evaluates_only_requested_journey_routes(self):
        """Routes from different journeys must never be mixed or evaluated together."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            # Journey A: Chrysler -> Daily News (2 routes)
            route_a1 = {
                "type": "FeatureCollection",
                "name": "direct",
                "features": [{
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[-73.9757, 40.7516], [-73.9734, 40.7501]]},
                    "properties": {
                        "route_id": "direct",
                        "route_name": "Direct Chrysler to Daily News",
                        "origin_landmark_id": "chrysler_building",
                        "destination_landmark_id": "daily_news_building",
                        "distance_m": 300.0,
                        "estimated_duration_s": 220.0,
                        "via_waypoints": [],
                    },
                }],
            }
            route_a2 = {
                "type": "FeatureCollection",
                "name": "via_vanderbilt",
                "features": [{
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[-73.9757, 40.7516], [-73.9785, 40.7523], [-73.9734, 40.7501]]},
                    "properties": {
                        "route_id": "via_vanderbilt",
                        "route_name": "Via One Vanderbilt",
                        "origin_landmark_id": "chrysler_building",
                        "destination_landmark_id": "daily_news_building",
                        "distance_m": 780.0,
                        "estimated_duration_s": 570.0,
                        "via_waypoints": [{"landmark_id": "one_vanderbilt", "landmark_name": "1 Vanderbilt"}],
                    },
                }],
            }

            # Journey B: Grand Central -> Bryant Park (1 route)
            route_b1 = {
                "type": "FeatureCollection",
                "name": "direct",
                "features": [{
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[-73.9772, 40.7523], [-73.9835, 40.7541]]},
                    "properties": {
                        "route_id": "direct",
                        "route_name": "Direct GCT to Bryant Park",
                        "origin_landmark_id": "grand_central_terminal",
                        "destination_landmark_id": "bryant_park",
                        "distance_m": 686.0,
                        "estimated_duration_s": 495.0,
                        "via_waypoints": [],
                    },
                }],
            }

            # Save all 3 files into the same directory
            (tmp_path / "chrysler_building__daily_news_building__direct.geojson").write_text(json.dumps(route_a1), encoding="utf-8")
            (tmp_path / "chrysler_building__daily_news_building__via_vanderbilt.geojson").write_text(json.dumps(route_a2), encoding="utf-8")
            (tmp_path / "grand_central_terminal__bryant_park__direct.geojson").write_text(json.dumps(route_b1), encoding="utf-8")

            # 1. load_routes for Journey A must return ONLY Journey A's 2 routes
            routes_a = load_routes(tmp_path, origin_id="chrysler_building", destination_id="daily_news_building")
            self.assertEqual(len(routes_a), 2)
            for r in routes_a:
                props = r["features"][0]["properties"]
                self.assertEqual(props["origin_landmark_id"], "chrysler_building")
                self.assertEqual(props["destination_landmark_id"], "daily_news_building")

            # 2. recommend_route for Journey A must evaluate ONLY Journey A routes
            rec_a = recommend_route(
                "shortest walk",
                origin_id="chrysler_building",
                destination_id="daily_news_building",
                routes_dir=tmp_path,
            )
            self.assertEqual(rec_a["origin_landmark_id"], "chrysler_building")
            self.assertEqual(rec_a["destination_landmark_id"], "daily_news_building")
            self.assertEqual(rec_a["recommended_route"]["route_id"], "direct")
            self.assertEqual(rec_a["distance_m"], 300.0)
            self.assertEqual(len(rec_a["ranked_alternatives"]), 1)
            self.assertEqual(rec_a["ranked_alternatives"][0]["route_id"], "via_vanderbilt")
            # Journey B must NOT appear anywhere in scores or alternatives
            self.assertNotIn("grand_central_terminal", rec_a["individual_scores"])

            # 3. Changing preference on Journey A changes recommendation
            rec_a_landmarks = recommend_route(
                "see interesting landmarks",
                origin_id="chrysler_building",
                destination_id="daily_news_building",
                routes_dir=tmp_path,
            )
            self.assertEqual(rec_a_landmarks["recommended_route"]["route_id"], "via_vanderbilt")

    # -----------------------------------------------------------------------
    # 12. Support for journeys with fewer than 3 alternatives (1 or 2 routes)
    # -----------------------------------------------------------------------
    def test_journey_with_single_available_route(self):
        """Journey with only 1 route available scores cleanly, sets empty alternatives, and explains."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            single_route = {
                "type": "FeatureCollection",
                "name": "direct",
                "features": [{
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[-73.9757, 40.7516], [-73.9734, 40.7501]]},
                    "properties": {
                        "route_id": "direct",
                        "route_name": "Direct Route",
                        "origin_landmark_id": "chrysler_building",
                        "destination_landmark_id": "daily_news_building",
                        "distance_m": 300.0,
                        "estimated_duration_s": 220.0,
                        "via_waypoints": [],
                    },
                }],
            }
            (tmp_path / "single.geojson").write_text(json.dumps(single_route), encoding="utf-8")

            res = recommend_route(
                "any route",
                origin_id="chrysler_building",
                destination_id="daily_news_building",
                routes_dir=tmp_path,
            )
            self.assertEqual(res["recommended_route"]["route_id"], "direct")
            self.assertEqual(res["ranked_alternatives"], [])
            self.assertIn("only one validated route", res["explanation"].lower())

    def test_all_72_journeys_recommendation_preferences_and_route_metrics(self):
        """Exercise six preference classes for every pair using deterministic route fixtures."""
        candidates_by_id = load_candidate_coordinates()
        architecture_ids = [
            lid for lid, meta in load_landmark_metadata().items()
            if "architecture" in meta.get("categories", [])
        ]
        queries = [
            ("historic architecture", "architecture"),
            ("shortest route", "shortest"),
            ("fastest walk", "fastest"),
            ("see as many landmarks as possible", "landmarks"),
            ("under 6 minutes", "time_limit"),
            ("avoid architecture and landmarks; give me a quick route", "negative"),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            routes_dir = Path(tmp_dir)
            for origin_id in self.all_landmark_ids:
                for destination_id in self.all_landmark_ids:
                    if origin_id == destination_id:
                        continue
                    waypoint_id = next(
                        lid for lid in architecture_ids if lid not in {origin_id, destination_id}
                    )
                    origin = candidates_by_id[origin_id]
                    destination = candidates_by_id[destination_id]
                    waypoint = candidates_by_id[waypoint_id]
                    for route_id, distance, duration, waypoints, via_point in (
                        ("direct", 400.0, 300.0, [], None),
                        ("architecture", 700.0, 500.0, [{
                            "landmark_id": waypoint_id,
                            "landmark_name": waypoint["landmark_name"],
                        }], waypoint),
                    ):
                        line = [[origin["lon"], origin["lat"]]]
                        if via_point:
                            line.append([via_point["lon"], via_point["lat"]])
                        line.append([destination["lon"], destination["lat"]])
                        feature_collection = {
                            "type": "FeatureCollection",
                            "name": route_id,
                            "features": [{
                                "type": "Feature",
                                "geometry": {"type": "LineString", "coordinates": line},
                                "properties": {
                                    "route_id": route_id,
                                    "route_name": route_id.title(),
                                    "origin_landmark_id": origin_id,
                                    "destination_landmark_id": destination_id,
                                    "distance_m": distance,
                                    "estimated_duration_s": duration,
                                    "via_waypoints": waypoints,
                                },
                            }],
                        }
                        filename = f"{origin_id}__{destination_id}__{route_id}.geojson"
                        (routes_dir / filename).write_text(json.dumps(feature_collection), encoding="utf-8")

                    for query, category in queries:
                        with self.subTest(origin=origin_id, destination=destination_id, query=category):
                            result = recommend_route(
                                query,
                                origin_id=origin_id,
                                destination_id=destination_id,
                                routes_dir=routes_dir,
                            )
                            winner = result["recommended_route"]
                            expected_id = "architecture" if category in {"architecture", "landmarks"} else "direct"
                            self.assertEqual(winner["route_id"], expected_id)
                            self.assertEqual(result["distance_m"], winner["distance_m"])
                            self.assertAlmostEqual(
                                result["estimated_walking_time_s"], winner["estimated_duration_s"]
                            )
                            self.assertEqual(winner["landmark_count"], len(winner["landmarks_visited"]))
                            self.assertIn(winner["route_name"], result["explanation"])
                            self.assertEqual(len(result["ranked_alternatives"]), 1)
                            self.assertEqual(len(result["individual_scores"]), 2)
                            if category == "architecture" and winner["landmark_count"]:
                                landmark_names = [item["landmark_name"] for item in winner["landmarks_visited"]]
                                self.assertTrue(any(name in result["explanation"] for name in landmark_names))
                            if category == "time_limit":
                                self.assertTrue(winner["is_feasible"])
                            if category == "negative":
                                self.assertEqual(result["parsed_preferences"]["architecture"], 0.0)
                                self.assertEqual(result["parsed_preferences"]["more_landmarks"], 0.0)


class TestPreferenceParser(unittest.TestCase):
    """Test natural language parsing edge cases."""

    def test_explicit_three_landmark_request_preserves_order_and_count(self):
        catalog = [
            {"id": "grand_central_terminal", "name": "Grand Central Terminal", "categories": ["architecture"]},
            {"id": "rockefeller_center", "name": "Rockefeller Center", "categories": ["architecture"]},
            {"id": "st_patricks_cathedral", "name": "St. Patrick's Cathedral", "categories": ["religion"]},
        ]
        prefs = parse_preferences(
            "I want to visit Grand Central Terminal, Rockefeller Center and St. Patrick's Cathedral.",
            catalog,
        )
        self.assertEqual(
            prefs.requested_landmark_ids,
            [
                "grand_central_terminal",
                "rockefeller_center",
                "st_patricks_cathedral",
            ],
        )
        self.assertEqual(
            prefs.sightseeing_waypoint_ids,
            prefs.requested_landmark_ids,
        )

    def test_validate_structured_preferences_uses_canonical_schema(self):
        catalog = [{
            "id": "times_square",
            "name": "Times Square",
            "categories": ["entertainment"],
        }]
        prefs = validate_structured_preferences(
            {
                "architecture": 0.0,
                "history": 0.0,
                "shortest_distance": 1.0,
                "minimum_walking_time": 0.0,
                "more_landmarks": 0.0,
                "min_landmarks": 0,
                "max_duration_minutes": 12.0,
                "max_distance_m": None,
                "max_detour_ratio": 1.0,
                "excluded_categories": [],
                "preferred_categories": ["entertainment"],
                "primary_interest": "entertainment",
                "secondary_interests": [],
                "requested_landmark_ids": ["times_square"],
                "preferred_landmark_ids": ["times_square"],
                "route_priority": "efficiency",
                "is_qualitative_time_constrained": False,
                "unsupported_preferences": [],
            },
            available_landmarks=catalog,
            raw_query="Visit Times Square",
        )
        self.assertEqual(prefs.requested_landmark_ids, ["times_square"])
        self.assertEqual(prefs.preferred_categories, ["entertainment"])
        self.assertEqual(prefs.max_detour_ratio, 1.0)

    def test_validate_structured_preferences_rejects_unknown_fields(self):
        with self.assertRaises(PreferenceValidationError):
            validate_structured_preferences({"architecture": 1.0, "invented": True})

    def test_parse_explicit_minutes_variations(self):
        """Parse various phrases specifying minutes."""
        test_cases = [
            ("under 10 minutes", 10.0),
            ("within 15 mins", 15.0),
            ("less than 8 min", 8.0),
            ("12 minutes or less", 12.0),
            ("maximum 20 minutes", 20.0),
        ]
        for text, expected_mins in test_cases:
            with self.subTest(query=text):
                prefs = parse_preferences(text)
                self.assertEqual(prefs.max_duration_minutes, expected_mins)

    def test_parse_walking_time_budget_vs_hard_limit(self):
        """Distinguish available walking-time budget from hard maximum constraints."""
        # A. Available walking-time budget examples
        budget_cases = [
            ("I have 20 minutes to explore", 20.0),
            ("I can spend around 30 minutes walking", 30.0),
            ("I've got 15 minutes", 15.0),
            ("Willing to walk for 45 minutes", 45.0),
            ("Plan something for around 25 minutes", 25.0),
            ("have 20 minutes to walk", 20.0),
        ]
        for query, expected_budget in budget_cases:
            with self.subTest(query=query):
                prefs = parse_preferences(query)
                self.assertEqual(prefs.walking_time_budget_minutes, expected_budget)
                self.assertIsNone(prefs.max_duration_minutes)
                self.assertEqual(prefs.minimum_walking_time, 0.0)

        # B. Hard maximum walking time examples
        hard_limit_cases = [
            ("Keep the walk under 15 minutes", 15.0),
            ("Maximum 10 minutes", 10.0),
            ("Less than 20 mins", 20.0),
            ("Do not exceed 12 minutes", 12.0),
            ("At most 30 minutes", 30.0),
            ("12 minutes or less", 12.0),
        ]
        for query, expected_max in hard_limit_cases:
            with self.subTest(query=query):
                prefs = parse_preferences(query)
                self.assertIsNone(prefs.walking_time_budget_minutes)
                self.assertEqual(prefs.max_duration_minutes, expected_max)
                self.assertGreaterEqual(prefs.minimum_walking_time, 0.7)

        # C. Combined expressions: budget AND hard maximum
        combined_query = "I have 25 minutes, but don't exceed 20 minutes"
        prefs = parse_preferences(combined_query)
        self.assertEqual(prefs.walking_time_budget_minutes, 25.0)
        self.assertEqual(prefs.max_duration_minutes, 20.0)

        # D. Pure theme request: no budget and no hard limit
        theme_query = "Show me historic architecture"
        prefs = parse_preferences(theme_query)
        self.assertIsNone(prefs.walking_time_budget_minutes)
        self.assertIsNone(prefs.max_duration_minutes)
        self.assertEqual(prefs.architecture, 1.0)
        self.assertEqual(prefs.history, 1.0)

    def test_time_budget_recommends_sightseeing_over_shorter_direct(self):
        """When user has 20 minutes and wants architecture, recommend sightseeing route over 4-minute direct route."""
        res = recommend_route(
            "I have 20 minutes and want to explore historic architecture.",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
        )
        self.assertNotEqual(res["recommended_route"]["route_id"], "direct")
        self.assertGreater(res["recommended_route"]["landmark_count"], 0)
        all_route_ids = [res["recommended_route"]["route_id"]] + [
            r["route_id"] for r in res["ranked_alternatives"]
        ]
        self.assertIn("direct", all_route_ids)
        self.assertEqual(res["parsed_preferences"]["walking_time_budget_minutes"], 20.0)
        self.assertIsNone(res["parsed_preferences"]["max_duration_minutes"])
        self.assertIn("20-minute", res["explanation"])

    def test_time_budget_without_theme_recommends_sightseeing(self):
        """When user provides a walking time budget without theme keywords, recommend sightseeing rather than bare direct."""
        res = recommend_route(
            "I have 20 minutes to explore",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
        )
        self.assertNotEqual(res["recommended_route"]["route_id"], "direct")
        self.assertGreater(res["recommended_route"]["landmark_count"], 0)
        self.assertEqual(res["parsed_preferences"]["walking_time_budget_minutes"], 20.0)

    def test_validate_structured_preferences_supports_walking_time_budget(self):
        prefs = validate_structured_preferences({
            "walking_time_budget_minutes": 25.0,
            "architecture": 0.8,
        })
        self.assertEqual(prefs.walking_time_budget_minutes, 25.0)
        self.assertEqual(prefs.to_dict()["walking_time_budget_minutes"], 25.0)

    def test_phase_5_budget_prefers_meaningful_sightseeing_within_budget(self):
        """Under a time budget, routes with relevant stops outrank bare direct routes without rewarding length alone."""
        res = recommend_route(
            "I have 20 minutes to walk and see architecture.",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
        )
        winner = res["recommended_route"]
        self.assertNotEqual(winner["route_id"], "direct")
        self.assertGreater(winner["landmark_count"], 0)
        self.assertLessEqual(winner["estimated_duration_minutes"], 20.0)
        self.assertIn("fits your available 20-minute walking-time budget", res["explanation"])

    def test_phase_5_never_claims_fits_budget_when_exceeded(self):
        """When the winning route duration exceeds the budget, engine discloses the excess and does not claim it fits."""
        res = recommend_route(
            "I have 2 minutes to walk and see architecture.",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
        )
        self.assertNotIn("fits your available 2-minute walking-time budget", res["explanation"])
        self.assertIn("exceeds your 2-minute walking-time budget", res["explanation"])

    def test_phase_5_hard_maximum_feasibility_and_disclosure(self):
        """Hard maximum enforces feasibility first, marks over-limit infeasible, and discloses no feasible candidate found."""
        res = recommend_route(
            "Keep the walk under 2 minutes.",
            origin_id="grand_central_terminal",
            destination_id="bryant_park",
        )
        self.assertFalse(res["recommended_route"]["is_feasible"])
        self.assertIn("No feasible candidate was found", res["explanation"])
        self.assertIn(f"{res['recommended_route']['estimated_duration_minutes']:.1f} minutes", res["explanation"])

    def test_parse_qualitative_time_constraints(self):
        """Qualitative phrases like 'don't have much time' set constraint flag and default limit."""
        prefs = parse_preferences("I love architecture but don't have much time.")
        self.assertTrue(prefs.is_qualitative_time_constrained)
        self.assertEqual(prefs.max_duration_minutes, 10.0)
        self.assertGreaterEqual(prefs.minimum_walking_time, 0.8)

    def test_parse_min_landmarks_pattern(self):
        """'at least one landmark' sets min_landmarks = 1."""
        prefs = parse_preferences("A quick walk with at least one interesting landmark.")
        self.assertEqual(prefs.min_landmarks, 1)
        self.assertGreater(prefs.more_landmarks, 0.0)

    def test_extracts_ordered_selected_stops_categories_and_metric_limits(self):
        catalog = [
            {
                "id": "rockefeller_center",
                "name": "Rockefeller Center",
                "categories": ["architecture", "historic_landmark"],
            },
            {
                "id": "times_square",
                "name": "Times Square",
                "categories": ["entertainment"],
            },
            {
                "id": "bryant_park",
                "name": "Bryant Park",
                "categories": ["nature", "recreation"],
            },
        ]
        preferences = parse_preferences(
            "Visit Times Square, then Rockefeller Center; under 1.5 km, I love architecture.",
            catalog,
        )
        self.assertEqual(
            preferences.requested_landmark_ids,
            ["times_square", "rockefeller_center"],
        )
        self.assertEqual(preferences.max_distance_m, 1500.0)
        self.assertEqual(preferences.preferred_categories, ["architecture"])
        self.assertEqual(preferences.primary_interest, "architecture")

    def test_nature_exclusions_are_structured(self):
        catalog = [{
            "id": "bryant_park",
            "name": "Bryant Park",
            "categories": ["nature", "recreation"],
        }]
        preferences = parse_preferences("Avoid parks and greenery.", catalog)
        self.assertIn("nature", preferences.excluded_categories)
        self.assertEqual(preferences.preferred_categories, [])

    # ------------------------------------------------------------------
    # Negative preference handling
    # ------------------------------------------------------------------
    def test_negated_architecture_constructions(self):
        """Recognise negative constructions on architecture and ensure architecture remains 0.0."""
        queries = [
            "don't want architecture",
            "I do not want historical buildings",
            "do not prefer architecture",
            "I don't prefer historic landmarks",
            "not interested in architecture",
            "not interested in art deco",
            "avoid architecture",
            "avoiding historic buildings",
            "without architecture",
            "walk without buildings",
            "no architecture",
            "no buildings",
            "skip architecture",
            "not looking for monuments",
            "don't care about architecture",
            "don't need historic buildings",
        ]
        for q in queries:
            with self.subTest(query=q):
                prefs = parse_preferences(q)
                self.assertEqual(prefs.architecture, 0.0, f"Expected architecture=0.0 for '{q}', got {prefs.architecture}")

    def test_negated_shortest_distance_constructions(self):
        """Recognise negative constructions on shortest distance and ensure shortest_distance remains 0.0."""
        queries = [
            "don't want the shortest route",
            "do not want shortest walk",
            "do not prefer direct route",
            "don't prefer the direct walk",
            "not interested in shortest distance",
            "not interested in direct route",
            "avoid shortest route",
            "avoid direct walk",
            "without direct route",
            "without taking the shortest walk",
            "no direct route",
            "skip shortest route",
            "don't care about shortest distance",
        ]
        for q in queries:
            with self.subTest(query=q):
                prefs = parse_preferences(q)
                self.assertEqual(prefs.shortest_distance, 0.0, f"Expected shortest_distance=0.0 for '{q}', got {prefs.shortest_distance}")

    def test_negated_walking_time_and_rush(self):
        """Recognise negative time and rush phrases; do not set time constraints or rush preference."""
        queries = [
            "walk without rushing",
            "I am not in a rush",
            "not in a rush",
            "no rush",
            "not in a hurry",
            "no hurry",
            "not pressed for time",
            "don't have a tight schedule",
            "not short on time",
            "avoid rushing",
            "don't need a fast walk",
            "not looking for the fastest route",
            "don't care about speed",
        ]
        for q in queries:
            with self.subTest(query=q):
                prefs = parse_preferences(q)
                self.assertEqual(prefs.minimum_walking_time, 0.0, f"Expected minimum_walking_time=0.0 for '{q}', got {prefs.minimum_walking_time}")
                self.assertFalse(prefs.is_qualitative_time_constrained, f"Expected is_qualitative_time_constrained=False for '{q}'")
                self.assertIsNone(prefs.max_duration_minutes, f"Expected max_duration_minutes=None for '{q}'")

    def test_negated_landmarks_and_sights(self):
        """Recognise negative landmark and sight constructions; ensure more_landmarks=0.0 and min_landmarks=0."""
        queries = [
            "don't want landmarks",
            "do not want sights",
            "do not prefer sights",
            "don't prefer landmarks",
            "not interested in sightseeing",
            "not interested in sights",
            "avoid landmarks",
            "avoid sights",
            "without landmarks",
            "walk without sights",
            "no landmarks",
            "no sights",
            "no attractions",
            "skip landmarks",
            "not looking for attractions",
            "don't care about sights",
        ]
        for q in queries:
            with self.subTest(query=q):
                prefs = parse_preferences(q)
                self.assertEqual(prefs.more_landmarks, 0.0, f"Expected more_landmarks=0.0 for '{q}', got {prefs.more_landmarks}")
                self.assertEqual(prefs.min_landmarks, 0, f"Expected min_landmarks=0 for '{q}', got {prefs.min_landmarks}")

    def test_mixed_preferences_positive_and_negative(self):
        """Handle mixed statements where one preference is positive and another is negative."""
        # 1. Fast walk without architecture
        p1 = parse_preferences("Fast walk without architecture")
        self.assertEqual(p1.minimum_walking_time, 1.0)
        self.assertEqual(p1.architecture, 0.0)

        # 2. Avoid architecture, give me the shortest route
        p2 = parse_preferences("Avoid architecture, give me the shortest route")
        self.assertEqual(p2.architecture, 0.0)
        self.assertEqual(p2.shortest_distance, 1.0)

        # 3. I want architecture but don't want the direct route
        p3 = parse_preferences("I want architecture but don't want the direct route")
        self.assertEqual(p3.architecture, 1.0)
        self.assertEqual(p3.shortest_distance, 0.0)

        # 4. Shortest route, not interested in sights
        p4 = parse_preferences("Shortest route, not interested in sights")
        self.assertEqual(p4.shortest_distance, 1.0)
        self.assertEqual(p4.more_landmarks, 0.0)

        # 5. Not interested in architecture; I want to see as many landmarks as possible
        p5 = parse_preferences("I'm not interested in architecture; I want to see as many landmarks as possible.")
        self.assertEqual(p5.architecture, 0.0)
        self.assertEqual(p5.more_landmarks, 1.0)

        # 6. Multi-negation: Avoid architecture and landmarks, want fastest route
        p6 = parse_preferences("Avoid architecture and landmarks, want fastest route")
        self.assertEqual(p6.architecture, 0.0)
        self.assertEqual(p6.more_landmarks, 0.0)
        self.assertEqual(p6.minimum_walking_time, 1.0)

        # 7. Architecture with negative rush
        p7 = parse_preferences("I am not in a rush, show me historic architecture")
        self.assertEqual(p7.architecture, 1.0)
        self.assertEqual(p7.minimum_walking_time, 0.0)
        self.assertFalse(p7.is_qualitative_time_constrained)

    def test_punctuation_and_phrasing_variations(self):
        """Verify negative and mixed preferences across various punctuation and phrasing."""
        # Comma separation
        p_comma = parse_preferences("Fast walk, no architecture")
        self.assertEqual(p_comma.minimum_walking_time, 1.0)
        self.assertEqual(p_comma.architecture, 0.0)

        # Semicolon separation
        p_semi = parse_preferences("Shortest route; avoid architecture")
        self.assertEqual(p_semi.shortest_distance, 1.0)
        self.assertEqual(p_semi.architecture, 0.0)

        # Period separation
        p_period = parse_preferences("Direct route. Not interested in landmarks.")
        self.assertEqual(p_period.shortest_distance, 1.0)
        self.assertEqual(p_period.more_landmarks, 0.0)

        # Exclamation marks
        p_excl = parse_preferences("Fast walk! No buildings!")
        self.assertEqual(p_excl.minimum_walking_time, 1.0)
        self.assertEqual(p_excl.architecture, 0.0)

        # Question mark
        p_quest = parse_preferences("Can I have the quickest route without sights?")
        self.assertEqual(p_quest.minimum_walking_time, 1.0)
        self.assertEqual(p_quest.more_landmarks, 0.0)

        # Prepositional 'instead of'
        p_instead = parse_preferences("Instead of architecture, give me the direct route")
        self.assertEqual(p_instead.shortest_distance, 1.0)
        self.assertEqual(p_instead.architecture, 0.0)

        # 'rather than'
        p_rather = parse_preferences("I want the direct route rather than architecture")
        self.assertEqual(p_rather.shortest_distance, 1.0)
        self.assertEqual(p_rather.architecture, 0.0)

    def test_unavailable_greenery_and_quietness_preferences_are_disclosed(self):
        """The parser must not claim unsupported route properties were scored."""
        prefs = parse_preferences("I prefer greenery and quiet places.")
        self.assertEqual(prefs.unsupported_preferences, ["greenery", "quietness"])
        self.assertEqual(prefs.architecture, 0.0)
        self.assertEqual(prefs.more_landmarks, 0.0)

    def test_history_detour_tolerance_and_exclusions_are_structured(self):
        prefs = parse_preferences(
            "I want historical places, avoid architecture, and allow up to 25% longer."
        )
        self.assertEqual(prefs.history, 1.0)
        self.assertEqual(prefs.architecture, 0.0)
        self.assertEqual(prefs.excluded_categories, ["architecture"])
        self.assertEqual(prefs.max_detour_ratio, 1.25)

        patient = parse_preferences("I love architecture and don't mind walking longer.")
        self.assertEqual(patient.architecture, 1.0)
        self.assertEqual(patient.max_detour_ratio, 2.0)

    def test_limited_time_expressions_activate_time_constraint(self):
        """Phrases like 'don't have much time' are positive time constraints, not negated preferences."""
        positive_time_queries = [
            "I don't have much time",
            "I do not have much time",
            "don't have much time",
            "I don't have a lot of time",
            "I do not have a lot of time",
            "I don't have enough time",
            "not much time",
            "not a lot of time",
            "no time to waste",
            "no time to lose",
            "no time to spare",
            "I have no time",
            "short on time",
            "little time",
            "running late",
            "time is limited",
            "time is short",
        ]
        for q in positive_time_queries:
            with self.subTest(query=q):
                prefs = parse_preferences(q)
                self.assertTrue(prefs.is_qualitative_time_constrained, f"Expected is_qualitative_time_constrained=True for '{q}'")
                self.assertEqual(prefs.max_duration_minutes, 10.0, f"Expected max_duration_minutes=10.0 for '{q}'")
                self.assertGreaterEqual(prefs.minimum_walking_time, 0.8, f"Expected minimum_walking_time>=0.8 for '{q}'")

    def test_limited_time_does_not_negate_subsequent_preferences(self):
        """'don't have much time' should not cause subsequent preferences to be negated."""
        # Architecture after limited-time phrase
        p1 = parse_preferences("I don't have much time to see architecture")
        self.assertTrue(p1.is_qualitative_time_constrained)
        self.assertEqual(p1.architecture, 1.0)

        # Landmarks after limited-time phrase
        p2 = parse_preferences("I don't have much time, want to see landmarks")
        self.assertTrue(p2.is_qualitative_time_constrained)
        self.assertEqual(p2.more_landmarks, 1.0)

        # Fastest route after limited-time phrase
        p3 = parse_preferences("I don't have much time, fastest route please")
        self.assertTrue(p3.is_qualitative_time_constrained)
        self.assertEqual(p3.minimum_walking_time, 1.0)

        # Direct route after limited-time phrase
        p4 = parse_preferences("I don't have much time, direct route please")
        self.assertTrue(p4.is_qualitative_time_constrained)
        self.assertEqual(p4.shortest_distance, 1.0)

        # Positive architecture after "do not have much time"
        p5 = parse_preferences("I do not have much time and want architecture")
        self.assertTrue(p5.is_qualitative_time_constrained)
        self.assertEqual(p5.architecture, 1.0)

    def test_limited_time_with_explicit_negation_of_other_preference(self):
        """Limited-time expression combined with an explicit negation of another dimension."""
        # Don't have much time, but avoid architecture
        p1 = parse_preferences("I don't have much time, but avoid architecture")
        self.assertTrue(p1.is_qualitative_time_constrained)
        self.assertEqual(p1.architecture, 0.0)

        # Not much time; not interested in sightseeing
        p2 = parse_preferences("Not much time; not interested in sightseeing")
        self.assertTrue(p2.is_qualitative_time_constrained)
        self.assertEqual(p2.more_landmarks, 0.0)

        # Short on time, skip the architecture
        p3 = parse_preferences("Short on time, skip the architecture")
        self.assertTrue(p3.is_qualitative_time_constrained)
        self.assertEqual(p3.architecture, 0.0)

    def test_genuinely_negated_speed_not_confused_with_limited_time(self):
        """Phrases expressing no urgency must not activate time constraints."""
        no_urgency_queries = [
            "I don't want a fast route",
            "I'm not in a rush",
            "not in a rush",
            "no rush",
            "not in a hurry",
            "no hurry",
            "I am not in a rush, show me architecture",
            "walk without rushing",
            "don't want to rush",
            "avoid rushing",
        ]
        for q in no_urgency_queries:
            with self.subTest(query=q):
                prefs = parse_preferences(q)
                self.assertEqual(prefs.minimum_walking_time, 0.0, f"Expected minimum_walking_time=0.0 for '{q}', got {prefs.minimum_walking_time}")
                self.assertFalse(prefs.is_qualitative_time_constrained, f"Expected is_qualitative_time_constrained=False for '{q}'")
                self.assertIsNone(prefs.max_duration_minutes, f"Expected max_duration_minutes=None for '{q}'")

    def test_recommendation_engine_respects_negative_preferences(self):
        """End-to-end: recommendation engine selects appropriate route when negative preferences are supplied."""
        # Fast walk without architecture: selects direct route (fastest) instead of routing via architecture
        res = recommend_route("Fast walk without architecture")
        self.assertEqual(res["recommended_route"]["route_id"], "direct")
        self.assertNotIn("architectural", res["explanation"].lower())

        # Avoid direct route, show me architecture: selects an architectural alternative (via_vanderbilt or architecture)
        res_arch = recommend_route("Avoid the direct route, I want to see architecture")
        self.assertIn(res_arch["recommended_route"]["route_id"], {"via_vanderbilt", "architecture"})


class TestMultiRouteRankingAndCacheCompatibility(unittest.TestCase):
    """Preference-sensitive ranking over multi-route journeys and cache precedence."""

    ORIGIN_ID = "chrysler_building"
    DESTINATION_ID = "daily_news_building"

    def _write_route(
        self,
        routes_dir: Path,
        route_id: str,
        distance_m: float,
        duration_s: float,
        coordinates: list[list[float]],
        waypoints: list[dict[str, object]] | None = None,
    ) -> None:
        feature_collection = {
            "type": "FeatureCollection",
            "name": route_id,
            "features": [{
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "properties": {
                    "route_id": route_id,
                    "route_name": route_id.replace("_", " ").title(),
                    "origin_landmark_id": self.ORIGIN_ID,
                    "destination_landmark_id": self.DESTINATION_ID,
                    "distance_m": distance_m,
                    "estimated_duration_s": duration_s,
                    "via_waypoints": waypoints or [],
                },
            }],
        }
        (routes_dir / f"{self.ORIGIN_ID}__{self.DESTINATION_ID}__{route_id}.geojson").write_text(
            json.dumps(feature_collection), encoding="utf-8"
        )

    def test_preference_sensitive_ranking_with_three_routes(self):
        """Multiple cached alternatives rerank deterministically for different preferences."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            routes_dir = Path(tmp_dir)
            base = [[-74.0, 40.0], [-73.999, 40.001]]
            self._write_route(routes_dir, "direct", 400.0, 300.0, base)
            self._write_route(
                routes_dir,
                "alternative_1",
                500.0,
                380.0,
                [[-74.0, 40.0], [-73.9985, 40.0015], [-73.999, 40.001]],
                [{"landmark_id": "one_vanderbilt", "landmark_name": "1 Vanderbilt"}],
            )
            self._write_route(
                routes_dir,
                "architecture",
                700.0,
                500.0,
                [[-74.0, 40.0], [-73.997, 40.002], [-73.999, 40.001]],
                [
                    {"landmark_id": "pershing_square_building", "landmark_name": "Pershing Square Building"},
                    {"landmark_id": "graybar_building", "landmark_name": "Graybar Building"},
                ],
            )

            cases = {
                "shortest route": "direct",
                "fastest walk": "direct",
                "I love historic architecture": "architecture",
                "see as many interesting landmarks as possible": "architecture",
                "avoid architecture and landmarks; give me a quick route": "direct",
            }
            for query, expected_route_id in cases.items():
                with self.subTest(query=query):
                    result = recommend_route(
                        query,
                        origin_id=self.ORIGIN_ID,
                        destination_id=self.DESTINATION_ID,
                        routes_dir=routes_dir,
                    )
                    self.assertEqual(result["recommended_route"]["route_id"], expected_route_id)
                    self.assertEqual(len(result["ranked_alternatives"]), 2)
                    self.assertEqual(len(result["individual_scores"]), 3)

    def test_load_routes_prefers_scoped_file_over_legacy(self):
        """A scoped journey file wins over a legacy unscoped file with the same key."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            routes_dir = Path(tmp_dir)
            legacy = {
                "type": "FeatureCollection",
                "name": "direct",
                "features": [{
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[-74.0, 40.0], [-73.999, 40.001]]},
                    "properties": {
                        "route_id": "direct",
                        "origin_landmark_id": self.ORIGIN_ID,
                        "destination_landmark_id": self.DESTINATION_ID,
                        "distance_m": 999.0,
                        "estimated_duration_s": 999.0,
                        "via_waypoints": [],
                    },
                }],
            }
            (routes_dir / "direct.geojson").write_text(json.dumps(legacy), encoding="utf-8")
            self._write_route(routes_dir, "direct", 400.0, 300.0, [[-74.0, 40.0], [-73.999, 40.001]])

            routes = load_routes(
                routes_dir,
                origin_id=self.ORIGIN_ID,
                destination_id=self.DESTINATION_ID,
            )
            self.assertEqual(len(routes), 1)
            self.assertEqual(routes[0]["features"][0]["properties"]["distance_m"], 400.0)


class TestGeometryBasedLandmarkDetection(unittest.TestCase):
    """Tests for route-geometry-based nearby-landmark detection."""

    def _make_route_fc(self, route_id, coords, via_waypoints=None,
                       origin_id="test_origin", dest_id="test_dest",
                       distance_m=500.0, duration_s=360.0):
        """Helper: build a minimal GeoJSON FeatureCollection for a route."""
        return {
            "type": "FeatureCollection",
            "name": route_id,
            "features": [{
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {
                    "route_id": route_id,
                    "route_name": f"Test Route {route_id}",
                    "origin_landmark_id": origin_id,
                    "destination_landmark_id": dest_id,
                    "distance_m": distance_m,
                    "estimated_duration_s": duration_s,
                    "via_waypoints": via_waypoints or [],
                },
            }],
        }

    def test_explicit_waypoint_loop_is_scored_and_recommended(self):
        route = self._make_route_fc(
            "via_loop",
            [[-73.977, 40.752], [-73.985, 40.758], [-73.977, 40.752]],
            via_waypoints=[{
                "landmark_id": "times_square",
                "landmark_name": "Times Square",
            }],
            origin_id="grand_central_terminal",
            dest_id="grand_central_terminal",
            distance_m=1800.0,
            duration_s=1200.0,
        )
        result = recommend_route(
            "visit Times Square",
            origin_id="grand_central_terminal",
            destination_id="grand_central_terminal",
            preferences=UserPreferences(requested_landmark_ids=["times_square"]),
            route_candidates=[route],
        )
        self.assertEqual(result["recommended_route"]["route_id"], "via_loop")
        self.assertTrue(result["recommended_route"]["constraint_status"]["requested_landmarks"])
        self.assertIn("loop", result["explanation"].lower())

    # ------------------------------------------------------------------
    # find_nearby_landmarks: nearby landmark detected
    # ------------------------------------------------------------------
    def test_nearby_landmark_within_threshold_is_detected(self):
        """A landmark whose candidate coordinate is within the threshold of the route geometry is found."""
        # Route runs east-west along lat ~40.752, lon -73.978 to -73.976
        route_coords = [[-73.978, 40.752], [-73.976, 40.752]]
        candidates = {
            "landmark_a": {
                "landmark_id": "landmark_a",
                "landmark_name": "Landmark A",
                "lat": 40.7522,   # ~22 m north of route at this longitude
                "lon": -73.977,
                "verification_status": "candidate_unconfirmed",
                "is_prototype_unverified": True,
            },
        }
        nearby = find_nearby_landmarks(route_coords, candidates, proximity_threshold_m=50.0)
        self.assertEqual(len(nearby), 1)
        self.assertEqual(nearby[0]["landmark_id"], "landmark_a")
        self.assertLessEqual(nearby[0]["min_distance_m"], 50.0)
        self.assertTrue(nearby[0]["is_prototype_unverified"])

    # ------------------------------------------------------------------
    # find_nearby_landmarks: distant landmark excluded
    # ------------------------------------------------------------------
    def test_distant_landmark_beyond_threshold_is_excluded(self):
        """A landmark far from the route geometry is not included."""
        route_coords = [[-73.978, 40.752], [-73.976, 40.752]]
        candidates = {
            "far_away": {
                "landmark_id": "far_away",
                "landmark_name": "Far Away Place",
                "lat": 40.760,   # ~890 m north
                "lon": -73.977,
                "verification_status": "candidate_unconfirmed",
                "is_prototype_unverified": True,
            },
        }
        nearby = find_nearby_landmarks(route_coords, candidates, proximity_threshold_m=60.0)
        self.assertEqual(len(nearby), 0)

    # ------------------------------------------------------------------
    # find_nearby_landmarks: via-waypoint duplicates excluded
    # ------------------------------------------------------------------
    def test_via_waypoint_duplicate_excluded_from_nearby(self):
        """A landmark already listed as a via-waypoint is not duplicated in nearby results."""
        route_coords = [[-73.978, 40.752], [-73.976, 40.752]]
        candidates = {
            "already_via": {
                "landmark_id": "already_via",
                "landmark_name": "Already Via WP",
                "lat": 40.7521,
                "lon": -73.977,
                "verification_status": "candidate_unconfirmed",
                "is_prototype_unverified": True,
            },
        }
        nearby = find_nearby_landmarks(
            route_coords, candidates,
            exclude_ids={"already_via"},
            proximity_threshold_m=100.0,
        )
        self.assertEqual(len(nearby), 0)

    # ------------------------------------------------------------------
    # find_nearby_landmarks: origin and destination excluded
    # ------------------------------------------------------------------
    def test_origin_and_destination_excluded_from_nearby(self):
        """Origin and destination landmarks are excluded from nearby detection."""
        route_coords = [[-73.978, 40.752], [-73.976, 40.752]]
        candidates = {
            "origin_lm": {
                "landmark_id": "origin_lm",
                "landmark_name": "Origin",
                "lat": 40.752, "lon": -73.978,
                "verification_status": "candidate_unconfirmed",
                "is_prototype_unverified": True,
            },
            "dest_lm": {
                "landmark_id": "dest_lm",
                "landmark_name": "Destination",
                "lat": 40.752, "lon": -73.976,
                "verification_status": "candidate_unconfirmed",
                "is_prototype_unverified": True,
            },
        }
        nearby = find_nearby_landmarks(
            route_coords, candidates,
            exclude_ids={"origin_lm", "dest_lm"},
            proximity_threshold_m=100.0,
        )
        self.assertEqual(len(nearby), 0)

    # ------------------------------------------------------------------
    # compute_route_landmark_relevance: merges without duplicates
    # ------------------------------------------------------------------
    def test_relevance_merges_via_and_nearby_without_duplicates(self):
        """Via-waypoints and nearby landmarks are merged; shared IDs are not duplicated."""
        via = [{"landmark_id": "lm_a", "landmark_name": "LM A"}]
        nearby = [
            {"landmark_id": "lm_a", "landmark_name": "LM A", "min_distance_m": 10.0,
             "is_prototype_unverified": True},
            {"landmark_id": "lm_b", "landmark_name": "LM B", "min_distance_m": 20.0,
             "is_prototype_unverified": True},
        ]
        _, visited = compute_route_landmark_relevance(via, {}, nearby_landmarks=nearby)
        ids = [v["landmark_id"] for v in visited]
        self.assertEqual(ids, ["lm_a", "lm_b"])  # lm_a appears once
        # lm_a should be from via_waypoint, lm_b from nearby
        self.assertEqual(visited[0]["source"], "via_waypoint")
        self.assertEqual(visited[1]["source"], "nearby_route_geometry")

    # ------------------------------------------------------------------
    # compute_route_landmark_relevance: route_coordinates detection
    # ------------------------------------------------------------------
    def test_compute_relevance_detects_nearby_from_geometry(self):
        """compute_route_landmark_relevance identifies landmarks near route_coordinates."""
        route_coords = [[-73.978, 40.752], [-73.976, 40.752]]
        candidates = {
            "lm_near": {
                "landmark_id": "lm_near",
                "landmark_name": "Near LM",
                "lat": 40.7521,
                "lon": -73.977,
                "verification_status": "candidate_unconfirmed",
                "is_prototype_unverified": True,
            },
        }
        meta = {
            "lm_near": {
                "categories": ["architecture"],
                "description": "Historic building",
            }
        }
        arch_score, visited = compute_route_landmark_relevance(
            via_waypoints=[],
            landmarks_meta=meta,
            route_coordinates=route_coords,
            candidate_coords=candidates,
            proximity_threshold_m=50.0,
        )
        self.assertEqual(len(visited), 1)
        self.assertEqual(visited[0]["landmark_id"], "lm_near")
        self.assertEqual(visited[0]["source"], "nearby_route_geometry")
        self.assertGreater(arch_score, 0.0)

    # ------------------------------------------------------------------
    # score_routes: nearby landmarks included in landmarks_visited and landmark_count
    # ------------------------------------------------------------------
    def test_score_routes_includes_nearby_landmarks_in_visited(self):
        """Routes passing near a landmark include it in landmarks_visited and landmark_count."""
        # Route A passes right next to candidate "near_lm"
        route_a = self._make_route_fc("route_a", [[-73.978, 40.752], [-73.976, 40.752]])
        # Route B passes far from any candidate
        route_b = self._make_route_fc("route_b", [[-73.990, 40.760], [-73.988, 40.760]])

        candidates = {
            "near_lm": {
                "landmark_id": "near_lm",
                "landmark_name": "Nearby Landmark",
                "lat": 40.7521,
                "lon": -73.977,
                "verification_status": "candidate_unconfirmed",
                "is_prototype_unverified": True,
            },
        }
        prefs = parse_preferences("")
        scored = score_routes(
            [route_a, route_b], prefs,
            landmarks_meta={},
            candidate_coords=candidates,
            proximity_threshold_m=50.0,
        )
        visited_counts = {s.route_id: len(s.landmarks_visited) for s in scored}
        self.assertGreater(visited_counts["route_a"], 0)
        self.assertEqual(visited_counts["route_b"], 0)
        # landmark_count must match len(landmarks_visited)
        counts = {s.route_id: s.landmark_count for s in scored}
        self.assertEqual(counts["route_a"], 1)
        self.assertEqual(counts["route_b"], 0)
        self.assertEqual(scored[0].to_dict()["landmark_count"], 1)

    # ------------------------------------------------------------------
    # Nearby landmark entries carry coordinate limitation note
    # ------------------------------------------------------------------
    def test_nearby_landmark_carries_unverified_note(self):
        """Nearby landmarks detected via geometry carry a coordinate limitation note."""
        via = []
        nearby = [
            {"landmark_id": "lm_x", "landmark_name": "LM X", "min_distance_m": 15.0,
             "is_prototype_unverified": True},
        ]
        _, visited = compute_route_landmark_relevance(via, {}, nearby_landmarks=nearby)
        self.assertEqual(len(visited), 1)
        self.assertIn("coordinate_note", visited[0])
        self.assertIn("unverified", visited[0]["coordinate_note"].lower())

    # ------------------------------------------------------------------
    # Real candidate data: load_candidate_coordinates returns 9 landmarks
    # ------------------------------------------------------------------
    def test_load_candidate_coordinates_preserves_original_and_selected_places(self):
        """New place records do not remove the original nine routing candidates."""
        coords = load_candidate_coordinates()
        self.assertEqual(len(coords), 13)
        self.assertTrue(LEGACY_LANDMARK_IDS.issubset(coords))
        for lid, c in coords.items():
            self.assertIn("lat", c)
            self.assertIn("lon", c)
            self.assertTrue(c["is_prototype_unverified"])

    # ------------------------------------------------------------------
    # Configurable threshold: larger threshold finds more, smaller finds fewer
    # ------------------------------------------------------------------
    def test_threshold_controls_detection_range(self):
        """Increasing the threshold widens detection; decreasing it narrows it."""
        route_coords = [[-73.978, 40.752], [-73.976, 40.752]]
        candidates = {
            "moderate": {
                "landmark_id": "moderate",
                "landmark_name": "Moderate Distance",
                "lat": 40.7524,  # ~44 m north
                "lon": -73.977,
                "verification_status": "candidate_unconfirmed",
                "is_prototype_unverified": True,
            },
        }
        # Tight threshold: should NOT find it
        nearby_tight = find_nearby_landmarks(route_coords, candidates, proximity_threshold_m=20.0)
        self.assertEqual(len(nearby_tight), 0)

        # Wide threshold: should find it
        nearby_wide = find_nearby_landmarks(route_coords, candidates, proximity_threshold_m=80.0)
        self.assertEqual(len(nearby_wide), 1)

    # ------------------------------------------------------------------
    # True minimum distance: early segment within threshold, later closer
    # ------------------------------------------------------------------
    def test_true_minimum_distance_evaluated_across_all_segments(self):
        """Calculates true minimum distance when an early segment is within threshold but a later segment is closer."""
        # 2 segments: V0 -> V1 (~43.6 m from candidate), V1 -> V2 (~11.1 m from candidate)
        route_coords = [
            [-73.978, 40.752],
            [-73.977, 40.752],
            [-73.976, 40.752],
        ]
        candidates = {
            "test_landmark": {
                "landmark_id": "test_landmark",
                "landmark_name": "Test Landmark",
                "lat": 40.7521,
                "lon": -73.9765,
                "verification_status": "candidate_unconfirmed",
                "is_prototype_unverified": True,
            },
        }
        # With threshold 60 m, early segment 1 is ~43.6 m away (< 60 m), but segment 2 is closer (~11.1 m).
        nearby = find_nearby_landmarks(route_coords, candidates, proximity_threshold_m=60.0)
        self.assertEqual(len(nearby), 1)
        self.assertEqual(nearby[0]["landmark_id"], "test_landmark")
        # Must report the true minimum distance (~11.1 m), not the early segment distance (~43.6 m).
        self.assertAlmostEqual(nearby[0]["min_distance_m"], 11.1, delta=0.5)

    # ------------------------------------------------------------------
    # Regression: geometry-detected landmarks affect landmark_count and scoring
    # ------------------------------------------------------------------
    def test_geometry_detected_landmarks_affect_landmark_count_and_scoring(self):
        """Geometry-detected landmarks update landmark_count, s_landmarks, and ranking."""
        route_a = self._make_route_fc("route_a", [[-73.978, 40.752], [-73.976, 40.752]], distance_m=500.0, duration_s=360.0)
        route_b = self._make_route_fc("route_b", [[-73.990, 40.760], [-73.988, 40.760]], distance_m=500.0, duration_s=360.0)

        candidates = {
            "geom_lm": {
                "landmark_id": "geom_lm",
                "landmark_name": "Geometry Landmark",
                "lat": 40.7521,
                "lon": -73.977,
                "verification_status": "candidate_unconfirmed",
                "is_prototype_unverified": True,
            },
        }

        # 1. Scoring with preference for more landmarks
        prefs = parse_preferences("I want to see as many landmarks as possible")
        scored = score_routes(
            [route_a, route_b],
            prefs,
            landmarks_meta={},
            candidate_coords=candidates,
            proximity_threshold_m=50.0,
        )

        by_id = {s.route_id: s for s in scored}
        self.assertEqual(by_id["route_a"].landmark_count, 1)
        self.assertEqual(by_id["route_b"].landmark_count, 0)
        self.assertEqual(by_id["route_a"].to_dict()["landmark_count"], 1)
        self.assertEqual(by_id["route_b"].to_dict()["landmark_count"], 0)
        self.assertEqual(by_id["route_a"].score_breakdown["landmarks_quantity"], 1.0)
        self.assertEqual(by_id["route_b"].score_breakdown["landmarks_quantity"], 0.0)
        self.assertEqual(scored[0].route_id, "route_a")
        self.assertGreater(by_id["route_a"].final_score, by_id["route_b"].final_score)

        # 2. Minimum landmark constraint enforcement
        prefs_min = parse_preferences("quick walk with at least one interesting landmark")
        scored_min = score_routes(
            [route_a, route_b],
            prefs_min,
            landmarks_meta={},
            candidate_coords=candidates,
            proximity_threshold_m=50.0,
        )
        by_id_min = {s.route_id: s for s in scored_min}
        self.assertEqual(by_id_min["route_a"].score_breakdown["landmarks_quantity"], 1.0)
        self.assertEqual(by_id_min["route_b"].score_breakdown["landmarks_quantity"], 0.0)
        self.assertEqual(scored_min[0].route_id, "route_a")

    # ------------------------------------------------------------------
    # Regression: origin, destination, and duplicate via excluded from count
    # ------------------------------------------------------------------
    def test_origin_dest_and_duplicates_excluded_from_landmark_count(self):
        """Origin, destination, and duplicate via-waypoints are strictly excluded from landmark_count."""
        route_coords = [[-73.978, 40.752], [-73.977, 40.752], [-73.976, 40.752]]
        via_wps = [
            {"landmark_id": "wp_1", "landmark_name": "Waypoint 1"},
            {"landmark_id": "wp_1", "landmark_name": "Duplicate Waypoint 1"},
            {"landmark_id": "origin_lm", "landmark_name": "Origin Waypoint"},
        ]
        route = self._make_route_fc(
            "test_route",
            route_coords,
            via_waypoints=via_wps,
            origin_id="origin_lm",
            dest_id="dest_lm",
        )

        candidates = {
            "origin_lm": {
                "landmark_id": "origin_lm", "landmark_name": "Origin",
                "lat": 40.752, "lon": -73.978, "verification_status": "candidate_unconfirmed", "is_prototype_unverified": True,
            },
            "dest_lm": {
                "landmark_id": "dest_lm", "landmark_name": "Dest",
                "lat": 40.752, "lon": -73.976, "verification_status": "candidate_unconfirmed", "is_prototype_unverified": True,
            },
            "wp_1": {
                "landmark_id": "wp_1", "landmark_name": "Waypoint 1",
                "lat": 40.7521, "lon": -73.9775, "verification_status": "candidate_unconfirmed", "is_prototype_unverified": True,
            },
            "geom_lm": {
                "landmark_id": "geom_lm", "landmark_name": "Geom LM",
                "lat": 40.7521, "lon": -73.9765, "verification_status": "candidate_unconfirmed", "is_prototype_unverified": True,
            },
        }

        prefs = parse_preferences("")
        scored = score_routes(
            [route],
            prefs,
            landmarks_meta={},
            candidate_coords=candidates,
            proximity_threshold_m=50.0,
        )

        res = scored[0]
        self.assertEqual(res.landmark_count, 2)
        visited_ids = [lm["landmark_id"] for lm in res.landmarks_visited]
        self.assertEqual(visited_ids, ["wp_1", "geom_lm"])
        self.assertNotIn("origin_lm", visited_ids)
        self.assertNotIn("dest_lm", visited_ids)
        self.assertEqual(len(set(visited_ids)), len(visited_ids))

    # ------------------------------------------------------------------
    # Regression: preserve existing behaviour for routes with only via-waypoints
    # ------------------------------------------------------------------
    def test_preserve_existing_behaviour_for_routes_with_only_via_waypoints(self):
        """Routes with only via-waypoints (no geometry candidates) preserve exact count and scoring."""
        route_1 = self._make_route_fc(
            "r1", [[-73.978, 40.752], [-73.976, 40.752]],
            via_waypoints=[{"landmark_id": "v1", "landmark_name": "V1"}, {"landmark_id": "v2", "landmark_name": "V2"}],
            distance_m=600.0, duration_s=400.0,
        )
        route_2 = self._make_route_fc(
            "r2", [[-73.978, 40.752], [-73.976, 40.752]],
            via_waypoints=[{"landmark_id": "v1", "landmark_name": "V1"}],
            distance_m=550.0, duration_s=380.0,
        )
        route_3 = self._make_route_fc(
            "r3", [[-73.978, 40.752], [-73.976, 40.752]],
            via_waypoints=[],
            distance_m=500.0, duration_s=360.0,
        )

        prefs = parse_preferences("I want to see as many interesting buildings as possible.")
        scored = score_routes(
            [route_1, route_2, route_3],
            prefs,
            landmarks_meta={},
            candidate_coords={},
        )

        by_id = {s.route_id: s for s in scored}
        self.assertEqual(by_id["r1"].landmark_count, 2)
        self.assertEqual(by_id["r2"].landmark_count, 1)
        self.assertEqual(by_id["r3"].landmark_count, 0)
        self.assertEqual(by_id["r1"].score_breakdown["landmarks_quantity"], 1.0)
        self.assertAlmostEqual(
            by_id["r2"].score_breakdown["landmarks_quantity"],
            0.6309297535714574,)
        self.assertEqual(by_id["r3"].score_breakdown["landmarks_quantity"], 0.0)
        self.assertEqual(scored[0].route_id, "r1")


if __name__ == "__main__":
    unittest.main()
