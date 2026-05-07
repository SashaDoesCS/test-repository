"""
route27_comparison.py -- KEPT / NEW / REMOVED stop comparison for Route 27.

Joins the optimised stop set (outputs/tables/route27_stop_suggestions.csv) to
the current observed stop set (data/processed/route27_full_stops.csv from
src.vta_rbs) and produces a single per-stop comparison table plus summary
tiles for the dashboard.

Why a spatial join: the suggestions CSV uses synthetic OSM-node IDs
(R27_OSM_<node>) for "existing-kept" stops, while the RBS dataset uses VTA
stop_ids (e.g. 5396). They don't join on stop_id directly. We match each
optimised stop to its nearest current stop within MATCH_TOLERANCE_FT (250ft);
unmatched optimised stops are NEW, unmatched current stops are REMOVED.

Three sections in the output:
    KEPT      -- current stop is retained (an optimised stop is within tolerance)
                 -> show observed boardings/day before & after (assumed unchanged)
    NEW       -- optimised stop with no current stop nearby
                 -> show est_new_riders_daily and BCR
    REMOVED   -- current stop with no optimised stop nearby
                 -> show observed boardings/day; project boardings reabsorbed
                 by the nearest kept stop at REABSORPTION_RATE.

Two summary scopes (LG-only and full corridor):
    baseline_daily      sum of current weekday boardings on stops in scope
    projected_daily     baseline - removed*(1-reabsorption) + new
    delta               projected - baseline

Outputs:
    outputs/tables/route27_stop_comparison.csv      one row per stop
    outputs/tables/route27_comparison_summary.csv   2 rows (LG, full)
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MATCH_TOLERANCE_FT = 250.0   # 250 ft is roughly the urban stop-spacing minimum
REABSORPTION_RATE = 0.60     # ~60% of riders at a removed stop walk to a nearby kept stop
_FEET_PER_DEGREE_LAT = 364_000   # at 37 N
_FEET_PER_DEGREE_LON = 287_000   # cos(37) * 364,000

# W7-5: frequency elasticity of ridership.
# Source: TCRP Report 95 Ch.9, standard elasticity for service frequency on
# local bus routes: 0.5 (doubling frequency increases ridership by 50%).
FREQUENCY_ELASTICITY = 0.5


def frequency_uplift_factor(
    baseline_headway_min: float,
    optimised_headway_min: float,
) -> float:
    """Return ridership multiplier from headway improvement.

    Formula: 1 + elasticity * (delta_freq / baseline_freq)
    where freq = 1/headway (trips per unit time).

    Example: 30->15 min = freq doubles (delta_freq/baseline_freq = 1.0)
    -> factor = 1 + 0.5 * 1.0 = 1.50 (+50%).

    Source: TCRP Report 95 Ch.9 standard frequency elasticity = 0.5.
    """
    if baseline_headway_min <= 0 or optimised_headway_min <= 0:
        return 1.0
    # freq is inversely proportional to headway
    baseline_freq = 1.0 / baseline_headway_min
    optimised_freq = 1.0 / optimised_headway_min
    delta_freq_pct = (optimised_freq - baseline_freq) / baseline_freq
    return 1.0 + FREQUENCY_ELASTICITY * delta_freq_pct


def _ft_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular distance in feet -- adequate at <1 mile separations."""
    dy = (lat1 - lat2) * _FEET_PER_DEGREE_LAT
    dx = (lon1 - lon2) * _FEET_PER_DEGREE_LON
    return math.sqrt(dx * dx + dy * dy)


def _nearest(lat: float, lon: float, target_df: pd.DataFrame) -> tuple[int, float]:
    """Return (index_in_target_df, distance_ft) of the closest target stop."""
    if target_df.empty:
        return -1, float("inf")
    dy = (target_df["stop_lat"].to_numpy() - lat) * _FEET_PER_DEGREE_LAT
    dx = (target_df["stop_lon"].to_numpy() - lon) * _FEET_PER_DEGREE_LON
    d = np.sqrt(dx * dx + dy * dy)
    idx = int(np.argmin(d))
    return idx, float(d[idx])


