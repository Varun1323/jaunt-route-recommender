"""FastAPI backend for Jaunt walking route recommender.

Exposes landmark data, available walking routes, and the preference-based
recommendation engine over a JSON REST API.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import math
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Ensure the scripts directory is importable
_SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import recommendation_engine
import fetch_routes
import openrouter_client
import preference_parser

sys.modules["backend.scripts.recommendation_engine"] = recommendation_engine
sys.modules["backend.scripts.fetch_routes"] = fetch_routes
sys.modules["backend.scripts.openrouter_client"] = openrouter_client
sys.modules["backend.scripts.preference_parser"] = preference_parser

from backend.scripts.recommendation_engine import (
    PROTOTYPE_LIMITATION_DISCLOSURE,
    load_landmark_metadata,
    load_routes,
    recommend_route,
    score_routes,
)
from backend.scripts.fetch_routes import (
    MAX_ALTERNATIVES_PER_JOURNEY,
    RouteFetchError,
    fetch_all_routes,
    load_landmark_candidates,
    routes_are_meaningfully_distinct,
    select_alternative_candidates,
    waypoint_route_id,
)
from backend.scripts.openrouter_client import (
    OpenRouterClient,
    OpenRouterError,
    get_openrouter_config,
)
from backend.scripts.preference_parser import parse_preferences
from backend.scripts.v1_api import router as v1_router
# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "backend" / "data"
LANDMARKS_PATH = DATA_DIR / "landmarks.json"
ROUTES_DIR = DATA_DIR / "routes"
_journey_locks: dict[tuple[str, str], threading.RLock] = {}
_journey_locks_guard = threading.Lock()

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Jaunt Route Recommender API",
    description=(
        "Walking-route recommendation API for a trip between two Manhattan landmarks. "
        "Compares genuine pedestrian routes against traveler preferences."
    ),
    version="0.1.0",
)
app.include_router(v1_router)
# The API uses no cookies or credentials, so credentialed CORS is disabled.
# Override the allowed origins with JAUNT_CORS_ORIGINS (comma-separated).
_DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.environ.get("JAUNT_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class RecommendRequest(BaseModel):
    """Body for POST /api/recommend."""
    origin_id: str = Field(
        default="grand_central_terminal",
        description="Landmark ID for the journey origin.",
    )
    destination_id: str = Field(
        default="bryant_park",
        description="Landmark ID for the journey destination.",
    )
    preference: str = Field(
        default="",
        max_length=500,
        description="Free-text traveler preference query.",
    )
    via_landmark_ids: list[str] = Field(
        default_factory=list,
        max_length=7,
        description="Ordered intermediate landmarks; endpoints must not be repeated.",
    )


# ---------------------------------------------------------------------------
# Helper: load & validate landmark IDs
# ---------------------------------------------------------------------------
def _load_landmarks_list() -> list[dict[str, Any]]:
    """Return the full landmarks list from landmarks.json."""
    if not LANDMARKS_PATH.exists():
        raise HTTPException(status_code=500, detail="Landmarks data file not found.")
    try:
        data = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))
        return data.get("landmarks", [])
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Error reading landmarks data: {exc}")


def _get_valid_landmark_ids() -> set[str]:
    """Return the set of all known landmark IDs."""
    return {lm["id"] for lm in _load_landmarks_list() if "id" in lm}


def _load_selectable_landmarks() -> list[dict[str, Any]]:
    """Return only the eight landmarks exposed as user-selectable places."""
    return [
        landmark
        for landmark in _load_landmarks_list()
        if landmark.get("prototype_selection") is True
    ]


def _validate_landmark_ids(
    origin_id: str,
    destination_id: str,
    *,
    allow_loop: bool = False,
    via_landmark_ids: list[str] | None = None,
) -> None:
    """Raise HTTPException for unknown or identical landmark IDs."""
    valid_ids = _get_valid_landmark_ids()

    if origin_id not in valid_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown origin landmark ID '{origin_id}'. Available: {sorted(valid_ids)}",
        )
    if destination_id not in valid_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown destination landmark ID '{destination_id}'. Available: {sorted(valid_ids)}",
        )
    if origin_id == destination_id and not allow_loop:
        raise HTTPException(
            status_code=422,
            detail=f"Origin and destination cannot be the same landmark ('{origin_id}').",
        )

    if via_landmark_ids:
        selectable_ids = {
            landmark["id"] for landmark in _load_selectable_landmarks()
        }
        if len(set(via_landmark_ids)) != len(via_landmark_ids):
            raise HTTPException(status_code=422, detail="Intermediate landmarks cannot be repeated.")
        if any(landmark_id in {origin_id, destination_id} for landmark_id in via_landmark_ids):
            raise HTTPException(
                status_code=422,
                detail="Intermediate landmarks must be different from the selected endpoints.",
            )
        invalid_ids = [landmark_id for landmark_id in via_landmark_ids if landmark_id not in selectable_ids]
        if invalid_ids:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown or unavailable intermediate landmark IDs: {invalid_ids}",
            )


def _get_journey_lock(origin_id: str, destination_id: str) -> threading.RLock:
    """Return a process-local lock shared by all requests for this directed journey."""
    key = (origin_id, destination_id)
    with _journey_locks_guard:
        return _journey_locks.setdefault(key, threading.RLock())


_live_fetch_lock = threading.Lock()


def _live_fetch_routes(**kwargs: Any) -> Any:
    """Run a live Valhalla route fetch, one at a time across all requests.

    Serializing keeps the hosted routing service from being hit in parallel.
    Set JAUNT_OFFLINE=1 to serve cached routes only (no live requests and no
    writes to backend/data).
    """
    if os.environ.get("JAUNT_OFFLINE") == "1":
        raise RouteFetchError("Live route generation is currently unavailable.")
    with _live_fetch_lock:
        return fetch_all_routes(**kwargs)


def _ensure_journey_routes(origin_id: str, destination_id: str) -> list[dict[str, Any]]:
    """Return the validated cached route set, generating a direct route on a cache miss."""
    with _get_journey_lock(origin_id, destination_id):
        routes = load_routes(
            ROUTES_DIR, origin_id=origin_id, destination_id=destination_id,
            strict_validation=True,
        )
        if routes:
            return routes
        try:
            generation_results = _live_fetch_routes(
                origin_id=origin_id,
                destination_id=destination_id,
                route_filter="direct",
                skip_existing=True,
            )
        except (RouteFetchError, OSError, ValueError) as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Unable to generate a valid pedestrian route for '{origin_id}' to '{destination_id}': {exc}",
            ) from exc
        routes = load_routes(
            ROUTES_DIR, origin_id=origin_id, destination_id=destination_id,
            strict_validation=True,
        )
        if not routes:
            raise HTTPException(
                status_code=502,
                detail=f"Valhalla did not produce a valid cached route for '{origin_id}' to '{destination_id}'.",
            )
        return routes


def _route_properties(route: dict[str, Any]) -> dict[str, Any]:
    return route["features"][0].get("properties", route.get("metadata", {}))


def _select_candidate_routes(
    routes: list[dict[str, Any]],
    preferences: Any,
    origin_id: str,
    destination_id: str,
    via_landmark_ids: list[str],
) -> list[dict[str, Any]]:
    """Choose at most four distinct cached routes before scoring."""
    candidates = []
    for route in routes:
        properties = _route_properties(route)
        if (
            properties.get("origin_landmark_id") != origin_id
            or properties.get("destination_landmark_id") != destination_id
        ):
            continue
        if origin_id == destination_id and not properties.get("via_waypoints"):
            continue
        candidates.append(route)

    def via_ids(route: dict[str, Any]) -> list[str]:
        return [
            waypoint.get("landmark_id")
            for waypoint in _route_properties(route).get("via_waypoints", [])
            if isinstance(waypoint, dict) and isinstance(waypoint.get("landmark_id"), str)
        ]

    metadata = load_landmark_metadata()

    def relevance(route: dict[str, Any]) -> tuple[int, int]:
        ids = via_ids(route)
        exact_match = int(bool(via_landmark_ids) and ids == via_landmark_ids)
        preferred_ids = set(preferences.preferred_landmark_ids)
        categories = set(preferences.preferred_categories)
        category_matches = sum(
            bool(categories.intersection(metadata.get(landmark_id, {}).get("categories", [])))
            for landmark_id in ids
        )
        preferred_matches = len(preferred_ids.intersection(ids))
        theme_matches = sum(
            bool(
                (preferences.architecture > 0 and "architecture" in metadata.get(landmark_id, {}).get("categories", []))
                or (preferences.history > 0 and "historic_landmark" in metadata.get(landmark_id, {}).get("categories", []))
            )
            for landmark_id in ids
        )
        return exact_match, category_matches + preferred_matches + theme_matches

    unique: list[dict[str, Any]] = []

    def add_if_distinct(route: dict[str, Any]) -> None:
        # Part 2 requires comparing two or three possible routes.
        # Never return more than three meaningfully distinct candidates.
        if len(unique) >= 3 or route in unique:
            return
        if all(routes_are_meaningfully_distinct(route, existing) for existing in unique):
            unique.append(route)

    direct = next(
        (route for route in candidates if _route_properties(route).get("route_id") == "direct"),
        None,
    )
    if direct is not None and origin_id != destination_id:
        add_if_distinct(direct)

    exact_routes = sorted(
        (route for route in candidates if via_landmark_ids and via_ids(route) == via_landmark_ids),
        key=lambda route: str(_route_properties(route).get("route_id", "")),
    )
    for route in exact_routes:
        add_if_distinct(route)

    def candidate_key(route: dict[str, Any]) -> tuple[Any, ...]:
        props = _route_properties(route)
        exact_match, fit = relevance(route)
        distance = float(props.get("distance_m", float("inf")))
        duration = float(props.get("estimated_duration_s", float("inf")))
        efficiency = (distance, duration)
        over_budget = (
            int(duration > preferences.walking_time_budget_minutes * 60.0)
            if preferences.walking_time_budget_minutes is not None
            else 0
        )
        return (
            -exact_match,
            over_budget,
            -fit,
            *(efficiency if preferences.route_priority == "efficiency" else ()),
            str(props.get("route_id", "")),
        )

    for route in sorted(candidates, key=candidate_key):
        add_if_distinct(route)
    return unique


def _loop_waypoint_sequences(
    origin_id: str,
    selected_landmarks: list[dict[str, Any]],
    preferences: Any,
    explicit_waypoints: list[str],
) -> list[list[str]]:
    """Plan up to three ordered, preference-relevant loop stop sequences."""
    catalog = {landmark["id"]: landmark for landmark in selected_landmarks}
    candidate_locations = load_landmark_candidates()
    options = [
        landmark_id
        for landmark_id in catalog
        if landmark_id != origin_id and landmark_id in candidate_locations
    ]
    preferred_order = {
        landmark_id: index
        for index, landmark_id in enumerate(preferences.preferred_landmark_ids)
    }
    preferred_categories = set(preferences.preferred_categories)

    def option_key(landmark_id: str) -> tuple[Any, ...]:
        landmark = catalog[landmark_id]
        category_match = len(preferred_categories.intersection(landmark.get("categories", [])))
        return (
            -int(landmark_id in preferred_order),
            -category_match,
            preferred_order.get(landmark_id, len(preferred_order)),
            landmark_id,
        )

    options.sort(key=option_key)
    if explicit_waypoints:
        sequences = [list(explicit_waypoints)]
        for landmark_id in options:
            if landmark_id in explicit_waypoints:
                continue
            sequences.append([*explicit_waypoints, landmark_id])
            if len(sequences) == 3:
                break
        return sequences
    return [[landmark_id] for landmark_id in options[:3]]



def _sightseeing_waypoint_sequences(
    origin_id: str,
    destination_id: str,
    selected_landmarks: list[dict[str, Any]],
    preferences: Any,
    explicit_waypoints: list[str],
) -> list[list[str]]:
    """Plan distinct, preference-relevant waypoint sequences within a journey corridor."""
    catalog = {
        landmark["id"]: landmark
        for landmark in selected_landmarks
        if isinstance(landmark.get("id"), str)
    }
    candidates = load_landmark_candidates()
    selectable_ids = set(catalog)

    # Explicit user choices take priority and retain their exact order.
    if explicit_waypoints:
        explicit = [
            landmark_id
            for landmark_id in explicit_waypoints
            if landmark_id not in {origin_id, destination_id}
            and landmark_id in selectable_ids
            and landmark_id in candidates
        ]
        return [explicit] if explicit else []

    origin = candidates.get(origin_id)
    destination = candidates.get(destination_id)

    if not origin or not destination:
        return []

    o_lat, o_lon = float(origin["lat"]), float(origin["lon"])
    d_lat, d_lon = float(destination["lat"]), float(destination["lon"])

    mean_lat = math.radians((o_lat + d_lat) / 2.0)
    metres_per_lon = 111_320.0 * math.cos(mean_lat)
    metres_per_lat = 111_320.0

    dx = (d_lon - o_lon) * metres_per_lon
    dy = (d_lat - o_lat) * metres_per_lat
    direct_distance = math.hypot(dx, dy)

    if direct_distance < 1.0:
        return []

    # A broader corridor accommodates nearby Manhattan blocks while
    # still excluding landmarks that require substantial backtracking.
    max_lateral_distance_m = 400.0
    max_extra_distance_m = 750.0
    max_detour_ratio = 2.0

    preferred_categories = set(preferences.preferred_categories)
    excluded_categories = set(preferences.excluded_categories)

    def point_metrics(landmark_id: str) -> tuple[float, float, float]:
        point = candidates[landmark_id]

        px = (float(point["lon"]) - o_lon) * metres_per_lon
        py = (float(point["lat"]) - o_lat) * metres_per_lat

        projection = (px * dx + py * dy) / (direct_distance ** 2)

        projected_x = projection * dx
        projected_y = projection * dy

        lateral = math.hypot(
            px - projected_x,
            py - projected_y,
        )

        origin_leg = math.hypot(px, py)
        destination_leg = math.hypot(dx - px, dy - py)

        extra_distance = max(
            0.0,
            origin_leg + destination_leg - direct_distance,
        )

        return projection, lateral, extra_distance

    def is_geographically_relevant(landmark_id: str) -> bool:
        if landmark_id not in candidates or landmark_id not in catalog:
            return False

        if landmark_id in {origin_id, destination_id}:
            return False

        categories = set(catalog[landmark_id].get("categories", []))

        if categories.intersection(excluded_categories):
            return False

        projection, lateral, extra_distance = point_metrics(landmark_id)

        return (
            -0.30 <= projection <= 1.20
            and lateral <= max_lateral_distance_m
            and extra_distance <= max_extra_distance_m
            and extra_distance <= direct_distance * (max_detour_ratio - 1.0)
        )

    def relevance_score(landmark_id: str) -> float:
        categories = set(catalog[landmark_id].get("categories", []))
        score = 0.0

        if preferences.architecture > 0 and "architecture" in categories:
            score += 3.0 * preferences.architecture

        if preferences.history > 0 and categories.intersection(
            {"historic_landmark", "historic_site", "heritage"}
        ):
            score += 3.0 * preferences.history

        if preferences.more_landmarks > 0:
            score += preferences.more_landmarks

        if preferences.primary_interest in categories:
            score += 2.0

        score += 1.5 * len(preferred_categories.intersection(categories))

        if landmark_id in preferences.preferred_landmark_ids:
            score += 2.5

        # Recognize equivalent catalogue classifications for historical places.
        if preferences.history > 0 and categories.intersection(
            {"historic_landmark", "historic_site", "heritage"}
        ):
            score += 1.0 * preferences.history

        # Default fallback: prefer places with an identifiable visitor interest.
        if score == 0.0:
            interesting_categories = {
                "architecture",
                "historic_landmark",
                "historic_site",
                "heritage",
                "art",
                "culture",
                "public_space",
                "park",
                "nature",
                "entertainment",
            }
            if categories.intersection(interesting_categories):
                score = 0.5

        return score

    eligible = [
        landmark_id
        for landmark_id in catalog
        if is_geographically_relevant(landmark_id)
        and relevance_score(landmark_id) > 0
    ]

    def candidate_key(landmark_id: str) -> tuple[float, float, float, str]:
        _, lateral, extra_distance = point_metrics(landmark_id)
        return (
            -relevance_score(landmark_id),
            extra_distance,
            lateral,
            landmark_id,
        )

    eligible.sort(key=candidate_key)
    budget = preferences.walking_time_budget_minutes
    max_waypoints = 2

    if budget is not None:
        if budget >= 25.0:
            max_waypoints = 3
        elif budget < 10.0:
            max_waypoints = 1
    elif (
        preferences.architecture == 0
        and preferences.history == 0
        and preferences.more_landmarks == 0
        and not preferred_categories
        and not preferences.preferred_landmark_ids
        and not preferences.primary_interest
    ):
        max_waypoints = 1

    def sequence_is_valid(sequence: list[str]) -> bool:
        if not sequence:
            return False

        if len(sequence) != len(set(sequence)):
            return False

        if any(not is_geographically_relevant(lid) for lid in sequence):
            return False

        # Estimate the total journey through all stops, not just each
        # stop's individual detour from the direct journey.
        ordered = sorted(
            sequence,
            key=lambda lid: point_metrics(lid)[0],
        )

        points = [origin, *[candidates[lid] for lid in ordered], destination]

        total_distance = 0.0
        for first, second in zip(points, points[1:]):
            lat1, lon1 = float(first["lat"]), float(first["lon"])
            lat2, lon2 = float(second["lat"]), float(second["lon"])

            leg_x = (lon2 - lon1) * metres_per_lon
            leg_y = (lat2 - lat1) * metres_per_lat

            total_distance += math.hypot(leg_x, leg_y)

        extra_distance = max(0.0, total_distance - direct_distance)

        return (
            extra_distance <= max_extra_distance_m
            and total_distance <= direct_distance * max_detour_ratio
        )

    def ordered_sequence(sequence: list[str]) -> list[str]:
        return sorted(
            sequence,
            key=lambda lid: point_metrics(lid)[0],
        )

    sequences: list[list[str]] = []


    def add_sequence(sequence: list[str]) -> None:
        ordered = ordered_sequence(sequence)

        # AI-generated and locally generated sequences must respect
        # the same waypoint limit before any Valhalla request is made.
        if len(ordered) > max_waypoints:
            return

        if not sequence_is_valid(ordered):
            return

        if ordered in sequences or len(sequences) >= 3:
            return

        sequences.append(ordered)


    # An AI-proposed primary sequence is considered first, but it must
    # pass the same geographic and aggregate-detour checks.
    ai_primary = [
        lid
        for lid in preferences.sightseeing_waypoint_ids
        if lid in selectable_ids
        and lid not in {origin_id, destination_id}
    ]

    if ai_primary:
        add_sequence(ai_primary)

    # Otherwise select the best locally generated primary sequence.
    if not sequences:
        primary = eligible[:max_waypoints]
        add_sequence(primary)

    # Validate and include AI-generated additional alternatives.
    for proposed in preferences.additional_sightseeing_sequences:
        if len(sequences) >= 3:
            break

        valid = list(dict.fromkeys(
            lid
            for lid in proposed
            if lid in selectable_ids
            and lid not in {origin_id, destination_id}
        ))

        if valid:
            add_sequence(valid)

    # The direct route is already one candidate.
    # Generate only the additional sightseeing routes that can be displayed.
    requested_count = min(
        MAX_ALTERNATIVES_PER_JOURNEY,
        max(1, preferences.requested_route_count - 1),
    )

    # Generate genuinely different single-stop alternatives.
    for landmark_id in eligible:
        if len(sequences) >= requested_count or len(sequences) >= 3:
            break

        add_sequence([landmark_id])

    # If needed, generate distinct two-stop combinations.
    if len(sequences) < requested_count and max_waypoints >= 2:
        for first_index, first in enumerate(eligible):
            for second in eligible[first_index + 1:]:
                if len(sequences) >= requested_count or len(sequences) >= 3:
                    break

                add_sequence([first, second])

            if len(sequences) >= requested_count or len(sequences) >= 3:
                break

    return sequences[:3]



def _route_strategy_context(
    routes: list[dict[str, Any]],
    preferences: Any,
) -> tuple[list[dict[str, Any]], list[str], str, int]:
    """Derive safe strategy choices from validated cached route geometry and metrics."""
    scored = score_routes(routes, preferences)
    summaries = [
        {
            "route_id": route.route_id,
            "route_name": route.route_name,
            "distance_m": round(route.distance_m, 1),
            "estimated_duration_minutes": round(route.duration_minutes, 1),
            "landmarks": [
                {
                    "name": place["landmark_name"],
                    "categories": place.get("categories", []),
                }
                for place in route.landmarks_visited
            ],
            "constraint_status": route.to_dict()["constraint_status"],
        }
        for route in scored
    ]

    slots = max(0, MAX_ALTERNATIVES_PER_JOURNEY - max(0, len(routes) - 1))
    if slots == 0:
        return summaries, ["none"], "none", 0

    place_interest = (
        preferences.architecture > 0
        or preferences.history > 0
        or preferences.more_landmarks > 0
        or preferences.min_landmarks > 0
        or bool(preferences.excluded_categories)
        or bool(preferences.preferred_categories)
        or bool(preferences.preferred_landmark_ids)
        or bool(preferences.requested_landmark_ids)
        or preferences.walking_time_budget_minutes is not None
    )
    existing_places = [place for route in scored for place in route.landmarks_visited]
    relevant_place_exists = any(
        (
            preferences.architecture > 0
            and "architecture" in place.get("categories", [])
        )
        or (
            preferences.history > 0
            and "historic_landmark" in place.get("categories", [])
        )
        or (
            (preferences.more_landmarks > 0 or preferences.min_landmarks > 0)
            and bool(place.get("landmark_id"))
        )
        or bool(
            set(preferences.preferred_categories).intersection(place.get("categories", []))
        )
        or place.get("landmark_id") in preferences.preferred_landmark_ids
        or place.get("landmark_id") in preferences.requested_landmark_ids
        for place in existing_places
    )
    needs_place_alternative = place_interest and (
        len(routes) == 1 or not relevant_place_exists
    )

    has_feasible_route = any(route.is_feasible for route in scored)
    needs_efficiency_alternative = (
        preferences.shortest_distance > 0
        or preferences.minimum_walking_time > 0
        or preferences.max_distance_m is not None
    ) and len(routes) == 1
    if preferences.max_duration_minutes is not None and not has_feasible_route:
        needs_efficiency_alternative = True

    has_exclusion_satisfying_route = any(
        route.constraint_status.get("exclusions", True) for route in scored
    )
    needs_exclusion_alternative = (
        bool(preferences.excluded_categories)
        and not has_exclusion_satisfying_route
    )
    if needs_exclusion_alternative:
        needs_efficiency_alternative = True

    justified: list[str] = []
    if needs_place_alternative:
        justified.append("landmark")
    if needs_efficiency_alternative:
        justified.append("native")

    choices = ["none", *justified]
    if len(justified) == 2 and slots >= 2:
        choices.append("both")

    if len(justified) == 2:
        if slots >= 2:
            fallback_strategy = "both"
        elif (
            preferences.max_duration_minutes is not None
            and not has_feasible_route
        ) or needs_exclusion_alternative:
            fallback_strategy = "native"
        else:
            fallback_strategy = "landmark"
    elif justified:
        fallback_strategy = justified[0]
    else:
        fallback_strategy = "none"

    return summaries, choices, fallback_strategy, min(slots, MAX_ALTERNATIVES_PER_JOURNEY)




# ---------------------------------------------------------------------------
# GET /health & GET /api/health
# ---------------------------------------------------------------------------
@app.get("/health")
@app.get("/api/health")
def health_check() -> dict[str, str]:
    """Health check endpoint returning service status."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /api/landmarks
