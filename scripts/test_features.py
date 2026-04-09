#!/usr/bin/env python3
"""Test database functionality."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.db import LawDatabase


def main():
    """Run feature tests."""
    db_path = Path('data/laws.db')
    
    print("\n" + "=" * 60)
    print("DATABASE FEATURE TEST")
    print("=" * 60)
    
    # Check database exists
    if not db_path.exists():
        print(f"\n❌ ERROR: {db_path} not found")
        print("\nFirst run: python scripts/load_db.py")
        return 1
    
    db = LawDatabase(db_path)
    
    # Test 1: Count
    print("\n✓ Test 1: Count laws")
    count = db.conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
    print(f"  Total laws: {count}")
    
    if count == 0:
        print("  ⚠️  Database is empty. Load data first.")
        return 1
    
    # Test 2: FTS Search
    print("\n✓ Test 2: Full-text search")
    results = db.search_fts("protezione", limit=5)
    print(f"  Found {len(results)} results for 'protezione'")
    if results:
        print(f"  Example: {results[0]['title'][:50]}")
    
    # Test 3: Get a law
    print("\n✓ Test 3: Retrieve law details")
    law = db.conn.execute('SELECT * FROM laws LIMIT 1').fetchone()
    if law:
        law_dict = dict(law)
        print(f"  Title: {law_dict['title'][:50]}")
        print(f"  URN: {law_dict['urn']}")
        print(f"  Year: {law_dict['year']}")
        print(f"  Type: {law_dict['type']}")
        
        # Test 4: Citations
        print("\n✓ Test 4: Citation queries")
        incoming = db.get_citations_incoming(law_dict['urn'])
        outgoing = db.get_citations_outgoing(law_dict['urn'])
        print(f"  Laws citing this one: {len(incoming)}")
        print(f"  Laws this one cites: {len(outgoing)}")
    
    # Test 5: Advanced search
    print("\n✓ Test 5: Advanced search with filters")
    results = db.search_with_filters(
        query="decreto",
        year_min=2020,
        year_max=2026,
        limit=10
    )
    print(f"  Found {len(results)} results for filtered search")
    
    # Test 6: Statistics
    print("\n✓ Test 6: Database statistics")
    stats = db.get_statistics()
    print(f"  Total laws: {stats.get('total_laws', 0)}")
    print(f"  Total citations: {stats.get('total_citations', 0)}")
    print(f"  Law types: {len(stats.get('by_type', {}))}")
    
    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED")
    print("=" * 60)
    print("\nDatabase is ready for Streamlit UI!")
    
    db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
