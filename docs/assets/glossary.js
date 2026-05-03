/**
 * glossary.js
 *
 * Shared glossary data + jargon-tooltip + click-to-glossary system.
 * Works on both analysis.html and route-redesign.html.
 *
 * Usage:
 *   1. Include this script AFTER the page body (or defer it).
 *   2. Add <dl class="glossary-grid" id="glossary-dl"></dl> where the
 *      glossary should render (if not already server-rendered).
 *   3. Terms in body text that match GLOSS keys are automatically
 *      wrapped in <span class="jargon"> with hover tooltip + click-
 *      to-definition behaviour.
 */

const GLOSS = {
  "ACS": {
    full: "American Community Survey",
    body: "The U.S. Census Bureau's annual survey providing estimates of demographics, income, commute mode, and vehicle availability. The primary source of equity and demand data in this analysis.",
    link: "https://www.census.gov/programs-surveys/acs",
    linkText: "census.gov/acs"
  },
  "BCR": {
    full: "Benefit-Cost Ratio",
    body: "Present value of total benefits divided by present value of total costs. A BCR above 1.0 means the project's economic value exceeds its cost and is worth funding. See also: NPV, discount rate.",
    link: "https://www.transportation.gov/sites/dot.gov/files/2024-11/Benefit%20Cost%20Analysis%20Guidance%202025%20Update%20(Final).pdf",
    linkText: "USDOT BCA Guidance 2024"
  },
  "BenMAP-CE": {
    full: "Environmental Benefits Mapping and Analysis Program",
    body: "EPA's tool that translates changes in air pollutant concentrations to health outcomes and economic damages. Used for criteria pollutant benefits in Category 4.",
    link: "https://www.epa.gov/benmap",
    linkText: "EPA BenMAP page"
  },
  "CEI": {
    full: "Cost Effectiveness Index",
    body: "Annual net project cost divided by annualized user benefits in TSUB-hours. The FTA CIG threshold is below $2 per TSUB-hr for Medium-High rating and below $4 for Medium. Required for federal Capital Investment Grant applications.",
    link: "https://www.transit.dot.gov/CIG",
    linkText: "FTA CIG Policy Guidance"
  },
  "CIG": {
    full: "Capital Investment Grant",
    body: "The FTA's primary discretionary funding program for major transit projects, including New Starts, Small Starts, and Core Capacity. Applications require a Cost Effectiveness Index below FTA thresholds.",
    link: "https://www.transit.dot.gov/CIG",
    linkText: "transit.dot.gov/CIG"
  },
  "Clarke-Wright": {
    full: "Clarke-Wright Savings Algorithm",
    body: "A vehicle routing algorithm that builds efficient routes by merging individual trips wherever doing so saves distance. Used in Phase B to optimize the Route 27 stop sequence.",
    link: "https://www.trb.org/publications/tcrp/tcrp_rpt_19.pdf",
    linkText: "TCRP Report 19"
  },
  "consumer surplus": {
    full: "Consumer Surplus",
    body: "The economic benefit a consumer receives beyond what they pay. In transit cost-benefit analysis, induced riders gain a surplus equal to roughly 50% of the auto trip cost they would have faced, because their willingness to pay is lower than that cost.",
    link: "https://www.cambridge.org/us/universitypress/subjects/economics/public-economics-and-public-policy/cost-benefit-analysis-concepts-and-practice-5th-edition",
    linkText: "Boardman et al., Ch. 3"
  },
  "discount rate": {
    full: "Discount Rate",
    body: "The annual rate used to convert future dollars into present-value dollars. Higher rates reduce the weight given to distant future benefits. OMB Circular A-94 prescribes 2%, 3.5%, and 7% for infrastructure cost-benefit sensitivity testing.",
    link: "https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf",
    linkText: "OMB Circular A-94"
  },
  "diversion rate": {
    full: "Diversion Rate",
    body: "The share of transit riders who previously made the same trip by private automobile. Auto-diversion riders generate vehicle operating cost savings, time savings, crash reduction, and emission reduction benefits. The complementary share is induced demand."
  },
  "EPA": {
    full: "U.S. Environmental Protection Agency",
    body: "Federal agency responsible for environmental regulation. The EPA's 2022 Social Cost of Carbon update raised the value from $56 to $120 per metric ton of CO2, substantially increasing the emission-reduction benefit category in this analysis.",
    link: "https://www.epa.gov/system/files/documents/2023-12/epa_scghg_2023_report_final.pdf",
    linkText: "EPA SC-GHG Report 2023"
  },
  "FHWA": {
    full: "Federal Highway Administration",
    body: "The U.S. DOT agency responsible for highway infrastructure. Publishes the KABCO crash cost tables used in the safety benefit calculation in Category 3.",
    link: "https://safety.fhwa.dot.gov/hsip/docs/fhwasa17071.pdf",
    linkText: "FHWA Crash Cost Tables"
  },
  "FTA": {
    full: "Federal Transit Administration",
    body: "The U.S. DOT agency that funds, regulates, and provides technical guidance for public transit. Administers the Capital Investment Grant program and publishes the cost-benefit analysis guidelines used throughout this analysis.",
    link: "https://www.transit.dot.gov",
    linkText: "transit.dot.gov"
  },
  "FTA Circular 9040.1G": {
    full: "FTA Circular 9040.1G",
    body: "FTA's Formula Grants for Rural Areas program guidance circular. Sets minimum stop spacing, accessibility, and service standards for federally funded rural and suburban transit. Used as the baseline stop-spacing criterion in Phase B route optimization. Superseded by Circular 9040.1H (November 2024).",
    link: "https://www.transit.dot.gov/sites/fta.dot.gov/files/2024-09/C9040.1H-Circular-11-01-2024.pdf",
    linkText: "Circular 9040.1H (Nov 2024)"
  },
  "GTFS": {
    full: "General Transit Feed Specification",
    body: "The open standard format for transit schedules, including stops.txt, trips.txt, and stop_times.txt. This analysis ingests VTA's GTFS feed for stop locations, routes, and headways.",
    link: "https://gtfs.org/schedule/reference/",
    linkText: "gtfs.org"
  },
  "headway": {
    full: "Headway",
    body: "The time interval between consecutive transit vehicle departures on the same route. Shorter headways mean more frequent service. Headways in this analysis are computed using the Mohring (1972) wait-time formula calibrated to demand."
  },
  "induced demand": {
    full: "Induced Demand",
    body: "Trips that only happen because transit exists, riders who would not otherwise have made the trip by any mode. Estimated at 20% of boardings per TCRP Report 95. Valued via consumer surplus (50% of equivalent auto trip value) rather than auto diversion savings.",
    link: "https://www.trb.org/publications/tcrp/tcrp_rpt_95c9.pdf",
    linkText: "TCRP Report 95"
  },
  "KABCO": {
    full: "KABCO Crash Severity Scale",
    body: "K = Fatal, A = Severe Injury, B = Moderate Injury, C = Minor Injury, O = Property Damage Only. Used by FHWA and SWITRS to weight crash costs in safety benefit calculations.",
    link: "https://safety.fhwa.dot.gov/hsip/docs/fhwasa17071.pdf",
    linkText: "FHWA Crash Costs for Highway Safety Analysis"
  },
  "LGHS": {
    full: "Los Gatos High School",
    body: "Los Gatos High School, a major trip generator in the study area and the anchor institution for school-trip demand modeling. Route 76 was maintained in part for LGHS student access before discontinuation in June 2010."
  },
  "Mohring": {
    full: "Mohring (1972) Wait-Time Formula",
    body: "An economic formula showing that the optimal transit frequency increases with ridership, because the cost of waiting is shared among more passengers. Used in Phase B to derive peak and off-peak headways from the demand model."
  },
  "MOVES3.1": {
    full: "Motor Vehicle Emission Simulator, version 3.1",
    body: "EPA's emissions modeling tool. Used to estimate per-mile emission factors for CO2, NOx, and PM2.5 from avoided automobile trips in the emission-reduction benefit category.",
    link: "https://www.epa.gov/moves/latest-version-motor-vehicle-emission-simulator-moves",
    linkText: "EPA MOVES page"
  },
  "NTD": {
    full: "National Transit Database",
    body: "The FTA's annual data collection from U.S. transit agencies covering ridership, costs, and service statistics. Used here for peer benchmarking of VTA operating costs against California peers.",
    link: "https://www.transit.dot.gov/ntd",
    linkText: "transit.dot.gov/ntd"
  },
  "NPV": {
    full: "Net Present Value",
    body: "The sum of all future cash flows (benefits minus costs) discounted to today's dollars. A positive NPV means the project produces a net economic gain over the analysis period. See also: BCR, discount rate, PV.",
  },
  "OMB": {
    full: "Office of Management and Budget",
    body: "White House office that sets federal guidelines for budget analysis. OMB Circular A-94 prescribes the discount rates (2%, 3.5%, 7%) used in this dashboard for infrastructure cost-benefit analysis.",
    link: "https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf",
    linkText: "OMB Circular A-94"
  },
  "OMB Circular A-94": {
    full: "OMB Circular A-94",
    body: "The federal guidelines for benefit-cost analysis of government programs. Prescribes the discount rates (2%, 3.5%, 7%) used in this dashboard. Required methodology for federal infrastructure investment analysis.",
    link: "https://whitehouse.gov/wp-content/uploads/2023/11/CircularA-94.pdf",
    linkText: "OMB Circular A-94 (PDF)"
  },
  "option value": {
    full: "Option Value",
    body: "The economic value that non-riders place on the mere availability of transit, as insurance against car breakdown, gas price spikes, or loss of driving ability. Estimated at $20 to $40 per capita per year from stated-preference surveys.",
    link: "https://www.trb.org/publications/tcrp/tcrp78/guidebook/tcrp78.pdf",
    linkText: "TCRP Report 78"
  },
  "PV": {
    full: "Present Value",
    body: "The current worth of a future sum of money, discounted at a chosen rate to reflect the time value of money. See also: NPV, discount rate, BCR."
  },
  "SCC": {
    full: "Social Cost of Carbon",
    body: "The estimated economic damage caused by emitting one metric ton of CO2. The EPA SC-GHG Report (Dec 2023) sets SCC at $120 per ton at a 3% discount rate, up from the prior $56 per ton IWG value used in many older transit analyses.",
    link: "https://www.epa.gov/system/files/documents/2023-12/epa_scghg_2023_report_final.pdf",
    linkText: "EPA SC-GHG Report (Dec 2023)"
  },
  "SWITRS": {
    full: "Statewide Integrated Traffic Records System",
    body: "California's crash database maintained by the California Highway Patrol. Used to derive Santa Clara County crash rates (approximately 120 crashes per 100 million VMT) for Category 3 safety benefits.",
    link: "https://tims.berkeley.edu/help/SWITRS.php",
    linkText: "UC Berkeley TIMS"
  },
  "TCRP": {
    full: "Transit Cooperative Research Program",
    body: "A federally funded research program producing peer-reviewed transit planning guidance. TCRP Report 95 underpins the induced-demand category; Report 78 underpins option value estimates; Report 19 provides route optimization methods.",
    link: "https://www.trb.org/TCRP/TCRP.aspx",
    linkText: "trb.org/TCRP"
  },
  "TSUB": {
    full: "Transportation System User Benefit",
    body: "The FTA metric for CIG cost effectiveness. Measures time saved by diverted auto users plus transit travel time for transit-dependent users, in hours per year. Used to compute the Cost Effectiveness Index.",
    link: "https://www.transit.dot.gov/CIG",
    linkText: "FTA CIG Policy Guidance"
  },
  "USDOT": {
    full: "U.S. Department of Transportation",
    body: "Sets federal cost-benefit analysis guidance values including Value of Time, Value of a Statistical Life, and discount rate recommendations used throughout this analysis.",
    link: "https://www.transportation.gov/sites/dot.gov/files/2024-11/Benefit%20Cost%20Analysis%20Guidance%202025%20Update%20(Final).pdf",
    linkText: "USDOT BCA Guidance 2024"
  },
  "VMT": {
    full: "Vehicle Miles Traveled",
    body: "The total miles driven by motor vehicles in a given area and period. Reducing VMT cuts emissions, crashes, and congestion. Emission factors come from MOVES3.1; crash costs are weighted using the KABCO scale."
  },
  "VOT": {
    full: "Value of Time",
    body: "The dollar value assigned to one hour of travel time, used to monetize travel time savings. USDOT BCA Guidance 2024 sets $17.80 per hour for personal trips and $31.90 per hour for employer business trips.",
    link: "https://www.transportation.gov/sites/dot.gov/files/2024-11/Benefit%20Cost%20Analysis%20Guidance%202025%20Update%20(Final).pdf",
    linkText: "USDOT BCA Guidance 2024"
  },
  "VSL": {
    full: "Value of a Statistical Life",
    body: "The dollar amount used in regulatory analysis to represent the economic cost of a fatality. USDOT 2024 sets VSL at $12.8 million per fatality, used in crash-reduction benefit calculations.",
    link: "https://www.transportation.gov/resources/value-of-a-statistical-life-guidance",
    linkText: "USDOT VSL Guidance"
  },
  "VTA": {
    full: "Santa Clara Valley Transportation Authority",
    body: "The regional transit agency that operates bus and light-rail service in Santa Clara County, including Route 27 (Winchester to Los Gatos), the discontinued Route 76 (Los Gatos to Summit Road), and the Highway 17 Express. The primary operator studied in this cost-benefit analysis.",
    link: "https://www.vta.org",
    linkText: "vta.org"
  },
  "walk-shed": {
    full: "Walk-Shed",
    body: "The area reachable on foot within a given time (typically 5 to 10 minutes, or 0.25 to 0.5 miles) from a transit stop. A larger walk-shed means the stop serves more potential riders. Buffer standards follow FTA Circular 9040.1H.",
    link: "https://www.transit.dot.gov/sites/fta.dot.gov/files/2024-09/C9040.1H-Circular-11-01-2024.pdf",
    linkText: "FTA Circular 9040.1H"
  },
  "WHO HEAT": {
    full: "WHO Health Economic Assessment Tool",
    body: "World Health Organization tool that quantifies the mortality-reduction benefit of walking and cycling. Version 5.2 default: 12 walk-minutes per transit trip, valued at $0.16 per minute. Used for Health Benefits (Active Transport) in Category 5.",
    link: "https://www.who.int/tools/heat-for-walking-and-cycling",
    linkText: "WHO HEAT tool"
  }
};

