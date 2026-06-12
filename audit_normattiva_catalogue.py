#!/usr/bin/env python3
"""Unified Normattiva audit: website/API catalogue, DB coverage, download checks.

This replaces ad-hoc _probe_* scripts with one reusable report.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

BASE_API = "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1"
HEADERS = {
    "User-Agent": "NormattivaVOOM/1.0 (audit)",
    "Referer": "https://dati.normattiva.it/",
    "Accept": "*/*",
}


def api_get(path: str):
    req = urllib.request.Request(f"{BASE_API}{path}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def fetch_catalogue() -> list[dict]:
    return api_get("/api/v1/collections/collection-predefinite")


def read_db_stats(db_path: Path) -> tuple[int, dict[str, int], dict[str, int], int]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    total_distinct = int(cur.execute("SELECT COUNT(DISTINCT law_urn) FROM law_versions").fetchone()[0])

    has_sources = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='law_version_sources'"
    ).fetchone()
    if has_sources:
        by_distinct = dict(
            cur.execute(
                "SELECT source_collection, COUNT(DISTINCT law_urn) FROM law_version_sources GROUP BY source_collection"
            ).fetchall()
        )
    else:
        by_distinct = dict(
            cur.execute(
                "SELECT source_collection, COUNT(DISTINCT law_urn) FROM law_versions GROUP BY source_collection"
            ).fetchall()
        )

    by_label = dict(
        cur.execute(
            "SELECT source_collection, COUNT(DISTINCT law_urn) FROM law_versions GROUP BY source_collection"
        ).fetchall()
    )

    has_original = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='original_acts'"
    ).fetchone()
    original_count = 0
    if has_original:
        original_count = int(cur.execute("SELECT COUNT(*) FROM original_acts").fetchone()[0])

    conn.close()
    return total_distinct, {str(k): int(v) for k, v in by_distinct.items()}, {str(k): int(v) for k, v in by_label.items()}, original_count


def test_download(nome: str, formato: str, richiesta: str) -> int:
    params = urllib.parse.urlencode(
        {"nome": nome, "formato": formato, "formatoRichiesta": richiesta}
    )
    req = urllib.request.Request(
        f"{BASE_API}/api/v1/collections/download/collection-preconfezionata?{params}",
        headers=HEADERS,
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return int(resp.status)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Normattiva catalogue vs local DB")
    parser.add_argument("--db", default="data/multivigente.db", help="Path to multivigente.db")
    parser.add_argument("--test-downloads", action="store_true", help="Test GET download links for sample collections")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    catalogue = fetch_catalogue()
    m_items = [c for c in catalogue if c.get("formatoCollezione") == "M"]
    o_items = [c for c in catalogue if c.get("formatoCollezione") == "O"]
    v_items = [c for c in catalogue if c.get("formatoCollezione") == "V"]

    total_distinct, by_distinct, by_label, original_count = read_db_stats(db_path)

    logical_m: dict[str, int] = {}
    for c in m_items:
        name = c.get("nomeCollezione") or c.get("nome")
        if name:
            logical_m[name] = int(c.get("numeroAtti") or 0)

    print("=== LIVE CATALOGUE ===")
    print(f"  M entries: {len(m_items)}")
    print(f"  O entries: {len(o_items)}")
    print(f"  V entries: {len(v_items)}")
    print(f"  M logical collections: {len(logical_m)}")

    print("\n=== M-TRACK COVERAGE (effective via law_version_sources) ===")
    live_total = sum(logical_m.values())
    for name in sorted(logical_m):
        official = logical_m[name]
        local = by_distinct.get(name, 0)
        pct = (local / official * 100.0) if official else 0.0
        gap = official - local
        print(f"  {name:<48} {local:>6}/{official:<6}  gap={gap:>5}  pct={pct:6.2f}%")

    overall = (total_distinct / live_total * 100.0) if live_total else 0.0
    print("\n=== TOTALS ===")
    print(f"  Distinct URNs in law_versions: {total_distinct:,}")
    print(f"  Official M total:              {live_total:,}")
    print(f"  Overall M coverage:            {overall:.2f}%")

    abrogati = [c for c in o_items if (c.get("nomeCollezione") or c.get("nome")) == "Atti normativi abrogati (in originale)"]
    if abrogati:
        official_o = int(abrogati[0].get("numeroAtti") or 0)
        pct_o = (original_count / official_o * 100.0) if official_o else 0.0
        print("\n=== O-TRACK ABROGATI ===")
        print(f"  Official O acts:      {official_o:,}")
        print(f"  original_acts rows:   {original_count:,}")
        print(f"  Coverage in MV DB:    {pct_o:.2f}%")

    print("\n=== LABEL VS EFFECTIVE NOTE ===")
    print("  'by_label' (law_versions.source_collection) can undercount collection coverage.")
    print("  'effective' uses law_version_sources and is the accurate coverage metric.")

    if args.test_downloads:
        print("\n=== DOWNLOAD LINK TESTS (GET) ===")
        tests = [
            ("Codici", "AKN", "M"),
            ("Leggi costituzionali", "AKN", "O"),
            ("Leggi costituzionali", "AKN", "V"),
            ("Atti normativi abrogati (in originale)", "AKN", "O"),
        ]
        for nome, fmt, req in tests:
            try:
                code = test_download(nome, fmt, req)
                print(f"  [{req}] {nome}: {code}")
            except Exception as exc:
                print(f"  [{req}] {nome}: ERROR {exc}")


if __name__ == "__main__":
    main()
