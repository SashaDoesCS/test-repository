# Los Gatos Bus Transit — Cost-Benefit Analysis

District-level CBA of the VTA bus transit system serving Los Gatos, CA and the Union School District attendance area, plus a Phase B route-optimization layer.

## Quick Start

```bash
cd los_gatos_transit_cba

# CBA only (Phase A1–A4 + scenarios + dashboard) — recommended for cost-benefit work
python run_cba.py

# Full pipeline (CBA + Phase B route optimization, scheduling, GTFS, Route 27)
python run_analysis.py

# Same as run_cba.py via flag, if you prefer one entry point
python run_analysis.py --cba-only

# Tests
python -m unittest discover tests -v
```

The CBA-only run skips Phase B but still produces the dashboard at `outputs/cba_dashboard.html`. Phase B / Route 27 panels render with an empty-state message rather than data.

## Two Entry Points

| Entry point | Phases run | What you get |
|---|---|---|
| `run_cba.py` | A1 → A4 + scenarios | District profiles, costs, benefits, NPV/BCR, equity, dashboard |
| `run_analysis.py` | A1 → A4 + scenarios + Phase B (B1–B6) | Everything above plus route optimization, schedules, GTFS feed, Route 27 stop suggestions, rider placards |

Both produce `outputs/cba_dashboard.html`. The dashboard auto-detects which phases ran and shows empty-state messages for missing panels.

## Project Structure

```
config.yaml                 ← Parameters, district definitions, valuations, scenarios
run_analysis.py             ← Master pipeline (CBA + Phase B). Accepts --cba-only.
run_cba.py                  ← CBA-only entry point (calls run_analysis.main(cba_only=True))

src/                        (see docs/cba_module_reference.md for full descriptions)
  __init__.py
  districts.py              ← District boundaries, point-in-polygon, GeoJSON export
  data_ingestion.py         ← Census ACS, GTFS, SWITRS, traffic, survey loaders
  fetch_real_data.py        ← Optional: fetch live ACS + GTFS before pipeline run
  cost_model.py             ← Operating, capital, NPV; district allocation; peer benchmarks
  benefit_model.py          ← 8 benefit categories; allocation; FTA Cost Effectiveness Index
  scenarios.py              ← Conservative / Moderate / Optimistic scenario parameter sets
  demand_model.py           ← TDI, SLI, unmet need, coverage gaps, equity flag
  demand_matrix.py          ← Phase B: O-D gravity model, school dismissal windows
  network_graph.py          ← Phase B: OSM road graph + travel-time matrix
  route_optimizer.py        ← Phase B: Clarke-Wright + headway + stop selection
  route27_corridor.py       ← Phase B: Route 27 corridor geometry from OSM
  route27_walkshed.py       ← Phase B: Walk-shed population for Route 27 candidates
  route27_optimizer.py      ← Phase B: Linear stop selection + new-stop BCR
  candidate_generator.py    ← Phase B: Synthetic stop candidates for underserved districts
  schedule_generator.py     ← Phase B: Timetable with school dismissal constraints
  gtfs_exporter.py          ← Phase B: GTFS Static feed export
  placard_renderer.py       ← Phase B: Rider-facing per-stop HTML placards
  generate_dashboard.py     ← Self-contained HTML dashboard with glossary tooltips

tests/
  test_districts.py
  test_cost_model.py
  test_benefit_model.py
  test_demand_model.py

data/
  geospatial/districts/     ← GeoJSON boundary files (auto-generated)
  geospatial/gtfs/          ← VTA GTFS feed (real if fetched, synthetic otherwise)
  processed/                ← Cleaned analysis-ready datasets (e.g. census BG with district)

outputs/
  tables/                   ← All CSV pipeline outputs (cost, benefit, NPV, equity, scenarios)
  cba_dashboard.html        ← Interactive dashboard (open directly in browser)
  gtfs_optimised/           ← (Phase B) Optimized GTFS feed
  placards/                 ← (Phase B) Per-stop rider placards

docs/
  district_methodology.md
  assumptions_register.md
  cba_module_reference.md   ← Per-file purpose, key functions, dependencies
```

