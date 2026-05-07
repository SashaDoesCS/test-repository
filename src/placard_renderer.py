"""
placard_renderer.py -- Generate rider-facing HTML placards per stop.

Per stop: route-colored header, Mermaid strip diagram (±2 stops),
Leaflet mini-map (parent route + nearby stops), weekday/weekend
timetable sourced from outputs/gtfs_optimised/, QR code footer.

Emits:
    outputs/placards/<stop_id>.html   — one page per stop
    outputs/placards/index.html       — filterable index
"""

import base64
import io
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Route colors matching the dashboard JS palette
_ROUTE_COLORS = [
    "#6c9bff", "#ff6b6b", "#f7b731", "#26de81",
    "#fc5c65", "#45aaf2", "#4b7bec", "#a55eea",
    "#fd9644", "#2bcbba",
]


def _make_qr_b64(url: str) -> str:
    """Return a base64-encoded PNG of a QR code for url, or '' on failure."""
    try:
        import qrcode  # type: ignore
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        logger.warning("QR code generation failed: %s", exc)
        return ""


def _load_gtfs(gtfs_dir: str) -> Dict[str, pd.DataFrame]:
    """Load the GTFS txt files we need into DataFrames."""
    base = Path(gtfs_dir)
    tables = {}
    for name in ("stops", "trips", "stop_times", "routes", "calendar", "shapes"):
        p = base / f"{name}.txt"
        if p.exists():
            try:
                tables[name] = pd.read_csv(p, dtype=str)
            except Exception as exc:
                logger.warning("Could not read %s: %s", p, exc)
                tables[name] = pd.DataFrame()
        else:
            tables[name] = pd.DataFrame()
    return tables


def _route_color(route_id: str, route_idx: int) -> str:
    return _ROUTE_COLORS[route_idx % len(_ROUTE_COLORS)]


def _stop_strip(stop_id: str, route_id: str, tables: Dict[str, pd.DataFrame]) -> str:
    """Return a Mermaid graph LR snippet showing ±2 stops around stop_id for route_id."""
    st = tables.get("stop_times", pd.DataFrame())
    tr = tables.get("trips", pd.DataFrame())
    if st.empty or tr.empty:
        return ""

    # Find a representative trip for this route
    route_trips = tr[tr.get("route_id", pd.Series(dtype=str)) == route_id]
    if route_trips.empty:
        return ""
    trip_id = route_trips.iloc[0]["trip_id"]

    # Get ordered stop sequence for that trip
    trip_st = st[st["trip_id"] == trip_id].copy()
    trip_st["stop_sequence"] = pd.to_numeric(trip_st["stop_sequence"], errors="coerce")
    trip_st = trip_st.sort_values("stop_sequence").reset_index(drop=True)

    stop_ids = trip_st["stop_id"].tolist()
    if stop_id not in stop_ids:
        return ""

    idx = stop_ids.index(stop_id)
    window = trip_st.iloc[max(0, idx - 2): idx + 3].copy()

    # Build stop name map
    stops_df = tables.get("stops", pd.DataFrame())
    name_map: Dict[str, str] = {}
    if not stops_df.empty and "stop_id" in stops_df.columns:
        name_map = stops_df.set_index("stop_id").get("stop_name", pd.Series()).to_dict()

    nodes = []
    for _, row in window.iterrows():
        sid = row["stop_id"]
        label = name_map.get(sid, sid)
        # Truncate long names
        label = label[:22] + "…" if len(label) > 22 else label
        label = label.replace('"', "'")
        if sid == stop_id:
            nodes.append(f'  THIS["**{label}**"]')
        else:
            seq = int(row.get("stop_sequence", 0))
            nodes.append(f'  s{seq}["{label}"]')

    if not nodes:
        return ""

    # Build chain with arrows
    node_ids = []
    for i, row in enumerate(window.itertuples()):
        if row.stop_id == stop_id:
            node_ids.append("THIS")
        else:
            node_ids.append(f"s{int(row.stop_sequence)}")

    chain = " --> ".join(node_ids)
    return f"graph LR\n{chr(10).join(nodes)}\n  {chain}"


