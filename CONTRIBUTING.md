# Contributing

Thank you for your interest in the **Pathways to Instability — Global
Water–Conflict Hotspot Watch**. Contributions are welcome, but this tool carries
a few non-negotiable design commitments. Please read them before opening a PR.

## Running it locally

No dependencies beyond Python 3 (standard library only):

```bash
python3 scripts/fetch_data.py   # optional: warm the data cache up front
python3 server.py               # then open http://localhost:8765
```

Set `PORT=…` to change the port. Raw upstream responses are written to `./cache`
(git-ignored) so the provenance is inspectable and reruns are fast.

## The three commitments

These are the spine of the project. A change that violates one will not be merged.

1. **Do not make things up.** Every number shown must be fetched from a named,
   open, citable public source, and every response is cached to `./cache` as the
   audit trail. No synthetic values, no plugged gaps.

2. **Declare data gaps; never fabricate.** Where a framework category has no open,
   global quantitative stream, mark it as a **data gap** (with the source that
   *could* fill it) rather than estimating a value.

3. **Susceptibility, not a forecast.** The Beames et al. (2025) framework is
   explicitly *not predictive*, and neither is this tool. It reports the standing
   conditions that make a place more liable to instability *if* a water
   disturbance occurs — it never forecasts conflict. In particular, the observed
   instability *outcome* (UCDP fatalities) is shown as context and is **never**
   folded into the susceptibility score (doing so would make the tool predictive).

## Adding a data source

When you wire in a new stream:

- Add it to the provenance registry in `server.py` (the single source of truth the
  Sources tab renders verbatim), with a real name and URL.
- Cache the raw response under `./cache` with a sensible TTL.
- Add the fetch to `scripts/fetch_data.py` if it should be warmed up front.
- Document its license in the `README.md` data-sources table and the `LICENSE`
  data note. Prefer open licenses (CC BY, CC0, public domain).
- Keep it **standard-library only** — no third-party runtime dependencies.

## Style

- Match the surrounding code: stdlib idioms, clear provenance comments, graceful
  degradation when a source is unavailable (the tool should never hard-crash
  because one upstream feed is down).
- Keep the frontend build-step-free (plain HTML/CSS/JS + D3 from a CDN).

## Reporting issues

Please include the upstream source involved, the cached file under `./cache` if
relevant, and the steps to reproduce.