def build_stop_comparison(
    suggestions_csv: Path = Path("outputs/tables/route27_stop_suggestions.csv"),
    full_stops_csv: Path = Path("data/processed/route27_full_stops.csv"),
    match_tolerance_ft: float = MATCH_TOLERANCE_FT,
    reabsorption_rate: float = REABSORPTION_RATE,
) -> pd.DataFrame:
    """Build a unified KEPT / NEW / REMOVED stop comparison table.

    Returns:
        DataFrame columns:
          stop_id, stop_name, stop_lat, stop_lon, in_los_gatos, status,
          current_daily_boardings, projected_daily_boardings, delta_daily,
          bcr_20yr, corridor_deviation_ft, match_distance_ft,
          matched_current_stop_id, matched_current_stop_name, notes
    """
    if not suggestions_csv.exists():
        raise FileNotFoundError(f"{suggestions_csv} not found -- run the optimizer first.")
    if not full_stops_csv.exists():
        raise FileNotFoundError(
            f"{full_stops_csv} not found -- run `python -m src.vta_rbs` first."
        )

    sug = pd.read_csv(suggestions_csv)
    cur = pd.read_csv(full_stops_csv)

    # Each current stop is uniquely keyed by stop_id; carry boardings + LG flag.
    cur = cur.assign(
        current_daily_boardings=cur["weekday_boardings"].astype(float),
        current_annual_boardings=cur["annual_boardings"].astype(float),
        in_los_gatos=cur["in_geofence"].astype(bool),
    )
    cur_lookup = cur[["stop_id", "stop_name", "stop_lat", "stop_lon",
                      "current_daily_boardings", "current_annual_boardings",
                      "in_los_gatos"]].reset_index(drop=True)

    matched_cur_indices: set[int] = set()
    rows: list[dict] = []

    # 1) KEPT and NEW from the optimised stop set.
    for _, s in sug.iterrows():
        slat, slon = s["stop_lat"], s["stop_lon"]
        if pd.isna(slat) or pd.isna(slon):
            continue
        idx, dist_ft = _nearest(slat, slon, cur_lookup)
        if idx >= 0 and dist_ft <= match_tolerance_ft:
            cur_row = cur_lookup.iloc[idx]
            matched_cur_indices.add(idx)
            current = float(cur_row["current_daily_boardings"])
            current_annual = float(cur_row["current_annual_boardings"])
            # Kept-stop projected boardings: take the optimiser's est if non-zero
            # (NEW_IN_SELECTION re-uses an existing stop position with model est),
            # otherwise hold current (status quo retention).
            est = float(s.get("est_new_riders_daily") or 0.0)
            projected = est if est > 0 else current
            # Scale annual proportionally to the daily delta (preserves the
            # weekday/weekend split the W0 anchor used).
            projected_annual = (projected / current * current_annual) if current > 0 else current_annual
            rows.append({
                "stop_id":                    s["stop_id"],
                "stop_name":                  s["stop_name"],
                "stop_lat":                   slat,
                "stop_lon":                   slon,
                "in_los_gatos":               bool(cur_row["in_los_gatos"]),
                "status":                     "KEPT",
                "current_daily_boardings":    round(current, 2),
                "projected_daily_boardings":  round(projected, 2),
                "current_annual_boardings":   round(current_annual, 0),
                "projected_annual_boardings": round(projected_annual, 0),
                "delta_daily":                round(projected - current, 2),
                "bcr_20yr":                   s.get("bcr_20yr"),
                "corridor_deviation_ft":      s.get("corridor_deviation_ft", 0),
                "match_distance_ft":          round(dist_ft, 1),
                "matched_current_stop_id":    cur_row["stop_id"],
                "matched_current_stop_name":  cur_row["stop_name"],
                "notes":                      "Existing stop retained; current ridership held forward.",
            })
        else:
            # No nearby current stop -> NEW
            est = float(s.get("est_new_riders_daily") or 0.0)
            # Determine LG flag for new stops: use district_id heuristic
            # (D1-D10 = LGHS zone, treated as LG context per W0).
            did = str(s.get("district_id", "") or "")
            in_lg = did.startswith("D")
            # Use a typical LG weekday/weekend split (~46% weekday share of
            # annual boardings, derived from W0 anchor: 199*255 / (199*255 +
            # 92*52 + 78*52) = 0.85 weekday share -> annualize via /0.85*255
            # is overly precise; use the empirical ~ratio:
            # annual_per_daily = 60000 / 199 ~= 301 ([annual]/[wkdy daily]).
            est_annual = est * 301
            rows.append({
                "stop_id":                    s["stop_id"],
                "stop_name":                  s["stop_name"],
                "stop_lat":                   slat,
                "stop_lon":                   slon,
                "in_los_gatos":               in_lg,
                "status":                     "NEW",
                "current_daily_boardings":    0.0,
                "projected_daily_boardings":  round(est, 2),
                "current_annual_boardings":   0.0,
                "projected_annual_boardings": round(est_annual, 0),
                "delta_daily":                round(est, 2),
                "bcr_20yr":                   s.get("bcr_20yr"),
                "corridor_deviation_ft":      s.get("corridor_deviation_ft", 0),
                "match_distance_ft":          round(dist_ft, 1) if idx >= 0 else None,
                "matched_current_stop_id":    None,
                "matched_current_stop_name":  None,
                "notes":                      f"New stop; nearest current is {dist_ft:.0f} ft away (>{match_tolerance_ft:.0f} ft tolerance).",
            })

    # 2) REMOVED: current stops with no optimised stop within tolerance.
    kept_lat_lon = pd.DataFrame(
        [(r["stop_lat"], r["stop_lon"])
         for r in rows if r["status"] == "KEPT"],
        columns=["stop_lat", "stop_lon"],
    )
    for idx, cur_row in cur_lookup.iterrows():
        if idx in matched_cur_indices:
            continue
        # Find nearest kept stop to project reabsorption
        if not kept_lat_lon.empty:
            kidx, kdist = _nearest(cur_row["stop_lat"], cur_row["stop_lon"], kept_lat_lon)
        else:
            kidx, kdist = -1, float("inf")
        current = float(cur_row["current_daily_boardings"])
        current_annual = float(cur_row["current_annual_boardings"])
        reabsorbed_to_others = round(current * reabsorption_rate, 2)
        rows.append({
            "stop_id":                    cur_row["stop_id"],
            "stop_name":                  cur_row["stop_name"],
            "stop_lat":                   cur_row["stop_lat"],
            "stop_lon":                   cur_row["stop_lon"],
            "in_los_gatos":               bool(cur_row["in_los_gatos"]),
            "status":                     "REMOVED",
            "current_daily_boardings":    round(current, 2),
            "projected_daily_boardings":  0.0,
            "current_annual_boardings":   round(current_annual, 0),
            "projected_annual_boardings": 0.0,
            "delta_daily":                round(-current, 2),
            "bcr_20yr":                   None,
            "corridor_deviation_ft":      None,
            "match_distance_ft":          round(kdist, 1) if kidx >= 0 else None,
            "matched_current_stop_id":    None,
            "matched_current_stop_name":  None,
            "notes": (
                f"Removed; ~{int(reabsorption_rate*100)}% of riders ({reabsorbed_to_others:.1f}/day) "
                f"assumed to walk to nearest kept stop {kdist:.0f} ft away."
            ),
        })

    df = pd.DataFrame(rows)
    logger.info(
        "Stop comparison: %d KEPT, %d NEW, %d REMOVED (LG-only: %d KEPT / %d NEW / %d REMOVED)",
        (df["status"] == "KEPT").sum(),
        (df["status"] == "NEW").sum(),
        (df["status"] == "REMOVED").sum(),
        ((df["status"] == "KEPT") & df["in_los_gatos"]).sum(),
        ((df["status"] == "NEW") & df["in_los_gatos"]).sum(),
        ((df["status"] == "REMOVED") & df["in_los_gatos"]).sum(),
    )
    return df


