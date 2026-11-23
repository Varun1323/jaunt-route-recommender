#!/usr/bin/env python3
"""Collect selective OSM feature candidates without approving landmark matches.

Uses only the Python standard library. Raw Overpass JSON is cached under
backend/data/raw/osm/ and the versioned candidate report is separate from the
original broad-search evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
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
LANDMARKS_PATH = DATA_DIR / "landmarks.json"
RAW_DIR = DATA_DIR / "raw" / "osm"
RESULTS_PATH = DATA_DIR / "landmark_candidates_v2.json"
PEDESTRIAN_NETWORK_PATH = RAW_DIR / "pedestrian_network_midtown_extension.json"
DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def configured_overpass_url() -> str:
    """Resolve the endpoint when collection starts so PowerShell overrides apply."""
    return os.environ.get("JAUNT_OVERPASS_URL", DEFAULT_OVERPASS_URL).strip() or DEFAULT_OVERPASS_URL


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = os.environ.get(
    "JAUNT_USER_AGENT", "JauntRouteRecommender/0.1 (geographic research collector)"
)
USER_AGENT = USER_AGENT.strip()
OVERPASS_DELAY_SECONDS = 2.0
OVERPASS_MAX_ATTEMPTS = 3
OVERPASS_RETRY_BASE_SECONDS = 2.0
NOMINATIM_DELAY_SECONDS = 1.1
AREA_BBOX = (40.746, -73.986, 40.762, -73.970)  # Overpass search window, not landmark coordinates

# Known OSM name forms are query hints only. They do not establish object identity.
LANDMARK_SPECS: dict[str, dict[str, Any]] = {
    "grand_central_terminal": {
        "names": ["Grand Central Terminal"],
        "feature_kind": "building",
        "required_tags": {"building": "*"},
    },
    "pershing_square_building": {
        "names": ["Pershing Square Building"],
        "feature_kind": "building",
        "required_tags": {"building": "*"},
    },
    "chrysler_building": {
        "names": ["Chrysler Building"],
        "feature_kind": "building",
        "required_tags": {"building": "*"},
    },
    "graybar_building": {
        "names": ["Graybar Building"],
        "feature_kind": "building",
        "required_tags": {"building": "*"},
    },
    "chanin_building": {
        "names": ["Chanin Building", "122 East 42nd Street"],
        "feature_kind": "building",
        "required_tags": {"building": "*"},
    },
    "daily_news_building": {
        "names": ["Daily News Building", "The News Building"],
        "feature_kind": "building",
        "required_tags": {"building": "*"},
    },
    "one_vanderbilt": {
        "names": ["One Vanderbilt", "1 Vanderbilt", "One Vanderbilt Avenue", "1 Vanderbilt Avenue"],
        "feature_kind": "building",
        "required_tags": {"building": "*"},
    },
    "stephen_a_schwarzman_building": {
        "names": ["Stephen A. Schwarzman Building", "Schwarzman Building", "New York Public Library", "New York Public Library Main Branch"],
        "feature_kind": "building",
        "required_tags": {"building": "*"},
    },
    "bryant_park": {
        "names": ["Bryant Park"],
        "feature_kind": "park",
        "required_tags": {"leisure": "park"},
    },
    "times_square": {
        "names": ["Times Square"],
        "feature_kind": "place",
        "required_tags": {"place": "square"},
        "nominatim_types": [("place", "square")],
    },
    "rockefeller_center": {
        "names": ["Rockefeller Center", "Rockefeller Centre"],
        "feature_kind": "place",
        "required_tags": {"landuse": "commercial"},
        "nominatim_types": [("landuse", "commercial")],
    },
    "st_patricks_cathedral": {
        "names": [
            "St. Patrick's Cathedral",
            "Saint Patrick's Cathedral",
            "St Patrick's Cathedral",
        ],
        "feature_kind": "building",
        "required_tags": {"building": "*"},
        "nominatim_types": [("amenity", "place_of_worship")],
    },
    "museum_of_modern_art": {
        "names": ["Museum of Modern Art", "The Museum of Modern Art", "MoMA"],
        "feature_kind": "building",
        "required_tags": {"building": "*"},
        "nominatim_types": [("tourism", "museum")],
    },
}

PARK_TAGS = ("leisure", "landuse", "boundary", "tourism", "place")
EXCLUDED_TRANSPORT_KEYS = {"public_transport", "railway", "railway:signal", "railway:ref"}


class CollectionError(RuntimeError):
    """A request could not produce trustworthy collection evidence."""


class RetryableCollectionError(CollectionError):
    """Transient Overpass failure eligible for bounded retry."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
    cache_path: Path,
    max_age_hours: float,
    min_interval: float,
) -> tuple[Any, dict[str, Any]]:
    """Fetch JSON, using fresh disk cache and atomic raw-response persistence."""
    if cache_path.exists() and max_age_hours > 0:
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
        if age_hours <= max_age_hours:
            try:
                raw = cache_path.read_bytes()
                parsed = json.loads(raw)
                return parsed, {"cache": "hit", "retrieved_at": None, "http_status": None}
            except (OSError, json.JSONDecodeError):
                # Corrupt cache is ignored; the network response must replace it.
                pass

    delay_file = RAW_DIR / ".last_request"
    if delay_file.exists():
        try:
            elapsed = time.time() - float(delay_file.read_text(encoding="utf-8"))
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
        except (OSError, ValueError):
            pass

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    retrieved_at = utc_now()
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        delay_file.write_text(str(time.time()), encoding="utf-8")
        with urllib.request.urlopen(req, timeout=35) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(2000).decode("utf-8", errors="replace")
        if exc.code in {502, 503, 504}:
            raise RetryableCollectionError(f"HTTP {exc.code}: {detail}") from exc
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After", "unspecified")
            raise CollectionError(f"HTTP 429 rate limited; Retry-After={retry_after}; {detail}") from exc
        raise CollectionError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise RetryableCollectionError(f"request timed out for {url}") from exc
        raise CollectionError(f"network/DNS failure for {urllib.parse.urlsplit(url).netloc}: {exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RetryableCollectionError(f"request timed out for {url}") from exc

    # Keep the exact unmodified response body for audit/reproduction.
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp_path.write_bytes(raw)
    tmp_path.replace(cache_path)
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError(f"service returned non-JSON response; raw body saved at {cache_path}") from exc
    return parsed, {"cache": "miss", "retrieved_at": retrieved_at, "http_status": status}


def landmark_batches(landmarks: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    if batch_size != 1:
        raise ValueError("each Overpass request must contain exactly one landmark; batch_size must be 1")
    return [landmarks[index : index + batch_size] for index in range(0, len(landmarks), batch_size)]


def overpass_query(landmarks: list[dict[str, Any]] | None = None) -> str:
    """Build deterministic, feature-type-scoped queries for the required targets."""
    landmarks = landmarks or list(json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))["landmarks"])
    south, west, north, east = AREA_BBOX
    bbox = f"({south},{west},{north},{east})"
    clauses: list[str] = []
    for landmark in landmarks:
        spec = LANDMARK_SPECS[landmark["id"]]
        alternatives = "|".join(re.escape(name) for name in spec["names"])
        name_selectors = [f'["{key}"~"{alternatives}",i]' for key in ("name", "official_name", "alt_name")]
        if spec["feature_kind"] == "building":
            # Only named building ways/relations: exclude nodes including transit stop objects.
            for selector in name_selectors:
                clauses.append(f'way{selector}["building"]{bbox};')
                clauses.append(f'relation{selector}["building"]{bbox};')
        elif spec["feature_kind"] == "park":
            # Search park polygons by expected feature tags, not by all nearby names.
            for key in PARK_TAGS:
                for selector in name_selectors:
                    clauses.append(f'way{selector}["{key}"]{bbox};')
                    clauses.append(f'relation{selector}["{key}"]{bbox};')
        else:
            for key in spec["required_tags"]:
                for selector in name_selectors:
                    clauses.append(f'way{selector}["{key}"]{bbox};')
                    clauses.append(f'relation{selector}["{key}"]{bbox};')
    output = "out body center geom;" if any(
        landmark["id"] == "one_vanderbilt" for landmark in landmarks
    ) else "out center tags;"
    return "[out:json][timeout:40];\n(\n  " + "\n  ".join(clauses) + f"\n);\n{output}"


