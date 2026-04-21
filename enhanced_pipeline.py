#!/usr/bin/env python3
"""
ENHANCED PIPELINE: Incremental Updates + Staging/Production Mirror
====================================================================

Improvements over static_pipeline.py:

1. **Update-Only Downloads**
   - Only fetch NEW/CHANGED laws since last sync
   - No re-downloading entire collections
   - Keep pipeline live during updates

2. **Staging/Production Pattern**
   - staging_jsonl: Where we build updates
   - production_jsonl: Where users query (never interrupted)
   - Verification step before promoting staging → production

3. **Multivigente Support (Optional)**
   - Can include historical versions
   - Separate dataset from current
   - For research use case

Usage:
    python enhanced_pipeline.py --mode incremental      # Update only new laws
    python enhanced_pipeline.py --mode staging-verify   # Check staging before promote
    python enhanced_pipeline.py --mode promote          # Move staging to production
    python enhanced_pipeline.py --status                # Show current state
"""

import json
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Set
from datetime import datetime, timedelta
import hashlib
import tempfile
import shutil

from normattiva_api_client import NormattivaAPI
from parse_akn import AKNParser

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

VIGENTE_COLLECTIONS = [
    'Codici', 'DL proroghe', 'Leggi costituzionali', 'Regi decreti', 'DPR',
    'DL e leggi di conversione', 'Decreti Legislativi', 'Leggi di ratifica',
    'Regolamenti ministeriali', 'Regolamenti governativi', 'DL decaduti',
    'Decreti legislativi luogotenenziali', 'Leggi delega e relativi provvedimenti delegati',
    'Atti di recepimento direttive UE', 'Regolamenti di delegificazione', 'DPCM',
    'Testi Unici', 'Regi decreti legislativi', 'Leggi contenenti deleghe',
    'Leggi finanziarie e di bilancio', 'Leggi di delegazione europea',
    'Atti di attuazione Regolamenti UE',
]

ABROGATE_COLLECTION = 'Atti normativi abrogati (in originale)'


