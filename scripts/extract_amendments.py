#!/usr/bin/env python3
"""
Extract amendments and versioning information from multivigente laws.

Processes raw JSONL files to identify:
- Amendment chains (original → amendments → current)
- Temporal validity periods
- Cross-references between laws
- Modifying/Modification relationships

Output: Enhanced JSONL with amendment metadata.

Usage:
    python scripts/extract_amendments.py --input data/raw/laws_multivigente.jsonl --output data/processed/amendments.jsonl
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set, Optional
from collections import defaultdict
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AmendmentExtractor:
    """Extract and organize amendment relationships from laws."""

    def __init__(self):
        self.laws_by_id = {}
        self.amendment_chains = defaultdict(list)
        self.modifications = defaultdict(set)

    def load_laws(self, jsonl_file: Path) -> int:
        """Load all laws from JSONL file."""
        count = 0
        logger.info(f"Loading laws from {jsonl_file}...")
        
        try:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        law = json.loads(line)
                        law_id = law.get('id') or law.get('urn')
                        self.laws_by_id[law_id] = law
                        count += 1
                        
                        if count % 1000 == 0:
                            logger.info(f"  Loaded {count} laws...")
            
            logger.info(f"✓ Loaded {count} laws total")
            return count
        
        except Exception as e:
            logger.error(f"✗ Failed to load laws: {e}")
            raise

    def extract_amendment_chains(self) -> Dict[str, List[Dict]]:
        """
        Build amendment chains: original law → modifications → current.
        
        Returns:
            Dict mapping original law ID to list of amendments in chronological order
        """
        logger.info("Building amendment chains...")
        chains = defaultdict(list)
        
        for law_id, law in self.laws_by_id.items():
            # Check for modification relationships
            modifies = law.get('modifies', [])
            if isinstance(modifies, str):
                modifies = [modifies]
            
            for modified_id in modifies:
                chains[modified_id].append({
                    'law_id': law_id,
                    'date': law.get('published_date'),
                    'type': law.get('type'),
                    'title': law.get('title')
                })
        
        # Sort each chain by date
        for chain in chains.values():
            chain.sort(key=lambda x: x.get('date', ''))
        
        logger.info(f"✓ Found {len(chains)} law chains with amendments")
        return dict(chains)

    def extract_temporal_validity(self, law: Dict) -> Dict:
        """Extract validity period for a specific law version."""
        return {
            'valid_from': law.get('date_in_force'),
            'valid_to': law.get('date_out_of_force'),
            'published_date': law.get('published_date'),
            'is_current': law.get('is_current', False)
        }

    def build_amendment_metadata(self) -> Dict:
        """Build comprehensive amendment metadata."""
        logger.info("Building amendment metadata...")
        
        metadata = {
            'extraction_date': datetime.now().isoformat(),
            'total_laws': len(self.laws_by_id),
            'amendment_chains': self.extract_amendment_chains(),
            'statistics': {
                'laws_with_amendments': 0,
                'total_amendments': 0,
                'avg_chain_length': 0
            }
        }
        
        # Calculate stats
        chains = metadata['amendment_chains']
        metadata['statistics']['laws_with_amendments'] = len(chains)
        total_amendments = sum(len(chain) for chain in chains.values())
        metadata['statistics']['total_amendments'] = total_amendments
        
        if chains:
            metadata['statistics']['avg_chain_length'] = (
                total_amendments / len(chains)
            )
        
        logger.info(
            f"✓ Amendment metadata: "
            f"{len(chains)} chains, "
            f"{total_amendments} total amendments"
        )
        return metadata

    def save_enhanced_laws(self, output_file: Path, include_amendments: bool = True):
        """
        Save laws with amendment metadata included.
        
        Args:
            output_file: Path to output JSONL
            include_amendments: Whether to add amendment info to each law
        """
        logger.info(f"Saving enhanced laws to {output_file}...")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        chains = self.extract_amendment_chains()
        count = 0
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for law_id, law in self.laws_by_id.items():
                # Add amendment context
                if include_amendments:
                    law['amendments'] = chains.get(law_id, [])
                    
                    # Add reverse: what does this law amend?
                    modifies = law.get('modifies', [])
                    if isinstance(modifies, str):
                        modifies = [modifies]
                    law['modifies'] = modifies
                
                f.write(json.dumps(law, ensure_ascii=False) + '\n')
                count += 1
                
                if count % 1000 == 0:
                    logger.info(f"  Saved {count} laws...")
        
        logger.info(f"✓ Saved {count} enhanced laws to {output_file}")

    def save_amendment_chains(self, output_file: Path):
        """Save amendment chains as standalone file."""
        logger.info(f"Saving amendment chains to {output_file}...")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        chains = self.extract_amendment_chains()
        
        with open(output_file, 'w', encoding='utf-8') as f:
            for original_id, amendments in chains.items():
                record = {
                    'original_law': original_id,
                    'amendment_count': len(amendments),
                    'amendments': amendments
                }
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
        
        logger.info(f"✓ Saved {len(chains)} amendment chains to {output_file}")


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Extract amendment relationships from Normattiva laws'
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        help='Input JSONL file with multivigente laws'
    )
    parser.add_argument(
        '--output', '-o',
        default='data/processed/laws_with_amendments.jsonl',
        help='Output JSONL file with amendment metadata'
    )
    parser.add_argument(
        '--chains-output',
        default='data/processed/amendment_chains.jsonl',
        help='Output file for amendment chains'
    )
    
    args = parser.parse_args()
    
    input_file = Path(args.input)
    if not input_file.exists():
        logger.error(f"✗ Input file not found: {input_file}")
        sys.exit(1)
    
    extractor = AmendmentExtractor()
    
    try:
        # Load laws
        extractor.load_laws(input_file)
        
        # Extract amendments
        extractor.save_enhanced_laws(Path(args.output))
        extractor.save_amendment_chains(Path(args.chains_output))
        
        # Print summary
        metadata = extractor.build_amendment_metadata()
        print("\n=== Amendment Extraction Summary ===")
        print(json.dumps(metadata['statistics'], indent=2))
        
    except Exception as e:
        logger.error(f"Amendment extraction failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
