#!/usr/bin/env python3
"""Check the size of the abrogati collection before downloading."""
import urllib.request
import urllib.parse

BASE_API = "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1"
UA = "NormattivaClient/1.0 (research; contact redazione@normattiva.it)"

# Collection name for abrogated laws
COLL_NAME = "Atti normativi abrogati (in originale)"

params = urllib.parse.urlencode({
    "nome": COLL_NAME,
    "formato": "AKN",
    "formatoRichiesta": "O",
})
url = f"{BASE_API}/api/v1/collections/download/collection-preconfezionata?{params}"

print(f"Probing: {url[:100]}...")

req = urllib.request.Request(url, headers={
    "User-Agent": UA,
    "Referer": "https://dati.normattiva.it/",
    "Accept": "*/*",
}, method="HEAD")

try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"Status: {r.status}")
        for key, val in r.headers.items():
            print(f"  {key}: {val}")
except urllib.error.HTTPError as e:
    print(f"HEAD HTTP {e.code}:")
    for key, val in e.headers.items():
        print(f"  {key}: {val}")
except Exception as e:
    print(f"Error: {e}")
