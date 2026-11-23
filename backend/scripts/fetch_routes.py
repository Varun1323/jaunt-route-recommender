#!/usr/bin/env python3
"""Fetch genuine pedestrian routes from Valhalla; never synthesize geometry.

Uses prototype candidate coordinates from pedestrian_access_point_candidates.json.
Generates genuine pedestrian routes between arbitrary origin and destination landmarks.
Preserves unverified status and discloses prototype coordinate limitations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "backend" / "data"
CANDIDATES_PATH = DATA_DIR / "pedestrian_access_point_candidates.json"
ROUTE_DIR = DATA_DIR / "routes"
RAW_DIR = DATA_DIR / "raw" / "valhalla"

DEFAULT_ENDPOINT = "https://valhalla1.openstreetmap.de"
DEFAULT_USER_AGENT = "JauntWalkingRouteApp/1.0 (https://github.com/jaunt-app; dev@jauntroutes.org)"
DEFAULT_CLIENT_ID = "jaunt-walking-route-app"
USER_AGENT = os.environ.get("JAUNT_USER_AGENT", DEFAULT_USER_AGENT).strip()
CLIENT_ID = os.environ.get("JAUNT_CLIENT_ID", DEFAULT_CLIENT_ID).strip()
REQUEST_DELAY_SECONDS = 1.2
MAX_RETRIES = 3
RETRY_BASE_SECONDS = 2.0
RETRYABLE_HTTP_STATUSES = {429, 503}

# Alternative-generation policy.  A candidate intermediate landmark is only
# considered when the straight-line detour it implies is bounded, it is clear
# of both endpoints, and it lies off the direct corridor.  These are routing
# heuristics, not access verification.
MAX_ALTERNATIVES_PER_JOURNEY = 2
LEGACY_LANDMARK_IDS = frozenset({
    "grand_central_terminal",
    "pershing_square_building",
    "chrysler_building",
    "graybar_building",
    "chanin_building",
    "daily_news_building",
    "one_vanderbilt",
    "stephen_a_schwarzman_building",
    "bryant_park",
})
SELECTED_LANDMARK_IDS = frozenset({
    "grand_central_terminal",
    "chrysler_building",
    "stephen_a_schwarzman_building",
    "bryant_park",
    "times_square",
    "rockefeller_center",
    "st_patricks_cathedral",
    "museum_of_modern_art",
})
MAX_DETOUR_RATIO = 2.0
MAX_EXTRA_DISTANCE_M = 500.0
MIN_ENDPOINT_CLEARANCE_M = 50.0
MIN_LATERAL_OFFSET_M = 15.0
MIN_WAYPOINT_SEPARATION_M = 50.0

# Post-fetch guard against Valhalla returning a path that detours far beyond
# the candidate's straight-line estimate.
MAX_ACTUAL_DETOUR_RATIO = 2.0
MAX_ACTUAL_EXTRA_DISTANCE_M = 750.0

# Curated alternatives are retained for the original research journey.  The
# same generic relevance/validation pipeline still applies to every journey;
# these entries simply seed the two alternative slots for this pair.
CURATED_ALTERNATIVES: dict[tuple[str, str], list[dict[str, Any]]] = {
    ("grand_central_terminal", "bryant_park"): [
        {
            "route_id": "via_vanderbilt",
            "name": "Via One Vanderbilt Transit Hall",
            "description": "Pedestrian route visiting One Vanderbilt and the pedestrian transit hall corridor.",
            "waypoint_ids": ["one_vanderbilt"],
        },
        {
            "route_id": "architecture",
            "name": "Art Deco Architectural Loop",
            "description": "Walking route past Pershing Square Building and Chrysler Building Art Deco facades before heading to Bryant Park.",
            "waypoint_ids": ["pershing_square_building", "chrysler_building"],
        },
    ],
}

CURATED_DIRECT_DEFINITIONS: dict[tuple[str, str], dict[str, str]] = {
    ("grand_central_terminal", "bryant_park"): {
        "name": "Direct 42nd Street Pedestrian Route",
        "description": "Direct walking route along 42nd Street corridor between Grand Central Terminal and Bryant Park.",
    },
}

COORDINATE_LIMITATION_DISCLOSURE = (
    "Route endpoints use unverified prototype routing candidates. Some are projected onto mapped OSM "
    "pedestrian ways; others are Nominatim place points whose entrances and sidewalk connections are "
    "unconfirmed. Valhalla must return and validate actual pedestrian geometry; no endpoint is represented "
    "as a field-verified public entrance."
)


class RouteFetchError(RuntimeError):
    """Raised when route retrieval, parsing, or validation fails."""
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in metres between two WGS84 positions."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return 6_371_000.0 * c


def load_landmark_candidates(path: Path = CANDIDATES_PATH) -> dict[str, dict[str, Any]]:
    """Load prototype candidate routing coordinates from candidate JSON.

    Preserves unverified status and discloses candidate limitation.
    """
    if not path.exists():
        raise RouteFetchError(f"Candidate file not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RouteFetchError(f"Malformed candidate JSON in {path}: {exc}") from exc

    landmarks = data.get("landmarks", [])
    if not landmarks:
        raise RouteFetchError(f"No landmarks found in {path}")

    by_id: dict[str, dict[str, Any]] = {}
    for lm in landmarks:
        lid = lm.get("landmark_id")
        rc = lm.get("routing_candidate")
        if not lid or not rc:
            continue

        lat = rc.get("candidate_latitude")
        lon = rc.get("candidate_longitude")
        if lat is None or lon is None or not math.isfinite(lat) or not math.isfinite(lon):
            continue

        by_id[lid] = {
            "landmark_id": lid,
            "landmark_name": lm.get("landmark_name", lid),
            "lat": float(lat),
            "lon": float(lon),
            "osm_way_id": rc.get("osm_way_id"),
            "projection_distance_m": rc.get("projection_distance_m"),
            "coordinate_basis": rc.get(
                "coordinate_basis",
                "projected onto an outdoor OSM pedestrian way; unverified",
            ),
            "reference_osm_feature": rc.get("reference_osm_feature"),
            "is_connected_to_main_network": rc.get("is_connected_to_main_network"),
            "verification_status": lm.get("verification_status", "candidate_unconfirmed"),
            "confidence": rc.get("confidence", "medium"),
            "is_prototype_unverified": True,
        }

    if not by_id:
        raise RouteFetchError(f"No valid landmark routing candidates found in {path}")

    return by_id


def get_candidate_location(candidates: dict[str, dict[str, Any]], landmark_id: str, role: str = "location") -> dict[str, Any]:
    """Retrieve candidate location for a landmark ID, raising RouteFetchError if unknown."""
    if landmark_id not in candidates:
        available = ", ".join(sorted(candidates.keys()))
        raise RouteFetchError(f"Unknown {role} landmark ID '{landmark_id}'. Available: [{available}]")
    return candidates[landmark_id]


def validate_endpoints(
    origin_id: str,
    destination_id: str,
    candidates: dict[str, dict[str, Any]],
    via_ids: list[str] | None = None,
) -> None:
    """Validate origin and destination selections."""
    get_candidate_location(candidates, origin_id, "origin")
    get_candidate_location(candidates, destination_id, "destination")
    if origin_id == destination_id and not via_ids:
        raise RouteFetchError(
            "A circular route requires at least one intermediate waypoint."
        )


def waypoint_route_id(
    origin_id: str,
    destination_id: str,
    via_ids: list[str],
) -> str:
    """Return a cache-safe ID bound to direction and the ordered waypoint sequence."""
    digest_input = json.dumps(
        [origin_id, destination_id, via_ids],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"via_{hashlib.sha256(digest_input).hexdigest()[:16]}"


def _make_route_definition(
    route_id: str,
    name: str,
    description: str,
    origin: dict[str, Any],
    destination: dict[str, Any],
    waypoints: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one route definition from ordered intermediate waypoint records."""
    locations = [{"lat": origin["lat"], "lon": origin["lon"]}]
    locations.extend({"lat": wp["lat"], "lon": wp["lon"]} for wp in waypoints)
    locations.append({"lat": destination["lat"], "lon": destination["lon"]})
    return {
        "route_id": route_id,
        "name": name,
        "description": description,
        "locations": locations,
        "waypoints": waypoints,
    }


def _reference_line(
    origin: dict[str, Any],
    destination: dict[str, Any],
    reference_coordinates: list[list[float]] | None,
) -> list[list[float]]:
    """Return the direct-route geometry when available, else the straight corridor.

    The straight corridor is used only to rank candidate landmarks; it is never
    used as route geometry.
    """
    if reference_coordinates and len(reference_coordinates) >= 2:
        return reference_coordinates
    return [
        [origin["lon"], origin["lat"]],
        [destination["lon"], destination["lat"]],
    ]


