"""Optional live Valhalla smoke test; disabled unless JAUNT_RUN_LIVE_VALHALLA=1."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_routes import (  # noqa: E402
    DEFAULT_ENDPOINT,
    build_route_definitions,
    load_landmark_candidates,
    request_route,
    validate_cached_route,
)


@unittest.skipUnless(os.environ.get("JAUNT_RUN_LIVE_VALHALLA") == "1", "live Valhalla test is opt-in")
class TestLiveValhallaRoute(unittest.TestCase):
    def test_live_direct_route_is_pedestrian_and_valid(self):
        candidates = load_landmark_candidates()
        origin_id, destination_id = "bryant_park", "chanin_building"
        definitions = build_route_definitions(origin_id, destination_id, candidates)
        origin, destination = candidates[origin_id], candidates[destination_id]
        with tempfile.TemporaryDirectory() as route_dir, tempfile.TemporaryDirectory() as raw_dir:
            result = request_route(
                os.environ.get("VALHALLA_URL", DEFAULT_ENDPOINT),
                "direct",
                definitions["direct"],
                origin,
                destination,
                skip_existing=False,
                routes_dir=Path(route_dir),
                raw_dir=Path(raw_dir),
                request_delay_s=1.2,
            )
            feature_collection = result["feature_collection"]
            feature = feature_collection["features"][0]
            self.assertEqual(feature["properties"]["costing"], "pedestrian")
            self.assertGreater(len(feature["geometry"]["coordinates"]), 1)
            validate_cached_route(
                feature_collection,
                origin_id,
                destination_id,
                "direct",
                definitions["direct"]["locations"],
            )


if __name__ == "__main__":
    unittest.main()
