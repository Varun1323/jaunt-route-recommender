#!/usr/bin/env python3
"""Collect unverified NYC BUILDING polygon candidates for the two targets."""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
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
RAW_DIR = DATA_DIR / "raw" / "nyc_open_data"
RESULTS_PATH = DATA_DIR / "nyc_building_candidates.json"
DATASET_ID = "5zhs-2jue"
API_URL = f"https://data.cityofnewyork.us/resource/{DATASET_ID}.json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = os.environ.get("JAUNT_USER_AGENT", "JauntRouteRecommender/0.1 (geographic research collector)").strip()
RADIUS_METRES = 200
MAX_ATTEMPTS = 3
SELECT_FIELDS = (
    "the_geom,name,bin,doitt_id,base_bbl,objectid,construction_year,feature_code,"
    "geom_source,ground_elevation,height_roof,last_edited_date,last_status_type,mappluto_bbl,"
    "shape_area,shape_length"
)

TARGETS: dict[str, dict[str, Any]] = {
    "one_vanderbilt": {
        "address": "1 Vanderbilt Avenue, Manhattan, New York City",
        "names": ["1 Vanderbilt", "One Vanderbilt", "One Vanderbilt Avenue", "1 Vanderbilt Avenue"],
        "anchor": None,
        "anchor_source": "Nominatim address lookup (search anchor only)",
        "anchor_source_url": NOMINATIM_URL,
    },
    "stephen_a_schwarzman_building": {
        "address": "476 Fifth Avenue, Manhattan, New York City",
        "names": ["Stephen A. Schwarzman Building", "Schwarzman Building", "New York Public Library", "New York Public Library Main Branch"],
        "anchor": {"latitude": 40.753014, "longitude": -73.9817686},
        "anchor_source": "existing OSM reference point supplied for geographic search only",
        "anchor_source_url": "https://www.openstreetmap.org/node/1634448982",
    },
}


class CollectionError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def request_json(url: str, cache_path: Path, *, max_age_hours: float = 168) -> tuple[Any, dict[str, Any]]:
    if cache_path.exists() and max_age_hours > 0:
        try:
            if (time.time() - cache_path.stat().st_mtime) / 3600 <= max_age_hours:
                return json.loads(cache_path.read_text(encoding="utf-8")), {"cache": "hit", "http_status": None}
        except (OSError, json.JSONDecodeError):
            pass
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=40) as response:
                status, raw = response.status, response.read()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
            tmp.write_bytes(raw)
            tmp.replace(cache_path)
            payload = json.loads(raw)
            return payload, {"cache": "miss", "http_status": status, "retrieved_at": utc_now()}
        except urllib.error.HTTPError as exc:
            detail = exc.read(1200).decode("utf-8", errors="replace")
            last_error = CollectionError(f"HTTP {exc.code}: {detail}")
            retryable = exc.code in {429, 500, 502, 503, 504}
        except urllib.error.URLError as exc:
            last_error = CollectionError(f"network error: {exc.reason}")
            retryable = isinstance(exc.reason, (TimeoutError, socket.timeout))
        except (TimeoutError, socket.timeout) as exc:
            last_error, retryable = CollectionError(f"request timed out: {exc}"), True
        except (OSError, json.JSONDecodeError) as exc:
            last_error, retryable = CollectionError(f"invalid/unavailable response: {exc}"), False
        if not retryable or attempt == MAX_ATTEMPTS:
            break
        time.sleep(2 ** (attempt - 1))
    raise last_error or CollectionError("request failed")


def geocode_address(address: str, cache_path: Path, max_age_hours: float) -> tuple[float, float, dict[str, Any]]:
    query = urllib.parse.urlencode({"q": address, "format": "jsonv2", "limit": 5})
    results, meta = request_json(f"{NOMINATIM_URL}?{query}", cache_path, max_age_hours=max_age_hours)
    if not isinstance(results, list):
        raise CollectionError("address lookup response was not a list")
    matches = [item for item in results if isinstance(item, dict) and item.get("lat") and item.get("lon")]
    if not matches:
        raise CollectionError("address lookup returned no usable search point")
    # Use only an address match; this point is a search center and never a footprint coordinate.
    nyc_matches = [
        item for item in matches
        if "manhattan" in str(item.get("display_name", "")).casefold()
        and "new york" in str(item.get("display_name", "")).casefold()
    ]
    if not nyc_matches:
        raise CollectionError("address lookup returned no result explicitly located in Manhattan, New York")
    preferred = next((item for item in nyc_matches if "vanderbilt avenue" in item.get("display_name", "").casefold()), nyc_matches[0])
    try:
        return float(preferred["lat"]), float(preferred["lon"]), meta
    except (TypeError, ValueError) as exc:
        raise CollectionError(f"address lookup returned invalid coordinates: {exc}") from exc


