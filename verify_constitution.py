import sqlite3
db = sqlite3.connect('data/laws.db')
r = db.execute("SELECT urn, title, type, year, text_length, article_count FROM laws WHERE type='COSTITUZIONE'").fetchall()
print("Constitution records:", len(r))
for row in r:
    print(f"  URN: {row[0]}")
    print(f"  Title: {row[1]}")
    print(f"  Type: {row[2]}, Year: {row[3]}")
    print(f"  Text length: {row[4]:,}, Articles: {row[5]}")
total = db.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
print(f"\nTotal laws: {total:,}")
db.close()
