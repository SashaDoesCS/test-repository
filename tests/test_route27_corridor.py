"""test_route27_corridor.py -- P1.4 validation tests for Route 27 corridor fixes.

Tests cover:
  - GTFS shape loading (P1.1)
  - Corridor uses GTFS shapes by default (P1.1)
  - All candidates within snap tolerance (P1.3)
  - No zero-s_coord for unmatched existing stops (Bug 1 / P1.2)
  - Mandatory stops present in selected set (P1.2)
  - No spacing violations in selected stops (P1.2)

Integration tests (marked @pytest.mark.integration) hit the real
OSM/GTFS pipeline and require the cached data files in data/geospatial/.
"""

import sys
import math
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import pytest

# Make sure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.route27_corridor import (
    load_route27_shape_from_gtfs,
    build_route27_corridor,
    compute_s_coordinates,
    project_to_path,
    _haversine_ft,
    FORCED_CANDIDATES,
    STOP_SPACING,
    URBAN_DISTRICTS,
)
from src.route27_optimizer import _merge_existing_stops


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GTFS_DIR = Path("data/geospatial/gtfs")


def _simple_path() -> Tuple[List[Tuple[float, float]], List[float]]:
    """A tiny synthetic path for unit tests: straight line, ~1 mile."""
    coords = [
        (37.2581, -121.9498),   # Winchester TC (s=0)
        (37.2524, -121.9572),   # LG Blvd & Lark (~0.5 mi)
        (37.2249, -121.9806),   # Downtown LG (~2 mi)
    ]
    s_coords = compute_s_coordinates(coords)
    return coords, s_coords


# ---------------------------------------------------------------------------
# P1.1 — GTFS shape loading
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_gtfs_shape_loads():
    """load_route27_shape_from_gtfs returns ≥ 100 points spanning ≥ 4 miles."""
    coords = load_route27_shape_from_gtfs(_GTFS_DIR)
    assert coords is not None, "load_route27_shape_from_gtfs returned None"
    assert len(coords) >= 100, (
        f"Expected ≥100 shape points, got {len(coords)}"
    )
    s_coords = compute_s_coordinates(coords)
    total_mi = s_coords[-1] / 5280
    assert total_mi >= 4.0, (
        f"Expected shape length ≥ 4 miles, got {total_mi:.2f} miles"
    )


@pytest.mark.integration
def test_corridor_uses_gtfs_when_available():
    """With default config, build_route27_corridor reports corridor_source='gtfs_shapes'."""
    config = {}
    result = build_route27_corridor(config)
    assert result["corridor_source"] == "gtfs_shapes", (
        f"Expected corridor_source='gtfs_shapes', got '{result['corridor_source']}'"
    )
    # Path should have far more points than the 10-anchor fallback
    assert len(result["path_coords"]) > 50, (
        f"GTFS shape should produce >50 path points, got {len(result['path_coords'])}"
    )


# ---------------------------------------------------------------------------
# P1.3 — All candidates within snap tolerance
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_all_candidates_within_snap_tolerance():
    """Every row in candidates_df has snap_dist_ft <= 300 (P1.9 default)."""
    config = {}
    result = build_route27_corridor(config)
    df = result["candidates_df"]
    if df is None or len(df) == 0:
        pytest.skip("No candidates produced (OSM graph unavailable).")
    # OSM intersection candidates must be within tolerance.  Forced candidates
    # (P1.12) record the original off-corridor distance for QA but their
    # rendered stop_lat/stop_lon is now the snapped point on the path, so they
    # are exempt from the tolerance check.
    if "is_forced" in df.columns:
        osm_only = df[~df["is_forced"].fillna(False).astype(bool)]
    else:
        osm_only = df
    over_limit = osm_only[osm_only["snap_dist_ft"] > 300]
    assert len(over_limit) == 0, (
        f"{len(over_limit)} OSM candidates exceed 300 ft snap tolerance:\n"
        f"{over_limit[['candidate_id', 'snap_dist_ft']].to_string()}"
    )


# ---------------------------------------------------------------------------
# P1.2 — Bug 1: no zero s_coord for unmatched existing stops
# ---------------------------------------------------------------------------

