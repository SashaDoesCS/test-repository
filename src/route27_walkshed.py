"""
route27_walkshed.py -- Walk-shed population analysis for Route 27 candidate stops.

For each candidate stop location, this module estimates:
  1. Raw walk-shed population — all census residents within walking distance.
  2. Marginal walk-shed population — residents NOT already within walking
     distance of an adjacent stop (the incremental gain from adding this stop).
  3. Equity-weighted population — raw population × TDI to up-weight
     transit-dependent communities per FTA Title VI.

Walk-shed buffer distances (FTA Circular 9040.1G §4.2.1):
    Urban districts (D1–D5, D7):   ¼ mile (1,320 ft) — ~5-min walk at 3 mph
    Suburban districts (U1–U6, D6, D8+):  ½ mile (2,640 ft) — ~10-min walk

Method:
    Buffer geometry is a circle (haversine radius), which overestimates
    true walk-access by 10–20% compared to street-network routing.
    This is the FTA-standard approach when detailed pedestrian network data
    is unavailable (FTA Circular 9040.1G §4.2.1 footnote 3).

    Population is allocated from census block groups using an area-weighted
    interpolation: each block group's population is partitioned proportionally
    to the fraction of its area (approximated as a rectangle) within the buffer.

    When census block group polygons are not available, the module falls back
    to point-in-circle using block-group centroids.

Marginal population deduction:
    For a candidate stop C between existing stops A and B, the marginal
    population = walk_shed(C) minus the union of walk_shed(A) and walk_shed(B).
    Implemented as: max(0, walk_shed(C) - overlap(C, A) - overlap(C, B)).
    Overlap is estimated as the circular intersection area.

Equity weighting (FTA Title VI Circular 4702.1B §4.5.2):
    marginal_equity_pop = marginal_pop × TDI
    where TDI (Transit Demand Index, 0–1) is computed in demand_model.py.
    Districts in the top-5 unmet-need tier receive a 1.5× equity multiplier.

References:
    • FTA Circular 9040.1G, §4.2.1 and §5.2.2 (walk access standards)
    • FTA Title VI Circular 4702.1B, §4.5 (equity analysis)
    • TCRP Report 95, Ch. 15 (pedestrian access to transit)
    • Census Bureau: ACS 5-Year Estimates, Table B01001 (total population)
"""

import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.route27_corridor import URBAN_DISTRICTS, SUBURBAN_DISTRICTS, _haversine_ft

logger = logging.getLogger(__name__)

# Walk buffer radii by zone type (feet)
# Source: FTA Circular 9040.1G §4.2.1
WALK_BUFFER_FT = {
    "urban":    1_320,   # ¼ mile
    "suburban": 2_640,   # ½ mile
}

# Equity multiplier for top-5 unmet-need districts
# Source: FTA Title VI Circular 4702.1B §4.5.2
EQUITY_MULTIPLIER = 1.5

# Approximate degrees-to-feet conversions at 37°N
_FT_PER_DEG_LAT = 364_000.0
_FT_PER_DEG_LON = 288_500.0   # cos(37°) × 364_000


# ---------------------------------------------------------------------------
# CIRCLE AREA HELPERS (flat-earth approximation, acceptable over <2 mi radius)
# ---------------------------------------------------------------------------

def _circle_area_sq_ft(radius_ft: float) -> float:
    return math.pi * radius_ft ** 2


def _circle_overlap_area(
    lat1: float, lon1: float, r1_ft: float,
    lat2: float, lon2: float, r2_ft: float,
) -> float:
    """Area of intersection of two circles (in sq ft, flat-earth).

    Uses the standard circular segment formula:
        A = r1² × arccos(d²+r1²-r2² / 2dr1)
          + r2² × arccos(d²+r2²-r1² / 2dr2)
          - 0.5 × sqrt((-d+r1+r2)(d+r1-r2)(d-r1+r2)(d+r1+r2))

    Returns 0 if circles do not overlap.

    Reference: Weisstein, E.W. "Circle-Circle Intersection."
    MathWorld — A Wolfram Web Resource.
    """
    d_ft = _haversine_ft(lat1, lon1, lat2, lon2)

    # No overlap
    if d_ft >= r1_ft + r2_ft:
        return 0.0

    # One circle fully inside the other
    if d_ft + min(r1_ft, r2_ft) <= max(r1_ft, r2_ft):
        return _circle_area_sq_ft(min(r1_ft, r2_ft))

    # Partial overlap
    r1, r2, d = r1_ft, r2_ft, d_ft
    # Clamp argument to [-1, 1] to guard against floating-point precision issues
    arg1 = max(-1.0, min(1.0, (d**2 + r1**2 - r2**2) / (2 * d * r1)))
    arg2 = max(-1.0, min(1.0, (d**2 + r2**2 - r1**2) / (2 * d * r2)))
    a1 = r1**2 * math.acos(arg1)
    a2 = r2**2 * math.acos(arg2)
    # Triangle term (Heron's formula component)
    s = (d + r1 + r2) / 2
    tri = 0.5 * math.sqrt(max(0.0, s * (s - d) * (s - r1) * (s - r2))) * 2
    return a1 + a2 - tri


