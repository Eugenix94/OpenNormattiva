#!/usr/bin/env python3
"""
download_abrogati.py

Download and import the "Atti normativi abrogati (in originale)" collection.
Adds 124,036 abrogated laws to the database with status='abrogated'.

Usage: py download_abrogati.py [--limit N]
"""

import argparse
import sqlite3
import zipfile
import urllib.request
import urllib.parse
import urllib.error
import hashlib
import time
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

BASE_API = "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1"
UA = "NormattivaRawMirror/1.0 (Apache-2.0; contact redazione@normattiva.it)"
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://dati.normattiva.it/",
    "Accept": "*/*",
}
COLLECTION_NAME = "Atti normativi abrogati (in originale)"
ZIP_OUT = Path("akn/originale/atti_normativi_abrogati_O.zip")
DB_PATH = Path("data/laws.db")

_session_cookies = {}


def _cookie_header():
    if _session_cookies:
        return "; ".join(f"{k}={v}" for k, v in _session_cookies.items())
    return None


def _absorb_cookies(response):
    for raw_hdr, val in response.headers.items():
        if raw_hdr.lower() == "set-cookie":
            kv = val.split(";")[0].split("=", 1)
            if len(kv) == 2:
                _session_cookies[kv[0].strip()] = kv[1].strip()


# ─────────────────────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────────────────────

