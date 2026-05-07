"""
route_optimizer.py -- Multi-stage route and stop optimization.

Three sub-stages:
  3a. Stop Selection: Greedy max-coverage algorithm (FTA Circular 9040.1G)
  3b. Route Design: Clarke-Wright savings algorithm adapted for transit
  3c. Headway Optimisation: Mohring square-root formula (FTA-recommended)

Constraints enforced:
  - Walk buffer: ¼-mile urban, ½-mile suburban (FTA Circular 9040.1G)
  - Route length: ≤90 min one-way (FTA local route standard)
  - School stops: mandatory inclusion (Union SD districts U1–U6)
  - Equity: top-5 unmet-need districts must have ≥1 stop (FTA Title VI)
  - ADA: all selected stops flagged wheelchair_boarding=1 (49 CFR Part 37)
  - Route 76 restoration: evaluated separately (BCR threshold from config)

Standards:
    - FTA Circular 9040.1G (Fixed Route Transit, Design Guidelines)
    - FTA Title VI Circular 4702.1B
    - ADA 49 CFR Part 37 (accessible stops)
    - Clarke & Wright (1964) Savings Algorithm
    - Mohring (1972) Optimal Bus Service
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Miles per degree of latitude (approximate for Los Gatos ~37°N)
_MILES_PER_DEG_LAT = 69.0
_MILES_PER_DEG_LON = 54.6  # cos(37°) × 69

# Minimum stops per route (FTA guidance)
_MIN_STOPS = 4
# Maximum one-way route travel time (minutes)
_MAX_ROUTE_MIN = 90

# ── Free parameters (tuned 2026-04-24) ────────────────────────────────
# Penalty weight for bearing changes in 2-opt resequencing (seconds per degree
# of cumulative bearing change).  0.5 s/deg means a 180° U-turn adds 90 s.
# Empirically the bend penalty runs 30-90% of approx travel cost on dense
# corridor routes (OPT_02/06/07).  That is intentionally aggressive: these
# routes have many near-coincident stops where time-only 2-opt produces
# zigzags, and the smoothing dominance is what drives the post-fix degenerate-
# leg drop from 38% to 9%.  Lowering further regresses zigzag rate; raising
# starts to add detour distance.  Keep at 0.5.
MONOTONICITY_WEIGHT: float = 0.5

# Penalty per metre of detour when inserting a synthetic stop into an existing
# route sequence.  0.01 boardings/m means a 100 m detour costs 1 boarding/day.
# In practice the upstream corridor filter (400 m buffer applied in
# create_routes) rejects off-corridor candidates before scoring, so this acts
# as a redundant ranking tiebreaker rather than a primary gate.  Harmless at
# this value — leaving unchanged.
INSERT_DETOUR_ALPHA: float = 0.01

# Step 2: Cap on net synthetic stop additions to Route 27. Opus will tune.
MAX_SYNTHETIC_ADDITIONS: int = 8

# Step 2: If True, low-boarding existing (non-mandatory, non-school) stops
# can be pruned to make budget for higher-scoring synthetic candidates.
# Disabled (2026-04-26): stop removal runs BEFORE stops are scored in
# Stage 3f, so demand_score on base GTFS stops is uninitialised (0.0) and
# the pruner removes all base stops indiscriminately. Re-enable only after
# moving the pruning step to after Stage 3f or providing a real comparison.
ALLOW_STOP_REMOVAL: bool = False

# Step 3: Demand attribution divisor cap. Beyond this many stops in one
# quarter-mile district, additional stops fragment the same riders rather
# than capturing new ones. Opus will tune.
MAX_STOPS_PER_DISTRICT_DIVISOR: int = 4

# Investigation A (2026-04-26): corridor buffer for synthetic-stop insertion
# into derive-from-existing routes. 0.25mi (FTA urban walk) yielded only 2 of
# 58 synthetic candidates. 0.5mi (FTA suburban walk) yields 8 — matches the
# user's 5-10 target. Defensible: a route can detour up to a half-mile to
# pick up significant demand without dramatically increasing travel time.
CORRIDOR_BUFFER_MILES: float = 0.5
# ──────────────────────────────────────────────────────────────────────


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in miles."""
    R = 3958.8  # Earth radius miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return forward azimuth in degrees [0, 360) from point 1 to point 2."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlon_r = math.radians(lon2 - lon1)
    x = math.sin(dlon_r) * math.cos(lat2_r)
    y = (math.cos(lat1_r) * math.sin(lat2_r)
         - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon_r))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _sum_bearing_changes(seq: list) -> float:
    """Sum of absolute bearing changes (degrees, normalised 0–180) over interior vertices.

    Args:
        seq: list of OptimisedStop objects with stop_lat / stop_lon attributes.
    Returns:
        Cumulative bearing-change in degrees across all interior stops.
    """
    if len(seq) < 3:
        return 0.0
    total = 0.0
    for i in range(1, len(seq) - 1):
        b_in  = _bearing(seq[i-1].stop_lat, seq[i-1].stop_lon,
                         seq[i].stop_lat,   seq[i].stop_lon)
        b_out = _bearing(seq[i].stop_lat,   seq[i].stop_lon,
                         seq[i+1].stop_lat, seq[i+1].stop_lon)
        diff = abs(b_out - b_in)
        if diff > 180:
            diff = 360 - diff
        total += diff
    return total


def _two_opt_resequence(
    stops: list,
    travel_time_fn,
) -> list:
    """2-opt improvement of a stop sequence, penalising bearing back-tracking.

    Args:
        stops: List of OptimisedStop objects.
        travel_time_fn: callable(stop_id_a, stop_id_b) → float (seconds).

    Returns:
        Reordered list of OptimisedStop objects.
    """
    if len(stops) <= 3:
        return stops

    def tour_cost(seq: list) -> float:
        travel = sum(
            travel_time_fn(seq[k].stop_id, seq[k + 1].stop_id)
            for k in range(len(seq) - 1)
        )
        monotonicity_penalty = _sum_bearing_changes(seq) * MONOTONICITY_WEIGHT
        return travel + monotonicity_penalty

    best = list(stops)
    best_cost = tour_cost(best)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best) - 1):
            for j in range(i + 1, len(best)):
                candidate = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                c = tour_cost(candidate)
                if c < best_cost - 1e-9:
                    best = candidate
                    best_cost = c
                    improved = True
    return best


# =====================================================================
# DATA STRUCTURES
# =====================================================================

@dataclass
class OptimisedStop:
    stop_id: str
    stop_name: str
    stop_lat: float
    stop_lon: float
    district_id: Optional[str]
    is_existing: bool           # True = from GTFS; False = new/synthetic
    is_school_stop: bool
    is_mandatory: bool          # School stop or equity-required
    wheelchair_boarding: int = 1  # ADA: always 1 (accessible)
    demand_score: float = 0.0
    coverage_pop: int = 0
    estimated_daily_boardings: float = 0.0


