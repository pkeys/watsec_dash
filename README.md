# HEADWATERS — A Global Water–Conflict Susceptibility Watch

**🌍 Live demo: https://headwaters.onrender.com**
*(Free-tier hosting — the first visit after a quiet spell may take ~30–60 s to wake.)*

A dynamic, global, web-based **hotspot alert system** that operationalises the
**Pathways to Instability Framework** of Beames et al. (2025) into a live watch-list.

It blends **quantitative** structural indicators (World Bank Open Data) with a
**qualitative**, real-time feed of **water disturbances** (GDACS droughts, floods,
cyclones), and it traces every figure back to its source.

> **Scope: susceptibility, not a forecast.** The framework's authors are explicit that
> it is *not predictive*, and neither is this tool. It reports **susceptibility** — the
> standing conditions that make a place more liable to instability *if* a water
> disturbance occurs — alongside the disturbances currently active. It does not forecast
> conflict.

## Run it

No dependencies beyond Python 3 (standard library only) — **no `pip install`**:

```bash
# 0. get the code
git clone https://github.com/pkeys/watsec_dash.git
cd watsec_dash

# 1. (optional) warm every data dependency into ./cache up front
python3 scripts/fetch_data.py

# 2. start the dashboard
python3 server.py
# then open http://localhost:8765
```

The server **self-warms** its data streams on first start regardless, so step 1 is a
convenience: it pulls every upstream stream ahead of time (so the first page load is
instant) and doubles as an explicit, inspectable **manifest of every data origin** —
see [`scripts/fetch_data.py`](scripts/fetch_data.py). Raw responses are cached under
`./cache/` so the provenance is inspectable and reruns are fast. Set `PORT=…` to change
the port.

> **Provenance & data refresh.** The `cache/` directory (up to ~270 MB, including a
> 239 MB UCDP CSV) is **not** committed to the repo — it is regenerated on demand from the
> open upstream sources. Delete a cached file (or the whole `cache/`) and the next run
> re-fetches it; structural data is held for 24 h, hazards for 30 min. Re-running
> `scripts/fetch_data.py` is safe and idempotent.

## Repository layout

```
watsec_dash/
├── server.py              # stdlib HTTP server: fetch + cache + score + serve
├── news.py                # stdlib RSS harvester (real-time water→instability signal)
├── elections.py           # Wikipedia election-calendar fallback parser
├── seed_elections.py      # force-seed the Wikidata election cache
├── scripts/
│   └── fetch_data.py      # download/warm every data dependency into ./cache
├── web/                   # single-page D3 dashboard (no build step)
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── cache/                 # raw upstream responses — the audit trail (git-ignored)
├── beames-et-al-2025-…pdf # the framework paper this tool operationalises
├── README.md
├── CONTRIBUTING.md
├── CITATION.cff
├── LICENSE
└── requirements.txt
```

## What it does

For every country it computes:

1. **Standing susceptibility** (0–100) — a transparent, equal-weighted composite of
   structural indicators, each mapped to one of the framework's seven conceptual
   categories and converted to a robust **percentile-rank stress** across all countries.
2. **Live disturbance overlay** — real-time GDACS drought / flood / cyclone alerts
   (Green / Orange / Red) that **elevate** a country's score.
3. **Hotspot alert tier** — Watch → Elevated → High → Critical. Only a country that is
   both structurally susceptible and currently disturbed can reach the top tiers.

**Toggle any layer.** A *Data layers* panel lets you switch any indicator or live boost on/off;
the score, map, watch-list and tiers recompute in the browser instantly (no server round-trip),
so you can isolate streams or stress-test how much any one drives the result.

### The seven framework categories → data

| # | Category | Data stream |
|---|----------|-------------|
| 1 | Antecedent systemic conditions | **UCDP** conflict fatalities (5 yr) · **Powell & Thyne** coups (15 yr) · **V-Dem** regime type (inverted-U) · WB: agriculture %GDP, youth %, rural % |
| 2 | Water disturbance | WB: water stress **+ LIVE GDACS** drought/flood/cyclone |
| 3 | Direct impacts | WB: employment in agriculture |
| 4 | Secondary impacts | WB: undernourishment, GDP/capita, safe-water access |
| 5 | Antecedent social conditions | WB: Gini inequality, internet penetration |
| 6 | Actors | **UCDP** armed-actor roster (side_a/side_b, classified) + **news** named groups; non-violent actors = analyst scaffold. Descriptive, not scored |
| 7 | Instability outcome | **UCDP** observed fatalities — shown as context, *not* scored (using the outcome as input would make the tool predictive) |

The **recent conflict fatalities** indicator (UCDP Georeferenced Event Dataset, last 5
years, log-scaled) fills what was previously the largest gap — the framework's
strongest-evidenced antecedent, the recent history of conflict / political instability.
Remaining categories without a suitable open, global quantitative dataset are marked as
**data gaps** rather than estimated, with the source that could be connected to fill them.

