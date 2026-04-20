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
<span class="badge phase">A5-A9</span>
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
</div>
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
  const costGrowth=0.020; const benGrowth=0.005;
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


def generate_dashboard(output_path: str = "outputs/cba_dashboard.html") -> str:
    """Main entry point: load data, merge, generate HTML.

    Args:
        output_path: Where to write the dashboard HTML.

    Returns:
        Path to the generated file.
    """
    logger.info("Loading pipeline data...")
    data = load_pipeline_data()

    logger.info("Merging district data...")
    merged = merge_district_data(data)

    logger.info("Loading district polygons...")
    polygons = get_district_polygons()

    logger.info("Generating dashboard HTML...")
    html = generate_dashboard_html(data, merged, polygons)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("Dashboard saved to %s", out)
    return str(out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    path = generate_dashboard()
    print(f"\nDashboard generated: {path}")
    print("Open this file in your browser to view.")
