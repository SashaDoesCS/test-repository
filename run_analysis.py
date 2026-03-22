"""
run_analysis.py -- Master pipeline for Los Gatos Transit CBA.

Phase A1: Project setup, district loading, data ingestion, stop-to-district
mapping, data quality report, and initial district profile table.

Usage:
    python run_analysis.py

Outputs:
    outputs/tables/district_profile_initial.csv
    outputs/tables/stop_district_matrix.csv
    outputs/tables/route_district_matrix.csv
    outputs/tables/data_quality_report.md
    data/geospatial/districts/*.geojson
"""

import logging
import sys
from pathlib import Path

import pandas as pd

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.districts import DistrictManager, load_config
from src.data_ingestion import (
    load_census_block_groups,
    load_gtfs_stops,
    load_gtfs_routes,
    load_ridership_data,
    load_rbs_stop_detail,
    load_bus_schedule_observations,
    load_student_survey,
    load_crash_data,
    load_traffic_volumes,
    load_road_closures,
    run_data_quality_report,
    count_system_stops_per_route,
)
from src.cost_model import (
    get_cost_params,
    compute_annual_operating_costs,
    generate_route_service_estimates,
    compute_capital_costs,
    allocate_operating_costs_to_districts,
    compute_peer_benchmarks,
    estimate_route76_restoration_costs,
    compute_cost_npv,
    build_district_cost_summary,
)
from src.benefit_model import (
    get_benefit_params,
    compute_all_benefits,
    compute_benefit_npv,
    allocate_benefits_to_districts,
)
from src.demand_model import run_demand_analysis
from src.scenarios import run_scenario_comparison

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_analysis")


