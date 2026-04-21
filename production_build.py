#!/usr/bin/env python3
"""
Production Build Script for Normattiva Legal Research Platform

One-shot script that:
1. Parses ALL 22 downloaded ZIPs into a unified JSONL
2. Loads all laws into SQLite with FTS5
3. Runs all enrichments (citations, PageRank, domains)
4. Validates data quality
5. Reports final statistics

Usage:
    python production_build.py              # Full build from raw ZIPs
    python production_build.py --enrich     # Only re-run enrichments on existing DB
    python production_build.py --status     # Show current state
"""

import sys
import json
import time
import os
import zipfile
import sqlite3
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

# ── Configuration ────────────────────────────────────────────────────────────

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")
JSONL_PATH = PROCESSED_DIR / "laws_vigente.jsonl"
DB_PATH = Path("data/laws.db")


def timestamp():
    return datetime.now().strftime("%H:%M:%S")


def step(msg):
    print(f"\n{'='*70}")
    print(f"  [{timestamp()}] {msg}")
    print(f"{'='*70}", flush=True)


def substep(msg):
    print(f"  [{timestamp()}] {msg}", flush=True)


# ── Step 1: Parse all ZIPs → JSONL ──────────────────────────────────────────

def build_jsonl():
    """Parse all 22 vigente ZIP files into a single deduplicated JSONL."""
    from parse_akn import AKNParser

    step("STEP 1/5: Parsing all ZIP files → JSONL")

    zips = sorted(RAW_DIR.glob("*_vigente.zip"))
    if not zips:
        print("  ERROR: No ZIP files found in data/raw/")
        print("  Run: python download_normattiva.py  first")
        return False

    substep(f"Found {len(zips)} ZIP files in {RAW_DIR}")

    parser = AKNParser()
    all_laws = {}  # URN → law dict (deduplicates)
    total_parsed = 0
    total_skipped = 0

    for i, zip_path in enumerate(zips, 1):
        collection_name = zip_path.stem.replace("_vigente", "")
        zip_size_mb = zip_path.stat().st_size / (1024 * 1024)
        substep(f"[{i}/{len(zips)}] {collection_name} ({zip_size_mb:.1f} MB)")

        try:
            laws = parser.parse_zip_file(zip_path)
            for law in laws:
                law = parser.enrich_with_metadata(law)
                law['source_collection'] = collection_name
                urn = law.get('urn')
                if urn:
                    if urn not in all_laws:
                        all_laws[urn] = law
                        total_parsed += 1
                    else:
                        total_skipped += 1
                else:
                    total_skipped += 1
        except Exception as e:
            print(f"    WARNING: Failed to parse {collection_name}: {e}")
            continue

    substep(f"Total unique laws: {len(all_laws):,}")
    substep(f"Duplicates skipped: {total_skipped:,}")

    # Write JSONL
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Backup existing
    if JSONL_PATH.exists():
        bak = JSONL_PATH.with_suffix('.jsonl.bak')
        substep(f"Backing up existing JSONL → {bak.name}")
        import shutil
        shutil.copy2(JSONL_PATH, bak)

    substep(f"Writing {len(all_laws):,} laws to {JSONL_PATH}")
    with open(JSONL_PATH, 'w', encoding='utf-8') as f:
        for law in all_laws.values():
            f.write(json.dumps(law, ensure_ascii=False) + '\n')

    jsonl_size = JSONL_PATH.stat().st_size / (1024 * 1024)
    substep(f"JSONL written: {jsonl_size:.1f} MB")
    return True


# ── Step 2: Load JSONL → SQLite ─────────────────────────────────────────────

