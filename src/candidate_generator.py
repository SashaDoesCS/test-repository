"""
candidate_generator.py -- Generate synthetic bus stop candidates for underserved districts.

Places candidate stops along drivable road edges at FTA-recommended ¼-mile spacing
(FTA Circular 9040.1G) for the top underserved districts by unmet need.

Reuses the cached route27 OSMnx road network (covers all Los Gatos districts).
Falls back to a uniform grid per district when the network is unavailable.
"""

import logging
import math
import pickle
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Earth radius in miles
_R_MILES = 3958.8
_MI_PER_DEG_LAT = 69.0
_MI_PER_DEG_LON = 54.6  # cos(37°) × 69

# Where the pipeline caches the OSMnx road graph (written by route27_corridor.py).
# network_graph.pkl covers the full study-area bounding box (incl. D9/Summit area)
# and is checked first; route27_network.pkl covers only the Route 27 corridor.
_NETWORK_CACHE_CANDIDATES = [
    PROJECT_ROOT / "data" / "geospatial" / "network_graph.pkl",
    PROJECT_ROOT / "data" / "geospatial" / "route27_network.pkl",
    PROJECT_ROOT / "data" / "cache" / "route27_road_network.pkl",
]


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return _R_MILES * 2 * math.asin(math.sqrt(a))


def _load_cached_network():
    """Load the pre-built OSMnx road graph from cache, or return None."""
    for path in _NETWORK_CACHE_CANDIDATES:
        if path.exists():
            try:
                with open(path, "rb") as f:
                    G = pickle.load(f)
                logger.info("Loaded cached road network from %s (%d nodes, %d edges).",
                            path, G.number_of_nodes(), G.number_of_edges())
                return G
            except Exception as exc:
                logger.warning("Failed to load network cache %s: %s", path, exc)
    return None


def _points_along_edge(lat1, lon1, lat2, lon2, spacing_miles: float):
    """Yield (lat, lon) points at spacing_miles intervals along a straight edge."""
    total = _haversine_miles(lat1, lon1, lat2, lon2)
    if total < 1e-6:
        return
    if total < spacing_miles:
        yield (lat1 + lat2) / 2, (lon1 + lon2) / 2
        return
    n = max(1, int(total / spacing_miles))
    for i in range(1, n + 1):
        t = i / (n + 1)
        yield lat1 + t * (lat2 - lat1), lon1 + t * (lon2 - lon1)


def _candidates_from_graph(
    G,
    bbox_n: float,
    bbox_s: float,
    bbox_e: float,
    bbox_w: float,
    existing_latlons: list,
    spacing_miles: float,
    exclusion_miles: float,
    max_collect: int,
) -> list:
    """Extract candidate points from a graph filtered to a bounding box."""
    node_data = {
        nid: (d["y"], d["x"])
        for nid, d in G.nodes(data=True)
        if bbox_s <= d.get("y", 0) <= bbox_n and bbox_w <= d.get("x", 0) <= bbox_e
    }
    if not node_data:
        return []

    candidates = []
    for u, v, _ in G.edges(keys=True):
        if u not in node_data or v not in node_data:
            continue
        lat1, lon1 = node_data[u]
        lat2, lon2 = node_data[v]
        for lat, lon in _points_along_edge(lat1, lon1, lat2, lon2, spacing_miles):
            if any(_haversine_miles(lat, lon, elat, elon) < exclusion_miles
                   for elat, elon in existing_latlons):
                continue
            candidates.append((lat, lon))
            if len(candidates) >= max_collect:
                return candidates
    return candidates


def _min_dist_to_polyline_miles(
    lat: float,
    lon: float,
    polyline: List[Tuple[float, float]],
) -> float:
    """Return minimum haversine distance (miles) from (lat, lon) to any vertex of polyline."""
    if not polyline:
        return float("inf")
    return min(_haversine_miles(lat, lon, plat, plon) for plat, plon in polyline)


