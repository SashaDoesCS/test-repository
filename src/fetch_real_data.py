"""
fetch_real_data.py -- Fetch real data from public APIs to replace synthetic data.

APIs used (all free, no authentication required):
  1. U.S. Census Bureau ACS 5-Year API (block group level)
  2. VTA GTFS feed (public download)

Run this script BEFORE run_analysis.py to populate data/processed/ and
data/geospatial/gtfs/ with real data. If fetch fails, the pipeline
falls back to synthetic data automatically.

Usage:
    python src/fetch_real_data.py

Requirements:
    - Internet connection
    - requests library (pip install requests)

Standards:
    - Census API: https://api.census.gov/data.html
    - GTFS: https://gtfs.org/schedule/reference/
"""

import csv
import io
import json
import logging
import os
import zipfile
from pathlib import Path

# Resolve project root from this file's location (src/fetch_real_data.py -> project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    logger.warning("requests library not installed. Run: pip install requests")


# Santa Clara County FIPS: State=06, County=085
STATE_FIPS = "06"
COUNTY_FIPS = "085"

# ACS 5-Year vintage
ACS_YEAR = "2022"  # Latest stable 5-year: 2018-2022
ACS_DATASET = f"{ACS_YEAR}/acs/acs5"

# Census tracts that cover our study area (Los Gatos + adjacent)
# These cover ZIP codes 95030, 95032, 95033, 95124, 95118, 95120
# We fetch ALL block groups in the county and filter spatially later
STUDY_TRACTS = None  # Fetch all, filter in pipeline

# VTA GTFS
VTA_GTFS_URL = "https://gtfs.vta.org/gtfs_vta.zip"
# Backup URL patterns
VTA_GTFS_URLS = [
    "https://gtfs.vta.org/gtfs_vta.zip",  # Official VTA developer site (confirmed active)
    "https://data.vta.org/documents/47506a089a5146ca91f400ad9ee04ccf/content",  # ArcGIS Open Data
]

# Census Gazetteer for tract centroids (block group Gazetteer doesn't exist)
# We use tract centroids as proxy for block group locations -- accurate enough
# since block groups nest within tracts and tracts are small.
# Confirmed file exists in directory listing at:
# https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/
GAZETTEER_TRACT_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_tracts_national.zip"


