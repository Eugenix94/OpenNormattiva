#!/usr/bin/env python3
"""Deep search for Constitution and check codici collection."""
import sqlite3

db = sqlite3.connect('data/laws.db')

# All distinct types
print('All law types:')
for r in db.execute("SELECT type, COUNT(*) c FROM laws GROUP BY type ORDER BY c DESC").fetchall():
    print(f'  {r[0]:<50} {r[1]:>8,}')

# Check if there's a COSTITUZIONE type
print('\nCOSTITUZIONE type:')
for r in db.execute("SELECT urn, title, year FROM laws WHERE UPPER(type) LIKE '%COSTIT%'").fetchall():
    print(f'  {r[0]} | {r[1][:70]} ({r[2]})')

# What's in the 'Codici' collection? Check URNs with 'codice' or 'codicecivile'
print('\nCodici URNs:')
for r in db.execute("SELECT urn, title, type FROM laws WHERE urn LIKE '%codice%' OR LOWER(title) LIKE 'codice %' LIMIT 20").fetchall():
    print(f'  [{r[2]}] {r[0]} | {r[1][:60]}')

db.close()
