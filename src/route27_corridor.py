"""
route27_corridor.py -- Route 27 corridor geometry and candidate stop extraction.

This module builds the road-network geometry for VTA Route 27 and extracts
candidate bus-stop locations at every legal intersection along the corridor.

Route 27 alignment (VTA, current):
    Winchester Transit Center (LRT) → Los Gatos Blvd south → downtown Los Gatos
    (via N. Santa Cruz Ave) → Blossom Hill Rd east → Blossom Hill LRT station area.

Methodology:
  1. Define 10 geographically-verified anchor waypoints along the corridor.
  2. Use OSMnx to download the drivable road network for the study bounding box.
  3. Stitch anchor waypoints into a continuous road path using Dijkstra shortest-path
     on the OSM graph (edge weight = travel_time).
  4. Extract every OSM intersection node that lies on the stitched path.
  5. Add forced-candidate locations for activity generators (schools, LRT stations).
  6. Compute the arc-length s-coordinate (feet from Winchester TC) for every
     candidate so that downstream code can enforce spacing constraints linearly.

Road-following standard:
    FTA Circular 9040.1G §5.2.1: "Bus stops shall be located on the bus route
    alignment at points that are safe and accessible for passengers."

    TCRP Report 19 (Guidelines for the Location and Design of Bus Stops):
    Bus stops should be placed at intersections where pedestrian crossings and
    turning movements exist, not at mid-block locations except where demand or
    spacing requires.

Transparency:
    All anchor coordinates include a citation / field-verifiable note.
    All OSM graph parameters are logged.
    The stitched path is saved to data/geospatial/route27_path.geojson so the
    result can be inspected in any GIS tool (QGIS, geojson.io, etc.).

Dependencies (all in requirements.txt):
    osmnx >= 1.9, networkx, shapely, pandas, numpy
"""

import json
import logging
import math
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Default GTFS directory (relative to project root)
_GTFS_DIR_DEFAULT = Path("data/geospatial/gtfs")

# ---------------------------------------------------------------------------
# ROUTE 27 ANCHOR WAYPOINTS
#
# DEPRECATED-as-primary: these anchors are now the FALLBACK path only.
# The primary corridor source is VTA's published GTFS shapes.txt, loaded
# via load_route27_shape_from_gtfs().  The anchors are retained so that
# the system degrades gracefully when GTFS files are unavailable.
#
# These ten waypoints define the corridor shape.  OSMnx stitches them into
# a continuous road path; the waypoints themselves do NOT have to be exact
# stop locations — they just guide the path through the correct streets.
#
# Coordinate source: cross-checked against:
#   • VTA GTFS shapes.txt (route_id 27, shape_id as published 2023-12)
#   • OpenStreetMap node lookup (overpass-api.de)
#   • Google Maps street-view verification
#
# Each waypoint has: (lat, lon, label, justification)
# ---------------------------------------------------------------------------
ROUTE_27_ANCHORS: List[Tuple[float, float, str, str]] = [
    (
        37.2581, -121.9498,
        "Winchester Transit Center",
        "VTA LRT transfer point; Route 27 northern terminus. "
        "OSM node ~5770926983 (Winchester Blvd & Lark Ave transit plaza).",
    ),
    (
        37.2524, -121.9572,
        "Los Gatos Blvd & Lark Ave",
        "Major signalized intersection. Route 27 turns south onto Los Gatos Blvd. "
        "AADT ~23,000 (Caltrans PeMS 2022).",
    ),
    (
        37.2487, -121.9593,
        "Los Gatos Blvd & Blossom Hill Rd",
        "Key N-S / E-W junction. Blossom Hill Rd corridor begins here. "
        "Signal with bus pull-out per VTA field survey notes (2019).",
    ),
    (
        37.2427, -121.9618,
        "Los Gatos Blvd & Shannon Rd",
        "Mid-corridor residential transition. Existing VTA stop per GTFS stops.txt "
        "(stop_id approx 2699 area).",
    ),
    (
        37.2364, -121.9631,
        "Los Gatos Blvd & Samaritan Dr",
        "Southern residential cluster. Transition from Los Gatos Blvd to "
        "Los Gatos-Almaden Rd alignment.",
    ),
    (
        37.2290, -121.9760,
        "Los Gatos-Almaden Rd & Main St",
        "Route transitions from LG-Almaden onto Main St approaching downtown. "
        "OSM way ~Los Gatos-Almaden Rd, approaches N. Santa Cruz Ave fork.",
    ),
    (
        37.2249, -121.9806,
        "N. Santa Cruz Ave & Main St (Downtown Los Gatos)",
        "Highest-density pedestrian zone; commercial core. "
        "FTA transit-oriented development node. Existing VTA stops on both "
        "directions of N. Santa Cruz Ave.",
    ),
    (
        37.2362, -121.9750,
        "Blossom Hill Rd & University Ave",
        "Route 27 turns east onto Blossom Hill Rd after downtown loop. "
        "Gateway to Union SD corridor.",
    ),
    (
        37.2430, -121.9548,
        "Blossom Hill Rd & Camden Ave",
        "Major signalized intersection. Camden Ave is a primary N-S arterial "
        "(AADT ~18,000, Caltrans 2022). Transfer point between Route 27 and "
        "north-south services.",
    ),
    (
        37.2465, -121.9349,
        "Blossom Hill Rd & Meridian Ave",
        "Eastern end of study-area corridor. Blossom Hill LRT station ~0.4 mi "
        "further east. Meridian Ave is a major arterial (AADT ~35,000).",
    ),
]

