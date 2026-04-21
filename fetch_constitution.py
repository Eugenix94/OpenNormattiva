#!/usr/bin/env python3
"""
fetch_constitution.py
Fetch the Italian Constitution from Normattiva and insert into laws.db.

The Costituzione della Repubblica Italiana (1947-12-27) needs to be
inserted separately since it is not in any pre-packaged collection.
"""

import urllib.request
import urllib.error
import sqlite3
import json
import re
import time
from datetime import datetime, timezone

BASE_API = "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1"
NORM_BASE = "https://www.normattiva.it"
UA = "NormattivaClient/1.0 (research mirror; contact redazione@normattiva.it)"

HEADERS = {
    "User-Agent": UA,
    "Referer": "https://dati.normattiva.it/",
    "Accept": "application/json, text/html, */*",
}


def _req(url, headers=None):
    hdrs = dict(HEADERS)
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def try_api_endpoints():
    """Try various Normattiva API endpoints for the Constitution."""
    print("\n=== Probing API endpoints ===")

    # Endpoint 1: URN search
    endpoints = [
        f"{BASE_API}/api/v1/atto/urnnir/stato/costituzione/1947-12-27",
        f"{BASE_API}/api/v1/atto?urn=urn:nir:stato:costituzione:1947-12-27",
        f"{BASE_API}/api/v1/search?q=costituzione&tipo=COSTITUZIONE&anno=1947",
        f"{BASE_API}/api/v1/collections/collection-predefinite",  # Known working
    ]

    for url in endpoints:
        try:
            data = _req(url)
            print(f"  OK ({len(data)} bytes): {url}")
            # Show first 300 chars
            txt = data.decode("utf-8", errors="replace")[:300]
            print(f"     Preview: {txt[:200]}")
            return url, data
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}: {url}")
        except Exception as e:
            print(f"  ERR {e}: {url}")

    return None, None


def fetch_from_normattiva_web():
    """
    Fetch the Constitution via Normattiva's N2Ls URN resolver.
    Returns structured text.
    """
    print("\n=== Fetching via N2Ls URN resolver ===")
    
    urn = "urn:nir:stato:costituzione:1947-12-27"
    url = f"{NORM_BASE}/uri-res/N2Ls?{urn}"
    
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Referer": "https://www.normattiva.it/",
        })
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read().decode("utf-8", errors="replace")
            print(f"  Got {len(content):,} bytes, status={r.status}")
            print(f"  Content-Type: {r.headers.get('Content-Type', '?')}")
            print(f"  URL after redirect: {r.url}")
            return content
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def fetch_akn_format():
    """Try fetching AKN XML format of the Constitution."""
    print("\n=== Fetching AKN XML format ===")
    
    # Try direct AKN URL pattern
    urls = [
        f"{NORM_BASE}/uri-res/N2Ls?urn:nir:stato:costituzione:1947-12-27!vig=",
        f"{BASE_API}/api/v1/atto/akn/stato/costituzione/1947-12-27",
    ]
    
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/xml, text/xml, */*",
                "Referer": "https://dati.normattiva.it/",
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                ct = r.headers.get("Content-Type", "?")
                print(f"  OK ({len(data):,} bytes, {ct}): {url}")
                return data
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}: {url}")
        except Exception as e:
            print(f"  ERR {e}: {url}")
    
    return None


def extract_text_from_html(html):
    """Extract law text from Normattiva HTML response."""
    # Remove scripts/styles
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL|re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL|re.IGNORECASE)
    
    # Look for main content div
    match = re.search(r'<div[^>]*class="[^"]*articolato[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL|re.IGNORECASE)
    if match:
        return match.group(1)
    
    # Fallback: extract all text
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:50000]  # First 50k chars


if __name__ == "__main__":
    print("=== Constitution Fetcher ===")
    
    # Try API endpoints
    try_api_endpoints()
    
    # Try web fetch
    html = fetch_from_normattiva_web()
    if html:
        print(f"\nFirst 1000 chars of HTML response:")
        print(html[:1000])
    
    # Try AKN
    akn = fetch_akn_format()
    if akn:
        print(f"\nFirst 500 chars of AKN response:")
        print(akn[:500].decode('utf-8', errors='replace'))
