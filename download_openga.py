#!/usr/bin/env python3
"""
Download OpenGA (Giustizia Amministrativa) datasets via CKAN API and store into laws.db.

OpenGA publishes sentenze, ordinanze, pareri and other acts from:
  - Consiglio di Stato (CdS)
  - CGA Sicilia
  - All TAR courts

API base: https://openga.giustizia-amministrativa.it/api/3/action
License: CC-BY 4.0

Usage:
  python download_openga.py --catalog          # fetch & store catalog only
  python download_openga.py --download         # fetch catalog + download CSV data
  python download_openga.py --download --limit 50   # limit datasets fetched
  python download_openga.py --stats            # print DB stats
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import requests

CKAN_BASE = "https://openga.giustizia-amministrativa.it/api/3/action"
DEFAULT_DB = "data/laws.db"

# Court name normalisations (package name prefix → display name)
COURT_MAP: dict[str, str] = {
    "cds": "Consiglio di Stato",
    "cga": "CGA Sicilia",
    "tar-lazio-roma": "TAR Lazio Roma",
    "tar-lazio": "TAR Lazio",
    "tar-lombardia": "TAR Lombardia",
    "tar-campania": "TAR Campania",
    "tar-veneto": "TAR Veneto",
    "tar-toscana": "TAR Toscana",
    "tar-piemonte": "TAR Piemonte",
    "tar-sicilia": "TAR Sicilia",
    "tar-puglia": "TAR Puglia",
    "tar-emilia-romagna": "TAR Emilia Romagna",
    "tar-liguria": "TAR Liguria",
    "tar-marche": "TAR Marche",
    "tar-umbria": "TAR Umbria",
    "tar-abruzzo": "TAR Abruzzo",
    "tar-basilicata": "TAR Basilicata",
    "tar-calabria": "TAR Calabria",
    "tar-molise": "TAR Molise",
    "tar-sardegna": "TAR Sardegna",
    "tar-valle-d-aosta": "TAR Valle d'Aosta",
    "tar-trentino": "TAR Trentino",
}

DATASET_TYPE_KEYWORDS = {
    "sentenze": ["sentenze", "sentenza"],
    "ordinanze": ["ordinanze", "ordinanza"],
    "pareri": ["pareri", "parere"],
    "decreti": ["decreti", "decreto"],
    "provvedimenti": ["provvedimenti", "provvedimento"],
}

SESSION = requests.Session()
SESSION.headers["User-Agent"] = "OpenNormattiva/1.0 (+https://github.com/diatribe00/OpenNormattiva)"


def ckan_get(action: str, params: dict | None = None, retries: int = 3) -> Any:
    url = f"{CKAN_BASE}/{action}"
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params or {}, timeout=60)
            r.raise_for_status()
            data = r.json()
            if not data.get("success"):
                raise ValueError(f"CKAN error: {data.get('error')}")
            return data["result"]
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise
            print(f"  Retry {attempt + 1}/{retries} for {url}: {exc}")
            time.sleep(2 ** attempt)


def detect_court(package_id: str, title: str) -> str:
    pid = package_id.lower()
    for prefix, name in COURT_MAP.items():
        if pid.startswith(prefix + "-") or pid == prefix:
            return name
    # fallback: try to extract from title
    if "consiglio di stato" in title.lower():
        return "Consiglio di Stato"
    if "cga" in pid or "cga" in title.lower():
        return "CGA Sicilia"
    if "tar" in pid:
        m = re.search(r"tar[- ](\w+(?:[- ]\w+)?)", pid)
        return f"TAR {m.group(1).title()}" if m else "TAR"
    return "Altro"


def detect_type(package_id: str, title: str) -> str:
    combined = (package_id + " " + title).lower()
    for dtype, keywords in DATASET_TYPE_KEYWORDS.items():
        if any(k in combined for k in keywords):
            return dtype
    return "altro"


def pick_best_resource(resources: list[dict]) -> dict | None:
    """Prefer JSON, then CSV, then ODS for download."""
    for fmt in ("json", "csv", "ods"):
        for r in resources:
            if (r.get("format") or "").lower() == fmt and r.get("url"):
                return r
    # fallback: any downloadable
    for r in resources:
        if r.get("url"):
            return r
    return None


def fetch_catalog(limit: int | None = None) -> list[dict]:
    """Fetch all package metadata from CKAN."""
    names: list[str] = ckan_get("package_list")
    if limit:
        names = names[:limit]
    print(f"Fetching metadata for {len(names)} packages...")

    catalog: list[dict] = []
    for i, name in enumerate(names):
        try:
            pkg = ckan_get("package_show", {"id": name})
            resources = pkg.get("resources", [])
            best = pick_best_resource(resources)
            court = detect_court(pkg["name"], pkg.get("title", ""))
            dtype = detect_type(pkg["name"], pkg.get("title", ""))
            catalog.append(
                {
                    "package_id": pkg["name"],
                    "title": pkg.get("title", ""),
                    "court": court,
                    "dataset_type": dtype,
                    "resource_url": best["url"] if best else "",
                    "resource_format": (best.get("format") or "").upper() if best else "",
                    "record_count": 0,
                    "last_updated": pkg.get("metadata_modified", ""),
                    "license": pkg.get("license_title", "CC-BY 4.0"),
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            )
        except Exception as exc:
            print(f"  [skip] {name}: {exc}")

        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(names)} packages fetched")

    return catalog


def store_catalog(conn: sqlite3.Connection, catalog: list[dict]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO openga_catalog
        (package_id, title, court, dataset_type, resource_url, resource_format,
         record_count, last_updated, license, fetched_at)
        VALUES (:package_id, :title, :court, :dataset_type, :resource_url, :resource_format,
                :record_count, :last_updated, :license, :fetched_at)
        """,
        catalog,
    )
    conn.commit()
    print(f"Stored {len(catalog)} catalog entries.")


