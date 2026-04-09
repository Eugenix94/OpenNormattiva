#!/usr/bin/env python3
"""Upload the large JSONL and DB files to the HF Dataset."""
import os
import sys
from pathlib import Path

TOKEN = os.environ.get("HF_TOKEN", "")
if not TOKEN:
    print("No HF_TOKEN"); sys.exit(1)

from huggingface_hub import HfApi
api = HfApi(token=TOKEN)
user = api.whoami()
DATASET_ID = f"{user['name']}/normattiva-data"

# JSONL first (it's the most important one)
jsonl = Path("data/processed/laws_vigente.jsonl")
if jsonl.exists():
    sz = round(jsonl.stat().st_size / 1e6, 1)
    print(f"Uploading JSONL ({sz} MB) to {DATASET_ID}...")
    api.upload_file(
        path_or_fileobj=str(jsonl),
        path_in_repo="data/processed/laws_vigente.jsonl",
        repo_id=DATASET_ID, repo_type="dataset",
        commit_message=f"Add laws JSONL ({sz} MB, 164K laws, 253K citations with full URNs)",
    )
    print("  JSONL uploaded!")
else:
    print(f"JSONL not found: {jsonl}")

# DB
db = Path("data/laws.db")
if db.exists():
    sz = round(db.stat().st_size / 1e6, 1)
    print(f"\nUploading SQLite DB ({sz} MB)...")
    api.upload_file(
        path_or_fileobj=str(db),
        path_in_repo="data/laws.db",
        repo_id=DATASET_ID, repo_type="dataset",
        commit_message=f"Add pre-built SQLite DB ({sz} MB, 157K laws, 193K citations, PageRank)",
    )
    print("  DB uploaded!")
else:
    print(f"DB not found: {db}")

print(f"\nDone! https://huggingface.co/datasets/{DATASET_ID}")
