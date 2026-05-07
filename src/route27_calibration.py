"""
route27_calibration.py -- LG-anchored ridership estimator + plausibility caps.

Loads the empirical Route 27 LG-only anchor produced by src.vta_rbs (typical-day
boardings per stop, derived from VTA OCT 2025 RBS), exposes:

  - LGAnchor dataclass: baseline annual, p90 per-stop daily ceiling, observed
    capture rate (boardings/day per walkshed person)
  - HARD_CAP_LG_ANNUAL = 150_000   visible-red error if exceeded
  - WARN_THRESHOLD_LG_ANNUAL = 120_000   amber warning
  - cap_per_stop_daily(anchor)   per-new-stop boarding ceiling (p90 of
    observed LG stops -- a new stop in LG cannot realistically out-board
    the existing 90th-percentile LG stop)
  - estimate_new_stop_boardings(walkshed_pop, tdi, anchor)   capped, anchored
    replacement for the unbounded
        marginal_pop * diversion * tdi_adj
    estimator in route27_optimizer.compute_stop_bcr.
  - check_total_lg_uplift(predicted_annual, anchor)   returns
    ("OK"|"WARN"|"OVER_CAP", banner_text, details_dict).

Why caps exist:
    Los Gatos has ~30,000 residents and current transit mode share <1%. Even
    aggressive scenarios (pre-pandemic 2017 LG ridership ~570k/yr) sit far
    below the theoretical mode-share ceiling but also far above the current
    ~60k/yr. The 150k hard cap (~2.5x current, ~26% of 2017 peak) is the
    upper edge of "credible 5-year recovery + modest growth"; anything above
    that indicates a model bug (per-stop cap not firing, walkshed
    double-counting, calibration not applied).

References:
    - VTA OCT 2025 RBS (data/processed/route27_lg_stops.csv via src.vta_rbs)
    - VTA Title VI Service Standards FY2023 (Route 27 annual unlinked trips)
    - TCRP Report 95 Ch.9 (frequency-ridership elasticity ~0.5)
    - VTA Next Network 2019 productivity uplift (+4%)
    - VTA Visionary Network projection (+45-70% with 83% more service)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Plausibility thresholds for LG-only Route 27 annual boardings.
HARD_CAP_LG_ANNUAL = 150_000
WARN_THRESHOLD_LG_ANNUAL = 120_000

# Default LG anchor table location (produced by src.vta_rbs).
DEFAULT_ANCHOR_CSV = Path("data/processed/route27_lg_stops.csv")

# Annualization weights -- typical day to year. Mirror src.vta_rbs.ANNUAL_WEIGHTS
# but kept local so this module can be imported without that dependency.
ANNUAL_WEIGHTS = {"weekday": 255, "saturday": 52, "sunday": 52}


@dataclass(frozen=True)
class LGAnchor:
    """Empirical Los Gatos Route 27 ridership anchor (typical-day basis).

    Built from src.vta_rbs.build_lg_anchor_table output.
    """
    n_lg_stops: int
    baseline_daily_weekday: float          # sum of typical-weekday boardings across LG
    baseline_daily_saturday: float
    baseline_daily_sunday: float
    baseline_annual: int                   # using ANNUAL_WEIGHTS
    per_stop_p90_daily: float              # 90th percentile of LG stop daily wkdy boardings
    per_stop_median_daily: float           # median (sanity ref)
    per_stop_max_daily: float              # observed top stop (cap-of-last-resort)


def load_lg_anchor(anchor_csv: Path = DEFAULT_ANCHOR_CSV) -> LGAnchor:
    """Build an LGAnchor from the W0 per-stop CSV.

    Raises:
        FileNotFoundError: if the CSV is missing (run ``python -m src.vta_rbs``).
    """
    if not anchor_csv.exists():
        raise FileNotFoundError(
            f"LG anchor CSV not found: {anchor_csv}. "
            f"Run `python -m src.vta_rbs` to build it from VTA OCT 2025 RBS data."
        )
    df = pd.read_csv(anchor_csv)
    if df.empty:
        raise ValueError(f"LG anchor CSV is empty: {anchor_csv}")

    wd = float(df["weekday_boardings"].sum())
    sa = float(df["saturday_boardings"].sum())
    su = float(df["sunday_boardings"].sum())
    annual = int(round(
        wd * ANNUAL_WEIGHTS["weekday"]
        + sa * ANNUAL_WEIGHTS["saturday"]
        + su * ANNUAL_WEIGHTS["sunday"]
    ))
    daily_wd_per_stop = df["weekday_boardings"].astype(float)
    p90 = float(np.percentile(daily_wd_per_stop, 90))
    med = float(np.median(daily_wd_per_stop))
    mx = float(daily_wd_per_stop.max())

    anchor = LGAnchor(
        n_lg_stops=int(len(df)),
        baseline_daily_weekday=round(wd, 1),
        baseline_daily_saturday=round(sa, 1),
        baseline_daily_sunday=round(su, 1),
        baseline_annual=annual,
        per_stop_p90_daily=round(p90, 2),
        per_stop_median_daily=round(med, 2),
        per_stop_max_daily=round(mx, 2),
    )
    logger.info(
        "Loaded LG anchor: %d stops, %.0f wkdy boardings/day, %d/yr; "
        "per-stop p90=%.1f median=%.1f max=%.1f",
        anchor.n_lg_stops, anchor.baseline_daily_weekday, anchor.baseline_annual,
        anchor.per_stop_p90_daily, anchor.per_stop_median_daily, anchor.per_stop_max_daily,
    )
    return anchor


# Headroom over the busiest currently observed LG stop. A well-placed new stop
# might modestly exceed today's top stop (Good Samaritan Hospital ~17/day), but
# shouldn't dramatically out-perform it given the town's <1% transit mode share.
# 25% headroom is judgment-based and is documented in the cap_basis output column
# so reviewers can see when a row hit the ceiling.
PER_STOP_HEADROOM_OVER_OBSERVED_MAX = 1.25


def per_stop_daily_cap(anchor: LGAnchor) -> float:
    """Empirical ceiling for a single new stop's typical-weekday boardings.

    Uses the busiest observed LG stop * 25% headroom. The p90 (~5.8/day) is
    too restrictive because most LG stops see <5/day -- but we know stops at
    major activity generators (hospitals, schools, transit centres) can see
    15-20/day, and a new well-placed stop could plausibly match those.
    """
    return round(anchor.per_stop_max_daily * PER_STOP_HEADROOM_OVER_OBSERVED_MAX, 2)


def estimate_new_stop_daily_boardings(
    walkshed_pop: float,
    tdi: float,
    anchor: LGAnchor,
    diversion_rate: float = 0.08,
) -> tuple[float, str]:
    """Estimate typical-weekday boardings for a NEW stop, capped at the
    empirical ceiling (busiest LG stop + 25% headroom).

    Returns:
        (boardings_per_day, basis) where basis is "uncapped" or "capped_at_max"
        so the caller can flag rows whose value was clamped.

    Method:
        raw = walkshed_pop * diversion_rate * tdi_adj    (TCRP 167 §4.3.2)
        tdi_adj in [0.75, 1.25]
        capped = min(raw, per_stop_daily_cap(anchor))

    The cap exists because Los Gatos has ~30k residents and <1% transit mode
    share. The busiest currently observed LG Route 27 stop sees ~17 boardings/
    weekday (Good Samaritan Hospital). No new stop in LG can realistically
    out-perform that by a large margin. A single new stop predicting 100+/day
    is a model bug (walkshed double-counting, missing calibration, etc.).
    """
    tdi_adj = max(0.75, min(1.25, 1.0 + (tdi - 0.5) * 0.5))
    raw = max(0.0, walkshed_pop * diversion_rate * tdi_adj)
    cap = per_stop_daily_cap(anchor)
    if raw <= cap:
        return raw, "uncapped"
    return cap, "capped_at_max"


def check_total_lg_uplift(
    predicted_lg_annual: float,
    anchor: LGAnchor,
) -> tuple[str, str, dict]:
    """Compare a model-predicted LG annual boardings to the plausibility caps.

    Returns:
        (status, banner_text, details) where status is one of
        "OK" | "WARN" | "OVER_CAP".
    """
    baseline = anchor.baseline_annual
    delta = predicted_lg_annual - baseline
    multiple = predicted_lg_annual / baseline if baseline else float("inf")
    details = {
        "predicted_lg_annual": int(round(predicted_lg_annual)),
        "baseline_lg_annual": baseline,
        "delta_annual": int(round(delta)),
        "multiple_of_baseline": round(multiple, 2),
        "warn_threshold": WARN_THRESHOLD_LG_ANNUAL,
        "hard_cap": HARD_CAP_LG_ANNUAL,
    }

    if predicted_lg_annual > HARD_CAP_LG_ANNUAL:
        status = "OVER_CAP"
        banner = (
            f"MODEL OUTPUT EXCEEDS PHYSICAL PLAUSIBILITY CAP. "
            f"Predicted LG-only Route 27 annual boardings = {int(predicted_lg_annual):,} "
            f"vs hard cap {HARD_CAP_LG_ANNUAL:,} (current observed {baseline:,}, "
            f"multiple = {multiple:.2f}x). Diagnose before publishing -- check the "
            f"per-stop cap, walkshed double-counting, and calibration scaling."
        )
    elif predicted_lg_annual > WARN_THRESHOLD_LG_ANNUAL:
        status = "WARN"
        banner = (
            f"Predicted LG-only Route 27 annual boardings = {int(predicted_lg_annual):,} "
            f"({multiple:.2f}x current baseline {baseline:,}). At the upper edge of "
            f"comparable VTA projects (Next Network +4%, Visionary +45-70%). "
            f"Verify per-stop estimates before publishing."
        )
    else:
        status = "OK"
        banner = (
            f"Predicted LG-only Route 27 annual boardings = {int(predicted_lg_annual):,} "
            f"({multiple:.2f}x baseline {baseline:,}, +{int(delta):,}). "
            f"Within plausibility envelope (warn at {WARN_THRESHOLD_LG_ANNUAL:,}, "
            f"cap at {HARD_CAP_LG_ANNUAL:,})."
        )
    return status, banner, details


def top_lg_contributors(suggestions_df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Return the top-N LG stops by est_annual_boardings for diagnostic display.

    Used in the dashboard's reconciliation block when the cap fires so the
    analyst can see at a glance which stops are driving the over-projection.
    """
    if suggestions_df is None or suggestions_df.empty:
        return pd.DataFrame()
    cols_pref = ["stop_id", "stop_name", "status", "est_new_riders_daily",
                 "est_annual_boardings", "bcr_20yr"]
    cols = [c for c in cols_pref if c in suggestions_df.columns]
    if "est_annual_boardings" not in suggestions_df.columns:
        return pd.DataFrame(columns=cols)
    return (
        suggestions_df.sort_values("est_annual_boardings", ascending=False)
        .head(n)[cols]
        .reset_index(drop=True)
    )
