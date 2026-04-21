#!/usr/bin/env python3
"""
Fix HF dataset by removing empty placeholder files and re-uploading
"""
import os
from pathlib import Path
from huggingface_hub import HfApi, RepoUrl
import time

HF_TOKEN = os.environ.get('HF_TOKEN')
if not HF_TOKEN:
    print("ERROR: HF_TOKEN env var not set")
    exit(1)

api = HfApi(token=HF_TOKEN)
repo_id = 'diatribe00/normattiva-data'
repo_type = 'dataset'

print("Step 1: Delete existing 0-byte files from HF")
print("-" * 60)

files_to_delete = [
    'data/laws.db',
    'data/laws_summary.csv',
    'data/processed/laws_vigente.jsonl',
    'data/citation_graph.json',
    'test_upload.txt',
]

for path in files_to_delete:
    try:
        print(f"Deleting {path}...")
        api.delete_file(
            path_in_repo=path,
            repo_id=repo_id,
            repo_type=repo_type,
            commit_message=f"Remove empty placeholder: {path}"
        )
        time.sleep(1)  # Rate limiting
    except Exception as e:
        print(f"  (Skipping: {str(e)[:100]})")

print("\nStep 2: Upload fresh data files")
print("-" * 60)

files_to_upload = [
    ('data/processed/laws_vigente.jsonl', 'data/processed/laws_vigente.jsonl'),
    ('data/laws.db', 'data/laws.db'),
    ('data/laws_summary.csv', 'data/laws_summary.csv'),
    ('data/citation_graph.json', 'data/citation_graph.json'),
]

for local_path, remote_path in files_to_upload:
    p = Path(local_path)
    if not p.exists():
        print(f"❌ {local_path}: NOT FOUND")
        continue
    
    size_mb = p.stat().st_size / 1e6
    print(f"\n⬆️  Uploading {local_path} ({size_mb:.1f} MB)...")
    
    try:
        api.upload_file(
            path_or_fileobj=str(p),
            path_in_repo=remote_path,
            repo_id=repo_id,
            repo_type=repo_type,
            commit_message=f'Upload: {remote_path}',
        )
        print(f"✅ Success!")
        time.sleep(2)  # Rate limiting
    except Exception as e:
        print(f"❌ ERROR: {str(e)[:150]}")

print("\n" + "=" * 60)
print("Upload complete!")
print(f"Dataset: https://huggingface.co/datasets/{repo_id}")
print("Checking file sizes...")

time.sleep(5)

# Verify
import requests
r = requests.get(f'https://huggingface.co/api/datasets/{repo_id}', timeout=10)
if r.status_code == 200:
    ds = r.json()
    files = ds.get('siblings', [])
    print(f"\nFiles on HF ({len(files)}):")
    for f in sorted(files, key=lambda x: x.get('rfilename', '')):
        fname = f.get('rfilename', '?')
        fsize = f.get('size', 0)
        if fsize > 1e9:
            status = "✓" if fsize > 1e6 else "✗"
            print(f"  {status} {fname}: {fsize/1e9:.2f} GB")
        elif fsize > 1e6:
            print(f"  ✓ {fname}: {fsize/1e6:.2f} MB")
        elif fsize > 0:
            print(f"  ✓ {fname}: {fsize} bytes")
        else:
            print(f"  ✗ {fname}: 0 bytes (EMPTY)")