def _build_timetable(stop_id: str, route_id: str, tables: Dict[str, pd.DataFrame]) -> str:
    """Return an HTML table of departures for this stop grouped by weekday/weekend."""
    st = tables.get("stop_times", pd.DataFrame())
    tr = tables.get("trips", pd.DataFrame())
    cal = tables.get("calendar", pd.DataFrame())
    if st.empty or tr.empty:
        return "<p style='color:#7a8098;font-size:10px'>No schedule data available.</p>"

    route_trips = tr[tr.get("route_id", pd.Series(dtype=str)) == route_id]["trip_id"].tolist()
    stop_rows = st[(st["stop_id"] == stop_id) & (st["trip_id"].isin(route_trips))].copy()
    if stop_rows.empty:
        return "<p style='color:#7a8098;font-size:10px'>No departures found for this route/stop.</p>"

    # Join service_id from trips
    service_map = tr.set_index("trip_id")["service_id"].to_dict() if "service_id" in tr.columns else {}
    stop_rows["service_id"] = stop_rows["trip_id"].map(service_map)

    # Classify weekday vs weekend via calendar
    weekday_sids: set = set()
    weekend_sids: set = set()
    if not cal.empty and "service_id" in cal.columns:
        for _, crow in cal.iterrows():
            sid = crow["service_id"]
            wd = any(str(crow.get(d, "0")) == "1" for d in ("monday", "tuesday", "wednesday", "thursday", "friday"))
            we = any(str(crow.get(d, "0")) == "1" for d in ("saturday", "sunday"))
            if wd:
                weekday_sids.add(sid)
            if we:
                weekend_sids.add(sid)

    def _times(sids: set) -> List[str]:
        rows = stop_rows[stop_rows["service_id"].isin(sids)] if sids else stop_rows
        times = rows["departure_time"].dropna().sort_values().tolist()
        # Normalize times > 24h (GTFS wraps overnight)
        normalized = []
        for t in times:
            parts = str(t).split(":")
            if len(parts) == 3:
                h = int(parts[0]) % 24
                normalized.append(f"{h:02d}:{parts[1]}")
            else:
                normalized.append(str(t))
        return normalized

    wd_times = _times(weekday_sids)
    we_times = _times(weekend_sids)
    max_rows = max(len(wd_times), len(we_times), 1)

    rows_html = ""
    for i in range(max_rows):
        wd = wd_times[i] if i < len(wd_times) else ""
        we = we_times[i] if i < len(we_times) else ""
        bg = "rgba(42,48,80,.3)" if i % 2 == 0 else "transparent"
        rows_html += f'<tr style="background:{bg}"><td style="padding:2px 8px">{wd}</td><td style="padding:2px 8px">{we}</td></tr>'

    return f"""
<table style="border-collapse:collapse;font-size:10px;width:100%;color:#d8dce8">
  <thead><tr style="color:#7a8098;border-bottom:1px solid rgba(42,48,80,.6)">
    <th style="padding:3px 8px;text-align:left">Weekday</th>
    <th style="padding:3px 8px;text-align:left">Weekend</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>"""


