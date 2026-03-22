"""
districts.py -- District boundary management and spatial operations.

Handles loading, validation, and spatial queries for both the LGHS zone
(D1-D10) and Union SD zone (U1-U6) districts. All district definitions
are driven by config.yaml.

Uses matplotlib.path.Path for point-in-polygon tests and numpy for
vectorized spatial operations, avoiding the geopandas/shapely dependency
for Milestone A CLI usage. GeoJSON export is supported via the standard
library json module.

Standards:
    - GeoJSON per RFC 7946
    - All coordinates in WGS84 (EPSG:4326)
    - Area calculations use the Haversine-based spherical excess formula
      (accurate to ~0.3% for districts at this latitude).

References:
    - Boardman et al., "Cost-Benefit Analysis: Concepts and Practice," Ch. 6
    - TCRP Report 167 for demand index weighting
"""

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml
from matplotlib.path import Path as MplPath

logger = logging.getLogger(__name__)

# Earth radius in miles (mean, WGS84)
EARTH_RADIUS_MILES = 3958.8


def load_config(config_path: str = "config.yaml") -> dict:
    """Load the master configuration file.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If config file does not exist.
    """
    p = Path(config_path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p.resolve()}")
    with open(p, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    logger.info("Loaded config from %s", p.resolve())
    return config


@dataclass
class District:
    """A single analysis district with polygon boundary.

    Attributes:
        id: Unique identifier (e.g., "D1", "U3").
        name: Human-readable name.
        zone: "LGHS" or "UNION".
        zip_primary: Primary ZIP code.
        description: Brief description.
        road_boundaries: Road boundary description string.
        characteristics: List of characteristic tags.
        coords: List of (lat, lon) tuples defining the polygon boundary.
        _mpl_path: Cached matplotlib Path for point-in-polygon tests.
    """
    id: str
    name: str
    zone: str
    zip_primary: str = ""
    description: str = ""
    road_boundaries: str = ""
    characteristics: list = field(default_factory=list)
    coords: list = field(default_factory=list)
    _mpl_path: Optional[MplPath] = field(default=None, repr=False)

    def __post_init__(self):
        if self.coords and self._mpl_path is None:
            # matplotlib Path expects (x, y) = (lon, lat) for geographic consistency
            # but since we only need containment, (lat, lon) works if consistent
            vertices = [(lat, lon) for lat, lon in self.coords]
            self._mpl_path = MplPath(vertices)

    def contains_point(self, lat: float, lon: float) -> bool:
        """Test whether a point falls inside this district.

        Uses matplotlib.path.Path.contains_point with the ray-casting
        algorithm (winding number method).

        Args:
            lat: Latitude (WGS84).
            lon: Longitude (WGS84).

        Returns:
            True if the point is inside the polygon.

        Statistical method: Ray-casting point-in-polygon (O(n) per vertex count).
        Standard: matplotlib.path.Path, equivalent to Shapely contains().
        """
        if self._mpl_path is None:
            return False
        return bool(self._mpl_path.contains_point((lat, lon)))

    def contains_points(self, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
        """Vectorized point-in-polygon test for arrays of coordinates.

        Args:
            lats: Array of latitudes.
            lons: Array of longitudes.

        Returns:
            Boolean array, True where point is inside this district.
        """
        if self._mpl_path is None:
            return np.zeros(len(lats), dtype=bool)
        points = np.column_stack([lats, lons])
        return self._mpl_path.contains_points(points)

    def area_sq_miles(self) -> float:
        """Compute polygon area in square miles using spherical excess.

        Uses the surveyor's formula on a spherical Earth (Haversine-based).
        Accurate to ~0.3% at this latitude vs. UTM projection.

        Returns:
            Area in square miles.

        Statistical method: Spherical polygon area via excess angle formula.
        Standard: Karney (2013), "Algorithms for geodesics."
        Assumptions: Spherical Earth approximation (adequate for <100 sq mi).
        """
        if len(self.coords) < 3:
            return 0.0

        # Convert to radians
        coords_rad = [(math.radians(lat), math.radians(lon)) for lat, lon in self.coords]
        n = len(coords_rad)

        # Spherical excess formula
        total = 0.0
        for i in range(n):
            lat1, lon1 = coords_rad[i]
            lat2, lon2 = coords_rad[(i + 1) % n]
            total += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))

        area_steradians = abs(total) / 2.0
        area_sq_miles = area_steradians * EARTH_RADIUS_MILES ** 2
        return round(area_sq_miles, 4)

    def centroid(self) -> tuple[float, float]:
        """Compute the centroid (lat, lon) of the polygon.

        Uses the simple average of vertices (adequate for convex-ish polygons).

        Returns:
            (latitude, longitude) tuple.
        """
        if not self.coords:
            return (0.0, 0.0)
        lats = [c[0] for c in self.coords]
        lons = [c[1] for c in self.coords]
        return (round(np.mean(lats), 6), round(np.mean(lons), 6))

    def to_geojson_feature(self) -> dict:
        """Export district as a GeoJSON Feature.

        Returns:
            GeoJSON Feature dict per RFC 7946.
        """
        # GeoJSON uses [lon, lat] order
        ring = [[lon, lat] for lat, lon in self.coords]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])  # Close the ring

        return {
            "type": "Feature",
            "properties": {
                "id": self.id,
                "name": self.name,
                "zone": self.zone,
                "zip_primary": self.zip_primary,
                "description": self.description,
                "road_boundaries": self.road_boundaries,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [ring],
            },
        }


