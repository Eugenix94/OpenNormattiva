#!/usr/bin/env python3
"""
Live API Change Monitor for Normattiva

Polls the Normattiva API for collection changes using ETags.
Detects new/updated collections and provides preview data
*before* the full pipeline runs.

Usage:
    from law_monitor import LawMonitor
    monitor = LawMonitor(db)
    changes = monitor.check_all_collections()
    pending = monitor.get_pending_changes()
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

from normattiva_api_client import NormattivaAPI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LawMonitor:
    """Monitor Normattiva API for changes and preview updates."""

    ETAG_CACHE_PATH = Path('data/.etag_cache.json')

    def __init__(self, db=None, api: Optional[NormattivaAPI] = None):
        self.api = api or NormattivaAPI(timeout_s=15, retries=1)
        self.db = db
        self._etag_cache = self._load_etag_cache()

    # ── ETag cache ───────────────────────────────────────────────────────

    def _load_etag_cache(self) -> Dict[str, str]:
        """Load saved ETags from disk."""
        if self.ETAG_CACHE_PATH.exists():
            try:
                return json.loads(self.ETAG_CACHE_PATH.read_text())
            except Exception:
                pass
        return {}

    def _save_etag_cache(self):
        """Persist ETags to disk."""
        self.ETAG_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.ETAG_CACHE_PATH.write_text(json.dumps(self._etag_cache, indent=2))

    # ── Core monitoring ──────────────────────────────────────────────────

    def check_collection(self, name: str) -> Optional[Dict]:
        """Check a single collection for changes.

        Returns change dict if changed, None if unchanged or error.
        """
        try:
            new_etag = self.api.check_collection_etag(name, variant='V', format='AKN')
            if not new_etag:
                return None

            old_etag = self._etag_cache.get(name)

            if old_etag and old_etag == new_etag:
                return None  # No change

            change = {
                'collection': name,
                'old_etag': old_etag,
                'new_etag': new_etag,
                'detected_at': datetime.utcnow().isoformat() + 'Z',
                'is_new': old_etag is None,
            }

            # Update cache
            self._etag_cache[name] = new_etag

            return change

        except Exception as e:
            logger.debug(f"Error checking {name}: {e}")
            return None

    def check_all_collections(self) -> List[Dict]:
        """Check all collections from catalogue for changes.

        Returns list of detected changes.
        """
        changes = []

        try:
            catalogue = self.api.get_collection_catalogue()
        except Exception as e:
            logger.error(f"Failed to fetch catalogue: {e}")
            return changes

        seen = set()
        for c in catalogue:
            name = c.get('nomeCollezione', c.get('nome'))
            if not name or name in seen:
                continue
            seen.add(name)

            change = self.check_collection(name)
            if change:
                # Attach catalogue metadata
                change['num_acts'] = c.get('numeroAtti', 0)
                change['created'] = c.get('dataCreazione', '')
                changes.append(change)

        self._save_etag_cache()

        # Record in DB if available
        if self.db and changes:
            self._record_changes(changes)

        logger.info(
            f"Checked {len(seen)} collections: {len(changes)} changed"
        )
        return changes

    def _record_changes(self, changes: List[Dict]):
        """Record detected changes in database."""
        for ch in changes:
            try:
                self.db.conn.execute('''
                    INSERT INTO api_changes
                    (collection, old_etag, new_etag, detected_at, status, preview_data)
                    VALUES (?, ?, ?, ?, 'pending', ?)
                ''', (
                    ch['collection'],
                    ch.get('old_etag'),
                    ch['new_etag'],
                    ch['detected_at'],
                    json.dumps({
                        'num_acts': ch.get('num_acts', 0),
                        'is_new': ch.get('is_new', False),
                    }),
                ))
            except Exception as e:
                logger.warning(f"Failed to record change for {ch['collection']}: {e}")
        try:
            self.db.conn.commit()
        except Exception:
            pass

    # ── Pending changes ──────────────────────────────────────────────────

    def get_pending_changes(self) -> List[Dict]:
        """Get all unprocessed API changes from DB."""
        if not self.db:
            return []
        try:
            rows = self.db.conn.execute('''
                SELECT id, collection, old_etag, new_etag, detected_at, preview_data
                FROM api_changes
                WHERE status = 'pending'
                ORDER BY detected_at DESC
            ''').fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if d.get('preview_data'):
                    d['preview_data'] = json.loads(d['preview_data'])
                results.append(d)
            return results
        except Exception:
            return []

    def get_recent_changes(self, limit: int = 20) -> List[Dict]:
        """Get recent API changes (all statuses)."""
        if not self.db:
            return []
        try:
            rows = self.db.conn.execute('''
                SELECT id, collection, old_etag, new_etag, detected_at,
                       status, preview_data, processed_at
                FROM api_changes
                ORDER BY detected_at DESC
                LIMIT ?
            ''', (limit,)).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if d.get('preview_data'):
                    try:
                        d['preview_data'] = json.loads(d['preview_data'])
                    except Exception:
                        pass
                results.append(d)
            return results
        except Exception:
            return []

    def mark_processed(self, change_ids: List[int]):
        """Mark changes as processed after pipeline run."""
        if not self.db or not change_ids:
            return
        now = datetime.utcnow().isoformat() + 'Z'
        for cid in change_ids:
            self.db.conn.execute(
                "UPDATE api_changes SET status='processed', processed_at=? WHERE id=?",
                (now, cid),
            )
        self.db.conn.commit()

    def get_change_summary(self) -> Dict:
        """Get summary of all tracked changes."""
        if not self.db:
            return {}
        try:
            total = self.db.conn.execute(
                'SELECT COUNT(*) FROM api_changes'
            ).fetchone()[0]
            pending = self.db.conn.execute(
                "SELECT COUNT(*) FROM api_changes WHERE status='pending'"
            ).fetchone()[0]
            latest = self.db.conn.execute(
                'SELECT detected_at FROM api_changes ORDER BY detected_at DESC LIMIT 1'
            ).fetchone()
            return {
                'total_detected': total,
                'pending': pending,
                'last_check': latest[0] if latest else None,
            }
        except Exception:
            return {}
