"""test_demand_model.py -- Tests for transit demand and equity analysis."""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.demand_model import (
    compute_transit_demand_index,
    compute_service_level_index,
    compute_unmet_need,
    compute_coverage_gaps,
    run_demand_analysis,
)


def _demo():
    return pd.DataFrame({
        "district_id": ["D1", "D2", "D3", "D4"],
        "total_pop": [2000, 5000, 3000, 0],
        "pop_density_per_sq_mi": [1600, 5000, 3000, 0],
        "area_sq_miles": [1.2, 1.0, 1.1, 11.0],
        "mean_income": [240000, 135000, 210000, 0],
        "zero_veh_rate": [0.017, 0.027, 0.035, 0.0],
        "transit_share": [0.019, 0.035, 0.045, 0.0],
    })


def _costs():
    return pd.DataFrame({
        "district_id": ["D1", "D2", "D3", "D4"],
        "n_stops": [3, 0, 2, 0],
        "n_routes": [2, 0, 2, 0],
        "total_annual_cost": [300000, 0, 500000, 0],
    })


def _stops():
    return pd.DataFrame({
        "stop_id": ["S1", "S2", "S3", "S4", "S5"],
        "district_id": ["D1", "D1", "D1", "D3", "D3"],
    })


class TestTDI(unittest.TestCase):
    def test_returns_all_districts(self):
        tdi = compute_transit_demand_index(_demo())
        self.assertEqual(len(tdi), 4)

    def test_tdi_between_0_and_1(self):
        tdi = compute_transit_demand_index(_demo())
        self.assertTrue((tdi["tdi"] >= 0).all())
        self.assertTrue((tdi["tdi"] <= 1).all())

    def test_higher_density_higher_tdi(self):
        tdi = compute_transit_demand_index(_demo())
        d2 = tdi[tdi["district_id"] == "D2"]["tdi"].iloc[0]
        d4 = tdi[tdi["district_id"] == "D4"]["tdi"].iloc[0]
        self.assertGreater(d2, d4)

    def test_custom_weights(self):
        w = {"pop_density": 1.0, "zero_veh": 0, "transit_share": 0,
             "income_inverse": 0, "age_dependent": 0, "employment_proxy": 0}
        tdi = compute_transit_demand_index(_demo(), weights=w)
        self.assertEqual(len(tdi), 4)

    def test_rank_assigned(self):
        tdi = compute_transit_demand_index(_demo())
        self.assertIn("tdi_rank", tdi.columns)
        self.assertEqual(tdi["tdi_rank"].min(), 1)


class TestSLI(unittest.TestCase):
    def test_returns_all_districts(self):
        sli = compute_service_level_index(_costs(), _demo(), _stops())
        self.assertEqual(len(sli), 4)

    def test_zero_service_zero_sli(self):
        sli = compute_service_level_index(_costs(), _demo(), _stops())
        d4 = sli[sli["district_id"] == "D4"]["sli"].iloc[0]
        self.assertEqual(d4, 0.0)

    def test_more_stops_higher_sli(self):
        sli = compute_service_level_index(_costs(), _demo(), _stops())
        d1 = sli[sli["district_id"] == "D1"]["sli"].iloc[0]
        d2 = sli[sli["district_id"] == "D2"]["sli"].iloc[0]
        self.assertGreater(d1, d2)


class TestUnmetNeed(unittest.TestCase):
    def test_unmet_need_non_negative(self):
        tdi = compute_transit_demand_index(_demo())
        sli = compute_service_level_index(_costs(), _demo(), _stops())
        unmet = compute_unmet_need(tdi, sli)
        self.assertTrue((unmet["unmet_need"] >= 0).all())

    def test_equity_flags_assigned(self):
        tdi = compute_transit_demand_index(_demo())
        sli = compute_service_level_index(_costs(), _demo(), _stops())
        unmet = compute_unmet_need(tdi, sli)
        self.assertTrue(all(f in ("PRIORITY", "WATCH", "OK") for f in unmet["equity_flag"]))

    def test_no_service_flagged(self):
        tdi = compute_transit_demand_index(_demo())
        sli = compute_service_level_index(_costs(), _demo(), _stops())
        unmet = compute_unmet_need(tdi, sli)
        d2 = unmet[unmet["district_id"] == "D2"]
        # D2 has high pop density but 0 stops -- should be flagged
        self.assertIn(d2["service_gap"].iloc[0], ("NO SERVICE", "SEVERE GAP"))


class TestCoverage(unittest.TestCase):
    def test_coverage_fraction_range(self):
        cov = compute_coverage_gaps(_demo(), _stops())
        self.assertTrue((cov["coverage_fraction"] >= 0).all())
        self.assertTrue((cov["coverage_fraction"] <= 1).all())

    def test_zero_stops_zero_coverage(self):
        cov = compute_coverage_gaps(_demo(), _stops())
        d4 = cov[cov["district_id"] == "D4"]["coverage_fraction"].iloc[0]
        self.assertEqual(d4, 0.0)

    def test_gap_pop_not_negative(self):
        cov = compute_coverage_gaps(_demo(), _stops())
        self.assertTrue((cov["gap_population"] >= 0).all())


class TestFullPipeline(unittest.TestCase):
    def test_run_demand_analysis(self):
        results = run_demand_analysis(_demo(), _costs(), _stops())
        self.assertIn("tdi", results)
        self.assertIn("sli", results)
        self.assertIn("unmet_need", results)
        self.assertIn("coverage", results)


if __name__ == "__main__":
    unittest.main(verbosity=2)