def _placard_html(
    stop_id: str,
    stop_name: str,
    stop_lat: float,
    stop_lon: float,
    wheelchair_boarding: int,
    district_id: str,
    is_existing: bool,
    route_ids: List[str],
    parent_route_lookup: Dict[str, str],
    tables: Dict[str, pd.DataFrame],
    route_color_map: Dict[str, str],
    qr_b64: str,
) -> str:
    """Render a single stop placard as an HTML string."""
    # Top band: route pills
    route_pills = ""
    for rid in route_ids:
        color = route_color_map.get(rid, "#6c9bff")
        parent = parent_route_lookup.get(rid, "")
        headsign = ""
        tr = tables.get("trips", pd.DataFrame())
        if not tr.empty and "route_id" in tr.columns and "trip_headsign" in tr.columns:
            match = tr[tr["route_id"] == rid]["trip_headsign"].dropna()
            headsign = match.iloc[0] if not match.empty else ""
        label = f"Route {rid}" + (f" → {headsign}" if headsign else "")
        if parent:
            label += f" (derived from VTA {parent})"
        route_pills += (
            f'<span style="background:{color}22;border:1px solid {color};border-radius:4px;'
            f'padding:2px 8px;color:{color};font-size:11px;font-weight:700;margin-right:6px">'
            f'&#9679; {label}</span>'
        )

    # Mermaid diagram — first route
    mermaid_src = ""
    if route_ids:
        mermaid_src = _stop_strip(stop_id, route_ids[0], tables)

    # Timetable — first route
    timetable_html = ""
    if route_ids:
        timetable_html = _build_timetable(stop_id, route_ids[0], tables)

    # QR image
    qr_html = ""
    if qr_b64:
        qr_html = f'<img src="data:image/png;base64,{qr_b64}" style="width:80px;height:80px;border-radius:4px" alt="QR">'
    else:
        qr_html = '<div style="width:80px;height:80px;background:rgba(42,48,80,.4);border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:9px;color:#7a8098">QR N/A</div>'

    ada_glyph = "♿ ADA accessible" if wheelchair_boarding == 1 else ""
    stop_type = "Existing stop" if is_existing else '<span style="color:#ffa94d">New stop</span>'

    # Nearby stops for mini-map (up to ±5 from sequence)
    nearby_json = "[]"
    tr = tables.get("trips", pd.DataFrame())
    st = tables.get("stop_times", pd.DataFrame())
    stops_df = tables.get("stops", pd.DataFrame())
    if route_ids and not tr.empty and not st.empty and not stops_df.empty:
        route_trips = tr[tr.get("route_id", pd.Series(dtype=str)) == route_ids[0]]
        if not route_trips.empty:
            trip_id = route_trips.iloc[0]["trip_id"]
            trip_st = st[st["trip_id"] == trip_id].copy()
            trip_st["stop_sequence"] = pd.to_numeric(trip_st["stop_sequence"], errors="coerce")
            trip_st = trip_st.sort_values("stop_sequence").reset_index(drop=True)
            sids = trip_st["stop_id"].tolist()
            if stop_id in sids:
                idx = sids.index(stop_id)
                window_sids = sids[max(0, idx - 5): idx + 6]
                stop_locs = stops_df.set_index("stop_id")[["stop_lat", "stop_lon", "stop_name"]].to_dict("index")
                nearby = []
                for sid in window_sids:
                    if sid in stop_locs:
                        info = stop_locs[sid]
                        nearby.append({
                            "id": sid,
                            "name": info.get("stop_name", sid),
                            "lat": float(info.get("stop_lat", 0)),
                            "lon": float(info.get("stop_lon", 0)),
                            "is_this": sid == stop_id,
                        })
                nearby_json = json.dumps(nearby)

    mermaid_block = ""
    if mermaid_src:
        mermaid_block = f"""
<div style="margin:16px 0">
  <div style="font-size:10px;color:#7a8098;margin-bottom:6px">Stop sequence</div>
  <div class="mermaid" style="font-size:10px">{mermaid_src}</div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stop Placard — {stop_name}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#141720;color:#d8dce8;font-family:IBM Plex Mono,Consolas,monospace;font-size:12px;padding:16px;max-width:720px;margin:0 auto}}
  .card{{background:rgba(20,23,40,.9);border:1px solid rgba(42,48,80,.8);border-radius:8px;padding:16px;margin-bottom:12px}}
  h2{{font-size:15px;font-weight:700;color:#fff;margin-bottom:8px}}
  .section-label{{font-size:9px;color:#7a8098;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}}
  #mini-map{{height:220px;border-radius:6px;border:1px solid rgba(42,48,80,.8)}}
  a{{color:#6c9bff;text-decoration:none}}
  a:hover{{text-decoration:underline}}
</style>
</head>
<body>
<div style="margin-bottom:10px">
  <a href="index.html" style="font-size:10px;color:#7a8098">← All placards</a>
  &nbsp;|&nbsp;
  <a href="../route_optimization.html" style="font-size:10px;color:#7a8098">Route dashboard →</a>
</div>

<!-- Header band -->
<div class="card">
  <div class="section-label">Routes serving this stop</div>
  <div style="margin-bottom:10px">{route_pills or '<span style="color:#7a8098">No optimised routes assigned</span>'}</div>
  <h2>{stop_name}</h2>
  <div style="color:#7a8098;font-size:10px;margin-top:4px">
    Stop ID: {stop_id} &nbsp;|&nbsp; District: {district_id or '—'} &nbsp;|&nbsp; {stop_type}
    {(' &nbsp;|&nbsp; ' + ada_glyph) if ada_glyph else ''}
  </div>
</div>

<!-- Strip diagram -->
{mermaid_block if mermaid_block else ""}

<!-- Mini-map -->
<div class="card">
  <div class="section-label">Map</div>
  <div id="mini-map"></div>
</div>

<!-- Timetable -->
<div class="card">
  <div class="section-label">Schedule (from optimised GTFS)</div>
  {timetable_html}
</div>

<!-- Footer: QR -->
<div class="card" style="display:flex;gap:16px;align-items:flex-start">
  <div>
    {qr_html}
    <div style="font-size:9px;color:#7a8098;margin-top:4px;text-align:center">Scan to open<br>route dashboard</div>
  </div>
  <div style="font-size:10px;color:#7a8098">
    <div><b style="color:#d8dce8">{stop_name}</b></div>
    <div>ID: {stop_id}</div>
    <div>Lat: {stop_lat:.5f} / Lon: {stop_lon:.5f}</div>
    {('<div>' + ada_glyph + '</div>') if ada_glyph else ''}
    <div style="margin-top:8px">Generated by Los Gatos Transit CBA</div>
  </div>
</div>

<script>
mermaid.initialize({{startOnLoad:true,theme:'dark',themeVariables:{{fontSize:'10px'}}}});

const NEARBY={nearby_json};
const MAP_LAT={stop_lat};
const MAP_LON={stop_lon};

const map=L.map('mini-map',{{attributionControl:false,zoomControl:true}}).setView([MAP_LAT,MAP_LON],15);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}.png',{{
  attribution:'&copy; OpenStreetMap / CartoDB'
}}).addTo(map);

// Route polyline (dim) from nearby stops
if(NEARBY.length>1){{
  const pts=NEARBY.map(s=>[s.lat,s.lon]);
  L.polyline(pts,{{color:'#6c9bff',weight:2,opacity:.35,dashArray:'4 4'}}).addTo(map);
}}

// Upstream/downstream stops
NEARBY.forEach(s=>{{
  if(s.is_this){{
    L.circleMarker([s.lat,s.lon],{{radius:10,color:'#fff',weight:2,fillColor:'#ffa94d',fillOpacity:.95}})
      .addTo(map).bindPopup('<b>'+s.name+'</b><br><i>This stop</i>');
  }}else{{
    L.circleMarker([s.lat,s.lon],{{radius:5,color:'#fff',weight:1,fillColor:'#6c9bff',fillOpacity:.75}})
      .addTo(map).bindPopup(s.name);
  }}
}});
</script>
</body>
</html>"""


