"""
data_ingestion.py -- Data loading, cleaning, and validation for all sources.

Handles ingestion of:
  - U.S. Census ACS 5-year block group data (demographics)
  - VTA GTFS feed (routes, stops, schedules)
  - Caltrans PeMS traffic volume data
  - SWITRS crash records
  - Road closure records

When live data sources are unavailable (no network, API key missing),
generates clearly-labeled synthetic data based on published aggregate
statistics so the pipeline can run end-to-end. All synthetic values
are flagged in the assumptions register.

Standards:
    - ACS data: Census Bureau API, vintage 2019-2023
    - GTFS: General Transit Feed Specification, v2.0
    - NTD: National Transit Database, FY2023
    - SWITRS: CHP Statewide Integrated Traffic Records System

References:
    - U.S. Census Bureau, ACS 5-Year Technical Documentation
    - Google, GTFS Reference (https://gtfs.org)
    - FTA, NTD Glossary (https://www.transit.dot.gov/ntd)
"""

import csv
import io
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)


def load_config(config_path: str = "config.yaml") -> dict:
    """Load master config. See districts.py for full docstring."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ===========================================================
# CENSUS / DEMOGRAPHICS
# ===========================================================

def load_census_block_groups(config: dict, data_dir: str = "data") -> pd.DataFrame:
    """Load ACS 5-year block group data for the Los Gatos study area.

    Attempts to load from a local CSV file first. If unavailable,
    generates synthetic block group data based on published aggregate
    statistics for Los Gatos and surrounding areas.

    Args:
        config: Master configuration dict.
        data_dir: Base data directory.

    Returns:
        DataFrame with columns: geoid, tract, block_group, county_fips,
        total_pop, median_income, zero_veh_hh, total_hh, transit_commuters,
        total_workers, pop_under_18, pop_65_plus, lat, lon.

    Statistical method: Direct census tabulation (or synthetic generation
        using published marginal distributions).
    Standard: ACS 5-Year Estimates, Tables B01001, B08201, B08301, B19013.
    Assumptions: Synthetic data uses Los Gatos CDP aggregate values
        distributed with realistic block-group-level variance.
    """
    local_path = Path(data_dir) / "processed" / "census_block_groups.csv"

    if local_path.exists():
        df = pd.read_csv(local_path, dtype={"geoid": str})
        logger.info("Loaded census data from %s (%d block groups)", local_path, len(df))
        return df

    logger.warning(
        "Census data file not found at %s -- generating synthetic block groups "
        "based on published Los Gatos CDP aggregates. Flag for assumptions register.",
        local_path,
    )
    return _generate_synthetic_census(config)


def _generate_synthetic_census(config: dict) -> pd.DataFrame:
    """Generate synthetic census block group data for the study area.

    Based on published ACS aggregates for Los Gatos CDP + Union SD area:
      - Los Gatos CDP pop: ~33,000 (ACS 2019-2023)
      - Median household income: ~$172,000
      - Zero-vehicle households: ~3.5%
      - Transit commute share: ~2.8%
      - 95033 mountain area pop: ~8,000 (density 89/sq mi)
      - Union SD area pop: ~25,000

    Each block group is assigned a centroid within the study area bounding
    box, with demographic values drawn from normal distributions anchored
    to the published aggregates.

    Returns:
        Synthetic DataFrame matching the census schema.

    Standard: Synthetic data methodology per Boardman et al. Ch. 8
        (sensitivity to data availability).
    Assumptions: Block group variance set at CV=0.3 for income, 0.5 for
        transit share. See assumptions_register.md.
    """
    rng = np.random.default_rng(42)

    # Define approximate block groups: ~35 for LGHS zone, ~20 for Union SD
    records = []

    # LGHS zone block groups (in-town)
    for i in range(25):
        lat = rng.uniform(37.215, 37.260)
        lon = rng.uniform(-122.000, -121.945)
        pop = int(rng.normal(1200, 400))
        pop = max(200, pop)
        records.append(_make_bg_record(
            f"060855050{i+1:02d}", "505", f"{i+1}", "085",
            pop, lat, lon, rng, area_type="urban"
        ))

    # Mountain block groups (95033) -- large area, low density
    for i in range(6):
        lat = rng.uniform(37.080, 37.200)
        lon = rng.uniform(-122.080, -122.020)
        pop = int(rng.normal(400, 200))
        pop = max(50, pop)
        records.append(_make_bg_record(
            f"060855051{i+1:02d}", "506", f"{i+1}", "085",
            pop, lat, lon, rng, area_type="mountain"
        ))

    # Union SD block groups (SJ 95124/95118/95120 portion)
    for i in range(18):
        lat = rng.uniform(37.218, 37.262)
        lon = rng.uniform(-121.935, -121.888)
        pop = int(rng.normal(1400, 500))
        pop = max(300, pop)
        records.append(_make_bg_record(
            f"060856050{i+1:02d}", "605", f"{i+1}", "085",
            pop, lat, lon, rng, area_type="suburban"
        ))

    df = pd.DataFrame(records)
    df["is_synthetic"] = True

    # Save for reproducibility
    out_path = Path("data/processed/census_block_groups.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    logger.info("Generated %d synthetic block groups, saved to %s", len(df), out_path)
    return df


def _make_bg_record(
    geoid: str, tract: str, bg: str, county: str,
    pop: int, lat: float, lon: float,
    rng: np.random.Generator, area_type: str = "urban",
) -> dict:
    """Create a single synthetic block group record.

    Demographic rates vary by area type to reflect real patterns:
    - urban: higher density, moderate income, more transit
    - mountain: very low density, high income, no transit
    - suburban: moderate density, moderate income, low transit
    """
    profiles = {
        "urban":    {"income_mean": 165000, "income_sd": 50000, "zvh_rate": 0.04, "transit_rate": 0.035, "senior_rate": 0.16, "youth_rate": 0.22},
        "mountain": {"income_mean": 195000, "income_sd": 70000, "zvh_rate": 0.01, "transit_rate": 0.005, "senior_rate": 0.14, "youth_rate": 0.18},
        "suburban": {"income_mean": 145000, "income_sd": 45000, "zvh_rate": 0.03, "transit_rate": 0.025, "senior_rate": 0.15, "youth_rate": 0.24},
    }
    p = profiles.get(area_type, profiles["urban"])

    hh = max(50, int(pop / rng.uniform(2.2, 2.8)))
    workers = max(20, int(pop * rng.uniform(0.45, 0.60)))
    income = max(30000, int(rng.normal(p["income_mean"], p["income_sd"])))
    zvh = max(0, int(hh * rng.normal(p["zvh_rate"], p["zvh_rate"] * 0.5)))
    transit = max(0, int(workers * rng.normal(p["transit_rate"], p["transit_rate"] * 0.5)))
    seniors = max(0, int(pop * rng.normal(p["senior_rate"], 0.04)))
    youth = max(0, int(pop * rng.normal(p["youth_rate"], 0.05)))

    return {
        "geoid": geoid,
        "tract": tract,
        "block_group": bg,
        "county_fips": county,
        "total_pop": pop,
        "median_income": income,
        "zero_veh_hh": zvh,
        "total_hh": hh,
        "transit_commuters": transit,
        "total_workers": workers,
        "pop_under_18": youth,
        "pop_65_plus": seniors,
        "lat": round(lat, 6),
        "lon": round(lon, 6),
    }


# ===========================================================
# GTFS (Transit Routes, Stops, Schedules)
# ===========================================================

def load_gtfs_stops(config: dict, data_dir: str = "data") -> pd.DataFrame:
    """Load transit stop locations from VTA GTFS feed.

    When real GTFS files are available (stops.txt, stop_times.txt, trips.txt),
    this function:
      1. Loads stops.txt (all VTA stops system-wide)
      2. Joins stop_times.txt -> trips.txt -> routes.txt to determine
         which routes serve each stop
      3. Filters to stops within the study area bounding box
      4. Returns a DataFrame with consistent columns

    If GTFS files are unavailable, falls back to synthetic stops.

    Args:
        config: Master config dict.
        data_dir: Base data directory.

    Returns:
        DataFrame with columns: stop_id, stop_name, stop_lat, stop_lon,
        route_ids (comma-separated list of routes serving this stop).

    Standard: GTFS v2.0 (stops.txt, stop_times.txt, trips.txt specs).
    """
    gtfs_dir = Path(data_dir) / "geospatial" / "gtfs"
    stops_path = gtfs_dir / "stops.txt"

    if not stops_path.exists():
        logger.warning("GTFS stops.txt not found -- generating synthetic stops for VTA routes.")
        return _generate_synthetic_stops(config)

    # -- Load real GTFS --
    stops = pd.read_csv(stops_path, dtype={"stop_id": str})
    logger.info("Loaded %d GTFS stops from %s", len(stops), stops_path)

    # Standardize column names (GTFS uses stop_lon, some feeds use stop_lng)
    if "stop_lng" in stops.columns and "stop_lon" not in stops.columns:
        stops = stops.rename(columns={"stop_lng": "stop_lon"})

    # -- Filter to study area bounding box --
    # Los Gatos + Union SD + Mountains: lat 37.06-37.27, lon -122.15 to -121.88
    STUDY_LAT_MIN, STUDY_LAT_MAX = 37.06, 37.27
    STUDY_LON_MIN, STUDY_LON_MAX = -122.15, -121.88

    before = len(stops)
    stops = stops[
        (stops["stop_lat"] >= STUDY_LAT_MIN) & (stops["stop_lat"] <= STUDY_LAT_MAX) &
        (stops["stop_lon"] >= STUDY_LON_MIN) & (stops["stop_lon"] <= STUDY_LON_MAX)
    ].copy()
    logger.info("Filtered to %d stops in study area (from %d system-wide)", len(stops), before)

    # -- Build route associations from stop_times + trips --
    stop_times_path = gtfs_dir / "stop_times.txt"
    trips_path = gtfs_dir / "trips.txt"

    if stop_times_path.exists() and trips_path.exists():
        logger.info("Building stop-route associations from stop_times + trips...")
        try:
            # Only load columns we need (stop_times can be very large)
            stop_times = pd.read_csv(
                stop_times_path,
                usecols=["trip_id", "stop_id"],
                dtype={"trip_id": str, "stop_id": str},
            )
            trips = pd.read_csv(
                trips_path,
                usecols=["trip_id", "route_id"],
                dtype={"trip_id": str, "route_id": str},
            )

            # Join: stop_times -> trips to get route_id for each stop
            stop_routes = stop_times.merge(trips[["trip_id", "route_id"]], on="trip_id", how="left")

            # Get unique routes per stop
            route_map = (
                stop_routes
                .dropna(subset=["route_id"])
                .groupby("stop_id")["route_id"]
                .apply(lambda x: ",".join(sorted(x.unique())))
                .reset_index()
                .rename(columns={"route_id": "route_ids"})
            )

            # Merge route info onto stops
            stops = stops.merge(route_map, on="stop_id", how="left")
            stops["route_ids"] = stops["route_ids"].fillna("")

            n_with_routes = (stops["route_ids"] != "").sum()
            logger.info("Matched %d / %d study-area stops to routes", n_with_routes, len(stops))

        except Exception as e:
            logger.warning("Failed to build route associations: %s. Using stops without routes.", e)
            stops["route_ids"] = ""
    else:
        logger.warning("stop_times.txt or trips.txt not found -- stops will have no route associations.")
        stops["route_ids"] = ""

    # -- Ensure consistent output columns --
    result = stops[["stop_id", "stop_name", "stop_lat", "stop_lon", "route_ids"]].copy()
    result["is_synthetic"] = False

    return result


def count_system_stops_per_route(data_dir: str = "data") -> dict:
    """Count total stops per route from the full GTFS (no area filter).

    This is needed to compute the study area's share of each route's
    total cost. If Route 27 has 80 stops system-wide and 17 in our
    study area, our share is 17/80 = 21%.

    Args:
        data_dir: Base data directory.

    Returns:
        Dict mapping route_id -> total_system_stop_count.
        Returns empty dict if GTFS data not available.
    """
    gtfs_dir = Path(data_dir) / "geospatial" / "gtfs"
    stops_path = gtfs_dir / "stops.txt"
    stop_times_path = gtfs_dir / "stop_times.txt"
    trips_path = gtfs_dir / "trips.txt"

    if not all(p.exists() for p in [stops_path, stop_times_path, trips_path]):
        return {}

    try:
        stop_times = pd.read_csv(
            stop_times_path,
            usecols=["trip_id", "stop_id"],
            dtype={"trip_id": str, "stop_id": str},
        )
        trips = pd.read_csv(
            trips_path,
            usecols=["trip_id", "route_id"],
            dtype={"trip_id": str, "route_id": str},
        )

        # Join to get route per stop
        merged = stop_times.merge(trips[["trip_id", "route_id"]], on="trip_id")

        # Count UNIQUE stops per route (not stop visits)
        system_counts = (
            merged.groupby("route_id")["stop_id"]
            .nunique()
            .to_dict()
        )

        logger.info("Counted system-wide stops for %d routes from GTFS", len(system_counts))
        for rid in sorted(system_counts.keys())[:10]:
            logger.info("  Route %s: %d total system stops", rid, system_counts[rid])

        return system_counts

    except Exception as e:
        logger.warning("Failed to count system stops from GTFS: %s", e)
        return {}


def _generate_synthetic_stops(config: dict) -> pd.DataFrame:
    """Generate synthetic transit stops along known route corridors.

    Route 27: Winchester LRT -> Los Gatos (via LG Blvd, downtown, Blossom Hill)
    Route 76 (discontinued): Downtown LG -> Summit Road via SR-17 corridor

    Stop spacing: ~0.3 miles for urban, ~1.0 miles for mountain route.

    Returns:
        Synthetic stops DataFrame.

    Standard: Stop locations approximate real VTA Route 27 alignment.
    """
    stops = []
    sid = 1000

    # Route 27 -- main corridor through Los Gatos
    # Winchester station -> LG Blvd -> downtown -> Blossom Hill -> south
    r27_waypoints = [
        (37.258, -121.950, "Winchester Transit Center"),
        (37.254, -121.955, "LG Blvd & Lark"),
        (37.250, -121.958, "LG Blvd & Blossom Hill"),
        (37.246, -121.961, "LG Blvd & Roberts"),
        (37.242, -121.961, "LG Blvd & Shannon"),
        (37.238, -121.961, "LG Blvd & Samaritan"),
        (37.234, -121.961, "National & Carlton"),
        (37.230, -121.960, "LG-Almaden & Ross Creek"),
        (37.226, -121.959, "LG-Almaden & Blossom Valley"),
        (37.232, -121.978, "N Santa Cruz & Main"),
        (37.228, -121.978, "S Santa Cruz & University"),
        (37.236, -121.975, "Blossom Hill & University"),
        (37.238, -121.970, "Blossom Hill & Los Gatos Blvd"),
        (37.240, -121.965, "Blossom Hill & Union"),
        (37.242, -121.955, "Blossom Hill & Camden"),
        (37.244, -121.945, "Blossom Hill & Leigh"),
        (37.246, -121.935, "Blossom Hill & Meridian"),
    ]
    for lat, lon, name in r27_waypoints:
        stops.append({
            "stop_id": f"VTA_{sid}",
            "stop_name": name,
            "stop_lat": lat,
            "stop_lon": lon,
            "route_ids": "27",
        })
        sid += 1

    # Route 76 (discontinued) -- downtown to Summit
    r76_waypoints = [
        (37.230, -121.978, "S Santa Cruz & Hwy 9"),
        (37.222, -121.981, "SR-17 & Los Gatos Creek"),
        (37.214, -121.988, "Alma Bridge Rd & SR-17"),
        (37.206, -121.998, "Lexington Reservoir"),
        (37.195, -122.012, "Black Rd & SR-17"),
        (37.183, -122.018, "Redwood Estates"),
        (37.168, -122.030, "Summit Road & SR-17"),
        (37.155, -122.040, "Summit & Loma Prieta"),
    ]
    for lat, lon, name in r76_waypoints:
        stops.append({
            "stop_id": f"VTA_{sid}",
            "stop_name": name,
            "stop_lat": lat,
            "stop_lon": lon,
            "route_ids": "76",
        })
        sid += 1

    # Highway 17 Express -- limited stops
    hwy17_stops = [
        (37.258, -121.950, "Winchester Transit Center (Hwy17X)"),
        (37.228, -121.978, "Los Gatos (Hwy17X)"),
    ]
    for lat, lon, name in hwy17_stops:
        stops.append({
            "stop_id": f"VTA_{sid}",
            "stop_name": name,
            "stop_lat": lat,
            "stop_lon": lon,
            "route_ids": "17X",
        })
        sid += 1

    df = pd.DataFrame(stops)
    df["is_synthetic"] = True

    out_path = Path("data/geospatial/gtfs/stops_synthetic.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    logger.info("Generated %d synthetic stops, saved to %s", len(df), out_path)
    return df


def load_gtfs_routes(config: dict, data_dir: str = "data") -> pd.DataFrame:
    """Load route definitions from GTFS or generate synthetic.

    Real GTFS routes.txt has columns like: route_id, agency_id,
    route_short_name, route_long_name, route_type, route_color, etc.
    We add a 'status' column (all 'active' for current GTFS feed).

    Returns:
        DataFrame: route_id, route_short_name, route_long_name, route_type, status.
    """
    gtfs_path = Path(data_dir) / "geospatial" / "gtfs" / "routes.txt"
    if gtfs_path.exists():
        df = pd.read_csv(gtfs_path, dtype={"route_id": str})
        logger.info("Loaded %d GTFS routes from %s", len(df), gtfs_path)

        # Ensure required columns exist
        if "route_short_name" not in df.columns:
            df["route_short_name"] = df["route_id"]
        if "route_long_name" not in df.columns:
            df["route_long_name"] = ""
        if "status" not in df.columns:
            df["status"] = "active"  # All routes in current GTFS are active

        df["is_synthetic"] = False
        return df

    routes = [
        {"route_id": "27", "route_short_name": "27", "route_long_name": "Winchester - Los Gatos - Santa Teresa", "route_type": 3, "status": "active"},
        {"route_id": "17X", "route_short_name": "17X", "route_long_name": "Highway 17 Express (San Jose - Santa Cruz)", "route_type": 3, "status": "active"},
        {"route_id": "76", "route_short_name": "76", "route_long_name": "Los Gatos - Summit Road", "route_type": 3, "status": "discontinued_2010"},
    ]
    df = pd.DataFrame(routes)
    df["is_synthetic"] = True
    logger.info("Generated %d synthetic route definitions", len(df))
    return df


# ===========================================================
# RIDERSHIP
# ===========================================================

def load_ridership_data(config: dict, data_dir: str = "data") -> pd.DataFrame:
    """Load ridership data from VTA RBS dataset or fall back to NTD estimates.

    Checks for OCT_2025_RBS_FULL_DATA_SET.xlsx in data/raw/ first.
    If found, loads real stop-level peak load data, filters to study area
    routes, and aggregates to route-level annual boardings.

    Args:
        config: Master config dict.
        data_dir: Base data directory.

    Returns:
        DataFrame: route_id, annual_boardings, avg_daily_boardings,
        avg_weekday_boardings, source.

    Standard: VTA Ridecheck (APC) data collection methodology.
    """
    # Check for pre-processed CSV first
    local_path = Path(data_dir) / "processed" / "ridership.csv"
    if local_path.exists():
        df = pd.read_csv(local_path)
        logger.info("Loaded ridership from %s (%d rows)", local_path, len(df))
        return df

    # Check for real RBS Excel file
    rbs_path = Path(data_dir) / "raw" / "OCT_2025_RBS_FULL_DATA_SET.xlsx"
    if rbs_path.exists():
        return _load_rbs_ridership(rbs_path, config)

    logger.warning("Ridership data not found -- generating NTD-based estimates.")
    logger.warning("  Place OCT_2025_RBS_FULL_DATA_SET.xlsx in data/raw/ for real data.")

    # NTD FY2023 VTA bus: ~22M annual unlinked trips
    vta_annual = 22_000_000
    r27_share = 0.012
    r27_annual = int(vta_annual * r27_share)

    records = [
        {"route_id": "27", "annual_boardings": r27_annual,
         "avg_daily_boardings": int(r27_annual / 310),
         "avg_weekday_boardings": int(r27_annual / 255),
         "source": "NTD FY2023 estimate"},
        {"route_id": "17X", "annual_boardings": 180_000,
         "avg_daily_boardings": 580,
         "avg_weekday_boardings": 700,
         "source": "NTD FY2023 estimate"},
        {"route_id": "76", "annual_boardings": 0,
         "avg_daily_boardings": 0,
         "avg_weekday_boardings": 0,
         "source": "Discontinued June 2010 -- historical est. ~40/day when active"},
    ]
    df = pd.DataFrame(records)
    df["is_synthetic"] = True
    return df


def _load_rbs_ridership(rbs_path: Path, config: dict) -> pd.DataFrame:
    """Load and process VTA Ridecheck/RBS Excel data.

    The RBS file contains stop-level peak load observations across
    all VTA routes. We filter to routes serving the study area,
    aggregate to route-level daily boardings, and annualize.

    Columns used:
        PEAK_LOAD - max passengers at this stop for this trip
        AVG_PEAK_LOAD - average peak load across trips
        ROUTE_REV - route identifier (e.g. "22:", "27:", "64:")
        Stop_ID_Text - VTA stop identifier
        STOP_DISPLAY - stop display name
        CITY - city name (filter to study area)
        SERVICE_CODE2 - Local / Express
        PATTERN_KEY - direction pattern (EB/WB/NB/SB + variant)

    Args:
        rbs_path: Path to the RBS Excel file.
        config: Master config.

    Returns:
        DataFrame with route-level ridership.
    """
    logger.info("Loading VTA RBS ridership data from %s", rbs_path)

    try:
        rbs = pd.read_excel(rbs_path)
    except Exception as e:
        logger.error("Failed to read RBS Excel: %s", e)
        logger.warning("Falling back to NTD estimates.")
        return load_ridership_data.__wrapped__(config) if hasattr(load_ridership_data, '__wrapped__') else pd.DataFrame()

    logger.info("  RBS raw rows: %d", len(rbs))
    logger.info("  Columns: %s", ", ".join(rbs.columns.tolist()))

    # Standardize column names (handle whitespace)
    rbs.columns = rbs.columns.str.strip()

    # Extract route number from ROUTE_REV (format: "22:", "27:", "64:")
    if "ROUTE_REV" in rbs.columns:
        rbs["route_id"] = rbs["ROUTE_REV"].astype(str).str.replace(":", "").str.strip()
    elif "route_id" in rbs.columns:
        pass
    else:
        logger.error("Cannot find route column in RBS data. Columns: %s", rbs.columns.tolist())
        return pd.DataFrame()

    # Log what routes are in the data
    route_counts = rbs["route_id"].value_counts()
    logger.info("  Routes in RBS data: %d unique", len(route_counts))
    logger.info("  Top 10 routes by observations: %s",
                ", ".join(f"{r}({c})" for r, c in route_counts.head(10).items()))

    # Filter to study area routes
    # Study area routes: any that appear in our GTFS-filtered stops
    study_routes = config.get("transit", {}).get("study_routes", None)
    if study_routes:
        rbs_study = rbs[rbs["route_id"].isin(study_routes)].copy()
    else:
        # Filter by city if available
        study_cities = ["Los Gatos", "Campbell", "San Jose", "Monte Sereno", "Saratoga"]
        if "CITY" in rbs.columns:
            rbs_study = rbs[rbs["CITY"].str.strip().isin(study_cities)].copy()
            logger.info("  Filtered by city: %d rows in study cities", len(rbs_study))
        else:
            rbs_study = rbs.copy()

    if len(rbs_study) == 0:
        logger.warning("No RBS records found for study area. Using all data.")
        rbs_study = rbs.copy()

    # Log cities found
    if "CITY" in rbs_study.columns:
        city_counts = rbs_study["CITY"].value_counts()
        logger.info("  Cities in filtered data: %s",
                    ", ".join(f"{c}({n})" for c, n in city_counts.head(10).items()))

    # Aggregate: average peak load per route (across all stops and trips)
    route_stats = (
        rbs_study.groupby("route_id")
        .agg(
            mean_peak_load=("PEAK_LOAD", "mean"),
            max_peak_load=("PEAK_LOAD", "max"),
            n_observations=("PEAK_LOAD", "count"),
            n_stops=("Stop_ID_Text" if "Stop_ID_Text" in rbs_study.columns else "route_id", "nunique"),
        )
        .reset_index()
    )

    # Estimate daily boardings from peak load data
    # Peak load = max passengers on bus at once, not total boardings.
    # Typical ratio: daily boardings ~ peak_load * trips_per_day * boarding_factor
    # For VTA local bus: ~30-50 trips/day/route, boarding factor ~2.5 (turnover)
    # Simpler: use AVG_PEAK_LOAD * n_observations as proxy, annualize
    if "AVG_PEAK_LOAD" in rbs_study.columns:
        route_avgs = rbs_study.groupby("route_id")["AVG_PEAK_LOAD"].first().reset_index()
        route_stats = route_stats.merge(route_avgs, on="route_id", how="left")

    # Estimate annual boardings
    # Each observation represents one trip's peak load at one stop
    # Daily boardings per route ~ sum of peak loads across all stops / coverage factor
    daily_by_route = (
        rbs_study.groupby("route_id")["PEAK_LOAD"]
        .sum()
        .reset_index()
        .rename(columns={"PEAK_LOAD": "total_peak_load_sum"})
    )
    # Number of unique blocks (trips) per route
    if "BLOCK" in rbs_study.columns:
        trips_by_route = (
            rbs_study.groupby("route_id")["BLOCK"]
            .nunique()
            .reset_index()
            .rename(columns={"BLOCK": "n_trips"})
        )
        daily_by_route = daily_by_route.merge(trips_by_route, on="route_id", how="left")
    else:
        daily_by_route["n_trips"] = 1

    route_stats = route_stats.merge(daily_by_route, on="route_id", how="left")

    # Build output
    records = []
    for _, row in route_stats.iterrows():
        rid = row["route_id"]
        n_trips = row.get("n_trips", 1)
        mean_pl = row.get("mean_peak_load", 0)
        # Daily boardings estimate: mean_peak_load * n_trips * turnover factor (2.0)
        daily_est = mean_pl * max(n_trips, 1) * 2.0
        annual_est = int(daily_est * 255)  # 255 weekdays

        records.append({
            "route_id": rid,
            "annual_boardings": annual_est,
            "avg_daily_boardings": int(daily_est),
            "avg_weekday_boardings": int(daily_est),
            "mean_peak_load": round(mean_pl, 1),
            "max_peak_load": round(row.get("max_peak_load", 0), 1),
            "n_observations": int(row.get("n_observations", 0)),
            "n_trips_observed": int(n_trips),
            "source": "VTA RBS Oct 2025",
        })

    df = pd.DataFrame(records)
    df["is_synthetic"] = False

    logger.info("  Processed %d routes from RBS data", len(df))
    for _, r in df.iterrows():
        logger.info("    Route %s: %d annual boardings (%.1f mean peak load, %d trips observed)",
                    r["route_id"], r["annual_boardings"], r["mean_peak_load"], r["n_trips_observed"])

    # Save processed version
    out_path = Path("data/processed/ridership.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    logger.info("  Saved processed ridership to %s", out_path)

    return df


def load_rbs_stop_detail(data_dir: str = "data") -> Optional[pd.DataFrame]:
    """Load full stop-level detail from RBS for district allocation.

    Returns the raw RBS data filtered to study area with route_id parsed,
    for use in allocating ridership to districts by actual stop-level loads
    instead of the stop-count proxy.

    Args:
        data_dir: Base data directory.

    Returns:
        DataFrame with stop-level peak loads, or None if not available.
    """
    rbs_path = Path(data_dir) / "raw" / "OCT_2025_RBS_FULL_DATA_SET.xlsx"
    if not rbs_path.exists():
        return None

    try:
        rbs = pd.read_excel(rbs_path)
        rbs.columns = rbs.columns.str.strip()

        if "ROUTE_REV" in rbs.columns:
            rbs["route_id"] = rbs["ROUTE_REV"].astype(str).str.replace(":", "").str.strip()

        # Keep essential columns
        keep_cols = []
        for col in ["route_id", "PEAK_LOAD", "AVG_PEAK_LOAD", "CITY",
                     "Stop_ID_Text", "STOP_DISPLAY", "PATTERN_KEY", "BLOCK",
                     "SERVICE_CODE2"]:
            if col in rbs.columns:
                keep_cols.append(col)

        if keep_cols:
            return rbs[keep_cols].copy()
        return rbs
    except Exception as e:
        logger.warning("Failed to load RBS stop detail: %s", e)
        return None


# ===========================================================
# BUS SCHEDULE OBSERVATIONS (Real observed arrival times)
# ===========================================================

def load_bus_schedule_observations(data_dir: str = "data") -> Optional[pd.DataFrame]:
    """Load observed bus arrival times at school-serving stops.

    Reads the Bus_Schedules___Observed_Times.md markdown table containing
    scheduled vs actual arrival times at stops near schools. Computes
    schedule deviation metrics for the reliability benefit calculation.

    Searches for the file in data/raw/ directory.

    Args:
        data_dir: Base data directory.

    Returns:
        DataFrame with one row per stop-trip observation containing:
        stop_name, school, school_start, scheduled_time, actual_time,
        deviation_minutes. Or None if file not found.

    Standard: Hand-collected field observations, 2023-2025.
    """
    # Search for the file in multiple locations
    search_paths = [
        Path(data_dir) / "raw" / "Bus_Schedules___Observed_Times.md",
        Path(data_dir) / "raw" / "bus_schedules.md",
        Path(data_dir) / "raw" / "Bus_Schedules___Observed_Times.csv",
    ]

    md_path = None
    for p in search_paths:
        if p.exists():
            md_path = p
            break

    if md_path is None:
        logger.info("Bus schedule observations file not found in data/raw/")
        logger.info("  Place Bus_Schedules___Observed_Times.md in data/raw/ for real reliability data.")
        return None

    logger.info("Loading bus schedule observations from %s", md_path)

    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()

    # Parse markdown table
    lines = [l.strip() for l in text.strip().split("\n") if l.strip() and "|" in l]
    if len(lines) < 3:
        logger.warning("Bus schedule file has fewer than 3 lines, cannot parse table.")
        return None

    # Skip separator line (line with :----)
    header_line = lines[0]
    data_lines = [l for l in lines[2:] if not l.replace("|", "").replace("-", "").replace(":", "").strip() == ""]

    # Parse header
    headers = [h.strip() for h in header_line.split("|") if h.strip()]

    # Parse rows
    rows = []
    for line in data_lines:
        cells = [c.strip() for c in line.split("|") if c.strip() != ""]
        if len(cells) >= len(headers):
            cells = cells[:len(headers)]
        rows.append(dict(zip(headers, cells)))

    if not rows:
        logger.warning("No data rows parsed from bus schedule file.")
        return None

    df = pd.DataFrame(rows)
    logger.info("  Parsed %d stop records from schedule observations", len(df))

    # Expand multi-time entries into individual observations
    observations = []
    for _, row in df.iterrows():
        stop = row.get("Stop", "")
        school = row.get("School", "")
        school_start = row.get("School Start Time", "")

        # Parse time columns
        sched_2025 = row.get("2025 Scheduled Arrival Time", "")
        sched_2023 = row.get("2023 Scheduled Arrival Time", "")
        actual_2023 = row.get("2023 Actual Arrival Time", "")
        predicted_2025 = row.get("2025 Predicted Actual Arrival Time", "")

        # Split comma-separated times
        times_2025 = _split_times(sched_2025)
        times_2023_sched = _split_times(sched_2023)
        times_2023_actual = _split_times(actual_2023)

        # Pair scheduled and actual times where both exist
        n_pairs = min(len(times_2023_sched), len(times_2023_actual))
        for i in range(n_pairs):
            sched_min = _time_to_minutes(times_2023_sched[i])
            actual_min = _time_to_minutes(times_2023_actual[i])

            if sched_min is not None and actual_min is not None:
                deviation = actual_min - sched_min
                observations.append({
                    "stop_name": stop,
                    "school": school,
                    "school_start_time": school_start,
                    "scheduled_2023": times_2023_sched[i],
                    "actual_2023": times_2023_actual[i],
                    "scheduled_2025": times_2025[i] if i < len(times_2025) else "",
                    "predicted_2025": predicted_2025,
                    "deviation_minutes": deviation,
                    "is_am_trip": sched_min < 720,  # Before noon
                })

    obs_df = pd.DataFrame(observations)

    if len(obs_df) > 0:
        am_obs = obs_df[obs_df["is_am_trip"]]
        pm_obs = obs_df[~obs_df["is_am_trip"]]
        mean_dev = obs_df["deviation_minutes"].mean()
        mean_abs_dev = obs_df["deviation_minutes"].abs().mean()

        logger.info("  Expanded to %d individual time observations", len(obs_df))
        logger.info("  AM observations: %d | PM observations: %d", len(am_obs), len(pm_obs))
        logger.info("  Mean deviation: %.1f min (positive = late)", mean_dev)
        logger.info("  Mean absolute deviation: %.1f min", mean_abs_dev)

        # Log per-stop details
        for stop_name, group in obs_df.groupby("stop_name"):
            avg_dev = group["deviation_minutes"].mean()
            school = group["school"].iloc[0]
            logger.info("    %s (%s): avg %.1f min deviation (%d obs)",
                       stop_name, school, avg_dev, len(group))
    else:
        logger.warning("  No paired time observations could be extracted.")

    return obs_df


def _split_times(time_str) -> list:
    """Split a comma-separated time string into individual time entries."""
    # Handle NaN, None, float, empty
    if time_str is None or (isinstance(time_str, float) and pd.isna(time_str)):
        return []
    time_str = str(time_str).strip()
    if not time_str or time_str == "nan":
        return []
    # Handle entries like "~Scheduled Time" or "Was not recorded"
    if "scheduled" in time_str.lower() or "recorded" in time_str.lower():
        return []
    parts = [t.strip() for t in time_str.split(",")]
    return [p for p in parts if p and not p.startswith("\\")]


def _time_to_minutes(time_str: str) -> Optional[int]:
    """Convert a time string like '8:30' or '1:51' to minutes since midnight.

    Handles AM/PM inference: times <= 6:xx assumed PM (add 12 hours),
    times 7:xx-12:xx assumed AM, times 12:xx+ kept as-is.
    """
    time_str = time_str.strip().replace("~", "")
    if not time_str:
        return None
    try:
        parts = time_str.split(":")
        if len(parts) != 2:
            return None
        h = int(parts[0])
        m = int(parts[1])
        # Infer AM/PM: school context means 1:xx-6:xx are PM
        if h < 7:
            h += 12  # 1:51 -> 13:51, 3:08 -> 15:08
        return h * 60 + m
    except (ValueError, IndexError):
        return None


# ===========================================================
# STUDENT SURVEY DATA
# ===========================================================

def load_student_survey(data_dir: str = "data") -> Optional[pd.DataFrame]:
    """Load Middle School Bus Survey responses.

    Reads the survey Excel file with student responses about their
    transportation mode, willingness to ride transit, preferred
    stop locations, and timing preferences.

    The survey was conducted at Union Middle School (and potentially
    other schools) with responses from students about their
    current and preferred transportation modes.

    Args:
        data_dir: Base data directory.

    Returns:
        DataFrame with parsed survey responses, or None if not found.
        Key derived columns:
        - current_mode: how the student currently gets to school
        - currently_rides_bus: Yes/No
        - would_ride_bus: Yes/No/Maybe
        - preferred_stop_location: free text
        - preferred_timing: Before/After/Both
        - minutes_early: how early before school they want the bus
        - minutes_after: how late after school they want the bus

    Standard: Primary stated-preference survey data.
    """
    search_names = [
        "Middle School Bus Survey (Responses).xlsx",
        "Middle_School_Bus_Survey_Responses.xlsx",
        "bus_survey.xlsx",
        "survey_responses.xlsx",
    ]

    survey_path = None
    raw_dir = Path(data_dir) / "raw"
    for name in search_names:
        p = raw_dir / name
        if p.exists():
            survey_path = p
            break

    if survey_path is None:
        logger.info("Student survey file not found in data/raw/")
        logger.info("  Place 'Middle School Bus Survey (Responses).xlsx' in data/raw/ for real demand data.")
        return None

    logger.info("Loading student survey from %s", survey_path)

    try:
        df = pd.read_excel(survey_path)
    except Exception as e:
        logger.error("Failed to read survey Excel: %s", e)
        return None

    logger.info("  Raw survey rows: %d, columns: %d", len(df), len(df.columns))
    logger.info("  Columns: %s", ", ".join(df.columns.tolist()[:15]))

    # The survey has unnamed columns based on Google Forms output
    # Map by position since column names may be the question text
    cols = df.columns.tolist()
    col_map = {}

    # Try to identify columns by content patterns
    for i, col in enumerate(cols):
        col_lower = str(col).lower()
        if "timestamp" in col_lower or i == 0:
            col_map["timestamp"] = col
        elif "email" in col_lower or "@" in str(df[col].iloc[0] if len(df) > 0 else ""):
            col_map["email"] = col
        elif "school" in col_lower and "start" not in col_lower and "end" not in col_lower:
            col_map["school"] = col
        elif "grade" in col_lower:
            col_map["grade"] = col
        elif "how do you" in col_lower or "get to school" in col_lower or "drives" in str(df[col].iloc[0] if len(df) > 0 else "").lower():
            col_map["current_mode"] = col
        elif "currently" in col_lower and "bus" in col_lower:
            col_map["currently_rides_bus"] = col
        elif "would" in col_lower and ("ride" in col_lower or "bus" in col_lower):
            col_map["would_ride_bus"] = col

    logger.info("  Identified columns: %s", ", ".join(f"{k}={v}" for k, v in list(col_map.items())[:8]))

    # Build standardized output
    result = pd.DataFrame()
    result["timestamp"] = df[col_map["timestamp"]] if "timestamp" in col_map else None
    result["school"] = df[col_map["school"]] if "school" in col_map else "Unknown"
    result["grade"] = df[col_map["grade"]] if "grade" in col_map else "Unknown"

    # Current mode
    if "current_mode" in col_map:
        result["current_mode"] = df[col_map["current_mode"]]
    elif len(cols) >= 5:
        result["current_mode"] = df[cols[4]]  # Typically 5th column

    # Currently rides bus
    if "currently_rides_bus" in col_map:
        result["currently_rides_bus"] = df[col_map["currently_rides_bus"]]
    elif len(cols) >= 6:
        result["currently_rides_bus"] = df[cols[5]]

    # Would ride bus
    if "would_ride_bus" in col_map:
        result["would_ride_bus"] = df[col_map["would_ride_bus"]]
    elif len(cols) >= 7:
        result["would_ride_bus"] = df[cols[6]]

    # Keep all other columns as additional data
    result["n_total_columns"] = len(cols)

    # Compute summary statistics
    n_total = len(result)
    if "current_mode" in result.columns:
        mode_counts = result["current_mode"].value_counts()
        logger.info("  Current transportation modes:")
        for mode, count in mode_counts.items():
            logger.info("    %s: %d (%.1f%%)", mode, count, 100 * count / n_total)

    if "currently_rides_bus" in result.columns:
        bus_counts = result["currently_rides_bus"].value_counts()
        logger.info("  Currently rides bus:")
        for val, count in bus_counts.items():
            logger.info("    %s: %d (%.1f%%)", val, count, 100 * count / n_total)

    if "would_ride_bus" in result.columns:
        would_counts = result["would_ride_bus"].value_counts()
        logger.info("  Would ride bus if available/improved:")
        for val, count in would_counts.items():
            logger.info("    %s: %d (%.1f%%)", val, count, 100 * count / n_total)

        # Mode diversion potential
        n_yes = sum(1 for v in result["would_ride_bus"] if str(v).lower().startswith("yes"))
        n_maybe = sum(1 for v in result["would_ride_bus"] if str(v).lower().startswith("maybe"))
        pct_potential = (n_yes + n_maybe) / max(n_total, 1) * 100
        logger.info("  MODE DIVERSION POTENTIAL: %.1f%% (Yes: %d, Maybe: %d, Total: %d)",
                    pct_potential, n_yes, n_maybe, n_total)

    # Save processed version
    out_path = Path("outputs/tables/student_survey_processed.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False)
    logger.info("  Saved processed survey to %s", out_path)

    return result


# ===========================================================
# CRASH DATA
# ===========================================================

def load_crash_data(config: dict, data_dir: str = "data") -> pd.DataFrame:
    """Load SWITRS crash records for the study area.

    Returns:
        DataFrame: crash_id, date, lat, lon, severity (KABCO),
        involved_modes, road_name.

    Standard: SWITRS / KABCO injury classification.
    Assumptions: Synthetic data calibrated to Santa Clara County
        crash rates (~3.5 crashes/1000 VMT).
    """
    local_path = Path(data_dir) / "traffic" / "incidents" / "switrs_los_gatos.csv"
    if local_path.exists():
        return pd.read_csv(local_path)

    logger.warning("SWITRS crash data not found -- generating synthetic crash records.")
    rng = np.random.default_rng(123)
    n_crashes = 150  # ~3 years of local crashes

    severity_probs = [0.005, 0.03, 0.15, 0.25, 0.565]  # K, A, B, C, O
    severities = rng.choice(["K", "A", "B", "C", "O"], size=n_crashes, p=severity_probs)

    records = []
    for i in range(n_crashes):
        # Concentrate crashes near major roads
        if rng.random() < 0.4:  # SR-17 corridor
            lat = rng.uniform(37.205, 37.255)
            lon = rng.uniform(-121.985, -121.975)
            road = "SR-17"
        elif rng.random() < 0.5:  # LG Blvd
            lat = rng.uniform(37.222, 37.256)
            lon = rng.uniform(-121.963, -121.957)
            road = "Los Gatos Blvd"
        elif rng.random() < 0.5:  # Blossom Hill
            lat = rng.uniform(37.235, 37.240)
            lon = rng.uniform(-121.990, -121.945)
            road = "Blossom Hill Rd"
        else:
            lat = rng.uniform(37.210, 37.260)
            lon = rng.uniform(-122.000, -121.940)
            road = "Local road"

        year = rng.choice([2021, 2022, 2023, 2024])
        month = rng.integers(1, 13)
        day = rng.integers(1, 29)

        records.append({
            "crash_id": f"SW{year}{i:04d}",
            "date": f"{year}-{month:02d}-{day:02d}",
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "severity": severities[i],
            "involved_pedestrian": bool(rng.random() < 0.08),
            "involved_bicycle": bool(rng.random() < 0.05),
            "road_name": road,
        })

    df = pd.DataFrame(records)
    df["is_synthetic"] = True
    return df


# ===========================================================
# TRAFFIC VOLUME
# ===========================================================

def load_traffic_volumes(config: dict, data_dir: str = "data") -> pd.DataFrame:
    """Load PeMS traffic volume data for SR-17 and SR-85.

    Returns:
        DataFrame: station_id, route, direction, hour, avg_speed_mph,
        avg_volume_vph (vehicles per hour), date.

    Standard: Caltrans PeMS detector data.
    Assumptions: Synthetic data based on published AADT for SR-17
        (~75,000 vpd) and SR-85 (~110,000 vpd at peak segment).
    """
    local_path = Path(data_dir) / "traffic" / "volume" / "pems_sr17_sr85.csv"
    if local_path.exists():
        return pd.read_csv(local_path)

    logger.warning("PeMS data not found -- generating synthetic traffic profiles.")
    rng = np.random.default_rng(456)

    records = []
    for route, aadt in [("SR-17", 75000), ("SR-85", 110000)]:
        for direction in ["NB", "SB"]:
            dir_share = aadt / 2
            for hour in range(24):
                # Typical hourly distribution
                if 7 <= hour <= 9:
                    hourly_share = 0.085
                    speed = rng.normal(35, 10) if direction == "NB" else rng.normal(55, 8)
                elif 16 <= hour <= 18:
                    hourly_share = 0.090
                    speed = rng.normal(55, 8) if direction == "NB" else rng.normal(35, 10)
                elif 10 <= hour <= 15:
                    hourly_share = 0.055
                    speed = rng.normal(60, 5)
                elif 6 <= hour <= 6 or 19 <= hour <= 21:
                    hourly_share = 0.045
                    speed = rng.normal(60, 5)
                else:
                    hourly_share = 0.015
                    speed = rng.normal(65, 3)

                volume = int(dir_share * hourly_share * rng.normal(1.0, 0.05))
                speed = max(15, min(70, speed))

                records.append({
                    "route": route,
                    "direction": direction,
                    "hour": hour,
                    "avg_volume_vph": volume,
                    "avg_speed_mph": round(speed, 1),
                })

    df = pd.DataFrame(records)
    df["is_synthetic"] = True
    return df


# ===========================================================
# ROAD CLOSURES
# ===========================================================

def load_road_closures(config: dict, data_dir: str = "data") -> pd.DataFrame:
    """Load road closure records for the study area.

    Returns:
        DataFrame: closure_id, start_date, end_date, location,
        lat, lon, closure_type, affected_routes.

    Standard: Caltrans Lane Closure System format.
    """
    local_path = Path(data_dir) / "traffic" / "closures" / "closures.csv"
    if local_path.exists():
        return pd.read_csv(local_path)

    logger.warning("Closure data not found -- generating synthetic closures.")
    records = [
        {"closure_id": "CL001", "start_date": "2024-01-15", "end_date": "2024-01-15",
         "location": "SR-17 NB near Lexington Reservoir", "lat": 37.206, "lon": -121.998,
         "closure_type": "incident", "affected_routes": "17X",
         "duration_hours": 4},
        {"closure_id": "CL002", "start_date": "2024-03-10", "end_date": "2024-03-22",
         "location": "SR-17 NB/SB near Bear Creek Rd", "lat": 37.195, "lon": -122.012,
         "closure_type": "construction", "affected_routes": "17X",
         "duration_hours": 288},
        {"closure_id": "CL003", "start_date": "2024-06-01", "end_date": "2024-06-01",
         "location": "Los Gatos Blvd at Blossom Hill Rd", "lat": 37.238, "lon": -121.961,
         "closure_type": "utility_work", "affected_routes": "27",
         "duration_hours": 8},
        {"closure_id": "CL004", "start_date": "2024-12-20", "end_date": "2024-12-20",
         "location": "SR-17 both directions near Summit", "lat": 37.168, "lon": -122.030,
         "closure_type": "weather_snow", "affected_routes": "17X",
         "duration_hours": 12},
    ]
    df = pd.DataFrame(records)
    df["is_synthetic"] = True
    return df


# ===========================================================
# DATA QUALITY REPORT
# ===========================================================

def run_data_quality_report(
    census: pd.DataFrame,
    stops: pd.DataFrame,
    crashes: pd.DataFrame,
    traffic: pd.DataFrame,
    closures: pd.DataFrame,
) -> str:
    """Generate a data quality report for all ingested datasets.

    Checks completeness, outliers, and distributions for each dataset.
    Returns a formatted Markdown string.

    Args:
        census: Census block group data.
        stops: Transit stop locations.
        crashes: Crash records.
        traffic: Traffic volume data.
        closures: Road closure records.

    Returns:
        Markdown-formatted data quality report string.

    Standard: APA/ASA reporting standards for data documentation.
    """
    lines = [
        "# Data Quality Report -- Phase A1",
        f"*Generated by data_ingestion.py*\n",
        "## Summary\n",
        f"| Dataset | Records | Synthetic? | Completeness |",
        f"|---------|---------|------------|--------------|",
    ]

    for name, df in [("Census BGs", census), ("Transit Stops", stops),
                     ("Crash Records", crashes), ("Traffic Volume", traffic),
                     ("Road Closures", closures)]:
        n = len(df)
        synth = "Yes" if df.get("is_synthetic", pd.Series([False])).any() else "No"
        nulls = df.isnull().sum().sum()
        total_cells = df.shape[0] * df.shape[1]
        completeness = f"{(1 - nulls/max(total_cells,1))*100:.1f}%"
        lines.append(f"| {name} | {n} | {synth} | {completeness} |")

    lines.append("")

    # Census details
    lines.append("## Census Block Groups\n")
    if len(census) > 0:
        lines.append(f"- **Total block groups:** {len(census)}")
        lines.append(f"- **Total population:** {census['total_pop'].sum():,}")
        lines.append(f"- **Median income range:** ${census['median_income'].min():,} -- ${census['median_income'].max():,}")
        lines.append(f"- **Mean income:** ${census['median_income'].mean():,.0f}")
        lines.append(f"- **Zero-vehicle HH rate:** {census['zero_veh_hh'].sum()/max(census['total_hh'].sum(),1)*100:.1f}%")
        lines.append(f"- **Transit commute share:** {census['transit_commuters'].sum()/max(census['total_workers'].sum(),1)*100:.1f}%")

    # Stops details
    lines.append("\n## Transit Stops\n")
    if len(stops) > 0 and "route_ids" in stops.columns:
        lines.append(f"- **Total stops:** {len(stops)}")
        # Split comma-separated route_ids and count individual routes
        all_routes = stops["route_ids"].dropna().str.split(",").explode().str.strip()
        all_routes = all_routes[all_routes != ""]
        route_counts = all_routes.value_counts().head(15)  # Top 15 routes
        unique_routes = all_routes.nunique()
        lines.append(f"- **Unique routes serving study area:** {unique_routes}")
        for route, count in route_counts.items():
            lines.append(f"- **Route {route}:** {count} stops")
    elif len(stops) > 0:
        lines.append(f"- **Total stops:** {len(stops)}")
        lines.append("- Route associations not available")

    # Crash details
    lines.append("\n## Crash Records\n")
    if len(crashes) > 0:
        sev_counts = crashes["severity"].value_counts().sort_index()
        lines.append(f"- **Total crashes:** {len(crashes)}")
        for sev, count in sev_counts.items():
            labels = {"K": "Fatal", "A": "Serious Injury", "B": "Minor Injury",
                      "C": "Possible Injury", "O": "PDO"}
            lines.append(f"- **{labels.get(sev, sev)} ({sev}):** {count}")

    lines.append("\n## Data Flags\n")
    flags = []
    if census.get("is_synthetic", pd.Series([False])).any():
        flags.append("- Census data is SYNTHETIC -- replace with ACS API pull before final analysis")
    if stops.get("is_synthetic", pd.Series([False])).any():
        flags.append("- Transit stops are SYNTHETIC -- replace with VTA GTFS download")
    if crashes.get("is_synthetic", pd.Series([False])).any():
        flags.append("- Crash data is SYNTHETIC -- replace with SWITRS query")
    if traffic.get("is_synthetic", pd.Series([False])).any():
        flags.append("- Traffic data is SYNTHETIC -- replace with PeMS download")

    if flags:
        lines.extend(flags)
    else:
        lines.append("- All data from live sources _")

    return "\n".join(lines)
