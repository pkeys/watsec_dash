# Deploying HEADWATERS to the web (Render free tier)

HEADWATERS runs as a small Python server, so it cannot be hosted on GitHub Pages
(which serves static files only). It needs a host that runs Python. These notes
cover [Render](https://render.com), which has a genuine free tier and a simple
GitHub-connected flow. The repo already contains everything needed.

## Why it deploys cleanly

- **No dependencies.** Pure Python standard library — nothing to `pip install`.
- **Reads `$PORT`, binds `0.0.0.0`.** Exactly what cloud hosts require; no code
  change needed.
- **No 270 MB download on boot.** The one thing that would break a free instance
  is the 239 MB UCDP conflict CSV. We avoid it: the small pre-computed conflict
  summary (and map geometry, regime data) is committed under [`seed_cache/`](seed_cache/),
  copied into the runtime cache on startup, and the heavy CSV fetch is disabled
  via the `SKIP_HEAVY_FETCH=1` environment variable (set in `render.yaml`).

## Deploy steps (≈10 minutes, mostly clicking)

1. Sign in to **https://render.com** with your GitHub account.
2. **New → Blueprint** and pick the `pkeys/watsec_dash` repo. Render reads
   [`render.yaml`](render.yaml) and proposes a free web service named `headwaters`.
   (Alternatively **New → Web Service**, pick the repo, set start command
   `python3 server.py`, and add env var `SKIP_HEAVY_FETCH=1` manually.)
3. Click **Apply / Create**. The first build takes ~1–2 minutes.
4. Render gives you a public URL like `https://headwaters.onrender.com`. Open it —
   the dashboard loads and serves real scored data. Share that link.

## What to expect on the free tier

- **Idle sleep.** After ~15 minutes with no traffic the instance sleeps; the next
  visitor waits ~30–60 s while it wakes. Fine for a demo / portfolio link.
- **Ephemeral disk.** The runtime `cache/` is wiped on each redeploy or wake. The
  committed `seed_cache/` is always there, so conflict data + the map are instant;
  the light live streams (World Bank, GDACS, news) re-fetch on demand.
- **Conflict data freshness.** The hosted conflict figures are as current as the
  last time you refreshed `seed_cache/` (see below), not live. The susceptibility
  score only ever uses the most recent 5 years, which the summary already holds.

## How each data stream stays fresh

The streams differ enormously in how often they actually change, so they refresh on
different cadences (cache TTLs in `server.py`):

| Stream | Cache TTL | Reality | On the hosted (free) tier |
|--------|-----------|---------|---------------------------|
| GDACS disturbances | 30 min | real-time hazards | lazy-refreshed on a visit when stale |
| News (RSS) | per-visit, 7-day rolling | new articles constantly | accumulates while the app is awake |
| World Bank indicators | 24 h | slow structural | lazy-refreshed |
| Elections | 3 days | calendar | lazy-refreshed |
| **UCDP conflict** | 30 days | **annual** (next ~mid-2026) | **served from the committed seed** |

We deliberately do **not** run any background scheduler. Render's free tier sleeps
after ~15 min idle and wipes the runtime `cache/`, so a timer-in-a-thread is
unreliable there anyway. Instead every stream refreshes lazily when a visitor
arrives and its cache has expired — except the slow, annual conflict data, which is
served from the committed seed and never fetched on a web request (see below).

> **Keep-warm pinger (included).** [`.github/workflows/keep-warm.yml`](.github/workflows/keep-warm.yml)
> pings `/api/hotspots` every 20 min via GitHub Actions. This keeps the free Render
> instance awake and, crucially, lets the rolling 7-day **news** harvest accumulate
> instead of resetting on every sleep. It uses zero UCDP quota (conflict is seeded).
> Enable it by pushing to GitHub with Actions on; trigger a first run manually from
> the repo's **Actions → keep-warm → Run workflow**. To stop it, disable the workflow
> in the Actions tab.

## Refreshing the seeded conflict data (with the UCDP API token)

UCDP introduced **token-authenticated** API access in February 2026 (free — request
a token from the API maintainer). The server already supports it: when the
`UCDP_API_TOKEN` env var is set **locally**, `get_conflict()` pulls only the recent
5 years from the REST API (a few MB via the `x-ucdp-access-token` header, paginated)
instead of the 239 MB CSV, and writes the same small `ucdp_conflict.json`.

Because UCDP GED is annual, you only need to refresh the seed when a new release
lands. To do it (≈5 min):

```bash
# fetch the recent years via the API and rebuild the summary into ./cache
UCDP_API_TOKEN=your-token python3 -c "import server; server.get_conflict(server.get_countries())"

# refresh the rest of the committed seed too (regime, geometry, country list)
python3 scripts/fetch_data.py
cp cache/ucdp_conflict.json \
   cache/owid_political_regime.txt \
   cache/world-110m.json \
   cache/ne_50m_rivers.json \
   cache/wb_countries.json seed_cache/
git add seed_cache && git commit -m "Refresh seeded data" && git push
```

Render auto-deploys on push (`autoDeploy: true`), so the live site updates within a
minute or two.

**Why the cloud doesn't call the API itself.** The hosted instance runs with
`SKIP_HEAVY_FETCH=1`, and `get_conflict()` checks that guard *before* the API/CSV
paths — so a web request always serves the seed instantly and never triggers the
~5-min paginated fetch. (If you ever *did* want the cloud to refresh from the API,
you would set `UCDP_API_TOKEN` in the Render dashboard as a secret and remove
`SKIP_HEAVY_FETCH` — but for annual data that just spends time and request-quota for
no real gain.) Either way the token must stay a dashboard secret, never committed.

Nothing else changes when you refresh — scoring, the map, and the frontend only ever
consume `get_conflict()`'s output, so they are unaffected.

## Other hosts

The same approach works on Railway or Fly.io — they also inject `$PORT` and run
the stdlib server unchanged; only the config file format differs. Ask if you want
a `railway.json` or `fly.toml` instead.
