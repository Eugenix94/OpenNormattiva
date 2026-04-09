#!/usr/bin/env python3
"""Redeploy Space with XML fallback support."""
from huggingface_hub import HfApi

import os
HF_TOKEN = os.environ.get("HF_TOKEN", "")
SPACE_REPO = "diatribe00/normattiva-search"

api = HfApi(token=HF_TOKEN)

print("Uploading updated app.py with XML fallback...")
api.upload_file(
    path_or_fileobj="space/app.py",
    path_in_repo="app.py",
    repo_id=SPACE_REPO,
    repo_type="space",
    commit_message="Pipeline: Add XML format fallback (AKN first, then XML)",
)
print("  ✓ Uploaded")

print("Restarting Space...")
api.restart_space(repo_id=SPACE_REPO)
print("  ✓ Space restarting")

print("\nSpace will catch the 3 failed collections on next re-run.")
print("https://huggingface.co/spaces/diatribe00/normattiva-search")