// ── Render glossary entries into a <dl> ──────────────────────────────────────
function renderGlossary(dlEl) {
  if (!dlEl) return;
  const terms = Object.keys(GLOSS).sort();
  let html = '';
  terms.forEach(function (term) {
    const g = GLOSS[term];
    const slug = slugifyTerm(term);
    let bodyHtml = escHtml(g.body);
    if (g.link) {
      bodyHtml += ' <a href="' + g.link + '" target="_blank" rel="noopener">' + escHtml(g.linkText || g.link) + '</a>.';
    }
    html += '<dt id="gl-' + slug + '"><a href="#gl-' + slug + '" style="color:inherit;text-decoration:none">' + escHtml(term) + '</a>';
    if (g.full && g.full !== term) {
      html += ' <span style="font-weight:400;color:var(--tm);font-size:11px">— ' + escHtml(g.full) + '</span>';
    }
    html += '</dt>';
    html += '<dd>' + bodyHtml + '</dd>';
  });
  dlEl.innerHTML = html;
}

// ── Tooltip system ────────────────────────────────────────────────────────────
(function () {
  // Create tooltip element once
  const tip = document.createElement('div');
  tip.className = 'jargon-tip';
  tip.id = 'jargonTip';
  tip.innerHTML = '<div class="jt-term" id="jargonTipTerm"></div><div class="jt-body" id="jargonTipBody"></div><div class="jt-footer">Click to jump to glossary definition</div>';
  document.body.appendChild(tip);

  const tipTerm = document.getElementById('jargonTipTerm');
  const tipBody = document.getElementById('jargonTipBody');

  let hideTimer = null;
  let activeTerm = null;

  function buildBodyHtml(g) {
    let html = escHtml(g.body || '');
    if (g.full && g.full !== '') {
      // no-op: full name shown in dt, keep body clean
    }
    return html;
  }

  function showTip(el) {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    const key = el.dataset.term;
    if (!GLOSS[key]) return;
    activeTerm = key;
    tipTerm.textContent = key + (GLOSS[key].full && GLOSS[key].full !== key ? ' — ' + GLOSS[key].full : '');
    tipBody.innerHTML = buildBodyHtml(GLOSS[key]);
    tip.style.display = 'block';
    tip.classList.add('is-active');
    positionTip(el);
  }

  function positionTip(el) {
    const r = el.getBoundingClientRect();
    const tw = tip.offsetWidth || 320;
    const th = tip.offsetHeight || 100;
    let left = r.left;
    let top = r.bottom + 10;
    if (left + tw > window.innerWidth - 8) left = window.innerWidth - tw - 8;
    if (top + th > window.innerHeight - 8) top = Math.max(4, r.top - th - 10);
    tip.style.left = Math.max(4, left) + 'px';
    tip.style.top = top + 'px';
  }

  function scheduleHide() {
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(function () {
      tip.style.display = 'none';
      tip.classList.remove('is-active');
      activeTerm = null;
    }, 200);
  }

  document.addEventListener('mouseover', function (e) {
    const j = e.target.closest && e.target.closest('.jargon');
    if (j) { showTip(j); return; }
    if (e.target.closest && e.target.closest('#jargonTip')) {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    }
  });

  document.addEventListener('mouseout', function (e) {
    const j = e.target.closest && e.target.closest('.jargon');
    const t = e.target.closest && e.target.closest('#jargonTip');
    if (!j && !t) return;
    const to = e.relatedTarget;
    if (to && to.closest && (to.closest('.jargon') || to.closest('#jargonTip'))) return;
    scheduleHide();
  });

  // Click: scroll to glossary entry and flash it
  document.addEventListener('click', function (e) {
    const a = e.target.closest && e.target.closest('.jargon');
    if (!a) return;
    e.preventDefault();
    const term = a.dataset.term;
    const slug = slugifyTerm(term);
    const dt = document.getElementById('gl-' + slug);
    if (!dt) return;
    // Manual scroll to avoid scrollIntoView
    const navH = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--nav-h')) || 56;
    const top = dt.getBoundingClientRect().top + window.pageYOffset - navH - 24;
    window.scrollTo({ top: top, behavior: 'smooth' });
    flashEntry(dt);
    if (history.replaceState) history.replaceState(null, '', '#gl-' + slug);
    scheduleHide();
  });

  function flashEntry(dt) {
    const dd = dt.nextElementSibling;
    [dt, dd].forEach(function (n) {
      if (!n) return;
      n.classList.remove('gloss-flash');
      void n.offsetWidth;
      n.classList.add('gloss-flash');
    });
  }

  // On page load: flash entry if URL has #gl- hash
  window.addEventListener('load', function () {
    if (location.hash && location.hash.indexOf('#gl-') === 0) {
      const dt = document.getElementById(location.hash.slice(1));
      if (dt) flashEntry(dt);
    }
  });

  // ── Auto-wrap text nodes ───────────────────────────────────────────────────
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'CANVAS', 'SVG', 'BUTTON', 'INPUT',
    'TEXTAREA', 'SELECT', 'OPTION', 'CODE', 'PRE']);
  const SKIP_CLASS_SUBSTRINGS = ['jargon', 'jargon-tip', 'leaflet', 'glossary-grid',
    'brand', 'site-header', 'site-footer', 'eyebrow'];

  // Sort longest-first so "OMB Circular A-94" matches before "OMB"
  const termKeys = Object.keys(GLOSS).sort(function (a, b) { return b.length - a.length; });

  const termMeta = termKeys.map(function (term) {
    const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    // Acronyms (all caps) are case-sensitive; mixed-case terms are case-insensitive
    const flags = /^[A-Z0-9 .\-\/&]+$/.test(term) ? 'g' : 'gi';
    return { term: term, re: new RegExp('\\b' + escaped + '\\b', flags) };
  });

  function shouldSkip(el) {
    if (!el || !el.tagName) return true;
    if (SKIP_TAGS.has(el.tagName)) return true;
    if (el.tagName === 'A') return true;
    const cls = (typeof el.className === 'string' ? el.className : (el.className && el.className.baseVal) || '');
    for (let i = 0; i < SKIP_CLASS_SUBSTRINGS.length; i++) {
      if (cls.indexOf(SKIP_CLASS_SUBSTRINGS[i]) >= 0) return true;
    }
    if (el.id === 'jargonTip') return true;
    return false;
  }

  function wrapTextNode(node) {
    const text = node.textContent;
    if (!text || text.length < 2 || !/[A-Za-z]/.test(text)) return false;
    const matches = [];
    for (let i = 0; i < termMeta.length; i++) {
      const tm = termMeta[i];
      tm.re.lastIndex = 0;
      let m;
      while ((m = tm.re.exec(text)) !== null) {
        const start = m.index, end = start + m[0].length;
        let overlap = false;
        for (let k = 0; k < matches.length; k++) {
          if (!(end <= matches[k].start || start >= matches[k].end)) { overlap = true; break; }
        }
        if (!overlap) matches.push({ start, end, term: tm.term, match: m[0] });
      }
    }
    if (!matches.length) return false;
    matches.sort(function (a, b) { return a.start - b.start; });
    const parent = node.parentNode;
    if (!parent) return false;
    const frag = document.createDocumentFragment();
    let cursor = 0;
    for (let i = 0; i < matches.length; i++) {
      const mm = matches[i];
      if (mm.start > cursor) frag.appendChild(document.createTextNode(text.slice(cursor, mm.start)));
      const span = document.createElement('span');
      span.className = 'jargon';
      span.dataset.term = mm.term;
      span.tabIndex = 0;
      span.setAttribute('role', 'button');
      span.setAttribute('aria-label', mm.term + ': ' + ((GLOSS[mm.term] && GLOSS[mm.term].body) || ''));
      span.textContent = mm.match;
      frag.appendChild(span);
      cursor = mm.end;
    }
    if (cursor < text.length) frag.appendChild(document.createTextNode(text.slice(cursor)));
    parent.replaceChild(frag, node);
    return true;
  }

  function walkNode(root) {
    if (!root || shouldSkip(root)) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        let p = n.parentNode;
        while (p && p !== root) {
          if (shouldSkip(p)) return NodeFilter.FILTER_REJECT;
          p = p.parentNode;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const targets = [];
    let n;
    while ((n = walker.nextNode())) targets.push(n);
    targets.forEach(wrapTextNode);
  }

  function run() {
    walkNode(document.body);
    // Also render glossary dl if present
    const dl = document.getElementById('glossary-dl');
    if (dl && dl.children.length === 0) renderGlossary(dl);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }

  // Re-wrap dynamically injected content (charts, NPV breakdowns, etc.)
  let pending = null;
  const observer = new MutationObserver(function (records) {
    const roots = [];
    for (let i = 0; i < records.length; i++) {
      const r = records[i];
      for (let j = 0; j < r.addedNodes.length; j++) {
        const node = r.addedNodes[j];
        if (node.nodeType === 1 && !shouldSkip(node) && !node.dataset.glossWrapped) {
          roots.push(node);
        }
      }
    }
    if (!roots.length) return;
    if (pending) cancelAnimationFrame(pending);
    pending = requestAnimationFrame(function () {
      pending = null;
      roots.forEach(walkNode);
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
})();

// ── Helpers ───────────────────────────────────────────────────────────────────
function slugifyTerm(s) {
  return s.toLowerCase().replace(/\./g, '').replace(/\s+/g, '-').replace(/\//g, '-');
}

function escHtml(str) {
  return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
