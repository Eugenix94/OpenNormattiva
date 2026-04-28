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
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, Tuple


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS law_snapshot_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at TEXT NOT NULL,
            source_mode TEXT NOT NULL,
            laws_count INTEGER DEFAULT 0,
            abrogated_count INTEGER DEFAULT 0,
            variants_count INTEGER DEFAULT 0,
            multivigente_count INTEGER DEFAULT 0,
            notes TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS law_snapshot_latest (
            urn TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            status TEXT,
            title TEXT,
            type TEXT,
            date TEXT,
            year INTEGER,
            text_length INTEGER DEFAULT 0,
            article_count INTEGER DEFAULT 0,
            snapshot_run_id INTEGER,
            observed_at TEXT,
            FOREIGN KEY (snapshot_run_id) REFERENCES law_snapshot_runs(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS law_snapshot_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_run_id INTEGER NOT NULL,
            urn TEXT NOT NULL,
            change_type TEXT NOT NULL,
            previous_status TEXT,
            status TEXT,
            title TEXT,
            type TEXT,
            date TEXT,
            year INTEGER,
            text_length INTEGER DEFAULT 0,
            article_count INTEGER DEFAULT 0,
            content_hash TEXT NOT NULL,
            changed_fields TEXT DEFAULT '[]',
            observed_at TEXT NOT NULL,
            FOREIGN KEY (snapshot_run_id) REFERENCES law_snapshot_runs(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_law_snapshot_events_urn ON law_snapshot_events(urn)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_law_snapshot_events_run ON law_snapshot_events(snapshot_run_id)")


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


def build_law_hash(law: Dict) -> str:
    payload = "\u241f".join(
        [
            str(law.get("title", "")),
            str(law.get("type", "")),
            str(law.get("date", "")),
            str(law.get("year", "")),
            str(law.get("status", "")),
            str(law.get("text_length", 0)),
            str(law.get("article_count", 0)),
            str(law.get("text", "")),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_snapshot_history(conn: sqlite3.Connection, source_mode: str, notes: str | None = None) -> Dict[str, int]:
    observed_at = datetime.now(timezone.utc).isoformat()

    laws_count = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    abrogated_count = conn.execute("SELECT COUNT(*) FROM laws WHERE status='abrogated'").fetchone()[0]
    variants_count = conn.execute("SELECT COUNT(*) FROM law_variants").fetchone()[0]
    multivigente_count = conn.execute(
        "SELECT COUNT(*) FROM law_variants WHERE variant='multivigente'"
    ).fetchone()[0]

    cursor = conn.execute(
        """
        INSERT INTO law_snapshot_runs
            (snapshot_at, source_mode, laws_count, abrogated_count, variants_count, multivigente_count, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observed_at,
            source_mode,
            laws_count,
            abrogated_count,
            variants_count,
            multivigente_count,
            notes,
        ),
    )
    run_id = cursor.lastrowid

    latest_rows = conn.execute(
        "SELECT urn, content_hash, status, title, type, date, year, text_length, article_count FROM law_snapshot_latest"
    ).fetchall()
    latest = {
        row[0]: {
            "content_hash": row[1],
            "status": row[2],
            "title": row[3],
            "type": row[4],
            "date": row[5],
            "year": row[6],
            "text_length": row[7],
            "article_count": row[8],
        }
        for row in latest_rows
    }

    current_rows = conn.execute(
        "SELECT urn, title, type, date, year, status, text_length, article_count, text FROM laws"
    ).fetchall()

    changed = 0
    for row in current_rows:
        law = {
            "urn": row[0],
            "title": row[1],
            "type": row[2],
            "date": row[3],
            "year": row[4],
            "status": row[5],
            "text_length": row[6],
            "article_count": row[7],
            "text": row[8],
        }
        urn = law["urn"]
        content_hash = build_law_hash(law)
        prev = latest.get(urn)

        if prev is None:
            change_type = "added"
            changed_fields = ["initial_observation"]
            previous_status = None
        elif prev["content_hash"] != content_hash:
            changed_fields = []
            for field in ["status", "title", "type", "date", "year", "text_length", "article_count"]:
                if prev.get(field) != law.get(field):
                    changed_fields.append(field)
            if not changed_fields:
                changed_fields = ["text"]
            change_type = "status_changed" if "status" in changed_fields else "updated"
            previous_status = prev.get("status")
        else:
            continue

        conn.execute(
            """
            INSERT INTO law_snapshot_events
                (snapshot_run_id, urn, change_type, previous_status, status, title, type, date, year,
                 text_length, article_count, content_hash, changed_fields, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                urn,
                change_type,
                previous_status,
                law.get("status"),
                law.get("title"),
                law.get("type"),
                law.get("date"),
                law.get("year"),
                law.get("text_length", 0),
                law.get("article_count", 0),
                content_hash,
                json.dumps(changed_fields, ensure_ascii=False),
                observed_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO law_snapshot_latest
                (urn, content_hash, status, title, type, date, year, text_length, article_count, snapshot_run_id, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(urn) DO UPDATE SET
                content_hash=excluded.content_hash,
                status=excluded.status,
                title=excluded.title,
                type=excluded.type,
                date=excluded.date,
                year=excluded.year,
                text_length=excluded.text_length,
                article_count=excluded.article_count,
                snapshot_run_id=excluded.snapshot_run_id,
                observed_at=excluded.observed_at
            """,
            (
                urn,
                content_hash,
                law.get("status"),
                law.get("title"),
                law.get("type"),
                law.get("date"),
                law.get("year"),
                law.get("text_length", 0),
                law.get("article_count", 0),
                run_id,
                observed_at,
            ),
        )
        changed += 1

    conn.commit()
    return {
        "run_id": run_id,
        "laws_count": laws_count,
        "abrogated_count": abrogated_count,
        "variants_count": variants_count,
        "multivigente_count": multivigente_count,
        "changed_laws": changed,
    }


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
            "snapshot_runs": conn.execute("SELECT COUNT(*) FROM law_snapshot_runs").fetchone()[0],
            "snapshot_events": conn.execute("SELECT COUNT(*) FROM law_snapshot_events").fetchone()[0],
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
    parser.add_argument("--snapshot-mode", default="incremental", help="Label for snapshot history run")
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

        snapshot = record_snapshot_history(conn, args.snapshot_mode)
        print(
            "[sync] snapshot recorded: "
            f"run={snapshot['run_id']} changed={snapshot['changed_laws']:,} "
            f"laws={snapshot['laws_count']:,}"
        )

        report = build_report(conn, Path(args.report_path), processed_dir)
        print("[sync] report written:")
        print(json.dumps(report["db_summary"], ensure_ascii=False, indent=2))
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
