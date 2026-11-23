"""Recommendation engine for Jaunt walking route recommender.

Evaluates available walking routes (from GeoJSON files) against structured traveler preferences,
producing deterministic scores, ranking, and human-readable explanations.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from preference_parser import UserPreferences, parse_preferences
from fetch_routes import RouteFetchError, load_landmark_candidates, validate_cached_route

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "backend" / "data"
ROUTES_DIR = DATA_DIR / "routes"
LANDMARKS_PATH = DATA_DIR / "landmarks.json"
CANDIDATES_PATH = DATA_DIR / "pedestrian_access_point_candidates.json"

# Maximum perpendicular distance (metres) from a route geometry segment
# for a landmark to be considered "nearby".  Configurable via the
# ``proximity_threshold_m`` parameter on the public API.
DEFAULT_NEARBY_THRESHOLD_M: float = 60.0

# Relative score weight for landmarks passed near the route geometry
# versus deliberate via-waypoints (which receive full 1.0 weight).
NEARBY_WEIGHT_FACTOR: float = 0.2

PROTOTYPE_LIMITATION_DISCLOSURE = (
    "Prototype note: Route evaluations are based on pedestrian routes generated with "
    "unverified prototype coordinates projected onto OSM sidewalk centerlines. "
    "Walk times and distances are routing engine estimates."
)


@dataclass
class ScoredRoute:
    """A route evaluated and scored against traveler preferences."""
    route_id: str
    route_name: str
    distance_m: float
    duration_s: float
    duration_minutes: float
    landmarks_visited: list[dict[str, Any]]
    landmark_count: int
    is_feasible: bool
    final_score: float
    score_breakdown: dict[str, float]
    constraint_status: dict[str, bool]
    raw_feature: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "route_name": self.route_name,
            "distance_m": round(self.distance_m, 1),
            "estimated_duration_s": round(self.duration_s, 1),
            "estimated_duration_minutes": round(self.duration_minutes, 1),
            "landmarks_visited": self.landmarks_visited,
            "landmark_count": self.landmark_count,
            "is_feasible": self.is_feasible,
            "score": round(self.final_score, 4),
            "score_breakdown": {
                k: round(v, 4) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                for k, v in self.score_breakdown.items()
            },
            "constraint_status": self.constraint_status,
        }


def load_routes(
    routes_dir: Path = ROUTES_DIR,
    origin_id: str | None = None,
    destination_id: str | None = None,
    strict_validation: bool = False,
) -> list[dict[str, Any]]:
    """Load GeoJSON route files from the routes directory, filtered by origin and destination."""
    if not routes_dir.exists():
        raise FileNotFoundError(f"Routes directory not found: {routes_dir}")

    # Deduplicate by (origin, destination, route_id), preferring the
    # journey-scoped filename over a legacy unscoped file for the same key.
    routes_by_key: dict[tuple[str, str, str], tuple[int, dict[str, Any]]] = {}
    candidates: dict[str, dict[str, Any]] | None = None

    for geojson_path in sorted(routes_dir.glob("*.geojson")):
        try:
            data = json.loads(geojson_path.read_text(encoding="utf-8"))
            if data.get("type") != "FeatureCollection" or not data.get("features"):
                continue

            feat = data["features"][0]
            props = feat.get("properties", data.get("metadata", {}))

            r_origin = props.get("origin_landmark_id")
            r_dest = props.get("destination_landmark_id")
            r_id = props.get("route_id", data.get("name", geojson_path.stem))

            # Filter by origin/destination if specified
            if origin_id is not None and r_origin != origin_id:
                continue
            if destination_id is not None and r_dest != destination_id:
                continue

            if strict_validation:
                if not r_origin or not r_dest or not r_id:
                    continue
                if candidates is None:
                    candidates = load_landmark_candidates()
                validate_cached_route(
                    data, str(r_origin), str(r_dest), str(r_id),
                    [candidates[str(r_origin)], candidates[str(r_dest)]],
                )

            dedup_key = (str(r_origin), str(r_dest), str(r_id))
            is_scoped = geojson_path.name.startswith(f"{r_origin}__{r_dest}__")
            priority = 0 if is_scoped else 1
            existing = routes_by_key.get(dedup_key)
            if existing is None or priority < existing[0]:
                routes_by_key[dedup_key] = (priority, data)
        except (json.JSONDecodeError, OSError, RouteFetchError, TypeError, ValueError):
            continue

    return [data for _priority, data in routes_by_key.values()]


def load_landmark_metadata(landmarks_path: Path = LANDMARKS_PATH) -> dict[str, dict[str, Any]]:
    """Load landmark categories and relevance descriptions from landmarks.json."""
    if not landmarks_path.exists():
        return {}
    try:
        data = json.loads(landmarks_path.read_text(encoding="utf-8"))
        return {item["id"]: item for item in data.get("landmarks", []) if "id" in item}
    except (json.JSONDecodeError, OSError):
        return {}


# ---------------------------------------------------------------------------
# Candidate-coordinate loading and geometry-based landmark proximity
# ---------------------------------------------------------------------------

def load_candidate_coordinates(
    candidates_path: Path = CANDIDATES_PATH,
) -> dict[str, dict[str, Any]]:
    """Load the nine landmark candidate coordinates from pedestrian_access_point_candidates.json.

    Returns a dict keyed by ``landmark_id`` with ``lat``, ``lon``,
    ``landmark_name``, and ``verification_status`` for each landmark.
    Coordinates are unverified prototype projections — the caller must
    preserve this limitation in any output.
    """
    if not candidates_path.exists():
        return {}
    try:
        data = json.loads(candidates_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for lm in data.get("landmarks", []):
        lid = lm.get("landmark_id")
        rc = lm.get("routing_candidate")
        if not lid or not rc:
            continue
        lat = rc.get("candidate_latitude")
        lon = rc.get("candidate_longitude")
        if lat is None or lon is None or not math.isfinite(lat) or not math.isfinite(lon):
            continue
        result[lid] = {
            "landmark_id": lid,
            "landmark_name": lm.get("landmark_name", lid),
            "lat": float(lat),
            "lon": float(lon),
            "verification_status": lm.get("verification_status", "candidate_unconfirmed"),
            "is_prototype_unverified": True,
        }
    return result


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres between two WGS-84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2.0) ** 2
    return 6_371_000.0 * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _point_to_segment_distance_m(
    p_lat: float, p_lon: float,
    a_lat: float, a_lon: float,
    b_lat: float, b_lon: float,
) -> float:
    """Approximate minimum distance (metres) from point P to segment A–B.

    Uses a local flat-Earth projection (adequate for sub-kilometre Manhattan
    segments) to compute the perpendicular / endpoint distance.
    """
    # Local metre offsets relative to A
    cos_lat = math.cos(math.radians(a_lat))
    ax, ay = 0.0, 0.0
    bx = (b_lon - a_lon) * cos_lat * 111_320.0
    by = (b_lat - a_lat) * 111_320.0
    px = (p_lon - a_lon) * cos_lat * 111_320.0
    py = (p_lat - a_lat) * 111_320.0

    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-12:
        return math.hypot(px - ax, py - ay)

    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def find_nearby_landmarks(
    route_coordinates: list[list[float]],
    candidate_coords: dict[str, dict[str, Any]],
    exclude_ids: set[str] | None = None,
    proximity_threshold_m: float = DEFAULT_NEARBY_THRESHOLD_M,
) -> list[dict[str, Any]]:
    """Identify landmarks whose candidate coordinate lies within
    *proximity_threshold_m* of the route LineString geometry.

    Parameters
    ----------
    route_coordinates:
        GeoJSON ``[lon, lat]`` coordinate list from the route Feature.
    candidate_coords:
        Dict returned by :func:`load_candidate_coordinates`.
    exclude_ids:
        Landmark IDs to skip (e.g. origin, destination, via-waypoints)
        to avoid duplicates.
    proximity_threshold_m:
        Maximum distance in metres from the route geometry for a
        landmark to be considered "nearby".  Default is
        ``DEFAULT_NEARBY_THRESHOLD_M`` (60 m).

    Returns
    -------
    list[dict]
        Each entry contains ``landmark_id``, ``landmark_name``,
        ``min_distance_m``, and ``is_prototype_unverified``.

    Notes
    -----
    Coordinates are unverified prototype projections from
    ``pedestrian_access_point_candidates.json``.  Proximity results
    inherit this unverified status.
    """
    if not route_coordinates or len(route_coordinates) < 2:
        return []

    exclude = exclude_ids or set()
    nearby: list[dict[str, Any]] = []

    for lid, cand in candidate_coords.items():
        if lid in exclude:
            continue

        c_lat, c_lon = cand["lat"], cand["lon"]
        min_dist = float("inf")

        for i in range(len(route_coordinates) - 1):
            seg_a = route_coordinates[i]      # [lon, lat]
            seg_b = route_coordinates[i + 1]
            d = _point_to_segment_distance_m(
                c_lat, c_lon,
                seg_a[1], seg_a[0],
                seg_b[1], seg_b[0],
            )
            if d < min_dist:
                min_dist = d
                if min_dist == 0.0:
                    break

        if min_dist <= proximity_threshold_m:
            nearby.append({
                "landmark_id": lid,
                "landmark_name": cand["landmark_name"],
                "min_distance_m": round(min_dist, 1),
                "is_prototype_unverified": True,
            })

    # Sort by distance for deterministic output
    nearby.sort(key=lambda x: (x["min_distance_m"], x["landmark_id"]))
    return nearby


def compute_route_landmark_relevance(
    via_waypoints: list[dict[str, Any]],
    landmarks_meta: dict[str, dict[str, Any]],
    route_coordinates: list[list[float]] | None = None,
    candidate_coords: dict[str, dict[str, Any]] | None = None,
    proximity_threshold_m: float = DEFAULT_NEARBY_THRESHOLD_M,
    exclude_ids: set[str] | None = None,
    nearby_landmarks: list[dict[str, Any]] | None = None,
) -> tuple[float, list[dict[str, Any]]]:
    """Calculate architectural/historical interest score and list visited landmarks.

    Parameters
    ----------
    via_waypoints:
        Explicit intermediate waypoints from the route definition.
    landmarks_meta:
        Metadata dict (categories, descriptions) keyed by landmark ID.
    route_coordinates:
        Optional GeoJSON ``[lon, lat]`` coordinates of the route LineString.
        When provided and ``nearby_landmarks`` is None, landmarks near the
        route geometry are automatically detected using
        :func:`find_nearby_landmarks`.
    candidate_coords:
        Candidate coordinates dictionary (from :func:`load_candidate_coordinates`).
        Loaded automatically if None and ``route_coordinates`` is provided.
    proximity_threshold_m:
        Proximity distance threshold in metres. Default is ``DEFAULT_NEARBY_THRESHOLD_M`` (60 m).
    exclude_ids:
        Landmark IDs to skip during proximity search (e.g. origin and destination).
    nearby_landmarks:
        Optional precomputed nearby landmarks list from :func:`find_nearby_landmarks`.
        These are merged with *via_waypoints* while avoiding duplicates.

    Returns
    -------
    (arch_score, visited)
        ``arch_score`` is a float reflecting architectural / historical
        interest.  ``visited`` is the combined list of via-waypoint and
        nearby-landmark dicts with metadata.
    """
    visited: list[dict[str, Any]] = []
    arch_score = 0.0
    seen_ids: set[str] = set()
    exclude = set(exclude_ids or ())

    # --- via-waypoints (primary, deliberate route stops) ---
    for wp in via_waypoints:
        lid = wp.get("landmark_id")
        if not lid or lid in seen_ids or lid in exclude:
            continue
        seen_ids.add(lid)

        lname = wp.get("landmark_name", lid)
        meta = landmarks_meta.get(lid, {})
        categories = meta.get("categories", [])
        desc = meta.get("description", "")
        arch_rel = meta.get("architectural_relevance", "")

        is_arch = "architecture" in categories
        is_historic = "historic_landmark" in categories

        if is_arch:
            arch_score += 1.0
        if is_historic:
            arch_score += 0.5
        if not is_arch and not is_historic and categories:
            arch_score += 0.2

        visited.append({
            "landmark_id": lid,
            "landmark_name": lname,
            "categories": categories,
            "description": desc,
            "architectural_relevance": arch_rel,
            "source": "via_waypoint",
        })

    # --- geometry-detected nearby landmarks (supplementary) ---
    if nearby_landmarks is None and route_coordinates and len(route_coordinates) >= 2:
        if candidate_coords is None:
            candidate_coords = load_candidate_coordinates()
        combined_exclude = seen_ids | (exclude_ids or set())
        nearby_landmarks = find_nearby_landmarks(
            route_coordinates,
            candidate_coords,
            exclude_ids=combined_exclude,
            proximity_threshold_m=proximity_threshold_m,
        )

    for nl in (nearby_landmarks or []):
        lid = nl.get("landmark_id")
        if not lid or lid in seen_ids or lid in exclude:
            continue
        seen_ids.add(lid)

        lname = nl.get("landmark_name", lid)
        meta = landmarks_meta.get(lid, {})
        categories = meta.get("categories", [])
        desc = meta.get("description", "")
        arch_rel = meta.get("architectural_relevance", "")

        is_arch = "architecture" in categories
        is_historic = "historic_landmark" in categories

        # Secondary score weight for landmarks passed along the route
        if is_arch:
            arch_score += 1.0 * NEARBY_WEIGHT_FACTOR
        if is_historic:
            arch_score += 0.5 * NEARBY_WEIGHT_FACTOR
        if not is_arch and not is_historic and categories:
            arch_score += 0.2 * NEARBY_WEIGHT_FACTOR

        visited.append({
            "landmark_id": lid,
            "landmark_name": lname,
            "categories": categories,
            "description": desc,
            "architectural_relevance": arch_rel,
            "source": "nearby_route_geometry",
            "min_distance_m": nl.get("min_distance_m"),
            "coordinate_note": "Proximity based on unverified prototype coordinates.",
        })

    return arch_score, visited


def score_routes(
    routes: list[dict[str, Any]],
    preferences: UserPreferences,
    landmarks_meta: dict[str, dict[str, Any]] | None = None,
    candidate_coords: dict[str, dict[str, Any]] | None = None,
    proximity_threshold_m: float = DEFAULT_NEARBY_THRESHOLD_M,
) -> list[ScoredRoute]:
    """Score all routes deterministically against user preferences.

    Parameters
    ----------
    candidate_coords:
        Landmark candidate coordinates for geometry-based proximity
        detection.  Loaded automatically from
        ``pedestrian_access_point_candidates.json`` when ``None``.
    proximity_threshold_m:
        Maximum distance (metres) from route geometry for a landmark
        to count as "nearby".  Default ``DEFAULT_NEARBY_THRESHOLD_M``.
    """
    if landmarks_meta is None:
        landmarks_meta = load_landmark_metadata()
    if candidate_coords is None:
        candidate_coords = load_candidate_coordinates()

    # 1. Extract raw metrics for all routes
    route_data: list[dict[str, Any]] = []
    for fc in routes:
        meta = fc.get("metadata", {})
        feat = fc["features"][0]
        props = feat.get("properties", meta)

        r_id = props.get("route_id", fc.get("name", "unknown"))
        r_name = props.get("route_name", r_id)
        dist_m = float(props.get("distance_m", 0.0))
        dur_s = float(props.get("estimated_duration_s", 0.0))
        via_wps = props.get("via_waypoints", [])
        via_ids = [
            waypoint.get("landmark_id")
            for waypoint in via_wps
            if isinstance(waypoint, dict) and waypoint.get("landmark_id")
        ]

        # Identify landmarks near the actual route geometry using candidate coordinates
        route_coords = feat.get("geometry", {}).get("coordinates", [])
        origin_id = props.get("origin_landmark_id")
        dest_id = props.get("destination_landmark_id")
        exclude_endpoints = {origin_id, dest_id} - {None}

        arch_val, visited = compute_route_landmark_relevance(
            via_wps,
            landmarks_meta,
            route_coordinates=route_coords,
            candidate_coords=candidate_coords,
            proximity_threshold_m=proximity_threshold_m,
            exclude_ids=exclude_endpoints,
        )
        history_val = sum(
            1.0
            for landmark in visited
            if "historic_landmark" in landmark.get("categories", [])
        )
        excluded_categories = set(preferences.excluded_categories)
        has_excluded_place = any(
            (
                "landmarks" in excluded_categories
                or bool(excluded_categories.intersection(landmark.get("categories", [])))
                or ("history" in excluded_categories and "historic_landmark" in landmark.get("categories", []))
                or ("nature" in excluded_categories and "nature" in landmark.get("categories", []))
            )
            for landmark in visited
        )
        category_places = [
            landmark for landmark in visited
            if landmark.get("source") == "via_waypoint"
        ]
        preferred_categories = set(preferences.preferred_categories)
        category_relevance = (
            sum(
                bool(preferred_categories.intersection(landmark.get("categories", [])))
                for landmark in category_places
            ) / max(1, len(category_places))
            if preferred_categories and category_places
            else 0.0
        )
        requested_ids = set(preferences.requested_landmark_ids)
        requested_satisfied = requested_ids.issubset(set(via_ids))
        preferred_landmark_matches = len(
            set(preferences.preferred_landmark_ids).intersection(via_ids)
        )
        # Preference-relevant sightseeing value, used to evaluate whether
        # additional walking distance is justified by the places visited.
        interest_value_raw = (
            max(0.0, arch_val) * preferences.architecture
            + max(0.0, history_val) * preferences.history
            + category_relevance * len(preferred_categories)
            + preferred_landmark_matches * 1.5
            + math.log1p(len(visited)) * preferences.more_landmarks
        )
        route_data.append({
            "route_id": r_id,
            "route_name": r_name,
            "distance_m": dist_m,
            "duration_s": dur_s,
            "duration_min": dur_s / 60.0,
            "via_waypoints": via_wps,
            "via_ids": via_ids,
            "landmarks_visited": visited,
            "landmark_count": len(visited),
            "arch_raw": arch_val,
            "history_raw": history_val,
            "has_excluded_place": has_excluded_place,
            "requested_satisfied": requested_satisfied,
            "category_relevance": category_relevance,
            "preferred_landmark_matches": preferred_landmark_matches,
            "interest_value_raw": interest_value_raw,
            "raw_feature": feat,
        })

    # 2. Min/Max normalization bounds across available routes
    d_min = min(r["distance_m"] for r in route_data)
    d_max = max(r["distance_m"] for r in route_data)
    t_min = min(r["duration_s"] for r in route_data)
    t_max = max(r["duration_s"] for r in route_data)
    l_max = max(r["landmark_count"] for r in route_data)
    a_max = max(r["arch_raw"] for r in route_data)
    h_max = max(r["history_raw"] for r in route_data)

    detour_reference_m = d_min
    # Evaluate sightseeing value against the additional walking distance.
    # 250 m represents one reference unit of extra walking.
    for route in route_data:
        extra_distance = max(0.0, route["distance_m"] - d_min)
        interest_value = route["interest_value_raw"]

        route["extra_distance_vs_shortest_m"] = extra_distance

        route["detour_value"] = (
            interest_value / (interest_value + extra_distance / 250.0)
            if interest_value > 0
            else 0.0
        )
    for route in route_data:
        route["detour_ratio"] = route["distance_m"] / detour_reference_m
        route["within_detour"] = (
            preferences.max_detour_ratio is None
            or route["detour_ratio"] <= preferences.max_detour_ratio
        )
        route["hard_priority"] = {}
        route["distance_feasible"] = (
            preferences.max_distance_m is None
            or route["distance_m"] <= preferences.max_distance_m
        )

    # 3. Time feasibility check
    t_limit = preferences.max_duration_minutes
    for r in route_data:
        r["is_feasible"] = (t_limit is None) or (r["duration_min"] <= t_limit)

    any_time_feasible = any(r["is_feasible"] for r in route_data)

    # 4. Dimension weights
    w_dist = preferences.shortest_distance
    w_time = preferences.minimum_walking_time
    w_arch = preferences.architecture
    w_history = preferences.history
    w_landmarks = preferences.more_landmarks
    category_preferences = [
        category for category in preferences.preferred_categories
        if category not in {"architecture", "historic_landmark"}
    ]
    w_categories = 0.8 if category_preferences else 0.0
    w_preferred_landmarks = 1.0 if preferences.preferred_landmark_ids else 0.0

    if preferences.route_priority == "sightseeing" and w_landmarks == 0:
        w_landmarks = 0.7
    if preferences.route_priority == "efficiency":
        w_dist = max(w_dist, 0.7)
        w_time = max(w_time, 0.7)

    total_weight = (
        w_dist + w_time + w_arch + w_history + w_landmarks
        + w_categories + w_preferred_landmarks
    )
    if total_weight <= 1e-6:
        if preferences.walking_time_budget_minutes is not None:
            # User provided a time budget for walking/exploring without explicit theme:
            # encourage sightseeing within budget rather than recommending the direct route solely because it is shorter
            w_landmarks = 0.8
            w_arch = 0.4
            w_history = 0.4
            total_weight = w_landmarks + w_arch + w_history
        else:
            # Default balanced preference when none specified
            w_dist = 0.5
            w_time = 0.5
            total_weight = 1.0

    scored_routes: list[ScoredRoute] = []

    # Hard constraints are applied in order before comparing soft preference scores.
    # If no candidate satisfies a constraint, retain candidates and report its failure.
    active_routes = route_data
    constraint_priorities: list[str] = []
    hard_constraints: list[tuple[str, Any]] = []
    if t_limit is not None and any(r["is_feasible"] for r in route_data):
        hard_constraints.append(("time_limit", lambda route: route["is_feasible"]))
    if preferences.max_distance_m is not None:
        hard_constraints.append(("distance_limit", lambda route: route["distance_feasible"]))
    if preferences.requested_landmark_ids:
        hard_constraints.append((
            "requested_landmarks",
            lambda route: route["requested_satisfied"],
        ))
    if preferences.min_landmarks > 0 and any(
        r["landmark_count"] >= preferences.min_landmarks for r in route_data
    ):
        hard_constraints.append((
            "min_landmarks",
            lambda route: route["landmark_count"] >= preferences.min_landmarks,
        ))
    if preferences.excluded_categories:
        hard_constraints.append(("exclusions", lambda route: not route["has_excluded_place"]))
    if preferences.max_detour_ratio is not None:
        hard_constraints.append(("detour_limit", lambda route: route["within_detour"]))

    for name, predicate in hard_constraints:
        satisfying_routes = [route for route in active_routes if predicate(route)]
        if satisfying_routes:
            constraint_priorities.append(name)
            for route in route_data:
                route["hard_priority"][name] = route in satisfying_routes
            active_routes = satisfying_routes

    for r in route_data:
        # Normalized dimension scores in [0.0, 1.0]
        s_dist = 1.0 - (r["distance_m"] - d_min) / (d_max - d_min) if d_max > d_min else 1.0
        s_time = 1.0 - (r["duration_s"] - t_min) / (t_max - t_min) if t_max > t_min else 1.0
        # Diminishing returns: the first few interesting places add more value
        # than repeatedly adding further stops. This prevents raw landmark
        # quantity from dominating the recommendation.
        s_landmarks = (
            math.log1p(r["landmark_count"]) / math.log1p(l_max)
            if l_max > 0
            else 0.0
        )
        s_arch = r["arch_raw"] / a_max if a_max > 0 else 0.0
        s_history = r["history_raw"] / h_max if h_max > 0 else 0.0
        s_categories = (
            len({
                category
                for landmark in r["landmarks_visited"]
                if landmark.get("source") == "via_waypoint"
                for category in category_preferences
                if category in landmark.get("categories", [])
            }) / max(1, len(category_preferences))
            if category_preferences
            else 0.0
        )
        s_preferred_landmarks = (
            min(
                1.0,
                r["preferred_landmark_matches"]
                / max(1, len(set(preferences.preferred_landmark_ids))),
            )
            if preferences.preferred_landmark_ids
            else 0.0
        )

        # Enforce minimum landmark count preference if specified (e.g. "at least one landmark")
        if preferences.min_landmarks > 0:
            if r["landmark_count"] < preferences.min_landmarks and l_max >= preferences.min_landmarks:
                s_landmarks = 0.0
                s_arch = 0.0

        # Base weighted score
        base_score = (
            w_dist * s_dist
            + w_time * s_time
            + w_arch * s_arch
            + w_history * s_history
            + w_landmarks * s_landmarks
            + w_categories * s_categories
            + w_preferred_landmarks * s_preferred_landmarks
        ) / total_weight

        # Time constraint penalty
        final_score = base_score
        penalty_factor = 1.0

        if t_limit is not None and not r["is_feasible"]:
            overtime_ratio = (r["duration_min"] - t_limit) / t_limit
            if any_time_feasible:
                # Heavy penalty so feasible routes always outrank infeasible routes
                penalty_factor = 0.15 * max(0.1, 1.0 - overtime_ratio)
            else:
                # When no route is feasible, rank by proximity to the limit
                penalty_factor = max(0.2, t_limit / r["duration_min"])

            final_score = base_score * penalty_factor

        if preferences.max_distance_m is not None and not r["distance_feasible"]:
            over_distance_ratio = (
                r["distance_m"] - preferences.max_distance_m
            ) / preferences.max_distance_m
            distance_penalty = (
                0.15 * max(0.1, 1.0 - over_distance_ratio)
                if any(route["distance_feasible"] for route in route_data)
                else max(0.2, preferences.max_distance_m / r["distance_m"])
            )
            final_score *= distance_penalty
        else:
            distance_penalty = 1.0

        budget_penalty_factor = 1.0
        if (
            preferences.walking_time_budget_minutes is not None
            and r["duration_min"] > preferences.walking_time_budget_minutes
            and (t_limit is None or r["is_feasible"])
        ):
            over_budget_ratio = (
                r["duration_min"] - preferences.walking_time_budget_minutes
            ) / preferences.walking_time_budget_minutes
            budget_penalty_factor = max(0.2, 1.0 / (1.0 + over_budget_ratio))
            final_score *= budget_penalty_factor

        # When a walking-time budget is provided:
        # Penalize excessive unused time on zero-landmark routes (do not automatically prefer bare direct routes)
        if (
            preferences.walking_time_budget_minutes is not None
            and preferences.walking_time_budget_minutes >= 10.0
            and r["landmark_count"] == 0
        ):
            unused_ratio = max(0.0, (preferences.walking_time_budget_minutes - r["duration_min"]) / preferences.walking_time_budget_minutes)
            if unused_ratio > 0.4:
                final_score *= max(0.2, 1.0 - 0.5 * unused_ratio)

        breakdown = {
            "base_score": base_score,
            "distance_efficiency": s_dist,
            "time_efficiency": s_time,
            "landmarks_quantity": s_landmarks,
            "landmarks_quantity_raw": r["landmark_count"],
            "landmarks_quantity_method": "logarithmic_diminishing_returns",
            "architecture_relevance": s_arch,
            "history_relevance": s_history,
            "preferred_category_relevance": s_categories,
            "preferred_landmark_relevance": s_preferred_landmarks,
            "detour_ratio_vs_shortest": r["detour_ratio"],
            "extra_distance_vs_shortest_m": r["extra_distance_vs_shortest_m"],
            "extra_time_vs_fastest_s": r["duration_s"] - t_min,
            "time_penalty_factor": penalty_factor,
            "distance_penalty_factor": distance_penalty,
            "walking_time_budget_minutes": preferences.walking_time_budget_minutes,
            "budget_penalty_factor": budget_penalty_factor,
            "budget_feasible": (
                r["duration_min"] <= preferences.walking_time_budget_minutes
                if preferences.walking_time_budget_minutes is not None
                else None
            ),
            "detour_value": r["detour_value"],
            "detour_interest_value": r["interest_value_raw"],
            "detour_extra_distance_m": r["extra_distance_vs_shortest_m"],
            "detour_value_method": "preference_value_per_extra_distance",
        }

        constraint_status = {
            "time_limit": r["is_feasible"],
            "budget_limit": (
                r["duration_min"] <= preferences.walking_time_budget_minutes
                if preferences.walking_time_budget_minutes is not None
                else True
            ),
            "distance_limit": r["distance_feasible"],
            "min_landmarks": (
                preferences.min_landmarks == 0
                or r["landmark_count"] >= preferences.min_landmarks
            ),
            "exclusions": not r["has_excluded_place"],
            "requested_landmarks": r["requested_satisfied"],
            "detour_limit": r["within_detour"],
        }

        scored_routes.append(
            ScoredRoute(
                route_id=r["route_id"],
                route_name=r["route_name"],
                distance_m=r["distance_m"],
                duration_s=r["duration_s"],
                duration_minutes=r["duration_min"],
                landmarks_visited=r["landmarks_visited"],
                landmark_count=r["landmark_count"],
                is_feasible=all(constraint_status.values()),
                final_score=final_score,
                score_breakdown=breakdown,
                constraint_status=constraint_status,
                raw_feature=r["raw_feature"],
            )
        )

    # Rank feasible routes ahead of infeasible routes first, then by score.
    # This preserves the documented guarantee even when a feasible route has a
    # zero base score for the requested dimension and an infeasible route has a
    # high base score.
    priorities_by_id = {
        route["route_id"]: tuple(
            route["hard_priority"].get(name, False)
            for name in constraint_priorities
        )
        for route in route_data
    }
    scored_routes.sort(
        key=lambda route: (
            *priorities_by_id[route.route_id],
            route.final_score,
        ),
        reverse=True,
    )
    return scored_routes




def generate_explanation(
    recommended: ScoredRoute,
    ranked: list[ScoredRoute],
    preferences: UserPreferences,
    origin_name: str = "",
    destination_name: str = "",
) -> str:
    """Explain the recommendation using the actual preferences and route facts."""
    routes = ranked or [recommended]
    shortest = min(routes, key=lambda route: route.distance_m)
    fastest = min(routes, key=lambda route: route.duration_s)
    runner_up = next(
        (route for route in routes if route.route_id != recommended.route_id),
        None,
    )

    landmarks = recommended.landmarks_visited
    landmark_names = [place["landmark_name"] for place in landmarks]

    architectural_names = [
        place["landmark_name"]
        for place in landmarks
        if {"architecture", "historic_landmark", "art_deco"}.intersection(
            place.get("categories", [])
        )
    ]

    historical_names = [
        place["landmark_name"]
        for place in landmarks
        if "historic_landmark" in place.get("categories", [])
    ]

    reasons: list[str] = []

    if (
        preferences.shortest_distance > 0.5
        and recommended.route_id == shortest.route_id
    ):
        reasons.append(
            "You wanted to avoid unnecessary detours, so we selected "
            "the shortest available route."
        )

    elif (
        preferences.minimum_walking_time > 0.5
        and recommended.route_id == fastest.route_id
    ):
        reasons.append(
            "You wanted to get there quickly, so we selected the route "
            "with the lowest estimated walking time."
        )

    if preferences.architecture > 0.5 and architectural_names:
        reasons.append(
            f"You wanted to see architecture, and this route passes "
            f"{', '.join(architectural_names)}."
        )

    elif preferences.history > 0.5 and historical_names:
        reasons.append(
            f"You wanted historical places, and this route passes "
            f"{', '.join(historical_names)}."
        )

    if (
        preferences.more_landmarks > 0.5
        and landmark_names
        and (
            runner_up is None
            or recommended.landmark_count > runner_up.landmark_count
        )
    ):
        reasons.append(
            f"You wanted interesting places, and this route includes "
            f"{', '.join(landmark_names)}."
        )

    if (
        runner_up is not None
        and recommended.route_id != shortest.route_id
        and landmark_names
        and (
            preferences.architecture > 0.5
            or preferences.history > 0.5
            or preferences.more_landmarks > 0.5
        )
    ):
        extra_distance = recommended.distance_m - shortest.distance_m
        extra_minutes = (
            recommended.duration_s - shortest.duration_s
        ) / 60.0

        if extra_distance > 0 and extra_minutes > 0:
            reasons.append(
                f"It adds {extra_distance:.0f} m and approximately "
                f"{extra_minutes:.1f} minutes compared with the shortest route, "
                "in exchange for these sightseeing stops."
            )

    if not reasons:
        reasons.append(
            "This route is the best match for your supported preferences "
            "among the available walking routes."
        )

    explanation = (
        f"Recommended route: {recommended.route_name}. "
        + " ".join(reasons[:3])
    )

    # Soft walking-time budget: disclose whether the selected route fits.
    budget = preferences.walking_time_budget_minutes
    if budget is not None:
        if recommended.duration_minutes <= budget:
            explanation += (
                f" It fits your available {budget:g}-minute walking-time budget "
                f"(your {budget:g}-minute walking budget)."
            )
        else:
            explanation += (
                f" It exceeds your {budget:g}-minute walking-time budget; "
                f"the estimated duration is "
                f"{recommended.duration_minutes:.1f} minutes."
            )

    # Hard maximum: disclose feasibility and identify the fastest alternative.
    time_limit = preferences.max_duration_minutes
    if time_limit is not None:
        over_limit_routes = [
            route for route in routes
            if route.duration_minutes > time_limit
        ]

        if len(over_limit_routes) == len(routes):
            explanation += (
                " No feasible candidate was found. "
                "All available routes exceed your requested time limit "
                f"of {time_limit:g} minutes. "
                f"The fastest available route is {fastest.route_name}, "
                f"at {fastest.duration_minutes:.1f} minutes."
            )
        elif over_limit_routes:
            if recommended.duration_minutes > time_limit:
                explanation += (
                    f" The recommended route exceeds your "
                    f"{time_limit:g}-minute walking-time limit; "
                    f"it takes {recommended.duration_minutes:.1f} minutes."
                )
            else:
                explanation += (
                    f" Some available alternatives exceed your "
                    f"{time_limit:g}-minute walking-time limit."
                )

    if len(routes) == 1:
        explanation += (
            " Only one validated route is currently available: "
            f"{recommended.distance_m:.0f} m, approximately "
            f"{recommended.duration_minutes:.1f} minutes."
        )

    return explanation



def recommend_route(
    query: str | None = None,
    origin_id: str = "grand_central_terminal",
    destination_id: str = "bryant_park",
    preferences: UserPreferences | None = None,
    routes_dir: Path = ROUTES_DIR,
    landmarks_path: Path = LANDMARKS_PATH,
    auto_fetch: bool = False,
    strict_route_validation: bool = False,
    route_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Top-level recommendation entry point for any journey between two landmarks.

    Evaluates ONLY routes belonging to the requested journey (origin_id -> destination_id).
    Identical endpoints are permitted only when a cached route contains an
    explicit intermediate waypoint.
    """
    if preferences is None:
        preferences = parse_preferences(query)

    landmarks_meta = load_landmark_metadata(landmarks_path)
    if landmarks_meta:
        if origin_id not in landmarks_meta:
            raise ValueError(f"Unknown origin landmark ID '{origin_id}'. Available: {sorted(landmarks_meta.keys())}")
        if destination_id not in landmarks_meta:
            raise ValueError(f"Unknown destination landmark ID '{destination_id}'. Available: {sorted(landmarks_meta.keys())}")

    origin_name = landmarks_meta.get(origin_id, {}).get("name", origin_id)
    destination_name = landmarks_meta.get(destination_id, {}).get("name", destination_id)

    routes = route_candidates if route_candidates is not None else load_routes(
        routes_dir, origin_id=origin_id, destination_id=destination_id,
        strict_validation=strict_route_validation,
    )

    if not routes and auto_fetch:
        try:
            from fetch_routes import fetch_all_routes
            fetch_all_routes(origin_id=origin_id, destination_id=destination_id)
            routes = load_routes(
                routes_dir, origin_id=origin_id, destination_id=destination_id,
                strict_validation=strict_route_validation,
            )
        except Exception:
            pass

    if not routes:
        raise ValueError(
            f"No routes found for journey from '{origin_id}' to '{destination_id}'. "
            f"Routes must be generated first for this journey."
        )

    if origin_id == destination_id and not any(
        route["features"][0].get("properties", {}).get("via_waypoints")
        for route in routes
    ):
        raise ValueError(
            f"A circular route from '{origin_id}' requires at least one validated intermediate waypoint."
        )

    ranked_scored = score_routes(routes, preferences, landmarks_meta)
    if not ranked_scored:
        raise ValueError("No routes were scored")

    winner = ranked_scored[0]
    explanation = generate_explanation(
        winner,
        ranked_scored,
        preferences,
        origin_name=origin_name,
        destination_name=destination_name,
    )

    return {
        "query": preferences.raw_query,
        "origin_landmark_id": origin_id,
        "origin_landmark_name": origin_name,
        "destination_landmark_id": destination_id,
        "destination_landmark_name": destination_name,
        "parsed_preferences": preferences.to_dict(),
        "recommended_route": winner.to_dict(),
        "ranked_alternatives": [r.to_dict() for r in ranked_scored[1:]],
        "individual_scores": {r.route_id: round(r.final_score, 4) for r in ranked_scored},
        "estimated_walking_time_s": winner.duration_s,
        "estimated_walking_time_minutes": winner.duration_minutes,
        "distance_m": winner.distance_m,
        "landmarks_visited": [lm["landmark_name"] for lm in winner.landmarks_visited],
        "explanation": explanation,
        "limitation_disclosure": PROTOTYPE_LIMITATION_DISCLOSURE,
    }


if __name__ == "__main__":
    import sys
    test_queries = [
        "I love architecture but don't have much time.",
        "I want the shortest possible walk.",
        "I want to see as many interesting buildings as possible.",
        "I prefer a quick walk with at least one interesting landmark.",
    ]
    query_to_run = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    queries = [query_to_run] if query_to_run else test_queries

    for q in queries:
        print("\n" + "=" * 70)
        print(f"QUERY: {q}")
        print("=" * 70)
        rec = recommend_route(q)
        print(f"RECOMMENDED: {rec['recommended_route']['route_name']} ({rec['recommended_route']['route_id']})")
        print(f"  Distance: {rec['distance_m']:.0f} m | Time: {rec['estimated_walking_time_minutes']:.1f} min")
        print(f"  Landmarks: {rec['landmarks_visited']}")
        print(f"  Scores: {rec['individual_scores']}")
        print(f"  Explanation: {rec['explanation']}")