def select_alternative_candidates(
    origin_id: str,
    destination_id: str,
    candidates: dict[str, dict[str, Any]],
    reference_coordinates: list[list[float]] | None = None,
    max_alternatives: int = MAX_ALTERNATIVES_PER_JOURNEY,
    exclude_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Select geographically relevant intermediate landmarks for alternatives.

    A candidate is eligible only when all of the following hold:

    * its straight-line via distance is within ``MAX_DETOUR_RATIO`` and
      ``MAX_EXTRA_DISTANCE_M`` of the direct distance;
    * it is at least ``MIN_ENDPOINT_CLEARANCE_M`` from both endpoints;
    * it is at least ``MIN_LATERAL_OFFSET_M`` from the direct corridor;
    * it is at least ``MIN_WAYPOINT_SEPARATION_M`` from already selected
      candidates.

    Candidates are ranked by lateral offset with a detour penalty.  The result
    is deterministic (score, then landmark ID) and is a heuristic only: the
    returned Valhalla geometry is validated separately.
    """
    if max_alternatives <= 0:
        return []

    origin = get_candidate_location(candidates, origin_id, "origin")
    destination = get_candidate_location(candidates, destination_id, "destination")
    base_distance = haversine_distance_m(
        origin["lat"], origin["lon"], destination["lat"], destination["lon"]
    )
    if base_distance <= 0:
        return []

    max_ratio = min(MAX_DETOUR_RATIO, 1.0 + MAX_EXTRA_DISTANCE_M / base_distance)
    line = _reference_line(origin, destination, reference_coordinates)
    excluded = set(exclude_ids or ()) | {origin_id, destination_id}

    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for landmark_id, candidate in candidates.items():
        if landmark_id not in SELECTED_LANDMARK_IDS:
            continue
        if landmark_id in excluded:
            continue

        origin_to_candidate = haversine_distance_m(
            origin["lat"], origin["lon"], candidate["lat"], candidate["lon"]
        )
        candidate_to_destination = haversine_distance_m(
            candidate["lat"], candidate["lon"], destination["lat"], destination["lon"]
        )
        via_distance = origin_to_candidate + candidate_to_destination
        detour_ratio = via_distance / base_distance
        if detour_ratio > max_ratio:
            continue

        endpoint_clearance = min(origin_to_candidate, candidate_to_destination)
        if endpoint_clearance < MIN_ENDPOINT_CLEARANCE_M:
            continue

        lateral_offset = _point_to_line_distance_m(
            [candidate["lon"], candidate["lat"]], line
        )
        if lateral_offset < MIN_LATERAL_OFFSET_M:
            continue

        score = lateral_offset - 250.0 * (detour_ratio - 1.0)
        ranked.append((score, landmark_id, candidate))

    ranked.sort(key=lambda item: (-item[0], item[1]))

    selected: list[dict[str, Any]] = []
    for _score, _landmark_id, candidate in ranked:
        if any(
            haversine_distance_m(
                candidate["lat"], candidate["lon"], chosen["lat"], chosen["lon"]
            )
            < MIN_WAYPOINT_SEPARATION_M
            for chosen in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= max_alternatives:
            break

    return selected


def _curated_alternative_definitions(
    origin_id: str,
    destination_id: str,
    candidates: dict[str, dict[str, Any]],
    origin: dict[str, Any],
    destination: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return curated alternatives for a journey, skipping missing waypoints."""
    definitions: dict[str, dict[str, Any]] = {}
    for spec in CURATED_ALTERNATIVES.get((origin_id, destination_id), []):
        waypoints: list[dict[str, Any]] = []
        for waypoint_id in spec["waypoint_ids"]:
            if waypoint_id in {origin_id, destination_id} or waypoint_id not in candidates:
                waypoints = []
                break
            waypoints.append(candidates[waypoint_id])
        if not waypoints:
            continue
        definitions[spec["route_id"]] = _make_route_definition(
            spec["route_id"],
            spec["name"],
            spec["description"],
            origin,
            destination,
            waypoints,
        )
    return definitions


def build_route_definitions(
    origin_id: str,
    destination_id: str,
    candidates: dict[str, dict[str, Any]],
    via_ids: list[str] | None = None,
    reference_coordinates: list[list[float]] | None = None,
    max_alternatives: int = MAX_ALTERNATIVES_PER_JOURNEY,
    include_alternatives: bool = True,
) -> dict[str, dict[str, Any]]:
    """Build one direct route and up to ``max_alternatives`` genuine alternatives.

    Alternatives are selected from geographically relevant intermediate
    landmarks (see :func:`select_alternative_candidates`).  Curated
    alternatives are seeded first for their specific journey; the generic
    strategy fills any remaining slots for every other journey.  Explicit
    ``via_ids`` still produce a single ``custom_via`` definition.

    Ensures intermediate waypoints are strictly distinct from endpoints.
    """
    if origin_id == destination_id and not via_ids:
        raise RouteFetchError("A circular route requires at least one intermediate waypoint.")
    validate_endpoints(origin_id, destination_id, candidates, via_ids)

    origin = get_candidate_location(candidates, origin_id, "origin")
    dest = get_candidate_location(candidates, destination_id, "destination")

    direct_spec = CURATED_DIRECT_DEFINITIONS.get((origin_id, destination_id), {})
    routes: dict[str, dict[str, Any]] = {
        "direct": _make_route_definition(
            "direct",
            direct_spec.get("name", f"Direct Route: {origin['landmark_name']} to {dest['landmark_name']}"),
            direct_spec.get(
                "description",
                f"Direct pedestrian route between {origin['landmark_name']} and {dest['landmark_name']}.",
            ),
            origin,
            dest,
            [],
        )
    }

    if not include_alternatives:
        return routes

    # If explicit intermediate waypoints were requested via CLI/caller.
    if via_ids is not None:
        if not via_ids:
            raise RouteFetchError("An explicit waypoint route requires at least one intermediate landmark.")
        if len(set(via_ids)) != len(via_ids):
            raise RouteFetchError("An explicit waypoint route cannot repeat an intermediate landmark.")
        if len(via_ids) > len(SELECTED_LANDMARK_IDS):
            raise RouteFetchError("An explicit waypoint route exceeds the selected landmark limit.")
        via_records: list[dict[str, Any]] = []
        for vid in via_ids:
            if vid == origin_id or vid == destination_id:
                raise RouteFetchError(f"Intermediate waypoint '{vid}' cannot be identical to origin or destination")
            via_records.append(get_candidate_location(candidates, vid, "waypoint"))

        route_id = waypoint_route_id(origin_id, destination_id, via_ids)
        route_kind = "Circular route" if origin_id == destination_id else "Route"
        routes[route_id] = _make_route_definition(
            route_id,
            f"Via {', '.join(v['landmark_name'] for v in via_records)}",
            f"{route_kind} from {origin['landmark_name']} to {dest['landmark_name']} via the requested intermediate landmarks in order.",
            origin,
            dest,
            via_records,
        )
        return routes

    curated = _curated_alternative_definitions(
        origin_id, destination_id, candidates, origin, dest
    )
    used_waypoint_ids: set[str] = set()
    for route_id, definition in curated.items():
        if len(routes) - 1 >= max_alternatives:
            break
        routes[route_id] = definition
        used_waypoint_ids.update(wp["landmark_id"] for wp in definition["waypoints"])

    remaining = max_alternatives - (len(routes) - 1)
    if remaining > 0:
        selected = select_alternative_candidates(
            origin_id,
            destination_id,
            candidates,
            reference_coordinates=reference_coordinates,
            max_alternatives=remaining,
            exclude_ids=used_waypoint_ids,
        )
        index = 1
        for candidate in selected:
            while f"alternative_{index}" in routes:
                index += 1
            route_id = f"alternative_{index}"
            routes[route_id] = _make_route_definition(
                route_id,
                f"Via {candidate['landmark_name']}",
                f"Alternative pedestrian route from {origin['landmark_name']} to {dest['landmark_name']} via {candidate['landmark_name']}.",
                origin,
                dest,
                [candidate],
            )
            index += 1

    return routes


def decode_polyline(encoded: str, precision: int = 6) -> list[list[float]]:
    """Decode Valhalla polyline6 to GeoJSON positions [longitude, latitude]."""
    factor = 10**precision
    index = lat = lon = 0
    points: list[list[float]] = []
    while index < len(encoded):
        deltas = []
        for _ in range(2):
            result = shift = 0
            while True:
                if index >= len(encoded):
                    raise RouteFetchError("truncated encoded polyline")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
                if shift > 60:
                    raise RouteFetchError("invalid encoded polyline integer")
            deltas.append(~(result >> 1) if result & 1 else (result >> 1))
        lat += deltas[0]
        lon += deltas[1]
        points.append([round(lon / factor, 6), round(lat / factor, 6)])
    if len(points) < 2:
        raise RouteFetchError("route geometry has fewer than two positions")
    return points


def _decode_trip_metrics(
    trip: dict[str, Any],
    requested_locations: list[dict[str, float]],
    max_endpoint_tolerance_m: float = 150.0,
) -> tuple[list[list[float]], float, float]:
    """Validate a single Valhalla trip object and extract its coordinates, distance, duration."""
    if not isinstance(trip, dict):
        raise RouteFetchError("Valhalla trip object missing or not a dict")

    status = trip.get("status")
    if status not in (None, 0):
        msg = trip.get("status_message", f"status code {status}")
        raise RouteFetchError(f"Valhalla routing engine returned error status: {msg}")

    legs = trip.get("legs") or []
    if not legs:
        raise RouteFetchError("Valhalla response contains no route legs")
    if len(requested_locations) > 2 and len(legs) != len(requested_locations) - 1:
        raise RouteFetchError(
            "Valhalla returned a different number of route legs than the requested waypoint sequence."
        )

    coordinates: list[list[float]] = []
    for leg_idx, leg in enumerate(legs):
        shape_enc = leg.get("shape")
        if not shape_enc:
            raise RouteFetchError(f"Valhalla route leg {leg_idx} missing encoded shape")
        leg_coords = decode_polyline(shape_enc)
        if len(requested_locations) > 2:
            for position, requested in (
                (leg_coords[0], requested_locations[leg_idx]),
                (leg_coords[-1], requested_locations[leg_idx + 1]),
            ):
                separation = haversine_distance_m(
                    requested["lat"],
                    requested["lon"],
                    position[1],
                    position[0],
                )
                if separation > max_endpoint_tolerance_m:
                    raise RouteFetchError(
                        f"Valhalla leg {leg_idx} is {separation:.1f} m from a requested "
                        f"waypoint (exceeds {max_endpoint_tolerance_m} m tolerance)."
                    )
        if coordinates and leg_coords and leg_coords[0] == coordinates[-1]:
            coordinates.extend(leg_coords[1:])
        else:
            coordinates.extend(leg_coords)

    if len(coordinates) < 2:
        raise RouteFetchError("Decoded route geometry has fewer than 2 coordinates")

    for index, position in enumerate(coordinates):
        if (
            len(position) != 2
            or not all(math.isfinite(value) for value in position)
            or not -180 <= position[0] <= 180
            or not -90 <= position[1] <= 90
        ):
            raise RouteFetchError(f"Invalid WGS84 GeoJSON coordinate at index {index}: {position!r}")

    summary = trip.get("summary") or {}
    length_km = summary.get("length")
    time_s = summary.get("time")

    if length_km is None or time_s is None:
        raise RouteFetchError("Valhalla route summary missing 'length' or 'time'")

    distance_m = float(length_km) * 1000.0
    duration_s = float(time_s)

    if not math.isfinite(distance_m) or not math.isfinite(duration_s) or distance_m <= 0 or duration_s <= 0:
        raise RouteFetchError(f"Invalid non-positive distance ({distance_m} m) or duration ({duration_s} s)")

    # Validate origin endpoint proximity
    req_orig = requested_locations[0]
    orig_dist = haversine_distance_m(req_orig["lat"], req_orig["lon"], coordinates[0][1], coordinates[0][0])
    if orig_dist > max_endpoint_tolerance_m:
        raise RouteFetchError(
            f"Route start coordinate [{coordinates[0][1]}, {coordinates[0][0]}] is {orig_dist:.1f} m "
            f"from requested origin [{req_orig['lat']}, {req_orig['lon']}] (exceeds {max_endpoint_tolerance_m} m tolerance)"
        )

    # Validate destination endpoint proximity
    req_dest = requested_locations[-1]
    dest_dist = haversine_distance_m(req_dest["lat"], req_dest["lon"], coordinates[-1][1], coordinates[-1][0])
    if dest_dist > max_endpoint_tolerance_m:
        raise RouteFetchError(
            f"Route end coordinate [{coordinates[-1][1]}, {coordinates[-1][0]}] is {dest_dist:.1f} m "
            f"from requested destination [{req_dest['lat']}, {req_dest['lon']}] (exceeds {max_endpoint_tolerance_m} m tolerance)"
        )

    return coordinates, distance_m, duration_s


def validate_route_response(
    result: dict[str, Any],
    requested_locations: list[dict[str, float]],
    max_endpoint_tolerance_m: float = 150.0,
) -> tuple[list[list[float]], float, float]:
    """Validate Valhalla route response, decoding coordinates and checking endpoints, distance, duration."""
    trip = result.get("trip")
    if not isinstance(trip, dict):
        raise RouteFetchError("Valhalla response missing top-level 'trip' object")
    return _decode_trip_metrics(trip, requested_locations, max_endpoint_tolerance_m)


def extract_native_alternates_from_response(
    result: dict[str, Any],
    requested_locations: list[dict[str, float]],
    max_endpoint_tolerance_m: float = 150.0,
) -> list[tuple[list[list[float]], float, float]]:
    """Decode and validate each alternate trip from Valhalla's 'alternates' array.

    Safely skips invalid or empty alternates without raising an exception.
    """
    raw_alternates = result.get("alternates")
    if not isinstance(raw_alternates, list):
        return []

    decoded: list[tuple[list[list[float]], float, float]] = []
    for item in raw_alternates:
        if not isinstance(item, dict):
            continue
        trip = item.get("trip")
        if not isinstance(trip, dict):
            continue
        try:
            coords, dist_m, dur_s = _decode_trip_metrics(
                trip, requested_locations, max_endpoint_tolerance_m
            )
            decoded.append((coords, dist_m, dur_s))
        except RouteFetchError:
            continue
    return decoded


def validate_cached_route(
    feature_collection: dict[str, Any],
    origin_id: str,
    destination_id: str,
    route_id: str,
    requested_locations: list[dict[str, float]] | None = None,
    requested_waypoint_ids: list[str] | None = None,
) -> tuple[float, float]:
    """Validate persisted GeoJSON and confirm its journey and Valhalla provenance."""
    if not isinstance(feature_collection, dict) or feature_collection.get("type") != "FeatureCollection":
        raise RouteFetchError("Cached route is not a GeoJSON FeatureCollection")
    features = feature_collection.get("features")
    if not isinstance(features, list) or len(features) != 1:
        raise RouteFetchError("Cached route must contain exactly one GeoJSON Feature")
    feature = features[0]
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise RouteFetchError("Cached route does not contain a GeoJSON Feature")
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    if geometry.get("type") != "LineString" or not isinstance(coordinates, list) or len(coordinates) < 2:
        raise RouteFetchError("Cached route geometry must be a non-empty LineString")
    for index, position in enumerate(coordinates):
        if (
            not isinstance(position, list)
            or len(position) < 2
            or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                for value in position
            )
            or not -180 <= position[0] <= 180
            or not -90 <= position[1] <= 90
        ):
            raise RouteFetchError(f"Cached route has invalid WGS84 coordinate at index {index}: {position!r}")

    properties = feature.get("properties") or feature_collection.get("metadata") or {}
    if properties.get("origin_landmark_id") != origin_id or properties.get("destination_landmark_id") != destination_id:
        raise RouteFetchError("Cached route origin/destination do not match the requested journey")
    if properties.get("route_id") != route_id:
        raise RouteFetchError("Cached route ID does not match the requested route")
    raw_waypoints = properties.get("via_waypoints", [])
    if not isinstance(raw_waypoints, list):
        raise RouteFetchError("Cached route waypoint sequence is malformed")
    cached_waypoint_ids = [
        item.get("landmark_id")
        for item in raw_waypoints
        if isinstance(item, dict)
    ]
    if len(cached_waypoint_ids) != len(raw_waypoints) or any(
        not isinstance(waypoint_id, str) or not waypoint_id
        for waypoint_id in cached_waypoint_ids
    ):
        raise RouteFetchError("Cached route waypoint sequence is malformed")
    is_hashed_waypoint_id = (
        route_id.startswith("via_")
        and len(route_id) == 20
        and all(character in "0123456789abcdef" for character in route_id[4:])
    )
    if is_hashed_waypoint_id and route_id != waypoint_route_id(
        origin_id, destination_id, cached_waypoint_ids
    ):
        raise RouteFetchError("Cached route ID does not match its ordered waypoint sequence")
    if requested_waypoint_ids is not None:
        if cached_waypoint_ids != requested_waypoint_ids:
            raise RouteFetchError("Cached route waypoint sequence does not match the requested route")
    if properties.get("costing") != "pedestrian" or "valhalla" not in str(properties.get("provider", "")).lower():
        raise RouteFetchError("Cached route lacks genuine Valhalla pedestrian-routing provenance")
    if "valhalla" not in str(properties.get("geometry_source", "")).lower():
        raise RouteFetchError("Cached route geometry source is not identified as Valhalla")

    if requested_locations is not None:
        if len(requested_locations) < 2:
            raise RouteFetchError("Requested route endpoints are incomplete")
        for endpoint_position, requested in (
            (coordinates[0], requested_locations[0]),
            (coordinates[-1], requested_locations[-1]),
        ):
            separation = haversine_distance_m(
                requested["lat"], requested["lon"], endpoint_position[1], endpoint_position[0]
            )
            if separation > 150.0:
                raise RouteFetchError(
                    f"Cached route endpoint is {separation:.1f} m from current requested coordinates "
                    "(exceeds 150 m tolerance)"
                )

    raw_distance = properties.get("distance_m")
    raw_duration = properties.get("estimated_duration_s")
    if isinstance(raw_distance, bool) or isinstance(raw_duration, bool):
        raise RouteFetchError("Cached route distance and duration must be numeric, not boolean")
    try:
        distance_m = float(raw_distance)
        duration_s = float(raw_duration)
    except (TypeError, ValueError) as exc:
        raise RouteFetchError("Cached route is missing numeric distance or duration") from exc
    if not math.isfinite(distance_m) or not math.isfinite(duration_s) or distance_m <= 0 or duration_s <= 0:
        raise RouteFetchError("Cached route distance and duration must be finite and positive")
    return distance_m, duration_s


