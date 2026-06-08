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

## Refreshing the seeded data

When you want the hosted app to reflect newer UCDP / regime / geometry data, run a
full warm-up locally (which fetches the heavy sources on your machine, where size
is no problem) and copy the small products back into `seed_cache/`:

```bash
python3 scripts/fetch_data.py            # full local warm-up into ./cache
cp cache/ucdp_conflict.json \
   cache/owid_political_regime.txt \
   cache/world-110m.json \
   cache/ne_50m_rivers.json \
   cache/wb_countries.json seed_cache/
git add seed_cache && git commit -m "Refresh seeded data" && git push
```

Render auto-deploys on push (`autoDeploy: true`), so the live site updates within
a minute or two.

## Switching to the UCDP API (later)

UCDP introduced **token-authenticated** API access in February 2026 (it is free —
request a token from the API maintainer, describing your research use). Until you
have a token, the hosted app uses the committed conflict summary, which is fine.

When the token arrives, the swap is small and self-contained — it touches only
`get_conflict()` in `server.py` (see the `TODO(ucdp-api)` marker there):

1. In the Render dashboard → your service → **Environment**, add a secret env var
   `UCDP_API_TOKEN` = your token. (Keep it out of `render.yaml` so it never enters
   git — the file already has a commented placeholder noting this.)
2. Fill in the API branch at the `TODO(ucdp-api)` marker: pull the recent
   `CONFLICT_WINDOW` years from `https://ucdpapi.pcr.uu.se/api/gedevents/<version>`
   with `Authorization: Bearer <token>`, aggregate into the **same** summary shape
   the function already returns, and write `UCDP_JSON`. A few MB of JSON, not the
   239 MB CSV — so it stays well within free-tier limits and needs no seed file.
3. Push. Render auto-deploys; the conflict data is now live rather than seeded.

Nothing else changes — scoring, the map, and the frontend only ever consume
`get_conflict()`'s output, so they are unaffected.

## Other hosts

The same approach works on Railway or Fly.io — they also inject `$PORT` and run
the stdlib server unchanged; only the config file format differs. Ask if you want
a `railway.json` or `fly.toml` instead.