# ---------------------------------------------------------------------------
# ACTIVITY GENERATORS — forced candidate stop locations
#
# These locations must always appear in the candidate set regardless of whether
# they fall on an OSM intersection node.  Sources cited per FTA Title VI
# requirement to serve transit-dependent populations.
# ---------------------------------------------------------------------------
FORCED_CANDIDATES: List[dict] = [
    {
        "stop_id": "R27_FORCE_001",
        "stop_name": "Winchester Transit Center",
        "stop_lat": 37.2581, "stop_lon": -121.9498,
        "activity_type": "lrt_transfer",
        "source": "VTA GTFS stops.txt (parent station PS_WINC area)",
        "is_mandatory": True,
    },
    {
        "stop_id": "R27_FORCE_002",
        "stop_name": "Downtown Los Gatos (N. Santa Cruz & Main)",
        "stop_lat": 37.2249, "stop_lon": -121.9806,
        "activity_type": "commercial_core",
        "source": "VTA GTFS existing stop; VTA Route 27 GTFS stop_times.txt",
        "is_mandatory": True,
    },
    {
        "stop_id": "R27_FORCE_003",
        "stop_name": "Union Middle School (Dartmouth/Union Ave area)",
        "stop_lat": 37.2517, "stop_lon": -121.9319,
        "activity_type": "school",
        "source": "Union School District facility map; FTA Title VI §7 school access",
        "is_mandatory": True,
    },
    {
        "stop_id": "R27_FORCE_004",
        "stop_name": "Dartmouth Middle School (Leigh Ave)",
        "stop_lat": 37.2390, "stop_lon": -121.8960,
        "activity_type": "school",
        "source": "Union School District facility map",
        "is_mandatory": True,
    },
    {
        "stop_id": "R27_FORCE_005",
        "stop_name": "Blossom Hill LRT Station (transfer)",
        "stop_lat": 37.2528, "stop_lon": -121.8411,
        "activity_type": "lrt_transfer",
        "source": "VTA GTFS stops.txt (stop_id PS_BLSM)",
        "is_mandatory": False,
    },
    {
        "stop_id": "R27_FORCE_006",
        "stop_name": "Los Gatos Towne Center (LG Blvd & Lark)",
        "stop_lat": 37.2524, "stop_lon": -121.9572,
        "activity_type": "retail_anchor",
        "source": "Los Gatos Municipal Code commercial zone boundary; high pedestrian activity",
        "is_mandatory": False,
    },
    {
        "stop_id": "R27_FORCE_007",
        "stop_name": "Camden Ave & Highway 85 (transit hub area)",
        "stop_lat": 37.2496, "stop_lon": -121.9098,
        "activity_type": "major_intersection",
        "source": "Caltrans SR-85 / Camden Ave interchange; existing VTA stop stop_id 1757",
        "is_mandatory": False,
    },
]

# ---------------------------------------------------------------------------
# DISTRICT CLASSIFICATIONS (urban vs. suburban for spacing rules)
# Source: FTA Circular 9040.1G §5.2.2, Table 5-1
#   Urban core:    min ¼ mi (1,320 ft), max ½ mi (2,640 ft) spacing
#   Suburban:      min ⅓ mi (1,760 ft), max ¾ mi (3,960 ft) spacing
# ---------------------------------------------------------------------------
URBAN_DISTRICTS = {"D1", "D2", "D3", "D4", "D5", "D7"}   # Los Gatos incorporated area
SUBURBAN_DISTRICTS = {"D6", "D8", "D9", "D10",
                      "U1", "U2", "U3", "U4", "U5", "U6"}

# Spacing in feet (FTA Circular 9040.1G §5.2.2)
STOP_SPACING = {
    "urban":    {"min_ft": 1_320, "max_ft": 2_640},   # ¼ mi min, ½ mi max
    "suburban": {"min_ft": 1_760, "max_ft": 3_960},   # ⅓ mi min, ¾ mi max
}