def _point_to_line_distance_m(position: list[float], line: list[list[float]]) -> float:
    """Return a local-projection minimum distance from a GeoJSON point to a LineString."""
    lon, lat = position[:2]
    cos_lat = math.cos(math.radians(lat))
    best = float("inf")
    for start, end in zip(line, line[1:]):
        ax, ay = (start[0] - lon) * cos_lat * 111_320.0, (start[1] - lat) * 111_320.0
        bx, by = (end[0] - lon) * cos_lat * 111_320.0, (end[1] - lat) * 111_320.0
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        t = 0.0 if length_sq == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def routes_are_meaningfully_distinct(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Conservatively classify alternatives by detour length or substantial path divergence.

    A different ``via_waypoints`` label is not sufficient on its own: two
    requests that return the same corridor are duplicates even if one was
    routed through a landmark that lies on that corridor.  The route must
    differ in distance or in a substantial share of its geometry.
    """
    try:
        first_feature, second_feature = first["features"][0], second["features"][0]
        first_props = first_feature.get("properties", {})
        second_props = second_feature.get("properties", {})
        first_distance = float(first_props["distance_m"])
        second_distance = float(second_props["distance_m"])
        if abs(first_distance - second_distance) >= max(75.0, min(first_distance, second_distance) * 0.15):
            return True
        first_line = first_feature["geometry"]["coordinates"]
        second_line = second_feature["geometry"]["coordinates"]
        if len(first_line) < 2 or len(second_line) < 2:
            return False
        divergent_first = sum(_point_to_line_distance_m(point, second_line) > 35.0 for point in first_line) / len(first_line)
        divergent_second = sum(_point_to_line_distance_m(point, first_line) > 35.0 for point in second_line) / len(second_line)
        return max(divergent_first, divergent_second) >= 0.35
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
        return False


def route_detour_is_excessive(
    alternative: dict[str, Any],
    direct: dict[str, Any],
) -> bool:
    """Return True when an alternative's actual length exceeds the detour policy."""
    try:
        alternative_distance = float(alternative["features"][0]["properties"]["distance_m"])
        direct_distance = float(direct["features"][0]["properties"]["distance_m"])
    except (KeyError, IndexError, TypeError, ValueError):
        return True
    if direct_distance <= 0 or alternative_distance <= 0:
        return True
    return (
        alternative_distance - direct_distance > MAX_ACTUAL_EXTRA_DISTANCE_M
        or alternative_distance / direct_distance > MAX_ACTUAL_DETOUR_RATIO
    )


def load_valid_journey_cache(
    origin_id: str,
    destination_id: str,
    routes_dir: Path = ROUTE_DIR,
) -> dict[str, dict[str, Any]]:
    """Read and validate all persisted routes for one journey, including legacy core files."""
    paths = list(routes_dir.glob(f"{origin_id}__{destination_id}__*.geojson"))
    if origin_id == "grand_central_terminal" and destination_id == "bryant_park":
        paths.extend(routes_dir.glob("*.geojson"))
    valid: dict[str, dict[str, Any]] = {}
    candidates = load_landmark_candidates()
    requested_locations = [candidates[origin_id], candidates[destination_id]]
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            feature = data["features"][0]
            props = feature.get("properties", {})
            route_id = str(props.get("route_id", ""))
            validate_cached_route(data, origin_id, destination_id, route_id, requested_locations)
            if route_id:
                valid.setdefault(route_id, data)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, RouteFetchError):
            continue
    return valid