def fetch_census_data(output_dir: str = None) -> bool:
    """Fetch ACS 5-Year block group data from Census Bureau API.

    Variables fetched:
        B01001_001E  Total population
        B19013_001E  Median household income
        B08201_001E  Total households
        B08201_002E  Households with 0 vehicles
        B08301_001E  Total workers 16+
        B08301_010E  Workers commuting by public transit
        B09001_001E  Population in households (proxy for under-18)
        B01001_020E+ Senior population bins (65+)

    No API key needed for <500 requests/day.

    Args:
        output_dir: Directory to save output CSV.

    Returns:
        True if successful, False otherwise.

    Standard: U.S. Census Bureau API, ACS 5-Year Estimates.
    """
    if not HAS_REQUESTS:
        logger.error("Cannot fetch census data: requests library not installed")
        return False

    if output_dir is None:
        output_dir = str(PROJECT_ROOT / "data" / "processed")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Variables to fetch
    variables = [
        "B01001_001E",  # Total population
        "B19013_001E",  # Median household income
        "B08201_001E",  # Total households
        "B08201_002E",  # 0-vehicle households
        "B08301_001E",  # Total workers 16+
        "B08301_010E",  # Public transit commuters
        "B09001_001E",  # Population under 18 (in households)
        # Senior population: sum of male 65-66 through 85+ and female equivalents
        "B01001_020E",  # Male 65-66
        "B01001_021E",  # Male 67-69
        "B01001_022E",  # Male 70-74
        "B01001_023E",  # Male 75-79
        "B01001_024E",  # Male 80-84
        "B01001_025E",  # Male 85+
        "B01001_044E",  # Female 65-66
        "B01001_045E",  # Female 67-69
        "B01001_046E",  # Female 70-74
        "B01001_047E",  # Female 75-79
        "B01001_048E",  # Female 80-84
        "B01001_049E",  # Female 85+
    ]

    var_str = ",".join(variables)
    url = (
        f"https://api.census.gov/data/{ACS_DATASET}"
        f"?get=NAME,{var_str}"
        f"&for=block%20group:*"
        f"&in=state:{STATE_FIPS}%20county:{COUNTY_FIPS}"
    )

    logger.info("Fetching Census ACS data from: %s", url[:120] + "...")

    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        logger.error("Census API request failed: %s", e)
        return False
    except json.JSONDecodeError as e:
        logger.error("Census API returned invalid JSON: %s", e)
        return False

    if not data or len(data) < 2:
        logger.error("Census API returned no data")
        return False

    headers = data[0]
    rows = data[1:]
    logger.info("Received %d block groups from Census API", len(rows))

    # Parse into records
    records = []
    for row in rows:
        row_dict = dict(zip(headers, row))

        # Build GEOID
        state = row_dict.get("state", "")
        county = row_dict.get("county", "")
        tract = row_dict.get("tract", "")
        bg = row_dict.get("block group", "")
        geoid = f"{state}{county}{tract}{bg}"

        # Parse values (Census returns strings, sometimes negative for missing/suppressed)
        def safe_int(val):
            """Parse ACS integer value. Returns None for suppressed data (-666666666)."""
            try:
                v = int(val)
                if v < 0:
                    return None  # ACS suppression marker -- keep as None, not 0
                return v
            except (ValueError, TypeError):
                return None

        def safe_int_zero(val):
            """Parse ACS integer value. Returns 0 for suppressed data (for sums)."""
            try:
                v = int(val)
                return max(0, v)
            except (ValueError, TypeError):
                return 0

        # Sum senior bins (use safe_int_zero since we're summing)
        senior_bins = [
            "B01001_020E", "B01001_021E", "B01001_022E",
            "B01001_023E", "B01001_024E", "B01001_025E",
            "B01001_044E", "B01001_045E", "B01001_046E",
            "B01001_047E", "B01001_048E", "B01001_049E",
        ]
        pop_65_plus = sum(safe_int_zero(row_dict.get(v, 0)) for v in senior_bins)

        total_pop = safe_int_zero(row_dict.get("B01001_001E"))
        if total_pop == 0:
            continue  # Skip empty block groups

        # For household/vehicle variables, use safe_int (preserves None)
        # so that suppressed data doesn't drag rates to zero
        total_hh = safe_int(row_dict.get("B08201_001E"))
        zero_veh_hh = safe_int(row_dict.get("B08201_002E"))
        total_workers = safe_int(row_dict.get("B08301_001E"))
        transit_commuters = safe_int(row_dict.get("B08301_010E"))

        # Pre-compute rates at block-group level (better than aggregating raw counts
        # when some BGs have suppressed numerator but valid denominator)
        if total_hh is not None and total_hh > 0 and zero_veh_hh is not None:
            zero_veh_rate = zero_veh_hh / total_hh
        else:
            zero_veh_rate = None

        if total_workers is not None and total_workers > 0 and transit_commuters is not None:
            transit_share = transit_commuters / total_workers
        else:
            transit_share = None

        records.append({
            "geoid": geoid,
            "tract": tract,
            "block_group": bg,
            "county_fips": county,
            "name": row_dict.get("NAME", ""),
            "total_pop": total_pop,
            "median_income": safe_int_zero(row_dict.get("B19013_001E")),
            "total_hh": total_hh if total_hh is not None else 0,
            "zero_veh_hh": zero_veh_hh if zero_veh_hh is not None else 0,
            "zero_veh_rate": zero_veh_rate,
            "total_workers": total_workers if total_workers is not None else 0,
            "transit_commuters": transit_commuters if transit_commuters is not None else 0,
            "transit_share": transit_share,
            "pop_under_18": safe_int_zero(row_dict.get("B09001_001E")),
            "pop_65_plus": pop_65_plus,
            "lat": 0.0,
            "lon": 0.0,
            "is_synthetic": False,
        })

    logger.info("Parsed %d non-empty block groups", len(records))

    # Now fetch tract centroids from Census Gazetteer
    # (Block group Gazetteer doesn't exist -- tract is the finest available)
    logger.info("Fetching tract centroids from Census Gazetteer...")
    centroids = _fetch_block_group_centroids()
    matched = 0
    if centroids:
        for rec in records:
            # Match block group to its parent tract using first 11 chars of GEOID
            # Block group GEOID: SSCCCTTTTTB (12 chars: 2 state + 3 county + 6 tract + 1 BG)
            # Tract GEOID:       SSCCCTTTTTT (11 chars: 2 state + 3 county + 6 tract)
            tract_geoid = rec["geoid"][:11]
            if tract_geoid in centroids:
                rec["lat"] = centroids[tract_geoid]["lat"]
                rec["lon"] = centroids[tract_geoid]["lon"]
                matched += 1
        logger.info("Matched %d / %d block groups to tract centroids", matched, len(records))

    # Filter to study area bounding box
    # Los Gatos + Union SD + Mountains: wide enough to catch mountain tracts
    # D9/D10 extend south to ~37.06 lat, D8 extends west to -122.08 lon
    STUDY_LAT_MIN, STUDY_LAT_MAX = 37.04, 37.28
    STUDY_LON_MIN, STUDY_LON_MAX = -122.16, -121.87
    study_records = []
    no_coords = 0
    for rec in records:
        if rec["lat"] == 0.0:
            no_coords += 1
            continue
        if STUDY_LAT_MIN <= rec["lat"] <= STUDY_LAT_MAX and STUDY_LON_MIN <= rec["lon"] <= STUDY_LON_MAX:
            study_records.append(rec)

    logger.info(
        "Filtered to %d block groups in study area (%d had no coordinates)",
        len(study_records), no_coords,
    )

    if not study_records:
        logger.warning("No centroids available -- keeping all %d county BGs", len(records))
        study_records = records

    # Diagnostic: check data quality for key rate variables
    n_zvr = sum(1 for r in study_records if r.get("zero_veh_rate") is not None)
    n_ts = sum(1 for r in study_records if r.get("transit_share") is not None)
    n_hh = sum(1 for r in study_records if r.get("total_hh", 0) > 0)
    logger.info(
        "BG-level data quality: %d/%d have zero_veh_rate, %d/%d have transit_share",
        n_zvr, len(study_records), n_ts, len(study_records),
    )

    # -- Fetch tract-level rates (rarely suppressed) --
    # Block-group-level zero_veh and transit data is frequently suppressed
    # in affluent/small-population areas. Tract-level data is almost never
    # suppressed because tracts have 2,000-8,000 people.
    pct_with_zvr = n_zvr / max(len(study_records), 1)
    if pct_with_zvr < 0.5:
        logger.warning(
            "Only %.0f%% of BGs have zero_veh_rate -- fetching tract-level rates as supplement.",
            pct_with_zvr * 100,
        )
        tract_rates = _fetch_tract_level_rates()
        if tract_rates:
            n_filled = 0
            for rec in study_records:
                tract_id = rec["geoid"][:11]  # First 11 chars = tract GEOID
                if tract_id in tract_rates:
                    tr = tract_rates[tract_id]
                    # Always overwrite with tract-level (more reliable)
                    rec["zero_veh_rate"] = tr.get("zero_veh_rate")
                    rec["transit_share"] = tr.get("transit_share")
                    # Also fill in counts if BG-level was suppressed
                    if rec.get("total_hh", 0) == 0 and tr.get("total_hh", 0) > 0:
                        # Estimate BG's share based on population proportion
                        bg_pop = rec.get("total_pop", 0)
                        tract_pop = tr.get("total_pop", 1)
                        scale = bg_pop / max(tract_pop, 1)
                        rec["total_hh"] = round(tr["total_hh"] * scale)
                        rec["zero_veh_hh"] = round(tr.get("zero_veh_hh", 0) * scale)
                        rec["total_workers"] = round(tr.get("total_workers", 0) * scale)
                        rec["transit_commuters"] = round(tr.get("transit_commuters", 0) * scale)
                    n_filled += 1
            logger.info("Applied tract-level rates to %d / %d BGs", n_filled, len(study_records))
        else:
            logger.warning("Tract-level fetch failed -- rates may be inaccurate.")

    # Final quality check
    n_zvr_final = sum(1 for r in study_records if r.get("zero_veh_rate") is not None)
    n_ts_final = sum(1 for r in study_records if r.get("transit_share") is not None)
    logger.info(
        "Final data quality: %d/%d have zero_veh_rate, %d/%d have transit_share",
        n_zvr_final, len(study_records), n_ts_final, len(study_records),
    )

    # Save
    out_file = output_path / "census_block_groups.csv"
    _write_csv(study_records, out_file)
    logger.info("Saved %d block groups to %s", len(study_records), out_file)
    return True


