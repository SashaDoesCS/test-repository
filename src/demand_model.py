"""
demand_model.py -- Transit demand index, equity scoring, and unmet need.

Computes for each district:
  1. Transit Demand Index (TDI) -- composite score of need factors
  2. Service Level Index (SLI) -- composite score of current service
  3. Unmet Need Score -- gap between demand and service
  4. Equity Flag -- districts with high need and low service
  5. Coverage Gap -- % of district beyond 0.5mi walk of a stop

The demand index methodology follows TCRP Report 167 "Making Effective
Fixed-Route Transit Improvements" which identifies population density,
employment density, zero-vehicle households, transit commute share,
income (inverse), and age as key demand predictors.

Standards:
    - TCRP Report 167 (demand factor identification)
    - FTA Title VI guidance (equity analysis)
    - APTA "Transit Ridership Report" methodology

References:
    - Pushkarev & Zupan, "Public Transportation and Land Use Policy"
    - Taylor et al., "Nature of and Factors Contributing to Transit
      Ridership," TRR 2009
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# =====================================================================
# TRANSIT DEMAND INDEX
# =====================================================================

def compute_transit_demand_index(
    demographics: pd.DataFrame,
    weights: Optional[dict] = None,
) -> pd.DataFrame:
    """Compute a composite Transit Demand Index for each district.

    The TDI combines six demand-side factors, each normalized to 0-1
    and weighted. Higher TDI = more potential demand for transit.

    Factors and default weights (from TCRP Report 167):
        - Population density (0.25) -- primary ridership driver
        - Zero-vehicle HH rate (0.20) -- transit-dependent population
        - Transit commute share (0.15) -- revealed preference
        - Income inverse (0.15) -- lower income = higher need
        - Youth/senior share (0.10) -- age groups more transit-dependent
        - Employment proximity (0.15) -- commute demand (proxy: density)

    Args:
        demographics: DataFrame with district_id, total_pop,
            pop_density_per_sq_mi, zero_veh_rate, transit_share,
            mean_income.
        weights: Optional dict overriding default factor weights.

    Returns:
        DataFrame with district_id and TDI components + composite score.

    Standard: TCRP Report 167, Chapter 3 (Demand Factors).
    """
    if weights is None:
        weights = {
            "pop_density": 0.25,
            "zero_veh": 0.20,
            "transit_share": 0.15,
            "income_inverse": 0.15,
            "age_dependent": 0.10,
            "employment_proxy": 0.15,
        }

    df = demographics.copy()
    did_col = "district_id" if "district_id" in df.columns else "id"

    # Normalize each factor to 0-1 using min-max scaling
    def normalize(series):
        s = pd.to_numeric(series, errors="coerce").fillna(0)
        mn, mx = s.min(), s.max()
        if mx == mn:
            return pd.Series(0.5, index=s.index)
        return (s - mn) / (mx - mn)

    result = pd.DataFrame()
    result["district_id"] = df[did_col]

    # Factor 1: Population density
    result["f_pop_density"] = normalize(df["pop_density_per_sq_mi"])

    # Factor 2: Zero-vehicle HH rate
    zvr_col = "zero_veh_rate" if "zero_veh_rate" in df.columns else None
    if zvr_col:
        result["f_zero_veh"] = normalize(df[zvr_col])
    else:
        result["f_zero_veh"] = 0.5

    # Factor 3: Transit commute share (revealed demand)
    ts_col = "transit_share" if "transit_share" in df.columns else None
    if ts_col:
        result["f_transit_share"] = normalize(df[ts_col])
    else:
        result["f_transit_share"] = 0.5

    # Factor 4: Income inverse (lower income = higher need)
    if "mean_income" in df.columns:
        incomes = pd.to_numeric(df["mean_income"], errors="coerce").fillna(0)
        max_inc = incomes[incomes > 0].max() if (incomes > 0).any() else 1
        # Invert: high income -> low score
        result["f_income_inverse"] = normalize(max_inc - incomes.clip(lower=0))
    else:
        result["f_income_inverse"] = 0.5

    # Factor 5: Age-dependent population (youth + senior as % of total)
    if "pop_under_18" in df.columns and "pop_65_plus" in df.columns:
        total = pd.to_numeric(df["total_pop"], errors="coerce").clip(lower=1)
        under18 = pd.to_numeric(df.get("pop_under_18", 0), errors="coerce").fillna(0)
        over65 = pd.to_numeric(df.get("pop_65_plus", 0), errors="coerce").fillna(0)
        age_share = (under18 + over65) / total
        result["f_age_dependent"] = normalize(age_share)
    else:
        result["f_age_dependent"] = 0.5

    # Factor 6: Employment proximity (proxy: use density as stand-in)
    # Ideally from LEHD LODES data; density is a reasonable proxy
    result["f_employment_proxy"] = result["f_pop_density"] * 0.8 + 0.1

    # Composite TDI
    result["tdi"] = (
        result["f_pop_density"] * weights["pop_density"]
        + result["f_zero_veh"] * weights["zero_veh"]
        + result["f_transit_share"] * weights["transit_share"]
        + result["f_income_inverse"] * weights["income_inverse"]
        + result["f_age_dependent"] * weights["age_dependent"]
        + result["f_employment_proxy"] * weights["employment_proxy"]
    ).round(4)

    # Rank
    result["tdi_rank"] = result["tdi"].rank(ascending=False).astype(int)

    logger.info("Transit Demand Index computed for %d districts", len(result))
    return result


# =====================================================================
# SERVICE LEVEL INDEX
# =====================================================================

def compute_service_level_index(
    cost_summary: pd.DataFrame,
    demographics: pd.DataFrame,
    stop_matrix: pd.DataFrame,
) -> pd.DataFrame:
    """Compute a Service Level Index for each district.

    The SLI measures how much transit service currently exists.
    Higher SLI = more service. Combines:
        - Stops per sq mi (stop density)
        - Routes serving district
        - Annual service cost allocated (proxy for revenue-hours)
        - Stops per 1000 population

    Args:
        cost_summary: District cost summary with n_stops, n_routes, total_annual_cost.
        demographics: District demographics with total_pop, area_sq_miles.
        stop_matrix: Stop-district assignments.

    Returns:
        DataFrame with district_id and SLI components + composite score.
    """
    did_col = "district_id"

    # Build service metrics
    service = demographics[["district_id", "total_pop", "area_sq_miles"]].copy() if "district_id" in demographics.columns else demographics[["id", "total_pop", "area_sq_miles"]].copy().rename(columns={"id": "district_id"})

    # Merge cost/stop data
    if "district_id" in cost_summary.columns:
        cs = cost_summary[["district_id", "n_stops", "n_routes", "total_annual_cost"]].copy()
    else:
        cs = pd.DataFrame({"district_id": service["district_id"], "n_stops": 0, "n_routes": 0, "total_annual_cost": 0})

    service = service.merge(cs, on="district_id", how="left").fillna(0)

    def normalize(series):
        s = pd.to_numeric(series, errors="coerce").fillna(0)
        mn, mx = s.min(), s.max()
        if mx == mn:
            return pd.Series(0.0 if mx == 0 else 0.5, index=s.index)
        return (s - mn) / (mx - mn)

    result = pd.DataFrame()
    result["district_id"] = service["district_id"]

    # Stop density (stops per sq mi)
    service["stops_per_sqmi"] = service["n_stops"] / service["area_sq_miles"].clip(lower=0.01)
    result["s_stop_density"] = normalize(service["stops_per_sqmi"])

    # Route count
    result["s_route_count"] = normalize(service["n_routes"])

    # Service investment per capita
    service["cost_per_capita"] = service["total_annual_cost"] / service["total_pop"].clip(lower=1)
    result["s_cost_per_capita"] = normalize(service["cost_per_capita"])

    # Stops per 1000 pop
    service["stops_per_1000"] = service["n_stops"] / (service["total_pop"].clip(lower=1) / 1000)
    result["s_stops_per_1000"] = normalize(service["stops_per_1000"])

    # Composite SLI
    result["sli"] = (
        result["s_stop_density"] * 0.30
        + result["s_route_count"] * 0.25
        + result["s_cost_per_capita"] * 0.25
        + result["s_stops_per_1000"] * 0.20
    ).round(4)

    result["sli_rank"] = result["sli"].rank(ascending=False).astype(int)
    result["n_stops"] = service["n_stops"].astype(int)

    logger.info("Service Level Index computed for %d districts", len(result))
    return result


# =====================================================================
# UNMET NEED & EQUITY
# =====================================================================

def compute_unmet_need(
    tdi: pd.DataFrame,
    sli: pd.DataFrame,
    equity_threshold: float = 0.6,
) -> pd.DataFrame:
    """Compute Unmet Need Score and Equity Flags.

    Unmet Need = TDI - SLI (normalized to 0-1 range).
    High unmet need means high demand but low service.

    Districts are flagged as "equity priority" when:
        - TDI > equity_threshold (high need)
        - SLI < (1 - equity_threshold) (low service)
        OR
        - Unmet need score in top 3

    Args:
        tdi: Transit Demand Index DataFrame.
        sli: Service Level Index DataFrame.
        equity_threshold: Threshold for equity flagging (default 0.6).

    Returns:
        DataFrame with district_id, tdi, sli, unmet_need, equity_flag,
        unmet_need_rank.

    Standard: FTA Title VI Equity Analysis guidance.
    """
    merged = tdi[["district_id", "tdi", "tdi_rank"]].merge(
        sli[["district_id", "sli", "sli_rank", "n_stops"]], on="district_id", how="outer"
    ).fillna(0)

    # Unmet need = demand minus service (clipped to 0-1)
    merged["unmet_need"] = (merged["tdi"] - merged["sli"]).clip(lower=0).round(4)
    merged["unmet_need_rank"] = merged["unmet_need"].rank(ascending=False).astype(int)

    # Equity flags
    top3 = merged.nlargest(3, "unmet_need")["district_id"].tolist()
    merged["equity_flag"] = merged.apply(
        lambda r: (
            "PRIORITY" if (r["tdi"] > equity_threshold and r["sli"] < (1 - equity_threshold))
            or r["district_id"] in top3
            else ("WATCH" if r["tdi"] > 0.4 and r["sli"] < 0.3 else "OK")
        ),
        axis=1,
    )

    # Service gap description
    def gap_desc(row):
        if row["n_stops"] == 0:
            return "NO SERVICE"
        elif row["unmet_need"] > 0.4:
            return "SEVERE GAP"
        elif row["unmet_need"] > 0.2:
            return "MODERATE GAP"
        else:
            return "ADEQUATE"

    merged["service_gap"] = merged.apply(gap_desc, axis=1)

    logger.info("Unmet need computed: %d PRIORITY, %d WATCH, %d OK",
               (merged["equity_flag"] == "PRIORITY").sum(),
               (merged["equity_flag"] == "WATCH").sum(),
               (merged["equity_flag"] == "OK").sum())

    return merged


def compute_coverage_gaps(
    demographics: pd.DataFrame,
    stop_matrix: pd.DataFrame,
    walk_buffer_miles: float = 0.5,
) -> pd.DataFrame:
    """Estimate transit coverage gaps by district.

    A coverage gap is the population beyond walking distance (0.5 mi)
    of any transit stop. Without detailed road network data, we
    estimate using stop density as a proxy:
        - Each stop "covers" a circle of radius walk_buffer_miles
        - Coverage area = n_stops * pi * r^2
        - Coverage fraction = min(1.0, coverage_area / district_area)
        - Gap population = (1 - coverage_fraction) * total_pop

    Args:
        demographics: District demographics.
        stop_matrix: Stop-district assignments.
        walk_buffer_miles: Walk access radius (default 0.5 mi / 10 min).

    Returns:
        DataFrame with coverage metrics per district.

    Standard: FTA guidance: 0.5 mi (10-min walk) as transit access threshold.
    """
    did_col = "district_id" if "district_id" in demographics.columns else "id"

    # Count stops per district
    if "district_id" in stop_matrix.columns:
        stop_counts = (
            stop_matrix.dropna(subset=["district_id"])
            .groupby("district_id")
            .size()
            .reset_index(name="n_stops")
        )
    else:
        stop_counts = pd.DataFrame({"district_id": [], "n_stops": []})

    result = demographics[[did_col, "total_pop", "area_sq_miles"]].copy()
    if did_col != "district_id":
        result = result.rename(columns={did_col: "district_id"})

    result = result.merge(stop_counts, on="district_id", how="left").fillna(0)

    # Coverage estimation
    stop_coverage_area = np.pi * walk_buffer_miles ** 2  # ~0.785 sq mi per stop
    result["coverage_area_sqmi"] = (result["n_stops"] * stop_coverage_area).round(2)
    result["coverage_fraction"] = (
        result["coverage_area_sqmi"] / result["area_sq_miles"].clip(lower=0.01)
    ).clip(upper=1.0).round(3)
    result["gap_fraction"] = (1.0 - result["coverage_fraction"]).round(3)
    result["gap_population"] = (result["gap_fraction"] * result["total_pop"]).round(0).astype(int)

    total_gap_pop = result["gap_population"].sum()
    total_pop = result["total_pop"].sum()
    logger.info("Coverage gaps: %d people (%.1f%%) beyond %.1f-mi walk of transit",
               total_gap_pop, 100 * total_gap_pop / max(total_pop, 1), walk_buffer_miles)

    return result


# =====================================================================
# FULL DEMAND ANALYSIS
# =====================================================================

def run_demand_analysis(
    demographics: pd.DataFrame,
    cost_summary: pd.DataFrame,
    stop_matrix: pd.DataFrame,
) -> dict:
    """Run the complete demand analysis pipeline.

    Returns:
        Dict with keys: tdi, sli, unmet_need, coverage.
    """
    tdi = compute_transit_demand_index(demographics)
    sli = compute_service_level_index(cost_summary, demographics, stop_matrix)
    unmet = compute_unmet_need(tdi, sli)
    coverage = compute_coverage_gaps(demographics, stop_matrix)

    return {
        "tdi": tdi,
        "sli": sli,
        "unmet_need": unmet,
        "coverage": coverage,
    }
