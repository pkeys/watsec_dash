#!/usr/bin/env python3
"""
Wikipedia election-calendar harvester (stdlib only).

A fallback / alternative to the Wikidata SPARQL calendar (which is prone to
outages). Parses the English Wikipedia "List of elections in <year>" pages for
upcoming national elections, deriving the demonym→country map from Wikipedia's
own "List of adjectival and demonymic forms…" page so nothing is hand-invented.

Returns the same per-country structure the dashboard expects, so it slots into
the election toggle unchanged. Degrades gracefully (empty) if Wikipedia is
unreachable.
"""

import datetime
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error

UA = "Headwaters/1.0 (water-security research; pkeys.earth@gmail.com)"
WIKI_API = "https://en.wikipedia.org/w/api.php"
SOURCE = "Wikipedia (national election calendar)"
TTL = 24 * 3600  # election pages move slowly; refresh daily

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}

# Election-type phrases stripped from a title to isolate the demonym prefix.
TYPE_PHRASES = ["general election", "parliamentary election", "presidential election",
                "legislative election", "local elections", "municipal elections",
                "municipal election", "regional election", "by-election",
                "senate election", "referendum", "elections", "election"]

# Sub-national / non-national election types we exclude.
DENY = ("municipal", "local", "regional", "by-election", "state ", "provincial",
        "gubernatorial", "council", "assembly", "barangay", "metropolitan",
        "mayoral", "parliament ", "legislative council", "recall")

# Demonyms the Wikipedia demonym table renders awkwardly; supply the few we miss.
DEMONYM_OVERRIDE = {
    "cape verdean": "CPV", "são toméan": "STP", "sao tomean": "STP",
    "gambian": "GMB", "bosnian": "BIH", "kosovan": "XKX", "kosovar": "XKX",
    "bissau-guinean": "GNB",
}