def main():
    """Execute the full Phase A1 pipeline."""
    logger.info("=" * 70)
    logger.info("LOS GATOS TRANSIT CBA -- Phase A1 Pipeline")
    logger.info("=" * 70)

    # Ensure output directories exist
    for d in ["outputs/tables", "outputs/figures", "outputs/maps",
              "data/geospatial/districts", "data/geospatial/gtfs",
              "data/processed", "data/traffic/incidents",
              "data/traffic/volume", "data/traffic/closures"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    # -- Step 1: Load Config & Districts ----------------------
    logger.info("Step 1: Loading configuration and districts...")
    config = load_config(str(PROJECT_ROOT / "config.yaml"))
    dm = DistrictManager(config)

    # Export GeoJSON
    dm.export_geojson("data/geospatial/districts/lghs_districts.geojson", zone="LGHS")
    dm.export_geojson("data/geospatial/districts/union_districts.geojson", zone="UNION")
    dm.export_geojson("data/geospatial/districts/all_districts.geojson")

    # District summary
    summary = dm.summary_table()
    summary.to_csv("outputs/tables/district_profile_initial.csv", index=False)
    logger.info("District profile table saved (%d districts)", len(summary))

    print("\n" + "=" * 70)
    print("DISTRICT PROFILE TABLE")
    print("=" * 70)
    print(summary.to_string(index=False))

    # -- Step 2: Ingest Data ----------------------------------
    logger.info("\nStep 2: Ingesting data sources...")
    census = load_census_block_groups(config)
    stops = load_gtfs_stops(config)
    routes = load_gtfs_routes(config)
    ridership = load_ridership_data(config)
    crashes = load_crash_data(config)
    traffic = load_traffic_volumes(config)
    closures = load_road_closures(config)

    # -- Step 2b: Load real observational data --
    logger.info("\nStep 2b: Loading real observational data sources...")
    rbs_detail = load_rbs_stop_detail()
    schedule_obs = load_bus_schedule_observations()
    survey = load_student_survey()

    # -- Console: Data Source Status Report --
    print("\n" + "=" * 70)
    print("DATA SOURCE STATUS")
    print("=" * 70)

    def _src_status(name, df, synthetic_col="is_synthetic"):
        if df is None:
            return f"  {name:<40s}  NOT FOUND"
        n = len(df)
        if synthetic_col in df.columns if hasattr(df, 'columns') else False:
            is_synth = df[synthetic_col].any() if n > 0 else True
            tag = "SYNTHETIC" if is_synth else "REAL"
        else:
            tag = "REAL" if n > 0 else "EMPTY"
        return f"  {name:<40s}  {tag:>10s}  ({n:,} records)"

    print(_src_status("Census block groups", census))
    print(_src_status("GTFS stops", stops))
    print(_src_status("GTFS routes", routes))
    print(_src_status("Ridership (route-level)", ridership))
    print(_src_status("Crash records", crashes))
    print(_src_status("Traffic volumes", traffic))
    print(_src_status("Road closures", closures))
    print(_src_status("RBS stop-level ridership", rbs_detail, "route_id"))
    print(_src_status("Bus schedule observations", schedule_obs, "_none_"))
    print(_src_status("Student survey", survey, "_none_"))
    print()

    # Log schedule observation reliability summary
    if schedule_obs is not None and len(schedule_obs) > 0:
        mean_abs_dev = schedule_obs["deviation_minutes"].abs().mean()
        mean_dev = schedule_obs["deviation_minutes"].mean()
        max_late = schedule_obs["deviation_minutes"].max()
        n_late = (schedule_obs["deviation_minutes"] > 0).sum()
        n_early = (schedule_obs["deviation_minutes"] < 0).sum()

        print("=" * 70)
        print("SCHEDULE RELIABILITY (from observed arrival times)")
        print("=" * 70)
        print(f"  Observations:        {len(schedule_obs)}")
        print(f"  Mean deviation:      {mean_dev:+.1f} min (positive = late)")
        print(f"  Mean absolute dev:   {mean_abs_dev:.1f} min")
        print(f"  Worst late arrival:  {max_late:+.1f} min")
        print(f"  Trips arriving late: {n_late} ({100*n_late/len(schedule_obs):.0f}%)")
        print(f"  Trips arriving early: {n_early} ({100*n_early/len(schedule_obs):.0f}%)")
        print()
        for stop, grp in schedule_obs.groupby("stop_name"):
            school = grp["school"].iloc[0]
            avg = grp["deviation_minutes"].mean()
            print(f"    {stop:<30s} ({school}): avg {avg:+.1f} min, {len(grp)} obs")
        print()

    # Log survey summary
    if survey is not None and len(survey) > 0:
        print("=" * 70)
        print("STUDENT SURVEY SUMMARY")
        print("=" * 70)
        print(f"  Total responses:  {len(survey)}")
        if "current_mode" in survey.columns:
            print(f"  Current modes:")
            for mode, cnt in survey["current_mode"].value_counts().items():
                print(f"    {mode}: {cnt} ({100*cnt/len(survey):.1f}%)")
        if "would_ride_bus" in survey.columns:
            print(f"  Would ride bus:")
            for val, cnt in survey["would_ride_bus"].value_counts().items():
                print(f"    {val}: {cnt} ({100*cnt/len(survey):.1f}%)")
            n_yes = sum(1 for v in survey["would_ride_bus"] if str(v).lower().startswith("yes"))
            n_maybe = sum(1 for v in survey["would_ride_bus"] if str(v).lower().startswith("maybe"))
            print(f"  >> DIVERSION POTENTIAL: {n_yes} yes + {n_maybe} maybe = "
                  f"{n_yes+n_maybe}/{len(survey)} ({100*(n_yes+n_maybe)/len(survey):.1f}%)")
        print()

    # -- Step 3: Assign Stops to Districts --------------------
    logger.info("\nStep 3: Assigning transit stops to districts...")
    stops_assigned = dm.assign_points(
        stops.rename(columns={"stop_lat": "lat", "stop_lon": "lon"}),
        zone=None,  # Assign to all zones (a stop can be in both D and U)
    )

    stop_district = stops_assigned[["stop_id", "stop_name", "route_ids", "district_id"]].copy()
    stop_district.to_csv("outputs/tables/stop_district_matrix.csv", index=False)
    logger.info("Stop-district matrix saved (%d stops)", len(stop_district))

    print("\n" + "=" * 70)
    print("STOP-DISTRICT ASSIGNMENT")
    print("=" * 70)
    print(stop_district.to_string(index=False))

    # -- Step 4: Route-District Matrix ------------------------
    logger.info("\nStep 4: Building route-district matrix...")

    # Explode comma-separated route_ids so each stop-route pair gets its own row
    rd_data = stop_district.dropna(subset=["district_id"]).copy()
    rd_data["route_ids"] = rd_data["route_ids"].fillna("").astype(str)
    rd_data = rd_data[rd_data["route_ids"] != ""]
    rd_exploded = rd_data.assign(
        route_ids=rd_data["route_ids"].str.split(",")
    ).explode("route_ids")
    rd_exploded["route_ids"] = rd_exploded["route_ids"].str.strip()
    rd_exploded = rd_exploded[rd_exploded["route_ids"] != ""]

    # Count stops per route per district
    rd_matrix = (
        rd_exploded
        .groupby(["route_ids", "district_id"])
        .agg(n_stops=("stop_id", "count"))
        .reset_index()
        .pivot_table(index="route_ids", columns="district_id", values="n_stops", fill_value=0)
    )
    rd_matrix.to_csv("outputs/tables/route_district_matrix.csv")
    logger.info("Route-district matrix saved (%d routes x %d districts)", *rd_matrix.shape)

    print("\n" + "=" * 70)
    print("ROUTE-DISTRICT MATRIX (stops per route per district)")
    print("=" * 70)
    print(rd_matrix.to_string())

    # -- Step 5: Assign Census BGs to Districts ---------------
    logger.info("\nStep 5: Assigning census block groups to districts...")
    census_assigned = dm.assign_points(census, zone=None)
    census_assigned.to_csv("data/processed/census_bg_with_districts.csv", index=False)

    # Aggregate population by district
    assigned = census_assigned.dropna(subset=["district_id"]).copy()

    pop_by_district = (
        assigned
        .groupby("district_id")
        .agg(
            total_pop=("total_pop", "sum"),
            total_hh=("total_hh", "sum"),
            mean_income=("median_income", "mean"),
            zero_veh_hh=("zero_veh_hh", "sum"),
            transit_commuters=("transit_commuters", "sum"),
            total_workers=("total_workers", "sum"),
            n_bgs=("total_pop", "count"),
        )
        .reset_index()
    )

    # __ Fix 1: Zero-vehicle rate suppression detection __
    # If the real ACS data has -666666666 _ 0 for suppressed values,
    # all zero_veh_hh will be 0 even though the real rate is ~2-5%.
    # Detect this and use county-level fallback.
    has_bg_rates = "zero_veh_rate" in assigned.columns and "transit_share" in assigned.columns
    total_zvh = pop_by_district["zero_veh_hh"].sum()
    total_hh = pop_by_district["total_hh"].sum()
    all_zvh_zero = (total_zvh == 0 and total_hh > 0)

    if has_bg_rates:
        zvr_rates = {}
        ts_rates = {}
        for did, group in assigned.groupby("district_id"):
            valid_zvr = group.dropna(subset=["zero_veh_rate"])
            if len(valid_zvr) > 0 and valid_zvr["total_pop"].sum() > 0:
                zvr_rates[did] = round(
                    (valid_zvr["zero_veh_rate"] * valid_zvr["total_pop"]).sum()
                    / valid_zvr["total_pop"].sum(), 4
                )
            valid_ts = group.dropna(subset=["transit_share"])
            if len(valid_ts) > 0 and valid_ts["total_pop"].sum() > 0:
                ts_rates[did] = round(
                    (valid_ts["transit_share"] * valid_ts["total_pop"]).sum()
                    / valid_ts["total_pop"].sum(), 4
                )
        pop_by_district["zero_veh_rate"] = pop_by_district["district_id"].map(zvr_rates)
        pop_by_district["transit_share"] = pop_by_district["district_id"].map(ts_rates)
        logger.info("Used pre-computed BG-level rates (handles ACS suppression)")
    elif all_zvh_zero:
        # ALL zero_veh_hh are 0 -- data is suppressed. Use income-adjusted
        # county rate. Santa Clara County: ~5.5% zero-vehicle HH (ACS 2022).
        # Higher-income areas have lower rates.
        logger.warning("ALL zero_veh_hh values are 0 -- ACS data is suppressed.")
        logger.warning("Using income-adjusted county rate (Santa Clara: 5.5%%).")
        county_zvr = 0.055
        county_ts = 0.035
        county_median_income = 145_000
        for i, row in pop_by_district.iterrows():
            if row["total_hh"] > 0:
                inc = row["mean_income"] if row["mean_income"] > 0 else county_median_income
                # Higher income -> lower zero-veh rate (inversely proportional)
                income_adj = min(2.0, max(0.3, county_median_income / max(inc, 50000)))
                pop_by_district.at[i, "zero_veh_rate"] = round(county_zvr * income_adj, 4)
                pop_by_district.at[i, "transit_share"] = round(county_ts * income_adj, 4)
            else:
                pop_by_district.at[i, "zero_veh_rate"] = 0
                pop_by_district.at[i, "transit_share"] = 0
    else:
        pop_by_district["zero_veh_rate"] = (
            pop_by_district["zero_veh_hh"] / pop_by_district["total_hh"].clip(lower=1)
        ).round(4)
        pop_by_district["transit_share"] = (
            pop_by_district["transit_commuters"] / pop_by_district["total_workers"].clip(lower=1)
        ).round(4)

    # __ Fix 2: Population density cap + zone-level control __
    # Tract centroids can assign too many BGs to a district. Apply two layers:
    # Layer 1: Per-district density caps (realistic for the area)
    # Layer 2: Zone-level total caps (Los Gatos CDP ~33K, Union SD area ~35K)
    KNOWN_POP = {
        "LGHS": 33_000,  # Los Gatos CDP, ACS 2022
        "UNION": 35_000,  # Union SD attendance area estimate
    }

    areas = summary[["id", "name", "zone", "area_sq_miles"]].rename(columns={"id": "district_id"})
    profile = areas.merge(pop_by_district, on="district_id", how="left").fillna(0)
    profile["pop_density_per_sq_mi"] = (profile["total_pop"] / profile["area_sq_miles"].clip(lower=0.01)).round(0)

    # Layer 1: density caps
    DENSITY_CAPS = {
        "D1": 5500, "D2": 6000, "D3": 5500, "D4": 6500, "D5": 7500,
        "D6": 5000, "D7": 3000, "D8": 400, "D9": 100, "D10": 50,
        "U1": 4000, "U2": 5500, "U3": 5500, "U4": 5500, "U5": 5500, "U6": 3000,
    }
    pop_adjustments = []
    for i, row in profile.iterrows():
        did = row["district_id"]
        area = row["area_sq_miles"]
        pop = row["total_pop"]
        max_d = DENSITY_CAPS.get(did, 6000)
        max_pop = max_d * area
        if pop > max_pop and pop > 0:
            scale = max_pop / pop
            pop_adjustments.append(f"  {did}: capped {pop:,.0f} -> {max_pop:,.0f} (density {row['pop_density_per_sq_mi']:.0f} > {max_d})")
            profile.at[i, "total_pop"] = round(max_pop)
            profile.at[i, "pop_density_per_sq_mi"] = max_d
            for col in ["total_hh", "zero_veh_hh", "transit_commuters", "total_workers"]:
                if col in profile.columns:
                    profile.at[i, col] = round(profile.at[i, col] * scale)

    # Layer 2: zone-level totals
    for zone, target in KNOWN_POP.items():
        zone_mask = profile["zone"] == zone
        zone_pop = profile.loc[zone_mask, "total_pop"].sum()
        if zone_pop > target * 1.15:  # Allow 15% overshoot before capping
            scale = target / zone_pop
            pop_adjustments.append(f"  {zone} zone: scaled {zone_pop:,.0f} -> {target:,.0f} (exceeded known pop)")
            for i in profile[zone_mask].index:
                profile.at[i, "total_pop"] = round(profile.at[i, "total_pop"] * scale)
                for col in ["total_hh", "zero_veh_hh", "transit_commuters", "total_workers"]:
                    if col in profile.columns:
                        profile.at[i, col] = round(profile.at[i, col] * scale)
            profile.loc[zone_mask, "pop_density_per_sq_mi"] = (
                profile.loc[zone_mask, "total_pop"] / profile.loc[zone_mask, "area_sq_miles"].clip(lower=0.01)
            ).round(0)

    if pop_adjustments:
        logger.warning("Population adjustments applied:")
        for adj in pop_adjustments:
            logger.warning(adj)

    # __ Fix 3: Fallback demographics for empty districts __
    # Mountain and edge districts may have no assigned BGs because
    # tract centroids miss the polygon. Use known Census estimates.
    FALLBACK_DEMOGRAPHICS = {
        "D8": {"total_pop": 1350, "pop_density_per_sq_mi": 319, "mean_income": 155000,
               "zero_veh_rate": 0.012, "transit_share": 0.008, "source": "ACS 2022 est. 95030 rural tracts"},
        "D9": {"total_pop": 420, "pop_density_per_sq_mi": 46, "mean_income": 180000,
               "zero_veh_rate": 0.005, "transit_share": 0.003, "source": "ACS 2022 est. 95033 Lexington area"},
        "D10": {"total_pop": 380, "pop_density_per_sq_mi": 34, "mean_income": 175000,
                "zero_veh_rate": 0.004, "transit_share": 0.002, "source": "ACS 2022 est. 95033 Summit/Skyline area"},
        "U1": {"total_pop": 8200, "pop_density_per_sq_mi": 2300, "mean_income": 195000,
               "zero_veh_rate": 0.025, "transit_share": 0.018, "source": "ACS 2022 est. Alta Vista attendance area"},
        "U6": {"total_pop": 3100, "pop_density_per_sq_mi": 1625, "mean_income": 105000,
               "zero_veh_rate": 0.032, "transit_share": 0.025, "source": "ACS 2022 est. S. Almaden area"},
    }

    for did, fallback in FALLBACK_DEMOGRAPHICS.items():
        idx = profile[profile["district_id"] == did].index
        if len(idx) > 0:
            row_idx = idx[0]
            current_pop = profile.at[row_idx, "total_pop"]
            if current_pop == 0:
                for key, val in fallback.items():
                    if key != "source" and key in profile.columns:
                        profile.at[row_idx, key] = val
                logger.info("Applied fallback demographics for %s: pop=%d (%s)",
                           did, fallback["total_pop"], fallback.get("source", ""))

    # Recompute density after fixes
    profile["pop_density_per_sq_mi"] = (
        profile["total_pop"] / profile["area_sq_miles"].clip(lower=0.01)
    ).round(0)

    profile.to_csv("outputs/tables/district_demographic_profile.csv", index=False)

    # __ Population validation __
    lghs_pop = profile[profile["zone"] == "LGHS"]["total_pop"].sum()
    union_pop = profile[profile["zone"] == "UNION"]["total_pop"].sum()
    total_pop = profile["total_pop"].sum()
    print("\n" + "=" * 70)
    print("POPULATION VALIDATION")
    print("=" * 70)
    print(f"  LGHS zone (D1-D10):  {lghs_pop:>8,.0f}  (expected ~33,000 - Los Gatos CDP)")
    print(f"  Union zone (U1-U6):  {union_pop:>8,.0f}  (expected ~35,000 - Union SD area)")
    print(f"  Total study area:    {total_pop:>8,.0f}")
    if total_pop > 100_000:
        print(f"  ** WARNING: Total exceeds 100K -- some BGs may be over-assigned **")
    if total_pop < 20_000:
        print(f"  ** WARNING: Total below 20K -- some BGs may be under-assigned **")

    print("\n" + "=" * 70)
    print("DISTRICT DEMOGRAPHIC PROFILE")
    print("=" * 70)
    cols = ["district_id", "name", "zone", "total_pop", "pop_density_per_sq_mi",
            "mean_income", "zero_veh_rate", "transit_share"]
    print(profile[cols].to_string(index=False))

    # -- Step 6: Assign Crashes to Districts ------------------
    logger.info("\nStep 6: Assigning crash records to districts...")
    crashes_assigned = dm.assign_points(crashes, zone="LGHS")
    crash_by_district = (
        crashes_assigned
        .dropna(subset=["district_id"])
        .groupby("district_id")
        .agg(total_crashes=("crash_id", "count"),
             fatal=("severity", lambda x: (x == "K").sum()),
             serious_injury=("severity", lambda x: (x == "A").sum()))
        .reset_index()
    )
    crash_by_district.to_csv("outputs/tables/crashes_by_district.csv", index=False)
    logger.info("Crash-district summary saved")

    # -- Step 7: Data Quality Report --------------------------
    logger.info("\nStep 7: Generating data quality report...")
    report = run_data_quality_report(census, stops, crashes, traffic, closures)
    report_path = Path("outputs/tables/data_quality_report.md")
    report_path.write_text(report)
    logger.info("Data quality report saved to %s", report_path)

    print("\n" + "=" * 70)
    print("DATA QUALITY REPORT")
    print("=" * 70)
    print(report)

    # ================================================================
    # PHASE A2 -- COST MODEL
    # ================================================================
    logger.info("\n" + "=" * 70)
    logger.info("PHASE A2 -- COST MODEL")
    logger.info("=" * 70)

    # -- Step 8: Route Service Estimates -----------------------
    logger.info("\nStep 8: Generating route service estimates...")
    cost_params = get_cost_params(config)
    route_service = generate_route_service_estimates(config)

    # Update total_system_stops from real GTFS if available
    system_stop_counts = count_system_stops_per_route()
    if system_stop_counts:
        for i, row in route_service.iterrows():
            rid = row["route_id"]
            if rid in system_stop_counts:
                route_service.at[i, "total_system_stops"] = system_stop_counts[rid]
                logger.info("  Route %s: updated total_system_stops to %d (from GTFS)",
                           rid, system_stop_counts[rid])

    print("\n" + "=" * 70)
    print("ROUTE SERVICE ESTIMATES (NTD-based)")
    print("=" * 70)
    print(route_service[["route_id", "route_name", "annual_revenue_hours",
                         "annual_revenue_miles", "annual_boardings", "status"]].to_string(index=False))

    # -- Step 9: Operating Costs by Route ---------------------
    logger.info("\nStep 9: Computing operating costs by route...")
    route_costs = compute_annual_operating_costs(route_service, cost_params)

    print("\n" + "=" * 70)
    print("ANNUAL OPERATING COSTS BY ROUTE")
    print("=" * 70)
    cost_cols = ["route_id", "annual_operating_cost", "fare_revenue", "net_operating_cost"]
    for _, row in route_costs.iterrows():
        print(f"  Route {row['route_id']:>3s}: Operating ${row['annual_operating_cost']:>12,.0f}  "
              f"Fare Rev ${row['fare_revenue']:>10,.0f}  "
              f"Net ${row['net_operating_cost']:>12,.0f}")
    print(f"  {'TOTAL':>9s}: Operating ${route_costs['annual_operating_cost'].sum():>12,.0f}  "
          f"Fare Rev ${route_costs['fare_revenue'].sum():>10,.0f}  "
          f"Net ${route_costs['net_operating_cost'].sum():>12,.0f}")

    route_costs.to_csv("outputs/tables/route_operating_costs.csv", index=False)

    # -- Step 10: Allocate Operating Costs to Districts -------
    logger.info("\nStep 10: Allocating operating costs to districts...")

    # Load the route-district matrix from Phase A1
    rdm_path = Path("outputs/tables/route_district_matrix.csv")
    rdm = pd.read_csv(rdm_path, index_col="route_ids")

    district_op_costs = allocate_operating_costs_to_districts(route_costs, rdm)

    print("\n" + "=" * 70)
    print("OPERATING COSTS ALLOCATED TO DISTRICTS")
    print("=" * 70)
    for _, row in district_op_costs.iterrows():
        print(f"  {row['district_id']:>4s}: ${row['allocated_operating_cost']:>10,.0f} operating  "
              f"({row['n_routes']:.0f} routes)")
    print(f"  Total: ${district_op_costs['allocated_operating_cost'].sum():>10,.0f}")

    # -- Step 11: Capital Costs by District --------------------
    logger.info("\nStep 11: Computing capital costs by district...")
    stop_dm = pd.read_csv("outputs/tables/stop_district_matrix.csv")
    district_cap_costs = compute_capital_costs(stop_dm, config)

    print("\n" + "=" * 70)
    print("CAPITAL COSTS BY DISTRICT")
    print("=" * 70)
    for _, row in district_cap_costs.iterrows():
        print(f"  {row['district_id']:>4s}: {row['n_stops']:>2.0f} stops  "
              f"${row['total_capital']:>10,.0f} total capital")
    print(f"  Total: ${district_cap_costs['total_capital'].sum():>10,.0f}")

    district_cap_costs.to_csv("outputs/tables/district_capital_costs.csv", index=False)

    # -- Step 12: Combined District Cost Summary ---------------
    logger.info("\nStep 12: Building district cost summary...")
    district_cost_summary = build_district_cost_summary(district_op_costs, district_cap_costs)

    print("\n" + "=" * 70)
    print("DISTRICT COST SUMMARY (Annual)")
    print("=" * 70)
    print(f"  {'District':>8s}  {'Operating':>12s}  {'Capital/yr':>12s}  {'Total/yr':>12s}  {'Stops':>5s}")
    print(f"  {'--------':>8s}  {'----------':>12s}  {'----------':>12s}  {'--------':>12s}  {'-----':>5s}")
    for _, row in district_cost_summary.iterrows():
        print(f"  {row['district_id']:>8s}  ${row['allocated_operating_cost']:>11,.0f}  "
              f"${row['annual_amortized_capital']:>11,.0f}  "
              f"${row['total_annual_cost']:>11,.0f}  "
              f"{row['n_stops']:>5.0f}")

    district_cost_summary.to_csv("outputs/tables/district_cost_summary.csv", index=False)

    # -- Step 13: NTD Peer Benchmarking ------------------------
    logger.info("\nStep 13: Peer agency benchmarking...")
    peers = compute_peer_benchmarks()

    print("\n" + "=" * 70)
    print("NTD PEER BENCHMARKS")
    print("=" * 70)
    print(peers.to_string(index=False))
    peers.to_csv("outputs/tables/peer_benchmarks.csv", index=False)

    # -- Step 14: Route 76 Restoration Scenario ----------------
    logger.info("\nStep 14: Route 76 restoration cost estimate...")
    r76_costs = estimate_route76_restoration_costs(cost_params)

    print("\n" + "=" * 70)
    print("ROUTE 76 RESTORATION SCENARIO -- Cost Estimate")
    print("=" * 70)
    print(f"  Capital (total):          ${r76_costs['capital_cost_total']:>12,}")
    print(f"    Stop rehabilitation:    ${r76_costs['capital_stop_rehab']:>12,}")
    print(f"    Vehicle (+ spare):      ${r76_costs['capital_vehicle']:>12,}")
    print(f"  Annual operating cost:    ${r76_costs['annual_operating_cost']:>12,}")
    print(f"  Annual fare revenue:      ${r76_costs['annual_fare_revenue']:>12,}")
    print(f"  Annual net cost:          ${r76_costs['annual_net_cost']:>12,}")
    print(f"  Cost per boarding:        ${r76_costs['cost_per_boarding']:>12.2f}")
    print(f"  Service: {r76_costs['assumptions']['trips_per_day']} trips/day, "
          f"{r76_costs['assumptions']['service_days']} days/yr, "
          f"{r76_costs['assumptions']['route_miles']} mi")

    # -- Step 15: NPV of Costs ---------------------------------
    logger.info("\nStep 15: Computing NPV of costs...")

    # Full route costs (what VTA spends to run these routes system-wide)
    total_annual_op_full_route = route_costs[route_costs["status"] == "active"]["annual_operating_cost"].sum()
    total_capital = district_cap_costs["total_capital"].sum()

    # Study area allocated costs (our districts' share based on stop proportion)
    total_annual_op_allocated = district_cost_summary["allocated_operating_cost"].sum()
    total_annual_cost_allocated = district_cost_summary["total_annual_cost"].sum()

    npv_costs_full = compute_cost_npv(
        total_annual_op_full_route, total_capital,
        cost_params["discount_rates"], cost_params["time_horizon_operating"],
    )
    npv_costs_allocated = compute_cost_npv(
        total_annual_op_allocated, total_capital,
        cost_params["discount_rates"], cost_params["time_horizon_operating"],
    )

    # Zone-level costs
    demo_temp = pd.read_csv("outputs/tables/district_demographic_profile.csv")
    lghs_districts = set(demo_temp[demo_temp["zone"] == "LGHS"]["district_id"])
    union_districts = set(demo_temp[demo_temp["zone"] == "UNION"]["district_id"])

    lghs_annual_cost = district_cost_summary[
        district_cost_summary["district_id"].isin(lghs_districts)
    ]["total_annual_cost"].sum()
    union_annual_cost = district_cost_summary[
        district_cost_summary["district_id"].isin(union_districts)
    ]["total_annual_cost"].sum()
    lghs_capital = district_cap_costs[
        district_cap_costs["district_id"].isin(lghs_districts)
    ]["total_capital"].sum() if "district_id" in district_cap_costs.columns else total_capital * 0.9
    union_capital = total_capital - lghs_capital

    npv_costs_lghs = compute_cost_npv(
        lghs_annual_cost, lghs_capital,
        cost_params["discount_rates"], cost_params["time_horizon_operating"],
    )
    npv_costs_union = compute_cost_npv(
        union_annual_cost, union_capital,
        cost_params["discount_rates"], cost_params["time_horizon_operating"],
    )

    print("\n" + "=" * 70)
    print("PRESENT VALUE OF COSTS")
    print("=" * 70)
    print(f"\n  Full Route Costs (entire Rt 27 + 17X system-wide):")
    print(f"    Annual operating: {total_annual_op_full_route:>12,.0f}")
    for _, row in npv_costs_full.iterrows():
        print(f"    At {row['discount_rate_label']:>6s}: PV {row['pv_total_cost']:>14,}")

    print(f"\n  Study Area Allocated Costs (stops in our districts only):")
    print(f"    Annual operating: {total_annual_op_allocated:>12,.0f}  "
          f"({100*total_annual_op_allocated/max(total_annual_op_full_route,1):.0f}% of full route)")
    for _, row in npv_costs_allocated.iterrows():
        print(f"    At {row['discount_rate_label']:>6s}: PV {row['pv_total_cost']:>14,}")

    print(f"\n  LGHS Zone (D1-D10):  Annual {lghs_annual_cost:>10,.0f}")
    print(f"  Union Zone (U1-U6): Annual {union_annual_cost:>10,.0f}")

    # Save the ALLOCATED costs as the primary NPV (this is what the BCR should use)
    npv_costs = npv_costs_allocated
    npv_costs.to_csv("outputs/tables/npv_costs.csv", index=False)

    # Save zone-level data
    zone_costs = pd.DataFrame({
        "zone": ["LGHS", "UNION", "FULL_AREA"],
        "annual_operating": [lghs_annual_cost, union_annual_cost, total_annual_op_allocated],
        "capital": [lghs_capital, union_capital, total_capital],
        "annual_total": [lghs_annual_cost + lghs_capital/20, union_annual_cost + union_capital/20,
                         total_annual_cost_allocated],
    })
    zone_costs.to_csv("outputs/tables/zone_costs.csv", index=False)

    # -- Step 16: Generate Dashboard -------------------------
    # ================================================================
    # PHASE A3 -- BENEFIT MODEL
    # ================================================================
    logger.info("\n" + "=" * 70)
    logger.info("PHASE A3 -- BENEFIT MODEL")
    logger.info("=" * 70)

    logger.info("\nStep 16: Computing all benefit categories...")
    benefit_params = get_benefit_params(config)

    demo_df = pd.read_csv("outputs/tables/district_demographic_profile.csv")
    service_pop = int(demo_df["total_pop"].sum())

    active_routes = route_costs[route_costs["status"] == "active"]
    total_boardings_active = int(active_routes["annual_boardings"].sum())
    total_rev_miles = active_routes["annual_revenue_miles"].sum()

    all_benefits = compute_all_benefits(
        config, route_costs, total_rev_miles, service_pop,
        avg_auto_trip_min=22.0, avg_transit_trip_min=35.0, avg_auto_trip_miles=7.5,
    )

    print("\n" + "=" * 70)
    print("ANNUAL BENEFITS BY CATEGORY")
    print("=" * 70)
    total_annual_benefits = 0
    for b in all_benefits:
        print(f"  {b['category']:<40s}  ${b['annual_benefit']:>12,.0f}")
        total_annual_benefits += b["annual_benefit"]
    print(f"  {'TOTAL':<40s}  ${total_annual_benefits:>12,.0f}")

    benefits_df = pd.DataFrame(all_benefits)
    benefits_df.to_csv("outputs/tables/annual_benefits_by_category.csv", index=False)

    logger.info("\nStep 17: Allocating benefits to districts...")
    stop_dm_b = pd.read_csv("outputs/tables/stop_district_matrix.csv")
    district_benefits = allocate_benefits_to_districts(
        all_benefits, stop_dm_b, demo_df, benefit_params,
    )
    print("\n" + "=" * 70)
    print("BENEFITS ALLOCATED TO DISTRICTS")
    print("=" * 70)
    for _, row in district_benefits.iterrows():
        print(f"  {row['district_id']:>4s}: ${row['total_benefits']:>12,.0f}  ({row.get('n_stops',0):.0f} stops)")
    district_benefits.to_csv("outputs/tables/district_benefits.csv", index=False)

    logger.info("\nStep 18: Computing NPV of benefits...")
    npv_benefits = compute_benefit_npv(
        total_annual_benefits, benefit_params["discount_rates"], benefit_params["time_horizon"],
    )
    print("\n" + "=" * 70)
    print("PRESENT VALUE OF BENEFITS")
    print("=" * 70)
    for _, row in npv_benefits.iterrows():
        print(f"  At {row['discount_rate_label']:>6s}: PV Benefits ${row['pv_benefits']:>14,}")
    npv_benefits.to_csv("outputs/tables/npv_benefits.csv", index=False)

    logger.info("\nStep 19: BCR preview...")

    # Compute zone-level benefits
    district_benefits_with_zone = district_benefits.copy()
    did_to_zone = dict(zip(demo_df["district_id"], demo_df["zone"]))
    district_benefits_with_zone["zone"] = district_benefits_with_zone["district_id"].map(did_to_zone)

    lghs_annual_ben = district_benefits_with_zone[
        district_benefits_with_zone["zone"] == "LGHS"
    ]["total_benefits"].sum()
    union_annual_ben = district_benefits_with_zone[
        district_benefits_with_zone["zone"] == "UNION"
    ]["total_benefits"].sum()

    npv_ben_lghs = compute_benefit_npv(
        lghs_annual_ben, benefit_params["discount_rates"], benefit_params["time_horizon"],
    )
    npv_ben_union = compute_benefit_npv(
        union_annual_ben, benefit_params["discount_rates"], benefit_params["time_horizon"],
    )

    # Save zone-level benefits
    zone_benefits = pd.DataFrame({
        "zone": ["LGHS", "UNION", "FULL_AREA"],
        "annual_benefits": [lghs_annual_ben, union_annual_ben, total_annual_benefits],
    })
    zone_benefits.to_csv("outputs/tables/zone_benefits.csv", index=False)

    print("\n" + "=" * 70)
    print("BENEFIT-COST RATIOS BY ZONE")
    print("=" * 70)

    def print_zone_bcr(label, npv_b_df, npv_c_df, ann_ben, ann_cost):
        print(f"\n  {label}:")
        print(f"    Annual benefits:  {ann_ben:>12,.0f}")
        print(f"    Annual costs:     {ann_cost:>12,.0f}")
        for i, r in enumerate(benefit_params["discount_rates"]):
            pv_b = npv_b_df.iloc[i]["pv_benefits"]
            pv_c = npv_c_df.iloc[i]["pv_total_cost"]
            bcr = pv_b / max(pv_c, 1)
            net = pv_b - pv_c
            print(f"    At {r*100:.1f}%: BCR = {bcr:.2f}  |  PV Benefits {pv_b:>12,}  |  PV Costs {pv_c:>12,}  |  Net {net:>12,}")

    print_zone_bcr("FULL STUDY AREA (all districts)",
                   npv_benefits, npv_costs, total_annual_benefits, total_annual_cost_allocated)
    print_zone_bcr("LGHS ZONE (D1-D10, Los Gatos)",
                   npv_ben_lghs, npv_costs_lghs, lghs_annual_ben, lghs_annual_cost)
    print_zone_bcr("UNION ZONE (U1-U6, Union SD area)",
                   npv_ben_union, npv_costs_union, union_annual_ben, union_annual_cost)

    # Save all zone NPVs for dashboard
    zone_npv_rows = []
    for i, r in enumerate(benefit_params["discount_rates"]):
        for zone, nb, nc in [
            ("FULL_AREA", npv_benefits, npv_costs),
            ("LGHS", npv_ben_lghs, npv_costs_lghs),
            ("UNION", npv_ben_union, npv_costs_union),
        ]:
            zone_npv_rows.append({
                "zone": zone,
                "discount_rate": r,
                "discount_rate_label": f"{r*100:.1f}%",
                "pv_benefits": nb.iloc[i]["pv_benefits"],
                "pv_costs": nc.iloc[i]["pv_total_cost"],
                "bcr": round(nb.iloc[i]["pv_benefits"] / max(nc.iloc[i]["pv_total_cost"], 1), 3),
                "net_benefits": nb.iloc[i]["pv_benefits"] - nc.iloc[i]["pv_total_cost"],
            })
    pd.DataFrame(zone_npv_rows).to_csv("outputs/tables/zone_npv.csv", index=False)

    # ================================================================
    # PHASE A4 -- DEMAND MODEL & EQUITY SCORING
    # ================================================================
    logger.info("\n" + "=" * 70)
    logger.info("PHASE A4 -- DEMAND MODEL & EQUITY SCORING")
    logger.info("=" * 70)

    logger.info("\nStep 20: Computing demand index, service level, and unmet need...")
    demo_for_demand = pd.read_csv("outputs/tables/district_demographic_profile.csv")
    cost_for_demand = pd.read_csv("outputs/tables/district_cost_summary.csv")
    stops_for_demand = pd.read_csv("outputs/tables/stop_district_matrix.csv")

    demand_results = run_demand_analysis(demo_for_demand, cost_for_demand, stops_for_demand)
    tdi_df = demand_results["tdi"]
    sli_df = demand_results["sli"]
    unmet_df = demand_results["unmet_need"]
    coverage_df = demand_results["coverage"]

    tdi_df.to_csv("outputs/tables/transit_demand_index.csv", index=False)
    sli_df.to_csv("outputs/tables/service_level_index.csv", index=False)
    unmet_df.to_csv("outputs/tables/unmet_need.csv", index=False)
    coverage_df.to_csv("outputs/tables/coverage_gaps.csv", index=False)

    print("\n" + "=" * 70)
    print("TRANSIT DEMAND INDEX (higher = more need)")
    print("=" * 70)
    print(f"  {'District':>8s}  {'TDI':>5s}  {'Rank':>4s}  {'PopDens':>7s}  {'ZeroVeh':>7s}  {'Income':>7s}")
    for _, r in tdi_df.sort_values("tdi", ascending=False).iterrows():
        print(f"  {r['district_id']:>8s}  {r['tdi']:.3f}  {r['tdi_rank']:>4.0f}  "
              f"{r['f_pop_density']:.3f}    {r['f_zero_veh']:.3f}    {r['f_income_inverse']:.3f}")

    print("\n" + "=" * 70)
    print("UNMET NEED & EQUITY ANALYSIS")
    print("=" * 70)
    print(f"  {'District':>8s}  {'TDI':>5s}  {'SLI':>5s}  {'Unmet':>5s}  {'Rank':>4s}  {'Stops':>5s}  {'Flag':>8s}  {'Gap':<15s}")
    for _, r in unmet_df.sort_values("unmet_need", ascending=False).iterrows():
        flag_color = "**" if r["equity_flag"] == "PRIORITY" else ""
        print(f"  {flag_color}{r['district_id']:>8s}  {r['tdi']:.3f}  {r['sli']:.3f}  "
              f"{r['unmet_need']:.3f}  {r['unmet_need_rank']:>4.0f}  {r['n_stops']:>5.0f}  "
              f"{r['equity_flag']:>8s}  {r['service_gap']:<15s}{flag_color}")

    print("\n" + "=" * 70)
    print("COVERAGE GAPS")
    print("=" * 70)
    total_gap = coverage_df["gap_population"].sum()
    total_pop = coverage_df["total_pop"].sum()
    print(f"  Total pop beyond 0.5-mi walk: {total_gap:,} / {total_pop:,} ({100*total_gap/max(total_pop,1):.1f}%)")
    print()
    for _, r in coverage_df.sort_values("gap_fraction", ascending=False).iterrows():
        if r["total_pop"] > 0:
            bar = "#" * int(r["gap_fraction"] * 20)
            print(f"  {r['district_id']:>4s}: {r['gap_fraction']*100:5.1f}% gap  "
                  f"({r['gap_population']:>5,.0f} people)  [{bar:<20s}]")

    # ================================================================
    # SCENARIO COMPARISON
    # ================================================================
    logger.info("\n" + "=" * 70)
    logger.info("SCENARIO COMPARISON")
    logger.info("=" * 70)

    # Get survey data for moderate/optimistic scenarios
    survey_yes = 0
    if survey is not None and "would_ride_bus" in survey.columns:
        survey_yes = sum(1 for v in survey["would_ride_bus"] if str(v).lower().startswith("yes"))
        logger.info("Survey 'Yes' responses for scenario modeling: %d", survey_yes)

    demo_for_scen = pd.read_csv("outputs/tables/district_demographic_profile.csv")
    union_pop_scen = int(demo_for_scen[demo_for_scen["zone"] == "UNION"]["total_pop"].sum())
    service_pop_scen = int(demo_for_scen["total_pop"].sum())
    n_study_stops = int(stop_district["district_id"].notna().sum())

    scenario_results = run_scenario_comparison(
        base_boardings=total_boardings_active,
        bus_revenue_miles=total_rev_miles,
        service_area_pop=service_pop_scen,
        union_pop=union_pop_scen,
        allocated_annual_operating=total_annual_op_allocated,
        n_study_area_stops=n_study_stops,
        route_costs_df=route_costs,
        survey_yes_count=survey_yes,
        discount_rate=0.035,
        time_horizon=20,
    )

    print("\n" + "=" * 70)
    print("SCENARIO COMPARISON (at 3.5% discount rate)")
    print("=" * 70)
    print(f"\n  {'':>14s}  {'Conservative':>14s}  {'Moderate':>14s}  {'Optimistic':>14s}")
    print(f"  {'':>14s}  {'(base case)':>14s}  {'(+survey)':>14s}  {'(best case)':>14s}")
    print(f"  {'-'*14}  {'-'*14}  {'-'*14}  {'-'*14}")

    fields = [
        ("Boardings/yr", "total_boardings", "{:>14,}"),
        ("Diverted trips", "diverted_trips", "{:>14,}"),
        ("Avoided VMT", "avoided_vmt", "{:>14,}"),
        ("Ann. Benefits", "annual_benefits", "${:>13,}"),
        ("Ann. Costs", "annual_costs", "${:>13,}"),
        ("Capital", "total_capital", "${:>13,}"),
        ("PV Benefits", "pv_benefits", "${:>13,}"),
        ("PV Costs", "pv_costs", "${:>13,}"),
        ("Net PV", "net_pv", "${:>13,}"),
        ("BCR", "bcr", "{:>14.2f}"),
    ]
    for label, key, fmt in fields:
        vals = [fmt.format(r[key]) for r in scenario_results]
        print(f"  {label:>14s}  {'  '.join(vals)}")

    # Print key parameter differences
    print(f"\n  Key parameter differences:")
    for s in scenario_results:
        kp = s["key_params"]
        print(f"    {s['scenario']}: diversion={kp['diversion_rate']:.0%}, "
              f"option=${kp['option_value_per_capita']}/cap, "
              f"ben_growth={kp['benefit_growth']:.1%}, "
              f"cost_growth={kp['cost_growth']:.1%}")

    # Print benefit breakdown per scenario
    for s in scenario_results:
        print(f"\n  {s['scenario']} benefit breakdown:")
        for b in s["benefit_categories"]:
            print(f"    {b['category']:<40s}  ${b['annual_benefit']:>12,.0f}")

    # Save scenario results
    import json
    scenario_summary = [{k: v for k, v in r.items() if k != "benefit_categories"} for r in scenario_results]
    pd.DataFrame(scenario_summary).to_csv("outputs/tables/scenario_comparison.csv", index=False)
    with open("outputs/tables/scenario_detail.json", "w", encoding="utf-8") as f:
        json.dump(scenario_results, f, indent=2, default=str)

    # -- Step 22: Generate Dashboard --
    logger.info("\nStep 22: Generating interactive dashboard...")
    from src.generate_dashboard import generate_dashboard
    dashboard_path = generate_dashboard("outputs/cba_dashboard.html")

    # -- Summary ----------------------------------------------
    print("\n" + "=" * 70)
    print("PHASE A1 + A2 + A3 + A4 COMPLETE")
    print("=" * 70)
    print(f"  Districts loaded:     {len(dm.districts)} (10 LGHS + 6 Union)")
    print(f"  Census block groups:  {len(census)}")
    print(f"  Transit stops:        {len(stops)}")
    print(f"  Routes:               {len(routes)}")
    print(f"  Crash records:        {len(crashes)}")
    print(f"  Traffic profiles:     {len(traffic)} hourly records")
    print(f"  Closures:             {len(closures)}")
    print(f"\n  Outputs in: outputs/tables/")
    print(f"  GeoJSON in: data/geospatial/districts/")
    print(f"  Dashboard:  {dashboard_path}")
    print(f"\n  Open the dashboard in your browser to visualize all results.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
