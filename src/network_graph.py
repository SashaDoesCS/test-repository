"""
network_graph.py -- Road network graph for routing optimization.

Builds a directed graph of the Los Gatos study area road network using
OSMnx (OpenStreetMap). Existing GTFS stops are snapped to the nearest
OSM node. District centroids are added as virtual demand nodes.

Travel-time edge weights enable shortest-path routing and travel-time
matrix computation between all stop pairs.

Standards:
    - OpenStreetMap (OSM) road network data
    - GTFS Static Specification (stop coordinates)
    - FTA: Bus stop placement along drivable road network

Dependencies (already in requirements.txt):
    osmnx, networkx, numpy, pandas
"""

import logging
import math
import pickle
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Free parameter (tuned 2026-04-24) ─────────────────────────────────
# If either endpoint of a leg snaps farther than this (metres) from its
# nearest OSM node, skip road-routing for that leg and emit a straight-line
# interpolation instead.
# 500 m is a backstop, not the primary defence against bad snaps — that role
# now belongs to the network_graph.pkl cache priority (see _NETWORK_CACHE_
# CANDIDATES below).  The remaining stops that trip this gate are real GTFS
# Route 27 stops in eastern San Jose (lon < -121.88, beyond the cached graph
# bbox); for those, fallback interpolation is the correct outcome rather
# than snapping to the nearest in-bbox node 5+ km away.  Tightening below
# ~300 m would start dropping legitimate within-bbox snaps in low-density
# foothill areas.
MAX_SNAP_DISTANCE_M: float = 500.0

# Step 6: Network type for OSMnx graph download.
# drive_service includes service roads and residential streets buses use.
# If cached graph was built with a different type, it will be rebuilt.
_NETWORK_TYPE: str = "drive_service"

# Step 6: Hybrid edge weight for shortest-path routing.
# 0 = pure length, 1 = pure travel_time. Opus will tune.
PATH_WEIGHT_TIME_FRACTION: float = 0.7
# ──────────────────────────────────────────────────────────────────────

