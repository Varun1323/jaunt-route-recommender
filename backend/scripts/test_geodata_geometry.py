"""Offline geometry overlap checks for OSM and NYC building polygons."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("geodata_geometry", Path(__file__).with_name("geodata_geometry.py"))
geometry = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(geometry)


class GeometryComparisonTests(unittest.TestCase):
    def test_overlapping_polygons_are_detected_across_geojson_and_osm_formats(self) -> None:
        city = {"type": "Polygon", "coordinates": [[[-73.0, 40.0], [-72.999, 40.0], [-72.999, 40.001], [-73.0, 40.001], [-73.0, 40.0]]]}
        osm = [
            {"lon": -72.9995, "lat": 39.9995}, {"lon": -72.9985, "lat": 40.0005},
            {"lon": -72.9995, "lat": 40.0015}, {"lon": -73.0005, "lat": 40.0005},
            {"lon": -72.9995, "lat": 39.9995},
        ]
        self.assertTrue(geometry.polygons_overlap(osm, city, first_is_osm_way=True))

    def test_disjoint_provider_polygons_are_detected(self) -> None:
        a = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
        b = {"type": "Polygon", "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]]}
        self.assertFalse(geometry.polygons_overlap(a, b))

    def test_iou_and_boundary_difference_for_shifted_squares(self) -> None:
        first = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
        second = {"type": "Polygon", "coordinates": [[[0.5, 0], [1.5, 0], [1.5, 1], [0.5, 1], [0.5, 0]]]}
        self.assertAlmostEqual(geometry.polygon_iou(first, second), 1 / 3, places=6)
        delta = geometry.boundary_difference(first, second)
        self.assertGreater(delta["max_m"], 0)
        self.assertGreater(delta["mean_m"], 0)

    def test_iou_supports_concave_footprints(self) -> None:
        concave = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2], [0, 0]]]}
        square = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
        self.assertAlmostEqual(geometry.polygon_iou(concave, square), 0.75, places=6)

    def test_cached_nypl_way_overlaps_selected_city_footprint(self) -> None:
        nyc_data = json.loads((ROOT / "backend/data/nyc_building_candidates.json").read_text(encoding="utf-8"))
        nyc_record = next(item for item in nyc_data["landmarks"] if item["landmark_id"] == "stephen_a_schwarzman_building")
        nyc_candidate = next(item for item in nyc_record["candidates"] if item["identifiers"]["doitt_id"] == "556382")
        osm_data = json.loads((ROOT / "backend/data/raw/osm/overpass_landmark_stephen_a_schwarzman_building.json").read_text(encoding="utf-8"))
        osm_way = next(item for item in osm_data["elements"] if item.get("type") == "way" and item.get("id") == 265302076)
        self.assertTrue(geometry.polygons_overlap(osm_way["geometry"], nyc_candidate["geometry"], first_is_osm_way=True))
        self.assertEqual(nyc_candidate["identifiers"]["doitt_id"], "556382")
        self.assertEqual(osm_way["id"], 265302076)
        self.assertEqual(nyc_candidate["verification_status"], "candidate_unconfirmed")
        self.assertFalse(nyc_candidate["verified"])

    def test_stored_one_vanderbilt_sources_overlap_with_high_iou_and_complete_osm_way(self) -> None:
        nyc_data = json.loads((ROOT / "backend/data/nyc_building_candidates.json").read_text(encoding="utf-8"))
        record = next(item for item in nyc_data["landmarks"] if item["landmark_id"] == "one_vanderbilt")
        candidate = next(item for item in record["candidates"] if item["identifiers"]["doitt_id"] == "1284912")
        direct = json.loads((ROOT / "backend/data/raw/osm/direct_way_265875648.json").read_text(encoding="utf-8-sig"))
        way = next(item for item in direct["elements"] if item.get("type") == "way" and item.get("id") == 265875648)
        self.assertEqual(geometry.validate_osm_way(way), {"complete": True, "valid": True, "issues": []})
        self.assertTrue(geometry.polygons_overlap(way["geometry"], candidate["geometry"], first_is_osm_way=True))
        self.assertAlmostEqual(geometry.polygon_iou(way["geometry"], candidate["geometry"], first_is_osm_way=True), 0.9569, delta=0.001)
        boundary = geometry.boundary_difference(way["geometry"], candidate["geometry"], first_is_osm_way=True)
        self.assertLess(boundary["max_m"], 4)
        self.assertLess(boundary["mean_m"], 1)
        self.assertEqual(geometry.geometry_bounds(way["geometry"], osm_way=True), (-73.9790293, 40.7526087, -73.9780314, 40.7533487))
        self.assertEqual(geometry.geometry_bounds(candidate["geometry"]), (-73.979066084266, 40.752588379527, -73.978033346173, 40.753357974013))
        self.assertEqual(candidate["identifiers"]["doitt_id"], "1284912")
        self.assertEqual(candidate["identifiers"]["bin"], "1090825")
        self.assertEqual(way["tags"]["nycdoitt:bin"], "1035349")
        self.assertEqual(way["tags"]["name"], "One Vanderbilt")
        self.assertFalse(candidate["verified"])

    def test_stored_grand_central_sources_overlap_and_reproduce_metrics(self) -> None:
        osm_direct = json.loads((ROOT / "backend/data/raw/osm/direct_way_265947358.json").read_text(encoding="utf-8"))
        way = next(item for item in osm_direct["elements"] if item.get("type") == "way" and item.get("id") == 265947358)
        nyc_raw = json.loads((ROOT / "backend/data/raw/nyc_open_data/building_grand_central_terminal.json").read_text(encoding="utf-8"))
        city_building = next(item for item in nyc_raw if item.get("doitt_id") == "49507")

        validation = geometry.validate_osm_way(way)
        self.assertTrue(validation["complete"])
        self.assertTrue(validation["valid"])
        self.assertTrue(geometry.polygons_overlap(way["geometry"], city_building["the_geom"], first_is_osm_way=True))

        iou = geometry.polygon_iou(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(iou, 0.624459, places=5)

        boundary = geometry.boundary_difference(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(boundary["mean_m"], 12.03, places=1)
        self.assertAlmostEqual(boundary["max_m"], 24.99, places=1)

        self.assertEqual(way["tags"]["nycdoitt:bin"], "1035381")
        self.assertEqual(city_building["bin"], "1035381")
        self.assertEqual(city_building["doitt_id"], "49507")

    def test_stored_pershing_square_sources_overlap_and_reproduce_metrics(self) -> None:
        osm_direct = json.loads((ROOT / "backend/data/raw/osm/direct_way_265947370.json").read_text(encoding="utf-8"))
        way = next(item for item in osm_direct["elements"] if item.get("type") == "way" and item.get("id") == 265947370)
        nyc_raw = json.loads((ROOT / "backend/data/raw/nyc_open_data/building_pershing_square_building.json").read_text(encoding="utf-8"))
        city_building = next(item for item in nyc_raw if item.get("doitt_id") == "563691")

        validation = geometry.validate_osm_way(way)
        self.assertTrue(validation["complete"])
        self.assertTrue(validation["valid"])
        self.assertTrue(geometry.polygons_overlap(way["geometry"], city_building["the_geom"], first_is_osm_way=True))

        iou = geometry.polygon_iou(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(iou, 0.945406, places=5)

        boundary = geometry.boundary_difference(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(boundary["mean_m"], 0.66, places=2)
        self.assertAlmostEqual(boundary["max_m"], 1.00, places=2)

        self.assertEqual(way["tags"]["nycdoitt:bin"], "1036151")
        self.assertEqual(city_building["bin"], "1036151")
        self.assertEqual(city_building["doitt_id"], "563691")

    def test_stored_chrysler_building_sources_overlap_and_reproduce_metrics(self) -> None:
        osm_direct = json.loads((ROOT / "backend/data/raw/osm/direct_way_42500770.json").read_text(encoding="utf-8"))
        way = next(item for item in osm_direct["elements"] if item.get("type") == "way" and item.get("id") == 42500770)
        nyc_raw = json.loads((ROOT / "backend/data/raw/nyc_open_data/building_chrysler_building.json").read_text(encoding="utf-8"))
        city_building = next(item for item in nyc_raw if item.get("doitt_id") == "464793")

        validation = geometry.validate_osm_way(way)
        self.assertTrue(validation["complete"])
        self.assertTrue(validation["valid"])
        self.assertTrue(geometry.polygons_overlap(way["geometry"], city_building["the_geom"], first_is_osm_way=True))

        iou = geometry.polygon_iou(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(iou, 0.870962, places=5)

        boundary = geometry.boundary_difference(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(boundary["mean_m"], 2.05, places=2)
        self.assertAlmostEqual(boundary["max_m"], 4.18, places=2)

        self.assertEqual(way["tags"]["nycdoitt:bin"], "1036156")
        self.assertEqual(city_building["bin"], "1036156")
        self.assertEqual(city_building["doitt_id"], "464793")

    def test_stored_graybar_building_sources_overlap_and_reproduce_metrics(self) -> None:
        osm_direct = json.loads((ROOT / "backend/data/raw/osm/direct_way_265947361.json").read_text(encoding="utf-8"))
        way = next(item for item in osm_direct["elements"] if item.get("type") == "way" and item.get("id") == 265947361)
        nyc_raw = json.loads((ROOT / "backend/data/raw/nyc_open_data/building_graybar_building.json").read_text(encoding="utf-8"))
        city_building = next(item for item in nyc_raw if item.get("doitt_id") == "231162")

        validation = geometry.validate_osm_way(way)
        self.assertTrue(validation["complete"])
        self.assertTrue(validation["valid"])
        self.assertTrue(geometry.polygons_overlap(way["geometry"], city_building["the_geom"], first_is_osm_way=True))

        iou = geometry.polygon_iou(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(iou, 0.967821, places=5)

        boundary = geometry.boundary_difference(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(boundary["mean_m"], 0.65, places=2)
        self.assertAlmostEqual(boundary["max_m"], 1.14, places=2)

        self.assertEqual(way["tags"]["nycdoitt:bin"], "1035385")
        self.assertEqual(city_building["bin"], "1035385")
        self.assertEqual(city_building["doitt_id"], "231162")

    def test_stored_chanin_building_sources_overlap_and_reproduce_metrics(self) -> None:
        osm_direct = json.loads((ROOT / "backend/data/raw/osm/direct_way_265947366.json").read_text(encoding="utf-8"))
        way = next(item for item in osm_direct["elements"] if item.get("type") == "way" and item.get("id") == 265947366)
        nyc_raw = json.loads((ROOT / "backend/data/raw/nyc_open_data/building_chanin_building.json").read_text(encoding="utf-8"))
        city_building = next(item for item in nyc_raw if item.get("doitt_id") == "534814")

        validation = geometry.validate_osm_way(way)
        self.assertTrue(validation["complete"])
        self.assertTrue(validation["valid"])
        self.assertTrue(geometry.polygons_overlap(way["geometry"], city_building["the_geom"], first_is_osm_way=True))

        iou = geometry.polygon_iou(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(iou, 0.936971, places=5)

        boundary = geometry.boundary_difference(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(boundary["mean_m"], 0.61, places=2)
        self.assertAlmostEqual(boundary["max_m"], 0.93, places=2)

        self.assertEqual(way["tags"]["nycdoitt:bin"], "1036152")
        self.assertEqual(city_building["bin"], "1036152")
        self.assertEqual(city_building["doitt_id"], "534814")

    def test_stored_daily_news_building_sources_overlap_and_reproduce_metrics(self) -> None:
        osm_direct = json.loads((ROOT / "backend/data/raw/osm/direct_way_42500778.json").read_text(encoding="utf-8"))
        way = next(item for item in osm_direct["elements"] if item.get("type") == "way" and item.get("id") == 42500778)
        nyc_raw = json.loads((ROOT / "backend/data/raw/nyc_open_data/building_daily_news_building.json").read_text(encoding="utf-8"))
        city_building = next(item for item in nyc_raw if item.get("doitt_id") == "800556")

        validation = geometry.validate_osm_way(way)
        self.assertTrue(validation["complete"])
        self.assertTrue(validation["valid"])
        self.assertTrue(geometry.polygons_overlap(way["geometry"], city_building["the_geom"], first_is_osm_way=True))

        iou = geometry.polygon_iou(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(iou, 0.939474, places=5)

        boundary = geometry.boundary_difference(way["geometry"], city_building["the_geom"], first_is_osm_way=True)
        self.assertAlmostEqual(boundary["mean_m"], 1.13, places=2)
        self.assertAlmostEqual(boundary["max_m"], 2.16, places=2)

        self.assertEqual(city_building["bin"], "1037546")
        self.assertEqual(city_building["doitt_id"], "800556")
        self.assertEqual(city_building["name"], "Daily News Building & TV Studios")

    def test_stored_bryant_park_boundary_is_complete_and_valid(self) -> None:
        osm_direct = json.loads((ROOT / "backend/data/raw/osm/direct_way_22727025.json").read_text(encoding="utf-8"))
        way = next(item for item in osm_direct["elements"] if item.get("type") == "way" and item.get("id") == 22727025)

        validation = geometry.validate_osm_way(way)
        self.assertTrue(validation["complete"])
        self.assertTrue(validation["valid"])
        self.assertEqual(way["tags"]["leisure"], "park")
        self.assertEqual(way["tags"]["name"], "Bryant Park")
        self.assertEqual(way["tags"]["operator"], "Bryant Park Corporation")

        bounds = geometry.geometry_bounds(way["geometry"], osm_way=True)
        self.assertIsNotNone(bounds)
        min_lon, min_lat, max_lon, max_lat = bounds
        self.assertAlmostEqual(min_lon, -73.984804, places=5)
        self.assertAlmostEqual(max_lon, -73.982273, places=5)
        self.assertAlmostEqual(min_lat, 40.752846, places=5)
        self.assertAlmostEqual(max_lat, 40.754655, places=5)

    def test_unclosed_osm_geometry_is_not_complete_or_valid(self) -> None:
        way = {"type": "way", "nodes": [1, 2, 3], "geometry": [
            {"lon": 0, "lat": 0}, {"lon": 1, "lat": 0}, {"lon": 1, "lat": 1},
        ]}
        result = geometry.validate_osm_way(way)
        self.assertFalse(result["complete"])
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main()
