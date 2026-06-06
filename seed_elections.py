#!/usr/bin/env python3
"""One-off: patiently fetch the Wikidata election calendar and seed the server
cache (cache/wikidata_elections.txt). WDQS is rate-limited (1 req/min) and
currently flaky, so we retry with long backoff. Lean query to avoid 504s."""
import urllib.request, urllib.parse, json, time, os, datetime

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
today = datetime.date.today()
end = today + datetime.timedelta(days=300)
# Lean: single property-path triple, label via rdfs:label (cheaper than the
# label service), no ORDER BY. Type label kept so we can drop local elections.
q = f'''SELECT ?electionLabel ?date ?iso ?typeLabel WHERE {{
  ?election wdt:P31/wdt:P279* wd:Q40231 ;
            wdt:P585 ?date ;
            wdt:P17 ?country .
  ?country wdt:P298 ?iso .
  ?election rdfs:label ?electionLabel . FILTER(LANG(?electionLabel)="en")
  ?election wdt:P31 ?type . ?type rdfs:label ?typeLabel . FILTER(LANG(?typeLabel)="en")
  FILTER(?date >= "{today.isoformat()}"^^xsd:dateTime && ?date <= "{end.isoformat()}"^^xsd:dateTime)
}}'''
url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": q, "format": "json"})
req = urllib.request.Request(url, headers={
    "User-Agent": "GWSC-Pathways-Dashboard/1.0 (research; pkeys.earth@gmail.com)",
    "Accept": "application/sparql-results+json"})
for attempt in range(5):
    try:
        body = urllib.request.urlopen(req, timeout=75).read().decode("utf-8")
        json.loads(body)  # validate
        with open(os.path.join(CACHE, "wikidata_elections.txt"), "w", encoding="utf-8") as f:
            f.write(body)
        n = len(json.loads(body)["results"]["bindings"])
        print(f"SEEDED ok: {n} rows -> cache/wikidata_elections.txt")
        break
    except Exception as e:
        print(f"attempt {attempt}: {str(e)[:80]}")
        if attempt < 4:
            time.sleep(65)
else:
    print("FAILED to seed (WDQS unavailable). Integration will populate when WDQS recovers.")