def test_no_zero_s_coord_unmatched():
    """Unmatched existing stop placed exactly on the path is appended with
    s_coord_ft matching the expected arc-length (within ±100 ft) — NOT
    silently dropped, NOT clustered at s=0.

    P1.7 hardening: previously this test accepted both 'appended with s>0'
    and 'silently dropped'.  That hides the regression where the unmatched
    stop is dropped because path coords were missing.  Now we require
    presence and an accurate s-coordinate.
    """
    path_coords, path_s_coords = _simple_path()

    candidates_df = pd.DataFrame([{
        "candidate_id":          "R27_OSM_TEST_1",
        "stop_lat":              37.2581,
        "stop_lon":              -121.9498,
        "s_coord_ft":            0.0,
        "district_id":           None,
        "is_existing":           False,
        "is_mandatory":          False,
        "is_forced":             False,
        "street_names":          "Winchester & Lark",
        "raw_walkshed_pop":      100,
        "equity_walkshed_pop":   100.0,
        "marginal_walkshed_pop": 100,
        "tdi":                   0.3,
        "equity_priority":       False,
        "activity_type":         "intersection",
        "source":                "test",
        "snap_dist_ft":          0.0,
    }])

    # Place the existing stop exactly at path_coords[1] (LG Blvd & Lark).
    # Its expected s-coordinate is path_s_coords[1].
    existing_lat = path_coords[1][0]
    existing_lon = path_coords[1][1]
    expected_s = path_s_coords[1]

    existing_df = pd.DataFrame([{
        "stop_id":   "EX_LGBLVD_LARK",
        "stop_name": "LG Blvd & Lark",
        "stop_lat":  existing_lat,
        "stop_lon":  existing_lon,
    }])

    result_df = _merge_existing_stops(
        candidates_df, existing_df,
        path_coords=path_coords,
        path_s_coords=path_s_coords,
    )

    ex_rows = result_df[result_df["candidate_id"] == "EX_LGBLVD_LARK"]
    assert len(ex_rows) == 1, (
        "On-path existing stop must be appended (not silently dropped). "
        f"Got {len(ex_rows)} rows."
    )
    s_val = ex_rows.iloc[0]["s_coord_ft"]
    assert s_val > 0, (
        f"Unmatched existing stop got s_coord_ft={s_val}; expected > 0"
    )
    assert abs(s_val - expected_s) < 100, (
        f"s_coord_ft={s_val:.1f} differs from expected {expected_s:.1f} "
        f"by more than 100 ft"
    )


def test_far_offshape_stop_dropped():
    """An existing stop > 500 ft off the path is dropped (not appended at s=0).

    Guards against the regression where off-route stops re-cluster at s=0.
    """
    path_coords, path_s_coords = _simple_path()

    candidates_df = pd.DataFrame([{
        "candidate_id":          "R27_OSM_TEST_1",
        "stop_lat":              37.2581,
        "stop_lon":              -121.9498,
        "s_coord_ft":            0.0,
        "district_id":           None,
        "is_existing":           False,
        "is_mandatory":          False,
        "is_forced":             False,
        "street_names":          "X & Y",
        "raw_walkshed_pop":      100,
        "equity_walkshed_pop":   100.0,
        "marginal_walkshed_pop": 100,
        "tdi":                   0.3,
        "equity_priority":       False,
        "activity_type":         "intersection",
        "source":                "test",
        "snap_dist_ft":          0.0,
    }])

    # Far-off-path stop: ~3 miles south of any path point.
    existing_df = pd.DataFrame([{
        "stop_id":   "EX_FAR",
        "stop_name": "Far Off Route",
        "stop_lat":  37.180,
        "stop_lon":  -121.900,
    }])

    result_df = _merge_existing_stops(
        candidates_df, existing_df,
        path_coords=path_coords,
        path_s_coords=path_s_coords,
    )

    ex_rows = result_df[result_df["candidate_id"] == "EX_FAR"]
    assert len(ex_rows) == 0, (
        "Off-route existing stop (>500 ft snap_dist) must be dropped, "
        f"not appended.  Got {len(ex_rows)} rows."
    )


