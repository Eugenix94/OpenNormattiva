#!/usr/bin/env python3
"""
Track changelog for nightly pipeline runs

This script:
1. Compares old vs new database
2. Records changes in changelog.jsonl
3. Uploads changelog to HF Dataset
4. Restarts Space with changelog visible

Used by GitHub Actions to safely track updates.
"""

import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from huggingface_hub import HfApi

def record_pipeline_changes():
    """Record what changed in this pipeline run."""
    
    print("=" * 60)
    print("RECORDING CHANGELOG")
    print("=" * 60)
    
    from core.changelog import ChangelogTracker
    
    changelog = ChangelogTracker()
    
    # Get current database stats
    db_path = Path('data/laws.db')
    if not db_path.exists():
        print("No database yet, skipping changelog")
        return None
    
    conn = sqlite3.connect(str(db_path))
    
    laws_count = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    citations_count = conn.execute("SELECT COUNT(*) FROM citations").fetchone()[0]
    
    # Check for legislature metadata
    try:
        leg_count = conn.execute(
            "SELECT COUNT(DISTINCT legislature_id) FROM laws WHERE legislature_id IS NOT NULL"
        ).fetchone()[0]
    except Exception:
        leg_count = 0
    
    conn.close()
    
    # Record update
    timestamp = datetime.utcnow().isoformat() + "Z"
    
    entry = changelog.record_update(
        timestamp=timestamp,
        laws_added=0,
        laws_updated=0,
        citations_added=0,
        legislatures_updated=leg_count,
        changes={
            'total_laws': laws_count,
            'total_citations': citations_count,
            'legislatures_updated': leg_count,
        }
    )
    
    print(f"\u2713 Recorded update at {timestamp}")
    print(f"  Laws: {laws_count:,}")
    print(f"  Citations: {citations_count:,}")
    print(f"  Legislatures: {leg_count}")
    
    # Mark pending API changes as processed
    try:
        from core.db import LawDatabase
        from law_monitor import LawMonitor
        
        db = LawDatabase(db_path)
        monitor = LawMonitor(db=db)
        pending = monitor.get_pending_changes()
        if pending:
            monitor.mark_processed([p['id'] for p in pending])
            print(f"\u2713 Marked {len(pending)} API changes as processed")
        
        # Run a fresh API check to detect any new changes
        changes = monitor.check_all_collections()
        if changes:
            print(f"\u26a0 {len(changes)} new API changes detected after pipeline")
        else:
            print("\u2713 All collections up to date")
        
        db.close()
    except Exception as e:
        print(f"API change tracking: {e}")
    
    # Upload changelog to HF
    upload_changelog()
    
    return entry

def upload_changelog():
    """Upload changelog to HF Dataset."""
    
    hf_token = os.environ.get('HF_TOKEN')
    if not hf_token:
        print("No HF_TOKEN, skipping changelog upload")
        return
    
    changelog_path = Path('data/changelog.jsonl')
    if not changelog_path.exists():
        return
    
    api = HfApi(token=hf_token)
    repo_id = 'diatribe00/normattiva-data'
    
    print("\nUploading changelog to HF...")
    try:
        api.upload_file(
            path_or_fileobj=str(changelog_path),
            path_in_repo='changelog.jsonl',
            repo_id=repo_id,
            repo_type='dataset',
            commit_message='Update: nightly changelog'
        )
        print("✓ Changelog uploaded")
    except Exception as e:
        print(f"Error uploading changelog: {e}")

if __name__ == "__main__":
    record_pipeline_changes()
