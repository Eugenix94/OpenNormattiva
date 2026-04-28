#!/usr/bin/env python3
"""
Live Updater for Normattiva

Monitors vigente collections for changes and updates JSONL incrementally.
Uses ETag to detect modifications without re-downloading entire collections.

Usage:
    python live_updater.py --variant vigente --collections all
    python live_updater.py --collections "DPR" "Codici"
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import zipfile
import tempfile

from normattiva_api_client import NormattivaAPI
from parse_akn import AKNParser

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ETagCache:
    """Simple ETag cache to detect collection changes."""
    
    def __init__(self, cache_file: Path):
        self.cache_file = cache_file
        self.data = self._load()
    
    def _load(self) -> Dict:
        """Load cache from disk."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load ETag cache: {e}")
        return {}
    
    def save(self):
        """Persist cache to disk."""
        self.cache_file.parent.mkdir(exist_ok=True)
        with open(self.cache_file, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def get(self, collection: str, variant: str = 'V') -> Optional[str]:
        """Get cached ETag for collection."""
        key = f"{collection}#{variant}"
        return self.data.get(key, {}).get('etag')
    
    def set(self, collection: str, etag: str, variant: str = 'V'):
        """Cache ETag for collection."""
        key = f"{collection}#{variant}"
        self.data[key] = {
            'etag': etag,
            'checked_at': datetime.now().isoformat(),
            'variant': variant
        }
        self.save()


class AmendmentLog:
    """Track amendments and changes over time."""
    
    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.log_file.parent.mkdir(exist_ok=True)
    
    def record_amendment(self, law_urn: str, collection: str, action: str, details: Dict = None):
        """Record an amendment or change."""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'law_urn': law_urn,
            'collection': collection,
            'action': action,  # 'added', 'updated', 'removed'
            'details': details or {}
        }
        
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    
    def get_recent(self, hours: int = 24) -> List[Dict]:
        """Get amendments from last N hours."""
        from datetime import datetime, timedelta
        
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = []
        
        if self.log_file.exists():
            with open(self.log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry['timestamp'])
                    if ts > cutoff:
                        recent.append(entry)
        
        return recent