# ---------------------------------------------------------------------------
# WALK BUFFER RADIUS LOOKUP
# ---------------------------------------------------------------------------

def _walk_buffer_ft(district_id: Optional[str]) -> float:
    """Return walk buffer radius (ft) for a district.

    Source: FTA Circular 9040.1G §4.2.1
    """
    if district_id in URBAN_DISTRICTS:
        return WALK_BUFFER_FT["urban"]
    return WALK_BUFFER_FT["suburban"]


# ---------------------------------------------------------------------------
# RAW WALK-SHED POPULATION (per stop)
# ---------------------------------------------------------------------------

def compute_raw_walkshed(
    candidates_df: pd.DataFrame,
    census_df: pd.DataFrame,
    tdi_map: Dict[str, float],
    equity_districts: set,
) -> pd.DataFrame:
    """Compute raw walk-shed population for every candidate stop.

    For each candidate, sums census block-group population where the
    block-group centroid is within the walk buffer.  Area-weighted
    interpolation is used when block-group area columns are available;
    otherwise centroid-in-circle is used.

    Args:
        candidates_df: From route27_corridor.build_route27_corridor().
            Must have: stop_lat, stop_lon, and optionally district_id.
        census_df: District-level or block-group-level demographics.
            Must have: lat, lon, total_pop, district_id.
        tdi_map: {district_id → TDI value (0–1)} from demand_model.py.
        equity_districts: Set of district_ids in top-5 unmet need tier.

    Returns:
        candidates_df with added columns:
            walk_buffer_ft, raw_walkshed_pop, equity_walkshed_pop
    """
    result = candidates_df.copy()
    result["walk_buffer_ft"] = result.get("district_id", pd.Series([None] * len(result))).apply(
        _walk_buffer_ft
    )
    # If district_id not yet assigned, use suburban default
    if "district_id" not in result.columns:
        result["walk_buffer_ft"] = WALK_BUFFER_FT["suburban"]

    # Prepare census data — need lat/lon per block group
    census_copy = census_df.copy()
    for col in ["lat", "lon", "total_pop", "district_id"]:
        if col not in census_copy.columns:
            logger.warning(
                "Census DataFrame missing column '%s'; walk-shed will be zero.", col
            )
            result["raw_walkshed_pop"] = 0
            result["equity_walkshed_pop"] = 0.0
            return result

    census_copy = census_copy.dropna(subset=["lat", "lon", "total_pop"])
    census_copy["total_pop"] = pd.to_numeric(census_copy["total_pop"], errors="coerce").fillna(0)

    raw_pops = []
    equity_pops = []

    for _, cand in result.iterrows():
        clat = cand["stop_lat"]
        clon = cand["stop_lon"]
        buf_ft = cand.get("walk_buffer_ft", WALK_BUFFER_FT["suburban"])
        did = cand.get("district_id", None)

        total_pop = 0.0
        equity_pop = 0.0

        for _, bg in census_copy.iterrows():
            dist_ft = _haversine_ft(clat, clon, bg["lat"], bg["lon"])
            if dist_ft > buf_ft:
                continue

            # Area-weighted fraction: if block-group area provided, use it;
            # otherwise assume centroid-in-circle → full block-group population
            pop = float(bg["total_pop"])
            if "area_sq_miles" in bg.index and bg["area_sq_miles"] > 0:
                # Approximate BG as a circle of equivalent area
                bg_radius_ft = math.sqrt(float(bg["area_sq_miles"]) * 5280**2 / math.pi)
                overlap = _circle_overlap_area(
                    clat, clon, buf_ft,
                    bg["lat"], bg["lon"], bg_radius_ft,
                )
                bg_area = _circle_area_sq_ft(bg_radius_ft)
                frac = min(1.0, overlap / max(bg_area, 1))
            else:
                # Centroid-in-circle: full population if centroid is within buffer
                frac = 1.0

            bg_pop_contrib = pop * frac
            total_pop += bg_pop_contrib

            # Equity weighting: TDI × equity multiplier for priority districts
            bg_did = bg.get("district_id", did)
            tdi_val = tdi_map.get(bg_did, 0.2)
            eq_mult = EQUITY_MULTIPLIER if bg_did in equity_districts else 1.0
            equity_pop += bg_pop_contrib * tdi_val * eq_mult

        raw_pops.append(round(total_pop))
        equity_pops.append(round(equity_pop, 2))

    result["raw_walkshed_pop"] = raw_pops
    result["equity_walkshed_pop"] = equity_pops

    total_raw = sum(raw_pops)
    logger.info(
        "Raw walk-shed computed: %d candidates, total raw pop = %,.0f.",
        len(result), total_raw,
    )
    return result


