"""Cache-independent, single-route Valhalla service for Jaunt V1.

This module deliberately does not read or write route-cache files. It reuses the
existing Valhalla transport, candidate-coordinate loader, response validator,
and GeoJSON builder from fetch_routes.py.
"""

from __future__ import annotations

from typing import Any

from fetch_routes import (
    DEFAULT_ENDPOINT,
    RouteFetchError,
    SELECTED_LANDMARK_IDS,
    create_geojson_feature_collection,
    load_landmark_candidates,
    request_json,
    validate_route_response,
)


class V1RouteValidationError(ValueError):
    """Raised when a V1 route request contains invalid landmark selections."""


def validate_waypoint_selection(
    origin_id: str,
    destination_id: str,
    waypoint_ids: list[str],
    *,
    allowed_ids: set[str] | None = None,
) -> None:
    """Validate endpoint and ordered waypoint IDs against the V1 catalogue."""
    allowed = set(allowed_ids or SELECTED_LANDMARK_IDS)

    if origin_id not in allowed:
        raise V1RouteValidationError(f"Unknown or unavailable origin landmark: {origin_id}")
    if destination_id not in allowed:
        raise V1RouteValidationError(
            f"Unknown or unavailable destination landmark: {destination_id}"
        )
    if not isinstance(waypoint_ids, list):
        raise V1RouteValidationError("waypoint_ids must be a list")
    if len(waypoint_ids) > max(0, len(allowed) - 1):
        raise V1RouteValidationError("Too many intermediate waypoints")
    if any(not isinstance(item, str) for item in waypoint_ids):
        raise V1RouteValidationError("Every waypoint ID must be a string")
    if len(set(waypoint_ids)) != len(waypoint_ids):
        raise V1RouteValidationError("Intermediate waypoint IDs cannot repeat")
    if any(item not in allowed for item in waypoint_ids):
        invalid = [item for item in waypoint_ids if item not in allowed]
        raise V1RouteValidationError(f"Unknown or unavailable waypoint IDs: {invalid}")
    if any(item in {origin_id, destination_id} for item in waypoint_ids):
        raise V1RouteValidationError(
            "Intermediate waypoints must differ from the origin and destination"
        )
    if origin_id == destination_id and not waypoint_ids:
        raise V1RouteValidationError(
            "A walking loop requires at least one intermediate waypoint"
        )


def generate_v1_route(
    origin_id: str,
    destination_id: str,
    waypoint_ids: list[str],
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    candidates: dict[str, dict[str, Any]] | None = None,
    max_retries: int = 3,
    backoff_base_s: float = 1.0,
) -> dict[str, Any]:
    """Generate exactly one genuine pedestrian route, without cache persistence.

    The returned object is a GeoJSON FeatureCollection containing route geometry,
    metrics, explicit via-waypoint metadata, and the existing unverified-coordinate
    disclosure. No synthetic geometry or fallback route is produced.
    """
    validate_waypoint_selection(origin_id, destination_id, waypoint_ids)
    candidate_map = candidates if candidates is not None else load_landmark_candidates()

    missing = [item for item in [origin_id, *waypoint_ids, destination_id]
               if item not in candidate_map]
    if missing:
        raise V1RouteValidationError(
            f"Routing coordinates are unavailable for landmark IDs: {missing}"
        )

    origin = candidate_map[origin_id]
    destination = candidate_map[destination_id]
    ordered_waypoints = [candidate_map[item] for item in waypoint_ids]
    ordered_ids = [origin_id, *waypoint_ids, destination_id]

    locations = [
        {"lat": candidate_map[item]["lat"], "lon": candidate_map[item]["lon"]}
        for item in ordered_ids
    ]
    route_id = "jaunt_v1_" + "_".join(ordered_ids)
    payload = {
        "locations": locations,
        "costing": "pedestrian",
        "units": "kilometers",
        "id": route_id,
        "shape_format": "polyline6",
    }

    result, _raw_bytes, http_status = request_json(
        endpoint.rstrip("/") + "/route",
        method="POST",
        payload=payload,
        max_retries=max_retries,
        backoff_base_s=backoff_base_s,
    )
    coordinates, distance_m, duration_s = validate_route_response(
        result,
        locations,
    )

    route_definition = {
        "route_id": route_id,
        "name": "Jaunt walking route",
        "description": "Pedestrian route through the selected intermediate landmarks in order.",
        "locations": locations,
        "waypoints": ordered_waypoints,
    }
    feature_collection = create_geojson_feature_collection(
        route_id=route_id,
        route_def=route_definition,
        coordinates=coordinates,
        distance_m=distance_m,
        duration_s=duration_s,
        origin=origin,
        destination=destination,
        endpoint=endpoint,
        http_status=http_status,
    )
    return feature_collection
