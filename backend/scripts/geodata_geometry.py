"""Small dependency-free polygon utilities for comparing stored provider geometry."""

from __future__ import annotations

import math
from typing import Any


def as_polygons(geometry: Any, *, osm_way: bool = False) -> list[list[tuple[float, float]]]:
    """Normalize GeoJSON Polygon/MultiPolygon or OSM way geometry to lon/lat rings."""
    if osm_way:
        if not isinstance(geometry, list):
            return []
        try:
            return [[(float(point["lon"]), float(point["lat"])) for point in geometry]]
        except (KeyError, TypeError, ValueError):
            return []
    if not isinstance(geometry, dict):
        return []
    kind, coordinates = geometry.get("type"), geometry.get("coordinates")
    if kind == "Polygon":
        raw_polygons = [coordinates]
    elif kind == "MultiPolygon":
        raw_polygons = coordinates
    else:
        return []
    polygons: list[list[tuple[float, float]]] = []
    for polygon in raw_polygons or []:
        if not isinstance(polygon, list) or not polygon:
            continue
        try:
            # First ring is the exterior; keep inner rings out of this footprint overlap test.
            polygons.append([(float(point[0]), float(point[1])) for point in polygon[0]])
        except (IndexError, TypeError, ValueError):
            continue
    return polygons


def _project(ring: list[tuple[float, float]], lon0: float, lat0: float) -> list[tuple[float, float]]:
    x_scale = 111_320 * math.cos(math.radians(lat0))
    return [((lon - lon0) * x_scale, (lat - lat0) * 111_320) for lon, lat in ring]


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
    return min(a[0], b[0]) - 1e-8 <= p[0] <= max(a[0], b[0]) + 1e-8 and min(a[1], b[1]) - 1e-8 <= p[1] <= max(a[1], b[1]) + 1e-8


def _segments_intersect(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    o1, o2, o3, o4 = _orientation(a, b, c), _orientation(a, b, d), _orientation(c, d, a), _orientation(c, d, b)
    if ((o1 > 0 > o2) or (o2 > 0 > o1)) and ((o3 > 0 > o4) or (o4 > 0 > o3)):
        return True
    return (
        (abs(o1) < 1e-8 and _on_segment(a, b, c))
        or (abs(o2) < 1e-8 and _on_segment(a, b, d))
        or (abs(o3) < 1e-8 and _on_segment(c, d, a))
        or (abs(o4) < 1e-8 and _on_segment(c, d, b))
    )


def _point_in_ring(point: tuple[float, float], ring: list[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for i, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(i + 1) % len(ring)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-30) + x1:
            inside = not inside
    return inside


def polygons_overlap(first: Any, second: Any, *, first_is_osm_way: bool = False, second_is_osm_way: bool = False) -> bool:
    """Return whether any exterior polygon regions overlap (including boundary touch)."""
    a = as_polygons(first, osm_way=first_is_osm_way)
    b = as_polygons(second, osm_way=second_is_osm_way)
    points = [point for polygon in a + b for point in polygon]
    if not points:
        return False
    lon0 = sum(point[0] for point in points) / len(points)
    lat0 = sum(point[1] for point in points) / len(points)
    a = [_project(ring, lon0, lat0) for ring in a if len(ring) >= 3]
    b = [_project(ring, lon0, lat0) for ring in b if len(ring) >= 3]
    for ring_a in a:
        for ring_b in b:
            if _point_in_ring(ring_a[0], ring_b) or _point_in_ring(ring_b[0], ring_a):
                return True
            for i, p1 in enumerate(ring_a):
                p2 = ring_a[(i + 1) % len(ring_a)]
                for j, q1 in enumerate(ring_b):
                    q2 = ring_b[(j + 1) % len(ring_b)]
                    if _segments_intersect(p1, p2, q1, q2):
                        return True
    return False


def geometry_bounds(geometry: Any, *, osm_way: bool = False) -> tuple[float, float, float, float] | None:
    """Return geographic bounds as (min_lon, min_lat, max_lon, max_lat)."""
    rings = as_polygons(geometry, osm_way=osm_way)
    points = [point for ring in rings for point in ring]
    if not points:
        return None
    lons, lats = zip(*points)
    return min(lons), min(lats), max(lons), max(lats)


def _signed_area(ring: list[tuple[float, float]]) -> float:
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1])) / 2


