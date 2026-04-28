import sqlite3
conn = sqlite3.connect('data/laws.db')

# Show schema first
cols = [r[1] for r in conn.execute("PRAGMA table_info(sentenze)").fetchall()]
print("Columns:", cols)

b = conn.execute('SELECT COUNT(*) FROM sentenze').fetchone()[0]
print(f"Before: {b}")

# Sample a row to see what we're dealing with
if b > 0:
    row = conn.execute("SELECT * FROM sentenze LIMIT 1").fetchone()
    print("Sample row:", row)

# Delete rows where oggetto or testo contains CAPTCHA-related content
conn.execute("""
DELETE FROM sentenze
WHERE LOWER(COALESCE(oggetto,'')) LIKE '%captcha%'
   OR LOWER(COALESCE(testo,'')) LIKE '%captcha%'
   OR LOWER(COALESCE(oggetto,'')) LIKE '%radware%'
   OR LOWER(COALESCE(testo,'')) LIKE '%radware%'
""")
conn.commit()

a = conn.execute('SELECT COUNT(*) FROM sentenze').fetchone()[0]
print(f"After: {a}, Removed: {b-a}")
conn.close()
