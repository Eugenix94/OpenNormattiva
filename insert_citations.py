#!/usr/bin/env python3
"""
Insert citations from JSONL into the SQLite DB.

Builds the laws DB from JSONL if it doesn't exist, then inserts all
citation relationships extracted by reenrich_citations.py.
"""
import argparse
import os
import sqlite3
import sys
import json
from pathlib import Path

JSONL = Path("data/processed/laws_vigente.jsonl")
DB_PATH = Path("data/laws.db")


def build_db_from_jsonl(db_path: Path, jsonl: Path):
    """Build (or rebuild) DB from JSONL then insert laws."""
    sys.path.insert(0, str(Path(__file__).parent))
    from core.db import LawDatabase
    if db_path.exists():
        os.remove(db_path)
        print(f"Removed old {db_path}")
    db = LawDatabase(db_path)
    count = db.insert_laws_from_jsonl(jsonl)
    print(f"Inserted {count} laws")
    db.close()


def insert_citations(db_path: Path, jsonl: Path):
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    print("Inserting citations (FK checks off)...")
    inserted = 0

    with open(jsonl, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            law = json.loads(line)
            citing_urn = law.get("urn")
            if not citing_urn:
                continue
            for cit in law.get("citations", []):
                if isinstance(cit, dict):
                    cited_urn = cit.get("target_urn", "")
                    context = cit.get("ref", "")
                    art = cit.get("article")
                    if art:
                        context = f"art. {art} -- {context}"
                else:
                    cited_urn = str(cit)
                    context = ""
                if cited_urn:
                    conn.execute(
                        "INSERT OR IGNORE INTO citations (citing_urn, cited_urn, count, context) VALUES (?, ?, 1, ?)",
                        (citing_urn, cited_urn, context),
                    )
                    inserted += 1
            if (i + 1) % 10000 == 0:
                conn.commit()
                print(f"  ... {i+1} laws, {inserted} citations inserted")

    conn.commit()
    print(f"Done: {inserted} citations inserted")
    r = conn.execute("SELECT COUNT(*) FROM citations").fetchone()
    print(f"Total citations in DB: {r[0]:,}")

    # Update law_metadata.citation_count_incoming using canonical URN matching.
    # citations.cited_urn uses YYYY;N format; laws.urn uses YYYY-MM-DD;N format.
    print("Rebuilding citation_count_incoming in law_metadata...")
    import re as _re_cit
    from collections import Counter

    def _canon(urn):
        return _re_cit.sub(r':(\d{4})-\d{2}-\d{2};', r':\1;', urn or '')

    canon_map = {}
    for (urn,) in conn.execute("SELECT urn FROM laws WHERE urn IS NOT NULL").fetchall():
        c = _canon(urn)
        if c and c not in canon_map:
            canon_map[c] = urn

    counts: Counter = Counter()
    for cited_urn, cnt in conn.execute(
        "SELECT cited_urn, count FROM citations WHERE cited_urn IS NOT NULL"
    ).fetchall():
        full_urn = canon_map.get(cited_urn)
        if full_urn:
            counts[full_urn] += max(int(cnt or 0), 1)

    conn.executemany(
        "UPDATE law_metadata SET citation_count_incoming = ? WHERE urn = ?",
        [(c, u) for u, c in counts.items()],
    )
    conn.commit()
    matched = conn.execute("SELECT COUNT(*) FROM law_metadata WHERE citation_count_incoming > 0").fetchone()[0]
    print(f"Updated citation_count_incoming for {matched:,} laws")
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=str(JSONL))
    ap.add_argument("--db",   default=str(DB_PATH))
    ap.add_argument("--rebuild", action="store_true",
                    help="Force a full DB rebuild from JSONL first")
    args = ap.parse_args()

    db_path = Path(args.db)
    jsonl   = Path(args.jsonl)

    if not jsonl.exists():
        print(f"ERROR: {jsonl} not found")
        sys.exit(1)

    if args.rebuild or not db_path.exists():
        print("Building DB from JSONL...")
        build_db_from_jsonl(db_path, jsonl)
    else:
        # Clear existing citations before re-inserting
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM citations")
        conn.commit()
        conn.close()
        print("Cleared existing citations")

    insert_citations(db_path, jsonl)


if __name__ == "__main__":
    main()