def spatial_query(latitude: float, longitude: float, radius: int = RADIUS_METRES) -> str:
    params = {
        "$select": SELECT_FIELDS,
        "$where": f"within_circle(the_geom,{latitude:.8f},{longitude:.8f},{radius})",
        "$limit": "500",
    }
    return f"{API_URL}?{urllib.parse.urlencode(params)}"


def polygon_vertices(geometry: Any) -> list[tuple[float, float]]:
    if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        return []
    coords = geometry.get("coordinates")
    points: list[tuple[float, float]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
            points.append((float(value[0]), float(value[1])))
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(coords)
    return points


def footprint_distance_m(geometry: Any, latitude: float, longitude: float) -> float | None:
    points = polygon_vertices(geometry)
    if not points:
        return None
    # Distance to polygon rings, with point-in-ring for zero distance. This is only a
    # candidate-ranking hint; the full returned polygon remains the geographic evidence.
    sx = 111_320 * math.cos(math.radians(latitude))
    sy = 111_320
    target = (longitude * sx, latitude * sy)

    def rings(value: Any):
        if isinstance(value, list) and value and isinstance(value[0], list) and len(value[0]) >= 2 and all(isinstance(v, (int, float)) for v in value[0][:2]):
            yield value
        elif isinstance(value, list):
            for child in value:
                yield from rings(child)

    nearest = float("inf")
    for ring in rings(geometry.get("coordinates")):
        xy = [(float(p[0]) * sx, float(p[1]) * sy) for p in ring if isinstance(p, list) and len(p) >= 2]
        if len(xy) < 3:
            continue
        inside = False
        x, y = target
        for i, (x1, y1) in enumerate(xy):
            x2, y2 = xy[(i + 1) % len(xy)]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-20) + x1:
                inside = not inside
            dx, dy = x2 - x1, y2 - y1
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy or 1e-20)))
            nearest = min(nearest, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
        if inside:
            return 0.0
    return nearest if math.isfinite(nearest) else None


def candidate_score(target: dict[str, Any], row: dict[str, Any], distance_m: float | None) -> tuple[int, list[str]]:
    tags: list[str] = []
    name = str(row.get("name") or "").casefold()
    if name and any(alias.casefold() in name or name in alias.casefold() for alias in target["names"]):
        tags.append("name_alias_support")
    if isinstance(row.get("doitt_id"), (int, str)) and str(row["doitt_id"]).strip():
        tags.append("doitt_id_present")
    if isinstance(row.get("bin"), (int, str)) and str(row["bin"]).strip():
        tags.append("bin_present")
    if row.get("base_bbl"):
        tags.append("base_bbl_present")
    if row.get("objectid") is not None:
        tags.append("objectid_present")
    if row.get("mappluto_bbl"):
        tags.append("mappluto_bbl_present")
    if row.get("construction_year") is not None:
        tags.append("construction_year_present")
    if row.get("geom_source"):
        tags.append("geometry_source_present")
    if row.get("feature_code") == 2100:
        tags.append("feature_code_building")
    score = (100 if "name_alias_support" in tags else 0) + (25 if "feature_code_building" in tags else 0)
    score += sum(5 for key in ("doitt_id_present", "bin_present", "base_bbl_present", "mappluto_bbl_present") if key in tags)
    score += sum(2 for key in ("objectid_present", "construction_year_present", "geometry_source_present") if key in tags)
    if distance_m is not None:
        tags.append("spatially_returned_by_within_circle")
        score += max(0, 20 - int(distance_m / 10))
    return score, tags


def collect(landmark_ids: list[str], max_age_hours: float = 168) -> dict[str, Any]:
    unknown = set(landmark_ids) - set(TARGETS)
    if unknown:
        raise ValueError(f"unknown NYC building target(s): {', '.join(sorted(unknown))}")
    if not USER_AGENT:
        raise ValueError("configure JAUNT_USER_AGENT before making requests")
    previous: dict[str, Any] = {}
    if RESULTS_PATH.exists():
        try:
            previous = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
    previous_records = {item.get("landmark_id"): item for item in previous.get("landmarks", []) if isinstance(item, dict)}
    records = dict(previous_records)
    errors: list[dict[str, Any]] = []
    for landmark_id in landmark_ids:
        target = TARGETS[landmark_id]
        anchor = target["anchor"]
        anchor_meta = {"source": target["anchor_source"], "source_url": target["anchor_source_url"]}
        if anchor is None:
            try:
                lat, lon, geocode_meta = geocode_address(
                    target["address"], RAW_DIR / f"nominatim_{landmark_id}.json", max_age_hours
                )
                anchor = {"latitude": lat, "longitude": lon}
                anchor_meta.update(geocode_meta)
            except CollectionError as exc:
                errors.append({"landmark_id": landmark_id, "stage": "address_lookup", "error": str(exc)})
                records[landmark_id] = unresolved_record(landmark_id, target, str(exc), None)
                continue
        url = spatial_query(anchor["latitude"], anchor["longitude"])
        cache_path = RAW_DIR / f"building_{landmark_id}.json"
        try:
            rows, request_meta = request_json(url, cache_path, max_age_hours=max_age_hours)
            if not isinstance(rows, list):
                raise CollectionError("NYC BUILDING API response was not a JSON row array")
            candidates = []
            malformed_geometry = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                geometry = row.get("the_geom")
                if not polygon_vertices(geometry):
                    malformed_geometry += 1
                    continue
                distance = footprint_distance_m(geometry, anchor["latitude"], anchor["longitude"])
                score, evidence = candidate_score(target, row, distance)
                candidates.append({
                    "source_dataset": "NYC Open Data BUILDING",
                    "source_dataset_id": DATASET_ID,
                    "source_url": f"https://data.cityofnewyork.us/City-Government/BUILDING/{DATASET_ID}",
                    "source_type": "NYC Open Data Socrata API",
                    "retrieved_at": request_meta.get("retrieved_at"),
                    "record_id": row.get("doitt_id") or row.get("objectid"),
                    "identifiers": {key: row.get(key) for key in ("doitt_id", "bin", "base_bbl", "objectid", "mappluto_bbl")},
                    "attributes": row,
                    "geometry": geometry,
                    "geometry_type": geometry["type"],
                    "search_distance_m": distance,
                    "match_score": score,
                    "match_evidence": evidence,
                    "verification_status": "candidate_unconfirmed",
                    "verified": False,
                })
            if rows and not candidates and malformed_geometry:
                raise CollectionError("NYC BUILDING API returned rows without usable polygon geometry")
            records[landmark_id] = {
                "landmark_id": landmark_id,
                "landmark_name": target["names"][0],
                "target_address": target["address"],
                "search_anchor": anchor,
                "search_anchor_provenance": anchor_meta,
                "search_radius_m": RADIUS_METRES,
                "candidate_count": len(candidates),
                "collection_status": "candidate_unconfirmed" if candidates else "unresolved_no_matches",
                "verified": False,
                "candidates": sorted(candidates, key=lambda item: (-item["match_score"], item["search_distance_m"] or float("inf"))),
                "request": {"url": url, **request_meta, "cache_file": str(cache_path.relative_to(DATA_DIR))},
                "error": None,
            }
        except CollectionError as exc:
            errors.append({"landmark_id": landmark_id, "stage": "building_query", "error": str(exc)})
            records[landmark_id] = unresolved_record(landmark_id, target, str(exc), anchor)
    output = {
        "schema_version": "1.0",
        "collected_at": utc_now(),
        "provider": "NYC Open Data",
        "dataset": {"name": "BUILDING", "id": DATASET_ID, "url": f"https://data.cityofnewyork.us/City-Government/BUILDING/{DATASET_ID}"},
        "verification_policy": "NYC building footprints are candidates only; manual identity review is required. No NYC identifier is an OSM identifier.",
        "landmarks": list(records.values()),
        "errors": errors,
    }
    RESULTS_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def unresolved_record(landmark_id: str, target: dict[str, Any], error: str, anchor: dict[str, float] | None) -> dict[str, Any]:
    return {
        "landmark_id": landmark_id, "landmark_name": target["names"][0], "target_address": target["address"],
        "search_anchor": anchor, "candidate_count": 0, "collection_status": "unresolved_request_failed",
        "verified": False, "candidates": [], "error": error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--landmarks", required=True, help="Comma-separated: one_vanderbilt,stephen_a_schwarzman_building")
    parser.add_argument("--cache-hours", type=float, default=168, help="Successful response cache age; use 0 to refresh")
    args = parser.parse_args()
    try:
        ids = [part.strip() for part in args.landmarks.split(",") if part.strip()]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("provide one or more unique landmark IDs")
        result = collect(ids, args.cache_hours)
    except (OSError, json.JSONDecodeError, ValueError, CollectionError) as exc:
        print(f"NYC building collection failed: {exc}")
        return 1
    result_by_id = {item["landmark_id"]: item for item in result["landmarks"]}
    for landmark_id in ids:
        item = result_by_id.get(landmark_id)
        if item:
            print(f"{item['landmark_id']}: {item['collection_status']}; {item['candidate_count']} candidate footprint(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
