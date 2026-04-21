#!/usr/bin/env python3
"""Fetch one Constitution article to see HTML structure."""
import urllib.request
import http.cookiejar
import re

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
NORM_BASE = "https://www.normattiva.it"

# Use a cookie jar for session management
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Step 1: Load the main page to get session cookie
main_url = f"{NORM_BASE}/uri-res/N2Ls?urn:nir:stato:costituzione:1947-12-27"
req0 = urllib.request.Request(main_url, headers={
    "User-Agent": UA, "Accept": "text/html,*/*",
})
with opener.open(req0, timeout=30) as r:
    _ = r.read()
    print(f"Main page cookies: {[(c.name, c.value[:20]) for c in cj]}")

# Step 2: Fetch the article
path = "/atto/caricaArticolo?art.versione=1&art.idGruppo=1&art.flagTipoArticolo=0&art.codiceRedazionale=047U0001&art.idArticolo=1&art.idSottoArticolo=1&art.idSottoArticolo1=10&art.dataPubblicazioneGazzetta=1947-12-27&art.progressivo=0&"
url = NORM_BASE + path

req = urllib.request.Request(url, headers={
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Referer": main_url,
    "X-Requested-With": "XMLHttpRequest",
})
with opener.open(req, timeout=30) as r:
    html = r.read().decode("utf-8", errors="replace")

print(f"Status: {r.status}, Content-Type: {r.headers.get('Content-Type','?')}")
print(f"Length: {len(html):,} chars")
print("\nFull response:")
print(html[:3000])