def point_for_element(element: dict[str, Any]) -> tuple[float, float] | None:
    if element.get("type") == "node" and "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center")
    if center and "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def is_excluded_transport(tags: dict[str, Any]) -> bool:
    """Reject transit/rail objects even if an unexpected query response includes one."""
    if any(key in tags for key in EXCLUDED_TRANSPORT_KEYS):
        return True
    if tags.get("highway") in {"bus_stop", "platform"}:
        return True
    return False


def match_score(landmark: dict[str, Any], tags: dict[str, Any]) -> tuple[int, str]:
    """Rank candidate name evidence after feature-type filtering; never confirm it."""
    spec = LANDMARK_SPECS.get(landmark["id"])
    if not spec or is_excluded_transport(tags):
        return 0, "excluded_or_unknown_feature"
    if spec["feature_kind"] == "building":
        if "building" not in tags:
            return 0, "not_a_building_feature"
    elif spec["feature_kind"] == "park":
        # Bryant Park must have park-compatible tagging; a building named after the park is rejected.
        if not any(key in tags for key in PARK_TAGS):
            return 0, "not_a_park_feature"
        if tags.get("leisure") not in {"park", "garden", "nature_reserve"} and not (
            tags.get("landuse") in {"grass", "recreation_ground", "village_green"}
            or tags.get("boundary") == "protected_area"
            or tags.get("tourism") == "attraction"
        ):
            return 0, "park_tags_not_relevant"
    else:
        for key, expected_value in spec["required_tags"].items():
            if expected_value != "*" and tags.get(key) != expected_value:
                return 0, "feature_tags_not_relevant"
            if expected_value == "*" and not tags.get(key):
                return 0, "feature_tags_not_relevant"

    candidate_names = {
        norm_name(str(tags.get(key, "")))
        for key in ("name", "official_name", "short_name", "alt_name", "old_name")
        if tags.get(key)
    }
    expected = {norm_name(value) for value in spec["names"]}
    exact = candidate_names & expected
    if exact:
        # Exact name affects ranking only. Feature identity still requires manual review.
        return 100, "exact_configured_name_and_expected_feature_type"
    return 0, "name_not_in_configured_aliases"


