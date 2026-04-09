#!/usr/bin/env python3
"""Load laws from JSONL into SQLite database."""

import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.db import LawDatabase


def main():
    """Load JSONL into database."""
    db_path = Path('data/laws.db')
    jsonl_file = Path('data/processed/laws_vigente.jsonl')
    
    print("=" * 60)
    print("NORMATTIVA DATABASE LOADER")
    print("=" * 60)
    
    # Check source exists
    if not jsonl_file.exists():
        print(f"\n❌ ERROR: {jsonl_file} not found")
        print("\nPlease run: python pipeline.py --variants vigente")
        return 1
    
    # Create/connect database
    print(f"\nConnecting to database: {db_path}")
    db = LawDatabase(db_path)
    
    # Load data
    print(f"\nLoading laws from {jsonl_file}...")
    count = db.insert_laws_from_jsonl(jsonl_file)
    
    # Show results
    print(f"\n✓ Loaded {count} laws into database")
    
    # Statistics
    if count > 0:
        print("\nDatabase Statistics:")
        stats = db.get_statistics()
        print(f"  Total laws: {stats.get('total_laws', 0)}")
        print(f"  Total citations: {stats.get('total_citations', 0)}")
        
        types = stats.get('by_type', {})
        if types:
            print(f"  Law types: {len(types)}")
            for law_type, type_count in sorted(types.items(), key=lambda x: -x[1])[:5]:
                print(f"    - {law_type}: {type_count}")
        
        print(f"\n✅ Database ready for use!")
    else:
        print("\n⚠️  No laws loaded. Check source file.")
        return 1
    
    db.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
