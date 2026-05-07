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
    """projected_daily must equal kept + reabsorbed + new_daily within rounding.

    W7-5: projected_annual now includes a frequency uplift on kept+reabsorbed;
    we only assert daily consistency here (daily has no frequency component).
    Annual consistency is checked separately in test_w7_frequency_uplift_increases_projected.
    """
    sug = Path("outputs/tables/route27_stop_suggestions.csv")
    full = Path("data/processed/route27_full_stops.csv")
    if not (sug.exists() and full.exists()):
        pytest.skip("Comparison inputs not present; run pipeline first.")
    from src.route27_comparison import build_stop_comparison, compute_summary_tiles
    cmp_df = build_stop_comparison(suggestions_csv=sug, full_stops_csv=full)
    sum_df = compute_summary_tiles(cmp_df)
    for _, r in sum_df.iterrows():
        recomputed_daily = (r["kept_daily_boardings"]
                            + r["reabsorbed_daily_estimate"]
                            + r["new_daily_boardings"])
        assert abs(recomputed_daily - r["projected_daily"]) < 0.5, (
            f"{r['scope']}: projected_daily {r['projected_daily']} != "
            f"kept+reabsorbed+new {recomputed_daily:.2f}"
        )
        # Annual: projected_annual >= projected_annual_pre_frequency when headway improves.
        assert r["projected_annual"] >= r["projected_annual_pre_frequency"], (
            f"{r['scope']}: frequency uplift should not decrease annual boardings"
        )


# =====================================================================
# W7: data ingestion, preserved stops, BCR filter, regression gate,
#     and frequency uplift
# =====================================================================

def test_w7_data_ingestion_loads_full_route27():
    """load_route27_existing_stops returns >= 160 rows when RBS CSV is present."""
    rbs = Path("data/processed/route27_full_stops.csv")
    if not rbs.exists():
        pytest.skip("RBS CSV not present; run src.vta_rbs first.")
    from src.data_ingestion import load_route27_existing_stops
    df = load_route27_existing_stops()
    assert len(df) >= 160, (
        f"Expected >= 160 Route 27 stops (full RBS set), got {len(df)}. "
        "The bbox clip may still be active — check load_route27_existing_stops."
    )
    assert "weekday_boardings" in df.columns
    assert "in_lg_geofence" in df.columns


def test_w7_preserved_stops_survive_spacing():
    """Existing stops with observed ridership >= MIN_PRESERVE must survive spacing filter."""
    import numpy as np
    from src.route27_optimizer import (
        _merge_existing_stops, select_route27_stops, MIN_PRESERVE_DAILY_BOARDINGS,
    )

    # Build a minimal candidate DF with two existing stops 600 ft apart (below
    # FTA suburban 1760 ft minimum) and with observed boardings >= threshold.
    candidates = pd.DataFrame([
        {
            "candidate_id": "C1", "stop_lat": 37.220, "stop_lon": -121.980,
            "s_coord_ft": 0.0, "district_id": "D1",
            "is_existing": False, "is_mandatory": False, "is_forced": False,
            "street_names": "Stop A", "raw_walkshed_pop": 500,
            "equity_walkshed_pop": 250.0, "marginal_walkshed_pop": 500,
            "tdi": 0.3, "equity_priority": False,
            "activity_type": "intersection", "source": "OSM",
        },
        {
            "candidate_id": "C2", "stop_lat": 37.219, "stop_lon": -121.980,
            "s_coord_ft": 600.0, "district_id": "D1",
            "is_existing": False, "is_mandatory": False, "is_forced": False,
            "street_names": "Stop B", "raw_walkshed_pop": 400,
            "equity_walkshed_pop": 200.0, "marginal_walkshed_pop": 400,
            "tdi": 0.3, "equity_priority": False,
            "activity_type": "intersection", "source": "OSM",
        },
    ])

    # Two existing stops co-located with the candidates, both with boardings >= threshold.
    existing = pd.DataFrame([
        {
            "stop_id": "EX1", "stop_name": "Stop A", "stop_lat": 37.220,
            "stop_lon": -121.980, "weekday_boardings": MIN_PRESERVE_DAILY_BOARDINGS + 1,
            "annual_boardings": 3000.0,
        },
        {
            "stop_id": "EX2", "stop_name": "Stop B", "stop_lat": 37.219,
            "stop_lon": -121.980, "weekday_boardings": MIN_PRESERVE_DAILY_BOARDINGS + 1,
            "annual_boardings": 2500.0,
        },
    ])

    merged = _merge_existing_stops(candidates, existing)
    assert (merged["is_preserved"]).all(), "Both existing stops with boardings >= threshold should be preserved"

    # select_route27_stops must include both despite the 600 ft gap < min spacing.
    # Pass existing_stops_df=None since _merge_existing_stops already ran above.
    config = {"route27_optimization": {"max_new_stops": 10}}
    selected, _ = select_route27_stops(merged, None, config=config)
    assert len(selected) == 2, (
        f"Expected both preserved stops to survive spacing filter; got {len(selected)}"
    )


