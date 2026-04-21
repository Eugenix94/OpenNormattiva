#!/usr/bin/env python3
"""Inspect database for completeness and missing content."""
import sqlite3
from pathlib import Path

db = sqlite3.connect('data/laws.db')
db.row_factory = sqlite3.Row

total = db.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
print(f'Total laws: {total:,}')

print('\nStatus breakdown:')
for r in db.execute('SELECT status, COUNT(*) cnt FROM laws GROUP BY status ORDER BY cnt DESC').fetchall():
    print(f'  {r[0]!r}: {r[1]:,}')

print('\nTop 15 types:')
for r in db.execute('SELECT type, COUNT(*) cnt FROM laws GROUP BY type ORDER BY cnt DESC LIMIT 15').fetchall():
    print(f'  {r[0]!r}: {r[1]:,}')

print('\nSource collections (top 10):')
for r in db.execute('SELECT source_collection, COUNT(*) cnt FROM laws GROUP BY source_collection ORDER BY cnt DESC LIMIT 10').fetchall():
    print(f'  {r[0]!r}: {r[1]:,}')

print('\nConstitution search:')
for r in db.execute("SELECT urn, title, year FROM laws WHERE urn LIKE '%costituzione%' OR title LIKE '%Costituzione%' LIMIT 5").fetchall():
    print(f'  {r["urn"]} | {r["title"]} | {r["year"]}')

print('\nMain Codici search:')
codici_urns = [
    'urn:nir:stato:regio.decreto:1942-03-16;262',
    'urn:nir:stato:regio.decreto:1930-10-19;1398',
    'urn:nir:stato:regio.decreto:1940-10-28;1443',
    'urn:nir:stato:decreto.del.presidente.della.repubblica:1988-09-22;447',
]
for urn in codici_urns:
    row = db.execute("SELECT urn, title, year FROM laws WHERE urn = ? OR urn LIKE ?", (urn, urn+'%')).fetchone()
    if row:
        print(f'  FOUND: {row["urn"]} | {row["title"]}')
    else:
        print(f'  MISSING: {urn}')

print('\nCitations table size:')
cit = db.execute('SELECT COUNT(*) FROM citations').fetchone()[0]
print(f'  {cit:,} citation relationships')

print('\nLaws with no text (empty text field):')
no_text = db.execute("SELECT COUNT(*) FROM laws WHERE text IS NULL OR text = ''").fetchone()[0]
print(f'  {no_text:,} laws without text content')

print('\nTop important laws by PageRank:')
for r in db.execute('SELECT urn, title, importance_score FROM laws ORDER BY importance_score DESC LIMIT 10').fetchall():
    print(f'  [{r["importance_score"]:.4f}] {r["title"][:60]}')

db.close()
