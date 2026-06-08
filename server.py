#!/usr/bin/env python3
"""
HEADWATERS — A Global Water–Conflict Susceptibility Watch — backend.

Operationalises the Beames et al. (2025) "Pathways to Instability" framework.

A small, dependency-free (stdlib only) server that:
  * serves the static dashboard from ./web
  * pulls LIVE, OPEN, CITABLE data streams:
      - World Bank Open Data API   (structural / quantitative antecedent conditions)
      - GDACS GeoJSON              (real-time water disturbances: drought / flood / storm)
  * computes a "Pathways Susceptibility Index" + live "Hotspot Alert" per country,
    decomposed by the seven conceptual categories of Beames et al. (2025).

Design commitments (per project CLAUDE.md):
  * Do not make things up. Every number is fetched from a named public source and
    every API response is cached to ./cache so the raw provenance is inspectable.
  * The framework is NOT predictive (the authors say so). We compute *susceptibility*
    and flag *active disturbances to watch* — never a forecast of conflict.
  * Where a framework category has no open quantitative stream (actors, instability,
    political-stability history), we declare a DATA GAP rather than fabricate a value.

Reference:
  Beames, Brauman, Keys, McCracken, Mitchell, Rosengaertner, Schmeier, Wolf & Gremillion
  (2025) "Pathways to instability: A synthetic framework to parse connections between
  water and conflict." Environment and Security. DOI: 10.1177/27538796251340790
"""

import datetime
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import news       # lightweight stdlib RSS harvester (real-time water–instability signal)
import elections  # Wikipedia election-calendar fallback when Wikidata/WDQS is down

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
CACHE = os.path.join(ROOT, "cache")
SEED_CACHE = os.path.join(ROOT, "seed_cache")
os.makedirs(CACHE, exist_ok=True)