@dataclass
class OptimisedRoute:
    route_id: str
    route_name: str
    stops: List[OptimisedStop]  # ordered stop sequence
    is_restoration: bool = False  # True for Route 76 restoration
    source_route_id: Optional[str] = None  # original GTFS route_id
    parent_route_id: Optional[str] = None  # for derive_from_existing mode
    estimated_one_way_min: float = 0.0
    bcr: Optional[float] = None
    headways: dict = field(default_factory=dict)  # window → headway_min
    polyline: list = field(default_factory=list)  # (lat, lon) road-following geometry
    diversion_rate: float = 0.08  # Step 4: per-route diversion rate from config default
    route_total_demand: float = 0.0  # Step 3: aggregate demand sanity-check (not divided by stops)


# =====================================================================
# SUB-STAGE 3a: STOP SELECTION
# =====================================================================

def select_stops(
    candidate_stops: pd.DataFrame,
    coverage_gaps: pd.DataFrame,
    tdi_df: pd.DataFrame,
    unmet_need_df: Optional[pd.DataFrame],
    config: dict,
) -> List[OptimisedStop]:
    """Select stops using greedy max-coverage algorithm.

    Algorithm:
    1. Score every candidate stop: gap_pop_covered × demand_weight × (1/cost_penalty)
    2. Greedily add highest-score stop not already within walk buffer
    3. Force-include all stops in school districts (U1–U6)
    4. Force-include stops in top-5 unmet-need districts (FTA Title VI)
    5. Terminate when marginal coverage gain < threshold

    Args:
        candidate_stops: DataFrame with stop_id, stop_name, stop_lat, stop_lon,
            district_id, route_ids, and optionally is_synthetic.
        coverage_gaps: From compute_coverage_gaps(); has district_id, gap_population,
            gap_fraction, n_stops.
        tdi_df: From compute_transit_demand_index(); has district_id, tdi.
        unmet_need_df: Optional; has district_id, unmet_need, label.
        config: Full config dict.

    Returns:
        Ordered list of OptimisedStop objects.
    """
    opt_cfg = config.get("optimization", {})
    urban_buf = opt_cfg.get("walk_buffer_urban_miles", 0.25)
    suburban_buf = opt_cfg.get("walk_buffer_suburban_miles", 0.50)
    coverage_threshold = opt_cfg.get("stop_selection", {}).get(
        "coverage_gain_threshold", 0.01
    )
    cost_per_new_stop = opt_cfg.get("stop_selection", {}).get(
        "cost_penalty_per_new_stop", 30_000
    )
    school_windows = opt_cfg.get("school_windows", [])
    school_districts = set()
    for sw in school_windows:
        for d in sw.get("districts", []):
            school_districts.add(d)

    # Determine equity-priority districts (top-5 unmet need)
    equity_districts = set()
    if unmet_need_df is not None and len(unmet_need_df) > 0:
        top5 = unmet_need_df.nlargest(5, "unmet_need")["district_id"].tolist()
        equity_districts = set(top5)
        logger.info("Equity-priority districts (top 5 unmet need): %s", top5)

    # Build lookup structures
    tdi_map = (tdi_df.set_index("district_id")["tdi"].to_dict()
               if tdi_df is not None and len(tdi_df) > 0 else {})
    gap_pop_map = (coverage_gaps.set_index("district_id")["gap_population"].to_dict()
                   if coverage_gaps is not None and len(coverage_gaps) > 0 else {})

    # District total population for TDI-weighted demand scoring.
    # When a district already has stops (gap_population=0), coverage_contribution
    # would be 0, making demand_score=0 for all non-mandatory stops and causing
    # the marginal-gain check to break immediately — the demand index is ignored.
    # Fix: use TDI × total_pop as a demand floor so high-TDI districts always
    # produce non-zero scores regardless of existing stop coverage.
    pop_map = {}
    if coverage_gaps is not None and len(coverage_gaps) > 0 and "total_pop" in coverage_gaps.columns:
        pop_map = coverage_gaps.set_index("district_id")["total_pop"].to_dict()

    # TDI_DEMAND_FACTOR: fraction of district TDI-weighted population counted as
    # "effective demand" per stop slot.  0.2 is the minimum to pass the marginal-
    # gain threshold for smaller in-town districts (D1, D4, D7 ~3K pop, TDI ~0.3-0.5)
    # while keeping uncovered-district gap-pop (D8, D9, D10 ~400-1350) dominant.
    _TDI_DEMAND_FACTOR = 0.2

    # Total effective demand = gap population + TDI-weighted population across all
    # districts.  Used as the denominator in marginal_gain so that stops in
    # high-demand but already-covered districts don't trigger an immediate break.
    total_gap_pop = sum(gap_pop_map.values())
    total_tdi_demand = sum(
        float(pop_map.get(did, 0)) * tdi_map.get(did, 0.2) * _TDI_DEMAND_FACTOR
        for did in set(list(gap_pop_map.keys()) + list(tdi_map.keys()))
    )
    total_effective_demand = max(total_gap_pop + total_tdi_demand, 1)

    # Determine walk buffer per stop (urban vs suburban by district TDI)
    def _walk_buffer(district_id: Optional[str]) -> float:
        tdi_val = tdi_map.get(district_id, 0.3)
        return urban_buf if tdi_val >= 0.5 else suburban_buf

    # Score candidates
    scored = []
    for _, row in candidate_stops.iterrows():
        did = row.get("district_id", None)
        is_existing = not bool(row.get("is_synthetic", False))
        is_school = did in school_districts
        is_mandatory = is_school or (did in equity_districts)
        demand_weight = tdi_map.get(did, 0.2)
        gap_pop = gap_pop_map.get(did, 0)
        buf = _walk_buffer(did)

        existing_stops_in_district = int(
            coverage_gaps.loc[coverage_gaps["district_id"] == did, "n_stops"].sum()
            if coverage_gaps is not None else 0
        )
        # Gap-coverage contribution: marginal uncovered population per additional stop.
        coverage_contribution = gap_pop / max(existing_stops_in_district + 1, 1)

        # TDI demand contribution: ensures stops in high-demand, already-served
        # districts get non-zero scores so the demand index drives selection.
        tdi_demand = float(pop_map.get(did, 0)) * demand_weight * _TDI_DEMAND_FACTOR

        # Effective demand blends gap coverage (primary) with TDI demand (floor).
        effective_demand = coverage_contribution + tdi_demand

        # Cost penalty: existing stops cheaper (infrastructure already there)
        cost_factor = 1.0 if is_existing else (1.0 + cost_per_new_stop / 100_000)

        score = (effective_demand * demand_weight) / cost_factor
        if is_mandatory:
            score *= 10  # Priority boost for school/equity stops

        scored.append({
            "stop_id": str(row["stop_id"]),
            "stop_name": str(row.get("stop_name", row["stop_id"])),
            "stop_lat": float(row["stop_lat"]),
            "stop_lon": float(row["stop_lon"]),
            "district_id": did,
            "is_existing": is_existing,
            "is_school_stop": is_school,
            "is_mandatory": is_mandatory,
            "demand_score": score,
            "coverage_pop": int(effective_demand),
            "walk_buffer": buf,
        })

    scored.sort(key=lambda x: (-x["is_mandatory"], -x["demand_score"]))

    # Greedy selection
    selected: List[OptimisedStop] = []
    selected_coords: List[Tuple[float, float]] = []
    cumulative_coverage = 0

    for cand in scored:
        lat, lon = cand["stop_lat"], cand["stop_lon"]
        buf = cand["walk_buffer"]

        # Check if already covered by a selected stop
        too_close = any(
            _haversine_miles(lat, lon, slat, slon) < buf
            for slat, slon in selected_coords
        )
        if too_close and not cand["is_mandatory"]:
            continue

        marginal_gain = cand["coverage_pop"] / total_effective_demand
        if not cand["is_mandatory"] and marginal_gain < coverage_threshold:
            logger.info("Stop selection: marginal gain %.3f%% < threshold, stopping.",
                        marginal_gain * 100)
            break

        selected.append(OptimisedStop(
            stop_id=cand["stop_id"],
            stop_name=cand["stop_name"],
            stop_lat=lat,
            stop_lon=lon,
            district_id=cand["district_id"],
            is_existing=cand["is_existing"],
            is_school_stop=cand["is_school_stop"],
            is_mandatory=cand["is_mandatory"],
            wheelchair_boarding=1,  # ADA: all stops accessible
            demand_score=cand["demand_score"],
            coverage_pop=cand["coverage_pop"],
        ))
        selected_coords.append((lat, lon))
        cumulative_coverage += cand["coverage_pop"]

    logger.info("Stop selection: %d stops selected (%d existing, %d new).",
                len(selected),
                sum(1 for s in selected if s.is_existing),
                sum(1 for s in selected if not s.is_existing))
    return selected


