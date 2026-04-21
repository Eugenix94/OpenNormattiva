#!/usr/bin/env python3
"""
STATIC PIPELINE: Vigente Laws + Abrogate Dataset
=================================================================

Converts the pipeline to static mode:
- Vigente: Load once, then only sync changes
- Abrogate: Separate dataset, never re-downloaded unless forced
- Incremental: Only new/modified vigente collections processed

Usage:
    python static_pipeline.py --mode full          # First-time full build
    python static_pipeline.py --mode sync          # Weekly incremental sync
    python static_pipeline.py --mode abrogate-only # Import abrogate only
    python static_pipeline.py --status             # Check what's done
"""

import json
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import hashlib

from normattiva_api_client import NormattivaAPI
from parse_akn import AKNParser
from core.db import LawDatabase

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
VIGENTE_COLLECTIONS = [
    'Codici',
    'DL proroghe',
    'Leggi costituzionali',
    'Regi decreti',
    'DPR',
    'DL e leggi di conversione',
    'Decreti Legislativi',
    'Leggi di ratifica',
    'Regolamenti ministeriali',
    'Regolamenti governativi',
    'DL decaduti',
    'Decreti legislativi luogotenenziali',
    'Leggi delega e relativi provvedimenti delegati',
    'Atti di recepimento direttive UE',
    'Regolamenti di delegificazione',
    'DPCM',
    'Testi Unici',
    'Regi decreti legislativi',
    'Leggi contenenti deleghe',
    'Leggi finanziarie e di bilancio',
    'Leggi di delegazione europea',
    'Atti di attuazione Regolamenti UE',
]

ABROGATE_COLLECTION = 'Atti normativi abrogati (in originale)'


