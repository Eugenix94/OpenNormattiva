#!/usr/bin/env python3
"""
analyze_multivigente_delta.py

Compare vigente and multivigente datasets to identify what's unique in each.
Enables data-driven decision making for selective multivigente downloads.

Usage:
    # Analyze gap (what's in multivigente but not vigente)
    python analyze_multivigente_delta.py \\
      --vigente-dir ./data/vigente \\
      --check-multivigente \\
      --output gap_report.json
    
    # Generate recommendations
    python analyze_multivigente_delta.py \\
      --vigente-dir ./data/vigente \\
      --recommend-selective \\
      --budget-mb 1500
"""

import json
import logging
from pathlib import Path
from collections import defaultdict, Counter
from typing import Dict, List, Set, Tuple
import argparse
import re
from datetime import datetime
import hashlib

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

class MultivigenteAnalyzer:
    """Analyze gap between vigente and multivigente."""
    
    def __init__(self, vigente_dir: str):
        self.vigente_dir = Path(vigente_dir)
        self.vigente_content = {}
        self.vigente_hashes = {}
        self.collection_stats = defaultdict(dict)
        
    def load_vigente_dataset(self) -> Dict:
        """Load JSONL vigente dataset to understand what we have."""
        
        logger.info("Loading vigente dataset...")
        
        # Support both JSONL and directory of XMLs
        if self.vigente_dir.suffix == '.jsonl':
            return self._load_jsonl(self.vigente_dir)
        else:
            return self._load_from_directory(self.vigente_dir)
    
    def _load_jsonl(self, path: Path) -> Dict:
        """Load JSONL file."""
        dataset = {}
        count = 0
        
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                law = json.loads(line)
                law_id = law.get('id') or law.get('law_id')
                dataset[law_id] = law
                count += 1
        
        logger.info(f"  ✓ Loaded {count} laws from JSONL")
        return dataset
    
    def _load_from_directory(self, path: Path) -> Dict:
        """Load XML files from directory."""
        dataset = {}
        count = 0
        
        for xml_file in path.rglob('*.xml'):
            with open(xml_file) as f:
                content = f.read()
            
            # Extract law ID from AKN URI
            law_id = self._extract_law_id_from_akn(content)
            if law_id:
                dataset[law_id] = {
                    'id': law_id,
                    'akn_uri': self._extract_akn_uri(content),
                    'file': str(xml_file),
                    'size_bytes': xml_file.stat().st_size,
                    'hash': self._hash_file(xml_file)
                }
                count += 1
        
        logger.info(f"  ✓ Loaded {count} laws from XML files")
        return dataset
    
    def _extract_law_id_from_akn(self, akn_xml: str) -> str:
        """Extract law ID from AKN XML."""
        # AKN format: it/legge/2018/112 or similar
        match = re.search(r'eid="([^"]+)"', akn_xml[:500])
        if match:
            eid = match.group(1)
            parts = eid.split('/')
            if len(parts) >= 3:
                return f"{parts[-2]}-{parts[-1]}"  # "2018-112"
        return None
    
    def _extract_akn_uri(self, akn_xml: str) -> str:
        """Extract full AKN URI."""
        match = re.search(r'eid="([^"]+)"', akn_xml[:500])
        return match.group(1) if match else None
    
    def _hash_file(self, path: Path) -> str:
        """Calculate file hash."""
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            h.update(f.read())
        return h.hexdigest()
    
    def analyze_gap(self, vigente_dataset: Dict) -> Dict:
        """Analyze what's unique in multivigente."""
        
        logger.info(f"\n{'='*80}")
        logger.info("ANALYSIS: Multivigente Gap vs Vigente")
        logger.info(f"{'='*80}\n")
        
        logger.info(f"Vigente dataset size: {len(vigente_dataset)} laws\n")
        
        # Expected gap analysis
        gap_report = {
            'timestamp': datetime.now().isoformat(),
            'vigente_count': len(vigente_dataset),
            'analysis': {}
        }
        
        # Analyze per collection
        collections_in_vigente = self._group_by_collection(vigente_dataset)
        
        logger.info("By Collection:\n")
        for collection, laws in collections_in_vigente.items():
            logger.info(f"  {collection}: {len(laws)} laws")
            gap_report['analysis'][collection] = {
                'vigente_count': len(laws),
                'expected_multivigente_ratio': self._estimate_mv_ratio(collection),
                'expected_mv_laws': int(len(laws) * self._estimate_mv_ratio(collection)),
                'additional_laws': int(len(laws) * (self._estimate_mv_ratio(collection) - 1))
            }
        
        return gap_report
    
    def _group_by_collection(self, dataset: Dict) -> Dict[str, List]:
        """Group laws by collection."""
        by_collection = defaultdict(list)
        
        for law_id, law_data in dataset.items():
            # Extract collection from law_id or metadata
            collection = self._infer_collection(law_id, law_data)
            by_collection[collection].append(law_id)
        
        return dict(by_collection)
    
    def _infer_collection(self, law_id: str, law_data: Dict) -> str:
        """Infer collection name from law ID and metadata."""
        # Could be encoded in law_data or type
        if isinstance(law_data, dict):
            if 'collection' in law_data:
                return law_data['collection']
            if 'type' in law_data:
                return law_data['type']
        
        # Default based on ID pattern
        if 'cost' in str(law_id).lower():
            return 'Costituzione'
        elif 'codice' in str(law_id).lower():
            return 'Codici'
        else:
            return 'Varie'
    
    def _estimate_mv_ratio(self, collection: str) -> float:
        """Estimate multivigente/vigente ratio based on collection type."""
        # Based on real_download_data.json
        ratios = {
            'Codici': 41.17,  # 118.23x size, 41.17x files
            'Leggi costituzionali': 2.86,
            'Leggi di delegazione europea': 5.82,
            'Atti di attuazione': 2.69,
            'Varie': 2.0  # Conservative default
        }
        return ratios.get(collection, 2.0)
    
    def recommend_selective_download(self, budget_mb: int = 1500) -> Dict:
        """Generate selective multivigente download recommendations."""
        
        logger.info(f"\n{'='*80}")
        logger.info(f"RECOMMENDATIONS: Selective Multivigente Download")
        logger.info(f"Budget: {budget_mb} MB")
        logger.info(f"{'='*80}\n")
        
        recommendations = {
            'timestamp': datetime.now().isoformat(),
            'budget_mb': budget_mb,
            'strategy': 'vigente-first with selective multivigente',
            'collections': []
        }
        
        # Based on real measurements:
        collection_sizes_mv = {
            'Codici': {'mv_mb': 1116.21, 'files': 3294, 'value': 'HIGH',
                       'reason': 'Legal amendment history essential'},
            'Leggi costituzionali': {'mv_mb': 0.86, 'files': 143, 'value': 'HIGH',
                                     'reason': 'Constitutional history'},
            'Leggi di delegazione europea': {'mv_mb': 10.41, 'files': 192, 'value': 'MEDIUM',
                                             'reason': 'EU delegation history'},
            'Atti di attuazione': {'mv_mb': 1.38, 'files': 105, 'value': 'LOW',
                                   'reason': 'Regulatory implementation'},
        }
        
        remaining_budget = budget_mb
        
        logger.info("Priority ranking by value/size ratio:\n")
        
        # Sort by value then size
        priority_order = [
            ('Leggi costituzionali', {'mv_mb': 0.86, 'files': 143, 'value': 'HIGH'}),
            ('Codici', {'mv_mb': 1116.21, 'files': 3294, 'value': 'HIGH'}),
            ('Leggi di delegazione europea', {'mv_mb': 10.41, 'files': 192, 'value': 'MEDIUM'}),
            ('Atti di attuazione', {'mv_mb': 1.38, 'files': 105, 'value': 'LOW'}),
        ]
        
        for collection, info in priority_order:
            mv_mb = info['mv_mb']
            
            if mv_mb <= remaining_budget:
                recommendations['collections'].append({
                    'name': collection,
                    'type': 'multivigente',
                    'size_mb': mv_mb,
                    'files_count': info['files'],
                    'value': info['value'],
                    'reason': info.get('reason', ''),
                    'include': True
                })
                remaining_budget -= mv_mb
                
                logger.info(f"  ✓ {collection}")
                logger.info(f"    Size: {mv_mb:.2f} MB | Value: {info['value']}")
                logger.info(f"    Reason: {info.get('reason', '')}")
            else:
                recommendations['collections'].append({
                    'name': collection,
                    'size_mb': mv_mb,
                    'value': info['value'],
                    'include': False,
                    'reason': f'Exceeds budget ({mv_mb:.2f} > {remaining_budget:.2f})'
                })
                logger.info(f"  ✗ {collection} (exceeds budget)")
        
        recommendations['remaining_budget_mb'] = remaining_budget
        recommendations['total_mv_recommended_mb'] = budget_mb - remaining_budget
        
        # Calculate total dataset size
        vigente_base_mb = 12200  # From analysis
        total_recommended_mb = vigente_base_mb + recommendations['total_mv_recommended_mb']
        recommendations['estimated_total_mb'] = total_recommended_mb
        recommendations['estimated_total_gb'] = round(total_recommended_mb / 1024, 2)
        
        logger.info(f"\n📊 SUMMARY:")
        logger.info(f"  Vigente baseline: 12,200 MB")
        logger.info(f"  Multivigente addition: {recommendations['total_mv_recommended_mb']:.0f} MB")
        logger.info(f"  Total estimated: {recommendations['estimated_total_gb']} GB")
        logger.info(f"  Remaining budget: {remaining_budget:.0f} MB\n")
        
        return recommendations
    
    def export_json(self, data: Dict, output_path: str):
        """Save analysis results."""
        path = Path(output_path)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"✓ Saved to {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze multivigente gap vs vigente"
    )
    parser.add_argument(
        "--vigente-dir",
        required=True,
        help="Path to vigente dataset (JSONL or directory of XMLs)"
    )
    parser.add_argument(
        "--check-multivigente",
        action="store_true",
        help="Analyze what's unique in multivigente"
    )
    parser.add_argument(
        "--recommend-selective",
        action="store_true",
        help="Generate selective download recommendations"
    )
    parser.add_argument(
        "--budget-mb",
        type=int,
        default=1500,
        help="Max MB for multivigente additions (default: 1500)"
    )
    parser.add_argument(
        "--output",
        default="gap_analysis.json",
        help="Output JSON file (default: gap_analysis.json)"
    )
    
    args = parser.parse_args()
    
    analyzer = MultivigenteAnalyzer(args.vigente_dir)
    vigente_ds = analyzer.load_vigente_dataset()
    
    results = {
        'analysis_type': 'multivigente_delta',
        'timestamp': datetime.now().isoformat(),
        'components': {}
    }
    
    if args.check_multivigente:
        gap = analyzer.analyze_gap(vigente_ds)
        results['components']['gap_analysis'] = gap
    
    if args.recommend_selective:
        recommendations = analyzer.recommend_selective_download(budget_mb=args.budget_mb)
        results['components']['recommendations'] = recommendations
    
    analyzer.export_json(results, args.output)


if __name__ == "__main__":
    main()
