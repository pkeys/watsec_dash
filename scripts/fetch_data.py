#!/usr/bin/env python3
"""
fetch_data.py — the dependencies fetcher for the GWSC Pathways-to-Instability
dashboard.

This script pulls every LIVE, OPEN, CITABLE upstream data stream the dashboard
relies on into ./cache, so a fresh clone is fully warmed *before* you start the
server. It is also an explicit, inspectable MANIFEST of every data origin — in
keeping with the project's transparency mandate, nothing here is fabricated and
nothing is hidden.

It does NOT reimplement any fetch logic. It calls the very same routines the
server uses at startup (`server.cached_payload(force=True)`, plus the map
geometry helpers), so what this script downloads is exactly what the running
dashboard would download. The raw responses land in ./cache as the audit trail.

Usage:
    python3 scripts/fetch_data.py

Notes:
  * Standard library only — no `pip install`.
  * Idempotent and TTL-aware: re-running re-uses still-fresh cache entries and
    only re-fetches what has gone stale (or is missing).
  * The server ALSO self-warms on startup, so this script is a convenience and a
    manifest, not a hard requirement. But running it first means the first page
    load is instant and the full provenance trail exists offline.

Data origins fetched here (all open, all citable — see README for licenses):
  * World Bank Open Data .................. structural / quantitative indicators
  * GDACS ................................. real-time water disturbances
  * UCDP Georeferenced Event Dataset ..... recent conflict fatalities (~30 MB zip)
  * Powell & Thyne Coup d'Etat Dataset ... coups (15 yr window)
  * V-Dem Regimes of the World (via OWID). regime type
  * Wikidata SPARQL / Wikipedia .......... national election calendar
  * world-atlas 110m TopoJSON ............ country geometry for the map
  * Natural Earth 50m rivers ............. river overlay
"""

import os
import sys
import time

# Make the script runnable from anywhere: put the repo root (one level up) on
# the path so `import server` resolves to the dashboard's own module.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import server  # noqa: E402  (path set above on purpose)


def _human(path):
    """Return a short 'name (size)' label for a cache file, if it exists."""
    if path and os.path.exists(path):
        size = os.path.getsize(path)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{os.path.basename(path)} ({size:.0f} {unit})"
            size /= 1024.0
    return f"{os.path.basename(path) if path else '?'} (not written)"


def main():
    print("GWSC Pathways dashboard — fetching data dependencies into ./cache")
    print(f"  cache directory: {server.CACHE}")
    print("  (this reuses the server's own fetch routines; nothing is faked)\n")

    t0 = time.time()
    failures = []

    # 1) The full structured payload. This pulls World Bank indicators, GDACS
    #    disturbances, the UCDP GED zip (~30 MB, fetched & cached once), Powell &
    #    Thyne coups, V-Dem/OWID regime, the election calendar and the news feed
    #    — i.e. every scored/contextual stream. force=True writes fresh cache.
    print("[1/2] Warming all scored data streams via cached_payload(force=True)…")
    try:
        payload = server.cached_payload(force=True)
        counts = payload.get("counts", {})
        print(f"      scored {counts.get('countries_scored', '?')} countries; "
              f"{counts.get('active_disturbances', '?')} active disturbances; "
              f"{counts.get('countries_with_recent_conflict', '?')} with recent conflict.")
    except Exception as e:  # noqa: BLE001 — report, don't crash the whole fetch
        print(f"      ERROR warming payload: {e}")
        failures.append(("data payload (World Bank / GDACS / UCDP / …)", e))

    # 2) Map geometry. The server fetches these lazily on first /api/world and
    #    /api/rivers hit, so we pull them explicitly to warm the map offline too.
    print("[2/2] Fetching map geometry (country borders + rivers)…")
    try:
        world = server.ensure_world_topojson()
        print(f"      world geometry: {_human(world)}")
        if not world:
            failures.append(("world-110m TopoJSON", "download returned no file"))
    except Exception as e:  # noqa: BLE001
        print(f"      ERROR fetching world geometry: {e}")
        failures.append(("world-110m TopoJSON", e))
    try:
        rivers = server.ensure_rivers()
        print(f"      rivers overlay: {_human(rivers)}")
        if not rivers:
            failures.append(("Natural Earth 50m rivers", "download returned no file"))
    except Exception as e:  # noqa: BLE001
        print(f"      ERROR fetching rivers: {e}")
        failures.append(("Natural Earth 50m rivers", e))

    dt = time.time() - t0
    print(f"\nDone in {dt:.0f}s.")

    if failures:
        print(f"\n{len(failures)} source(s) could not be fetched:")
        for name, err in failures:
            print(f"  - {name}: {err}")
        print("\nThe dashboard will retry these the next time it needs them; some "
              "sources (e.g. Wikidata) degrade gracefully when unavailable.")
        return 1

    print("All data dependencies are in ./cache. You can now run:  python3 server.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