def load_into_db():
    """Load all laws from JSONL into SQLite database with FTS5."""
    from core.db import LawDatabase

    step("STEP 2/5: Loading JSONL → SQLite + FTS5")

    if not JSONL_PATH.exists():
        print(f"  ERROR: {JSONL_PATH} not found. Run step 1 first.")
        return False

    # Remove old DB for clean build
    if DB_PATH.exists():
        db_bak = DB_PATH.with_suffix('.db.bak')
        substep(f"Backing up existing DB → {db_bak.name}")
        import shutil
        shutil.copy2(DB_PATH, db_bak)
        DB_PATH.unlink()

    db = LawDatabase(DB_PATH)

    # Disable FK for bulk insert (citations reference laws not yet loaded)
    db.conn.execute("PRAGMA foreign_keys = OFF")
    db.conn.execute("PRAGMA synchronous = OFF")
    db.conn.execute("PRAGMA journal_mode = MEMORY")

    line_count = sum(1 for _ in open(JSONL_PATH, 'r', encoding='utf-8'))
    substep(f"Loading {line_count:,} laws from JSONL...")

    t0 = time.time()
    inserted = 0
    errors = 0

    with open(JSONL_PATH, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            try:
                law = json.loads(line)
                if db.insert_law(law):
                    inserted += 1
                else:
                    errors += 1
            except Exception as e:
                errors += 1

            if (i + 1) % 10000 == 0:
                db.conn.commit()
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed
                substep(f"  {i+1:,}/{line_count:,} ({rate:.0f} laws/sec)")

    db.conn.commit()
    elapsed = time.time() - t0
    substep(f"Loaded {inserted:,} laws in {elapsed:.1f}s ({errors} errors)")

    # Verify
    count = db.conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    fts_count = db.conn.execute("SELECT COUNT(*) FROM laws_fts").fetchone()[0]
    substep(f"DB laws: {count:,} | FTS indexed: {fts_count:,}")

    db.conn.execute("PRAGMA synchronous = NORMAL")
    db.conn.execute("PRAGMA journal_mode = WAL")
    db.conn.commit()
    db.close()
    return True


# ── Step 3: Run enrichments ─────────────────────────────────────────────────

def run_enrichments():
    """Run citations, PageRank, and domain detection."""
    from core.db import LawDatabase

    step("STEP 3/5: Running enrichments")

    db = LawDatabase(DB_PATH)
    db.conn.execute("PRAGMA foreign_keys = OFF")

    # 3a: Insert citations from JSONL
    substep("3a. Inserting citations from JSONL...")
    t0 = time.time()
    cit_inserted = 0

    with open(JSONL_PATH, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if not line.strip():
                continue
            try:
                law = json.loads(line)
            except Exception:
                continue
            
            urn = law.get('urn')
            citations = law.get('citations', [])
            if not urn or not citations:
                continue

            for cit in citations:
                if isinstance(cit, dict):
                    cited_urn = cit.get('target_urn', cit.get('cited_urn', ''))
                    context = cit.get('ref', '')
                else:
                    cited_urn = str(cit)
                    context = ''
                if not cited_urn:
                    continue
                try:
                    db.conn.execute(
                        "INSERT OR IGNORE INTO citations (citing_urn, cited_urn, count, context) VALUES (?, ?, 1, ?)",
                        (urn, cited_urn, context)
                    )
                    cit_inserted += 1
                except Exception:
                    pass

            if i % 10000 == 0 and i > 0:
                db.conn.commit()

    db.conn.commit()
    substep(f"  Citations inserted: {cit_inserted:,} ({time.time()-t0:.1f}s)")

    # 3b: Compute citation counts
    substep("3b. Computing citation counts...")
    t0 = time.time()
    db.compute_citation_counts()
    c = db.conn.execute("SELECT COUNT(*) FROM law_metadata WHERE citation_count_incoming > 0").fetchone()[0]
    substep(f"  Laws with incoming citations: {c:,} ({time.time()-t0:.1f}s)")

    # 3c: PageRank
    substep("3c. Computing PageRank importance scores...")
    t0 = time.time()
    db.compute_importance_scores()
    top = db.conn.execute(
        "SELECT l.title, l.importance_score FROM laws l "
        "WHERE l.importance_score > 0 ORDER BY l.importance_score DESC LIMIT 5"
    ).fetchall()
    substep(f"  Top 5 by importance:")
    for t_item in top:
        substep(f"    {t_item[0][:60]}  (score: {t_item[1]:.2f})")
    substep(f"  PageRank done ({time.time()-t0:.1f}s)")

    # 3d: Domain detection
    substep("3d. Detecting legal domains...")
    t0 = time.time()
    db.detect_law_domains()
    domains = db.conn.execute(
        "SELECT domain_cluster, COUNT(*) c FROM law_metadata WHERE domain_cluster IS NOT NULL "
        "GROUP BY domain_cluster ORDER BY c DESC"
    ).fetchall()
    substep(f"  Domain distribution:")
    for d in domains:
        substep(f"    {d[0]:30s} {d[1]:>6,}")
    substep(f"  Done ({time.time()-t0:.1f}s)")

    db.close()
    return True


# ── Step 4: Validate ────────────────────────────────────────────────────────

def validate():
    """Run data quality checks."""
    from core.db import LawDatabase

    step("STEP 4/5: Data validation")

    db = LawDatabase(DB_PATH)

    report = db.validate_data()
    substep(f"Total laws: {report['total_laws']:,}")
    substep(f"Validation issues: {len(report['issues'])}")
    if report['issues']:
        for issue in report['issues'][:10]:
            substep(f"  ⚠ {issue}")

    # Additional checks
    checks = [
        ("Laws with no URN", "SELECT COUNT(*) FROM laws WHERE urn IS NULL"),
        ("Laws with no title", "SELECT COUNT(*) FROM laws WHERE title IS NULL OR title = ''"),
        ("Laws with no text", "SELECT COUNT(*) FROM laws WHERE text IS NULL OR length(text) < 10"),
        ("Laws with no year", "SELECT COUNT(*) FROM laws WHERE year IS NULL"),
        ("Orphan citations", "SELECT COUNT(*) FROM citations WHERE cited_urn NOT IN (SELECT urn FROM laws)"),
        ("FTS5 index count", "SELECT COUNT(*) FROM laws_fts"),
        ("Citation count", "SELECT COUNT(*) FROM citations"),
        ("Metadata rows", "SELECT COUNT(*) FROM law_metadata"),
    ]

    for label, sql in checks:
        try:
            val = db.conn.execute(sql).fetchone()[0]
            substep(f"  {label}: {val:,}")
        except Exception as e:
            substep(f"  {label}: ERROR - {e}")

    db.close()
    return True


# ── Step 5: Export & summarize ──────────────────────────────────────────────

def export_and_summarize():
    """Export CSV, citation graph, and print final summary."""
    from core.db import LawDatabase

    step("STEP 5/5: Export & summary")

    db = LawDatabase(DB_PATH)

    # Export CSV
    csv_path = db.export_csv(Path("data/laws_summary.csv"))
    substep(f"CSV exported: {csv_path}")

    # Export citation graph
    graph_path = db.export_graph_json(Path("data/citation_graph.json"), min_citations=1)
    substep(f"Citation graph exported: {graph_path}")

    # Final stats
    stats = db.get_statistics()

    step("BUILD COMPLETE")
    print(f"""
  Database: {DB_PATH} ({DB_PATH.stat().st_size / (1024*1024):.1f} MB)
  JSONL:    {JSONL_PATH} ({JSONL_PATH.stat().st_size / (1024*1024):.1f} MB)

  Total laws:       {stats.get('total_laws', 0):,}
  Total citations:  {stats.get('total_citations', 0):,}
  Year range:       {stats.get('year_range', {}).get('min', '?')} - {stats.get('year_range', {}).get('max', '?')}
  Law types:        {len(stats.get('by_type', {}))}
  Legal domains:    {len(stats.get('by_domain', {}))}
  FTS5 index:       Ready ✓

  Top cited laws:
""", flush=True)
    for law in stats.get('most_cited', [])[:10]:
        print(f"    [{law.get('citation_count',0):>4}] {law.get('title', '?')[:60]}", flush=True)

    print(f"""
  ──────────────────────────────────────────────
  READY FOR USE
  ──────────────────────────────────────────────

  Launch the research platform:
    streamlit run space/app.py

  Search example (Python):
    from core.db import LawDatabase
    db = LawDatabase()
    results = db.search_fts("imposta reddito")
    for r in results[:5]:
        print(r['title'])
""", flush=True)

    db.close()
    return True


# ── Status check ────────────────────────────────────────────────────────────

def show_status():
    """Show current data state without modification."""
    step("CURRENT STATE")

    # Raw ZIPs
    zips = list(RAW_DIR.glob("*_vigente.zip")) if RAW_DIR.exists() else []
    total_raw = sum(z.stat().st_size for z in zips)
    substep(f"Raw ZIPs: {len(zips)} ({total_raw/1024/1024:.1f} MB)")

    # JSONL
    if JSONL_PATH.exists():
        jsize = JSONL_PATH.stat().st_size / (1024*1024)
        jlines = sum(1 for _ in open(JSONL_PATH, 'r', encoding='utf-8'))
        substep(f"JSONL: {jlines:,} lines ({jsize:.1f} MB)")
    else:
        substep("JSONL: NOT FOUND")

    # DB
    if DB_PATH.exists():
        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
        law_count = db.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
        cit_count = db.execute("SELECT COUNT(*) FROM citations").fetchone()[0]
        fts_count = db.execute("SELECT COUNT(*) FROM laws_fts").fetchone()[0]
        meta_count = db.execute("SELECT COUNT(*) FROM law_metadata").fetchone()[0]
        pr_count = db.execute("SELECT COUNT(*) FROM law_metadata WHERE pagerank > 0").fetchone()[0]
        dom_count = db.execute("SELECT COUNT(*) FROM law_metadata WHERE domain_cluster IS NOT NULL").fetchone()[0]
        cit_in = db.execute("SELECT COUNT(*) FROM law_metadata WHERE citation_count_incoming > 0").fetchone()[0]
        db_size = DB_PATH.stat().st_size / (1024*1024)
        substep(f"Database: {law_count:,} laws ({db_size:.1f} MB)")
        substep(f"  FTS indexed:    {fts_count:,}")
        substep(f"  Citations:      {cit_count:,}")
        substep(f"  Metadata rows:  {meta_count:,}")
        substep(f"  PageRank > 0:   {pr_count:,}")
        substep(f"  Domains set:    {dom_count:,}")
        substep(f"  CitCount > 0:   {cit_in:,}")
        db.close()
    else:
        substep("Database: NOT FOUND")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Normattiva Production Build")
    parser.add_argument('--enrich', action='store_true', help='Only re-run enrichments')
    parser.add_argument('--status', action='store_true', help='Show current state')
    parser.add_argument('--skip-parse', action='store_true', help='Skip ZIP parsing (use existing JSONL)')
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    t_start = time.time()

    step("NORMATTIVA PRODUCTION BUILD")
    print(f"  Started at {timestamp()}")
    print(f"  Raw ZIPs: {RAW_DIR}")
    print(f"  Output DB: {DB_PATH}")

    if args.enrich:
        # Only enrichments
        run_enrichments()
        validate()
        export_and_summarize()
    else:
        # Full build
        if not args.skip_parse:
            if not build_jsonl():
                print("FAILED at step 1")
                return 1

        if not load_into_db():
            print("FAILED at step 2")
            return 1

        if not run_enrichments():
            print("FAILED at step 3")
            return 1

        validate()
        export_and_summarize()

    elapsed = time.time() - t_start
    print(f"\n  Total time: {elapsed/60:.1f} minutes")
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
