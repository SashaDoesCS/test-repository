"""
demand_matrix.py -- Origin-Destination demand matrix for routing optimization.

Builds a demand matrix that drives route scoring and stop selection.
Combines:
  - Transit Demand Index (TDI) per district (from demand_model.py)
  - Student survey modal diversion potential
  - Census demographics
  - Time-of-day profiles including school dismissal windows

The O-D matrix uses a gravity model with negative exponential distance decay,
calibrated to ~5 km half-life (appropriate for Los Gatos suburban scale).

Standards:
    - TCRP Report 95 "Traveler Response to Transportation System Changes"
    - FTA Title VI equity analysis framework
    - Four-step demand model (simplified: generation + distribution)
"""

import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Distance decay parameter (β): exp(-β * km).
# At β=0.3, half of demand is within ~2.3 km -- appropriate for walkable suburb.
_DECAY_BETA = 0.3

# Time-of-day demand multipliers (relative to daily average = 1.0)
_TOD_MULTIPLIERS = {
    "am_peak":    1.6,
    "midday":     0.7,
    "pm_school":  1.4,
    "pm_commute": 1.5,
    "evening":    0.5,
}


# =====================================================================
# HAVERSINE DISTANCE
# =====================================================================

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in kilometres between two WGS84 points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# =====================================================================
# DISTRICT CENTROID HELPER
# =====================================================================

def _load_district_centroids(config: dict) -> pd.DataFrame:
    """Extract district centroids from config district definitions.

    Returns DataFrame with columns: district_id, zone, lat, lon.
    Centroid is the mean of polygon vertices (same method as districts.py).
    """
    rows = []
    for zone_key in ("districts_lghs", "districts_union"):
        zone = "LGHS" if zone_key == "districts_lghs" else "UNION"
        for d in config.get(zone_key, []):
            # Centroid is mean of boundary polygon vertices
            # The actual coords live in districts.py hardcoded; here we use
            # approximate centroids from known district descriptions.
            # Fallback centroids are derived from Los Gatos geographic knowledge.
            rows.append({
                "district_id": d["id"],
                "zone": zone,
                "name": d.get("name", ""),
            })
    return pd.DataFrame(rows)


# =====================================================================
# O-D DEMAND MATRIX
# =====================================================================