def create_geojson_feature_collection(
    route_id: str,
    route_def: dict[str, Any],
    coordinates: list[list[float]],
    distance_m: float,
    duration_s: float,
    origin: dict[str, Any],
    destination: dict[str, Any],
    endpoint: str,
    http_status: int = 200,
) -> dict[str, Any]:
    """Construct standard GeoJSON FeatureCollection with rich provenance and unverified disclosure."""
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": coordinates,
        },
        "properties": {
            "route_id": route_id,
            "route_name": route_def.get("name", route_id),
            "route_description": route_def.get("description", ""),
            "origin_landmark_id": origin["landmark_id"],
            "origin_landmark_name": origin["landmark_name"],
            "origin_routing_coordinate": {"lat": origin["lat"], "lon": origin["lon"]},
            "destination_landmark_id": destination["landmark_id"],
            "destination_landmark_name": destination["landmark_name"],
            "destination_routing_coordinate": {"lat": destination["lat"], "lon": destination["lon"]},
            "via_waypoints": [
                {
                    "landmark_id": wp["landmark_id"],
                    "landmark_name": wp["landmark_name"],
                    "lat": wp["lat"],
                    "lon": wp["lon"],
                }
                for wp in route_def.get("waypoints", [])
            ],
            "distance_m": round(distance_m, 2),
            "estimated_duration_s": round(duration_s, 1),
            "duration_type": "routing_engine_estimate",
            "costing": "pedestrian",
            "units": "kilometers",
            "provider": "Valhalla public demo",
            "provider_endpoint": endpoint.rstrip("/"),
            "retrieved_at": utc_now(),
            "http_status": http_status,
            "verification_status": "prototype_candidate_unverified",
            "coordinate_limitation_disclosure": COORDINATE_LIMITATION_DISCLOSURE,
            "geometry_source": "Valhalla response only; genuine pedestrian routing, no synthetic fallback",
        },
    }

    return {
        "type": "FeatureCollection",
        "name": route_id,
        "metadata": feature["properties"],
        "features": [feature],
    }