def _filter_by_corridor(
    candidates: list,
    corridor_polyline: List[Tuple[float, float]],
    corridor_buffer_m: float,
) -> list:
    """Drop candidates farther than corridor_buffer_m from the corridor polyline.

    Uses haversine distance to each vertex of the polyline as a fast approximation.
    Logs a warning (for Opus tuning) if all candidates are filtered out.
    """
    if not corridor_polyline:
        return candidates
    buffer_miles = corridor_buffer_m / 1609.344
    kept = [
        (lat, lon) for lat, lon in candidates
        if _min_dist_to_polyline_miles(lat, lon, corridor_polyline) <= buffer_miles
    ]
    n_dropped = len(candidates) - len(kept)
    if n_dropped:
        logger.debug("Corridor filter: dropped %d/%d candidates (buffer=%.0fm).",
                     n_dropped, len(candidates), corridor_buffer_m)
    if not kept and candidates:
        logger.warning(
            "Corridor filter rejected ALL %d candidates (buffer=%.0fm). "
            "No synthetic stops will be added for this district on this route. "
            "Consider loosening corridor_buffer_m for Opus tuning.",
            len(candidates), corridor_buffer_m,
        )
    return kept


def _candidates_grid_fallback(
    centroid_lat: float,
    centroid_lon: float,
    area_sq_miles: float,
    existing_latlons: list,
    spacing_miles: float,
    exclusion_miles: float,
    max_collect: int,
) -> list:
    """Grid fallback when OSMnx network is unavailable."""
    half_side = max(0.5, math.sqrt(area_sq_miles) / 2.0)
    lat_step = spacing_miles / _MI_PER_DEG_LAT
    lon_step = spacing_miles / _MI_PER_DEG_LON

    lat_s = centroid_lat - half_side / _MI_PER_DEG_LAT
    lat_n = centroid_lat + half_side / _MI_PER_DEG_LAT + lat_step
    lon_w = centroid_lon - half_side / _MI_PER_DEG_LON
    lon_e = centroid_lon + half_side / _MI_PER_DEG_LON + lon_step

    candidates = []
    lat = lat_s
    while lat <= lat_n:
        lon = lon_w
        while lon <= lon_e:
            if not any(_haversine_miles(lat, lon, elat, elon) < exclusion_miles
                       for elat, elon in existing_latlons):
                candidates.append((lat, lon))
                if len(candidates) >= max_collect:
                    return candidates
            lon += lon_step
        lat += lat_step
    return candidates


