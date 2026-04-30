"""
generate_dashboard.py -- Build an interactive HTML dashboard from pipeline outputs.

Reads all CSV files from outputs/tables/ and data from the district/cost
pipeline, then generates a self-contained HTML file with:
  - Leaflet map with all districts, stops, and routes
  - Chart.js visualizations for costs, demographics, equity, benchmarks
  - Full data tables
  - Route 76 restoration scenario

The dashboard uses only CDN-hosted libraries (Leaflet, Chart.js) so it
works by opening the HTML file directly in a browser -- no server needed.

Usage:
    python src/generate_dashboard.py

    Or automatically via run_analysis.py (called at the end of pipeline).

Output:
    outputs/cba_dashboard.html
"""

import json
import logging
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_pipeline_data() -> dict:
    """Load all pipeline output CSVs into a single data dict.

    Returns:
        Dict with keys: districts, demographics, costs, route_costs,
        stops, route_district, crashes, peers, npv, r76, config.
    """
    tables = PROJECT_ROOT / "outputs" / "tables"
    data_dir = PROJECT_ROOT / "data"

    result = {}

    # District profile
    p = tables / "district_profile_initial.csv"
    result["districts"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Demographics
    p = tables / "district_demographic_profile.csv"
    result["demographics"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Cost summary
    p = tables / "district_cost_summary.csv"
    result["costs"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Route operating costs
    p = tables / "route_operating_costs.csv"
    result["route_costs"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Zone costs (allocated)
    p = tables / "zone_costs.csv"
    result["zone_costs"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Allocated operating total (study-area share, not full route)
    p = tables / "district_cost_summary.csv"
    if p.exists():
        dcs = pd.read_csv(p)
        result["allocated_annual_operating"] = round(dcs["allocated_operating_cost"].sum())
        result["total_capital"] = round(dcs.get("total_capital", pd.Series([0])).sum())
    else:
        result["allocated_annual_operating"] = 0
        result["total_capital"] = 0

    # Stop-district matrix
    p = tables / "stop_district_matrix.csv"
    result["stops"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Route-district matrix
    p = tables / "route_district_matrix.csv"
    result["route_district"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Crashes
    p = tables / "crashes_by_district.csv"
    result["crashes"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Peer benchmarks
    p = tables / "peer_benchmarks.csv"
    result["peers"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # NPV
    p = tables / "npv_costs.csv"
    result["npv"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # NPV benefits
    p = tables / "npv_benefits.csv"
    result["npv_benefits"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Benefits by category
    p = tables / "annual_benefits_by_category.csv"
    result["benefits"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Census (check if synthetic)
    p = data_dir / "processed" / "census_block_groups.csv"
    if p.exists():
        census = pd.read_csv(p)
        result["census_synthetic"] = bool(census.get("is_synthetic", pd.Series([True])).any())
        result["census_count"] = len(census)
    else:
        result["census_synthetic"] = True
        result["census_count"] = 0

    # Use district-level population total (after caps/fixes), not raw census
    p_demo = tables / "district_demographic_profile.csv"
    if p_demo.exists():
        demo = pd.read_csv(p_demo)
        result["census_total_pop"] = int(demo["total_pop"].sum())
    else:
        result["census_total_pop"] = 0

    # GTFS stops (check if synthetic)
    p = data_dir / "geospatial" / "gtfs" / "stops.txt"
    result["gtfs_real"] = p.exists()
    p2 = data_dir / "geospatial" / "gtfs" / "stops_synthetic.csv"
    result["gtfs_synthetic"] = p2.exists() and not result["gtfs_real"]

    # Config
    cfg_path = PROJECT_ROOT / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            result["config"] = yaml.safe_load(f)
    else:
        result["config"] = {}

    # Phase B: optimised routes (stop-level, with headways per row)
    p = tables / "optimised_routes.csv"
    result["optimised_routes"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Phase B: selected stops
    p = tables / "selected_stops.csv"
    result["selected_stops"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Phase B: O-D demand matrix
    p = tables / "od_demand_matrix.csv"
    result["od_matrix"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Phase B: district demand totals
    p = tables / "district_demand_totals.csv"
    result["district_demand"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Phase B: school demand windows
    p = tables / "school_demand_windows.csv"
    result["school_demand"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Phase B: school coverage verification
    p = tables / "school_coverage_verification.csv"
    result["school_coverage"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Phase B: GTFS feed summary (exporter writes feed_summary.csv)
    p = PROJECT_ROOT / "outputs" / "gtfs_optimised" / "feed_summary.csv"
    result["gtfs_summary"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Transit demand index (for fallback boardings estimate when demand_score=0)
    p = tables / "transit_demand_index.csv"
    result["tdi"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Unmet need (equity priority flags)
    p = tables / "unmet_need.csv"
    result["unmet_need"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Phase B: road-following polylines for optimised routes
    p = tables / "route_shapes.csv"
    result["route_shapes"] = pd.read_csv(p).to_dict("records") if p.exists() else []

    # Route 27 stop suggestions (new stop recommendations)
    p = tables / "route27_stop_suggestions.csv"
    if p.exists():
        r27_df = pd.read_csv(p)
        # Replace NaN with None for clean JSON serialization
        r27_df = r27_df.where(pd.notnull(r27_df), None)
        result["route27_suggestions"] = r27_df.to_dict("records")
    else:
        result["route27_suggestions"] = []

    # Route 27 path GeoJSON (road-following line for the map)
    import json as _json
    p = PROJECT_ROOT / "data" / "geospatial" / "route27_path.geojson"
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                result["route27_geojson"] = _json.load(f)
        except Exception:
            result["route27_geojson"] = None
    else:
        result["route27_geojson"] = None

    # Step 7: Load original (pre-optimization) Route 27 stops and shape from GTFS
    # for the "Original Route 27" layer in the map.
    result["gtfs_route27_stops"] = []
    result["gtfs_route27_shape"] = []
    gtfs_dir = PROJECT_ROOT / "data" / "geospatial" / "gtfs"
    try:
        import pandas as _pd
        trips_p = gtfs_dir / "trips.txt"
        stop_times_p = gtfs_dir / "stop_times.txt"
        stops_p = gtfs_dir / "stops.txt"
        if trips_p.exists() and stop_times_p.exists() and stops_p.exists():
            _trips = _pd.read_csv(trips_p, dtype=str)
            _stop_times = _pd.read_csv(stop_times_p, dtype=str)
            _stops = _pd.read_csv(stops_p, dtype=str)
            # Find route_id matching "27"
            _r27_trips = _trips[_trips["route_id"] == "27"]
            if _r27_trips.empty:
                # fallback: look for route containing "27" in route long name via routes.txt
                routes_p = gtfs_dir / "routes.txt"
                if routes_p.exists():
                    _routes = _pd.read_csv(routes_p, dtype=str)
                    _r27_route = _routes[
                        _routes.get("route_long_name", _pd.Series(dtype=str)).str.contains("27", na=False)
                        | _routes["route_id"].astype(str).str.contains("27", na=False)
                    ]
                    if not _r27_route.empty:
                        _r27_route_id = _r27_route.iloc[0]["route_id"]
                        _r27_trips = _trips[_trips["route_id"] == _r27_route_id]
            if not _r27_trips.empty:
                # P1.10: VTA Route 27 has multiple service patterns (full corridor +
                # short-turns).  Picking _dir0.iloc[0] silently selected a short-turn
                # in some feeds, erasing Los Gatos from the upper-map "Original
                # Route 27" layer.  Pick the LONGEST trip in dir-0 instead so we
                # always render the full Winchester ↔ Santa Teresa pattern.
                _dir0 = _r27_trips[_r27_trips.get("direction_id", _pd.Series(dtype=str)) == "0"] if "direction_id" in _r27_trips.columns else _r27_trips
                _candidate_trips = _dir0 if not _dir0.empty else _r27_trips
                _trip_lengths = (
                    _stop_times[_stop_times["trip_id"].isin(_candidate_trips["trip_id"])]
                    .groupby("trip_id").size()
                )
                if len(_trip_lengths) == 0:
                    _rep_trip = _candidate_trips.iloc[0]["trip_id"]
                else:
                    _rep_trip = _trip_lengths.idxmax()
                _trip_sts = _stop_times[_stop_times["trip_id"] == _rep_trip].copy()
                _trip_sts["stop_sequence"] = _pd.to_numeric(_trip_sts["stop_sequence"], errors="coerce")
                _trip_sts = _trip_sts.sort_values("stop_sequence")
                _slookup = _stops.set_index("stop_id")
                _r27_stops_list = []
                for _, _sr in _trip_sts.iterrows():
                    _sid = _sr["stop_id"]
                    if _sid not in _slookup.index:
                        continue
                    _srow = _slookup.loc[_sid]
                    _r27_stops_list.append({
                        "stop_id": _sid,
                        "stop_name": str(_srow.get("stop_name", _sid)),
                        "stop_lat": float(_srow.get("stop_lat", 0)),
                        "stop_lon": float(_srow.get("stop_lon", 0)),
                    })
                result["gtfs_route27_stops"] = _r27_stops_list

                # P1.10: Build the "Original Route 27" polyline from shapes.txt
                # (the canonical road-following geometry) instead of a stop chain
                # that flattens curves through Los Gatos.  Falls back to the
                # stop-chain only when shapes.txt is missing the trip's shape_id.
                _r27_shape_pts: list[dict] = []
                _shapes_p = gtfs_dir / "shapes.txt"
                _rep_shape_id = (
                    _candidate_trips.loc[_candidate_trips["trip_id"] == _rep_trip, "shape_id"].iloc[0]
                    if "shape_id" in _candidate_trips.columns
                    and (_candidate_trips["trip_id"] == _rep_trip).any()
                    else None
                )
                if _shapes_p.exists() and _rep_shape_id:
                    try:
                        _shapes = _pd.read_csv(_shapes_p, dtype=str)
                        _trip_shape = _shapes[_shapes["shape_id"] == _rep_shape_id].copy()
                        _trip_shape["shape_pt_sequence"] = _pd.to_numeric(
                            _trip_shape["shape_pt_sequence"], errors="coerce"
                        )
                        _trip_shape = _trip_shape.sort_values("shape_pt_sequence")
                        for _, _pt in _trip_shape.iterrows():
                            try:
                                _r27_shape_pts.append({
                                    "lat": float(_pt["shape_pt_lat"]),
                                    "lon": float(_pt["shape_pt_lon"]),
                                })
                            except (TypeError, ValueError):
                                continue
                    except Exception:
                        _r27_shape_pts = []
                if not _r27_shape_pts:
                    # Fallback: connect stops in sequence (legacy behaviour).
                    _r27_shape_pts = [
                        {"lat": s["stop_lat"], "lon": s["stop_lon"]}
                        for s in _r27_stops_list
                    ]
                result["gtfs_route27_shape"] = _r27_shape_pts
    except Exception as _gtfs_exc:
        pass  # silently degrade; layer will be empty

    return result


def merge_district_data(data: dict) -> list[dict]:
    """Merge district profile, demographics, costs, and crashes into one list.

    Returns:
        List of dicts, one per district, with all available metrics.
    """
    # Start with district profile
    districts = {d["id"]: dict(d) for d in data.get("districts", [])}

    # Merge demographics
    for d in data.get("demographics", []):
        did = d.get("district_id", "")
        if did in districts:
            districts[did].update({
                "total_pop": d.get("total_pop", 0),
                "pop_density": d.get("pop_density_per_sq_mi", 0),
                "mean_income": d.get("mean_income", 0),
                "zero_veh_rate": d.get("zero_veh_rate", 0),
                "transit_share": d.get("transit_share", 0),
            })

    # Merge costs
    for d in data.get("costs", []):
        did = d.get("district_id", "")
        if did in districts:
            districts[did].update({
                "op_cost": d.get("allocated_operating_cost", 0),
                "cap_cost": d.get("total_capital", 0),
                "total_annual_cost": d.get("total_annual_cost", 0),
                "n_stops": d.get("n_stops", 0),
                "n_routes": d.get("n_routes", 0),
            })

    # Merge crashes
    for d in data.get("crashes", []):
        did = d.get("district_id", "")
        if did in districts:
            districts[did].update({
                "crashes": d.get("total_crashes", 0),
                "fatal_crashes": d.get("fatal", 0),
            })

    # Fill missing values
    for did, d in districts.items():
        for key in ["total_pop", "pop_density", "mean_income", "zero_veh_rate",
                     "transit_share", "op_cost", "cap_cost", "total_annual_cost",
                     "n_stops", "n_routes", "crashes", "fatal_crashes"]:
            if key not in d:
                d[key] = 0

    return list(districts.values())


def get_district_polygons() -> dict:
    """Load district polygon coordinates from the districts module.

    Returns:
        Dict mapping district_id -> list of [lat, lon] coordinate pairs.
    """
    import sys
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.districts import DistrictManager, load_config

    config = load_config(str(PROJECT_ROOT / "config.yaml"))
    dm = DistrictManager(config)

    polygons = {}
    for did, d in dm.districts.items():
        polygons[did] = d.coords  # Already [(lat, lon), ...]
    return polygons


def get_stop_locations(data: dict) -> list[dict]:
    """Extract stop locations with lat/lon for map plotting.

    Returns:
        List of dicts with stop_name, lat, lon, route_ids, district_id.
    """
    stops = []
    for s in data.get("stops", []):
        # Try to get coordinates from the GTFS or synthetic data
        stop_name = s.get("stop_name", "Unknown")
        route = s.get("route_ids", "")
        district = s.get("district_id", "")
        stops.append({
            "name": stop_name,
            "route": route,
            "district": district,
        })
    return stops


# ---------------------------------------------------------------------------
# Glossary of domain terms displayed in the dashboard.
# Keys are matched (case-sensitive for acronyms, case-insensitive for phrases)
# against the rendered HTML text; first occurrence per text node is wrapped
# in a .jargon span with a hover tooltip.
# ---------------------------------------------------------------------------
# NOTE: Values are TRUSTED HTML (not auto-escaped) so cross-reference anchors
# (<a href="#gl-...">) and external source links (<a href="https://...">)
# render directly.  Pre-encode ampersands inside URL query strings as &amp;
# if you ever add one.
_LINK_BCA = '<a href="https://www.transportation.gov/sites/dot.gov/files/2024-11/Benefit%20Cost%20Analysis%20Guidance%202025%20Update%20(Final).pdf" target="_blank" style="color:var(--ac)">USDOT BCA Guidance 2024</a>'
_LINK_OMB_A94 = '<a href="https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf" target="_blank" style="color:var(--ac)">OMB Circular A-94</a>'
_LINK_FTA_CIG = '<a href="https://www.transit.dot.gov/CIG" target="_blank" style="color:var(--ac)">FTA CIG Policy Guidance</a>'
_LINK_TCRP_95 = '<a href="https://www.trb.org/publications/tcrp/tcrp_rpt_95c9.pdf" target="_blank" style="color:var(--ac)">TCRP Report 95</a>'
_LINK_TCRP_78 = '<a href="https://www.trb.org/publications/tcrp/tcrp78/guidebook/tcrp78.pdf" target="_blank" style="color:var(--ac)">Report 78</a>'

GLOSSARY: dict[str, str] = {
    "BCR": (
        "Benefit-Cost Ratio. Present value of total benefits divided by present "
        "value of total costs. BCR &gt; 1.0 means the project's economic value "
        "exceeds its cost and is worth funding. See also: "
        '<a href="#gl-npv">NPV</a>, <a href="#gl-discount-rate">discount rate</a>. '
        f'Methodology: {_LINK_BCA}.'
    ),
    "NPV": (
        "Net Present Value. The sum of all future cash flows (benefits minus "
        "costs) discounted to today's dollars. Positive NPV = net economic gain "
        "over the analysis period. See also: "
        '<a href="#gl-bcr">BCR</a>, <a href="#gl-discount-rate">discount rate</a>, '
        '<a href="#gl-pv">PV</a>.'
    ),
    "PV": (
        "Present Value. The current worth of a future sum of money, discounted "
        "at a chosen rate to reflect the time value of money. See also: "
        '<a href="#gl-npv">NPV</a>, <a href="#gl-discount-rate">discount rate</a>.'
    ),
    "VOT": (
        "Value of Time. The dollar value assigned to one hour of travel time, "
        f"used to monetize travel time savings. {_LINK_BCA}: $17.80/hr "
        "personal trips, $31.90/hr employer business trips. See also: "
        '<a href="#gl-vsl">VSL</a>.'
    ),
    "VSL": (
        "Value of a Statistical Life. The dollar amount used in regulatory "
        "analysis to represent the economic cost of a fatality. USDOT 2024 "
        "sets VSL at $12.8 million per fatality. Source: "
        '<a href="https://www.transportation.gov/resources/value-of-a-statistical-life-guidance" target="_blank" style="color:var(--ac)">USDOT VSL Guidance</a>. '
        'See also: <a href="#gl-kabco">KABCO</a>.'
    ),
    "VMT": (
        "Vehicle Miles Traveled. The total miles driven by motor vehicles in "
        "a given area and period. Reducing VMT cuts emissions, crashes, and "
        "congestion. Emission factors from "
        '<a href="#gl-moves31">MOVES3.1</a>; crash costs from '
        '<a href="#gl-kabco">KABCO</a> scale.'
    ),
    "SCC": (
        "Social Cost of Carbon. The estimated economic damage caused by "
        "emitting one metric ton of CO₂. "
        '<a href="https://www.epa.gov/system/files/documents/2023-12/epa_scghg_2023_report_final.pdf" target="_blank" style="color:var(--ac)">EPA SC-GHG Report (Dec 2023)</a> '
        "sets SCC at $120/ton (3% discount rate), up from the prior $56/ton IWG "
        'value. See also: <a href="#gl-moves31">MOVES3.1</a>, '
        '<a href="#gl-vmt">VMT</a>.'
    ),
    "KABCO": (
        "Crash severity scale: K = Fatal, A = Severe Injury, B = Moderate "
        "Injury, C = Minor Injury, O = Property Damage Only. Used by FHWA "
        'and <a href="#gl-switrs">SWITRS</a> to weight crash costs in safety '
        'benefit calculations. Source: <a href="https://safety.fhwa.dot.gov/hsip/docs/fhwasa17071.pdf" target="_blank" style="color:var(--ac)">FHWA Crash Costs for Highway Safety Analysis</a>.'
    ),
    "NTD": (
        'National Transit Database. <a href="#gl-fta">FTA</a>\'s annual data '
        "collection from U.S. transit agencies covering ridership, costs, "
        "and service statistics. Used here for peer benchmarking of VTA "
        'operating costs. <a href="https://www.transit.dot.gov/ntd" target="_blank" style="color:var(--ac)">transit.dot.gov/ntd</a>'
    ),
    "FTA": (
        "Federal Transit Administration. The U.S. DOT agency that funds, "
        "regulates, and provides technical guidance for public transit. "
        'Administers the <a href="#gl-cig">Capital Investment Grant (CIG)</a> '
        'program. <a href="https://www.transit.dot.gov" target="_blank" style="color:var(--ac)">transit.dot.gov</a>'
    ),
    "USDOT": (
        "U.S. Department of Transportation. Sets federal BCA guidance values "
        'including <a href="#gl-vot">VOT</a>, <a href="#gl-vsl">VSL</a>, and '
        '<a href="#gl-discount-rate">discount rate</a> recommendations used '
        f"throughout this analysis. BCA Guidance: {_LINK_BCA}."
    ),
    "OMB Circular A-94": (
        "Office of Management and Budget Circular A-94. The federal guidelines "
        "for benefit-cost analysis of government programs. Prescribes the "
        '<a href="#gl-discount-rate">discount rates</a> (2%, 3.5%, 7%) used '
        f"in this dashboard. {_LINK_OMB_A94}"
    ),
    "TCRP": (
        "Transit Cooperative Research Program. A federally funded research "
        "program that produces peer-reviewed transit planning guidance. "
        f'{_LINK_TCRP_95} underpins the <a href="#gl-induced-demand">induced-demand</a> '
        f'category; {_LINK_TCRP_78} underpins '
        '<a href="#gl-option-value">option value</a> estimates. All reports: '
        '<a href="https://www.trb.org/TCRP/TCRP.aspx" target="_blank" style="color:var(--ac)">trb.org/TCRP</a>.'
    ),
    "MOVES3.1": (
        "EPA's Motor Vehicle Emission Simulator (version 3.1). Used to "
        "estimate per-mile emission factors for CO₂, NOx, and PM2.5 "
        'from avoided automobile trips. <a href="https://www.epa.gov/moves/latest-version-motor-vehicle-emission-simulator-moves" target="_blank" style="color:var(--ac)">EPA MOVES page</a>. '
        'See also: <a href="#gl-scc">SCC</a>, <a href="#gl-vmt">VMT</a>, '
        '<a href="#gl-benmap-ce">BenMAP-CE</a>.'
    ),
    "BenMAP-CE": (
        "EPA's Environmental Benefits Mapping and Analysis Program — Community "
        "Edition. Translates changes in air pollutant concentrations to "
        "health outcomes and economic damages. Used for criteria pollutant "
        'benefits in Category 4. <a href="https://www.epa.gov/benmap" target="_blank" style="color:var(--ac)">EPA BenMAP page</a>. '
        'See also: <a href="#gl-moves31">MOVES3.1</a>, <a href="#gl-scc">SCC</a>.'
    ),
    "WHO HEAT": (
        "World Health Organization Health Economic Assessment Tool. Quantifies "
        "the mortality-reduction benefit of walking and cycling. Version 5.2 "
        'default: 12 walk-minutes per transit trip, valued at $0.16/min. '
        '<a href="https://www.who.int/tools/heat-for-walking-and-cycling" target="_blank" style="color:var(--ac)">WHO HEAT tool</a>; '
        '<a href="https://www.who.int/publications/m/item/health-economic-assessment-tool-(-heat)--for-walking-and-for-cycling.-methods-and-user-guide-on-physical-activity--air-pollution--road-fatalities-and-carbon-impact-assessments--2024-update" target="_blank" style="color:var(--ac)">2024 methods guide</a>.'
    ),
    "CIG": (
        'Capital Investment Grant. <a href="#gl-fta">FTA</a>\'s primary '
        "discretionary funding program for major transit projects (New Starts, "
        "Small Starts, Core Capacity). Applications require a "
        '<a href="#gl-cei">Cost Effectiveness Index</a> below FTA thresholds. '
        '<a href="https://www.transit.dot.gov/CIG" target="_blank" style="color:var(--ac)">transit.dot.gov/CIG</a>'
    ),
    "CEI": (
        "Cost Effectiveness Index (also FTA Cost Effectiveness Index). Annual "
        "net project cost divided by annualized user benefits in hours "
        '(<a href="#gl-tsub">TSUB</a>-hours). <a href="#gl-cig">FTA CIG</a> '
        "threshold: &lt; $2/TSUB-hr = Medium-High; &lt; $4 = Medium. Source: "
        f"{_LINK_FTA_CIG}."
    ),
    "TSUB": (
        'Transportation System User Benefit. The <a href="#gl-fta">FTA</a> '
        'metric for <a href="#gl-cig">CIG</a> cost effectiveness. Measures '
        "time saved by diverted auto users plus transit travel time for "
        'transit-dependent users, in hours per year. See <a href="#gl-cei">CEI</a>. '
        f"Source: {_LINK_FTA_CIG}."
    ),
    "ACS": (
        "American Community Survey. The U.S. Census Bureau's annual survey "
        "providing estimates of demographics, income, commute mode, and "
        "vehicle availability — the primary source of equity and demand data "
        'in this analysis. <a href="https://www.census.gov/programs-surveys/acs" target="_blank" style="color:var(--ac)">census.gov/acs</a>'
    ),
    "GTFS": (
        "General Transit Feed Specification. The open standard format for "
        "transit schedules (stops.txt, trips.txt, stop_times.txt, etc.). "
        "This analysis ingests VTA's GTFS feed for stop locations, routes, "
        'and headways. Spec: <a href="https://gtfs.org/schedule/reference/" target="_blank" style="color:var(--ac)">gtfs.org</a>; '
        'VTA feed: <a href="https://www.vta.org/go/developers" target="_blank" style="color:var(--ac)">vta.org/developers</a>.'
    ),
    "SWITRS": (
        "Statewide Integrated Traffic Records System. California's crash "
        "database maintained by CHP. Used to derive Santa Clara County crash "
        'rates (~120 crashes per 100M <a href="#gl-vmt">VMT</a>) for Category 3 '
        'safety benefits. Data access: <a href="https://tims.berkeley.edu/help/SWITRS.php" target="_blank" style="color:var(--ac)">UC Berkeley TIMS</a> '
        'or <a href="https://iswitrs.chp.ca.gov/" target="_blank" style="color:var(--ac)">CHP iSWITRS</a>.'
    ),
    "induced demand": (
        "Trips that only happen because transit exists — riders who would "
        "not otherwise have made the trip by any mode. Estimated at 20% of "
        f"boardings ({_LINK_TCRP_95}, Ch. 9). Valued via "
        '<a href="#gl-consumer-surplus">consumer surplus</a> (50% of '
        "equivalent auto trip value) rather than auto diversion savings."
    ),
    "option value": (
        "The economic value that non-riders place on the mere availability "
        "of transit — insurance against car breakdown, gas price spikes, "
        "or loss of driving ability. Estimated at $20–$40 per capita per year "
        f'from stated-preference surveys ({_LINK_TCRP_78}). See also: '
        '<a href="#gl-tcrp">TCRP</a>.'
    ),
    "consumer surplus": (
        "The economic benefit a consumer receives beyond what they pay. In "
        'transit CBA, <a href="#gl-induced-demand">induced</a> riders gain a '
        "'triangle' of consumer surplus equal to roughly 50% of the auto trip "
        "cost they would have faced — because their willingness to pay is "
        'lower than that cost. Textbook treatment: <a href="https://www.cambridge.org/us/universitypress/subjects/economics/public-economics-and-public-policy/cost-benefit-analysis-concepts-and-practice-5th-edition" target="_blank" style="color:var(--ac)">Boardman et al., Ch. 3</a>.'
    ),
    "diversion rate": (
        "The share of transit riders who previously made the same trip by "
        "private automobile. Auto-diversion riders generate vehicle operating "
        "cost savings, time savings, crash reduction, and emission reduction "
        'benefits. Complementary share = <a href="#gl-induced-demand">induced demand</a> riders.'
    ),
    "walk-shed": (
        "The area reachable on foot within a given time (typically 5–10 min / "
        "0.25–0.5 mile) from a transit stop. A larger walk-shed means the stop "
        "serves more potential riders without transfers or feeder services. "
        'Buffer standards from <a href="https://www.transit.dot.gov/sites/fta.dot.gov/files/2024-09/C9040.1H-Circular-11-01-2024.pdf" target="_blank" style="color:var(--ac)">FTA Circular 9040.1H</a>.'
    ),
    "discount rate": (
        "The annual rate used to convert future dollars into present-value "
        "dollars. Higher rates reduce the weight given to distant future "
        f"benefits. {_LINK_OMB_A94} prescribes 2%, 3.5%, and 7% for "
        'infrastructure BCA sensitivity testing. See also: '
        '<a href="#gl-npv">NPV</a>, <a href="#gl-pv">PV</a>.'
    ),
    "FTA Circular 9040.1G": (
        '<a href="#gl-fta">FTA</a>\'s Formula Grants for Rural Areas program '
        "guidance circular. Sets minimum stop spacing, accessibility, and "
        "service standards for federally funded rural and suburban transit. "
        "Used as the baseline stop-spacing criterion in Phase B route "
        'optimization. Superseded by <a href="https://www.transit.dot.gov/sites/fta.dot.gov/files/2024-09/C9040.1H-Circular-11-01-2024.pdf" target="_blank" style="color:var(--ac)">Circular 9040.1H (Nov 2024)</a>.'
    ),
}


def generate_dashboard_html(data: dict, merged: list, polygons: dict) -> str:
    """Generate the full dashboard HTML with embedded data.

    All data is serialized to JSON and embedded in a <script> tag so
    the dashboard is completely self-contained (no server needed).
    """
    # Build stop coordinates from pipeline's synthetic CSV
    stop_coords = []
    csv_path = PROJECT_ROOT / "data" / "geospatial" / "gtfs" / "stops_synthetic.csv"
    gtfs_path = PROJECT_ROOT / "data" / "geospatial" / "gtfs" / "stops.txt"

    if gtfs_path.exists():
        stops_df = pd.read_csv(gtfs_path)
        source = "GTFS"
    elif csv_path.exists():
        stops_df = pd.read_csv(csv_path)
        source = "Synthetic"
    else:
        stops_df = pd.DataFrame()
        source = "None"

    if not stops_df.empty:
        lat_col = "stop_lat" if "stop_lat" in stops_df.columns else "lat"
        lon_col = "stop_lon" if "stop_lon" in stops_df.columns else "lon"
        for _, row in stops_df.iterrows():
            stop_coords.append({
                "name": row.get("stop_name", ""),
                "lat": float(row.get(lat_col, 0)),
                "lon": float(row.get(lon_col, 0)),
                "route": str(row.get("route_ids", row.get("route_id", ""))),
            })

    # District colors
    colors = {
        "D1":"#ff6b6b","D2":"#ffa94d","D3":"#ffd43b","D4":"#69db7c",
        "D5":"#6c9bff","D6":"#cc5de8","D7":"#ff7eb3","D8":"#70a1ff",
        "D9":"#20c997","D10":"#a29bfe",
        "U1":"#e74c3c","U2":"#e08283","U3":"#c0392b","U4":"#f39c12",
        "U5":"#d35400","U6":"#a04000",
    }

    # Determine data freshness
    data_status = "REAL" if not data.get("census_synthetic", True) else "SYNTHETIC"
    gtfs_status = "REAL" if data.get("gtfs_real", False) else "SYNTHETIC"

    # Prepare JSON payloads
    js_districts = json.dumps([{
        "id": d["id"], "name": d.get("name", ""), "zone": d.get("zone", ""),
        "color": colors.get(d["id"], "#888"),
        "pop": d.get("total_pop", 0), "density": d.get("pop_density", 0),
        "income": round(d.get("mean_income", 0)),
        "zvr": round(d.get("zero_veh_rate", 0), 4) if d.get("total_pop", 0) > 0 else None,
        "ts": round(d.get("transit_share", 0), 4) if d.get("total_pop", 0) > 0 else None,
        "stops": d.get("n_stops", 0),
        "opCost": round(d.get("op_cost", 0)),
        "totalAnn": round(d.get("total_annual_cost", 0)),
        "crashes": d.get("crashes", 0),
        "coords": polygons.get(d["id"], []),
    } for d in merged])

    js_stops = json.dumps(stop_coords)
    js_routes = json.dumps(data.get("route_costs", []))
    js_peers = json.dumps(data.get("peers", []))
    js_npv = json.dumps(data.get("npv", []))
    js_npv_benefits = json.dumps(data.get("npv_benefits", []))
    js_benefits = json.dumps(data.get("benefits", []))
    js_allocated_op = data.get("allocated_annual_operating", 0)
    js_total_capital = data.get("total_capital", 0)

    # Phase B optimization data
    js_opt_routes = json.dumps(data.get("optimised_routes", []))
    js_selected_stops = json.dumps(data.get("selected_stops", []))
    js_district_demand = json.dumps(data.get("district_demand", []))
    js_school_demand = json.dumps(data.get("school_demand", []))
    js_school_coverage = json.dumps(data.get("school_coverage", []))
    js_gtfs_summary = json.dumps(data.get("gtfs_summary", []))
    opt_run = len(data.get("optimised_routes", [])) > 0

    # Phase B: road-following polylines (NaN → "" for safe JSON)
    import math as _math
    def _clean_shape(r):
        return {k: ("" if isinstance(v, float) and not _math.isfinite(v) else v) for k, v in r.items()}
    js_route_shapes = json.dumps([_clean_shape(r) for r in data.get("route_shapes", [])])

    # Route 27 stop suggestions
    r27_suggestions = data.get("route27_suggestions", [])
    js_r27_suggestions = json.dumps(r27_suggestions)
    js_r27_geojson = json.dumps(data.get("route27_geojson"))
    r27_run = len(r27_suggestions) > 0
    # P1.11: route27_optimizer emits two new-stop statuses: NEW_SUGGEST (gap-fill
    # candidates with full BCR) and NEW_IN_SELECTION (FTA-spacing candidates,
    # no BCR).  Counters previously only saw NEW_SUGGEST, so the dashboard
    # showed "0 new stops" even when the optimizer produced 13.
    _NEW_STATUSES = {"NEW_SUGGEST", "NEW_IN_SELECTION"}
    r27_new_count = sum(1 for s in r27_suggestions if s.get("status") in _NEW_STATUSES)
    r27_high_count = sum(1 for s in r27_suggestions
                         if s.get("status") in _NEW_STATUSES and s.get("priority") == "HIGH")

    r76_data = {}
    cfg = data.get("config", {})
    transit_cfg = cfg.get("transit", {})
    for r in transit_cfg.get("discontinued_routes", []):
        if r.get("route_id") == "76":
            r76_data = r

    # Route 76 corridor line
    r76_line = json.dumps([
        [37.230,-121.978],[37.226,-121.979],[37.222,-121.981],[37.218,-121.984],
        [37.214,-121.988],[37.210,-121.993],[37.206,-121.998],[37.200,-122.004],
        [37.192,-122.010],[37.183,-122.018],[37.175,-122.025],[37.168,-122.030],
        [37.155,-122.040]
    ])

    # Build glossary HTML section (alphabetical by term)
    _gl_items = []
    for _term in sorted(GLOSSARY.keys(), key=str.lower):
        _slug = _term.lower().replace(" ", "-").replace("/", "-").replace(".", "")
        # Glossary values are authored as trusted HTML containing cross-refs
        # (<a href="#gl-...">) and external source links (<a href="https://...">).
        # Authors must pre-encode ampersands as &amp; in URL query strings.
        _def_html = GLOSSARY[_term]
        _gl_items.append(
            f'<dt id="gl-{_slug}"><a href="#gl-{_slug}" style="color:var(--ac);text-decoration:none">{_term}</a></dt>'
            f"<dd>{_def_html}</dd>"
        )
    _gl_body = "\n".join(_gl_items)
    glossary_html = (
        '<section id="glossary" class="full">\n'
        '<div class="stitle">Glossary</div>\n'
        '<div class="ssub">Acronyms and key concepts used throughout this dashboard. '
        'Underlined terms in the page text show a tooltip on hover.</div>\n'
        f'<dl class="glossary-grid">\n{_gl_body}\n</dl>\n'
        "</section>"
    )

    # Serialize glossary to JSON for JS auto-wrapping
    js_glossary = json.dumps(GLOSSARY)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Los Gatos Transit CBA Dashboard</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,700&display=swap');
:root{{--bg:#0c0e12;--s1:#141720;--s2:#1c2030;--s3:#252a3a;--bd:#2a3050;--tx:#d8dce8;--tm:#7a8098;--ac:#4ecdc4;--red:#ff6b6b;--amber:#ffa94d;--green:#69db7c;--blue:#6c9bff;--font-display:'Source Serif 4',Georgia,serif;--font-mono:'IBM Plex Mono',monospace}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:var(--font-mono);background:var(--bg);color:var(--tx);line-height:1.5}}
h1,h2,h3{{font-family:var(--font-display);font-weight:700}}
.hero{{padding:28px 36px 20px;border-bottom:1px solid var(--bd);background:linear-gradient(135deg,var(--s1),var(--s2))}}
.hero h1{{font-size:26px;letter-spacing:-.5px}}.hero h1 em{{font-style:normal;color:var(--ac)}}
.hero p{{font-size:11px;color:var(--tm);margin-top:4px}}
.data-badges{{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}}
.badge{{font-size:9px;padding:3px 10px;border-radius:12px;font-weight:600}}
.badge.real{{background:rgba(78,205,196,.15);color:var(--ac);border:1px solid rgba(78,205,196,.3)}}
.badge.synth{{background:rgba(255,107,107,.1);color:var(--red);border:1px solid rgba(255,107,107,.2)}}
.badge.phase{{background:var(--s3);color:var(--tm);border:1px solid var(--bd)}}
.badge.done{{color:var(--ac);border-color:rgba(78,205,196,.3)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--bd)}}
.grid>*{{background:var(--s1);padding:20px}}.grid .full{{grid-column:1/-1}}
.stitle{{font-size:13px;color:var(--ac);text-transform:uppercase;letter-spacing:2px;margin-bottom:12px;font-family:var(--font-mono);font-weight:600}}
.ssub{{font-size:10px;color:var(--tm);margin-top:-8px;margin-bottom:10px}}
#map{{height:460px;border-radius:6px;border:1px solid var(--bd)}}
#routeMap{{height:520px;border-radius:6px;border:1px solid var(--bd)}}
.hw-chip{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:9px;font-weight:700;margin:1px}}
.hw-fast{{background:rgba(105,219,124,.18);color:var(--green);border:1px solid rgba(105,219,124,.3)}}
.hw-med{{background:rgba(255,169,77,.12);color:var(--amber);border:1px solid rgba(255,169,77,.25)}}
.hw-slow{{background:rgba(255,107,107,.1);color:var(--red);border:1px solid rgba(255,107,107,.2)}}
.school-ok{{color:var(--ac);font-weight:700}}.school-miss{{color:var(--red);font-weight:700}}
.opt-empty{{text-align:center;padding:40px;color:var(--tm);font-size:11px;line-height:1.8}}
.route-legend{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}
.route-leg-item{{display:flex;align-items:center;gap:5px;font-size:9px;color:var(--tm)}}
.route-leg-swatch{{width:24px;height:3px;border-radius:2px}}
.stop-leg-item{{display:flex;align-items:center;gap:5px;font-size:9px;color:var(--tm)}}
.stop-leg-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.mrow{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}}
.metric{{background:var(--s2);border:1px solid var(--bd);border-radius:7px;padding:10px 14px;flex:1;min-width:110px}}
.metric .lb{{font-size:8px;color:var(--tm);text-transform:uppercase;letter-spacing:1px;margin-bottom:3px}}
.metric .vl{{font-size:20px;font-family:var(--font-display);font-weight:700}}
.metric .vl.ac{{color:var(--ac)}}.metric .vl.rd{{color:var(--red)}}
.metric .su{{font-size:8px;color:var(--tm);margin-top:1px}}
.cw{{position:relative;height:280px;margin-top:6px}}.cw.tall{{height:330px}}
table{{width:100%;border-collapse:collapse;font-size:10px}}
th{{text-align:left;padding:5px 6px;border-bottom:2px solid var(--bd);color:var(--tm);font-size:8px;text-transform:uppercase;letter-spacing:1px}}
td{{padding:4px 6px;border-bottom:1px solid rgba(42,48,80,.4)}}tr:hover td{{background:var(--s2)}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
td .bar{{display:inline-block;height:8px;border-radius:2px;margin-right:4px;vertical-align:middle}}
.r76{{background:linear-gradient(135deg,rgba(255,107,107,.07),rgba(255,107,107,.02));border:1px solid rgba(255,107,107,.2);border-radius:7px;padding:14px 18px;margin-top:10px}}
.r76 h3{{color:var(--red);font-size:13px;margin-bottom:8px}}
.r76g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px}}
.r76s{{text-align:center}}.r76s .v{{font-size:16px;font-family:var(--font-display);font-weight:700}}.r76s .l{{font-size:8px;color:var(--tm)}}
.leaflet-popup-content-wrapper{{background:var(--s2)!important;color:var(--tx)!important;border:1px solid var(--bd)!important;border-radius:6px!important;font-family:var(--font-mono)!important;font-size:10px!important}}
.leaflet-popup-tip{{background:var(--s2)!important}}
.callout{{border-radius:7px;padding:12px 16px;margin-bottom:14px;font-size:10px;line-height:1.6}}
.callout.warn{{background:rgba(255,169,77,.07);border:1px solid rgba(255,169,77,.25);}}
.callout.info{{background:rgba(78,205,196,.05);border:1px solid rgba(78,205,196,.2);}}
.callout strong{{color:var(--amber)}}
.callout.info strong{{color:var(--ac)}}
.ben-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;margin-top:10px}}
.ben-card{{background:var(--s2);border:1px solid var(--bd);border-radius:7px;padding:12px 14px}}
.ben-card .bc-num{{font-size:9px;font-weight:600;color:var(--ac);letter-spacing:1px;margin-bottom:4px}}
.ben-card .bc-name{{font-size:12px;font-family:var(--font-display);font-weight:700;margin-bottom:5px}}
.ben-card .bc-body{{font-size:10px;color:var(--tm);line-height:1.5}}
.ben-card .bc-src{{font-size:9px;color:rgba(122,128,152,.6);margin-top:5px;border-top:1px solid var(--bd);padding-top:5px}}
.correction-banner{{background:rgba(255,107,107,.06);border:1px solid rgba(255,107,107,.2);border-radius:7px;padding:10px 16px;margin:10px 36px 0;font-size:10px;display:flex;align-items:flex-start;gap:12px}}
.correction-banner .cb-icon{{font-size:16px;line-height:1;margin-top:2px}}
.correction-banner .cb-body{{flex:1}}
.correction-banner strong{{color:var(--amber)}}
@media(max-width:900px){{.grid{{grid-template-columns:1fr}}.grid>*{{grid-column:1/-1}}}}
/* ---- Jargon tooltips ---- */
.jargon{{border-bottom:1px dotted var(--ac);cursor:help;color:inherit;text-decoration:none;background:rgba(78,205,196,.04);padding:0 1px;border-radius:2px;transition:background 120ms}}
.jargon:hover,.jargon:focus{{background:rgba(78,205,196,.18);outline:none}}
.jargon-tip{{position:fixed;z-index:99999;max-width:300px;background:var(--s2);border:1px solid var(--bd);border-radius:7px;padding:10px 13px;font-size:10px;line-height:1.55;color:var(--tx);box-shadow:0 6px 22px rgba(0,0,0,.55);display:none}}
.jargon-tip .jt-term{{font-weight:700;color:var(--ac);margin-bottom:4px;font-size:11px;font-family:var(--font-mono)}}
/* ---- Glossary section ---- */
.glossary-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:0 28px}}
.glossary-grid dt{{font-weight:700;color:var(--ac);font-size:10px;padding:6px 0 1px;border-top:1px solid rgba(42,48,80,.5);scroll-margin-top:80px;border-radius:3px;transition:background 200ms,padding-left 200ms}}
.glossary-grid dd{{color:var(--tm);font-size:9px;line-height:1.55;margin:0 0 4px 0;padding-bottom:4px;border-radius:3px;transition:background 200ms}}
@keyframes glossFlash{{
  0%{{background:rgba(78,205,196,.55);box-shadow:0 0 0 4px rgba(78,205,196,.35)}}
  60%{{background:rgba(78,205,196,.22)}}
  100%{{background:transparent;box-shadow:none}}
}}
.glossary-grid dt.gloss-flash,.glossary-grid dd.gloss-flash{{animation:glossFlash 1800ms ease-out}}
.glossary-grid dt.gloss-flash{{padding-left:6px;color:#fff}}
</style>
</head>
<body>
<div class="hero">
<h1>Los Gatos Transit CBA -- <em>Pipeline Dashboard</em></h1>
<p>Auto-generated from pipeline output CSVs. Census: {data.get('census_count', 0)} block groups, {data.get('census_total_pop', 0):,} total pop.</p>
<div class="data-badges">
<span class="badge {'real' if data_status=='REAL' else 'synth'}">Census: {data_status}</span>
<span class="badge {'real' if gtfs_status=='REAL' else 'synth'}">GTFS: {gtfs_status}</span>
<span class="badge done">A1 Districts</span>
<span class="badge done">A2 Costs</span>
<span class="badge phase">A3 Benefits</span>
<span class="badge phase">A4 Demand</span>
<span class="badge {'done' if opt_run else 'phase'}">B Route Opt {'✓' if opt_run else ''}</span>
<span class="badge {'done' if r27_run else 'phase'}">Rt27 {'✓ ' + str(r27_new_count) + ' new stops' if r27_run else 'pending'}</span>
</div>
</div>
<div class="correction-banner">
<div class="cb-icon">&#9888;</div>
<div class="cb-body">
<strong>Model Corrections Applied (Gov. Compliance):</strong>
<strong>SCC $56 &rarr; $120/tCO&#8322;</strong> &mdash; EPA 2022 regulatory update supersedes prior IWG value.
&bull; <strong>VOT trip-purpose split</strong> &mdash; USDOT BCA 2024 Table 4: personal $17.80/hr, business $31.90/hr (was: flat $20.60).
&bull; <strong>Induced demand (8th category)</strong> &mdash; TCRP Report 95: 20% of riders are enabled by transit, not diverted from auto.
&bull; <strong>FTA Cost Effectiveness Index</strong> &mdash; Required metric for CIG federal funding applications.
</div>
</div>
<div class="grid">
<div class="full">
<div class="stitle">District Map + Transit</div>
<div class="ssub">Click districts for details. Teal stops = Rt 27, Red = Rt 76 (disc.), Orange = Hwy 17X</div>
<div id="map"></div>
</div>
<div>
<div class="stitle">Annual Cost by District</div>
<div class="ssub">Operating + amortized capital. $0 = no transit service (coverage gap)</div>
<div class="cw tall"><canvas id="cCost"></canvas></div>
</div>
<div>
<div class="stitle">Route Financials</div>
<div class="ssub">Operating cost vs fare revenue</div>
<div class="cw"><canvas id="cRoute"></canvas></div>
</div>
<div>
<div class="stitle">Population + Density</div>
<div class="ssub">Bar=population, Line=density per sq mi</div>
<div class="cw tall"><canvas id="cPop"></canvas></div>
</div>
<div>
<div class="stitle">Transit Equity</div>
<div class="ssub">Zero-vehicle HH rate vs stop count. Bottom-right = unmet need</div>
<div class="cw tall"><canvas id="cEq"></canvas></div>
</div>
<div>
<div class="stitle">NTD Peer Benchmarks</div>
<div class="ssub">VTA Bus vs CA peers (cost per revenue-hour)</div>
<div class="cw"><canvas id="cPeer"></canvas></div>
</div>
<div class="full">
<div class="stitle">Present Value Analysis</div>
<div class="ssub">20-year PV of costs vs. 8-category benefits at OMB Circular A-94 discount rates. Click any card for full breakdown with per-category methodology.</div>
<div class="callout info" style="margin-bottom:12px">
<strong>Reading these cards:</strong> BCR &gt; 1.0 = benefits exceed costs. The 3.5% rate is OMB&rsquo;s recommended rate for long-lived infrastructure. All costs are the study-area share of VTA route costs (not full system). Benefits use corrected SCC ($120/tCO&#8322;) and include induced demand (8th category).
</div>
<div id="npvCards" style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:14px"></div>
<div id="npvBreakdown" style="display:none;background:var(--s2);border:1px solid var(--bd);border-radius:7px;padding:16px;margin-bottom:14px">
<h3 style="font-size:13px;margin-bottom:10px;color:var(--ac)" id="npvBreakdownTitle">Breakdown</h3>
<div id="ftaCEBox" style="font-size:10px;padding:8px 12px;background:rgba(108,155,255,.07);border:1px solid rgba(108,155,255,.2);border-radius:5px;margin-bottom:12px;color:var(--tm)"></div>
<div>
<h4 style="font-size:10px;color:var(--tm);margin-bottom:6px">COST COMPONENTS (Annual) &mdash; Study-area share only</h4>
<table id="npvCostBreakdown"></table>
</div>
<div style="margin-top:14px">
<h4 style="font-size:10px;color:var(--tm);margin-bottom:6px">BENEFIT COMPONENTS (Annual) &mdash; 8 categories per FTA guidelines</h4>
<table id="npvBenBreakdown"></table>
</div>
<div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--bd)">
<h4 style="font-size:10px;color:var(--tm);margin-bottom:6px">YEAR-BY-YEAR PV STREAM (selected years, allocated costs)</h4>
<table id="npvYearStream"></table>
</div>
</div>
<div class="cw"><canvas id="cNPV"></canvas></div>
</div>
<div class="full">
<div class="stitle">Route 76 Restoration Scenario</div>
<div class="r76">
<h3>VTA Route 76 -- Downtown LG to Summit Road</h3>
<div class="r76g">
<div class="r76s"><div class="v" style="color:var(--red)">$900K</div><div class="l">Capital Investment</div></div>
<div class="r76s"><div class="v" style="color:var(--red)">$128K/yr</div><div class="l">Operating Cost</div></div>
<div class="r76s"><div class="v" style="color:var(--amber)">$113K/yr</div><div class="l">Net Cost</div></div>
<div class="r76s"><div class="v" style="color:var(--amber)">$17.76</div><div class="l">Cost/Boarding</div></div>
<div class="r76s"><div class="v">4 trips/day</div><div class="l">School Days (180/yr)</div></div>
<div class="r76s"><div class="v">~40/day</div><div class="l">Est. Boardings</div></div>
<div class="r76s"><div class="v">12 mi</div><div class="l">Route Length</div></div>
<div class="r76s"><div class="v" style="color:var(--ac)">8 stops</div><div class="l">To Rehabilitate</div></div>
</div>
<p style="margin-top:12px;font-size:9px;color:var(--tm);line-height:1.5">
Discontinued June 2010. Was the only transit to 95033 mountains (~8,000 residents).
Maintained for LGHS student access. Bus stop signs still present.
CBA Phase A3 will evaluate whether benefits justify the $113K/yr net cost.
</p>
</div>
</div>
<div class="full">
<div class="stitle">Phase B &mdash; Route Optimization</div>
<div class="ssub">Optimised routes, stop selection, headways, school coverage, and demand analysis.</div>
<div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:10px">
  <a href="route_optimization.html" style="display:flex;align-items:center;gap:12px;background:var(--s2);border:1px solid rgba(78,205,196,.3);border-radius:8px;padding:16px 22px;text-decoration:none;flex:1;min-width:260px">
    <div style="font-size:28px">🗺</div>
    <div>
      <div style="font-size:13px;font-weight:700;color:var(--ac)">Open Route Optimization Dashboard</div>
      <div style="font-size:10px;color:var(--tm);margin-top:3px">{'<span style="color:var(--ac)">&#10003; Phase B complete &mdash; ' + str(len(set(r["route_id"] for r in data.get("optimised_routes", [])))) + ' routes, ' + str(len(data.get("selected_stops", []))) + ' stops</span>' if opt_run else 'Run python run_analysis.py to generate results'}</div>
      <div style="font-size:9px;color:var(--tm);margin-top:4px">Clarke-Wright routing &bull; Mohring headways &bull; FTA 9040.1G spacing &bull; Route 27 stop BCR</div>
    </div>
  </a>
</div>
</div>
<div class="full">
<div class="stitle">Benefit Category Reference</div>
<div class="ssub">All 8 economic benefit categories quantified in this analysis. Per FTA CBA guidelines + USDOT BCA Guidance 2024.</div>
<div class="callout info" style="margin-bottom:10px">
<strong>How the CBA works:</strong> For each year over 20 years, the model computes the economic value of benefits (right column) and compares them to the annual cost of running the transit service. Benefits and costs are discounted back to present value at OMB-prescribed rates (2%, 3.5%, 7%). The Benefit-Cost Ratio (BCR) is PV Benefits &divide; PV Costs. BCR &gt; 1.0 means the service creates more economic value than it costs.
</div>
<div class="ben-grid">
<div class="ben-card">
<div class="bc-num">CATEGORY 1</div>
<div class="bc-name">Travel Time Savings</div>
<div class="bc-body">
When a transit rider switches from driving, they save the productive value of reduced auto travel time. Transit in-vehicle time is valued at 60% of auto time (riders can read, work, or rest). VOT is split by trip purpose: personal trips = $17.80/hr, employer business trips = $31.90/hr (25% of LG ridership estimated as work trips via ACS).
</div>
<div class="bc-src">USDOT BCA Guidance 2024, Table 4 &bull; OMB Circular A-94</div>
</div>
<div class="ben-card">
<div class="bc-num">CATEGORY 2</div>
<div class="bc-name">Vehicle Operating Cost Savings</div>
<div class="bc-body">
Every auto trip replaced by transit avoids the marginal cost of operating a private vehicle: fuel, oil, tires, and maintenance. At $0.68/mile (AAA 2024 CA average) and 7.5-mile average trip, each diverted ride saves ~$5.10 in vehicle costs. Across tens of thousands of annual boardings this accumulates rapidly.
</div>
<div class="bc-src">AAA "Your Driving Costs" 2024 &bull; FTA CBA Guidelines</div>
</div>
<div class="ben-card">
<div class="bc-num">CATEGORY 3</div>
<div class="bc-name">Crash Reduction</div>
<div class="bc-body">
Fewer miles driven means fewer crashes. Using Santa Clara County crash rates (~120 crashes/100M VMT, SWITRS 5-year average) and FHWA KABCO severity weights, each avoided VMT reduces expected crash costs. Fatal crashes are valued at $12.8M (USDOT VSL 2024). Even rare reductions in serious injury rates produce large economic benefits.
</div>
<div class="bc-src">FHWA crash cost tables (2022 update) &bull; SWITRS Santa Clara County</div>
</div>
<div class="ben-card">
<div class="bc-num">CATEGORY 4</div>
<div class="bc-name">Emission Reduction</div>
<div class="bc-body">
Avoided auto VMT reduces CO&#8322;, NOx, and PM2.5 emissions. CO&#8322; is valued at <strong style="color:var(--ac)">$120/metric ton</strong> per EPA 2022 regulatory guidance (3% rate), corrected from the prior $56/ton IWG value. This ~114% increase in the SCC substantially raises this benefit category. Criteria pollutant health damage valued via EPA BenMAP-CE.
</div>
<div class="bc-src">EPA SC-CO&#8322; Comprehensive Update 2022 &bull; EPA MOVES3.1 &bull; BenMAP-CE</div>
</div>
<div class="ben-card">
<div class="bc-num">CATEGORY 5</div>
<div class="bc-name">Health Benefits (Active Transport)</div>
<div class="bc-body">
Transit riders walk an average of 12 minutes per trip to and from stops (WHO HEAT default). This physical activity reduces mortality risk and healthcare costs at $0.16 per walking minute (CDC valuation of avoided sedentary-related costs). For a system with ~264K annual boardings this represents ~790K walking-hours per year of health benefit.
</div>
<div class="bc-src">WHO HEAT v5.2 &bull; CDC Physical Activity Economics 2023</div>
</div>
<div class="ben-card">
<div class="bc-num">CATEGORY 6</div>
<div class="bc-name">Reliability Benefits</div>
<div class="bc-body">
Transit schedules are more predictable than driving on SR-17 and SR-85, where congestion variability can add 10-25% to travel time. Travelers value reliability at 80% of mean travel time savings (USDOT guidance). Observed schedule data shows average deviation of +2.2 minutes at LGHS stops, confirming real variability in this corridor.
</div>
<div class="bc-src">USDOT BCA Guidance 2024, Section 5.3 &bull; PeMS SR-17 observed data</div>
</div>
<div class="ben-card">
<div class="bc-num">CATEGORY 7</div>
<div class="bc-name">Option Value</div>
<div class="bc-body">
Even non-riders benefit from transit availability. When a car breaks down, gas prices spike, or someone loses driving ability, transit provides a backup. This option value is estimated at $20-$40 per capita per year (mid-range of stated-preference studies). With ~68,000 residents in the service area, this is a significant base benefit that does not depend on ridership levels.
</div>
<div class="bc-src">TCRP Report 78, Section 4.5 &bull; Boardman et al. Ch. 6</div>
</div>
<div class="ben-card" style="border-color:rgba(78,205,196,.3)">
<div class="bc-num" style="color:var(--amber)">CATEGORY 8 &mdash; NEW</div>
<div class="bc-name">Induced Demand (Accessibility)</div>
<div class="bc-body">
Not all transit riders would otherwise drive. An estimated 20% of riders are <em>induced</em> &mdash; they make trips that simply would not occur without transit: seniors without licenses, teens, zero-car households, and people making trips that aren't worth the parking cost. Their economic benefit is valued at 50% of the equivalent auto trip cost (consumer surplus triangle). This category is absent from analyses that assume all ridership is auto diversion.
</div>
<div class="bc-src">TCRP Report 95, Ch. 1 &bull; Boardman et al. Ch. 5 (demand curve CS triangle)</div>
</div>
</div>
<div class="callout warn" style="margin-top:14px">
<strong>What is NOT counted here (conservative scope):</strong>
Property value uplift near stops (+2% within 0.5mi &mdash; Optimistic scenario only) &bull;
Wider agglomeration/labor market effects &bull;
CEQA/environmental compliance value &bull;
Tax revenue impacts &bull;
School access value (Moderate/Optimistic scenarios only) &bull;
Route 76 restoration benefits (separate scenario analysis below)
</div>
</div>
<div class="full">
<div class="stitle">Full District Table</div>
<div style="overflow-x:auto"><table id="tbl"></table></div>
</div>
{glossary_html}
</div>
<div class="jargon-tip" id="jargonTip"><div class="jt-term" id="jargonTipTerm"></div><div id="jargonTipBody"></div></div>
<script>
const D={js_districts};
const S={js_stops};
const RC={js_routes};
const PE={js_peers};
const NV={js_npv};
const NVB={js_npv_benefits};
const BEN={js_benefits};
const ALLOC_OP={js_allocated_op};
const ALLOC_CAP={js_total_capital};
const R76={r76_line};
// Phase B data is loaded in route_optimization.html, not here.

// MAP
const map=L.map('map',{{center:[37.235,-121.960],zoom:13}});
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',{{maxZoom:19}}).addTo(map);
D.forEach(d=>{{
  if(!d.coords.length)return;
  const fill=d.totalAnn>0?0.22:0.06;
  const l=L.polygon(d.coords,{{color:d.color,weight:2,opacity:.7,fillColor:d.color,fillOpacity:fill}}).addTo(map);
  const ct=l.getBounds().getCenter();
  L.marker(ct,{{icon:L.divIcon({{className:'',html:'<div style="font:700 9px var(--font-mono);color:'+d.color+';text-shadow:0 1px 3px #000;pointer-events:none">'+d.id+'</div>',iconSize:[24,10],iconAnchor:[12,5]}})
  }}).addTo(map);
  l.bindPopup('<b style="color:'+d.color+'">'+d.id+': '+d.name+'</b><br>Pop: '+(d.pop||0).toLocaleString()+' | Density: '+(d.density||0).toLocaleString()+'/mi2<br>Stops: '+(d.stops||0)+' | Annual Cost: $'+(d.totalAnn||0).toLocaleString()+'<br>Crashes: '+(d.crashes||0));
}});
S.forEach(s=>{{
  if(!s.lat)return;
  const c=s.route.includes('76')?'#ff6b6b':s.route.includes('17')?'#ffa502':'#4ecdc4';
  L.circleMarker([s.lat,s.lon],{{radius:4,fillColor:c,fillOpacity:.9,color:'#fff',weight:1}}).addTo(map).bindTooltip(s.name);
}});
L.polyline(R76,{{color:'#ff6b6b',weight:2.5,opacity:.4,dashArray:'5 8'}}).addTo(map);

// CHARTS
Chart.defaults.color='#7a8098';Chart.defaults.borderColor='rgba(42,48,80,.5)';
Chart.defaults.font.family="'IBM Plex Mono',monospace";Chart.defaults.font.size=10;
new Chart(document.getElementById('cCost'),{{type:'bar',data:{{labels:D.map(d=>d.id),datasets:[
{{label:'Operating',data:D.map(d=>d.opCost),backgroundColor:'rgba(78,205,196,.7)'}},
{{label:'Capital/yr',data:D.map(d=>Math.max(0,d.totalAnn-d.opCost)),backgroundColor:'rgba(108,155,255,.5)'}}
]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'top'}}}},scales:{{x:{{stacked:true}},y:{{stacked:true,ticks:{{callback:v=>'$'+(v/1000).toFixed(0)+'K'}}}}}}}}
}});
const rcLabels=RC.map(r=>r.route_name||r.route_id);
new Chart(document.getElementById('cRoute'),{{type:'bar',data:{{labels:rcLabels,datasets:[
{{label:'Operating',data:RC.map(r=>r.annual_operating_cost||0),backgroundColor:'rgba(255,107,107,.6)'}},
{{label:'Fare Revenue',data:RC.map(r=>r.fare_revenue||0),backgroundColor:'rgba(78,205,196,.6)'}}
]}},options:{{responsive:true,maintainAspectRatio:false,indexAxis:'y',scales:{{x:{{ticks:{{callback:v=>'$'+(v/1e6).toFixed(1)+'M'}}}}}}}}
}});
const popD=D.filter(d=>d.pop>0);
new Chart(document.getElementById('cPop'),{{type:'bar',data:{{labels:popD.map(d=>d.id),datasets:[
{{label:'Population',data:popD.map(d=>d.pop),backgroundColor:popD.map(d=>d.color+'99'),yAxisID:'y'}},
{{label:'Density',data:popD.map(d=>d.density),type:'line',borderColor:'#ffd43b',pointRadius:3,yAxisID:'y1'}}
]}},options:{{responsive:true,maintainAspectRatio:false,scales:{{y:{{position:'left',title:{{display:true,text:'Pop'}}}},y1:{{position:'right',grid:{{drawOnChartArea:false}},title:{{display:true,text:'Density'}}}}}}}}
}});
const eqD=D.filter(d=>d.pop>0&&d.zvr!=null);
new Chart(document.getElementById('cEq'),{{type:'scatter',data:{{datasets:[{{
data:eqD.map(d=>({{x:d.zvr*100,y:d.stops}})),backgroundColor:eqD.map(d=>d.color),pointRadius:8
}}]}},options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:ctx=>{{const d=eqD[ctx.dataIndex];return d.id+': '+((d.zvr||0)*100).toFixed(1)+'% ZV, '+d.stops+' stops'}}}}}}}},scales:{{x:{{title:{{display:true,text:'Zero-Vehicle HH Rate (%)'}}}},y:{{title:{{display:true,text:'Transit Stops'}},beginAtZero:true}}}}}}
}});
new Chart(document.getElementById('cPeer'),{{type:'bar',data:{{labels:PE.map(p=>p.agency),datasets:[
{{label:'$/Rev-Hour',data:PE.map(p=>p.cost_per_rev_hour),backgroundColor:PE.map((p,i)=>i===0?'rgba(78,205,196,.8)':'rgba(108,155,255,.4)')}}
]}},options:{{responsive:true,maintainAspectRatio:false,scales:{{y:{{ticks:{{callback:v=>'$'+v}}}}}}}}
}});
new Chart(document.getElementById('cNPV'),{{type:'bar',data:{{labels:NV.map(n=>n.discount_rate_label),datasets:[
{{label:'PV Costs',data:NV.map(n=>n.pv_total_cost||n.pv_operating+n.pv_capital),backgroundColor:'rgba(255,107,107,.7)'}},
{{label:'PV Benefits',data:NVB.map(n=>n.pv_benefits||0),backgroundColor:'rgba(78,205,196,.7)'}}
]}},options:{{responsive:true,maintainAspectRatio:false,scales:{{y:{{ticks:{{callback:v=>'$'+(v/1e6).toFixed(0)+'M'}}}}}}}}
}});

// NPV Cards with click-to-expand
const npvCards=document.getElementById('npvCards');
const npvBreakdown=document.getElementById('npvBreakdown');
let activeRate=null;
NV.forEach((n,i)=>{{
  const pvC=n.pv_total_cost||(n.pv_operating+n.pv_capital);
  const pvB=NVB[i]?NVB[i].pv_benefits:0;
  const bcr=pvB/Math.max(pvC,1);
  const net=pvB-pvC;
  const card=document.createElement('div');
  card.className='metric';
  card.style.cursor='pointer';
  card.style.transition='border-color 0.2s';
  card.innerHTML='<div class="lb">'+n.discount_rate_label+' Discount Rate</div>'
    +'<div class="vl" style="font-size:16px;color:'+(bcr>=1?'var(--ac)':'var(--red)')+'">BCR '+bcr.toFixed(2)+'</div>'
    +'<div class="su">Costs: $'+(pvC/1e6).toFixed(1)+'M | Benefits: $'+(pvB/1e6).toFixed(1)+'M</div>'
    +'<div class="su">Net: $'+(net/1e6).toFixed(1)+'M</div>'
    +'<div class="su" style="color:var(--ac);margin-top:4px">Click for breakdown</div>';
  card.onclick=()=>showNPVBreakdown(i,n,pvC,pvB);
  npvCards.appendChild(card);
}});

function showNPVBreakdown(idx,nv,pvC,pvB){{
  const r=nv.discount_rate||0.035;
  const rLabel=nv.discount_rate_label||'3.5%';
  npvBreakdown.style.display='block';
  document.getElementById('npvBreakdownTitle').textContent='Breakdown at '+rLabel+' Discount Rate';

  // Cost breakdown using ALLOCATED costs (study-area share)
  const costTbl=document.getElementById('npvCostBreakdown');
  const fullRouteTotal=RC.reduce((s,r)=>s+(r.annual_operating_cost||0),0);
  const studyShareRatio=ALLOC_OP/Math.max(fullRouteTotal,1);

  let ch='<tr><th>Component</th><th style="text-align:right">Full Route</th><th style="text-align:right">Study Area Share</th><th style="text-align:right">20-yr PV</th></tr>';
  RC.forEach(rt=>{{
    const rtFull=rt.annual_operating_cost||0;
    const rtAlloc=Math.round(rtFull*studyShareRatio);
    const rtShare=rtAlloc/Math.max(ALLOC_OP,1);
    const rtPV=Math.round(rtShare*(nv.pv_operating||0));
    ch+='<tr><td>'+(rt.route_name||rt.route_id)+'</td><td class="n" style="color:var(--tm)">$'+rtFull.toLocaleString()+'</td><td class="n">$'+rtAlloc.toLocaleString()+'</td><td class="n">$'+rtPV.toLocaleString()+'</td></tr>';
  }});
  ch+='<tr style="border-top:2px solid var(--bd)"><td><b>Capital (one-time)</b></td><td class="n">--</td><td class="n">--</td><td class="n">$'+(ALLOC_CAP||0).toLocaleString()+'</td></tr>';
  ch+='<tr style="font-weight:600;color:var(--red)"><td>TOTAL COSTS</td><td class="n" style="color:var(--tm)">$'+fullRouteTotal.toLocaleString()+'</td><td class="n">$'+ALLOC_OP.toLocaleString()+'</td><td class="n">$'+pvC.toLocaleString()+'</td></tr>';
  ch+='<tr style="font-size:9px;color:var(--tm)"><td colspan="4">Study area = '+Math.round(studyShareRatio*100)+'% of full route cost (based on stop share)</td></tr>';
  costTbl.innerHTML=ch;

  // Benefit breakdown
  const benTbl=document.getElementById('npvBenBreakdown');
  const totalAnnBen=BEN.reduce((s,b)=>s+(b.annual_benefit||0),0);
  const benDesc={{'Travel Time Savings':'Diverted auto trips * (auto time - 60% of transit time) * purpose-weighted VOT ($17.80 personal / $31.90 business)','Vehicle Operating Cost Savings':'Diverted trips * avg trip miles * $0.68/mi (AAA 2024). Marginal auto operating cost avoided.','Crash Reduction':'Avoided VMT * Santa Clara County crash rate * FHWA KABCO severity-weighted costs. Includes fatal ($12.8M VSL), serious, minor, PDO.','Emission Reduction':'Avoided auto CO2 * $120/tCO2 (EPA 2022 regulatory, corrected from $56) + NOx/PM2.5 health damage via BenMAP-CE.','Health Benefits (Active Transport)':'Annual boardings * 12 walk-min/trip * $0.16/min (CDC sedentary cost avoided). WHO HEAT v5.2 methodology.','Reliability Benefits':'Diverted trips * (auto trip variability minutes / 60) * VOT * 0.80. USDOT reliability value = 80% of time value.','Option Value':'Service area population * $20-40/capita/yr (stated-preference WTP for transit availability, for non-riders too).','Induced Demand (Accessibility)':'20% of boardings are induced (TCRP 95) * 50% of auto trip value as consumer surplus. Trips that only exist because transit exists.'}};
  let bh='<tr><th>Category</th><th>What it measures</th><th style="text-align:right">Annual</th><th style="text-align:right">20-yr PV</th><th style="text-align:right">Share</th></tr>';
  BEN.forEach(b=>{{
    const bAnn=b.annual_benefit||0;
    const share=bAnn/Math.max(totalAnnBen,1);
    const bPV=Math.round(share*pvB);
    const cat=b.category||'';
    const desc=benDesc[cat]||'';
    const isNew=cat.includes('Induced');
    bh+='<tr'+(isNew?' style="background:rgba(78,205,196,.04)"':'')+'><td style="font-weight:600;color:'+(isNew?'var(--ac)':'var(--tx)')+'">'+cat+'</td><td style="color:var(--tm);font-size:9px;max-width:260px">'+desc+'</td><td class="n">$'+bAnn.toLocaleString()+'</td><td class="n">$'+bPV.toLocaleString()+'</td><td class="n">'+(share*100).toFixed(0)+'%</td></tr>';
  }});
  bh+='<tr style="font-weight:600;color:var(--ac)"><td>TOTAL BENEFITS</td><td></td><td class="n">$'+totalAnnBen.toLocaleString()+'</td><td class="n">$'+pvB.toLocaleString()+'</td><td class="n">100%</td></tr>';
  benTbl.innerHTML=bh;

  // FTA Cost Effectiveness Index
  const annualizedCost=Math.round(ALLOC_OP+(ALLOC_CAP||0)/20);
  const diverted=Math.round(totalAnnBen>0?ALLOC_OP*0.55:0); // approx diverted trips
  const baseBoarding=RC.reduce((s,rt)=>s+(rt.annual_boardings||0),0);
  const pctDiv=0.55;
  const avgAutoMin=22, avgTransitMin=35;
  const divertedTrips=Math.round(baseBoarding*pctDiv);
  const transitOnly=baseBoarding-divertedTrips;
  const tsub_div=Math.max(0,divertedTrips*(avgAutoMin-avgTransitMin)/60);
  const tsub_dep=transitOnly*avgTransitMin/60;
  const totalTSUB=tsub_div+tsub_dep;
  const ceIndex=totalTSUB>0?(annualizedCost/totalTSUB).toFixed(2):0;
  let ceRating='Low';
  const cei=parseFloat(ceIndex);
  if(cei<1)ceRating='High';
  else if(cei<2)ceRating='Medium-High';
  else if(cei<4)ceRating='Medium';
  else if(cei<6)ceRating='Medium-Low';
  const ceColor=cei<2?'var(--ac)':cei<4?'var(--amber)':'var(--red)';
  const ceBox=document.getElementById('ftaCEBox');
  if(ceBox)ceBox.innerHTML='<b>FTA Cost Effectiveness Index:</b> <span style="color:'+ceColor+';font-weight:700">$'+ceIndex+'/TSUB-hr</span> &mdash; Rating: <span style="color:'+ceColor+'">'+ceRating+'</span> ('+Math.round(totalTSUB).toLocaleString()+' user-benefit hrs/yr, $'+annualizedCost.toLocaleString()+' annualized cost). FTA CIG threshold: &lt;$2 = Medium-High; &lt;$4 = Medium.';

  // Year-by-year stream using ALLOCATED costs
  const yrTbl=document.getElementById('npvYearStream');
  // Source of truth: cost_model.compute_cost_npv (2.5%/yr), benefit_model.compute_benefit_npv (1.0%/yr)
  const costGrowth=0.025; const benGrowth=0.010;
  let yh='<tr><th>Year</th><th style="text-align:right">Cost (nominal)</th><th style="text-align:right">Benefit (nominal)</th><th style="text-align:right">PV Cost</th><th style="text-align:right">PV Benefit</th><th style="text-align:right">Cumulative Net PV</th></tr>';
  let cumNet=-(ALLOC_CAP||0);
  yh+='<tr style="color:var(--tm)"><td>0 (Capital)</td><td class="n">--</td><td class="n">--</td><td class="n">$'+(ALLOC_CAP||0).toLocaleString()+'</td><td class="n">--</td><td class="n">$'+Math.round(cumNet).toLocaleString()+'</td></tr>';
  const showYears=[1,2,3,4,5,10,15,20];
  showYears.forEach(t=>{{
    const cNom=Math.round(ALLOC_OP*Math.pow(1+costGrowth,t));
    const bNom=Math.round(totalAnnBen*Math.pow(1+benGrowth,t));
    const pvCy=Math.round(cNom/Math.pow(1+r,t));
    const pvBy=Math.round(bNom/Math.pow(1+r,t));
    cumNet+=pvBy-pvCy;
    yh+='<tr><td>'+t+'</td><td class="n">$'+cNom.toLocaleString()+'</td><td class="n">$'+bNom.toLocaleString()+'</td><td class="n">$'+pvCy.toLocaleString()+'</td><td class="n">$'+pvBy.toLocaleString()+'</td><td class="n" style="color:'+(cumNet>=0?'var(--ac)':'var(--red)')+'">$'+Math.round(cumNet).toLocaleString()+'</td></tr>';
  }});
  yrTbl.innerHTML=yh;
}}

// ---- Glossary tooltip + auto-wrap (robust) ----
// Wraps EVERY occurrence (not just the first), re-wraps dynamically-injected
// content via MutationObserver, uses delegated mouseover/mouseout with a
// settle delay so cursor can move from term -> tooltip without flicker, and
// flashes the glossary entry on click-through so you can spot it.
const GLOSS={js_glossary};
(function(){{
  const tip=document.getElementById('jargonTip');
  const tipTerm=document.getElementById('jargonTipTerm');
  const tipBody=document.getElementById('jargonTipBody');
  let hideTimer=null;
  let activeTerm=null;

  function showTip(el){{
    if(hideTimer){{clearTimeout(hideTimer);hideTimer=null;}}
    const key=el.dataset.term;
    if(activeTerm===key&&tip.style.display==='block'){{positionTip(el);return;}}
    activeTerm=key;
    tipTerm.textContent=key;
    tipBody.textContent=GLOSS[key]||'';
    tip.style.display='block';
    positionTip(el);
  }}
  function positionTip(el){{
    const r=el.getBoundingClientRect();
    const tw=tip.offsetWidth||300,th=tip.offsetHeight||90;
    let left=r.left;
    let top=r.bottom+8;
    if(left+tw>window.innerWidth-8)left=window.innerWidth-tw-8;
    if(top+th>window.innerHeight-8)top=Math.max(4,r.top-th-8);
    tip.style.left=Math.max(4,left)+'px';
    tip.style.top=top+'px';
  }}
  function scheduleHide(){{
    if(hideTimer)clearTimeout(hideTimer);
    hideTimer=setTimeout(function(){{tip.style.display='none';activeTerm=null;}},180);
  }}

  // Delegated hover — finds the nearest .jargon ancestor of the target so
  // child <a> elements inside the span keep the tooltip open.
  document.addEventListener('mouseover',function(e){{
    const j=e.target.closest&&e.target.closest('.jargon');
    if(j){{showTip(j);return;}}
    // Hovering the tooltip itself counts as "still on" — keep it visible.
    if(e.target.closest&&e.target.closest('#jargonTip')){{
      if(hideTimer){{clearTimeout(hideTimer);hideTimer=null;}}
      return;
    }}
  }});
  document.addEventListener('mouseout',function(e){{
    const j=e.target.closest&&e.target.closest('.jargon');
    const t=e.target.closest&&e.target.closest('#jargonTip');
    if(!j&&!t)return;
    // Did the cursor leave to either a jargon span or the tip? Then keep open.
    const to=e.relatedTarget;
    if(to&&to.closest&&(to.closest('.jargon')||to.closest('#jargonTip')))return;
    scheduleHide();
  }});

  // Click on a wrapped term: smooth-scroll to glossary entry and flash it.
  document.addEventListener('click',function(e){{
    const a=e.target.closest&&e.target.closest('.jargon');
    if(!a)return;
    e.preventDefault();
    const term=a.dataset.term;
    const slug=slugify(term);
    const dt=document.getElementById('gl-'+slug);
    if(!dt)return;
    dt.scrollIntoView({{behavior:'smooth',block:'center'}});
    flashEntry(dt);
    // Update history without jumping
    if(history.replaceState)history.replaceState(null,'','#gl-'+slug);
  }});

  function flashEntry(dt){{
    const dd=dt.nextElementSibling;
    [dt,dd].forEach(function(n){{
      if(!n)return;
      n.classList.remove('gloss-flash');
      // Force reflow so re-adding the class restarts the animation
      void n.offsetWidth;
      n.classList.add('gloss-flash');
    }});
  }}

  // ---- Auto-wrap ----
  const skipTags=new Set(['SCRIPT','STYLE','CANVAS','SVG','A','BUTTON','INPUT','TEXTAREA','SELECT','OPTION']);
  const skipClassSubstrings=['jargon','jargon-tip','leaflet','glossary'];
  const terms=Object.keys(GLOSS).sort(function(a,b){{return b.length-a.length;}});
  const acronymRe=/^[A-Z0-9\\s.\\/&-]+$/;
  // Pre-build per-term match regexes once
  const termMeta=terms.map(function(term){{
    const isAcronym=acronymRe.test(term);
    const escaped=term.replace(/[.*+?^${{}}()|[\\]\\\\]/g,'\\$&');
    // Word-boundary match. For acronyms we keep case-sensitivity strict.
    return {{term:term,re:new RegExp('\\\\b'+escaped+'\\\\b',isAcronym?'g':'gi')}};
  }});

  // Must match Python build (`_term.lower().replace(" ","-").replace("/","-").replace(".","")`)
  // exactly so anchors line up with the dt IDs emitted server-side.
  function slugify(s){{return s.toLowerCase().split('.').join('').split(' ').join('-').split('/').join('-');}}

  function shouldSkipElement(el){{
    if(!el||!el.tagName)return true;
    if(skipTags.has(el.tagName))return true;
    const cls=(typeof el.className==='string'?el.className:(el.className&&el.className.baseVal)||'');
    for(let i=0;i<skipClassSubstrings.length;i++){{
      if(cls.indexOf(skipClassSubstrings[i])>=0)return true;
    }}
    // Don't wrap inside the popover itself
    if(el.id==='jargonTip')return true;
    return false;
  }}

  function wrapTextNode(node){{
    const text=node.textContent;
    if(!text||text.length<2||!/[A-Za-z]/.test(text))return false;
    // Find first match across all terms (longest-first), produce ALL matches.
    // Strategy: scan the text once per term; collect non-overlapping matches.
    const matches=[];
    for(let i=0;i<termMeta.length;i++){{
      const tm=termMeta[i];
      tm.re.lastIndex=0;
      let m;
      while((m=tm.re.exec(text))!==null){{
        // Reject overlap with already-claimed range
        const start=m.index,end=start+m[0].length;
        let overlap=false;
        for(let k=0;k<matches.length;k++){{
          if(!(end<=matches[k].start||start>=matches[k].end)){{overlap=true;break;}}
        }}
        if(!overlap)matches.push({{start:start,end:end,term:tm.term,match:m[0]}});
      }}
    }}
    if(!matches.length)return false;
    matches.sort(function(a,b){{return a.start-b.start;}});
    const parent=node.parentNode;
    if(!parent)return false;
    const frag=document.createDocumentFragment();
    let cursor=0;
    for(let i=0;i<matches.length;i++){{
      const mm=matches[i];
      if(mm.start>cursor)frag.appendChild(document.createTextNode(text.slice(cursor,mm.start)));
      const span=document.createElement('span');
      span.className='jargon';
      span.dataset.term=mm.term;
      span.dataset.glossWrapped='1';
      span.tabIndex=0;
      span.setAttribute('role','button');
      span.setAttribute('aria-label',mm.term+': '+(GLOSS[mm.term]||''));
      span.textContent=mm.match;
      frag.appendChild(span);
      cursor=mm.end;
    }}
    if(cursor<text.length)frag.appendChild(document.createTextNode(text.slice(cursor)));
    parent.replaceChild(frag,node);
    return true;
  }}

  function walk(root){{
    if(!root||shouldSkipElement(root))return;
    // Use a TreeWalker for performance and to avoid recursion limits.
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT,{{
      acceptNode:function(n){{
        // Skip text inside excluded ancestors
        let p=n.parentNode;
        while(p&&p!==root){{
          if(shouldSkipElement(p))return NodeFilter.FILTER_REJECT;
          p=p.parentNode;
        }}
        return NodeFilter.FILTER_ACCEPT;
      }}
    }});
    const targets=[];
    let n;
    while((n=walker.nextNode()))targets.push(n);
    targets.forEach(wrapTextNode);
  }}

  function run(){{walk(document.body);}}

  if(document.readyState==='loading'){{
    document.addEventListener('DOMContentLoaded',run);
  }} else {{
    run();
  }}

  // Re-wrap dynamically-injected content (NPV breakdown, schedule panel,
  // school demand, district demand, route 27 table, etc.). Coalesce updates
  // through requestAnimationFrame to avoid thrashing during big innerHTML
  // replacements.
  let pending=null;
  const observer=new MutationObserver(function(records){{
    const roots=[];
    for(let i=0;i<records.length;i++){{
      const r=records[i];
      for(let j=0;j<r.addedNodes.length;j++){{
        const node=r.addedNodes[j];
        if(node.nodeType===1&&!shouldSkipElement(node)&&!node.dataset.glossWrapped){{
          roots.push(node);
        }}
      }}
    }}
    if(!roots.length)return;
    if(pending)cancelAnimationFrame(pending);
    pending=requestAnimationFrame(function(){{
      pending=null;
      roots.forEach(walk);
    }});
  }});
  observer.observe(document.body,{{childList:true,subtree:true}});

  // Highlight the glossary entry if the page is opened with #gl-... in URL.
  window.addEventListener('load',function(){{
    if(location.hash&&location.hash.indexOf('#gl-')===0){{
      const dt=document.getElementById(location.hash.slice(1));
      if(dt)flashEntry(dt);
    }}
  }});
}})();

// Route optimization and Route 27 panels are in route_optimization.html
// TABLE
const t=document.getElementById('tbl');const mx=Math.max(...D.map(d=>d.totalAnn),1);
let h='<tr><th>ID</th><th>Name</th><th>Zone</th><th style="text-align:right">Pop</th><th style="text-align:right">Density</th><th style="text-align:right">Income</th><th style="text-align:right">ZV%</th><th style="text-align:right">Stops</th><th style="text-align:right">Op Cost</th><th style="text-align:right">Total/yr</th><th style="text-align:right">Crashes</th></tr>';
D.forEach(d=>{{
  const bw=Math.max(1,(d.totalAnn||0)/mx*100);
  h+='<tr><td style="color:'+d.color+';font-weight:600">'+d.id+'</td><td>'+d.name+'</td><td style="color:var(--tm)">'+d.zone+'</td><td class="n">'+(d.pop||0).toLocaleString()+'</td><td class="n">'+(d.density||0).toLocaleString()+'</td><td class="n">'+(d.income?'$'+d.income.toLocaleString():'--')+'</td><td class="n">'+(d.zvr!=null?(d.zvr*100).toFixed(1)+'%':'--')+'</td><td class="n" style="color:'+(d.stops===0?'var(--red)':'var(--ac)')+'">'+d.stops+'</td><td class="n"><span class="bar" style="width:'+bw+'%;background:'+d.color+'"></span>$'+(d.opCost||0).toLocaleString()+'</td><td class="n">$'+(d.totalAnn||0).toLocaleString()+'</td><td class="n">'+(d.crashes||0)+'</td></tr>';
}});
t.innerHTML=h;
</script>
</body>
</html>"""

    return html


def generate_route_optimization_html(data: dict) -> str:
    """Generate the standalone route optimization HTML page.

    Contains Phase B outputs: route map, schedule/headway table, school
    coverage, demand charts, Route 27 stop suggestions.  Uses the same
    design tokens as the CBA dashboard so both pages feel consistent.

    Args:
        data: Dict returned by load_pipeline_data().

    Returns:
        Full HTML string for route_optimization.html.
    """
    opt_run = len(data.get("optimised_routes", [])) > 0
    r27_run = len(data.get("route27_suggestions", [])) > 0
    # P1.11: see counterpart in generate_main_html — count both NEW_SUGGEST
    # and NEW_IN_SELECTION as "new stops" for headline metrics.
    _NEW_STATUSES = {"NEW_SUGGEST", "NEW_IN_SELECTION"}
    r27_new_count = sum(1 for s in data.get("route27_suggestions", [])
                        if s.get("status") in _NEW_STATUSES)
    r27_high_count = sum(1 for s in data.get("route27_suggestions", [])
                         if s.get("status") in _NEW_STATUSES and s.get("priority") == "HIGH")

    import math as _math

    def _clean(v):
        """Convert float NaN/Inf to None for safe JSON serialization."""
        if isinstance(v, float) and (not _math.isfinite(v)):
            return None
        return v

    def _clean_record(rec: dict) -> dict:
        return {k: _clean(v) for k, v in rec.items()}

    opt_routes = [_clean_record(r) for r in data.get("optimised_routes", [])]
    sel_stops = [_clean_record(s) for s in data.get("selected_stops", [])]

    js_opt_routes = json.dumps(opt_routes)
    js_selected_stops = json.dumps(sel_stops)
    js_district_demand = json.dumps(data.get("district_demand", []))
    js_school_demand = json.dumps(data.get("school_demand", []))
    js_school_coverage = json.dumps(data.get("school_coverage", []))
    js_gtfs_summary = json.dumps(data.get("gtfs_summary", []))
    js_r27_suggestions = json.dumps(data.get("route27_suggestions", []))
    js_r27_geojson = json.dumps(data.get("route27_geojson"))
    js_tdi = json.dumps(data.get("tdi", []))
    js_unmet = json.dumps(data.get("unmet_need", []))
    js_route_shapes = json.dumps([_clean_record(r) for r in data.get("route_shapes", [])])
    # Step 7: Original Route 27 GTFS data for the "Original Route 27" layer
    js_gtfs_r27_stops = json.dumps(data.get("gtfs_route27_stops", []))
    js_gtfs_r27_shape = json.dumps(data.get("gtfs_route27_shape", []))

    route_design_mode = (
        data.get("config", {})
        .get("optimization", {})
        .get("route_design", {})
        .get("mode", "derive_from_existing")
    )
    js_route_design_mode = json.dumps(route_design_mode)

    r76_line = json.dumps([
        [37.230,-121.978],[37.226,-121.979],[37.222,-121.981],[37.218,-121.984],
        [37.214,-121.988],[37.210,-121.993],[37.206,-121.998],[37.200,-122.004],
        [37.192,-122.010],[37.183,-122.018],[37.175,-122.025],[37.168,-122.030],
        [37.155,-122.040]
    ])

    nroutes = len(set(r["route_id"] for r in opt_routes)) if opt_routes else 0
    nstops = len(sel_stops)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Los Gatos Transit — Route Optimization</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,700&display=swap');
:root{{--bg:#0c0e12;--s1:#141720;--s2:#1c2030;--s3:#252a3a;--bd:#2a3050;--tx:#d8dce8;--tm:#7a8098;--ac:#4ecdc4;--red:#ff6b6b;--amber:#ffa94d;--green:#69db7c;--blue:#6c9bff;--font-display:'Source Serif 4',Georgia,serif;--font-mono:'IBM Plex Mono',monospace}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:var(--font-mono);background:var(--bg);color:var(--tx);line-height:1.5}}
h1,h2,h3{{font-family:var(--font-display);font-weight:700}}
.hero{{padding:28px 36px 20px;border-bottom:1px solid var(--bd);background:linear-gradient(135deg,var(--s1),var(--s2))}}
.hero h1{{font-size:26px;letter-spacing:-.5px}}.hero h1 em{{font-style:normal;color:var(--ac)}}
.hero p{{font-size:11px;color:var(--tm);margin-top:4px}}
.data-badges{{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}}
.badge{{font-size:9px;padding:3px 10px;border-radius:12px;font-weight:600}}
.badge.real{{background:rgba(78,205,196,.15);color:var(--ac);border:1px solid rgba(78,205,196,.3)}}
.badge.done{{color:var(--ac);border-color:rgba(78,205,196,.3);background:var(--s3)}}
.badge.phase{{background:var(--s3);color:var(--tm);border:1px solid var(--bd)}}
.nav-back{{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;background:var(--s2);border:1px solid var(--bd);border-radius:6px;color:var(--tm);text-decoration:none;font-size:10px;margin:12px 36px 0}}
.nav-back:hover{{color:var(--ac);border-color:rgba(78,205,196,.3)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--bd)}}
.grid>*{{background:var(--s1);padding:20px}}.grid .full{{grid-column:1/-1}}
.stitle{{font-size:13px;color:var(--ac);text-transform:uppercase;letter-spacing:2px;margin-bottom:12px;font-family:var(--font-mono);font-weight:600}}
.ssub{{font-size:10px;color:var(--tm);margin-top:-8px;margin-bottom:10px}}
#routeMap{{height:520px;border-radius:6px;border:1px solid var(--bd)}}
#route27Map{{height:500px;border-radius:6px;border:1px solid var(--bd);margin-bottom:14px}}
.hw-chip{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:9px;font-weight:700;margin:1px}}
.hw-fast{{background:rgba(105,219,124,.18);color:var(--green);border:1px solid rgba(105,219,124,.3)}}
.hw-med{{background:rgba(255,169,77,.12);color:var(--amber);border:1px solid rgba(255,169,77,.25)}}
.hw-slow{{background:rgba(255,107,107,.1);color:var(--red);border:1px solid rgba(255,107,107,.2)}}
.school-ok{{color:var(--ac);font-weight:700}}.school-miss{{color:var(--red);font-weight:700}}
.opt-empty{{text-align:center;padding:40px;color:var(--tm);font-size:11px;line-height:1.8}}
.route-legend{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}
.route-leg-item{{display:flex;align-items:center;gap:5px;font-size:9px;color:var(--tm)}}
.route-leg-swatch{{width:24px;height:3px;border-radius:2px}}
.stop-leg-item{{display:flex;align-items:center;gap:5px;font-size:9px;color:var(--tm)}}
.stop-leg-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.mrow{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}}
.metric{{background:var(--s2);border:1px solid var(--bd);border-radius:7px;padding:10px 14px;flex:1;min-width:110px}}
.metric .lb{{font-size:8px;color:var(--tm);text-transform:uppercase;letter-spacing:1px;margin-bottom:3px}}
.metric .vl{{font-size:20px;font-family:var(--font-display);font-weight:700}}
.metric .vl.ac{{color:var(--ac)}}.metric .vl.rd{{color:var(--red)}}
.metric .su{{font-size:8px;color:var(--tm);margin-top:1px}}
.cw{{position:relative;height:280px;margin-top:6px}}.cw.tall{{height:330px}}
table{{width:100%;border-collapse:collapse;font-size:10px}}
th{{text-align:left;padding:5px 6px;border-bottom:2px solid var(--bd);color:var(--tm);font-size:8px;text-transform:uppercase;letter-spacing:1px}}
td{{padding:4px 6px;border-bottom:1px solid rgba(42,48,80,.4)}}tr:hover td{{background:var(--s2)}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
.leaflet-popup-content-wrapper{{background:var(--s2)!important;color:var(--tx)!important;border:1px solid var(--bd)!important;border-radius:6px!important;font-family:var(--font-mono)!important;font-size:10px!important}}
.leaflet-popup-tip{{background:var(--s2)!important}}
.callout{{border-radius:7px;padding:12px 16px;margin-bottom:14px;font-size:10px;line-height:1.6}}
.callout.warn{{background:rgba(255,169,77,.07);border:1px solid rgba(255,169,77,.25)}}
.callout.info{{background:rgba(78,205,196,.05);border:1px solid rgba(78,205,196,.2)}}
.callout strong{{color:var(--amber)}}.callout.info strong{{color:var(--ac)}}
.hub-note{{font-size:9px;color:var(--tm);background:rgba(108,155,255,.07);border:1px solid rgba(108,155,255,.2);border-radius:5px;padding:6px 10px;margin-top:8px;line-height:1.5}}
@media(max-width:900px){{.grid{{grid-template-columns:1fr}}.grid>*{{grid-column:1/-1}}}}
</style>
</head>
<body>
<div class="hero">
<h1>Los Gatos Transit &mdash; <em>Route Optimization</em></h1>
<p>Phase B outputs: Clarke-Wright routing, Mohring headways, FTA 9040.1G stop spacing, Route 27 BCR analysis.</p>
<div class="data-badges">
<span class="badge {'done' if opt_run else 'phase'}">Phase B {'&#10003; ' + str(nroutes) + ' routes, ' + str(nstops) + ' stops' if opt_run else 'not yet run'}</span>
<span class="badge {'done' if r27_run else 'phase'}">Rt27 {'&#10003; ' + str(r27_new_count) + ' new stops (' + str(r27_high_count) + ' HIGH)' if r27_run else 'pending'}</span>
</div>
</div>
<a class="nav-back" href="cba_dashboard.html">&larr; Back to Cost-Benefit Analysis Dashboard</a>
{'<div class="callout warn" style="margin:12px 36px 0"><strong>Phase B not yet run:</strong> Execute <code>python run_analysis.py</code> to generate routing results.</div>' if not opt_run else ''}
<div class="grid">

<div class="full">
<div class="stitle">Optimised Route Network</div>
{'<div class="ssub">Derived from existing VTA routes. Route 27 is the primary spine; other anchors modified with new/candidate stops.</div>' if route_design_mode == 'derive_from_existing' else ('<div class="ssub">Corridor clustering. No fixed hub. Routes follow dominant geographic direction per district cluster.</div>' if route_design_mode == 'corridor' else '<div class="ssub">Clarke-Wright savings algorithm. Routes radiate from Winchester Transit Center (hub). Teal = existing stop, Orange = new stop, Red = school stop.</div>')}
{'<div class="hub-note"><strong style="color:var(--blue)">Hub: Winchester Transit Center</strong> &mdash; All optimised routes originate here to connect with VTA light rail. This hub-and-spoke design is intentional (Clarke-Wright §3.1). Routes diverge from this single transfer point to serve different corridors.</div>' if route_design_mode == 'hub_spoke' else ''}
<div id="routeMap" style="margin-top:10px"></div>
<div class="route-legend" id="routeLegend" style="margin-top:12px;padding-top:10px;border-top:1px solid var(--bd)">
  <div class="stop-leg-item"><div class="stop-leg-dot" style="background:#4ecdc4;border:2px solid #fff"></div>Existing stop</div>
  <div class="stop-leg-item"><div class="stop-leg-dot" style="background:#ffa94d;border:2px solid #fff"></div>New stop</div>
  <div class="stop-leg-item"><div class="stop-leg-dot" style="background:#ff6b6b;border:2px solid #fff;width:14px;height:14px"></div>School stop</div>
  <div class="stop-leg-item"><div class="stop-leg-dot" style="background:#6c9bff;border:3px solid #fff;width:16px;height:16px"></div>Winchester Hub (VTA)</div>
</div>
<div class="mrow" id="routeMetrics" style="margin-top:14px"></div>
</div>

<div>
<div class="stitle">Schedule Summary</div>
<div class="ssub">Headways (minutes) by time window per Mohring (1972) formula. 15 min = green, 30 min = amber, 60 min = red.</div>
<div id="schedulePanel"></div>
</div>

<div>
<div class="stitle">Estimated Ridership &amp; Demand</div>
<div class="ssub">Daily boardings by route (TDI-weighted population model). School demand from student survey.</div>
<div class="cw tall"><canvas id="cRidership"></canvas></div>
<div id="schoolDemandPanel" style="margin-top:14px"></div>
</div>

<div class="full">
<div class="stitle">District Demand Index (TDI)</div>
<div class="ssub">Transit Demand Index per district. High = priority service area. Composite: pop density, zero-vehicle HH, transit share, income, age dependence, employment.</div>
<div class="cw tall"><canvas id="cTDI"></canvas></div>
</div>

<div class="full">
<div class="stitle">District O-D Demand Profile</div>
<div class="ssub">Gravity model trip production and attraction per district.</div>
<div class="cw"><canvas id="cDemand"></canvas></div>
</div>

<div class="full">
<div class="stitle">Route 27 Stop Optimization &amp; New Stop Suggestions</div>
<div class="ssub">Linear spacing per FTA 9040.1G §5.2.2. BCR: USDOT BCA 2024 / TCRP Report 167 / NTD FY2023 / OMB A-94 3.5%.
  <strong style="color:var(--ac)">Teal = existing | Orange = suggested NEW (BCR ≥ 1) | Red = HIGH priority (BCR ≥ 2)</strong></div>
{'<div class="callout warn" style="margin-bottom:12px"><strong>Route 27 not yet run.</strong> Execute <code>python run_analysis.py</code>.</div>' if not r27_run else ''}
<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">
  <div class="metric"><div class="lb">Stops in sequence</div><div class="vl ac" id="r27TotalStops">—</div><div class="su">optimized corridor</div></div>
  <div class="metric"><div class="lb">Existing retained</div><div class="vl" id="r27ExistingStops">—</div></div>
  <div class="metric"><div class="lb">New suggestions</div><div class="vl" style="color:var(--amber)" id="r27NewStops">—</div><div class="su">gap-fill candidates</div></div>
  <div class="metric"><div class="lb">HIGH priority</div><div class="vl" style="color:var(--red)" id="r27HighStops">—</div><div class="su">BCR ≥ 2.0</div></div>
  <div class="metric"><div class="lb">Corridor length</div><div class="vl" id="r27Miles">—</div><div class="su">Winchester → Meridian</div></div>
</div>
<div id="route27Map"></div>
<div id="route27SuggTable" style="margin-top:14px"></div>
</div>

</div>
<script>
const OPT_ROUTES={js_opt_routes};
const SEL_STOPS={js_selected_stops};
const ROUTE_SHAPES={js_route_shapes};
const DIST_DEMAND={js_district_demand};
const SCHOOL_DEMAND={js_school_demand};
const SCHOOL_COV={js_school_coverage};
const GTFS_SUMMARY={js_gtfs_summary};
const R27_SUGGESTIONS={js_r27_suggestions};
const R27_GEOJSON={js_r27_geojson};
const TDI_DATA={js_tdi};
const UNMET_NEED={js_unmet};
const ROUTE_DESIGN_MODE={js_route_design_mode};
// Step 7: Original Route 27 GTFS data
const GTFS_R27_STOPS={js_gtfs_r27_stops};
const GTFS_R27_SHAPE={js_gtfs_r27_shape};

Chart.defaults.color='#7a8098';Chart.defaults.borderColor='rgba(42,48,80,.5)';
Chart.defaults.font.family="'IBM Plex Mono',monospace";Chart.defaults.font.size=10;

// ====================================================
// ROUTE MAP
// ====================================================
(function(){{
  const ROUTE_COLORS=['#4ecdc4','#ff6b6b','#ffa94d','#69db7c','#6c9bff','#cc5de8','#ff7eb3'];
  const HW_WINDOWS=['am_peak','midday','pm_school','pm_commute','evening'];
  const HW_LABELS={{'am_peak':'AM Peak','midday':'Midday','pm_school':'PM School','pm_commute':'PM Commute','evening':'Evening'}};

  const rmEl=document.getElementById('routeMap');
  if(!rmEl)return;
  const rmap=L.map('routeMap',{{center:[37.235,-121.960],zoom:13}});
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',{{maxZoom:19}}).addTo(rmap);

  // Step 7: Three Leaflet layerGroups for map layer control
  const optLayer=L.layerGroup();    // Optimized Route 27 (ON by default)
  const origLayer=L.layerGroup();   // Original Route 27 from GTFS (OFF by default)
  const schoolLayer=L.layerGroup(); // School stops (ON by default)

  // Group routes and draw polylines into optLayer
  const routeGroups={{}};
  OPT_ROUTES.forEach(r=>{{
    if(!routeGroups[r.route_id])routeGroups[r.route_id]=[];
    routeGroups[r.route_id].push(r);
  }});

  // Group road-following polylines by route_id (Phase 3 — uses OSM shortest-
  // path geometry rather than straight lines between stops).
  const optShapeMap={{}};
  (ROUTE_SHAPES||[]).forEach(p=>{{
    if(!optShapeMap[p.route_id])optShapeMap[p.route_id]=[];
    optShapeMap[p.route_id].push([+p.lat,+p.lon]);
  }});

  let ri=0;
  const routeEntries=Object.entries(routeGroups);
  routeEntries.forEach(([rid,stops])=>{{
    stops.sort((a,b)=>a.stop_sequence-b.stop_sequence);
    const color=ROUTE_COLORS[ri%ROUTE_COLORS.length];
    // Prefer road-following polyline; fall back to straight-line through stops.
    const latlngs=(optShapeMap[rid]&&optShapeMap[rid].length>=2)
      ? optShapeMap[rid]
      : stops.map(s=>[s.stop_lat,s.stop_lon]);
    if(latlngs.length>=2){{
      L.polyline(latlngs,{{color,weight:3,opacity:.85,dashArray:stops[0].is_restoration?'8,4':null}})
        .bindPopup('<b>'+rid+'</b>: '+(stops[0].route_name||rid)+'<br>'+stops.length+' stops'+(stops[0].parent_route_id?'<br><small style="color:var(--tm)">Derived from VTA route '+stops[0].parent_route_id+'</small>':'')+(ROUTE_DESIGN_MODE==='hub_spoke'?'<br><small style="color:var(--tm)">Originates at Winchester Hub (VTA connection)</small>':''))
        .addTo(optLayer);
    }}
    const leg=document.getElementById('routeLegend');
    if(leg){{
      const item=document.createElement('div');
      item.className='route-leg-item';
      item.innerHTML='<div class="route-leg-swatch" style="background:'+color+'"></div>'+(stops[0].route_name||rid);
      leg.appendChild(item);
    }}
    ri++;
  }});

  // Plot selected stops — non-school into optLayer, school into schoolLayer
  SEL_STOPS.forEach(s=>{{
    // Winchester hub highlight only in hub_spoke mode
    const isHub=ROUTE_DESIGN_MODE==='hub_spoke'&&s.stop_name&&s.stop_name.toLowerCase().includes('winchester');
    const color=isHub?'#6c9bff':(s.is_school_stop?'#ff6b6b':(s.is_existing?'#4ecdc4':'#ffa94d'));
    const radius=isHub?12:(s.is_school_stop?8:6);
    const weight=isHub?3:1.5;
    const circle=L.circleMarker([s.stop_lat,s.stop_lon],{{
      radius,color:'#fff',weight,fillColor:color,fillOpacity:.9
    }});
    let pop='<b>'+(s.stop_name||s.stop_id)+'</b>';
    if(isHub)pop+='<br><span style="color:#6c9bff;font-weight:700">&#9679; Winchester Transit Hub (VTA Connection)</span>';
    pop+='<br>District: '+(s.district_id||'-');
    pop+='<br>Type: '+(isHub?'<span style="color:#6c9bff">Hub / Transfer Point</span>':(s.is_school_stop?'<span style="color:#ff6b6b">School</span>':(s.is_existing?'Existing':'<span style="color:#ffa94d">New</span>')));
    pop+='<br>Demand score: '+(s.demand_score||0).toFixed(3);
    if(s.wheelchair_boarding)pop+='<br>ADA accessible';
    pop+='<br><a href="placards/'+(s.stop_id||'')+'.html" target="_blank" style="color:var(--ac)">View rider placard</a>';
    circle.bindPopup(pop);
    if(isHub)circle.bindTooltip('Winchester Hub',{{permanent:false,direction:'top'}});
    if(s.is_school_stop){{
      circle.addTo(schoolLayer);
    }}else{{
      circle.addTo(optLayer);
    }}
  }});

  // Step 7: Original Route 27 layer (dashed gray polyline + gray stop markers)
  if(GTFS_R27_SHAPE&&GTFS_R27_SHAPE.length>=2){{
    const origLatlngs=GTFS_R27_SHAPE.map(p=>[+p.lat,+p.lon]);
    L.polyline(origLatlngs,{{color:'#888',weight:2,opacity:.65,dashArray:'6,4'}})
      .bindPopup('<b>Original VTA Route 27</b><br>Pre-optimization GTFS shape')
      .addTo(origLayer);
  }}
  (GTFS_R27_STOPS||[]).forEach(s=>{{
    L.circleMarker([s.stop_lat,s.stop_lon],{{
      radius:5,color:'#888',weight:1.5,fillColor:'#aaa',fillOpacity:.7
    }}).bindPopup('<b>'+(s.stop_name||s.stop_id)+'</b><br><span style="color:#888">Original Route 27 stop</span>')
      .addTo(origLayer);
  }});

  // Add optLayer and schoolLayer to map by default; origLayer off by default
  optLayer.addTo(rmap);
  schoolLayer.addTo(rmap);

  // Step 7: Layer control with three distinct layerGroups
  L.control.layers({{}},{{
    'Optimized Route 27': optLayer,
    'Original Route 27': origLayer,
    'School Stops': schoolLayer,
  }},{{collapsed:false}}).addTo(rmap);

  // Metrics bar
  const nRoutes=routeEntries.length;
  const nNew=SEL_STOPS.filter(s=>!s.is_existing).length;
  const nSchool=SEL_STOPS.filter(s=>s.is_school_stop).length;
  const nTotal=SEL_STOPS.length;
  const gsRow=(GTFS_SUMMARY||[]).find(r=>r.metric==='Trips/day')||{{}};
  const nTrips=gsRow.value||'–';
  const metricsEl=document.getElementById('routeMetrics');
  if(metricsEl){{
    const items=[
      ['Routes',''+nRoutes,''],
      ['Total stops',nTotal,''],
      ['New stops',nNew,'color:var(--amber)'],
      ['School stops',nSchool,'color:var(--red)'],
      ['Trips/day',nTrips,''],
    ];
    metricsEl.innerHTML=items.map(([lb,vl,st])=>
      '<div class="metric"><div class="lb">'+lb+'</div><div class="vl" style="'+st+'">'+vl+'</div></div>'
    ).join('');
  }}

  // Schedule panel
  const schedEl=document.getElementById('schedulePanel');
  if(schedEl){{
    if(!OPT_ROUTES.length){{
      schedEl.innerHTML='<div class="opt-empty">No schedule data.<br>Run <code>python run_analysis.py</code>.</div>';
    }} else {{
      const routeMap={{}};
      OPT_ROUTES.forEach(r=>{{if(!routeMap[r.route_id])routeMap[r.route_id]=r;}});
      const routes=Object.values(routeMap);
      function hwChip(min){{
        if(!min||min===0)return '<span style="color:var(--tm)">–</span>';
        const cls=min<=15?'hw-fast':min<=30?'hw-med':'hw-slow';
        return '<span class="hw-chip '+cls+'">'+min+'</span>';
      }}
      let h='<table><tr><th>Route</th>';
      HW_WINDOWS.forEach(w=>h+='<th style="text-align:center">'+HW_LABELS[w]+'</th>');
      h+='<th style="text-align:right">Diversion</th><th style="text-align:right">BCR</th></tr>';
      routes.forEach(r=>{{
        h+='<tr><td style="font-weight:600">'+(r.route_name||r.route_id)+'</td>';
        HW_WINDOWS.forEach(w=>h+='<td style="text-align:center">'+hwChip(r['headway_'+w])+'</td>');
        const div=r.diversion_rate!=null?(parseFloat(r.diversion_rate)*100).toFixed(1)+'%':'8.0%';
        h+='<td class="n" style="color:var(--tm)">'+div+'</td>';
        const bcr=r.bcr!=null?parseFloat(r.bcr).toFixed(2):'–';
        const bcrColor=parseFloat(bcr)>=1?'var(--ac)':'var(--red)';
        h+='<td class="n" style="color:'+bcrColor+'">'+bcr+'</td></tr>';
      }});
      h+='</table>';
      if(SCHOOL_COV.length){{
        h+='<div style="margin-top:14px;font-size:10px;color:var(--ac);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">School Trip Coverage</div>';
        h+='<table><tr><th>School</th><th>Dismissal</th><th>Window</th><th style="text-align:center">Covered</th><th>Earliest arrival</th></tr>';
        SCHOOL_COV.forEach(c=>{{
          const ok=c.covered===true||c.covered==='True'||c.covered==='true';
          h+='<tr><td>'+(c.school||'–')+'</td><td>'+(c.dismissal_time||'–')+'</td>';
          h+='<td>'+(c.pickup_window_min||10)+' min</td>';
          h+='<td style="text-align:center"><span class="'+(ok?'school-ok':'school-miss')+'">'+(ok?'✓ Yes':'✗ Miss')+'</span></td>';
          h+='<td class="n">'+(c.actual_arrival||'–')+'</td></tr>';
        }});
        h+='</table>';
      }}
      schedEl.innerHTML=h;
    }}
  }}

  // School demand panel
  const sdEl=document.getElementById('schoolDemandPanel');
  if(sdEl&&SCHOOL_DEMAND.length){{
    let h='<div style="font-size:10px;color:var(--ac);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">School Boarding Estimates</div>';
    h+='<table><tr><th>School</th><th>Dismissal</th><th style="text-align:right">Boardings/day</th><th>Diversion</th></tr>';
    SCHOOL_DEMAND.forEach(sd=>{{
      h+='<tr><td>'+(sd.school||'–').replace(/_/g,' ')+'</td>';
      h+='<td>'+(sd.dismissal_time||'–')+'</td>';
      h+='<td class="n" style="color:var(--ac);font-weight:700">'+(sd.estimated_boardings||0)+'</td>';
      h+='<td class="n">'+(sd.diversion_rate_used!=null?(sd.diversion_rate_used*100).toFixed(0)+'%':'–')+'</td></tr>';
    }});
    h+='</table>';
    sdEl.innerHTML=h;
  }}

  // Ridership chart — uses estimated_daily_boardings from route optimizer (NHTS trip rate + ACS mode share)
  const rCanvas=document.getElementById('cRidership');
  if(rCanvas&&OPT_ROUTES.length){{
    const routeScore={{}};const routeName={{}};
    OPT_ROUTES.forEach(r=>{{
      routeScore[r.route_id]=(routeScore[r.route_id]||0)+(r.demand_score||0);
      routeName[r.route_id]=r.route_name||r.route_id;
    }});
    const entries=Object.entries(routeScore).sort((a,b)=>b[1]-a[1]);
    const labels=entries.map(([id])=>routeName[id]||id);
    const schoolTotal=SCHOOL_DEMAND.reduce((s,sd)=>s+(sd.estimated_boardings||0),0);
    const routeBoardings={{}};
    OPT_ROUTES.forEach(r=>{{
      routeBoardings[r.route_id]=(routeBoardings[r.route_id]||0)+(r.estimated_daily_boardings||0);
    }});
    const vals=entries.map(([id])=>Math.round(routeBoardings[id]||0));
    new Chart(rCanvas,{{
      type:'bar',
      data:{{
        labels,
        datasets:[
          {{label:'Est. Regular Boardings/day',data:vals,backgroundColor:entries.map((_,i)=>ROUTE_COLORS[i%ROUTE_COLORS.length]+'bb'),borderRadius:4}},
        ]
      }},
      options:{{
        responsive:true,maintainAspectRatio:false,
        plugins:{{
          legend:{{labels:{{color:'#7a8098',font:{{size:10}}}}}},
          title:{{display:true,text:'Estimated Daily Boardings by Route (NHTS trip rate + ACS mode share)',color:'#7a8098',font:{{size:10}}}}
        }},
        scales:{{
          x:{{ticks:{{color:'#7a8098',font:{{size:9}}}},grid:{{color:'rgba(42,48,80,.3)'}}}},
          y:{{ticks:{{color:'#7a8098',font:{{size:9}}}},grid:{{color:'rgba(42,48,80,.3)'}},
             title:{{display:true,text:'Boardings/day',color:'#7a8098',font:{{size:9}}}}}},
        }}
      }}
    }});
  }}

  // TDI chart
  const tdiCanvas=document.getElementById('cTDI');
  if(tdiCanvas&&TDI_DATA.length){{
    const td=TDI_DATA.slice().sort((a,b)=>b.tdi-a.tdi);
    const unmetMap={{}};
    UNMET_NEED.forEach(u=>{{unmetMap[u.district_id]=u.unmet_need||0;}});
    new Chart(tdiCanvas,{{
      type:'bar',
      data:{{
        labels:td.map(d=>d.district_id),
        datasets:[
          {{label:'TDI (demand)',data:td.map(d=>+(d.tdi||0).toFixed(3)),backgroundColor:'rgba(78,205,196,.75)',borderRadius:3}},
          {{label:'Unmet Need (TDI − SLI)',data:td.map(d=>+(unmetMap[d.district_id]||0).toFixed(3)),backgroundColor:'rgba(255,107,107,.65)',borderRadius:3}},
        ]
      }},
      options:{{
        responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{labels:{{color:'#7a8098',font:{{size:10}}}}}}}},
        scales:{{
          x:{{ticks:{{color:'#7a8098',font:{{size:9}}}},grid:{{color:'rgba(42,48,80,.3)'}}}},
          y:{{max:1,ticks:{{color:'#7a8098',font:{{size:9}}}},grid:{{color:'rgba(42,48,80,.3)'}},
             title:{{display:true,text:'Score (0–1)',color:'#7a8098',font:{{size:9}}}}}},
        }}
      }}
    }});
  }}

  // District demand chart
  const dCanvas=document.getElementById('cDemand');
  if(dCanvas&&DIST_DEMAND.length){{
    const dd=DIST_DEMAND.slice(0,16);
    new Chart(dCanvas,{{
      type:'bar',
      data:{{
        labels:dd.map(d=>d.district_id||d.index||''),
        datasets:[
          {{label:'Trip Productions',data:dd.map(d=>+(d.total_productions||0).toFixed(1)),backgroundColor:'rgba(78,205,196,.7)',borderRadius:3}},
          {{label:'Trip Attractions',data:dd.map(d=>+(d.total_attractions||0).toFixed(1)),backgroundColor:'rgba(108,155,255,.7)',borderRadius:3}},
        ]
      }},
      options:{{
        responsive:true,maintainAspectRatio:false,
        plugins:{{legend:{{labels:{{color:'#7a8098',font:{{size:10}}}}}}}},
        scales:{{
          x:{{stacked:false,ticks:{{color:'#7a8098',font:{{size:9}}}},grid:{{color:'rgba(42,48,80,.3)'}}}},
          y:{{ticks:{{color:'#7a8098',font:{{size:9}}}},grid:{{color:'rgba(42,48,80,.3)'}},
             title:{{display:true,text:'Estimated daily trips',color:'#7a8098',font:{{size:9}}}}}},
        }}
      }}
    }});
  }}
}})();

// ====================================================
// ROUTE 27 STOP SUGGESTIONS MAP + TABLE
// ====================================================
(function(){{
  const mapEl=document.getElementById('route27Map');
  if(!mapEl)return;
  const rmap=L.map('route27Map',{{center:[37.237,-121.955],zoom:13}});
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',{{maxZoom:19}}).addTo(rmap);

  if(R27_GEOJSON&&R27_GEOJSON.features){{
    R27_GEOJSON.features.forEach(f=>{{
      if(f.geometry.type==='LineString'){{
        const coords=f.geometry.coordinates.map(c=>[c[1],c[0]]);
        L.polyline(coords,{{color:'#4ecdc4',weight:4,opacity:.7}})
          .bindPopup('<b>Route 27 corridor</b>'+(f.properties.total_length_ft?'<br>Length: '+(f.properties.total_length_ft/5280).toFixed(2)+' mi':''))
          .addTo(rmap);
      }}
    }});
    R27_GEOJSON.features.forEach(f=>{{
      if(f.geometry.type==='Point'&&f.properties.type==='anchor_waypoint'){{
        const [lon,lat]=f.geometry.coordinates;
        L.circleMarker([lat,lon],{{radius:4,color:'#4ecdc4',fillColor:'#4ecdc4',fillOpacity:.5,weight:1}})
          .bindTooltip('<b>Anchor</b>: '+f.properties.name)
          .addTo(rmap);
      }}
    }});
  }}

  // P1.11: route27_optimizer emits NEW_SUGGEST (gap-fill, full BCR) and
  // NEW_IN_SELECTION (FTA-spacing pick, no BCR).  Treat both as "new" for
  // counting/styling; only show BCR popup section when BCR is populated.
  const isNewStatus=s=>s&&typeof s.status==='string'&&s.status.indexOf('NEW_')===0;
  let nTotal=0,nExist=0,nNew=0,nHigh=0,maxMi=0;
  R27_SUGGESTIONS.forEach(s=>{{
    if(s.stop_lat==null||s.stop_lon==null)return;
    nTotal++;
    const isNew=isNewStatus(s);
    const hasBcr=s.bcr_20yr!=null;
    const isHigh=isNew&&s.priority==='HIGH';
    const isMed=isNew&&s.priority==='MEDIUM';
    if(!isNew)nExist++;else nNew++;
    if(isHigh)nHigh++;
    if((s.s_coord_mi||0)>maxMi)maxMi=s.s_coord_mi;
    let color=isHigh?'#ff6b6b':isMed?'#ffa94d':isNew?'#ffa94d':'#4ecdc4';
    let radius=isHigh?10:isMed?8:isNew?7:6;
    let pop='<b>'+(s.stop_name||s.stop_id||'Stop')+'</b>';
    pop+='<br>Status: <span style="color:'+color+'">'+s.status+'</span>';
    pop+='<br>Priority: '+(s.priority||'—')+' | District: '+(s.district_id||'—');
    pop+='<br>Position: '+(s.s_coord_mi||0).toFixed(2)+' mi from Winchester TC';
    if(isNew&&hasBcr){{
      pop+='<hr style="border-color:rgba(120,128,160,.3);margin:5px 0">';
      pop+='<b style="color:var(--amber)">BCR Analysis (20 yr @ 3.5%)</b>';
      pop+='<br>Marginal walk-shed pop: '+(s.marginal_walkshed_pop||0).toLocaleString();
      pop+='<br>Est. new riders/day: '+(s.est_new_riders_daily||0).toFixed(1);
      pop+='<br>Annual benefit: $'+(s.annual_benefit_usd||0).toLocaleString();
      pop+='<br>Capital cost: $'+(s.capital_cost_usd||0).toLocaleString();
      pop+='<br><b>BCR: '+parseFloat(s.bcr_20yr).toFixed(2)+'</b>';
      pop+='<br>FTA CEI: $'+(s.fta_cei_per_user_hr!=null?parseFloat(s.fta_cei_per_user_hr).toFixed(2):'—')+'/user-hr';
      pop+='<br><small style="color:var(--tm)">'+((s.justification||'').slice(0,120))+'…</small>';
    }}else if(isNew){{
      pop+='<hr style="border-color:rgba(120,128,160,.3);margin:5px 0">';
      pop+='<small style="color:var(--tm)">Selected via FTA spacing algorithm; no BCR yet (gap-fill BCR runs only for spacing-violation gaps).</small>';
    }}
    if(s.wheelchair_boarding)pop+='<br>♿ ADA accessible';
    if(s.stop_id)pop+='<br><a href="placards/'+(s.stop_id||'')+'.html" target="_blank" style="color:var(--ac)">View rider placard →</a>';
    const marker=L.circleMarker([s.stop_lat,s.stop_lon],{{
      radius,color:'#fff',weight:isNew?2:1.5,fillColor:color,fillOpacity:.92
    }}).addTo(rmap);
    marker.bindPopup(pop,{{maxWidth:300}});
    if(isNew&&hasBcr){{
      L.marker([s.stop_lat,s.stop_lon],{{
        icon:L.divIcon({{className:'',
          html:'<div style="font:700 8px var(--font-mono);color:'+color+';text-shadow:0 1px 3px #000;white-space:nowrap">BCR '+parseFloat(s.bcr_20yr).toFixed(1)+'</div>',
          iconSize:[50,12],iconAnchor:[-4,6]}})
      }}).addTo(rmap);
    }}
  }});

  const setM=(id,v)=>{{const el=document.getElementById(id);if(el)el.textContent=v;}};
  setM('r27TotalStops',nTotal||'—');setM('r27ExistingStops',nExist||'—');
  setM('r27NewStops',nNew||'—');setM('r27HighStops',nHigh||'—');
  setM('r27Miles',maxMi>0?maxMi.toFixed(2)+' mi':'—');

  const legDiv=L.control({{position:'bottomright'}});
  legDiv.onAdd=function(){{
    const d=L.DomUtil.create('div');
    d.style.cssText='background:rgba(20,23,32,.92);border:1px solid rgba(42,48,80,.8);border-radius:6px;padding:10px 14px;font:10px IBM Plex Mono,monospace;color:#d8dce8';
    d.innerHTML='<div style="font-size:9px;color:#7a8098;margin-bottom:6px">Route 27 Legend</div>'
      +'<div style="display:flex;align-items:center;gap:7px;margin-bottom:4px"><div style="width:10px;height:10px;border-radius:50%;background:#4ecdc4;border:2px solid #fff"></div>Existing stop (EXISTING_KEEP)</div>'
      +'<div style="display:flex;align-items:center;gap:7px;margin-bottom:4px"><div style="width:12px;height:12px;border-radius:50%;background:#ffa94d;border:2px solid #fff"></div>New (NEW_IN_SELECTION / MEDIUM)</div>'
      +'<div style="display:flex;align-items:center;gap:7px"><div style="width:14px;height:14px;border-radius:50%;background:#ff6b6b;border:2px solid #fff"></div>New gap-fill (NEW_SUGGEST HIGH, BCR≥2)</div>';
    return d;
  }};
  legDiv.addTo(rmap);

  const tblEl=document.getElementById('route27SuggTable');
  if(!tblEl)return;
  if(!R27_SUGGESTIONS.length){{
    tblEl.innerHTML='<div class="opt-empty">No Route 27 data. Run <code>python run_analysis.py</code> with osmnx.</div>';
    return;
  }}
  const newStops=R27_SUGGESTIONS.filter(isNewStatus);
  let h='<div style="font-size:10px;color:var(--ac);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">New Stop Recommendations</div>';
  h+='<div style="font-size:9px;color:var(--tm);margin-bottom:10px">Diversion: 8% (TCRP 167) &bull; Value/boarding: $4.20 (USDOT BCA 2024) &bull; Discount: 3.5% (OMB A-94) &bull; Horizon: 20 yr &bull; BCR ≥ 1.0 = FTA-justified</div>';
  if(!newStops.length){{
    h+='<div style="color:var(--tm);font-size:10px;padding:14px">No new stops needed — all segments within FTA spacing limits.</div>';
    tblEl.innerHTML=h;return;
  }}
  h+='<div style="overflow-x:auto"><table>';
  h+='<tr><th>Position</th><th>Stop</th><th>District</th><th>Priority</th>'
    +'<th style="text-align:right">Marg. Pop</th><th style="text-align:right">Riders/day</th>'
    +'<th style="text-align:right">Ann. Benefit</th><th style="text-align:right">Capital</th>'
    +'<th style="text-align:right">BCR (20yr)</th><th style="text-align:right">FTA CEI</th>'
    +'<th>Gap (mi)</th></tr>';
  newStops.sort((a,b)=>(a.s_coord_ft||0)-(b.s_coord_ft||0)).forEach(s=>{{
    const bcr=s.bcr_20yr!=null?parseFloat(s.bcr_20yr):null;
    const bcrColor=bcr==null?'var(--tm)':bcr>=2?'var(--red)':bcr>=1?'var(--amber)':'var(--tm)';
    const priColor=s.priority==='HIGH'?'var(--red)':s.priority==='MEDIUM'?'var(--amber)':'var(--tm)';
    const cei=s.fta_cei_per_user_hr!=null?parseFloat(s.fta_cei_per_user_hr):null;
    const ceiColor=cei==null?'var(--tm)':cei<2?'var(--ac)':cei<4?'var(--amber)':'var(--red)';
    const gap=(s.gap_before_ft&&s.gap_after_ft)?((s.gap_before_ft/5280).toFixed(2)+'+'+( s.gap_after_ft/5280).toFixed(2)):'—';
    h+='<tr>';
    h+='<td class="n">'+(s.s_coord_mi||0).toFixed(2)+' mi</td>';
    h+='<td style="max-width:160px">'+(s.stop_name||s.stop_id||'—')+'</td>';
    h+='<td style="color:var(--tm)">'+(s.district_id||'—')+'</td>';
    h+='<td style="color:'+priColor+';font-weight:700">'+(s.priority||'—')+'</td>';
    h+='<td class="n">'+(s.marginal_walkshed_pop||0).toLocaleString()+'</td>';
    h+='<td class="n">'+(s.est_new_riders_daily||0).toFixed(1)+'</td>';
    h+='<td class="n">$'+(s.annual_benefit_usd||0).toLocaleString()+'</td>';
    h+='<td class="n">$'+(s.capital_cost_usd||0).toLocaleString()+'</td>';
    h+='<td class="n" style="color:'+bcrColor+';font-weight:700">'+(bcr!=null?bcr.toFixed(2):'—')+'</td>';
    h+='<td class="n" style="color:'+ceiColor+'">'+(cei!=null?'$'+cei.toFixed(2):'—')+'</td>';
    h+='<td style="color:var(--tm);font-size:9px">'+gap+'</td>';
    h+='</tr>';
  }});
  h+='</table></div>';
  h+='<div style="margin-top:8px;font-size:9px;color:rgba(122,128,152,.6)">BCR parameters: diversion=8% (TCRP 167), value=$4.20/boarding (USDOT BCA 2024), discount=3.5% real (OMB A-94), capital=$25K–$55K (NTD FY2023). FTA CEI: &lt;$2/user-hr = Medium-High; &lt;$4 = Medium.</div>';
  tblEl.innerHTML=h;
}})();
</script>
</body>
</html>"""

    return html


def generate_dashboard(output_path: str = "outputs/cba_dashboard.html") -> str:
    """Main entry point: load data, merge, generate both HTML pages.

    Generates two pages that cross-link:
      - cba_dashboard.html  (Phases A1-A4: costs, benefits, NPV, BCR)
      - route_optimization.html  (Phase B: routes, schedules, Route 27 BCR)

    Args:
        output_path: Where to write the CBA dashboard HTML.

    Returns:
        Path to the CBA dashboard file.
    """
    logger.info("Loading pipeline data...")
    data = load_pipeline_data()

    logger.info("Merging district data...")
    merged = merge_district_data(data)

    logger.info("Loading district polygons...")
    polygons = get_district_polygons()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Generating CBA dashboard HTML...")
    cba_html = generate_dashboard_html(data, merged, polygons)
    with open(out, "w", encoding="utf-8") as f:
        f.write(cba_html)
    logger.info("CBA dashboard saved to %s", out)

    logger.info("Generating route optimization HTML...")
    route_opt_path = out.parent / "route_optimization.html"
    route_html = generate_route_optimization_html(data)
    with open(route_opt_path, "w", encoding="utf-8") as f:
        f.write(route_html)
    logger.info("Route optimization page saved to %s", route_opt_path)

    return str(out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    path = generate_dashboard()
    print(f"\nDashboard generated: {path}")
    print("Open this file in your browser to view.")
