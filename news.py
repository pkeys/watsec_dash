#!/usr/bin/env python3
"""
Lightweight news-signal harvester for the Pathways dashboard.

Inspired by the Aegis RSS harvester, but rebuilt with the Python standard
library only (no feedparser / requests) to keep the project dependency-free,
and narrowed to the Pathways framework's concern: water-linked instability.

Role in the system — a REAL-TIME complement to UCDP:
  * UCDP GED  = recorded organised violence, authoritative but lagged (to 2024).
                Feeds the STRUCTURAL susceptibility score (conflict history).
  * News feed = current media signal of water + instability, fast but noisy and
                media-biased. It is NOT folded into the structural score; it
                nudges the live ALERT and flags 'emerging' situations — chatter
                that may precede a recorded event.

Every counted article keeps its source, headline, link and date, so the signal
is fully traceable back to the reporting that produced it.
"""

import gzip
import html
import re
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

USER_AGENT = "Headwaters/1.0 (water-security research; pkeys.earth@gmail.com)"

# A curated, lightweight subset of free RSS feeds weighted toward water,
# humanitarian, hazard and conflict reporting — the framework's pathway.
FEEDS = [
    # Humanitarian / water / development — dense in fragile-state, country-tagged
    # reporting that sits squarely on the framework's pathway.
    ("The New Humanitarian",        "https://www.thenewhumanitarian.org/rss.xml"),
    ("Guardian Global Development",  "https://www.theguardian.com/global-development/rss"),
    ("UN News",                     "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
    ("ReliefWeb",                   "https://reliefweb.int/updates/rss.xml"),
    # Water & climate
    ("Circle of Blue (water)",      "https://www.circleofblue.org/feed/"),
    ("FloodList",                   "https://floodlist.com/feed"),
    ("Mongabay",                    "https://news.mongabay.com/feed/"),
    ("Carbon Brief",                "https://www.carbonbrief.org/feed"),
    ("Eos (AGU earth science)",     "https://eos.org/feed"),
    ("The Third Pole (water/Asia)", "https://www.thethirdpole.net/en/feed/"),
    ("Dialogue Earth",              "https://dialogue.earth/en/feed/"),
    # Conflict / world
    ("Al Jazeera",                  "https://www.aljazeera.com/xml/rss/all.xml"),
    ("The Guardian World",          "https://www.theguardian.com/world/rss"),
    ("BBC World",                   "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ("International Crisis Group",   "https://www.crisisgroup.org/rss.xml"),
    # Regional — strengthen non-Western, conflict-prone coverage
    ("allAfrica",                   "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf"),
    ("France 24 — Africa",          "https://www.france24.com/en/africa/rss"),
    ("Dawn (Pakistan)",             "https://www.dawn.com/feeds/home"),
    ("The Hindu — International",    "https://www.thehindu.com/news/international/?service=rss"),
    ("Premium Times (Nigeria)",     "https://www.premiumtimesng.com/feed"),
    ("Middle East Eye",             "https://www.middleeasteye.net/rss"),
    ("Rappler (Philippines)",       "https://www.rappler.com/feed/"),
]

# The framework's left side: a water disturbance must be present for an article
# to count toward this signal at all.
WATER_TERMS = [
    "drought", "flood", "water shortage", "water scarcity", "water crisis",
    "water stress", "dam", "river", "irrigation", "rainfall", "monsoon",
    "cyclone", "typhoon", "hurricane", "famine", "crop failure", "harvest",
    "aquifer", "groundwater", "reservoir", "desertification", "dry spell",
    "water supply", "wells", "rains", "deluge", "inundation",
]

# The framework's right side: instability / grievance / mobilisation.
INSTABILITY_TERMS = [
    "conflict", "clash", "protest", "unrest", "violence", "riot", "militia",
    "insurgency", "displaced", "displacement", "refugee", "grievance",
    "tension", "attack", "killed", "armed", "crisis", "dispute", "uprising",
    "demonstration", "rebel", "fighters", "war", "instability", "famine",
]

# Named non-state actors to surface in the Actors category as a LIGHT, current
# complement to the UCDP roster. Each: (regex term, display label, home ISO3).
# Kept deliberately small and high-confidence; this is enrichment, not ground truth.
NAMED_GROUPS = [
    ("taliban", "Taliban", "AFG"),
    ("islamic state|isis|isil|daesh", "Islamic State (IS/ISIS)", None),
    ("al[- ]?qaeda", "Al-Qaeda", None),
    ("al[- ]?shabaab", "Al-Shabaab", "SOM"),
    ("boko haram", "Boko Haram", "NGA"),
    ("iswap", "ISWAP", "NGA"),
    ("rsf|rapid support forces", "Rapid Support Forces (RSF)", "SDN"),
    ("janjaweed", "Janjaweed", "SDN"),
    ("m23", "M23", "COD"),
    ("adf|allied democratic forces", "Allied Democratic Forces (ADF)", "COD"),
    ("hezbollah", "Hezbollah", "LBN"),
    ("hamas", "Hamas", "PSE"),
    ("houthi|ansar allah", "Houthis (Ansar Allah)", "YEM"),
    ("jnim", "JNIM", "MLI"),
    ("wagner", "Wagner / Africa Corps", None),
    ("tplf", "TPLF", "ETH"),
    ("fano", "Fano", "ETH"),
    ("ola|oromo liberation", "Oromo Liberation Army (OLA)", "ETH"),
    ("cjtf", "Civilian Joint Task Force", "NGA"),
    ("pkk", "PKK", "TUR"),
    ("gang", "Armed gangs", "HTI"),
]


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def _strip_html(s):
    return _norm(html.unescape(re.sub(r"<[^>]+>", " ", s or "")))


def fetch_feed(url, timeout=12):
    """Fetch + parse one RSS/Atom feed with stdlib. Returns list of entries."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml",
    })
    raw = urllib.request.urlopen(req, timeout=timeout).read()
    if raw[:2] == b"\x1f\x8b":               # gzip magic
        raw = gzip.decompress(raw)
    raw = raw.lstrip()                        # tolerate leading whitespace / BOM
    root = ET.fromstring(raw)
    ATOM = "{http://www.w3.org/2005/Atom}"

    def text(el, *names):
        for n in names:
            child = el.find(n)
            # NB: an ElementTree element with no children is falsy — test "is not None".
            if child is not None and child.text:
                return child.text
        return ""

    entries = []
    items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    for it in items:
        title = text(it, "title", f"{ATOM}title")
        summary = text(it, "description", "summary", f"{ATOM}summary", "{http://purl.org/rss/1.0/modules/content/}encoded")
        link = text(it, "link")
        if not link:
            la = it.find(f"{ATOM}link")
            link = la.get("href") if la is not None else ""
        pub = text(it, "pubDate", "{http://purl.org/dc/elements/1.1/}date", f"{ATOM}updated", f"{ATOM}published")
        entries.append({
            "title": _norm(title), "summary": _strip_html(summary)[:400],
            "link": _norm(link), "published": _norm(pub),
        })
    return entries


# Demonyms / alternates to improve recall for conflict-prone countries whose
# news mentions rarely use the formal World Bank country name.
DEMONYMS = {
    "AFG": ["afghan", "kabul", "taliban"], "SYR": ["syrian", "damascus"],
    "YEM": ["yemeni", "houthi", "sanaa"], "SOM": ["somali", "mogadishu", "al-shabaab"],
    "SDN": ["sudanese", "khartoum", "darfur"], "SSD": ["south sudanese", "juba"],
    "ETH": ["ethiopian", "addis ababa", "tigray", "amhara", "oromia"],
    "COD": ["congolese", "kinshasa", "goma", "dr congo", "drc"],
    "NGA": ["nigerian", "abuja", "boko haram"], "MLI": ["malian", "bamako"],
    "BFA": ["burkinabe", "ouagadougou", "burkina faso"], "NER": ["nigerien", "niamey"],
    "PAK": ["pakistani", "islamabad", "karachi"], "IND": ["indian", "new delhi"],
    "UKR": ["ukrainian", "kyiv", "kiev"], "RUS": ["russian", "moscow", "kremlin"],
    "IRN": ["iranian", "tehran"], "IRQ": ["iraqi", "baghdad"],
    "MMR": ["myanmar", "burmese", "rohingya", "naypyidaw"], "KEN": ["kenyan", "nairobi"],
    "TCD": ["chadian", "n'djamena"], "MOZ": ["mozambican", "maputo", "cabo delgado"],
    "ZWE": ["zimbabwean", "harare"], "VEN": ["venezuelan", "caracas"],
    "HTI": ["haitian", "port-au-prince"], "LBN": ["lebanese", "beirut", "hezbollah"],
    "PSE": ["palestinian", "gaza", "west bank"], "ISR": ["israeli", "jerusalem", "tel aviv"],
    "BGD": ["bangladeshi", "dhaka"], "LKA": ["sri lankan", "colombo"],
    "MDG": ["malagasy", "antananarivo"], "MWI": ["malawian", "lilongwe"],
    "TZA": ["tanzanian", "dodoma", "dar es salaam"], "UGA": ["ugandan", "kampala"],
    "CMR": ["cameroonian", "yaounde"], "CAF": ["central african republic", "bangui"],
    "BDI": ["burundian", "bujumbura"], "EGY": ["egyptian", "cairo", "nile"],
}


def build_country_matchers(countries):
    """ISO3 -> compiled regex that matches the country name or a demonym.
    `countries` is the dashboard's {iso3: {name, ...}} map."""
    matchers = {}
    for iso, meta in countries.items():
        terms = set()
        name = meta["name"]
        base = re.split(r"[,(]", name)[0].strip()  # 'Congo, Dem. Rep.' -> 'Congo'
        if len(base) >= 4:
            terms.add(base.lower())
        terms.update(DEMONYMS.get(iso, []))
        terms = [re.escape(t) for t in terms if len(t) >= 4]
        if not terms:
            continue
        matchers[iso] = re.compile(r"\b(" + "|".join(sorted(terms, key=len, reverse=True)) + r")\b")
    return matchers


def _load_store(path):
    import json, os
    if path and os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (ValueError, OSError):
            pass
    return {"articles": {}}


def harvest(countries, store_path=None, window_days=7, max_per_feed=40, now=None):
    """Harvest feeds, attribute water-linked-instability articles to countries,
    and maintain a ROLLING window store so the signal accumulates across refreshes
    (a single snapshot is too sparse; UCDP is the deep record, this is the live skim).

    Returns (data, meta) where data[iso] = {water_articles, nexus_articles,
    signal_raw, samples[]} aggregated over the window."""
    import json, os
    now = now or int(time.time())
    cutoff = now - window_days * 86400
    matchers = build_country_matchers(countries)
    water_re = re.compile(r"\b(" + "|".join(re.escape(t) for t in WATER_TERMS) + r")\b")
    instab_re = re.compile(r"\b(" + "|".join(re.escape(t) for t in INSTABILITY_TERMS) + r")\b")
    group_res = [(re.compile(r"\b(" + pat + r")\b"), label, iso) for pat, label, iso in NAMED_GROUPS]

    store = _load_store(store_path)
    articles = {u: a for u, a in store.get("articles", {}).items()
                if a.get("first_seen", 0) >= cutoff}  # prune stale

    feeds_ok, feeds_failed, scanned, new_relevant = 0, [], 0, 0
    for name, url in FEEDS:
        try:
            entries = fetch_feed(url)
            feeds_ok += 1
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError,
                TimeoutError, OSError, ValueError) as e:
            feeds_failed.append({"feed": name, "error": str(e)[:80]})
            continue
        for e in entries[:max_per_feed]:
            scanned += 1
            link = e["link"]
            if not link or link in articles:
                continue
            blob = (e["title"] + " " + e["summary"]).lower()
            if not water_re.search(blob):
                continue                       # must sit on the framework's pathway
            hits = [iso for iso, rx in matchers.items() if rx.search(blob)]
            if not hits:
                continue
            groups = [{"label": label, "iso": iso}
                      for rx, label, iso in group_res if rx.search(blob)]
            articles[link] = {
                "headline": e["title"][:170], "source": name, "url": link,
                "published": e["published"][:25], "first_seen": now,
                "nexus": bool(instab_re.search(blob)), "countries": hits,
                "groups": groups,
            }
            new_relevant += 1
        time.sleep(0.2)  # polite crawl delay

    if store_path:
        try:
            with open(store_path, "w") as f:
                json.dump({"articles": articles, "updated": now}, f)
        except OSError:
            pass

    # Aggregate the rolling window per country.
    data = {iso: {"water_articles": 0, "nexus_articles": 0, "signal_raw": 0,
                  "samples": [], "groups": {}} for iso in countries}
    for a in sorted(articles.values(), key=lambda x: -x["first_seen"]):
        for iso in a["countries"]:
            if iso not in data:
                continue
            d = data[iso]
            d["water_articles"] += 1
            d["signal_raw"] += 3 if a["nexus"] else 1   # weight the water→instability nexus
            if a["nexus"]:
                d["nexus_articles"] += 1
            if len(d["samples"]) < 6:
                d["samples"].append({k: a[k] for k in
                                     ("headline", "source", "url", "published", "nexus")})
            # named non-state groups appearing in this country's coverage
            for g in a.get("groups", []):
                d["groups"][g["label"]] = d["groups"].get(g["label"], 0) + 1
    # compact groups -> sorted list of {label, mentions}
    for iso, d in data.items():
        d["groups"] = [{"label": k, "mentions": v} for k, v in
                       sorted(d["groups"].items(), key=lambda kv: -kv[1])]

    meta = {
        "harvested_at": now, "window_days": window_days,
        "feeds_total": len(FEEDS), "feeds_ok": feeds_ok, "feeds_failed": feeds_failed,
        "articles_scanned": scanned, "new_relevant": new_relevant,
        "window_articles": len(articles),
        "water_terms": len(WATER_TERMS), "instability_terms": len(INSTABILITY_TERMS),
    }
    return data, meta


if __name__ == "__main__":
    # quick manual test
    import json, urllib.request as u
    wb = json.load(u.urlopen("https://api.worldbank.org/v2/country?format=json&per_page=400"))[1]
    cs = {c["id"]: {"name": c["name"]} for c in wb if c["region"]["value"] != "Aggregates"}
    d, m = harvest(cs, store_path="cache/news_store.json")
    print("meta:", m)
    top = sorted(((iso, v["signal_raw"], v["water_articles"], v["nexus_articles"])
                  for iso, v in d.items() if v["signal_raw"] > 0), key=lambda t: -t[1])[:15]
    for iso, sig, wa, nx in top:
        print(f"  {iso} signal={sig} water={wa} nexus={nx}")
