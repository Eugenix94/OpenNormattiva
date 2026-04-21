#!/usr/bin/env python3
"""Pre-deployment file check."""
from pathlib import Path

files = [
    'space/app.py',
    'download_db.py',
    'normattiva_api_client.py',
    'parse_akn.py',
    'core/db.py',
    'core/__init__.py',
    'core/legislature.py',
    'core/changelog.py',
    'data/laws.db',
    'data/processed/laws_vigente.jsonl',
]

for f in files:
    p = Path(f)
    if p.exists():
        size = p.stat().st_size / 1e6
        print(f'  OK: {f} ({size:.1f}MB)')
    else:
        print(f'  MISSING: {f}')