# ---------------------------------------------------------------------------
@app.get("/api/landmarks")
def get_landmarks() -> dict[str, Any]:
    """Return the eight selected prototype places without removing research records."""
    landmarks = [
        {
            **landmark,
            "name": landmark.get("prototype_display_name", landmark.get("name")),
        }
        for landmark in _load_selectable_landmarks()
    ]
    return {
        "count": len(landmarks),
        "landmarks": landmarks,
        "limitation_disclosure": PROTOTYPE_LIMITATION_DISCLOSURE,
    }


# ---------------------------------------------------------------------------
# GET /api/routes
# ---------------------------------------------------------------------------
@app.get("/api/routes")
def get_routes(
    origin_id: str = Query(..., description="Origin landmark ID."),
    destination_id: str = Query(..., description="Destination landmark ID."),
) -> dict[str, Any]:
    """Return available walking routes for the given landmark pair."""
    _validate_landmark_ids(origin_id, destination_id)

    routes = _ensure_journey_routes(origin_id, destination_id)

    # Build a compact summary for each route
    result_routes: list[dict[str, Any]] = []
    for fc in routes:
        feat = fc["features"][0]
        props = feat.get("properties", fc.get("metadata", {}))
        result_routes.append({
            "route_id": props.get("route_id", fc.get("name", "unknown")),
            "route_name": props.get("route_name", ""),
            "route_description": props.get("route_description", ""),
            "distance_m": props.get("distance_m"),
            "estimated_duration_s": props.get("estimated_duration_s"),
            "estimated_duration_minutes": round(
                float(props.get("estimated_duration_s", 0)) / 60.0, 1
            ),
            "via_waypoints": props.get("via_waypoints", []),
            "verification_status": props.get("verification_status", "unknown"),
            "geojson": fc,
        })
    # Prioritize the direct route and landmark-based alternatives.
    # Generic native alternatives are used only when fewer than three
    # preferred routes are available.
    result_routes.sort(
        key=lambda route: (
            route["route_id"] != "direct",
            not bool(route["via_waypoints"]),
        )
    )

    # Return at most three routes.
    result_routes = result_routes[:3]
    return {
        "origin_id": origin_id,
        "destination_id": destination_id,
        "route_count": len(result_routes),
        "routes": result_routes,
        "limitation_disclosure": PROTOTYPE_LIMITATION_DISCLOSURE,
    }