## CBA Methodology (high level)

**Phase A1 — Foundations.** Districts loaded from `config.yaml`; census, [GTFS](outputs/cba_dashboard.html#gl-gtfs), crash, traffic, survey data ingested; stops and block groups assigned to districts; population reconciled against known totals (Los Gatos CDP ~33K, Union SD area ~35K).

**Phase A2 — Costs.** Per-route operating costs from [NTD](outputs/cba_dashboard.html#gl-ntd) FY2023 unit rates × estimated revenue-hours/miles. Capital from per-stop infrastructure × stop count. District allocation by stop share. [NPV](outputs/cba_dashboard.html#gl-npv) at [OMB Circular A-94](https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf) rates (2%, 3.5%, 7%) over a 20-year horizon.

**Phase A3 — Benefits.** Eight categories per [FTA](outputs/cba_dashboard.html#gl-fta) + [USDOT BCA Guidance 2024](https://www.transportation.gov/sites/dot.gov/files/2024-11/Benefit%20Cost%20Analysis%20Guidance%202025%20Update%20(Final).pdf):
1. Travel time savings (purpose-weighted [VOT](outputs/cba_dashboard.html#gl-vot), Table 4)
2. Vehicle operating cost savings ([AAA "Your Driving Costs" 2024](https://newsroom.aaa.com/wp-content/uploads/2024/08/YDC-Brochure-FINAL-9.2024.pdf) $0.68/mi)
3. Crash reduction ([FHWA KABCO](outputs/cba_dashboard.html#gl-kabco) costs × Santa Clara crash rate)
4. Emission reduction (EPA [SC-CO₂](outputs/cba_dashboard.html#gl-scc) $120/tCO₂, [BenMAP-CE](outputs/cba_dashboard.html#gl-benmap-ce) for criteria pollutants)
5. Health benefits from active transport ([WHO HEAT](outputs/cba_dashboard.html#gl-who-heat))
6. Reliability benefits (USDOT 80% of [VOT](outputs/cba_dashboard.html#gl-vot) × variability)
7. Option value ([TCRP](outputs/cba_dashboard.html#gl-tcrp) 78, $20–$40/cap/yr)
8. [Induced demand](outputs/cba_dashboard.html#gl-induced-demand) ([TCRP](outputs/cba_dashboard.html#gl-tcrp) 95, [consumer-surplus](outputs/cba_dashboard.html#gl-consumer-surplus) triangle)

All categories share a single set of behavioral parameters (`pct_diverted_from_auto`, `pct_business_trips`, `induced_share`) so changes propagate consistently. The model logs a warning if `induced_share + pct_diverted > 1.0` (potential double-counting).

**Phase A4 — Demand & equity.** TDI (need), SLI (current service), unmet-need ranking, coverage-gap analysis, equity flagging per [FTA Title VI (Circular 4702.1B)](https://www.transit.dot.gov/sites/fta.dot.gov/files/docs/FTA_Title_VI_FINAL.pdf).

**Scenarios.** `scenarios.py` defines Conservative, Moderate, Optimistic parameter sets. Each adjusts diversion rate, growth rates, option value, school access, and (Optimistic only) property-value uplift. The dashboard's headline cards are the conservative scenario; scenario comparison appears in the scenario panel.

**Phase B (optional).** Route optimization: O-D gravity model, OSM road graph, Clarke-Wright + headway optimization, schedule generation with school dismissal constraints, [GTFS](outputs/cba_dashboard.html#gl-gtfs) export. Route 27 gets its own linear corridor analysis with per-stop [BCR](outputs/cba_dashboard.html#gl-bcr). All Phase B work is gated behind `cba_only=False` and skipped by `run_cba.py`.

## Dashboard

`outputs/cba_dashboard.html` is a self-contained file (no server required). It loads Leaflet and Chart.js from CDNs; all data is embedded inline.

Acronyms and key concepts (BCR, NPV, VOT, VSL, SCC, TCRP, TSUB, KABCO, etc.) are auto-wrapped on page load. Hover any underlined term for a popup definition; click to jump to the full Glossary section at the bottom of the page (the entry flashes briefly so you can spot it). Dynamically-injected content — NPV breakdown table, schedule panel, school demand table — is wrapped via a MutationObserver so tooltips work everywhere, not just the initial page state.

## Audit Trail (CBA model)

The benefit model has been audited for double-counting and parameter consistency. See `docs/cba_module_reference.md` for the full audit notes. Resolved fixes:

- `pct_diverted_from_auto`, `pct_business_trips`, and `induced_share` now thread through `compute_all_benefits` instead of being hard-coded per sub-call.
- Reliability benefits use the same purpose-weighted VOT as travel time (USDOT 2024 §5.3).
- Dashboard year-stream display growth rates (`costGrowth=0.025`, `benGrowth=0.010`) match the Python NPV defaults.
- A runtime warning fires if `induced_share + pct_diverted > 1.0`.

## Districts

**LGHS Zone (D1–D10):** 10 road-bounded districts covering ZIP 95030/95032/95033.
**Union SD Zone (U1–U6):** 6 road-bounded districts covering the Union School District service area.

District boundaries use major roads (SR-17, SR-85, LG Blvd, Camden Ave, etc.) as barriers — no freeway or major arterial cuts through any district. See `docs/district_methodology.md`.

## Standards & References

- [FTA CIG Policy Guidance](https://www.transit.dot.gov/CIG); [FTA Circular 9040.1H](https://www.transit.dot.gov/sites/fta.dot.gov/files/2024-09/C9040.1H-Circular-11-01-2024.pdf) (route/stop standards); [FTA Circular 4702.1B](https://www.transit.dot.gov/sites/fta.dot.gov/files/docs/FTA_Title_VI_FINAL.pdf) (Title VI)
- [USDOT BCA Guidance 2024](https://www.transportation.gov/sites/dot.gov/files/2024-11/Benefit%20Cost%20Analysis%20Guidance%202025%20Update%20(Final).pdf) ([VOT](outputs/cba_dashboard.html#gl-vot), [VSL](outputs/cba_dashboard.html#gl-vsl), reliability)
- [EPA SC-GHG Report 2023](https://www.epa.gov/system/files/documents/2023-12/epa_scghg_2023_report_final.pdf); [EPA MOVES3.1](https://www.epa.gov/moves/latest-version-motor-vehicle-emission-simulator-moves); [EPA BenMAP-CE](https://www.epa.gov/benmap)
- [WHO HEAT](https://www.who.int/tools/heat-for-walking-and-cycling) (active transport health)
- [OMB Circular A-94](https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf) ([discount rates](outputs/cba_dashboard.html#gl-discount-rate))
- [TCRP Report 78](https://www.trb.org/publications/tcrp/tcrp78/guidebook/tcrp78.pdf); [TCRP Report 95](https://www.trb.org/publications/tcrp/tcrp_rpt_95c9.pdf); [TCRP](outputs/cba_dashboard.html#gl-tcrp) [Report 167](https://nap.nationalacademies.org/catalog/22355/making-effective-fixed-guideway-transit-investments-indicators-of-success); [FHWA KABCO](https://safety.fhwa.dot.gov/hsip/docs/fhwasa17071.pdf) crash costs; [SWITRS](https://tims.berkeley.edu/help/SWITRS.php)
- Boardman et al., [*Cost-Benefit Analysis: Concepts and Practice*, 5th ed.](https://www.cambridge.org/us/universitypress/subjects/economics/public-economics-and-public-policy/cost-benefit-analysis-concepts-and-practice-5th-edition) (Cambridge University Press, 2018)
