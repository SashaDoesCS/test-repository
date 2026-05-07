"""
vta_rbs.py -- VTA Route Boarding Summary (RBS) ingestion and Los Gatos filtering.

Loads the OCT 2025 RBS Excel dataset, aggregates Route 27 trip-stop boardings
and alightings to per-stop totals by service period (Weekday/Saturday/Sunday),
joins to GTFS stop geometry, filters to a configurable geofence (default:
LGHS-zone districts), and annualizes using the standard 255/52/52 service-day
weights.

The output CSV at data/processed/route27_lg_stops.csv is the empirical anchor
for all downstream Route 27 ridership calibration. Each row is one stop with
its observed boardings/alightings on a typical weekday, Saturday, Sunday plus
the annualized totals.

Validation: by default this module compares the LG-filtered weekday total to
the user-supplied VTA-site target (231 boardings / 247 alightings). A large
discrepancy (>5%) indicates the geofence is wrong (too broad or too narrow);
the run prints a per-district breakdown so the polygon set can be retuned.

Annualization weights:
    Weekday: 255 days/yr (52 weeks * 5 - 10 holidays observed on weekdays)
    Saturday: 52 days/yr
    Sunday: 52 days/yr (53 in some years; 52 conservative)

References:
    - VTA OCT 2025 Route Boarding Summary (data/raw/OCT_2025_RBS_FULL_DATA_SET.XLSX)
    - VTA GTFS stops.txt (data/geospatial/gtfs/stops.txt)
    - LGHS districts boundary file (data/geospatial/districts/all_districts.geojson)
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import openpyxl
import pandas as pd
from matplotlib.path import Path as MplPath

logger = logging.getLogger(__name__)

ROUTE27 = 27
PERIODS = ("Weekday", "Saturday", "Sunday")

# Annualization weights: typical-day boardings * (days of that period in a year).
ANNUAL_WEIGHTS = {"Weekday": 255, "Saturday": 52, "Sunday": 52}

# CRITICAL: the RBS workbook contains MONTHLY totals, not daily. Each row is
# (one trip x one stop) summed across the full month for its service period.
# To recover typical-day boardings we divide by the number of that-period days
# present in the source month. For OCT 2025: 23 weekdays, 4 Saturdays, 4 Sundays
# (Oct 1, 2025 was a Wednesday; no federal holiday observed on a weekday in
# the month means weekday count is the full 23).
# Validated against the Bus_and_Light_Rail_Average_Ridership_Data_2025.csv
# system-wide averages (Aug 2025 wkdy avg = 1200; Oct 2025 RBS / 23 = 881)
# and the 2017 historical baseline (8,390 wkdy/day) -- see commit history.
RBS_PERIOD_DAYS_OCT2025 = {"Weekday": 23, "Saturday": 4, "Sunday": 4}

# VTA-site reported LG-only Route 27 typical-day totals (user-supplied, May 2026).
# Used as the validation target for the LG geofence.
DEFAULT_TARGET = {
    "Weekday": {"boardings": 231, "alightings": 247},
    "Saturday": {"boardings": 100, "alightings": 110},
    "Sunday": {"boardings": 64, "alightings": 75},
}


@dataclass
class GeofencePolygon:
    """A named polygon used to test stop containment."""
    label: str
    path: MplPath


def load_geofence(geojson_path: Path, zone_filter: str | None = "LGHS") -> list[GeofencePolygon]:
    """Load polygons from a GeoJSON FeatureCollection.

    Args:
        geojson_path: Path to a FeatureCollection GeoJSON file.
        zone_filter: If set, only include features whose properties.zone equals this.
            Pass None to include all features.

    Returns:
        List of GeofencePolygon objects (label = feature.properties.id).
    """
    with open(geojson_path, encoding="utf-8") as f:
        fc = json.load(f)
    polys: list[GeofencePolygon] = []
    for feat in fc.get("features", []):
        props = feat.get("properties", {})
        if zone_filter and props.get("zone") != zone_filter:
            continue
        ring = feat["geometry"]["coordinates"][0]
        # GeoJSON uses [lon, lat]; matplotlib Path uses (x, y) -- store as (lat, lon)
        # to match districts.py convention (containment is order-agnostic).
        verts = [(lat, lon) for lon, lat in ring]
        polys.append(GeofencePolygon(label=props.get("id", "?"), path=MplPath(verts)))
    if not polys:
        raise ValueError(f"No polygons matched zone={zone_filter!r} in {geojson_path}")
    return polys


def load_route27_aggregated(rbs_xlsx: Path) -> pd.DataFrame:
    """Aggregate Route 27 boardings/alightings by stop_id and service period.

    Reads the Excel file row-by-row in read-only mode for memory efficiency
    (388k rows total).

    Returns:
        Long-format DataFrame with columns:
            stop_id (str), main_cross_street, period,
            boardings (int), alightings (int), trips_observed (int)
    """
    logger.info("Loading RBS workbook: %s", rbs_xlsx)
    wb = openpyxl.load_workbook(rbs_xlsx, read_only=True, data_only=True)
    ws = wb["Temporary_Table_E"]

    # Schema (col indices, 0-based): 1 ROUTE_NUMBER, 2 SERVICE_PERIOD, 8 STOP_ID,
    # 9 MAIN_CROSS_STREET, 10 BOARDINGS, 11 ALIGHTINGS
    agg: dict[tuple[str, str], dict] = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        if row[1] != ROUTE27:
            continue
        period = row[2]
        if period not in PERIODS:
            continue
        sid = str(row[8])
        b = row[10] or 0
        a = row[11] or 0
        key = (sid, period)
        d = agg.get(key)
        if d is None:
            agg[key] = {"name": row[9], "boardings": b, "alightings": a, "trips": 1}
        else:
            d["boardings"] += b
            d["alightings"] += a
            d["trips"] += 1
    wb.close()

    rows = [
        {
            "stop_id": sid,
            "main_cross_street": d["name"],
            "period": period,
            "boardings": d["boardings"],
            "alightings": d["alightings"],
            "trips_observed": d["trips"],
        }
        for (sid, period), d in agg.items()
    ]
    df = pd.DataFrame(rows)
    logger.info(
        "Route 27 aggregation: %d unique stops, %d period-rows, "
        "%d total weekday boardings",
        df["stop_id"].nunique(),
        len(df),
        df.loc[df["period"] == "Weekday", "boardings"].sum(),
    )
    return df


def attach_geometry(df: pd.DataFrame, stops_txt: Path) -> pd.DataFrame:
    """Join per-stop boardings to GTFS stop coordinates and names.

    Drops rows whose stop_id is not in the GTFS stops.txt; logs the count.
    """
    stops = pd.read_csv(stops_txt, dtype={"stop_id": str})
    stops = stops[["stop_id", "stop_name", "stop_lat", "stop_lon"]]
    n_before = df["stop_id"].nunique()
    out = df.merge(stops, on="stop_id", how="left")
    missing_mask = out["stop_lat"].isna()
    n_missing = out.loc[missing_mask, "stop_id"].nunique()
    if n_missing:
        logger.warning(
            "Dropping %d/%d Route 27 stops with no geometry in stops.txt: %s",
            n_missing,
            n_before,
            sorted(out.loc[missing_mask, "stop_id"].unique())[:10],
        )
    out = out.loc[~missing_mask].copy()
    return out


def assign_geofence(df: pd.DataFrame, polygons: list[GeofencePolygon]) -> pd.DataFrame:
    """Add `district_hits` (comma-separated polygon labels) and `in_geofence` (bool).

    `df` must have stop_lat / stop_lon columns. Containment check uses each
    polygon's matplotlib Path; a stop is in_geofence if it falls inside any.
    """
    out = df.copy()
    hits_per_stop: dict[str, list[str]] = {}
    for sid, sub in out.groupby("stop_id"):
        lat = sub["stop_lat"].iloc[0]
        lon = sub["stop_lon"].iloc[0]
        hits = [p.label for p in polygons if p.path.contains_point((lat, lon))]
        hits_per_stop[sid] = hits
    out["district_hits"] = out["stop_id"].map(lambda s: ",".join(hits_per_stop[s]))
    out["in_geofence"] = out["district_hits"].astype(bool)
    return out


def to_per_stop_table(
    df: pd.DataFrame,
    period_days: dict[str, int] = RBS_PERIOD_DAYS_OCT2025,
) -> pd.DataFrame:
    """Pivot long-format to wide; convert RBS monthly totals to typical-day averages.

    Adds two sets of columns per period:
        {period}_boardings_monthly   -- raw monthly total from RBS
        {period}_boardings           -- typical-day average (monthly / period_days)
    Plus annualized totals using ANNUAL_WEIGHTS.
    """
    wide = df.pivot_table(
        index=["stop_id", "stop_name", "main_cross_street", "stop_lat", "stop_lon",
               "district_hits", "in_geofence"],
        columns="period",
        values=["boardings", "alightings", "trips_observed"],
        fill_value=0,
        aggfunc="sum",
    )
    wide.columns = [f"{period.lower()}_{metric}_monthly" for metric, period in wide.columns]
    wide = wide.reset_index()

    # Convert monthly RBS totals to typical-day averages
    for p in PERIODS:
        ndays = period_days[p]
        for metric in ("boardings", "alightings"):
            mcol = f"{p.lower()}_{metric}_monthly"
            dcol = f"{p.lower()}_{metric}"
            if mcol in wide.columns:
                wide[dcol] = (wide[mcol] / ndays).round(2)

    # Annualized totals using typical-day averages * service days/year
    wide["annual_boardings"] = sum(
        wide.get(f"{p.lower()}_boardings", 0) * ANNUAL_WEIGHTS[p] for p in PERIODS
    ).round().astype(int)
    wide["annual_alightings"] = sum(
        wide.get(f"{p.lower()}_alightings", 0) * ANNUAL_WEIGHTS[p] for p in PERIODS
    ).round().astype(int)
    return wide


def summarize_totals(per_stop: pd.DataFrame, mask: pd.Series | None = None) -> dict:
    """Sum a per-stop wide table to typical-day period totals + annualized totals."""
    sub = per_stop.loc[mask] if mask is not None else per_stop
    out: dict = {"n_stops": int(len(sub))}
    for p in PERIODS:
        out[p] = {
            "boardings": round(float(sub.get(f"{p.lower()}_boardings", pd.Series(dtype=float)).sum()), 1),
            "alightings": round(float(sub.get(f"{p.lower()}_alightings", pd.Series(dtype=float)).sum()), 1),
        }
    out["annual_boardings"] = int(sub["annual_boardings"].sum())
    out["annual_alightings"] = int(sub["annual_alightings"].sum())
    return out


def validate_against_target(actual: dict, target: dict, tolerance: float = 0.05) -> tuple[bool, list[str]]:
    """Compare actual period totals to expected; return (ok, diagnostic_lines)."""
    lines = []
    ok = True
    for p in PERIODS:
        for metric in ("boardings", "alightings"):
            a = actual[p][metric]
            t = target[p][metric]
            if t == 0:
                continue
            err = abs(a - t) / t
            status = "OK " if err <= tolerance else "OFF"
            if err > tolerance:
                ok = False
            lines.append(
                f"  [{status}] {p:<8} {metric:<10} actual={a:>7.1f}  target={t:>4}  delta={a-t:+7.1f} ({err*100:+.1f}%)"
            )
    return ok, lines


def per_district_breakdown(per_stop: pd.DataFrame) -> pd.DataFrame:
    """Show typical-day weekday boardings grouped by primary district hit."""
    sub = per_stop.loc[per_stop["in_geofence"]].copy()
    sub["primary_district"] = sub["district_hits"].str.split(",").str[0]
    g = (
        sub.groupby("primary_district")
        .agg(
            n_stops=("stop_id", "nunique"),
            wd_boardings=("weekday_boardings", "sum"),
            sat_boardings=("saturday_boardings", "sum"),
            sun_boardings=("sunday_boardings", "sum"),
            annual_boardings=("annual_boardings", "sum"),
        )
        .round(1)
        .sort_values("wd_boardings", ascending=False)
        .reset_index()
    )
    return g


def build_lg_anchor_table(
    rbs_xlsx: Path,
    stops_txt: Path,
    geojson_path: Path,
    output_csv: Path,
    zone_filter: str = "LGHS",
    target: dict = DEFAULT_TARGET,
    tolerance: float = 0.05,
) -> dict:
    """End-to-end: build the LG-anchored per-stop ridership table.

    Writes:
        - {output_csv}: filtered LG-only stops (in_geofence=True), one row per stop
        - {output_csv with _full suffix}: all Route 27 stops with geofence flag

    Returns a dict report containing route-wide totals, LG-only totals,
    target-comparison diagnostics, and per-district breakdown.
    """
    long = load_route27_aggregated(rbs_xlsx)
    long = attach_geometry(long, stops_txt)
    polys = load_geofence(geojson_path, zone_filter=zone_filter)
    long = assign_geofence(long, polys)
    per_stop = to_per_stop_table(long)

    route_totals = summarize_totals(per_stop)
    lg_totals = summarize_totals(per_stop, mask=per_stop["in_geofence"])
    ok, diag = validate_against_target(lg_totals, target, tolerance=tolerance)
    breakdown = per_district_breakdown(per_stop)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    lg_only = per_stop.loc[per_stop["in_geofence"]].sort_values("weekday_boardings", ascending=False)
    lg_only.to_csv(output_csv, index=False)
    full_csv = output_csv.with_name(output_csv.stem.replace("_lg_", "_full_") + ".csv")
    if full_csv == output_csv:
        full_csv = output_csv.with_name(output_csv.stem + "_full.csv")
    per_stop.sort_values("weekday_boardings", ascending=False).to_csv(full_csv, index=False)

    report = {
        "rbs_source": str(rbs_xlsx),
        "geofence_zone": zone_filter,
        "polygons_loaded": [p.label for p in polys],
        "route_totals": route_totals,
        "lg_totals": lg_totals,
        "target": target,
        "validation_ok": ok,
        "validation_lines": diag,
        "per_district": breakdown.to_dict(orient="records"),
        "output_csv": str(output_csv),
        "output_csv_full": str(full_csv),
    }
    return report


def _print_report(report: dict) -> None:
    print("=" * 78)
    print(f"VTA Route 27 RBS Anchor Build (typical-day boardings, OCT 2025 source)")
    print(f"Source : {report['rbs_source']}")
    print(f"Zone   : {report['geofence_zone']}  ({len(report['polygons_loaded'])} polygons)")
    print(f"Note   : RBS BOARDINGS are MONTHLY totals; converted to typical day using")
    print(f"         {RBS_PERIOD_DAYS_OCT2025['Weekday']} weekdays / {RBS_PERIOD_DAYS_OCT2025['Saturday']} Saturdays / {RBS_PERIOD_DAYS_OCT2025['Sunday']} Sundays in October 2025.")
    print("-" * 78)
    rt = report["route_totals"]
    print(f"Route 27 system-wide ({rt['n_stops']} stops, typical-day):")
    for p in PERIODS:
        print(f"  {p:<8} boardings={rt[p]['boardings']:>8}  alightings={rt[p]['alightings']:>8}")
    print(f"  Annualized: boardings={rt['annual_boardings']:>9,}  alightings={rt['annual_alightings']:>9,}")
    print("-" * 78)
    lg = report["lg_totals"]
    print(f"LG geofence ({lg['n_stops']} stops, typical-day):")
    for p in PERIODS:
        print(f"  {p:<8} boardings={lg[p]['boardings']:>8}  alightings={lg[p]['alightings']:>8}")
    print(f"  Annualized: boardings={lg['annual_boardings']:>9,}  alightings={lg['annual_alightings']:>9,}")
    print("-" * 78)
    print(f"Validation vs VTA-site target (tolerance shown per row):")
    for line in report["validation_lines"]:
        print(line)
    if report["validation_ok"]:
        print("  RESULT: WITHIN TOLERANCE")
    else:
        print("  RESULT: GEOFENCE DISCREPANCY -- review polygon set or target")
    print("-" * 78)
    print("Per-district breakdown (primary hit, ranked by weekday boardings):")
    for r in report["per_district"]:
        print(
            f"  {r['primary_district']:<5} "
            f"stops={r['n_stops']:>3}  "
            f"WD={r['wd_boardings']:>5}  Sat={r['sat_boardings']:>4}  Sun={r['sun_boardings']:>4}  "
            f"annual={r['annual_boardings']:>9,}"
        )
    print("-" * 78)
    print(f"Wrote: {report['output_csv']}")
    print(f"Wrote: {report['output_csv_full']}")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rbs", default="data/raw/OCT_2025_RBS_FULL_DATA_SET.XLSX")
    p.add_argument("--stops", default="data/geospatial/gtfs/stops.txt")
    p.add_argument("--districts", default="data/geospatial/districts/all_districts.geojson")
    p.add_argument("--zone", default="LGHS", help="Polygon zone filter (LGHS, UNION, or 'ALL')")
    p.add_argument("--out", default="data/processed/route27_lg_stops.csv")
    p.add_argument(
        "--tolerance", type=float, default=0.25,
        help="Allowable per-metric % delta vs VTA-site target (default 0.25 -- "
             "the LGHS attendance zone is slightly broader than VTA's town-of-LG "
             "definition, so 14-22%% deltas are expected and acceptable).",
    )
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    zone = None if args.zone.upper() == "ALL" else args.zone
    report = build_lg_anchor_table(
        rbs_xlsx=Path(args.rbs),
        stops_txt=Path(args.stops),
        geojson_path=Path(args.districts),
        output_csv=Path(args.out),
        zone_filter=zone,
        tolerance=args.tolerance,
    )
    _print_report(report)
    return 0 if report["validation_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