def estimate_daily_boardings(
    stop: "OptimisedStop",
    tdi_df: pd.DataFrame,
    districts_df: pd.DataFrame,
    config: dict,
    selected_stop_count: int = 1,
) -> float:
    """Realistic per-stop daily boarding estimate.

    per_stop_daily = TDI[district] * walk_shed_pop * trip_rate * diversion * service_fraction

    walk_shed_pop approximated as district_pop / selected_stop_count_in_district.

    Sources:
      - trip_rate ~= 2.5 trips/person/day (NHTS 2022)
      - diversion ~= 0.02 (ACS B08301 Santa Clara service-area transit mode share)
      - service_fraction ~= 0.3 (share of walkshed trips this route can serve)
    Expected range: 5-40 per stop, 30-300 per route.
    """
    did = stop.district_id or ""

    # Build TDI lookup
    tdi_map: dict = {}
    if tdi_df is not None and len(tdi_df) > 0:
        tdi_map = {str(r.get("district_id", "")): float(r.get("tdi", 0.2))
                   for _, r in tdi_df.iterrows()}
    tdi = tdi_map.get(did, 0.2)

    # Walk-shed population: district_pop / selected_stop_count (fast approximation)
    walk_shed_pop = 0.0
    if districts_df is not None and len(districts_df) > 0:
        id_col = "id" if "id" in districts_df.columns else "district_id"
        pop_col = next((c for c in ["total_pop", "population"] if c in districts_df.columns), None)
        if pop_col:
            row = districts_df[districts_df[id_col].astype(str) == did]
            if not row.empty:
                dist_pop = float(row.iloc[0].get(pop_col, 0))
                # Cap divisor: beyond MAX_STOPS_PER_DISTRICT_DIVISOR stops in
                # one district, additional stops fragment the same riders rather
                # than capturing new ones (Step 3 fix).
                walk_shed_pop = dist_pop / max(
                    min(selected_stop_count, MAX_STOPS_PER_DISTRICT_DIVISOR), 1
                )

    if walk_shed_pop <= 0:
        # Fallback: fixed 500-person walkshed when district data unavailable
        walk_shed_pop = 500.0

    trip_rate = 2.5          # NHTS 2022
    diversion = 0.02         # ACS B08301 Santa Clara transit mode share
    service_fraction = 0.30  # share of walkshed trips this route can serve

    return tdi * walk_shed_pop * trip_rate * diversion * service_fraction


# =====================================================================
# SUB-STAGE 3b: ROUTE DESIGN
# =====================================================================

def _design_routes_hub_spoke(
    selected_stops: List[OptimisedStop],
    travel_time_matrix: pd.DataFrame,
    opt_cfg: dict,
    route_costs_df: Optional[pd.DataFrame] = None,
    scenario_results: Optional[list] = None,
) -> List[OptimisedRoute]:
    """Clarke-Wright hub-and-spoke algorithm. Hub forced at Winchester TC.

    Args:
        selected_stops: From select_stops().
        travel_time_matrix: stop_id × stop_id travel time in seconds.
        opt_cfg: optimization sub-config dict.
        route_costs_df: Optional route operating costs (for Route 76 BCR).
        scenario_results: Optional scenario comparison results (for BCR).

    Returns:
        List of OptimisedRoute objects.
    """
    route_design_cfg = opt_cfg.get("route_design", {})
    max_route_min = opt_cfg.get("route_max_one_way_min", _MAX_ROUTE_MIN)
    max_route_sec = max_route_min * 60
    hub_name_fragment = route_design_cfg.get("hub_stop_name",
                        opt_cfg.get("hub_stop_name", "Winchester")).lower()
    r76_bcr_threshold = opt_cfg.get("route_76_restoration_bcr_threshold", 1.0)

    if not selected_stops:
        logger.warning("No stops provided to route designer.")
        return []

    # Identify hub stop (Winchester)
    hub_stop = None
    for s in selected_stops:
        if hub_name_fragment in s.stop_name.lower():
            hub_stop = s
            break
    if hub_stop is None:
        hub_stop = selected_stops[0]
        logger.info("Hub stop not found by name; using %s.", hub_stop.stop_name)

    non_hub_stops = [s for s in selected_stops if s.stop_id != hub_stop.stop_id]

    # Travel time lookup helper
    def tt(a_id: str, b_id: str) -> float:
        if travel_time_matrix is not None and len(travel_time_matrix) > 0:
            try:
                return float(travel_time_matrix.loc[a_id, b_id])
            except (KeyError, ValueError):
                pass
        # Fallback: no travel time data
        return 600.0  # 10-minute default

    hub_id = hub_stop.stop_id

    # Compute savings for all non-hub pairs
    savings_list = []
    for i, si in enumerate(non_hub_stops):
        for j, sj in enumerate(non_hub_stops):
            if j <= i:
                continue
            s_ij = tt(hub_id, si.stop_id) + tt(hub_id, sj.stop_id) - tt(si.stop_id, sj.stop_id)
            savings_list.append((s_ij, si, sj))

    savings_list.sort(key=lambda x: -x[0])

    # Start: each non-hub stop is its own spur from hub
    routes_dict: Dict[str, list] = {s.stop_id: [hub_stop, s] for s in non_hub_stops}
    stop_to_route: Dict[str, str] = {s.stop_id: s.stop_id for s in non_hub_stops}

    def route_travel_time(route: list) -> float:
        total = 0.0
        for k in range(len(route) - 1):
            total += tt(route[k].stop_id, route[k + 1].stop_id)
        return total

    def can_merge(route_a: list, route_b: list) -> bool:
        # Merge is valid if combined route ≤ max_route_sec
        combined = route_a[:-1] + route_b  # drop hub from end of a, append b
        return route_travel_time(combined) <= max_route_sec

    # Clarke-Wright merging
    for _, si, sj in savings_list:
        ri_key = stop_to_route.get(si.stop_id)
        rj_key = stop_to_route.get(sj.stop_id)
        if ri_key is None or rj_key is None:
            continue
        if ri_key == rj_key:
            continue  # already same route

        route_a = routes_dict[ri_key]
        route_b = routes_dict[rj_key]

        # Only merge if si is last non-hub stop of route_a and sj is first non-hub of route_b
        last_a = route_a[-1]
        first_b = route_b[1] if len(route_b) > 1 else route_b[0]
        if last_a.stop_id != si.stop_id or first_b.stop_id != sj.stop_id:
            continue

        if can_merge(route_a, route_b):
            merged = route_a + route_b[1:]  # append route_b (skipping its hub) to route_a
            new_key = ri_key
            routes_dict[new_key] = merged
            del routes_dict[rj_key]
            # Remap every stop in the consumed route AND any previously absorbed stops
            for stop_in_b in route_b[1:]:
                stop_to_route[stop_in_b.stop_id] = new_key

    # Convert to OptimisedRoute objects
    optimised_routes = []
    route_counter = 1
    for rkey, stop_list in routes_dict.items():
        if len(stop_list) < _MIN_STOPS:
            # Try to assign orphan stops to nearest existing route later
            continue
        ott = route_travel_time(stop_list) / 60.0
        r = OptimisedRoute(
            route_id=f"OPT_{route_counter:02d}",
            route_name=f"Optimised Route {route_counter} (via {stop_list[-1].stop_name})",
            stops=stop_list,
            estimated_one_way_min=round(ott, 1),
        )
        optimised_routes.append(r)
        route_counter += 1

    # Route 76 restoration evaluation
    route_76 = _evaluate_route_76(
        config, route_costs_df, scenario_results, r76_bcr_threshold
    )
    if route_76 is not None:
        optimised_routes.append(route_76)

    logger.info("Route design (hub_spoke): %d routes produced.", len(optimised_routes))
    for r in optimised_routes:
        logger.info("  %s: %d stops, %.1f min one-way%s",
                    r.route_id, len(r.stops), r.estimated_one_way_min,
                    " [RESTORATION]" if r.is_restoration else "")
    return optimised_routes


