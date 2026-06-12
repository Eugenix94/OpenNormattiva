#!/usr/bin/env python3
"""
build_voom.py — VOOM Full Pipeline Builder

VOOM = Vigente + Originale (abrogati) + Multivigente

Builds the complete Italian law corpus in two databases:

  data/laws.db            — PRIMARY (~1.15 GB)
    • 162,410 vigente laws  (status='in_force', from V track)
    • 124,036 abrogati laws (status='abrogated', from O track)
    Total: ~286,000 laws

  data/multivigente.db    — RESEARCH (~2.0 GB, on-demand)
    • ~440,000 versioned amendment snapshots (from M track)

Usage:
    py build_voom.py                         # full build
    py build_voom.py --steps vigente abrogati  # partial build
    py build_voom.py --steps multivigente      # M track only
    py build_voom.py --skip-download           # use cached ZIPs
    py build_voom.py --limit 500               # test with 500 laws per step

The script automatically:
  1. Copies vigente laws from search project (OpenNormattiva) if available
  2. Downloads abrogati ZIP from Normattiva API (O track)
  3. Downloads all 22 vigente collections in M track (amendment versions)
  4. Merges into the correct DBs
  5. Rebuilds FTS5 indexes
  6. Prints a summary

After running, upload with:
    py deploy_hf.py --skip-space   # upload DB to HF dataset only
    py deploy_hf.py                # full redeploy including Space

Freshness note:
  The Normattiva API packages collections on a rolling basis.
  Laws published on the same day may appear in the next collection refresh
  (typically within 24-48h). The ETag headers are checked to avoid
  re-downloading unchanged collections.
"""

import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

LAB_ROOT = Path(__file__).parent
SEARCH_ROOT = LAB_ROOT.parent / "OpenNormattiva"

DB_PATH = LAB_ROOT / "data" / "laws.db"
MV_DB_PATH = LAB_ROOT / "data" / "multivigente.db"
PROCESSED_DIR = LAB_ROOT / "data" / "processed"
RAW_VIGENTE_DIR = LAB_ROOT / "akn" / "vigente"
RAW_ABROGATI_DIR = LAB_ROOT / "akn" / "originale"
RAW_MULTI_DIR = LAB_ROOT / "akn" / "multivigente"

ABROGATI_COLLECTION = "Atti normativi abrogati (in originale)"
ABROGATI_ZIP = RAW_ABROGATI_DIR / "atti_normativi_abrogati_O.zip"

# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

import urllib.request
import urllib.parse

BASE_API = "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1"
UA = "NormattivaVOOM/1.0 (research; contact redazione@normattiva.it)"
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://dati.normattiva.it/",
    "Accept": "*/*",
}

_cookies: dict = {}

# Format fallback order for collection downloads.
# We still prefer AKN/XML first because the importer parses XML payloads.
FORMAT_FALLBACKS = ("AKN", "XML", "HTML", "PDF")


def _ck():
    return "; ".join(f"{k}={v}" for k, v in _cookies.items()) if _cookies else None


def _absorb(resp):
    for h, v in resp.headers.items():
        if h.lower() == "set-cookie":
            kv = v.split(";")[0].split("=", 1)
            if len(kv) == 2:
                _cookies[kv[0].strip()] = kv[1].strip()


def api_get_catalogue() -> list[dict]:
    url = f"{BASE_API}/api/v1/collections/collection-predefinite"
    hdr = dict(HEADERS)
    if _ck():
        hdr["Cookie"] = _ck()
    with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30) as r:
        _absorb(r)
        return json.loads(r.read())


