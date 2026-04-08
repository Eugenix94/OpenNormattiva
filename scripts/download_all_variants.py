#!/usr/bin/env python3
"""
Download all Normattiva law variants (Originale, Vigente, Multivigente).

This runs nightly via GitHub Actions to fetch the latest laws from Normattiva API.
Stores raw JSONL files for processing by other scripts.

Usage:
    python scripts/download_all_variants.py --output data/raw --format jsonl
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from normattiva_api_client import NormativaAPIClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class VariantDownloader:
    """Download and organize Normattiva law variants."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.api_client = NormativaAPIClient()

    def download_originale(self, filename: Optional[str] = None) -> Path:
        """Download original law texts (Originale)."""
        logger.info("Starting download of Originale (original) laws...")
        
        if filename is None:
            filename = f"laws_originale_{datetime.now().strftime('%Y%m%d')}.jsonl"
        
        output_file = self.output_dir / filename
        
        try:
            # Fetch from API - adjust based on actual API
            laws = self.api_client.get_all_laws(variant='originale')
            
            with open(output_file, 'w', encoding='utf-8') as f:
                for law in laws:
                    f.write(json.dumps(law, ensure_ascii=False) + '\n')
            
            logger.info(f"✓ Downloaded Originale laws → {output_file}")
            return output_file
            
        except Exception as e:
            logger.error(f"✗ Failed to download Originale laws: {e}")
            raise

    def download_vigente(self, filename: Optional[str] = None) -> Path:
        """Download current in-force laws (Vigente)."""
        logger.info("Starting download of Vigente (current) laws...")
        
        if filename is None:
            filename = f"laws_vigente_{datetime.now().strftime('%Y%m%d')}.jsonl"
        
        output_file = self.output_dir / filename
        
        try:
            laws = self.api_client.get_all_laws(variant='vigente')
            
            with open(output_file, 'w', encoding='utf-8') as f:
                for law in laws:
                    f.write(json.dumps(law, ensure_ascii=False) + '\n')
            
            logger.info(f"✓ Downloaded Vigente laws → {output_file}")
            return output_file
            
        except Exception as e:
            logger.error(f"✗ Failed to download Vigente laws: {e}")
            raise

    def download_multivigente(self, filename: Optional[str] = None) -> Path:
        """
        Download all historical versions (Multivigente).
        
        Warning: This is ~41x larger than Vigente.
        Run selectively or on scheduled basis only.
        """
        logger.warning("Starting download of Multivigente (all versions) laws...")
        logger.warning("⚠️  This is 41x larger than Vigente - may take significant time")
        
        if filename is None:
            filename = f"laws_multivigente_{datetime.now().strftime('%Y%m%d')}.jsonl"
        
        output_file = self.output_dir / filename
        
        try:
            laws = self.api_client.get_all_laws(variant='multivigente')
            
            with open(output_file, 'w', encoding='utf-8') as f:
                for law in laws:
                    f.write(json.dumps(law, ensure_ascii=False) + '\n')
            
            logger.info(f"✓ Downloaded Multivigente laws → {output_file}")
            return output_file
            
        except Exception as e:
            logger.error(f"✗ Failed to download Multivigente laws: {e}")
            raise

    def download_all(self) -> dict:
        """Download all three variants. Used for full refresh."""
        results = {
            'originale': None,
            'vigente': None,
            'multivigente': None,
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            results['originale'] = str(self.download_originale())
            results['vigente'] = str(self.download_vigente())
            # Multivigente is optional - only on full refresh days
            # results['multivigente'] = str(self.download_multivigente())
            
            logger.info("✓ All core variants downloaded successfully")
            return results
            
        except Exception as e:
            logger.error(f"✗ Download pipeline failed: {e}")
            raise


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Download Normattiva law variants'
    )
    parser.add_argument(
        '--output', '-o',
        default='data/raw',
        help='Output directory for JSONL files'
    )
    parser.add_argument(
        '--variant', '-v',
        choices=['originale', 'vigente', 'multivigente', 'all'],
        default='vigente',
        help='Which variant to download (default: vigente)'
    )
    
    args = parser.parse_args()
    
    downloader = VariantDownloader(args.output)
    
    try:
        if args.variant == 'all':
            result = downloader.download_all()
            print(json.dumps(result, indent=2))
        elif args.variant == 'originale':
            output = downloader.download_originale()
            print(f"Downloaded to: {output}")
        elif args.variant == 'vigente':
            output = downloader.download_vigente()
            print(f"Downloaded to: {output}")
        elif args.variant == 'multivigente':
            output = downloader.download_multivigente()
            print(f"Downloaded to: {output}")
    
    except Exception as e:
        logger.error(f"Download failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
