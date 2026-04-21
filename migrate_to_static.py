#!/usr/bin/env python3
"""
Quick Migration Script: Convert to Static Pipeline

This script helps you transition from the dynamic (reload everything) pipeline
to the static (incremental) pipeline.

Usage:
    python migrate_to_static.py --help
"""

import shutil
from pathlib import Path
import json

def migrate():
    """Perform migration to static pipeline."""
    
    data_dir = Path('data')
    print("=" * 80)
    print("MIGRATE TO STATIC PIPELINE")
    print("=" * 80)
    
    # Backup existing data
    print("\n1. Backing up existing data...")
    backup_dir = data_dir / '.backup'
    backup_dir.mkdir(exist_ok=True)
    
    for item in (data_dir / 'processed').glob('*.jsonl'):
        if item.is_file():
            backup_path = backup_dir / item.name
            shutil.copy2(item, backup_path)
            print(f"   ✓ Backed up {item.name}")
    
    # Check existing state
    print("\n2. Analyzing current state...")
    
    vigente_path = data_dir / 'processed' / 'laws_vigente.jsonl'
    if vigente_path.exists():
        lines = sum(1 for _ in open(vigente_path))
        size_mb = vigente_path.stat().st_size / 1e6
        print(f"   ✓ Found existing vigente.jsonl: {lines:,} laws, {size_mb:.1f} MB")
    else:
        print("   - No existing vigente.jsonl (expected for first run)")
    
    # Create state file
    print("\n3. Initializing static pipeline state...")
    
    state_file = data_dir / '.static_state.json'
    state = {
        'version': 1,
        'mode': 'migrated',
        'vigente_completed': vigente_path.exists(),
        'abrogate_completed': False,
        'vigente_collections': {},
        'abrogate_etag': None,
        'last_update': None,
        'migrated_from': 'dynamic_pipeline'
    }
    
    with open(state_file, 'w') as f:
        json.dump(state, f, indent=2)
    
    print("   ✓ Created .static_state.json")
    
    # Show next steps
    print("\n4. Next steps:")
    print("   a) Review the new pipeline structure:")
    print("      - static_pipeline.py (main script)")
    print("      - STATIC_PIPELINE_GUIDE.md (usage guide)")
    print("      - DATA_FORMAT_ANALYSIS.md (formats explained)")
    
    print("\n   b) Run initial full build (if this is first time):")
    print("      python static_pipeline.py --mode full")
    
    print("\n   c) Or sync to check what's new:")
    print("      python static_pipeline.py --mode sync")
    
    print("\n   d) Schedule weekly syncs in GitHub Actions")
    
    print("\n   e) Remove old pipeline references:")
    print("      - Edit redeploy.py to use static_pipeline.py")
    print("      - Update .github/workflows/nightly-update.yml")
    print("      - Archive/remove old pipeline.py")
    
    print("\n" + "=" * 80)
    print("✓ MIGRATION READY")
    print("=" * 80)
    print("Backup created in: data/.backup/")
    print("\nYour existing data is safe. Ready to use new static pipeline!")

if __name__ == '__main__':
    migrate()