def test_no_zero_s_coord_unmatched_on_path():
    """Existing stop clearly on the path gets s_coord_ft > 0."""
    path_coords, path_s_coords = _simple_path()

    # Midpoint of path segment 0→1 — guaranteed on the path
    mid_lat = (path_coords[0][0] + path_coords[1][0]) / 2
    mid_lon = (path_coords[0][1] + path_coords[1][1]) / 2

    candidates_df = pd.DataFrame([{
        "candidate_id":          "R27_OSM_FAR",
        "stop_lat":              37.200,    # nowhere near
        "stop_lon":              -121.800,
        "s_coord_ft":            99999.0,
        "district_id":           None,
        "is_existing":           False,
        "is_mandatory":          False,
        "is_forced":             False,
        "street_names":          "Far Away St",
        "raw_walkshed_pop":      0,
        "equity_walkshed_pop":   0.0,
        "marginal_walkshed_pop": 0,
        "tdi":                   0.2,
        "equity_priority":       False,
        "activity_type":         "intersection",
        "source":                "test",
        "snap_dist_ft":          9999.0,
    }])

    existing_df = pd.DataFrame([{
        "stop_id":   "EX_MID",
        "stop_name": "Mid Path Stop",
        "stop_lat":  mid_lat,
        "stop_lon":  mid_lon,
    }])

    result_df = _merge_existing_stops(
        candidates_df, existing_df,
        path_coords=path_coords,
        path_s_coords=path_s_coords,
    )

    ex_rows = result_df[result_df["candidate_id"] == "EX_MID"]
    assert len(ex_rows) == 1, "Midpoint stop should be appended"
    s_val = ex_rows.iloc[0]["s_coord_ft"]
    assert s_val > 0, f"Midpoint stop should have s_coord_ft > 0, got {s_val}"


# ---------------------------------------------------------------------------
# P1.2 — Mandatory stops present
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_mandatory_stops_present():
    """Winchester TC and downtown LG forced candidates appear in candidates_df."""
    config = {}
    result = build_route27_corridor(config)
    df = result["candidates_df"]
    assert df is not None and len(df) > 0, "candidates_df is empty"

    # Winchester TC (R27_FORCE_001) and Downtown LG (R27_FORCE_002) must appear
    mandatory_ids = {"R27_FORCE_001", "R27_FORCE_002"}
    present_ids = set(df["candidate_id"].tolist())
    missing = mandatory_ids - present_ids
    assert not missing, (
        f"Mandatory forced candidates missing from candidates_df: {missing}"
    )

    # They must be flagged is_mandatory
    for fid in mandatory_ids:
        row = df[df["candidate_id"] == fid].iloc[0]
        assert row.get("is_mandatory", False) or row.get("is_forced", False), (
            f"Candidate {fid} is not flagged mandatory/forced"
        )


