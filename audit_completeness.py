#!/usr/bin/env python3
"""Audit completeness of Italian legal system data in lab database"""

import sqlite3
from pathlib import Path

db_path = Path('data/laws.db')
conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

print("=== ITALIAN LEGAL SYSTEM COMPLETENESS CHECK ===\n")

# Check laws table
cursor.execute("SELECT COUNT(*) FROM laws")
total_laws = cursor.fetchone()[0]

cursor.execute("SELECT COUNT(*) FROM laws WHERE status = 'in_force'")
vigente = cursor.fetchone()[0]

cursor.execute("SELECT COUNT(*) FROM laws WHERE status = 'abrogated'")
abrogate = cursor.fetchone()[0]

# Check year range
cursor.execute("SELECT MIN(year), MAX(year) FROM laws")
min_year, max_year = cursor.fetchone()

# Check citations
cursor.execute("SELECT COUNT(*) FROM citations")
citations_count = cursor.fetchone()[0]

# Check distinct fields
cursor.execute("SELECT DISTINCT category FROM laws WHERE category IS NOT NULL ORDER BY category LIMIT 20")
categories = [row[0] for row in cursor.fetchall()]

print(f"Total Laws: {total_laws}")
print(f"  In Force (vigente): {vigente}")
print(f"  Abrogated (abrogate): {abrogate}")
print(f"  Year Range: {min_year} - {max_year}")
print(f"\nSample Categories: {', '.join(categories[:5]) if categories else 'N/A'}")
print(f"\nCitations: {citations_count}")

# Check tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print(f"\nDatabase Tables: {', '.join(sorted(tables))}")

# Check sentenza tables
print("\n=== CONSTITUTIONAL COURT SENTENZE ===")
try:
    cursor.execute("SELECT COUNT(*) FROM sentenze")
    sentenza_count = cursor.fetchone()[0]
    print(f"Sentenze loaded: {sentenza_count}")
    
    cursor.execute("SELECT COUNT(*) FROM sentenza_citations")
    cit_count = cursor.fetchone()[0]
    print(f"Sentenza citations: {cit_count}")
except Exception as e:
    print(f"Sentenze tables not yet initialized: {e}")

conn.close()

print("\n" + "="*60)
print("COMPLETENESS ASSESSMENT")
print("="*60)
print(f"\n✓ Normattiva Corpus: {total_laws} laws")
print(f"  - Vigente: {vigente} (in force)")
print(f"  - Abrogate: {abrogate} (repealed)")
print(f"  - Coverage: 1861-{max_year}")
print(f"  - Cross-references: {citations_count}")

print(f"\nℹ Comparison with official Normattiva:")
print(f"  (As of 2026, Normattiva contains ~190,000+ laws)")
print(f"  Current: {total_laws} laws")
if vigente >= 156000:
    print(f"  ✓ Vigente count appears complete")
else:
    print(f"  ⚠ Vigente count may be incomplete")