# ---------------------------------------------------------------------------
# POST /api/recommend
# ---------------------------------------------------------------------------
@app.post("/api/recommend")
@app.post("/api/v2/recommend")
def post_recommend(body: RecommendRequest) -> dict[str, Any]:
    """Build up to three validated candidates, then recommend one deterministically."""
    _validate_landmark_ids(
        body.origin_id,
        body.destination_id,
        allow_loop=True,
    )
    selected_landmarks = _load_selectable_landmarks()
    selectable_ids = {landmark["id"] for landmark in selected_landmarks}

    with _get_journey_lock(body.origin_id, body.destination_id):
        local_preferences = parse_preferences(body.preference, selected_landmarks)
        spoken_waypoints = [
            landmark_id
            for landmark_id in local_preferences.requested_landmark_ids
            if landmark_id not in {body.origin_id, body.destination_id}
        ]
        explicit_waypoints = list(body.via_landmark_ids)
        explicit_waypoints.extend(
            landmark_id for landmark_id in spoken_waypoints
            if landmark_id not in explicit_waypoints
        )
        _validate_landmark_ids(
            body.origin_id,
            body.destination_id,
            allow_loop=True,
            via_landmark_ids=explicit_waypoints,
        )
        if any(landmark_id not in selectable_ids for landmark_id in explicit_waypoints):
            raise HTTPException(
                status_code=422,
                detail="Intermediate places must be among the eight selected landmarks.",
            )
        if body.origin_id == body.destination_id and not explicit_waypoints:
            explicit_waypoints = []

        preferences = local_preferences
        openrouter_client: OpenRouterClient | None = None
        provider_preferences_used = False
        ai_status = {
            "provider": "openrouter",
            "status": "unconfigured",
            "message": "OpenRouter API key is not configured; using local preference matching.",
        }

        config = get_openrouter_config()
        if config.is_configured:
            openrouter_client = OpenRouterClient(config)
            try:
                cached_routes_for_planning = load_routes(
                    ROUTES_DIR,
                    origin_id=body.origin_id,
                    destination_id=body.destination_id,
                    strict_validation=False,
                )
                preferences = openrouter_client.parse_preferences(
                    body.preference,
                    selected_landmarks,
                    body.origin_id,
                    body.destination_id,
                    cached_routes_for_planning,
                )
                provider_preferences_used = True
                ai_status = {
                    "provider": "openrouter",
                    "status": "active",
                    "message": (
                        "Preference interpreted by OpenRouter."
                        if body.preference.strip()
                        else "Default sightseeing planned by OpenRouter."
                    ),
                }
            except OpenRouterError as exc:
                ai_status = {
                    "provider": "openrouter",
                    "status": "fallback",
                    "message": f"{exc} Using local preference matching.",
                }

        spoken_waypoints = [
            landmark_id
            for landmark_id in preferences.requested_landmark_ids
            if landmark_id not in {body.origin_id, body.destination_id}
        ]
        explicit_waypoints = list(body.via_landmark_ids)
        explicit_waypoints.extend(
            landmark_id for landmark_id in spoken_waypoints
            if landmark_id not in explicit_waypoints
        )
        if len(set(explicit_waypoints)) != len(explicit_waypoints):
            raise HTTPException(status_code=422, detail="Intermediate landmarks cannot be repeated.")
        if any(landmark_id not in selectable_ids for landmark_id in explicit_waypoints):
            raise HTTPException(
                status_code=422,
                detail="Intermediate places must be among the eight selected landmarks.",
            )
        if body.origin_id == body.destination_id and not explicit_waypoints:
            preferences.requested_landmark_ids = []
        else:
            preferences.requested_landmark_ids = list(explicit_waypoints)

        is_loop = body.origin_id == body.destination_id
        if is_loop:
            routes = load_routes(
                ROUTES_DIR,
                origin_id=body.origin_id,
                destination_id=body.destination_id,
                strict_validation=True,
            )
        else:
            routes = _ensure_journey_routes(body.origin_id, body.destination_id)

        route_ids_before = {
            str(_route_properties(route).get("route_id", route.get("name", "")))
            for route in routes
        }
        route_generation = {
            "strategy": "none",
            "status": "reused",
            "reason": "Validated cached routes are reused before considering additional candidates.",
            "generated_route_ids": [],
            "reused_route_count": len(routes),
            "available_route_count": len(routes),
            "failure_count": 0,
        }
        generation_errors: list[str] = []
        generation_disabled = (
            os.environ.get("JAUNT_DISABLE_PREFERENCE_ROUTE_GENERATION") == "1"
        )

        if is_loop:
            try:
                sequences = _loop_waypoint_sequences(
                    body.origin_id,
                    selected_landmarks,
                    preferences,
                    explicit_waypoints,
                )
                cached_loop_count = sum(
                    bool(_route_properties(route).get("via_waypoints"))
                    for route in routes
                )
                if cached_loop_count >= 3:
                    route_generation.update({
                        "strategy": "none",
                        "reason": "Three validated loop candidates are already cached.",
                    })
                elif generation_disabled:
                    route_generation.update({
                        "status": "disabled",
                        "reason": "On-demand route generation is disabled by server configuration.",
                    })
                else:
                    route_generation["strategy"] = "landmark"
                    for sequence in sequences:
                        loop_route_count = sum(
                            bool(_route_properties(route).get("via_waypoints"))
                            for route in routes
                        )
                        if loop_route_count >= 3:
                            break
                        route_id = waypoint_route_id(
                            body.origin_id, body.destination_id, sequence
                        )
                        if any(
                            _route_properties(route).get("route_id") == route_id
                            for route in routes
                        ):
                            continue
                        try:
                            _live_fetch_routes(
                                origin_id=body.origin_id,
                                destination_id=body.destination_id,
                                route_filter=route_id,
                                via_ids=sequence,
                                skip_existing=True,
                                routes_dir=ROUTES_DIR,
                            )
                        except (RouteFetchError, OSError, ValueError) as exc:
                            generation_errors.append(str(exc))
                        routes = load_routes(
                            ROUTES_DIR,
                            origin_id=body.origin_id,
                            destination_id=body.destination_id,
                            strict_validation=True,
                        )
            except RouteFetchError as exc:
                generation_errors.append(str(exc))
        else:
            sequences = _sightseeing_waypoint_sequences(
                body.origin_id,
                body.destination_id,
                selected_landmarks,
                preferences,
                explicit_waypoints,
            )
            route_generation["strategy"] = "landmark" if sequences else "none"
            route_generation["requested_waypoint_sequences"] = sequences
            route_generation["failed_waypoint_sequences"] = []

            if generation_disabled:
                route_generation.update({
                    "status": "disabled",
                    "reason": "On-demand route generation is disabled by server configuration.",
                })
            else:
                for sequence in sequences:
                    route_id = waypoint_route_id(
                        body.origin_id, body.destination_id, sequence
                    )
                    if any(
                        _route_properties(route).get("route_id") == route_id
                        for route in routes
                    ):
                        continue
                    try:
                        _live_fetch_routes(
                            origin_id=body.origin_id,
                            destination_id=body.destination_id,
                            route_filter=route_id,
                            via_ids=sequence,
                            skip_existing=True,
                            routes_dir=ROUTES_DIR,
                        )
                    except (RouteFetchError, OSError, ValueError) as exc:
                        generation_errors.append(str(exc))
                        route_generation["failed_waypoint_sequences"].append({
                            "waypoint_ids": sequence,
                            "error": str(exc),
                        })
                    routes = load_routes(
                        ROUTES_DIR,
                        origin_id=body.origin_id,
                        destination_id=body.destination_id,
                        strict_validation=True,
                    )
                    if not any(
                        _route_properties(route).get("route_id") == route_id
                        for route in routes
                    ) and not any(
                        failure["waypoint_ids"] == sequence
                        for failure in route_generation["failed_waypoint_sequences"]
                    ):
                        error = "Valhalla route was unavailable or rejected as duplicate geometry."
                        generation_errors.append(error)
                        route_generation["failed_waypoint_sequences"].append({
                            "waypoint_ids": sequence,
                            "error": error,
                        })

        # If landmark-based generation does not provide at least two
        # meaningfully distinct candidates, try native Valhalla alternatives.
        # Do not override an explicitly requested sightseeing waypoint.
        if (
            not is_loop
            and not explicit_waypoints
            and not generation_disabled
        ):
            current_candidates = _select_candidate_routes(
                routes,
                preferences,
                body.origin_id,
                body.destination_id,
                explicit_waypoints,
            )

            if len(current_candidates) < 3:
                try:
                    native_route_ids_before = {
                        str(_route_properties(route).get("route_id", route.get("name", "")))
                        for route in routes
                    }

                    _live_fetch_routes(
                        origin_id=body.origin_id,
                        destination_id=body.destination_id,
                        route_filter="all",
                        alternative_strategy="native",
                        max_alternatives=MAX_ALTERNATIVES_PER_JOURNEY,
                        skip_existing=False,
                        routes_dir=ROUTES_DIR,
                    )

                    routes = load_routes(
                        ROUTES_DIR,
                        origin_id=body.origin_id,
                        destination_id=body.destination_id,
                        strict_validation=True,
                    )

                    native_route_ids_after = {
                        str(_route_properties(route).get("route_id", route.get("name", "")))
                        for route in routes
                    }

                    native_fallback_generated_ids = (
                        native_route_ids_after - native_route_ids_before
                    )

                    if native_fallback_generated_ids:
                        route_generation["strategy"] = "native_fallback"

                except (RouteFetchError, OSError, ValueError) as exc:
                    generation_errors.append(
                        f"Native alternative fallback failed: {exc}"
                    )

        route_ids_after = {
            str(_route_properties(route).get("route_id", route.get("name", "")))
            for route in routes
        }
        newly_cached_ids = route_ids_after - route_ids_before
        candidate_routes = _select_candidate_routes(
            routes,
            preferences,
            body.origin_id,
            body.destination_id,
            explicit_waypoints,
        )
        candidate_route_ids = {
            str(_route_properties(route).get("route_id", ""))
            for route in candidate_routes
        }
        generated_ids = sorted(newly_cached_ids.intersection(candidate_route_ids))
        if explicit_waypoints and generated_ids and not is_loop:
            route_generation["strategy"] = "landmark"
        if generated_ids:
            route_generation["status"] = (
                "generated_with_errors" if generation_errors else "generated"
            )
            route_generation["reason"] = (
                f"Added {len(generated_ids)} validated route candidate(s); "
                f"{len(generation_errors)} generation operation(s) failed."
                if generation_errors
                else "Added validated route candidates from actual Valhalla pedestrian responses."
            )
        elif generation_errors:
            route_generation["status"] = "generation_failed"
            route_generation["reason"] = (
                f"No new candidate was added; using available validated routes. "
                f"{generation_errors[0]}"
            )
            route_generation["failure_count"] = len(generation_errors)
        elif route_generation["status"] != "disabled":
            route_generation["status"] = (
                "no_meaningful_alternative"
                if route_generation["strategy"] != "none"
                else "reused"
            )
            if route_generation["strategy"] != "none":
                route_generation["reason"] = (
                    "No additional validated, meaningfully distinct route was available."
                )

        if not candidate_routes:
            raise HTTPException(
                status_code=502 if is_loop else 404,
                detail=(
                    "No validated pedestrian loop is available; an intermediate place is required."
                    if is_loop
                    else f"No validated routes found for '{body.origin_id}' to '{body.destination_id}'."
                ),
            )

        try:
            result = recommend_route(
                query=body.preference,
                preferences=preferences,
                origin_id=body.origin_id,
                destination_id=body.destination_id,
                routes_dir=ROUTES_DIR,
                strict_route_validation=True,
                route_candidates=candidate_routes,
            )
        except ValueError as exc:
            msg = str(exc)
            if "no routes found" in msg.lower():
                raise HTTPException(status_code=404, detail=msg)
            raise HTTPException(status_code=422, detail=msg)

        selected_ids = {result["recommended_route"]["route_id"]} | {
            alternative["route_id"]
            for alternative in result["ranked_alternatives"]
        }
        result["candidate_routes"] = [
            {
                "route_id": _route_properties(route).get("route_id"),
                "route_name": _route_properties(route).get("route_name", ""),
                "route_description": _route_properties(route).get("route_description", ""),
                "distance_m": _route_properties(route).get("distance_m"),
                "estimated_duration_s": _route_properties(route).get("estimated_duration_s"),
                "estimated_duration_minutes": round(
                    float(_route_properties(route).get("estimated_duration_s", 0)) / 60.0,
                    1,
                ),
                "via_waypoints": _route_properties(route).get("via_waypoints", []),
                "verification_status": _route_properties(route).get(
                    "verification_status", "unknown"
                ),
                "geojson": route,
                "recommended": _route_properties(route).get("route_id")
                == result["recommended_route"]["route_id"],
                "rank": (
                    1
                    if _route_properties(route).get("route_id")
                    == result["recommended_route"]["route_id"]
                    else next(
                        (
                            index + 2
                            for index, alternative in enumerate(result["ranked_alternatives"])
                            if alternative["route_id"]
                            == _route_properties(route).get("route_id")
                        ),
                        None,
                    )
                ),
            }
            for route in candidate_routes
            if _route_properties(route).get("route_id") in selected_ids
        ]
        route_generation["generated_route_ids"] = generated_ids
        route_generation["reused_route_count"] = len(route_ids_before)
        route_generation["available_route_count"] = len(result["candidate_routes"])
        route_generation["failure_count"] = len(generation_errors)



        result["ai_status"] = ai_status
        result["route_generation"] = route_generation
        return result


# ---------------------------------------------------------------------------
# Optional: serve the built frontend (frontend/dist) from the same origin.
# Mounted last so every /api and /health route above takes precedence.
# ---------------------------------------------------------------------------
_FRONTEND_DIST = ROOT / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
