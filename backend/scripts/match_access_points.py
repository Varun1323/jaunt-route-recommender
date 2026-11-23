"""Match and validate pedestrian access points against walkable OSM network features.

Generates candidate routing coordinates by projecting documented physical access
frontages onto nearby walkable, connected, outdoor pedestrian ways.

Design notes
------------
* Only explicitly pedestrian-class ways (footway, pedestrian, path, steps) are
  admitted unconditionally.  Road carriageways (primary, residential, etc.) are
  admitted only when they carry an integrated sidewalk mapped on the carriageway
  itself (sidewalk=yes|both|left|right).  sidewalk=separate means the pedestrian
  way is drawn as a separate OSM element and the carriageway itself should not be
  used as a routing surface.  sidewalk=no or a missing sidewalk tag on a road
  means the road is not walkable here.
* The main outdoor pedestrian component is identified as the largest connected
  component in the filtered graph.  Candidates must belong to this specific
  component, not merely have a component size >= some threshold.
* Underground, tunnel, indoor, and negative-layer ways are excluded at the
  filtering stage and therefore cannot appear in the graph at all.
* All produced coordinates are unverified projections.  review_notes and
  confidence are set accordingly.
"""

from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "backend" / "data"
ACCESS_POINTS_PATH = DATA_DIR / "pedestrian_access_points.json"
PEDESTRIAN_FEATURES_PATH = DATA_DIR / "raw" / "osm" / "pedestrian_network_features.json"
PEDESTRIAN_EXTENSION_PATH = DATA_DIR / "raw" / "osm" / "pedestrian_network_midtown_extension.json"
OUTPUT_CANDIDATES_PATH = DATA_DIR / "pedestrian_access_point_candidates.json"
LANDMARK_CANDIDATES_PATH = DATA_DIR / "landmark_candidates_v2.json"
LANDMARKS_PATH = DATA_DIR / "landmarks.json"

# ---------------------------------------------------------------------------
# Way-classification constants
# ---------------------------------------------------------------------------

# highway values that classify a way as a dedicated pedestrian infrastructure
PEDESTRIAN_HIGHWAY_VALUES: frozenset[str] = frozenset(
    {"footway", "pedestrian", "steps", "path"}
)

# highway values for road carriageways that *may* carry an integrated sidewalk
ROAD_CARRIAGEWAY_VALUES: frozenset[str] = frozenset(
    {"primary", "secondary", "tertiary", "residential", "unclassified", "living_street"}
)

# sidewalk tag values that mean an integrated pavement exists on the carriageway
INTEGRATED_SIDEWALK_VALUES: frozenset[str] = frozenset({"yes", "both", "left", "right"})

# access values that prohibit pedestrian use
EXCLUDED_ACCESS: frozenset[str] = frozenset(
    {"private", "no", "customers", "delivery", "emergency"}
)

# ---------------------------------------------------------------------------
# Reference approach targets — research-derived coordinates indicating the
# general area of the documented physical entrance or frontage.
# These are NOT independently verified GPS coordinates.
# ---------------------------------------------------------------------------
TARGET_APPROACH_LOCATIONS: dict[str, dict[str, Any]] = {
    "grand_central_terminal": {
        "description": (
            "Research-derived reference point near 89 East 42nd Street south portal "
            "(unverified; for projection search only)"
        ),
        "target": (40.752350, -73.977200),
        "frontage_street": "East 42nd Street",
        "max_search_radius_m": 45.0,
    },
    "stephen_a_schwarzman_building": {
        "description": (
            "Research-derived reference point near 10 West 42nd Street accessible entrance "
            "on north facade (unverified; for projection search only)"
        ),
        "target": (40.753750, -73.982200),
        "frontage_street": "West 42nd Street",
        "max_search_radius_m": 40.0,
    },
    "bryant_park": {
        "description": (
            "Research-derived reference point near mid-block West 42nd Street park entrance "
            "(unverified; for projection search only)"
        ),
        "target": (40.754150, -73.983500),
        "frontage_street": "West 42nd Street",
        "max_search_radius_m": 40.0,
    },
    "one_vanderbilt": {
        "description": (
            "Research-derived reference point near 45 East 42nd Street SUMMIT / Transit Hall "
            "entrance (unverified; for projection search only)"
        ),
        "target": (40.752400, -73.978500),
        "frontage_street": "East 42nd Street",
        "max_search_radius_m": 35.0,
    },
    "pershing_square_building": {
        "description": (
            "Research-derived reference point near Pershing Square Plaza along East 42nd Street "
            "(unverified; for projection search only)"
        ),
        "target": (40.751950, -73.977350),
        "frontage_street": "East 42nd Street / Park Avenue",
        "max_search_radius_m": 35.0,
    },
    "chrysler_building": {
        "description": (
            "Research-derived reference point near Lexington Avenue and East 42nd Street corner "
            "(unverified; for projection search only)"
        ),
        "target": (40.751635, -73.975743),
        "frontage_street": "Lexington Avenue / East 42nd Street",
        "max_search_radius_m": 35.0,
    },
    "graybar_building": {
        "description": (
            "Research-derived reference point near 420 Lexington Avenue entrance frontage "
            "(unverified; for projection search only)"
        ),
        "target": (40.752307, -73.975845),
        "frontage_street": "Lexington Avenue",
        "max_search_radius_m": 35.0,
    },
    "chanin_building": {
        "description": (
            "Research-derived reference point near 122 East 42nd Street sidewalk frontage "
            "(unverified; for projection search only)"
        ),
        "target": (40.751386, -73.976965),
        "frontage_street": "East 42nd Street",
        "max_search_radius_m": 35.0,
    },
    "daily_news_building": {
        "description": (
            "Research-derived reference point near 220 East 42nd Street exterior frontage "
            "(unverified; for projection search only)"
        ),
        "target": (40.750100, -73.973400),
        "frontage_street": "East 42nd Street",
        "max_search_radius_m": 35.0,
    },
}