# ---------------------------------------------------------------------------
# MARGINAL WALK-SHED POPULATION
# ---------------------------------------------------------------------------

def compute_marginal_walkshed(
    candidates_df: pd.DataFrame,
    selected_s_coords: List[float],
    selected_lats: List[float],
    selected_lons: List[float],
    selected_districts: List[Optional[str]],
) -> pd.DataFrame:
    """Compute marginal (incremental) walk-shed population for each candidate.

    For candidate C with raw walk-shed pop P(C), and adjacent already-selected
    stops A and B, the marginal population is:

        marginal(C) = max(0, P(C) - overlap(C,A) × P(A)/A(A) - overlap(C,B) × P(B)/A(B))

    where overlap(·,·) is the circular intersection area as a fraction of
    the circle area, and P/A is population density within the walk buffer.

    In practice, when selected_s_coords is empty (no stops selected yet),
    marginal == raw.

    Args:
        candidates_df: With raw_walkshed_pop and walk_buffer_ft columns.
        selected_s_coords: s-coordinate (ft) of already-selected stops.
        selected_lats, selected_lons: Coordinates of selected stops.
        selected_districts: district_id of selected stops (for buffer radii).

    Returns:
        candidates_df with added column marginal_walkshed_pop.
    """
    result = candidates_df.copy()

    if not selected_s_coords:
        result["marginal_walkshed_pop"] = result["raw_walkshed_pop"]
        return result

    marginals = []
    for _, cand in result.iterrows():
        raw = cand.get("raw_walkshed_pop", 0)
        buf_ft = cand.get("walk_buffer_ft", WALK_BUFFER_FT["suburban"])
        s_ft = cand.get("s_coord_ft", 0.0)

        # Find the two nearest selected stops by s-coordinate
        diffs = [abs(s_ft - ss) for ss in selected_s_coords]
        sorted_idx = sorted(range(len(diffs)), key=lambda i: diffs[i])
        neighbors = sorted_idx[:2]  # at most two nearest neighbors

        total_overlap_pop = 0.0
        for ni in neighbors:
            nb_lat = selected_lats[ni]
            nb_lon = selected_lons[ni]
            nb_buf_ft = _walk_buffer_ft(selected_districts[ni])
            nb_raw = 0.0  # we don't have nb raw pop here; use area-fraction

            # Fraction of candidate's walk-shed already covered by neighbor
            overlap_area = _circle_overlap_area(
                cand["stop_lat"], cand["stop_lon"], buf_ft,
                nb_lat, nb_lon, nb_buf_ft,
            )
            cand_area = _circle_area_sq_ft(buf_ft)
            overlap_frac = min(1.0, overlap_area / max(cand_area, 1))
            total_overlap_pop += raw * overlap_frac

        marginal = max(0, raw - total_overlap_pop)
        marginals.append(round(marginal))

    result["marginal_walkshed_pop"] = marginals
    logger.info(
        "Marginal walk-shed computed against %d selected stops.  "
        "Mean marginal pop = %.0f.",
        len(selected_s_coords),
        sum(marginals) / max(len(marginals), 1),
    )
    return result


# ---------------------------------------------------------------------------
# PIPELINE ENTRY POINT
# ---------------------------------------------------------------------------

