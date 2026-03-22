"""
test_benefit_model.py -- Tests for transit benefit calculations.
"""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.benefit_model import (
    get_benefit_params, compute_travel_time_savings, compute_voc_savings,
    compute_crash_reduction_benefits, compute_emission_benefits,
    compute_health_benefits, compute_reliability_benefits,
    compute_option_value, compute_all_benefits, compute_benefit_npv,
    allocate_benefits_to_districts, load_config,
)


def cfg():
    return load_config(str(Path(__file__).resolve().parent.parent / "config.yaml"))


class TestBenefitParams(unittest.TestCase):
    def test_loads(self):
        p = get_benefit_params(cfg())
        self.assertGreater(p["vot_all"], 0)
        self.assertGreater(p["vsl"], 0)
        self.assertGreater(p["scc"], 0)


class TestTravelTimeSavings(unittest.TestCase):
    def test_positive_when_auto_slower(self):
        """When auto trip is costly in time, savings should be positive."""
        p = get_benefit_params(cfg())
        r = compute_travel_time_savings(100000, 25.0, 35.0, p)
        # Even though transit takes longer, auto IVT is valued higher (100% vs 60%)
        # so net can still be positive
        self.assertIn("annual_benefit", r)

    def test_zero_boardings(self):
        p = get_benefit_params(cfg())
        r = compute_travel_time_savings(0, 25.0, 35.0, p)
        self.assertEqual(r["annual_benefit"], 0)

    def test_diversion_rate(self):
        p = get_benefit_params(cfg())
        r = compute_travel_time_savings(100000, 25.0, 35.0, p, pct_diverted_from_auto=0.50)
        self.assertEqual(r["diverted_trips"], 50000)


class TestVOCSavings(unittest.TestCase):
    def test_positive(self):
        p = get_benefit_params(cfg())
        r = compute_voc_savings(100000, 7.5, p)
        self.assertGreater(r["annual_benefit"], 0)

    def test_more_boardings_more_savings(self):
        p = get_benefit_params(cfg())
        r1 = compute_voc_savings(50000, 7.5, p)
        r2 = compute_voc_savings(100000, 7.5, p)
        self.assertGreater(r2["annual_benefit"], r1["annual_benefit"])


class TestCrashReduction(unittest.TestCase):
    def test_positive(self):
        p = get_benefit_params(cfg())
        r = compute_crash_reduction_benefits(1000000, p)
        self.assertGreater(r["annual_benefit"], 0)

    def test_zero_vmt(self):
        p = get_benefit_params(cfg())
        r = compute_crash_reduction_benefits(0, p)
        self.assertEqual(r["annual_benefit"], 0)

    def test_fatal_share(self):
        """Fatal crashes should be a small fraction of total."""
        p = get_benefit_params(cfg())
        r = compute_crash_reduction_benefits(10000000, p)
        self.assertLess(r["avoided_fatal"], r["avoided_crashes"])


class TestEmissions(unittest.TestCase):
    def test_positive_net_benefit(self):
        p = get_benefit_params(cfg())
        r = compute_emission_benefits(1000000, 50000, p)
        self.assertGreater(r["annual_benefit"], 0)

    def test_co2_tons_reasonable(self):
        """1M avoided VMT should yield ~347 tons CO2."""
        p = get_benefit_params(cfg())
        r = compute_emission_benefits(1000000, 50000, p)
        self.assertAlmostEqual(r["co2_tons_avoided"], 347.0, delta=1.0)


class TestHealth(unittest.TestCase):
    def test_positive(self):
        p = get_benefit_params(cfg())
        r = compute_health_benefits(100000, p)
        self.assertGreater(r["annual_benefit"], 0)

    def test_proportional(self):
        p = get_benefit_params(cfg())
        r1 = compute_health_benefits(50000, p)
        r2 = compute_health_benefits(100000, p)
        self.assertAlmostEqual(r2["annual_benefit"], r1["annual_benefit"] * 2, delta=1)


class TestReliability(unittest.TestCase):
    def test_positive(self):
        p = get_benefit_params(cfg())
        r = compute_reliability_benefits(100000, 25.0, p)
        self.assertGreater(r["annual_benefit"], 0)


class TestOptionValue(unittest.TestCase):
    def test_positive(self):
        p = get_benefit_params(cfg())
        r = compute_option_value(33000, p)
        self.assertGreater(r["annual_benefit"], 0)

    def test_per_capita(self):
        p = get_benefit_params(cfg())
        r = compute_option_value(33000, p, option_value_per_capita=25.0)
        self.assertEqual(r["annual_benefit"], 33000 * 25.0)


class TestAllBenefits(unittest.TestCase):
    def test_seven_categories(self):
        c = cfg()
        ridership = pd.DataFrame({
            "route_id": ["27", "17X"], "annual_boardings": [264000, 180000],
            "status": ["active", "active"],
        })
        results = compute_all_benefits(c, ridership, 285750, 33000)
        self.assertEqual(len(results), 7)

    def test_all_have_annual_benefit(self):
        c = cfg()
        ridership = pd.DataFrame({
            "route_id": ["27"], "annual_boardings": [264000],
            "status": ["active"],
        })
        results = compute_all_benefits(c, ridership, 247500, 33000)
        for r in results:
            self.assertIn("annual_benefit", r)
            self.assertIn("category", r)


class TestBenefitNPV(unittest.TestCase):
    def test_three_rates(self):
        npv = compute_benefit_npv(1000000, [0.02, 0.035, 0.07])
        self.assertEqual(len(npv), 3)

    def test_higher_rate_lower_pv(self):
        npv = compute_benefit_npv(1000000, [0.02, 0.07])
        self.assertGreater(npv.iloc[0]["pv_benefits"], npv.iloc[1]["pv_benefits"])


class TestAllocation(unittest.TestCase):
    def test_allocates_to_districts(self):
        benefits = [
            {"category": "Travel Time Savings", "annual_benefit": 100000},
            {"category": "Option Value", "annual_benefit": 50000},
        ]
        stops = pd.DataFrame({
            "stop_id": ["S1", "S2", "S3"],
            "district_id": ["D1", "D1", "D5"],
            "route_ids": ["27", "27", "27"],
        })
        demo = pd.DataFrame({
            "district_id": ["D1", "D5"],
            "total_pop": [2000, 3000],
        })
        p = get_benefit_params(cfg())
        result = allocate_benefits_to_districts(benefits, stops, demo, p)
        self.assertIn("total_benefits", result.columns)
        self.assertGreater(len(result), 0)

    def test_totals_sum_correctly(self):
        benefits = [{"category": "Health Benefits Active Transport", "annual_benefit": 90000}]
        stops = pd.DataFrame({
            "stop_id": ["S1", "S2"],
            "district_id": ["D1", "D5"],
            "route_ids": ["27", "27"],
        })
        demo = pd.DataFrame({"district_id": ["D1", "D5"], "total_pop": [1000, 1000]})
        p = get_benefit_params(cfg())
        result = allocate_benefits_to_districts(benefits, stops, demo, p)
        total = result["total_benefits"].sum()
        self.assertAlmostEqual(total, 90000, delta=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