def normalize_row(row: dict, package_id: str, court: str) -> dict:
    """Normalise a CSV/JSON row into openga_sentenze columns."""
    # Try common field name patterns across datasets
    def get(*keys: str) -> str:
        for k in keys:
            for actual_key in row:
                if actual_key.lower().replace(" ", "_") == k.lower().replace(" ", "_"):
                    v = row[actual_key]
                    if v and str(v).strip():
                        return str(v).strip()
        return ""

    anno_raw = get("anno", "year", "anno_deposito", "data_deposito")
    anno = None
    if anno_raw:
        m = re.search(r"\d{4}", anno_raw)
        if m:
            anno = int(m.group())

    return {
        "package_id": package_id,
        "court": court,
        "anno": anno,
        "numero": get("numero", "numero_sentenza", "number"),
        "data_deposito": get("data_deposito", "data", "date", "data_pubblicazione"),
        "sezione": get("sezione", "section"),
        "oggetto": get("oggetto", "materia", "subject", "titolo")[:2000],
        "esito": get("esito", "dispositivo", "outcome")[:500],
        "source_url": get("url", "link", "permalink"),
        "raw_json": json.dumps(row, ensure_ascii=False)[:4000],
        "imported_at": datetime.now(UTC).isoformat(),
    }


def download_dataset_csv(url: str, package_id: str, court: str) -> list[dict]:
    """Download a CSV resource and return normalized rows."""
    try:
        r = SESSION.get(url, timeout=120, stream=True)
        r.raise_for_status()
        content = r.content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(content))
        rows = [normalize_row(dict(row), package_id, court) for row in reader]
        return rows
    except Exception as exc:
        print(f"  [CSV error] {url}: {exc}")
        return []


def download_dataset_json(url: str, package_id: str, court: str) -> list[dict]:
    """Download a JSON resource and return normalized rows."""
    try:
        r = SESSION.get(url, timeout=120)
        r.raise_for_status()
        data = r.json()
        # OpenGA JSON is often {"fields": [...], "records": [...]}
        if isinstance(data, dict) and "records" in data:
            records = data["records"]
        elif isinstance(data, list):
            records = data
        else:
            records = [data]
        return [normalize_row(rec if isinstance(rec, dict) else {"value": rec}, package_id, court) for rec in records]
    except Exception as exc:
        print(f"  [JSON error] {url}: {exc}")
        return []