# ---------------------------------------------------------------------------
# P1.2 — No spacing violations in selected stops
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_no_spacing_violations():
    """No two selected stops are closer than min_ft for their zone."""
    from src.route27_optimizer import select_route27_stops
    from src.route27_walkshed import run_walkshed_analysis

    config = {}
    corridor = build_route27_corridor(config)
    candidates_df = corridor["candidates_df"]

    if candidates_df is None or len(candidates_df) == 0:
        pytest.skip("No candidates produced.")

    walkshed_df = run_walkshed_analysis(
        candidates_df=candidates_df,
        census_df=pd.DataFrame(),
        tdi_df=pd.DataFrame(),
        unmet_need_df=None,
    )

    selected_df, _ = select_route27_stops(
        walkshed_df, None, config,
        path_coords=corridor["path_coords"],
        path_s_coords=corridor["path_s_coords"],
    )

    if len(selected_df) < 2:
        pytest.skip("Fewer than 2 stops selected.")

    sel = selected_df.sort_values("s_coord_ft").reset_index(drop=True)

    violations = []
    for i in range(len(sel) - 1):
        a = sel.iloc[i]
        b = sel.iloc[i + 1]
        gap = b["s_coord_ft"] - a["s_coord_ft"]
        # Use the stricter (smaller) min_ft of the two endpoints
        did_a = a.get("district_id")
        did_b = b.get("district_id")
        zone_a = "urban" if did_a in URBAN_DISTRICTS else "suburban"
        zone_b = "urban" if did_b in URBAN_DISTRICTS else "suburban"
        # Urban min is smaller so is stricter between two urban stops
        min_ft_a = STOP_SPACING[zone_a]["min_ft"]
        min_ft_b = STOP_SPACING[zone_b]["min_ft"]
        # If either stop is forced/mandatory/existing the pipeline always keeps
        # it regardless of spacing — skip spacing check for that pair.
        # (The optimizer guarantees FTA spacing only for purely optional stops.)
        either_required = (
            bool(a.get("is_mandatory")) or bool(a.get("is_forced"))
            or bool(a.get("is_existing"))
            or bool(b.get("is_mandatory")) or bool(b.get("is_forced"))
            or bool(b.get("is_existing"))
        )
        if not either_required and gap < min(min_ft_a, min_ft_b):
            violations.append(
                f"  [{i}→{i+1}] gap={gap:.0f} ft < min={min(min_ft_a, min_ft_b):.0f} ft  "
                f"({a.get('street_names','?')} → {b.get('street_names','?')})"
            )

    assert not violations, (
        f"Spacing violations found:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# project_to_path unit test
# ---------------------------------------------------------------------------

def test_project_to_path_basic():
    """project_to_path returns s=0 for start of path and s>0 for later points."""
    path_coords, path_s_coords = _simple_path()

    # Project the first point — should be at s≈0, dist≈0
    s, d = project_to_path(path_coords[0][0], path_coords[0][1],
                            path_coords, path_s_coords)
    assert s == pytest.approx(0.0, abs=10), f"Start point s={s}, expected ≈0"
    assert d < 10, f"Start point dist={d} ft, expected <10 ft"

    # Project the last point — should be at s>0
    s_end, d_end = project_to_path(path_coords[-1][0], path_coords[-1][1],
                                   path_coords, path_s_coords)
    assert s_end > 1000, f"Last point s={s_end}, expected >1000 ft"
    assert d_end < 10, f"Last point dist={d_end} ft, expected <10 ft"


def test_project_to_path_empty_path():
    """P1.8: empty path returns (0.0, inf) — never (0.0, 0.0).

    Regression guard: if the corridor failed to load and path_coords is
    empty, we must NOT silently report snap_dist=0 (which would let any
    point be accepted as 'on the path').  Returning inf forces the
    snap-distance filter to drop the point.
    """
    s, d = project_to_path(37.25, -121.96, [], [])
    assert s == 0.0
    assert d == float("inf"), f"Empty path should return inf, got {d}"


def test_project_to_path_single_point():
    """P1.8: a path with only one point is degenerate — (0.0, inf)."""
    s, d = project_to_path(37.25, -121.96, [(37.25, -121.96)], [0.0])
    assert s == 0.0
    assert d == float("inf"), f"Single-point path should return inf, got {d}"


# ---------------------------------------------------------------------------
# P1.9 — Known Route 27 intersections present
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_known_route27_intersections_present():
    """Two well-known Route 27 intersections survive snap+filter and reach
    candidates_df.  Coordinates approximate; we look for any candidate
    within ~400 ft of each reference point.

    Reference points (Google Maps):
      - Camden Ave & Blossom Hill Rd:  ~37.243, -121.955
      - Los Gatos Blvd & Lark Ave:     ~37.252, -121.957
    """
    config = {}
    result = build_route27_corridor(config)
    df = result["candidates_df"]
    if df is None or len(df) == 0:
        pytest.skip("No candidates produced (OSM graph unavailable).")

    references = [
        ("Camden & Blossom Hill", 37.243, -121.955),
        ("LG Blvd & Lark",        37.252, -121.957),
    ]

    missing = []
    for name, ref_lat, ref_lon in references:
        # 400 ft ≈ 0.0011° lat at 37°N
        dlat = (df["stop_lat"] - ref_lat) * 364_000
        dlon = (df["stop_lon"] - ref_lon) * 288_500
        dist_ft = (dlat ** 2 + dlon ** 2) ** 0.5
        if dist_ft.min() > 400:
            missing.append(f"{name} (closest candidate {dist_ft.min():.0f} ft)")

    assert not missing, (
        "Known Route 27 intersections missing from candidates_df:\n  "
        + "\n  ".join(missing)
    )
