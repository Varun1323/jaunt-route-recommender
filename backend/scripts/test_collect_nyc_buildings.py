"""Offline tests for NYC BUILDING footprint collection."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("collect_nyc_buildings.py")
SPEC = importlib.util.spec_from_file_location("collect_nyc_buildings", SCRIPT)
collector = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(collector)


class NYCBuildingCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data = root / "data"
        self.raw = self.data / "raw" / "nyc_open_data"
        self.output = self.data / "nyc_building_candidates.json"
        self.landmarks = self.data / "landmarks.json"
        self.data.mkdir(parents=True)
        self.landmarks.write_text(json.dumps({"landmarks": []}), encoding="utf-8")

    def collect_mocked(self, ids, response):
        with (
            mock.patch.object(collector, "DATA_DIR", self.data),
            mock.patch.object(collector, "RAW_DIR", self.raw),
            mock.patch.object(collector, "RESULTS_PATH", self.output),
            mock.patch.object(collector, "LANDMARKS_PATH", self.landmarks),
            mock.patch.object(collector, "request_json", side_effect=response),
            mock.patch.object(collector.time, "sleep"),
        ):
            return collector.collect(ids, max_age_hours=0)

    def test_geographic_queries_select_full_polygon_and_identifier_fields(self) -> None:
        url = collector.spatial_query(40.753014, -73.9817686)
        from urllib.parse import parse_qs, urlsplit
        params = parse_qs(urlsplit(url).query)
        self.assertIn("the_geom", params["$select"][0])
        self.assertIn("doitt_id", params["$select"][0])
        self.assertEqual(params["$where"], ["within_circle(the_geom,40.75301400,-73.98176860,200)"])
        self.assertIn("$limit", params)

    def test_nypl_candidate_preserves_full_geometry_and_nyc_identifiers(self) -> None:
        geometry = {"type": "MultiPolygon", "coordinates": [[[[ -73.9818, 40.7530], [-73.9817, 40.7530], [-73.9817, 40.7531], [-73.9818, 40.7530]]]]}
        row = {"the_geom": geometry, "name": "New York Public Library", "bin": 1000000, "doitt_id": 12345,
               "base_bbl": "1000010001", "objectid": 88, "feature_code": 2100, "geom_source": "Photogrammetric"}

        def response(url, cache_path, **kwargs):
            from urllib.parse import parse_qs, urlsplit
            self.assertEqual(parse_qs(urlsplit(url).query)["$where"], ["within_circle(the_geom,40.75301400,-73.98176860,200)"])
            return [row], {"cache": "miss", "http_status": 200, "retrieved_at": "2026-11-20T00:00:00Z"}

        result = self.collect_mocked(["stephen_a_schwarzman_building"], response)
        candidate = result["landmarks"][0]["candidates"][0]
        self.assertEqual(candidate["geometry"], geometry)
        self.assertEqual(candidate["attributes"], row)
        self.assertEqual(candidate["identifiers"]["doitt_id"], 12345)
        self.assertNotIn("osm_id", candidate)
        self.assertEqual(candidate["verification_status"], "candidate_unconfirmed")
        self.assertFalse(candidate["verified"])
        self.assertEqual(result["landmarks"][0]["collection_status"], "candidate_unconfirmed")

    def test_one_vanderbilt_geocodes_only_for_query_anchor_then_collects_spatial_rows(self) -> None:
        geometry = {"type": "Polygon", "coordinates": [[[-73.9789, 40.7520], [-73.9788, 40.7520], [-73.9788, 40.7521], [-73.9789, 40.7520]]]}
        row = {"the_geom": geometry, "name": None, "bin": 1234567, "doitt_id": 789, "base_bbl": "1000000001", "objectid": 22, "feature_code": 2100}
        calls = []

        def response(url, cache_path, **kwargs):
            calls.append(url)
            if "nominatim.openstreetmap.org" in url:
                return [{"lat": "40.75205", "lon": "-73.97885", "display_name": "1 Vanderbilt Avenue, Manhattan, New York, United States"}], {"cache": "miss", "http_status": 200}
            from urllib.parse import parse_qs, urlsplit
            self.assertEqual(parse_qs(urlsplit(url).query)["$where"], ["within_circle(the_geom,40.75205000,-73.97885000,200)"])
            return [row], {"cache": "miss", "http_status": 200}

        result = self.collect_mocked(["one_vanderbilt"], response)
        self.assertEqual(len(calls), 2)
        record = result["landmarks"][0]
        self.assertEqual(record["candidate_count"], 1)
        self.assertEqual(record["search_anchor_provenance"]["source"], "Nominatim address lookup (search anchor only)")
        self.assertEqual(record["candidates"][0]["geometry"], geometry)
        self.assertIn("doitt_id_present", record["candidates"][0]["match_evidence"])
        self.assertFalse(record["candidates"][0]["verified"])

    def test_name_is_not_required_for_spatial_candidate(self) -> None:
        target = collector.TARGETS["one_vanderbilt"]
        score, evidence = collector.candidate_score(target, {"name": None, "doitt_id": 44, "bin": 123, "feature_code": 2100}, 15)
        self.assertGreater(score, 0)
        self.assertNotIn("name_alias_support", evidence)

    def test_failed_geocode_and_api_errors_are_unresolved_not_empty_success(self) -> None:
        def response(url, cache_path, **kwargs):
            if "nominatim.openstreetmap.org" in url:
                return [], {"cache": "miss"}
            raise collector.CollectionError("simulated HTTP 503")

        result = self.collect_mocked(["one_vanderbilt", "stephen_a_schwarzman_building"], response)
        self.assertEqual(result["landmarks"][0]["collection_status"], "unresolved_request_failed")
        self.assertEqual(result["landmarks"][1]["collection_status"], "unresolved_request_failed")
        self.assertTrue(all(item["candidate_count"] == 0 for item in result["landmarks"]))
        self.assertEqual(len(result["errors"]), 2)

    def test_successful_empty_response_is_distinct_from_failure(self) -> None:
        result = self.collect_mocked(["stephen_a_schwarzman_building"], lambda *args, **kwargs: ([], {"http_status": 200}))
        self.assertEqual(result["landmarks"][0]["collection_status"], "unresolved_no_matches")
        self.assertEqual(result["errors"], [])

    def test_cli_prints_only_requested_landmark_and_keeps_full_result(self) -> None:
        stored = {
            "landmarks": [
                {"landmark_id": "one_vanderbilt", "collection_status": "candidate_unconfirmed", "candidate_count": 35},
                {"landmark_id": "stephen_a_schwarzman_building", "collection_status": "candidate_unconfirmed", "candidate_count": 56},
            ]
        }
        output = io.StringIO()
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT), "--landmarks", "one_vanderbilt"]),
            mock.patch.object(collector, "collect", return_value=stored),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(collector.main(), 0)
        self.assertIn("one_vanderbilt", output.getvalue())
        self.assertNotIn("stephen_a_schwarzman_building", output.getvalue())
        self.assertEqual(len(stored["landmarks"]), 2)


if __name__ == "__main__":
    unittest.main()
