"""
scenarios.py -- Scenario definitions for the CBA.

Three scenarios:
  1. Conservative (base case) -- closest to real-world current state
  2. Moderate -- adds survey-measured latent demand from Union SD
  3. Optimistic -- best defensible case from literature

Each scenario modifies key parameters that the benefit model uses.
All parameter changes are documented with citations.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Scenario:
    """A named set of parameter overrides for the CBA."""
    name: str
    description: str
    # Ridership & diversion
    pct_diverted_from_auto: float      # Share of riders who would otherwise drive
    ridership_growth_rate: float        # Annual real growth in ridership
    additional_union_boardings: int     # Added boardings from survey-measured latent demand
    # Trip characteristics
    avg_auto_trip_min: float
    avg_transit_trip_min: float
    avg_auto_trip_miles: float
    congestion_variability_pct: float   # For reliability benefit
    # Valuations
    option_value_per_capita: float
    benefit_growth_rate: float          # Annual real growth in benefits
    cost_growth_rate: float             # Annual real growth in costs
    # Capital
    capital_per_stop: float             # Existing stop infrastructure value
    vehicle_capital_study_share: float  # Study area share of fleet value
    # Additional benefits (not in base model)
    include_property_value: bool
    property_value_uplift_pct: float    # % increase in home values near stops
    include_school_access: bool
    school_access_value_per_student: float
    n_school_students_served: int
    # Sources
    sources: dict = field(default_factory=dict)


def get_conservative_scenario() -> Scenario:
    """Conservative base case -- closest to real-world current state.

    Changes from previous default:
    - Diversion rate raised to 55% (from 35%) -- in an affluent area with
      96% car ownership, nearly all riders are choice riders who own cars.
      The 35% was a national average; Los Gatos is not average.
    - Capital uses existing infrastructure value ($30K/stop), not full
      buildout. Vehicle capital uses marginal share (0.5% vs 1.5%).
    - Congestion variability from observed schedule data (mean absolute
      deviation of ~2.2 minutes on 22-min trip = ~10%, not 30%).
    - Benefit growth at 0.5% (conservative, just background pop growth).
    - Cost growth at 2.0% (still Bay Area labor inflation, but lower bound).
    """
    return Scenario(
        name="Conservative",
        description="Real-world base case with corrected parameters",
        pct_diverted_from_auto=0.55,
        ridership_growth_rate=0.005,
        additional_union_boardings=0,
        avg_auto_trip_min=22.0,
        avg_transit_trip_min=35.0,
        avg_auto_trip_miles=7.5,
        congestion_variability_pct=0.10,
        option_value_per_capita=20.0,
        benefit_growth_rate=0.005,
        cost_growth_rate=0.020,
        capital_per_stop=30_000,
        vehicle_capital_study_share=0.005,
        include_property_value=False,
        property_value_uplift_pct=0,
        include_school_access=False,
        school_access_value_per_student=0,
        n_school_students_served=0,
        sources={
            "diversion_rate": "Los Gatos ACS: 96% car ownership -> ~55% choice riders (Taylor et al. TRR 2009)",
            "congestion": "Observed schedule data: MAD = 2.2 min / 22 min avg trip = 10%",
            "option_value": "$20/capita -- lower bound from Boardman et al. Ch.6 ($10-50 range)",
            "capital": "$30K/stop -- existing VTA stop infrastructure (sign + bench, no shelter)",
            "cost_growth": "2.0% -- BLS CPI-U West Urban, trailing 3-year",
        },
    )


def get_moderate_scenario() -> Scenario:
    """Moderate case -- adds survey-measured demand and school access.

    Changes from conservative:
    - Adds Union SD survey respondents who said 'Yes' to riding bus
      (applied only to Union zone districts U1-U6).
    - Includes school access value for students at Fisher, Union MS,
      LGHS, and Leigh who depend on or would use transit.
    - Congestion variability uses full observed range (some stops 4+ min late).
    - Option value at $30/capita (mid-range of literature).
    - Benefit growth at 1.5% (VTA system ridership trend pre-COVID).
    """
    return Scenario(
        name="Moderate",
        description="Survey-informed with school access benefits",
        pct_diverted_from_auto=0.60,
        ridership_growth_rate=0.015,
        additional_union_boardings=0,  # Will be computed from survey data
        avg_auto_trip_min=22.0,
        avg_transit_trip_min=32.0,  # Slightly faster with service optimization
        avg_auto_trip_miles=7.5,
        congestion_variability_pct=0.18,  # Observed: worst stops avg 4+ min on 22 min trip
        option_value_per_capita=30.0,
        benefit_growth_rate=0.015,
        cost_growth_rate=0.025,
        capital_per_stop=30_000,
        vehicle_capital_study_share=0.005,
        include_property_value=False,
        property_value_uplift_pct=0,
        include_school_access=True,
        school_access_value_per_student=2_500,
        n_school_students_served=350,
        sources={
            "diversion_rate": "60% -- survey shows most non-riders have cars (choice riders)",
            "additional_boardings": "Union SD survey: 'Yes' respondents x 2 trips/day x 180 school days",
            "school_access": "$2,500/student/yr -- avoided parent driving time + safety (TCRP 78)",
            "n_students": "~350 students at Fisher, Union MS, LGHS who would use if available",
            "option_value": "$30/capita -- mid-range of stated-preference studies",
        },
    )


def get_optimistic_scenario() -> Scenario:
    """Optimistic case -- best defensible case from peer-reviewed literature.

    Changes from moderate:
    - Diversion at 70% (upper bound for high-income suburban areas).
    - Includes property value uplift (2% for homes within 0.5mi of stops).
    - Higher option value ($40/capita) and school access value.
    - Benefit growth at 2.5% (transit-oriented development scenario).
    - Faster transit (assumes signal priority, stop consolidation).
    """
    return Scenario(
        name="Optimistic",
        description="Best defensible case with property value and full demand",
        pct_diverted_from_auto=0.70,
        ridership_growth_rate=0.025,
        additional_union_boardings=0,  # Will be computed from survey
        avg_auto_trip_min=25.0,  # Higher congestion assumed
        avg_transit_trip_min=30.0,  # Service improvements
        avg_auto_trip_miles=8.0,
        congestion_variability_pct=0.25,  # SR-17 peak variability
        option_value_per_capita=40.0,
        benefit_growth_rate=0.025,
        cost_growth_rate=0.025,
        capital_per_stop=30_000,
        vehicle_capital_study_share=0.005,
        include_property_value=True,
        property_value_uplift_pct=0.02,
        include_school_access=True,
        school_access_value_per_student=3_000,
        n_school_students_served=500,
        sources={
            "diversion_rate": "70% -- upper bound, Cervero & Kockelman 1997",
            "property_value": "2% uplift within 0.5mi -- APTA meta-analysis 2019",
            "school_access": "$3,000/student -- includes full avoided VMT + parent time",
            "option_value": "$40/capita -- upper mid-range from Boardman et al.",
            "benefit_growth": "2.5% -- assumes TOD and service improvements",
        },
    )


def compute_scenario_benefits(
    scenario: Scenario,
    base_boardings: int,
    bus_revenue_miles: float,
    service_area_pop: int,
    union_pop: int = 0,
    median_home_value: float = 1_800_000,
    n_homes_near_stops: int = 2_500,
) -> dict:
    """Compute all benefits under a given scenario.

    Args:
        scenario: Scenario parameter set.
        base_boardings: Current annual boardings (active routes).
        bus_revenue_miles: Total annual bus revenue-miles.
        service_area_pop: Total population in service area.
        union_pop: Population in Union zone (for survey-based demand).
        median_home_value: Median home value near transit stops.
        n_homes_near_stops: Homes within 0.5mi of a transit stop.

    Returns:
        Dict with per-category annual benefits and total.
    """
    # Adjust boardings for additional demand
    total_boardings = base_boardings + scenario.additional_union_boardings
    diverted = int(total_boardings * scenario.pct_diverted_from_auto)
    avoided_vmt = diverted * scenario.avg_auto_trip_miles

    from src.benefit_model import (
        compute_travel_time_savings, compute_voc_savings,
        compute_crash_reduction_benefits, compute_emission_benefits,
        compute_health_benefits, compute_reliability_benefits,
        compute_option_value, get_benefit_params,
    )

    # Build params with scenario overrides
    # (load from config, then override option value)
    from src.benefit_model import load_config
    config = load_config()
    params = get_benefit_params(config)

    benefits = []

    # Standard 7 categories with scenario parameters
    benefits.append(compute_travel_time_savings(
        total_boardings, scenario.avg_auto_trip_min,
        scenario.avg_transit_trip_min, params,
        pct_diverted_from_auto=scenario.pct_diverted_from_auto,
    ))
    benefits.append(compute_voc_savings(
        total_boardings, scenario.avg_auto_trip_miles, params,
        pct_diverted_from_auto=scenario.pct_diverted_from_auto,
    ))
    benefits.append(compute_crash_reduction_benefits(avoided_vmt, params))
    benefits.append(compute_emission_benefits(avoided_vmt, bus_revenue_miles, params))
    benefits.append(compute_health_benefits(total_boardings, params))
    benefits.append(compute_reliability_benefits(
        total_boardings, scenario.avg_auto_trip_min, params,
        congestion_variability_pct=scenario.congestion_variability_pct,
        pct_diverted_from_auto=scenario.pct_diverted_from_auto,
    ))
    benefits.append(compute_option_value(
        service_area_pop, params,
        option_value_per_capita=scenario.option_value_per_capita,
    ))

    # Additional benefits
    if scenario.include_school_access:
        school_benefit = scenario.school_access_value_per_student * scenario.n_school_students_served
        benefits.append({
            "category": "School Access Value",
            "annual_benefit": round(school_benefit, 2),
            "n_students": scenario.n_school_students_served,
            "per_student": scenario.school_access_value_per_student,
            "source": scenario.sources.get("school_access", "TCRP Report 78"),
        })

    if scenario.include_property_value:
        prop_benefit = n_homes_near_stops * median_home_value * scenario.property_value_uplift_pct / 20
        benefits.append({
            "category": "Property Value Uplift (annualized)",
            "annual_benefit": round(prop_benefit, 2),
            "n_homes": n_homes_near_stops,
            "uplift_pct": scenario.property_value_uplift_pct,
            "source": scenario.sources.get("property_value", "APTA 2019"),
        })

    total = sum(b["annual_benefit"] for b in benefits)

    return {
        "scenario": scenario.name,
        "total_boardings": total_boardings,
        "diverted_trips": diverted,
        "avoided_vmt": round(avoided_vmt),
        "annual_benefits": benefits,
        "total_annual_benefit": round(total, 2),
    }


def compute_scenario_costs(
    scenario: Scenario,
    route_costs_df,
    n_study_area_stops: int,
    allocated_annual_operating: float,
) -> dict:
    """Compute costs under a given scenario.

    Args:
        scenario: Scenario parameter set.
        route_costs_df: Route-level cost DataFrame.
        n_study_area_stops: Number of stops in study area.
        allocated_annual_operating: Already-allocated operating cost.

    Returns:
        Dict with annual cost, capital, and growth rate.
    """
    capital = n_study_area_stops * scenario.capital_per_stop
    fleet_value = 500 * 550_000  # VTA fleet
    vehicle_capital = fleet_value * scenario.vehicle_capital_study_share
    total_capital = capital + vehicle_capital

    return {
        "scenario": scenario.name,
        "annual_operating": round(allocated_annual_operating),
        "stop_capital": round(capital),
        "vehicle_capital": round(vehicle_capital),
        "total_capital": round(total_capital),
        "cost_growth_rate": scenario.cost_growth_rate,
    }


def run_scenario_comparison(
    base_boardings: int,
    bus_revenue_miles: float,
    service_area_pop: int,
    union_pop: int,
    allocated_annual_operating: float,
    n_study_area_stops: int,
    route_costs_df,
    survey_yes_count: int = 0,
    discount_rate: float = 0.035,
    time_horizon: int = 20,
) -> list[dict]:
    """Run all three scenarios and compare.

    Args:
        base_boardings: Current annual boardings.
        bus_revenue_miles: Annual bus revenue-miles.
        service_area_pop: Total service area population.
        union_pop: Union zone population.
        allocated_annual_operating: Study-area allocated operating cost.
        n_study_area_stops: Number of stops in study area.
        route_costs_df: Route cost DataFrame.
        survey_yes_count: Number of survey respondents who said "Yes" to riding.
        discount_rate: Discount rate for NPV comparison.
        time_horizon: Analysis period.

    Returns:
        List of dicts with scenario comparison results.
    """
    scenarios = [
        get_conservative_scenario(),
        get_moderate_scenario(),
        get_optimistic_scenario(),
    ]

    # Apply survey data to moderate and optimistic scenarios
    if survey_yes_count > 0:
        # Each "Yes" student = 2 trips/day x 180 school days = 360 annual boardings
        additional = survey_yes_count * 360
        scenarios[1].additional_union_boardings = additional
        scenarios[2].additional_union_boardings = int(additional * 1.5)  # Optimistic adds "Maybe" respondents
        logger.info("Survey-based additional boardings: Moderate=%d, Optimistic=%d",
                    additional, int(additional * 1.5))

    results = []
    for s in scenarios:
        ben = compute_scenario_benefits(
            s, base_boardings, bus_revenue_miles,
            service_area_pop, union_pop,
        )
        cost = compute_scenario_costs(
            s, route_costs_df, n_study_area_stops,
            allocated_annual_operating,
        )

        # NPV
        pv_benefits = sum(
            ben["total_annual_benefit"] * (1 + s.benefit_growth_rate) ** t / (1 + discount_rate) ** t
            for t in range(1, time_horizon + 1)
        )
        pv_operating = sum(
            cost["annual_operating"] * (1 + s.cost_growth_rate) ** t / (1 + discount_rate) ** t
            for t in range(1, time_horizon + 1)
        )
        pv_costs = pv_operating + cost["total_capital"]
        bcr = pv_benefits / max(pv_costs, 1)

        results.append({
            "scenario": s.name,
            "description": s.description,
            "annual_benefits": round(ben["total_annual_benefit"]),
            "annual_costs": cost["annual_operating"],
            "total_capital": cost["total_capital"],
            "pv_benefits": round(pv_benefits),
            "pv_costs": round(pv_costs),
            "bcr": round(bcr, 3),
            "net_pv": round(pv_benefits - pv_costs),
            "total_boardings": ben["total_boardings"],
            "diverted_trips": ben["diverted_trips"],
            "avoided_vmt": ben["avoided_vmt"],
            "benefit_categories": ben["annual_benefits"],
            "key_params": {
                "diversion_rate": s.pct_diverted_from_auto,
                "option_value_per_capita": s.option_value_per_capita,
                "benefit_growth": s.benefit_growth_rate,
                "cost_growth": s.cost_growth_rate,
            },
            "sources": s.sources,
        })

    return results