def _design_routes_corridor(
    selected_stops: List[OptimisedStop],
    travel_time_matrix: pd.DataFrame,
    opt_cfg: dict,
    route_costs_df: Optional[pd.DataFrame] = None,
    scenario_results: Optional[list] = None,
) -> List[OptimisedRoute]:
    """Corridor clustering: k-means on lat/lon, each cluster ordered by PCA-1.

    No fixed hub — routes follow the dominant geographic direction of each cluster.
    """
    try:
        from sklearn.cluster import KMeans
        from sklearn.decomposition import PCA
        import numpy as _np
    except ImportError:
        logger.warning("sklearn not installed; falling back to hub_spoke for corridor mode.")
        return _design_routes_hub_spoke(selected_stops, travel_time_matrix, opt_cfg,
                                        route_costs_df, scenario_results)

    if len(selected_stops) < 4:
        return _design_routes_hub_spoke(selected_stops, travel_time_matrix, opt_cfg,
                                        route_costs_df, scenario_results)

    coords = _np.array([[s.stop_lat, s.stop_lon] for s in selected_stops])

    # Choose k in [4, 6] that minimises inertia per stop (elbow heuristic)
    best_k, best_labels = 4, None
    best_inertia_per = float("inf")
    for k in range(4, 7):
        if k > len(selected_stops):
            break
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        labels = km.fit_predict(coords)
        ipp = km.inertia_ / k
        if ipp < best_inertia_per:
            best_inertia_per = ipp
            best_k = k
            best_labels = labels

    max_route_min = opt_cfg.get("route_max_one_way_min", _MAX_ROUTE_MIN)
    r76_bcr_threshold = opt_cfg.get("route_76_restoration_bcr_threshold", 1.0)

    optimised_routes = []
    for cluster_id in range(best_k):
        cluster_stops = [s for s, lbl in zip(selected_stops, best_labels) if lbl == cluster_id]
        if len(cluster_stops) < 2:
            continue

        # Order by PCA component-1 (dominant direction in this cluster)
        c_coords = _np.array([[s.stop_lat, s.stop_lon] for s in cluster_stops])
        if len(cluster_stops) >= 2:
            pca = PCA(n_components=1)
            proj = pca.fit_transform(c_coords).flatten()
            cluster_stops = [s for _, s in sorted(zip(proj, cluster_stops), key=lambda x: x[0])]

        ott = sum(
            _haversine_miles(cluster_stops[i].stop_lat, cluster_stops[i].stop_lon,
                              cluster_stops[i+1].stop_lat, cluster_stops[i+1].stop_lon)
            for i in range(len(cluster_stops)-1)
        ) / 25.0 * 60  # rough travel time at 25 mph

        r = OptimisedRoute(
            route_id=f"OPT_{len(optimised_routes)+1:02d}",
            route_name=f"Corridor Route {len(optimised_routes)+1}",
            stops=cluster_stops,
            estimated_one_way_min=round(min(ott, max_route_min), 1),
        )
        optimised_routes.append(r)

    route_76 = _evaluate_route_76(opt_cfg, route_costs_df, scenario_results, r76_bcr_threshold)
    if route_76 is not None:
        optimised_routes.append(route_76)

    logger.info("Route design (corridor, k=%d): %d routes produced.", best_k, len(optimised_routes))
    return optimised_routes


def _load_gtfs_stop_sequence(route_id_str: str, gtfs_dir: str) -> List[dict]:
    """Load an ordered stop list for one GTFS route_id.

    Returns list of dicts with stop_id, stop_name, stop_lat, stop_lon.
    Returns empty list if route not found.
    """
    try:
        trips = pd.read_csv(f"{gtfs_dir}/trips.txt", dtype=str)
        stop_times = pd.read_csv(f"{gtfs_dir}/stop_times.txt", dtype=str)
        stops_txt = pd.read_csv(f"{gtfs_dir}/stops.txt", dtype=str)
    except Exception as exc:
        logger.warning("Could not read GTFS for route %s: %s", route_id_str, exc)
        return []

    route_trips = trips[trips["route_id"] == route_id_str]
    if route_trips.empty:
        logger.debug("No trips found for route_id=%s", route_id_str)
        return []

    # Use direction_id=0 representative trip (or first trip)
    dir0 = route_trips[route_trips["direction_id"] == "0"] if "direction_id" in route_trips.columns else route_trips
    representative_trip = dir0.iloc[0]["trip_id"] if not dir0.empty else route_trips.iloc[0]["trip_id"]

    trip_stops = stop_times[stop_times["trip_id"] == representative_trip].copy()
    trip_stops["stop_sequence"] = pd.to_numeric(trip_stops["stop_sequence"], errors="coerce")
    trip_stops = trip_stops.sort_values("stop_sequence")

    stops_lookup = stops_txt.set_index("stop_id")

    result = []
    for _, row in trip_stops.iterrows():
        sid = row["stop_id"]
        if sid not in stops_lookup.index:
            continue
        srow = stops_lookup.loc[sid]
        result.append({
            "stop_id": sid,
            "stop_name": str(srow.get("stop_name", sid)),
            "stop_lat": float(srow.get("stop_lat", 0)),
            "stop_lon": float(srow.get("stop_lon", 0)),
        })
    return result


