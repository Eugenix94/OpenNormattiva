#!/usr/bin/env python3
"""
Manually upload local data files to HuggingFace Dataset.
This fixes the issue where April 10 pipeline uploaded empty files.
"""
import os
from pathlib import Path
from huggingface_hub import HfApi

HF_TOKEN = os.environ.get('HF_TOKEN')
if not HF_TOKEN:
    print("ERROR: HF_TOKEN not set")
    print("Set it with: $env:HF_TOKEN = 'hf_YOUR_TOKEN'")
    exit(1)

api = HfApi(token=HF_TOKEN)
repo_id = 'diatribe00/normattiva-data'
repo_type = 'dataset'

files_to_upload = [
    ('data/processed/laws_vigente.jsonl', 'data/processed/laws_vigente.jsonl'),
    ('data/laws.db', 'data/laws.db'),
    ('data/laws_summary.csv', 'data/laws_summary.csv'),
    ('data/citation_graph.json', 'data/citation_graph.json'),
]

print(f"Uploading to {repo_id}...\n")

for local_path, remote_path in files_to_upload:
    p = Path(local_path)
    if not p.exists():
        print(f"❌ {local_path}: NOT FOUND")
        continue
    
    size_mb = p.stat().st_size / 1e6
    print(f"⬆️  {local_path} ({size_mb:.1f} MB) → {remote_path}")
    
    try:
        api.upload_file(
            path_or_fileobj=str(p),
            path_in_repo=remote_path,
            repo_id=repo_id,
            repo_type=repo_type,
            commit_message=f'Fix: upload {remote_path}',
        )
        print(f"✅ Uploaded successfully\n")
    except Exception as e:
        print(f"❌ ERROR: {e}\n")

print("Upload complete!")
print(f"Dataset: https://huggingface.co/datasets/{repo_id}")
