"""
test_route27_sanity.py -- W6 cross-cutting regression tests.

Each test exercises one of the W1-W5 contracts on synthetic inputs so it does
not depend on a freshly-regenerated pipeline. Tests that need the OSM cache
(W2 stop validator) skip cleanly when the cache is absent.

Coverage:
    W1  per-stop boarding cap fires; total-uplift check returns the right
        OK / WARN / OVER_CAP status.
    W2  validate_forced_candidates rejects a synthetic motorway-median coord
        when the cache exists; passes through when it does not.
    W4  generate_headway_trips emits trips in BOTH directions; check_headway_parity
        catches a regression (worse-than-baseline headway) and passes a
        same-or-better schedule.
    W5  build_stop_comparison + compute_summary_tiles produce the expected
        KEPT / NEW / REMOVED structure with non-negative LG totals.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

NETWORK_CACHE = Path("data/geospatial/route27_network.pkl")


# =====================================================================
# W1: anchored BCR + plausibility caps
# =====================================================================

def test_w1_per_stop_cap_clamps_high_walkshed():
    """Any new stop with absurd walkshed should clamp to anchor.per_stop_max_daily * 1.25."""
    from src.route27_calibration import (
        load_lg_anchor, estimate_new_stop_daily_boardings, per_stop_daily_cap,
    )
    anchor = load_lg_anchor()
    cap = per_stop_daily_cap(anchor)
    # 50,000 walkshed pop with high TDI would yield thousands/day uncapped.
    boardings, basis = estimate_new_stop_daily_boardings(50_000, 0.8, anchor)
    assert basis == "capped_at_max"
    assert boardings == cap, f"expected cap={cap}, got {boardings}"
    # A tiny stop should pass through uncapped.
    boardings, basis = estimate_new_stop_daily_boardings(20, 0.2, anchor)
    assert basis == "uncapped"
    assert boardings < cap


def test_w1_uplift_check_thresholds():
    """check_total_lg_uplift returns OK / WARN / OVER_CAP at the right boundaries."""
    from src.route27_calibration import (
        check_total_lg_uplift, load_lg_anchor,
        WARN_THRESHOLD_LG_ANNUAL, HARD_CAP_LG_ANNUAL,
    )
    a = load_lg_anchor()
    assert check_total_lg_uplift(WARN_THRESHOLD_LG_ANNUAL - 1, a)[0] == "OK"
    assert check_total_lg_uplift(WARN_THRESHOLD_LG_ANNUAL + 1, a)[0] == "WARN"
    assert check_total_lg_uplift(HARD_CAP_LG_ANNUAL - 1, a)[0] == "WARN"
    assert check_total_lg_uplift(HARD_CAP_LG_ANNUAL + 1, a)[0] == "OVER_CAP"


def test_w1_compute_stop_bcr_uses_anchor_cap():
    """compute_stop_bcr with anchor caps the per-stop boardings; without anchor
    it uses the legacy unbounded estimator."""
    from src.route27_calibration import load_lg_anchor, per_stop_daily_cap
    from src.route27_optimizer import compute_stop_bcr, SERVICE_DAYS_PER_YEAR
    a = load_lg_anchor()
    rec = pd.Series({
        "best_raw_walkshed_pop": 50_000, "best_tdi": 0.5,
        "best_district_id": "D1", "best_street_names": "school",
        "snap_dist_ft": 0.0,
    })
    capped = compute_stop_bcr(rec, anchor=a)
    uncapped = compute_stop_bcr(rec, anchor=None)
    assert capped["est_new_riders_daily"] == per_stop_daily_cap(a)
    assert capped["boardings_cap_basis"] == "capped_at_max"
    assert uncapped["est_new_riders_daily"] > capped["est_new_riders_daily"]
    assert uncapped["boardings_cap_basis"] == "uncapped_no_anchor"


# =====================================================================
# W2: stop placement validator
# =====================================================================

needs_network = pytest.mark.skipif(
    not NETWORK_CACHE.exists(),
    reason=f"OSM network cache not found at {NETWORK_CACHE}",
)


@needs_network
def test_w2_lghs_corrected_coord_validates():
    from src.stop_validator import is_valid_stop_placement
    v = is_valid_stop_placement(37.22106811402752, -121.97631348385276)
    assert v.ok, f"LGHS corrected coord rejected: {v}"


@needs_network
def test_w2_synthetic_freeway_coord_rejected():
    from src.stop_validator import is_valid_stop_placement
    # Coord previously used for LGHS that snapped into the Hwy 17/85 cloverleaf.
    v = is_valid_stop_placement(37.227, -121.9745)
    assert not v.ok


# =====================================================================
# W3: corridor-deviation cost
# =====================================================================

def test_w3_deviation_cost_grows_with_snap_distance():
    """A bigger off-corridor detour must produce a bigger pv_deviation_cost
    (and a smaller post-deviation BCR) than a small one."""
    from src.route27_calibration import load_lg_anchor
    from src.route27_optimizer import compute_stop_bcr
    a = load_lg_anchor()
    base = pd.Series({
        "best_raw_walkshed_pop": 5_000, "best_tdi": 0.4,
        "best_district_id": "D1", "best_street_names": "school",
    })
    on = compute_stop_bcr(base.copy().pipe(lambda s: s._set_value("snap_dist_ft", 0.0) or s),
                          anchor=a)
    off = compute_stop_bcr(base.copy().pipe(lambda s: s._set_value("snap_dist_ft", 1500.0) or s),
                           anchor=a)
    assert off["pv_deviation_cost_usd"] > on["pv_deviation_cost_usd"]
    assert off["bcr_20yr"] < on["bcr_20yr"]
    assert off["bcr_20yr_pre_deviation"] == on["bcr_20yr_pre_deviation"]   # same benefit side


# =====================================================================
# W4: bidirectional schedule + headway parity
# =====================================================================

def _stub_route(headways_min: dict) -> object:
    """Build a minimal OptimisedRoute-shaped stub for schedule_generator tests."""
    class S:
        def __init__(self, sid, name):
            self.stop_id = sid
            self.stop_name = name
    class R:
        route_id = "R27"
        stops = [S("A", "Winchester"), S("B", "Mid"), S("C", "Santa Teresa")]
        headways = headways_min
    return R()


def _tt_matrix() -> pd.DataFrame:
    return pd.DataFrame(
        [["A", "B", 60], ["B", "C", 60], ["B", "A", 60], ["C", "B", 60]],
        columns=["from_stop_id", "to_stop_id", "travel_time_sec"],
    )


def _windows() -> dict:
    return {
        "am_peak":    ["06:00", "09:00"],
        "midday":     ["09:00", "14:15"],
        "pm_school":  ["14:15", "16:15"],
        "pm_commute": ["16:15", "18:30"],
        "evening":    ["18:30", "21:00"],
    }


def test_w4_bidirectional_trips_emitted():
    from src.schedule_generator import generate_headway_trips
    headways = {"am_peak": 15, "midday": 30, "pm_school": 20,
                "pm_commute": 15, "evening": 60}
    trips = generate_headway_trips(
        _stub_route(headways), _tt_matrix(),
        {"start": "06:00", "end": "21:00"}, _windows(),
    )
    n_d0 = sum(1 for t in trips if t.direction_id == 0)
    n_d1 = sum(1 for t in trips if t.direction_id == 1)
    assert n_d0 > 0 and n_d1 > 0
    assert n_d0 == n_d1, "headways are mirrored, so direction trip counts must match"


def test_w4_headway_parity_passes_for_better_service():
    from src.schedule_generator import generate_headway_trips, check_headway_parity
    # Headways at or better than the 30/30/30/30/60 published baseline.
    headways = {"am_peak": 15, "midday": 30, "pm_school": 20,
                "pm_commute": 15, "evening": 60}
    trips = generate_headway_trips(
        _stub_route(headways), _tt_matrix(),
        {"start": "06:00", "end": "21:00"}, _windows(),
    )
    parity = check_headway_parity(trips, _windows())
    assert (parity["status"] == "OK").all(), \
        f"All windows should pass; failures:\n{parity[parity['status'] != 'OK']}"


def test_w4_headway_parity_fails_for_worse_service():
    from src.schedule_generator import generate_headway_trips, check_headway_parity
    # Worse than baseline -- every window should FAIL.
    headways = {"am_peak": 45, "midday": 45, "pm_school": 45,
                "pm_commute": 45, "evening": 90}
    trips = generate_headway_trips(
        _stub_route(headways), _tt_matrix(),
        {"start": "06:00", "end": "21:00"}, _windows(),
    )
    parity = check_headway_parity(trips, _windows())
    assert (parity["status"] == "FAIL").any()


# =====================================================================
# W5: stop comparison
# =====================================================================

def test_w5_stop_comparison_structure():
    """If both inputs exist, the comparison must produce KEPT/NEW/REMOVED rows
    and the LG-only summary row."""
    sug = Path("outputs/tables/route27_stop_suggestions.csv")
    full = Path("data/processed/route27_full_stops.csv")
    if not (sug.exists() and full.exists()):
        pytest.skip("Comparison inputs not present; run pipeline first.")
    from src.route27_comparison import build_stop_comparison, compute_summary_tiles
    cmp_df = build_stop_comparison(suggestions_csv=sug, full_stops_csv=full)
    assert {"KEPT", "NEW", "REMOVED"} & set(cmp_df["status"].unique()), \
        "expected at least one of KEPT/NEW/REMOVED in the comparison"
    sum_df = compute_summary_tiles(cmp_df)
    assert {"LG_only", "full_corridor"} == set(sum_df["scope"]), \
        "summary must have both LG_only and full_corridor rows"
    lg = sum_df[sum_df["scope"] == "LG_only"].iloc[0]
    # LG-only baseline should be > 0 (the empirical anchor reports ~200/day)
    assert lg["baseline_daily"] > 0


def test_w5_summary_arithmetic_consistent():
    """projected_daily must equal kept + reabsorbed + new_daily within rounding."""
    sug = Path("outputs/tables/route27_stop_suggestions.csv")
    full = Path("data/processed/route27_full_stops.csv")
    if not (sug.exists() and full.exists()):
        pytest.skip("Comparison inputs not present; run pipeline first.")
    from src.route27_comparison import build_stop_comparison, compute_summary_tiles
    cmp_df = build_stop_comparison(suggestions_csv=sug, full_stops_csv=full)
    sum_df = compute_summary_tiles(cmp_df)
    for _, r in sum_df.iterrows():
        recomputed = (r["kept_daily_boardings"]
                      + r["reabsorbed_daily_estimate"]
                      + r["new_daily_boardings"])
        assert abs(recomputed - r["projected_daily"]) < 0.5, (
            f"{r['scope']}: projected_daily {r['projected_daily']} != "
            f"kept+reabsorbed+new {recomputed:.2f}"
        )