def render_all_placards(
    gtfs_dir: str,
    selected_stops: list,
    out_dir: str,
    parent_route_lookup: Dict[str, str],
) -> None:
    """Render one HTML placard per selected stop and an index page.

    Args:
        gtfs_dir: Path to outputs/gtfs_optimised directory.
        selected_stops: List of OptimisedStop objects.
        out_dir: Destination directory (outputs/placards).
        parent_route_lookup: Maps optimised route_id → parent VTA route_id.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    tables = _load_gtfs(gtfs_dir)

    # Build stop→routes lookup from GTFS stop_times + trips
    stop_routes: Dict[str, List[str]] = {}
    st_df = tables.get("stop_times", pd.DataFrame())
    tr_df = tables.get("trips", pd.DataFrame())
    if not st_df.empty and not tr_df.empty and "route_id" in tr_df.columns:
        trip_route = tr_df.set_index("trip_id")["route_id"].to_dict()
        for _, row in st_df.iterrows():
            sid = str(row["stop_id"])
            rid = trip_route.get(str(row.get("trip_id", "")), "")
            if rid:
                stop_routes.setdefault(sid, [])
                if rid not in stop_routes[sid]:
                    stop_routes[sid].append(rid)

    # Assign colors to routes deterministically
    all_route_ids = sorted({rid for rids in stop_routes.values() for rid in rids})
    route_color_map = {rid: _route_color(rid, i) for i, rid in enumerate(all_route_ids)}

    dashboard_base = "../route_optimization.html"
    index_rows = []

    for stop in selected_stops:
        stop_id = str(stop.stop_id)
        stop_name = getattr(stop, "stop_name", stop_id)
        stop_lat = float(getattr(stop, "stop_lat", 0))
        stop_lon = float(getattr(stop, "stop_lon", 0))
        wheelchair_boarding = int(getattr(stop, "wheelchair_boarding", 1))
        district_id = str(getattr(stop, "district_id", "") or "")
        is_existing = bool(getattr(stop, "is_existing", True))

        route_ids = stop_routes.get(stop_id, [])

        qr_url = f"{dashboard_base}#stop_{stop_id}"
        qr_b64 = _make_qr_b64(qr_url)

        html = _placard_html(
            stop_id=stop_id,
            stop_name=stop_name,
            stop_lat=stop_lat,
            stop_lon=stop_lon,
            wheelchair_boarding=wheelchair_boarding,
            district_id=district_id,
            is_existing=is_existing,
            route_ids=route_ids,
            parent_route_lookup=parent_route_lookup,
            tables=tables,
            route_color_map=route_color_map,
            qr_b64=qr_b64,
        )

        placard_path = out_path / f"{stop_id}.html"
        placard_path.write_text(html, encoding="utf-8")

        index_rows.append({
            "stop_id": stop_id,
            "stop_name": stop_name,
            "district_id": district_id,
            "routes": ", ".join(route_ids),
            "is_existing": is_existing,
            "file": f"{stop_id}.html",
        })

    logger.info("Placards: wrote %d stop pages to %s", len(index_rows), out_dir)

    _render_index(out_path, index_rows)
    logger.info("Placards: index page written to %s/index.html", out_dir)


def _render_index(out_path: Path, rows: List[dict]) -> None:
    """Write a filterable HTML index of all placard pages."""
    rows_html = ""
    for r in rows:
        stop_type = "Existing" if r["is_existing"] else '<span style="color:#ffa94d">New</span>'
        rows_html += (
            f'<tr data-route="{r["routes"]}" data-district="{r["district_id"]}" data-name="{r["stop_name"].lower()}">'
            f'<td style="padding:4px 8px"><a href="{r["file"]}" target="_blank">{r["stop_name"]}</a></td>'
            f'<td style="padding:4px 8px">{r["stop_id"]}</td>'
            f'<td style="padding:4px 8px">{r["district_id"] or "—"}</td>'
            f'<td style="padding:4px 8px">{r["routes"] or "—"}</td>'
            f'<td style="padding:4px 8px">{stop_type}</td>'
            f'</tr>\n'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Stop Placards — Los Gatos Transit CBA</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#141720;color:#d8dce8;font-family:IBM Plex Mono,Consolas,monospace;font-size:12px;padding:20px}}
  h1{{font-size:16px;font-weight:700;color:#fff;margin-bottom:4px}}
  .sub{{font-size:10px;color:#7a8098;margin-bottom:16px}}
  input{{background:#1e2235;border:1px solid rgba(42,48,80,.8);border-radius:4px;color:#d8dce8;font:11px IBM Plex Mono,monospace;padding:6px 10px;width:300px;margin-right:8px;margin-bottom:12px}}
  table{{border-collapse:collapse;width:100%}}
  th{{text-align:left;padding:6px 8px;font-size:10px;color:#7a8098;border-bottom:1px solid rgba(42,48,80,.6)}}
  tr:hover td{{background:rgba(42,48,80,.3)}}
  a{{color:#6c9bff;text-decoration:none}}
  a:hover{{text-decoration:underline}}
  .back{{font-size:10px;color:#7a8098;margin-bottom:12px;display:block}}
</style>
</head>
<body>
<a class="back" href="../route_optimization.html">← Route dashboard</a>
<h1>Stop Placards</h1>
<div class="sub">{len(rows)} stops — click any row to open its placard</div>
<div>
  <input id="filter" type="text" placeholder="Filter by name, district, or route…" oninput="filterRows()">
</div>
<table>
  <thead><tr>
    <th>Stop name</th><th>Stop ID</th><th>District</th><th>Routes</th><th>Type</th>
  </tr></thead>
  <tbody id="tbody">
{rows_html}
  </tbody>
</table>
<script>
function filterRows(){{
  const q=document.getElementById('filter').value.toLowerCase();
  document.querySelectorAll('#tbody tr').forEach(r=>{{
    const txt=(r.dataset.name+' '+r.dataset.district+' '+r.dataset.route).toLowerCase();
    r.style.display=txt.includes(q)?'':'none';
  }});
}}
</script>
</body>
</html>"""

    (out_path / "index.html").write_text(html, encoding="utf-8")
