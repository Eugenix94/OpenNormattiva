#!/usr/bin/env python3
"""
Normattiva Pipeline: Download → Parse → Index → Upload

Orchestrates the complete data processing workflow:
1. Download collections from Normattiva API
2. Parse AKN XML → JSONL
3. Build citation indexes
4. Generate metrics
5. Push to HF Dataset

Usage:
    python pipeline.py --variants vigente originale --upload-hf
"""

import json
import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict
import zipfile
import re

from normattiva_api_client import NormattivaAPI
from parse_akn import AKNParser

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class NormattivaPipeline:
    """Complete data pipeline."""

    def __init__(self, data_dir: Path = Path('data')):
        self.data_dir = data_dir
        self.data_dir.mkdir(exist_ok=True)
        
        self.raw_dir = self.data_dir / 'raw'
        self.processed_dir = self.data_dir / 'processed'
        self.indexes_dir = self.data_dir / 'indexes'
        
        for d in [self.raw_dir, self.processed_dir, self.indexes_dir]:
            d.mkdir(exist_ok=True)
        
        self.api = NormattivaAPI()
        self.parser = AKNParser()

    def download_collection(self, collection_name: str, variant: str = 'O') -> Path:
        """Download a collection from API."""
        variant_map = {'originale': 'O', 'vigente': 'V', 'multivigente': 'M'}
        variant_code = variant_map.get(variant, variant)
        
        logger.info(f"Downloading {collection_name} ({variant})...")
        
        try:
            data, etag, content_type = self.api.get_collection(
                collection_name,
                variant=variant_code,
                format='AKN'
            )
            
            output_file = self.raw_dir / f"{collection_name}_{variant}.zip"
            with open(output_file, 'wb') as f:
                f.write(data)
            
            logger.info(f"✓ Downloaded {collection_name} → {output_file} ({len(data)/1e6:.1f} MB)")
            return output_file
        
        except Exception as e:
            logger.error(f"✗ Failed to download {collection_name}: {e}")
            raise

    def parse_collection(self, zip_file: Path) -> List[Dict]:
        """Parse ZIP to JSONL."""
        logger.info(f"Parsing {zip_file.name}...")
        laws = self.parser.parse_zip_file(zip_file)
        return laws

    def save_laws_jsonl(self, laws: List[Dict], variant: str) -> Path:
        """Save laws to JSONL."""
        output_file = self.processed_dir / f"laws_{variant}.jsonl"
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for law in laws:
                law = self.parser.enrich_with_metadata(law)
                f.write(json.dumps(law, ensure_ascii=False) + '\n')
        
        logger.info(f"✓ Saved {len(laws)} laws to {output_file}")
        return output_file

    def build_citation_index(self, jsonl_file: Path) -> Path:
        """Build citation index from JSONL."""
        logger.info(f"Building citation index from {jsonl_file.name}...")
        
        citations = {}
        total_citations = 0
        
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                
                law = json.loads(line)
                urn = law.get('urn')
                law_citations = law.get('citations', [])
                
                if law_citations:
                    citations[urn] = {
                        'law': law.get('title'),
                        'citations': law_citations,
                        'count': len(law_citations)
                    }
                    total_citations += len(law_citations)
        
        index_file = self.indexes_dir / f"{jsonl_file.stem}_citations.json"
        with open(index_file, 'w', encoding='utf-8') as f:
            json.dump({
                'generated': datetime.now().isoformat(),
                'total_laws_with_citations': len(citations),
                'total_citations': total_citations,
                'citations': citations
            }, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ Citation index: {len(citations)} laws, {total_citations} citations")
        return index_file

    def generate_metrics(self, jsonl_file: Path) -> Path:
        """Generate dataset metrics."""
        logger.info(f"Generating metrics from {jsonl_file.name}...")
        
        metrics = {
            'generated': datetime.now().isoformat(),
            'source_file': jsonl_file.name,
            'total_laws': 0,
            'by_type': {},
            'by_year': {},
            'text_stats': {'total_chars': 0, 'avg_chars': 0},
            'article_stats': {'total': 0, 'avg': 0}
        }
        
        laws_data = []
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                
                law = json.loads(line)
                laws_data.append(law)
                
                metrics['total_laws'] += 1
                
                # By type
                law_type = law.get('type', 'unknown')
                metrics['by_type'][law_type] = metrics['by_type'].get(law_type, 0) + 1
                
                # By year
                year = law.get('year')
                if year:
                    metrics['by_year'][year] = metrics['by_year'].get(year, 0) + 1
                
                # Text stats
                text_len = law.get('text_length', 0)
                metrics['text_stats']['total_chars'] += text_len
                
                # Articles
                article_count = law.get('article_count', 0)
                metrics['article_stats']['total'] += article_count
        
        if laws_data:
            metrics['text_stats']['avg_chars'] = metrics['text_stats']['total_chars'] / len(laws_data)
            metrics['article_stats']['avg'] = metrics['article_stats']['total'] / len(laws_data)
        
        metrics_file = self.indexes_dir / f"{jsonl_file.stem}_metrics.json"
        with open(metrics_file, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ Metrics: {metrics['total_laws']} laws, avg {metrics['text_stats']['avg_chars']:.0f} chars")
        return metrics_file

    def run_pipeline(self, variants: List[str], collections: List[str] = None):
        """Run full pipeline."""
        if collections is None:
            # Get all collections from API, filtering by available variants
            try:
                collections_data = self.api.get_collection_catalogue()
                
                # Build dict: collection_name -> set of available variants
                available = {}
                for c in collections_data:
                    name = c.get('nomeCollezione', c.get('nome'))
                    fmt = c.get('formatoCollezione', c.get('formato'))
                    if name and fmt:
                        if name not in available:
                            available[name] = set()
                        available[name].add(fmt)
                
                # Get variant codes for requested variants
                variant_codes = {
                    'originale': 'O',
                    'vigente': 'V',
                    'multivigente': 'M'
                }
                requested_codes = set(variant_codes.get(v, v) for v in variants)
                
                # Only include collections that have at least one requested variant
                collections = []
                for name, variants_available in available.items():
                    if variants_available & requested_codes:  # intersection
                        collections.append(name)
                
                logger.info(f"Found {len(collections)} collections with variants {variants}")
                
            except Exception as e:
                logger.error(f"Failed to get collections: {e}")
                collections = ['Cost', 'DPR']  # Fallback

        logger.info(f"Starting pipeline: {len(collections)} collections, variants: {variants}")
        
        all_laws = []
        
        for variant in variants:
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing variant: {variant.upper()}")
            logger.info(f"{'='*60}")
            
            variant_laws = []
            
            for collection in collections:
                try:
                    # Download
                    zip_file = self.download_collection(collection, variant)
                    
                    # Parse
                    laws = self.parse_collection(zip_file)
                    variant_laws.extend(laws)
                    
                except Exception as e:
                    logger.warning(f"Skipping {collection}: {e}")
                    continue
            
            if variant_laws:
                # Save JSONL
                jsonl_file = self.save_laws_jsonl(variant_laws, variant)
                
                # Build indexes
                self.build_citation_index(jsonl_file)
                self.generate_metrics(jsonl_file)
                
                all_laws.extend(variant_laws)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"✓ Pipeline complete: {len(all_laws)} total laws processed")
        logger.info(f"{'='*60}")
        
        return all_laws


def main():
    parser = argparse.ArgumentParser(description='Normattiva data pipeline')
    parser.add_argument(
        '--variants', '-v',
        nargs='+',
        default=['vigente'],
        choices=['originale', 'vigente', 'multivigente'],
        help='Law variants to process'
    )
    parser.add_argument(
        '--collections', '-c',
        nargs='+',
        help='Specific collections (default: all)'
    )
    parser.add_argument(
        '--data-dir', '-d',
        default='data',
        help='Data directory'
    )
    
    args = parser.parse_args()
    
    pipeline = NormattivaPipeline(Path(args.data_dir))
    pipeline.run_pipeline(args.variants, args.collections)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