## Architecture

- `server.py` — stdlib HTTP server. Fetches + caches World Bank and GDACS, computes the
  index, serves the API and static frontend.
  - `GET /api/hotspots` — full scored payload (countries, stages, indicators, disturbances, provenance)
  - `GET /api/sources` — source registry, indicator definitions, declared data gaps
- `web/` — single-page dashboard (D3 projection map — Robinson / Equal Earth / rotating
  globe, with country borders and a toggleable Natural Earth rivers overlay; geometry served
  from `/api/world` and `/api/rivers`; alert feed, per-country pathway
  drill-down, methodology & sources). No build step.
- `cache/` — raw upstream API responses (the audit trail).

## Data sources

- **World Bank Open Data** — https://data.worldbank.org — CC BY 4.0, no key.
- **GDACS** (Global Disaster Alert and Coordination System) — https://www.gdacs.org —
  JRC, no key.
- **UCDP Georeferenced Event Dataset (GED v25.1)** — https://ucdp.uu.se/downloads/ —
  Uppsala Conflict Data Program; open static download, no token. ~30 MB zip fetched and
  cached once on first run; per-country recent fatalities recomputed from it.
- **Powell & Thyne Coup d'État Dataset** — http://www.jonathanmpowell.com/coup-detat-dataset.html —
  open static file, no key; successful & attempted coups 1950–present (actively maintained).
- **V-Dem Regimes of the World** (via Our World in Data) — https://ourworldindata.org/grapher/political-regime —
  CC BY; regime type 0–3, ISO3-keyed, mapped to instability stress as an inverted-U.
- **Election calendar** — **Wikidata SPARQL** (primary; https://query.wikidata.org/, CC0) with a
  **Wikipedia fallback** (`List of elections in <year>`, CC BY-SA) when WDQS is unavailable. Upcoming
  national elections (date + ISO3) become a temporal flashpoint flag. The Wikipedia parser derives its
  demonym→country map from Wikipedia's own demonym list (`elections.py`), so nothing is hand-keyed; both
  paths degrade gracefully to empty. `python3 seed_elections.py` force-seeds the Wikidata cache when WDQS is up.
- **Open RSS news feeds (22 humanitarian / water / conflict sources)** — `news.py` — a
  stdlib RSS harvester (no `feedparser`). Articles that mention a water-disturbance term
  **and** a country accumulate over a rolling 7-day store (`cache/news_store.json`),
  producing a real-time *water→instability* signal that complements UCDP's lagged record.
  Each article keeps its headline, source and link. The signal nudges the live **alert**
  (capped, nexus-weighted) and flags "emerging" countries — it is **not** folded into the
  structural susceptibility score, since news is media-biased.
- **Framework:** Beames PL, Brauman KA, Keys PW, McCracken M, Mitchell P, Rosengaertner S,
  Schmeier S, Wolf AT, Gremillion M (2025). *Pathways to instability: A synthetic
  framework to parse connections between water and conflict.* Environment and Security.
  [doi:10.1177/27538796251340790](https://doi.org/10.1177/27538796251340790)

## Known limitations

- Country-resolution only (the framework also operates subnationally; not yet resolved here).
- The composite is an equal-weighted index, not a calibrated risk model.
- UCDP records *organised-violence* fatalities; it does not capture every form of political
  instability (e.g. non-violent unrest, repression short of killing). Latest annual release
  runs to 2024.
- The **Actors** category's armed actors are now populated from UCDP (named sides of
  recorded violence) plus a light news layer; only the *non-violent* mobilisers (activists,
  dissidents, leaders, conflict entrepreneurs) remain an analyst-populated scaffold.
- **Regime type** is a categorical V-Dem class mapped to stress via a fixed inverted-U; the
  mapping is a documented modelling choice, not an empirically calibrated curve. Coups use a
  15-year window; older instability is not counted.

## Citing

Please cite both the software and the framework paper it operationalises — see
[`CITATION.cff`](CITATION.cff). GitHub renders a "Cite this repository" button from it.

## License

The **code** is released under the [MIT License](LICENSE). The **data** it fetches is
not covered by that license: each upstream source keeps its own terms (World Bank — CC BY
4.0, Wikidata — CC0, OWID / V-Dem — CC BY, Wikipedia — CC BY-SA, Natural Earth — public
domain, plus GDACS, UCDP and Powell & Thyne). See the data-sources table above and the
in-app **Sources** tab for the full provenance registry, and honour those terms when
redistributing data.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The short version: every number must come from a
named open source, data gaps are declared rather than fabricated, and the tool reports
**susceptibility, not a forecast**.
