"""Offline regression tests for verified pedestrian access points."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCESS_POINTS_PATH = ROOT / "backend" / "data" / "pedestrian_access_points.json"
LANDMARKS_PATH = ROOT / "backend" / "data" / "landmarks.json"


class PedestrianAccessPointsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(ACCESS_POINTS_PATH.exists(), f"Missing {ACCESS_POINTS_PATH}")
        self.data = json.loads(ACCESS_POINTS_PATH.read_text(encoding="utf-8"))
        self.landmarks_data = json.loads(LANDMARKS_PATH.read_text(encoding="utf-8"))

    def test_all_nine_landmarks_have_access_points(self) -> None:
        all_landmark_ids = {item["id"] for item in self.landmarks_data["landmarks"]}
        self.assertEqual(len(all_landmark_ids), 13)

        recorded_ids = {ap["landmark_id"] for ap in self.data["access_points"]}
        self.assertEqual(len(recorded_ids), 9)
        self.assertTrue(recorded_ids.issubset(all_landmark_ids))

    def test_access_point_fields_and_coordinate_validity(self) -> None:
        valid_access_types = {"public_entrance", "park_entrance", "exterior_viewpoint"}
        study_area_bbox = (-73.990, 40.745, -73.970, 40.760)  # min_lon, min_lat, max_lon, max_lat

        for ap in self.data["access_points"]:
            # Required fields
            self.assertIn("landmark_id", ap)
            self.assertIn("landmark_name", ap)
            self.assertIn("access_type", ap)
            self.assertIn("latitude", ap)
            self.assertIn("longitude", ap)
            self.assertIn("public_access", ap)
            self.assertIn("status", ap)
            self.assertIn("access_description_status", ap)
            self.assertIn("coordinate_verification_status", ap)
            self.assertIn("supporting_evidence", ap)
            self.assertIn("source_type", ap)
            self.assertIn("source_url", ap)

            # Validity
            self.assertIn(ap["access_type"], valid_access_types)
            self.assertEqual(ap["public_access"], "public")
            self.assertEqual(ap["access_description_status"], "verified")
            self.assertEqual(ap["coordinate_verification_status"], "needs_review")
            self.assertEqual(ap["status"], "needs_review")

            lat = ap["latitude"]
            lon = ap["longitude"]
            self.assertTrue(isinstance(lat, (int, float)) and math.isfinite(lat))
            self.assertTrue(isinstance(lon, (int, float)) and math.isfinite(lon))

            # Coordinates must fall within the Midtown Manhattan study area
            self.assertGreaterEqual(lon, study_area_bbox[0])
            self.assertLessEqual(lon, study_area_bbox[2])
            self.assertGreaterEqual(lat, study_area_bbox[1])
            self.assertLessEqual(lat, study_area_bbox[3])

    def test_distinction_between_public_entrances_and_exterior_viewpoints(self) -> None:
        by_id = {ap["landmark_id"]: ap for ap in self.data["access_points"]}

        # Public attractions with confirmed public interior access
        self.assertEqual(by_id["grand_central_terminal"]["access_type"], "public_entrance")
        self.assertEqual(by_id["stephen_a_schwarzman_building"]["access_type"], "public_entrance")
        self.assertEqual(by_id["one_vanderbilt"]["access_type"], "public_entrance")

        # Park entrance
        self.assertEqual(by_id["bryant_park"]["access_type"], "park_entrance")

        # Commercial office towers with restricted/private tenant access designated as exterior viewpoints
        for office_id in [
            "pershing_square_building",
            "chrysler_building",
            "graybar_building",
            "chanin_building",
            "daily_news_building",
        ]:
            self.assertEqual(
                by_id[office_id]["access_type"],
                "exterior_viewpoint",
                f"{office_id} should be exterior_viewpoint",
            )


if __name__ == "__main__":
    unittest.main()

