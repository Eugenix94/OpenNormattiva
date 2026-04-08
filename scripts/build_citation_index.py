#!/usr/bin/env python3
"""
Build citation index from law texts.

Extracts and indexes all references between laws:
- Direct citations (references to other laws)
- Amendment relationships
- Cross-sectional references
- Subject matter linkages

Output: Queryable citation graph for research interface.

Usage:
    python scripts/build_citation_index.py --input data/processed/laws_with_amendments.jsonl --output data/indexes/citations.json
"""

import json
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class CitationIndexBuilder:
    """Build citation index from law texts."""

    # Regex patterns for citation detection
    CITATION_PATTERNS = [
        # "articolo 1 della legge 123/2021"
        r"articolo\s+(\d+)\s+(?:della|del|d[ei]lla)\s+(?:legge|decreto|d\.lgs|d\.l\.)\s+(\d+/\d+)",
        # "art. 1 L. 123/2021"
        r"art\.\s+(\d+)\s+(?:L\.|D\.L\.)\s+(\d+/\d+)",
        # "L. 123/2021"
        r"(?:legge|decreto|d\.lgs)\s+(\d+/\d+)",
        # "D.Lgs. 123/2021"
        r"d\.lgs\.?\s+(\d+/\d+)",
        # "D.L. 123/2021"
        r"d\.l\.?\s+(\d+/\d+)",
    ]

    def __init__(self):
        self.laws = {}
        self.citations = defaultdict(set)  # {citing_law_id: set(cited_law_ids)}
        self.citation_details = defaultdict(list)  # Details of each citation

    def load_laws(self, jsonl_file: Path) -> int:
        """Load laws from JSONL file."""
        logger.info(f"Loading laws from {jsonl_file}...")
        count = 0
        
        try:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        law = json.loads(line)
                        law_id = law.get('id') or law.get('urn')
                        self.laws[law_id] = law
                        count += 1
                        
                        if count % 1000 == 0:
                            logger.info(f"  Loaded {count} laws...")
            
            logger.info(f"✓ Loaded {count} laws")
            return count
        
        except Exception as e:
            logger.error(f"✗ Failed to load laws: {e}")
            raise

    def extract_citations(self, text: str) -> Set[str]:
        """
        Extract citations from text.
        
        Args:
            text: Law text to analyze
        
        Returns:
            Set of citation strings found
        """
        citations = set()
        
        for pattern in self.CITATION_PATTERNS:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                citations.add(match.group(0))
        
        return citations

    def normalize_citation(self, citation: str) -> Optional[str]:
        """
        Normalize citation to standard format.
        
        Returns:
            Normalized citation (e.g., "123/2021") or None
        """
        # Extract law number from various formats
        match = re.search(r"(\d+/\d+)", citation)
        if match:
            return match.group(1)
        return None

    def build_index(self) -> Dict:
        """Build the citation index."""
        logger.info("Building citation index...")
        
        law_count = 0
        citation_count = 0
        
        for law_id, law in self.laws.items():
            # Get law text
            text = law.get('text', '') or ''
            title = law.get('title', '') or ''
            
            # Extract citations from text
            raw_citations = self.extract_citations(text.lower())
            raw_citations.update(self.extract_citations(title.lower()))
            
            # Normalize and dedup
            normalized = set()
            for citation in raw_citations:
                norm = self.normalize_citation(citation)
                if norm:
                    normalized.add(norm)
                    citation_count += 1
                    
                    self.citation_details[law_id].append({
                        'cited_law': norm,
                        'raw_citation': citation
                    })
            
            self.citations[law_id] = normalized
            law_count += 1
            
            if law_count % 1000 == 0:
                logger.info(f"  Processed {law_count} laws, found {citation_count} citations...")
        
        logger.info(f"✓ Index complete: {law_count} laws, {citation_count} citations found")
        return {
            'total_laws_indexed': law_count,
            'total_citations': citation_count,
            'laws_with_citations': len([c for c in self.citations.values() if c])
        }

    def calculate_citation_stats(self) -> Dict:
        """Calculate statistics about citations."""
        citation_counts = [len(c) for c in self.citations.values()]
        
        if not citation_counts:
            return {'error': 'No citations found'}
        
        return {
            'total_citations': sum(citation_counts),
            'avg_per_law': sum(citation_counts) / len(citation_counts),
            'max_citations_in_one_law': max(citation_counts),
            'min_citations_in_one_law': min(citation_counts),
            'laws_with_no_citations': len([c for c in citation_counts if c == 0]),
            'highly_cited_laws': len([c for c in citation_counts if c > 50])
        }

    def save_index(self, output_file: Path):
        """Save citation index as JSON."""
        logger.info(f"Saving citation index to {output_file}...")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        index = {
            'metadata': {
                'generated': datetime.now().isoformat(),
                'total_laws': len(self.laws),
                'total_citations': sum(len(c) for c in self.citations.values()),
                'statistics': self.calculate_citation_stats()
            },
            'citations': {}
        }
        
        # Convert sets to lists for JSON serialization
        for law_id, cited_laws in self.citations.items():
            index['citations'][law_id] = {
                'cited_laws': list(cited_laws),
                'citations_count': len(cited_laws),
                'details': self.citation_details.get(law_id, [])
            }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ Saved citation index to {output_file}")

    def save_citation_graph(self, output_file: Path):
        """Save as graph format for analysis."""
        logger.info(f"Saving citation graph to {output_file}...")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        graph = {
            'nodes': [],
            'edges': []
        }
        
        # Add nodes
        for law_id in self.laws.keys():
            graph['nodes'].append({
                'id': law_id,
                'label': self.laws[law_id].get('title', law_id),
                'citations_count': len(self.citations.get(law_id, []))
            })
        
        # Add edges
        edge_id = 0
        for citing_law, cited_laws in self.citations.items():
            for cited_law in cited_laws:
                graph['edges'].append({
                    'id': edge_id,
                    'source': citing_law,
                    'target': cited_law,
                    'type': 'cites'
                })
                edge_id += 1
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(graph, f, ensure_ascii=False, indent=2)
        
        logger.info(
            f"✓ Saved citation graph: "
            f"{len(graph['nodes'])} nodes, "
            f"{len(graph['edges'])} edges"
        )


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Build citation index from laws'
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        help='Input JSONL file with laws'
    )
    parser.add_argument(
        '--output', '-o',
        default='data/indexes/citations.json',
        help='Output JSON file for citation index'
    )
    parser.add_argument(
        '--graph-output',
        default='data/indexes/citation_graph.json',
        help='Output file for graph format'
    )
    
    args = parser.parse_args()
    
    input_file = Path(args.input)
    if not input_file.exists():
        logger.error(f"✗ Input file not found: {input_file}")
        sys.exit(1)
    
    builder = CitationIndexBuilder()
    
    try:
        # Load laws
        builder.load_laws(input_file)
        
        # Build index
        stats = builder.build_index()
        
        # Save outputs
        builder.save_index(Path(args.output))
        builder.save_citation_graph(Path(args.graph_output))
        
        # Print summary
        print("\n=== Citation Index Summary ===")
        print(json.dumps(stats, indent=2))
        print("\nCitation Statistics:")
        print(json.dumps(builder.calculate_citation_stats(), indent=2))
        
    except Exception as e:
        logger.error(f"Citation indexing failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