def seed_cache():
    """Copy any committed pre-computed cache files (seed_cache/) into the runtime
    cache/ if missing. This lets a fresh deploy (e.g. a free-tier cloud box) boot
    in seconds with the small derived data already present — chiefly the reduced
    UCDP conflict summary — instead of downloading the ~270 MB raw UCDP CSV, which
    would exhaust limited RAM/disk. Locally it is a harmless no-op once cache/ is
    warm. The seed files are refreshed by re-running the full warm-up and copying
    the products back into seed_cache/ (see DEPLOY.md)."""
    import shutil
    if not os.path.isdir(SEED_CACHE):
        return
    restored = []
    for name in os.listdir(SEED_CACHE):
        src = os.path.join(SEED_CACHE, name)
        dst = os.path.join(CACHE, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
                restored.append(name)
            except OSError:
                pass
    if restored:
        print(f"  seeded {len(restored)} cache file(s) from seed_cache/: "
              f"{', '.join(sorted(restored))}")

# Cache lifetimes (seconds). Structural data moves slowly; hazards move fast.
TTL_INDICATOR = 24 * 3600
TTL_DISTURBANCE = 30 * 60
TTL_COUNTRY = 7 * 24 * 3600

USER_AGENT = "Headwaters/1.0 (water-security research; contact pkeys.earth@gmail.com)"

# ---------------------------------------------------------------------------
# Provenance registry — the single source of truth for "where did this come from".
# The frontend renders this verbatim on the Sources tab and links every indicator
# back to its entry here.
# ---------------------------------------------------------------------------

WB = "https://api.worldbank.org/v2"

# stage keys mirror the seven conceptual categories of the framework
STAGES = [
    ("systemic",     "1. Antecedent systemic conditions"),
    ("disturbance",  "2. Water disturbance"),
    ("direct",       "3. Direct impacts"),
    ("secondary",    "4. Secondary impacts"),
    ("social",       "5. Antecedent social conditions"),
    ("actors",       "6. Actors"),
    ("instability",  "7. Instability outcome"),
]

# Each indicator: World Bank code, the framework stage it informs, human label,
# unit, and `direction` = +1 if a HIGHER value means HIGHER susceptibility,
# -1 if a higher value means LOWER susceptibility (protective).
INDICATORS = [
    # 1. Antecedent systemic conditions
    {"code": "UCDP.GED.DEATHS", "source": "ucdp", "stage": "systemic", "dir": +1,
     "norm": "log",
     "label": "Recent conflict fatalities (UCDP GED, last 5 yrs)", "unit": "deaths",
     "rationale": "Recent history of political instability and conflict is the framework's strongest-evidenced antecedent predictor of future instability (Cingranelli et al. 2019; Mach et al. 2019; Fearon & Laitin 2003)."},
    {"code": "PT.COUPS", "source": "coups", "stage": "systemic", "dir": +1,
     "norm": "log",
     "label": "Coup events (Powell & Thyne, last 15 yrs)", "unit": "weighted",
     "rationale": "Coups and coup attempts are the sharpest form of recent political instability — the framework's strongest antecedent — and capture bloodless seizures of power that fatality data miss."},
    {"code": "OWID.REGIME", "source": "regime", "stage": "systemic", "dir": +1,
     "norm": "regime", "labels": True,
     "label": "Political regime type (V-Dem, Regimes of the World)", "unit": "",
     "rationale": "Mixed/hybrid regimes (electoral autocracies & weak electoral democracies) lack both political and repressive means of dispute settlement and tend toward instability; consolidated liberal democracies and closed autocracies are comparatively stable (framework: anocracy, Dyrstad & Hillesund 2020; Raleigh & Urdal 2007). Mapped as an inverted-U, not a straight line."},
    {"code": "NV.AGR.TOTL.ZS", "stage": "systemic", "dir": +1,
     "label": "Agriculture, forestry & fishing, value added", "unit": "% of GDP",
     "rationale": "Reliance on climate-sensitive sectors raises exposure of livelihoods to water disturbance (framework: antecedent systemic conditions)."},
    {"code": "SP.POP.0014.TO.ZS", "stage": "systemic", "dir": +1,
     "label": "Population ages 0–14", "unit": "% of total",
     "rationale": "Larger, younger populations are associated with higher likelihood of instability (Acemoglu et al. 2020; Oka et al. 2017, cited in framework)."},
    {"code": "SP.RUR.TOTL.ZS", "stage": "systemic", "dir": +1,
     "label": "Rural population", "unit": "% of total",
     "rationale": "Rural, often rainfed-dependent populations are more directly exposed to agricultural water shortfalls."},

    # 2. Water disturbance (standing baseline; the LIVE layer comes from GDACS)
    {"code": "ER.H2O.FWST.ZS", "stage": "disturbance", "dir": +1,
     "label": "Level of water stress (freshwater withdrawal vs. available)", "unit": "%",
     "rationale": "Baseline hydrologic stress amplifies the impact of any acute drought/flood disturbance."},

    # 3. Direct impacts
    {"code": "SL.AGR.EMPL.ZS", "stage": "direct", "dir": +1,
     "label": "Employment in agriculture", "unit": "% of employment",
     "rationale": "Share of workforce whose income is directly hit when water disturbance damages crops/livestock (framework: direct impacts → water restriction to users)."},

    # 4. Secondary impacts
    {"code": "SN.ITK.DEFC.ZS", "stage": "secondary", "dir": +1,
     "label": "Prevalence of undernourishment", "unit": "% of population",
     "rationale": "Pre-existing food insecurity means secondary impacts (food scarcity, health) bite harder and faster."},
    {"code": "NY.GDP.PCAP.CD", "stage": "secondary", "dir": -1,
     "label": "GDP per capita", "unit": "current US$",
     "rationale": "Lower income = thinner buffers and weaker capacity to absorb economic secondary impacts and render disaster aid."},
    {"code": "SH.H2O.SMDW.ZS", "stage": "secondary", "dir": -1,
     "label": "Safely managed drinking water access", "unit": "% of population",
     "rationale": "Low safe-water access widens health/sense-of-place secondary impacts of any disturbance."},

    # 5. Antecedent social conditions
    {"code": "SI.POV.GINI", "stage": "social", "dir": +1,
     "label": "Gini index (income inequality)", "unit": "0–100",
     "rationale": "Horizontal inequality is the framework's most strongly evidenced grievance driver (von Uexkull et al. 2016; Cederman et al. 2011)."},
    {"code": "IT.NET.USER.ZS", "stage": "social", "dir": +1,
     "label": "Individuals using the Internet", "unit": "% of population",
     "rationale": "Densely networked societies can more readily mobilize and sustain collective action (Almeida 2018; Leenders 2012)."},
]

# Declared data gaps — categories the framework needs but for which no open,
# no-key, global quantitative stream exists. We surface these honestly instead
# of inventing values. Each names the source a user could plug in.
DATA_GAPS = [
    {"stage": "actors",
     "what": "Non-violent actors who mobilise grievance/opportunity",
     "note": "Armed actors are now populated from UCDP (named sides of recorded violence) and current reporting. But the framework's NON-VIOLENT mobilisers — activists, dissidents, community/religious leaders and conflict entrepreneurs — leave no lethal-event trace and remain an expert-populated category.",
     "plug_in": "ACLED actor/event coding (acleddata.com); analyst input. No global quantitative proxy is asserted for the non-violent actors."},
]

SOURCES = [
    {"id": "worldbank", "name": "World Bank Open Data API",
     "url": "https://data.worldbank.org", "api": f"{WB}/country/all/indicator/<code>",
     "license": "CC BY 4.0", "auth": "none (open)",
     "role": "Structural / quantitative antecedent conditions and impact-exposure indicators."},
    {"id": "gdacs", "name": "GDACS — Global Disaster Alert and Coordination System",
     "url": "https://www.gdacs.org", "api": "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH",
     "license": "Open (GDACS / JRC, ReliefWeb terms)", "auth": "none (open)",
     "role": "Real-time water disturbances — drought (DR), flood (FL), tropical cyclone (TC) — with Green/Orange/Red alert levels."},
    {"id": "ucdp", "name": "UCDP Georeferenced Event Dataset (GED v25.1)",
     "url": "https://ucdp.uu.se/downloads/", "api": "https://ucdp.uu.se/downloads/ged/ged251-csv.zip",
     "license": "Open data (Uppsala Conflict Data Program); cite Davies et al.", "auth": "none (static download)",
     "role": "Recorded organised-violence events & fatalities (1989–2024) — recent conflict history (antecedent), observed-instability context, and named armed actors."},
    {"id": "coups", "name": "Powell & Thyne Coup d'État Dataset",
     "url": "http://www.jonathanmpowell.com/coup-detat-dataset.html",
     "api": "http://www.uky.edu/~clthyn2/coup_data/powell_thyne_coups_final.txt",
     "license": "Open; cite Powell & Thyne (2011)", "auth": "none (static file)",
     "role": "Successful & attempted coups (1950–present) — the sharpest form of recent political instability (antecedent systemic condition)."},
    {"id": "regime", "name": "V-Dem Regimes of the World (via Our World in Data)",
     "url": "https://ourworldindata.org/grapher/political-regime",
     "api": "https://ourworldindata.org/grapher/political-regime.csv",
     "license": "CC BY (V-Dem; OWID)", "auth": "none (open)",
     "role": "Regime type (closed autocracy → liberal democracy). Mapped to instability stress as an inverted-U: hybrid regimes most unstable."},
    {"id": "elections", "name": "Wikidata SPARQL + Wikipedia (national election calendar)",
     "url": "https://en.wikipedia.org/wiki/List_of_elections_in_2026",
     "api": "Wikidata SPARQL (primary) → Wikipedia 'List of elections in <year>' (fallback)",
     "license": "CC0 (Wikidata) / CC BY-SA (Wikipedia)", "auth": "none (open)",
     "role": "Upcoming national elections as a temporal flashpoint flag. Within the anticipation window it nudges the live alert; never the structural score."},
    {"id": "news", "name": "Open RSS news feeds (16 humanitarian / water / conflict sources)",
     "url": "https://www.thenewhumanitarian.org", "api": "RSS / Atom (see news.py registry)",
     "license": "Headlines & links only, per each publisher", "auth": "none (open)",
     "role": "Real-time water→instability media signal (rolling 7-day window): an emerging-watch complement to UCDP's lagged record. Nudges the live alert; never the structural score."},
    {"id": "basemap", "name": "Natural Earth geometry (country borders + 50m river centerlines)",
     "url": "https://www.naturalearthdata.com/", "api": "/api/world, /api/rivers (cached locally)",
     "license": "Public domain (Natural Earth)", "auth": "none",
     "role": "Map base geometry: country fills & borders, and the optional river overlay, for the D3 projections (Robinson / Equal Earth / orthographic globe)."},
    {"id": "framework", "name": "Beames et al. (2025), Pathways to Instability Framework",
     "url": "https://doi.org/10.1177/27538796251340790", "api": "—",
     "license": "Author(s) 2025, Environment and Security", "auth": "—",
     "role": "The analytical backbone: seven conceptual categories from water disturbance to instability."},
]

# ---------------------------------------------------------------------------
# Tiny cached HTTP fetch
# ---------------------------------------------------------------------------

def _cache_path(key):
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    return os.path.join(CACHE, safe + ".json")


def fetch_text(url, cache_key, ttl, retries=4, timeout=60, neg_ttl=0):
    """Fetch a plain-text/CSV body with a disk cache. Returns (text, meta).
    Cached as <key>.txt so the raw upstream file is inspectable as provenance.
    neg_ttl>0 enables a negative cache: after a total failure, don't retry the
    network for neg_ttl seconds (so a down upstream can't slow every rebuild)."""
    path = os.path.join(CACHE, "".join(c if c.isalnum() or c in "._-" else "_"
                                       for c in cache_key) + ".txt")
    negpath = path + ".fail"
    now = time.time()
    if os.path.exists(path) and (now - os.path.getmtime(path)) < ttl:
        with open(path, encoding="utf-8") as f:
            return f.read(), {"url": url, "ok": True, "from_cache": True}
    if neg_ttl and os.path.exists(negpath) and (now - os.path.getmtime(negpath)) < neg_ttl:
        return None, {"url": url, "ok": False, "from_cache": False,
                      "error": "skipped (recent failure cached)"}
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if body[:2] == b"\x1f\x8b":
                import gzip
                body = gzip.decompress(body)
            text = body.decode("utf-8-sig", errors="replace")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return text, {"url": url, "ok": True, "from_cache": False,
                          "fetched_at": int(now), "attempts": attempt + 1}
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError, ValueError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read(), {"url": url, "ok": True, "from_cache": True,
                              "stale": True, "error": str(last_err)}
    if neg_ttl:  # remember the failure so we don't hammer a down upstream
        try:
            with open(negpath, "w") as f:
                f.write(str(last_err))
        except OSError:
            pass
    return None, {"url": url, "ok": False, "error": str(last_err)}


WORLD_TOPOJSON_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json"
RIVERS_URL = ("https://cdn.jsdelivr.net/gh/martynafford/natural-earth-geojson@master/"
              "50m/physical/ne_50m_rivers_lake_centerlines.json")


def _ensure_geo(url, filename):
    """Download a geometry file to cache once; return its path (or None)."""
    path = os.path.join(CACHE, filename)
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as out:
            out.write(r.read())
        return path
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return path if os.path.exists(path) else None


def ensure_world_topojson():
    """world-atlas 110m country geometry (land + country polygons), cached locally."""
    return _ensure_geo(WORLD_TOPOJSON_URL, "world-110m.json")


def ensure_rivers():
    """Natural Earth 50m river centerlines (GeoJSON), cached locally."""
    return _ensure_geo(RIVERS_URL, "ne_50m_rivers.json")


def fetch_json(url, cache_key, ttl, retries=6):
    """Fetch JSON with a disk cache. Returns (data, meta) where meta records
    provenance: the exact url, fetch time, and whether it was served from cache.

    The World Bank API intermittently returns an XML 'Request Error' page under
    rapid-fire load (rate-limiting). We treat any non-JSON body as a transient
    failure and retry with backoff before giving up to stale cache."""
    path = _cache_path(cache_key)
    now = time.time()
    if os.path.exists(path) and (now - os.path.getmtime(path)) < ttl:
        with open(path) as f:
            payload = json.load(f)
        payload["_meta"]["from_cache"] = True
        return payload["data"], payload["_meta"]

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=45) as r:
                # Some World Bank responses carry a UTF-8 BOM, which breaks a plain
                # utf-8 decode -> json.loads. utf-8-sig strips it.
                data = json.loads(r.read().decode("utf-8-sig"))
            meta = {"url": url, "fetched_at": int(time.time()),
                    "from_cache": False, "ok": True, "attempts": attempt + 1}
            with open(path, "w") as f:
                json.dump({"data": data, "_meta": meta}, f)
            return data, meta
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, ValueError, OSError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))  # linear backoff

    # All retries failed. Fall back to stale cache if we have it.
    if os.path.exists(path):
        with open(path) as f:
            payload = json.load(f)
        payload["_meta"]["from_cache"] = True
        payload["_meta"]["stale"] = True
        payload["_meta"]["error"] = str(last_err)
        return payload["data"], payload["_meta"]
    return None, {"url": url, "ok": False, "error": str(last_err)}


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def get_countries():
    """ISO3 -> {name, lon, lat, region, income}. Aggregates dropped."""
    url = f"{WB}/country?format=json&per_page=400"
    data, _ = fetch_json(url, "wb_countries", TTL_COUNTRY)
    out = {}
    if not data or len(data) < 2:
        return out
    for c in data[1]:
        if c["region"]["value"] == "Aggregates":
            continue
        try:
            lon = float(c["longitude"]); lat = float(c["latitude"])
        except (ValueError, TypeError):
            continue
        out[c["id"]] = {
            "iso3": c["id"], "name": c["name"], "lon": lon, "lat": lat,
            "region": c["region"]["value"].strip(),
            "income": c["incomeLevel"]["value"],
        }
    return out


