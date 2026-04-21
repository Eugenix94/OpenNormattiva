#!/usr/bin/env python3
"""Inspect the HTML structure of the normattiva Constitution page."""
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

print(f"Total HTML: {len(html):,} chars")

# Find div IDs and classes to understand page structure
divs = re.findall(r'<div[^>]*(?:id|class)="([^"]+)"', html)
unique_divs = list(dict.fromkeys(divs))[:50]
print(f"\nFirst 50 div id/class values:")
for d in unique_divs:
    print(f"  {d}")

# Look for "art" "articolo" or article markers
art_matches = re.findall(r'<[^>]*(?:articolo|art\.|Articolo)[^>]*>', html, re.IGNORECASE)[:10]
print(f"\nArticle-related tags:")
for m in art_matches:
    print(f"  {m[:100]}")

# Look for the actual text content areas
content_areas = re.findall(r'<div[^>]*id="[^"]*(?:text|content|corpo|articolato|atto)[^"]*"[^>]*>', html, re.IGNORECASE)
print(f"\nContent area divs:")
for a in content_areas:
    print(f"  {a[:120]}")

# Find any h2/h3 tags (likely article headings)
headings = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL|re.IGNORECASE)[:10]
print(f"\nFirst 10 headings:")
for h in headings:
    clean = re.sub(r'<[^>]+>', '', h).strip()
    print(f"  {clean[:80]}")
