#!/usr/bin/env python3
"""
Automatic Data Building Pipeline - Complete Vigente Dataset

Orchestrates the entire data lifecycle:
1. Resume/complete downloading all 22 vigente collections
2. Parse and merge JSONL incrementally
3. Build SQLite database with FTS5
4. Generate citation indexes
5. Setup amendment tracking
6. Create search indexes
7. Generate reports

Usage:
    python scripts/auto_build_data.py --full      # Start fresh
    python scripts/auto_build_data.py --resume    # Continue from last checkpoint
    python scripts/auto_build_data.py --status    # Check progress
"""

import sys
import json
import sqlite3
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Set, Optional
from datetime import datetime
import hashlib

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from normattiva_api_client import NormattivaAPI
from parse_akn import AKNParser
from core.db import LawDatabase

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class AutoDataBuilder:
    """Automatic data building with checkpoint recovery."""
    
    # All 22 vigente collections as per official Normattiva
    ALL_COLLECTIONS = [
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
    
    def __init__(self, data_dir: Path = Path('data'), checkpoint_file: Path = Path('data/.build_checkpoint.json')):
        self.data_dir = Path(data_dir)
        self.checkpoint_file = checkpoint_file
        self.raw_dir = self.data_dir / 'raw'
        self.processed_dir = self.data_dir / 'processed'
        self.indexes_dir = self.data_dir / 'indexes'
        self.db_path = self.data_dir / 'laws.db'
        
        # Create directories
        for d in [self.raw_dir, self.processed_dir, self.indexes_dir]:
            d.mkdir(parents=True, exist_ok=True)
        
        self.api = NormattivaAPI()
        self.parser = AKNParser()
        self.checkpoint = self._load_checkpoint()
    
    def _load_checkpoint(self) -> Dict:
        """Load checkpoint or create new."""
        if self.checkpoint_file.exists():
            with open(self.checkpoint_file, 'r') as f:
                return json.load(f)
        return {
            'stage': 'init',
            'downloaded': [],
            'parsed': [],
            'imported': [],
            'start_time': datetime.now().isoformat(),
            'stats': {}
        }
    
    def _save_checkpoint(self):
        """Save checkpoint."""
        self.checkpoint['last_update'] = datetime.now().isoformat()
        with open(self.checkpoint_file, 'w') as f:
            json.dump(self.checkpoint, f, indent=2)
        logger.info(f"✓ Checkpoint saved")
    
    def get_status(self) -> Dict:
        """Get current build status."""
        status = {
            'stage': self.checkpoint.get('stage'),
            'progress': {
                'downloaded': len(self.checkpoint.get('downloaded', [])),
                'parsed': len(self.checkpoint.get('parsed', [])),
                'imported': len(self.checkpoint.get('imported', [])),
            },
            'total_collections': len(self.ALL_COLLECTIONS),
            'remaining': len(self.ALL_COLLECTIONS) - len(self.checkpoint.get('downloaded', [])),
        }
        
        # Get database stats
        if self.db_path.exists():
            try:
                db = LawDatabase(self.db_path)
                count = db.conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
                status['laws_in_db'] = count
                db.close()
            except:
                pass
        
        # Get JSONL stats
        jsonl_file = self.processed_dir / 'laws_vigente.jsonl'
        if jsonl_file.exists():
            status['jsonl_lines'] = sum(1 for _ in open(jsonl_file))
            status['jsonl_size_mb'] = jsonl_file.stat().st_size / 1e6
        
        return status
    
    def download_all_collections(self, force: bool = False) -> int:
        """Download all pending collections."""
        logger.info(f"\n{'='*60}")
        logger.info(f"STAGE 1: Download all 22 vigente collections")
        logger.info(f"{'='*60}")
        
        downloaded = self.checkpoint.get('downloaded', [])
        pending = [c for c in self.ALL_COLLECTIONS if c not in downloaded or force]
        
        logger.info(f"Already downloaded: {len(downloaded)}/22")
        logger.info(f"Pending: {len(pending)}/22")
        
        if not pending and not force:
            logger.info("✓ All collections already downloaded")
            return len(downloaded)
        
        for i, collection in enumerate(pending, 1):
            try:
                logger.info(f"\n[{i}/{len(pending)}] Downloading {collection}...")
                
                zip_file = self._download_collection(collection)
                
                if zip_file:
                    self.checkpoint['downloaded'].append(collection)
                    self._save_checkpoint()
                    logger.info(f"✓ {collection} → {zip_file.name}")
            
            except Exception as e:
                logger.error(f"✗ Failed to download {collection}: {e}")
                logger.error(f"  Skipping {collection}, will retry next run")
                continue
        
        logger.info(f"\n✓ Downloaded {len(self.checkpoint['downloaded'])}/22 collections")
        self.checkpoint['stage'] = 'downloading'
        self._save_checkpoint()
        return len(self.checkpoint['downloaded'])
    
    def _download_collection(self, collection_name: str) -> Optional[Path]:
        """Download single collection."""
        try:
            data, etag, content_type = self.api.get_collection(
                collection_name,
                variant='V',
                format='AKN'
            )
            
            zip_file = self.raw_dir / f"{collection_name}_vigente.zip"
            with open(zip_file, 'wb') as f:
                f.write(data)
            
            logger.info(f"   Size: {len(data)/1e6:.1f} MB, Hash: {etag[:16]}")
            return zip_file
        
        except Exception as e:
            logger.error(f"   Error: {e}")
            return None
    
    def parse_all_zipfiles(self, force: bool = False) -> int:
        """Parse all ZIP files to incremental JSONL."""
        logger.info(f"\n{'='*60}")
        logger.info(f"STAGE 2: Parse all ZIP files to JSONL")
        logger.info(f"{'='*60}")
        
        parsed = set(self.checkpoint.get('parsed', []))
        zip_files = sorted(self.raw_dir.glob('*_vigente.zip'))
        pending = [z for z in zip_files if z.name not in parsed or force]
        
        logger.info(f"Already parsed: {len(parsed)}/22")
        logger.info(f"Pending: {len(pending)}/22")
        
        jsonl_file = self.processed_dir / 'laws_vigente.jsonl'
        total_laws = 0
        total_errors = 0
        
        for i, zip_file in enumerate(pending, 1):
            try:
                logger.info(f"\n[{i}/{len(pending)}] Parsing {zip_file.name}...")
                
                laws = self.parser.parse_zip_file(zip_file)
                logger.info(f"   Found {len(laws)} laws")
                
                # Append to JSONL (incremental)
                with open(jsonl_file, 'a', encoding='utf-8') as f:
                    for law in laws:
                        law = self.parser.enrich_with_metadata(law)
                        f.write(json.dumps(law, ensure_ascii=False) + '\n')
                        total_laws += 1
                
                parsed.add(zip_file.name)
                self.checkpoint['parsed'] = list(parsed)
                self._save_checkpoint()
                logger.info(f"✓ Appended to JSONL")
            
            except Exception as e:
                logger.error(f"✗ Parse error: {e}")
                total_errors += 1
                continue
        
        logger.info(f"\n✓ Parsed {len(parsed)}/22 collections → {total_laws} laws")
        self.checkpoint['stage'] = 'parsing'
        self.checkpoint['stats']['total_laws'] = total_laws
        self.checkpoint['stats']['parse_errors'] = total_errors
        self._save_checkpoint()
        return total_laws
    
    def build_database(self, force: bool = False) -> int:
        """Load JSONL to SQLite database."""
        logger.info(f"\n{'='*60}")
        logger.info(f"STAGE 3: Build SQLite database with FTS5")
        logger.info(f"{'='*60}")
        
        jsonl_file = self.processed_dir / 'laws_vigente.jsonl'
        
        if not jsonl_file.exists():
            logger.error(f"✗ {jsonl_file} not found. Run parse first.")
            return 0
        
        # Initialize database
        db = LawDatabase(self.db_path)
        logger.info(f"Database: {self.db_path}")
        
        # Count existing
        existing = db.conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
        logger.info(f"Existing laws in DB: {existing}")
        
        if existing > 0 and not force:
            logger.info("✓ Database already populated")
            db.close()
            return existing
        
        if force and existing > 0:
            logger.info("Force mode: clearing database...")
            db.conn.execute('DELETE FROM laws')
            db.conn.execute('DELETE FROM citations')
            db.conn.commit()
        
        # Load from JSONL
        logger.info(f"Loading from {jsonl_file.name}...")
        imported = 0
        skipped = 0
        errors = 0
        
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                
                try:
                    law = json.loads(line)
                    
                    # Insert law
                    db.conn.execute('''
                        INSERT OR REPLACE INTO laws (
                            urn, title, type, date, year, text, 
                            text_length, article_count, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        law.get('urn'),
                        law.get('title'),
                        law.get('type'),
                        law.get('date'),
                        law.get('year'),
                        law.get('text'),
                        law.get('text_length', 0),
                        law.get('article_count', 0),
                        'in_force'
                    ))
                    
                    # Handle citations
                    citations = law.get('citations', [])
                    if citations:
                        for cited_urn in citations:
                            db.conn.execute('''
                                INSERT OR IGNORE INTO citations (from_urn, to_urn)
                                VALUES (?, ?)
                            ''', (law.get('urn'), cited_urn))
                    
                    imported += 1
                    
                    if imported % 5000 == 0:
                        db.conn.commit()
                        logger.info(f"   Imported {imported} laws...")
                
                except json.JSONDecodeError as e:
                    logger.warning(f"   Line {line_no}: JSON decode error")
                    errors += 1
                except Exception as e:
                    logger.warning(f"   Line {line_no}: {e}")
                    errors += 1
        
        db.conn.commit()
        logger.info(f"\n✓ Imported {imported} laws")
        if errors > 0:
            logger.warning(f"⚠ Errors: {errors}, Skipped: {skipped}")
        
        db.close()
        
        self.checkpoint['stage'] = 'database'
        self.checkpoint['imported'] = [self.db_path.name]
        self.checkpoint['stats']['laws_imported'] = imported
        self._save_checkpoint()
        return imported
    
    def build_indexes(self) -> Dict:
        """Build citation and search indexes."""
        logger.info(f"\n{'='*60}")
        logger.info(f"STAGE 4: Build citation and search indexes")
        logger.info(f"{'='*60}")
        
        db = LawDatabase(self.db_path)
        
        # Citation index
        logger.info("\nBuilding citation index...")
        citations_by_law = {}
        incoming_citations = {}
        
        cursor = db.conn.execute('SELECT from_urn, to_urn FROM citations')
        for from_urn, to_urn in cursor:
            if from_urn not in citations_by_law:
                citations_by_law[from_urn] = []
            citations_by_law[from_urn].append(to_urn)
            
            if to_urn not in incoming_citations:
                incoming_citations[to_urn] = []
            incoming_citations[to_urn].append(from_urn)
        
        logger.info(f"   {len(citations_by_law)} laws with outgoing citations")
        logger.info(f"   {len(incoming_citations)} laws with incoming citations")
        
        citation_index = {
            'generated': datetime.now().isoformat(),
            'total_laws_with_citations': len(citations_by_law) + len(incoming_citations),
            'outgoing_citations': citations_by_law,
            'incoming_citations': incoming_citations,
        }
        
        index_file = self.indexes_dir / 'citations_index.json'
        with open(index_file, 'w') as f:
            json.dump(citation_index, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Citation index → {index_file}")
        
        # Search index (top terms)
        logger.info("\nBuilding search index...")
        cursor = db.conn.execute('SELECT title FROM laws LIMIT 10000')
        all_words: Dict[str, int] = {}
        
        for (title,) in cursor:
            if not title:
                continue
            words = title.lower().split()
            for word in words:
                if len(word) > 3 and word not in all_words:
                    all_words[word] = 0
                if word in all_words:
                    all_words[word] += 1
        
        top_terms = sorted(all_words.items(), key=lambda x: x[1], reverse=True)[:500]
        search_index = {
            'generated': datetime.now().isoformat(),
            'top_terms': dict(top_terms),
        }
        
        index_file = self.indexes_dir / 'search_index.json'
        with open(index_file, 'w') as f:
            json.dump(search_index, f, indent=2, ensure_ascii=False)
        logger.info(f"✓ Search index → {index_file}")
        
        db.close()
        
        self.checkpoint['stage'] = 'indexes'
        self._save_checkpoint()
        
        return {
            'citations': len(citations_by_law),
            'search_terms': len(top_terms),
        }
    
    def generate_report(self) -> Dict:
        """Generate final build report."""
        logger.info(f"\n{'='*60}")
        logger.info(f"STAGE 5: Generate build report")
        logger.info(f"{'='*60}")
        
        db = LawDatabase(self.db_path)
        
        # Statistics
        total_laws = db.conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
        total_citations = db.conn.execute('SELECT COUNT(*) FROM citations').fetchone()[0]
        
        # By type
        by_type = db.conn.execute('''
            SELECT type, COUNT(*) as count FROM laws 
            GROUP BY type ORDER BY count DESC
        ''').fetchall()
        
        # By year
        by_year = db.conn.execute('''
            SELECT year, COUNT(*) as count FROM laws 
            WHERE year IS NOT NULL
            GROUP BY year ORDER BY year DESC LIMIT 10
        ''').fetchall()
        
        report = {
            'generated': datetime.now().isoformat(),
            'status': 'complete',
            'summary': {
                'total_laws': total_laws,
                'total_citations': total_citations,
            },
            'by_type': dict((type_, count) for type_, count in by_type),
            'recent_years': dict((year, count) for year, count in by_year),
            'database_file': str(self.db_path),
            'coverage': f"{total_laws}/162391 laws",
        }
        
        report_file = self.indexes_dir / 'build_report.json'
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        logger.info(f"\n📊 BUILD REPORT:")
        logger.info(f"   Total laws: {total_laws:,}")
        logger.info(f"   Total citations: {total_citations:,}")
        logger.info(f"   Law types: {len(report['by_type'])}")
        logger.info(f"   Coverage: {report['coverage']}")
        logger.info(f"   Report → {report_file}")
        
        db.close()
        
        self.checkpoint['stage'] = 'complete'
        self.checkpoint['stats'].update(report['summary'])
        self._save_checkpoint()
        
        return report
    
    def run_full_pipeline(self, force: bool = False):
        """Run all stages."""
        logger.info(f"\n🚀 Starting automatic data build pipeline")
        logger.info(f"Start time: {datetime.now().isoformat()}")
        
        try:
            # Stage 1: Download
            dlcount = self.download_all_collections(force=force)
            if dlcount < 22:
                logger.warning(f"⚠ Only {dlcount}/22 collections downloaded. Retrying is needed.")
                return
            
            # Stage 2: Parse
            laws_count = self.parse_all_zipfiles(force=force)
            if laws_count == 0:
                logger.error("✗ No laws parsed")
                return
            
            # Stage 3: Database
            imported = self.build_database(force=force)
            if imported == 0:
                logger.error("✗ No laws imported to database")
                return
            
            # Stage 4: Indexes
            self.build_indexes()
            
            # Stage 5: Report
            report = self.generate_report()
            
            logger.info(f"\n✅ PIPELINE COMPLETE")
            logger.info(f"End time: {datetime.now().isoformat()}")
            
        except Exception as e:
            logger.error(f"\n❌ PIPELINE FAILED: {e}")
            raise


def main():
    parser = argparse.ArgumentParser(description='Automatic data build pipeline')
    parser.add_argument('--full', action='store_true', help='Start fresh (delete checkpoints)')
    parser.add_argument('--resume', action='store_true', help='Resume from checkpoint')
    parser.add_argument('--status', action='store_true', help='Show status only')
    parser.add_argument('--force', action='store_true', help='Force re-process everything')
    
    args = parser.parse_args()
    
    builder = AutoDataBuilder()
    
    if args.status:
        status = builder.get_status()
        print(f"\n{'='*60}")
        print(f"BUILD STATUS")
        print(f"{'='*60}")
        for key, value in status.items():
            if isinstance(value, dict):
                print(f"{key}:")
                for k, v in value.items():
                    print(f"  {k}: {v}")
            else:
                print(f"{key}: {value}")
        return 0
    
    if args.full:
        builder.checkpoint_file.unlink(missing_ok=True)
        builder.checkpoint = builder._load_checkpoint()
    
    builder.run_full_pipeline(force=args.force)
    return 0


if __name__ == '__main__':
    sys.exit(main())