def get_indicator(code):
    """ISO3 -> {value, year} latest non-empty value, plus provenance meta."""
    url = f"{WB}/country/all/indicator/{code}?format=json&mrnev=1&per_page=400"
    data, meta = fetch_json(url, f"wb_{code}", TTL_INDICATOR)
    out = {}
    if data and len(data) >= 2 and isinstance(data[1], list):
        for r in data[1]:
            if r.get("value") is None:
                continue
            iso = r.get("countryiso3code")
            if iso:
                out[iso] = {"value": r["value"], "year": r.get("date")}
    return out, meta


# GDACS uses these type codes; we only care about water disturbances.
DISTURBANCE_TYPES = {"DR": "Drought", "FL": "Flood", "TC": "Tropical cyclone"}
ALERT_RANK = {"Green": 1, "Orange": 2, "Red": 3}


def get_disturbances():
    """Live water disturbances from GDACS, normalised to a small list."""
    url = ("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
           "?eventtypes=DR;FL;TC&alertlevel=Green;Orange;Red")
    data, meta = fetch_json(url, "gdacs_events", TTL_DISTURBANCE)
    events = []
    if data and isinstance(data, dict):
        for f in data.get("features", []):
            p = f.get("properties", {})
            etype = p.get("eventtype")
            if etype not in DISTURBANCE_TYPES:
                continue
            geom = f.get("geometry", {}) or {}
            coords = geom.get("coordinates") or [None, None]
            events.append({
                "id": p.get("eventid"),
                "type": etype,
                "type_label": DISTURBANCE_TYPES[etype],
                "name": p.get("name") or p.get("eventname") or DISTURBANCE_TYPES[etype],
                "alert": p.get("alertlevel"),
                "alert_rank": ALERT_RANK.get(p.get("alertlevel"), 0),
                "country": p.get("country"),
                "iso3": (p.get("iso3") or "").upper() or None,
                "from": p.get("fromdate"),
                "to": p.get("todate"),
                "severity": (p.get("severitydata") or {}).get("severitytext"),
                "lon": coords[0], "lat": coords[1],
                "url": p.get("url", {}).get("report") if isinstance(p.get("url"), dict) else None,
            })
    events.sort(key=lambda e: (-e["alert_rank"], e["type"]))
    return events, meta


# ---------------------------------------------------------------------------
# UCDP conflict data (Georeferenced Event Dataset, GED) — open static download,
# no token required. Fills the framework's strongest antecedent: recent history
# of conflict / political instability. Also supplies an observed-instability
# context figure (shown, never scored into susceptibility).
# ---------------------------------------------------------------------------

UCDP_GED_URL = "https://ucdp.uu.se/downloads/ged/ged251-csv.zip"
UCDP_VERSION = "GED v25.1 (1989–2024)"
UCDP_CSV = os.path.join(CACHE, "ged251.csv")
UCDP_JSON = os.path.join(CACHE, "ucdp_conflict.json")
CONFLICT_WINDOW = 5            # years for the "recent history" indicator
TTL_CONFLICT = 30 * 24 * 3600  # UCDP is annual; refresh monthly at most

# Authenticated REST API (UCDP introduced token access Feb 2026). When the env var
# UCDP_API_TOKEN is set, get_conflict() pulls only the recent CONFLICT_WINDOW years
# from here (a few MB of JSON) instead of the 239 MB CSV. Token goes in the
# x-ucdp-access-token header; never hard-code it. 5,000 requests/day cap.
UCDP_API_BASE = "https://ucdpapi.pcr.uu.se/api/gedevents/25.1"
UCDP_API_PAGESIZE = 5000      # ~22 pages for a 5-year window -> well under the cap

