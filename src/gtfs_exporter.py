"""
gtfs_exporter.py -- Export optimised routes and schedule as a GTFS Static feed.

Produces a complete, valid GTFS Static feed in outputs/gtfs_optimised/ that
could be submitted to VTA or loaded into any GTFS-compatible trip planner.

GTFS files produced:
    agency.txt        VTA agency info
    routes.txt        Optimised route definitions
    stops.txt         Selected stops with ADA wheelchair_boarding flags
    trips.txt         One row per trip
    stop_times.txt    Arrival/departure per stop per trip
    calendar.txt      WEEKDAY and SCHOOL_WEEKDAY service calendars
    shapes.txt        Route geometry (straight-line segments; OSM snap if available)
    feed_info.txt     Feed metadata

Validation:
    Attempts gtfs-kit validation if installed; logs any errors/warnings.

Standards:
    - GTFS Static Specification (https://gtfs.org/schedule/)
    - ADA 49 CFR Part 37: wheelchair_boarding = 1 on all stops
    - FTA: routes.txt route_type = 3 (bus)
"""

import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# =====================================================================
# HELPERS
# =====================================================================

def _write_csv(path: Path, rows: list, fieldnames: list) -> None:
    """Write a GTFS CSV file (comma-delimited, UTF-8, with header)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logger.debug("  Wrote %s (%d rows)", path.name, len(rows))


# =====================================================================
# INDIVIDUAL FILE WRITERS
# =====================================================================

def _write_agency(output_dir: Path, config: dict) -> None:
    transit_cfg = config.get("transit", {})
    opt_cfg = config.get("optimization", {}).get("gtfs_export", {})
    rows = [{
        "agency_id": transit_cfg.get("agency_id", "VTA"),
        "agency_name": transit_cfg.get("agency_name", "Valley Transportation Authority"),
        "agency_url": opt_cfg.get("feed_publisher_url", "https://www.vta.org"),
        "agency_timezone": "America/Los_Angeles",
        "agency_lang": opt_cfg.get("feed_lang", "en"),
        "agency_phone": "408-321-2300",
    }]
    _write_csv(output_dir / "agency.txt", rows,
               ["agency_id", "agency_name", "agency_url",
                "agency_timezone", "agency_lang", "agency_phone"])


def _write_feed_info(output_dir: Path, config: dict) -> None:
    opt_cfg = config.get("optimization", {}).get("gtfs_export", {})
    rows = [{
        "feed_publisher_name": opt_cfg.get("feed_publisher_name",
                                            "Los Gatos Transit CBA Study"),
        "feed_publisher_url": opt_cfg.get("feed_publisher_url",
                                           "https://www.losgatosca.gov"),
        "feed_lang": opt_cfg.get("feed_lang", "en"),
        "feed_start_date": "20260801",
        "feed_end_date": "20270630",
        "feed_version": opt_cfg.get("feed_version", "1.0-optimised"),
    }]
    _write_csv(output_dir / "feed_info.txt", rows,
               ["feed_publisher_name", "feed_publisher_url", "feed_lang",
                "feed_start_date", "feed_end_date", "feed_version"])


def _write_calendar(output_dir: Path) -> None:
    rows = [
        {
            "service_id": "WEEKDAY",
            "monday": 1, "tuesday": 1, "wednesday": 1,
            "thursday": 1, "friday": 1,
            "saturday": 0, "sunday": 0,
            "start_date": "20260801",
            "end_date": "20270630",
        },
        {
            "service_id": "SCHOOL_WEEKDAY",
            "monday": 1, "tuesday": 1, "wednesday": 1,
            "thursday": 1, "friday": 1,
            "saturday": 0, "sunday": 0,
            "start_date": "20260901",  # School year start
            "end_date": "20270613",    # School year end
        },
    ]
    _write_csv(output_dir / "calendar.txt", rows,
               ["service_id", "monday", "tuesday", "wednesday",
                "thursday", "friday", "saturday", "sunday",
                "start_date", "end_date"])


def _write_stops(output_dir: Path, selected_stops: list) -> None:
    """Write stops.txt from OptimisedStop objects."""
    rows = []
    for stop in selected_stops:
        rows.append({
            "stop_id": stop.stop_id,
            "stop_name": stop.stop_name,
            "stop_lat": f"{stop.stop_lat:.6f}",
            "stop_lon": f"{stop.stop_lon:.6f}",
            "zone_id": stop.district_id or "",
            "location_type": 0,
            "wheelchair_boarding": stop.wheelchair_boarding,  # ADA: always 1
        })
    _write_csv(output_dir / "stops.txt", rows,
               ["stop_id", "stop_name", "stop_lat", "stop_lon",
                "zone_id", "location_type", "wheelchair_boarding"])


def _write_routes(output_dir: Path, routes: list, config: dict) -> None:
    """Write routes.txt from OptimisedRoute objects."""
    transit_cfg = config.get("transit", {})
    agency_id = transit_cfg.get("agency_id", "VTA")

    # Colour scheme: existing routes keep VTA red; restored routes orange
    rows = []
    for route in routes:
        if route.is_restoration:
            color, text_color = "FF8C00", "FFFFFF"  # orange
        else:
            color, text_color = "CC0000", "FFFFFF"  # VTA red

        rows.append({
            "route_id": route.route_id,
            "agency_id": agency_id,
            "route_short_name": route.route_id.replace("OPT_", ""),
            "route_long_name": route.route_name,
            "route_type": 3,  # Bus (FTA GTFS standard)
            "route_color": color,
            "route_text_color": text_color,
        })
    _write_csv(output_dir / "routes.txt", rows,
               ["route_id", "agency_id", "route_short_name", "route_long_name",
                "route_type", "route_color", "route_text_color"])


def _write_trips(output_dir: Path, all_trips: list) -> None:
    """Write trips.txt."""
    rows = []
    for trip in all_trips:
        rows.append({
            "route_id": trip.route_id,
            "service_id": trip.service_id,
            "trip_id": trip.trip_id,
            "trip_headsign": trip.headsign,
            "direction_id": 0,
            "shape_id": f"shape_{trip.route_id}",
            "wheelchair_accessible": 1,
            "bikes_allowed": 1,
        })
    _write_csv(output_dir / "trips.txt", rows,
               ["route_id", "service_id", "trip_id", "trip_headsign",
                "direction_id", "shape_id", "wheelchair_accessible", "bikes_allowed"])


def _write_stop_times(output_dir: Path, all_trips: list) -> None:
    """Write stop_times.txt."""
    rows = []
    for trip in all_trips:
        for st in trip.stop_times:
            rows.append({
                "trip_id": trip.trip_id,
                "arrival_time": st.arrival_time,
                "departure_time": st.departure_time,
                "stop_id": st.stop_id,
                "stop_sequence": st.stop_sequence,
                "pickup_type": 0,      # Regular pickup
                "drop_off_type": 0,    # Regular dropoff
                "timepoint": 1,        # Exact time (not interpolated)
            })
    _write_csv(output_dir / "stop_times.txt", rows,
               ["trip_id", "arrival_time", "departure_time", "stop_id",
                "stop_sequence", "pickup_type", "drop_off_type", "timepoint"])


def _write_shapes(
    output_dir: Path,
    routes: list,
    polylines: Optional[dict] = None,
) -> None:
    """Write shapes.txt — using OSM road-network polylines when supplied,
    otherwise straight-line stop-to-stop fallback.

    `polylines` is dict route_id -> list of (lat, lon) along real roads,
    produced by network_graph.compute_route_polylines(), or each route's
    .polyline attribute when polylines dict is None.
    """
    import math

    def _haversine_m(lat1, lon1, lat2, lon2):
        R = 6371000.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
             * math.sin(dlon / 2) ** 2)
        return R * 2 * math.asin(math.sqrt(a))

    rows = []
    for route in routes:
        shape_id = f"shape_{route.route_id}"
        # Prefer road-network geometry from polylines dict, then route.polyline,
        # fall back to stop centroids.
        coords = None
        if polylines and route.route_id in polylines and polylines[route.route_id]:
            coords = polylines[route.route_id]
        elif getattr(route, "polyline", None):
            coords = route.polyline
        if not coords:
            coords = [(s.stop_lat, s.stop_lon) for s in route.stops]

        cumulative = 0.0
        prev = None
        for seq, (lat, lon) in enumerate(coords):
            if prev is not None:
                cumulative += _haversine_m(prev[0], prev[1], lat, lon)
            rows.append({
                "shape_id": shape_id,
                "shape_pt_lat": f"{lat:.6f}",
                "shape_pt_lon": f"{lon:.6f}",
                "shape_pt_sequence": seq,
                "shape_dist_traveled": round(cumulative, 1),
            })
            prev = (lat, lon)

    _write_csv(output_dir / "shapes.txt", rows,
               ["shape_id", "shape_pt_lat", "shape_pt_lon",
                "shape_pt_sequence", "shape_dist_traveled"])


# =====================================================================
# VALIDATION
# =====================================================================

def _validate_gtfs(output_dir: Path) -> str:
    """Run gtfs-kit validation if available. Returns report as string."""
    report_lines = ["GTFS Validation Report", "=" * 40]
    try:
        import gtfs_kit as gk  # type: ignore
        feed = gk.read_feed(str(output_dir), dist_units="km")
        results = feed.validate()
        if isinstance(results, pd.DataFrame) and len(results) > 0:
            for _, row in results.iterrows():
                report_lines.append(
                    f"  [{row.get('type', 'INFO')}] {row.get('message', '')}"
                )
            errors = results[results.get("type", "") == "error"] if "type" in results.columns else []
            n_err = len(errors)
        else:
            n_err = 0
        report_lines.append(f"\nValidation complete: {n_err} error(s).")
        logger.info("GTFS validation: %d error(s).", n_err)
    except ImportError:
        report_lines.append("gtfs-kit not installed; validation skipped.")
        report_lines.append("Install with: pip install gtfs-kit")
        logger.info("gtfs-kit not available; GTFS validation skipped.")
    except Exception as exc:
        report_lines.append(f"Validation failed: {exc}")
        logger.warning("GTFS validation error: %s", exc)
    return "\n".join(report_lines)


# =====================================================================
# PIPELINE ENTRY POINT
# =====================================================================

def export_gtfs(
    routes: list,          # List[OptimisedRoute]
    schedule: dict,        # From generate_schedule()
    selected_stops: list,  # List[OptimisedStop]
    config: dict,
    output_dir: str = "outputs/gtfs_optimised",
) -> str:
    """Write the full GTFS Static feed and validate it.

    Args:
        routes: Optimised routes from route_optimizer.py.
        schedule: Output dict from schedule_generator.generate_schedule().
        selected_stops: From route_optimizer.select_stops().
        config: Full config dict.
        output_dir: Destination directory for GTFS files.

    Returns:
        Path to the output directory as a string.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    all_trips = schedule.get("all_trips", [])

    logger.info("Exporting GTFS feed to %s ...", out)
    _write_agency(out, config)
    _write_feed_info(out, config)
    _write_calendar(out)
    _write_stops(out, selected_stops)
    _write_routes(out, routes, config)
    _write_trips(out, all_trips)
    _write_stop_times(out, all_trips)
    _write_shapes(out, routes)

    # Mirror road-following geometry as a flat CSV for the dashboard.
    shapes_table_path = "outputs/tables/route_shapes.csv"
    shape_path = Path(shapes_table_path)
    shape_path.parent.mkdir(parents=True, exist_ok=True)
    shape_rows = []
    for route in routes:
        coords = getattr(route, "polyline", None) or [
            (s.stop_lat, s.stop_lon) for s in route.stops
        ]
        for seq, (lat, lon) in enumerate(coords):
            shape_rows.append({
                "route_id": route.route_id,
                "source_route_id": route.source_route_id or "",
                "seq": seq,
                "lat": round(float(lat), 6),
                "lon": round(float(lon), 6),
            })
    shape_df = pd.DataFrame(shape_rows)
    # Fill NaN in source_route_id so JSON serialisation in dashboard is clean.
    if "source_route_id" in shape_df.columns:
        shape_df["source_route_id"] = shape_df["source_route_id"].fillna("")
    shape_df.to_csv(shape_path, index=False)
    logger.info("Route shapes CSV written: %s (%d rows)", shape_path, len(shape_rows))

    n_routes = len(routes)
    n_stops = len(selected_stops)
    n_trips = len(all_trips)
    logger.info("GTFS export: %d routes, %d stops, %d trips.", n_routes, n_stops, n_trips)

    # Validation
    report = _validate_gtfs(out)
    report_path = out / "validation_report.txt"
    report_path.write_text(report, encoding="utf-8")
    logger.info("Validation report: %s", report_path)

    # Summary stats file
    stats = pd.DataFrame([{
        "metric": "Routes",        "value": n_routes},
        {"metric": "Stops",        "value": n_stops},
        {"metric": "Trips/day",    "value": n_trips},
        {"metric": "School trips", "value": sum(1 for t in all_trips if t.is_school_trip)},
        {"metric": "Files written","value": 8},
    ])
    stats.to_csv(out / "feed_summary.csv", index=False)

    print(f"\n{'='*60}")
    print("GTFS EXPORT COMPLETE")
    print(f"{'='*60}")
    print(f"  Output directory:  {out}")
    print(f"  Routes:            {n_routes}")
    print(f"  Stops:             {n_stops}")
    print(f"  Trips/day:         {n_trips}")
    print(f"  School trips:      {sum(1 for t in all_trips if t.is_school_trip)}")

    school_cov = schedule.get("school_coverage")
    if school_cov is not None and len(school_cov) > 0:
        print()
        print("=" * 70)
        print("SCHOOL PICKUP VERIFICATION")
        print("  Pass criterion: bus must arrive within window [dismissal - 5 min, dismissal + 10 min]")
        print("  Why: FTA on-time guidance; arriving too early misses students still in class,")
        print("       arriving too late leaves them waiting beyond the 10-min tolerance.")
        print("=" * 70)
        print(f"  {'School':<26} {'Dismissal':<11} {'Window-end':<12} {'Scheduled-arr':<15} {'Slack(min)':<12} Status")
        for _, row in school_cov.iterrows():
            status = "PASS" if row["constraint_met"] else "FAIL"
            school_name = str(row["school"])
            dismissal = str(row["dismissal_time"])
            # Truncate HH:MM:SS to HH:MM for column alignment
            _deadline_raw = str(row.get("pickup_deadline", "N/A"))
            deadline = ":".join(_deadline_raw.split(":")[:2])
            _actual_raw = str(row.get("actual_arrival", "N/A"))
            actual = ":".join(_actual_raw.split(":")[:2])
            # Compute slack in minutes (dismissal + window_min - actual_arrival)
            try:
                import datetime as _dt
                def _parse_hms(t):
                    """Parse HH:MM or HH:MM:SS string to timedelta."""
                    parts = str(t).split(":")
                    h, m = int(parts[0]), int(parts[1])
                    s = int(parts[2]) if len(parts) > 2 else 0
                    return _dt.timedelta(hours=h, minutes=m, seconds=s)
                slack_td = _parse_hms(_deadline_raw) - _parse_hms(_actual_raw)
                slack_min = slack_td.total_seconds() / 60.0
                slack_str = f"+{slack_min:.1f}" if slack_min >= 0 else f"{slack_min:.1f}"
            except Exception:
                slack_str = "N/A"
            print(f"  {school_name:<26} {dismissal:<11} {deadline:<12} {actual:<15} {slack_str:<12} {status}")

    return str(out)