def request_json(
    url: str,
    *,
    method: str = "POST",
    payload: dict[str, Any] | None = None,
    max_retries: int = MAX_RETRIES,
    backoff_base_s: float = RETRY_BASE_SECONDS,
) -> tuple[Any, bytes, int]:
    """Execute JSON HTTP request to Valhalla with bounded retry and backoff for transient 429/503 errors."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Client-Id": CLIENT_ID,
        "Referer": "https://valhalla.openstreetmap.de/",
    }
    body = None
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.status
                raw = response.read()
            try:
                return json.loads(raw), raw, status
            except json.JSONDecodeError as exc:
                raise RouteFetchError(f"Valhalla returned non-JSON response: {raw[:500]!r}") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read(3000).decode("utf-8", errors="replace")
            if exc.code in RETRYABLE_HTTP_STATUSES and attempt < max_retries:
                retry_after_hdr = exc.headers.get("Retry-After")
                sleep_s = backoff_base_s * (2 ** (attempt - 1))
                if retry_after_hdr:
                    try:
                        sleep_s = max(float(retry_after_hdr), sleep_s)
                    except ValueError:
                        pass
                time.sleep(sleep_s)
                continue

            if exc.code == 429:
                raise RouteFetchError(
                    f"Valhalla service rate limited (HTTP 429, exhausted {max_retries} retries, Retry-After={exc.headers.get('Retry-After', 'unspecified')}): {detail}"
                ) from exc
            if exc.code in RETRYABLE_HTTP_STATUSES:
                raise RouteFetchError(
                    f"Valhalla service returned HTTP {exc.code} (exhausted {max_retries} retries): {detail.strip()}"
                ) from exc
            raise RouteFetchError(f"Valhalla service returned HTTP {exc.code}: {detail.strip()}") from exc
        except urllib.error.URLError as exc:
            if attempt < max_retries:
                time.sleep(backoff_base_s * (2 ** (attempt - 1)))
                continue
            raise RouteFetchError(
                f"Valhalla network/DNS failure for {urllib.parse.urlsplit(url).netloc} "
                f"(exhausted {max_retries} retries): {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            if attempt < max_retries:
                time.sleep(backoff_base_s * (2 ** (attempt - 1)))
                continue
            raise RouteFetchError(f"Valhalla service timed out for {url} (exhausted {max_retries} retries)") from exc

    raise RouteFetchError(f"Exhausted {max_retries} retries for {url}")


def request_route(
    endpoint: str,
    route_id: str,
    route_def: dict[str, Any],
    origin: dict[str, Any],
    destination: dict[str, Any],
    skip_existing: bool = True,
    routes_dir: Path = ROUTE_DIR,
    raw_dir: Path = RAW_DIR,
    max_retries: int = MAX_RETRIES,
    backoff_base_s: float = RETRY_BASE_SECONDS,
    request_delay_s: float = REQUEST_DELAY_SECONDS,
    num_alternates: int = 0,
) -> dict[str, Any]:
    """Request a single route from Valhalla (or reuse existing valid cache) and save raw artifacts and GeoJSON."""
    orig_id = origin["landmark_id"]
    dest_id = destination["landmark_id"]
    locations = route_def["locations"]
    prefix = f"{orig_id}__{dest_id}__{route_id}"

    routes_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    scoped_filename = f"{prefix}.geojson"
    output_path = routes_dir / scoped_filename
    is_core_journey = (orig_id == "grand_central_terminal" and dest_id == "bryant_park")
    legacy_output_path = routes_dir / f"{route_id}.geojson" if is_core_journey else None

    request_path = raw_dir / f"{prefix}_request.json"
    response_path = raw_dir / f"{prefix}_response.json"

    # Cache check: if valid GeoJSON already exists and skip_existing is True, reuse it
    target_cache_path = None
    if skip_existing:
        if output_path.exists():
            target_cache_path = output_path
        elif legacy_output_path and legacy_output_path.exists():
            target_cache_path = legacy_output_path

    if target_cache_path is not None:
        try:
            cached_data = json.loads(target_cache_path.read_text(encoding="utf-8"))
            d_m, dur_s = validate_cached_route(
                cached_data,
                orig_id,
                dest_id,
                route_id,
                locations,
                [wp["landmark_id"] for wp in route_def.get("waypoints", [])],
            )
            # Ensure scoped output path is populated if loaded from legacy.
            if target_cache_path != output_path:
                output_path.write_text(json.dumps(cached_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            cached_native: list[tuple[list[list[float]], float, float]] = []
            if num_alternates > 0 and response_path.exists():
                try:
                    raw_cached_resp = json.loads(response_path.read_text(encoding="utf-8"))
                    cached_native = extract_native_alternates_from_response(raw_cached_resp, locations)
                except (json.JSONDecodeError, OSError):
                    cached_native = []
            return {
                "feature_collection": cached_data,
                "output_path": str(output_path),
                "raw_response_path": str(response_path) if response_path.exists() else None,
                "distance_m": d_m,
                "duration_s": dur_s,
                "cached": True,
                "native_alternates": cached_native,
            }
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            pass  # Corrupt cache, proceed to live request
        except RouteFetchError:
            pass  # Invalid or mismatched cache, proceed to live request

    # Check for previous rejection record if skip_existing is True
    rejected_marker_path = raw_dir / f"{prefix}__rejected.json"
    if skip_existing and rejected_marker_path.exists():
        try:
            rejected_data = json.loads(rejected_marker_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            rejected_data = {}
        return {
            "feature_collection": None,
            "output_path": None,
            "raw_response_path": str(response_path) if response_path.exists() else None,
            "distance_m": None,
            "duration_s": None,
            "cached": True,
            "rejected": True,
            "rejection_reason": rejected_data.get("reason", "previously_rejected"),
            "meaningful": False,
            "native_alternates": [],
        }

    # Live request
    payload = {
        "locations": locations,
        "costing": "pedestrian",
        "units": "kilometers",
        "id": route_id,
        "shape_format": "polyline6",
    }
    if num_alternates > 0 and len(locations) == 2:
        payload["alternates"] = num_alternates

    request_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if is_core_journey:
        (raw_dir / f"{route_id}_request.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if request_delay_s > 0:
        time.sleep(request_delay_s)
    valhalla_url = endpoint.rstrip("/") + "/route"
    result, raw_bytes, http_status = request_json(
        valhalla_url, method="POST", payload=payload,
        max_retries=max_retries, backoff_base_s=backoff_base_s,
    )
    response_path.write_bytes(raw_bytes)
    if is_core_journey:
        (raw_dir / f"{route_id}_response.json").write_bytes(raw_bytes)

    coordinates, distance_m, duration_s = validate_route_response(result, locations)
    native_alternates = (
        extract_native_alternates_from_response(result, locations)
        if num_alternates > 0 and len(locations) == 2
        else []
    )

    feature_collection = create_geojson_feature_collection(
        route_id=route_id,
        route_def=route_def,
        coordinates=coordinates,
        distance_m=distance_m,
        duration_s=duration_s,
        origin=origin,
        destination=destination,
        endpoint=endpoint,
        http_status=http_status,
    )

    output_path.write_text(json.dumps(feature_collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if legacy_output_path:
        legacy_output_path.write_text(json.dumps(feature_collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "feature_collection": feature_collection,
        "output_path": str(output_path),
        "raw_response_path": str(response_path),
        "distance_m": distance_m,
        "duration_s": duration_s,
        "cached": False,
        "native_alternates": native_alternates,
    }


def request_native_alternates(
    endpoint: str,
    origin: dict[str, Any],
    destination: dict[str, Any],
    num_alternates: int,
    raw_dir: Path = RAW_DIR,
    max_retries: int = MAX_RETRIES,
    backoff_base_s: float = RETRY_BASE_SECONDS,
    request_delay_s: float = REQUEST_DELAY_SECONDS,
) -> list[tuple[list[list[float]], float, float]]:
    """Request native Valhalla alternatives without replacing the cached baseline route."""
    origin_id = origin["landmark_id"]
    destination_id = destination["landmark_id"]
    locations = [
        {"lat": origin["lat"], "lon": origin["lon"]},
        {"lat": destination["lat"], "lon": destination["lon"]},
    ]
    payload = {
        "locations": locations,
        "costing": "pedestrian",
        "units": "kilometers",
        "id": f"{origin_id}__{destination_id}__native_alternates",
        "shape_format": "polyline6",
        "alternates": num_alternates,
    }
    prefix = f"{origin_id}__{destination_id}__native_alternates"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{prefix}_request.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    if request_delay_s > 0:
        time.sleep(request_delay_s)
    response, raw_bytes, _http_status = request_json(
        endpoint.rstrip("/") + "/route",
        method="POST",
        payload=payload,
        max_retries=max_retries,
        backoff_base_s=backoff_base_s,
    )
    (raw_dir / f"{prefix}_response.json").write_bytes(raw_bytes)
    validate_route_response(response, locations)
    return extract_native_alternates_from_response(response, locations)


def _feature_coordinates(result: dict[str, Any] | None) -> list[list[float]] | None:
    """Extract LineString coordinates from a successful route result."""
    if not result:
        return None
    try:
        coordinates = result["feature_collection"]["features"][0]["geometry"]["coordinates"]
    except (KeyError, IndexError, TypeError):
        return None
    return coordinates if isinstance(coordinates, list) and len(coordinates) >= 2 else None


def _load_cached_direct_coordinates(
    origin_id: str,
    destination_id: str,
    routes_dir: Path,
) -> list[list[float]] | None:
    """Read the cached direct route geometry, preferring the scoped filename."""
    paths = [routes_dir / f"{origin_id}__{destination_id}__direct.geojson"]
    if origin_id == "grand_central_terminal" and destination_id == "bryant_park":
        paths.append(routes_dir / "direct.geojson")
    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            coordinates = data["features"][0]["geometry"]["coordinates"]
            if isinstance(coordinates, list) and len(coordinates) >= 2:
                return coordinates
        except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            continue
    return None


def _comparison_details(
    candidate: dict[str, Any],
    reference: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact distance metrics for a rejection record."""
    if not reference:
        return {}
    try:
        candidate_distance = float(candidate["features"][0]["properties"]["distance_m"])
        reference_distance = float(reference["features"][0]["properties"]["distance_m"])
    except (KeyError, IndexError, TypeError, ValueError):
        return {}
    return {
        "alternative_distance_m": round(candidate_distance, 2),
        "reference_distance_m": round(reference_distance, 2),
        "distance_difference_m": round(candidate_distance - reference_distance, 2),
        "detour_ratio": round(candidate_distance / reference_distance, 4) if reference_distance > 0 else None,
    }


