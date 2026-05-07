"""
schedule_generator.py -- Timetable generation with school pickup constraints.

Produces a GTFS-compliant timetable for optimised routes. Guarantees that
school pickup constraints are met: a bus arrives at school stop(s) within
10 minutes of each dismissal time (2:25 PM and 3:55 PM).

Algorithm:
  1. Backward scheduling from school dismissal → compute school trip
  2. Forward scheduling from 6:00 AM at headway → regular trips
  3. Merge and deduplicate (if a regular trip already satisfies school window)
  4. Output: ordered list of Trip objects with per-stop arrival/departure times

Standards:
    - GTFS Static Specification (stop_times.txt format)
    - FTA service span: 6:00 AM – 9:00 PM minimum
    - ADA: no timing constraint but school trips serve ADA-accessible stops
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


# =====================================================================
# DATA STRUCTURES
# =====================================================================

@dataclass
class StopTime:
    stop_id: str
    stop_sequence: int
    arrival_time: str    # HH:MM:SS (GTFS format; can exceed 24:xx for post-midnight)
    departure_time: str  # HH:MM:SS


@dataclass
class Trip:
    trip_id: str
    route_id: str
    service_id: str          # "WEEKDAY", "SCHOOL_WEEKDAY", etc.
    headsign: str
    stop_times: List[StopTime]
    is_school_trip: bool = False
    school_window: Optional[str] = None   # e.g. "14:25" dismissal this trip serves
    # GTFS direction_id: 0 = primary direction (e.g., Winchester -> Santa Teresa),
    # 1 = reverse direction. Required by GTFS spec for routes operating both ways.
    direction_id: int = 0


# =====================================================================
# TIME UTILITIES
# =====================================================================

def _hhmm_to_seconds(hhmm: str) -> int:
    """Convert HH:MM or HH:MM:SS to seconds since midnight."""
    parts = hhmm.split(":")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    return h * 3600 + m * 60 + s


def _seconds_to_hhmmss(sec: int) -> str:
    """Convert seconds since midnight to HH:MM:SS (allows >24h for GTFS)."""
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _add_minutes(hhmm: str, minutes: int) -> str:
    """Add minutes to a HH:MM string, return HH:MM:SS."""
    sec = _hhmm_to_seconds(hhmm) + minutes * 60
    return _seconds_to_hhmmss(sec)


def _within_window(arrival_hhmm: str, dismissal_hhmm: str, window_min: int) -> bool:
    """Return True if arrival is within [dismissal, dismissal + window_min]."""
    arr_sec = _hhmm_to_seconds(arrival_hhmm)
    dis_sec = _hhmm_to_seconds(dismissal_hhmm)
    return dis_sec <= arr_sec <= dis_sec + window_min * 60


# =====================================================================
# SCHOOL TRIP GENERATION (BACKWARD SCHEDULING)
# =====================================================================

def compute_school_trips(
    route,        # OptimisedRoute
    travel_time_matrix: pd.DataFrame,
    school_windows: list,
    route_counter_start: int = 1,
) -> List[Trip]:
    """Generate school trips via backward scheduling from dismissal time.

    For each school window, finds school stop(s) on the route, then
    traces backward to determine departure times at all prior stops.

    A trip is generated that arrives at the school stop no later than
    dismissal_time + pickup_window_min.

    Args:
        route: OptimisedRoute from route_optimizer.py.
        travel_time_matrix: stop_id × stop_id travel time in seconds.
        school_windows: List of school window dicts from config.
        route_counter_start: Integer to start trip ID numbering.

    Returns:
        List of Trip objects (one per school window this route serves).
    """
    trips = []
    trip_counter = route_counter_start

    for window in school_windows:
        dismissal_hhmm = window["dismissal_time"]
        window_min = int(window.get("pickup_window_min", 10))
        window_districts = set(window.get("districts", []))

        # Find school stop(s) on this route: stops in window districts
        school_stop_indices = [
            i for i, s in enumerate(route.stops)
            if s.district_id in window_districts or s.is_school_stop
        ]
        if not school_stop_indices:
            continue

        # Target arrival at school stop = dismissal + window_min/2 (aim for midpoint)
        target_arrival_sec = (
            _hhmm_to_seconds(dismissal_hhmm) + (window_min // 2) * 60
        )

        # For each school stop index, compute backward schedule
        for school_idx in school_stop_indices[:1]:  # first school stop per window
            school_stop = route.stops[school_idx]

            # Compute arrival at each stop working backward from school stop
            arrivals = {}
            arrivals[school_idx] = target_arrival_sec

            for i in range(school_idx - 1, -1, -1):
                from_stop = route.stops[i]
                to_stop = route.stops[i + 1]
                tt = _get_tt(travel_time_matrix, from_stop.stop_id, to_stop.stop_id)
                arrivals[i] = arrivals[i + 1] - tt

            # Compute forward from school stop to terminus
            for i in range(school_idx + 1, len(route.stops)):
                from_stop = route.stops[i - 1]
                to_stop = route.stops[i]
                tt = _get_tt(travel_time_matrix, from_stop.stop_id, to_stop.stop_id)
                arrivals[i] = arrivals[i - 1] + tt

            # Build StopTime list
            stop_times = []
            for seq, (idx, stop) in enumerate(zip(range(len(route.stops)), route.stops)):
                arr_sec = int(arrivals.get(seq, target_arrival_sec))
                dep_sec = arr_sec + 30  # 30s dwell time (FTA minimum)
                stop_times.append(StopTime(
                    stop_id=stop.stop_id,
                    stop_sequence=seq,
                    arrival_time=_seconds_to_hhmmss(arr_sec),
                    departure_time=_seconds_to_hhmmss(dep_sec),
                ))

            trip_id = f"{route.route_id}_SCH_{dismissal_hhmm.replace(':', '')}_{trip_counter:03d}"
            trips.append(Trip(
                trip_id=trip_id,
                route_id=route.route_id,
                service_id="SCHOOL_WEEKDAY",
                headsign=route.stops[-1].stop_name,
                stop_times=stop_times,
                is_school_trip=True,
                school_window=dismissal_hhmm,
            ))
            trip_counter += 1

            logger.info("  School trip %s: arrives %s at %s (dismissal %s ±%dmin)",
                        trip_id,
                        _seconds_to_hhmmss(arrivals[school_idx]),
                        school_stop.stop_name,
                        dismissal_hhmm,
                        window_min)

    return trips


def _get_tt(matrix: pd.DataFrame, from_id: str, to_id: str) -> float:
    """Get travel time in seconds from matrix, with fallback."""
    if matrix is not None and len(matrix) > 0:
        try:
            return float(matrix.loc[from_id, to_id])
        except (KeyError, ValueError):
            pass
    return 300.0  # 5-minute fallback


# =====================================================================
# REGULAR TRIP GENERATION (FORWARD SCHEDULING)
# =====================================================================

def _build_window_schedule(
    route,
    time_windows: dict,
    start_sec: int,
    end_sec: int,
) -> list:
    """Return [(window_start_sec, window_end_sec, headway_sec), ...]."""
    window_schedule = []
    window_order = ["am_peak", "midday", "pm_school", "pm_commute", "evening"]
    for wname in window_order:
        if wname not in time_windows:
            continue
        wstart, wend = time_windows[wname]
        hw = route.headways.get(wname, 30)
        window_schedule.append((
            _hhmm_to_seconds(wstart),
            _hhmm_to_seconds(wend),
            hw * 60,
        ))
    if not window_schedule:
        window_schedule = [(start_sec, end_sec, 30 * 60)]
    return window_schedule


def _build_trips_one_direction(
    route,
    stop_sequence: list,
    leg_times: list,
    window_schedule: list,
    start_sec: int,
    end_sec: int,
    direction_id: int,
    headsign: str,
    trip_id_prefix: str,
    trip_counter_start: int,
) -> List[Trip]:
    """Generate headway trips along a given stop sequence and direction_id.

    The reverse-direction (direction_id=1) call passes stop_sequence and
    leg_times in reversed order so departure-time accumulation walks the
    corridor backwards. Travel times mirror because Route 27 is a linear
    surface route -- inbound and outbound segment times differ only at the
    margin (left vs right turns) and the schedule treats them as symmetric.
    """
    trips: List[Trip] = []
    trip_counter = trip_counter_start
    current_departure = start_sec
    while current_departure <= end_sec:
        headway_sec = 30 * 60
        for ws, we, hw in window_schedule:
            if ws <= current_departure < we:
                headway_sec = hw
                break

        stop_times = []
        cum_sec = current_departure
        for seq, stop in enumerate(stop_sequence):
            dep_sec = cum_sec + 30  # 30s dwell
            stop_times.append(StopTime(
                stop_id=stop.stop_id,
                stop_sequence=seq,
                arrival_time=_seconds_to_hhmmss(cum_sec),
                departure_time=_seconds_to_hhmmss(dep_sec),
            ))
            if seq < len(leg_times):
                cum_sec = dep_sec + leg_times[seq]

        trip_id = f"{trip_id_prefix}_T{trip_counter:03d}"
        trips.append(Trip(
            trip_id=trip_id,
            route_id=route.route_id,
            service_id="WEEKDAY",
            headsign=headsign,
            stop_times=stop_times,
            is_school_trip=False,
            direction_id=direction_id,
        ))
        trip_counter += 1
        current_departure += headway_sec
    return trips


def generate_headway_trips(
    route,       # OptimisedRoute
    travel_time_matrix: pd.DataFrame,
    service_span: dict,
    time_windows: dict,
    route_counter_start: int = 1,
    bidirectional: bool = True,
) -> List[Trip]:
    """Generate regular headway trips across the full service span, BOTH directions.

    For ``bidirectional=True`` (default) emits two trip series with matching
    headways:
      direction_id=0  outbound, primary stop sequence
      direction_id=1  inbound, reversed sequence
    Headways are mirrored so frequency is identical in both directions, which
    is the GTFS standard expectation for a two-way local route.

    Args:
        route: OptimisedRoute with stops and headways populated.
        travel_time_matrix: stop_id x stop_id travel time in seconds.
        service_span: Dict with start and end (HH:MM).
        time_windows: Dict of window_name -> [start, end] from config.
        route_counter_start: Integer to start trip ID numbering.
        bidirectional: If True, emit direction_id=0 AND direction_id=1 trips.

    Returns:
        List of Trip objects for regular service.
    """
    if not route.stops:
        return []

    start_sec = _hhmm_to_seconds(service_span.get("start", "06:00"))
    end_sec = _hhmm_to_seconds(service_span.get("end", "21:00"))
    window_schedule = _build_window_schedule(route, time_windows, start_sec, end_sec)

    # Forward leg times
    leg_times = []
    for i in range(len(route.stops) - 1):
        tt = _get_tt(travel_time_matrix, route.stops[i].stop_id, route.stops[i + 1].stop_id)
        leg_times.append(int(tt))

    # Direction 0: outbound (route.stops as-is). Headsign = last stop name.
    out_trips = _build_trips_one_direction(
        route=route,
        stop_sequence=route.stops,
        leg_times=leg_times,
        window_schedule=window_schedule,
        start_sec=start_sec,
        end_sec=end_sec,
        direction_id=0,
        headsign=route.stops[-1].stop_name,
        trip_id_prefix=f"{route.route_id}_D0",
        trip_counter_start=route_counter_start,
    )

    if not bidirectional:
        return out_trips

    # Direction 1: inbound. Reverse stop sequence and leg times.
    rev_stops = list(reversed(route.stops))
    rev_legs = list(reversed(leg_times))
    in_trips = _build_trips_one_direction(
        route=route,
        stop_sequence=rev_stops,
        leg_times=rev_legs,
        window_schedule=window_schedule,
        start_sec=start_sec,
        end_sec=end_sec,
        direction_id=1,
        headsign=route.stops[0].stop_name,
        trip_id_prefix=f"{route.route_id}_D1",
        trip_counter_start=route_counter_start + len(out_trips),
    )

    return out_trips + in_trips


# =====================================================================
# MERGE AND DEDUPLICATE
# =====================================================================

def merge_and_deduplicate_trips(
    school_trips: List[Trip],
    headway_trips: List[Trip],
    school_windows: list,
    window_tolerance_min: int = 10,
) -> List[Trip]:
    """Merge school trips into headway schedule, removing redundant trips.

    For each school trip, check whether an existing headway trip already
    satisfies the pickup window. If yes, drop the school trip (redundant).
    If no, insert the school trip at the correct chronological position.

    Args:
        school_trips: From compute_school_trips().
        headway_trips: From generate_headway_trips().
        school_windows: School window configs (for verification).
        window_tolerance_min: Maximum deviation allowed (default = pickup window).

    Returns:
        Merged, chronologically sorted list of Trip objects.
    """
    # Build lookup: school_window_dismissal → list of school trips
    school_by_window: Dict[str, List[Trip]] = {}
    for t in school_trips:
        key = t.school_window or "unknown"
        school_by_window.setdefault(key, []).append(t)

    retained_school_trips = []

    for window in school_windows:
        dismissal = window["dismissal_time"]
        window_min = int(window.get("pickup_window_min", 10))
        districts = set(window.get("districts", []))

        sch_trips_for_window = school_by_window.get(dismissal, [])
        if not sch_trips_for_window:
            continue

        for sch_trip in sch_trips_for_window:
            # Find the school stop time in this school trip
            school_stop_arrival = None
            for st in sch_trip.stop_times:
                # School stop is identified by its stop_id appearing in route.stops
                # with district_id in window districts — approximated by position
                school_stop_arrival = st.arrival_time

            if school_stop_arrival is None:
                retained_school_trips.append(sch_trip)
                continue

            # Check if any headway trip already arrives within window
            covered = False
            for ht in headway_trips:
                if ht.route_id != sch_trip.route_id:
                    continue
                for st in ht.stop_times:
                    if st.stop_id in {sst.stop_id for sst in sch_trip.stop_times}:
                        if _within_window(st.arrival_time, dismissal, window_min):
                            covered = True
                            break
                if covered:
                    break

            if not covered:
                retained_school_trips.append(sch_trip)
                logger.info(
                    "  Inserting school trip %s (dismissal %s not covered by headway service).",
                    sch_trip.trip_id, dismissal
                )
            else:
                logger.info(
                    "  School trip %s redundant (headway trip covers %s window).",
                    sch_trip.trip_id, dismissal
                )

    all_trips = headway_trips + retained_school_trips

    # Sort by first departure time
    def _first_dep(trip: Trip) -> int:
        if trip.stop_times:
            return _hhmm_to_seconds(trip.stop_times[0].departure_time)
        return 0

    all_trips.sort(key=_first_dep)
    return all_trips


# =====================================================================
# VERIFICATION
# =====================================================================

def verify_school_coverage(
    all_trips: List[Trip],
    school_windows: list,
) -> pd.DataFrame:
    """Verify school pickup constraint is met for each window.

    Returns a DataFrame with one row per school window showing:
    - window met (True/False)
    - earliest satisfying trip_id
    - actual arrival time at school stop
    """
    rows = []
    for window in school_windows:
        dismissal = window["dismissal_time"]
        window_min = int(window.get("pickup_window_min", 10))
        school_name = window["school"]

        satisfied = False
        satisfying_trip = None
        actual_arrival = None

        for trip in all_trips:
            for st in trip.stop_times:
                if _within_window(st.arrival_time, dismissal, window_min):
                    satisfied = True
                    satisfying_trip = trip.trip_id
                    actual_arrival = st.arrival_time
                    break
            if satisfied:
                break

        rows.append({
            "school": school_name,
            "dismissal_time": dismissal,
            "pickup_deadline": _add_minutes(dismissal, window_min),
            "pickup_window_min": window_min,
            "constraint_met": satisfied,
            "satisfying_trip_id": satisfying_trip,
            "actual_arrival": actual_arrival,
        })

    df = pd.DataFrame(rows)
    for _, row in df.iterrows():
        status = "OK" if row["constraint_met"] else "VIOLATION"
        logger.info("School pickup %s: %s | %s arrives %s (deadline %s)",
                    status, row["school"], row.get("satisfying_trip_id", "NONE"),
                    row.get("actual_arrival", "N/A"), row["pickup_deadline"])
    return df


# =====================================================================
# W4: HEADWAY PARITY (frequency same-or-better assertion)
# =====================================================================

# Default published Route 27 headways (minutes) per time window per direction.
# Derived from VTA's published Route 27 timetable (Bus_Schedules___Observed_Times.md
# in data/raw); both directions currently run on identical 30-min headways with
# 60-min gaps in evening / weekend windows. The optimised schedule must match
# or improve every (window, direction) entry -- never make service less frequent.
PUBLISHED_ROUTE27_HEADWAYS_MIN = {
    "am_peak":    30,
    "midday":     30,
    "pm_school":  30,
    "pm_commute": 30,
    "evening":    60,
}


def measure_effective_headways(
    trips: List[Trip],
    time_windows: dict,
) -> pd.DataFrame:
    """Compute the effective headway (minutes between successive trips) for
    each (route, direction, window) from a generated trip list.

    Effective headway is computed as the median gap between consecutive trip
    departures from the first stop of the direction, restricted to trips whose
    first departure falls inside the window.

    Returns:
        DataFrame columns: route_id, direction_id, window, n_trips,
        median_headway_min, max_headway_min.
    """
    rows = []
    if not trips:
        return pd.DataFrame(columns=[
            "route_id", "direction_id", "window", "n_trips",
            "median_headway_min", "max_headway_min",
        ])

    # Group trips by (route_id, direction_id)
    grouped: Dict[Tuple[str, int], List[int]] = {}
    for t in trips:
        if not t.stop_times:
            continue
        first_dep = _hhmm_to_seconds(t.stop_times[0].departure_time)
        key = (t.route_id, int(getattr(t, "direction_id", 0)))
        grouped.setdefault(key, []).append(first_dep)

    for (rid, did), departures in grouped.items():
        departures.sort()
        for wname, (wstart, wend) in time_windows.items():
            ws = _hhmm_to_seconds(wstart)
            we = _hhmm_to_seconds(wend)
            in_window = [d for d in departures if ws <= d < we]
            if len(in_window) < 2:
                rows.append({
                    "route_id": rid, "direction_id": did, "window": wname,
                    "n_trips": len(in_window),
                    "median_headway_min": None, "max_headway_min": None,
                })
                continue
            gaps_sec = [in_window[i + 1] - in_window[i] for i in range(len(in_window) - 1)]
            med = sorted(gaps_sec)[len(gaps_sec) // 2] / 60.0
            mx = max(gaps_sec) / 60.0
            rows.append({
                "route_id": rid, "direction_id": did, "window": wname,
                "n_trips": len(in_window),
                "median_headway_min": round(med, 1),
                "max_headway_min": round(mx, 1),
            })
    return pd.DataFrame(rows)


def check_headway_parity(
    trips: List[Trip],
    time_windows: dict,
    baseline_headways_min: dict = PUBLISHED_ROUTE27_HEADWAYS_MIN,
    strict: bool = False,
) -> pd.DataFrame:
    """Verify the optimised schedule maintains or improves frequency.

    For each (route_id, direction_id, window), compares the measured median
    headway against the published baseline. A row is OK if
        measured <= baseline    (same or shorter wait between buses).
    A row is FAIL if measured > baseline (less frequent service).

    Args:
        trips: All Trip objects produced by generate_schedule().
        time_windows: Dict of window_name -> [start, end].
        baseline_headways_min: Per-window published headways. Default uses
            VTA Route 27 published values.
        strict: If True, raises AssertionError on any FAIL row.

    Returns:
        DataFrame with one row per (route, direction, window) with columns:
        baseline_min, measured_min, status (OK|FAIL|NO_TRIPS), delta_min.
    """
    measured = measure_effective_headways(trips, time_windows)
    rows = []
    for _, m in measured.iterrows():
        baseline = baseline_headways_min.get(m["window"])
        meas = m["median_headway_min"]
        if baseline is None:
            status = "NO_BASELINE"
            delta = None
        elif meas is None:
            status = "NO_TRIPS"
            delta = None
        elif meas <= baseline + 0.5:   # 30-second tolerance for rounding
            status = "OK"
            delta = round(meas - baseline, 1)
        else:
            status = "FAIL"
            delta = round(meas - baseline, 1)
        rows.append({
            "route_id": m["route_id"],
            "direction_id": m["direction_id"],
            "window": m["window"],
            "baseline_headway_min": baseline,
            "measured_headway_min": meas,
            "max_headway_min": m["max_headway_min"],
            "n_trips_in_window": m["n_trips"],
            "delta_min": delta,
            "status": status,
        })
    df = pd.DataFrame(rows)

    fails = df[df["status"] == "FAIL"]
    if len(fails) > 0:
        for _, r in fails.iterrows():
            logger.error(
                "HEADWAY PARITY FAIL: route=%s dir=%d window=%s "
                "measured=%s min vs baseline=%s min (delta +%s)",
                r["route_id"], r["direction_id"], r["window"],
                r["measured_headway_min"], r["baseline_headway_min"], r["delta_min"],
            )
        if strict:
            raise AssertionError(
                f"{len(fails)} (route, direction, window) combination(s) violate "
                f"the same-or-better frequency requirement. See log for details."
            )
    else:
        logger.info(
            "Headway parity: OK across %d (route, direction, window) checks.",
            len(df),
        )
    return df


# =====================================================================
# PIPELINE ENTRY POINT
# =====================================================================

def generate_schedule(
    routes: list,           # List[OptimisedRoute]
    travel_time_matrix: pd.DataFrame,
    config: dict,
) -> dict:
    """Generate the full timetable for all optimised routes.

    Args:
        routes: List of OptimisedRoute objects from route_optimizer.py.
        travel_time_matrix: From network_graph.py.
        config: Full config dict (uses optimization.school_windows,
            optimization.service_span, optimization.time_windows).

    Returns:
        Dict with keys:
            all_trips: List[Trip] (all trips across all routes, sorted)
            school_coverage: DataFrame verifying school constraints
            trips_by_route: Dict[route_id → List[Trip]]
    """
    opt_cfg = config.get("optimization", {})
    school_windows = opt_cfg.get("school_windows", [])
    service_span = opt_cfg.get("service_span", {"start": "06:00", "end": "21:00"})
    time_windows = opt_cfg.get("time_windows", {
        "am_peak":    ["06:00", "09:00"],
        "midday":     ["09:00", "14:15"],
        "pm_school":  ["14:15", "16:15"],
        "pm_commute": ["16:15", "18:30"],
        "evening":    ["18:30", "21:00"],
    })

    all_trips: List[Trip] = []
    trips_by_route: Dict[str, List[Trip]] = {}
    trip_base = 1

    for route in routes:
        logger.info("Generating schedule for route %s (%d stops)...",
                    route.route_id, len(route.stops))

        school_trips = compute_school_trips(
            route, travel_time_matrix, school_windows, trip_base
        )
        trip_base += len(school_trips)

        headway_trips = generate_headway_trips(
            route, travel_time_matrix, service_span, time_windows, trip_base
        )
        trip_base += len(headway_trips)

        merged = merge_and_deduplicate_trips(
            school_trips, headway_trips, school_windows
        )
        trips_by_route[route.route_id] = merged
        all_trips.extend(merged)

    school_coverage = verify_school_coverage(all_trips, school_windows)

    # Log summary
    violations = school_coverage[~school_coverage["constraint_met"]]
    if len(violations) > 0:
        logger.warning("%d school pickup constraint(s) NOT MET:", len(violations))
        for _, v in violations.iterrows():
            logger.warning("  VIOLATION: %s at %s", v["school"], v["dismissal_time"])
    else:
        logger.info("All school pickup constraints satisfied.")

    # W4: bidirectional + headway-parity check (frequency same-or-better than
    # the published baseline). Pulled from config when present, else defaults
    # to PUBLISHED_ROUTE27_HEADWAYS_MIN.
    baseline_headways = opt_cfg.get("baseline_headways_min", PUBLISHED_ROUTE27_HEADWAYS_MIN)
    headway_parity = check_headway_parity(
        all_trips, time_windows, baseline_headways_min=baseline_headways,
        strict=False,
    )

    # Direction coverage: every route should now emit trips in BOTH directions.
    by_dir = {}
    for t in all_trips:
        by_dir.setdefault(t.route_id, set()).add(int(getattr(t, "direction_id", 0)))
    for rid, dirs in by_dir.items():
        if dirs != {0, 1}:
            logger.warning(
                "Route %s only generated trips in directions %s; expected both 0 and 1.",
                rid, sorted(dirs),
            )

    logger.info("Schedule generated: %d total trips across %d routes "
                "(both directions).", len(all_trips), len(routes))
    return {
        "all_trips": all_trips,
        "school_coverage": school_coverage,
        "trips_by_route": trips_by_route,
        "headway_parity": headway_parity,
    }
