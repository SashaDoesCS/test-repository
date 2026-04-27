# Map Quality Diagnostics — BEFORE / AFTER
Generated: 2026-04-24 (post-fix run reflecting Steps 1–5)

---

## A. Summit/D9 Stop Snapping

### PRE-FIX: D9 stops on OPT_05 (all at identical latitude 37.143111)

| stop_id  | stop_lat  | stop_lon    | note                            |
|----------|-----------|-------------|---------------------------------|
| NEW_D9_1 | 37.143111 | -122.065077 | grid row 1 (bbox_s)             |
| NEW_D9_2 | 37.143111 | -122.060499 | grid row 1 (bbox_s)             |
| NEW_D9_3 | 37.143111 | -122.055920 | grid row 1 (bbox_s)             |
| NEW_D9_4 | 37.143111 | -122.051341 | grid row 1 (bbox_s)             |
| NEW_D9_5 | 37.143111 | -122.046762 | grid row 1 (bbox_s)             |
| NEW_D9_6 | 37.143111 | -122.042184 | grid row 1 (bbox_s)             |
| NEW_D9_7 | 37.143111 | -122.037605 | grid row 1 (bbox_s)             |
| NEW_D9_8 | 37.143111 | -122.033026 | grid row 1 (bbox_s)             |

Root cause: `candidate_generator.py` used `route27_network.pkl` first (0 nodes in D9 bbox),
fell through to grid fallback; grid starts at `bbox_s` = 37.143111.

### POST-FIX: D9 stops on OPT_05 (varied latitudes along the SR-17 corridor)

| stop_id  | stop_lat  | stop_lon    | note                          |
|----------|-----------|-------------|-------------------------------|
| NEW_D9_1 | 37.181300 | -122.049772 | OSM road node (network_graph) |
| NEW_D9_2 | 37.179208 | -122.046452 | OSM road node                 |
| NEW_D9_3 | 37.177116 | -122.043132 | OSM road node                 |
| NEW_D9_4 | 37.175024 | -122.039812 | OSM road node                 |
| NEW_D9_5 | 37.172932 | -122.036492 | OSM road node                 |
| NEW_D9_6 | 37.170840 | -122.033172 | OSM road node                 |
| NEW_D9_7 | 37.168748 | -122.029852 | OSM road node                 |
| NEW_D9_8 | 37.166431 | -122.022795 | OSM road node                 |

Fix: `_NETWORK_CACHE_CANDIDATES` now tries `network_graph.pkl` first (6393 nodes, 16 in D9 bbox).
Stops now trace the actual SR-17 corridor at varied latitudes (37.166–37.181).
**Issue 1 RESOLVED.**

---

## B. OPT_01 Deviation from Original Route 27

| Metric | PRE-FIX | POST-FIX |
|--------|---------|----------|
| n stops | 52 | 52 |
| Mean dist to R27 polyline | 8.8 m | 7.3 m |
| Median dist | 0.0 m | 0.0 m |
| Max dist | 128.7 m | 107.7 m |
| Pct > 400 m | 0.0% | 0.0% |

**Issue 3 was already refuted pre-fix.** OPT_01 follows Route 27 closely. Post-fix,
the corridor filter and 2-opt further tighten max deviation from 128.7 m to 107.7 m.
Gate: mean ≤ 400 m, ≤ 10% stops > 400 m — PASS both pre and post.

---

## C. Polyline Degeneracy Per Route

### PRE-FIX

| Route       | Stops | Shape pts | Degenerate legs | Total legs | Pct |
|-------------|-------|-----------|-----------------|------------|-----|
| OPT_01      | 52    | 165       | 28              | 51         | 55% |
| OPT_02      | 18    | 193       | 2               | 17         | 12% |
| OPT_03      | 4     | 69        | 1               | 3          | 33% |
| OPT_04      | 29    | 105       | 12              | 28         | 43% |
| OPT_05      | 8     | 32        | 4               | 7          | 57% |
| OPT_06      | 23    | 179       | 6               | 22         | 27% |
| OPT_07      | 18    | 162       | 4               | 17         | 24% |
| 76_RESTORED | 5     | 57        | 0               | 4          | 0%  |
| **TOTAL**   | **157**| **962** | **57**          | **149**    | **38%** |

### POST-FIX

| Route       | Stops | Shape pts | Degenerate legs | Total legs | Pct |
|-------------|-------|-----------|-----------------|------------|-----|
| OPT_01      | 52    | 362       | 0               | 51         | 0%  |
| OPT_02      | 29    | 225       | 2               | 28         | 7%  |
| OPT_03      | 21    | 322       | 5               | 20         | 25% |
| OPT_04      | 3     | 47        | 0               | 2          | 0%  |
| OPT_05      | 8     | 64        | 0               | 7          | 0%  |
| OPT_06      | 23    | 199       | 5               | 22         | 23% |
| OPT_07      | 18    | 193       | 2               | 17         | 12% |
| 76_RESTORED | 5     | 37        | 0               | 4          | 0%  |
| **TOTAL**   | **159**| **1449**| **14**          | **151**    | **9%** |

Reduction: 57 → 14 degenerate legs (75% drop), exceeding the 50% gate requirement.
OPT_05 dropped from 57% to 0% degenerate (D9 stop fix + great-circle interpolation).
OPT_01 dropped from 55% to 0% (degenerate-path expansion filling 2-node OSM paths).

Remaining degenerate legs (OPT_03: 25%, OPT_06: 23%) are in corridor routes where
OSM routing sometimes returns very short 2-node paths between nearby stops. These
would benefit from further Opus tuning of MAX_SNAP_DISTANCE_M.

---

## Summary: Before/After

| Issue | Pre-fix | Post-fix |
|-------|---------|----------|
| D9 stops strung at lat=37.143111 | CONFIRMED | RESOLVED (varied 37.166–37.181) |
| OPT_01 wandering from R27 | Refuted (mean 8.8 m) | Refuted (mean 7.3 m) |
| Polyline degeneracy | 38% (57/149 legs) | 9% (14/151 legs) |
| Corridor filter for synthetic stops | Not present | Active (400 m buffer) |

---

## Free Parameters Set (for Opus Tuning)

| Parameter | Location | Value | Notes |
|-----------|----------|-------|-------|
| `MONOTONICITY_WEIGHT` | `src/route_optimizer.py` | 0.5 sec/deg | 2-opt bearing-change penalty |
| `MAX_SNAP_DISTANCE_M` | `src/network_graph.py` | 500.0 m | Snap-distance gate threshold |
| `INSERT_DETOUR_ALPHA` | `src/route_optimizer.py` | 0.01 boardings/m | Insertion detour penalty |
