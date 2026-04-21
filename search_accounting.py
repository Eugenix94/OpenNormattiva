#!/usr/bin/env python3
"""
Legal Accounting Research Helper
─────────────────────────────────
Search the Normattiva database for laws relevant to legal accounting analysis.
Supports full-text search, domain filtering, and citation exploration.

Usage:
    py search_accounting.py                    # interactive mode
    py search_accounting.py "query terms"      # direct search
    py search_accounting.py --urn URN          # view law + citations
    py search_accounting.py --top-tax          # top tax/fiscal laws by importance
"""
import sys, os, json, argparse, textwrap
sys.path.insert(0, os.path.dirname(__file__))
from core.db import LawDatabase

DB_PATH = "data/laws.db"

# ── Preset queries for legal accounting ─────────────────────────────────────
PRESETS = {
    "contabilita":     "contabilità contabile revisione bilancio",
    "fiscale":         "imposta fiscale tributario reddito IVA IRES IRAP",
    "bilancio":        "bilancio esercizio conto economico stato patrimoniale",
    "revisore":        "revisore legale revisione contabile certificazione",
    "fallimento":      "fallimentare fallimento concordato liquidazione insolvenza",
    "societa":         "società azioni responsabilità limitata capitale sociale",
    "antiriciclaggio": "antiriciclaggio riciclaggio segnalazione sospetta",
    "codice_civile":   "codice civile obbligazione contratto responsabilità",
    "finanza":         "finanza pubblica debito bilancio statale",
    "sanzioni":        "sanzione amministrativa penale tributaria violazione",
}

def fmt_law(r, show_text=False):
    lines = []
    title = r.get('title', 'N/A')
    lines.append(f"  URN:   {r['urn']}")
    lines.append(f"  Title: {title[:120]}")
    lines.append(f"  Type:  {r.get('type','?')}  |  Date: {r.get('date','?')}  |  Year: {r.get('year','?')}")
    score = r.get('importance_score') or 0
    inc = r.get('citation_count_incoming') or 0
    out = r.get('citation_count_outgoing') or 0
    lines.append(f"  Importance: {score:.2f}  |  Cited by: {inc}  |  Cites: {out}")
    if show_text:
        text = r.get('text', '')
        if text:
            lines.append(f"  Text preview: {text[:300]}...")
    return "\n".join(lines)

def search(db, query, limit=20, domain=None):
    results = db.search_fts(query, limit=limit)
    if domain:
        results = [r for r in results if r.get('domain_cluster') == domain]
    print(f"\n{'='*80}")
    print(f"  Search: \"{query}\"  ({len(results)} results)")
    if domain:
        print(f"  Domain filter: {domain}")
    print(f"{'='*80}")
    for i, r in enumerate(results, 1):
        print(f"\n  [{i}]")
        print(fmt_law(r))
    return results

def view_law(db, urn):
    law = db.get_law(urn)
    if not law:
        print(f"Law not found: {urn}")
        return
    print(f"\n{'='*80}")
    print(f"  LAW DETAIL")
    print(f"{'='*80}")
    print(fmt_law(dict(law), show_text=True))

    # Citations
    outgoing = db.get_citations_outgoing(urn)
    incoming = db.get_citations_incoming(urn)
    if outgoing:
        print(f"\n  --- Cites ({len(outgoing)} laws) ---")
        for c in outgoing[:15]:
            print(f"    -> {c['cited_urn']}")
    if incoming:
        print(f"\n  --- Cited by ({len(incoming)} laws) ---")
        for c in incoming[:15]:
            print(f"    <- {c['citing_urn']}")

def top_tax(db, n=30):
    rows = db.conn.execute('''
        SELECT l.urn, l.title, l.type, l.date, l.year, l.importance_score,
               m.citation_count_incoming, m.citation_count_outgoing, m.domain_cluster
        FROM laws l
        LEFT JOIN law_metadata m ON l.urn = m.urn
        WHERE m.domain_cluster = 'diritto_tributario'
        ORDER BY COALESCE(m.citation_count_incoming, 0) DESC, l.importance_score DESC
        LIMIT ?
    ''', (n,)).fetchall()
    print(f"\n{'='*80}")
    print(f"  TOP {n} TAX/FISCAL LAWS BY IMPORTANCE")
    print(f"{'='*80}")
    for i, r in enumerate(rows, 1):
        title = (r[1] or '')[:80]
        score = r[5] or 0
        inc = r[6] or 0
        print(f"  [{i:>2}] score={score:>6.2f}  cited={inc:>5}  {r[2]} {r[4]}  {title}")

def interactive(db):
    print("\n" + "="*80)
    print("  NORMATTIVA LEGAL ACCOUNTING RESEARCH")
    print("  157,121 Italian laws  |  FTS5 full-text  |  PageRank importance")
    print("="*80)
    print("\n  Preset searches (type the keyword):")
    for k, v in PRESETS.items():
        print(f"    {k:20s} -> {v}")
    print(f"\n  Commands:")
    print(f"    <query>          Free-text search")
    print(f"    urn:<URN>        View law detail + citations")
    print(f"    top              Top tax/fiscal laws")
    print(f"    q                Quit")

    while True:
        try:
            q = input("\n  search> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q == 'q':
            break
        if q in PRESETS:
            search(db, PRESETS[q])
        elif q.startswith("urn:"):
            view_law(db, q)
        elif q == "top":
            top_tax(db)
        else:
            search(db, q)

def main():
    parser = argparse.ArgumentParser(description="Legal accounting law search")
    parser.add_argument("query", nargs="?", help="Search query (or use interactive mode)")
    parser.add_argument("--urn", help="View a specific law by URN")
    parser.add_argument("--top-tax", action="store_true", help="Show top tax/fiscal laws")
    parser.add_argument("--limit", type=int, default=20, help="Max results")
    parser.add_argument("--domain", help="Filter by domain (e.g. diritto_tributario)")
    args = parser.parse_args()

    db = LawDatabase(DB_PATH)

    if args.urn:
        view_law(db, args.urn)
    elif args.top_tax:
        top_tax(db)
    elif args.query:
        search(db, args.query, limit=args.limit, domain=args.domain)
    else:
        interactive(db)

    db.close()

if __name__ == "__main__":
    main()