def compute_summary_tiles(
    comparison_df: pd.DataFrame,
    reabsorption_rate: float = REABSORPTION_RATE,
    baseline_headway_min: float = 30.0,
    optimised_headway_min: float = 15.0,
) -> pd.DataFrame:
    """Produce two summary rows: scope=LG_only and scope=full_corridor.

    Each row reports the daily-boarding totals before and after, with the
    delta broken down into removed / new / reabsorbed components.

    W7-5: the projected_annual figure now includes a frequency-elasticity uplift
    on the kept+reabsorbed portion (NOT the new-stop component, which already
    has its own ridership estimate from the BCR model). The pre-uplift figure is
    preserved as projected_annual_pre_frequency for traceability.

    Args:
        comparison_df: From build_stop_comparison().
        reabsorption_rate: Fraction of removed-stop riders walking to a kept stop.
        baseline_headway_min: Current typical peak headway (min). Default 30.
        optimised_headway_min: Proposed peak headway (min). Default 15 (W4 schedule).
    """
    uplift = frequency_uplift_factor(baseline_headway_min, optimised_headway_min)
    uplift_pct = round((uplift - 1.0) * 100.0, 2)

    def _summary(df: pd.DataFrame, scope: str) -> dict:
        kept = df[df["status"] == "KEPT"]
        new = df[df["status"] == "NEW"]
        removed = df[df["status"] == "REMOVED"]

        # Daily totals (typical weekday)
        baseline_daily = float(kept["current_daily_boardings"].sum() +
                               removed["current_daily_boardings"].sum())
        kept_daily = float(kept["current_daily_boardings"].sum())
        new_daily = float(new["projected_daily_boardings"].sum())
        removed_daily = float(removed["current_daily_boardings"].sum())
        reabsorbed_daily = round(removed_daily * reabsorption_rate, 2)
        projected_daily = kept_daily + reabsorbed_daily + new_daily

        # Annual totals using the per-stop annual_boardings column (built by
        # W0 vta_rbs.py with proper 255/52/52 service-day weights). This keeps
        # the comparison summary on the SAME SCALE as the W0 LG anchor (~60k/yr
        # for LG, not 51k as a weekday-only multiplication would suggest).
        baseline_annual = float(kept["current_annual_boardings"].sum() +
                                removed["current_annual_boardings"].sum())
        kept_annual = float(kept["current_annual_boardings"].sum())
        new_annual = float(new["projected_annual_boardings"].sum())
        removed_annual = float(removed["current_annual_boardings"].sum())
        reabsorbed_annual = removed_annual * reabsorption_rate

        # W7-5: apply frequency uplift only to the retained ridership base
        # (kept + reabsorbed). New-stop ridership uses its own BCR estimate.
        pre_frequency_annual = kept_annual + reabsorbed_annual + new_annual
        uplifted_kept_reabsorbed = (kept_annual + reabsorbed_annual) * uplift
        projected_annual = uplifted_kept_reabsorbed + new_annual

        return {
            "scope":                  scope,
            "n_kept":                 int(len(kept)),
            "n_new":                  int(len(new)),
            "n_removed":              int(len(removed)),
            "baseline_daily":         round(baseline_daily, 2),
            "projected_daily":        round(projected_daily, 2),
            "delta_daily":            round(projected_daily - baseline_daily, 2),
            "kept_daily_boardings":   round(kept_daily, 2),
            "new_daily_boardings":    round(new_daily, 2),
            "removed_daily_boardings": round(removed_daily, 2),
            "reabsorbed_daily_estimate": reabsorbed_daily,
            "reabsorption_rate_used": reabsorption_rate,
            "baseline_annual":        int(round(baseline_annual)),
            "projected_annual_pre_frequency": int(round(pre_frequency_annual)),
            "projected_annual":       int(round(projected_annual)),
            "delta_annual":           int(round(projected_annual - baseline_annual)),
            "baseline_headway_min":   baseline_headway_min,
            "optimised_headway_min":  optimised_headway_min,
            "frequency_uplift_pct":   uplift_pct,
        }

    rows = [
        _summary(comparison_df[comparison_df["in_los_gatos"]], "LG_only"),
        _summary(comparison_df, "full_corridor"),
    ]
    return pd.DataFrame(rows)