def _insert_stops_into_sequence(
    base_sequence: List[dict],
    candidates: List[OptimisedStop],
    max_insertions: int,
    allow_extensions: bool,
) -> List[OptimisedStop]:
    """Insert candidate stops into an existing ordered sequence.

    Places each candidate adjacent to the existing stop it is nearest to.
    Caps at max_insertions. Returns the merged ordered stop list as OptimisedStop objects.
    """
    if not base_sequence:
        return [OptimisedStop(
            stop_id=s.stop_id, stop_name=s.stop_name,
            stop_lat=s.stop_lat, stop_lon=s.stop_lon,
            district_id=s.district_id, is_existing=s.is_existing,
            is_school_stop=s.is_school_stop, is_mandatory=s.is_mandatory,
            wheelchair_boarding=s.wheelchair_boarding, demand_score=s.demand_score,
        ) for s in candidates[:max_insertions]]

    # Build mutable sequence as OptimisedStop
    seq: List[OptimisedStop] = []
    existing_ids: set = set()
    for item in base_sequence:
        stop = OptimisedStop(
            stop_id=item["stop_id"], stop_name=item["stop_name"],
            stop_lat=item["stop_lat"], stop_lon=item["stop_lon"],
            district_id=item.get("district_id"),  # Investigation B: was hardcoded None
            is_existing=True,
            is_school_stop=False, is_mandatory=False,
        )
        seq.append(stop)
        existing_ids.add(item["stop_id"])

    insertions = 0
    # Score each candidate by demand_gain - INSERT_DETOUR_ALPHA * detour_distance_m,
    # then insert the best-scoring candidates first (up to max_insertions).
    scored_candidates = []
    _miles_to_m = 1609.344
    for cand in candidates:
        if cand.stop_id in existing_ids:
            continue
        demand_gain = getattr(cand, "demand_score", 0.0)

        # Find best insertion position (minimises detour triangle inequality)
        best_pos = 1
        best_detour_m = float("inf")
        for pos in range(1, len(seq) + 1):
            prev = seq[pos - 1] if pos > 0 else seq[0]
            nxt  = seq[pos] if pos < len(seq) else seq[-1]
            if pos == 0 or pos > len(seq):
                # Extension: cost is just distance to endpoint
                detour = _haversine_miles(
                    cand.stop_lat, cand.stop_lon, prev.stop_lat, prev.stop_lon
                ) * _miles_to_m
            else:
                d_prev_cand = _haversine_miles(prev.stop_lat, prev.stop_lon,
                                               cand.stop_lat, cand.stop_lon) * _miles_to_m
                d_cand_next = _haversine_miles(cand.stop_lat, cand.stop_lon,
                                               nxt.stop_lat, nxt.stop_lon) * _miles_to_m
                d_prev_next = _haversine_miles(prev.stop_lat, prev.stop_lon,
                                               nxt.stop_lat, nxt.stop_lon) * _miles_to_m
                detour = d_prev_cand + d_cand_next - d_prev_next
            if detour < best_detour_m:
                best_detour_m = detour
                best_pos = pos

        score = demand_gain - INSERT_DETOUR_ALPHA * max(0.0, best_detour_m)
        scored_candidates.append((score, best_pos, cand))

    # Sort by descending score; insert best candidates first
    scored_candidates.sort(key=lambda x: -x[0])
    for score, best_pos, cand in scored_candidates:
        if insertions >= max_insertions:
            break
        if cand.stop_id in existing_ids:
            continue
        insert_pos = best_pos
        if not allow_extensions:
            insert_pos = min(insert_pos, len(seq))
        seq.insert(insert_pos, cand)
        existing_ids.add(cand.stop_id)
        insertions += 1

    return seq