# UCDP uses historical / parenthetical country names; map them to ISO3.
CONFLICT_OVERRIDES = {
    "yemen": "YEM", "dr congo": "COD", "congo": "COG", "myanmar": "MMR",
    "russia": "RUS", "united states of america": "USA", "laos": "LAO",
    "ivory coast": "CIV", "syria": "SYR", "iran": "IRN", "tanzania": "TZA",
    "macedonia": "MKD", "serbia": "SRB", "yugoslavia": "SRB",
    "bosnia-herzegovina": "BIH", "cambodia": "KHM", "south korea": "KOR",
    "north korea": "PRK", "vietnam": "VNM", "turkey": "TUR", "venezuela": "VEN",
    "egypt": "EGY", "kingdom of eswatini": "SWZ", "swaziland": "SWZ",
    "east timor": "TLS", "kyrgyzstan": "KGZ",
}


def _norm_country(s):
    import re
    s = re.sub(r"\(.*?\)", "", s).lower().strip()
    s = s.replace("&", "and").replace(".", "").replace(",", "").replace("'", "")
    return re.sub(r"\s+", " ", s).strip()


def _conflict_name_maps(countries):
    """Build full-name and comma-split lookup tables WB-name -> ISO3."""
    full, split = {}, {}
    for iso, c in countries.items():
        full[_norm_country(c["name"])] = iso
        key = _norm_country(c["name"].split(",")[0])
        if key not in split and key != "congo":
            split[key] = iso
    return full, split


def _classify_actor(name, tovs):
    """Map a UCDP actor onto the framework's actor typology, using its name and
    the UCDP violence types (1=state-based, 2=non-state, 3=one-sided) it appears in."""
    if name.lower().startswith("government of"):
        return "state_government", "State government"
    if "1" in tovs:
        return "non_state_armed", "Non-state armed group (rebel / insurgent)"
    if "2" in tovs:
        return "communal", "Communal / identity militia"
    return "armed_other", "Armed group"


def _rank_actors(roster, top=8):
    """Rank a country's actors by event count, classify, return a compact list."""
    ranked = sorted(roster.items(), key=lambda kv: (-kv[1]["events"], -kv[1]["deaths"]))
    out = []
    for name, d in ranked[:top]:
        cat, cat_label = _classify_actor(name, d["tovs"])
        out.append({"name": name, "events": d["events"], "deaths": d["deaths"],
                    "category": cat, "category_label": cat_label})
    return out


def _aggregate_conflict(rows, countries, max_year, source_url, via):
    """Reduce UCDP GED event rows (CSV DictReader rows OR API JSON objects — same
    field names) into the per-ISO3 summary. Shared by the CSV and API paths so both
    produce an identical shape. Only rows within the recent CONFLICT_WINDOW count."""
    full, split = _conflict_name_maps(countries)

    def lookup(name):
        nm = _norm_country(name)
        return CONFLICT_OVERRIDES.get(nm) or full.get(nm) or split.get(nm)

    agg = {iso: {"recent_deaths": 0, "recent_events": 0,
                 "latest_deaths": 0, "latest_events": 0} for iso in countries}
    rosters = {iso: {} for iso in countries}   # iso -> {actor -> {events,deaths,tovs}}
    recent_from = max_year - CONFLICT_WINDOW + 1
    unmatched = 0
    for r in rows:
        y = int(r["year"])
        if y < recent_from:
            continue
        iso = lookup(r["country"])
        if iso is None or iso not in agg:
            unmatched += 1
            continue
        best = int(r["best"]) if r["best"] not in (None, "") else 0
        agg[iso]["recent_deaths"] += best
        agg[iso]["recent_events"] += 1
        if y == max_year:
            agg[iso]["latest_deaths"] += best
            agg[iso]["latest_events"] += 1
        tov = r.get("type_of_violence", "")
        for side in (r.get("side_a", ""), r.get("side_b", "")):
            side = (str(side) if side is not None else "").strip()
            if not side or side.lower() == "civilians":
                continue  # civilians are victims, not mobilising actors
            a = rosters[iso].setdefault(side, {"events": 0, "deaths": 0, "tovs": set()})
            a["events"] += 1
            a["deaths"] += best
            a["tovs"].add(tov)

    window = f"{recent_from}–{max_year}"
    for iso in agg:
        agg[iso]["latest_year"] = max_year
        agg[iso]["window"] = window
        agg[iso]["actors"] = _rank_actors(rosters[iso])
        agg[iso]["actor_count"] = len(rosters[iso])

    meta = {"source": UCDP_VERSION, "url": source_url, "ok": True,
            "fetched_at": int(time.time()), "from_cache": False, "via": via,
            "window": window, "latest_year": max_year, "unmatched_events": unmatched}
    return agg, meta