def _is_convex(ring: list[tuple[float, float]]) -> bool:
    signs = []
    for i in range(len(ring)):
        cross = _orientation(ring[i], ring[(i + 1) % len(ring)], ring[(i + 2) % len(ring)])
        if abs(cross) > 1e-10:
            signs.append(cross > 0)
    return bool(signs) and all(sign == signs[0] for sign in signs)


def _line_intersection(p1: tuple[float, float], p2: tuple[float, float], q1: tuple[float, float], q2: tuple[float, float]) -> tuple[float, float]:
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    sx, sy = q2[0] - q1[0], q2[1] - q1[1]
    denom = rx * sy - ry * sx
    if abs(denom) < 1e-15:
        return p2
    t = ((q1[0] - p1[0]) * sy - (q1[1] - p1[1]) * sx) / denom
    return p1[0] + t * rx, p1[1] + t * ry


def _convex_clip(subject: list[tuple[float, float]], clip: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Clip any simple subject ring to a convex clip ring (Sutherland–Hodgman)."""
    if not _is_convex(clip):
        raise ValueError("polygon_iou needs at least one convex exterior ring")
    output = subject[:]
    orientation = 1 if _signed_area(clip) >= 0 else -1
    for i, edge_start in enumerate(clip):
        edge_end = clip[(i + 1) % len(clip)]
        source, output = output, []
        if not source:
            break
        previous = source[-1]
        prev_inside = orientation * _orientation(edge_start, edge_end, previous) >= -1e-9
        for current in source:
            cur_inside = orientation * _orientation(edge_start, edge_end, current) >= -1e-9
            if cur_inside:
                if not prev_inside:
                    output.append(_line_intersection(previous, current, edge_start, edge_end))
                output.append(current)
            elif prev_inside:
                output.append(_line_intersection(previous, current, edge_start, edge_end))
            previous, prev_inside = current, cur_inside
    return output


def _area(ring: list[tuple[float, float]]) -> float:
    return abs(_signed_area(ring)) if len(ring) >= 3 else 0.0


def _cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_in_triangle(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], orientation: float) -> bool:
    eps = 1e-9
    return (
        orientation * _cross(a, b, point) >= -eps
        and orientation * _cross(b, c, point) >= -eps
        and orientation * _cross(c, a, point) >= -eps
    )


def _triangulate(ring: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Ear-clip a simple polygon ring into non-overlapping convex triangles."""
    points = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring[:]
    if len(points) < 3 or abs(_signed_area(points)) < 1e-9:
        raise ValueError("cannot triangulate a degenerate polygon ring")
    orientation = 1.0 if _signed_area(points) > 0 else -1.0
    indexes = list(range(len(points)))
    triangles: list[list[tuple[float, float]]] = []
    while len(indexes) > 3:
        ear_found = False
        for pos, current in enumerate(indexes):
            prev = indexes[(pos - 1) % len(indexes)]
            nxt = indexes[(pos + 1) % len(indexes)]
            a, b, c = points[prev], points[current], points[nxt]
            if orientation * _cross(a, b, c) <= 1e-9:
                continue
            if any(
                _point_in_triangle(points[other], a, b, c, orientation)
                for other in indexes if other not in {prev, current, nxt}
            ):
                continue
            triangles.append([a, b, c])
            del indexes[pos]
            ear_found = True
            break
        if not ear_found:
            raise ValueError("polygon ring is not a simple triangulable exterior")
    triangles.append([points[index] for index in indexes])
    return triangles


def polygon_iou(first: Any, second: Any, *, first_is_osm_way: bool = False, second_is_osm_way: bool = False) -> float:
    """Approximate planar IoU for local simple building footprints, including concave rings."""
    a, b = as_polygons(first, osm_way=first_is_osm_way), as_polygons(second, osm_way=second_is_osm_way)
    points = [point for ring in a + b for point in ring]
    if not points:
        return 0.0
    lon0, lat0 = (sum(point[i] for point in points) / len(points) for i in (0, 1))
    a = [_project(ring, lon0, lat0) for ring in a if len(ring) >= 3]
    b = [_project(ring, lon0, lat0) for ring in b if len(ring) >= 3]
    triangles_a = [triangle for ring in a for triangle in _triangulate(ring)]
    triangles_b = [triangle for ring in b for triangle in _triangulate(ring)]
    area_a = sum(_area(triangle) for triangle in triangles_a)
    area_b = sum(_area(triangle) for triangle in triangles_b)
    intersection = 0.0
    for triangle_a in triangles_a:
        for triangle_b in triangles_b:
            intersection += _area(_convex_clip(triangle_a, triangle_b))
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _point_segment_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy or 1e-30)))
    return math.hypot(point[0] - start[0] - t * dx, point[1] - start[1] - t * dy)