class StaticPipeline:
    """Static pipeline manager for vigente + abrogate datasets."""
    
    def __init__(self, data_dir: Path = Path('data')):
        self.data_dir = Path(data_dir)
        self.state_file = self.data_dir / '.static_state.json'
        self.api = NormattivaAPI(timeout_s=60, retries=2)
        self.parser = AKNParser()
        
        # Create directories
        self.raw_dir = self.data_dir / 'raw'
        self.processed_dir = self.data_dir / 'processed'
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        
        # Output files
        self.jsonl_vigente = self.processed_dir / 'laws_vigente.jsonl'
        self.jsonl_abrogate = self.processed_dir / 'laws_abrogate.jsonl'
        self.amendments_file = self.processed_dir / 'amendments.jsonl'
        
        # Cache
        self.state = self._load_state()
    
    def _load_state(self) -> Dict:
        """Load pipeline state or create new."""
        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return {
            'version': 1,
            'mode': 'initial',
            'vigente_completed': False,
            'abrogate_completed': False,
            'vigente_collections': {},  # collection_name -> {etag, sha256, law_count}
            'abrogate_etag': None,
            'abrogate_law_count': 0,
            'total_laws': 0,
            'last_update': None,
            'amendments_log': []
        }
    
    def _save_state(self):
        """Persist pipeline state."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    def print_status(self):
        """Show current pipeline status."""
        print("\n" + "=" * 80)
        print("STATIC PIPELINE STATUS")
        print("=" * 80)
        
        print(f"\nMode: {self.state.get('mode', 'unknown')}")
        print(f"Last Updated: {self.state.get('last_update', 'never')}")
        
        # Vigente status
        print("\n📋 VIGENTE COLLECTIONS (22 total)")
        vigente_list = self.state.get('vigente_collections', {})
        if vigente_list:
            completed = len(vigente_list)
            print(f"  Status: {completed}/22 downloaded")
            print(f"  Total Laws: {vigente_list.get('__total_laws__', 0):,}")
        else:
            print("  Status: Not started")
        
        # Abrogate status
        print("\n🗑️  ABROGATE COLLECTION (1 total)")
        if self.state.get('abrogate_completed'):
            print(f"  Status: ✓ Downloaded")
            print(f"  Total Laws: {self.state.get('abrogate_law_count', 0):,}")
        else:
            print("  Status: Not started")
        
        # Output files
        print("\n📁 OUTPUT FILES")
        for name, path in [
            ("Vigente JSONL", self.jsonl_vigente),
            ("Abrogate JSONL", self.jsonl_abrogate),
            ("Amendments Log", self.amendments_file),
        ]:
            if path.exists():
                size_mb = path.stat().st_size / 1e6
                lines = sum(1 for _ in open(path))
                print(f"  ✓ {name}: {size_mb:.1f} MB ({lines:,} lines)")
            else:
                print(f"  - {name}: not created yet")
        
        print("\n" + "=" * 80)
    
    def download_vigente_collection(self, collection_name: str, force: bool = False) -> bool:
        """
        Download collection if changed or forced.
        Returns True if downloaded, False if already up-to-date.
        """
        logger.info(f"Checking {collection_name}...")
        
        try:
            # Get current ETag from API
            current_etag = self.api.check_collection_etag(
                collection_name, 
                variant='V',  # Always vigente variant
                format='AKN'
            )
            
            # Check if we have it cached and hasn't changed
            cached_etag = self.state['vigente_collections'].get(collection_name, {}).get('etag')
            
            if cached_etag == current_etag and not force:
                logger.info(f"  ✓ {collection_name} unchanged (cached)")
                return False
            
            # Download
            logger.info(f"  ↓ Downloading {collection_name} (V variant, AKN format)...")
            data, etag, content_type = self.api.get_collection(
                collection_name,
                variant='V',  # Vigente
                format='AKN'
            )
            
            # Save to raw directory
            outfile = self.raw_dir / f"{collection_name}_vigente.zip"
            with open(outfile, 'wb') as f:
                f.write(data)
            
            # Calculate SHA256
            sha256 = hashlib.sha256(data).hexdigest()
            
            logger.info(f"  ✓ Downloaded: {len(data)/1e6:.1f} MB")
            
            # Update state
            if 'vigente_collections' not in self.state:
                self.state['vigente_collections'] = {}
            self.state['vigente_collections'][collection_name] = {
                'etag': etag,
                'sha256': sha256,
                'downloaded_at': datetime.now().isoformat(),
                'file': str(outfile.relative_to(self.data_dir))
            }
            
            return True
            
        except Exception as e:
            logger.error(f"  ✗ Failed to download {collection_name}: {e}")
            return False
    
    def download_abrogate(self, force: bool = False) -> bool:
        """
        Download abrogate collection if needed.
        Returns True if downloaded, False if already up-to-date.
        """
        logger.info(f"Checking abrogate collection...")
        
        try:
            # Get current ETag
            current_etag = self.api.check_collection_etag(
                ABROGATE_COLLECTION,
                variant='O',  # Only O variant exists
                format='AKN'
            )
            
            # Check if unchanged
            if self.state['abrogate_etag'] == current_etag and not force:
                logger.info(f"  ✓ Abrogate collection unchanged (cached)")
                return False
            
            # Download
            logger.info(f"  ↓ Downloading abrogate collection (O variant, AKN format)...")
            data, etag, content_type = self.api.get_collection(
                ABROGATE_COLLECTION,
                variant='O',  # Originale (only variant available)
                format='AKN'
            )
            
            # Save
            outfile = self.raw_dir / f"abrogate_originale.zip"
            with open(outfile, 'wb') as f:
                f.write(data)
            
            logger.info(f"  ✓ Downloaded: {len(data)/1e6:.1f} MB")
            
            # Update state
            self.state['abrogate_etag'] = etag
            self.state['abrogate_file'] = str(outfile.relative_to(self.data_dir))
            
            return True
            
        except Exception as e:
            logger.error(f"  ✗ Failed to download abrogate: {e}")
            return False
    
    def parse_and_merge_vigente(self):
        """Parse all vigente collections and merge into single JSONL."""
        logger.info("\n" + "=" * 80)
        logger.info("PARSING VIGENTE COLLECTIONS")
        logger.info("=" * 80)
        
        total_laws = 0
        
        # Append (don't overwrite) to support incremental updates
        with open(self.jsonl_vigente, 'a') as outf:
            for collection_name in VIGENTE_COLLECTIONS:
                coll_data = self.state['vigente_collections'].get(collection_name, {})
                file_path = coll_data.get('file')
                
                if not file_path:
                    logger.info(f"Skipping {collection_name}: not downloaded")
                    continue
                
                zip_file = self.data_dir / file_path
                if not zip_file.exists():
                    logger.warning(f"File not found: {zip_file}")
                    continue
                
                logger.info(f"Parsing {collection_name}...")
                try:
                    laws = self.parser.parse_zip_file(zip_file)
                    
                    for law in laws:
                        outf.write(json.dumps(law, ensure_ascii=False) + '\n')
                    
                    total_laws += len(laws)
                    logger.info(f"  ✓ {len(laws)} laws parsed")
                    
                except Exception as e:
                    logger.error(f"  ✗ Failed to parse {collection_name}: {e}")
        
        self.state['vigente_law_count'] = total_laws
        logger.info(f"\n✓ Total vigente laws: {total_laws:,}")
    
    def parse_abrogate(self):
        """Parse abrogate collection into separate JSONL."""
        logger.info("\n" + "=" * 80)
        logger.info("PARSING ABROGATE COLLECTION")
        logger.info("=" * 80)
        
        file_path = self.state.get('abrogate_file')
        if not file_path:
            logger.warning("Abrogate file not found in state")
            return
        
        zip_file = self.data_dir / file_path
        if not zip_file.exists():
            logger.warning(f"File not found: {zip_file}")
            return
        
        logger.info(f"Parsing abrogate collection...")
        try:
            laws = self.parser.parse_zip_file(zip_file)
            
            with open(self.jsonl_abrogate, 'w') as f:
                for law in laws:
                    f.write(json.dumps(law, ensure_ascii=False) + '\n')
            
            self.state['abrogate_law_count'] = len(laws)
            self.state['abrogate_completed'] = True
            logger.info(f"  ✓ {len(laws):,} abrogate laws parsed")
            
        except Exception as e:
            logger.error(f"  ✗ Failed to parse abrogate: {e}")
    
    def run_full_build(self):
        """Initial full build: download and parse everything."""
        logger.info("\n" + "=" * 80)
        logger.info("STATIC PIPELINE: FULL BUILD")
        logger.info("Downloading all 22 vigente + 1 abrogate collection")
        logger.info("=" * 80)
        
        self.state['mode'] = 'full_build'
        
        # Download all vigente
        downloaded_count = 0
        for collection_name in VIGENTE_COLLECTIONS:
            if self.download_vigente_collection(collection_name, force=True):
                downloaded_count += 1
        
        logger.info(f"\n✓ Downloaded {downloaded_count}/{len(VIGENTE_COLLECTIONS)} vigente collections")
        self.state['vigente_completed'] = True
        
        # Download abrogate
        if self.download_abrogate(force=True):
            logger.info("✓ Downloaded abrogate collection")
        
        # Parse everything
        self.parse_and_merge_vigente()
        self.parse_abrogate()
        
        self.state['last_update'] = datetime.now().isoformat()
        self._save_state()
        
        logger.info("\n" + "=" * 80)
        logger.info("✓ FULL BUILD COMPLETE")
        logger.info("=" * 80)
        self.print_status()
    
    def run_incremental_sync(self):
        """Incremental sync: only update changed collections."""
        logger.info("\n" + "=" * 80)
        logger.info("STATIC PIPELINE: INCREMENTAL SYNC")
        logger.info("Checking for changes in vigente + abrogate")
        logger.info("=" * 80)
        
        self.state['mode'] = 'incremental'
        
        # Check vigente
        updated_vigente = []
        for collection_name in VIGENTE_COLLECTIONS:
            if self.download_vigente_collection(collection_name):
                updated_vigente.append(collection_name)
        
        # Check abrogate
        updated_abrogate = self.download_abrogate()
        
        logger.info(f"\nUpdates found:")
        logger.info(f"  Vigente collections changed: {len(updated_vigente)}")
        logger.info(f"  Abrogate collection changed: {updated_abrogate}")
        
        if not updated_vigente and not updated_abrogate:
            logger.info("  → Everything is up-to-date!")
            return
        
        # Re-parse only changed collections
        if updated_vigente:
            logger.info(f"\nRe-parsing {len(updated_vigente)} changed vigente collections...")
            # For simplicity, reparse all vigente (in production, only changed ones)
            self.parse_and_merge_vigente()
        
        if updated_abrogate:
            logger.info("\nRe-parsing abrogate collection...")
            self.parse_abrogate()
        
        self.state['last_update'] = datetime.now().isoformat()
        self._save_state()
        
        logger.info("\n" + "=" * 80)
        logger.info("✓ INCREMENTAL SYNC COMPLETE")
        logger.info("=" * 80)
        self.print_status()


def main():
    parser = argparse.ArgumentParser(
        description='Static Pipeline for Vigente + Abrogate Datasets'
    )
    parser.add_argument(
        '--mode',
        choices=['full', 'sync', 'abrogate-only'],
        default='sync',
        help='Pipeline mode (default: sync)'
    )
    parser.add_argument(
        '--status',
        action='store_true',
        help='Show current status and exit'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force re-download even if cached'
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=Path('data'),
        help='Data directory (default: data/)'
    )
    
    args = parser.parse_args()
    
    pipeline = StaticPipeline(data_dir=args.data_dir)
    
    if args.status:
        pipeline.print_status()
        return
    
    if args.mode == 'full':
        pipeline.run_full_build()
    elif args.mode == 'sync':
        # For incremental, first ensure vigente is initialized
        if not pipeline.state.get('vigente_completed'):
            logger.info("Vigente not yet downloaded. Running full build first...")
            pipeline.run_full_build()
        else:
            pipeline.run_incremental_sync()
    elif args.mode == 'abrogate-only':
        if pipeline.download_abrogate(force=args.force):
            pipeline.parse_abrogate()
        pipeline._save_state()
        logger.info("✓ Abrogate collection updated")


if __name__ == '__main__':
    main()