def build_od_matrix(
    tdi_df: pd.DataFrame,
    demographics_df: pd.DataFrame,
    district_centroids: pd.DataFrame,
    school_windows: Optional[list] = None,
) -> pd.DataFrame:
    """Build an Origin-Destination demand matrix across all districts.

    Uses a gravity model:
        demand(i→j) = production(i) × attraction(j) × exp(-β × dist(i,j))

    where:
        production(i)  = TDI score × population of origin district i
        attraction(j)  = TDI score × employment proxy of destination j
        dist(i,j)      = haversine distance between district centroids

    Args:
        tdi_df: DataFrame with district_id, tdi (score 0-1), and optionally
            tdi_rank. From compute_transit_demand_index().
        demographics_df: DataFrame with district_id, total_pop, mean_income,
            area_sq_miles (for density proxy).
        district_centroids: DataFrame with district_id, lat, lon.
        school_windows: List of school window dicts from config
            optimization.school_windows.

    Returns:
        DataFrame with columns: origin_district, destination_district,
            distance_km, daily_demand, am_peak, midday, pm_school,
            pm_commute, evening.
    """
    if district_centroids is None or len(district_centroids) == 0:
        logger.warning("No district centroids provided; returning empty O-D matrix")
        return pd.DataFrame()

    # Merge TDI and demographics onto centroids
    tdi_indexed = tdi_df.set_index("district_id")[["tdi"]]
    demo_indexed = demographics_df.set_index("district_id")[
        [c for c in ["total_pop", "pop_density_per_sq_mi", "area_sq_miles"]
         if c in demographics_df.columns]
    ] if demographics_df is not None else pd.DataFrame()

    centroids = district_centroids.copy()
    centroids = centroids.join(tdi_indexed, on="district_id", how="left")
    centroids["tdi"] = centroids["tdi"].fillna(0.3)  # neutral fallback

    if not demo_indexed.empty:
        centroids = centroids.join(demo_indexed, on="district_id", how="left")
        centroids["total_pop"] = centroids.get("total_pop", pd.Series(dtype=float)).fillna(1000)
    else:
        centroids["total_pop"] = 1000

    # Build O-D pairs
    rows = []
    district_ids = centroids["district_id"].tolist()
    centroid_map = centroids.set_index("district_id")

    for orig in district_ids:
        orig_row = centroid_map.loc[orig]
        orig_lat = orig_row.get("lat", 0.0)
        orig_lon = orig_row.get("lon", 0.0)
        orig_pop = float(orig_row.get("total_pop", 1000))
        orig_tdi = float(orig_row.get("tdi", 0.3))
        production = orig_pop * orig_tdi

        for dest in district_ids:
            if orig == dest:
                continue
            dest_row = centroid_map.loc[dest]
            dest_lat = dest_row.get("lat", 0.0)
            dest_lon = dest_row.get("lon", 0.0)
            dest_tdi = float(dest_row.get("tdi", 0.3))
            dest_pop = float(dest_row.get("total_pop", 1000))
            attraction = dest_pop * dest_tdi

            if orig_lat and dest_lat:
                dist_km = _haversine_km(orig_lat, orig_lon, dest_lat, dest_lon)
            else:
                dist_km = 5.0  # fallback

            decay = math.exp(-_DECAY_BETA * dist_km)
            daily_demand = production * attraction * decay / 1e6  # scale to boardings

            rows.append({
                "origin_district": orig,
                "destination_district": dest,
                "distance_km": round(dist_km, 3),
                "daily_demand": round(daily_demand, 2),
                "am_peak": round(daily_demand * _TOD_MULTIPLIERS["am_peak"], 2),
                "midday": round(daily_demand * _TOD_MULTIPLIERS["midday"], 2),
                "pm_school": round(daily_demand * _TOD_MULTIPLIERS["pm_school"], 2),
                "pm_commute": round(daily_demand * _TOD_MULTIPLIERS["pm_commute"], 2),
                "evening": round(daily_demand * _TOD_MULTIPLIERS["evening"], 2),
            })

    od_df = pd.DataFrame(rows)
    logger.info("O-D matrix: %d pairs across %d districts", len(od_df), len(district_ids))
    return od_df


# =====================================================================
# SCHOOL DEMAND WINDOWS
# =====================================================================

