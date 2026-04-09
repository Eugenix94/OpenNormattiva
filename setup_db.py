#!/usr/bin/env python3
"""
One-shot setup: populate citations, run PageRank, detect domains, export.
Run from project root: python setup_db.py
"""
import sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core.db import LawDatabase

DB_PATH = Path("data/laws.db")
JSONL_PATH = Path("data/processed/laws_vigente.jsonl")

def step(msg):
    print(f"\n{'='*60}", flush=True)
    print(f"  {msg}", flush=True)
    print('='*60, flush=True)

db = LawDatabase(DB_PATH)

# Disable FK enforcement throughout (citations use short-code refs, not URNs)
db.conn.execute("PRAGMA foreign_keys = OFF")

# Step 1: Insert citations from JSONL (FK disabled)
step("1/5  Inserting citations from JSONL")
t0 = time.time()
inserted = 0
skipped = 0
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
                context = cit.get('context', '')
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
                inserted += 1
            except Exception:
                skipped += 1
        if i % 5000 == 0 and i > 0:
            db.conn.commit()
            print(f"  ...{i:,} lines, {inserted:,} citations so far", flush=True)

db.conn.commit()
print(f"  Done: {inserted:,} citations inserted, {skipped} skipped ({time.time()-t0:.1f}s)", flush=True)

# Step 2: Citation counts
step("2/5  Computing citation counts")
t0 = time.time()
db.compute_citation_counts()
c = db.conn.execute("SELECT COUNT(*) FROM law_metadata WHERE citation_count_incoming > 0").fetchone()[0]
print(f"  Laws with incoming citations: {c:,}  ({time.time()-t0:.1f}s)", flush=True)

# Step 3: PageRank
step("3/5  Computing PageRank importance scores")
t0 = time.time()
db.compute_importance_scores()
top = db.conn.execute(
    "SELECT l.title, m.pagerank FROM laws l JOIN law_metadata m ON l.urn = m.urn "
    "WHERE m.pagerank IS NOT NULL ORDER BY m.pagerank DESC LIMIT 5"
).fetchall()
print(f"  Top 5 laws by importance:", flush=True)
for t in top:
    print(f"    {t[0][:60]}  ({t[1]:.6f})", flush=True)
print(f"  Done ({time.time()-t0:.1f}s)", flush=True)

# Step 4: Domain detection
step("4/5  Detecting legal domains")
t0 = time.time()
db.detect_law_domains()
domains = db.conn.execute(
    "SELECT domain_cluster, COUNT(*) c FROM law_metadata WHERE domain_cluster IS NOT NULL "
    "GROUP BY domain_cluster ORDER BY c DESC"
).fetchall()
print("  Domain distribution:", flush=True)
for d in domains:
    print(f"    {d[0]:30s} {d[1]:>6,}", flush=True)
print(f"  Done ({time.time()-t0:.1f}s)", flush=True)

# Step 5: Validate + export
step("5/5  Validation & export")
t0 = time.time()
report = db.validate_data()
print(f"  Total laws: {report['total_laws']:,}", flush=True)
print(f"  Validation issues: {len(report['issues'])}", flush=True)

csv_path = db.export_csv(Path("data/laws_summary.csv"))
print(f"  Exported CSV: {csv_path}", flush=True)

graph_path = db.export_graph_json(Path("data/citation_graph.json"), min_citations=1)
print(f"  Exported graph: {graph_path}", flush=True)
print(f"  Done ({time.time()-t0:.1f}s)", flush=True)

db.close()

step("COMPLETE")
total_laws = report['total_laws']
print(f"  Database at data/laws.db is ready!", flush=True)
print(f"  {total_laws:,} laws  |  {inserted:,} citations  |  {len(domains)} domains", flush=True)
print(f"\n  Now run:  streamlit run space/app.py", flush=True)
