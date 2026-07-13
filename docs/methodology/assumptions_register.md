# Assumptions Register — Los Gatos Transit CBA

## Purpose
Every assumption in the CBA must be documented, sourced, and flagged for sensitivity analysis. This register is the central record.

Key jargon used throughout: [BCR](../outputs/cba_dashboard.html#gl-bcr) (Benefit-Cost Ratio), [NPV](../outputs/cba_dashboard.html#gl-npv) (Net Present Value), [VOT](../outputs/cba_dashboard.html#gl-vot) (Value of Time), [VSL](../outputs/cba_dashboard.html#gl-vsl) (Value of Statistical Life), [VMT](../outputs/cba_dashboard.html#gl-vmt) (Vehicle Miles Traveled), [SCC](../outputs/cba_dashboard.html#gl-scc) (Social Cost of Carbon), [GTFS](../outputs/cba_dashboard.html#gl-gtfs) (General Transit Feed Specification), [NTD](../outputs/cba_dashboard.html#gl-ntd) (National Transit Database), [TCRP](../outputs/cba_dashboard.html#gl-tcrp) (Transit Cooperative Research Program).

## Active Assumptions

| ID | Parameter | Value | Source | Sensitivity Flag | Notes |
|----|-----------|-------|--------|-----------------|-------|
| A01 | [Discount rate](../outputs/cba_dashboard.html#gl-discount-rate) (low) | 2.0% | [OMB Circular A-94](https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf) | ✓ Tornado | Standard federal low rate |
| A02 | [Discount rate](../outputs/cba_dashboard.html#gl-discount-rate) (mid) | 3.5% | [OMB Circular A-94](https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf) | ✓ Tornado | Recommended for long-lived infrastructure |
| A03 | [Discount rate](../outputs/cba_dashboard.html#gl-discount-rate) (high) | 7.0% | [OMB Circular A-94](https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf) | ✓ Tornado | Standard federal high rate |
| A04 | [Value of Time](../outputs/cba_dashboard.html#gl-vot) (all purposes) | $20.60/hr | [USDOT BCA Guidance 2024, Table 4](https://www.transportation.gov/sites/dot.gov/files/2024-11/Benefit%20Cost%20Analysis%20Guidance%202025%20Update%20(Final).pdf) | ✓ Tornado, MC | |
| A05 | [Value of Statistical Life](../outputs/cba_dashboard.html#gl-vsl) | $12.8M | [USDOT VSL Guidance](https://www.transportation.gov/resources/value-of-a-statistical-life-guidance) | ✓ Tornado | |
| A06 | [Social Cost of Carbon](../outputs/cba_dashboard.html#gl-scc) | $120/tCO2 (3%), $190/tCO2 (2.5%) | [EPA SC-GHG Report 2023](https://www.epa.gov/system/files/documents/2023-12/epa_scghg_2023_report_final.pdf) | ✓ MC, Tornado | Prior IWG value ($51-56/ton) superseded for regulatory use |
| A07 | Auto operating cost | $0.68/mile | [AAA "Your Driving Costs" 2024](https://newsroom.aaa.com/wp-content/uploads/2024/08/YDC-Brochure-FINAL-9.2024.pdf) | ✓ Tornado | CA average |
| A08 | Fuel price | $5.10/gal | [EIA CA Retail Gasoline Prices](https://www.eia.gov/dnav/pet/pet_pri_gnd_dcus_sca_w.htm) | ✓ MC | |
| A09 | Fleet fuel economy | 28.5 mpg | [EPA Automotive Trends Report 2025](https://www.epa.gov/system/files/documents/2026-02/420r26001.pdf) | ✓ MC | Light-duty |
| A10 | Auto occupancy | 1.15 | [ACS Santa Clara County](https://data.census.gov/table/ACSST5Y2023.S0802?g=050XX00US06085) | — | Low sensitivity |
| A11 | Transit operating cost/rev-hr | $195.50 | [NTD FY2023 Annual Data](https://www.transit.dot.gov/ntd) | ✓ Tornado | |
| A12 | Transit farebox recovery ratio | 8% | [NTD FY2023 Annual Data](https://www.transit.dot.gov/ntd) | — | Farebox recovery = fare revenue ÷ total operating cost |
| A13 | CO2 per [VMT](../outputs/cba_dashboard.html#gl-vmt) | 347 g/mi | [EPA MOVES3.1](https://www.epa.gov/moves/latest-version-motor-vehicle-emission-simulator-moves) | ✓ MC | |
| A14 | Bus CO2 per rev-mi | 2,230 g/mi | [NTD FY2023 Annual Data](https://www.transit.dot.gov/ntd) | — | |
| A15 | Walk time per transit trip | 12 min | [WHO HEAT](https://www.who.int/tools/heat-for-walking-and-cycling) default | ✓ MC | |
| A16 | Cross-district benefit split | 50/50 | [Boardman et al., *Cost-Benefit Analysis: Concepts and Practice*, 5th ed.](https://www.cambridge.org/us/universitypress/subjects/economics/public-economics-and-public-policy/cost-benefit-analysis-concepts-and-practice-5th-edition), Ch. 6 | ✓ Scenario | Origin/destination |
| A17 | Analysis period (operating) | 20 years | [FTA CIG Policy Guidance](https://www.transit.dot.gov/CIG) | — | |
| A18 | Analysis period (capital) | 30 years | [FTA CIG Policy Guidance](https://www.transit.dot.gov/CIG) | — | |
| A19 | Monte Carlo iterations | 10,000 | Industry standard | — | |
| A20 | [Induced demand](../outputs/cba_dashboard.html#gl-induced-demand) share | 20% of boardings | [TCRP Report 95, Ch. 9 – Transit Scheduling and Frequency](https://www.trb.org/publications/tcrp/tcrp_rpt_95c9.pdf) (15-25% range) | ✓ MC | Conservative end of range; transit-dependent + zero-car HH riders |
| A21 | FTA CE Index [TSUB](../outputs/cba_dashboard.html#gl-tsub) method | Diverted time savings + transit-dependent mobility hours | [FTA CIG Policy Guidance](https://www.transit.dot.gov/CIG) | — | Required for New Starts/Small Starts rating |

## Data Proxies (Flagged)

| ID | Data Need | Proxy Used | Justification | Impact |
|----|-----------|-----------|---------------|--------|
| P01 | [ACS](../outputs/cba_dashboard.html#gl-acs) Census BG demographics | Synthetic from CDP aggregates | No API access in dev environment | Medium — replace with [ACS 5-year API](https://www.census.gov/data/developers/data-sets/acs-5year.html) pull |
| P02 | Transit stop locations | Synthetic along known corridors | No [GTFS](../outputs/cba_dashboard.html#gl-gtfs) download available | Medium — replace with [VTA GTFS](https://www.vta.org/go/developers) feed |
| P03 | Crash records | Synthetic from county rates | No [SWITRS](../outputs/cba_dashboard.html#gl-switrs) query available | Medium — replace with [SWITRS data](https://tims.berkeley.edu/help/SWITRS.php) |
| P04 | Traffic volumes | Synthetic from published AADT | No PeMS access | Low-Medium |
| P05 | Mountain area density (95033) | 89 ppl/sq mi | Wikipedia / [LiveInLosGatos blog](https://liveinlosgatosblog.com/) | Low — stable metric |
| P06 | Route 76 ridership (historical) | ~40 boardings/day | [VTA Watch blog](http://vtawatch.blogspot.com/2010/06/last-day-of-line-76.html), community sources | High — limited historical data |