class LiveUpdater:
    """Monitors and updates vigente collections in real-time."""
    
    def __init__(self, data_dir: Path = Path('data')):
        self.data_dir = data_dir
        self.raw_dir = data_dir / 'raw'
        self.processed_dir = data_dir / 'processed'
        self.indexes_dir = data_dir / 'indexes'
        
        for d in [self.raw_dir, self.processed_dir, self.indexes_dir]:
            d.mkdir(exist_ok=True)
        
        self.api = NormattivaAPI()
        self.parser = AKNParser()
        
        self.etag_cache = ETagCache(self.data_dir / '.etag_cache.json')
        self.amendment_log = AmendmentLog(self.processed_dir / 'amendments.jsonl')

    @staticmethod
    def variant_code_to_name(variant: str) -> str:
        mapping = {'V': 'vigente', 'M': 'multivigente', 'O': 'originale'}
        return mapping.get(variant, variant.lower())

    @staticmethod
    def infer_status(variant: str, collection: str) -> str:
        c = (collection or '').lower()
        if 'abrogat' in c:
            return 'abrogated'
        if variant == 'V':
            return 'in_force'
        if variant == 'M':
            return 'multi_version'
        if variant == 'O':
            return 'original_text'
        return 'unknown'
    
    def check_for_updates(self, collections: List[str] = None, variant: str = 'V') -> Dict[str, bool]:
        """Check which collections have changed since last sync."""
        if collections is None:
            # Get all collections from catalogue
            try:
                catalogue = self.api.get_collection_catalogue()
                collections = []
                seen = set()
                target_variant = variant
                for entry in catalogue:
                    if entry.get('formatoCollezione') == target_variant:
                        nome = entry.get('nomeCollezione')
                        if nome not in seen:
                            collections.append(nome)
                            seen.add(nome)
            except Exception as e:
                logger.error(f"Failed to get catalogue: {e}")
                return {}
        
        changed = {}
        
        for collection in collections:
            try:
                # Check ETag without downloading
                current_etag = self.api.check_collection_etag(collection, variant=variant)
                cached_etag = self.etag_cache.get(collection, variant)
                
                if current_etag != cached_etag:
                    changed[collection] = True
                    logger.info(f"✓ Change detected: {collection}")
                else:
                    changed[collection] = False
                    logger.info(f"✗ No change: {collection}")
                
                # Update cache
                if current_etag:
                    self.etag_cache.set(collection, current_etag, variant)
            
            except Exception as e:
                logger.warning(f"Could not check ETag for {collection}: {e}")
                changed[collection] = False
        
        return changed
    
    def update_collection(self, collection: str, variant: str = 'V') -> int:
        """Download and parse updated collection."""
        logger.info(f"Updating {collection} ({variant})...")
        
        try:
            # Download
            data, etag, ct = self.api.get_collection(collection, variant=variant)
            
            # Parse to temporary location
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(data)
            
            laws = self.parser.parse_zip_file(tmp_path)
            tmp_path.unlink()  # Clean up temp file
            
            # Merge into existing JSONL by REPLACING the current collection slice.
            # This keeps the dataset aligned with the present state instead of only appending.
            variant_name = self.variant_code_to_name(variant)
            jsonl_file = self.processed_dir / f"laws_{variant_name}.jsonl"
            
            # Load existing laws by URN
            existing = {}
            if jsonl_file.exists():
                with open(jsonl_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        law = json.loads(line)
                        existing[law.get('urn')] = law

            # Remove the previous copy of this collection before inserting the fresh one.
            previous_urns = {
                urn for urn, law in existing.items()
                if (law.get('source_collection') or '') == collection
            }
            for urn in previous_urns:
                existing.pop(urn, None)
            
            # Merge new laws
            updated_count = 0
            added_count = 0
            current_urns = set()
            
            for law in laws:
                urn = law.get('urn')
                if not urn:
                    continue
                current_urns.add(urn)

                if urn in previous_urns:
                    # Law already existed - record update
                    updated_count += 1
                    self.amendment_log.record_amendment(
                        urn, collection, 'updated',
                        {'title': law.get('title')}
                    )
                else:
                    # New law
                    added_count += 1
                    self.amendment_log.record_amendment(
                        urn, collection, 'added',
                        {'title': law.get('title')}
                    )
                
                # Replace in dict (new version)
                law = self.parser.enrich_with_metadata(law)
                law['source_collection'] = collection
                law['status'] = self.infer_status(variant, collection)
                existing[urn] = law

            removed_urns = previous_urns - current_urns
            for urn in sorted(removed_urns):
                self.amendment_log.record_amendment(
                    urn, collection, 'removed', {'variant': variant_name}
                )
            
            # Write back to JSONL
            with open(jsonl_file, 'w', encoding='utf-8') as f:
                for law in existing.values():
                    f.write(json.dumps(law, ensure_ascii=False) + '\n')
            
            logger.info(
                f"✓ Updated {collection}: {added_count} added, {updated_count} updated, {len(removed_urns)} removed"
            )
            
            # Update ETag cache
            if etag:
                self.etag_cache.set(collection, etag, variant)
            
            return added_count + updated_count + len(removed_urns)
        
        except Exception as e:
            logger.error(f"Failed to update {collection}: {e}")
            return 0
    
    def full_sync(self, collections: List[str] = None, variant: str = 'V'):
        """Full sync: check all, update changed ones."""
        logger.info(f"Starting full sync (variant: {variant})...")
        
        # Check for changes
        changed = self.check_for_updates(collections, variant)
        
        changed_collections = [c for c, modified in changed.items() if modified]
        
        if not changed_collections:
            logger.info("No changes detected. All collections up-to-date.")
            return
        
        logger.info(f"Updating {len(changed_collections)} changed collection(s)...")
        
        total_changed = 0
        for collection in changed_collections:
            count = self.update_collection(collection, variant)
            total_changed += count
        
        logger.info(f"✓ Sync complete: {total_changed} laws changed/added")
        
        # Show recent amendments
        recent = self.amendment_log.get_recent(hours=1)
        if recent:
            logger.info(f"Recent amendments ({len(recent)} in last hour):")
            for entry in recent[-5:]:  # Last 5
                logger.info(f"  {entry['action']}: {entry['law_urn']}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Live updater for Normattiva vigente')
    parser.add_argument(
        '--variant', '-v',
        default='vigente',
        choices=['vigente', 'multivigente', 'originale'],
        help='Law variant to track'
    )
    parser.add_argument(
        '--collections', '-c',
        nargs='+',
        help='Specific collections to update (default: all)'
    )
    parser.add_argument(
        '--data-dir', '-d',
        default='data',
        help='Data directory'
    )
    parser.add_argument(
        '--check-only',
        action='store_true',
        help='Only check for changes, do not update'
    )
    
    args = parser.parse_args()
    
    variant_map = {'vigente': 'V', 'multivigente': 'M', 'originale': 'O'}
    variant_code = variant_map.get(args.variant, 'V')
    
    updater = LiveUpdater(Path(args.data_dir))
    
    if args.check_only:
        logger.info("Checking for updates (no downloads)...")
        changed = updater.check_for_updates(args.collections, variant_code)
        
        changed_count = sum(1 for v in changed.values() if v)
        logger.info(f"Result: {changed_count}/{len(changed)} collections have changes")
    
    else:
        updater.full_sync(args.collections, variant_code)


if __name__ == '__main__':
    main()