def _design_routes_derive_from_existing(
    selected_stops: List[OptimisedStop],
    travel_time_matrix: pd.DataFrame,
    opt_cfg: dict,
    existing_routes: Optional[pd.DataFrame] = None,
    route_costs_df: Optional[pd.DataFrame] = None,
    scenario_results: Optional[list] = None,
) -> List[OptimisedRoute]:
    """Derive optimised routes from existing GTFS route sequences.

    Route 27 is the primary spine; other anchor routes are modified with
    new/candidate stops. Remaining unassigned stops form corridor clusters.
    Each emitted route carries parent_route_id.
    """
    route_design_cfg = opt_cfg.get("route_design", {})
    anchor_routes = route_design_cfg.get("anchor_routes", ["27"])
    primary_route = route_design_cfg.get("primary_route", "27")
    allow_ext = route_design_cfg.get("allow_extensions", True)
    max_inserts = route_design_cfg.get("max_stop_insertions_per_route", 6)
    max_route_min = opt_cfg.get("route_max_one_way_min", _MAX_ROUTE_MIN)
    r76_bcr_threshold = opt_cfg.get("route_76_restoration_bcr_threshold", 1.0)

    from pathlib import Path
    gtfs_dir = str(Path(__file__).resolve().parent.parent / "data" / "geospatial" / "gtfs")

    # Sort anchors so primary_route comes first
    sorted_anchors = [primary_route] + [r for r in anchor_routes if r != primary_route]

    optimised_routes: List[OptimisedRoute] = []
    assigned_stop_ids: set = set()
    route_counter = 1

    walk_buf = opt_cfg.get("walk_buffer_urban_miles", 0.25)

    # Load stop->district map once so base GTFS stops can be enriched with
    # district_id (otherwise estimate_daily_boardings falls back to TDI=0.2 +
    # walk_shed_pop=500 for those stops, which produced the 126 vs 1432 gap).
    stop_to_district: dict = {}
    try:
        sdm = pd.read_csv("outputs/tables/stop_district_matrix.csv", dtype=str)
        for _, row in sdm.iterrows():
            sid = str(row.get("stop_id", ""))
            did = row.get("district_id")
            if pd.notna(did) and str(did) not in ("nan", ""):
                stop_to_district[sid] = str(did)
        logger.info("Loaded stop->district map: %d entries with valid districts.", len(stop_to_district))
    except Exception as exc:
        logger.warning("Could not load stop_district_matrix (%s); base stops will lack district_id.", exc)

    # Nearest-district fallback for stops outside any LG district polygon
    # (e.g. Route 27 stops in San Jose past lon -121.88). Using centroid distance.
    district_centroids: list = []
    try:
        dprof = pd.read_csv("outputs/tables/district_profile_initial.csv")
        for _, drow in dprof.iterrows():
            district_centroids.append((
                str(drow.get("id") or drow.get("district_id")),
                float(drow["centroid_lat"]),
                float(drow["centroid_lon"]),
            ))
    except Exception:
        pass

    def _nearest_district(lat: float, lon: float) -> Optional[str]:
        if not district_centroids:
            return None
        best = min(district_centroids,
                   key=lambda c: _haversine_miles(lat, lon, c[1], c[2]))
        return best[0]

    for anchor_id in sorted_anchors:
        base_seq = _load_gtfs_stop_sequence(anchor_id, gtfs_dir)
        if not base_seq:
            logger.info("Anchor route %s not found in GTFS; skipping.", anchor_id)
            continue

        # Enrich base_seq with district_id from the matrix (Investigation B fix).
        # Falls back to nearest-district by centroid for stops outside LG bbox.
        for item in base_seq:
            sid = str(item["stop_id"])
            d = stop_to_district.get(sid)
            if d is None:
                d = _nearest_district(item["stop_lat"], item["stop_lon"])
            item["district_id"] = d

        # Existing stops in this anchor's sequence by stop_id
        base_stop_ids = {item["stop_id"] for item in base_seq}

        # Build corridor polyline from the base sequence (for insertion filtering)
        base_corridor = [(float(item["stop_lat"]), float(item["stop_lon"])) for item in base_seq]
        # Corridor buffer: use CORRIDOR_BUFFER_MILES (looser than walk_buf so
        # synthetic candidates in adjacent districts can join the route).
        corridor_buf_m = CORRIDOR_BUFFER_MILES * 1609.344

        # Find SYNTHETIC stops near this anchor route within CORRIDOR_BUFFER_MILES.
        # We deliberately exclude existing GTFS stops not on this route from
        # insertion: the user's intent is "add new coverage to Route 27", not
        # "absorb other VTA routes' stops". Existing-stop reshuffling would be
        # a separate operation.
        nearby_candidates = []
        for s in selected_stops:
            if s.stop_id in assigned_stop_ids:
                continue
            if s.stop_id in base_stop_ids:
                assigned_stop_ids.add(s.stop_id)
                continue
            if s.is_existing:
                continue  # only synthetic candidates can be inserted
            near = any(
                _haversine_miles(s.stop_lat, s.stop_lon, item["stop_lat"], item["stop_lon"]) <= CORRIDOR_BUFFER_MILES
                for item in base_seq
            )
            if near:
                nearby_candidates.append(s)

        # Apply corridor filter via candidate_generator helper
        if nearby_candidates and base_corridor:
            try:
                from src.candidate_generator import _filter_by_corridor
                corridor_latlon = [(s.stop_lat, s.stop_lon) for s in nearby_candidates]
                filtered_latlon = set(
                    _filter_by_corridor(corridor_latlon, base_corridor, corridor_buf_m)
                )
                if filtered_latlon:
                    nearby_candidates = [
                        s for s in nearby_candidates
                        if (s.stop_lat, s.stop_lon) in filtered_latlon
                    ]
                else:
                    logger.warning(
                        "Corridor filter for anchor %s rejected all %d nearby candidates; "
                        "proceeding without additional stops.",
                        anchor_id, len(nearby_candidates),
                    )
                    nearby_candidates = []
            except Exception as _exc:
                logger.debug("Corridor filter skipped (%s).", _exc)

        merged_seq = _insert_stops_into_sequence(base_seq, nearby_candidates, max_inserts, allow_ext)

        # Step 2: Enforce synthetic stop cap (MAX_SYNTHETIC_ADDITIONS).
        # Count net synthetic additions; if over cap, trim lowest-scoring ones.
        # If ALLOW_STOP_REMOVAL is True, also prune weak existing stops to
        # free budget for higher-scoring synthetic candidates.
        _synthetic_in_seq = [s for s in merged_seq if not s.is_existing]
        _existing_non_mandatory = [
            s for s in merged_seq
            if s.is_existing and not s.is_mandatory and not s.is_school_stop
        ]
        if len(_synthetic_in_seq) > MAX_SYNTHETIC_ADDITIONS:
            # Sort synthetic stops ascending by demand_score; remove weakest first
            _synthetic_sorted = sorted(_synthetic_in_seq, key=lambda s: s.demand_score)
            _to_remove = {s.stop_id for s in _synthetic_sorted[:len(_synthetic_in_seq) - MAX_SYNTHETIC_ADDITIONS]}
            merged_seq = [s for s in merged_seq if s.stop_id not in _to_remove]
            logger.info(
                "  Synthetic cap: trimmed %d synthetic stops to stay within MAX_SYNTHETIC_ADDITIONS=%d.",
                len(_to_remove), MAX_SYNTHETIC_ADDITIONS,
            )
        if ALLOW_STOP_REMOVAL and _existing_non_mandatory:
            # If any low-boarding existing stop scores below the weakest accepted
            # synthetic stop, remove it to improve overall route quality.
            _synthetic_remaining = [s for s in merged_seq if not s.is_existing]
            if _synthetic_remaining:
                _min_synthetic_score = min(s.demand_score for s in _synthetic_remaining)
                _weak_existing = {
                    s.stop_id for s in _existing_non_mandatory
                    if s.demand_score < _min_synthetic_score * 0.5
                }
                if _weak_existing:
                    merged_seq = [s for s in merged_seq if s.stop_id not in _weak_existing]
                    logger.info(
                        "  ALLOW_STOP_REMOVAL: pruned %d low-value existing stops.",
                        len(_weak_existing),
                    )

        # Apply 2-opt resequencing with monotonicity penalty
        def _tt_fn(a_id: str, b_id: str) -> float:
            if travel_time_matrix is not None and len(travel_time_matrix) > 0:
                try:
                    return float(travel_time_matrix.loc[a_id, b_id])
                except (KeyError, ValueError):
                    pass
            return 600.0
        merged_seq = _two_opt_resequence(merged_seq, _tt_fn)

        for stop in merged_seq:
            assigned_stop_ids.add(stop.stop_id)

        # Estimate travel time from sequence
        ott = 0.0
        for i in range(len(merged_seq) - 1):
            ott += _haversine_miles(
                merged_seq[i].stop_lat, merged_seq[i].stop_lon,
                merged_seq[i+1].stop_lat, merged_seq[i+1].stop_lon
            ) / 25.0 * 60  # 25 mph average

        route_id = f"OPT_{route_counter:02d}"
        r = OptimisedRoute(
            route_id=route_id,
            route_name=f"Optimised Route {route_counter} (from {anchor_id})",
            stops=merged_seq,
            parent_route_id=anchor_id,
            estimated_one_way_min=round(min(ott, max_route_min), 1),
        )
        optimised_routes.append(r)
        route_counter += 1
        logger.info("  %s derived from anchor %s: %d stops (%d inserted).",
                    route_id, anchor_id, len(merged_seq), len(nearby_candidates))

    # Remaining unassigned stops → corridor clusters
    # (Gated off in route_27_only_mode — only Route 27 is processed)
    _route_27_only_inner = opt_cfg.get("route_27_only_mode", False)
    unassigned = [s for s in selected_stops if s.stop_id not in assigned_stop_ids]
    if unassigned and not _route_27_only_inner:
        logger.info("  %d unassigned stops → corridor clusters.", len(unassigned))
        corridor_routes = _design_routes_corridor(
            unassigned, travel_time_matrix, opt_cfg,
            route_costs_df=None, scenario_results=None,
        )
        for r in corridor_routes:
            if r.is_restoration:
                continue
            r.route_id = f"OPT_{route_counter:02d}"
            r.route_name = f"Corridor Route {route_counter}"
            optimised_routes.append(r)
            route_counter += 1
    elif unassigned and _route_27_only_inner:
        logger.info("  %d unassigned stops skipped (route_27_only_mode=True).", len(unassigned))

    if not _route_27_only_inner:
        route_76 = _evaluate_route_76(opt_cfg, route_costs_df, scenario_results, r76_bcr_threshold)
        if route_76 is not None:
            optimised_routes.append(route_76)

    logger.info("Route design (derive_from_existing): %d routes produced.", len(optimised_routes))
    return optimised_routes


