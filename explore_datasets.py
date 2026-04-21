#!/usr/bin/env python3
"""Explore available datasets and their formats on Normattiva API."""
import json
from normattiva_api_client import NormattivaAPI

api = NormattivaAPI(timeout_s=60, retries=2)

print("=" * 80)
print("NORMATTIVA DATASET EXPLORATION")
print("=" * 80)

# Get all collections
print("\nFetching collection catalogue...")
try:
    catalogue = api.get_collection_catalogue()
    print(f"✓ Found {len(catalogue)} collection variants")
except Exception as e:
    print(f"✗ Error: {e}")
    exit(1)

# Group by collection name
by_name = {}
for c in catalogue:
    name = c.get("nomeCollezione") or c.get("nome", "Unknown")
    if name not in by_name:
        by_name[name] = []
    by_name[name].append(c)

# Get available formats
print("\nFetching available formats...")
try:
    formats = api.get_extensions()
    print(f"✓ Available formats: {formats}")
except Exception as e:
    print(f"✗ Could not fetch formats: {e}")
    formats = []

# Print summary
print(f"\n{'Collection Name':<45} {'O':<3} {'V':<3} {'M':<3} {'Acts':<8}")
print("-" * 80)

collections_summary = []
for name in sorted(by_name.keys()):
    variants = by_name[name]
    has_o = any(v.get("formatoCollezione") == "O" for v in variants)
    has_v = any(v.get("formatoCollezione") == "V" for v in variants)
    has_m = any(v.get("formatoCollezione") == "M" for v in variants)
    
    # Get act count from first variant
    acts = variants[0].get("numeroAtti", "?")
    
    o_mark = "✓" if has_o else "-"
    v_mark = "✓" if has_v else "-"
    m_mark = "✓" if has_m else "-"
    
    print(f"{name:<45} {o_mark:<3} {v_mark:<3} {m_mark:<3} {acts:<8}")
    
    collections_summary.append({
        'name': name,
        'o': has_o,
        'v': has_v,
        'm': has_m,
        'acts': acts
    })

# Analyze missing variants
print("\n" + "=" * 80)
print("MISSING VARIANTS ANALYSIS")
print("=" * 80)

missing_v = [c['name'] for c in collections_summary if c['v'] is False]
missing_o = [c['name'] for c in collections_summary if c['o'] is False]
missing_m = [c['name'] for c in collections_summary if c['m'] is False]

print(f"\nCollections missing VIGENTE (V) variant: {len(missing_v)}")
for name in missing_v:
    print(f"  - {name}")

print(f"\nCollections missing ORIGINALE (O) variant: {len(missing_o)}")
for name in missing_o:
    print(f"  - {name}")

print(f"\nCollections missing MULTIVIGENZA (M) variant: {len(missing_m)}")
for name in missing_m:
    print(f"  - {name}")

# Check if abrogate collection exists
print("\n" + "=" * 80)
print("SPECIAL COLLECTIONS")
print("=" * 80)

abrogate_collections = [name for name in by_name.keys() if 'abrogate' in name.lower() or 'abrogat' in name.lower()]
print(f"\nAbrogate/Abrogated collections found: {len(abrogate_collections)}")
for name in abrogate_collections:
    variants = by_name[name]
    acts_count = variants[0].get("numeroAtti", "?")
    fmt = ", ".join(set(v.get("formatoCollezione", "?") for v in variants))
    print(f"  - {name}: {acts_count} acts, formats: [{fmt}]")

# Summary statistics
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
total_collections = len(by_name)
total_acts = sum(c['acts'] for c in collections_summary if isinstance(c['acts'], int))
print(f"\nTotal unique collections: {total_collections}")
print(f"Total variants: {len(catalogue)}")
print(f"Vigente-only collections: {len([c for c in collections_summary if c['v'] and not c['o'] and not c['m']])}")
print(f"Collections with all 3 variants: {len([c for c in collections_summary if c['o'] and c['v'] and c['m']])}")
