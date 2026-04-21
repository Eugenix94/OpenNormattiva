#!/usr/bin/env python3
"""Check Constitution and abrogati coverage."""
import sqlite3

db = sqlite3.connect('data/laws.db')

# Search for constitution-related URNs
results = db.execute("SELECT urn, title, year FROM laws WHERE urn LIKE '%costit%' LIMIT 10").fetchall()
print('URN LIKE costit:')
for r in results:
    print(f'  {r[0]} | {r[1][:60]}')

# Check the leggi costituzionali
results2 = db.execute("SELECT urn, title, year FROM laws WHERE type = 'LEGGE COSTITUZIONALE' ORDER BY year LIMIT 20").fetchall()
print(f'\nLEGGE COSTITUZIONALE ({len(results2)} found):')
for r in results2:
    print(f'  {r[0]} | {r[1][:70]} ({r[2]})')

# Year range
yr = db.execute('SELECT MIN(year), MAX(year) FROM laws').fetchone()
print(f'\nYear range: {yr[0]} - {yr[1]}')

# Laws from 1948
results3 = db.execute("SELECT urn, title, year, type FROM laws WHERE year = 1948 ORDER BY type LIMIT 20").fetchall()
print(f'\nLaws from 1948:')
for r in results3:
    print(f'  [{r[3]}] {r[0]} | {r[1][:60]}')

db.close()
