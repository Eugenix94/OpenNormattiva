#!/usr/bin/env python3
"""
download_constitution.py
Download the Italian Constitution from Normattiva article by article,
then insert into laws.db.

Usage: py download_constitution.py
"""

import urllib.request
import http.cookiejar
import sqlite3
import re
import time
from datetime import datetime, timezone

NORM_BASE = "https://www.normattiva.it"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DB_PATH = "data/laws.db"

CONST_URN = "urn:nir:stato:costituzione:1947-12-27"
CONST_TITLE = "Costituzione della Repubblica Italiana"
CONST_DATE = "1947-12-27"
CONST_YEAR = 1947
CONST_TYPE = "COSTITUZIONE"

# Build opener with cookie jar for session management
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def _get(url, referer=None, ajax=False):
    hdrs = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
        "Referer": referer or (NORM_BASE + "/"),
    }
    if ajax:
        hdrs["X-Requested-With"] = "XMLHttpRequest"
    req = urllib.request.Request(url, headers=hdrs)
    with opener.open(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def strip_html(html):
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    html = re.sub(r'&nbsp;', ' ', html)
    html = re.sub(r'&egrave;', 'è', html)
    html = re.sub(r'&agrave;', 'à', html)
    html = re.sub(r'&igrave;', 'ì', html)
    html = re.sub(r'&ograve;', 'ò', html)
    html = re.sub(r'&ugrave;', 'ù', html)
    html = re.sub(r'&amp;', '&', html)
    html = re.sub(r'&lt;', '<', html)
    html = re.sub(r'&gt;', '>', html)
    html = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), html)
    html = re.sub(r'[ \t]+', ' ', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


def extract_article_text(html):
    """Extract article text from caricaArticolo HTML response."""
    art_num_m = re.search(r'<h2[^>]*class="article-num-akn"[^>]*>(.*?)</h2>', html, re.DOTALL | re.IGNORECASE)
    art_num = strip_html(art_num_m.group(1)) if art_num_m else ""

    preamble_m = re.search(r'<h2[^>]*class="preamble-title-akn"[^>]*>(.*?)</h2>', html, re.DOTALL | re.IGNORECASE)
    preamble = strip_html(preamble_m.group(1)) if preamble_m else ""

    body_m = re.search(r'<div[^>]*class="bodyTesto"[^>]*>(.*?)(?:</div>\s*<div[^>]*class="d-flex)', html, re.DOTALL | re.IGNORECASE)
    if body_m:
        body_html = body_m.group(1)
        # Remove the article-num heading from body (we already captured it separately)
        body_html = re.sub(r'<h2[^>]*class="(?:article-num|preamble)[^"]*"[^>]*>.*?</h2>', '', body_html, flags=re.DOTALL | re.IGNORECASE)
    else:
        body_m2 = re.search(r'<span[^>]*class="art-just-text-akn"[^>]*>(.*?)</span>', html, re.DOTALL | re.IGNORECASE)
        body_html = body_m2.group(1) if body_m2 else ""

    body_text = strip_html(body_html)

    parts = []
    if preamble:
        parts.append(preamble)
    if art_num:
        parts.append(art_num)
    if body_text:
        parts.append(body_text)

    return "\n".join(parts)


def fetch_all_article_paths():
    """Load the main page and return list of article paths in order."""
    main_url = f"{NORM_BASE}/uri-res/N2Ls?{CONST_URN}"
    print(f"Loading main page...")
    html = _get(main_url)
    print(f"  Got {len(html):,} chars, {len(cj)} cookies")

    paths = re.findall(r"showArticle\('(/atto/caricaArticolo[^']+)'", html)
    seen = set()
    unique_paths = []
    for p in paths:
        clean = p.strip()
        # Key for dedup: use main identifying params only
        m = re.search(r'art\.idArticolo=(\d+)&art\.idSottoArticolo=(\d+)&art\.idSottoArticolo1=(\d+)', clean)
        key = m.group(0) if m else clean[:100]
        if key not in seen:
            seen.add(key)
            unique_paths.append(clean)

    print(f"  Found {len(unique_paths)} unique articles")
    return unique_paths, main_url


def fetch_article(path, referer):
    url = NORM_BASE + path
    try:
        html = _get(url, referer=referer, ajax=True)
        return extract_article_text(html)
    except Exception as e:
        return f"[Error: {e}]"


def insert_into_db(text, article_count):
    conn = sqlite3.connect(DB_PATH)
    existing = conn.execute("SELECT urn FROM laws WHERE urn = ?", (CONST_URN,)).fetchone()

    params = (
        CONST_URN, CONST_TITLE, CONST_TYPE, CONST_DATE, CONST_YEAR,
        text, len(text), article_count, 'in_force',
        datetime.now(timezone.utc).isoformat() + "Z",
        100.0, "Costituzione"
    )

    if existing:
        conn.execute("""
            UPDATE laws SET title=?, type=?, date=?, year=?, text=?,
                text_length=?, article_count=?, status=?, parsed_at=?,
                importance_score=?, source_collection=?
            WHERE urn=?
        """, params[1:] + (CONST_URN,))
    else:
        conn.execute("""
            INSERT INTO laws (urn, title, type, date, year, text, text_length,
                article_count, status, parsed_at, importance_score, source_collection)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, params)

    conn.execute("INSERT INTO laws_fts(laws_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    print(f"  Saved: {len(text):,} chars, {article_count} articles")


def main():
    print("=" * 60)
    print("ITALIAN CONSTITUTION DOWNLOADER")
    print("=" * 60)

    paths, referer = fetch_all_article_paths()
    if not paths:
        print("ERROR: No article paths found!")
        return

    print(f"\nFetching {len(paths)} articles...")
    articles = []
    errors = 0

    for i, path in enumerate(paths):
        text = fetch_article(path, referer)
        if text.startswith("[Error"):
            errors += 1
            if errors <= 3:
                print(f"  [{i+1}] ERROR: {text[:80]}")
        elif len(text) > 5:
            articles.append(text)
            if i < 3 or (i + 1) % 50 == 0:
                print(f"  [{i+1}/{len(paths)}] {text[:70].replace(chr(10), ' ')}")

        time.sleep(0.25)

    print(f"\nFetched {len(articles)} articles, {errors} errors")
    full_text = "\n\n".join(articles)
    print(f"Total: {len(full_text):,} chars")
    print("\nSample:")
    print(full_text[:500])

    if len(full_text) > 5000:
        art_count = len([1 for a in articles if re.search(r'Art\.\s*\d+', a)])
        insert_into_db(full_text, art_count)
        print("\nDone!")
    else:
        print(f"\nERROR: Text too short ({len(full_text)} chars) - not saving")


if __name__ == "__main__":
    main()
