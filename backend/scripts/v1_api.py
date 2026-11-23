
"""FastAPI V1 endpoint for Jaunt's single-route recommendation flow."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from fetch_routes import RouteFetchError
from openrouter_client import OpenRouterClient, get_openrouter_config
from v1_preference_service import (
    V1PreferenceError,
    interpret_v1_preference,
)
from v1_route_service import (
    V1RouteValidationError,
    generate_v1_route,
)

logger = logging.getLogger(__name__)

router = APIRouter()

BACKEND_DIR = Path(__file__).resolve().parents[1]
LANDMARKS_PATH = BACKEND_DIR / "data" / "landmarks.json"


class V1RecommendRequest(BaseModel):
    """Request body for POST /api/v1/recommend."""

    origin_id: str = Field(min_length=1)
    destination_id: str = Field(min_length=1)
    preference: str = Field(default="", max_length=500)


def _load_selectable_landmarks() -> list[dict[str, Any]]:
    """Load the approved selectable landmark catalogue."""
    try:
        data = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        logger.exception("Jaunt landmark catalogue is missing.")
        raise HTTPException(
            status_code=500,
            detail="Landmark catalogue is unavailable.",
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Unable to read Jaunt landmark catalogue.")
        raise HTTPException(
            status_code=500,
            detail="Landmark catalogue could not be read.",
        ) from exc

    landmarks = data.get("landmarks")
    if not isinstance(landmarks, list):
        raise HTTPException(
            status_code=500,
            detail="Landmark catalogue has an invalid structure.",
        )

    return [
        landmark
        for landmark in landmarks
        if isinstance(landmark, dict)
        and landmark.get("prototype_selection") is True
    ]


@router.post("/api/v1/recommend")
def post_v1_recommend(body: V1RecommendRequest) -> dict[str, Any]:
    """Interpret a preference and generate exactly one on-demand walking route."""

    landmarks = _load_selectable_landmarks()
    selectable_ids = {
        item["id"]
        for item in landmarks
        if isinstance(item.get("id"), str)
    }

    if body.origin_id not in selectable_ids:
        raise HTTPException(
            status_code=422,
            detail="Origin must be one of the eight selectable landmarks.",
        )

    if body.destination_id not in selectable_ids:
        raise HTTPException(
            status_code=422,
            detail="Destination must be one of the eight selectable landmarks.",
        )

    config = get_openrouter_config()
    if not config.is_configured:
        raise HTTPException(
            status_code=503,
            detail="Preference interpretation is unavailable because the AI provider is not configured.",
        )

    client = OpenRouterClient(config)

    try:
        preference = interpret_v1_preference(
            body.preference,
            origin_id=body.origin_id,
            destination_id=body.destination_id,
            available_landmarks=landmarks,
            client=client,
        )
    except V1PreferenceError as exc:
        logger.warning("Jaunt V1 preference interpretation failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="The AI provider could not produce a valid route preference plan.",
        ) from exc

    try:
        route = generate_v1_route(
            body.origin_id,
            body.destination_id,
            preference.waypoint_ids,
        )
    except V1RouteValidationError as exc:
        logger.warning("Jaunt V1 route validation failed: %s", exc)
        raise HTTPException(
            status_code=422,
            detail="The selected route contains invalid or unavailable landmarks.",
        ) from exc
    except (RouteFetchError, OSError, ValueError) as exc:
        logger.exception("Jaunt V1 pedestrian route generation failed.")
        raise HTTPException(
            status_code=502,
            detail="The pedestrian routing service could not generate a valid route.",
        ) from exc

    features = route.get("features")
    if not isinstance(features, list) or len(features) != 1:
        logger.error("V1 route service returned an unexpected GeoJSON structure.")
        raise HTTPException(
            status_code=502,
            detail="The routing service returned an invalid route.",
        )

    properties = features[0].get("properties")
    if not isinstance(properties, dict):
        logger.error("V1 route service returned missing route properties.")
        raise HTTPException(
            status_code=502,
            detail="The routing service returned incomplete route information.",
        )

    return {
        "route": route,
        "route_summary": {
            "route_id": properties.get("route_id"),
            "origin_id": body.origin_id,
            "destination_id": body.destination_id,
            "distance_m": properties.get("distance_m"),
            "estimated_duration_s": properties.get("estimated_duration_s"),
            "estimated_duration_minutes": round(
                float(properties.get("estimated_duration_s", 0)) / 60.0,
                1,
            ),
            "via_waypoints": properties.get("via_waypoints", []),
            "costing": properties.get("costing"),
            "verification_status": properties.get("verification_status"),
            "coordinate_limitation_disclosure": properties.get(
                "coordinate_limitation_disclosure"
            ),
        },
        "preference": preference.to_dict(),
        "ai_status": {
            "provider": "openrouter",
            "model": config.model,
            "status": "active",
        },
        "route_count": 1,
    }