class EnhancedPipeline:
    """Production-grade pipeline with incremental updates and staging."""
    
    def __init__(self, data_dir: Path = Path('data')):
        self.data_dir = Path(data_dir)
        self.api = NormattivaAPI(timeout_s=60, retries=2)
        self.parser = AKNParser()
        
        # Directory structure
        self.raw_dir = self.data_dir / 'raw'
        self.processed_dir = self.data_dir / 'processed'
        self.staging_dir = self.data_dir / 'staging'
        self.archives_dir = self.data_dir / 'archives'
        
        for d in [self.raw_dir, self.processed_dir, self.staging_dir, self.archives_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        # State files
        self.state_file = self.data_dir / '.enhanced_state.json'
        self.urns_index = self.data_dir / '.urns_index.json'  # Track all URNs seen
        self.removed_urns = self.data_dir / '.removed_urns.json'  # Track removed
        
        # Data files
        self.jsonl_vigente_prod = self.processed_dir / 'laws_vigente.jsonl'
        self.jsonl_vigente_staging = self.staging_dir / 'laws_vigente_staging.jsonl'
        self.jsonl_abrogate_prod = self.processed_dir / 'laws_abrogate.jsonl'
        self.jsonl_abrogate_staging = self.staging_dir / 'laws_abrogate_staging.jsonl'
        
        self.state = self._load_state()
        self.urns_seen = self._load_urns_index()
    
    def _load_state(self) -> Dict:
        """Load or initialize state."""
        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return {
            'version': 1,
            'mode': 'initial',
            'last_full_sync': None,
            'last_incremental_sync': None,
            'collections': {},  # name -> {etag, last_seen_laws_count, checksum}
            'production_stats': {'vigente_count': 0, 'abrogate_count': 0},
            'staging_stats': {'vigente_count': 0, 'abrogate_count': 0},
            'amendments': []
        }
    
    def _save_state(self):
        """Persist state."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    def _load_urns_index(self) -> Dict[str, Dict]:
        """Load or initialize URN index (deduplication)."""
        if self.urns_index.exists():
            with open(self.urns_index, 'r') as f:
                return json.load(f)
        return {}
    
    def _save_urns_index(self):
        """Save URN index."""
        with open(self.urns_index, 'w') as f:
            json.dump(self.urns_seen, f, indent=2)
    
    def _read_jsonl_into_dict(self, path: Path) -> Dict[str, Dict]:
        """Read JSONL file into dict keyed by URN for dedup."""
        result = {}
        if path.exists():
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            law = json.loads(line)
                            urn = law.get('urn')
                            if urn:
                                result[urn] = law
                        except json.JSONDecodeError:
                            pass
        return result
    
    def _write_jsonl_from_dict(self, path: Path, laws_dict: Dict[str, Dict]):
        """Write dict back to JSONL, sorted by URN."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            for urn in sorted(laws_dict.keys()):
                law = laws_dict[urn]
                f.write(json.dumps(law, ensure_ascii=False) + '\n')
    
    def download_collection_incremental(
        self, 
        collection_name: str, 
        variant: str = 'V'
    ) -> Optional[List[Dict]]:
        """
        Download collection and extract only NEW/CHANGED laws.
        
        Strategy:
        1. Download full collection (collections change infrequently)
        2. Parse all laws
        3. Compare with PRODUCTION version (not staging)
        4. Return only new/changed laws
        """
        logger.info(f"Checking {collection_name} ({variant})...")
        
        try:
            # Get current ETag
            current_etag = self.api.check_collection_etag(
                collection_name, 
                variant=variant,
                format='AKN'
            )
            
            coll_state = self.state['collections'].get(collection_name, {})
            cached_etag = coll_state.get(f'etag_{variant}')
            
            # If unchanged, return early
            if cached_etag == current_etag:
                logger.info(f"  ✓ {collection_name} unchanged")
                return None
            
            # Download
            logger.info(f"  ↓ Downloading {collection_name} ({variant})...")
            data, etag, _ = self.api.get_collection(
                collection_name,
                variant=variant,
                format='AKN'
            )
            
            # Save raw
            outfile = self.raw_dir / f"{collection_name}_{variant}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            with open(outfile, 'wb') as f:
                f.write(data)
            logger.info(f"  ✓ Downloaded {len(data)/1e6:.1f} MB")
            
            # Parse all
            all_laws = self.parser.parse_zip_file(outfile)
            logger.info(f"  ✓ Parsed {len(all_laws)} laws")
            
            # Find NEW or CHANGED laws compared to production
            prod_laws = self._read_jsonl_into_dict(self.jsonl_vigente_prod if variant == 'V' else self.jsonl_abrogate_prod)
            
            new_or_changed = []
            for law in all_laws:
                urn = law.get('urn')
                if urn not in prod_laws:
                    # Completely new
                    new_or_changed.append(law)
                else:
                    # Check if changed (compare text_length or other fields)
                    prod_law = prod_laws[urn]
                    if law.get('text_length') != prod_law.get('text_length') or \
                       law.get('article_count') != prod_law.get('article_count'):
                        # Changed
                        new_or_changed.append(law)
                    # else: unchanged, skip
            
            logger.info(f"  ✓ {len(new_or_changed)} new/changed laws")
            
            # Update state
            if 'collections' not in self.state:
                self.state['collections'] = {}
            if collection_name not in self.state['collections']:
                self.state['collections'][collection_name] = {}
            
            self.state['collections'][collection_name][f'etag_{variant}'] = etag
            self.state['collections'][collection_name][f'law_count_{variant}'] = len(all_laws)
            self.state['collections'][collection_name][f'last_check'] = datetime.now().isoformat()
            
            return new_or_changed if new_or_changed else None
            
        except Exception as e:
            logger.error(f"  ✗ Error: {e}")
            return None
    
    def run_incremental_update(self):
        """
        Incremental update:
        1. Check each collection for changes
        2. For changed collections: download and extract new/changed laws
        3. Write to STAGING JSONL
        4. Mark as ready for verification
        """
        logger.info("\n" + "=" * 80)
        logger.info("INCREMENTAL UPDATE: Only new/changed laws")
        logger.info("=" * 80)
        
        # Initialize staging from production (so we keep old laws)
        logger.info("\nInitializing staging from production...")
        vigente_staging = self._read_jsonl_into_dict(self.jsonl_vigente_prod)
        abrogate_staging = self._read_jsonl_into_dict(self.jsonl_abrogate_prod)
        
        update_count = 0
        
        # Check vigente collections
        logger.info(f"\nChecking {len(VIGENTE_COLLECTIONS)} vigente collections...")
        for collection_name in VIGENTE_COLLECTIONS:
            new_laws = self.download_collection_incremental(collection_name, variant='V')
            if new_laws:
                update_count += 1
                for law in new_laws:
                    urn = law.get('urn')
                    vigente_staging[urn] = law
                logger.info(f"  Added to staging: {len(new_laws)} laws")
        
        # Check abrogate
        logger.info("\nChecking abrogate collection...")
        new_abrogate = self.download_collection_incremental(ABROGATE_COLLECTION, variant='O')
        if new_abrogate:
            update_count += 1
            for law in new_abrogate:
                urn = law.get('urn')
                abrogate_staging[urn] = law
            logger.info(f"  Added to staging: {len(new_abrogate)} laws")
        
        # Write staging
        logger.info("\nWriting staging JSONL files...")
        self._write_jsonl_from_dict(self.jsonl_vigente_staging, vigente_staging)
        self._write_jsonl_from_dict(self.jsonl_abrogate_staging, abrogate_staging)
        
        self.state['staging_stats']['vigente_count'] = len(vigente_staging)
        self.state['staging_stats']['abrogate_count'] = len(abrogate_staging)
        self.state['last_incremental_sync'] = datetime.now().isoformat()
        self._save_state()
        
        logger.info("\n" + "=" * 80)
        logger.info(f"✓ INCREMENTAL UPDATE COMPLETE")
        logger.info(f"  Collections with changes: {update_count}")
        logger.info(f"  Staging vigente: {len(vigente_staging):,} laws")
        logger.info(f"  Staging abrogate: {len(abrogate_staging):,} laws")
        logger.info("=" * 80)
        logger.info("\nNext: Review staging, then promote to production")
        logger.info("  python enhanced_pipeline.py --mode promote")
    
    def verify_staging(self):
        """
        Verify staging before promoting to production.
        Checks:
        - No negative changes (fewer laws than before)
        - Data integrity
        - Quality metrics
        """
        logger.info("\n" + "=" * 80)
        logger.info("STAGING VERIFICATION")
        logger.info("=" * 80)
        
        prod_vigente = self._read_jsonl_into_dict(self.jsonl_vigente_prod)
        staging_vigente = self._read_jsonl_into_dict(self.jsonl_vigente_staging)
        
        prod_abrogate = self._read_jsonl_into_dict(self.jsonl_abrogate_prod)
        staging_abrogate = self._read_jsonl_into_dict(self.jsonl_abrogate_staging)
        
        print("\n📊 VIGENTE DATASET")
        print(f"  Production: {len(prod_vigente):,} laws")
        print(f"  Staging:    {len(staging_vigente):,} laws")
        
        vigente_diff = len(staging_vigente) - len(prod_vigente)
        if vigente_diff > 0:
            print(f"  ✓ Change: +{vigente_diff} laws (new/updates)")
        elif vigente_diff < 0:
            print(f"  ⚠️  WARNING: -{abs(vigente_diff)} laws removed!")
        else:
            print(f"  - No changes")
        
        print("\n🗑️  ABROGATE DATASET")
        print(f"  Production: {len(prod_abrogate):,} laws")
        print(f"  Staging:    {len(staging_abrogate):,} laws")
        
        abrogate_diff = len(staging_abrogate) - len(prod_abrogate)
        if abrogate_diff > 0:
            print(f"  ✓ Change: +{abrogate_diff} laws (new/updates)")
        elif abrogate_diff < 0:
            print(f"  ⚠️  WARNING: -{abs(abrogate_diff)} laws removed!")
        else:
            print(f"  - No changes")
        
        # Check for data quality
        print("\n🔍 DATA QUALITY CHECKS")
        
        # Check for NULLs
        null_urns = sum(1 for law in staging_vigente.values() if not law.get('urn'))
        null_titles = sum(1 for law in staging_vigente.values() if not law.get('title'))
        print(f"  Vigente with NULL urn: {null_urns}")
        print(f"  Vigente with NULL title: {null_titles}")
        
        if null_urns > 0 or null_titles > 0:
            print("  ⚠️  WARNING: Data quality issues detected!")
        else:
            print("  ✓ No NULL values")
        
        print("\n" + "=" * 80)
        print("Review complete. Ready to promote?")
        print("  python enhanced_pipeline.py --mode promote")
        print("=" * 80)
    
    def promote_staging_to_production(self):
        """
        Move staging to production:
        1. Backup current production
        2. Move staging files to production
        3. Update production stats
        4. Keep users' session uninterrupted
        """
        logger.info("\n" + "=" * 80)
        logger.info("PROMOTING STAGING TO PRODUCTION")
        logger.info("=" * 80)
        
        # Backup current production
        logger.info("\n1. Backing up current production...")
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir = self.archives_dir / f"backup_{timestamp}"
        backup_dir.mkdir(exist_ok=True)
        
        if self.jsonl_vigente_prod.exists():
            shutil.copy2(self.jsonl_vigente_prod, backup_dir / 'laws_vigente.jsonl')
            logger.info(f"   ✓ Backed up vigente")
        
        if self.jsonl_abrogate_prod.exists():
            shutil.copy2(self.jsonl_abrogate_prod, backup_dir / 'laws_abrogate.jsonl')
            logger.info(f"   ✓ Backed up abrogate")
        
        # Move staging to production
        logger.info("\n2. Moving staging to production...")
        if self.jsonl_vigente_staging.exists():
            shutil.move(
                str(self.jsonl_vigente_staging), 
                str(self.jsonl_vigente_prod)
            )
            logger.info(f"   ✓ Moved vigente")
        
        if self.jsonl_abrogate_staging.exists():
            shutil.move(
                str(self.jsonl_abrogate_staging),
                str(self.jsonl_abrogate_prod)
            )
            logger.info(f"   ✓ Moved abrogate")
        
        # Update production stats
        logger.info("\n3. Updating production stats...")
        prod_vigente = self._read_jsonl_into_dict(self.jsonl_vigente_prod)
        prod_abrogate = self._read_jsonl_into_dict(self.jsonl_abrogate_prod)
        
        self.state['production_stats']['vigente_count'] = len(prod_vigente)
        self.state['production_stats']['abrogate_count'] = len(prod_abrogate)
        self.state['last_full_sync'] = datetime.now().isoformat()
        self._save_state()
        
        logger.info(f"   Vigente: {len(prod_vigente):,} laws")
        logger.info(f"   Abrogate: {len(prod_abrogate):,} laws")
        
        # Notify about backup
        logger.info(f"\n✓ PROMOTION COMPLETE")
        logger.info(f"  Backup: {backup_dir}")
        logger.info(f"  Production: {self.jsonl_vigente_prod}")
        logger.info(f"  Status: LIVE for users")
        logger.info("=" * 80)
    
    def print_status(self):
        """Show detailed status."""
        print("\n" + "=" * 80)
        print("ENHANCED PIPELINE STATUS")
        print("=" * 80)
        
        print(f"\nMode: {self.state.get('mode', 'unknown')}")
        print(f"Last Full Sync: {self.state.get('last_full_sync', 'never')}")
        print(f"Last Incremental Sync: {self.state.get('last_incremental_sync', 'never')}")
        
        print("\n📊 PRODUCTION (Live for Users)")
        vigente_count = self.state['production_stats'].get('vigente_count', 0)
        abrogate_count = self.state['production_stats'].get('abrogate_count', 0)
        print(f"  Vigente: {vigente_count:,} laws")
        print(f"  Abrogate: {abrogate_count:,} laws")
        print(f"  Total: {vigente_count + abrogate_count:,} laws")
        
        if self.jsonl_vigente_prod.exists():
            size_mb = self.jsonl_vigente_prod.stat().st_size / 1e6
            print(f"  Vigente file: {size_mb:.1f} MB")
        
        if self.jsonl_abrogate_prod.exists():
            size_mb = self.jsonl_abrogate_prod.stat().st_size / 1e6
            print(f"  Abrogate file: {size_mb:.1f} MB")
        
        print("\n🚀 STAGING (Ready for Verification)")
        staging_vigente_count = self.state['staging_stats'].get('vigente_count', 0)
        staging_abrogate_count = self.state['staging_stats'].get('abrogate_count', 0)
        
        if staging_vigente_count > 0 or staging_abrogate_count > 0:
            print(f"  Vigente: {staging_vigente_count:,} laws")
            print(f"  Abrogate: {staging_abrogate_count:,} laws")
            print(f"  ✓ Ready to verify and promote")
        else:
            print(f"  (empty - run incremental update first)")
        
        # Collection status
        print("\n🔄 COLLECTION STATUS")
        collections = self.state.get('collections', {})
        if collections:
            for cname in list(collections.keys())[:5]:  # Show first 5
                cstate = collections[cname]
                last_check = cstate.get('last_check', 'unknown')
                print(f"  {cname}: {last_check}")
            if len(collections) > 5:
                print(f"  ... and {len(collections) - 5} more")
        else:
            print("  (none checked yet)")
        
        print("\n" + "=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description='Enhanced Pipeline: Incremental Updates + Staging'
    )
    parser.add_argument(
        '--mode',
        choices=['incremental', 'verify', 'promote'],
        default='incremental',
        help='Pipeline mode'
    )
    parser.add_argument(
        '--status',
        action='store_true',
        help='Show status and exit'
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=Path('data'),
        help='Data directory'
    )
    
    args = parser.parse_args()
    
    pipeline = EnhancedPipeline(data_dir=args.data_dir)
    
    if args.status:
        pipeline.print_status()
        return
    
    if args.mode == 'incremental':
        pipeline.run_incremental_update()
    elif args.mode == 'verify':
        pipeline.verify_staging()
    elif args.mode == 'promote':
        pipeline.promote_staging_to_production()


if __name__ == '__main__':
    main()