def _fetch_ucdp_api(token):
    """Pull the recent CONFLICT_WINDOW years of GED events from the authenticated
    REST API (x-ucdp-access-token header), paginating until done. Returns
    (rows, max_year). Filtered server-side by StartDate so we transfer only what we
    need — a few MB, not the 239 MB CSV."""
    # We don't know max_year a priori, so request a generous recent window and let
    # the data tell us the latest year. UCDP GED is annual; a 6-year StartDate
    # comfortably covers CONFLICT_WINDOW even if a new annual release lands.
    this_year = datetime.date.today().year
    start = f"{this_year - CONFLICT_WINDOW - 1}-01-01"
    rows = []
    page = 0
    while True:
        qs = urllib.parse.urlencode({"pagesize": UCDP_API_PAGESIZE, "page": page,
                                     "StartDate": start})
        url = f"{UCDP_API_BASE}?{qs}"
        req = urllib.request.Request(url, headers={
            "x-ucdp-access-token": token, "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.load(resp)
        batch = payload.get("Result") or []
        rows.extend(batch)
        total_pages = payload.get("TotalPages") or 1
        page += 1
        if page >= total_pages or not batch:
            break
    if not rows:
        raise RuntimeError("UCDP API returned no events")
    max_year = max(int(r["year"]) for r in rows)
    return rows, max_year


def get_conflict(countries):
    """Per-ISO3 conflict aggregates from UCDP GED. Returns (data, meta).

    data[iso] = {recent_deaths, recent_events, latest_year, latest_deaths,
                 latest_events, window}.  Countries absent from GED are genuine
                 zeros (no recorded organised-violence deaths), not missing."""
    # Fast path: small cached JSON (the reduced per-country summary).
    if os.path.exists(UCDP_JSON) and (time.time() - os.path.getmtime(UCDP_JSON)) < TTL_CONFLICT:
        with open(UCDP_JSON) as f:
            payload = json.load(f)
        return payload["data"], payload["_meta"]

    # Constrained-environment guard (checked BEFORE any fetch): on a small cloud box
    # (SKIP_HEAVY_FETCH=1) we must never run the slow ~5-min API paginate or the
    # 239 MB CSV download on a web request. If a committed/seeded summary exists,
    # serve it even when older than TTL_CONFLICT. UCDP GED is annual, so the seed
    # only needs refreshing when a new release lands — done locally (with the API
    # token) and committed; see DEPLOY.md "Switching to the UCDP API".
    if os.environ.get("SKIP_HEAVY_FETCH") and os.path.exists(UCDP_JSON):
        with open(UCDP_JSON) as f:
            payload = json.load(f)
        meta = dict(payload.get("_meta", {}))
        meta["from_cache"] = True
        meta["note"] = "served from seeded summary (heavy fetch skipped)"
        return payload["data"], meta

    # Authenticated API path (local refresh). When UCDP_API_TOKEN is set and we are
    # NOT in the constrained cloud env, pull only the recent CONFLICT_WINDOW years
    # from the REST API (a few MB of JSON, not the 239 MB CSV) and aggregate into the
    # SAME summary shape as the CSV path. This is how you regenerate the seed:
    # run locally with the token, then commit the refreshed cache/ucdp_conflict.json.
    token = os.environ.get("UCDP_API_TOKEN")
    if token:
        try:
            rows, max_year = _fetch_ucdp_api(token)
            agg, meta = _aggregate_conflict(rows, countries, max_year,
                                            source_url=UCDP_API_BASE, via="api")
            with open(UCDP_JSON, "w") as f:
                json.dump({"data": agg, "_meta": meta}, f)
            return agg, meta
        except Exception as e:  # noqa — fall through to CSV on any API trouble
            print(f"  UCDP API fetch failed ({e}); falling back to CSV")

    # Ensure the raw CSV is present (download + unzip once).
    if not os.path.exists(UCDP_CSV):
        try:
            import zipfile
            zpath = os.path.join(CACHE, "ged.zip")
            req = urllib.request.Request(UCDP_GED_URL, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=180) as r, open(zpath, "wb") as out:
                out.write(r.read())
            with zipfile.ZipFile(zpath) as z:
                name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
                with z.open(name) as src, open(UCDP_CSV, "wb") as dst:
                    dst.write(src.read())
        except Exception as e:  # noqa
            return {}, {"source": UCDP_VERSION, "ok": False, "error": str(e)}

    import csv as _csv
    try:
        with open(UCDP_CSV, newline="", encoding="utf-8") as f:
            rows = list(_csv.DictReader(f))
        max_year = max(int(r["year"]) for r in rows)
        agg, meta = _aggregate_conflict(rows, countries, max_year,
                                        source_url=UCDP_GED_URL, via="csv")
    except Exception as e:  # noqa
        return {}, {"source": UCDP_VERSION, "ok": False, "error": str(e)}
    with open(UCDP_JSON, "w") as f:
        json.dump({"data": agg, "_meta": meta}, f)
    return agg, meta


# ---------------------------------------------------------------------------
# Coups — Powell & Thyne Coup d'État Dataset (open, maintained, no key)
# ---------------------------------------------------------------------------

COUPS_URL = "http://www.uky.edu/~clthyn2/coup_data/powell_thyne_coups_final.txt"
COUPS_SOURCE = "Powell & Thyne Coup d'État Dataset"
COUPS_WINDOW = 15  # coups are rarer than conflict events -> a longer memory


def get_coups(countries):
    """ISO3 -> {value, year} weighted coup count over the recent window.
    coup==2 successful (weight 2), coup==1 attempted (weight 1). Returns (data, meta)."""
    data, meta = fetch_text(COUPS_URL, "powell_thyne_coups", TTL_CONFLICT)
    full, split = _conflict_name_maps(countries)

    def lookup(name):
        nm = _norm_country(name)
        return CONFLICT_OVERRIDES.get(nm) or full.get(nm) or split.get(nm)

    out = {iso: {"value": 0, "successful": 0, "attempted": 0} for iso in countries}
    if not meta.get("ok") or not data:
        return out, meta
    import csv as _csv
    rows = list(_csv.DictReader(data.splitlines(), delimiter="\t"))
    years = [int(r["year"]) for r in rows if r.get("year", "").isdigit()]
    if not years:
        return out, {**meta, "ok": False, "error": "no parsable rows"}
    max_year = max(years)
    since = max_year - COUPS_WINDOW + 1
    unmatched = 0
    for r in rows:
        if not r.get("year", "").strip().isdigit():
            continue
        y = int(r["year"])
        if y < since:
            continue
        iso = lookup((r.get("country") or "").strip().strip('"'))
        if iso is None or iso not in out:
            unmatched += 1
            continue
        kind = (r.get("coup") or "").strip()
        if kind == "2":
            out[iso]["successful"] += 1
            out[iso]["value"] += 2
        elif kind == "1":
            out[iso]["attempted"] += 1
            out[iso]["value"] += 1
    window = f"{since}–{max_year}"
    for iso in out:
        out[iso]["year"] = window
    meta = {**meta, "source": COUPS_SOURCE, "url": COUPS_URL, "ok": True,
            "window": window, "latest_year": max_year, "unmatched_rows": unmatched}
    return out, meta


# ---------------------------------------------------------------------------
# Regime type — Our World in Data 'political-regime' (V-Dem Regimes of the World)
# ---------------------------------------------------------------------------

REGIME_URL = ("https://ourworldindata.org/grapher/political-regime.csv"
              "?csvType=full&useColumnShortNames=true")
REGIME_SOURCE = "V-Dem Regimes of the World (via Our World in Data)"


def get_regime(countries):
    """ISO3 -> {value, year} latest regime category 0–3. Returns (data, meta)."""
    data, meta = fetch_text(REGIME_URL, "owid_political_regime", TTL_CONFLICT)
    out = {}
    if not meta.get("ok") or not data:
        return out, meta
    import csv as _csv
    rdr = _csv.DictReader(data.splitlines())
    latest = {}
    col = "regime_row_owid"
    for r in rdr:
        iso = (r.get("code") or "").strip()
        val = (r.get(col) or "").strip()
        if len(iso) != 3 or not val.isdigit():
            continue
        if iso not in countries:
            continue
        y = int(r.get("year", 0) or 0)
        if iso not in latest or y > latest[iso][0]:
            latest[iso] = (y, int(val))
    for iso, (y, v) in latest.items():
        out[iso] = {"value": v, "year": y}
    meta = {**meta, "source": REGIME_SOURCE, "url": REGIME_URL, "ok": True,
            "countries": len(out)}
    return out, meta


# ---------------------------------------------------------------------------
# Elections — Wikidata structured calendar (open SPARQL, no key)
# Treated as a TEMPORAL FLASHPOINT flag, not a structural indicator: an election
# inside the anticipation window raises the live alert (the framework treats
# elections as moments where grievance is mobilised), never the standing score.
# ---------------------------------------------------------------------------

ELECTION_WDQS = "https://query.wikidata.org/sparql"
ELECTION_SOURCE = "Wikidata (national election calendar)"
TTL_ELECTION = 3 * 24 * 3600
ANTICIPATION_DAYS = 180      # within 6 months of a vote -> 'anticipation' window
ELECTION_LOOKAHEAD_DAYS = 300
ELECTION_BOOST_CAP = 8       # max alert nudge (closer election -> larger), capped
# Sub-national / non-national election types we exclude (framework cares about
# national-scale flashpoints).
ELECTION_DENY = ("local", "municipal", "regional", "by-election", "mayoral",
                 "gubernatorial", "provincial", "council", "city", "county",
                 "district", "state election", "village", "recall", "primary")


def get_elections(countries):
    """ISO3 -> election calendar. Tries Wikidata (structured) first; if WDQS is
    unavailable, falls back to the Wikipedia year-list parser. Either way the
    election layer is a temporal flashpoint flag, never a structural indicator."""
    data, meta = _elections_wikidata(countries)
    if data and meta.get("ok"):
        return data, meta
    try:
        wdata, wmeta = elections.harvest(countries, CACHE,
                                         ANTICIPATION_DAYS, ELECTION_LOOKAHEAD_DAYS)
    except Exception as e:  # noqa - elections must never break the dashboard
        return data, {**meta, "fallback_error": str(e)}
    if wdata:
        wmeta["primary"] = "wikidata unavailable → wikipedia fallback"
        return wdata, wmeta
    return data, meta


def _elections_wikidata(countries):
    """ISO3 -> {next_date, days_until, label, type, anticipation, upcoming[]}.
    Sourced from Wikidata; returns empty if WDQS is unavailable."""
    today = datetime.date.today()
    end = today + datetime.timedelta(days=ELECTION_LOOKAHEAD_DAYS)
    q = (
        "SELECT ?electionLabel ?date ?iso ?typeLabel WHERE {"
        " ?election wdt:P31/wdt:P279* wd:Q40231 ; wdt:P585 ?date ; wdt:P17 ?country ."
        " ?country wdt:P298 ?iso ."
        ' ?election rdfs:label ?electionLabel . FILTER(LANG(?electionLabel)="en")'
        " ?election wdt:P31 ?type ."
        ' ?type rdfs:label ?typeLabel . FILTER(LANG(?typeLabel)="en")'
        f' FILTER(?date >= "{today.isoformat()}"^^xsd:dateTime'
        f' && ?date <= "{end.isoformat()}"^^xsd:dateTime) }}'
    )
    url = ELECTION_WDQS + "?" + urllib.parse.urlencode({"query": q, "format": "json"})
    # Short timeout + negative cache: a down WDQS must not slow rebuilds. Once the
    # cache is seeded (or WDQS recovers) it's read instantly for TTL_ELECTION.
    text, meta = fetch_text(url, "wikidata_elections", TTL_ELECTION,
                            retries=1, timeout=15, neg_ttl=20 * 60)
    out = {}
    if not meta.get("ok") or not text:
        return out, {"source": ELECTION_SOURCE, "ok": False,
                     "error": meta.get("error", "WDQS unavailable"),
                     "note": "Election layer present but empty until WDQS responds."}
    try:
        rows = json.loads(text)["results"]["bindings"]
    except (ValueError, KeyError) as e:
        return out, {"source": ELECTION_SOURCE, "ok": False, "error": str(e)}

    per = {}
    for r in rows:
        iso = r.get("iso", {}).get("value")
        if not iso or iso not in countries:
            continue
        typ = (r.get("typeLabel", {}).get("value", "") or "").lower()
        if any(bad in typ for bad in ELECTION_DENY):
            continue
        ds = r.get("date", {}).get("value", "")[:10]
        try:
            edate = datetime.date.fromisoformat(ds)
        except ValueError:
            continue
        if edate < today:
            continue
        per.setdefault(iso, []).append({
            "date": ds, "days_until": (edate - today).days,
            "label": r.get("electionLabel", {}).get("value", "")[:90],
            "type": r.get("typeLabel", {}).get("value", "")[:40],
        })
    for iso, evs in per.items():
        evs.sort(key=lambda e: e["days_until"])
        nxt = evs[0]
        out[iso] = {
            "next_date": nxt["date"], "days_until": nxt["days_until"],
            "label": nxt["label"], "type": nxt["type"],
            "anticipation": nxt["days_until"] <= ANTICIPATION_DAYS,
            "upcoming": evs[:4],
        }
    meta = {"source": ELECTION_SOURCE, "url": "https://query.wikidata.org/", "ok": True,
            "window": f"{today.isoformat()}–{end.isoformat()}",
            "countries_with_upcoming": len(out),
            "in_anticipation": sum(1 for v in out.values() if v["anticipation"]),
            "anticipation_window_days": ANTICIPATION_DAYS,
            "from_cache": meta.get("from_cache")}
    return out, meta


def election_boost(elec):
    """Alert nudge from an upcoming election: closer = larger, capped. Only inside
    the anticipation window. Returns 0 if no election or out of window."""
    if not elec or not elec.get("anticipation"):
        return 0.0
    d = elec.get("days_until", ANTICIPATION_DAYS)
    frac = max(0.0, 1.0 - d / ANTICIPATION_DAYS)
    return round(ELECTION_BOOST_CAP * frac, 1)


# ---------------------------------------------------------------------------
# Scoring — the Pathways Susceptibility Index
# ---------------------------------------------------------------------------

def percentile_normalise(values, direction):
    """Map raw indicator values to a 0–100 'stress contribution' using PERCENTILE
    RANK across the observed country set. Percentile rank is robust to outliers
    (a handful of micro-economies can't blow out the scale the way min-max lets
    them) and is directly interpretable: 'this country sits in the Nth percentile
    of global water stress'.

    direction +1: a higher raw value -> higher stress (higher percentile).
    direction -1: a higher raw value -> lower stress (percentile inverted)."""
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    n = len(present)
    norm = {i: None for i in range(len(values))}
    if n == 0:
        return norm
    if n == 1:
        norm[present[0][0]] = 50.0
        return norm
    ordered = sorted(present, key=lambda t: t[1])
    # average-rank percentile to handle ties fairly
    j = 0
    while j < n:
        k = j
        while k + 1 < n and ordered[k + 1][1] == ordered[j][1]:
            k += 1
        avg_rank = (j + k) / 2.0  # 0-based average rank of this tied block
        pct = (avg_rank / (n - 1)) * 100
        if direction < 0:
            pct = 100 - pct
        for idx in range(j, k + 1):
            norm[ordered[idx][0]] = round(pct, 1)
        j = k + 1
    return norm


def log_minmax_normalise(values, direction):
    """For heavy-tailed, zero-inflated COUNT data (e.g. conflict fatalities),
    percentile rank misleads: with many countries at exactly zero, any tiny
    nonzero count leaps above the whole zero block regardless of magnitude.
    Instead we take log10(value+1) and min-max scale it, so 0 stays at the floor
    and stress grows with the ORDER OF MAGNITUDE of the count."""
    import math
    present = [(i, v) for i, v in enumerate(values) if v is not None]
    norm = {i: None for i in range(len(values))}
    if not present:
        return norm
    logs = [(i, math.log10(v + 1)) for i, v in present]
    lo = min(x for _, x in logs)
    hi = max(x for _, x in logs)
    span = (hi - lo) or 1.0
    for i, x in logs:
        s = (x - lo) / span
        if direction < 0:
            s = 1 - s
        norm[i] = round(s * 100, 1)
    return norm


# V-Dem "Regimes of the World" categories and their framework-faithful stress.
# Inverted-U: hybrid/mixed regimes are the unstable middle; the consolidated
# extremes (closed autocracy via repression, liberal democracy via responsive
# institutions) are comparatively stable.
REGIME_LABELS = {0: "Closed autocracy", 1: "Electoral autocracy",
                 2: "Electoral democracy", 3: "Liberal democracy"}
REGIME_STRESS = {0: 45.0, 1: 90.0, 2: 62.0, 3: 12.0}


def regime_normalise(values):
    norm = {i: None for i in range(len(values))}
    for i, v in enumerate(values):
        if v is None:
            continue
        norm[i] = REGIME_STRESS.get(int(v))
    return norm


def normalise(values, direction, method):
    if method == "regime":
        return regime_normalise(values)
    if method == "log":
        return log_minmax_normalise(values, direction)
    return percentile_normalise(values, direction)


def build_payload():
    countries = get_countries()
    iso_list = list(countries.keys())

    # UCDP conflict aggregates (fills the recent-conflict-history antecedent and
    # supplies observed-instability context).
    conflict, conflict_meta = get_conflict(countries)
    # Other systemic antecedents: coups (Powell & Thyne) and regime type (V-Dem).
    coups, coups_meta = get_coups(countries)
    regime, regime_meta = get_regime(countries)
    # Election calendar (Wikidata) — a temporal flashpoint flag, not a structural
    # indicator. Degrades gracefully to empty if WDQS is unavailable.
    elections, election_meta = get_elections(countries)

    # Pull each indicator. World Bank indicators come from the API; the UCDP,
    # coup and regime indicators come from the aggregates fetched above.
    SPECIAL = {
        "ucdp": (lambda iso: {"value": conflict[iso]["recent_deaths"],
                              "year": conflict[iso]["window"]}, conflict, conflict_meta),
        "coups": (lambda iso: {"value": coups[iso]["value"], "year": coups[iso].get("year")},
                  coups, coups_meta),
        "regime": (lambda iso: regime[iso], regime, regime_meta),
    }
    indicator_data = {}
    indicator_meta = {}
    for ind in INDICATORS:
        src = ind.get("source")
        if src in SPECIAL:
            getter, store, meta = SPECIAL[src]
            indicator_data[ind["code"]] = {iso: getter(iso) for iso in store}
            indicator_meta[ind["code"]] = meta
            continue
        vals, meta = get_indicator(ind["code"])
        indicator_data[ind["code"]] = vals
        indicator_meta[ind["code"]] = meta
        if not meta.get("from_cache"):
            time.sleep(0.4)  # be polite to the World Bank API, avoid rate-limiting

    # Normalise each indicator across countries -> per-country stress 0..100.
    norm_by_code = {}
    for ind in INDICATORS:
        code = ind["code"]
        raw = [indicator_data[code].get(iso, {}).get("value") for iso in iso_list]
        nm = normalise(raw, ind["dir"], ind.get("norm", "percentile"))
        norm_by_code[code] = {iso_list[i]: nm.get(i) for i in range(len(iso_list))}

    # Live disturbances, grouped by ISO3.
    disturbances, dist_meta = get_disturbances()
    dist_by_iso = {}
    for d in disturbances:
        if d["iso3"]:
            dist_by_iso.setdefault(d["iso3"], []).append(d)

    # Live news signal — water-linked instability chatter (rolling 7-day window).
    # Complements UCDP: UCDP is the deep, lagged record; this is the real-time skim.
    try:
        news_data, news_meta = news.harvest(
            countries, store_path=os.path.join(CACHE, "news_store.json"))
    except Exception as e:  # noqa - news must never break the dashboard
        news_data, news_meta = {}, {"ok": False, "error": str(e)}
    # Log-normalise the (heavy-tailed, zero-inflated) media signal to 0–100.
    iso_idx = {iso: i for i, iso in enumerate(iso_list)}
    media_raw = [news_data.get(iso, {}).get("signal_raw", 0) for iso in iso_list]
    media_norm_list = log_minmax_normalise(media_raw, +1)
    media_norm = {iso: media_norm_list.get(iso_idx[iso]) for iso in iso_list}

    stage_codes = {}
    for ind in INDICATORS:
        stage_codes.setdefault(ind["stage"], []).append(ind["code"])

    results = []
    for iso, meta in countries.items():
        # per-stage average of available normalised indicator stresses
        stage_scores = {}
        indicators_out = {}
        for ind in INDICATORS:
            code = ind["code"]
            nval = norm_by_code[code].get(iso)
            raw = indicator_data[code].get(iso)
            src = ind.get("source", "worldbank")
            if src == "ucdp":
                src_name, src_url = "UCDP GED", UCDP_GED_URL
            elif src == "coups":
                src_name, src_url = COUPS_SOURCE, COUPS_URL
            elif src == "regime":
                src_name, src_url = REGIME_SOURCE, REGIME_URL
            else:
                src_name = f"World Bank · {code}"
                src_url = f"{WB}/country/{iso}/indicator/{code}"
            raw_val = raw["value"] if raw else None
            # human-readable label for categorical regime values
            raw_label = REGIME_LABELS.get(raw_val) if ind.get("labels") and raw_val is not None else None
            indicators_out[code] = {
                "label": ind["label"], "unit": ind["unit"], "stage": ind["stage"],
                "dir": ind["dir"], "rationale": ind["rationale"],
                "raw": raw_val,
                "raw_label": raw_label,
                "year": raw["year"] if raw else None,
                "stress": nval,
                "source": src_name, "source_url": src_url,
            }
        for stage_key, codes in stage_codes.items():
            svals = [norm_by_code[c].get(iso) for c in codes]
            svals = [v for v in svals if v is not None]
            stage_scores[stage_key] = round(sum(svals) / len(svals), 1) if svals else None

        scored = [v for v in stage_scores.values() if v is not None]
        susceptibility = round(sum(scored) / len(scored), 1) if scored else None
        # data completeness: how many of the scored indicators are present
        total_inds = len(INDICATORS)
        have = sum(1 for ind in INDICATORS if indicator_data[ind["code"]].get(iso))
        completeness = round(100 * have / total_inds)

        active = dist_by_iso.get(iso, [])
        max_alert = max([a["alert_rank"] for a in active], default=0)

        # Live news signal for this country (rolling window).
        nd = news_data.get(iso, {})
        media = {
            "signal": media_norm.get(iso),
            "water_articles": nd.get("water_articles", 0),
            "nexus_articles": nd.get("nexus_articles", 0),
            "samples": nd.get("samples", []),
            "window_days": news_meta.get("window_days", 7),
        }
        # 'Emerging' = current water→instability reporting exists for this country.
        emerging = media["nexus_articles"] >= 1

        # Election flashpoint (temporal): a vote inside the anticipation window.
        elec = elections.get(iso)
        e_boost = election_boost(elec)

        # Hotspot alert = standing susceptibility ELEVATED by (a) an active physical
        # disturbance (GDACS), (b) a small nudge from a live water→instability news
        # nexus, and (c) a small nudge if a national election is imminent. Each live
        # nudge is capped well below a physical disturbance, and resists over-counting.
        alert_score = None
        if susceptibility is not None:
            dist_boost = {0: 0, 1: 6, 2: 16, 3: 28}[max_alert]
            media_boost = min(8, media["nexus_articles"] * 4)
            alert_score = round(min(100, susceptibility + dist_boost + media_boost + e_boost), 1)

        # Observed instability (stage 7) — UCDP latest-year figures, shown as
        # context/validation only. Deliberately NOT fed into susceptibility, to
        # keep the tool descriptive rather than a predictor of its own outcome.
        cf = conflict.get(iso, {})
        observed = {
            "latest_year": cf.get("latest_year"),
            "latest_deaths": cf.get("latest_deaths"),
            "latest_events": cf.get("latest_events"),
            "window": cf.get("window"),
            "window_deaths": cf.get("recent_deaths"),
            "window_events": cf.get("recent_events"),
            "source": UCDP_VERSION, "source_url": UCDP_GED_URL,
        } if cf else None

        # Actors (framework stage 6). Two complementary, clearly-labelled streams:
        #   - UCDP roster: the named ARMED actors recorded in lethal events (hard,
        #     lagged), classified into the framework's actor typology.
        #   - News groups: named non-state groups currently surfacing in reporting
        #     (soft, live). Enrichment, not ground truth.
        # The framework's NON-VIOLENT actors (activists, dissidents, community/
        # religious leaders, conflict entrepreneurs) remain analyst-populated.
        actors = {
            "ucdp_roster": cf.get("actors", []),
            "ucdp_actor_count": cf.get("actor_count", 0),
            "window": cf.get("window"),
            "news_groups": nd.get("groups", []),
            "source": UCDP_VERSION, "source_url": UCDP_GED_URL,
            "analyst_categories": [
                "Activists, dissidents & political opposition",
                "Community, group & religious leaders",
                "Conflict entrepreneurs",
            ],
        }

        results.append({
            **meta,
            "susceptibility": susceptibility,
            "stages": stage_scores,
            "indicators": indicators_out,
            "completeness": completeness,
            "active_disturbances": active,
            "observed_instability": observed,
            "actors": actors,
            "media": media,
            "emerging": emerging,
            "election": elec,
            "election_boost": e_boost,
            "max_alert_rank": max_alert,
            "alert_score": alert_score,
            "tier": tier_for(alert_score, max_alert, completeness),
        })

    # Rank: sufficiently-measured countries first (by alert score), data-sparse last.
    results.sort(key=lambda r: (
        r["tier"] != "insufficient_data",
        r["alert_score"] is not None,
        r["alert_score"] or 0,
    ), reverse=True)

    return {
        "generated_at": int(time.time()),
        "framework_stages": STAGES,
        "indicator_defs": INDICATORS,
        "data_gaps": DATA_GAPS,
        "sources": SOURCES,
        # Constants the browser uses to recompute the score live as layers toggle.
        "scoring": {
            "min_indicators": MIN_INDICATORS,
            "dist_boost": {"0": 0, "1": 6, "2": 16, "3": 28},
            "media_boost_per": 4, "media_boost_cap": 8,
            "election_boost_cap": ELECTION_BOOST_CAP,
            "anticipation_days": ANTICIPATION_DAYS,
        },
        "provenance": {
            "indicators": indicator_meta,
            "disturbances": dist_meta,
            "conflict": conflict_meta,
            "coups": coups_meta,
            "regime": regime_meta,
            "news": news_meta,
            "elections": election_meta,
        },
        "disturbances": disturbances,
        "countries": results,
        "counts": {
            "countries_scored": sum(1 for r in results if r["susceptibility"] is not None),
            "active_disturbances": len(disturbances),
            "countries_with_disturbance": len(dist_by_iso),
            "countries_with_recent_conflict": sum(
                1 for iso in conflict if conflict[iso].get("recent_deaths", 0) > 0),
            "emerging_signals": sum(1 for r in results if r.get("emerging")),
            "news_window_articles": news_meta.get("window_articles", 0),
            "election_windows": sum(1 for r in results
                                    if r.get("election") and r["election"].get("anticipation")),
        },
    }


# A country needs at least this many of the indicators present to earn a real tier,
# rather than being ranked on one or two extreme values. Declared, not hidden.
MIN_INDICATORS = 7


def tier_for(alert_score, max_alert, completeness):
    """Watch tiers. Mirrors the framework: standing susceptibility (dry timber)
    elevated by an active water disturbance (the spark). Data-sparse countries are
    quarantined as 'insufficient_data' instead of being given a false alert."""
    if alert_score is None:
        return "unknown"
    have = round(completeness / 100 * len(INDICATORS))
    if have < MIN_INDICATORS:
        return "insufficient_data"
    if max_alert >= 3 and alert_score >= 60:
        return "critical"
    if alert_score >= 70 or (max_alert >= 2 and alert_score >= 55):
        return "high"
    if alert_score >= 50 or max_alert >= 1:
        return "elevated"
    return "watch"


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

_PAYLOAD_CACHE = {"data": None, "ts": 0}


def cached_payload(force=False):
    now = time.time()
    if force or _PAYLOAD_CACHE["data"] is None or (now - _PAYLOAD_CACHE["ts"]) > TTL_DISTURBANCE:
        _PAYLOAD_CACHE["data"] = build_payload()
        _PAYLOAD_CACHE["ts"] = now
    return _PAYLOAD_CACHE["data"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/hotspots":
                force = "refresh" in self.path
                self._send(200, cached_payload(force=force)); return
            if path == "/api/sources":
                self._send(200, {"sources": SOURCES, "data_gaps": DATA_GAPS,
                                 "indicator_defs": INDICATORS, "framework_stages": STAGES}); return
            if path == "/api/health":
                self._send(200, {"ok": True}); return
            if path == "/api/world":
                # Country geometry (world-atlas 110m TopoJSON) served from our own
                # cache so the map needs no third-party CORS request.
                wpath = ensure_world_topojson()
                if wpath:
                    with open(wpath, "rb") as f:
                        self._send(200, f.read(), "application/json")
                else:
                    self._send(502, {"error": "world geometry unavailable"})
                return
            if path == "/api/rivers":
                rpath = ensure_rivers()
                if rpath:
                    with open(rpath, "rb") as f:
                        self._send(200, f.read(), "application/json")
                else:
                    self._send(502, {"error": "rivers geometry unavailable"})
                return
            # static
            rel = path.lstrip("/") or "index.html"
            full = os.path.join(WEB, rel)
            if os.path.isdir(full):
                full = os.path.join(full, "index.html")
            if not os.path.abspath(full).startswith(WEB) or not os.path.exists(full):
                self._send(404, {"error": "not found"}); return
            ctype = {
                ".html": "text/html", ".js": "application/javascript",
                ".css": "text/css", ".json": "application/json",
                ".svg": "image/svg+xml",
            }.get(os.path.splitext(full)[1], "application/octet-stream")
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype)
        except Exception as e:  # noqa
            self._send(500, {"error": str(e)})


def main():
    port = int(os.environ.get("PORT", "8765"))
    print(f"HEADWATERS — A Global Water–Conflict Susceptibility Watch")
    print(f"  -> http://localhost:{port}")
    seed_cache()  # restore committed pre-computed data into cache/ if missing
    print("Warming live data streams (World Bank + GDACS)...")
    try:
        p = cached_payload(force=True)
        print(f"  scored {p['counts']['countries_scored']} countries; "
              f"{p['counts']['active_disturbances']} active water disturbances; "
              f"{p['counts']['countries_with_disturbance']} countries with a live disturbance.")
    except Exception as e:  # noqa
        print(f"  warm-up warning: {e}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
