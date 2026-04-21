#!/usr/bin/env python3
"""Quick production status check and accounting law search."""
import sys
sys.path.insert(0, '.')
from core.db import LawDatabase

db = LawDatabase('data/laws.db')

# Production status
stats = db.get_statistics()
print("=== PRODUCTION DATABASE STATUS ===")
for k, v in stats.items():
    print(f"  {k}: {v}")

# Tax law domain count
rows = db.conn.execute(
    "SELECT COUNT(*) FROM law_metadata WHERE domain = 'diritto_tributario'"
).fetchone()
print(f"\nTax law (diritto_tributario) count: {rows[0]}")

# Search for accounting/fiscal terms
for query in ["contabilita bilancio fiscale", "revisore contabile", "imposta reddito societa"]:
    results = db.search_fts(query, limit=5)
    print(f"\nSearch '{query}' -> {len(results)} results:")
    for r in results:
        score = r.get('importance_score', 0) or 0
        print(f"  [{r['type']:>20}] score={score:>6.2f} | {r['title'][:65]} ({r.get('date','')})")

db.close()
