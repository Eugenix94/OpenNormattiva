#!/usr/bin/env python3
"""
Generate metrics and statistics for the Normattiva dataset.

Produces:
- Dataset statistics (size, coverage, dates)
- Law distribution by type/year
- Amendment activity metrics
- Data quality indicators

Output: Metrics saved as JSON for monitoring and reporting.

Usage:
    python scripts/generate_metrics.py --input data/processed/laws_with_amendments.jsonl --output data/indexes/metrics.json
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Counter as CounterType
from collections import Counter
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MetricsGenerator:
    """Generate metrics from law dataset."""

    def __init__(self):
        self.laws = []
        self.metrics = {}

    def load_laws(self, jsonl_file: Path) -> int:
        """Load all laws from JSONL."""
        logger.info(f"Loading laws from {jsonl_file}...")
        count = 0
        
        try:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        law = json.loads(line)
                        self.laws.append(law)
                        count += 1
                        
                        if count % 1000 == 0:
                            logger.info(f"  Loaded {count} laws...")
            
            logger.info(f"✓ Loaded {count} laws")
            return count
        
        except Exception as e:
            logger.error(f"✗ Failed to load laws: {e}")
            raise

    def calculate_basic_stats(self) -> Dict:
        """Calculate basic dataset statistics."""
        logger.info("Calculating basic statistics...")
        
        return {
            'total_laws': len(self.laws),
            'extraction_date': datetime.now().isoformat(),
            'file_size_mb': sum(
                len(json.dumps(law, ensure_ascii=False).encode())
                for law in self.laws
            ) / (1024 * 1024)
        }

    def calculate_type_distribution(self) -> Dict[str, int]:
        """Count laws by type."""
        logger.info("Calculating type distribution...")
        
        types = Counter()
        for law in self.laws:
            law_type = law.get('type', 'unknown')
            types[law_type] += 1
        
        return dict(types)

    def calculate_temporal_distribution(self) -> Dict:
        """Analyze distribution by date."""
        logger.info("Calculating temporal distribution...")
        
        by_year = Counter()
        by_decade = Counter()
        
        for law in self.laws:
            date = law.get('published_date') or law.get('date')
            if date:
                try:
                    if isinstance(date, str):
                        year = int(date[:4])
                    else:
                        year = date.year
                    
                    by_year[year] += 1
                    decade = (year // 10) * 10
                    by_decade[decade] += 1
                except (ValueError, AttributeError):
                    pass
        
        return {
            'by_year': dict(sorted(by_year.items())),
            'by_decade': dict(sorted(by_decade.items())),
            'earliest_law_year': min(by_year.keys()) if by_year else None,
            'latest_law_year': max(by_year.keys()) if by_year else None
        }

    def calculate_amendment_stats(self) -> Dict:
        """Analyze amendment activity."""
        logger.info("Calculating amendment statistics...")
        
        laws_with_amendments = 0
        laws_modified_by_others = 0
        total_amendment_count = 0
        amendment_depths = []
        
        for law in self.laws:
            amendments = law.get('amendments', [])
            if amendments:
                laws_with_amendments += 1
                total_amendment_count += len(amendments)
                amendment_depths.append(len(amendments))
            
            modifies = law.get('modifies', [])
            if modifies:
                if isinstance(modifies, str):
                    modifies = [modifies]
                if modifies:
                    laws_modified_by_others += 1
        
        return {
            'laws_with_amendments': laws_with_amendments,
            'laws_modifying_others': laws_modified_by_others,
            'total_amendment_relationships': total_amendment_count,
            'avg_amendments_per_law': (
                total_amendment_count / laws_with_amendments
                if laws_with_amendments > 0 else 0
            ),
            'max_amendments_single_law': (
                max(amendment_depths) if amendment_depths else 0
            )
        }

    def calculate_text_metrics(self) -> Dict:
        """Analyze text metrics."""
        logger.info("Calculating text metrics...")
        
        text_lengths = []
        title_lengths = []
        laws_with_text = 0
        
        for law in self.laws:
            text = law.get('text', '')
            title = law.get('title', '')
            
            if text:
                laws_with_text += 1
                text_lengths.append(len(text))
            
            if title:
                title_lengths.append(len(title))
        
        def get_stat(lengths):
            if not lengths:
                return {}
            lengths_sorted = sorted(lengths)
            return {
                'min': min(lengths),
                'max': max(lengths),
                'avg': sum(lengths) / len(lengths),
                'median': lengths_sorted[len(lengths) // 2]
            }
        
        return {
            'laws_with_text': laws_with_text,
            'text_length_chars': get_stat(text_lengths),
            'title_length_chars': get_stat(title_lengths)
        }

    def calculate_coverage_metrics(self) -> Dict:
        """Calculate data completeness metrics."""
        logger.info("Calculating coverage metrics...")
        
        coverage = {
            'has_id': 0,
            'has_text': 0,
            'has_title': 0,
            'has_date': 0,
            'has_type': 0,
            'has_amendments': 0
        }
        
        for law in self.laws:
            if law.get('id') or law.get('urn'):
                coverage['has_id'] += 1
            if law.get('text'):
                coverage['has_text'] += 1
            if law.get('title'):
                coverage['has_title'] += 1
            if law.get('published_date') or law.get('date'):
                coverage['has_date'] += 1
            if law.get('type'):
                coverage['has_type'] += 1
            if law.get('amendments'):
                coverage['has_amendments'] += 1
        
        total = len(self.laws)
        return {
            'counts': coverage,
            'percentages': {
                k: (v / total * 100) if total > 0 else 0
                for k, v in coverage.items()
            }
        }

    def generate_all_metrics(self) -> Dict:
        """Generate complete metrics report."""
        logger.info("Generating complete metrics report...")
        
        metrics = {
            'generated': datetime.now().isoformat(),
            'basic_stats': self.calculate_basic_stats(),
            'type_distribution': self.calculate_type_distribution(),
            'temporal_distribution': self.calculate_temporal_distribution(),
            'amendment_statistics': self.calculate_amendment_stats(),
            'text_metrics': self.calculate_text_metrics(),
            'coverage': self.calculate_coverage_metrics()
        }
        
        return metrics

    def save_metrics(self, output_file: Path):
        """Save metrics as JSON."""
        logger.info(f"Saving metrics to {output_file}...")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        metrics = self.generate_all_metrics()
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(metrics, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ Saved metrics to {output_file}")
        return metrics


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Generate metrics from Normattiva dataset'
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        help='Input JSONL file with laws'
    )
    parser.add_argument(
        '--output', '-o',
        default='data/indexes/metrics.json',
        help='Output JSON file for metrics'
    )
    
    args = parser.parse_args()
    
    input_file = Path(args.input)
    if not input_file.exists():
        logger.error(f"✗ Input file not found: {input_file}")
        sys.exit(1)
    
    generator = MetricsGenerator()
    
    try:
        # Load laws
        generator.load_laws(input_file)
        
        # Generate and save metrics
        metrics = generator.save_metrics(Path(args.output))
        
        # Print summary
        print("\n=== Metrics Summary ===")
        print(f"Total Laws: {metrics['basic_stats']['total_laws']}")
        print(f"Types: {len(metrics['type_distribution'])} different types")
        print(
            f"Time Span: "
            f"{metrics['temporal_distribution']['earliest_law_year']} - "
            f"{metrics['temporal_distribution']['latest_law_year']}"
        )
        print(
            f"Laws with Amendments: "
            f"{metrics['amendment_statistics']['laws_with_amendments']}"
        )
        print(
            f"Data Coverage: "
            f"{metrics['coverage']['percentages']['has_text']:.1f}% have text"
        )
        
    except Exception as e:
        logger.error(f"Metrics generation failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
