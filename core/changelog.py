#!/usr/bin/env python3
"""
Changelog Tracking for Normattiva Pipeline

Tracks what changed between pipeline runs:
- New laws added
- Laws updated
- Citation changes
- Legislature metadata updates

Allows Space to display:
1. What's new since last update
2. Jurisprudential evolution
3. Legislature metadata (government, parliament info)
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import hashlib

class ChangelogTracker:
    """Track dataset changes between runs."""
    
    def __init__(self, changelog_path: Path = Path('data/changelog.jsonl')):
        self.changelog_path = changelog_path
        self.changelog_path.parent.mkdir(parents=True, exist_ok=True)
    
    def record_update(self, 
                     timestamp: str,
                     laws_added: int,
                     laws_updated: int,
                     citations_added: int,
                     legislatures_updated: int,
                     changes: Dict) -> Dict:
        """Record an update run."""
        entry = {
            'timestamp': timestamp,
            'laws_added': laws_added,
            'laws_updated': laws_updated,
            'citations_added': citations_added,
            'legislatures_updated': legislatures_updated,
            'changes': changes,
        }
        
        # Append to JSONL
        with open(self.changelog_path, 'a') as f:
            f.write(json.dumps(entry) + '\n')
        
        return entry
    
    def compare_databases(self, 
                         old_db_path: Path, 
                         new_db_path: Path) -> Dict:
        """Compare two database snapshots.
        
        Returns:
            {
                'laws_added': [urns],
                'laws_updated': [urns],
                'citations_new': count,
                'legislatures_affected': [ids]
            }
        """
        if not old_db_path.exists():
            return {
                'laws_added': [],
                'laws_updated': [],
                'citations_new': 0,
                'legislatures_affected': [],
                'note': 'First run - no previous data'
            }
        
        old = sqlite3.connect(str(old_db_path))
        new = sqlite3.connect(str(new_db_path))
        
        try:
            # New laws
            old_urns = set(old.execute('SELECT urn FROM laws').fetchall())
            new_urns = set(new.execute('SELECT urn FROM laws').fetchall())
            laws_added = list(new_urns - old_urns)
            
            # Updated laws (same URN, different content/metadata)
            laws_updated = []
            for urn in old_urns & new_urns:
                old_row = old.execute(
                    'SELECT title, text_length, importance_score FROM laws WHERE urn=?',
                    (urn,)
                ).fetchone()
                new_row = new.execute(
                    'SELECT title, text_length, importance_score FROM laws WHERE urn=?',
                    (urn,)
                ).fetchone()
                
                if old_row != new_row:
                    laws_updated.append(urn)
            
            # New citations
            old_cit_count = old.execute(
                'SELECT COUNT(*) FROM citations'
            ).fetchone()[0]
            new_cit_count = new.execute(
                'SELECT COUNT(*) FROM citations'
            ).fetchone()[0]
            citations_new = new_cit_count - old_cit_count
            
            # Updated legislatures
            try:
                new_leg_count = new.execute(
                    'SELECT COUNT(DISTINCT legislature_id) FROM laws'
                ).fetchone()[0]
            except:
                new_leg_count = 0
            
            return {
                'laws_added': laws_added[:100],  # Sample first 100
                'laws_added_total': len(laws_added),
                'laws_updated': laws_updated[:50],
                'laws_updated_total': len(laws_updated),
                'citations_new': citations_new,
                'legislatures_affected': new_leg_count,
            }
        finally:
            old.close()
            new.close()
    
    def get_latest_updates(self, limit: int = 10) -> List[Dict]:
        """Get latest changelog entries."""
        if not self.changelog_path.exists():
            return []
        
        entries = []
        with open(self.changelog_path, 'r') as f:
            for line in f:
                entries.append(json.loads(line))
        
        return entries[-limit:]
    
    def get_summary(self) -> Dict:
        """Get overall summary of all changes."""
        if not self.changelog_path.exists():
            return {}
        
        total_added = 0
        total_updated = 0
        total_citations = 0
        all_legislatures = set()
        
        with open(self.changelog_path, 'r') as f:
            for line in f:
                entry = json.loads(line)
                total_added += entry.get('laws_added', 0)
                total_updated += entry.get('laws_updated', 0)
                total_citations += entry.get('citations_added', 0)
                # Could track legislatures too
        
        return {
            'total_laws_added_history': total_added,
            'total_laws_updated_history': total_updated,
            'total_citations_added_history': total_citations,
            'update_runs': len(list(self._read_changelog())),
        }
    
    def _read_changelog(self):
        """Iterator over changelog entries."""
        if self.changelog_path.exists():
            with open(self.changelog_path, 'r') as f:
                for line in f:
                    yield json.loads(line)
