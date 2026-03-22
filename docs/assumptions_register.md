# Assumptions Register — Los Gatos Transit CBA

## Purpose
Every assumption in the CBA must be documented, sourced, and flagged for sensitivity analysis. This register is the central record.

## Active Assumptions

| ID | Parameter | Value | Source | Sensitivity Flag | Notes |
|----|-----------|-------|--------|-----------------|-------|
| A01 | Discount rate (low) | 2.0% | OMB Circular A-94 | ✓ Tornado | Standard federal low rate |
| A02 | Discount rate (mid) | 3.5% | OMB infrastructure guidance | ✓ Tornado | Recommended for long-lived infrastructure |
| A03 | Discount rate (high) | 7.0% | OMB Circular A-94 | ✓ Tornado | Standard federal high rate |
| A04 | Value of Time (all purposes) | $20.60/hr | USDOT BCA Guidance 2024, Table 4 | ✓ Tornado, MC | |
| A05 | Value of Statistical Life | $12.8M | USDOT VSL Guidance 2024 | ✓ Tornado | |
| A06 | Social Cost of Carbon | $56/tCO2 | EPA SC-CO2, 2024, 3% rate | ✓ MC | Central estimate |
| A07 | Auto operating cost | $0.68/mile | AAA "Your Driving Costs" 2024 | ✓ Tornado | CA average |
| A08 | Fuel price | $5.10/gal | EIA CA average 2025 | ✓ MC | |
| A09 | Fleet fuel economy | 28.5 mpg | EPA fleet average 2024 | ✓ MC | Light-duty |
| A10 | Auto occupancy | 1.15 | ACS Santa Clara County | — | Low sensitivity |
| A11 | Transit operating cost/rev-hr | $195.50 | NTD FY2023, VTA Bus | ✓ Tornado | |
| A12 | Transit fare recovery ratio | 8% | NTD FY2023, VTA Bus | — | |
| A13 | CO2 per VMT | 347 g/mi | EPA MOVES3.1, SCC defaults | ✓ MC | |
| A14 | Bus CO2 per rev-mi | 2,230 g/mi | NTD 2023, VTA | — | |
| A15 | Walk time per transit trip | 12 min | WHO HEAT default | ✓ MC | |
| A16 | Cross-district benefit split | 50/50 | Boardman et al., Ch. 6 | ✓ Scenario | Origin/destination |
| A17 | Analysis period (operating) | 20 years | FTA guidance | — | |
| A18 | Analysis period (capital) | 30 years | FTA guidance | — | |
| A19 | Monte Carlo iterations | 10,000 | Industry standard | — | |

## Data Proxies (Flagged)

| ID | Data Need | Proxy Used | Justification | Impact |
|----|-----------|-----------|---------------|--------|
| P01 | Census BG demographics | Synthetic from CDP aggregates | No API access in dev environment | Medium — replace with ACS pull |
| P02 | Transit stop locations | Synthetic along known corridors | No GTFS download available | Medium — replace with VTA GTFS |
| P03 | Crash records | Synthetic from county rates | No SWITRS query available | Medium — replace with SWITRS data |
| P04 | Traffic volumes | Synthetic from published AADT | No PeMS access | Low-Medium |
| P05 | Mountain area density (95033) | 89 ppl/sq mi | Wikipedia / LiveInLosGatos blog | Low — stable metric |
| P06 | Route 76 ridership (historical) | ~40 boardings/day | VTA Watch blog, community sources | High — limited historical data |