def download_zip(output_path: Path) -> bool:
    """Stream-download the abrogati ZIP from Normattiva API."""
    if output_path.exists():
        size_mb = output_path.stat().st_size / 1e6
        print(f"ZIP already exists: {output_path} ({size_mb:.1f} MB)")
        user = input("Re-download? [y/N] ").strip().lower()
        if user != 'y':
            return True

    params = urllib.parse.urlencode({
        "nome": COLLECTION_NAME,
        "formato": "AKN",
        "formatoRichiesta": "O",
    })
    url = f"{BASE_API}/api/v1/collections/download/collection-preconfezionata?{params}"

    print(f"Downloading: {COLLECTION_NAME}")
    print(f"URL: {url[:100]}...")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp")

    hdrs = dict(HEADERS)
    ck = _cookie_header()
    if ck:
        hdrs["Cookie"] = ck
    req = urllib.request.Request(url, headers=hdrs)

    downloaded = 0
    sha = hashlib.sha256()
    t0 = time.time()
    last_print = 0

    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            _absorb_cookies(r)
            total = int(r.headers.get("Content-Length", 0))
            total_mb = total / 1e6 if total else "?"

            with open(tmp_path, "wb") as fh:
                while True:
                    chunk = r.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    fh.write(chunk)
                    sha.update(chunk)
                    downloaded += len(chunk)

                    now = time.time()
                    if now - last_print >= 5:
                        elapsed = now - t0
                        rate = downloaded / elapsed / 1e6
                        print(f"  {downloaded/1e6:.1f} / {total_mb} MB  ({rate:.1f} MB/s)")
                        last_print = now

        # Rename to final
        tmp_path.rename(output_path)
        elapsed = time.time() - t0
        print(f"\nDownloaded: {downloaded/1e6:.1f} MB in {elapsed:.0f}s")
        print(f"SHA256: {sha.hexdigest()}")
        return True

    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        print(f"\nERROR during download: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Parse AKN XML
# ─────────────────────────────────────────────────────────────────────────────

try:
    from lxml import etree
    HAS_LXML = True
except ImportError:
    HAS_LXML = False
    print("WARNING: lxml not available, using xml.etree.ElementTree (slower)")
    import xml.etree.ElementTree as etree

AKN_NS = {
    'akn': 'http://docs.oasis-open.org/legaldocml/ns/akn/3.0',
    'eli': 'http://data.europa.eu/eli/ontology#',
    'na': 'http://www.normattiva.it/eli/',
    'nrdfa': 'http://www.normattiva.it/rdfa/',
}


def extract_text(elem):
    """Extract all text from XML element recursively."""
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(extract_text(child))
        if child.tail:
            parts.append(child.tail)
    return ' '.join(p for p in parts if p).strip()


def parse_akn_xml(xml_bytes: bytes):
    """Parse one AKN XML file, return dict or None."""
    try:
        root = etree.fromstring(xml_bytes)

        def find(path):
            # Try with namespace, then without
            for ns_path in [path.replace('akn:', '{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}'),
                            path.replace('akn:', '').replace('nrdfa:', '{http://www.normattiva.it/rdfa/}')]:
                r = root.find(f'.//{ns_path}')
                if r is not None:
                    return r
            return None

        def findall(path):
            ns_path = path.replace('akn:', '{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}')
            return root.findall(f'.//{ns_path}')

        # URN
        urn = None
        for alias in findall('akn:FRBRalias'):
            if alias.get('name') == 'urn:nir':
                urn = alias.get('value')
                break

        if not urn:
            return None

        # Title
        title = ""
        title_ns = '{http://www.normattiva.it/rdfa/}span'
        for span in root.findall(f'.//{title_ns}'):
            if span.get('property') == 'eli:title':
                title = span.get('content', '').strip()
                break

        # Date
        date_str = None
        for fd in findall('akn:FRBRdate'):
            d = fd.get('date')
            if d:
                date_str = d
                break

        # Type
        doc_type = None
        type_ns = '{http://www.normattiva.it/rdfa/}span'
        for span in root.findall(f'.//{type_ns}'):
            if span.get('property') == 'eli:type_document':
                resource = span.get('resource', '')
                doc_type = resource.split('#')[-1] if '#' in resource else resource
                break

        # Year from date or URN
        year = None
        if date_str:
            try:
                year = int(date_str.split('-')[0])
            except Exception:
                pass
        if not year and urn:
            import re
            m = re.search(r':(\d{4})-', urn)
            if m:
                year = int(m.group(1))

        # Full text from body
        body_ns = '{http://docs.oasis-open.org/legaldocml/ns/akn/3.0}body'
        body = root.find(f'.//{body_ns}')
        full_text = extract_text(body) if body is not None else ""
        article_count = len(root.findall(f'.//{body_ns.replace("body", "article")}')) if body is not None else 0

        return {
            'urn': urn,
            'title': title or f"Atto del {date_str}",
            'type': doc_type,
            'date': date_str,
            'year': year,
            'text': full_text[:100000],  # Cap at 100KB per record
            'text_length': len(full_text),
            'article_count': article_count,
            'status': 'abrogated',
            'source_collection': COLLECTION_NAME,
            'parsed_at': datetime.now(timezone.utc).isoformat() + "Z",
        }

    except Exception as e:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Insert into DB
# ─────────────────────────────────────────────────────────────────────────────

def import_to_db(zip_path: Path, limit: int = 0) -> int:
    """Parse ZIP and insert abrogated laws into DB. Returns count inserted."""
    print(f"\nOpening ZIP: {zip_path}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    inserted = 0
    skipped = 0
    errors = 0
    batch = []
    BATCH_SIZE = 500

    def flush_batch():
        nonlocal inserted
        conn.executemany("""
            INSERT OR IGNORE INTO laws
                (urn, title, type, date, year, text, text_length,
                 article_count, status, parsed_at, importance_score, source_collection)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, batch)
        conn.commit()
        inserted += len(batch)
        batch.clear()

    with zipfile.ZipFile(zip_path, 'r') as zf:
        xml_files = [f for f in zf.namelist() if f.endswith('.xml')]
        total = len(xml_files)
        print(f"ZIP contains {total:,} XML files")

        if limit:
            xml_files = xml_files[:limit]
            print(f"Limited to first {limit:,}")

        t0 = time.time()
        for i, xml_file in enumerate(xml_files):
            try:
                xml_bytes = zf.read(xml_file)
                law = parse_akn_xml(xml_bytes)
                if law:
                    batch.append((
                        law['urn'], law['title'], law['type'], law['date'], law['year'],
                        law['text'], law['text_length'], law['article_count'],
                        law['status'], law['parsed_at'], 0.0, law['source_collection']
                    ))
                else:
                    skipped += 1
            except Exception as e:
                errors += 1

            if len(batch) >= BATCH_SIZE:
                flush_batch()

            if (i + 1) % 5000 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed
                remaining = (total - i - 1) / rate if rate > 0 else 0
                print(f"  [{i+1:,}/{total:,}] inserted={inserted:,}  ETA={remaining:.0f}s")

        if batch:
            flush_batch()

    # Rebuild FTS
    print(f"\nRebuilding FTS index...")
    conn.execute("INSERT INTO laws_fts(laws_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()

    print(f"\nImport complete:")
    print(f"  Inserted: {inserted:,}")
    print(f"  Skipped (parse fail): {skipped:,}")
    print(f"  Errors: {errors:,}")
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Download and import abrogati laws")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit to first N laws (for testing)")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip download, use existing ZIP")
    parser.add_argument("--skip-import", action="store_true",
                        help="Only download, don't import to DB")
    args = parser.parse_args()

    print("=" * 60)
    print("ABROGATI LAWS DOWNLOADER")
    print(f"Collection: {COLLECTION_NAME}")
    print(f"Expected: 124,036 acts")
    print("=" * 60)

    if not args.skip_download:
        ok = download_zip(ZIP_OUT)
        if not ok:
            print("Download failed!")
            sys.exit(1)
    elif not ZIP_OUT.exists():
        print(f"ERROR: ZIP not found: {ZIP_OUT}")
        sys.exit(1)

    if not args.skip_import:
        count = import_to_db(ZIP_OUT, limit=args.limit)
        print(f"\nTotal inserted: {count:,} laws")

    print("\nDone!")


if __name__ == "__main__":
    main()