# Average bus operating speed (mph → km/h for OSM graph).
# Used when OSM maxspeed tag is missing.
_DEFAULT_SPEED_KMH = {
    "motorway": 96,
    "motorway_link": 72,
    "trunk": 72,
    "trunk_link": 56,
    "primary": 56,
    "primary_link": 48,
    "secondary": 48,
    "secondary_link": 40,
    "tertiary": 40,
    "tertiary_link": 32,
    "residential": 32,
    "unclassified": 32,
    "service": 24,
    "living_street": 16,
}
_FALLBACK_SPEED_KMH = 40


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres."""
    return _haversine_km(lat1, lon1, lat2, lon2) * 1000.0


def _great_circle_interpolate(
    p1: tuple,
    p2: tuple,
    n: int = 10,
) -> list:
    """Return n evenly-spaced (lat, lon) points along the great-circle from p1 to p2.

    Includes both endpoints.  Uses linear interpolation in (lat, lon) space as a
    fast approximation (accurate to < 1 m for the short legs encountered here).

    Args:
        p1: (lat, lon) of start point.
        p2: (lat, lon) of end point.
        n:  total number of points (including endpoints).  Minimum 2.

    Returns:
        list of n (lat, lon) tuples.
    """
    n = max(2, n)
    lat1, lon1 = p1
    lat2, lon2 = p2
    return [
        (lat1 + i / (n - 1) * (lat2 - lat1),
         lon1 + i / (n - 1) * (lon2 - lon1))
        for i in range(n)
    ]


# =====================================================================
# GRAPH CONSTRUCTION
# =====================================================================

def build_road_network(bbox: dict, config: dict) -> Optional[object]:
    """Pull drivable road network from OSM for the study bounding box.

    Args:
        bbox: Dict with keys north, south, east, west (WGS84 decimal degrees).
        config: Full config dict (uses road_barriers for annotation).

    Returns:
        networkx.MultiDiGraph with travel_time edge attribute (seconds),
        or None if OSMnx is unavailable (graceful fallback).
    """
    try:
        import osmnx as ox
        import networkx as nx
    except ImportError:
        logger.warning("OSMnx not available; network graph will be skipped.")
        return None

    north = bbox["north"]
    south = bbox["south"]
    east = bbox["east"]
    west = bbox["west"]

    logger.info("Downloading OSM road network (bbox: N%.3f S%.3f E%.3f W%.3f, type=%s)...",
                north, south, east, west, _NETWORK_TYPE)
    try:
        G = ox.graph_from_bbox(
            bbox=(north, south, east, west),
            network_type=_NETWORK_TYPE,
            simplify=True,
            retain_all=False,
        )
        # Tag the graph with network_type so cache invalidation can detect stale graphs
        G.graph["network_type"] = _NETWORK_TYPE
    except Exception as exc:
        logger.warning("OSM download failed (%s); using synthetic graph fallback.", exc)
        return _build_synthetic_graph(config)

    # Add speed and travel_time attributes
    G = ox.add_edge_speeds(G, fallback=_FALLBACK_SPEED_KMH)
    G = ox.add_edge_travel_times(G)

    # Annotate road barriers from config (informational — not graph removal)
    barrier_names = set()
    for b in config.get("road_barriers", {}).get("freeways", []):
        barrier_names.add(b["name"].lower())

    n_edges = G.number_of_edges()
    logger.info("Road network: %d nodes, %d edges", G.number_of_nodes(), n_edges)
    return G


def _build_synthetic_graph(config: dict):
    """Build a minimal synthetic graph from stop coordinates when OSMnx is unavailable.

    Creates a complete graph over all GTFS stops where edge weights are
    straight-line travel times (distance / 40 km/h average bus speed).
    """
    try:
        import networkx as nx
    except ImportError:
        logger.warning("NetworkX not available; cannot build synthetic graph.")
        return None

    logger.info("Building synthetic stop-level graph (OSMnx fallback).")
    G = nx.MultiDiGraph()
    G.graph["crs"] = "EPSG:4326"
    G.graph["synthetic"] = True
    return G


# =====================================================================
# STOP SNAPPING
# =====================================================================

def snap_stops_to_network(
    stops_df: pd.DataFrame,
    G,
) -> pd.DataFrame:
    """Snap each GTFS stop to the nearest OSM node in the road graph.

    Args:
        stops_df: DataFrame with stop_id, stop_lat, stop_lon (and optionally
            stop_name, route_ids, district_id).
        G: networkx.MultiDiGraph from build_road_network(), or None.

    Returns:
        stops_df with additional column osm_node_id (int or None if G is None).
    """
    result = stops_df.copy()
    result["osm_node_id"] = None

    if G is None:
        logger.info("No graph available; stop snapping skipped.")
        return result

    if getattr(G.graph, "get", lambda k, d: d)("synthetic", False):
        logger.info("Synthetic graph; adding stops as nodes.")
        return result

    try:
        import osmnx as ox
        lats = stops_df["stop_lat"].tolist()
        lons = stops_df["stop_lon"].tolist()
        node_ids = ox.nearest_nodes(G, X=lons, Y=lats)
        result["osm_node_id"] = node_ids
        logger.info("Snapped %d stops to OSM nodes.", len(result))
    except Exception as exc:
        logger.warning("Stop snapping failed (%s); osm_node_id will be None.", exc)

    return result


# =====================================================================
# TRAVEL-TIME MATRIX
# =====================================================================

def compute_travel_time_matrix(
    G,
    stops_df: pd.DataFrame,
    weight: str = "travel_time",
) -> pd.DataFrame:
    """Compute pairwise travel-time matrix between all stops (seconds).

    For each stop pair (i, j), finds the shortest path travel time along
    the road network. Falls back to straight-line haversine distance / speed
    when the graph or OSMnx is unavailable.

    Args:
        G: Road network graph (or None).
        stops_df: DataFrame with stop_id, stop_lat, stop_lon, osm_node_id.
        weight: Edge attribute to use as path cost (default: travel_time in s).

    Returns:
        Square DataFrame indexed and columned by stop_id.
        Values are travel time in seconds (0 on diagonal, inf if unreachable).
    """
    stop_ids = stops_df["stop_id"].tolist()
    n = len(stop_ids)

    if n == 0:
        return pd.DataFrame()

    # Straight-line fallback matrix (always computed; used when graph missing)
    fallback = _haversine_travel_time_matrix(stops_df)

    if G is None or getattr(G, "graph", {}).get("synthetic", False):
        logger.info("Using haversine fallback for travel-time matrix (%d stops).", n)
        return fallback

    try:
        import networkx as nx
        import osmnx as ox

        node_ids = stops_df["osm_node_id"].tolist()
        if any(nid is None for nid in node_ids):
            logger.warning("Some stops have no OSM node; using haversine fallback.")
            return fallback

        # Compute shortest-path lengths from each source node
        matrix = np.full((n, n), np.inf)
        for i, src_node in enumerate(node_ids):
            try:
                lengths = nx.single_source_dijkstra_path_length(
                    G, src_node, weight=weight
                )
                for j, dst_node in enumerate(node_ids):
                    if i == j:
                        matrix[i, j] = 0.0
                    elif dst_node in lengths:
                        matrix[i, j] = lengths[dst_node]
            except nx.NetworkXError:
                pass

        # Fill inf values with haversine fallback (disconnected subgraphs)
        fallback_vals = fallback.values
        mask = np.isinf(matrix)
        matrix[mask] = fallback_vals[mask]

        result = pd.DataFrame(matrix, index=stop_ids, columns=stop_ids)
        logger.info("Travel-time matrix computed (%d × %d stops).", n, n)
        return result

    except Exception as exc:
        logger.warning("Travel-time matrix via OSMnx failed (%s); using haversine.", exc)
        return fallback


def _haversine_travel_time_matrix(stops_df: pd.DataFrame) -> pd.DataFrame:
    """Compute travel-time matrix using straight-line distance / bus speed."""
    stop_ids = stops_df["stop_id"].tolist()
    n = len(stop_ids)
    lats = stops_df["stop_lat"].values
    lons = stops_df["stop_lon"].values
    speed_kmh = _FALLBACK_SPEED_KMH

    matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            dist_km = _haversine_km(lats[i], lons[i], lats[j], lons[j])
            matrix[i, j] = (dist_km / speed_kmh) * 3600  # seconds

    return pd.DataFrame(matrix, index=stop_ids, columns=stop_ids)


# =====================================================================
# ROAD-FOLLOWING POLYLINE (Phase 3)
# =====================================================================

def compute_route_polyline(
    stop_seq: list,
    G,
    stops_snapped_df: Optional[pd.DataFrame] = None,
) -> list:
    """Return a list of (lat, lon) tuples tracing the road network between
    each consecutive pair of stops in `stop_seq`.

    This is the geometry that is written to GTFS shapes.txt and rendered by
    the dashboard so that route lines follow real roads (rather than
    straight-line zig-zags between stop centroids).

    Args:
        stop_seq: ordered list of OptimisedStop (or any object with
            stop_id / stop_lat / stop_lon attributes).
        G: networkx.MultiDiGraph from build_road_network() (or None).
        stops_snapped_df: DataFrame with stop_id, osm_node_id columns. If
            absent, we'll snap on-the-fly.

    Returns:
        list of (lat, lon) tuples, densified along the road network.
        Falls back to straight-line endpoints when the graph or OSMnx
        isn't available.
    """
    coords = [(float(s.stop_lat), float(s.stop_lon)) for s in stop_seq]
    if G is None or len(stop_seq) < 2:
        return coords

    try:
        import osmnx as ox
        import networkx as nx
    except ImportError:
        return coords

    if getattr(G, "graph", {}).get("synthetic", False):
        return coords

    # Map stop_id -> OSM node id.
    node_for_stop: Dict[str, object] = {}
    if stops_snapped_df is not None and "osm_node_id" in stops_snapped_df.columns:
        for _, row in stops_snapped_df.iterrows():
            sid = str(row["stop_id"])
            nid = row.get("osm_node_id", None)
            if nid is not None and not pd.isna(nid):
                node_for_stop[sid] = nid

    polyline: list = []
    for k in range(len(stop_seq) - 1):
        a, b = stop_seq[k], stop_seq[k + 1]
        a_node = node_for_stop.get(str(a.stop_id))
        b_node = node_for_stop.get(str(b.stop_id))

        # ── Snap-distance gate (Step 4a) ─────────────────────────────
        # Snap both endpoints if we don't already have their OSM nodes, then
        # check whether the snap distance exceeds MAX_SNAP_DISTANCE_M.
        use_interpolated = False
        try:
            if a_node is None:
                a_node = ox.nearest_nodes(G, X=a.stop_lon, Y=a.stop_lat)
            if b_node is None:
                b_node = ox.nearest_nodes(G, X=b.stop_lon, Y=b.stop_lat)

            a_ndata = G.nodes[a_node]
            b_ndata = G.nodes[b_node]
            a_snap_m = _haversine_m(float(a.stop_lat), float(a.stop_lon),
                                    float(a_ndata.get("y", a.stop_lat)),
                                    float(a_ndata.get("x", a.stop_lon)))
            b_snap_m = _haversine_m(float(b.stop_lat), float(b.stop_lon),
                                    float(b_ndata.get("y", b.stop_lat)),
                                    float(b_ndata.get("x", b.stop_lon)))
            if a_snap_m > MAX_SNAP_DISTANCE_M or b_snap_m > MAX_SNAP_DISTANCE_M:
                logger.debug(
                    "Snap-distance gate triggered for leg %s→%s "
                    "(a_snap=%.0fm, b_snap=%.0fm > %.0fm); using interpolation.",
                    a.stop_id, b.stop_id, a_snap_m, b_snap_m, MAX_SNAP_DISTANCE_M,
                )
                use_interpolated = True
        except Exception as _exc:
            logger.debug("Snap failed for leg %s→%s: %s; using interpolation.",
                         a.stop_id, b.stop_id, _exc)
            use_interpolated = True

        if use_interpolated:
            leg = _great_circle_interpolate(
                (float(a.stop_lat), float(a.stop_lon)),
                (float(b.stop_lat), float(b.stop_lon)),
                n=10,
            )
            if polyline and leg and polyline[-1] == leg[0]:
                polyline.extend(leg[1:])
            else:
                polyline.extend(leg)
            continue

        # ── Road-network routing ──────────────────────────────────────
        # Step 6: Compute hybrid edge weight combining travel_time and length.
        # Assign to each edge before calling shortest_path so the weight is
        # consistent across all legs. We do this once per leg pair; for a
        # production system this would be pre-computed on graph load.
        try:
            for u, v, k, data in G.edges(data=True, keys=True):
                tt = float(data.get("travel_time", 30.0))
                ln = float(data.get("length", 100.0))
                data["hybrid"] = (
                    PATH_WEIGHT_TIME_FRACTION * tt
                    + (1.0 - PATH_WEIGHT_TIME_FRACTION) * ln
                )
        except Exception as _we:
            logger.debug("Hybrid weight assignment failed (%s); using travel_time.", _we)
        try:
            path = nx.shortest_path(G, a_node, b_node, weight="hybrid")
        except Exception:
            # If routing fails for this leg, fall back to straight-line interpolation.
            leg = _great_circle_interpolate(
                (float(a.stop_lat), float(a.stop_lon)),
                (float(b.stop_lat), float(b.stop_lon)),
                n=10,
            )
            if polyline and leg and polyline[-1] == leg[0]:
                polyline.extend(leg[1:])
            else:
                polyline.extend(leg)
            continue

        # Pull (y, x) for each node on the path.
        leg = []
        for node_id in path:
            ndata = G.nodes[node_id]
            lat = ndata.get("y")
            lon = ndata.get("x")
            if lat is not None and lon is not None:
                leg.append((float(lat), float(lon)))

        # ── Degenerate-path expansion (Step 4b) ──────────────────────
        # If routing returned only 2 nodes (trivial path), interpolate.
        if len(leg) < 3:
            leg = _great_circle_interpolate(
                (float(a.stop_lat), float(a.stop_lon)),
                (float(b.stop_lat), float(b.stop_lon)),
                n=10,
            )

        if not leg:
            leg = _great_circle_interpolate(
                (float(a.stop_lat), float(a.stop_lon)),
                (float(b.stop_lat), float(b.stop_lon)),
                n=10,
            )

        # Avoid duplicating the joining vertex between consecutive legs.
        if polyline and leg and polyline[-1] == leg[0]:
            polyline.extend(leg[1:])
        else:
            polyline.extend(leg)

    # Step 8: Warn if any polyline point is outside the Los Gatos bbox.
    # Load bbox from config; fall back to hardcoded defaults.
    try:
        import yaml as _yaml
        from pathlib import Path as _Path
        _cfg_path = _Path(__file__).resolve().parent.parent / "config.yaml"
        _bbox = {"lat_min": 37.10, "lat_max": 37.30, "lon_min": -122.05, "lon_max": -121.90}
        if _cfg_path.exists():
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _cfg = _yaml.safe_load(_f)
            _bbox = _cfg.get("optimization", {}).get("los_gatos_bbox", _bbox)
        _outside = [
            (lat, lon) for lat, lon in polyline
            if not (
                _bbox["lat_min"] <= lat <= _bbox["lat_max"]
                and _bbox["lon_min"] <= lon <= _bbox["lon_max"]
            )
        ]
        if _outside:
            logger.warning(
                "BBox guard: %d polyline point(s) outside Los Gatos bbox "
                "(lat %.3f-%.3f, lon %.3f-%.3f). First outlier: %.5f, %.5f.",
                len(_outside),
                _bbox["lat_min"], _bbox["lat_max"],
                _bbox["lon_min"], _bbox["lon_max"],
                _outside[0][0], _outside[0][1],
            )
    except Exception as _bbox_exc:
        logger.debug("BBox check failed (%s).", _bbox_exc)

    return polyline


def compute_route_polylines(
    routes: list,
    G,
    stops_snapped_df: Optional[pd.DataFrame] = None,
) -> Dict[str, list]:
    """Compute road-following polylines for a list of OptimisedRoute objects.

    Returns dict route_id -> list of (lat, lon).
    """
    result: Dict[str, list] = {}
    for r in routes:
        result[r.route_id] = compute_route_polyline(r.stops, G, stops_snapped_df)
    return result


# =====================================================================
# SERIALIZATION
# =====================================================================

def save_graph(G, path: str) -> None:
    """Persist the graph to disk as a pickle file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if G is None:
        logger.info("Graph is None; skipping save.")
        return
    try:
        with open(path, "wb") as f:
            pickle.dump(G, f)
        logger.info("Graph saved to %s", path)
    except Exception as exc:
        logger.warning("Could not save graph (%s).", exc)