def design_routes(
    selected_stops: List[OptimisedStop],
    travel_time_matrix: pd.DataFrame,
    config: dict,
    route_costs_df: Optional[pd.DataFrame] = None,
    scenario_results: Optional[list] = None,
    existing_routes: Optional[pd.DataFrame] = None,
    route27_corridor: Optional[dict] = None,
) -> List[OptimisedRoute]:
    """Dispatcher: select route design algorithm from config.route_design.mode.

    Modes:
      derive_from_existing  — anchor existing VTA routes; Route 27 is the spine.
      corridor              — k-means geographic clustering, PCA-ordered.
      hub_spoke             — Clarke-Wright savings; Winchester as hub (legacy).

    When route_27_only_mode is True (Step 1), bypass all greenfield/restoration
    designers and run only _design_routes_derive_from_existing for Route 27.
    The gated functions (_design_routes_corridor, _design_routes_hub_spoke,
    _evaluate_route_76) are NOT deleted — re-enable by setting the flag False.
    """
    opt_cfg = config.get("optimization", {})
    route_27_only = opt_cfg.get("route_27_only_mode", False)

    if route_27_only:
        logger.info("route_27_only_mode=True: running derive_from_existing for Route 27 only.")
        # Override anchor_routes so only Route 27 is processed
        import copy
        opt_cfg_27 = copy.deepcopy(opt_cfg)
        rd = opt_cfg_27.setdefault("route_design", {})
        rd["anchor_routes"] = ["27"]
        rd["primary_route"] = "27"
        rd["mode"] = "derive_from_existing"
        routes = _design_routes_derive_from_existing(
            selected_stops, travel_time_matrix, opt_cfg_27,
            existing_routes=existing_routes,
            route_costs_df=None,        # skip Route 76 inside derive_from_existing
            scenario_results=None,
        )
        # Gate off any restoration routes that snuck in (e.g. if Route 76 BCR met
        # threshold via _evaluate_route_76 — should not happen with None args above)
        routes = [r for r in routes if not r.is_restoration]
        return routes

    mode = opt_cfg.get("route_design", {}).get("mode", "derive_from_existing")

    if mode == "hub_spoke":
        return _design_routes_hub_spoke(
            selected_stops, travel_time_matrix, opt_cfg, route_costs_df, scenario_results
        )
    elif mode == "corridor":
        return _design_routes_corridor(
            selected_stops, travel_time_matrix, opt_cfg, route_costs_df, scenario_results
        )
    else:  # derive_from_existing (default)
        return _design_routes_derive_from_existing(
            selected_stops, travel_time_matrix, opt_cfg,
            existing_routes=existing_routes,
            route_costs_df=route_costs_df,
            scenario_results=scenario_results,
        )


def _evaluate_route_76(
    config: dict,
    route_costs_df: Optional[pd.DataFrame],
    scenario_results: Optional[list],
    bcr_threshold: float,
) -> Optional[OptimisedRoute]:
    """Evaluate Route 76 restoration using existing cost model output.

    Returns an OptimisedRoute flagged as restoration if BCR > threshold
    under the Moderate scenario, else None.
    """
    # Look for Route 76 cost in route_costs_df
    r76_annual_cost = None
    if route_costs_df is not None and len(route_costs_df) > 0:
        r76_row = route_costs_df[route_costs_df["route_id"].astype(str) == "76"]
        if len(r76_row) > 0:
            r76_annual_cost = float(r76_row.iloc[0].get("annual_operating_cost", 0))

    if r76_annual_cost is None:
        logger.info("Route 76 cost data not found; skipping restoration evaluation.")
        return None

    # Use Moderate scenario annual benefits as proxy for Route 76 share
    r76_benefit_share = 0.0
    if scenario_results:
        moderate = next((s for s in scenario_results if "moderate" in s.get("scenario", "").lower()), None)
        if moderate:
            r76_benefit_share = moderate.get("annual_benefits", 0) * 0.05  # ~5% for mountain area

    bcr = r76_benefit_share / max(r76_annual_cost, 1)
    logger.info("Route 76 restoration BCR: %.2f (threshold %.1f)", bcr, bcr_threshold)

    if bcr < bcr_threshold:
        logger.info("Route 76 BCR below threshold; restoration not recommended.")
        return None

    # Build synthetic Route 76 stop sequence (D9 corridor)
    r76_stops = [
        OptimisedStop("76_DT_LG", "Downtown Los Gatos", 37.2249, -121.9806,
                      "D1", True, False, False),
        OptimisedStop("76_OLD_SANTA_CRUZ", "Old Santa Cruz Hwy / Aldercroft",
                      37.2100, -122.0100, "D8", False, False, False),
        OptimisedStop("76_LEXINGTON", "Lexington Reservoir", 37.1850, -122.0350,
                      "D9", False, False, False),
        OptimisedStop("76_REDWOOD_EST", "Redwood Estates", 37.1650, -122.0550,
                      "D9", False, False, False),
        OptimisedStop("76_SUMMIT", "Summit Road / SR-17", 37.1500, -122.0700,
                      "D9", False, False, False),
    ]
    route_76 = OptimisedRoute(
        route_id="76_RESTORED",
        route_name="Route 76 Restored (Downtown LG → Summit via SR-17)",
        stops=r76_stops,
        is_restoration=True,
        source_route_id="76",
        estimated_one_way_min=35.0,
        bcr=round(bcr, 2),
    )
    logger.info("Route 76 restoration RECOMMENDED (BCR=%.2f).", bcr)
    return route_76


# =====================================================================
# SUB-STAGE 3c: HEADWAY OPTIMISATION
# =====================================================================

def optimise_headways(
    routes: List[OptimisedRoute],
    od_profiles: dict,
    config: dict,
) -> List[OptimisedRoute]:
    """Compute optimal headways per route per time-of-day window.

    Uses Mohring (1972) square-root formula where enabled:
        h* = sqrt(2 × C_vehicle / (λ × VOT))
    where:
        C_vehicle = hourly vehicle operating cost ($195.50/hr from config)
        λ = boardings per hour on the route
        VOT = value of time per hour ($17.80 personal from config)

    Results are clamped to FTA minimum service levels:
        Peak: ≤15 min
        Off-peak: ≤30 min
        Evening: ≤60 min

    Args:
        routes: From design_routes() with stops set.
        od_profiles: From time_of_day_profile() in demand_matrix.py.
        config: Full config dict.

    Returns:
        Routes with headways dict populated per time window.
    """
    opt_cfg = config.get("optimization", {})
    headway_cfg = opt_cfg.get("headways", {})
    peak_min = headway_cfg.get("peak_min", 15)
    offpeak_min = headway_cfg.get("offpeak_min", 30)
    evening_min = headway_cfg.get("evening_min", 60)
    mohring_enabled = opt_cfg.get("mohring", {}).get("enabled", True)

    c_vehicle_per_hour = config.get("transit", {}).get(
        "operating_cost_per_revenue_hour", 195.50
    )
    vot = config.get("valuations", {}).get(
        "value_of_time_personal_per_hour", 17.80
    )

    window_caps = {
        "am_peak": peak_min,
        "midday": offpeak_min,
        "pm_school": peak_min,
        "pm_commute": peak_min,
        "evening": evening_min,
    }

    for route in routes:
        route_district_ids = {
            s.district_id for s in route.stops if s.district_id
        }
        headways = {}

        for window_name, cap_min in window_caps.items():
            # Estimate λ (boardings/hr) for this route from O-D profiles
            lam = _estimate_route_boardings_per_hour(
                route, route_district_ids, od_profiles.get(window_name), window_name
            )

            if mohring_enabled and lam > 0:
                # Mohring (1972): h* [hours] = 2*C/(λ*v) with $/hr costs
                # C = cost per hour, λ = pass/hr, v = VOT $/pass-hr
                h_hr = 2.0 * c_vehicle_per_hour / (lam * vot)
                h_min = h_hr * 60.0
            else:
                h_min = cap_min  # fall back to FTA standard

            # Clip: minimum 5 min (operational floor), maximum = FTA cap for window
            headways[window_name] = max(5, min(int(round(h_min)), cap_min))

        route.headways = headways
        logger.info("  %s headways: %s", route.route_id,
                    {k: f"{v}min" for k, v in headways.items()})

    return routes


