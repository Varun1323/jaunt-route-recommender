"""Offline tests for Overpass batching, deduplication, and partial failures."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("collect_geodata.py")
SPEC = importlib.util.spec_from_file_location("collect_geodata", SCRIPT)
collector = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(collector)


class CollectionPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.landmarks = [
            {"id": key, "name": key.replace("_", " ")}
            for key in (
                "grand_central_terminal",
                "pershing_square_building",
                "chrysler_building",
                "graybar_building",
            )
        ]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.landmarks_path = self.root / "landmarks.json"
        self.landmarks_path.write_text(json.dumps({"landmarks": self.landmarks}), encoding="utf-8")

    def run_with_mock(self, response_function, batch_size: int = 1, cache_hours: float = 0, landmark_ids=None):
        raw_dir = self.root / "raw" / "osm"
        output_path = self.root / "landmark_candidates_v2.json"
        with (
            mock.patch.object(collector, "LANDMARKS_PATH", self.landmarks_path),
            mock.patch.object(collector, "RAW_DIR", raw_dir),
            mock.patch.object(collector, "DATA_DIR", self.root),
            mock.patch.object(collector, "RESULTS_PATH", output_path),
            mock.patch.object(collector, "request_json", side_effect=response_function),
            mock.patch.object(collector.time, "sleep"),
        ):
            result = collector.run_collection(False, cache_hours, batch_size, landmark_ids)
        return result

    def test_batches_default_one_landmark_per_request(self) -> None:
        batches = collector.landmark_batches(self.landmarks, 1)
        self.assertEqual([len(batch) for batch in batches], [1, 1, 1, 1])
        with self.assertRaises(ValueError):
            collector.landmark_batches(self.landmarks, 2)

    def test_one_vanderbilt_query_uses_names_and_requests_geometry(self) -> None:
        one_vanderbilt = {"id": "one_vanderbilt", "name": "1 Vanderbilt"}
        query = collector.overpass_query([one_vanderbilt])
        self.assertIn("One\\ Vanderbilt", query)
        self.assertIn("1\\ Vanderbilt", query)
        self.assertIn("One\\ Vanderbilt\\ Avenue", query)
        self.assertIn("1\\ Vanderbilt\\ Avenue", query)
        self.assertNotIn("around:", query)
        self.assertTrue(query.endswith("out body center geom;"))
        self.assertIn("one_vanderbilt", collector.LANDMARK_SPECS)
        self.assertIn("stephen_a_schwarzman_building", collector.LANDMARK_SPECS)

    def test_one_vanderbilt_candidate_keeps_full_geometry_unverified(self) -> None:
        one_vanderbilt = {"id": "one_vanderbilt", "name": "1 Vanderbilt"}
        self.landmarks_path.write_text(json.dumps({"landmarks": [one_vanderbilt]}), encoding="utf-8")
        geometry = [
            {"lat": 40.7530, "lon": -73.9819},
            {"lat": 40.7531, "lon": -73.9818},
            {"lat": 40.7530, "lon": -73.9819},
        ]
        elements = [
            {"type": "way", "id": 1001, "center": {"lat": 40.75305, "lon": -73.98185}, "tags": {"name": "One Vanderbilt", "building": "yes"}, "geometry": geometry},
            {"type": "relation", "id": 1002, "center": {"lat": 40.75306, "lon": -73.98184}, "tags": {"official_name": "1 Vanderbilt Avenue", "building": "yes"}, "geometry": geometry},
        ]

        def response(url, **kwargs):
            return {"elements": elements}, {"cache": "miss"}

        result = self.run_with_mock(response, batch_size=1)
        record = result["landmarks"][0]
        self.assertEqual(record["candidate_count"], 2)
        for candidate in record["candidates"]:
            osm_id = candidate["osm_id"]
            self.assertIn(osm_id, {1001, 1002})
            self.assertEqual(candidate["osm_id"], osm_id)
            self.assertEqual(candidate["geometry"], geometry)
            self.assertEqual(candidate["geometry_status"], "full_osm_geometry_returned")
            self.assertEqual(candidate["verification_status"], "candidate_unconfirmed")
            self.assertFalse(candidate["verified"])

    def test_deduplicates_same_osm_id(self) -> None:
        element = {
            "type": "way", "id": 123,
            "center": {"lat": 40.75, "lon": -73.98},
            "tags": {"name": "Grand Central Terminal", "building": "train_station"},
        }

        def response(url, **kwargs):
            return {"elements": [element, element]}, {"cache": "miss"}

        result = self.run_with_mock(response)
        record = result["landmarks"][0]
        self.assertEqual(record["candidate_count"], 1)
        self.assertEqual(record["candidates"][0]["osm_id"], 123)
        self.assertFalse(record["candidates"][0]["verified"])

    def test_partial_batch_failure_continues_and_marks_unresolved(self) -> None:
        calls = 0

        def response(url, **kwargs):
            nonlocal calls
            calls += 1
            if calls <= collector.OVERPASS_MAX_ATTEMPTS:
                raise collector.RetryableCollectionError("simulated timeout")
            element = {
                "type": "way", "id": 234,
                "center": {"lat": 40.75, "lon": -73.98},
                "tags": {"name": "Chrysler Building", "building": "office"},
            }
            return {"elements": [element]}, {"cache": "miss"}

        result = self.run_with_mock(response, batch_size=1)
        self.assertEqual(calls, 6)  # failed first batch retried; three remaining landmarks were still processed
        self.assertEqual(result["overpass_batches"][0]["status"], "failed")
        self.assertEqual(result["landmarks"][0]["verification_status"], "unresolved_batch_failed")
        self.assertEqual(result["landmarks"][1]["verification_status"], "unresolved_no_matches")
        self.assertEqual(result["landmarks"][2]["verification_status"], "candidate_unconfirmed")

    def test_transient_failures_retry_until_success(self) -> None:
        calls = 0

        def response(url, **kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise collector.RetryableCollectionError("simulated HTTP 502")
            return {"elements": []}, {"cache": "miss"}

        result = self.run_with_mock(response, batch_size=1)
        self.assertEqual(calls, 6)  # three attempts for first landmark, then one request for each remaining landmark
        self.assertEqual(result["overpass_batches"][0]["status"], "success")
        self.assertEqual(result["overpass_batches"][0]["attempts"], 3)

    def test_exhausted_retries_remain_failed_not_empty_success(self) -> None:
        calls = 0

        def response(url, **kwargs):
            nonlocal calls
            calls += 1
            raise collector.RetryableCollectionError("simulated timeout")

        result = self.run_with_mock(response, batch_size=1)
        self.assertEqual(calls, collector.OVERPASS_MAX_ATTEMPTS * len(self.landmarks))
        self.assertEqual(result["overpass_batches"][0]["status"], "failed")
        self.assertEqual(result["overpass_batches"][0]["attempts"], 3)
        self.assertTrue(all(item["verification_status"] == "unresolved_batch_failed" for item in result["landmarks"]))

    def test_successful_batch_is_preserved_when_later_batch_retries_out(self) -> None:
        calls_by_batch: dict[str, int] = {}

        def response(url, **kwargs):
            cache_path = kwargs["cache_path"]
            if cache_path.exists() and kwargs["max_age_hours"] > 0:
                return json.loads(cache_path.read_text(encoding="utf-8")), {"cache": "hit"}
            calls_by_batch[cache_path.name] = calls_by_batch.get(cache_path.name, 0) + 1
            if "grand_central_terminal" in cache_path.name:
                element = {
                    "type": "way", "id": 456,
                    "center": {"lat": 40.75, "lon": -73.98},
                    "tags": {"name": "Grand Central Terminal", "building": "train_station"},
                }
                # Mimic the raw cache write performed by request_json on a successful response.
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps({"elements": [element]}), encoding="utf-8")
                return {"elements": [element]}, {"cache": "miss"}
            raise collector.RetryableCollectionError("simulated HTTP 504")

        result = self.run_with_mock(response, batch_size=1)
        successful = result["landmarks"][0]
        self.assertEqual(successful["candidate_count"], 1)
        self.assertEqual(successful["candidates"][0]["osm_id"], 456)
        self.assertFalse(successful["candidates"][0]["verified"])
        failed_batch = next(batch for batch in result["overpass_batches"] if batch["landmark_ids"] == ["pershing_square_building"])
        self.assertEqual(failed_batch["attempts"], collector.OVERPASS_MAX_ATTEMPTS)

        # Re-run with a valid cache window: successful batch is served from its cache;
        # only the batch that previously failed is requested again.
        result = self.run_with_mock(response, cache_hours=168)
        successful_batch_name = "overpass_landmark_grand_central_terminal.json"
        failed_batch_name = "overpass_landmark_pershing_square_building.json"
        self.assertEqual(calls_by_batch[successful_batch_name], 1)
        self.assertEqual(calls_by_batch[failed_batch_name], 6)
        self.assertEqual(result["landmarks"][0]["candidate_count"], 1)

    def test_pedestrian_network_extension_uses_selected_place_bounds_and_cached_raw_path(self) -> None:
        candidate_path = self.root / "pedestrian_access_point_candidates.json"
        candidate_path.write_text(json.dumps({
            "landmarks": [{
                "landmark_id": "grand_central_terminal",
                "routing_candidate": {
                    "candidate_latitude": 40.752,
                    "candidate_longitude": -73.977,
                },
            }],
        }), encoding="utf-8")
        previous_results = self.root / "landmark_candidates_v2.json"
        previous_results.write_text(json.dumps({"landmarks": []}), encoding="utf-8")
        network_path = self.root / "raw" / "osm" / "pedestrian_network_midtown_extension.json"
        way = {"type": "way", "id": 1234, "nodes": [1, 2], "geometry": []}

        with (
            mock.patch.object(collector, "DATA_DIR", self.root),
            mock.patch.object(collector, "RESULTS_PATH", previous_results),
            mock.patch.object(collector, "PEDESTRIAN_NETWORK_PATH", network_path),
            mock.patch.object(collector, "request_json", return_value=(
                {"elements": [way]}, {"cache": "miss", "http_status": 200}
            )) as request,
        ):
            result = collector.collect_pedestrian_network_extension()

        self.assertEqual(result["elements"], [way])
        self.assertTrue(network_path.name in result["cache_path"])
        query_body = urllib.parse.parse_qs(request.call_args.kwargs["body"].decode("utf-8"))["data"][0]
        self.assertIn('way["highway"]', query_body)
        self.assertIn("(40.751000,-73.978000,40.753000,-73.976000)", query_body)

    def test_selective_collection_preserves_prior_success_records(self) -> None:
        output_path = self.root / "landmark_candidates_v2.json"
        prior_records = [
            {
                "landmark_id": landmark["id"],
                "landmark_name": landmark["name"],
                "candidate_count": 1 if landmark["id"] in {"chrysler_building", "graybar_building"} else 0,
                "candidate_tag_summary": [],
                "verification_status": "candidate_unconfirmed" if landmark["id"] in {"chrysler_building", "graybar_building"} else "unresolved_batch_failed",
                "verified": False,
                "candidates": [{"osm_type": "way", "osm_id": 900 + index, "verified": False, "verification_status": "candidate_unconfirmed"}] if landmark["id"] in {"chrysler_building", "graybar_building"} else [],
            }
            for index, landmark in enumerate(self.landmarks)
        ]
        prior_output = {"landmarks": prior_records, "overpass_batches": []}
        output_path.write_text(json.dumps(prior_output), encoding="utf-8")
        calls = []

        def response(url, **kwargs):
            calls.append((url, kwargs["cache_path"].name, kwargs["body"].decode("utf-8")))
            element = {
                "type": "way", "id": 777,
                "center": {"lat": 40.75, "lon": -73.98},
                "tags": {"name": "Grand Central Terminal", "building": "train_station"},
            }
            return {"elements": [element]}, {"cache": "miss"}

        result = self.run_with_mock(response, batch_size=1)
        # run_with_mock patches RESULTS_PATH to the same path used here.
        self.assertEqual(len(calls), 2)  # only the two unresolved/non-success landmarks are requested
        self.assertEqual(result["landmarks"][0]["candidates"][0]["osm_id"], 777)
        self.assertEqual(result["landmarks"][2], prior_records[2])
        self.assertEqual(result["landmarks"][3], prior_records[3])

    def test_landmark_filter_sends_one_selected_landmark(self) -> None:
        calls = []

        def response(url, **kwargs):
            calls.append(urllib.parse.parse_qs(kwargs["body"].decode("utf-8"))["data"][0])
            return {"elements": []}, {"cache": "miss"}

        with (
            mock.patch.object(collector, "LANDMARKS_PATH", self.landmarks_path),
            mock.patch.object(collector, "RAW_DIR", self.root / "raw" / "osm"),
            mock.patch.object(collector, "DATA_DIR", self.root),
            mock.patch.object(collector, "RESULTS_PATH", self.root / "landmark_candidates_v2.json"),
            mock.patch.object(collector, "request_json", side_effect=response),
            mock.patch.object(collector.time, "sleep"),
        ):
            result = collector.run_collection(False, 0, 1, ["grand_central_terminal"])
        self.assertEqual(len(calls), 1)
        self.assertIn("Grand\\ Central\\ Terminal", calls[0])
        self.assertEqual(result["requested_landmark_ids"], ["grand_central_terminal"])
        self.assertEqual(result["landmarks"][0]["verification_status"], "unresolved_no_matches")

    def test_alternate_endpoint_is_used_and_recorded_for_one_vanderbilt(self) -> None:
        one_vanderbilt = {"id": "one_vanderbilt", "name": "1 Vanderbilt"}
        self.landmarks_path.write_text(json.dumps({"landmarks": [one_vanderbilt]}), encoding="utf-8")
        alternate = "https://overpass.example.test/api/interpreter"
        calls = []

        def response(url, **kwargs):
            calls.append(url)
            return {"elements": []}, {"cache": "miss"}

        with mock.patch.dict("os.environ", {"JAUNT_OVERPASS_URL": alternate}):
            result = self.run_with_mock(response, landmark_ids=["one_vanderbilt"])
        self.assertEqual(calls, [alternate])
        self.assertEqual(result["request_metadata"]["overpass_endpoint"], alternate)
        self.assertEqual(result["overpass_batches"][0]["status"], "success")

    def test_one_vanderbilt_exhausted_endpoint_retries_remain_batch_failed(self) -> None:
        one_vanderbilt = {"id": "one_vanderbilt", "name": "1 Vanderbilt"}
        self.landmarks_path.write_text(json.dumps({"landmarks": [one_vanderbilt]}), encoding="utf-8")
        calls = []

        def response(url, **kwargs):
            calls.append(url)
            raise collector.RetryableCollectionError("simulated alternate endpoint timeout")

        with mock.patch.dict("os.environ", {"JAUNT_OVERPASS_URL": "https://overpass.example.test/api/interpreter"}):
            result = self.run_with_mock(response, landmark_ids=["one_vanderbilt"])
        self.assertEqual(len(calls), collector.OVERPASS_MAX_ATTEMPTS)
        self.assertEqual(result["overpass_batches"][0]["status"], "failed")
        self.assertEqual(result["overpass_batches"][0]["attempts"], collector.OVERPASS_MAX_ATTEMPTS)
        self.assertEqual(result["landmarks"][0]["verification_status"], "unresolved_batch_failed")

    def test_empty_success_is_distinct_from_failed_batch(self) -> None:
        def response(url, **kwargs):
            return {"elements": []}, {"cache": "miss"}

        result = self.run_with_mock(response)
        self.assertTrue(all(item["verification_status"] == "unresolved_no_matches" for item in result["landmarks"]))
        self.assertTrue(all(batch["status"] == "success" for batch in result["overpass_batches"]))


if __name__ == "__main__":
    unittest.main()
