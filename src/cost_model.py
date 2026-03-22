"""
cost_model.py -- Transit cost calculations (town-wide + by district).

Computes:
  - Operating costs (per revenue-hour and per revenue-mile)
  - Capital costs (bus stops, shelters, vehicles)
  - Maintenance costs
  - Administrative overhead
  - District-level cost allocation based on service within each district

All cost benchmarks sourced from NTD FY2023 (VTA Bus Mode, Agency 90019)
and allocated to districts proportionally based on the fraction of
revenue-miles and revenue-hours within each district.

Standards:
    - FTA CBA guidelines for cost categorization
    - NTD operating/capital cost benchmarks
    - OMB Circular A-94 for discounting
    - Boardman et al., Ch. 4 (resource costs vs. transfer payments)

References:
    - FTA, "Guidance on New Starts/Small Starts Policies & Procedures"
    - NTD FY2023, VTA Agency Profile (Bus Mode)
    - APTA 2024 Public Transportation Fact Book
    - OMB Circular A-94 (discount rates)
"""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)


def load_config(config_path: str = "config.yaml") -> dict:
    """Load master configuration. See districts.py for full docstring."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# =====================================================================
# COST PARAMETERS (from config, with NTD-sourced defaults)
# =====================================================================

def get_cost_params(config: dict) -> dict:
    """Extract cost-related parameters from config with validation.

    Args:
        config: Master configuration dictionary.

    Returns:
        Dict of cost parameters with keys:
            op_cost_per_rev_hour, op_cost_per_rev_mile, fare_recovery_ratio,
            avg_fare, discount_rates, time_horizon, inflation_rate.

    Standard: NTD FY2023, VTA Bus Mode.
    """
    transit = config.get("transit", {})
    analysis = config.get("analysis", {})

    params = {
        "op_cost_per_rev_hour": transit.get("operating_cost_per_revenue_hour", 195.50),
        "op_cost_per_rev_mile": transit.get("operating_cost_per_revenue_mile", 14.80),
        "fare_recovery_ratio": transit.get("fare_recovery_ratio", 0.08),
        "avg_fare": transit.get("avg_fare", 2.00),
        "discount_rates": analysis.get("discount_rates", [0.02, 0.035, 0.07]),
        "time_horizon_operating": analysis.get("time_horizon_operating_years", 20),
        "time_horizon_capital": analysis.get("time_horizon_capital_years", 30),
        "inflation_rate": analysis.get("inflation_rate", 0.025),
        "base_year": analysis.get("base_year", 2025),
    }

    logger.info(
        "Cost params: $%.2f/rev-hr, $%.2f/rev-mi, %.0f%% fare recovery",
        params["op_cost_per_rev_hour"],
        params["op_cost_per_rev_mile"],
        params["fare_recovery_ratio"] * 100,
    )
    return params


# =====================================================================
# OPERATING COSTS
# =====================================================================

def compute_annual_operating_costs(
    route_service: pd.DataFrame,
    cost_params: dict,
) -> pd.DataFrame:
    """Compute annual operating costs by route.

    Operating cost = max(revenue_hours * cost_per_hour,
                         revenue_miles * cost_per_mile)
    This dual-formula approach follows NTD methodology where the binding
    constraint (time or distance) determines cost.

    Args:
        route_service: DataFrame with columns: route_id, annual_revenue_hours,
            annual_revenue_miles.
        cost_params: Dict from get_cost_params().

    Returns:
        DataFrame with added columns: cost_by_hour, cost_by_mile,
        annual_operating_cost, fare_revenue, net_operating_cost.

    Statistical method: Deterministic cost allocation.
    Standard: NTD cost methodology; FTA CBA guidelines, Section 4.
    Assumptions:
        - Operating cost per rev-hr and rev-mi are system-wide averages
          applied uniformly. In reality, mountain routes (Route 76) would
          have higher per-mile costs due to terrain. Flagged for sensitivity.
    """
    result = route_service.copy()

    result["cost_by_hour"] = (
        result["annual_revenue_hours"] * cost_params["op_cost_per_rev_hour"]
    )
    result["cost_by_mile"] = (
        result["annual_revenue_miles"] * cost_params["op_cost_per_rev_mile"]
    )
    # Binding constraint: use the higher of the two estimates
    result["annual_operating_cost"] = result[["cost_by_hour", "cost_by_mile"]].max(axis=1)

    # Fare revenue offset (transfer payment, not resource cost, but needed
    # for financial analysis per Boardman et al., Ch. 4)
    result["fare_revenue"] = (
        result.get("annual_boardings", pd.Series(0, index=result.index))
        * cost_params["avg_fare"]
    )
    result["net_operating_cost"] = result["annual_operating_cost"] - result["fare_revenue"]

    logger.info(
        "Operating costs computed: $%.0f total across %d routes",
        result["annual_operating_cost"].sum(),
        len(result),
    )
    return result


def generate_route_service_estimates(config: dict) -> pd.DataFrame:
    """Generate route-level service estimates (rev-hours, rev-miles, boardings).

    Based on NTD FY2023 VTA system data and route-specific estimates.

    VTA system totals (Bus, FY2023 NTD):
        - Revenue hours: ~1.1M
        - Revenue miles: ~16.5M
        - Unlinked trips: ~22M

    Route 27 estimated share: ~1.2% of system (based on route-mile
    proportion and frequency -- runs ~every 30 min weekdays).

    Returns:
        DataFrame: route_id, route_name, annual_revenue_hours,
        annual_revenue_miles, annual_boardings, service_days_per_year.

    Standard: NTD FY2023 (VTA Agency 90019).
    Assumptions: Route shares estimated from route-mile proportion.
        Sensitivity flag: HIGH for Route 76 restoration scenario.
    """
    # VTA system (Bus mode, FY2023)
    vta_rev_hours = 1_100_000
    vta_rev_miles = 16_500_000
    vta_boardings = 22_000_000

    routes = []

    # Route 27: ~15-mile route, ~30 min frequency, ~16 hrs/day, ~255 weekdays + 55 weekend
    r27_daily_hours_wd = 16.0 * 2  # both directions
    r27_daily_hours_we = 12.0 * 2
    r27_daily_miles_wd = 15.0 * (16.0 / 0.5) * 2  # 15 mi * trips * directions - simplified
    r27_rev_hours = r27_daily_hours_wd * 255 + r27_daily_hours_we * 55
    r27_rev_miles = 15.0 * 64 * 255 + 15.0 * 48 * 55  # simplified trip counts
    r27_boardings = int(vta_boardings * 0.012)

    routes.append({
        "route_id": "27",
        "route_name": "Winchester - Los Gatos - Santa Teresa",
        "annual_revenue_hours": round(r27_rev_hours),
        "annual_revenue_miles": round(min(r27_rev_miles, vta_rev_miles * 0.015)),
        "annual_boardings": r27_boardings,
        "service_days_per_year": 310,
        "status": "active",
        "total_system_stops": 52,  # VTA Route 27 full route stop count (GTFS)
    })

    # Highway 17 Express: limited service, ~6 trips/day weekdays
    routes.append({
        "route_id": "17X",
        "route_name": "Highway 17 Express",
        "annual_revenue_hours": 6 * 1.5 * 255,  # 6 trips * 1.5 hrs * weekdays
        "annual_revenue_miles": 6 * 25 * 255,    # 6 trips * 25 mi
        "annual_boardings": 180_000,
        "service_days_per_year": 255,
        "status": "active",
        "total_system_stops": 14,  # Express route, fewer stops
    })

    # Route 76 (discontinued -- for restoration scenario)
    # Historical: ~4 trips/day, school days only (~180 days), 12-mile route
    routes.append({
        "route_id": "76",
        "route_name": "Los Gatos - Summit Road",
        "annual_revenue_hours": 4 * 0.75 * 180,  # 4 trips * 45 min * school days
        "annual_revenue_miles": 4 * 12 * 180,     # 4 trips * 12 mi
        "annual_boardings": 40 * 180,              # ~40/day historical estimate
        "service_days_per_year": 180,
        "status": "discontinued",
        "total_system_stops": 8,  # All stops are in study area
    })

    return pd.DataFrame(routes)


# =====================================================================
# CAPITAL COSTS
# =====================================================================

def compute_capital_costs(
    stop_district_matrix: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """Compute capital costs by district.

    Capital cost categories:
        - Bus stop infrastructure (signs, benches, shelters, ADA pads)
        - Vehicle capital (allocated by revenue-mile share)
        - Technology (fareboxes, AVL, real-time signs)

    Stop-level capital costs are assigned directly to the district
    where the asset is physically located.

    Args:
        stop_district_matrix: DataFrame with stop_id, district_id, route_ids.
        config: Master config.

    Returns:
        DataFrame: district_id, n_stops, stop_capital, vehicle_capital_share,
        tech_capital, total_capital.

    Standard: FTA Capital Investment Grant guidelines; APTA Fact Book 2024.
    Assumptions:
        - Average bus stop capital: $15,000 (basic) to $75,000 (shelter+ADA).
          Using $25,000 weighted average per APTA benchmarks.
        - Vehicle capital allocated by revenue-mile share. VTA bus fleet
          replacement: ~$550,000 per standard 40-ft bus (NTD FY2023).
        - These are EXISTING infrastructure costs for the base case.
          New capital for Route 76 restoration would be separate.
    """
    # Cost per stop (weighted average: 70% basic sign+bench @ $15k, 30% with shelter @ $50k)
    cost_per_stop = 25_000

    # Count stops per district
    stops_by_dist = (
        stop_district_matrix
        .dropna(subset=["district_id"])
        .groupby("district_id")
        .agg(n_stops=("stop_id", "count"))
        .reset_index()
    )
    stops_by_dist["stop_capital"] = stops_by_dist["n_stops"] * cost_per_stop

    # Vehicle capital: VTA operates ~500 buses, ~$550k each = $275M fleet
    # Study area uses 2-3 buses out of 500 = ~0.5% of fleet
    total_fleet_value = 500 * 550_000
    study_area_share = 0.005  # 0.5% -- 2-3 buses for Rt 27 LG segment + 17X
    study_area_vehicle_capital = total_fleet_value * study_area_share

    # Distribute vehicle capital across districts by stop count (proxy for service)
    total_stops = stops_by_dist["n_stops"].sum()
    if total_stops > 0:
        stops_by_dist["vehicle_capital_share"] = (
            stops_by_dist["n_stops"] / total_stops * study_area_vehicle_capital
        ).round(0)
    else:
        stops_by_dist["vehicle_capital_share"] = 0

    # Technology capital: ~$5,000 per stop for real-time signs, fareboxes
    stops_by_dist["tech_capital"] = stops_by_dist["n_stops"] * 5_000

    stops_by_dist["total_capital"] = (
        stops_by_dist["stop_capital"]
        + stops_by_dist["vehicle_capital_share"]
        + stops_by_dist["tech_capital"]
    )

    logger.info(
        "Capital costs: $%.0f total across %d districts",
        stops_by_dist["total_capital"].sum(),
        len(stops_by_dist),
    )
    return stops_by_dist


# =====================================================================
# COST ALLOCATION TO DISTRICTS
# =====================================================================

def allocate_operating_costs_to_districts(
    route_costs: pd.DataFrame,
    route_district_matrix: pd.DataFrame,
) -> pd.DataFrame:
    """Allocate route-level operating costs to districts.

    Uses the number of stops per route per district as a proxy for
    the share of revenue-miles within each district. This is the
    standard approach when stop-level ridership data is unavailable
    (TCRP Report 88, Section 3.4).

    Args:
        route_costs: DataFrame with route_id, annual_operating_cost, etc.
        route_district_matrix: Pivot table of stops per route per district.

    Returns:
        DataFrame: district_id, allocated_operating_cost, allocated_fare_revenue,
        allocated_net_cost.

    Statistical method: Proportional allocation by stop share.
    Standard: TCRP Report 88, "A Guidebook for Developing a Transit
        Performance-Measurement System."
    Assumptions: Stop count is proportional to revenue-miles. This holds
        well for urban routes with regular stop spacing but may over-
        allocate to terminal districts. Flagged for sensitivity.
    """
    # Melt the route-district matrix to long form
    rdm = route_district_matrix.reset_index()
    melted = rdm.melt(
        id_vars=["route_ids"],
        var_name="district_id",
        value_name="n_stops",
    )
    melted = melted[melted["n_stops"] > 0].copy()

    # Compute each route's total stops across all districts
    route_totals = melted.groupby("route_ids")["n_stops"].sum().rename("study_area_stops")
    melted = melted.merge(route_totals, on="route_ids")

    # Get total system stops if available (for proper study-area share)
    if "total_system_stops" in route_costs.columns:
        sys_stops = route_costs.set_index("route_id")["total_system_stops"].to_dict()
        melted["total_system_stops"] = melted["route_ids"].map(sys_stops)
        melted["total_system_stops"] = melted["total_system_stops"].fillna(melted["study_area_stops"])

        # If study_area_stops > total_system_stops, the hardcoded total is wrong
        # (real GTFS has more stops in bounding box than expected).
        # Use max(study_area, system) so we never exceed 100% share.
        melted["effective_system_stops"] = melted[["study_area_stops", "total_system_stops"]].max(axis=1)

        melted["study_area_share"] = (
            melted["study_area_stops"] / melted["effective_system_stops"].clip(lower=1)
        ).clip(upper=1.0)  # Never more than 100% of route cost

        melted["stop_share"] = (melted["n_stops"] / melted["study_area_stops"]) * melted["study_area_share"]

        # Log per-route shares for debugging
        for rid in melted["route_ids"].unique():
            rm = melted[melted["route_ids"] == rid].iloc[0]
            logger.info(
                "  Route %s: %d study stops / %d system stops = %.0f%% share",
                rid, int(rm["study_area_stops"]),
                int(rm["effective_system_stops"]),
                rm["study_area_share"] * 100,
            )
    else:
        melted["stop_share"] = melted["n_stops"] / melted["study_area_stops"]
        logger.warning("No total_system_stops -- allocating full route cost to study area")

    # Merge with route costs
    merged = melted.merge(
        route_costs[["route_id", "annual_operating_cost", "fare_revenue", "net_operating_cost"]],
        left_on="route_ids",
        right_on="route_id",
        how="left",
    )

    # Allocate proportionally
    merged["allocated_operating_cost"] = merged["annual_operating_cost"] * merged["stop_share"]
    merged["allocated_fare_revenue"] = merged["fare_revenue"] * merged["stop_share"]
    merged["allocated_net_cost"] = merged["net_operating_cost"] * merged["stop_share"]

    # Aggregate by district
    by_district = (
        merged.groupby("district_id")
        .agg(
            allocated_operating_cost=("allocated_operating_cost", "sum"),
            allocated_fare_revenue=("allocated_fare_revenue", "sum"),
            allocated_net_cost=("allocated_net_cost", "sum"),
            n_routes=("route_ids", "nunique"),
        )
        .reset_index()
        .round(2)
    )

    logger.info(
        "Allocated operating costs to %d districts (total: $%.0f)",
        len(by_district),
        by_district["allocated_operating_cost"].sum(),
    )
    return by_district


# =====================================================================
# NTD PEER BENCHMARKING
# =====================================================================

def compute_peer_benchmarks() -> pd.DataFrame:
    """Compute operating cost benchmarks against NTD peer agencies.

    Peers selected as small-to-medium bus agencies in California
    with similar service characteristics.

    Returns:
        DataFrame: agency, cost_per_rev_hour, cost_per_rev_mile,
        cost_per_boarding, fare_recovery_ratio.

    Standard: NTD FY2023 agency profiles.
    Assumptions: Peer group is limited to CA agencies for regional
        cost comparability. VTA's costs are high relative to peers
        due to Bay Area labor costs.
    """
    peers = [
        {"agency": "VTA (Bus)", "cost_per_rev_hour": 195.50, "cost_per_rev_mile": 14.80,
         "cost_per_boarding": 9.77, "fare_recovery": 0.08},
        {"agency": "SamTrans", "cost_per_rev_hour": 210.30, "cost_per_rev_mile": 16.20,
         "cost_per_boarding": 11.50, "fare_recovery": 0.10},
        {"agency": "AC Transit", "cost_per_rev_hour": 188.70, "cost_per_rev_mile": 13.50,
         "cost_per_boarding": 7.20, "fare_recovery": 0.12},
        {"agency": "Santa Cruz Metro", "cost_per_rev_hour": 145.20, "cost_per_rev_mile": 11.30,
         "cost_per_boarding": 8.90, "fare_recovery": 0.09},
        {"agency": "Monterey-Salinas Transit", "cost_per_rev_hour": 132.80, "cost_per_rev_mile": 10.10,
         "cost_per_boarding": 10.20, "fare_recovery": 0.11},
        {"agency": "Golden Gate Transit", "cost_per_rev_hour": 225.40, "cost_per_rev_mile": 18.90,
         "cost_per_boarding": 15.30, "fare_recovery": 0.15},
    ]
    return pd.DataFrame(peers)


# =====================================================================
# ROUTE 76 RESTORATION SCENARIO COSTS
# =====================================================================

def estimate_route76_restoration_costs(cost_params: dict) -> dict:
    """Estimate costs for restoring discontinued VTA Route 76.

    Route 76 ran from Downtown Los Gatos to Summit Road (~12 miles).
    Restoration would require:
        - Rehabilitating ~8 bus stops (signs, ADA pads, shelters)
        - Operating costs for ~4 daily trips on school days
        - One dedicated vehicle (plus spare ratio)

    Returns:
        Dict with capital_cost, annual_operating_cost, annual_fare_revenue,
        annual_net_cost, assumptions.

    Standard: FTA Small Starts cost estimation methodology.
    Assumptions:
        - Stop rehab: $30,000/stop (higher than avg due to mountain conditions)
        - Vehicle: 1 bus @ $550,000 + 20% spare ratio = $660,000
        - Service: 4 trips/day, 180 school days, 45 min/trip, 12 mi/trip
        - Ridership: 40 boardings/day (matching historical pre-2010 levels)
    """
    # Capital
    n_stops = 8
    stop_rehab_cost = n_stops * 30_000  # Mountain conditions premium
    vehicle_cost = 550_000 * 1.2  # 1 bus + 20% spare ratio
    total_capital = stop_rehab_cost + vehicle_cost

    # Annual operating
    trips_per_day = 4
    service_days = 180
    trip_time_hours = 0.75
    trip_distance_miles = 12

    annual_rev_hours = trips_per_day * trip_time_hours * service_days
    annual_rev_miles = trips_per_day * trip_distance_miles * service_days

    annual_op_cost = max(
        annual_rev_hours * cost_params["op_cost_per_rev_hour"],
        annual_rev_miles * cost_params["op_cost_per_rev_mile"],
    )

    # Revenue
    daily_boardings = 40
    annual_boardings = daily_boardings * service_days
    annual_fare_revenue = annual_boardings * cost_params["avg_fare"]

    return {
        "capital_cost_total": round(total_capital),
        "capital_stop_rehab": round(stop_rehab_cost),
        "capital_vehicle": round(vehicle_cost),
        "annual_revenue_hours": round(annual_rev_hours),
        "annual_revenue_miles": round(annual_rev_miles),
        "annual_operating_cost": round(annual_op_cost),
        "annual_boardings": annual_boardings,
        "annual_fare_revenue": round(annual_fare_revenue),
        "annual_net_cost": round(annual_op_cost - annual_fare_revenue),
        "cost_per_boarding": round(annual_op_cost / max(annual_boardings, 1), 2),
        "assumptions": {
            "trips_per_day": trips_per_day,
            "service_days": service_days,
            "daily_boardings": daily_boardings,
            "stop_count": n_stops,
            "route_miles": trip_distance_miles,
            "source": "FTA Small Starts methodology; historical Route 76 data",
        },
    }


# =====================================================================
# PRESENT VALUE OF COSTS
# =====================================================================

def compute_cost_npv(
    annual_operating_cost: float,
    capital_cost: float,
    discount_rates: list[float],
    time_horizon_operating: int = 20,
    time_horizon_capital: int = 30,
    growth_rate: float = 0.025,
) -> pd.DataFrame:
    """Compute net present value of costs at multiple discount rates.

    Operating costs grow at the inflation/growth rate over the analysis
    period. Capital costs are incurred in year 0 (no discounting).

    Args:
        annual_operating_cost: Year-0 annual operating cost.
        capital_cost: One-time capital investment (year 0).
        discount_rates: List of real discount rates.
        time_horizon_operating: Years for operating analysis.
        time_horizon_capital: Years for capital analysis.
        growth_rate: Annual real growth rate for operating costs.

    Returns:
        DataFrame: discount_rate, pv_operating, pv_capital, pv_total.

    Statistical method: Discounted cash flow (standard PV formula).
    Standard: OMB Circular A-94, Section 8.
    Assumptions: Operating costs grow at the specified real rate.
        Capital is a lump sum in year 0.
    """
    results = []
    for r in discount_rates:
        # PV of growing annuity: sum of C*(1+g)^t / (1+r)^t for t=1..T
        pv_operating = sum(
            annual_operating_cost * (1 + growth_rate) ** t / (1 + r) ** t
            for t in range(1, time_horizon_operating + 1)
        )
        pv_capital = capital_cost  # Year 0, no discounting

        results.append({
            "discount_rate": r,
            "discount_rate_label": f"{r*100:.1f}%",
            "pv_operating": round(pv_operating),
            "pv_capital": round(pv_capital),
            "pv_total_cost": round(pv_operating + pv_capital),
        })

    return pd.DataFrame(results)


# =====================================================================
# FULL COST SUMMARY
# =====================================================================

def build_district_cost_summary(
    district_operating: pd.DataFrame,
    district_capital: pd.DataFrame,
) -> pd.DataFrame:
    """Combine operating and capital costs into a single district summary.

    Args:
        district_operating: From allocate_operating_costs_to_districts().
        district_capital: From compute_capital_costs().

    Returns:
        DataFrame: district_id, annual_operating_cost, capital_cost,
        total_annual_cost (operating + amortized capital over 20 years).

    Standard: FTA CBA guidelines (combined cost table format).
    """
    merged = district_operating.merge(
        district_capital[["district_id", "n_stops", "total_capital"]],
        on="district_id",
        how="outer",
    ).fillna(0)

    # Amortize capital over 20-year operating horizon
    merged["annual_amortized_capital"] = (merged["total_capital"] / 20).round(2)
    merged["total_annual_cost"] = (
        merged["allocated_operating_cost"] + merged["annual_amortized_capital"]
    ).round(2)

    return merged
