import sqlite3
db = sqlite3.connect('data/laws.db')
c = db.cursor()

print("=== LEGGE URNs ===")
c.execute("SELECT urn FROM laws WHERE type = 'LEGGE' LIMIT 5")
for r in c.fetchall():
    print(f"  {r[0]}")

print("\n=== CITED legge URNs ===")
c.execute("SELECT DISTINCT cited_urn FROM citations WHERE cited_urn LIKE '%legge%' LIMIT 5")
for r in c.fetchall():
    print(f"  {r[0]}")

print("\n=== DECRETO LEGISLATIVO URNs ===")
c.execute("SELECT urn FROM laws WHERE type = 'DECRETO LEGISLATIVO' LIMIT 5")
for r in c.fetchall():
    print(f"  {r[0]}")

print("\n=== CITED decreto.legislativo URNs ===")
c.execute("SELECT DISTINCT cited_urn FROM citations WHERE cited_urn LIKE '%decreto.legislativo%' LIMIT 5")
for r in c.fetchall():
    print(f"  {r[0]}")

print("\n=== DPR URNs ===")
c.execute("SELECT urn FROM laws WHERE type = 'DECRETO DEL PRESIDENTE DELLA REPUBBLICA' LIMIT 5")
for r in c.fetchall():
    print(f"  {r[0]}")

print("\n=== CITED dpr URNs ===")
c.execute("SELECT DISTINCT cited_urn FROM citations WHERE cited_urn LIKE '%presidente.della.repubblica%' LIMIT 5")
for r in c.fetchall():
    print(f"  {r[0]}")

# Try fuzzy match: extract year;number from cited and match against laws
print("\n=== Attempting fuzzy match ===")
# Get a cited URN and try to find its law
c.execute("SELECT cited_urn FROM citations WHERE cited_urn LIKE '%legge%' LIMIT 1")
sample = c.fetchone()[0]
print(f"  Cited: {sample}")
# Extract year and number
parts = sample.split(":")
type_part = parts[3] if len(parts) > 3 else ""
id_part = parts[4] if len(parts) > 4 else ""
print(f"  Type: {type_part}, ID: {id_part}")
# Try to find match using LIKE with year and number
year = id_part.split(";")[0] if ";" in id_part else ""
number = id_part.split(";")[1] if ";" in id_part else ""
print(f"  Year: {year}, Number: {number}")
if year and number:
    c.execute(f"SELECT urn, title FROM laws WHERE urn LIKE '%legge%' AND urn LIKE '%{year}%' AND urn LIKE '%;{number}' LIMIT 3")
    matches = c.fetchall()
    print(f"  Matches: {len(matches)}")
    for m in matches:
        print(f"    {m[0]}")
        print(f"    {m[1][:60]}")

db.close()