def download_all(conn: sqlite3.Connection, priority_types: list[str] | None = None, max_per_type: int | None = None) -> None:
    """Download CSV/JSON resources for catalog entries and store in openga_sentenze."""
    where = ""
    params: list[Any] = []
    if priority_types:
        placeholders = ",".join("?" * len(priority_types))
        where = f"WHERE dataset_type IN ({placeholders})"
        params = list(priority_types)

    rows = conn.execute(
        f"SELECT package_id, court, dataset_type, resource_url, resource_format FROM openga_catalog {where} ORDER BY court, dataset_type",
        params,
    ).fetchall()

    print(f"Downloading data for {len(rows)} datasets...")
    type_counts: dict[str, int] = {}
    total_inserted = 0

    for pkg_id, court, dtype, url, fmt in rows:
        if not url:
            continue
        if max_per_type and type_counts.get(dtype, 0) >= max_per_type:
            continue

        print(f"  {court} / {dtype}: {pkg_id} [{fmt}]")
        if fmt == "JSON":
            data_rows = download_dataset_json(url, pkg_id, court)
        elif fmt in ("CSV", ""):
            data_rows = download_dataset_csv(url, pkg_id, court)
        else:
            # Try CSV by default for ODS etc.
            data_rows = download_dataset_csv(url, pkg_id, court)

        if data_rows:
            conn.executemany(
                """
                INSERT INTO openga_sentenze
                (package_id, court, anno, numero, data_deposito, sezione, oggetto, esito, source_url, raw_json, imported_at)
                VALUES (:package_id, :court, :anno, :numero, :data_deposito, :sezione, :oggetto, :esito, :source_url, :raw_json, :imported_at)
                """,
                data_rows,
            )
            conn.execute(
                "UPDATE openga_catalog SET record_count = ? WHERE package_id = ?",
                (len(data_rows), pkg_id),
            )
            conn.commit()
            total_inserted += len(data_rows)
            type_counts[dtype] = type_counts.get(dtype, 0) + 1
            print(f"    -> {len(data_rows)} records inserted")
        else:
            print("    -> 0 records (skipped or error)")

        time.sleep(0.3)  # polite rate limiting

    print(f"\nTotal records inserted: {total_inserted}")


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS openga_catalog (
            package_id TEXT PRIMARY KEY,
            title TEXT,
            court TEXT,
            dataset_type TEXT,
            resource_url TEXT,
            resource_format TEXT,
            record_count INTEGER DEFAULT 0,
            last_updated TEXT,
            license TEXT,
            fetched_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS openga_sentenze (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_id TEXT,
            court TEXT,
            anno INTEGER,
            numero TEXT,
            data_deposito TEXT,
            sezione TEXT,
            oggetto TEXT,
            esito TEXT,
            source_url TEXT,
            raw_json TEXT,
            imported_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_openga_court ON openga_sentenze(court)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_openga_anno ON openga_sentenze(anno)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_openga_pkg ON openga_sentenze(package_id)")
    conn.commit()


def print_stats(conn: sqlite3.Connection) -> None:
    cat = conn.execute("SELECT COUNT(*) FROM openga_catalog").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM openga_sentenze").fetchone()[0]
    print(f"\n{'='*50}")
    print(f"OpenGA catalog entries : {cat}")
    print(f"OpenGA sentenze rows   : {total}")
    if total:
        by_court = conn.execute(
            "SELECT court, COUNT(*) as n FROM openga_sentenze GROUP BY court ORDER BY n DESC LIMIT 15"
        ).fetchall()
        print("\nBy court:")
        for court, n in by_court:
            print(f"  {court:<40} {n:>8,}")
        by_type = conn.execute(
            "SELECT dataset_type, COUNT(*) as n FROM openga_sentenze s "
            "JOIN openga_catalog c ON s.package_id=c.package_id "
            "GROUP BY dataset_type ORDER BY n DESC"
        ).fetchall()
        print("\nBy type:")
        for dtype, n in by_type:
            print(f"  {dtype:<40} {n:>8,}")
        years = conn.execute(
            "SELECT anno, COUNT(*) FROM openga_sentenze WHERE anno IS NOT NULL GROUP BY anno ORDER BY anno DESC LIMIT 10"
        ).fetchall()
        print("\nRecent years:")
        for yr, n in years:
            print(f"  {yr}: {n:,}")
    print("=" * 50)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download OpenGA administrative court data")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--catalog", action="store_true", help="Fetch CKAN catalog only (no CSV download)")
    parser.add_argument("--download", action="store_true", help="Fetch catalog + download records")
    parser.add_argument("--skip-catalog", action="store_true", help="Skip catalog fetch (use existing DB catalog)")
    parser.add_argument("--stats", action="store_true", help="Print DB stats and exit")
    parser.add_argument("--limit", type=int, help="Limit number of catalog packages to process")
    parser.add_argument("--types", nargs="+", default=["sentenze", "ordinanze", "pareri"],
                        help="Dataset types to download (default: sentenze ordinanze pareri)")
    parser.add_argument("--max-per-type", type=int, help="Max datasets to download per type")
    args = parser.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    ensure_tables(conn)

    if args.stats:
        print_stats(conn)
        conn.close()
        return 0

    if not args.catalog and not args.download:
        parser.error("Select one mode: --catalog, --download, or --stats")

    if args.skip_catalog:
        existing = conn.execute("SELECT COUNT(*) FROM openga_catalog").fetchone()[0]
        print(f"Skipping catalog fetch, using existing {existing} entries.")
        catalog = None
    else:
        catalog = fetch_catalog(limit=args.limit)
        store_catalog(conn, catalog)

    if args.download:
        print(f"\nDownloading records for types: {args.types}")
        # Clear existing data to avoid duplicates on re-run
        conn.execute("DELETE FROM openga_sentenze")
        conn.commit()
        download_all(conn, priority_types=args.types, max_per_type=args.max_per_type)

    print_stats(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