def assess_lg_improvement(summary_df: pd.DataFrame) -> dict:
    """W7-4: Assess whether the optimisation improves LG-only annual ridership.

    Returns:
        dict with keys:
            lg_delta_annual: int -- delta from LG_only row's delta_annual
            lg_delta_pct:    float -- delta / baseline * 100 (0 if baseline 0)
            status:          str -- "REGRESSION" if delta_annual <= 0, "IMPROVED" otherwise
            banner_text:     str -- text for the dashboard regression banner
    """
    lg = next(
        (r for r in summary_df.to_dict("records") if r.get("scope") == "LG_only"),
        None,
    )
    if lg is None:
        return {
            "lg_delta_annual": 0,
            "lg_delta_pct": 0.0,
            "status": "REGRESSION",
            "banner_text": "LG_only row missing from summary — cannot assess improvement.",
        }

    delta = int(lg.get("delta_annual", 0))
    baseline = float(lg.get("baseline_annual", 0))
    pct = (delta / baseline * 100.0) if baseline > 0 else 0.0

    if delta <= 0:
        status = "REGRESSION"
        banner_text = (
            f"OPTIMIZATION REGRESSION: LG annual boardings delta = {delta:+,}/yr "
            f"({pct:+.1f}%). The proposed stop changes degrade LG service. "
            f"Inspect route27_stop_comparison.csv (status=REMOVED) for stops being dropped."
        )
    else:
        status = "IMPROVED"
        banner_text = (
            f"LG ridership improved: {delta:+,}/yr ({pct:+.1f}%) above baseline "
            f"({int(baseline):,}/yr)."
        )

    return {
        "lg_delta_annual": delta,
        "lg_delta_pct": round(pct, 2),
        "status": status,
        "banner_text": banner_text,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--suggestions", default="outputs/tables/route27_stop_suggestions.csv")
    p.add_argument("--full-stops", default="data/processed/route27_full_stops.csv")
    p.add_argument("--out-comparison", default="outputs/tables/route27_stop_comparison.csv")
    p.add_argument("--out-summary", default="outputs/tables/route27_comparison_summary.csv")
    p.add_argument("--out-assessment", default="outputs/tables/route27_lg_assessment.csv")
    p.add_argument("--tolerance-ft", type=float, default=MATCH_TOLERANCE_FT)
    p.add_argument("--reabsorption-rate", type=float, default=REABSORPTION_RATE)
    p.add_argument("--baseline-headway-min", type=float, default=30.0,
                   help="Current peak headway in minutes (default 30)")
    p.add_argument("--optimised-headway-min", type=float, default=15.0,
                   help="Proposed peak headway in minutes (default 15, per W4 schedule)")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cmp_df = build_stop_comparison(
        suggestions_csv=Path(args.suggestions),
        full_stops_csv=Path(args.full_stops),
        match_tolerance_ft=args.tolerance_ft,
        reabsorption_rate=args.reabsorption_rate,
    )
    summary_df = compute_summary_tiles(
        cmp_df,
        reabsorption_rate=args.reabsorption_rate,
        baseline_headway_min=args.baseline_headway_min,
        optimised_headway_min=args.optimised_headway_min,
    )

    # W7-4: regression gate
    assessment = assess_lg_improvement(summary_df)
    if assessment["status"] == "REGRESSION":
        logger.error(
            "LG OPTIMIZATION REGRESSION: LG annual boardings delta = %+,d/yr (%+.1f%%). "
            "The proposed stop changes degrade LG service. Inspect "
            "outputs/tables/route27_stop_comparison.csv (status=REMOVED) for stops being dropped.",
            assessment["lg_delta_annual"],
            assessment["lg_delta_pct"],
        )

    Path(args.out_comparison).parent.mkdir(parents=True, exist_ok=True)
    cmp_df.to_csv(args.out_comparison, index=False)
    summary_df.to_csv(args.out_summary, index=False)
    pd.DataFrame([assessment]).to_csv(args.out_assessment, index=False)

    print("=" * 78)
    print("Route 27 stop comparison")
    print("-" * 78)
    print(summary_df.to_string(index=False))
    print("-" * 78)
    print(f"LG assessment: {assessment['status']} ({assessment['lg_delta_annual']:+,}/yr, "
          f"{assessment['lg_delta_pct']:+.1f}%)")
    print("-" * 78)
    print(f"Wrote: {args.out_comparison}")
    print(f"Wrote: {args.out_summary}")
    print(f"Wrote: {args.out_assessment}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
