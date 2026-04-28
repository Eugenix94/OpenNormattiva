#!/usr/bin/env python3
"""
Build normattiva-lab dataset artifacts from pipeline JSONL outputs.

Goals:
1. Keep `laws` table aligned with latest vigente data for app compatibility.
2. Persist all variants (vigente, originale, multivigente) in `law_variants`.
3. Produce a downloadable overnight bundle for the lab space/dataset.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, Tuple


VARIANT_FILES = {
    "vigente": "laws_vigente.jsonl",
    "originale": "laws_originale.jsonl",
    "multivigente": "laws_multivigente.jsonl",
}


def iter_jsonl(path: Path) -> Iterator[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS law_variants (
            urn TEXT NOT NULL,
            variant TEXT NOT NULL,
            title TEXT,
            type TEXT,
            date TEXT,
            year INTEGER,
            text TEXT,
            text_length INTEGER DEFAULT 0,
            article_count INTEGER DEFAULT 0,
            status TEXT,
            source_collection TEXT,
            parsed_at TEXT,
            citations_json TEXT DEFAULT '[]',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (urn, variant)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_law_variants_variant ON law_variants(variant)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_law_variants_status ON law_variants(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_law_variants_year ON law_variants(year)")


def resolve_status(variant: str, source_collection: str) -> str:
    source = (source_collection or "").lower()
    if "abrogat" in source:
        return "abrogated"
    if variant == "vigente":
        return "in_force"
    if variant == "multivigente":
        return "multi_version"
    if variant == "originale":
        return "original_text"
    return "unknown"


def normalize_law(law: Dict, variant: str) -> Tuple:
    source_collection = law.get("source_collection", "")
    parsed_at = law.get("parsed_at") or datetime.now(timezone.utc).isoformat()
    citations = law.get("citations", [])
    status = law.get("status") or resolve_status(variant, source_collection)

    return (
        law.get("urn"),
        variant,
        law.get("title", ""),
        law.get("type", ""),
        law.get("date"),
        int(law.get("year")) if law.get("year") not in (None, "") else None,
        law.get("text", ""),
        int(law.get("text_length", 0) or 0),
        int(law.get("article_count", 0) or 0),
        status,
        source_collection,
        parsed_at,
        json.dumps(citations, ensure_ascii=False),
    )


def upsert_variant_rows(conn: sqlite3.Connection, variant: str, jsonl_path: Path) -> int:
    rows = []
    count = 0
    for law in iter_jsonl(jsonl_path):
        urn = law.get("urn")
        if not urn:
            continue
        rows.append(normalize_law(law, variant))
        if len(rows) >= 500:
            conn.executemany(
                """
                INSERT INTO law_variants
                    (urn, variant, title, type, date, year, text, text_length, article_count,
                     status, source_collection, parsed_at, citations_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(urn, variant) DO UPDATE SET
                    title=excluded.title,
                    type=excluded.type,
                    date=excluded.date,
                    year=excluded.year,
                    text=excluded.text,
                    text_length=excluded.text_length,
                    article_count=excluded.article_count,
                    status=excluded.status,
                    source_collection=excluded.source_collection,
                    parsed_at=excluded.parsed_at,
                    citations_json=excluded.citations_json,
                    updated_at=CURRENT_TIMESTAMP
                """,
                rows,
            )
            conn.commit()
            count += len(rows)
            rows.clear()

    if rows:
        conn.executemany(
            """
            INSERT INTO law_variants
                (urn, variant, title, type, date, year, text, text_length, article_count,
                 status, source_collection, parsed_at, citations_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(urn, variant) DO UPDATE SET
                title=excluded.title,
                type=excluded.type,
                date=excluded.date,
                year=excluded.year,
                text=excluded.text,
                text_length=excluded.text_length,
                article_count=excluded.article_count,
                status=excluded.status,
                source_collection=excluded.source_collection,
                parsed_at=excluded.parsed_at,
                citations_json=excluded.citations_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            rows,
        )
        conn.commit()
        count += len(rows)

    return count


def refresh_laws_from_vigente(conn: sqlite3.Connection, vigente_jsonl: Path) -> int:
    rows = []
    count = 0
    for law in iter_jsonl(vigente_jsonl):
        urn = law.get("urn")
        if not urn:
            continue
        rows.append(
            (
                urn,
                law.get("title", ""),
                law.get("type", ""),
                law.get("date"),
                int(law.get("year")) if law.get("year") not in (None, "") else None,
                law.get("text", ""),
                int(law.get("text_length", 0) or 0),
                int(law.get("article_count", 0) or 0),
                "in_force",
                law.get("source_collection", ""),
                law.get("parsed_at") or datetime.now(timezone.utc).isoformat(),
            )
        )

        if len(rows) >= 500:
            conn.executemany(
                """
                INSERT INTO laws
                    (urn, title, type, date, year, text, text_length, article_count, status, source_collection, parsed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(urn) DO UPDATE SET
                    title=excluded.title,
                    type=excluded.type,
                    date=excluded.date,
                    year=excluded.year,
                    text=excluded.text,
                    text_length=excluded.text_length,
                    article_count=excluded.article_count,
                    status=excluded.status,
                    source_collection=excluded.source_collection,
                    parsed_at=excluded.parsed_at
                """,
                rows,
            )
            conn.commit()
            count += len(rows)
            rows.clear()

    if rows:
        conn.executemany(
            """
            INSERT INTO laws
                (urn, title, type, date, year, text, text_length, article_count, status, source_collection, parsed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(urn) DO UPDATE SET
                title=excluded.title,
                type=excluded.type,
                date=excluded.date,
                year=excluded.year,
                text=excluded.text,
                text_length=excluded.text_length,
                article_count=excluded.article_count,
                status=excluded.status,
                source_collection=excluded.source_collection,
                parsed_at=excluded.parsed_at
            """,
            rows,
        )
        conn.commit()
        count += len(rows)

    return count


def apply_abrogated_from_originale(conn: sqlite3.Connection, originale_jsonl: Path) -> int:
    """Mark/insert abrogated laws from originale-abrogati feed."""
    rows = []
    count = 0
    for law in iter_jsonl(originale_jsonl):
        urn = law.get("urn")
        if not urn:
            continue

        source_collection = (law.get("source_collection") or "")
        if "abrogat" not in source_collection.lower():
            continue

        rows.append(
            (
                urn,
                law.get("title", ""),
                law.get("type", ""),
                law.get("date"),
                int(law.get("year")) if law.get("year") not in (None, "") else None,
                law.get("text", ""),
                int(law.get("text_length", 0) or 0),
                int(law.get("article_count", 0) or 0),
                "abrogated",
                source_collection,
                law.get("parsed_at") or datetime.now(timezone.utc).isoformat(),
            )
        )

        if len(rows) >= 500:
            conn.executemany(
                """
                INSERT INTO laws
                    (urn, title, type, date, year, text, text_length, article_count, status, source_collection, parsed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(urn) DO UPDATE SET
                    title=excluded.title,
                    type=excluded.type,
                    date=excluded.date,
                    year=excluded.year,
                    text=excluded.text,
                    text_length=excluded.text_length,
                    article_count=excluded.article_count,
                    status='abrogated',
                    source_collection=excluded.source_collection,
                    parsed_at=excluded.parsed_at
                """,
                rows,
            )
            conn.commit()
            count += len(rows)
            rows.clear()

    if rows:
        conn.executemany(
            """
            INSERT INTO laws
                (urn, title, type, date, year, text, text_length, article_count, status, source_collection, parsed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(urn) DO UPDATE SET
                title=excluded.title,
                type=excluded.type,
                date=excluded.date,
                year=excluded.year,
                text=excluded.text,
                text_length=excluded.text_length,
                article_count=excluded.article_count,
                status='abrogated',
                source_collection=excluded.source_collection,
                parsed_at=excluded.parsed_at
            """,
            rows,
        )
        conn.commit()
        count += len(rows)

    return count


def export_bundle(conn: sqlite3.Connection, bundle_path: Path) -> int:
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(bundle_path, "w", encoding="utf-8") as out:
        cursor = conn.execute(
            """
            SELECT urn, variant, title, type, date, year, text, text_length,
                   article_count, status, source_collection, parsed_at, citations_json
            FROM law_variants
            ORDER BY variant, urn
            """
        )
        for row in cursor:
            obj = {
                "urn": row[0],
                "variant": row[1],
                "title": row[2],
                "type": row[3],
                "date": row[4],
                "year": row[5],
                "text": row[6],
                "text_length": row[7],
                "article_count": row[8],
                "status": row[9],
                "source_collection": row[10],
                "parsed_at": row[11],
                "citations": json.loads(row[12] or "[]"),
            }
            out.write(json.dumps(obj, ensure_ascii=False) + "\n")
            written += 1
    return written


def build_report(conn: sqlite3.Connection, report_path: Path, processed_dir: Path) -> Dict:
    report_path.parent.mkdir(parents=True, exist_ok=True)

    laws_total = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    laws_abrogated = conn.execute("SELECT COUNT(*) FROM laws WHERE status='abrogated'").fetchone()[0]

    by_variant = {}
    for variant, count in conn.execute(
        "SELECT variant, COUNT(*) FROM law_variants GROUP BY variant ORDER BY variant"
    ):
        by_variant[variant] = count

    by_status = {}
    for status, count in conn.execute(
        "SELECT status, COUNT(*) FROM law_variants GROUP BY status ORDER BY status"
    ):
        by_status[status or ""] = count

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_summary": {
            "laws_total": laws_total,
            "laws_abrogated": laws_abrogated,
            "law_variants_total": sum(by_variant.values()),
            "variants": by_variant,
            "variant_status": by_status,
        },
        "processed_files": {
            name: str((processed_dir / filename).exists())
            for name, filename in VARIANT_FILES.items()
        },
    }

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync normattiva-lab DB with all variants and bundle exports")
    parser.add_argument("--db", default="data/laws.db", help="Path to SQLite DB")
    parser.add_argument("--processed-dir", default="data/processed", help="Processed JSONL directory")
    parser.add_argument("--bundle-path", default="data/processed/laws_lab_bundle.jsonl", help="Output bundle JSONL")
    parser.add_argument("--report-path", default="data/processed/normattiva_lab_sync_report.json", help="Output JSON report")
    parser.add_argument("--skip-refresh-laws", action="store_true", help="Do not update main laws table from vigente JSONL")
    parser.add_argument("--skip-bundle", action="store_true", help="Do not write bundle JSONL")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    processed_dir = Path(args.processed_dir)

    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        ensure_schema(conn)
        print("[sync] law_variants schema ready")

        if not args.skip_refresh_laws:
            vigente_path = processed_dir / VARIANT_FILES["vigente"]
            if vigente_path.exists():
                refreshed = refresh_laws_from_vigente(conn, vigente_path)
                print(f"[sync] refreshed laws from vigente: {refreshed:,}")
            else:
                print(f"[sync] vigente file missing, skipped laws refresh: {vigente_path}")

            originale_path = processed_dir / VARIANT_FILES["originale"]
            if originale_path.exists():
                marked = apply_abrogated_from_originale(conn, originale_path)
                print(f"[sync] applied abrogated status from originale: {marked:,}")
            else:
                print(f"[sync] originale file missing, skipped abrogated mapping: {originale_path}")

            conn.execute("INSERT INTO laws_fts(laws_fts) VALUES('rebuild')")
            conn.commit()
            print("[sync] rebuilt laws_fts")

        for variant, filename in VARIANT_FILES.items():
            path = processed_dir / filename
            if not path.exists():
                print(f"[sync] missing file for {variant}, skip: {path}")
                continue
            upserted = upsert_variant_rows(conn, variant, path)
            print(f"[sync] upserted {upserted:,} rows into law_variants ({variant})")

        if not args.skip_bundle:
            bundle_written = export_bundle(conn, Path(args.bundle_path))
            print(f"[sync] bundle rows written: {bundle_written:,}")

        report = build_report(conn, Path(args.report_path), processed_dir)
        print("[sync] report written:")
        print(json.dumps(report["db_summary"], ensure_ascii=False, indent=2))
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