# Confidence thresholds
HIGH_CONFIDENCE_MAX_DIST_M = 20.0  # only if also in main component


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def project_to_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> tuple[float, float, float]:
    """Project point (px, py) onto segment [(ax, ay), (bx, by)] in projected metres.

    Returns (proj_x, proj_y, distance_m).
    """
    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-12:
        dist = math.hypot(px - ax, py - ay)
        return ax, ay, dist
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t_clamped = max(0.0, min(1.0, t))
    proj_x = ax + t_clamped * dx
    proj_y = ay + t_clamped * dy
    dist = math.hypot(px - proj_x, py - proj_y)
    return proj_x, proj_y, dist


def to_projected(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    """Convert (lon, lat) to local Cartesian metres relative to (lon0, lat0)."""
    x = (lon - lon0) * 111_320 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 111_320
    return x, y


def from_projected(x: float, y: float, lon0: float, lat0: float) -> tuple[float, float]:
    """Convert local Cartesian metres back to (lon, lat)."""
    lon = lon0 + x / (111_320 * math.cos(math.radians(lat0)))
    lat = lat0 + y / 111_320
    return lon, lat


def project_to_main_network(
    target_lat: float,
    target_lon: float,
    walkable_ways: list[dict[str, Any]],
    main_component: frozenset[int],
    lon0: float,
    lat0: float,
    max_distance_m: float,
) -> dict[str, Any] | None:
    """Project a sourced place reference onto the connected outdoor pedestrian graph."""
    t_x, t_y = to_projected(target_lon, target_lat, lon0, lat0)
    best_candidate: dict[str, Any] | None = None
    min_projection_distance = float("inf")

    for way in walkable_ways:
        geometry = way.get("geometry", [])
        nodes = way.get("nodes", [])
        if len(geometry) < 2 or len(nodes) < 2:
            continue
        for index in range(len(geometry) - 1):
            point_a, point_b = geometry[index], geometry[index + 1]
            ax, ay = to_projected(point_a["lon"], point_a["lat"], lon0, lat0)
            bx, by = to_projected(point_b["lon"], point_b["lat"], lon0, lat0)
            proj_x, proj_y, distance_m = project_to_segment(t_x, t_y, ax, ay, bx, by)
            if distance_m >= min_projection_distance or distance_m > max_distance_m:
                continue

            u_node, v_node = nodes[index], nodes[index + 1]
            if u_node not in main_component and v_node not in main_component:
                continue

            candidate_lon, candidate_lat = from_projected(proj_x, proj_y, lon0, lat0)
            min_projection_distance = distance_m
            best_candidate = {
                "osm_way_id": way["id"],
                "highway_type": way.get("tags", {}).get("highway"),
                "segment_nodes": [u_node, v_node],
                "candidate_latitude": round(candidate_lat, 6),
                "candidate_longitude": round(candidate_lon, 6),
                "projection_distance_m": round(distance_m, 2),
                "connected_component_size": len(main_component),
                "is_connected_to_main_network": True,
                "confidence": (
                    "high"
                    if distance_m < HIGH_CONFIDENCE_MAX_DIST_M
                    else "medium"
                    if distance_m <= 45.0
                    else "low"
                ),
                "way_tags": {
                    key: value
                    for key, value in way.get("tags", {}).items()
                    if key in {"highway", "footway", "name", "sidewalk", "surface"}
                },
            }
    return best_candidate


# ---------------------------------------------------------------------------
# Walkable-way filter
# ---------------------------------------------------------------------------

def is_walkable_way(way: dict[str, Any]) -> bool:
    """Return True only for outdoor, surface, public pedestrian ways.

    Admission rules (all must pass):

    1. Not underground: tunnel != 'yes' and layer >= 0.
    2. Not indoor: indoor != 'yes'.
    3. Not railway / subway.
    4. Not a transit platform.
    5. Not a closed polygon (area=yes) — those are plaza areas, not linear routes.
    6. Not restricted access.
    7. highway tag present (ways without a highway tag are not routable here).
    8. highway is a pedestrian class (footway, pedestrian, path, steps), OR
       highway is a road carriageway class AND sidewalk tag is an integrated value
       (yes | both | left | right).  sidewalk=separate, sidewalk=no, or a missing
       sidewalk tag on a road all mean the carriageway itself is not walkable.
    """
    tags = way.get("tags", {})

    # --- Level / underground exclusions ---
    if tags.get("tunnel") == "yes":
        return False
    try:
        if int(tags.get("layer", "0")) < 0:
            return False
    except (ValueError, TypeError):
        pass

    # --- Indoor / transit exclusions ---
    if tags.get("indoor") == "yes":
        return False
    if tags.get("railway"):
        return False
    if tags.get("subway") == "yes":
        return False
    if tags.get("public_transport") == "platform":
        return False

    # --- Polygon / area exclusion ---
    if tags.get("area") == "yes":
        return False

    # --- Access / foot restrictions ---
    if tags.get("access") in EXCLUDED_ACCESS:
        return False
    if tags.get("foot") in {"no", "private"}:
        return False

    # --- Highway classification ---
    hw = tags.get("highway")
    if not hw:
        # No highway tag → not a routable carriageway or footway
        return False

    if hw in PEDESTRIAN_HIGHWAY_VALUES:
        return True

    if hw in ROAD_CARRIAGEWAY_VALUES:
        # Admit only when an integrated sidewalk is physically mapped on the road
        return tags.get("sidewalk") in INTEGRATED_SIDEWALK_VALUES

    # All other highway types (service, motorway, trunk, track, etc.) are excluded
    return False


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_graph(ways: list[dict[str, Any]]) -> dict[int, set[int]]:
    """Build undirected adjacency graph from node IDs in walkable ways.

    Each way contributes edges between consecutive node pairs.  Ways that share
    nodes are connected; ways that do not share any node are in separate
    components regardless of geographic proximity.
    """
    graph: dict[int, set[int]] = {}
    for w in ways:
        nodes = w.get("nodes", [])
        for i in range(len(nodes) - 1):
            u, v = nodes[i], nodes[i + 1]
            graph.setdefault(u, set()).add(v)
            graph.setdefault(v, set()).add(u)
    return graph


def get_connected_component(start_node: int, graph: dict[int, set[int]]) -> frozenset[int]:
    """Return the set of all node IDs reachable from start_node (BFS)."""
    if start_node not in graph:
        return frozenset()
    visited: set[int] = {start_node}
    queue: deque[int] = deque([start_node])
    while queue:
        curr = queue.popleft()
        for nb in graph.get(curr, set()):
            if nb not in visited:
                visited.add(nb)
                queue.append(nb)
    return frozenset(visited)


def get_connected_component_size(start_node: int, graph: dict[int, set[int]]) -> int:
    """Return count of nodes reachable from start_node."""
    return len(get_connected_component(start_node, graph))


def find_main_pedestrian_component(graph: dict[int, set[int]]) -> frozenset[int]:
    """Return the node set of the largest connected component in the graph.

    The largest component is taken as the main outdoor public pedestrian
    network for this study area.  After correct filtering (surface, outdoor,
    public ways only) this component represents the interconnected sidewalk and
    footway grid.
    """
    if not graph:
        return frozenset()
    visited_all: set[int] = set()
    best: frozenset[int] = frozenset()
    for node in graph:
        if node in visited_all:
            continue
        comp = get_connected_component(node, graph)
        visited_all |= comp
        if len(comp) > len(best):
            best = comp
    return best


# ---------------------------------------------------------------------------
# Main matching routine
# ---------------------------------------------------------------------------

def match_access_point_candidates() -> dict[str, Any]:
    """Project each landmark's documented approach onto the main pedestrian network."""
    ap_data = json.load(open(ACCESS_POINTS_PATH, encoding="utf-8"))
    ped_data = json.load(open(PEDESTRIAN_FEATURES_PATH, encoding="utf-8"))
    if PEDESTRIAN_EXTENSION_PATH.exists():
        extension = json.load(open(PEDESTRIAN_EXTENSION_PATH, encoding="utf-8"))
        seen_way_ids = {
            item.get("id")
            for item in ped_data.get("elements", [])
            if item.get("type") == "way"
        }
        ped_data["elements"].extend(
            item
            for item in extension.get("elements", [])
            if item.get("type") == "way" and item.get("id") not in seen_way_ids
        )
    landmark_data = json.load(open(LANDMARKS_PATH, encoding="utf-8"))
    collected_data = (
        json.load(open(LANDMARK_CANDIDATES_PATH, encoding="utf-8"))
        if LANDMARK_CANDIDATES_PATH.exists()
        else {"landmarks": []}
    )
    previous_candidates = (
        json.load(open(OUTPUT_CANDIDATES_PATH, encoding="utf-8"))
        if OUTPUT_CANDIDATES_PATH.exists()
        else {"landmarks": []}
    )
    previous_by_id = {
        item.get("landmark_id"): item
        for item in previous_candidates.get("landmarks", [])
        if isinstance(item, dict) and item.get("landmark_id")
    }

    raw_ways = [e for e in ped_data["elements"] if e["type"] == "way"]
    walkable_ways = [w for w in raw_ways if is_walkable_way(w)]
    graph = build_graph(walkable_ways)
    main_component = find_main_pedestrian_component(graph)
    main_component_size = len(main_component)

    lon0, lat0 = -73.978, 40.752

    candidates_output: dict[str, Any] = {
        "schema_version": "1.1",
        "generated_at": "2026-11-20T17:30:00Z",
        "description": (
            "Candidate pedestrian routing coordinates projected onto the main outdoor "
            "connected pedestrian component. All coordinates are unverified projections. "
            "Some additional place references are Nominatim representative points, not "
            "documented entrances."
        ),
        "main_pedestrian_component_size": main_component_size,
        "landmarks": [],
    }

    for ap in ap_data["access_points"]:
        lid = ap["landmark_id"]
        previous = previous_by_id.get(lid)
        if (
            previous
            and previous.get("verification_status") == "candidate_unconfirmed"
            and isinstance(previous.get("routing_candidate"), dict)
        ):
            candidates_output["landmarks"].append(previous)
            continue
        lname = ap["landmark_name"]
        approach_info = TARGET_APPROACH_LOCATIONS.get(lid, {})
        target_lat, target_lon = approach_info.get("target", (ap["latitude"], ap["longitude"]))
        max_dist = approach_info.get("max_search_radius_m", 45.0)
        best_candidate = project_to_main_network(
            target_lat,
            target_lon,
            walkable_ways,
            main_component,
            lon0,
            lat0,
            max_dist,
        )

        # Build review note with accurate language
        if best_candidate is not None:
            dist = best_candidate["projection_distance_m"]
            conf = best_candidate["confidence"]
            review_note = (
                f"Unverified projection candidate. Reference target is a research-derived "
                f"coordinate (not independently verified) for the documented access frontage. "
                f"Projected onto OSM way {best_candidate['osm_way_id']} at {dist} m "
                f"({conf} confidence based on projection distance). "
                f"Candidate is in the main outdoor pedestrian component "
                f"({main_component_size} nodes). "
                f"Coordinate must be confirmed by field observation before use in routing."
            )
        else:
            review_note = (
                "No candidate found within the search radius on the main pedestrian network. "
                "Manual review required to identify a suitable projection point."
            )

        landmark_record: dict[str, Any] = {
            "landmark_id": lid,
            "landmark_name": lname,
            "documented_access": {
                "access_type": ap["access_type"],
                "access_name": ap["access_name"],
                "street_address": ap["street_address"],
                "approach_description": approach_info.get(
                    "description", ap.get("supporting_evidence", "")
                ),
                "reference_target_coordinates": {
                    "latitude": target_lat,
                    "longitude": target_lon,
                    "coordinate_status": "research_derived_unverified",
                },
            },
            "routing_candidate": best_candidate,
            "verification_status": "candidate_unconfirmed",
            "manual_review_required": True,
            "review_notes": review_note,
        }
        candidates_output["landmarks"].append(landmark_record)

    existing_ids = {item["landmark_id"] for item in candidates_output["landmarks"]}
    collected_by_id = {
        item.get("landmark_id"): item
        for item in collected_data.get("landmarks", [])
        if isinstance(item, dict) and item.get("landmark_id")
    }
    for landmark in landmark_data.get("landmarks", []):
        landmark_id = landmark.get("id")
        if not landmark_id or landmark_id in existing_ids:
            continue
        previous = previous_by_id.get(landmark_id)
        previous_candidate = previous.get("routing_candidate", {}) if previous else {}
        if (
            previous
            and previous.get("verification_status") == "candidate_unconfirmed"
            and isinstance(previous_candidate, dict)
            and previous_candidate.get("coordinate_basis", "").startswith("Nominatim result point")
        ):
            candidates_output["landmarks"].append(previous)
            continue
        collected = collected_by_id.get(landmark_id, {})
        references = [
            item
            for item in collected.get("candidates", [])
            if item.get("source_type") == "OpenStreetMap Nominatim"
        ]
        reference = references[0] if len(references) == 1 else None
        coordinates = reference.get("coordinates", {}) if reference else {}
        target_lat = coordinates.get("latitude")
        target_lon = coordinates.get("longitude")
        valid_target = (
            isinstance(target_lat, (int, float))
            and math.isfinite(target_lat)
            and -90 <= target_lat <= 90
            and isinstance(target_lon, (int, float))
            and math.isfinite(target_lon)
            and -180 <= target_lon <= 180
        )
        best_candidate = (
            project_to_main_network(
                float(target_lat),
                float(target_lon),
                walkable_ways,
                main_component,
                lon0,
                lat0,
                50.0,
            )
            if reference and valid_target
            else None
        )

        if best_candidate is not None:
            dist = best_candidate["projection_distance_m"]
            review_note = (
                "Unverified sidewalk projection from an OSM Nominatim place point used only "
                "as a search anchor. The point is not a verified entrance. "
                f"Projected onto OSM way {best_candidate['osm_way_id']} at {dist} m; "
                "manual access review is required."
            )
            status = "candidate_unconfirmed"
        elif len(references) > 1:
            review_note = "Multiple Nominatim place-point candidates; no routing point was selected."
            status = "unresolved_ambiguous_reference"
        elif not reference or not valid_target:
            review_note = "No single valid Nominatim place point is available for projection."
            status = "unresolved_reference_unavailable"
        else:
            best_candidate = {
                "osm_way_id": None,
                "highway_type": None,
                "segment_nodes": [],
                "candidate_latitude": round(float(target_lat), 6),
                "candidate_longitude": round(float(target_lon), 6),
                "projection_distance_m": None,
                "connected_component_size": None,
                "is_connected_to_main_network": False,
                "confidence": "unverified",
                "way_tags": {},
                "coordinate_basis": (
                    "Nominatim result point; entrance and local sidewalk connection not established"
                ),
                "reference_osm_feature": {
                    "osm_type": reference.get("osm_type"),
                    "osm_id": reference.get("osm_id"),
                    "source_url": reference.get("source_url"),
                },
            }
            review_note = (
                "Nominatim place point retained only as an unverified routing candidate because "
                "the local pedestrian graph has no nearby connected segment. It is not a public "
                "entrance; Valhalla must successfully snap it to a valid pedestrian route."
            )
            status = "candidate_unconfirmed"

        candidates_output["landmarks"].append({
            "landmark_id": landmark_id,
            "landmark_name": landmark.get("name", landmark_id),
            "documented_access": {
                "access_type": "unresolved",
                "access_name": None,
                "street_address": None,
                "approach_description": "Nominatim place point is a search anchor only; pedestrian entrance is not established.",
                "reference_target_coordinates": {
                    "latitude": target_lat if valid_target else None,
                    "longitude": target_lon if valid_target else None,
                    "coordinate_status": "nominatim_place_point_unverified" if valid_target else "unresolved",
                },
            },
            "reference_osm_feature": ({
                "osm_type": reference.get("osm_type"),
                "osm_id": reference.get("osm_id"),
                "name": reference.get("name"),
                "tags": reference.get("tags", {}),
                "source_url": reference.get("source_url"),
                "source_type": reference.get("source_type"),
            } if reference else None),
            "routing_candidate": best_candidate,
            "verification_status": status,
            "manual_review_required": True,
            "review_notes": review_note,
        })

    OUTPUT_CANDIDATES_PATH.write_text(
        json.dumps(candidates_output, indent=2), encoding="utf-8"
    )
    return candidates_output


if __name__ == "__main__":
    res = match_access_point_candidates()
    n = len(res["landmarks"])
    main_sz = res["main_pedestrian_component_size"]
    found = sum(1 for lm in res["landmarks"] if lm["routing_candidate"] is not None)
    print(f"Generated candidate routing points for {found}/{n} landmarks.")
    print(f"Main pedestrian component: {main_sz} nodes.")
