#!/usr/bin/env python3
"""
Resolve citation URNs to actual law URNs in the database.

Problem: parse_akn.py generates citations like:
    urn:nir:stato:legge:1985;77
But actual law URNs in DB are:
    urn:nir:stato:legge:1985-03-07;77

This script builds a lookup index and resolves citations.
"""
import sqlite3
import re
import time
from collections import defaultdict

DB_PATH = "data/laws.db"


def build_lookup_index(db):
    """Build (type, year, number) → actual_urn index from laws table."""
    print("  Building URN lookup index...")
    t0 = time.time()

    index = {}  # (type_pattern, year, number) → urn
    
    cursor = db.execute("SELECT urn FROM laws")
    for row in cursor:
        urn = row[0]
        if not urn or not urn.startswith("urn:nir:"):
            continue
        
        # Parse: urn:nir:AUTHORITY:TYPE:DATE;NUMBER
        parts = urn.split(":")
        if len(parts) < 5:
            continue
        
        type_part = parts[3]  # e.g., "legge", "decreto.legislativo", etc.
        id_part = parts[4]     # e.g., "1985-03-07;77"
        
        if ";" not in id_part:
            continue
        
        date_str, number = id_part.split(";", 1)
        
        # Extract year from date
        year = date_str[:4] if len(date_str) >= 4 else date_str
        
        key = (type_part, year, number)
        # Prefer shorter/simpler authority
        if key not in index:
            index[key] = urn
    
    print(f"  Index built: {len(index):,} entries ({time.time()-t0:.1f}s)")
    return index


def resolve_citations(db, index):
    """Update citations table to use actual law URNs for cited_urn."""
    print("  Resolving citations...")
    t0 = time.time()

    # Get all citations
    citations = db.execute("SELECT citing_urn, cited_urn, count, context FROM citations").fetchall()
    print(f"  Total citations to resolve: {len(citations):,}")

    resolved = 0
    already_valid = 0
    unresolved = 0
    updates = []

    for citing_urn, cited_urn, count, context in citations:
        # Check if cited_urn already exists in laws
        # (We skip this check for speed - we know from diagnosis that 0 match)
        
        # Parse cited_urn
        parts = cited_urn.split(":")
        if len(parts) < 5:
            unresolved += 1
            continue
        
        type_part = parts[3]
        id_part = parts[4]
        
        if ";" not in id_part:
            unresolved += 1
            continue
        
        year_str, number = id_part.split(";", 1)
        year = year_str[:4]
        
        # Look up in index
        key = (type_part, year, number)
        actual_urn = index.get(key)
        
        if actual_urn and actual_urn != cited_urn:
            updates.append((citing_urn, cited_urn, actual_urn, count, context))
            resolved += 1
        else:
            unresolved += 1

    print(f"  Resolved: {resolved:,} | Unresolved: {unresolved:,}")

    if not updates:
        print("  No updates needed.")
        return resolved

    # Apply updates: delete old, insert new
    print(f"  Applying {len(updates):,} citation updates...")
    batch_size = 5000
    for i in range(0, len(updates), batch_size):
        batch = updates[i:i+batch_size]
        for citing_urn, old_cited, new_cited, count, context in batch:
            # Remove old citation
            db.execute(
                "DELETE FROM citations WHERE citing_urn = ? AND cited_urn = ?",
                (citing_urn, old_cited)
            )
            # Insert resolved citation (may already exist from another ref)
            db.execute(
                "INSERT OR IGNORE INTO citations (citing_urn, cited_urn, count, context) VALUES (?, ?, ?, ?)",
                (citing_urn, new_cited, count, context)
            )
        db.commit()
        if (i + batch_size) % 20000 == 0:
            print(f"    {i+batch_size:,}/{len(updates):,}...")

    db.commit()
    elapsed = time.time() - t0
    print(f"  Resolved {resolved:,} citations in {elapsed:.1f}s")
    return resolved


def recompute_counts(db):
    """Recompute citation counts after resolution."""
    print("  Recomputing citation counts...")
    t0 = time.time()
    
    # Count valid citations (both sides exist in laws)
    valid = db.execute("""
        SELECT COUNT(*) FROM citations 
        WHERE cited_urn IN (SELECT urn FROM laws)
        AND citing_urn IN (SELECT urn FROM laws)
    """).fetchone()[0]
    print(f"  Valid citations (both ends in DB): {valid:,}")

    # Update law_metadata with incoming/outgoing counts
    db.execute("""
        INSERT OR REPLACE INTO law_metadata (urn, citation_count_incoming, citation_count_outgoing,
            pagerank, domain_cluster, keywords)
        SELECT l.urn,
               COALESCE(ci.cnt, 0),
               COALESCE(co.cnt, 0),
               COALESCE(m.pagerank, 0),
               m.domain_cluster,
               m.keywords
        FROM laws l
        LEFT JOIN (SELECT cited_urn, COUNT(*) cnt FROM citations 
                   WHERE cited_urn IN (SELECT urn FROM laws) GROUP BY cited_urn) ci 
            ON ci.cited_urn = l.urn
        LEFT JOIN (SELECT citing_urn, COUNT(*) cnt FROM citations 
                   WHERE citing_urn IN (SELECT urn FROM laws) GROUP BY citing_urn) co 
            ON co.citing_urn = l.urn
        LEFT JOIN law_metadata m ON m.urn = l.urn
    """)
    db.commit()

    result = db.execute("SELECT COUNT(*) FROM law_metadata WHERE citation_count_incoming > 0").fetchone()[0]
    print(f"  Laws with incoming citations: {result:,}")
    
    # Show top cited
    top = db.execute("""
        SELECT l.title, m.citation_count_incoming 
        FROM law_metadata m JOIN laws l ON l.urn = m.urn
        WHERE m.citation_count_incoming > 0 
        ORDER BY m.citation_count_incoming DESC LIMIT 10
    """).fetchall()
    print(f"  Top 10 most cited laws:")
    for t in top:
        print(f"    [{t[1]:>4}] {t[0][:65]}")
    
    elapsed = time.time() - t0
    print(f"  Done ({elapsed:.1f}s)")


def main():
    print("=" * 70)
    print("  CITATION RESOLUTION")
    print("=" * 70)

    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("PRAGMA journal_mode = WAL")

    index = build_lookup_index(db)
    resolved = resolve_citations(db, index)
    
    if resolved > 0:
        recompute_counts(db)
    else:
        print("  Trying direct recount anyway...")
        recompute_counts(db)

    # Final stats
    total_cit = db.execute("SELECT COUNT(*) FROM citations").fetchone()[0]
    valid_cit = db.execute("""
        SELECT COUNT(*) FROM citations 
        WHERE cited_urn IN (SELECT urn FROM laws)
    """).fetchone()[0]
    print(f"\n  Final: {total_cit:,} total citations, {valid_cit:,} resolved to known laws")

    db.close()
    print("\n  Done!")


if __name__ == "__main__":
    main()