def _remove_generated_route_files(
    result: dict[str, Any],
    route_id: str,
    origin_id: str,
    destination_id: str,
    routes_dir: Path,
) -> None:
    """Delete only the GeoJSON files written for a rejected, newly generated route."""
    paths = []
    if result.get("output_path"):
        paths.append(Path(result["output_path"]))
    if origin_id == "grand_central_terminal" and destination_id == "bryant_park":
        paths.append(routes_dir / f"{route_id}.geojson")
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            continue


def _write_rejection_record(
    raw_dir: Path,
    origin_id: str,
    destination_id: str,
    route_id: str,
    reason: str,
    compared_to: str | None,
    details: dict[str, Any],
) -> None:
    """Persist a rejection marker while retaining the raw request/response evidence."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{origin_id}__{destination_id}__{route_id}__rejected.json"
    payload = {
        "rejected_at": utc_now(),
        "origin": origin_id,
        "destination": destination_id,
        "route_id": route_id,
        "reason": reason,
        "compared_to": compared_to,
        **details,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _reject_non_distinct_alternatives(
    results: dict[str, dict[str, Any]],
    origin_id: str,
    destination_id: str,
    routes_dir: Path,
    raw_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Remove newly generated duplicate/excessive-detour alternatives.

    Existing cached routes are never deleted: they are preserved for cache
    compatibility and marked ``meaningful = False`` instead.  Raw request and
    response artifacts are always retained; a ``__rejected.json`` marker
    records why the candidate was not counted.
    """
    direct = results.get("direct", {}).get("feature_collection")
    accepted: list[tuple[str, dict[str, Any]]] = []
    if direct:
        accepted.append(("direct", direct))

    for route_id in sorted(key for key in results if key != "direct"):
        result = results[route_id]
        if result.get("rejected"):
            # Already rejected in a previous run and cached via __rejected.json
            results.pop(route_id)
            continue
        feature_collection = result.get("feature_collection")
        if not feature_collection:
            continue

        reason: str | None = None
        compared_to: str | None = None

        if direct and route_detour_is_excessive(feature_collection, direct):
            reason = "excessive_detour"
            compared_to = "direct"
        else:
            for accepted_id, accepted_feature in accepted:
                if not routes_are_meaningfully_distinct(accepted_feature, feature_collection):
                    reason = "duplicate_path"
                    compared_to = accepted_id
                    break

        if reason is None:
            accepted.append((route_id, feature_collection))
            continue

        reference = results.get(compared_to, {}).get("feature_collection") if compared_to else direct
        _write_rejection_record(
            raw_dir,
            origin_id,
            destination_id,
            route_id,
            reason,
            compared_to,
            _comparison_details(feature_collection, reference),
        )

        # Never delete a route file that existed before this run, even when
        # ``skip_existing=False`` regenerated it.  Cache preservation wins over
        # cleaning up a duplicate.
        if result.get("cached", False) or result.get("_pre_existing_route_files", False):
            result["meaningful"] = False
            result["rejection_reason"] = reason
            continue

        _remove_generated_route_files(result, route_id, origin_id, destination_id, routes_dir)
        results.pop(route_id)

    return results


