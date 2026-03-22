"""
test_districts.py -- Tests for district boundary loading and spatial operations.

Uses unittest (stdlib). Tests known locations, edge cases, area calculations,
and consistency checks for both LGHS (D1-D10) and Union (U1-U6) zones.
"""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.districts import DistrictManager, District, load_config


def get_dm():
    """Helper: load config and build DistrictManager."""
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    config = load_config(str(cfg_path))
    return DistrictManager(config)


class TestDistrictLoading(unittest.TestCase):
    """Tests for district configuration loading and geometry creation."""

    @classmethod
    def setUpClass(cls):
        cls.dm = get_dm()

    def test_lghs_count(self):
        """Should load exactly 10 LGHS districts."""
        self.assertEqual(len(self.dm.lghs_ids), 10)

    def test_union_count(self):
        """Should load exactly 6 Union SD districts."""
        self.assertEqual(len(self.dm.union_ids), 6)

    def test_total_count(self):
        """Combined should be 16."""
        self.assertEqual(len(self.dm.districts), 16)

    def test_ids_unique(self):
        """All district IDs must be unique."""
        all_ids = list(self.dm.districts.keys())
        self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_lghs_ids(self):
        """LGHS IDs should be D1-D10."""
        expected = {f"D{i}" for i in range(1, 11)}
        self.assertEqual(set(self.dm.lghs_ids), expected)

    def test_union_ids(self):
        """Union IDs should be U1-U6."""
        expected = {f"U{i}" for i in range(1, 7)}
        self.assertEqual(set(self.dm.union_ids), expected)

    def test_all_have_coords(self):
        """Every district should have at least 3 coordinate vertices."""
        for did, d in self.dm.districts.items():
            self.assertGreaterEqual(len(d.coords), 3, f"{did} has < 3 coords")

    def test_names_not_empty(self):
        """Every district should have a non-empty name."""
        for did, d in self.dm.districts.items():
            self.assertTrue(len(d.name) > 0, f"{did} has empty name")


class TestPointToDistrict(unittest.TestCase):
    """Tests for assigning geographic points to districts.

    Known locations verified against Google Maps and the confirmed
    interactive district map from Phase A1.
    """

    @classmethod
    def setUpClass(cls):
        cls.dm = get_dm()

    def test_town_hall_d1(self):
        """Los Gatos Town Hall (110 E Main St) -> D1 Downtown."""
        r = self.dm.point_to_district(37.2266, -121.9818, zone="LGHS")
        self.assertIsNotNone(r, "Town Hall not in any LGHS district")
        self.assertIn("D1", r, f"Town Hall -> {r}, expected D1")

    def test_vasona_park_d2(self):
        """Vasona Lake County Park -> D2."""
        r = self.dm.point_to_district(37.2400, -121.9870, zone="LGHS")
        self.assertIsNotNone(r)
        self.assertIn("D2", r, f"Vasona -> {r}, expected D2")

    def test_netflix_area_d6(self):
        """Netflix campus area -> D6."""
        r = self.dm.point_to_district(37.2500, -121.9520, zone="LGHS")
        self.assertIsNotNone(r)
        self.assertIn("D6", r, f"Netflix area -> {r}, expected D6")

    def test_shannon_rd_d7(self):
        """Shannon Rd (south hills) -> D7."""
        r = self.dm.point_to_district(37.2120, -121.9700, zone="LGHS")
        self.assertIsNotNone(r)
        self.assertIn("D7", r, f"Shannon Rd -> {r}, expected D7")

    def test_union_middle_in_union_zone(self):
        """Union Middle School -> some U district."""
        r = self.dm.point_to_district(37.2310, -121.9300, zone="UNION")
        self.assertIsNotNone(r, "Union Middle not in any Union district")
        self.assertTrue(any(uid in r for uid in ["U1", "U2", "U6"]),
                        f"Union Middle -> {r}, expected U*")

    def test_pacific_ocean_none(self):
        """Point in Pacific Ocean -> None."""
        r = self.dm.point_to_district(36.0, -124.0)
        self.assertIsNone(r)

    def test_san_francisco_none(self):
        """Point in SF -> None."""
        r = self.dm.point_to_district(37.7749, -122.4194)
        self.assertIsNone(r)

    def test_lghs_filter_only_d(self):
        """zone='LGHS' should only return D-prefixed IDs."""
        r = self.dm.point_to_district(37.2400, -121.9870, zone="LGHS")
        if r:
            for did in r.split(","):
                self.assertTrue(did.startswith("D"), f"LGHS filter returned {did}")

    def test_union_filter_only_u(self):
        """zone='UNION' should only return U-prefixed IDs."""
        r = self.dm.point_to_district(37.2400, -121.9400, zone="UNION")
        if r:
            for did in r.split(","):
                self.assertTrue(did.startswith("U"), f"UNION filter returned {did}")


class TestBatchAssignment(unittest.TestCase):
    """Tests for vectorized point assignment."""

    @classmethod
    def setUpClass(cls):
        cls.dm = get_dm()

    def test_assign_known_points(self):
        """Batch assign known locations."""
        df = pd.DataFrame({
            "name": ["Town Hall", "Vasona", "Netflix"],
            "lat": [37.2266, 37.2400, 37.2500],
            "lon": [-121.9818, -121.9870, -121.9520],
        })
        result = self.dm.assign_points(df, zone="LGHS")
        self.assertIn("district_id", result.columns)
        assigned = result["district_id"].notna().sum()
        self.assertGreaterEqual(assigned, 2, "Expected at least 2 points assigned")

    def test_empty_dataframe(self):
        """Empty DataFrame should return empty result."""
        df = pd.DataFrame({"lat": [], "lon": []})
        result = self.dm.assign_points(df)
        self.assertEqual(len(result), 0)


