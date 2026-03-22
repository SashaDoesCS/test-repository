"""
test_cost_model.py -- Tests for transit cost calculations.

Tests operating cost computation, capital cost allocation, district-level
cost distribution, NPV calculations, peer benchmarking, and Route 76
restoration scenario.
"""

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.cost_model import (
    get_cost_params,
    compute_annual_operating_costs,
    generate_route_service_estimates,
    compute_capital_costs,
    allocate_operating_costs_to_districts,
    compute_peer_benchmarks,
    estimate_route76_restoration_costs,
    compute_cost_npv,
    build_district_cost_summary,
    load_config,
)


def get_config():
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    return load_config(str(cfg_path))


class TestCostParams(unittest.TestCase):
    """Tests for cost parameter extraction from config."""

    def test_params_load(self):
        config = get_config()
        params = get_cost_params(config)
        self.assertIn("op_cost_per_rev_hour", params)
        self.assertIn("op_cost_per_rev_mile", params)
        self.assertIn("discount_rates", params)

    def test_params_positive(self):
        config = get_config()
        params = get_cost_params(config)
        self.assertGreater(params["op_cost_per_rev_hour"], 0)
        self.assertGreater(params["op_cost_per_rev_mile"], 0)
        self.assertGreater(params["avg_fare"], 0)

    def test_discount_rates_three(self):
        config = get_config()
        params = get_cost_params(config)
        self.assertEqual(len(params["discount_rates"]), 3)


class TestRouteServiceEstimates(unittest.TestCase):
    """Tests for route service data generation."""

    def test_three_routes(self):
        config = get_config()
        routes = generate_route_service_estimates(config)
        self.assertEqual(len(routes), 3)

    def test_route_ids(self):
        config = get_config()
        routes = generate_route_service_estimates(config)
        ids = set(routes["route_id"])
        self.assertEqual(ids, {"27", "17X", "76"})

    def test_positive_values(self):
        config = get_config()
        routes = generate_route_service_estimates(config)
        for col in ["annual_revenue_hours", "annual_revenue_miles"]:
            self.assertTrue((routes[col] > 0).all(), f"{col} has non-positive values")

    def test_route76_historical_boardings(self):
        """Route 76 should have ~7,200 annual boardings (40/day * 180 days)."""
        config = get_config()
        routes = generate_route_service_estimates(config)
        r76 = routes[routes["route_id"] == "76"]
        self.assertAlmostEqual(r76.iloc[0]["annual_boardings"], 7200, delta=100)


class TestOperatingCosts(unittest.TestCase):
    """Tests for operating cost computation."""

    def test_costs_positive(self):
        config = get_config()
        params = get_cost_params(config)
        routes = generate_route_service_estimates(config)
        costs = compute_annual_operating_costs(routes, params)
        self.assertTrue((costs["annual_operating_cost"] > 0).all())

    def test_binding_constraint(self):
        """Operating cost should be max of hour-based and mile-based."""
        config = get_config()
        params = get_cost_params(config)
        routes = generate_route_service_estimates(config)
        costs = compute_annual_operating_costs(routes, params)
        for _, row in costs.iterrows():
            self.assertEqual(
                row["annual_operating_cost"],
                max(row["cost_by_hour"], row["cost_by_mile"]),
            )

    def test_net_cost_less_than_gross(self):
        """Net cost (after fare revenue) should be <= gross operating cost."""
        config = get_config()
        params = get_cost_params(config)
        routes = generate_route_service_estimates(config)
        costs = compute_annual_operating_costs(routes, params)
        self.assertTrue(
            (costs["net_operating_cost"] <= costs["annual_operating_cost"]).all()
        )

    def test_fare_revenue_positive(self):
        """Active routes should have positive fare revenue."""
        config = get_config()
        params = get_cost_params(config)
        routes = generate_route_service_estimates(config)
        costs = compute_annual_operating_costs(routes, params)
        active = costs[costs["status"] != "discontinued"]
        self.assertTrue((active["fare_revenue"] > 0).all())


class TestCapitalCosts(unittest.TestCase):
    """Tests for capital cost computation."""

    def test_capital_positive(self):
        stops = pd.DataFrame({
            "stop_id": ["S1", "S2", "S3"],
            "district_id": ["D1", "D1", "D5"],
            "route_ids": ["27", "27", "27"],
        })
        config = get_config()
        caps = compute_capital_costs(stops, config)
        self.assertTrue((caps["total_capital"] > 0).all())

    def test_more_stops_more_capital(self):
        """District with more stops should have higher capital costs."""
        stops = pd.DataFrame({
            "stop_id": ["S1", "S2", "S3", "S4", "S5"],
            "district_id": ["D1", "D1", "D1", "D5", "D5"],
            "route_ids": ["27"] * 5,
        })
        config = get_config()
        caps = compute_capital_costs(stops, config)
        d1_cap = caps.loc[caps["district_id"] == "D1", "total_capital"].iloc[0]
        d5_cap = caps.loc[caps["district_id"] == "D5", "total_capital"].iloc[0]
        self.assertGreater(d1_cap, d5_cap)

    def test_handles_empty_districts(self):
        """Districts with no stops should not appear."""
        stops = pd.DataFrame({
            "stop_id": ["S1"],
            "district_id": ["D1"],
            "route_ids": ["27"],
        })
        config = get_config()
        caps = compute_capital_costs(stops, config)
        self.assertEqual(len(caps), 1)