def _densify(ring: list[tuple[float, float]], max_spacing_m: float) -> list[tuple[float, float]]:
    result = []
    for start, end in zip(ring, ring[1:] + ring[:1]):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        count = max(1, math.ceil(length / max_spacing_m))
        result.extend((start[0] + (end[0] - start[0]) * i / count, start[1] + (end[1] - start[1]) * i / count) for i in range(count))
    return result


def boundary_difference(first: Any, second: Any, *, first_is_osm_way: bool = False, second_is_osm_way: bool = False, sample_spacing_m: float = 1.0) -> dict[str, float]:
    """Approximate symmetric boundary distance using one-metre densified samples."""
    a, b = as_polygons(first, osm_way=first_is_osm_way), as_polygons(second, osm_way=second_is_osm_way)
    points = [point for ring in a + b for point in ring]
    if not points:
        return {"max_m": math.inf, "mean_m": math.inf}
    lon0, lat0 = (sum(point[i] for point in points) / len(points) for i in (0, 1))
    a = [_project(ring, lon0, lat0) for ring in a if len(ring) >= 3]
    b = [_project(ring, lon0, lat0) for ring in b if len(ring) >= 3]

    def directed(source: list[list[tuple[float, float]]], target: list[list[tuple[float, float]]]) -> list[float]:
        target_edges = [(ring[i], ring[(i + 1) % len(ring)]) for ring in target for i in range(len(ring))]
        samples = [point for ring in source for point in _densify(ring, sample_spacing_m)]
        return [min(_point_segment_distance(point, start, end) for start, end in target_edges) for point in samples]

    distances = directed(a, b) + directed(b, a)
    return {"max_m": max(distances, default=math.inf), "mean_m": sum(distances) / len(distances) if distances else math.inf}


def validate_osm_way(element: Any) -> dict[str, Any]:
    """Check closed-ring completeness and simple-polygon validity for an OSM way."""
    issues: list[str] = []
    if not isinstance(element, dict) or element.get("type") != "way":
        return {"complete": False, "valid": False, "issues": ["record is not an OSM way"]}
    nodes, geometry = element.get("nodes"), element.get("geometry")
    if not isinstance(nodes, list) or not isinstance(geometry, list) or len(nodes) != len(geometry):
        issues.append("nodes and full geometry are missing or have different lengths")
    if not isinstance(nodes, list) or len(nodes) < 4 or nodes[0] != nodes[-1]:
        issues.append("OSM way node ring is not closed")
    ring = as_polygons(geometry, osm_way=True)
    if not ring or len(set(ring[0][:-1])) < 3 or ring[0][0] != ring[0][-1]:
        issues.append("geometry ring is not a closed polygon with at least three unique vertices")
    if ring and len(ring[0]) >= 4:
        vertices = ring[0]
        n = len(vertices) - 1
        for i in range(n):
            for j in range(i + 1, n):
                if j == i + 1 or (i == 0 and j == n - 1):
                    continue
                if _segments_intersect(vertices[i], vertices[i + 1], vertices[j], vertices[j + 1]):
                    issues.append("geometry ring self-intersects")
                    break
            if issues and issues[-1] == "geometry ring self-intersects":
                break
        if abs(_signed_area(ring[0])) < 1e-14:
            issues.append("geometry ring has zero area")
    complete = not any("missing" in issue or "node ring" in issue or "geometry ring is not" in issue for issue in issues)
    return {"complete": complete, "valid": not issues, "issues": issues}
