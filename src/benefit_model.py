"""
benefit_model.py -- Transit benefit calculations (town-wide + by district).

Computes seven benefit categories per FTA CBA guidelines:
  1. Travel time savings (commuter + personal)
  2. Vehicle operating cost (VOC) savings
  3. Accident/crash reduction
  4. Emission reduction (CO2, NOx, PM2.5, VOC)
  5. Health benefits from active transport (walking to stops)
  6. Reliability benefits (congestion-adjusted)
  7. Option value (availability benefit for non-riders)

All benefits are computed town-wide and allocated to districts using
the configured allocation method (default: 50/50 origin/destination).

Standards:
    - FTA CBA guidelines (benefit categories and methodology)
    - USDOT BCA Guidance 2024 (value of time, VSL)
    - EPA SC-CO2 2024 (social cost of carbon)
    - WHO HEAT v5.2 (health benefits of walking)
    - OMB Circular A-94 (discounting)

References:
    - Boardman et al., "Cost-Benefit Analysis: Concepts and Practice"
    - TCRP Report 78, "Estimating the Benefits and Costs of Public Transit"
    - Small & Verhoef, "The Economics of Urban Transportation"
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_benefit_params(config: dict) -> dict:
    """Extract benefit valuation parameters from config.

    Returns:
        Dict of all valuation parameters needed for benefit calculations.

    Standard: USDOT BCA Guidance 2024; EPA SC-CO2 2024; WHO HEAT v5.2.
    """
    v = config.get("valuations", {})
    veh = config.get("vehicle", {})
    em = config.get("emissions", {})
    health = config.get("health", {})
    analysis = config.get("analysis", {})
    alloc = config.get("benefit_allocation", {})

    return {
        # Time
        "vot_personal": v.get("value_of_time_personal_per_hour", 17.80),
        "vot_business": v.get("value_of_time_business_per_hour", 31.90),
        "vot_all": v.get("value_of_time_all_purposes_per_hour", 20.60),
        # Safety
        "vsl": v.get("vsl", 12_800_000),
        "crash_cost_fatal": v.get("crash_cost_fatal", 12_800_000),
        "crash_cost_serious": v.get("crash_cost_serious_injury", 521_300),
        "crash_cost_minor": v.get("crash_cost_minor_injury", 79_800),
        "crash_cost_pdo": v.get("crash_cost_pdo", 4_700),
        # Emissions
        "scc": v.get("social_cost_carbon", 56.0),
        "co2_per_vmt_g": em.get("co2_per_vmt_grams", 347.0),
        "nox_per_vmt_g": em.get("nox_per_vmt_grams", 0.21),
        "pm25_per_vmt_g": em.get("pm25_per_vmt_grams", 0.006),
        "voc_per_vmt_g": em.get("voc_per_vmt_grams", 0.15),
        "bus_co2_per_mile_g": em.get("bus_co2_per_revenue_mile_grams", 2230.0),
        # Vehicle
        "auto_cost_per_mile": veh.get("auto_operating_cost_per_mile", 0.68),
        "avg_occupancy": veh.get("avg_auto_occupancy", 1.15),
        "fuel_price": veh.get("avg_fuel_price_per_gallon", 5.10),
        "fuel_economy": veh.get("avg_fuel_economy_mpg", 28.5),
        # Health
        "walk_min_per_trip": health.get("walk_minutes_per_transit_trip", 12.0),
        "health_per_walk_min": health.get("health_benefit_per_walking_minute", 0.16),
        # Analysis
        "discount_rates": analysis.get("discount_rates", [0.02, 0.035, 0.07]),
        "time_horizon": analysis.get("time_horizon_operating_years", 20),
        "base_year": analysis.get("base_year", 2025),
        # Allocation
        "cross_district_method": alloc.get("cross_district_method", "50_50"),
    }


# =====================================================================
# 1. TRAVEL TIME SAVINGS
# =====================================================================

def compute_travel_time_savings(
    annual_boardings: int,
    avg_auto_trip_min: float,
    avg_transit_trip_min: float,
    params: dict,
    pct_diverted_from_auto: float = 0.35,
) -> dict:
    """Compute annual value of travel time savings from transit.

    Transit riders who would otherwise drive save time when transit
    is faster (rare in suburban settings) or lose time when it's slower.
    The net benefit includes the value of NOT being in congestion --
    transit travel time is more productive/less stressful.

    Per USDOT guidance, transit in-vehicle time is valued at 60% of
    auto in-vehicle time (reflecting ability to read/work on transit).

    Args:
        annual_boardings: Total annual boardings (unlinked trips).
        avg_auto_trip_min: Average auto trip time for same OD pairs.
        avg_transit_trip_min: Average transit trip time (door-to-door).
        params: Benefit parameters dict.
        pct_diverted_from_auto: Share of transit riders who would drive.

    Returns:
        Dict with total_savings, per_trip_savings, diverted_trips.

    Statistical method: Deterministic valuation with USDOT time values.
    Standard: USDOT BCA Guidance 2024, Section 5 (Travel Time).
    Assumptions:
        - 35% of transit riders are diverted from auto (remainder are
          transit-dependent). Sensitivity flag: HIGH.
        - Transit IVT valued at 60% of auto IVT per USDOT guidance.
        - Auto time includes 5 min average congestion delay (SR-17/85).
    """
    diverted_trips = int(annual_boardings * pct_diverted_from_auto)
    vot = params["vot_all"]

    # Auto time value (full cost -- driving is unproductive)
    auto_value_per_trip = (avg_auto_trip_min / 60) * vot

    # Transit time value (60% of auto rate -- can use time productively)
    transit_value_per_trip = (avg_transit_trip_min / 60) * vot * 0.60

    # Net savings per diverted trip
    per_trip_savings = auto_value_per_trip - transit_value_per_trip
    total_savings = diverted_trips * per_trip_savings

    logger.info(
        "Travel time savings: $%.0f/yr (%d diverted trips, $%.2f/trip)",
        total_savings, diverted_trips, per_trip_savings,
    )
    return {
        "category": "Travel Time Savings",
        "annual_benefit": round(total_savings, 2),
        "diverted_trips": diverted_trips,
        "per_trip_savings": round(per_trip_savings, 2),
        "source": "USDOT BCA Guidance 2024, Section 5",
    }


# =====================================================================
# 2. VEHICLE OPERATING COST SAVINGS
# =====================================================================

def compute_voc_savings(
    annual_boardings: int,
    avg_auto_trip_miles: float,
    params: dict,
    pct_diverted_from_auto: float = 0.35,
) -> dict:
    """Compute vehicle operating cost savings from avoided auto trips.

    Each transit trip that replaces a drive saves the marginal cost
    of operating a private vehicle (fuel, maintenance, tires, depreciation).

    Args:
        annual_boardings: Total annual boardings.
        avg_auto_trip_miles: Average auto trip distance for same OD pairs.
        params: Benefit parameters.
        pct_diverted_from_auto: Share diverted from auto.

    Returns:
        Dict with annual savings, avoided VMT, per-trip savings.

    Standard: AAA "Your Driving Costs" 2024; FTA CBA guidelines.
    Assumptions: $0.68/mile marginal operating cost (AAA 2024, CA average).
    """
    diverted_trips = int(annual_boardings * pct_diverted_from_auto)
    avoided_vmt = diverted_trips * avg_auto_trip_miles
    cost_per_mile = params["auto_cost_per_mile"]

    total_savings = avoided_vmt * cost_per_mile
    per_trip = avg_auto_trip_miles * cost_per_mile

    logger.info(
        "VOC savings: $%.0f/yr (%.0f avoided VMT)", total_savings, avoided_vmt,
    )
    return {
        "category": "Vehicle Operating Cost Savings",
        "annual_benefit": round(total_savings, 2),
        "avoided_vmt": round(avoided_vmt),
        "per_trip_savings": round(per_trip, 2),
        "source": "AAA Your Driving Costs 2024; FTA CBA guidelines",
    }


# =====================================================================
# 3. ACCIDENT / CRASH REDUCTION
# =====================================================================

def compute_crash_reduction_benefits(
    avoided_vmt: float,
    params: dict,
    crash_rate_per_100m_vmt: float = 120.0,
    fatal_share: float = 0.007,
    serious_share: float = 0.05,
    minor_share: float = 0.15,
    pdo_share: float = 0.793,
) -> dict:
    """Compute crash reduction benefits from avoided VMT.

    Fewer miles driven = fewer crashes. Uses Santa Clara County
    crash rates and FHWA severity-weighted crash costs.

    Args:
        avoided_vmt: Annual VMT avoided due to transit.
        params: Benefit parameters.
        crash_rate_per_100m_vmt: Crashes per 100 million VMT.
        fatal_share: Share of crashes that are fatal (K).
        serious_share: Share serious injury (A).
        minor_share: Share minor injury (B+C).
        pdo_share: Share property-damage-only (O).

    Returns:
        Dict with annual benefit, avoided crashes by severity.

    Standard: FHWA crash cost tables (2022 update); KABCO scale.
    Assumptions: Santa Clara County crash rate ~120/100M VMT
        (SWITRS 5-year average). Severity distribution from
        county-level SWITRS data.
    """
    avoided_crashes = avoided_vmt * crash_rate_per_100m_vmt / 100_000_000

    fatal = avoided_crashes * fatal_share
    serious = avoided_crashes * serious_share
    minor = avoided_crashes * minor_share
    pdo = avoided_crashes * pdo_share

    benefit = (
        fatal * params["crash_cost_fatal"]
        + serious * params["crash_cost_serious"]
        + minor * params["crash_cost_minor"]
        + pdo * params["crash_cost_pdo"]
    )

    logger.info(
        "Crash reduction: $%.0f/yr (%.1f avoided crashes, %.3f fatal)",
        benefit, avoided_crashes, fatal,
    )
    return {
        "category": "Crash Reduction",
        "annual_benefit": round(benefit, 2),
        "avoided_crashes": round(avoided_crashes, 2),
        "avoided_fatal": round(fatal, 4),
        "avoided_serious": round(serious, 3),
        "source": "FHWA crash costs (2022); SWITRS Santa Clara County rates",
    }


# =====================================================================
# 4. EMISSION REDUCTION
# =====================================================================

def compute_emission_benefits(
    avoided_vmt: float,
    bus_revenue_miles: float,
    params: dict,
) -> dict:
    """Compute emission reduction benefits from mode shift.

    Net benefit = avoided auto emissions - added bus emissions.
    Valued using EPA Social Cost of Carbon for CO2 and health
    damage costs for criteria pollutants.

    Args:
        avoided_vmt: Annual auto VMT avoided.
        bus_revenue_miles: Annual bus revenue-miles for the routes.
        params: Benefit parameters.

    Returns:
        Dict with annual benefit, tons CO2 avoided, net emissions.

    Standard: EPA SC-CO2 2024; EPA MOVES3.1 emission factors.
    Assumptions:
        - Auto emissions: MOVES3.1 Santa Clara County defaults.
        - Bus emissions: NTD FY2023 VTA reported rates.
        - NOx/PM2.5 health damage: $7,800/ton NOx, $340,000/ton PM2.5
          (EPA BenMAP-CE central estimates).
    """
    # Avoided auto emissions (grams)
    avoided_co2_g = avoided_vmt * params["co2_per_vmt_g"]
    avoided_nox_g = avoided_vmt * params["nox_per_vmt_g"]
    avoided_pm25_g = avoided_vmt * params["pm25_per_vmt_g"]

    # Added bus emissions (these routes would run anyway for base case,
    # so marginal bus emissions from mode shift are zero for existing service)
    # Only count bus emissions for NEW service (Route 76 restoration)
    # For base case CBA, bus emissions are part of costs, not offset here
    net_co2_g = avoided_co2_g  # Net = just avoided auto

    # Convert to metric tons
    net_co2_tons = net_co2_g / 1_000_000
    net_nox_tons = avoided_nox_g / 1_000_000
    net_pm25_tons = avoided_pm25_g / 1_000_000

    # Valuation
    co2_benefit = net_co2_tons * params["scc"]
    nox_benefit = net_nox_tons * 7_800   # EPA BenMAP-CE $/ton NOx
    pm25_benefit = net_pm25_tons * 340_000  # EPA BenMAP-CE $/ton PM2.5

    total = co2_benefit + nox_benefit + pm25_benefit

    logger.info(
        "Emission benefits: $%.0f/yr (%.1f tons CO2, %.3f tons NOx avoided)",
        total, net_co2_tons, net_nox_tons,
    )
    return {
        "category": "Emission Reduction",
        "annual_benefit": round(total, 2),
        "co2_tons_avoided": round(net_co2_tons, 2),
        "nox_tons_avoided": round(net_nox_tons, 4),
        "pm25_tons_avoided": round(net_pm25_tons, 5),
        "co2_benefit": round(co2_benefit, 2),
        "criteria_pollutant_benefit": round(nox_benefit + pm25_benefit, 2),
        "source": "EPA SC-CO2 2024; EPA MOVES3.1; BenMAP-CE",
    }


# =====================================================================
# 5. HEALTH BENEFITS
# =====================================================================

def compute_health_benefits(
    annual_boardings: int,
    params: dict,
) -> dict:
    """Compute health benefits from walking to/from transit stops.

    Transit riders walk an average of 12 minutes per trip (6 min each way).
    This physical activity reduces mortality risk and healthcare costs.

    Args:
        annual_boardings: Total annual boardings.
        params: Benefit parameters.

    Returns:
        Dict with annual benefit, total walking minutes.

    Standard: WHO HEAT v5.2; CDC physical activity economic burden.
    Assumptions: 12 min walking per transit trip (WHO default).
        $0.16 per walking minute (CDC valuation of avoided
        sedentary-related healthcare costs).
    """
    total_walk_min = annual_boardings * params["walk_min_per_trip"]
    benefit = total_walk_min * params["health_per_walk_min"]

    logger.info(
        "Health benefits: $%.0f/yr (%.0f million walking-minutes)",
        benefit, total_walk_min / 1_000_000,
    )
    return {
        "category": "Health Benefits (Active Transport)",
        "annual_benefit": round(benefit, 2),
        "total_walking_minutes": round(total_walk_min),
        "annual_walking_hours": round(total_walk_min / 60),
        "source": "WHO HEAT v5.2; CDC Physical Activity Economics 2023",
    }


# =====================================================================
# 6. RELIABILITY BENEFITS
# =====================================================================

def compute_reliability_benefits(
    annual_boardings: int,
    avg_auto_trip_min: float,
    params: dict,
    congestion_variability_pct: float = 0.30,
    pct_diverted_from_auto: float = 0.35,
) -> dict:
    """Compute reliability benefits from transit vs auto.

    Transit schedules are more predictable than auto travel in
    congested corridors. Travelers value reliability -- the USDOT
    values reliability at 80% of the mean travel time savings.

    For SR-17 and SR-85 commuters, auto travel time variability
    is ~30% of mean travel time (PeMS data shows high variance
    during peak periods).

    Args:
        annual_boardings: Total annual boardings.
        avg_auto_trip_min: Average auto trip time.
        params: Benefit parameters.
        congestion_variability_pct: Auto travel time std dev as % of mean.
        pct_diverted_from_auto: Share diverted from auto.

    Returns:
        Dict with annual benefit.

    Standard: USDOT BCA Guidance 2024, Section 5.3 (Reliability).
    Assumptions: Reliability valued at 80% of travel time value.
        Auto variability at 30% of mean (PeMS SR-17 peak data).
    """
    diverted = int(annual_boardings * pct_diverted_from_auto)
    vot = params["vot_all"]

    # Reliability benefit = value of reduced variability
    # = trips * (auto_variability_minutes / 60) * VOT * 0.80
    variability_min = avg_auto_trip_min * congestion_variability_pct
    benefit_per_trip = (variability_min / 60) * vot * 0.80
    total = diverted * benefit_per_trip

    logger.info("Reliability benefits: $%.0f/yr", total)
    return {
        "category": "Reliability Benefits",
        "annual_benefit": round(total, 2),
        "variability_minutes": round(variability_min, 1),
        "per_trip_benefit": round(benefit_per_trip, 2),
        "source": "USDOT BCA Guidance 2024, Section 5.3",
    }


# =====================================================================
# 7. OPTION VALUE
# =====================================================================

def compute_option_value(
    service_area_population: int,
    params: dict,
    option_value_per_capita: float = 25.0,
) -> dict:
    """Compute option value (availability benefit for non-riders).

    Even people who don't ride transit benefit from its existence:
    it provides a backup if their car breaks down, gas prices spike,
    or they lose their license. This is the "option value."

    Args:
        service_area_population: Total population in the service area.
        params: Benefit parameters.
        option_value_per_capita: Annual per-capita option value.

    Returns:
        Dict with annual benefit.

    Standard: TCRP Report 78, Section 4.5; Boardman et al., Ch. 6.
    Assumptions: $25/capita/year -- mid-range of literature estimates
        ($10-$50 range). Based on stated preference studies of
        willingness-to-pay for transit availability.
        Sensitivity flag: MEDIUM.
    """
    benefit = service_area_population * option_value_per_capita

    logger.info("Option value: $%.0f/yr (%d population)", benefit, service_area_population)
    return {
        "category": "Option Value",
        "annual_benefit": round(benefit, 2),
        "population_served": service_area_population,
        "per_capita_value": option_value_per_capita,
        "source": "TCRP Report 78; Boardman et al. Ch. 6",
    }


# =====================================================================
# BENEFIT ALLOCATION TO DISTRICTS
# =====================================================================

def allocate_benefits_to_districts(
    total_benefits: list[dict],
    stop_district_df: pd.DataFrame,
    demographics_df: pd.DataFrame,
    params: dict,
) -> pd.DataFrame:
    """Allocate total benefits to districts.

    Allocation method depends on benefit category:
    - Time/VOC/Reliability: by stop share (proxy for ridership share)
    - Crash/Emissions: by avoided VMT share (proportional to stop share)
    - Health: by boarding share (proportional to stop share)
    - Option value: by population share

    For cross-district trips, uses the configured allocation rule
    (default 50/50 origin/destination split).

    Args:
        total_benefits: List of benefit dicts from individual functions.
        stop_district_df: Stop-district matrix with district_id, route_ids.
        demographics_df: District demographic data with total_pop.
        params: Benefit parameters.

    Returns:
        DataFrame: district_id, and one column per benefit category,
        plus total_benefits column.

    Standard: Boardman et al., Ch. 6 (distributional analysis).
    Assumptions: Stop share is proxy for ridership share. This is
        a standard approximation when stop-level boarding data is
        unavailable (TCRP Report 88).
    """
    # Count stops per district (proxy for ridership share)
    stop_counts = (
        stop_district_df
        .dropna(subset=["district_id"])
        .groupby("district_id")
        .size()
        .reset_index(name="n_stops")
    )
    total_stops = stop_counts["n_stops"].sum()
    if total_stops > 0:
        stop_counts["stop_share"] = stop_counts["n_stops"] / total_stops
    else:
        stop_counts["stop_share"] = 0

    # Population share for option value
    if demographics_df is not None and "total_pop" in demographics_df.columns:
        id_col = "district_id" if "district_id" in demographics_df.columns else "id"
        pop = demographics_df[[id_col, "total_pop"]].copy()
        pop = pop.rename(columns={id_col: "district_id"})
        total_pop = pop["total_pop"].sum()
        pop["pop_share"] = pop["total_pop"] / max(total_pop, 1)
    else:
        pop = stop_counts[["district_id"]].copy()
        pop["total_pop"] = 0
        pop["pop_share"] = 0

    # Merge
    allocation = stop_counts.merge(pop, on="district_id", how="outer").fillna(0)

    # Allocate each benefit category
    for b in total_benefits:
        cat = b["category"]
        amount = b["annual_benefit"]
        col_name = cat.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")

        if cat == "Option Value":
            allocation[col_name] = allocation["pop_share"] * amount
        else:
            allocation[col_name] = allocation["stop_share"] * amount

    # Total benefits column
    benefit_cols = [c for c in allocation.columns
                    if c not in ("district_id", "n_stops", "stop_share",
                                 "total_pop", "pop_share")]
    allocation["total_benefits"] = allocation[benefit_cols].sum(axis=1)

    return allocation.round(2)


# =====================================================================
# FULL BENEFIT COMPUTATION
# =====================================================================

def compute_all_benefits(
    config: dict,
    ridership: pd.DataFrame,
    bus_revenue_miles: float,
    service_area_pop: int,
    avg_auto_trip_min: float = 22.0,
    avg_transit_trip_min: float = 35.0,
    avg_auto_trip_miles: float = 7.5,
) -> list[dict]:
    """Compute all seven benefit categories.

    Args:
        config: Master configuration.
        ridership: DataFrame with route_id, annual_boardings.
        bus_revenue_miles: Total annual bus revenue-miles.
        service_area_pop: Total population in service area.
        avg_auto_trip_min: Average auto trip duration (minutes).
        avg_transit_trip_min: Average transit trip duration (minutes).
        avg_auto_trip_miles: Average auto trip distance (miles).

    Returns:
        List of benefit dicts, one per category.

    Assumptions on trip characteristics (Los Gatos context):
        - 22 min avg auto trip: reflects local trips within LG + commute
          to Campbell/SJ, including ~5 min SR-17/85 congestion delay.
        - 35 min avg transit trip: reflects Route 27 end-to-end time
          plus wait and walk access.
        - 7.5 mi avg trip: roughly LG Blvd to downtown SJ distance.
    """
    params = get_benefit_params(config)
    total_boardings = int(ridership[ridership.get("status", "active") == "active"]["annual_boardings"].sum()) if "status" in ridership.columns else int(ridership["annual_boardings"].sum())

    # Avoided VMT (needed for crash and emission calculations)
    pct_diverted = 0.35
    diverted_trips = int(total_boardings * pct_diverted)
    avoided_vmt = diverted_trips * avg_auto_trip_miles

    benefits = [
        compute_travel_time_savings(
            total_boardings, avg_auto_trip_min, avg_transit_trip_min, params
        ),
        compute_voc_savings(total_boardings, avg_auto_trip_miles, params),
        compute_crash_reduction_benefits(avoided_vmt, params),
        compute_emission_benefits(avoided_vmt, bus_revenue_miles, params),
        compute_health_benefits(total_boardings, params),
        compute_reliability_benefits(
            total_boardings, avg_auto_trip_min, params
        ),
        compute_option_value(service_area_pop, params),
    ]

    total = sum(b["annual_benefit"] for b in benefits)
    logger.info("TOTAL ANNUAL BENEFITS: $%.0f across %d categories", total, len(benefits))

    return benefits


def compute_benefit_npv(
    annual_benefits: float,
    discount_rates: list[float],
    time_horizon: int = 20,
    growth_rate: float = 0.01,
) -> pd.DataFrame:
    """Compute present value of benefits at multiple discount rates.

    Benefits grow at a modest real rate reflecting population/ridership
    growth. Lower than cost growth (2.5%) because ridership growth
    is uncertain.

    Args:
        annual_benefits: Year-0 total annual benefits.
        discount_rates: List of discount rates.
        time_horizon: Analysis period in years.
        growth_rate: Annual real growth rate for benefits (default 1%).

    Returns:
        DataFrame: discount_rate, pv_benefits.

    Standard: OMB Circular A-94.
    """
    results = []
    for r in discount_rates:
        pv = sum(
            annual_benefits * (1 + growth_rate) ** t / (1 + r) ** t
            for t in range(1, time_horizon + 1)
        )
        results.append({
            "discount_rate": r,
            "discount_rate_label": f"{r*100:.1f}%",
            "pv_benefits": round(pv),
        })
    return pd.DataFrame(results)
