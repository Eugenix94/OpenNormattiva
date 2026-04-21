#!/usr/bin/env python3
"""Debug article link extraction from normattiva HTML."""
import urllib.request
import re

UA = "NormattivaClient/1.0 (research mirror; contact redazione@normattiva.it)"

url = "https://www.normattiva.it/uri-res/N2Ls?urn:nir:stato:costituzione:1947-12-27"
req = urllib.request.Request(url, headers={
    "User-Agent": UA, 
    "Accept": "text/html,*/*",
    "Referer": "https://www.normattiva.it/",
})
with urllib.request.urlopen(req, timeout=30) as r:
    html = r.read().decode("utf-8", errors="replace")

# Find the region around showArticle calls
print("=== Context around first showArticle ===")
idx = html.find("showArticle")
if idx >= 0:
    print(html[max(0,idx-200):idx+500])
    
print("\n\n=== All codiceRedazionale values ===")
codes = re.findall(r'codiceRedazionale=(\w+)', html)
print(set(codes))

print("\n\n=== First article URL ===")
paths = re.findall(r"showArticle\('(/atto/caricaArticolo[^']+)'", html)
if paths:
    print(f"Total paths: {len(paths)}")
    print(f"First: {paths[0]}")
    print(f"Params: {dict(p.split('=',1) for p in paths[0].split('?',1)[-1].split('&') if '=' in p)}")