def generate_synthetic_candidates(
    existing_stops: pd.DataFrame,
    districts: pd.DataFrame,
    unmet_need: pd.DataFrame,
    tdi: pd.DataFrame,
    spacing_ft: int = 1320,
    max_per_district: int = 8,
    corridor_polyline: Optional[List[Tuple[float, float]]] = None,
    corridor_buffer_m: float = 400.0,
) -> pd.DataFrame:
    """Generate synthetic bus stop candidates in underserved districts.

    Places candidates along drivable road edges at FTA ¼-mile spacing for
    the top-10 districts by unmet need.  Reuses the cached Route 27 OSMnx
    road graph (covers all Los Gatos districts); falls back to a uniform
    grid if the cache is absent.

    Args:
        existing_stops: GTFS stops with stop_lat, stop_lon.
        districts: district_profile_initial with id/district_id, centroid_lat,
                   centroid_lon, area_sq_miles columns.
        unmet_need: has district_id and unmet_need columns.
        tdi: has district_id column (for schema alignment).
        spacing_ft: candidate spacing in feet (1320 = ¼ mile, FTA Circular 9040.1G).
        max_per_district: cap on unique candidates per district.
        corridor_polyline: optional list of (lat, lon) tuples defining the existing
            route corridor. When provided, candidates farther than corridor_buffer_m
            from the nearest vertex are dropped. Greenfield calls omit this arg.
        corridor_buffer_m: maximum distance (metres) a candidate may be from the
            corridor_polyline. Default 400 m. Tune via Opus agent.

    Returns:
        DataFrame with columns: stop_id, stop_name, stop_lat, stop_lon,
        district_id, route_ids, is_synthetic, wheelchair_boarding.
    """
    spacing_miles = spacing_ft / 5280.0
    exclusion_miles = (spacing_ft / 2) / 5280.0

    # Build district lookup: id → centroid + area
    id_col = "id" if "id" in districts.columns else "district_id"
    dist_lookup = {}
    for _, row in districts.iterrows():
        did = str(row[id_col])
        dist_lookup[did] = {
            "centroid_lat": float(row.get("centroid_lat", 37.23)),
            "centroid_lon": float(row.get("centroid_lon", -121.98)),
            "area_sq_miles": float(row.get("area_sq_miles", 1.0)),
        }

    # Top 10 districts by unmet_need
    un = unmet_need.copy()
    if "district_id" not in un.columns and "id" in un.columns:
        un = un.rename(columns={"id": "district_id"})
    top_districts = (
        un.sort_values("unmet_need", ascending=False)
        .head(10)["district_id"]
        .tolist()
    )
    logger.info("Generating synthetic candidates for top districts: %s", top_districts)

    # Existing stop coordinates for exclusion test
    existing_latlons = list(zip(
        existing_stops["stop_lat"].astype(float),
        existing_stops["stop_lon"].astype(float),
    ))

    # Load cached road network once (covers all districts)
    G = _load_cached_network()

    rows = []
    for did in top_districts:
        info = dist_lookup.get(did)
        if info is None:
            logger.warning("No district info for %s; skipping.", did)
            continue

        clat = info["centroid_lat"]
        clon = info["centroid_lon"]
        area = info["area_sq_miles"]

        half_side = max(0.5, math.sqrt(area) / 2.0)
        bbox_n = clat + half_side / _MI_PER_DEG_LAT
        bbox_s = clat - half_side / _MI_PER_DEG_LAT
        bbox_e = clon + half_side / _MI_PER_DEG_LON
        bbox_w = clon - half_side / _MI_PER_DEG_LON

        max_collect = max_per_district * 4

        if G is not None:
            raw = _candidates_from_graph(
                G, bbox_n, bbox_s, bbox_e, bbox_w,
                existing_latlons, spacing_miles, exclusion_miles, max_collect,
            )
        else:
            raw = []

        if not raw:
            logger.debug("Graph yielded no candidates for %s; using grid fallback.", did)
            raw = _candidates_grid_fallback(
                clat, clon, area,
                existing_latlons, spacing_miles, exclusion_miles, max_collect,
            )

        # Apply corridor filter if provided
        if corridor_polyline:
            raw = _filter_by_corridor(raw, corridor_polyline, corridor_buffer_m)

        # Deduplicate: keep first max_per_district unique candidates
        seen = []
        for lat, lon in raw:
            if not any(_haversine_miles(lat, lon, slat, slon) < exclusion_miles
                       for slat, slon in seen):
                seen.append((lat, lon))
            if len(seen) >= max_per_district:
                break

        for n, (lat, lon) in enumerate(seen, start=1):
            rows.append({
                "stop_id": f"NEW_{did}_{n}",
                "stop_name": f"Synthetic {did} #{n}",
                "stop_lat": round(lat, 6),
                "stop_lon": round(lon, 6),
                "district_id": did,
                "route_ids": [],
                "is_synthetic": True,
                "wheelchair_boarding": 1,
            })

        logger.info("  District %s: %d synthetic candidates placed.", did, len(seen))

    if rows:
        result_df = pd.DataFrame(rows)
        # Step 8: Drop candidates outside the Los Gatos bounding box.
        # This guards against grid/graph candidates that land outside the study area.
        import yaml as _yaml
        _cfg_path = PROJECT_ROOT / "config.yaml"
        _bbox = {"lat_min": 37.10, "lat_max": 37.30, "lon_min": -122.05, "lon_max": -121.90}
        try:
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _cfg = _yaml.safe_load(_f)
            _bbox = _cfg.get("optimization", {}).get("los_gatos_bbox", _bbox)
        except Exception:
            pass
        _n_before = len(result_df)
        result_df = result_df[
            (result_df["stop_lat"] >= _bbox["lat_min"])
            & (result_df["stop_lat"] <= _bbox["lat_max"])
            & (result_df["stop_lon"] >= _bbox["lon_min"])
            & (result_df["stop_lon"] <= _bbox["lon_max"])
        ]
        _n_dropped = _n_before - len(result_df)
        if _n_dropped > 0:
            logger.info("BBox guard: dropped %d candidates outside Los Gatos bbox.", _n_dropped)
        return result_df
    return pd.DataFrame(columns=[
        "stop_id", "stop_name", "stop_lat", "stop_lon",
        "district_id", "route_ids", "is_synthetic", "wheelchair_boarding",
    ])
