#!/usr/bin/env python3
"""Check available Normattiva API collections and find Constitution."""
import sys
sys.path.insert(0, '.')
from normattiva_api_client import NormattivaAPI

api = NormattivaAPI()

print('Fetching collection catalogue...')
try:
    collections = api.get_collection_catalogue()
    print(f'Total entries: {len(collections)}')
    
    # Show unique collection names with variant
    names = {}
    for c in collections:
        n = c.get('nomeCollezione', c.get('nome', '?'))
        fmt = c.get('formatoCollezione', c.get('formato', '?'))
        count = c.get('numeroAtti', 0)
        if n not in names:
            names[n] = {}
        names[n][fmt] = count
    
    print(f'\nUnique collections: {len(names)}')
    print('\nAll collections:')
    for name in sorted(names.keys()):
        variants = ', '.join(f"{k}:{v}" for k,v in sorted(names[name].items()))
        print(f'  {name:<50} {variants}')
        
except Exception as e:
    print(f'ERROR: {e}')
    import traceback
    traceback.print_exc()
