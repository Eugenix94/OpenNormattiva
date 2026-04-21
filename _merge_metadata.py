#!/usr/bin/env python3
"""Merge citation counts into law_metadata rows that have domain info."""
import sys, time
sys.path.insert(0, '.')
from core.db import LawDatabase

db = LawDatabase('data/laws.db')

total = db.conn.execute('SELECT COUNT(*) FROM law_metadata').fetchone()[0]
print(f'law_metadata rows: {total}')

t0 = time.time()
print('Updating citation counts on ALL law_metadata rows...')
db.conn.execute('''
    UPDATE law_metadata SET citation_count_incoming = (
        SELECT COUNT(*) FROM citations WHERE cited_urn = law_metadata.urn
    )
''')
db.conn.execute('''
    UPDATE law_metadata SET citation_count_outgoing = (
        SELECT COUNT(*) FROM citations WHERE citing_urn = law_metadata.urn
    )
''')
db.conn.commit()
print(f'Done in {time.time()-t0:.1f}s')

# Also insert law_metadata rows for laws that have citations but no domain yet
print('Inserting citation-only metadata for laws not yet in law_metadata...')
db.conn.execute('''
    INSERT OR IGNORE INTO law_metadata (urn, citation_count_incoming)
    SELECT cited_urn, COUNT(*) FROM citations
    WHERE cited_urn NOT IN (SELECT urn FROM law_metadata)
    GROUP BY cited_urn
''')
db.conn.execute('''
    UPDATE law_metadata SET citation_count_outgoing = (
        SELECT COUNT(*) FROM citations WHERE citing_urn = law_metadata.urn
    ) WHERE citation_count_outgoing IS NULL OR citation_count_outgoing = 0
''')
db.conn.commit()

total2 = db.conn.execute('SELECT COUNT(*) FROM law_metadata').fetchone()[0]
both = db.conn.execute(
    'SELECT COUNT(*) FROM law_metadata WHERE citation_count_incoming > 0 AND domain_cluster IS NOT NULL'
).fetchone()[0]
inc = db.conn.execute(
    'SELECT COUNT(*) FROM law_metadata WHERE citation_count_incoming > 0'
).fetchone()[0]
dom = db.conn.execute(
    'SELECT COUNT(*) FROM law_metadata WHERE domain_cluster IS NOT NULL'
).fetchone()[0]
print(f'After merge: total={total2}, inc>0={inc}, domain={dom}, both={both}')

# Top tax laws with citations
rows = db.conn.execute('''
    SELECT l.title, l.type, l.year, m.citation_count_incoming, l.importance_score
    FROM laws l JOIN law_metadata m ON l.urn = m.urn
    WHERE m.domain_cluster = 'diritto_tributario' AND m.citation_count_incoming > 0
    ORDER BY m.citation_count_incoming DESC LIMIT 10
''').fetchall()
print(f'\nTop 10 tax laws with citations:')
for r in rows:
    print(f'  cited={r[3]:>5}  score={r[4]:>6.2f}  {r[1]} {r[2]}  {r[0][:65]}')

db.close()
