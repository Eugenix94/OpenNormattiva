#!/usr/bin/env python3
import requests
import json

# Get recent workflow runs
print("=== GITHUB ACTIONS WORKFLOW RUNS ===\n")
r = requests.get(
    'https://api.github.com/repos/Eugenix94/OpenNormattiva/actions/workflows/nightly-update.yml/runs?per_page=10',
    timeout=10
)

if r.status_code == 200:
    data = r.json()
    runs = data.get('workflow_runs', [])
    
    for i, run in enumerate(runs[:3]):
        print(f"\n[{i}] {run['created_at']}")
        print(f"    Status: {run['status']}")
        print(f"    Conclusion: {run['conclusion']}")
        print(f"    Logs URL: {run['logs_url']}")
        print(f"    Run ID: {run['id']}")
        
        # Fetch logs
        try:
            logs_r = requests.get(run['logs_url'], timeout=10)
            if logs_r.status_code == 200:
                logs_text = logs_r.text
                # Extract key lines
                lines = logs_text.split('\n')
                print(f"    Last 30 lines of logs:")
                for line in lines[-30:]:
                    if line.strip() and ('error' in line.lower() or 'failed' in line.lower() or 'upload' in line.lower() or 'complete' in line.lower()):
                        print(f"      > {line[:120]}")
        except Exception as e:
            print(f"    Error fetching logs: {e}")

print("\n\n=== HF DATASET FILES ===")
r = requests.get('https://huggingface.co/api/datasets/diatribe00/normattiva-data', timeout=5)
if r.status_code == 200:
    ds = r.json()
    files = ds.get('siblings', [])
    for f in sorted(files, key=lambda x: x.get('rfilename', '')):
        fname = f.get('rfilename', '?')
        fsize = f.get('size', 0)
        if fsize > 1e9:
            print(f'  {fname}: {fsize/1e9:.2f} GB')
        elif fsize > 1e6:
            print(f'  {fname}: {fsize/1e6:.2f} MB')
        else:
            print(f'  {fname}: {fsize} bytes')
