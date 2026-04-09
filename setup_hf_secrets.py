#!/usr/bin/env python3
"""Set HF_TOKEN secret in the Space and create/upload the Dataset."""
import os
import sys
import shutil
import tempfile
import requests
from pathlib import Path

TOKEN = os.environ.get("HF_TOKEN", "")
if not TOKEN:
    print("No HF_TOKEN"); sys.exit(1)

from huggingface_hub import HfApi
api = HfApi(token=TOKEN)
user = api.whoami()
username = user["name"]
SPACE_ID   = f"{username}/normattiva-search"
DATASET_ID = f"{username}/normattiva-data"

# ── 1. Set HF_TOKEN secret in Space ─────────────────────────────────────────
print(f"Setting HF_TOKEN secret in {SPACE_ID}...")
r = requests.post(
    f"https://huggingface.co/api/spaces/{SPACE_ID}/secrets",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"key": "HF_TOKEN", "value": TOKEN},
)
if r.ok:
    print(f"  Secret set (status {r.status_code})")
else:
    print(f"  Failed to set secret: {r.status_code} {r.text[:200]}")

# ── 2. Create Dataset repo ───────────────────────────────────────────────────
print(f"\nCreating dataset repo {DATASET_ID}...")
try:
    api.create_repo(repo_id=DATASET_ID, repo_type="dataset", private=False, exist_ok=True)
    print("  Dataset repo ready")
except Exception as e:
    print(f"  Note: {e}")

# Upload dataset card README
staging = Path(tempfile.mkdtemp(prefix="normattiva_ds_"))
try:
    card = (
        "---\n"
        "license: mit\n"
        "language:\n  - it\n"
        "tags:\n  - legal\n  - italian-law\n  - normattiva\n"
        "size_categories:\n  - 100K<n<1M\n"
        "---\n\n"
        "# OpenNormattiva Dataset\n\n"
        "160,000+ Italian laws from [Normattiva](https://www.normattiva.it/).\n\n"
        "## Files\n\n"
        "| File | Description |\n"
        "|------|-------------|\n"
        "| `data/processed/laws_vigente.jsonl` | All laws as JSONL, one per line |\n"
        "| `data/laws.db` | Pre-built SQLite + FTS5 database |\n"
        "| `data/laws_summary.csv` | Summary CSV for quick exploration |\n"
        "| `data/citation_graph.json` | Citation graph export |\n\n"
        "## Schema (per law)\n\n"
        "```json\n"
        "{\n"
        '  "urn": "urn:nir:stato:legge:2006;290",\n'
        '  "title": "...",\n'
        '  "type": "legge",\n'
        '  "date": "2006-12-27",\n'
        '  "citations": [\n'
        '    {"target_urn": "urn:nir:stato:decreto.legislativo:2016;50",\n'
        '     "ref": "d.lgs. 50/2016", "article": "1"}\n'
        "  ]\n"
        "}\n"
        "```\n\n"
        "Updated nightly by GitHub Actions.\n"
    )
    (staging / "README.md").write_text(card, encoding="utf-8")
    api.upload_folder(
        repo_id=DATASET_ID, repo_type="dataset",
        folder_path=str(staging),
        commit_message="Add dataset card",
    )
    print("  Dataset card uploaded")
finally:
    shutil.rmtree(staging, ignore_errors=True)

# ── 3. Upload data files ─────────────────────────────────────────────────────
files = [
    ("data/laws_summary.csv",        "data/laws_summary.csv"),
    ("data/citation_graph.json",     "data/citation_graph.json"),
]
for local, repo_path in files:
    p = Path(local)
    if p.exists():
        sz = round(p.stat().st_size / 1e6, 1)
        print(f"\nUploading {local} ({sz} MB)...")
        api.upload_file(
            path_or_fileobj=str(p),
            path_in_repo=repo_path,
            repo_id=DATASET_ID, repo_type="dataset",
            commit_message=f"Add {repo_path}",
        )
        print(f"  Done: {repo_path}")

print("\nSmall files uploaded. Run upload_large_files.py for JSONL + DB.")
print(f"\nSpace:   https://huggingface.co/spaces/{SPACE_ID}")
print(f"Dataset: https://huggingface.co/datasets/{DATASET_ID}")
