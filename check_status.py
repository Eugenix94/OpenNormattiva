#!/usr/bin/env python3
import requests
from datetime import datetime

print('=== HUGGINGFACE SPACE STATUS ===')
try:
    r = requests.get('https://huggingface.co/api/spaces/diatribe00/normattiva-search', timeout=5)
    if r.status_code == 200:
        space = r.json()
        stage = space.get('runtime', {}).get('stage', 'UNKNOWN')
        sdk = space.get('sdk', 'unknown')
        updated = space.get('last_modified', 'unknown')
        print(f'Stage: {stage}')
        print(f'SDK: {sdk}')
        print(f'Updated: {updated}')
    else:
        print(f'Error: {r.status_code}')
except Exception as e:
    print(f'Error: {e}')

print()
print('=== HUGGINGFACE DATASET STATUS ===')
try:
    r = requests.get('https://huggingface.co/api/datasets/diatribe00/normattiva-data', timeout=5)
    if r.status_code == 200:
        ds = r.json()
        updated = ds.get('last_modified', 'unknown')
        print(f'Updated: {updated}')
        files = ds.get('siblings', [])
        print(f'Files ({len(files)}):')
        for f in sorted(files, key=lambda x: x.get('rfilename', ''))[:15]:
            fname = f.get('rfilename', '?')
            fsize = f.get('size', 0)
            if fsize > 1e9:
                print(f'  - {fname} ({fsize/1e9:.1f} GB)')
            elif fsize > 1e6:
                print(f'  - {fname} ({fsize/1e6:.1f} MB)')
            else:
                print(f'  - {fname}')
    else:
        print(f'Error: {r.status_code}')
except Exception as e:
    print(f'Error: {e}')

print()
print('=== GITHUB ACTIONS WORKFLOW STATUS ===')
try:
    r = requests.get('https://api.github.com/repos/Eugenix94/OpenNormattiva/actions/workflows/nightly-update.yml/runs', timeout=5)
    if r.status_code == 200:
        data = r.json()
        runs = data.get('workflow_runs', [])
        if runs:
            latest = runs[0]
            print(f'Latest run: {latest.get("name")}')
            print(f'Status: {latest.get("status")}')
            print(f'Conclusion: {latest.get("conclusion")}')
            print(f'Created: {latest.get("created_at")}')
            print(f'Updated: {latest.get("updated_at")}')
            for i, run in enumerate(runs[:5]):
                print(f'  [{i}] {run.get("created_at")} - {run.get("status")} ({run.get("conclusion")})')
        else:
            print('No runs found')
    else:
        print(f'Error: {r.status_code}')
except Exception as e:
    print(f'Error: {e}')
