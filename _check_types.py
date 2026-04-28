import sqlite3
conn = sqlite3.connect('data/laws.db')

print('=== LAW TYPES ===')
rows = conn.execute('SELECT type, COUNT(*) as cnt FROM laws GROUP BY type ORDER BY cnt DESC').fetchall()
for r in rows:
    print(f'  {repr(r[0]):45s} {r[1]:>7,}')

print()
print('=== CORTE COSTITUZIONALE related ===')
q = """
SELECT type, COUNT(*) FROM laws
WHERE LOWER(type) LIKE '%corte%'
   OR LOWER(title) LIKE '%corte costituzionale%'
   OR LOWER(type) LIKE '%sentenza%'
   OR LOWER(type) LIKE '%ordinanza%'
GROUP BY type ORDER BY COUNT(*) DESC
"""
rows2 = conn.execute(q).fetchall()
for r in rows2:
    print(f'  {repr(r[0]):45s} {r[1]:>7,}')

print()
print('=== SAMPLE TITLES (corte costituzionale) ===')
rows3 = conn.execute("""
    SELECT title, type, year FROM laws
    WHERE LOWER(title) LIKE '%corte costituzionale%'
    LIMIT 10
""").fetchall()
for r in rows3:
    print(f'  [{r[2]}] {r[1]} — {r[0][:80]}')

conn.close()
