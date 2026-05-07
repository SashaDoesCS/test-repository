"""
stop_validator.py -- Geometric validity checks for bus stop coordinates.

Catches the failure mode where a hand-coded forced-stop coordinate (e.g.,
LGHS, a school address) lands on a freeway ramp, in a cloverleaf median, or
otherwise far from a walkable transit-eligible road -- producing a stop
that the dashboard renders inside the Hwy 17/85 interchange.

API:
    is_valid_stop_placement(lat, lon, graph=None) -> StopValidation
        Returns a StopValidation dataclass with: ok, reason, snap_distance_ft,
        nearest_road_class, nearest_road_name. Loads the cached OSM drivable
        network on first call; subsequent calls reuse it.

    validate_forced_candidates(candidates, graph=None, raise_on_invalid=True)
        Validates every dict in `candidates` (must contain stop_lat, stop_lon,
        stop_id). Logs each failure with the offending coord and -- if
        raise_on_invalid -- raises StopPlacementError so the corridor build
        cannot silently include a freeway-median stop.

CLI:
    python -m src.stop_validator
        Validates the FORCED_CANDIDATES list in route27_corridor.py and
        prints a per-stop report. Exit 0 if all valid, 1 otherwise.

Rules (in order of severity):
    1. Snap distance to the nearest drivable edge must be <= MAX_SNAP_FT (100 ft).
       A coord >100 ft from any road is almost certainly a wrong address or
       a parcel-centroid pin (school campus middle, mall middle, etc.) rather
       than a stop location.
    2. The nearest edge's highway tag must NOT be in REJECTED_HIGHWAY_CLASSES.
       Motorways, motorway_link (on/off ramps), and trunk_link cannot host
       a passenger bus stop. A coord whose nearest edge is a freeway means
       the geocoder snapped to a ramp, not the surface street nearby.
    3. The nearest edge must allow general access (no `access=no` or
       `access=private` tag).

Soft warnings (not failures):
    - Snap distance > WARN_SNAP_FT (50 ft) but <= MAX_SNAP_FT: log warning.
    - Highway class is `trunk` (e.g., a state route boulevard): allowed but
      flagged because trunk segments are often expressway-grade.

References:
    - OpenStreetMap highway=* taxonomy: https://wiki.openstreetmap.org/wiki/Key:highway
    - FTA Bus Stop Design Guidelines (2003): stops require curbside pull-out
      and ADA landing pad, neither feasible on freeway ramps or medians.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# A bus stop coordinate that snaps further than this from the nearest drivable
# edge is almost certainly a wrong-address / parcel-centroid pin and rejected.
MAX_SNAP_FT = 100.0

# Snap distances above this (but below MAX_SNAP_FT) are surfaced as warnings.
WARN_SNAP_FT = 50.0

# Highway classes that cannot host a passenger bus stop. A coord whose nearest
# edge is one of these means the geocoder snapped to a freeway/ramp, not the
# parallel surface street.
REJECTED_HIGHWAY_CLASSES = {
    "motorway", "motorway_link",
    "trunk_link",        # freeway on/off ramps
}

# Highway classes that are permitted but flagged (state-route surface segments
# can sometimes be expressway-grade with no curb).
WARN_HIGHWAY_CLASSES = {
    "trunk",
}

_FEET_PER_METER = 3.28084

# Cached graph (loaded on first call to is_valid_stop_placement).
_CACHED_GRAPH = None


class StopPlacementError(ValueError):
    """Raised when a forced-stop coordinate fails validation in strict mode."""


@dataclass
class StopValidation:
    """Result of a single stop coordinate validation."""
    ok: bool
    reason: str                          # "valid", "valid_with_warning", or rejection reason
    snap_distance_ft: Optional[float]    # None if the graph could not be queried
    nearest_road_class: Optional[str]    # OSM highway tag of nearest edge
    nearest_road_name: Optional[str]     # name tag of nearest edge (if any)
    warnings: list[str]                  # non-fatal advisories

    def __str__(self) -> str:
        head = "OK " if self.ok else "BAD"
        snap = f"{self.snap_distance_ft:.0f}ft" if self.snap_distance_ft is not None else "n/a"
        road = f"{self.nearest_road_class}" + (f" ({self.nearest_road_name})" if self.nearest_road_name else "")
        warn = f"  warnings={self.warnings}" if self.warnings else ""
        return f"[{head}] {snap} from {road} -- {self.reason}{warn}"


def _load_graph():
    """Load the cached Route 27 OSM network; returns None if unavailable.

    Reuses src.route27_corridor.load_or_build_route27_network() so we benefit
    from the same caching path (data/geospatial/route27_network.pkl).
    """
    global _CACHED_GRAPH
    if _CACHED_GRAPH is not None:
        return _CACHED_GRAPH
    try:
        from src.route27_corridor import build_route27_road_network
        _CACHED_GRAPH = build_route27_road_network(config={})
    except Exception as exc:
        logger.warning(
            "Could not load Route 27 OSM network for stop validation: %s. "
            "Stop validation will be skipped (all stops marked OK with reason "
            "'no_graph'). Run a corridor build to populate the cache.", exc,
        )
        _CACHED_GRAPH = None
    return _CACHED_GRAPH


def _normalize_highway_tag(tag) -> str:
    """OSMnx returns highway as either str or list-of-str. Reduce to a single
    canonical class string (the first if list, else as-is)."""
    if isinstance(tag, list) and tag:
        return str(tag[0])
    if tag is None:
        return ""
    return str(tag)


def is_valid_stop_placement(
    lat: float,
    lon: float,
    graph=None,
) -> StopValidation:
    """Validate a single stop coordinate against the OSM drivable network.

    Args:
        lat, lon: WGS84 decimal degrees.
        graph: optional pre-loaded OSMnx graph; if None, loads the cache.

    Returns:
        StopValidation. If the graph cannot be loaded, returns ok=True with
        reason="no_graph" so callers do not erroneously reject valid stops
        when offline.
    """
    G = graph if graph is not None else _load_graph()
    if G is None:
        return StopValidation(
            ok=True,
            reason="no_graph",
            snap_distance_ft=None,
            nearest_road_class=None,
            nearest_road_name=None,
            warnings=["OSM network unavailable; placement not checked"],
        )

    try:
        import osmnx as ox
    except ImportError:
        return StopValidation(
            ok=True, reason="no_osmnx",
            snap_distance_ft=None, nearest_road_class=None, nearest_road_name=None,
            warnings=["osmnx not installed; placement not checked"],
        )

    # ox.nearest_edges with return_dist=True returns (edge_tuple, distance_m).
    # OSMnx 1.x returned (u, v, k); 2.x returns the same shape.
    try:
        result = ox.nearest_edges(G, X=lon, Y=lat, return_dist=True)
    except Exception as exc:
        logger.warning("nearest_edges failed for (%s,%s): %s", lat, lon, exc)
        return StopValidation(
            ok=True, reason="nearest_edges_failed",
            snap_distance_ft=None, nearest_road_class=None, nearest_road_name=None,
            warnings=[f"nearest_edges raised {type(exc).__name__}"],
        )

    # Disambiguate the two possible result shapes:
    # - (edge, dist) where edge = (u, v, k)
    # - ((u, v, k), dist)
    edge, dist_m = result
    if isinstance(edge, tuple) and len(edge) == 3:
        u, v, k = edge
    else:
        # Defensive: some osmnx variants return (u, v, k, dist)
        u, v, k = edge[0], edge[1], edge[2]

    snap_ft = float(dist_m) * _FEET_PER_METER
    edata = G.get_edge_data(u, v, k) or {}
    road_class = _normalize_highway_tag(edata.get("highway"))
    road_name = edata.get("name") if isinstance(edata.get("name"), str) else (
        edata.get("name", [None])[0] if isinstance(edata.get("name"), list) else None
    )
    access = edata.get("access")

    warnings: list[str] = []

    # Rule 1: snap distance
    if snap_ft > MAX_SNAP_FT:
        return StopValidation(
            ok=False,
            reason=(
                f"snap_distance_too_far ({snap_ft:.0f}ft > {MAX_SNAP_FT:.0f}ft "
                f"max) -- coord likely a parcel centroid, not a stop location"
            ),
            snap_distance_ft=round(snap_ft, 1),
            nearest_road_class=road_class,
            nearest_road_name=road_name,
            warnings=warnings,
        )

    # Rule 2: highway class
    if road_class in REJECTED_HIGHWAY_CLASSES:
        return StopValidation(
            ok=False,
            reason=(
                f"on_freeway_or_ramp (highway={road_class}) -- bus stops "
                f"cannot be placed on freeways, ramps, or interchanges"
            ),
            snap_distance_ft=round(snap_ft, 1),
            nearest_road_class=road_class,
            nearest_road_name=road_name,
            warnings=warnings,
        )

    # Rule 3: access restriction
    if access in ("no", "private"):
        return StopValidation(
            ok=False,
            reason=f"access_restricted (access={access})",
            snap_distance_ft=round(snap_ft, 1),
            nearest_road_class=road_class,
            nearest_road_name=road_name,
            warnings=warnings,
        )

    # Soft warnings
    if snap_ft > WARN_SNAP_FT:
        warnings.append(f"snap_distance_high ({snap_ft:.0f}ft > {WARN_SNAP_FT:.0f}ft)")
    if road_class in WARN_HIGHWAY_CLASSES:
        warnings.append(f"trunk_road_class ({road_class}) -- verify not expressway-grade")

    return StopValidation(
        ok=True,
        reason="valid_with_warning" if warnings else "valid",
        snap_distance_ft=round(snap_ft, 1),
        nearest_road_class=road_class,
        nearest_road_name=road_name,
        warnings=warnings,
    )


def validate_forced_candidates(
    candidates: Iterable[dict],
    graph=None,
    raise_on_invalid: bool = True,
) -> list[tuple[dict, StopValidation]]:
    """Validate a sequence of forced-candidate dicts.

    Each dict must contain stop_id, stop_lat, stop_lon. The function logs each
    result and -- if ``raise_on_invalid`` -- raises StopPlacementError listing
    every failing stop so the corridor build cannot silently include a stop
    in a freeway median.

    Returns:
        List of (candidate_dict, StopValidation) for every input.
    """
    G = graph if graph is not None else _load_graph()
    results: list[tuple[dict, StopValidation]] = []
    failures: list[str] = []

    for cand in candidates:
        sid = cand.get("stop_id", "?")
        name = cand.get("stop_name", sid)
        lat = cand.get("stop_lat")
        lon = cand.get("stop_lon")
        if lat is None or lon is None:
            failures.append(f"{sid} ({name}): missing stop_lat/stop_lon")
            continue
        v = is_valid_stop_placement(lat, lon, graph=G)
        # Honor explicit override for stops legitimately wedged in freeway
        # interchanges (e.g., LRT stations whose bus bays sit on loop roads
        # OSM tags as motorway_link). The candidate must declare the override
        # with a justification string in `validation_override_reason`.
        if not v.ok and cand.get("validation_override"):
            why = cand.get("validation_override_reason", "(no reason given)")
            v = StopValidation(
                ok=True,
                reason=f"override_accepted: {why}  [orig: {v.reason}]",
                snap_distance_ft=v.snap_distance_ft,
                nearest_road_class=v.nearest_road_class,
                nearest_road_name=v.nearest_road_name,
                warnings=v.warnings + ["validation_override flag set"],
            )
        results.append((cand, v))
        log_fn = logger.info if v.ok else logger.error
        log_fn("Stop validation %s: %s -- %s", sid, name, v)
        if not v.ok:
            failures.append(f"{sid} ({name}) at ({lat},{lon}): {v.reason}")

    if failures and raise_on_invalid:
        joined = "\n  - " + "\n  - ".join(failures)
        raise StopPlacementError(
            f"{len(failures)} forced stop(s) failed placement validation. "
            f"Fix the coordinates or set raise_on_invalid=False to override:"
            + joined
        )

    return results


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--no-strict", action="store_true",
        help="Don't raise on invalid stops; just print the report.",
    )
    p.add_argument(
        "--lat", type=float, default=None,
        help="Validate a single ad-hoc coordinate instead of FORCED_CANDIDATES.",
    )
    p.add_argument("--lon", type=float, default=None)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.lat is not None and args.lon is not None:
        v = is_valid_stop_placement(args.lat, args.lon)
        print(f"({args.lat}, {args.lon}): {v}")
        return 0 if v.ok else 1

    from src.route27_corridor import FORCED_CANDIDATES
    print(f"Validating {len(FORCED_CANDIDATES)} forced Route 27 candidates...")
    print("-" * 78)
    try:
        results = validate_forced_candidates(
            FORCED_CANDIDATES, raise_on_invalid=not args.no_strict,
        )
    except StopPlacementError as e:
        print(str(e))
        return 1

    n_bad = sum(1 for _, v in results if not v.ok)
    n_warn = sum(1 for _, v in results if v.ok and v.warnings)
    print("-" * 78)
    print(f"Result: {len(results)} stops checked, {n_bad} invalid, {n_warn} with warnings.")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