class DistrictManager:
    """Manages district boundaries and spatial operations for both zones.

    Loads district definitions from config.yaml, provides methods to
    assign points to districts, aggregate data by district, and validate
    consistency between district-level and zone-wide totals.

    Attributes:
        config: Parsed configuration dictionary.
        districts: Dict mapping district ID to District object.
        lghs_ids: List of LGHS district IDs.
        union_ids: List of Union district IDs.
    """

    # Fallback coordinates for all 16 districts -- confirmed via interactive
    # map review in Phase A1. Used when GeoJSON files are not yet available.
    _LGHS_COORDS = {
        "D1": [(37.236,-121.980),(37.234,-121.978),(37.230,-121.978),(37.226,-121.979),(37.222,-121.981),(37.218,-121.984),(37.216,-121.988),(37.216,-121.994),(37.218,-121.998),(37.222,-122.000),(37.226,-121.998),(37.230,-121.995),(37.232,-121.992),(37.234,-121.990),(37.235,-121.985),(37.236,-121.980)],
        "D2": [(37.250,-121.980),(37.248,-121.985),(37.246,-121.993),(37.243,-122.000),(37.238,-122.002),(37.234,-122.000),(37.232,-121.995),(37.232,-121.992),(37.234,-121.990),(37.235,-121.985),(37.236,-121.980),(37.238,-121.978),(37.242,-121.979),(37.246,-121.980),(37.250,-121.980)],
        "D3": [(37.250,-121.980),(37.251,-121.975),(37.252,-121.970),(37.253,-121.965),(37.254,-121.960),(37.255,-121.955),(37.256,-121.950),(37.260,-121.948),(37.262,-121.955),(37.262,-121.970),(37.261,-121.980),(37.258,-121.985),(37.254,-121.983),(37.250,-121.980)],
        "D4": [(37.250,-121.980),(37.251,-121.975),(37.252,-121.970),(37.253,-121.965),(37.254,-121.960),(37.250,-121.961),(37.246,-121.961),(37.245,-121.965),(37.244,-121.970),(37.243,-121.974),(37.242,-121.979),(37.246,-121.980),(37.250,-121.980)],
        "D5": [(37.242,-121.979),(37.243,-121.974),(37.244,-121.970),(37.245,-121.965),(37.246,-121.961),(37.242,-121.961),(37.238,-121.961),(37.238,-121.965),(37.237,-121.975),(37.236,-121.980),(37.238,-121.978),(37.242,-121.979)],
        "D6": [(37.254,-121.960),(37.255,-121.955),(37.256,-121.950),(37.257,-121.945),(37.258,-121.938),(37.248,-121.938),(37.240,-121.938),(37.232,-121.940),(37.225,-121.945),(37.222,-121.950),(37.222,-121.957),(37.226,-121.959),(37.230,-121.960),(37.234,-121.961),(37.238,-121.961),(37.242,-121.961),(37.246,-121.961),(37.250,-121.961),(37.254,-121.960)],
        "D7": [(37.236,-121.980),(37.237,-121.975),(37.238,-121.970),(37.238,-121.965),(37.238,-121.961),(37.234,-121.961),(37.230,-121.960),(37.226,-121.959),(37.222,-121.957),(37.216,-121.958),(37.210,-121.960),(37.206,-121.968),(37.206,-121.975),(37.210,-121.980),(37.214,-121.988),(37.218,-121.984),(37.222,-121.981),(37.226,-121.979),(37.230,-121.978),(37.234,-121.978),(37.236,-121.980)],
        "D8": [(37.243,-122.000),(37.238,-122.002),(37.234,-122.000),(37.230,-121.998),(37.226,-121.998),(37.222,-122.000),(37.218,-121.998),(37.216,-121.994),(37.216,-121.988),(37.210,-121.993),(37.206,-121.998),(37.202,-122.004),(37.198,-122.012),(37.198,-122.020),(37.204,-122.028),(37.212,-122.030),(37.220,-122.028),(37.228,-122.024),(37.236,-122.018),(37.240,-122.010),(37.243,-122.000)],
        "D9": [(37.212,-122.030),(37.204,-122.028),(37.198,-122.020),(37.198,-122.012),(37.195,-122.012),(37.185,-122.020),(37.175,-122.028),(37.165,-122.035),(37.155,-122.040),(37.140,-122.045),(37.125,-122.042),(37.112,-122.035),(37.105,-122.040),(37.100,-122.055),(37.108,-122.068),(37.130,-122.070),(37.155,-122.058),(37.180,-122.046),(37.200,-122.038),(37.210,-122.035),(37.212,-122.030)],
        "D10": [(37.100,-122.055),(37.105,-122.040),(37.112,-122.035),(37.118,-122.042),(37.108,-122.068),(37.100,-122.080),(37.090,-122.110),(37.080,-122.138),(37.075,-122.145),(37.070,-122.140),(37.060,-122.110),(37.060,-122.095),(37.078,-122.050),(37.088,-122.042),(37.100,-122.055)],
    }
    _UNION_COORDS = {
        "U1": [(37.254,-121.960),(37.255,-121.955),(37.256,-121.950),(37.257,-121.945),(37.254,-121.948),(37.250,-121.944),(37.246,-121.940),(37.242,-121.936),(37.238,-121.934),(37.234,-121.932),(37.230,-121.930),(37.226,-121.928),(37.222,-121.930),(37.218,-121.935),(37.216,-121.940),(37.216,-121.948),(37.218,-121.955),(37.222,-121.957),(37.226,-121.959),(37.230,-121.960),(37.234,-121.961),(37.238,-121.961),(37.242,-121.961),(37.246,-121.961),(37.250,-121.961),(37.254,-121.960)],
        "U2": [(37.257,-121.945),(37.258,-121.938),(37.259,-121.932),(37.260,-121.926),(37.258,-121.926),(37.254,-121.926),(37.250,-121.926),(37.246,-121.926),(37.242,-121.926),(37.238,-121.926),(37.234,-121.928),(37.230,-121.930),(37.226,-121.928),(37.230,-121.930),(37.234,-121.932),(37.238,-121.934),(37.242,-121.936),(37.246,-121.940),(37.250,-121.944),(37.254,-121.948),(37.257,-121.945)],
        "U3": [(37.260,-121.926),(37.261,-121.920),(37.262,-121.914),(37.263,-121.908),(37.264,-121.900),(37.265,-121.894),(37.260,-121.892),(37.255,-121.894),(37.250,-121.898),(37.246,-121.905),(37.246,-121.910),(37.248,-121.916),(37.250,-121.920),(37.254,-121.922),(37.258,-121.924),(37.260,-121.926)],
        "U4": [(37.246,-121.905),(37.250,-121.898),(37.255,-121.894),(37.260,-121.892),(37.258,-121.888),(37.252,-121.886),(37.248,-121.888),(37.244,-121.892),(37.240,-121.895),(37.236,-121.898),(37.238,-121.904),(37.240,-121.908),(37.242,-121.912),(37.244,-121.916),(37.246,-121.910),(37.246,-121.905)],
        "U5": [(37.236,-121.898),(37.240,-121.895),(37.244,-121.892),(37.248,-121.888),(37.252,-121.886),(37.248,-121.882),(37.242,-121.882),(37.236,-121.885),(37.230,-121.890),(37.228,-121.896),(37.230,-121.900),(37.232,-121.904),(37.234,-121.908),(37.236,-121.898)],
        "U6": [(37.230,-121.930),(37.234,-121.928),(37.238,-121.926),(37.242,-121.926),(37.238,-121.920),(37.236,-121.914),(37.234,-121.908),(37.232,-121.904),(37.230,-121.900),(37.228,-121.896),(37.224,-121.900),(37.220,-121.908),(37.218,-121.916),(37.216,-121.924),(37.218,-121.930),(37.222,-121.930),(37.226,-121.928),(37.230,-121.930)],
    }

    def __init__(self, config: dict) -> None:
        """Initialize from configuration. Loads all 16 districts.

        Args:
            config: Parsed config dictionary from config.yaml.
        """
        self.config = config
        self.districts: dict[str, District] = {}
        self.lghs_ids: list[str] = []
        self.union_ids: list[str] = []

        for dc in config.get("districts_lghs", []):
            d = self._build_district(dc, "LGHS", self._LGHS_COORDS)
            if d:
                self.districts[d.id] = d
                self.lghs_ids.append(d.id)

        for dc in config.get("districts_union", []):
            d = self._build_district(dc, "UNION", self._UNION_COORDS)
            if d:
                self.districts[d.id] = d
                self.union_ids.append(d.id)

        logger.info(
            "Loaded %d LGHS + %d Union = %d total districts",
            len(self.lghs_ids), len(self.union_ids), len(self.districts),
        )

    def _build_district(
        self, dc: dict, zone: str, fallback: dict
    ) -> Optional[District]:
        """Build a District from config, with GeoJSON or fallback coords."""
        did = dc["id"]
        geojson_path = Path(dc.get("boundary_file", ""))

        if geojson_path.exists():
            coords = self._load_geojson_coords(geojson_path)
            logger.info("Loaded GeoJSON for %s", did)
        elif did in fallback:
            coords = fallback[did]
            logger.debug("Using fallback coords for %s", did)
        else:
            logger.warning("No geometry for %s -- skipping", did)
            return None

        return District(
            id=did,
            name=dc.get("name", ""),
            zone=zone,
            zip_primary=dc.get("zip_primary", ""),
            description=dc.get("description", ""),
            road_boundaries=dc.get("road_boundaries", ""),
            characteristics=dc.get("characteristics", []),
            coords=coords,
        )

    @staticmethod
    def _load_geojson_coords(path: Path) -> list[tuple[float, float]]:
        """Load polygon coordinates from a GeoJSON file.

        Returns list of (lat, lon) tuples (converting from GeoJSON's [lon, lat]).
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Handle both Feature and FeatureCollection
        if data["type"] == "FeatureCollection":
            geom = data["features"][0]["geometry"]
        elif data["type"] == "Feature":
            geom = data["geometry"]
        else:
            geom = data
        ring = geom["coordinates"][0]
        return [(lat, lon) for lon, lat in ring]

    def point_to_district(
        self, lat: float, lon: float, zone: Optional[str] = None
    ) -> Optional[str]:
        """Assign a point to its containing district.

        Args:
            lat: Latitude (WGS84).
            lon: Longitude (WGS84).
            zone: Optional -- "LGHS" or "UNION". None searches all.

        Returns:
            District ID, comma-separated IDs if multiple, or None.
        """
        ids = self._get_zone_ids(zone)
        matches = [did for did in ids if self.districts[did].contains_point(lat, lon)]

        if not matches:
            return None
        return ",".join(matches)

    def assign_points(
        self, df: pd.DataFrame, lat_col: str = "lat", lon_col: str = "lon",
        zone: Optional[str] = None,
    ) -> pd.DataFrame:
        """Assign each row in a DataFrame to its district.

        Adds a 'district_id' column. Uses vectorized containment test
        for each district (O(districts _ points)).

        Args:
            df: DataFrame with latitude and longitude columns.
            lat_col: Name of latitude column.
            lon_col: Name of longitude column.
            zone: Optional zone filter.

        Returns:
            DataFrame with added 'district_id' column.
        """
        result = df.copy()
        result["district_id"] = None
        lats = df[lat_col].values
        lons = df[lon_col].values

        ids = self._get_zone_ids(zone)
        for did in ids:
            mask = self.districts[did].contains_points(lats, lons)
            # Only assign if not already assigned (first match wins within zone)
            unassigned = result["district_id"].isna()
            result.loc[mask & unassigned, "district_id"] = did

        n_assigned = result["district_id"].notna().sum()
        logger.info("Assigned %d / %d points to districts", n_assigned, len(df))
        return result

    def aggregate_by_district(
        self, df: pd.DataFrame, value_col: str, agg: str = "sum",
        zone: Optional[str] = None,
    ) -> pd.DataFrame:
        """Aggregate a column by district.

        Args:
            df: DataFrame with 'district_id' column (or lat/lon to assign first).
            value_col: Column to aggregate.
            agg: "sum", "mean", "count", "median".
            zone: Optional zone filter.

        Returns:
            DataFrame with district_id and aggregated value.
        """
        if "district_id" not in df.columns:
            df = self.assign_points(df, zone=zone)

        grouped = (
            df.dropna(subset=["district_id"])
            .groupby("district_id")[value_col]
            .agg(agg)
            .reset_index()
            .rename(columns={value_col: f"{value_col}_{agg}"})
        )

        # Merge in district names
        name_map = {did: d.name for did, d in self.districts.items()}
        grouped["district_name"] = grouped["district_id"].map(name_map)
        return grouped

    def summary_table(self) -> pd.DataFrame:
        """Produce a summary of all districts: id, name, zone, area, centroid.

        Returns:
            DataFrame suitable for display or CSV export.
        """
        rows = []
        for did, d in self.districts.items():
            clat, clon = d.centroid()
            rows.append({
                "id": d.id,
                "name": d.name,
                "zone": d.zone,
                "zip_primary": d.zip_primary,
                "road_boundaries": d.road_boundaries,
                "area_sq_miles": d.area_sq_miles(),
                "centroid_lat": clat,
                "centroid_lon": clon,
                "n_vertices": len(d.coords),
            })
        return pd.DataFrame(rows)

    def validate_totals(
        self, district_values: pd.DataFrame, value_col: str,
        expected_total: float, zone: str, tolerance: float = 0.01,
    ) -> bool:
        """Validate district-level values sum to zone-wide total.

        Args:
            district_values: DataFrame with 'district_id' and value column.
            value_col: Column to sum.
            expected_total: Expected zone-wide total.
            zone: Zone name for logging.
            tolerance: Max relative error (default 1%).

        Returns:
            True if within tolerance.

        Standard: Boardman et al., Ch. 2 (consistency requirements).
        """
        actual = district_values[value_col].sum()
        if expected_total == 0:
            return actual == 0
        error = abs(actual - expected_total) / abs(expected_total)
        if error <= tolerance:
            logger.info("Validated %s (zone=%s): %.2f vs %.2f (%.3f%%)", value_col, zone, actual, expected_total, error * 100)
            return True
        logger.error("MISMATCH %s (zone=%s): %.2f vs %.2f (%.3f%%)", value_col, zone, actual, expected_total, error * 100)
        return False

    def export_geojson(self, output_path: str, zone: Optional[str] = None) -> None:
        """Export districts as a GeoJSON FeatureCollection.

        Args:
            output_path: File path for output.
            zone: Optional zone filter.
        """
        ids = self._get_zone_ids(zone)
        features = [self.districts[did].to_geojson_feature() for did in ids]
        fc = {"type": "FeatureCollection", "features": features}
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(fc, f, indent=2)
        logger.info("Exported %d districts to %s", len(features), output_path)

    def _get_zone_ids(self, zone: Optional[str]) -> list[str]:
        """Get district IDs for a zone, or all if zone is None."""
        if zone == "LGHS":
            return self.lghs_ids
        elif zone == "UNION":
            return self.union_ids
        elif zone is None:
            return list(self.districts.keys())
        else:
            raise ValueError(f"Unknown zone: {zone}. Use 'LGHS' or 'UNION'.")