def _fetch_block_group_centroids() -> dict:
    """Fetch tract centroids from Census Gazetteer and map to block groups.

    The Gazetteer publishes centroids at the tract level (not block group).
    Since block groups nest within tracts, we use the parent tract's centroid
    as a proxy. The tract GEOID is the first 11 characters of a block group
    GEOID (2 state + 3 county + 6 tract).

    Source: 2023_Gaz_tracts_national.zip from
        https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/

    Returns:
        Dict mapping tract GEOID (11 chars) -> {"lat": float, "lon": float}
        Callers should match block groups using geoid[:11].
    """
    if not HAS_REQUESTS:
        return {}

    logger.info("Downloading tract Gazetteer from Census Bureau...")
    try:
        resp = requests.get(GAZETTEER_TRACT_URL, timeout=120)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Gazetteer download failed: %s", e)
        return {}

    if len(resp.content) < 1000:
        logger.error("Gazetteer download too small (%d bytes)", len(resp.content))
        return {}

    # Extract the text file from the zip
    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        txt_files = [f for f in zf.namelist() if f.endswith(".txt")]
        if not txt_files:
            logger.error("No .txt file found in Gazetteer zip")
            return {}
        text = zf.read(txt_files[0]).decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        logger.error("Gazetteer download is not a valid zip file")
        return {}

    # Parse tab-delimited text
    centroids = {}
    lines = text.strip().split("\n")
    header = [h.strip() for h in lines[0].split("\t")]

    # Find column indices
    geoid_idx = lat_idx = lon_idx = None
    for i, h in enumerate(header):
        hu = h.upper()
        if hu == "GEOID":
            geoid_idx = i
        elif hu == "INTPTLAT":
            lat_idx = i
        elif hu in ("INTPTLONG", "INTPTLON"):
            lon_idx = i

    if None in (geoid_idx, lat_idx, lon_idx):
        logger.error("Missing columns in Gazetteer. Found headers: %s", header)
        return {}

    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) <= max(geoid_idx, lat_idx, lon_idx):
            continue
        geoid = parts[geoid_idx].strip()
        try:
            lat = float(parts[lat_idx].strip().replace("+", ""))
            lon = float(parts[lon_idx].strip().replace("+", ""))
            # Filter to Santa Clara County (state 06, county 085)
            if geoid.startswith("06085"):
                centroids[geoid] = {"lat": lat, "lon": lon}
        except (ValueError, TypeError):
            continue

    logger.info(
        "Extracted %d Santa Clara County tract centroids from Gazetteer",
        len(centroids),
    )
    return centroids


