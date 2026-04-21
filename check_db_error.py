import sqlite3

db = sqlite3.connect('data/laws.db')
db.row_factory = sqlite3.Row

# Check schema
print('Database schema:')
cols = db.execute("PRAGMA table_info(laws)").fetchall()
for col in cols:
    print(f'  {col[1]}: {col[2]}')
print()

# Check for any NULL or suspicious values
nulls = db.execute('SELECT COUNT(*) FROM laws WHERE urn IS NULL OR title IS NULL').fetchone()[0]
print(f'Laws with NULL urn/title: {nulls}')

# Check for very short text
short = db.execute('SELECT COUNT(*) FROM laws WHERE text_length < 10').fetchone()[0]
print(f'Laws with suspiciously short text (<10 chars): {short}')
if short > 0:
    short_laws = db.execute('SELECT urn, title, text_length FROM laws WHERE text_length < 10').fetchall()
    print('  Details:')
    for law in short_laws:
        print(f'    {law["urn"]}: text_length={law["text_length"]} - {law["title"][:100] if law["title"] else "(no title)"}')

# Check for incomplete laws (no text)
no_text = db.execute('SELECT COUNT(*) FROM laws WHERE text is NULL OR text = ""').fetchone()[0]
print(f'Laws with no text: {no_text}')

# Check for duplicate URNs
dups = db.execute('SELECT urn, COUNT(*) as cnt FROM laws GROUP BY urn HAVING cnt > 1').fetchall()
if dups:
    print(f'Duplicate URNs: {len(dups)}')
    for dup in dups:
        print(f'  {dup["urn"]}: {dup["cnt"]} copies')
else:
    print('Duplicate URNs: None')

# Check for anomalies in source collections
collections = db.execute('SELECT DISTINCT source_collection, COUNT(*) as cnt FROM laws GROUP BY source_collection ORDER BY source_collection').fetchall()
print(f'\nLaws by source collection:')
total = 0
for col in collections:
    print(f'  {col["source_collection"]}: {col["cnt"]}')
    total += col["cnt"]
print(f'Total: {total}')
