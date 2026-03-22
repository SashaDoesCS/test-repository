# Los Gatos Bus Transit — Cost-Benefit Analysis

District-level CBA of the VTA bus transit system serving Los Gatos, CA and the Union School District area.

## Quick Start

```bash
cd los_gatos_transit_cba
python3 run_analysis.py          # Run full Phase A1 pipeline
python3 -m unittest discover tests -v  # Run test suite
```

## Project Structure

```
config.yaml              ← All parameters, district definitions, scenarios
run_analysis.py          ← Master pipeline script
src/
  districts.py           ← District boundaries, spatial queries, aggregation
  data_ingestion.py      ← Census, GTFS, crash, traffic data loading
tests/
  test_districts.py      ← 33 unit tests for district module
data/
  geospatial/districts/  ← GeoJSON boundary files (auto-generated)
  processed/             ← Cleaned analysis-ready datasets
outputs/
  tables/                ← CSV results and reports
docs/
  district_methodology.md
  assumptions_register.md
```

## Districts

**LGHS Zone (D1–D10):** 10 road-bounded districts covering ZIP 95030/95032/95033.
**Union SD Zone (U1–U6):** 6 road-bounded districts covering the Union School District service area.

All district boundaries use major roads (SR-17, SR-85, LG Blvd, Camden Ave, etc.) as barriers — no freeway or major arterial cuts through any district.

## Current Status

- **Phase A1:** ✅ Complete — districts, data ingestion, stop mapping, quality report
- **Phase A2–A9:** Pending
