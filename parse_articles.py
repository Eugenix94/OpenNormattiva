#!/usr/bin/env python3
"""
Populate the articles table by splitting law text on article headers.
Runs locally against data/laws.db.
"""
import sys, re, time
sys.path.insert(0, '.')
from core.db import LawDatabase

db = LawDatabase("data/laws.db")

# Simple line-based split — much faster than DOTALL regex
ART_HEADER = re.compile(r'^\s*Art(?:icolo)?\.?\s*(\d+\w*)[.\s\-]*(.{0,200})', re.IGNORECASE)
MAX_TEXT = 300_000   # chars per law
BATCH = 200

def parse_articles_fast(text: str):
    """Split text on 'Art. N' lines. Returns list of (num, heading, body)."""
    if not text:
        return []
    text = text[:MAX_TEXT]
    results = []
    current_num = None
    current_head = ""
    current_lines = []
    for line in text.splitlines():
        m = ART_HEADER.match(line)
        if m:
            if current_num is not None:
                results.append((current_num, current_head, '\n'.join(current_lines).strip()))
            current_num = m.group(1)
            current_head = m.group(2).strip()[:200]
            current_lines = []
        elif current_num is not None:
            current_lines.append(line)
    if current_num is not None:
        results.append((current_num, current_head, '\n'.join(current_lines).strip()))
    return results

rows = db.conn.execute(
    "SELECT urn, text FROM laws "
    "WHERE text IS NOT NULL AND length(text) > 100 "
    "ORDER BY importance_score DESC LIMIT 2000"
).fetchall()

total_arts = 0
processed = 0
t0 = time.time()

for i, row in enumerate(rows):
    urn = row["urn"]
    text = row["text"] or ""
    articles = parse_articles_fast(text)
    if articles:
        db.conn.execute("DELETE FROM articles WHERE law_urn = ?", (urn,))
        db.conn.executemany(
            "INSERT INTO articles (law_urn, article_num, heading, text, char_count) VALUES (?,?,?,?,?)",
            [(urn, a[0], a[1], a[2], len(a[2])) for a in articles]
        )
        total_arts += len(articles)
    processed += 1
    if processed % BATCH == 0:
        db.conn.commit()
        elapsed = time.time() - t0
        print(f"  {processed}/{len(rows)} laws | {total_arts} articles | {elapsed:.1f}s")

db.conn.commit()
print(f"\nDone: {processed} laws, {total_arts} articles in {time.time()-t0:.1f}s")
arts_count = db.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
print(f"Total articles in DB: {arts_count}")
