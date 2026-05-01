#!/usr/bin/env python3
"""Helper: push per-space README templates to Hugging Face Spaces.

Usage:
  Set HF_TOKEN in the environment, then run:
    python hf_update_readmes.py

This script is optional — `deploy_hf.py` will automatically stage `hf_readmes/README.<space>.md`
when present during a Space deployment.
"""
import os
import sys
from pathlib import Path

MAPPING = {
    "diatribe00/opennormattiva-search": "hf_readmes/README.opennormattiva-search.md",
    "diatribe00/opennormattiva-lab": "hf_readmes/README.opennormattiva-lab.md",
    "diatribe00/italian-legal-lab": "hf_readmes/README.italian-legal-lab.md",
    "diatribe00/openitalaw": "hf_readmes/README.openitalaw.md",
}


def ensure_hf_api():
    try:
        from huggingface_hub import HfApi
        return HfApi
    except Exception:
        print("Installing huggingface_hub... (may take a minute)")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "huggingface_hub>=0.20.0"])  # pragma: no cover
        from huggingface_hub import HfApi
        return HfApi


def main():
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("HF_TOKEN missing. Set HF_TOKEN in your environment and retry.")
        sys.exit(2)

    HfApi = ensure_hf_api()
    api = HfApi()

    for repo_id, local_path in MAPPING.items():
        p = Path(local_path)
        if not p.exists():
            print(f"Skipping {repo_id}: local template {local_path} not found")
            continue
        print(f"Uploading README to {repo_id} from {local_path}...")
        try:
            with open(p, "rb") as fh:
                api.upload_file(
                    path_or_fileobj=fh,
                    path_in_repo="README.md",
                    repo_id=repo_id,
                    repo_type="space",
                    token=token,
                    commit_message=f"chore: update README for {repo_id}",
                )
            print(f"  -> OK: {repo_id}")
        except Exception as e:
            print(f"  -> FAILED: {repo_id}: {e}")

    print("Done. If uploads succeeded, check your Hugging Face profile to verify card titles/descriptions.")


if __name__ == "__main__":
    main()