def api_download_zip(nome: str, variant: str, output_path: Path,
                     skip_if_exists: bool = True) -> bool:
    """Download a collection ZIP. Returns True if downloaded or already exists."""

    def _zip_has_xml(path: Path) -> bool:
        try:
            with zipfile.ZipFile(path, "r") as zf:
                return any(n.lower().endswith(".xml") for n in zf.namelist())
        except Exception:
            return False

    if skip_if_exists and output_path.exists() and output_path.stat().st_size > 10_000:
        # Check ETag to see if server has a newer version
        etag_path = output_path.with_suffix(".etag")
        local_etag = etag_path.read_text().strip() if etag_path.exists() else None

        params = urllib.parse.urlencode({
            "nome": nome, "formato": "AKN", "formatoRichiesta": variant
        })
        url = f"{BASE_API}/api/v1/collections/download/collection-preconfezionata?{params}"
        try:
            hdr = dict(HEADERS)
            if local_etag:
                hdr["If-None-Match"] = local_etag
            req = urllib.request.Request(url, headers=hdr, method="HEAD")
            with urllib.request.urlopen(req, timeout=15) as r:
                server_etag = r.headers.get("x-etag") or r.headers.get("ETag")
            if local_etag and server_etag and local_etag == server_etag:
                print(f"  [skip] {nome} ({variant}) — ETag unchanged")
                return True
        except Exception:
            pass  # HEAD not supported; fall through to mtime check

        # ETag unavailable - use mtime as fallback: skip re-download if ZIP is < 12h old
        age_h = (time.time() - output_path.stat().st_mtime) / 3600
        if age_h < 12:
            print(f"  [cached] {nome} ({variant}) - ZIP is {age_h:.1f}h old")
            return True

    print(f"  Downloading {nome} ({variant})...", end=" ", flush=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()  # Remove stale .tmp from previous interrupted download

    for fmt in FORMAT_FALLBACKS:
        params = urllib.parse.urlencode({
            "nome": nome, "formato": fmt, "formatoRichiesta": variant
        })
        url = f"{BASE_API}/api/v1/collections/download/collection-preconfezionata?{params}"

        hdr = dict(HEADERS)
        if _ck():
            hdr["Cookie"] = _ck()

        downloaded = 0
        t0 = time.time()
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=hdr), timeout=600
            ) as r:
                _absorb(r)
                etag = r.headers.get("x-etag") or r.headers.get("ETag")
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = r.read(1024 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)

            # Some transient API failures return HTML/JSON payloads instead of ZIP bytes.
            if not zipfile.is_zipfile(tmp):
                tmp.unlink(missing_ok=True)
                print(f"[{fmt}] non-ZIP; trying next format", end=" ", flush=True)
                continue

            # Importer consumes XML files; reject ZIPs without XML members.
            if not _zip_has_xml(tmp):
                tmp.unlink(missing_ok=True)
                print(f"[{fmt}] ZIP has no XML; trying next format", end=" ", flush=True)
                continue

            tmp.replace(output_path)  # .replace() overwrites on Windows; .rename() does not
            if etag:
                output_path.with_suffix(".etag").write_text(etag)
            elapsed = time.time() - t0
            print(f"[{fmt}] {downloaded/1e6:.1f} MB  ({elapsed:.0f}s)")
            return True
        except Exception as e:
            if tmp.exists():
                tmp.unlink()
            print(f"[{fmt}] {e}; trying next format", end=" ", flush=True)

    print("ERROR: all format fallbacks failed")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# AKN Parser (shared)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from lxml import etree as ET
except ImportError:
    import xml.etree.ElementTree as ET

AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
RDFA = "http://www.normattiva.it/rdfa/"


def _find(root, local):
    return root.find(f".//{{{AKN}}}{local}")


def _findall(root, local):
    return root.findall(f".//{{{AKN}}}{local}")


def _rdfa_span(root, prop):
    for span in root.findall(f".//{{{RDFA}}}span"):
        if span.get("property") == prop:
            return span.get("content", span.text or "").strip()
    return ""


def _extract_text(elem):
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(_extract_text(child))
        if child.tail:
            parts.append(child.tail)
    return " ".join(p for p in parts if p).strip()