def fetch_all_routes(
    origin_id: str = "grand_central_terminal",
    destination_id: str = "bryant_park",
    endpoint: str = DEFAULT_ENDPOINT,
    route_filter: str = "all",
    alternative_strategy: str | None = None,
    max_alternatives: int = MAX_ALTERNATIVES_PER_JOURNEY,
    via_ids: list[str] | None = None,
    skip_existing: bool = True,
    routes_dir: Path = ROUTE_DIR,
    raw_dir: Path = RAW_DIR,
    max_retries: int = MAX_RETRIES,
    backoff_base_s: float = RETRY_BASE_SECONDS,
    request_delay_s: float = REQUEST_DELAY_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Fetch requested routes from Valhalla, preserving successful routes on partial failure.

    ``route_filter="all"`` fetches the direct baseline first, then derives
    geographically relevant alternatives from the direct geometry.  A failed
    alternative is recorded in a namespaced error artifact and never
    invalidates a successful direct route.
    """
    strategy_was_requested = alternative_strategy is not None
    if alternative_strategy is None:
        alternative_strategy = "both"
    if alternative_strategy not in {"landmark", "native", "both"}:
        raise RouteFetchError(
            "alternative_strategy must be one of: landmark, native, both"
        )
    if (
        isinstance(max_alternatives, bool)
        or not isinstance(max_alternatives, int)
        or not 1 <= max_alternatives <= MAX_ALTERNATIVES_PER_JOURNEY
    ):
        raise RouteFetchError(
            f"max_alternatives must be between 1 and {MAX_ALTERNATIVES_PER_JOURNEY}"
        )

    candidates = load_landmark_candidates()
    validate_endpoints(origin_id, destination_id, candidates, via_ids)

    origin = candidates[origin_id]
    destination = candidates[destination_id]

    results: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}
    attempted: set[str] = set()

    def attempt(definitions: dict[str, dict[str, Any]], num_alternates: int = 0) -> None:
        for r_id, r_def in definitions.items():
            attempted.add(r_id)
            scoped_path = routes_dir / f"{origin_id}__{destination_id}__{r_id}.geojson"
            legacy_path = routes_dir / f"{r_id}.geojson"
            pre_existing = scoped_path.exists() or (
                origin_id == "grand_central_terminal"
                and destination_id == "bryant_park"
                and legacy_path.exists()
            )
            try:
                res = request_route(
                    endpoint,
                    r_id,
                    r_def,
                    origin,
                    destination,
                    skip_existing=skip_existing,
                    routes_dir=routes_dir,
                    raw_dir=raw_dir,
                    max_retries=max_retries,
                    backoff_base_s=backoff_base_s,
                    request_delay_s=request_delay_s,
                    num_alternates=(num_alternates if r_id == "direct" else 0),
                )
                res["_pre_existing_route_files"] = pre_existing
                results[r_id] = res
            except Exception as exc:
                raw_dir.mkdir(parents=True, exist_ok=True)
                err_path = raw_dir / f"{origin_id}__{destination_id}__{r_id}__error.json"
                err_payload = {
                    "failed_at": utc_now(),
                    "origin": origin_id,
                    "destination": destination_id,
                    "route_id": r_id,
                    "endpoint": endpoint,
                    "error": str(exc),
                }
                err_path.write_text(json.dumps(err_payload, indent=2) + "\n", encoding="utf-8")
                failures[r_id] = str(exc)

    if route_filter == "direct":
        definitions = build_route_definitions(
            origin_id,
            destination_id,
            candidates,
            include_alternatives=False,
        )
        attempt({"direct": definitions["direct"]})
    elif via_ids is not None:
        cached_routes = load_valid_journey_cache(
            origin_id, destination_id, routes_dir
        )
        definitions = build_route_definitions(
            origin_id,
            destination_id,
            candidates,
            via_ids=via_ids,
        )
        selected = definitions if route_filter == "all" else {route_filter: definitions[route_filter]}
        attempt(selected)
        if results:
            merged = {
                route_id: {
                    "feature_collection": feature_collection,
                    "cached": True,
                }
                for route_id, feature_collection in cached_routes.items()
            }
            merged.update(results)
            results = _reject_non_distinct_alternatives(
                merged, origin_id, destination_id, routes_dir, raw_dir
            )
    elif route_filter == "all":
        # Fetch the baseline first with native alternates requested, so alternatives
        # can be ranked against its actual geometry rather than a straight-line approximation.
        direct_definitions = build_route_definitions(
            origin_id,
            destination_id,
            candidates,
            include_alternatives=False,
        )
        native_count = (
            max_alternatives if alternative_strategy in {"native", "both"} else 0
        )
        attempt({"direct": direct_definitions["direct"]}, num_alternates=native_count)

        for cached_id, cached_feature_collection in sorted(
            load_valid_journey_cache(origin_id, destination_id, routes_dir).items()
        ):
            if cached_id == "direct" or cached_id in results:
                continue
            if len([key for key in results if key != "direct"]) >= max_alternatives:
                break
            feature = cached_feature_collection["features"][0]
            properties = feature.get("properties", {})
            results[cached_id] = {
                "feature_collection": cached_feature_collection,
                "output_path": str(
                    routes_dir / f"{origin_id}__{destination_id}__{cached_id}.geojson"
                ),
                "distance_m": properties.get("distance_m"),
                "duration_s": properties.get("estimated_duration_s"),
                "cached": True,
            }

        reference = _feature_coordinates(results.get("direct"))
        if alternative_strategy in {"landmark", "both"}:
            try:
                alternative_definitions = build_route_definitions(
                    origin_id,
                    destination_id,
                    candidates,
                    reference_coordinates=reference,
                    max_alternatives=max_alternatives,
                )
                alternative_definitions.pop("direct", None)
                attempt(alternative_definitions)
            except Exception as exc:
                # A failure while deriving alternatives must not discard a
                # successful direct baseline.
                failures["alternatives"] = str(exc)
                print(
                    f"Alternative selection failed for {origin_id} -> {destination_id}: {exc}",
                    file=sys.stderr,
                )
        try:
            results = _reject_non_distinct_alternatives(
                results, origin_id, destination_id, routes_dir, raw_dir
            )
        except Exception as exc:
            # Keep accepted routes even if post-processing fails.
            failures["rejection"] = str(exc)
            print(
                f"Alternative rejection failed for {origin_id} -> {destination_id}: {exc}",
                file=sys.stderr,
            )

        # Supplement with native Valhalla alternatives only when the strategy permits it.
        direct_result = results.get("direct")
        if direct_result and direct_result.get("feature_collection"):
            direct_fc = direct_result["feature_collection"]
            native_candidates = direct_result.get("native_alternates") or []
            native_slot_available = any(
                f"alternative_{slot}" not in results
                and not (
                    skip_existing
                    and (
                        raw_dir
                        / f"{origin_id}__{destination_id}__alternative_{slot}__rejected.json"
                    ).exists()
                )
                for slot in range(1, max_alternatives + 1)
            )
            if (
                alternative_strategy in {"native", "both"}
                and (strategy_was_requested or not direct_result.get("cached", False))
                and not native_candidates
                and native_slot_available
            ):
                try:
                    native_candidates = request_native_alternates(
                        endpoint,
                        origin,
                        destination,
                        num_alternates=max_alternatives,
                        raw_dir=raw_dir,
                        max_retries=max_retries,
                        backoff_base_s=backoff_base_s,
                        request_delay_s=request_delay_s,
                    )
                except (RouteFetchError, OSError, ValueError) as exc:
                    failures["native_alternates"] = str(exc)

            accepted_alt_count = sum(
                1
                for key, value in results.items()
                if key != "direct"
                and value.get("meaningful") is not False
            )

            if accepted_alt_count < max_alternatives and native_candidates:
                for alt_coords, alt_dist, alt_dur in native_candidates:
                    accepted_alt_count = sum(
                        1
                        for key, value in results.items()
                        if key != "direct"
                        and value.get("meaningful") is not False
                    )
                    if accepted_alt_count >= max_alternatives:
                        break
                    slot_idx = 1
                    while f"alternative_{slot_idx}" in results:
                        slot_idx += 1
                    target_route_id = f"alternative_{slot_idx}"

                    rejection_marker = raw_dir / f"{origin_id}__{destination_id}__{target_route_id}__rejected.json"
                    if skip_existing and rejection_marker.exists():
                        continue

                    target_path = routes_dir / f"{origin_id}__{destination_id}__{target_route_id}.geojson"
                    if skip_existing and target_path.exists():
                        try:
                            cached_fc = json.loads(target_path.read_text(encoding="utf-8"))
                            c_dist, c_dur = validate_cached_route(
                                cached_fc, origin_id, destination_id, target_route_id, [origin, destination]
                            )
                            results[target_route_id] = {
                                "feature_collection": cached_fc,
                                "output_path": str(target_path),
                                "distance_m": c_dist,
                                "duration_s": c_dur,
                                "cached": True,
                            }
                            continue
                        except Exception:
                            pass

                    alt_def = {
                        "name": f"Alternative Route {slot_idx} (via Street Grid)",
                        "description": "Pedestrian route through parallel corridor generated by Valhalla routing engine.",
                        "waypoints": [],
                        "locations": [
                            {"lat": origin["lat"], "lon": origin["lon"]},
                            {"lat": destination["lat"], "lon": destination["lon"]},
                        ],
                    }
                    alt_fc = create_geojson_feature_collection(
                        route_id=target_route_id,
                        route_def=alt_def,
                        coordinates=alt_coords,
                        distance_m=alt_dist,
                        duration_s=alt_dur,
                        origin=origin,
                        destination=destination,
                        endpoint=endpoint,
                    )

                    reason: str | None = None
                    compared_to: str | None = None

                    if route_detour_is_excessive(alt_fc, direct_fc):
                        reason = "excessive_detour"
                        compared_to = "direct"
                    elif not routes_are_meaningfully_distinct(direct_fc, alt_fc):
                        reason = "duplicate_path"
                        compared_to = "direct"
                    else:
                        for acc_id, acc_res in list(results.items()):
                            if acc_id == "direct":
                                continue
                            acc_fc = acc_res.get("feature_collection")
                            if acc_fc and not routes_are_meaningfully_distinct(acc_fc, alt_fc):
                                reason = "duplicate_path"
                                compared_to = acc_id
                                break

                    if reason is not None:
                        ref_fc = results.get(compared_to, {}).get("feature_collection") if compared_to else direct_fc
                        _write_rejection_record(
                            raw_dir,
                            origin_id,
                            destination_id,
                            target_route_id,
                            reason,
                            compared_to,
                            _comparison_details(alt_fc, ref_fc),
                        )
                        continue

                    target_path.write_text(json.dumps(alt_fc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    results[target_route_id] = {
                        "feature_collection": alt_fc,
                        "output_path": str(target_path),
                        "distance_m": alt_dist,
                        "duration_s": alt_dur,
                        "cached": False,
                    }
    else:
        reference = _load_cached_direct_coordinates(origin_id, destination_id, routes_dir)
        definitions = build_route_definitions(
            origin_id,
            destination_id,
            candidates,
            reference_coordinates=reference,
        )
        if route_filter not in definitions:
            raise RouteFetchError(
                f"Unknown route ID '{route_filter}' for {origin_id} -> {destination_id}. "
                f"Available: {sorted(definitions)}"
            )
        attempt({route_filter: definitions[route_filter]})

    if not results:
        raw_dir.mkdir(parents=True, exist_ok=True)
        journey_err_path = raw_dir / f"{origin_id}__{destination_id}__route_error.json"
        error_msg = "; ".join(f"{k}: {v}" for k, v in failures.items()) or "No routes could be generated"
        journey_err_payload = {
            "failed_at": utc_now(),
            "origin": origin_id,
            "destination": destination_id,
            "endpoint": endpoint,
            "error": error_msg,
            "failed_routes": failures,
        }
        journey_err_path.write_text(json.dumps(journey_err_payload, indent=2) + "\n", encoding="utf-8")
        if origin_id == "grand_central_terminal" and destination_id == "bryant_park":
            (raw_dir / "route_error.json").write_text(json.dumps(journey_err_payload, indent=2) + "\n", encoding="utf-8")
        raise RouteFetchError(
            f"Failed to fetch any routes for {origin_id} -> {destination_id}: {error_msg}"
        )

    if failures:
        if results.get("direct"):
            results["direct"]["generation_errors"] = dict(failures)
        print(
            f"Partial availability for {origin_id} -> {destination_id}: "
            f"{len(results)}/{len(attempted)} routes available. "
            f"Failed routes: {list(failures.keys())}",
            file=sys.stderr,
        )

    return results


def generate_all_journeys(
    endpoint: str = DEFAULT_ENDPOINT,
    routes_dir: Path = ROUTE_DIR,
    raw_dir: Path = RAW_DIR,
    skip_existing: bool = True,
    max_retries: int = MAX_RETRIES,
    backoff_base_s: float = RETRY_BASE_SECONDS,
    request_delay_s: float = REQUEST_DELAY_SECONDS,
) -> dict[str, Any]:
    """Generate/resume a direct baseline plus relevant alternatives for each directed pair.

    The direct baseline is required for a journey to count as validated.
    Alternatives are attempted independently; a failed or rejected alternative
    never invalidates a successful direct route.  Existing cached routes are
    preserved.  The report is written after each pair, so interrupted runs can
    safely resume.
    """
    candidates = load_landmark_candidates()
    landmark_ids = sorted(LEGACY_LANDMARK_IDS.intersection(candidates))
    missing_legacy = LEGACY_LANDMARK_IDS - set(landmark_ids)
    if missing_legacy:
        raise RouteFetchError(
            f"Missing original route candidates: {', '.join(sorted(missing_legacy))}"
        )

    report_path = raw_dir / "route_coverage_report.json"
    if skip_existing and report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report.get("endpoint") != endpoint:
                report = {}
        except (json.JSONDecodeError, OSError):
            report = {}
    else:
        report = {}

    journeys: dict[str, Any] = report.get("journeys", {})
    pairs = [(origin, destination) for origin in landmark_ids for destination in landmark_ids if origin != destination]
    total = len(pairs)
    for index, (origin_id, destination_id) in enumerate(pairs, start=1):
        key = f"{origin_id}__{destination_id}"
        print(f"[{index}/{total}] {origin_id} -> {destination_id}", flush=True)
        try:
            result = fetch_all_routes(
                origin_id=origin_id,
                destination_id=destination_id,
                endpoint=endpoint,
                route_filter="all",
                skip_existing=skip_existing,
                routes_dir=routes_dir,
                raw_dir=raw_dir,
                max_retries=max_retries,
                backoff_base_s=backoff_base_s,
                request_delay_s=request_delay_s,
            )
            direct_result = result.get("direct")
            valid_routes = load_valid_journey_cache(origin_id, destination_id, routes_dir)
            route_ids = sorted(valid_routes)
            alternative_ids = [route_id for route_id in route_ids if route_id != "direct"]
            distinct_alternative_ids = sorted(
                route_id for route_id in alternative_ids
                if "direct" in valid_routes
                and routes_are_meaningfully_distinct(valid_routes["direct"], valid_routes[route_id])
            )

            rejected_ids = sorted({
                path.name.split("__")[2]
                for path in raw_dir.glob(f"{origin_id}__{destination_id}__*__rejected.json")
            } - set(route_ids))
            failed_ids = sorted({
                path.name.split("__")[2]
                for path in raw_dir.glob(f"{origin_id}__{destination_id}__*__error.json")
            } - set(route_ids))

            if direct_result:
                status = "route_generated_candidate_unverified"
                validation_status = "validated"
                failure_reason = None
            elif route_ids:
                status = "partial_alternative_only"
                validation_status = "partial"
                failure_reason = "Direct baseline unavailable"
            else:
                status = "failed"
                validation_status = "failed"
                failure_reason = "No valid routes available"

            journeys[key] = {
                "origin_id": origin_id,
                "destination_id": destination_id,
                "status": status,
                "route_validation_status": validation_status,
                "endpoint_access_verification_status": "candidate_unverified",
                "route_count_generated": sum(
                    1 for route_result in result.values() if not route_result.get("cached")
                ),
                "valid_route_count": len(route_ids),
                "route_ids": route_ids,
                "alternative_route_ids": alternative_ids,
                "distinct_alternative_ids": distinct_alternative_ids,
                "rejected_alternative_ids": rejected_ids,
                "failed_route_ids": failed_ids,
                "requested_route_ids": sorted(
                    set(route_ids) | set(rejected_ids) | set(failed_ids)
                ),
                "meaningful_route_count": (1 if "direct" in valid_routes else 0)
                + len(distinct_alternative_ids),
                "cached": bool(direct_result.get("cached")) if direct_result else False,
                "distance_m": direct_result["distance_m"] if direct_result else None,
                "duration_s": direct_result["duration_s"] if direct_result else None,
                "route_path": direct_result["output_path"] if direct_result else None,
                "failure_reason": failure_reason,
                "updated_at": utc_now(),
            }
            print(
                f"  {len(route_ids)} valid route(s), "
                f"{len(distinct_alternative_ids)} meaningful alternative(s)",
                flush=True,
            )
        except Exception as exc:
            try:
                valid_routes = load_valid_journey_cache(origin_id, destination_id, routes_dir)
                route_ids = sorted(valid_routes)
            except Exception:
                valid_routes, route_ids = {}, []
            journeys[key] = {
                "origin_id": origin_id,
                "destination_id": destination_id,
                "status": "failed",
                "route_validation_status": "failed",
                "endpoint_access_verification_status": "candidate_unverified",
                "route_count_generated": 0,
                "valid_route_count": len(route_ids),
                "route_ids": route_ids,
                "alternative_route_ids": [route_id for route_id in route_ids if route_id != "direct"],
                "distinct_alternative_ids": [],
                "rejected_alternative_ids": [],
                "failed_route_ids": [],
                "requested_route_ids": route_ids,
                "meaningful_route_count": 0,
                "cached": False,
                "distance_m": None,
                "duration_s": None,
                "route_path": None,
                "failure_reason": str(exc),
                "updated_at": utc_now(),
            }
            print(f"  FAILED: {exc}", file=sys.stderr, flush=True)

        validated = sum(1 for item in journeys.values() if item.get("route_validation_status") == "validated")
        partial = sum(1 for item in journeys.values() if item.get("status") == "partial_alternative_only")
        failed = sum(1 for item in journeys.values() if item.get("status") == "failed")
        report = {
            "schema_version": 2,
            "endpoint": endpoint,
            "costing": "pedestrian",
            "updated_at": utc_now(),
            "expected_journey_count": total,
            "successful_journey_count": validated,
            "validated_route_journey_count": validated,
            "partial_journey_count": partial,
            "failed_journey_count": failed,
            "valid_route_count": sum(int(item.get("valid_route_count", 0)) for item in journeys.values()),
            "journeys_with_meaningful_alternatives": sorted(
                key for key, item in journeys.items() if item.get("distinct_alternative_ids")
            ),
            "journeys_with_two_or_more_routes": sorted(
                key for key, item in journeys.items() if int(item.get("valid_route_count", 0)) >= 2
            ),
            "alternative_policy": {
                "max_alternatives_per_journey": MAX_ALTERNATIVES_PER_JOURNEY,
                "max_detour_ratio": MAX_DETOUR_RATIO,
                "max_extra_distance_m": MAX_EXTRA_DISTANCE_M,
                "min_endpoint_clearance_m": MIN_ENDPOINT_CLEARANCE_M,
                "min_lateral_offset_m": MIN_LATERAL_OFFSET_M,
                "min_waypoint_separation_m": MIN_WAYPOINT_SEPARATION_M,
                "max_actual_detour_ratio": MAX_ACTUAL_DETOUR_RATIO,
                "max_actual_extra_distance_m": MAX_ACTUAL_EXTRA_DISTANCE_M,
            },
            "journeys": journeys,
            "coordinate_verification_note": "Routes use candidate access coordinates; public access points remain unverified.",
        }
        raw_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="grand_central_terminal", help="Origin landmark ID")
    parser.add_argument("--destination", default="bryant_park", help="Destination landmark ID")
    parser.add_argument("--endpoint", default=os.environ.get("VALHALLA_URL", DEFAULT_ENDPOINT), help="Valhalla endpoint URL")
    parser.add_argument("--route", default="all", help="Specific route ID to fetch, or 'all'")
    parser.add_argument("--via", default=None, help="Comma-separated landmark IDs for intermediate waypoints")
    parser.add_argument("--force", action="store_true", help="Bypass cache and force re-fetching from Valhalla")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate/resume the direct baseline and relevant alternatives for all 72 directed journeys",
    )
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY_SECONDS, help="Delay in seconds before each live route request")
    parser.add_argument("--retries", type=int, default=MAX_RETRIES, help="Maximum attempts per route request")
    parser.add_argument("--backoff", type=float, default=RETRY_BASE_SECONDS, help="Base retry backoff in seconds")
    args = parser.parse_args()

    via_list = [v.strip() for v in args.via.split(",")] if args.via else None

    try:
        if args.delay < 0 or args.retries < 1 or args.backoff < 0:
            parser.error("--delay and --backoff must be non-negative; --retries must be at least 1")
        if args.all:
            report = generate_all_journeys(
                endpoint=args.endpoint,
                skip_existing=not args.force,
                max_retries=args.retries,
                backoff_base_s=args.backoff,
                request_delay_s=args.delay,
            )
            print(
                f"Validated route generations: {report['validated_route_journey_count']}/{report['expected_journey_count']} journeys "
                "(candidate access points remain unverified); "
                f"{report['valid_route_count']} valid routes; "
                f"{len(report['journeys_with_meaningful_alternatives'])} journeys with meaningful alternatives; "
                f"{report['partial_journey_count']} partial; {report['failed_journey_count']} failures."
            )
            return 0 if report["validated_route_journey_count"] == report["expected_journey_count"] else 1
        results = fetch_all_routes(
            origin_id=args.origin,
            destination_id=args.destination,
            endpoint=args.endpoint,
            route_filter=args.route,
            via_ids=via_list,
            skip_existing=not args.force,
            max_retries=args.retries,
            backoff_base_s=args.backoff,
            request_delay_s=args.delay,
        )
        for r_id, r_info in results.items():
            cache_note = " (cached)" if r_info.get("cached") else ""
            print(
                f"Successfully loaded {r_id}{cache_note}: {r_info['distance_m']:.1f} m, "
                f"{r_info['duration_s']:.0f} s -> {r_info['output_path']}"
            )
        return 0
    except (RouteFetchError, OSError, json.JSONDecodeError, KeyError) as exc:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        error_path = RAW_DIR / f"{args.origin}__{args.destination}__route_error.json"
        error_payload = {
            "failed_at": utc_now(),
            "origin": args.origin,
            "destination": args.destination,
            "endpoint": args.endpoint,
            "error": str(exc),
        }
        error_path.write_text(json.dumps(error_payload, indent=2) + "\n", encoding="utf-8")
        if args.origin == "grand_central_terminal" and args.destination == "bryant_park":
            (RAW_DIR / "route_error.json").write_text(json.dumps(error_payload, indent=2) + "\n", encoding="utf-8")
        print(f"Route retrieval failed: {exc}. Error saved to {error_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