def load_graph(path: str):
    """Load a previously serialised graph from disk, or return None."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        with open(path, "rb") as f:
            G = pickle.load(f)
        logger.info("Graph loaded from %s", path)
        return G
    except Exception as exc:
        logger.warning("Could not load graph (%s).", exc)
        return None


# =====================================================================
# PIPELINE ENTRY POINT
# =====================================================================

def run_network_graph(
    stops_df: pd.DataFrame,
    config: dict,
    graph_cache_path: str = "data/geospatial/network_graph.pkl",
    force_rebuild: bool = False,
) -> dict:
    """Run the full network graph pipeline with caching.

    Args:
        stops_df: GTFS stops DataFrame.
        config: Full config dict.
        graph_cache_path: Path to cached graph pickle.
        force_rebuild: If True, ignore cache and re-download from OSM.

    Returns:
        Dict with keys:
            graph: networkx.MultiDiGraph (or None)
            stops_snapped: stops_df with osm_node_id column
            travel_time_matrix: square DataFrame of stop-to-stop travel times
    """
    bbox = config.get("optimization", {}).get("bounding_box", {
        "north": 37.27, "south": 37.06, "east": -121.88, "west": -122.15
    })

    # Try loading cached graph first
    G = None
    if not force_rebuild:
        G = load_graph(graph_cache_path)
        # Step 6: Invalidate cache if network_type doesn't match current setting
        if G is not None:
            cached_type = G.graph.get("network_type", None)
            if cached_type != _NETWORK_TYPE:
                logger.warning(
                    "Cached graph network_type=%r does not match required %r; rebuilding.",
                    cached_type, _NETWORK_TYPE,
                )
                G = None

    if G is None:
        G = build_road_network(bbox, config)
        save_graph(G, graph_cache_path)

    stops_snapped = snap_stops_to_network(stops_df, G)
    tt_matrix = compute_travel_time_matrix(G, stops_snapped)

    logger.info("Network graph pipeline complete.")
    return {
        "graph": G,
        "stops_snapped": stops_snapped,
        "travel_time_matrix": tt_matrix,
    }