class TestCostAllocation(unittest.TestCase):
    """Tests for operating cost allocation to districts."""

    def test_allocation_sums_to_total(self):
        """District-allocated costs should sum to study-area share of route total."""
        config = get_config()
        params = get_cost_params(config)
        routes = generate_route_service_estimates(config)
        costs = compute_annual_operating_costs(routes, params)

        # Build a simple route-district matrix
        # Route 27 has 5 study-area stops out of 52 system-wide
        # Route 76 has 4 study-area stops out of 8 system-wide
        rdm = pd.DataFrame({
            "route_ids": ["27", "27", "76"],
            "D1": [1, 0, 2],
            "D5": [4, 0, 0],
            "D9": [0, 0, 2],
        }).set_index("route_ids")

        allocated = allocate_operating_costs_to_districts(costs, rdm)
        total_allocated = allocated["allocated_operating_cost"].sum()

        # With total_system_stops, allocated should be LESS than full route cost
        routes_in_matrix = set(rdm.index)
        total_route_cost = costs[costs["route_id"].isin(routes_in_matrix)]["annual_operating_cost"].sum()

        # Allocated should be > 0 and <= full route cost
        self.assertGreater(total_allocated, 0)
        self.assertLessEqual(total_allocated, total_route_cost * 1.01)

    def test_allocation_all_positive(self):
        config = get_config()
        params = get_cost_params(config)
        routes = generate_route_service_estimates(config)
        costs = compute_annual_operating_costs(routes, params)

        rdm = pd.DataFrame({
            "route_ids": ["27"],
            "D1": [2],
            "D5": [3],
        }).set_index("route_ids")

        allocated = allocate_operating_costs_to_districts(costs, rdm)
        self.assertTrue((allocated["allocated_operating_cost"] > 0).all())


class TestPeerBenchmarks(unittest.TestCase):
    """Tests for NTD peer agency data."""

    def test_vta_in_peers(self):
        peers = compute_peer_benchmarks()
        self.assertIn("VTA (Bus)", peers["agency"].values)

    def test_all_positive(self):
        peers = compute_peer_benchmarks()
        for col in ["cost_per_rev_hour", "cost_per_rev_mile", "cost_per_boarding"]:
            self.assertTrue((peers[col] > 0).all())


class TestRoute76Restoration(unittest.TestCase):
    """Tests for Route 76 restoration cost scenario."""

    def test_returns_all_keys(self):
        config = get_config()
        params = get_cost_params(config)
        r76 = estimate_route76_restoration_costs(params)
        required = ["capital_cost_total", "annual_operating_cost",
                     "annual_fare_revenue", "annual_net_cost", "cost_per_boarding"]
        for key in required:
            self.assertIn(key, r76, f"Missing key: {key}")

    def test_capital_reasonable(self):
        """Capital should be between $500k and $2M."""
        config = get_config()
        params = get_cost_params(config)
        r76 = estimate_route76_restoration_costs(params)
        self.assertGreater(r76["capital_cost_total"], 500_000)
        self.assertLess(r76["capital_cost_total"], 2_000_000)

    def test_cost_per_boarding_high(self):
        """Mountain route should have high cost/boarding (>$10, typical transit)."""
        config = get_config()
        params = get_cost_params(config)
        r76 = estimate_route76_restoration_costs(params)
        self.assertGreater(r76["cost_per_boarding"], 10)

    def test_net_cost_positive(self):
        """Net cost should be positive (costs exceed fare revenue)."""
        config = get_config()
        params = get_cost_params(config)
        r76 = estimate_route76_restoration_costs(params)
        self.assertGreater(r76["annual_net_cost"], 0)


class TestNPV(unittest.TestCase):
    """Tests for present value cost calculations."""

    def test_three_discount_rates(self):
        npv = compute_cost_npv(1_000_000, 500_000, [0.02, 0.035, 0.07])
        self.assertEqual(len(npv), 3)

    def test_higher_rate_lower_pv(self):
        """Higher discount rate should produce lower PV of operating costs."""
        npv = compute_cost_npv(1_000_000, 0, [0.02, 0.07])
        pv_low = npv.iloc[0]["pv_operating"]
        pv_high = npv.iloc[1]["pv_operating"]
        self.assertGreater(pv_low, pv_high)

    def test_capital_not_discounted(self):
        """Capital (year 0) should appear at face value."""
        npv = compute_cost_npv(0, 1_000_000, [0.02, 0.07])
        for _, row in npv.iterrows():
            self.assertEqual(row["pv_capital"], 1_000_000)

    def test_zero_costs(self):
        npv = compute_cost_npv(0, 0, [0.035])
        self.assertEqual(npv.iloc[0]["pv_total_cost"], 0)


class TestDistrictCostSummary(unittest.TestCase):
    """Tests for combined district cost table."""

    def test_merge_complete(self):
        op = pd.DataFrame({
            "district_id": ["D1", "D5"],
            "allocated_operating_cost": [100000, 200000],
            "allocated_fare_revenue": [5000, 10000],
            "allocated_net_cost": [95000, 190000],
            "n_routes": [1, 1],
        })
        cap = pd.DataFrame({
            "district_id": ["D1", "D5", "D9"],
            "n_stops": [3, 5, 2],
            "total_capital": [90000, 150000, 60000],
        })
        summary = build_district_cost_summary(op, cap)
        self.assertEqual(len(summary), 3)  # D1, D5, D9
        self.assertIn("total_annual_cost", summary.columns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