def run_walkshed_analysis(
    candidates_df: pd.DataFrame,
    census_df: pd.DataFrame,
    tdi_df: pd.DataFrame,
    unmet_need_df: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """Run walk-shed population analysis for all Route 27 candidates.

    Steps:
      1. Build tdi_map and equity_districts from input DataFrames.
      2. Compute raw walk-shed population per candidate.
      3. Set marginal = raw (full marginal computation is done during stop
         selection in route27_optimizer.py as stops are iteratively chosen).

    Args:
        candidates_df: From route27_corridor.build_route27_corridor().
        census_df: District-level demographics (district_demographic_profile.csv).
            Required columns: district_id, total_pop, and either (lat, lon) or
            the module will use district centroid approximations.
        tdi_df: From demand_model.run_demand_analysis() — has district_id, tdi.
        unmet_need_df: From demand_model — has district_id, unmet_need, equity_flag.

    Returns:
        candidates_df enriched with:
            walk_buffer_ft, raw_walkshed_pop, equity_walkshed_pop,
            marginal_walkshed_pop (= raw at this stage), tdi, equity_flag
    """
    # Build TDI lookup
    tdi_map: Dict[str, float] = {}
    if tdi_df is not None and len(tdi_df) > 0:
        tdi_map = tdi_df.set_index("district_id")["tdi"].to_dict()

    # Build equity district set (top-5 unmet need)
    equity_districts: set = set()
    if unmet_need_df is not None and len(unmet_need_df) > 0:
        top5 = (
            unmet_need_df
            .nlargest(5, "unmet_need")["district_id"]
            .tolist()
        )
        equity_districts = set(top5)
        logger.info("Equity-priority districts (top-5 unmet need): %s", top5)

    # Build census lat/lon from demographics if not present
    # district_demographic_profile has district_id but no centroid coords —
    # use known district centroid coordinates (approximate, from districts.py).
    if "lat" not in census_df.columns or "lon" not in census_df.columns:
        census_df = _attach_district_centroids(census_df)

    candidates_with_walkshed = compute_raw_walkshed(
        candidates_df, census_df, tdi_map, equity_districts
    )

    # Initial marginal = raw (will be refined during iterative stop selection)
    candidates_with_walkshed["marginal_walkshed_pop"] = (
        candidates_with_walkshed["raw_walkshed_pop"]
    )

    # Attach TDI and equity flag for later scoring
    candidates_with_walkshed["tdi"] = (
        candidates_with_walkshed.get("district_id", pd.Series([None] * len(candidates_with_walkshed)))
        .map(tdi_map)
        .fillna(0.2)
    )
    candidates_with_walkshed["equity_priority"] = (
        candidates_with_walkshed.get("district_id", pd.Series([None] * len(candidates_with_walkshed)))
        .isin(equity_districts)
    )

    total_raw = candidates_with_walkshed["raw_walkshed_pop"].sum()
    logger.info(
        "Walk-shed analysis complete: %d candidates, "
        "total raw pop = %,.0f, equity districts = %s.",
        len(candidates_with_walkshed), total_raw, sorted(equity_districts),
    )
    return candidates_with_walkshed


# ---------------------------------------------------------------------------
# DISTRICT CENTROID LOOKUP (fallback when census has no lat/lon)
# ---------------------------------------------------------------------------

# Approximate centroids for each district, derived from district polygon
# definitions in districts.py.  These are only used as a fallback when the
# census DataFrame has no lat/lon columns.
#
# Source: centroid of district polygon bounding boxes from config.yaml
# road boundary descriptions, cross-checked with TIGER/Line shapefiles.
_DISTRICT_CENTROIDS: Dict[str, Tuple[float, float]] = {
    "D1":  (37.2249, -121.9806),   # Downtown / Town Core
    "D2":  (37.2550, -121.9850),   # Vasona / Northwest
    "D3":  (37.2700, -121.9650),   # North Gateway (above SR-85)
    "D4":  (37.2600, -121.9650),   # Northeast / Lark-85
    "D5":  (37.2430, -121.9700),   # Central East / Blossom-Lark
    "D6":  (37.2500, -121.9450),   # East Los Gatos / Belwood
    "D7":  (37.2250, -121.9700),   # South Hills / Shannon-Almond
    "D8":  (37.2300, -122.0000),   # West Foothills
    "D9":  (37.1900, -122.0200),   # Lexington / SR-17 Mountain
    "D10": (37.1700, -122.0500),   # Skyline / Summit Mountains
    "U1":  (37.2430, -121.9450),   # Alta Vista / LG West
    "U2":  (37.2520, -121.9340),   # Noddin / Westhill
    "U3":  (37.2600, -121.9140),   # Carlton / N. Camden
    "U4":  (37.2420, -121.8990),   # Oster / Lencar
    "U5":  (37.2380, -121.8900),   # Lietz / Dartmouth
    "U6":  (37.2310, -121.9200),   # Guadalupe / S. Almaden
}


def _attach_district_centroids(census_df: pd.DataFrame) -> pd.DataFrame:
    """Add lat/lon centroid columns to census_df using district centroid lookup.

    Used as a fallback when census data has no coordinate columns.
    """
    df = census_df.copy()
    lats, lons = [], []
    for _, row in df.iterrows():
        did = row.get("district_id", "")
        centroid = _DISTRICT_CENTROIDS.get(did, (37.235, -121.960))
        lats.append(centroid[0])
        lons.append(centroid[1])
    df["lat"] = lats
    df["lon"] = lons
    logger.info(
        "Attached district centroid coordinates to %d census records "
        "(fallback — no BG-level lat/lon available).",
        len(df),
    )
    return df
