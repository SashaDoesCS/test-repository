# CBA Module Reference

This document describes every file in `src/` and at the project root that participates in the cost-benefit analysis pipeline. Files are grouped by phase. For each module: purpose, key public functions, the inputs it reads, the outputs it produces, and which other modules depend on it.

Key acronyms: [GTFS](../outputs/cba_dashboard.html#gl-gtfs) (General Transit Feed Specification), [NTD](../outputs/cba_dashboard.html#gl-ntd) (National Transit Database), [NPV](../outputs/cba_dashboard.html#gl-npv) (Net Present Value), [BCR](../outputs/cba_dashboard.html#gl-bcr) (Benefit-Cost Ratio), [VOT](../outputs/cba_dashboard.html#gl-vot) (Value of Time), [VMT](../outputs/cba_dashboard.html#gl-vmt) (Vehicle Miles Traveled), [SCC](../outputs/cba_dashboard.html#gl-scc) (Social Cost of Carbon), [TSUB](../outputs/cba_dashboard.html#gl-tsub) (Transportation System User Benefit), [TCRP](../outputs/cba_dashboard.html#gl-tcrp) (Transit Cooperative Research Program).

The CBA-only run (`run_cba.py`) exercises only the modules in **Phase A** + **Scenarios** + **Dashboard**. The full run (`run_analysis.py`) additionally exercises **Phase B**.

---

## Entry Points

### `run_analysis.py`
**Purpose.** Master pipeline. Runs Phase A1 (foundations) → A2 (costs) → A3 (benefits) → A4 (demand & equity) → scenario comparison, then optionally Phase B (route optimization, scheduling, GTFS, Route 27, dashboard).

**Signature.** `main(cba_only: bool = False) -> int`. With `cba_only=True`, the function exits at the Phase B boundary (just after the scenario comparison block) after generating the dashboard. With `cba_only=False`, it continues through Phase B.

**CLI.** `python run_analysis.py` (full) or `python run_analysis.py --cba-only` (CBA only).

### `run_cba.py`
**Purpose.** Thin CBA-only entry point. Calls `run_analysis.main(cba_only=True)`. Use this when iterating on the cost-benefit work — it skips the slow Phase B steps (OSMnx graph build, route optimization, schedule generation) and produces the dashboard from the A1–A4 + scenario outputs.

---

## Configuration

### `config.yaml`
Parameter source of truth. Defines:
- District boundary polygons (LGHS D1–D10, Union U1–U6)
- Valuation parameters: VOT, VSL, SCC, crash costs, auto $/mi, walk-min/trip
- Emission factors (CO₂, NOx, PM2.5 per VMT)
- Cost benchmarks (NTD FY2023 unit rates)
- Discount rates and time horizon
- Benefit-allocation method (default: 50/50 origin/destination split for cross-district trips)

Loaded via `src.districts.load_config()` and re-loaded inside `benefit_model.get_benefit_params()` and `cost_model.get_cost_params()`.

---

## Phase A — CBA Core

### `src/districts.py`
**Purpose.** District boundary management and spatial operations. Loads the LGHS and Union district polygons from `config.yaml`, performs point-in-polygon assignment for stops/crashes/census block groups, computes district areas via spherical-excess, and exports GeoJSON.

**Key API.**
- `load_config(path) -> dict` — loads `config.yaml`.
- `DistrictManager(config)` — main class. `.assign_points(df, zone=None)` adds a `district_id` column. `.summary_table()` returns the initial district profile. `.export_geojson(path, zone=None)` writes RFC 7946 GeoJSON.

**Dependencies.** Used by every other Phase A module that needs district context (data ingestion, cost allocation, benefit allocation, demand model). No spatial dependencies — uses `matplotlib.path.Path` so `geopandas`/`shapely` aren't required.

### `src/data_ingestion.py`
**Purpose.** All raw-data loaders. Each loader returns a clean DataFrame with consistent column names; if real data is unavailable it generates synthetic data from published aggregates (always flagged with `is_synthetic=True`).

**Loaders.**
- `load_census_block_groups(config)` — ACS 5-year (population, households, vehicles, income, transit commute). With ACS-suppression detection.
- `load_gtfs_stops(config)`, `load_gtfs_routes(config)` — VTA GTFS feed.
- `load_ridership_data(config)` — Route-level annual boardings.
- `load_rbs_stop_detail()` — Real Boarding Summary stop-level boardings (when available).
- `load_bus_schedule_observations()` — Observed schedule-deviation data for reliability calibration.
- `load_student_survey()` — Union SD student survey (drives Moderate/Optimistic scenarios).
- `load_crash_data(config)` — SWITRS crash records.
- `load_traffic_volumes(config)` — Caltrans PeMS hourly profiles.
- `load_road_closures(config)`.
- `count_system_stops_per_route()` — Per-route system-wide stop counts from real GTFS.
- `run_data_quality_report(...)` — Markdown report summarising data freshness and synthetic flags.

### `src/fetch_real_data.py`
**Purpose.** Optional pre-pipeline script that hits the U.S. Census ACS 5-Year API and downloads the VTA GTFS feed, populating `data/processed/` and `data/geospatial/gtfs/` with real data. Run **before** `run_cba.py` / `run_analysis.py` for a real-data run; otherwise the loaders fall back to synthetic data.

**CLI.** `python src/fetch_real_data.py`. Requires `requests`.

### `src/cost_model.py`
**Purpose.** All cost computations. Operating costs from [NTD](../outputs/cba_dashboard.html#gl-ntd) FY2023 unit rates (revenue-hour and revenue-mile), capital from per-stop infrastructure × stop counts. Allocates costs to districts proportionally by stop share. Computes [NPV](../outputs/cba_dashboard.html#gl-npv) at [OMB Circular A-94](https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf)-prescribed [discount rates](../outputs/cba_dashboard.html#gl-discount-rate) and a peer-benchmark comparison vs. CA agencies.

**Key API.**
- `get_cost_params(config) -> dict`
- `generate_route_service_estimates(config) -> DataFrame` — Route-level service quantities derived from NTD.
- `compute_annual_operating_costs(route_service, params) -> DataFrame` — Operating cost + fare revenue + net cost per route.
- `compute_capital_costs(stop_district_df, config) -> DataFrame` — Per-district capital from stop counts.
- `allocate_operating_costs_to_districts(route_costs, route_district_matrix) -> DataFrame`
- `build_district_cost_summary(op, cap) -> DataFrame` — Combined annual + amortized capital per district.
- `compute_peer_benchmarks() -> DataFrame` — VTA vs. CA peer agencies on $/rev-hr.
- `estimate_route76_restoration_costs(params) -> dict` — Discontinued Route 76 scenario.
- `compute_cost_npv(annual, capital, rates, horizon, growth=0.025) -> DataFrame`

**Outputs.** `outputs/tables/route_operating_costs.csv`, `district_capital_costs.csv`, `district_cost_summary.csv`, `peer_benchmarks.csv`, `npv_costs.csv`, `zone_costs.csv`.

### `src/benefit_model.py`
**Purpose.** All benefit computations. Eight [FTA](../outputs/cba_dashboard.html#gl-fta)-aligned categories computed town-wide and allocated to districts. Plus the FTA Cost Effectiveness Index for federal-funding contexts.

**Key API.**
- `get_benefit_params(config) -> dict`
- `compute_travel_time_savings(...)` — Purpose-weighted [VOT](../outputs/cba_dashboard.html#gl-vot) ([USDOT BCA Guidance 2024](https://www.transportation.gov/sites/dot.gov/files/2024-11/Benefit%20Cost%20Analysis%20Guidance%202025%20Update%20(Final).pdf), Table 4).
- `compute_voc_savings(...)` — Avoided auto [VMT](../outputs/cba_dashboard.html#gl-vmt) × $0.68/mi ([AAA "Your Driving Costs" 2024](https://newsroom.aaa.com/wp-content/uploads/2024/08/YDC-Brochure-FINAL-9.2024.pdf)).
- `compute_crash_reduction_benefits(avoided_vmt, ...)` — [KABCO](../outputs/cba_dashboard.html#gl-kabco)-weighted [FHWA crash costs](https://safety.fhwa.dot.gov/hsip/docs/fhwasa17071.pdf).
- `compute_emission_benefits(avoided_vmt, bus_revenue_miles, ...)` — [SCC](../outputs/cba_dashboard.html#gl-scc) × CO₂, [BenMAP-CE](../outputs/cba_dashboard.html#gl-benmap-ce) × NOx/PM2.5.
- `compute_health_benefits(boardings, ...)` — [WHO HEAT](../outputs/cba_dashboard.html#gl-who-heat) walk-time × $0.16/min.
- `compute_reliability_benefits(...)` — Purpose-weighted [VOT](../outputs/cba_dashboard.html#gl-vot) × 80% × variability (audit fix #3).
- `compute_option_value(pop, ...)` — [TCRP Report 78](https://www.trb.org/publications/tcrp/tcrp78/guidebook/tcrp78.pdf) per-capita.
- `compute_induced_demand_benefits(...)` — [TCRP Report 95](https://www.trb.org/publications/tcrp/tcrp_rpt_95c9.pdf) [consumer-surplus](../outputs/cba_dashboard.html#gl-consumer-surplus) triangle.
- `compute_all_benefits(config, ridership, bus_rev_mi, pop, ..., pct_diverted_from_auto, pct_business_trips, induced_share) -> list[dict]` — Wrapper that threads behavioral parameters into all sub-calls (audit fix #1) and warns if `induced + diverted > 1.0`.
- `compute_fta_cost_effectiveness(annualized_cost, boardings, ...) -> dict` — [FTA CIG](../outputs/cba_dashboard.html#gl-cig) [CEI](../outputs/cba_dashboard.html#gl-cei) index + rating.
- `compute_benefit_npv(annual, rates, horizon, growth=0.01)`
- `allocate_benefits_to_districts(total_benefits, stop_district, demographics, params) -> DataFrame` — Stop-share allocation for ridership-driven categories; population-share for option value.

**Outputs.** `outputs/tables/annual_benefits_by_category.csv`, `district_benefits.csv`, `npv_benefits.csv`, `zone_benefits.csv`, `zone_npv.csv`.

**Audit notes (resolved).** See `README.md` "Audit Trail" section. Outstanding documentation-only items: (a) reliability and travel time both monetize trip time but for different externalities (mean vs variance), so they are not double-counted; (b) option value applies to all population including riders by TCRP 78 convention; (c) emission benefits zero out bus-side emissions for existing service — this is correct for base CBA but new-service scenarios (Route 76) should add bus emissions back.

### `src/scenarios.py`
**Purpose.** Three named scenarios — Conservative (base case), Moderate (adds survey latent demand + school access), Optimistic (adds property uplift). Each is a `@dataclass Scenario` with parameter overrides for diversion rate, VOT business share, growth rates, option value, capital per stop, etc.

**Key API.**
- `get_conservative_scenario()`, `get_moderate_scenario()`, `get_optimistic_scenario()`
- `compute_scenario_benefits(scenario, ...) -> dict`
- `compute_scenario_costs(scenario, ...) -> dict`
- `run_scenario_comparison(...) -> list[dict]` — Runs all three, computes BCR/NPV per scenario, applies survey-based additional boardings to Moderate and Optimistic.

**Outputs.** `outputs/tables/scenario_comparison.csv`, `scenario_detail.json`.

### `src/demand_model.py`
**Purpose.** Per-district Transit Demand Index, Service Level Index, unmet-need score, equity flagging, and coverage-gap (% of district pop beyond 0.5-mi walk of any stop).

**Key API.** `run_demand_analysis(demographics, costs, stops) -> dict[str, DataFrame]` returning `tdi`, `sli`, `unmet_need`, `coverage`.

**Outputs.** `outputs/tables/transit_demand_index.csv`, `service_level_index.csv`, `unmet_need.csv`, `coverage_gaps.csv`.

---

## Phase B — Route Optimization (skipped by `run_cba.py`)

### `src/demand_matrix.py`
**Purpose.** Origin-Destination demand matrix using a gravity model with negative-exponential distance decay (~5 km half-life). Combines TDI, demographics, and survey diversion potential. Adds school-dismissal time-of-day windows.

**Key API.** `run_demand_matrix(tdi_df, demographics_df, district_manager, config, survey_df=None) -> dict` returning `od_matrix`, `school_demand`, `district_totals`, `tod_profiles`.

### `src/network_graph.py`
**Purpose.** Builds an OSMnx-derived directed graph of the study-area road network, snaps GTFS stops to nearest OSM nodes, computes a stop-to-stop travel-time matrix.

**Key API.** `run_network_graph(stops, config) -> dict` with `stops_snapped`, `travel_time_matrix`, `graph`.

**Cache.** Pickled to `data/geospatial/network_graph.pkl` to avoid re-downloading OSM on every run.

### `src/route_optimizer.py`
**Purpose.** Multi-stage hub-and-spoke route + stop optimization for the broader study-area routes (NOT Route 27, which has its own linear optimizer).

Stages: greedy max-coverage stop selection → Clarke-Wright savings algorithm for route formation → Mohring square-root headway optimization. Enforces walk-buffer, route-length, school-mandatory, equity, and ADA constraints.

**Key API.** `run_route_optimisation(candidate_stops, coverage_gaps, tdi_df, unmet_need_df, travel_time_matrix, od_profiles, config, route_costs_df, scenario_results) -> dict` with `routes`, `routes_df`, `selected_stops`.

### `src/route27_corridor.py`
**Purpose.** Builds the road-network geometry of VTA Route 27 (Winchester TC → Blossom Hill area) and extracts every legal intersection along the corridor as a candidate stop. Adds forced-candidate locations for activity generators (schools, LRT). Computes the arc-length s-coordinate for each candidate.

**Key API.** `build_route27_corridor(config) -> dict` with `path_coords`, `candidates_df`, `path_geojson`.

**Cache.** `data/geospatial/route27_network.pkl`.

### `src/route27_walkshed.py`
**Purpose.** [Walk-shed](../outputs/cba_dashboard.html#gl-walk-shed) population per Route 27 candidate stop: raw, marginal (incremental over adjacent stops), and equity-weighted (by TDI per [FTA Title VI](https://www.transit.dot.gov/sites/fta.dot.gov/files/docs/FTA_Title_VI_FINAL.pdf)). Buffer distances follow [FTA Circular 9040.1H](https://www.transit.dot.gov/sites/fta.dot.gov/files/2024-09/C9040.1H-Circular-11-01-2024.pdf) §4.2.1 (¼ mi urban, ½ mi suburban).

**Key API.** `run_walkshed_analysis(candidates_df, census_df, tdi_df, unmet_need_df) -> DataFrame`.

### `src/route27_optimizer.py`
**Purpose.** Linear stop selection + new-stop suggestion for Route 27. Replaces Clarke-Wright (inappropriate for a linear corridor). Three stages: linear spacing filter, coverage-gap detection, marginal [walk-shed](../outputs/cba_dashboard.html#gl-walk-shed) update. Computes per-suggestion [BCR](../outputs/cba_dashboard.html#gl-bcr) ([USDOT BCA Guidance 2024](https://www.transportation.gov/sites/dot.gov/files/2024-11/Benefit%20Cost%20Analysis%20Guidance%202025%20Update%20(Final).pdf) / [OMB Circular A-94](https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf) / [NTD](../outputs/cba_dashboard.html#gl-ntd) FY2023) and [FTA](../outputs/cba_dashboard.html#gl-fta) [CE index](../outputs/cba_dashboard.html#gl-cei).

**Key API.** `run_route27_optimization(corridor_result, walkshed_df, existing_stops_df, tdi_df, unmet_need_df, config) -> dict` with `suggestions`, `gaps`, `n_new_suggested`, `n_high_priority`.

### `src/candidate_generator.py`
**Purpose.** Generates synthetic bus-stop candidates along drivable road edges at [FTA](../outputs/cba_dashboard.html#gl-fta)-recommended ¼-mi spacing for the most underserved districts. Reuses the cached Route 27 OSM network. Falls back to a uniform grid when the network is unavailable.

### `src/schedule_generator.py`
**Purpose.** Timetable generation with school dismissal constraints. Backward-schedules school trips from each dismissal time (2:25 PM, 3:55 PM) so a bus arrives within 10 minutes; forward-schedules regular trips from 6:00 AM at the optimized headway; merges and deduplicates.

**Key API.** `generate_schedule(routes, travel_time_matrix, config) -> dict` with `school_coverage`, `all_trips`.

### `src/gtfs_exporter.py`
**Purpose.** Exports the optimized routes + schedule as a complete, valid [GTFS](../outputs/cba_dashboard.html#gl-gtfs) Static feed. Produces `agency.txt`, `routes.txt`, `stops.txt` (with ADA `wheelchair_boarding=1`), `trips.txt`, `stop_times.txt`, `calendar.txt`, `shapes.txt`, `feed_info.txt`. Attempts `gtfs-kit` validation if installed.

**Output.** `outputs/gtfs_optimised/`.

### `src/placard_renderer.py`
**Purpose.** Generates rider-facing per-stop HTML placards: route-colored header, Mermaid strip diagram (±2 stops), Leaflet mini-map, weekday/weekend timetable, QR code footer. One file per stop plus a filterable index.

**Output.** `outputs/placards/<stop_id>.html`, `outputs/placards/index.html`.

---

## Dashboard

### `src/generate_dashboard.py`
**Purpose.** Builds a single self-contained HTML file from all `outputs/tables/` CSVs. CDN-hosted Leaflet + Chart.js; all data embedded inline as JSON; opens directly in a browser without a server.

**Sections produced.** District map, district cost / population / equity / peer charts, [NPV](../outputs/cba_dashboard.html#gl-npv) cards (click for full breakdown), Route 76 restoration scenario, benefit-category reference (8 cards), Phase B route map + schedule + ridership + demand, Route 27 stop suggestion map + table, full district table, Glossary.

**Glossary tooltip system.** `GLOSSARY` dict (29 terms) is embedded as a JS const. On page load, a `TreeWalker` wraps every occurrence of each term in `<span class="jargon">`. A `MutationObserver` re-wraps content injected by click handlers (NPV breakdown, schedule, school demand, etc.). Hover shows a popover via delegated `mouseover`/`mouseout` with a 180 ms settle delay so the user can move the cursor to the popover. Clicking a term smooth-scrolls to the matching `<dt id="gl-...">` in the Glossary section, which flashes via a `gloss-flash` CSS animation.

**Key API.**
- `load_pipeline_data() -> dict` — Reads all expected CSVs and returns a single dict.
- `merge_district_data(data) -> list` — Joins district profile + demographics + costs + crashes.
- `get_district_polygons() -> dict[str, list[(lat, lon)]]` — Loaded via `DistrictManager`.
- `generate_dashboard_html(data, merged, polygons) -> str` — Builds the full HTML.
- `generate_dashboard(output_path) -> str` — Top-level: load, merge, render, write.

---

## Tests

### `tests/test_districts.py`
33 unit tests for `districts.py`: polygon containment, area calculation, GeoJSON round-trip, `assign_points` correctness across edge cases.

### `tests/test_cost_model.py`
Cost calculation correctness: operating-cost computation against NTD unit rates, capital allocation, NPV math, peer-benchmark structure.

### `tests/test_benefit_model.py`
Benefit calculation correctness: per-category math, parameter threading via `compute_all_benefits` (audit fix #1 covered), allocation sums equal town-wide totals.

### `tests/test_demand_model.py`
TDI / SLI / unmet-need monotonicity properties and equity-flag thresholding.

---

## Data Flow at a Glance

```
config.yaml
    │
    ▼
districts.py ─────────► assigns geometry to everything
data_ingestion.py ────► raw census / GTFS / crash / survey data
    │
    ├─► cost_model.py ──┐
    ├─► benefit_model.py┤
    ├─► scenarios.py ───┼──► outputs/tables/*.csv
    └─► demand_model.py ┘
                         │
                         ▼
                generate_dashboard.py ──► outputs/cba_dashboard.html
                         ▲
                         │
              (Phase B feeds in here when run_analysis.py is used)
              demand_matrix → network_graph → route_optimizer
                                              → schedule_generator
                                              → gtfs_exporter
              route27_corridor → route27_walkshed → route27_optimizer
                                                  → placard_renderer
```

The dashboard auto-detects which CSVs are present and renders empty-state messages for any panel whose source data is missing — this is what makes `run_cba.py` produce a complete, valid page even without Phase B output.
