# District Methodology — Los Gatos Transit CBA

## Overview

The study area is divided into two parallel analysis zones, each with road-bounded districts:

- **LGHS Zone (D1–D10):** 10 districts covering the Los Gatos High School service area (ZIP codes 95030, 95032, 95033). Includes the incorporated town (~11.6 sq mi) and ~100 sq mi of unincorporated mountain area.
- **Union SD Zone (U1–U6):** 6 districts covering the Union School District (portions of ZIP codes 95032, 95124, 95118, 95120). ~7 sq mi across eastern Los Gatos, Cambrian, and north Almaden in San Jose.

## Boundary Principles

Districts are bounded by **major roadways that function as pedestrian barriers**. A freeway or high-volume arterial creates a walkshed discontinuity that defines real transit access zones. No district straddles a barrier road.

### Barrier Roads

| Road | Type | Barrier Effect |
|------|------|---------------|
| SR-17 | Freeway | Uncrossable on foot. Divides LG east/west. |
| SR-85 (West Valley Fwy) | Freeway | Uncrossable. Northern boundary of Union SD. Divides LG north/south-east. Extended through full Union SD area. |
| Los Gatos Blvd | Major arterial (35 mph, ~23k AADT) | Major noise/pedestrian barrier. Divides LG core from east. |
| Lark Avenue | Arterial | Divides D4/D5 between freeways. |
| Blossom Hill Road | Major arterial | Divides urban core from south hills. Continues east through Union SD. |
| Camden Avenue | Major arterial | Primary N-S divider within Union SD (Union Middle vs Dartmouth Middle halves). |
| LG-Almaden Road | Arterial | Diagonal divider within western Union SD. |
| Branham Lane | Arterial | E-W divider in southern Union SD. |
| Union Avenue | Arterial | Secondary divider in Union SD. |

### District-Zone Overlap

District D6 (East LG / Belwood) and District U1 (Alta Vista / LG West) overlap geographically. This is intentional — they represent the same physical area analyzed from two different service-area perspectives. The LGHS analysis counts this area's population once under D6; the Union SD analysis counts it once under U1. They are never double-counted in a single zone's totals.

## Census Block Group Alignment

Phase A1 uses approximate polygon boundaries confirmed through interactive map review. In subsequent work, boundaries will be refined by:

1. Downloading census block group shapefiles for Santa Clara County.
2. Computing the geometric overlap between each block group and each district.
3. Assigning each block group to the district containing the majority of its area (majority-area rule).
4. Documenting edge cases in `data/geospatial/districts/district_block_groups.csv`.

## Area Types

| District | Area Type | Typical Density |
|----------|-----------|----------------|
| D1–D5 | Urban/Suburban | 1,500–6,000 ppl/sq mi |
| D6 | Suburban | 3,000–4,000 ppl/sq mi |
| D7 | Hillside residential | 1,000–2,000 ppl/sq mi |
| D8 | Foothills | 200–500 ppl/sq mi |
| D9 | Mountain corridor | 30–80 ppl/sq mi |
| D10 | Remote mountain | <100 ppl/sq mi |
| U1–U6 | Suburban (SJ) | 1,500–4,000 ppl/sq mi |

## Configuration

All district definitions live in `config.yaml` under `districts_lghs` and `districts_union`. Districts can be added, removed, renamed, or re-bounded without changing code.