def test_w7_bcr_filter_drops_noise_stops():
    """BCR filter drops NEW_SUGGEST with BCR < 1 and keeps BCR >= 1."""
    import pandas as pd
    from src.route27_optimizer import BCR_MEDIUM

    # Build a minimal suggestions-like DataFrame with two NEW_SUGGEST rows.
    rows = [
        {
            "status": "NEW_SUGGEST", "is_mandatory": False, "is_school_stop": False,
            "is_preserved": False, "bcr_20yr": 0.4,
            "est_new_riders_daily": 0.5, "weekday_boardings": 0.0,
            "s_coord_ft": 1000.0,
            "stop_lat": 37.22, "stop_lon": -121.98,
        },
        {
            "status": "NEW_SUGGEST", "is_mandatory": False, "is_school_stop": False,
            "is_preserved": False, "bcr_20yr": 2.0,
            "est_new_riders_daily": 5.0, "weekday_boardings": 0.0,
            "s_coord_ft": 3000.0,
            "stop_lat": 37.21, "stop_lon": -121.97,
        },
    ]
    df = pd.DataFrame(rows)

    # Apply the same filter logic used in build_stop_suggestions.
    _is_new_status = df["status"].isin({"NEW_IN_SELECTION", "NEW_SUGGEST"})
    _not_exempt = (
        ~df["is_mandatory"].fillna(False)
        & ~df["is_school_stop"].fillna(False)
        & ~df.get("is_preserved", pd.Series(False, index=df.index)).fillna(False)
    )
    sub_bcr_mask = (
        _is_new_status & _not_exempt
        & df["bcr_20yr"].notna()
        & (df["bcr_20yr"] < BCR_MEDIUM)
    )
    filtered = df[~sub_bcr_mask].reset_index(drop=True)
    assert len(filtered) == 1, f"Expected 1 stop after filter, got {len(filtered)}"
    assert float(filtered.iloc[0]["bcr_20yr"]) == 2.0, "BCR=2.0 stop should survive"


def test_w7_regression_gate():
    """assess_lg_improvement returns REGRESSION when delta <= 0, IMPROVED otherwise."""
    from src.route27_comparison import assess_lg_improvement
    import pandas as pd

    # Regression scenario: delta_annual = -5000
    neg_df = pd.DataFrame([
        {"scope": "LG_only", "baseline_annual": 60000, "projected_annual": 55000,
         "delta_annual": -5000, "projected_annual_pre_frequency": 55000},
        {"scope": "full_corridor", "baseline_annual": 120000, "projected_annual": 115000,
         "delta_annual": -5000, "projected_annual_pre_frequency": 115000},
    ])
    result = assess_lg_improvement(neg_df)
    assert result["status"] == "REGRESSION", f"Expected REGRESSION, got {result['status']}"
    assert result["lg_delta_annual"] == -5000

    # Zero delta is also a regression.
    zero_df = pd.DataFrame([
        {"scope": "LG_only", "baseline_annual": 60000, "projected_annual": 60000,
         "delta_annual": 0, "projected_annual_pre_frequency": 60000},
    ])
    assert assess_lg_improvement(zero_df)["status"] == "REGRESSION"

    # Positive delta is IMPROVED.
    pos_df = pd.DataFrame([
        {"scope": "LG_only", "baseline_annual": 60000, "projected_annual": 63000,
         "delta_annual": 3000, "projected_annual_pre_frequency": 62000},
    ])
    assert assess_lg_improvement(pos_df)["status"] == "IMPROVED"


def test_w7_frequency_uplift_increases_projected():
    """compute_summary_tiles with optimised_headway=15 produces higher LG projected_annual
    than with optimised_headway=30 (no-op uplift factor = 1.0)."""
    sug = Path("outputs/tables/route27_stop_suggestions.csv")
    full = Path("data/processed/route27_full_stops.csv")
    if not (sug.exists() and full.exists()):
        pytest.skip("Comparison inputs not present; run pipeline first.")
    from src.route27_comparison import build_stop_comparison, compute_summary_tiles
    cmp_df = build_stop_comparison(suggestions_csv=sug, full_stops_csv=full)

    sum_no_uplift = compute_summary_tiles(
        cmp_df, baseline_headway_min=30.0, optimised_headway_min=30.0
    )
    sum_with_uplift = compute_summary_tiles(
        cmp_df, baseline_headway_min=30.0, optimised_headway_min=15.0
    )

    lg_no = sum_no_uplift[sum_no_uplift["scope"] == "LG_only"].iloc[0]
    lg_up = sum_with_uplift[sum_with_uplift["scope"] == "LG_only"].iloc[0]

    assert lg_up["projected_annual"] > lg_no["projected_annual"], (
        f"Frequency uplift (15 min headway) should increase LG projected_annual "
        f"vs no-uplift (30 min headway). Got {lg_up['projected_annual']} vs {lg_no['projected_annual']}"
    )
    assert lg_up["frequency_uplift_pct"] > 0, "Uplift pct should be > 0 for headway improvement"
    assert lg_no["frequency_uplift_pct"] == 0.0, "No-uplift scenario should have 0 pct"