def parse_akn(xml_bytes: bytes, status: str = "in_force",
              version_date: str | None = None) -> dict | None:
    """Parse one AKN XML file. Returns law dict or None."""
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None

    # URN
    urn = None
    for alias in _findall(root, "FRBRalias"):
        if alias.get("name") == "urn:nir":
            urn = alias.get("value")
            break
    if not urn:
        return None

    # Title
    title = _rdfa_span(root, "eli:title") or ""

    # Date
    date_str = None
    for fd in _findall(root, "FRBRdate"):
        d = fd.get("date")
        if d:
            date_str = d
            break

    # Year
    year = None
    if date_str:
        try:
            year = int(date_str.split("-")[0])
        except Exception:
            pass
    if not year and urn:
        m = re.search(r":(\d{4})-", urn)
        if m:
            year = int(m.group(1))

    # Type
    doc_type = _rdfa_span(root, "eli:type_document")
    if "#" in doc_type:
        doc_type = doc_type.split("#")[-1]

    # Version date for M track
    if version_date is None and date_str:
        version_date = date_str

    # Full text
    body = _find(root, "body")
    full_text = _extract_text(body) if body is not None else ""
    article_count = len(_findall(root, "article")) if body is not None else 0

    return {
        "urn": urn,
        "title": title or f"Atto del {date_str or 'data sconosciuta'}",
        "type": doc_type or None,
        "date": date_str,
        "year": year,
        "text": full_text[:150_000],
        "text_length": len(full_text),
        "article_count": article_count,
        "status": status,
        "version_date": version_date,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# NIR Parser (Norme in Rete format, used by Codici collection)
# ─────────────────────────────────────────────────────────────────────────────

NIR_NS = "http://www.normeinrete.it/nir/2.2/"

_NIR_TYPE_MAP = {
    "DecretoLegislativo": "decreto.legislativo",
    "Legge": "legge",
    "Decreto": "decreto",
    "DecretoLegge": "decreto-legge",
    "DecretoDelPresidenteDellRepubblica": "decreto.del.presidente.della.repubblica",
    "DecretoDelPresidenteDelConsiglioDeMinistri": "decreto.del.presidente.del.consiglio.dei.ministri",
    "DecretoDelPresidenteDelConsiglio": "decreto.del.presidente.del.consiglio.dei.ministri",
    "DecretoMinisteriale": "decreto.ministeriale",
    "Regolamento": "regolamento",
    "CodiceCivile": "codice.civile",
    "CodicePenale": "codice.penale",
    "CodiceProc": "codice",
}


def _nir(tag: str) -> str:
    return f"{{{NIR_NS}}}{tag}"


def _nir_find(elem, tag: str):
    """Find child using NIR namespace, fall back to no-namespace."""
    r = elem.find(_nir(tag))
    if r is None:
        r = elem.find(tag)
    return r


def _nir_findall(elem, tag: str):
    r = elem.findall(_nir(tag))
    if not r:
        r = elem.findall(tag)
    return r


def parse_nir(xml_bytes: bytes, status: str = "in_force",
              version_date: str | None = None) -> dict | None:
    """Parse one NIR (Norme in Rete) XML file. Returns law dict or None."""
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None

    # Root must be <NIR ...>
    root_local = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    if root_local != "NIR":
        return None

    # First meaningful child = document type element
    doc_elem = None
    doc_tag = None
    for child in root:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        doc_elem = child
        doc_tag = local
        break

    if doc_elem is None:
        return None

    nir_type = _NIR_TYPE_MAP.get(doc_tag, doc_tag.lower().replace(" ", "."))

    # meta/descrittori/urn for explicit URN
    urn = None
    meta = _nir_find(doc_elem, "meta")
    if meta is not None:
        desc = _nir_find(meta, "descrittori")
        if desc is not None:
            urn_elem = _nir_find(desc, "urn")
            if urn_elem is not None:
                valore = urn_elem.get("valore", "")
                if valore and len(valore) > 6 and not valore.endswith(":"):
                    urn = valore

    # intestazione for date, number, title
    intestazione = _nir_find(doc_elem, "intestazione")
    date_str = None
    number = None
    title = ""

    if intestazione is not None:
        dataDoc = _nir_find(intestazione, "dataDoc")
        if dataDoc is not None:
            norm = dataDoc.get("norm", "")
            if norm and len(norm) == 8 and norm.isdigit():
                date_str = f"{norm[:4]}-{norm[4:6]}-{norm[6:]}"

        numDoc = _nir_find(intestazione, "numDoc")
        if numDoc is not None:
            number = (numDoc.text or "").strip()

        titoloDoc = _nir_find(intestazione, "titoloDoc")
        if titoloDoc is not None:
            title = " ".join((titoloDoc.text or "").split())

    # Construct URN from components if not found explicitly
    if not urn:
        if date_str and number:
            urn = f"urn:nir:stato:{nir_type}:{date_str};{number}"
        else:
            return None  # Cannot determine URN

    # Year
    year = None
    if date_str:
        try:
            year = int(date_str.split("-")[0])
        except Exception:
            pass
    if not year and urn:
        m = re.search(r":(\d{4})-", urn)
        if m:
            year = int(m.group(1))

    if version_date is None and date_str:
        version_date = date_str

    # Full text from articolato
    articolato = _nir_find(doc_elem, "articolato")
    full_text = _extract_text(articolato) if articolato is not None else ""
    article_count = 0
    if articolato is not None:
        article_count = len(_nir_findall(articolato, "articolo"))

    return {
        "urn": urn,
        "title": title or f"Atto del {date_str or 'data sconosciuta'}",
        "type": nir_type,
        "date": date_str,
        "year": year,
        "text": full_text[:150_000],
        "text_length": len(full_text),
        "article_count": article_count,
        "status": status,
        "version_date": version_date,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

LAWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS laws (
    urn TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    type TEXT,
    date TEXT,
    year INTEGER,
    text TEXT,
    text_length INTEGER DEFAULT 0,
    article_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'in_force',
    source_collection TEXT,
    parsed_at TEXT,
    importance_score REAL DEFAULT 0.0,
    subject_tags TEXT DEFAULT '[]',
    legislature_id INTEGER,
    government TEXT,
    era TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS laws_fts
    USING fts5(urn UNINDEXED, title, type, text, content='laws', content_rowid='rowid');
CREATE TABLE IF NOT EXISTS law_metadata (
    urn TEXT PRIMARY KEY REFERENCES laws(urn),
    citation_count_incoming INTEGER DEFAULT 0,
    citation_count_outgoing INTEGER DEFAULT 0,
    pagerank REAL DEFAULT 0.0,
    domain_cluster TEXT
);
CREATE TABLE IF NOT EXISTS citations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    citing_urn TEXT,
    cited_urn TEXT,
    count INTEGER DEFAULT 1,
    context TEXT DEFAULT '',
    UNIQUE(citing_urn, cited_urn)
);
"""

MV_SCHEMA = """
CREATE TABLE IF NOT EXISTS law_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    law_urn TEXT NOT NULL,
    version_date TEXT,
    title TEXT,
    type TEXT,
    year INTEGER,
    text TEXT,
    text_length INTEGER DEFAULT 0,
    article_count INTEGER DEFAULT 0,
    source_collection TEXT,
    parsed_at TEXT,
    UNIQUE(law_urn, version_date)
);
CREATE INDEX IF NOT EXISTS idx_lv_urn ON law_versions(law_urn);
CREATE TABLE IF NOT EXISTS law_version_sources (
    law_urn TEXT NOT NULL,
    source_collection TEXT NOT NULL,
    first_seen_at TEXT,
    UNIQUE(law_urn, source_collection)
);
CREATE INDEX IF NOT EXISTS idx_lvs_collection ON law_version_sources(source_collection);
CREATE TABLE IF NOT EXISTS original_acts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    urn TEXT NOT NULL UNIQUE,
    title TEXT,
    type TEXT,
    date TEXT,
    year INTEGER,
    text TEXT,
    text_length INTEGER DEFAULT 0,
    article_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'abrogated',
    source_collection TEXT,
    parsed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_original_urn ON original_acts(urn);
CREATE VIRTUAL TABLE IF NOT EXISTS lv_fts
    USING fts5(law_urn UNINDEXED, title, text,
               content='law_versions', content_rowid='id');
"""


def open_db(path: Path, schema: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    for stmt in schema.split(";"):
        s = stmt.strip()
        if s:
            try:
                conn.execute(s)
            except sqlite3.OperationalError:
                pass  # already exists
    conn.commit()
    return conn


def rebuild_fts(conn: sqlite3.Connection, fts_table: str = "laws_fts"):
    print(f"  Rebuilding FTS index ({fts_table})...", end=" ", flush=True)
    conn.execute(f"INSERT INTO {fts_table}({fts_table}) VALUES('rebuild')")
    conn.commit()
    print("done")


def insert_laws_batch(conn: sqlite3.Connection, rows: list[tuple]):
    if not rows:
        return 0

    # Abrogati can collide with existing URNs from previous imports.
    # In that case, keep the same URN and overwrite status/content with O-track data.
    is_abrogated_batch = rows[0][8] == "abrogated"
    before = conn.total_changes
    if is_abrogated_batch:
        conn.executemany("""
            INSERT INTO laws
                (urn, title, type, date, year, text, text_length, article_count,
                 status, source_collection, parsed_at, importance_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0)
            ON CONFLICT(urn) DO UPDATE SET
                title = excluded.title,
                type = excluded.type,
                date = excluded.date,
                year = excluded.year,
                text = excluded.text,
                text_length = excluded.text_length,
                article_count = excluded.article_count,
                status = 'abrogated',
                source_collection = excluded.source_collection,
                parsed_at = excluded.parsed_at
        """, rows)
    else:
        conn.executemany("""
            INSERT OR IGNORE INTO laws
                (urn, title, type, date, year, text, text_length, article_count,
                 status, source_collection, parsed_at, importance_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0)
        """, rows)
    conn.commit()
    return conn.total_changes - before


def insert_mv_batch(conn: sqlite3.Connection, rows: list[tuple]):
    if not rows:
        return 0
    conn.executemany("""
        INSERT OR IGNORE INTO law_versions
            (law_urn, version_date, title, type, year, text, text_length,
             article_count, source_collection, parsed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    # Count only primary inserts in law_versions; attribution table writes are
    # bookkeeping and should not affect ingestion counters.
    lv_inserted = conn.execute("SELECT changes()").fetchone()[0]

    # Preserve all source collections for each URN, even when law_versions
    # de-duplicates by (law_urn, version_date).
    source_rows = []
    seen = set()
    now_iso = datetime.now(timezone.utc).isoformat()
    for row in rows:
        key = (row[0], row[8])  # (law_urn, source_collection)
        if key in seen:
            continue
        seen.add(key)
        source_rows.append((row[0], row[8], now_iso))
    conn.executemany(
        """
        INSERT OR IGNORE INTO law_version_sources
            (law_urn, source_collection, first_seen_at)
        VALUES (?, ?, ?)
        """,
        source_rows,
    )

    conn.commit()
    return int(lv_inserted)


def insert_original_batch(conn: sqlite3.Connection, rows: list[tuple]):
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO original_acts
            (urn, title, type, date, year, text, text_length, article_count,
             status, source_collection, parsed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(urn) DO UPDATE SET
            title = excluded.title,
            type = excluded.type,
            date = excluded.date,
            year = excluded.year,
            text = excluded.text,
            text_length = excluded.text_length,
            article_count = excluded.article_count,
            status = excluded.status,
            source_collection = excluded.source_collection,
            parsed_at = excluded.parsed_at
        """,
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def _backfill_mv_sources(conn: sqlite3.Connection) -> None:
    """Backfill source attribution table from legacy law_versions rows."""
    conn.execute(
        """
        INSERT OR IGNORE INTO law_version_sources (law_urn, source_collection, first_seen_at)
        SELECT law_urn, source_collection, parsed_at
        FROM law_versions
        WHERE source_collection IS NOT NULL
        """
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Vigente — copy from search project or download fresh
# ─────────────────────────────────────────────────────────────────────────────

def step_vigente(skip_download: bool, limit: int):
    print("\n" + "=" * 60)
    print("STEP 1: VIGENTE laws (V track — in_force)")
    print("=" * 60)

    # Check if source DB from search project exists and is large enough
    search_db = SEARCH_ROOT / "data" / "laws.db"
    if search_db.exists() and search_db.stat().st_size > 500_000_000:
        print(f"  Found search DB: {search_db} ({search_db.stat().st_size/1e6:.0f} MB)")
        if DB_PATH.exists():
            existing = sqlite3.connect(str(DB_PATH))
            n = existing.execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
            existing.close()
            print(f"  Lab DB already has {n:,} vigente laws")
            if n >= 150_000:
                print("  Already sufficient. Skipping vigente copy.")
                return
        print(f"  Copying {search_db.name} to {DB_PATH} ...")
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(search_db, DB_PATH)
        size = DB_PATH.stat().st_size
        conn = sqlite3.connect(str(DB_PATH))
        n = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
        conn.close()
        print(f"  Copied: {size/1e6:.0f} MB, {n:,} laws")
        return

    # No search DB available — download from catalogue
    print("  Search project DB not found. Will download vigente from API...")
    catalogue = api_get_catalogue()
    vigente_colls = [c for c in catalogue if c.get("formatoCollezione") == "V"
                     and c["nome"] != ABROGATI_COLLECTION]
    print(f"  Vigente collections: {len(vigente_colls)}")

    RAW_VIGENTE_DIR.mkdir(parents=True, exist_ok=True)
    conn = open_db(DB_PATH, LAWS_SCHEMA)

    total_inserted = 0
    for col in vigente_colls:
        nome = col["nome"]
        zip_path = RAW_VIGENTE_DIR / f"{nome}_V.zip"
        ok = api_download_zip(nome, "V", zip_path, skip_if_exists=skip_download)
        if not ok:
            continue
        inserted = _import_zip(conn, zip_path, nome, "in_force", limit, "laws")
        total_inserted += inserted

    rebuild_fts(conn)
    conn.close()
    n = sqlite3.connect(str(DB_PATH)).execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
    print(f"\n  Vigente step done: {n:,} in_force laws in DB")


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Abrogati — O track, one collection
# ─────────────────────────────────────────────────────────────────────────────

def step_abrogati(skip_download: bool, limit: int):
    print("\n" + "=" * 60)
    print("STEP 2: ABROGATI laws (O track — abrogated)")
    print(f"  Collection: {ABROGATI_COLLECTION}")
    print("  Expected: 124,036 acts")
    print("=" * 60)

    # Check if already imported
    if DB_PATH.exists():
        conn_check = sqlite3.connect(str(DB_PATH))
        n_abr = conn_check.execute(
            "SELECT COUNT(*) FROM laws WHERE status='abrogated'"
        ).fetchone()[0]
        conn_check.close()
        if n_abr >= 100_000:
            print(f"  Already have {n_abr:,} abrogated laws. Skipping.")
            return

    # Download ZIP
    ok = api_download_zip(ABROGATI_COLLECTION, "O", ABROGATI_ZIP,
                          skip_if_exists=skip_download)
    if not ok:
        print("  ERROR: Could not download abrogati ZIP")
        return

    # Import into laws.db
    conn = open_db(DB_PATH, LAWS_SCHEMA)
    inserted = _import_zip(conn, ABROGATI_ZIP, ABROGATI_COLLECTION,
                           "abrogated", limit, "laws")

    rebuild_fts(conn)
    conn.close()

    conn2 = sqlite3.connect(str(DB_PATH))
    n_abr = conn2.execute("SELECT COUNT(*) FROM laws WHERE status='abrogated'").fetchone()[0]
    n_tot = conn2.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    conn2.close()
    print(f"\n  Abrogati step done: {n_abr:,} abrogated + {n_tot:,} total laws")


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Multivigente - M track, all 22 collections -> multivigente.db
# ─────────────────────────────────────────────────────────────────────────────

def step_multivigente(skip_download: bool, limit: int):
    print("\n" + "=" * 60)
    print("STEP 3: MULTIVIGENTE (M track — amendment versions)")
    print("  Expected: ~22 collections, ~440,000 versioned snapshots")
    print("=" * 60)

    catalogue = api_get_catalogue()
    # Some catalogue entries may lack the 'nome' key; be defensive.
    # API returns 'nomeCollezione' in current catalogue responses; accept either key.
    mv_colls = [c for c in catalogue
                if c.get("formatoCollezione") == "M"
                and (c.get("nome") or c.get("nomeCollezione"))
                and (c.get("nome") or c.get("nomeCollezione")) != ABROGATI_COLLECTION]
    print(f"  M-track collections: {len(mv_colls)}")

    RAW_MULTI_DIR.mkdir(parents=True, exist_ok=True)
    conn = open_db(MV_DB_PATH, MV_SCHEMA)
    _backfill_mv_sources(conn)

    total_inserted = 0
    for col in mv_colls:
        # Prefer 'nome' if present, otherwise use 'nomeCollezione' from API
        nome = col.get("nome") or col.get("nomeCollezione")
        official_count = col.get("numeroAtti", 0)
        zip_path = RAW_MULTI_DIR / f"{nome}_M.zip"

        # Skip collections already at 100% coverage — no new URNs possible
        if official_count > 0:
            has_sources = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='law_version_sources'"
            ).fetchone()
            if has_sources:
                existing_urn = conn.execute(
                    "SELECT COUNT(DISTINCT law_urn) FROM law_version_sources WHERE source_collection=?",
                    (nome,),
                ).fetchone()[0]
            else:
                existing_urn = conn.execute(
                    "SELECT COUNT(DISTINCT law_urn) FROM law_versions WHERE source_collection=?",
                    (nome,),
                ).fetchone()[0]
            if existing_urn >= official_count:
                print(f"  {nome}: [done] complete ({existing_urn:,}/{official_count:,}) - skipping")
                continue

        ok = api_download_zip(nome, "M", zip_path, skip_if_exists=skip_download)
        if not ok:
            continue
        try:
            inserted = _import_zip(conn, zip_path, nome, "in_force", limit, "multivigente")
        except zipfile.BadZipFile:
            print(f"  WARNING: Corrupted ZIP for {nome}; deleting cache and retrying download once")
            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass
            ok = api_download_zip(nome, "M", zip_path, skip_if_exists=False)
            if not ok:
                print(f"  ERROR: Re-download failed for {nome}; skipping collection")
                continue
            try:
                inserted = _import_zip(conn, zip_path, nome, "in_force", limit, "multivigente")
            except zipfile.BadZipFile:
                print(f"  ERROR: ZIP still invalid after retry for {nome}; skipping collection")
                continue
        except Exception as e:
            print(f"  ERROR: Failed importing {nome}: {e}")
            continue
        total_inserted += inserted
        print(f"  {nome}: {inserted:,} versions imported (total so far: {total_inserted:,})")

    rebuild_fts(conn, "lv_fts")
    conn.close()

    n = sqlite3.connect(str(MV_DB_PATH)).execute("SELECT COUNT(*) FROM law_versions").fetchone()[0]
    print(f"\n  Multivigente step done: {n:,} amendment versions in {MV_DB_PATH.name}")


def step_originale_mv(skip_download: bool, limit: int):
    print("\n" + "=" * 60)
    print("STEP 4: ORIGINALE ABROGATI into multivigente.db")
    print(f"  Collection: {ABROGATI_COLLECTION}")
    print("=" * 60)

    RAW_ABROGATI_DIR.mkdir(parents=True, exist_ok=True)
    conn = open_db(MV_DB_PATH, MV_SCHEMA)

    current = conn.execute("SELECT COUNT(*) FROM original_acts").fetchone()[0]
    if current >= 120_000 and not limit:
        print(f"  Already have {current:,} original acts in multivigente.db. Skipping.")
        conn.close()
        return

    ok = api_download_zip(ABROGATI_COLLECTION, "O", ABROGATI_ZIP, skip_if_exists=skip_download)
    if not ok:
        print("  ERROR: Could not download abrogati ZIP")
        conn.close()
        return

    inserted = _import_zip(conn, ABROGATI_ZIP, ABROGATI_COLLECTION, "abrogated", limit, "originale_mv")
    total = conn.execute("SELECT COUNT(*) FROM original_acts").fetchone()[0]
    conn.close()
    print(f"\n  Originale step done: {inserted:,} upserted, {total:,} total in original_acts")


# ─────────────────────────────────────────────────────────────────────────────
# Shared ZIP importer
# ─────────────────────────────────────────────────────────────────────────────

# Number of parallel parse workers. lxml releases the GIL so threads give
# real parallelism on multi-core machines.
_PARSE_WORKERS = min(os.cpu_count() or 4, 8)
# Only use parallel parsing for ZIPs large enough to justify the overhead.
_PARALLEL_THRESHOLD = 5_000


def _parse_xml_worker(args: tuple) -> tuple | None:
    """Parse one XML file. Returns a DB row tuple or None. Thread-safe."""
    xml_bytes, status, mv_date, target, collection_name = args
    try:
        law = parse_akn(xml_bytes, status=status, version_date=mv_date)
        if law is None:
            law = parse_nir(xml_bytes, status=status, version_date=mv_date)
    except Exception:
        law = None
    if not law:
        return None
    if target == "laws" or target == "originale_mv":
        return (
            law["urn"], law["title"], law["type"], law["date"],
            law["year"], law["text"], law["text_length"],
            law["article_count"], law["status"],
            collection_name, law["parsed_at"],
        )
    else:
        return (
            law["urn"], law["version_date"], law["title"],
            law["type"], law["year"], law["text"],
            law["text_length"], law["article_count"],
            collection_name, law["parsed_at"],
        )


def _import_zip(conn: sqlite3.Connection, zip_path: Path, collection_name: str,
                status: str, limit: int, target: str) -> int:
    """Parse ZIP and insert into DB. Returns count of new rows inserted."""
    print(f"  Parsing {zip_path.name} -> {target}...")
    BATCH = 1000

    with zipfile.ZipFile(zip_path, "r") as zf:
        xml_files = [f for f in zf.namelist() if f.endswith(".xml")]
        if limit:
            xml_files = xml_files[:limit]
        total = len(xml_files)
        print(f"    {total:,} XML files", end="", flush=True)

        inserted = 0
        skipped = 0
        t0 = time.time()

        def _mv_date_from_fname(fname: str) -> str | None:
            if target != "multivigente":
                return None
            basename = fname.rsplit("/", 1)[-1]
            m = re.search(r"_VIGENZA_(\d{4}-\d{2}-\d{2})", basename)
            if m:
                return m.group(1)
            m = re.match(r"(\d{4}-\d{2}-\d{2})_", basename)
            return m.group(1) if m else None

        use_parallel = total >= _PARALLEL_THRESHOLD

        if use_parallel:
            # Read ZIP entries in chunks; parse in parallel threads (lxml releases GIL).
            CHUNK = 2_000
            batch: list[tuple] = []
            processed = 0

            with ThreadPoolExecutor(max_workers=_PARSE_WORKERS) as executor:
                for chunk_start in range(0, total, CHUNK):
                    chunk_fnames = xml_files[chunk_start:chunk_start + CHUNK]
                    # Read bytes in main thread (ZipFile is not thread-safe)
                    work_items = []
                    for fname in chunk_fnames:
                        try:
                            xml_bytes = zf.read(fname)
                        except Exception:
                            xml_bytes = b""
                        mv_date = _mv_date_from_fname(fname)
                        work_items.append((xml_bytes, status, mv_date, target, collection_name))

                    # Parse in parallel
                    for row in executor.map(_parse_xml_worker, work_items):
                        processed += 1
                        if row is None:
                            skipped += 1
                        else:
                            batch.append(row)
                            if len(batch) >= BATCH:
                                if target == "laws":
                                    inserted += insert_laws_batch(conn, batch)
                                elif target == "originale_mv":
                                    inserted += insert_original_batch(conn, batch)
                                else:
                                    inserted += insert_mv_batch(conn, batch)
                                batch.clear()

                    if processed % 10_000 < CHUNK:
                        elapsed = time.time() - t0
                        rate = processed / elapsed if elapsed > 0 else 1
                        eta = (total - processed) / rate
                        print(f"\n    [{processed:,}/{total:,}] inserted={inserted:,}  "
                              f"ETA={eta:.0f}s", end="", flush=True)

            if batch:
                if target == "laws":
                    inserted += insert_laws_batch(conn, batch)
                elif target == "originale_mv":
                    inserted += insert_original_batch(conn, batch)
                else:
                    inserted += insert_mv_batch(conn, batch)

        else:
            # Small ZIP: simple sequential parse
            batch: list[tuple] = []
            for i, fname in enumerate(xml_files):
                try:
                    xml_data = zf.read(fname)
                    mv_date = _mv_date_from_fname(fname)
                    row = _parse_xml_worker((xml_data, status, mv_date, target, collection_name))
                except Exception:
                    row = None

                if row:
                    batch.append(row)
                else:
                    skipped += 1

                if len(batch) >= BATCH:
                    if target == "laws":
                        inserted += insert_laws_batch(conn, batch)
                    elif target == "originale_mv":
                        inserted += insert_original_batch(conn, batch)
                    else:
                        inserted += insert_mv_batch(conn, batch)
                    batch.clear()

                if (i + 1) % 10_000 == 0:
                    elapsed = time.time() - t0
                    rate = (i + 1) / elapsed
                    eta = (total - i - 1) / rate
                    print(f"\n    [{i+1:,}/{total:,}] inserted={inserted:,}  "
                          f"ETA={eta:.0f}s", end="", flush=True)

            if batch:
                if target == "laws":
                    inserted += insert_laws_batch(conn, batch)
                elif target == "originale_mv":
                    inserted += insert_original_batch(conn, batch)
                else:
                    inserted += insert_mv_batch(conn, batch)

    elapsed = time.time() - t0
    workers_note = f", {_PARSE_WORKERS} workers" if use_parallel else ""
    print(f"\n    Done: {inserted:,} inserted, {skipped:,} skipped ({elapsed:.0f}s{workers_note})")
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def print_summary():
    print("\n" + "=" * 60)
    print("VOOM BUILD SUMMARY")
    print("=" * 60)

    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH))
        n_force = conn.execute(
            "SELECT COUNT(*) FROM laws WHERE status='in_force'"
        ).fetchone()[0]
        n_abr = conn.execute(
            "SELECT COUNT(*) FROM laws WHERE status='abrogated'"
        ).fetchone()[0]
        n_tot = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
        conn.close()
        size = DB_PATH.stat().st_size / 1e6
        print(f"  laws.db ({size:.0f} MB)")
        print(f"    in_force (vigente):  {n_force:>8,}")
        print(f"    abrogated:           {n_abr:>8,}")
        print(f"    TOTAL:               {n_tot:>8,}")
    else:
        print("  laws.db: NOT BUILT")

    if MV_DB_PATH.exists():
        conn = sqlite3.connect(str(MV_DB_PATH))
        n_mv = conn.execute("SELECT COUNT(*) FROM law_versions").fetchone()[0]
        conn.close()
        size = MV_DB_PATH.stat().st_size / 1e6
        print(f"\n  multivigente.db ({size:.0f} MB)")
        print(f"    amendment versions:  {n_mv:>8,}")
    else:
        print("\n  multivigente.db: NOT BUILT (run --steps multivigente)")

    print("\n  Next steps:")
    print("    py deploy_hf.py --token $HF_TOKEN --skip-space   # upload dataset")
    print("    py deploy_hf.py --token $HF_TOKEN                # full redeploy")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

VALID_STEPS = {"vigente", "abrogati", "multivigente", "originale_mv"}


def main():
    parser = argparse.ArgumentParser(
        description="Build the VOOM (Vigente+abOrOgati+Multivigente) corpus"
    )
    parser.add_argument(
        "--steps", nargs="+", choices=sorted(VALID_STEPS), metavar="STEP",
        default=["vigente", "abrogati"],
        help="Steps to run (default: vigente abrogati). Add 'multivigente' for M track and 'originale_mv' for O-track in multivigente.db."
    )
    parser.add_argument(
        "--skip-download", action="store_true",
        help="Reuse existing ZIP files if ETag is unchanged"
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Limit to N laws per step (0=all; use for testing)"
    )
    args = parser.parse_args()

    steps = set(args.steps)
    print(f"VOOM Builder — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Steps: {', '.join(sorted(steps))}")
    print(f"Lab root: {LAB_ROOT}")
    print(f"Search root: {SEARCH_ROOT} "
          f"({'found' if SEARCH_ROOT.exists() else 'NOT FOUND'})")

    if "vigente" in steps:
        step_vigente(args.skip_download, args.limit)

    if "abrogati" in steps:
        step_abrogati(args.skip_download, args.limit)

    if "multivigente" in steps:
        step_multivigente(args.skip_download, args.limit)

    if "originale_mv" in steps:
        step_originale_mv(args.skip_download, args.limit)

    print_summary()


if __name__ == "__main__":
    main()