def deduplicate_representations(candidates: dict[tuple[str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse same-name way/relation representations close together, retaining all IDs."""
    kept: list[dict[str, Any]] = []
    for candidate in sorted(candidates.values(), key=lambda item: (item["osm_type"] != "relation", item["osm_id"])):
        tags = candidate.get("tags", {})
        identity_name = norm_name(str(tags.get("name") or candidate.get("name") or ""))
        lat = candidate["coordinates"]["latitude"]
        lon = candidate["coordinates"]["longitude"]
        duplicate = None
        if not identity_name:
            candidate["osm_representations"] = [{"osm_type": candidate["osm_type"], "osm_id": candidate["osm_id"]}]
            kept.append(candidate)
            continue
        for existing in kept:
            old_name = norm_name(str(existing.get("tags", {}).get("name") or existing.get("name") or ""))
            if identity_name != old_name or candidate["osm_type"] == existing["osm_type"]:
                continue
            old_lat = existing["coordinates"]["latitude"]
            old_lon = existing["coordinates"]["longitude"]
            # Local equirectangular distance is only used to identify duplicate OSM representations,
            # never for routing or as a coordinate verification claim.
            dx = (lon - old_lon) * 111_320 * math.cos(math.radians((lat + old_lat) / 2))
            dy = (lat - old_lat) * 111_320
            if math.hypot(dx, dy) <= 15:
                duplicate = existing
                break
        if duplicate is None:
            candidate["osm_representations"] = [{"osm_type": candidate["osm_type"], "osm_id": candidate["osm_id"]}]
            kept.append(candidate)
        else:
            duplicate.setdefault("osm_representations", [{"osm_type": duplicate["osm_type"], "osm_id": duplicate["osm_id"]}])
            duplicate["osm_representations"].append({"osm_type": candidate["osm_type"], "osm_id": candidate["osm_id"]})
    return kept


def address_from_tags(tags: dict[str, Any]) -> str | None:
    number = tags.get("addr:housenumber")
    street = tags.get("addr:street")
    city = tags.get("addr:city")
    postcode = tags.get("addr:postcode")
    parts = [" ".join(str(part) for part in (number, street) if part)]
    if city:
        parts.append(str(city))
    if postcode:
        parts.append(str(postcode))
    return ", ".join(part for part in parts if part) or None


def run_collection(
    use_nominatim: bool,
    max_age_hours: float,
    batch_size: int = 1,
    landmark_ids: list[str] | None = None,
    nominatim_only: bool = False,
) -> dict[str, Any]:
    source = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))
    all_landmarks = source["landmarks"]
    by_id = {item["id"]: item for item in all_landmarks}
    requested_ids = set(landmark_ids) if landmark_ids is not None else set(by_id)
    unknown_ids = requested_ids - set(by_id)
    if unknown_ids:
        raise ValueError(f"unknown landmark IDs: {', '.join(sorted(unknown_ids))}")
    overpass_url = configured_overpass_url()

    # Preserve existing successful candidate records and do not request them again.
    previous_output: dict[str, Any] = {}
    if RESULTS_PATH.exists():
        try:
            previous_output = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous_output = {}
    previous_records = {
        item.get("landmark_id"): item
        for item in previous_output.get("landmarks", [])
        if isinstance(item, dict) and item.get("landmark_id")
    }
    protected_ids = {
        landmark_id
        for landmark_id, record in previous_records.items()
        if record.get("candidate_count", 0) > 0
        and record.get("verification_status") == "candidate_unconfirmed"
    }
    collection_ids = requested_ids - protected_ids
    landmarks = [item for item in all_landmarks if item["id"] in collection_ids]
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    batches = [] if nominatim_only else landmark_batches(landmarks, batch_size)
    candidates_by_id: dict[str, dict[tuple[str, int], dict[str, Any]]] = {
        item["id"]: {} for item in landmarks
    }
    batch_results: list[dict[str, Any]] = []
    failed_landmark_ids: set[str] = set()
    for batch_number, batch in enumerate(batches, start=1):
        query_text = overpass_query(batch)
        landmark_id = batch[0]["id"]
        raw_path = RAW_DIR / f"overpass_landmark_{landmark_id}.json"
        batch_error: CollectionError | None = None
        payload = None
        req_meta: dict[str, Any] = {}
        attempts = 0
        for attempt in range(1, OVERPASS_MAX_ATTEMPTS + 1):
            attempts = attempt
            try:
                payload, req_meta = request_json(
                    overpass_url,
                    method="POST",
                    body=urllib.parse.urlencode({"data": query_text}).encode("utf-8"),
                    content_type="application/x-www-form-urlencoded; charset=utf-8",
                    cache_path=raw_path,
                    max_age_hours=max_age_hours,
                    min_interval=OVERPASS_DELAY_SECONDS,
                )
                if not isinstance(payload, dict) or not isinstance(payload.get("elements", []), list):
                    raise CollectionError("Overpass response is not a JSON object with an elements array")
                batch_error = None
                break
            except RetryableCollectionError as exc:
                batch_error = exc
                if attempt < OVERPASS_MAX_ATTEMPTS:
                    time.sleep(OVERPASS_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
            except CollectionError as exc:
                # Permanent HTTP and service errors are recorded without retry.
                batch_error = exc
                break

        if batch_error is not None:
            exc = batch_error
            failed_landmark_ids.update(item["id"] for item in batch)
            batch_results.append({
                "batch": batch_number,
                "landmark_ids": [item["id"] for item in batch],
                "status": "failed",
                "error": str(exc),
                "attempts": attempts,
                "query": query_text,
                "cache_file": str(raw_path.relative_to(DATA_DIR)),
                "candidate_count": 0,
            })
            continue

        batch_candidate_count = 0
        for element in payload["elements"]:
            tags = element.get("tags") or {}
            if not isinstance(tags, dict) or is_excluded_transport(tags):
                continue
            point = point_for_element(element)
            if point is None:
                continue
            osm_type, osm_id = element.get("type"), element.get("id")
            if osm_type not in {"way", "relation"} or not isinstance(osm_id, int):
                continue
            for landmark in batch:
                score, basis = match_score(landmark, tags)
                if not score:
                    continue
                lat, lon = point
                candidate = {
                    "osm_type": osm_type,
                    "osm_id": osm_id,
                    "name": tags.get("name"),
                    "coordinates": {"latitude": lat, "longitude": lon},
                    "coordinate_basis": "OSM node coordinate" if osm_type == "node" else "OSM way/relation representative center; not an entrance",
                    "address": address_from_tags(tags),
                    "tags": tags,
                    "geometry": element.get("geometry"),
                    "geometry_status": "full_osm_geometry_returned" if element.get("geometry") else "geometry_not_returned",
                    "match_score": score,
                    "match_basis": basis,
                    "source_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
                    "source_type": "OpenStreetMap via Overpass API",
                    "verification_status": "candidate_unconfirmed",
                    "verified": False,
                }
                candidates_by_id[landmark["id"]][(osm_type, osm_id)] = candidate
                batch_candidate_count += 1
        batch_results.append({
            "batch": batch_number,
            "landmark_ids": [item["id"] for item in batch],
            "status": "success",
            "request_metadata": req_meta,
            "attempts": attempts,
            "query": query_text,
            "cache_file": str(raw_path.relative_to(DATA_DIR)),
            "candidate_count": batch_candidate_count,
        })

    nominatim_errors: dict[str, str] = {}
    if use_nominatim:
        for index, landmark in enumerate(landmarks):
            params = urllib.parse.urlencode(
                {"format": "jsonv2", "addressdetails": 1, "limit": 5, "q": landmark["name"] + ", Manhattan, New York"}
            )
            npath = RAW_DIR / f"nominatim_{safe_name(landmark['id'])}.json"
            try:
                results, nmeta = request_json(
                    f"{NOMINATIM_URL}?{params}",
                    cache_path=npath,
                    max_age_hours=max_age_hours,
                    min_interval=NOMINATIM_DELAY_SECONDS,
                )
                if not isinstance(results, list):
                    continue
                for result in results:
                    try:
                        lat, lon = float(result["lat"]), float(result["lon"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if result.get("osm_type") not in {"node", "way", "relation"}:
                        continue
                    osm_key = (str(result.get("osm_type")), int(result.get("osm_id")))
                    # Nominatim is supplemental evidence only; do not insert transport or non-feature results.
                    result_tags = result.get("extratags") or {}
                    result_class = result.get("category") or result.get("class")
                    if result_class in {"railway", "public_transport", "highway"} or is_excluded_transport(result_tags):
                        continue
                    expected_nominatim_types = LANDMARK_SPECS[landmark["id"]].get("nominatim_types")
                    if expected_nominatim_types and (
                        result_class, result.get("type")
                    ) not in expected_nominatim_types:
                        continue
                    # Nominatim result fields may omit the full feature tags. Require configured-name
                    # evidence and a compatible category before retaining it as supplemental candidate.
                    result_name = result.get("name") or ""
                    alias_set = {norm_name(name) for name in LANDMARK_SPECS[landmark["id"]]["names"]}
                    if norm_name(str(result_name)) not in alias_set:
                        continue
                    if landmark["id"] == "bryant_park":
                        if result_class not in {"leisure", "landuse", "boundary", "tourism"}:
                            continue
                        result_tags = {**result_tags, "_nominatim_category": result_class, "_nominatim_type": result.get("type")}
                    elif result_class not in {"building", "historic", "tourism", "amenity", "place", "landuse"}:
                        continue
                    candidates_by_id[landmark["id"]].setdefault(
                        osm_key,
                        {
                            "osm_type": result.get("osm_type"),
                            "osm_id": result.get("osm_id"),
                            "name": result.get("name") or result.get("display_name"),
                            "coordinates": {"latitude": lat, "longitude": lon},
                            "coordinate_basis": "Nominatim result point; entrance not established",
                            "address": result.get("display_name"),
                            "tags": result_tags,
                            "match_score": None,
                            "match_basis": "Nominatim text search result; manual identity check required",
                            "source_url": f"https://www.openstreetmap.org/{result.get('osm_type')}/{result.get('osm_id')}",
                            "source_type": "OpenStreetMap Nominatim",
                            "retrieved_at": nmeta.get("retrieved_at"),
                            "verification_status": "candidate_unconfirmed",
                            "verified": False,
                        },
                    )
            except CollectionError as exc:
                nominatim_errors[landmark["id"]] = str(exc)
            # Enforce a delay between Nominatim requests, including cached iteration.
            if index < len(landmarks) - 1:
                time.sleep(NOMINATIM_DELAY_SECONDS)

    collected_records: dict[str, dict[str, Any]] = {}
    for landmark in landmarks:
        deduped = deduplicate_representations(candidates_by_id[landmark["id"]])
        if deduped:
            record_status = "candidate_unconfirmed"
        elif landmark["id"] in failed_landmark_ids:
            record_status = "unresolved_batch_failed"
        else:
            record_status = "unresolved_no_matches"
        collected_records[landmark["id"]] = {
                "landmark_id": landmark["id"],
                "landmark_name": landmark["name"],
                "candidate_count": len(deduped),
                "candidate_tag_summary": [
                    {"osm_type": candidate["osm_type"], "osm_id": candidate["osm_id"], "name": candidate.get("name"), "tags": candidate["tags"]}
                    for candidate in deduped
                ],
                "manual_review_required": True,
                "verification_status": record_status,
                "unresolved": record_status.startswith("unresolved_"),
                "verified": False,
                "candidates": sorted(deduped, key=lambda item: (-(item["match_score"] or 0), item["osm_type"], item["osm_id"])),
                "nominatim_error": nominatim_errors.get(landmark["id"]),
            }

    # A targeted run updates only its selected records; all other prior records are retained.
    records = []
    for landmark in all_landmarks:
        landmark_id = landmark["id"]
        if landmark_id in collected_records:
            records.append(collected_records[landmark_id])
        elif landmark_id in previous_records:
            records.append(previous_records[landmark_id])
        else:
            records.append({
                "landmark_id": landmark_id,
                "landmark_name": landmark["name"],
                "candidate_count": 0,
                "candidate_tag_summary": [],
                "manual_review_required": True,
                "verification_status": "not_collected",
                "unresolved": True,
                "verified": False,
                "candidates": [],
                "nominatim_error": None,
            })
    preserved_batches = [
        item for item in previous_output.get("overpass_batches", [])
        if not (set(item.get("landmark_ids", [])) & collection_ids)
    ]
    all_batch_results = preserved_batches + batch_results
    output = {
        "schema_version": "2.0",
        "collected_at": utc_now(),
        "verification_policy": "All external matches are candidates only; manual confirmation is required before landmarks.json can be updated.",
        "provider": "OpenStreetMap",
        "candidate_summary": [
            {
                "landmark_id": record["landmark_id"],
                "candidate_count": record["candidate_count"],
                "relevant_tags": record["candidate_tag_summary"],
                "status": record["verification_status"],
            }
            for record in records
        ],
        "request_metadata": {
            "overpass_endpoint": overpass_url,
            "batch_size": batch_size,
            "nominatim_enabled": use_nominatim,
            "nominatim_only": nominatim_only,
        },
        "overpass_batches": all_batch_results,
        "requested_landmark_ids": [item["id"] for item in all_landmarks if item["id"] in requested_ids],
        "skipped_existing_success_ids": [item["id"] for item in all_landmarks if item["id"] in (requested_ids & protected_ids)],
        "landmarks": records,
    }
    RESULTS_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def collect_pedestrian_network_extension(max_age_hours: float = 168.0) -> dict[str, Any]:
    """Collect a bounded walkable-network extension around the eight selected places."""
    catalog = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))
    selected_ids = {
        item["id"]
        for item in catalog.get("landmarks", [])
        if item.get("prototype_selection") is True
    }
    if len(selected_ids) != 8:
        raise CollectionError(
            f"Expected exactly 8 selected landmark records; found {len(selected_ids)}."
        )
    candidate_data = json.loads(
        (DATA_DIR / "pedestrian_access_point_candidates.json").read_text(encoding="utf-8")
    )
    existing_points = {
        item.get("landmark_id"): item.get("routing_candidate")
        for item in candidate_data.get("landmarks", [])
        if isinstance(item, dict)
    }
    collected_records = {
        item.get("landmark_id"): item
        for item in json.loads(RESULTS_PATH.read_text(encoding="utf-8")).get("landmarks", [])
        if isinstance(item, dict)
    } if RESULTS_PATH.exists() else {}
    points: list[tuple[float, float]] = []
    for landmark_id in selected_ids:
        candidate = existing_points.get(landmark_id)
        if candidate:
            lat, lon = candidate.get("candidate_latitude"), candidate.get("candidate_longitude")
        else:
            record = collected_records.get(landmark_id, {})
            matches = [
                item for item in record.get("candidates", [])
                if item.get("source_type") == "OpenStreetMap Nominatim"
            ]
            if len(matches) != 1:
                continue
            coords = matches[0].get("coordinates", {})
            lat, lon = coords.get("latitude"), coords.get("longitude")
        if (
            isinstance(lat, (int, float))
            and math.isfinite(lat)
            and isinstance(lon, (int, float))
            and math.isfinite(lon)
        ):
            points.append((float(lat), float(lon)))
    if not points:
        raise CollectionError("No selected-landmark coordinates are available for the network query.")

    south = min(lat for lat, _ in points) - 0.001
    west = min(lon for _, lon in points) - 0.001
    north = max(lat for lat, _ in points) + 0.001
    east = max(lon for _, lon in points) + 0.001
    bbox = f"({south:.6f},{west:.6f},{north:.6f},{east:.6f})"
    query = (
        "[out:json][timeout:60];\n"
        f'way["highway"]{bbox};\n'
        "out body geom;"
    )
    payload, metadata = request_json(
        configured_overpass_url(),
        method="POST",
        body=urllib.parse.urlencode({"data": query}).encode("utf-8"),
        content_type="application/x-www-form-urlencoded; charset=utf-8",
        cache_path=PEDESTRIAN_NETWORK_PATH,
        max_age_hours=max_age_hours,
        min_interval=OVERPASS_DELAY_SECONDS,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise CollectionError("Overpass network response is not a JSON object with an elements array.")
    return {
        "elements": payload["elements"],
        "bbox": bbox,
        "request_metadata": metadata,
        "cache_path": str(PEDESTRIAN_NETWORK_PATH),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nominatim", action="store_true", help="Also make one rate-limited Nominatim search per landmark")
    parser.add_argument("--cache-hours", type=float, default=168.0, help="Fresh cache lifetime; use 0 to force retrieval")
    parser.add_argument("--batch-size", type=int, choices=(1,), default=1, help="Exactly one landmark per Overpass request")
    parser.add_argument("--landmarks", help="Comma-separated landmark IDs to collect; successful prior records are preserved")
    parser.add_argument("--nominatim-only", action="store_true", help="Use cached/rate-limited Nominatim lookups without Overpass requests")
    parser.add_argument("--pedestrian-network", action="store_true", help="Collect a bounded OSM walkable-network extension for selected landmarks")
    args = parser.parse_args()
    if not USER_AGENT or "python-urllib" in USER_AGENT.lower():
        print("Refusing request: configure a descriptive JAUNT_USER_AGENT", file=sys.stderr)
        return 2
    try:
        selected_ids = None
        if args.landmarks:
            selected_ids = [part.strip() for part in args.landmarks.split(",") if part.strip()]
            if not selected_ids:
                raise ValueError("--landmarks must include at least one landmark ID")
            if len(selected_ids) != len(set(selected_ids)):
                raise ValueError("--landmarks contains duplicate IDs")
        if args.nominatim_only and not args.nominatim:
            raise ValueError("--nominatim-only requires --nominatim")
        if args.pedestrian_network:
            result = collect_pedestrian_network_extension(args.cache_hours)
            print(
                f"Saved {len(result['elements'])} OSM network ways; "
                f"bbox={result['bbox']}; cache={result['cache_path']}"
            )
            return 0
        result = run_collection(
            args.nominatim, args.cache_hours, args.batch_size, selected_ids,
            nominatim_only=args.nominatim_only,
        )
    except (OSError, json.JSONDecodeError, KeyError, ValueError, CollectionError) as exc:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        error_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        error_path = RAW_DIR / f"collection_error_{error_stamp}_{os.getpid()}.json"
        suffix = 1
        while error_path.exists():
            error_path = RAW_DIR / f"collection_error_{error_stamp}_{os.getpid()}_{suffix}.json"
            suffix += 1
        error_path.write_text(json.dumps({"failed_at": utc_now(), "error": str(exc)}, indent=2) + "\n", encoding="utf-8")
        print(f"Collection failed: {exc}. Error details saved to {error_path}", file=sys.stderr)
        return 1
    matched = sum(record["candidate_count"] > 0 for record in result["landmarks"])
    print(f"Saved v2 records for {len(result['landmarks'])} landmarks; {matched} have unconfirmed candidates.")
    for record in result["landmarks"]:
        tags = "; ".join(
            json.dumps(
                {
                    "osm_type": item["osm_type"],
                    "osm_id": item["osm_id"],
                    "name": item.get("name"),
                    "tags": item["tags"],
                },
                ensure_ascii=True,
            )
            for item in record["candidate_tag_summary"]
        ) or "no matching OSM feature returned"
        print(f"- {record['landmark_id']}: {record['verification_status']}; {record['candidate_count']} candidate(s); {tags}")
    failures = sum(batch["status"] == "failed" for batch in result["overpass_batches"])
    print(f"Overpass batches: {len(result['overpass_batches'])}; failed: {failures}; endpoint: {result['request_metadata']['overpass_endpoint']}")
    print(f"Results: {RESULTS_PATH}")
    print(f"Raw responses/cache: {RAW_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
