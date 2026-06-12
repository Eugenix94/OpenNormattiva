#!/usr/bin/env python3
import sqlite3

db = sqlite3.connect('multivigente.db')
c = db.cursor()
c.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = [row[0] for row in c.fetchall()]
print(f'Tables in multivigente.db: {tables}')

if 'law_versions' in tables:
    c.execute('SELECT COUNT(*) FROM law_versions')
    count = c.fetchone()[0]
    print(f'law_versions count: {count:,}')
    
if 'original_acts' in tables:
    c.execute('SELECT COUNT(*) FROM original_acts')
    count = c.fetchone()[0]
    print(f'original_acts count: {count:,}')
    
db.close()