class TestAreas(unittest.TestCase):
    """Tests for district area computation."""

    @classmethod
    def setUpClass(cls):
        cls.dm = get_dm()

    def test_all_positive(self):
        """All districts should have positive area."""
        for did, d in self.dm.districts.items():
            a = d.area_sq_miles()
            self.assertGreater(a, 0, f"{did} area is {a}")

    def test_d10_largest(self):
        """D10 (Skyline) should be the largest district."""
        areas = {did: d.area_sq_miles() for did, d in self.dm.districts.items()}
        max_id = max(areas, key=areas.get)
        self.assertEqual(max_id, "D10", f"Largest is {max_id} ({areas[max_id]:.1f} sq mi)")

    def test_d1_small(self):
        """D1 (Downtown) should be < 2 sq mi."""
        a = self.dm.districts["D1"].area_sq_miles()
        self.assertLess(a, 2.0, f"D1 area is {a}")

    def test_urban_districts_reasonable(self):
        """Urban districts (D1-D8) should each be < 5 sq mi."""
        for i in range(1, 9):
            did = f"D{i}"
            a = self.dm.districts[did].area_sq_miles()
            self.assertLess(a, 5.0, f"{did} area is {a}")


class TestCentroids(unittest.TestCase):
    """Tests for centroid computation."""

    @classmethod
    def setUpClass(cls):
        cls.dm = get_dm()

    def test_centroids_in_bay_area(self):
        """All centroids should be within the greater Bay Area bounding box."""
        for did, d in self.dm.districts.items():
            lat, lon = d.centroid()
            self.assertGreater(lat, 37.0, f"{did} centroid lat={lat}")
            self.assertLess(lat, 37.5, f"{did} centroid lat={lat}")
            self.assertGreater(lon, -122.2, f"{did} centroid lon={lon}")
            self.assertLess(lon, -121.8, f"{did} centroid lon={lon}")


class TestValidation(unittest.TestCase):
    """Tests for district total validation."""

    @classmethod
    def setUpClass(cls):
        cls.dm = get_dm()

    def test_exact_match_passes(self):
        """Should pass when sums match exactly."""
        df = pd.DataFrame({"district_id": ["D1", "D2"], "val": [100.0, 200.0]})
        self.assertTrue(self.dm.validate_totals(df, "val", 300.0, "LGHS"))

    def test_within_tolerance_passes(self):
        """Should pass within 1% tolerance."""
        df = pd.DataFrame({"district_id": ["D1", "D2"], "val": [100.0, 200.0]})
        self.assertTrue(self.dm.validate_totals(df, "val", 301.0, "LGHS"))

    def test_outside_tolerance_fails(self):
        """Should fail when > 1% off."""
        df = pd.DataFrame({"district_id": ["D1", "D2"], "val": [100.0, 200.0]})
        self.assertFalse(self.dm.validate_totals(df, "val", 350.0, "LGHS"))

    def test_zero_total(self):
        """Zero expected total with zero actual should pass."""
        df = pd.DataFrame({"district_id": ["D1"], "val": [0.0]})
        self.assertTrue(self.dm.validate_totals(df, "val", 0.0, "LGHS"))


class TestSummaryTable(unittest.TestCase):
    """Tests for the summary table output."""

    @classmethod
    def setUpClass(cls):
        cls.dm = get_dm()

    def test_has_all_districts(self):
        """Summary should include all 16 districts."""
        summary = self.dm.summary_table()
        self.assertEqual(len(summary), 16)

    def test_required_columns(self):
        """Summary should have required columns."""
        summary = self.dm.summary_table()
        for col in ["id", "name", "zone", "area_sq_miles", "centroid_lat", "centroid_lon"]:
            self.assertIn(col, summary.columns, f"Missing column: {col}")


class TestGeoJSONExport(unittest.TestCase):
    """Tests for GeoJSON export."""

    @classmethod
    def setUpClass(cls):
        cls.dm = get_dm()

    def test_geojson_feature_structure(self):
        """Each district's GeoJSON feature should be valid."""
        d = self.dm.districts["D1"]
        feat = d.to_geojson_feature()
        self.assertEqual(feat["type"], "Feature")
        self.assertIn("properties", feat)
        self.assertIn("geometry", feat)
        self.assertEqual(feat["geometry"]["type"], "Polygon")
        ring = feat["geometry"]["coordinates"][0]
        # Ring should be closed
        self.assertEqual(ring[0], ring[-1])
        # Coordinates should be [lon, lat]
        self.assertTrue(-122.1 < ring[0][0] < -121.9, "lon out of range")
        self.assertTrue(37.1 < ring[0][1] < 37.3, "lat out of range")


class TestEdgeCases(unittest.TestCase):
    """Edge cases and error handling."""

    @classmethod
    def setUpClass(cls):
        cls.dm = get_dm()

    def test_invalid_zone_raises(self):
        """Invalid zone should raise ValueError."""
        with self.assertRaises(ValueError):
            self.dm._get_zone_ids("INVALID")

    def test_district_with_no_coords(self):
        """District with empty coords should have zero area."""
        d = District(id="TEST", name="Empty", zone="TEST", coords=[])
        self.assertEqual(d.area_sq_miles(), 0.0)
        self.assertFalse(d.contains_point(37.22, -121.98))


if __name__ == "__main__":
    unittest.main(verbosity=2)
