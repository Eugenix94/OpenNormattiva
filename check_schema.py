#!/usr/bin/env python3
"""Check database schema"""

import sqlite3
from pathlib import Path

db_path = Path('data/laws.db')
conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

# Get schema
cursor.execute("PRAGMA table_info(laws)")
columns = cursor.fetchall()
print("=== LAWS TABLE SCHEMA ===")
for col in columns:
    print(f"  {col[1]} ({col[2]})")

# Simple count
cursor.execute("SELECT COUNT(*) FROM laws")
total = cursor.fetchone()[0]

cursor.execute("SELECT COUNT(*) FROM laws WHERE status = 'in_force'")
vigente = cursor.fetchone()[0]

cursor.execute("SELECT COUNT(*) FROM laws WHERE status = 'abrogated'")
abrogate = cursor.fetchone()[0]

cursor.execute("SELECT MIN(year), MAX(year) FROM laws")
miny, maxy = cursor.fetchone()

cursor.execute("SELECT COUNT(*) FROM citations")
citations = cursor.fetchone()[0]

print(f"\n=== LAW COUNTS ===")
print(f"Total: {total}")
print(f"Vigente (in force): {vigente}")
print(f"Abrogated: {abrogate}")
print(f"Year Range: {miny} - {maxy}")
print(f"Citations: {citations}")

# Sample a law
cursor.execute("SELECT urn, title, year FROM laws WHERE status = 'in_force' LIMIT 1")
row = cursor.fetchone()
if row:
    print(f"\nSample law: {row[0]} - {row[1]} ({row[2]})")

conn.close()