def _fetch_tract_level_rates() -> dict:
    """Fetch zero-vehicle and transit commute rates at the tract level.

    Tract-level data is almost never suppressed by the Census Bureau
    because tracts contain 2,000-8,000 people (vs 600-1,500 for block
    groups). This provides reliable rates even for affluent areas where
    block-group-level data is suppressed.

    Uses ACS 5-Year table B08201 (vehicles available) and B08301
    (means of transportation to work).

    Returns:
        Dict mapping tract GEOID (11 chars) -> dict with:
        zero_veh_rate, transit_share, total_hh, zero_veh_hh,
        total_workers, transit_commuters, total_pop.
    """
    if not HAS_REQUESTS:
        return {}

    variables = [
        "B01001_001E",  # Total population
        "B08201_001E",  # Total households
        "B08201_002E",  # 0-vehicle households
        "B08301_001E",  # Total workers 16+
        "B08301_010E",  # Public transit commuters
    ]
    var_str = ",".join(variables)

    url = (
        f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
        f"?get=NAME,{var_str}"
        f"&for=tract:*"
        f"&in=state:{STATE_FIPS}%20county:{COUNTY_FIPS}"
    )

    logger.info("Fetching tract-level rates from Census API...")
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Tract-level API request failed: %s", e)
        return {}

    if not data or len(data) < 2:
        return {}

    headers = data[0]
    rows = data[1:]
    logger.info("Received %d tracts from Census API", len(rows))

    tract_rates = {}
    n_valid_zvr = 0
    for row in rows:
        rd = dict(zip(headers, row))
        state = rd.get("state", "")
        county = rd.get("county", "")
        tract = rd.get("tract", "")
        geoid = f"{state}{county}{tract}"

        def safe(val):
            try:
                v = int(val)
                return v if v >= 0 else None
            except (ValueError, TypeError):
                return None

        total_pop = safe(rd.get("B01001_001E")) or 0
        total_hh = safe(rd.get("B08201_001E"))
        zero_veh_hh = safe(rd.get("B08201_002E"))
        total_workers = safe(rd.get("B08301_001E"))
        transit_commuters = safe(rd.get("B08301_010E"))

        zvr = None
        if total_hh is not None and total_hh > 0 and zero_veh_hh is not None:
            zvr = round(zero_veh_hh / total_hh, 4)
            n_valid_zvr += 1

        ts = None
        if total_workers is not None and total_workers > 0 and transit_commuters is not None:
            ts = round(transit_commuters / total_workers, 4)

        tract_rates[geoid] = {
            "total_pop": total_pop,
            "total_hh": total_hh or 0,
            "zero_veh_hh": zero_veh_hh or 0,
            "zero_veh_rate": zvr,
            "total_workers": total_workers or 0,
            "transit_commuters": transit_commuters or 0,
            "transit_share": ts,
        }

    logger.info(
        "Tract-level: %d tracts, %d/%d have valid zero_veh_rate",
        len(tract_rates), n_valid_zvr, len(tract_rates),
    )
    return tract_rates