def _fetch_wikitext(page, cache_dir):
    """Cached fetch of a page's wikitext via the MediaWiki API. None on failure
    or missing page."""
    key = "wiki_" + re.sub(r"[^A-Za-z0-9]", "_", page) + ".txt"
    path = os.path.join(cache_dir, key)
    now = time.time()
    if os.path.exists(path) and (now - os.path.getmtime(path)) < TTL:
        with open(path, encoding="utf-8") as f:
            return f.read()
    url = WIKI_API + "?" + urllib.parse.urlencode({
        "action": "parse", "page": page, "prop": "wikitext", "format": "json"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        return None
    if "parse" not in d:
        return None  # e.g. missingtitle
    text = d["parse"]["wikitext"]["*"]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass
    return text


def _clean(s):
    s = re.sub(r"<ref.*?(/>|</ref>)", "", s, flags=re.S)
    s = re.sub(r"\{\{cslist\|", "", s)
    s = re.sub(r"\{\{.*?\}\}", "", s)
    s = re.sub(r"\[\[[^\]\|]*\|([^\]]*)\]\]", r"\1", s)  # [[A|B]] -> B
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)            # [[A]]  -> A
    return s.replace("}}", "").strip(" |")


def _norm(s):
    s = re.sub(r"\(.*?\)", "", s).lower().strip()
    s = s.replace("&", "and").replace(".", "").replace(",", "").replace("'", "")
    return re.sub(r"\s+", " ", s).strip()


def _country_iso_maps(countries):
    full, split = {}, {}
    for iso, meta in countries.items():
        full[_norm(meta["name"])] = iso
        k = _norm(meta["name"].split(",")[0])
        if k not in split and k != "congo":
            split[k] = iso
    ov = {"cape verde": "CPV", "cabo verde": "CPV", "ivory coast": "CIV",
          "democratic republic of the congo": "COD", "dr congo": "COD",
          "republic of the congo": "COG", "congo": "COG", "east timor": "TLS",
          "timor-leste": "TLS", "sao tome and principe": "STP", "south korea": "KOR",
          "north korea": "PRK", "russia": "RUS", "syria": "SYR", "laos": "LAO",
          "brunei": "BRN", "myanmar": "MMR", "the gambia": "GMB", "gambia": "GMB",
          "bolivia": "BOL", "venezuela": "VEN", "tanzania": "TZA", "moldova": "MDA",
          "kyrgyzstan": "KGZ", "turkey": "TUR", "kosovo": "XKX"}

    def lookup(name):
        n = _norm(name)
        return ov.get(n) or full.get(n) or split.get(n)
    return lookup


def _demonym_map(cache_dir, iso_lookup):
    """Build {demonym_or_country_lower: ISO3} from Wikipedia's demonym list."""
    txt = _fetch_wikitext(
        "List_of_adjectival_and_demonymic_forms_for_countries_and_nations", cache_dir)
    out = dict(DEMONYM_OVERRIDE)
    if not txt:
        return out
    for line in txt.splitlines():
        if not line.strip().startswith("|") or "||" not in line:
            continue
        cells = line.split("||")
        if len(cells) < 2:
            continue
        country = _clean(cells[0])
        if not country:
            continue
        iso = iso_lookup(country)
        if not iso:
            continue
        out[country.lower()] = iso
        for adj in re.split(r"[,/]| or ", _clean(cells[1])):
            adj = adj.strip().lower()
            if len(adj) >= 4:
                out.setdefault(adj, iso)
    return out


def harvest(countries, cache_dir, anticipation_days=180, lookahead_days=300):
    today = datetime.date.today()
    iso_lookup = _country_iso_maps(countries)
    demo = _demonym_map(cache_dir, iso_lookup)

    per = {}
    pages_ok = 0
    for year in (today.year, today.year + 1):
        txt = _fetch_wikitext("List_of_elections_in_%d" % year, cache_dir)
        time.sleep(0.2)
        if not txt:
            continue
        pages_ok += 1
        for m in re.finditer(r"\[\[(\d{4})\s+([^\]]+?)\]\](?:,\s*(\d{1,2})\s+(\w+))?", txt):
            yr, title, day, mon = m.groups()
            low = title.lower()
            if "election" not in low and "referendum" not in low:
                continue
            if any(b in low for b in DENY):
                continue
            if not (day and mon in MONTHS):
                continue  # only dated entries can anchor a flashpoint window
            prefix = low
            for t in TYPE_PHRASES:
                if prefix.endswith(t):
                    prefix = prefix[:-len(t)].strip()
                    break
            iso = demo.get(prefix) or iso_lookup(prefix)
            if not iso or iso not in countries:
                continue
            try:
                edate = datetime.date(int(yr), MONTHS[mon], int(day))
            except ValueError:
                continue
            if edate < today or (edate - today).days > lookahead_days:
                continue
            per.setdefault(iso, []).append({
                "date": edate.isoformat(), "days_until": (edate - today).days,
                "label": _clean(title)[:90],
                "type": next((t for t in TYPE_PHRASES if t in low), "election"),
            })

    out = {}
    for iso, evs in per.items():
        evs.sort(key=lambda e: e["days_until"])
        nxt = evs[0]
        out[iso] = {
            "next_date": nxt["date"], "days_until": nxt["days_until"],
            "label": nxt["label"], "type": nxt["type"],
            "anticipation": nxt["days_until"] <= anticipation_days,
            "upcoming": evs[:4],
        }
    meta = {
        "source": SOURCE, "url": "https://en.wikipedia.org/wiki/List_of_elections_in_%d" % today.year,
        "ok": pages_ok > 0, "pages_ok": pages_ok,
        "window": f"{today.isoformat()}–{(today + datetime.timedelta(days=lookahead_days)).isoformat()}",
        "countries_with_upcoming": len(out),
        "in_anticipation": sum(1 for v in out.values() if v["anticipation"]),
        "anticipation_window_days": anticipation_days,
    }
    return out, meta


if __name__ == "__main__":
    wb = json.load(open(os.path.join(os.path.dirname(__file__), "cache", "wb_countries.json")))["data"][1]
    cs = {c["id"]: {"name": c["name"]} for c in wb if c["region"]["value"] != "Aggregates"}
    d, m = harvest(cs, os.path.join(os.path.dirname(__file__), "cache"))
    print("meta:", m)
    for iso, v in sorted(d.items(), key=lambda kv: kv[1]["days_until"])[:20]:
        print(f"  {iso} {v['next_date']} (+{v['days_until']}d) {v['label']}")