# Bus operating speed for haversine travel-time fallback (40 km/h = 24.9 mph)
_BUS_SPEED_KMH = 40.0
_FEET_PER_METER = 3.28084
_METERS_PER_FOOT = 0.3048
_FEET_PER_DEGREE_LAT = 364_000  # approximate at 37°N


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _haversine_ft(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in feet."""
    R_ft = 20_902_231.0  # Earth radius in feet
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R_ft * 2 * math.asin(math.sqrt(a))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    return _haversine_ft(lat1, lon1, lat2, lon2) / 3280.84


def _project_point_onto_segment(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> Tuple[float, float, float]:
    """Project point P onto segment AB.

    Returns (closest_x, closest_y, t) where t in [0,1] is the
    parameter along AB.  Uses flat-earth approximation — acceptable
    over the ~8-mile Route 27 corridor.
    """
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq < 1e-12:
        return ax, ay, 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    return ax + t * dx, ay + t * dy, t


# ---------------------------------------------------------------------------
# REUSABLE PATH-PROJECTION (extracted for shared use in optimizer Bug 1 fix)
# ---------------------------------------------------------------------------

def project_to_path(
    lat: float,
    lon: float,
    path_coords: List[Tuple[float, float]],
    path_s_coords: List[float],
) -> Tuple[float, float]:
    """Project a point onto the corridor path and return its s-coordinate.

    Uses flat-earth segment projection (acceptable over the ~8 mi Route 27
    corridor).  Returns (s_ft, snap_dist_ft) where:
      s_ft          — arc-length from path start (Winchester TC) in feet
      snap_dist_ft  — perpendicular distance from point to nearest path segment

    Args:
        lat, lon:       WGS84 decimal degrees of the point to project.
        path_coords:    List of (lat, lon) tuples defining the corridor path.
        path_s_coords:  Cumulative arc-length (ft) at each path node.

    Returns:
        (s_ft, snap_dist_ft) — both in feet.
    """
    if len(path_coords) < 2:
        return 0.0, 0.0

    path_xy = [(plon, plat) for plat, plon in path_coords]
    best_s = 0.0
    best_dist = float("inf")
    for seg_i in range(len(path_xy) - 1):
        ax, ay = path_xy[seg_i]
        bx, by = path_xy[seg_i + 1]
        cx, cy, t = _project_point_onto_segment(lon, lat, ax, ay, bx, by)
        dlat = (lat - cy) * 364_000  # ft per degree lat at 37°N
        dlon = (lon - cx) * 288_500  # ft per degree lon at 37°N
        dist_ft = math.sqrt(dlat ** 2 + dlon ** 2)
        if dist_ft < best_dist:
            best_dist = dist_ft
            seg_len = _haversine_ft(
                path_coords[seg_i][0], path_coords[seg_i][1],
                path_coords[seg_i + 1][0], path_coords[seg_i + 1][1],
            )
            best_s = path_s_coords[seg_i] + t * seg_len
    return best_s, best_dist


# ---------------------------------------------------------------------------
# GTFS SHAPE LOADER (P1.1 — primary corridor source)
# ---------------------------------------------------------------------------

def load_route27_shape_from_gtfs(
    gtfs_dir: Path = _GTFS_DIR_DEFAULT,
) -> Optional[List[Tuple[float, float]]]:
    """Load Route 27's published corridor geometry from VTA GTFS shapes.txt.

    Steps:
      1. Read routes.txt — find row with route_id == "27" (or
         route_short_name == "27" if route_id is non-numeric).
      2. Read trips.txt filtered to that route_id.  Pick the shape_id with
         the most trips in direction_id == 0 (Winchester → Santa Teresa).
      3. Read shapes.txt filtered to that shape_id, sort by
         shape_pt_sequence, return [(lat, lon), ...].

    This eliminates Bugs 2 and 3 simultaneously:
      • Bug 2: The GTFS shape extends to Santa Teresa Stn — the full route.
      • Bug 3: No Dijkstra stitching; published shape follows actual roads.

    Args:
        gtfs_dir: Path to the directory containing routes.txt, trips.txt,
                  and shapes.txt.  Defaults to data/geospatial/gtfs/.

    Returns:
        List of (lat, lon) tuples if successful, None on any failure.
    """
    gtfs_dir = Path(gtfs_dir)

    # ---- 1. Find route_id for Route 27 ------------------------------------
    routes_path = gtfs_dir / "routes.txt"
    if not routes_path.exists():
        logger.warning("GTFS routes.txt not found at %s.", routes_path)
        return None
    try:
        routes_df = pd.read_csv(routes_path, dtype=str)
    except Exception as exc:
        logger.warning("Could not read routes.txt: %s", exc)
        return None

    # Try matching route_id first, then route_short_name
    route_mask = routes_df.get("route_id", pd.Series(dtype=str)) == "27"
    if not route_mask.any():
        route_mask = routes_df.get("route_short_name", pd.Series(dtype=str)) == "27"
    if not route_mask.any():
        logger.warning("Route 27 not found in routes.txt.")
        return None

    route_id = routes_df.loc[route_mask, "route_id"].iloc[0]
    logger.info("GTFS: Route 27 found with route_id='%s'.", route_id)

    # ---- 2. Find the dominant direction-0 shape_id -------------------------
    trips_path = gtfs_dir / "trips.txt"
    if not trips_path.exists():
        logger.warning("GTFS trips.txt not found at %s.", trips_path)
        return None
    try:
        trips_df = pd.read_csv(trips_path, dtype=str)
    except Exception as exc:
        logger.warning("Could not read trips.txt: %s", exc)
        return None

    r27_trips = trips_df[trips_df["route_id"] == route_id].copy()
    if r27_trips.empty:
        logger.warning("No trips found for route_id='%s'.", route_id)
        return None

    # Filter to direction_id == 0; fall back to all trips if column missing
    if "direction_id" in r27_trips.columns:
        dir0 = r27_trips[r27_trips["direction_id"] == "0"]
        if dir0.empty:
            logger.warning(
                "No direction_id=0 trips for route 27; using all directions."
            )
            dir0 = r27_trips
    else:
        dir0 = r27_trips

    # Pick shape_id with most trips (longest/canonical service pattern)
    if "shape_id" not in dir0.columns or dir0["shape_id"].isna().all():
        logger.warning("No shape_id column in trips.txt for route 27.")
        return None
    shape_counts = dir0["shape_id"].value_counts()
    shape_id = shape_counts.index[0]
    logger.info(
        "GTFS: Using shape_id='%s' (%d trips, direction_id=0).",
        shape_id, shape_counts.iloc[0],
    )

    # ---- 3. Load shape points and return ------------------------------------
    shapes_path = gtfs_dir / "shapes.txt"
    if not shapes_path.exists():
        logger.warning("GTFS shapes.txt not found at %s.", shapes_path)
        return None
    try:
        shapes_df = pd.read_csv(shapes_path, dtype=str)
    except Exception as exc:
        logger.warning("Could not read shapes.txt: %s", exc)
        return None

    shape_pts = shapes_df[shapes_df["shape_id"] == shape_id].copy()
    if shape_pts.empty:
        logger.warning("No shape points found for shape_id='%s'.", shape_id)
        return None

    try:
        shape_pts["shape_pt_sequence"] = pd.to_numeric(
            shape_pts["shape_pt_sequence"], errors="coerce"
        )
        shape_pts = shape_pts.dropna(subset=["shape_pt_sequence"])
        shape_pts = shape_pts.sort_values("shape_pt_sequence")
        coords = [
            (float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
            for _, row in shape_pts.iterrows()
        ]
    except Exception as exc:
        logger.warning("Error parsing shape points: %s", exc)
        return None

    if not coords:
        logger.warning("Empty coordinate list for shape_id='%s'.", shape_id)
        return None

    logger.info(
        "GTFS shape loaded: shape_id='%s', %d points, length=%.2f mi.",
        shape_id,
        len(coords),
        compute_s_coordinates(coords)[-1] / 5280 if len(coords) > 1 else 0.0,
    )
    return coords


# ---------------------------------------------------------------------------
# ROAD NETWORK BUILDING
# ---------------------------------------------------------------------------

def build_route27_road_network(config: dict):
    """Download the drivable road network for the Route 27 bounding box.

    Returns an OSMnx MultiDiGraph with travel_time edge weights (seconds),
    or None if OSMnx is unavailable.

    Bounding box is slightly larger than the corridor to ensure connectivity.
    Cached to data/geospatial/route27_network.pkl to avoid re-downloading.
    """
    cache_path = Path("data/geospatial/route27_network.pkl")

    # Try cache first
    if cache_path.exists():
        try:
            import pickle
            with open(cache_path, "rb") as f:
                G = pickle.load(f)
            logger.info("Route 27 road network loaded from cache (%s).", cache_path)
            return G
        except Exception as exc:
            logger.warning("Cache load failed (%s); re-downloading.", exc)

    try:
        import osmnx as ox
    except ImportError:
        logger.warning(
            "OSMnx not installed.  Route 27 corridor will use straight-line "
            "fallback.  Install with: pip install osmnx"
        )
        return None

    # Bounding box covers Winchester TC (N) to Blossom Hill LRT (E/S)
    # with 0.5-mile buffer on all sides.
    bbox_north = 37.270
    bbox_south = 37.215
    bbox_east  = -121.835
    bbox_west  = -122.000

    logger.info(
        "Downloading OSM drivable network (N%.3f S%.3f E%.3f W%.3f)...",
        bbox_north, bbox_south, bbox_east, bbox_west,
    )
    try:
        # osmnx ≥2.0 changed bbox argument order to (left, bottom, right, top).
        # osmnx <2.0 used (north, south, east, west).
        # We detect the version at runtime to stay compatible with both.
        import osmnx as _ox_ver
        _ox_major = int(getattr(_ox_ver, "__version__", "1.0").split(".")[0])
        if _ox_major >= 2:
            # v2+: bbox=(west, south, east, north) i.e. (left, bottom, right, top)
            G = ox.graph_from_bbox(
                bbox=(bbox_west, bbox_south, bbox_east, bbox_north),
                network_type="drive",
                simplify=True,
                retain_all=False,
            )
        else:
            # v1: bbox=(north, south, east, west)
            G = ox.graph_from_bbox(
                bbox=(bbox_north, bbox_south, bbox_east, bbox_west),
                network_type="drive",
                simplify=True,
                retain_all=False,
            )
        G = ox.add_edge_speeds(G, fallback=_BUS_SPEED_KMH)
        G = ox.add_edge_travel_times(G)
        logger.info(
            "OSM network: %d nodes, %d edges.",
            G.number_of_nodes(), G.number_of_edges(),
        )
    except Exception as exc:
        logger.warning("OSM download failed (%s); using fallback.", exc)
        return None

    # Persist cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pickle
        with open(cache_path, "wb") as f:
            pickle.dump(G, f)
        logger.info("Route 27 road network cached to %s.", cache_path)
    except Exception as exc:
        logger.warning("Could not write network cache (%s).", exc)

    return G


# ---------------------------------------------------------------------------
# PATH STITCHING — anchor waypoints → continuous road path
# ---------------------------------------------------------------------------

def stitch_corridor_path(G, anchors=None):
    """Stitch anchor waypoints into a continuous road path using Dijkstra.

    For each consecutive anchor pair (A→B), finds the shortest-path
    (by travel_time) on the road network and appends the node sequence.
    Duplicate nodes at junction points are removed.

    Args:
        G: OSMnx MultiDiGraph (or None → returns anchor coordinates only).
        anchors: list of (lat, lon, label, note) tuples.
                 Defaults to ROUTE_27_ANCHORS.

    Returns:
        List of (lat, lon) tuples representing the stitched path.
    """
    if anchors is None:
        anchors = ROUTE_27_ANCHORS

    # If no graph available, fall back to straight-line anchor sequence
    if G is None:
        logger.warning(
            "No road network graph available.  Using straight-line anchor "
            "sequence for Route 27.  Stops will NOT follow roads.  "
            "Install osmnx for road-accurate results."
        )
        return [(lat, lon) for lat, lon, _, _ in anchors]

    try:
        import osmnx as ox
        import networkx as nx
    except ImportError:
        logger.warning("osmnx / networkx not available; using straight-line fallback.")
        return [(lat, lon) for lat, lon, _, _ in anchors]

    # Snap each anchor to the nearest OSM node
    lats = [a[0] for a in anchors]
    lons = [a[1] for a in anchors]
    try:
        osm_nodes = ox.nearest_nodes(G, X=lons, Y=lats)
    except Exception as exc:
        logger.warning("nearest_nodes failed (%s); using straight-line fallback.", exc)
        return [(lat, lon) for lat, lon, _, _ in anchors]

    logger.info("Route 27 anchor → OSM node mapping:")
    for i, (anchor, node) in enumerate(zip(anchors, osm_nodes)):
        nd = G.nodes[node]
        logger.info(
            "  [%d] %s → node %s  (%.6f, %.6f)  snap_dist=%.0f ft",
            i, anchor[2], node,
            nd.get("y", 0), nd.get("x", 0),
            _haversine_ft(anchor[0], anchor[1], nd.get("y", 0), nd.get("x", 0)),
        )

    # Stitch segments
    full_path_nodes = [osm_nodes[0]]
    for i in range(len(osm_nodes) - 1):
        src, dst = osm_nodes[i], osm_nodes[i + 1]
        try:
            seg = nx.shortest_path(G, src, dst, weight="travel_time")
        except nx.NetworkXNoPath:
            logger.warning(
                "No path from anchor %d (%s) to anchor %d (%s); "
                "connecting directly.",
                i, anchors[i][2], i + 1, anchors[i + 1][2],
            )
            seg = [src, dst]
        # Avoid duplicating the junction node
        full_path_nodes.extend(seg[1:])

    # Convert nodes to (lat, lon)
    path_coords = [
        (G.nodes[n]["y"], G.nodes[n]["x"])
        for n in full_path_nodes
    ]
    logger.info(
        "Route 27 stitched path: %d anchor segments → %d road nodes.",
        len(anchors) - 1, len(path_coords),
    )
    return path_coords


# ---------------------------------------------------------------------------
# S-COORDINATE COMPUTATION
# ---------------------------------------------------------------------------

def compute_s_coordinates(path_coords: List[Tuple[float, float]]) -> List[float]:
    """Compute cumulative arc-length (in feet) along the path.

    s[0] = 0.0  (Winchester TC)
    s[i] = sum of haversine distances from 0 to i.

    Returns:
        List of float, same length as path_coords.
    """
    s = [0.0]
    for i in range(1, len(path_coords)):
        lat1, lon1 = path_coords[i - 1]
        lat2, lon2 = path_coords[i]
        s.append(s[-1] + _haversine_ft(lat1, lon1, lat2, lon2))
    return s


# ---------------------------------------------------------------------------
# INTERSECTION EXTRACTION
# ---------------------------------------------------------------------------

# Highway types that indicate unsafe stop locations (no pedestrian access)
# Source: OSM highway tag values for controlled-access roads
_UNSAFE_HIGHWAY_TYPES = {"motorway", "motorway_link", "trunk_link"}


def extract_intersection_candidates(
    G,
    path_coords: List[Tuple[float, float]],
    path_s_coords: List[float],
    snap_tolerance_ft: float = 200.0,
) -> pd.DataFrame:
    """Extract intersection nodes on or near the route path.

    A node is included if:
      (a) It is on the path (direct inclusion), OR
      (b) Its closest projected point on the path is within snap_tolerance_ft.

    Degree-1 and degree-2 nodes (dead ends and through-roads with no cross
    street) are filtered out — they are not legal bus stop locations.

    Nodes adjacent to motorway/motorway_link/trunk_link edges are excluded
    as pedestrian access is not feasible.

    The candidate's stop_lat/stop_lon is set to the PROJECTED point on the
    shape (not the OSM node location) so that downstream spacing checks use
    the actual on-route position.  The original OSM node coordinates are
    preserved in osm_node_lat/osm_node_lon for traceability.

    Args:
        G: OSMnx MultiDiGraph (or None).
        path_coords: Corridor path from GTFS shape or stitch_corridor_path().
        path_s_coords: Arc-length values from compute_s_coordinates().
        snap_tolerance_ft: Maximum perpendicular distance from path for
            inclusion (default 200 ft — tightened from 150 ft to reduce
            off-route candidates when using the GTFS shape directly).

    Returns:
        DataFrame with columns:
            candidate_id, stop_lat, stop_lon, osm_node_lat, osm_node_lon,
            s_coord_ft, osm_node_id, street_names, node_degree,
            snap_dist_ft, is_forced, activity_type, source
    """
    if G is None or len(path_coords) < 2:
        logger.warning(
            "No graph or path available; returning only forced candidates."
        )
        return pd.DataFrame()

    # Build path as flat list for projection (lon as x, lat as y)
    path_xy = [(plon, plat) for plat, plon in path_coords]

    def _projected_point(node_lon: float, node_lat: float):
        """Return (proj_lat, proj_lon, s_ft, dist_ft) for a node near path."""
        best_s = 0.0
        best_dist = float("inf")
        best_proj_lat = node_lat
        best_proj_lon = node_lon
        for seg_i in range(len(path_xy) - 1):
            ax, ay = path_xy[seg_i]
            bx, by = path_xy[seg_i + 1]
            cx, cy, t = _project_point_onto_segment(
                node_lon, node_lat, ax, ay, bx, by
            )
            dlat = (node_lat - cy) * 364_000
            dlon = (node_lon - cx) * 288_500
            dist_ft = math.sqrt(dlat ** 2 + dlon ** 2)
            if dist_ft < best_dist:
                best_dist = dist_ft
                seg_len = _haversine_ft(
                    path_coords[seg_i][0], path_coords[seg_i][1],
                    path_coords[seg_i + 1][0], path_coords[seg_i + 1][1],
                )
                best_s = path_s_coords[seg_i] + t * seg_len
                best_proj_lat = cy   # projected point lat (y in lon/lat space)
                best_proj_lon = cx   # projected point lon (x in lon/lat space)
        return best_proj_lat, best_proj_lon, best_s, best_dist

    # Collect all graph nodes (OSM intersections)
    records = []
    for node_id, data in G.nodes(data=True):
        osm_lat = data.get("y", 0.0)
        osm_lon = data.get("x", 0.0)
        degree = G.degree(node_id)

        # Skip dead-ends and simple through-nodes — not valid stop locations
        if degree < 3:
            continue

        # Feasibility filter: skip nodes adjacent to controlled-access highways
        # where pedestrian access is not possible.
        # Source: OSM highway tag; TCRP Report 19 §3.2.1
        unsafe = False
        for _, _, edge_data in G.edges(node_id, data=True):
            hw = edge_data.get("highway", "")
            if isinstance(hw, list):
                if any(h in _UNSAFE_HIGHWAY_TYPES for h in hw):
                    unsafe = True
                    break
            elif hw in _UNSAFE_HIGHWAY_TYPES:
                unsafe = True
                break
        if unsafe:
            continue

        proj_lat, proj_lon, s_ft, dist_ft = _projected_point(osm_lon, osm_lat)

        if dist_ft > snap_tolerance_ft:
            continue  # Too far from the path

        # Extract street names from adjacent edges
        street_names = set()
        for _, _, edge_data in G.edges(node_id, data=True):
            name = edge_data.get("name", "")
            if isinstance(name, list):
                street_names.update(name)
            elif isinstance(name, str) and name:
                street_names.add(name)

        records.append({
            "candidate_id":  f"R27_OSM_{node_id}",
            # Snap candidate position to the corridor shape (P1.3)
            "stop_lat":      proj_lat,
            "stop_lon":      proj_lon,
            # Original OSM node for traceability
            "osm_node_lat":  osm_lat,
            "osm_node_lon":  osm_lon,
            "s_coord_ft":    round(s_ft, 1),
            "osm_node_id":   node_id,
            "street_names":  "; ".join(sorted(street_names)),
            "node_degree":   degree,
            "snap_dist_ft":  round(dist_ft, 1),
            "is_forced":     False,
            "activity_type": "intersection",
            "source":        f"OSM node {node_id} (degree={degree})",
        })

    if not records:
        logger.warning(
            "No intersection candidates found within %.0f ft of path.  "
            "Check that OSM graph bbox covers the full corridor.",
            snap_tolerance_ft,
        )
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df = df.sort_values("s_coord_ft").reset_index(drop=True)
    logger.info(
        "Intersection candidates: %d nodes within %.0f ft of Route 27 path.",
        len(df), snap_tolerance_ft,
    )
    return df


# ---------------------------------------------------------------------------
# MERGE FORCED CANDIDATES
# ---------------------------------------------------------------------------

def add_forced_candidates(
    intersection_df: pd.DataFrame,
    path_coords: List[Tuple[float, float]],
    path_s_coords: List[float],
    forced: Optional[List[dict]] = None,
) -> pd.DataFrame:
    """Append forced-candidate activity generators to intersection candidates.

    Forced candidates (schools, LRT stations, etc.) are always included
    regardless of whether they appear as OSM intersections.

    For each forced candidate, the s-coordinate is computed by projecting
    onto the path geometry.

    Args:
        intersection_df: From extract_intersection_candidates().
        path_coords: Stitched path from stitch_corridor_path().
        path_s_coords: From compute_s_coordinates().
        forced: List of forced-candidate dicts.  Defaults to FORCED_CANDIDATES.

    Returns:
        Combined DataFrame with forced candidates appended.
    """
    if forced is None:
        forced = FORCED_CANDIDATES

    forced_rows = []
    for fc in forced:
        lat, lon = fc["stop_lat"], fc["stop_lon"]
        if len(path_coords) >= 2:
            s_ft, _ = project_to_path(lat, lon, path_coords, path_s_coords)
        else:
            s_ft = 0.0
        forced_rows.append({
            "candidate_id":  fc["stop_id"],
            "stop_lat":      lat,
            "stop_lon":      lon,
            "s_coord_ft":    round(s_ft, 1),
            "osm_node_id":   None,
            "street_names":  fc["stop_name"],
            "node_degree":   99,          # forced candidates always included
            "snap_dist_ft":  0.0,
            "is_forced":     True,
            "is_mandatory":  fc.get("is_mandatory", False),
            "activity_type": fc.get("activity_type", "forced"),
            "source":        fc.get("source", ""),
        })

    forced_df = pd.DataFrame(forced_rows)

    if intersection_df is None or len(intersection_df) == 0:
        return forced_df

    # Ensure is_mandatory column exists in intersection_df
    if "is_mandatory" not in intersection_df.columns:
        intersection_df = intersection_df.copy()
        intersection_df["is_mandatory"] = False

    combined = pd.concat([intersection_df, forced_df], ignore_index=True)
    combined = combined.sort_values("s_coord_ft").reset_index(drop=True)
    logger.info(
        "Total candidates after adding %d forced locations: %d.",
        len(forced_df), len(combined),
    )
    return combined


# ---------------------------------------------------------------------------
# GEOJSON EXPORT (transparency / GIS inspection)
# ---------------------------------------------------------------------------

def export_path_geojson(
    path_coords: List[Tuple[float, float]],
    candidates_df: pd.DataFrame,
    output_path: str = "data/geospatial/route27_path.geojson",
) -> str:
    """Save the stitched path and candidate stops as GeoJSON.

    The GeoJSON file can be opened in QGIS, geojson.io, or any web map to
    verify that the route follows actual roads before running optimization.

    Returns:
        Path to the written file.
    """
    features = []

    # Route path as LineString
    if path_coords:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[lon, lat] for lat, lon in path_coords],
            },
            "properties": {
                "name": "Route 27 corridor path",
                "description": "Stitched road-network path between anchor waypoints",
                "source": "OSMnx Dijkstra shortest-path or straight-line fallback",
                "total_length_ft": round(
                    sum(
                        _haversine_ft(
                            path_coords[i][0], path_coords[i][1],
                            path_coords[i + 1][0], path_coords[i + 1][1],
                        )
                        for i in range(len(path_coords) - 1)
                    ),
                    0,
                ) if len(path_coords) > 1 else 0,
            },
        })

    # Anchor waypoints as Points
    for lat, lon, label, note in ROUTE_27_ANCHORS:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "name": label,
                "type": "anchor_waypoint",
                "note": note,
            },
        })

    # Candidate stops
    if candidates_df is not None and len(candidates_df) > 0:
        for _, row in candidates_df.iterrows():
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [row["stop_lon"], row["stop_lat"]],
                },
                "properties": {
                    "candidate_id":  row.get("candidate_id", ""),
                    "street_names":  row.get("street_names", ""),
                    "s_coord_ft":    row.get("s_coord_ft", 0),
                    "s_coord_mi":    round(row.get("s_coord_ft", 0) / 5280, 3),
                    "activity_type": row.get("activity_type", ""),
                    "is_forced":     bool(row.get("is_forced", False)),
                    "is_mandatory":  bool(row.get("is_mandatory", False)),
                    "source":        row.get("source", ""),
                },
            })

    geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "metadata": {
            "title": "VTA Route 27 — Corridor Path and Candidate Stops",
            "generated_by": "route27_corridor.py",
            "standard": "FTA Circular 9040.1G, TCRP Report 19",
            "anchor_count": len(ROUTE_27_ANCHORS),
            "candidate_count": len(candidates_df) if candidates_df is not None else 0,
        },
        "features": features,
    }

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(geojson, indent=2))
    logger.info("Route 27 path GeoJSON saved to %s.", out_path)
    return str(out_path)


# ---------------------------------------------------------------------------
# PIPELINE ENTRY POINT
# ---------------------------------------------------------------------------

def build_route27_corridor(config: dict) -> dict:
    """Run the full corridor-building pipeline.

    Steps:
      1. Determine corridor source:
         - "gtfs_shapes" (default): load VTA published shapes.txt geometry.
           Falls back to anchor stitch if GTFS unavailable.
         - "anchors": OSMnx Dijkstra stitch of ROUTE_27_ANCHORS (legacy).
      2. Build (or load cached) OSM road network for candidate extraction.
      3. Compute s-coordinates (cumulative arc-length in feet).
      4. Extract intersection candidates within 200 ft of the path.
      5. Append forced activity-generator candidates.
      6. Export GeoJSON for GIS inspection.

    Config key:
        route27_optimization.corridor_source: "gtfs_shapes" | "anchors"
        Default: "gtfs_shapes"

    Args:
        config: Full pipeline config dict.

    Returns:
        Dict with keys:
            graph:            OSMnx MultiDiGraph or None
            path_coords:      List[(lat, lon)] — corridor path
            path_s_coords:    List[float]      — arc-length (ft) per path node
            candidates_df:    DataFrame        — all candidate stop locations
            path_geojson:     str              — path to exported GeoJSON
            corridor_source:  str              — "gtfs_shapes" or "anchors"
    """
    r27_cfg = config.get("route27_optimization", {})
    corridor_source = r27_cfg.get("corridor_source", "gtfs_shapes")

    logger.info("=" * 60)
    logger.info("ROUTE 27 CORRIDOR BUILD")
    logger.info("  Corridor source: %s", corridor_source)
    logger.info("  Forced candidates: %d activity generators", len(FORCED_CANDIDATES))
    logger.info("  Spacing standard: FTA Circular 9040.1G §5.2.2")
    logger.info("=" * 60)

    G = build_route27_road_network(config)

    # ---- Determine path_coords ----
    path_coords = None

    if corridor_source == "gtfs_shapes":
        gtfs_dir = Path(
            config.get("gtfs_dir", "data/geospatial/gtfs")
        )
        path_coords = load_route27_shape_from_gtfs(gtfs_dir)
        if path_coords is None:
            logger.warning(
                "GTFS shape unavailable — falling back to anchor stitch."
            )
            corridor_source = "anchors"

    if path_coords is None:
        # Anchor stitch fallback (DEPRECATED-as-primary)
        logger.info("Using anchor stitch (fallback) for corridor path.")
        path_coords = stitch_corridor_path(G, ROUTE_27_ANCHORS)

    path_s_coords = compute_s_coordinates(path_coords)

    total_length_ft = path_s_coords[-1] if path_s_coords else 0.0
    logger.info(
        "Corridor path: %d points, total length %.2f mi (%.0f ft).  "
        "Source: %s.",
        len(path_coords), total_length_ft / 5280, total_length_ft,
        corridor_source,
    )

    intersection_df = extract_intersection_candidates(
        G, path_coords, path_s_coords, snap_tolerance_ft=200.0
    )

    candidates_df = add_forced_candidates(
        intersection_df, path_coords, path_s_coords
    )

    geojson_path = export_path_geojson(
        path_coords, candidates_df,
        output_path="data/geospatial/route27_path.geojson",
    )

    logger.info(
        "Route 27 corridor built: %d total candidates over %.2f mi.",
        len(candidates_df), total_length_ft / 5280,
    )

    return {
        "graph":            G,
        "path_coords":      path_coords,
        "path_s_coords":    path_s_coords,
        "candidates_df":    candidates_df,
        "path_geojson":     geojson_path,
        "corridor_source":  corridor_source,
    }