def build_school_demand(
    demographics_df: pd.DataFrame,
    school_windows: list,
    survey_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Build school-specific demand spikes for each dismissal window.

    For each school window, estimates peak boarding demand at school stops
    based on:
    - District population in school catchment area
    - Youth share (pop_under_18 / total_pop)
    - Survey modal diversion rate (yes + maybe / total respondents)

    Args:
        demographics_df: District demographics with district_id, total_pop,
            and optionally pop_under_18.
        school_windows: List of dicts from config optimization.school_windows.
        survey_df: Optional student survey DataFrame with would_ride_bus column.

    Returns:
        DataFrame with columns: school, dismissal_time, pickup_deadline,
            districts, estimated_boardings, source.
    """
    if not school_windows:
        return pd.DataFrame()

    # Compute survey diversion rate if available
    survey_diversion = 0.45  # conservative fallback (45%)
    if survey_df is not None and len(survey_df) > 0 and "would_ride_bus" in survey_df.columns:
        n_total = len(survey_df)
        n_yes = sum(1 for v in survey_df["would_ride_bus"] if str(v).lower().startswith("yes"))
        n_maybe = sum(1 for v in survey_df["would_ride_bus"] if str(v).lower().startswith("maybe"))
        survey_diversion = (n_yes + n_maybe * 0.5) / max(n_total, 1)
        logger.info("Survey diversion rate: %.1f%% (yes=%d, maybe=%d, total=%d)",
                    survey_diversion * 100, n_yes, n_maybe, n_total)

    demo_indexed = None
    if demographics_df is not None and len(demographics_df) > 0:
        demo_indexed = demographics_df.set_index("district_id")

    rows = []
    for window in school_windows:
        school = window["school"]
        dismissal = window["dismissal_time"]
        window_min = int(window.get("pickup_window_min", 10))
        districts = window.get("districts", [])

        # Parse dismissal time → deadline
        h, m = map(int, dismissal.split(":"))
        deadline_h = h
        deadline_m = m + window_min
        if deadline_m >= 60:
            deadline_h += 1
            deadline_m -= 60
        pickup_deadline = f"{deadline_h:02d}:{deadline_m:02d}"

        # Estimate student catchment population
        catchment_pop = 0
        youth_pop = 0
        for did in districts:
            if demo_indexed is not None and did in demo_indexed.index:
                row = demo_indexed.loc[did]
                catchment_pop += float(row.get("total_pop", 0))
                youth_pop += float(row.get("pop_under_18", row.get("total_pop", 0) * 0.22))

        # Middle school: roughly 40% of youth are middle-school age (6th-8th grade)
        middle_school_pop = youth_pop * 0.40
        estimated_boardings = int(round(middle_school_pop * survey_diversion))

        rows.append({
            "school": school,
            "dismissal_time": dismissal,
            "pickup_deadline": pickup_deadline,
            "pickup_window_min": window_min,
            "districts": ",".join(districts),
            "catchment_population": int(catchment_pop),
            "youth_population": int(youth_pop),
            "estimated_boardings": max(estimated_boardings, 10),  # floor of 10
            "diversion_rate_used": round(survey_diversion, 3),
            "source": "youth_population * middle_school_fraction * survey_diversion_rate",
        })

    df = pd.DataFrame(rows)
    logger.info("School demand windows: %d windows", len(df))
    for _, row in df.iterrows():
        logger.info("  %s at %s → deadline %s, est. %d boardings",
                    row["school"], row["dismissal_time"],
                    row["pickup_deadline"], row["estimated_boardings"])
    return df


# =====================================================================
# TIME-OF-DAY PROFILES
# =====================================================================

def time_of_day_profile(
    od_matrix: pd.DataFrame,
    school_demand: pd.DataFrame,
    time_windows: Optional[dict] = None,
) -> dict:
    """Combine O-D matrix and school demand into per-window demand frames.

    Args:
        od_matrix: From build_od_matrix().
        school_demand: From build_school_demand().
        time_windows: Dict of window_name → [start_HH:MM, end_HH:MM].

    Returns:
        Dict mapping window name → DataFrame with demand estimates.
        Each DataFrame has columns: origin_district, destination_district,
            demand, window_start, window_end.
    """
    windows = time_windows or {
        "am_peak":    ["06:00", "09:00"],
        "midday":     ["09:00", "14:15"],
        "pm_school":  ["14:15", "16:15"],
        "pm_commute": ["16:15", "18:30"],
        "evening":    ["18:30", "21:00"],
    }

    profiles = {}
    for window_name, (wstart, wend) in windows.items():
        if od_matrix is None or len(od_matrix) == 0:
            profiles[window_name] = pd.DataFrame()
            continue

        col = window_name if window_name in od_matrix.columns else "daily_demand"
        df = od_matrix[["origin_district", "destination_district", "distance_km", col]].copy()
        df = df.rename(columns={col: "demand"})
        df["window_start"] = wstart
        df["window_end"] = wend
        df["window"] = window_name

        # For pm_school window, add school demand as additional rows
        if window_name == "pm_school" and school_demand is not None and len(school_demand) > 0:
            for _, sw in school_demand.iterrows():
                districts = [d.strip() for d in sw["districts"].split(",")]
                for orig in districts:
                    # School demand: students travel FROM school to home districts
                    school_rows = pd.DataFrame([{
                        "origin_district": "SCHOOL_" + sw["school"],
                        "destination_district": orig,
                        "distance_km": 2.0,  # typical school-to-home
                        "demand": sw["estimated_boardings"] / max(len(districts), 1),
                        "window_start": wstart,
                        "window_end": wend,
                        "window": window_name,
                    }])
                    df = pd.concat([df, school_rows], ignore_index=True)

        profiles[window_name] = df

    return profiles


# =====================================================================
# DEMAND SUMMARY BY DISTRICT
# =====================================================================

def compute_district_demand_totals(od_matrix: pd.DataFrame) -> pd.DataFrame:
    """Sum total demand originating from each district across all windows.

    Returns DataFrame with district_id, total_productions, total_attractions,
        net_demand (productions - attractions), used for route scoring.
    """
    if od_matrix is None or len(od_matrix) == 0:
        return pd.DataFrame(columns=["district_id", "total_productions",
                                     "total_attractions", "net_demand"])

    prod = (od_matrix.groupby("origin_district")["daily_demand"].sum()
            .rename("total_productions"))
    attr = (od_matrix.groupby("destination_district")["daily_demand"].sum()
            .rename("total_attractions"))

    df = pd.concat([prod, attr], axis=1).fillna(0).reset_index()
    df = df.rename(columns={"index": "district_id"})
    if "district_id" not in df.columns:
        df = df.reset_index().rename(columns={"index": "district_id"})

    # Handle multi-index from concat
    if df.columns[0] != "district_id":
        df.columns = ["district_id", "total_productions", "total_attractions"]

    df["net_demand"] = df["total_productions"] - df["total_attractions"]
    df = df.sort_values("total_productions", ascending=False).reset_index(drop=True)
    return df


# =====================================================================
# PIPELINE ENTRY POINT
# =====================================================================

def run_demand_matrix(
    tdi_df: pd.DataFrame,
    demographics_df: pd.DataFrame,
    district_manager,
    config: dict,
    survey_df: Optional[pd.DataFrame] = None,
) -> dict:
    """Run the full demand matrix pipeline.

    Args:
        tdi_df: From compute_transit_demand_index().
        demographics_df: District demographics DataFrame.
        district_manager: DistrictManager instance (for centroids).
        config: Full config dict (uses optimization.school_windows and
            optimization.time_windows).
        survey_df: Optional student survey data.

    Returns:
        Dict with keys:
            od_matrix: Full O-D DataFrame
            school_demand: School window demand DataFrame
            tod_profiles: Dict of per-window DataFrames
            district_totals: Per-district production/attraction totals
    """
    opt_cfg = config.get("optimization", {})
    school_windows = opt_cfg.get("school_windows", [])
    time_windows = opt_cfg.get("time_windows", None)

    # Build centroid table from DistrictManager
    centroid_rows = []
    for d in district_manager.districts.values():
        c = d.centroid()
        centroid_rows.append({
            "district_id": d.id,
            "zone": d.zone,
            "name": d.name,
            "lat": c[0],
            "lon": c[1],
        })
    centroids_df = pd.DataFrame(centroid_rows)

    od = build_od_matrix(tdi_df, demographics_df, centroids_df, school_windows)
    school_dem = build_school_demand(demographics_df, school_windows, survey_df)
    tod = time_of_day_profile(od, school_dem, time_windows)
    totals = compute_district_demand_totals(od)

    logger.info("Demand matrix pipeline complete:")
    logger.info("  O-D pairs: %d", len(od))
    logger.info("  School windows: %d", len(school_dem))
    logger.info("  Time-of-day profiles: %d windows", len(tod))

    return {
        "od_matrix": od,
        "school_demand": school_dem,
        "tod_profiles": tod,
        "district_totals": totals,
    }