def fetch_gtfs_data(output_dir: str = None) -> bool:
    """Download VTA's GTFS feed and extract stops.txt, routes.txt, etc.

    Tries multiple URLs in case any are stale.

    Args:
        output_dir: Directory to extract GTFS files.

    Returns:
        True if successful, False otherwise.

    Standard: GTFS Schedule Reference (https://gtfs.org).
    """
    if not HAS_REQUESTS:
        logger.error("Cannot fetch GTFS: requests library not installed")
        return False

    if output_dir is None:
        output_dir = str(PROJECT_ROOT / "data" / "geospatial" / "gtfs")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    for url in VTA_GTFS_URLS:
        logger.info("Trying GTFS download: %s", url[:80] + "...")
        try:
            resp = requests.get(url, timeout=120, stream=True)
            resp.raise_for_status()

            # Check it's actually a zip
            content_type = resp.headers.get("content-type", "")
            if len(resp.content) < 1000:
                logger.warning("Response too small (%d bytes), skipping", len(resp.content))
                continue

            # Extract
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            gtfs_files = zf.namelist()
            logger.info("GTFS zip contains: %s", ", ".join(gtfs_files[:10]))

            # Extract key files
            for fname in ["stops.txt", "routes.txt", "trips.txt",
                          "stop_times.txt", "calendar.txt", "shapes.txt",
                          "agency.txt"]:
                if fname in gtfs_files:
                    zf.extract(fname, output_path)
                    logger.info("  Extracted %s", fname)

            # Verify stops.txt exists and has data
            stops_path = output_path / "stops.txt"
            if stops_path.exists() and stops_path.stat().st_size > 100:
                logger.info("GTFS download successful! %d files extracted.", len(gtfs_files))
                return True
            else:
                logger.warning("stops.txt missing or empty after extraction")
                continue

        except zipfile.BadZipFile:
            logger.warning("Not a valid zip file from %s", url[:60])
            continue
        except requests.exceptions.RequestException as e:
            logger.warning("Download failed from %s: %s", url[:60], e)
            continue

    logger.error("All GTFS download URLs failed")
    return False


def _write_csv(records: list[dict], path: Path) -> None:
    """Write list of dicts to CSV with consistent column ordering."""
    if not records:
        return
    fieldnames = list(records[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def main():
    """Fetch all available real data sources."""
    print("=" * 60)
    print("FETCHING REAL DATA FROM PUBLIC APIs")
    print("=" * 60)
    print(f"  Project root: {PROJECT_ROOT}")
    print(f"  Census  -> {PROJECT_ROOT / 'data' / 'processed' / 'census_block_groups.csv'}")
    print(f"  GTFS    -> {PROJECT_ROOT / 'data' / 'geospatial' / 'gtfs' / 'stops.txt'}")

    results = {}

    print("\n[1/2] Census Bureau ACS 5-Year (Block Groups)...")
    results["census"] = fetch_census_data()

    print("\n[2/2] VTA GTFS Feed (Routes & Stops)...")
    results["gtfs"] = fetch_gtfs_data()

    print("\n" + "=" * 60)
    print("FETCH RESULTS")
    print("=" * 60)
    for source, ok in results.items():
        status = "SUCCESS" if ok else "FAILED (will use synthetic)"
        print(f"  {source:>10s}: {status}")

    # Verify files exist at the expected paths
    census_path = PROJECT_ROOT / "data" / "processed" / "census_block_groups.csv"
    gtfs_path = PROJECT_ROOT / "data" / "geospatial" / "gtfs" / "stops.txt"
    print(f"\n  Census file exists: {census_path.exists()} ({census_path})")
    print(f"  GTFS file exists:   {gtfs_path.exists()} ({gtfs_path})")

    print("\nNow run: python run_analysis.py")
    print("The pipeline will automatically use real data where available.")


if __name__ == "__main__":
    main()