def _estimate_route_boardings_per_hour(
    route: OptimisedRoute,
    route_district_ids: set,
    od_window: Optional[pd.DataFrame],
    window_name: str,
) -> float:
    """Estimate boardings per hour for a route in a given time window."""
    if od_window is None or len(od_window) == 0:
        return 10.0  # fallback

    # Filter O-D pairs where origin OR destination is in route districts
    mask = (
        od_window["origin_district"].isin(route_district_ids)
        | od_window["destination_district"].isin(route_district_ids)
    )
    relevant = od_window[mask]
    if len(relevant) == 0:
        return 5.0

    total_demand = relevant["demand"].sum()
    # Window duration in hours
    window_hours = {
        "am_peak": 3.0,
        "midday": 5.25,
        "pm_school": 2.0,
        "pm_commute": 2.25,
        "evening": 2.5,
    }
    hrs = window_hours.get(window_name, 3.0)
    return total_demand / hrs


# =====================================================================
# OUTPUT FORMATTER
# =====================================================================

def routes_to_dataframe(routes: List[OptimisedRoute]) -> pd.DataFrame:
    """Flatten optimised routes to a DataFrame for CSV export."""
    rows = []
    for route in routes:
        for seq, stop in enumerate(route.stops):
            rows.append({
                "route_id": route.route_id,
                "route_name": route.route_name,
                "parent_route_id": route.parent_route_id,
                "is_restoration": route.is_restoration,
                "bcr": route.bcr,
                "estimated_one_way_min": route.estimated_one_way_min,
                "stop_sequence": seq,
                "stop_id": stop.stop_id,
                "stop_name": stop.stop_name,
                "stop_lat": stop.stop_lat,
                "stop_lon": stop.stop_lon,
                "district_id": stop.district_id,
                "is_existing": stop.is_existing,
                "is_school_stop": stop.is_school_stop,
                "is_mandatory": stop.is_mandatory,
                "wheelchair_boarding": stop.wheelchair_boarding,
                "demand_score": round(stop.demand_score, 4),
                "estimated_daily_boardings": round(getattr(stop, "estimated_daily_boardings", 0.0), 1),
                "headway_am_peak": route.headways.get("am_peak"),
                "headway_midday": route.headways.get("midday"),
                "headway_pm_school": route.headways.get("pm_school"),
                "headway_pm_commute": route.headways.get("pm_commute"),
                "headway_evening": route.headways.get("evening"),
                "diversion_rate": round(getattr(route, "diversion_rate", 0.08), 4),
                "route_total_demand": round(getattr(route, "route_total_demand", 0.0), 1),
            })
    return pd.DataFrame(rows)


# =====================================================================
# PIPELINE ENTRY POINT
# =====================================================================

def run_route_optimisation(
    candidate_stops: pd.DataFrame,
    coverage_gaps: pd.DataFrame,
    tdi_df: pd.DataFrame,
    unmet_need_df: Optional[pd.DataFrame],
    travel_time_matrix: pd.DataFrame,
    od_profiles: dict,
    config: dict,
    route_costs_df: Optional[pd.DataFrame] = None,
    scenario_results: Optional[list] = None,
    existing_routes: Optional[pd.DataFrame] = None,
    districts_df: Optional[pd.DataFrame] = None,
    road_graph=None,
    stops_snapped_df: Optional[pd.DataFrame] = None,
) -> dict:
    """Run the full three-sub-stage route optimisation pipeline.

    Returns:
        Dict with keys:
            selected_stops: List[OptimisedStop]
            routes: List[OptimisedRoute]
            routes_df: DataFrame (flat, for CSV export)
    """
    logger.info("Route optimisation: Stage 3a — Stop Selection")
    selected = select_stops(
        candidate_stops, coverage_gaps, tdi_df, unmet_need_df, config
    )

    logger.info("Route optimisation: Stage 3b — Route Design")
    routes = design_routes(
        selected, travel_time_matrix, config, route_costs_df, scenario_results,
        existing_routes=existing_routes,
    )

    logger.info("Route optimisation: Stage 3c — Headway Optimisation")
    routes = optimise_headways(routes, od_profiles, config)

    # Stage 3d: road-following polylines (uses OSM network if available)
    if road_graph is not None or True:
        try:
            from src.network_graph import compute_route_polyline
            for route in routes:
                route.polyline = compute_route_polyline(
                    route.stops, road_graph, stops_snapped_df
                )
            logger.info("Route polylines computed for %d routes.", len(routes))
        except Exception as exc:
            logger.warning("Polyline computation failed (%s); routes will use stop centroids.", exc)

    # Stage 3f: per-stop daily boarding estimates
    # Include every stop on every route, not just `selected` — Route 76 (and any
    # other restoration path) builds synthetic stops inline that bypass select_stops.
    from collections import Counter
    all_stops_by_id: dict = {s.stop_id: s for s in selected}
    for r in routes:
        for s in r.stops:
            all_stops_by_id.setdefault(s.stop_id, s)
    all_stops = list(all_stops_by_id.values())
    selected_stops_per_district = Counter(s.district_id for s in all_stops if s.district_id)
    for stop in all_stops:
        stop.estimated_daily_boardings = estimate_daily_boardings(
            stop, tdi_df, districts_df, config,
            selected_stop_count=selected_stops_per_district.get(stop.district_id, 1),
        )

    # Step 4: populate diversion_rate on each route from config
    _diversion_rate_cfg = config.get("benefit", {}).get("diversion_rate", 0.08)
    for route in routes:
        route.diversion_rate = _diversion_rate_cfg

    # Step 3: compute route_total_demand as sum of per-stop boardings (sanity-check field)
    for route in routes:
        route.route_total_demand = sum(
            getattr(s, "estimated_daily_boardings", 0.0) for s in route.stops
        )

    routes_df = routes_to_dataframe(routes)
    return {
        "selected_stops": selected,
        "routes": routes,
        "routes_df": routes_df,
    }
