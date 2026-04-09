#!/usr/bin/env python3
"""Quick integration test for all enhanced features."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(__file__))

from core.db import LawDatabase

db = LawDatabase(':memory:')

# Insert test laws
db.insert_law({
    'urn': 'urn:nir:stato:legge:2024-01-01;1',
    'title': 'Legge sulla protezione dei diritti fondamentali',
    'type': 'legge', 'date': '2024-01-01', 'year': '2024',
    'status': 'vigente', 'text': 'Articolo 1. I diritti fondamentali sono garantiti dalla Costituzione.',
    'article_count': 5, 'text_length': 100,
    'citations': [{'target_urn': 'urn:nir:stato:legge:1947;cost', 'context': 'costituzione'}],
})
db.insert_law({
    'urn': 'urn:nir:stato:legge:1947;cost',
    'title': 'Costituzione della Repubblica Italiana',
    'type': 'costituzione', 'date': '1947-12-22', 'year': '1947',
    'status': 'vigente', 'text': 'La Repubblica riconosce e garantisce i diritti inviolabili.',
    'article_count': 139, 'text_length': 50000,
    'citations': [],
})
db.insert_law({
    'urn': 'urn:nir:stato:decreto.legge:2020-03-25;19',
    'title': 'Misure urgenti per fronteggiare emergenza epidemiologica da COVID-19',
    'type': 'decreto.legge', 'date': '2020-03-25', 'year': '2020',
    'status': 'vigente',
    'text': 'Il Presidente del Consiglio dei ministri adotta misure per la sanita pubblica e la sicurezza.',
    'article_count': 8, 'text_length': 5000,
    'citations': [
        {'target_urn': 'urn:nir:stato:legge:1947;cost', 'context': 'costituzione'},
        {'target_urn': 'urn:nir:stato:legge:2024-01-01;1', 'context': 'diritti'}
    ],
})

print("=== FTS Search ===")
results = db.search_fts('diritti fondamentali', limit=5)
print(f"  Found {len(results)} results")
for r in results:
    print(f"  - {r['title']}")

print("\n=== Citation Counts ===")
db.compute_citation_counts()
meta = db.conn.execute('SELECT urn, citation_count_incoming, citation_count_outgoing FROM law_metadata').fetchall()
for m in meta:
    print(f"  {m[0]}: in={m[1]}, out={m[2]}")

print("\n=== PageRank ===")
db.compute_importance_scores()
pr = db.conn.execute('SELECT urn, pagerank FROM law_metadata WHERE pagerank IS NOT NULL ORDER BY pagerank DESC').fetchall()
for p in pr:
    print(f"  {p[0]}: {p[1]:.6f}")

print("\n=== Domain Detection ===")
db.detect_law_domains()
domains = db.conn.execute('SELECT urn, domain_cluster FROM law_metadata WHERE domain_cluster IS NOT NULL').fetchall()
for d in domains:
    print(f"  {d[0]}: {d[1]}")

print("\n=== Citation Neighborhood ===")
neighborhood = db.get_citation_neighborhood('urn:nir:stato:legge:2024-01-01;1', depth=2, max_nodes=20)
print(f"  {len(neighborhood['nodes'])} nodes, {len(neighborhood['edges'])} edges")

print("\n=== Related Laws (co-citation) ===")
related = db.find_related_laws('urn:nir:stato:decreto.legge:2020-03-25;19', limit=5)
print(f"  {len(related)} related laws found")
for r in related:
    print(f"  - {r['urn']}: shared={r.get('shared', 0)}")

print("\n=== Validation ===")
report = db.validate_data()
print(f"  {report['total_laws']} laws, {len(report['issues'])} issues")

print("\n=== Export CSV ===")
csv_path = db.export_csv(os.path.join(tempfile.gettempdir(), 'test_export.csv'))
print(f"  Exported to {csv_path}")

print("\n=== Export Graph JSON ===")
graph_path = db.export_graph_json(os.path.join(tempfile.gettempdir(), 'test_graph.json'))
print(f"  Exported to {graph_path}")

db.close()
print("\n=== ALL TESTS PASSED ===")
