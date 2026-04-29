# Data Dictionary

Key acronyms: [ACS](../outputs/cba_dashboard.html#gl-acs) (American Community Survey — U.S. Census Bureau 5-year estimates), [GTFS](../outputs/cba_dashboard.html#gl-gtfs) (General Transit Feed Specification).

## Census Block Groups (`census_block_groups.csv`)

Source: [U.S. Census Bureau ACS 5-Year Estimates](https://www.census.gov/data/developers/data-sets/acs-5year.html) via Census API.

| Variable | Type | Unit | Source | Description |
|----------|------|------|--------|-------------|
| geoid | str | — | Census | Full GEOID (state+county+tract+BG) |
| tract | str | — | Census | Census tract number |
| block_group | str | — | Census | Block group number within tract |
| county_fips | str | — | Census | County FIPS code (085 = Santa Clara) |
| total_pop | int | persons | [ACS B01001_001E](https://api.census.gov/data/2023/acs/acs5/variables/B01001_001E.json) | Total population |
| median_income | int | USD | [ACS B19013_001E](https://api.census.gov/data/2023/acs/acs5/variables/B19013_001E.json) | Median household income |
| zero_veh_hh | int | households | [ACS B08201_002E](https://api.census.gov/data/2023/acs/acs5/variables/B08201_002E.json) | Households with zero vehicles |
| total_hh | int | households | [ACS B08201_001E](https://api.census.gov/data/2023/acs/acs5/variables/B08201_001E.json) | Total households |
| transit_commuters | int | workers | [ACS B08301_010E](https://api.census.gov/data/2023/acs/acs5/variables/B08301_010E.json) | Workers commuting by public transit |
| total_workers | int | workers | [ACS B08301_001E](https://api.census.gov/data/2023/acs/acs5/variables/B08301_001E.json) | Total workers 16+ |
| pop_under_18 | int | persons | [ACS B09001_001E](https://api.census.gov/data/2023/acs/acs5/variables/B09001_001E.json) | Population under 18 |
| pop_65_plus | int | persons | [ACS B01001](https://api.census.gov/data/2023/acs/acs5/variables.html) | Population 65 and older |
| lat | float | degrees | Computed | Block group centroid latitude |
| lon | float | degrees | Computed | Block group centroid longitude |
| is_synthetic | bool | — | System | True if data was generated synthetically |

## Transit Stops (`stops_synthetic.csv`)

Source: [VTA GTFS feed](https://www.vta.org/go/developers). When real data is unavailable the loaders fall back to synthetic data (`is_synthetic=True`).

| Variable | Type | Unit | Source | Description |
|----------|------|------|--------|-------------|
| stop_id | str | — | [GTFS](../outputs/cba_dashboard.html#gl-gtfs) | Unique stop identifier |
| stop_name | str | — | GTFS | Human-readable stop name |
| stop_lat | float | degrees | GTFS | Stop latitude (WGS84) |
| stop_lon | float | degrees | GTFS | Stop longitude (WGS84) |
| route_ids | str | — | GTFS | Comma-separated route IDs serving this stop |
| is_synthetic | bool | — | System | True if synthetically generated |

## District Profile (`district_profile_initial.csv`)

| Variable | Type | Unit | Source | Description |
|----------|------|------|--------|-------------|
| id | str | — | Config | District identifier (D1–D10, U1–U6) |
| name | str | — | Config | District name |
| zone | str | — | Config | "LGHS" or "UNION" |
| zip_primary | str | — | Config | Primary ZIP code |
| road_boundaries | str | — | Config | Road boundary description |
| area_sq_miles | float | sq mi | Computed | Polygon area (spherical) |
| centroid_lat | float | degrees | Computed | Centroid latitude |
| centroid_lon | float | degrees | Computed | Centroid longitude |
| n_vertices | int | — | Computed | Number of polygon boundary vertices |
