#!/usr/bin/env python3
"""Quick diagnostic to find the pipeline error."""

import sqlite3

db = sqlite3.connect('data/laws.db')
db.row_factory = sqlite3.Row

print("=" * 60)
print("DATABASE DIAGNOSTIC")
print("=" * 60)

# Check for NULL or suspicious values
nulls = db.execute('SELECT COUNT(*) FROM laws WHERE urn IS NULL OR title IS NULL').fetchone()[0]
print(f'\nLaws with NULL urn/title: {nulls}')

# Check for very short text (likely parsing error)
short = db.execute('SELECT COUNT(*) FROM laws WHERE text_length < 10').fetchone()[0]
print(f'Laws with suspiciously short text (<10 chars): {short}')

if short > 0:
    print('\nExamples of short-text laws:')
    for row in db.execute('SELECT urn, title, text_length FROM laws WHERE text_length < 10 LIMIT 3').fetchall():
        print(f'  {row[0]}: {row[1][:50]} ({row[2]} chars)')

# Check for missing types or dates
no_type = db.execute('SELECT COUNT(*) FROM laws WHERE type IS NULL OR type=""').fetchone()[0]
no_date = db.execute('SELECT COUNT(*) FROM laws WHERE date IS NULL OR date=""').fetchone()[0]
print(f'\nLaws without type: {no_type}')
print(f'Laws without date: {no_date}')

# Get stats by collection
print('\nLaws by collection:')
for row in db.execute('SELECT source_collection, COUNT(*) as cnt FROM laws GROUP BY source_collection ORDER BY cnt DESC').fetchall():
    print(f'  {row[0]}: {row[1]:,}')

print('\n' + "=" * 60)
print("TOTALS")
print("=" * 60)
total_laws = db.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
total_cits = db.execute('SELECT COUNT(*) FROM citations').fetchone()[0]
print(f'Total laws: {total_laws:,}')
print(f'Total citations: {total_cits:,}')

db.close()
