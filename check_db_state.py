#!/usr/bin/env python3
"""Quick DB state check."""
import sqlite3, os

db = sqlite3.connect('data/laws.db')
db.row_factory = sqlite3.Row

total = db.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
print(f'Total laws: {total}')

const = db.execute("SELECT urn, title, type, year, text_length, article_count FROM laws WHERE type='COSTITUZIONE' OR LOWER(title) LIKE '%costituzione%italiana%'").fetchall()
print(f'Constitution entries: {len(const)}')
for c in const:
    print(f'  {dict(c)}')

abrog = db.execute("SELECT COUNT(*) FROM laws WHERE status='abrogated'").fetchone()[0]
print(f'Abrogated laws: {abrog}')

types = db.execute('SELECT type, COUNT(*) cnt FROM laws GROUP BY type ORDER BY cnt DESC LIMIT 15').fetchall()
print('Types:')
for t in types:
    print(f'  {t[0]}: {t[1]}')

codici_urns = [
    'urn:nir:stato:regio.decreto:1942-03-16;262',
    'urn:nir:stato:regio.decreto:1930-10-19;1398',
    'urn:nir:stato:regio.decreto:1940-10-28;1443',
    'urn:nir:stato:decreto.del.presidente.della.repubblica:1988-09-22;447',
]
print('Codici:')
for u in codici_urns:
    r = db.execute('SELECT urn, title, year FROM laws WHERE urn=? OR urn LIKE ?', (u, u+'%')).fetchone()
    if r:
        print(f'  FOUND: {r[0]} - {r[1]}')
    else:
        print(f'  MISSING: {u}')

size_mb = os.path.getsize('data/laws.db') / 1e6
print(f'DB size: {size_mb:.1f}MB')

cit = db.execute('SELECT COUNT(*) FROM citations').fetchone()[0]
print(f'Citations: {cit}')

fts = db.execute("SELECT COUNT(*) FROM laws_fts").fetchone()[0]
print(f'FTS entries: {fts}')

# Check URN samples
samples = db.execute("SELECT urn FROM laws WHERE urn LIKE 'urn:nir:%' LIMIT 5").fetchall()
print('URN samples:')
for s in samples:
    print(f'  {s[0]}')

db.close()
