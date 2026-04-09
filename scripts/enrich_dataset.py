#!/usr/bin/env python3
"""
Enrich Dataset: PageRank, Domain Detection, Validation, Export

Run this AFTER auto_build_data.py has populated the database.
Adds intelligence layers on top of raw data.

Usage:
    python scripts/enrich_dataset.py              # Run all enrichments
    python scripts/enrich_dataset.py --pagerank    # Just importance scores
    python scripts/enrich_dataset.py --domains     # Just domain detection
    python scripts/enrich_dataset.py --validate    # Just validation
    python scripts/enrich_dataset.py --export      # Export CSV + graph JSON
"""

import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.db import LawDatabase

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def run_enrichment(db_path: Path = Path('data/laws.db'),
                   do_pagerank: bool = True,
                   do_domains: bool = True,
                   do_validate: bool = True,
                   do_export: bool = True):
    """Run all enrichment stages."""
    
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        logger.error("Run auto_build_data.py first")
        return 1
    
    db = LawDatabase(db_path)
    stats = db.get_statistics()
    logger.info(f"Database: {stats.get('total_laws', 0)} laws, {stats.get('total_citations', 0)} citations")
    
    if stats.get('total_laws', 0) == 0:
        logger.error("Database is empty. Load data first.")
        db.close()
        return 1
    
    results = {}
    
    # Stage 1: Citation counts
    if do_pagerank:
        logger.info("\n" + "=" * 60)
        logger.info("STAGE 1: Computing citation counts")
        logger.info("=" * 60)
        db.compute_citation_counts()
        
        logger.info("\nSTAGE 2: Computing importance scores (PageRank)")
        db.compute_importance_scores(iterations=25)
        
        # Show top 10 most important laws
        top = db.get_most_cited_laws(limit=10)
        logger.info("\nTop 10 most cited laws:")
        for i, law in enumerate(top, 1):
            logger.info(f"  {i}. [{law['citation_count']} citations] {law['title'][:60]}")
        results['pagerank'] = True
    
    # Stage 2: Domain detection
    if do_domains:
        logger.info("\n" + "=" * 60)
        logger.info("STAGE 3: Detecting legal domains")
        logger.info("=" * 60)
        db.detect_law_domains()
        
        # Show domain distribution
        stats_updated = db.get_statistics()
        domains = stats_updated.get('by_domain', {})
        if domains:
            logger.info("\nDomain distribution:")
            for domain, count in sorted(domains.items(), key=lambda x: x[1], reverse=True):
                logger.info(f"  {domain}: {count} laws")
        results['domains'] = True
    
    # Stage 3: Validation
    if do_validate:
        logger.info("\n" + "=" * 60)
        logger.info("STAGE 4: Data validation")
        logger.info("=" * 60)
        
        report = db.validate_data()
        logger.info(f"\nValidation status: {report['status']}")
        logger.info(f"Total laws: {report['total_laws']}")
        logger.info(f"Orphan citations: {report.get('orphan_citations', 0)}")
        
        for check, passed in report['checks'].items():
            status = "PASS" if passed else "WARN"
            logger.info(f"  [{status}] {check}")
        
        if report['issues']:
            for issue in report['issues']:
                logger.warning(f"  Issue: {issue}")
        
        # Save report
        report_file = Path('data/indexes/validation_report.json')
        report_file.parent.mkdir(parents=True, exist_ok=True)
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2)
        logger.info(f"Report saved: {report_file}")
        results['validation'] = report
    
    # Stage 4: Export
    if do_export:
        logger.info("\n" + "=" * 60)
        logger.info("STAGE 5: Exporting data")
        logger.info("=" * 60)
        
        export_dir = Path('data/exports')
        export_dir.mkdir(parents=True, exist_ok=True)
        
        # CSV export
        csv_file = export_dir / 'laws_summary.csv'
        db.export_csv(csv_file)
        
        # Graph JSON export
        graph_file = export_dir / 'citation_graph.json'
        db.export_graph_json(graph_file, min_citations=1)
        
        results['exports'] = {
            'csv': str(csv_file),
            'graph': str(graph_file),
        }
    
    # Final summary
    logger.info("\n" + "=" * 60)
    logger.info("ENRICHMENT COMPLETE")
    logger.info("=" * 60)
    
    final_stats = db.get_statistics()
    logger.info(f"  Total laws: {final_stats.get('total_laws', 0):,}")
    logger.info(f"  Total citations: {final_stats.get('total_citations', 0):,}")
    logger.info(f"  Total amendments: {final_stats.get('total_amendments', 0):,}")
    logger.info(f"  Law types: {len(final_stats.get('by_type', {}))}")
    logger.info(f"  Domains detected: {len(final_stats.get('by_domain', {}))}")
    
    db.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description='Enrich dataset with intelligence layers')
    parser.add_argument('--pagerank', action='store_true', help='Compute importance scores')
    parser.add_argument('--domains', action='store_true', help='Detect legal domains')
    parser.add_argument('--validate', action='store_true', help='Run validation checks')
    parser.add_argument('--export', action='store_true', help='Export CSV + graph')
    parser.add_argument('--db', default='data/laws.db', help='Database path')
    
    args = parser.parse_args()
    
    # If no flags, run everything
    run_all = not any([args.pagerank, args.domains, args.validate, args.export])
    
    return run_enrichment(
        db_path=Path(args.db),
        do_pagerank=args.pagerank or run_all,
        do_domains=args.domains or run_all,
        do_validate=args.validate or run_all,
        do_export=args.export or run_all,
    )


if __name__ == '__main__':
    sys.exit(main())
