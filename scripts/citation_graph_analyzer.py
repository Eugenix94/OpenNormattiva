#!/usr/bin/env python3
"""
citation_graph_analyzer.py

Extract legal citation dependencies and identify dataset gaps.
Build citation graph to power LLM context and dataset expansion.

Usage:
    # Extract citations from JSONL
    python citation_graph_analyzer.py \\
      --input laws.jsonl \\
      --output citation_graph.json \\
      --analyze-gaps
    
    # Generate LLM context from high-degree nodes
    python citation_graph_analyzer.py \\
      --input laws.jsonl \\
      --input-graph citation_graph.json \\
      --generate-llm-context \\
      --top-n 50
"""

import json
import logging
import re
from pathlib import Path
from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple, Optional
import argparse
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

class SimpleGraph:
    """Simple directed graph implementation (no external deps)."""
    def __init__(self):
        self.nodes = set()
        self.edges = []
        self.adj = defaultdict(list)
    
    def add_edge(self, u, v, **attrs):
        self.nodes.add(u)
        self.nodes.add(v)
        self.edges.append({'source': u, 'target': v, **attrs})
        self.adj[u].append(v)
    
    def in_degree(self, node):
        return sum(1 for _, target in self.edges if target == node)

class CitationGraphAnalyzer:
    """Extract and analyze legal citation networks."""
    
    # Patterns to detect Italian legal references
    CITATION_PATTERNS = [
        # Law references: "L-2018-112", "legge 112/2018"
        (r'(?:l\.?|legge)\s*(?:n\.?|numero)?\s*(\d+)\s*(?:/|del)\s*(\d{4})', 'legge'),
        # Legislative decrees: "D.Lgs 5/2021", "Decreto Legislativo"
        (r'(?:d\.?\s*lgs\.?|decreto\s*legislativo)\s*(?:n\.?|numero)?\s*(\d+)\s*(?:/|del)\s*(\d{4})', 'dlgs'),
        # Presidential decrees: "DPR 394/1990"
        (r'(?:dpr|decreto\s*presidentziale)\s*(?:n\.?|numero)?\s*(\d+)\s*(?:/|del)\s*(\d{4})', 'dpr'),
        # Constitutional articles: "Art. IV Costituzione", "Cost. Art. 3"
        (r'(?:art|articolo|artt|articoli)\.?\s*([ivxIVX]+|(?:[0-9]+[a-z]?))(?:\s*(?:della|della|e\s+seguenti|ss\.?))?\s*(?:della\s*)?(?:cost\.?|costituzione)', 'cost'),
        # EU regulations: "Reg. (EU) 2016/679"
        (r'(?:reg|regolamento)\s*(?:\(eu\)|\(ce\))?\s*(\d+)/(\d{4})', 'regue'),
        # Codici: "Codice Civile art. 123"
        (r'(?:codice\s+(?:civile|penale|procedura|procedura\s+civile|procedura\s+penale|commercio|della\s+navigazione))\s*(?:art|articolo)?\.?\s*([0-9]+)', 'codice'),
        # International conventions/treaties
        (r'(?:convenzione|trattato|protocollo)\s+(?:di|del)?\s*([a-z\s]+)', 'convention'),
    ]
    
    def __init__(self):
        self.graph = SimpleGraph()
        self.citation_registry = defaultdict(set)  # law_id -> set of cited laws
        self.cited_by_registry = defaultdict(set)  # law_id -> set of citing laws
        self.laws_metadata = {}
        self.missing_laws = set()
        
    def load_jsonl_dataset(self, jsonl_path: str) -> Dict:
        """Load JSONL laws dataset."""
        laws = {}
        count = 0
        
        logger.info(f"Loading JSONL from {jsonl_path}...")
        
        with open(jsonl_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    law = json.loads(line)
                    law_id = law.get('id') or law.get('law_id')
                    if law_id:
                        laws[law_id] = law
                        self.laws_metadata[law_id] = {
                            'titolo': law.get('titolo', ''),
                            'tipo': law.get('tipo', ''),
                            'data': law.get('publication_date') or law.get('data', ''),
                        }
                        count += 1
                except json.JSONDecodeError:
                    continue
        
        logger.info(f"✓ Loaded {count} laws\n")
        return laws
    
    def extract_citations_from_text(self, text: str) -> Set[Tuple[str, str]]:
        """Extract all legal citations from text."""
        citations = set()
        
        if not text:
            return citations
        
        # Normalize text for matching
        text_normalized = text.lower()
        
        for pattern, citation_type in self.CITATION_PATTERNS:
            matches = re.finditer(pattern, text_normalized, re.IGNORECASE)
            
            for match in matches:
                try:
                    if citation_type in ['legge', 'dlgs', 'dpr']:
                        # Extract number/year
                        groups = match.groups()
                        if len(groups) >= 2:
                            number = groups[0]
                            year = groups[1]
                            cite_id = f"{number}-{year}"
                            citations.add((cite_id, citation_type))
                    
                    elif citation_type == 'cost':
                        # Constitutional article
                        article = match.group(1) if match.lastindex >= 1 else 'IV'
                        citations.add((f"COST-{article}", 'cost'))
                    
                    elif citation_type == 'codice':
                        # Code article reference
                        article = match.group(1) if match.lastindex >= 1 else '1'
                        code_name = match.group(0)
                        citations.add((f"{code_name.upper()[:20]}-{article}", 'codice'))
                
                except Exception as e:
                    logger.debug(f"Error parsing citation: {e}")
                    continue
        
        return citations
    
    def analyze_dataset(self, laws: Dict) -> Dict:
        """Extract citation graph from entire dataset."""
        
        logger.info(f"{'='*80}")
        logger.info("Extracting citation graph from dataset")
        logger.info(f"{'='*80}\n")
        
        extracted_count = 0
        total_citations = 0
        
        for law_id, law_data in laws.items():
            # Extract text from law
            text = law_data.get('testo', '') + ' ' + law_data.get('titolo', '')
            
            # Find all citations
            citations = self.extract_citations_from_text(text)
            
            for cite_id, cite_type in citations:
                self.citation_registry[law_id].add(cite_id)
                self.cited_by_registry[cite_id].add(law_id)
                
                # Add edge to graph
                self.graph.add_edge(law_id, cite_id, type=cite_type)
                total_citations += 1
            
            if citations:
                extracted_count += 1
        
        logger.info(f"Extraction complete:")
        logger.info(f"  Laws analyzed: {len(laws)}")
        logger.info(f"  Laws with citations: {extracted_count}")
        logger.info(f"  Total citations found: {total_citations}\n")
        
        return {
            'laws_analyzed': len(laws),
            'laws_with_citations': extracted_count,
            'total_citations': total_citations,
            'graph_nodes': len(self.graph.nodes),
            'graph_edges': len(self.graph.edges),
        }
    
    def identify_missing_laws(self) -> Set[str]:
        """Identify laws cited but not in dataset."""
        
        logger.info(f"{'='*80}")
        logger.info("Identifying missing cited laws")
        logger.info(f"{'='*80}\n")
        
        all_referenced = set()
        for citations_ in self.citation_registry.values():
            all_referenced.update(citations_)
        
        existing_ids = set(self.graph.nodes) - set(self.citation_registry.keys())
        self.missing_laws = all_referenced - set(self.graph.nodes)
        
        logger.info(f"Laws in dataset: {len(self.graph.nodes)}")
        logger.info(f"Unique citations found: {len(all_referenced)}")
        logger.info(f"Missing (cited but not in dataset): {len(self.missing_laws)}\n")
        
        if self.missing_laws:
            # Group missing by type (year, etc)
            missing_by_pattern = defaultdict(list)
            for missing_id in self.missing_laws:
                pattern = missing_id.split('-')[0] if '-' in missing_id else missing_id[:4]
                missing_by_pattern[pattern].append(missing_id)
            
            logger.info("Top missing law patterns:")
            for pattern, laws in sorted(
                missing_by_pattern.items(),
                key=lambda x: -len(x[1])
            )[:10]:
                logger.info(f"  {pattern}: {len(laws)} laws")
        
        return self.missing_laws
    
    def find_high_degree_nodes(self, top_n: int = 50) -> List[Tuple[str, int]]:
        """Find laws most frequently cited (high in-degree)."""
        
        logger.info(f"\n{'='*80}")
        logger.info(f"Finding most influential laws (top {top_n})")
        logger.info(f"{'='*80}\n")
        
        in_degrees = [(node, self.graph.in_degree(node))
                      for node in self.graph.nodes]
        
        top_cited = sorted(in_degrees, key=lambda x: -x[1])[:top_n]
        
        logger.info("Most frequently cited laws:\n")
        for law_id, in_deg in top_cited[:10]:
            metadata = self.laws_metadata.get(law_id, {})
            logger.info(f"  {law_id}: cited {in_deg} times")
            if metadata.get('titolo'):
                logger.info(f"    Title: {metadata['titolo'][:60]}...")
        
        return top_cited
    
    def generate_llm_context(self, top_n: int = 30) -> str:
        """Generate system prompt context from high-degree nodes."""
        
        logger.info(f"\n{'='*80}")
        logger.info("Generating LLM context snippet")
        logger.info(f"{'='*80}\n")
        
        high_degree = self.find_high_degree_nodes(top_n=top_n)
        
        context_parts = [
            "# ITALIAN LEGAL FRAMEWORK - KEY AUTHORITIES\n",
            "These are the most fundamental laws cited throughout the dataset:\n\n"
        ]
        
        for law_id, cite_count in high_degree[:15]:
            metadata = self.laws_metadata.get(law_id, {})
            title = metadata.get('titolo', law_id)
            cite_relationships = self.cited_by_registry.get(law_id, set())
            
            context_parts.append(f"- **{law_id}**: {title}")
            context_parts.append(f"  (Referenced in {len(cite_relationships)} laws in dataset)\n")
        
        context_str = "\n".join(context_parts)
        logger.info(f"✓ Generated {len(context_str)} char context\n")
        
        return context_str
    
    def export_graph(self, output_path: str):
        """Export citation graph as JSON."""
        
        logger.info(f"Exporting citation graph to {output_path}...")
        
        graph_data = {
            'timestamp': datetime.now().isoformat(),
            'nodes': list(self.graph.nodes),
            'edges': [
                {'source': u, 'target': v, 'type': d.get('type')}
                for u, v, d in self.graph.edges(data=True)
            ],
            'citation_registry': {
                k: list(v) for k, v in self.citation_registry.items()
            },
            'cited_by_registry': {
                k: list(v) for k, v in self.cited_by_registry.items()
            },
            'missing_laws': list(self.missing_laws),
            'statistics': {
                'total_laws': len(self.graph.nodes),
                'total_citations': len(self.graph.edges),
                'missing_cited_laws': len(self.missing_laws),
            }
        }
        
        with open(output_path, 'w') as f:
            json.dump(graph_data, f, indent=2)
        
        logger.info(f"✓ Saved to {output_path}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Analyze legal citation dependencies"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input JSONL laws file"
    )
    parser.add_argument(
        "--input-graph",
        help="Optional input citation graph for analysis"
    )
    parser.add_argument(
        "--output",
        default="citation_graph.json",
        help="Output citation graph JSON"
    )
    parser.add_argument(
        "--analyze-gaps",
        action="store_true",
        help="Identify missing cited laws"
    )
    parser.add_argument(
        "--generate-llm-context",
        action="store_true",
        help="Generate LLM system prompt context"
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=50,
        help="Number of top laws for LLM context (default: 50)"
    )
    
    args = parser.parse_args()
    
    analyzer = CitationGraphAnalyzer()
    laws = analyzer.load_jsonl_dataset(args.input)
    
    stats = analyzer.analyze_dataset(laws)
    
    if args.analyze_gaps:
        missing = analyzer.identify_missing_laws()
    
    if args.generate_llm_context:
        context = analyzer.generate_llm_context(top_n=args.top_n)
        # Save context to file
        context_file = args.output.replace('.json', '_llm_context.md')
        with open(context_file, 'w') as f:
            f.write(context)
        logger.info(f"LLM context saved to {context_file}")
    
    analyzer.export_graph(args.output)


if __name__ == "__main__":
    main()
