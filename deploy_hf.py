#!/usr/bin/env python3
"""
Deploy OpenNormattiva Normattiva-Lab to HuggingFace.

This branch (normattiva-lab) targets:
  Space:   diatribe00/normattiva-lab
  Dataset: diatribe00/normattiva-lab-data

Used for multivigente and experimental dataset enhancements.
Stable improvements are later merged into master (-> normattiva-search).

Usage:
    python deploy_hf.py --token hf_xxx
    python deploy_hf.py              # uses HF_TOKEN env var
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Deploy to HuggingFace (normattiva-lab branch)")
    parser.add_argument("--token", default=os.environ.get("HF_TOKEN", ""))
    parser.add_argument("--skip-space", action="store_true")
    parser.add_argument("--skip-dataset", action="store_true")
    args = parser.parse_args()

    if not args.token:
        print("ERROR: No HF token. Set HF_TOKEN or use --token")
        sys.exit(1)

    from huggingface_hub import HfApi
    api = HfApi(token=args.token)
    user = api.whoami()
    username = user["name"]
    print(f"Authenticated as: {username}")

    # normattiva-lab branch targets the lab space/dataset, NOT production
    space_id = f"{username}/normattiva-lab"
    dataset_id = f"{username}/normattiva-lab-data"

    # ── Deploy Space ──────────────────────────────────────────────────────
    if not args.skip_space:
        print("\n[1/2] Deploying HF Space...")

        # Try to create the space (Docker SDK for Streamlit)
        # If it already exists with a different SDK, that's fine too
        try:
            api.create_repo(
                repo_id=space_id, repo_type="space",
                space_sdk="docker", private=False, exist_ok=True,
            )
            print(f"  Space created/confirmed: {space_id}")
        except Exception as e:
            print(f"  Note: create_repo: {e} — will try uploading anyway")

        # Build a temp directory with the needed files
        staging = Path(tempfile.mkdtemp(prefix="normattiva_space_"))
        try:
            # README with HF Spaces YAML frontmatter
            readme = staging / "README.md"
            readme.write_text(
                "---\n"
                "title: OpenNormattiva Lab\n"
                "emoji: ⚖️\n"
                "colorFrom: blue\n"
                "colorTo: indigo\n"
                "sdk: docker\n"
                "pinned: true\n"
                "license: mit\n"
                "app_port: 8501\n"
                "---\n\n"
                "# OpenNormattiva Lab — Multivigente & Abrogated Research\n\n"
                "Search, browse, and analyse Italian laws with "
                "full-text search, citation graphs, and domain classification.\n\n"
                "## Deployment\n\n"
                "This Space automatically downloads the lab database from HF Dataset on first run.\n"
                "Subsequent restarts use the cached copy.\n\n"
                "### Environment Variables\n\n"
                "- `HF_DATASET_OWNER`: Owner of the dataset repo (default: `diatribe00`)\n"
                "- `HF_DATASET_NAME`: Name of the dataset repo (default: `normattiva-lab-data`)\n"
                "- `HF_TOKEN`: HuggingFace API token (auto-set if deploying to your Space)\n\n"
                "### Logs\n\n"
                "Check container logs for startup progress:\n"
                "```\n"
                "[startup] Downloading database (attempt 1/3)...\n"
                "[download_db] Fetching diatribe00/normattiva-lab-data/data/laws.db...\n"
                "[startup] Database ready: 969MB\n"
                "[startup] Starting Streamlit...\n"
                "```\n",
                encoding="utf-8",
            )

            # Dockerfile for Streamlit (DB pre-downloads on first run)
            (staging / "Dockerfile").write_text(
                "FROM python:3.11-slim\n"
                "WORKDIR /app\n"
                "ENV PYTHONUNBUFFERED=1 \\\n"
                "    PYTHONDONTWRITEBYTECODE=1 \\\n"
                "    PIP_NO_CACHE_DIR=1\n"
                # normattiva-lab branch: always pull from normattiva-lab-data
                "ENV HF_DATASET_OWNER=diatribe00 \\\n"
                "    HF_DATASET_NAME=normattiva-lab-data\n"
                "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
                "    libxml2-dev libxslt-dev gcc git && rm -rf /var/lib/apt/lists/*\n"
                "COPY requirements.txt .\n"
                "RUN pip install -r requirements.txt\n"
                "COPY . .\n"
                "RUN chmod +x startup.sh download_db.py\n"
                "EXPOSE 8501\n"
                'CMD ["/bin/bash", "-c", "exec ./startup.sh"]\n',
                encoding="utf-8",
            )

            # Startup script: production-ready DB pre-download with error handling
            # Uses LF line endings and proper shell quoting for reliability
            startup_sh_content = (
                "#!/bin/bash\n"
                "set -euo pipefail\n"
                "trap 'echo \"[startup] FATAL: Script failed (exit $?)\" >&2' EXIT\n"
                "trap 'exit 130' INT TERM\n"
                "\n"
                "DB_PATH=\"/app/data/laws.db\"\n"
                "MIN_DB_SIZE=100000000  # 100MB threshold\n"
                "MAX_RETRIES=3\n"
                "RETRY_DELAY=5\n"
                "\n"
                "# Ensure data directory exists\n"
                "mkdir -p /app/data\n"
                "cd /app\n"
                "\n"
                "# Check if DB exists and is large enough\n"
                "check_db() {\n"
                "  [ -f \"$DB_PATH\" ] && [ $(stat -c%s \"$DB_PATH\" 2>/dev/null || echo 0) -ge $MIN_DB_SIZE ]\n"
                "}\n"
                "\n"
                "# Download database with retry logic\n"
                "download_db() {\n"
                "  local attempt=1\n"
                "  while [ $attempt -le $MAX_RETRIES ]; do\n"
                "    if [ $attempt -gt 1 ]; then\n"
                "      echo \"[startup] Retry $attempt/$MAX_RETRIES (waiting ${RETRY_DELAY}s)...\"\n"
                "      sleep $RETRY_DELAY\n"
                "    fi\n"
                "    \n"
                "    echo \"[startup] Downloading database (attempt $attempt/$MAX_RETRIES, ~970MB)...\"\n"
                "    \n"
                "    # Use Python to handle download with proper error messages\n"
                "    if python3 /app/download_db.py \"$DB_PATH\"; then\n"
                "      if check_db; then\n"
                "        local size_mb=$(( $(stat -c%s \"$DB_PATH\" 2>/dev/null || echo 0) / 1000000 ))\n"
                "        echo \"[startup] Database ready: ${size_mb}MB\"\n"
                "        return 0\n"
                "      else\n"
                "        echo \"[startup] ERROR: Downloaded DB is too small (corrupted?)\" >&2\n"
                "        rm -f \"$DB_PATH\"\n"
                "      fi\n"
                "    fi\n"
                "    \n"
                "    attempt=$((attempt + 1))\n"
                "  done\n"
                "  \n"
                "  echo \"[startup] FATAL: Failed to download database after $MAX_RETRIES attempts\" >&2\n"
                "  return 1\n"
                "}\n"
                "\n"
                "# Main startup logic\n"
                "if check_db; then\n"
                "  echo \"[startup] Database already present, skipping download\"\n"
                "else\n"
                "  if ! download_db; then\n"
                "    echo \"[startup] FATAL: Cannot start without database\" >&2\n"
                "    exit 1\n"
                "  fi\n"
                "fi\n"
                "\n"
                "# Start Streamlit\n"
                "echo \"[startup] Starting Streamlit...\"\n"
                "exec streamlit run app.py \\\n"
                "  --server.port=8501 \\\n"
                "  --server.address=0.0.0.0 \\\n"
                "  --server.headless=true \\\n"
                "  --browser.gatherUsageStats=false\n"
            )
            (staging / "startup.sh").write_text(startup_sh_content, encoding="utf-8", newline="\n")

            # The main Streamlit app (flat, at root)
            shutil.copy("space/app.py", staging / "app.py")

            # Helper scripts
            shutil.copy("download_db.py", staging / "download_db.py")
            
            # Dependencies the app imports
            shutil.copy("normattiva_api_client.py", staging / "normattiva_api_client.py")
            if Path("parse_akn.py").exists():
                shutil.copy("parse_akn.py", staging / "parse_akn.py")

            # core package
            core_dst = staging / "core"
            core_dst.mkdir()
            for f in Path("core").glob("*.py"):
                shutil.copy(f, core_dst / f.name)
            if not (core_dst / "__init__.py").exists():
                (core_dst / "__init__.py").write_text("")

            # Pre-built database — NOT included in Space (too large, avoids 1GB limit)
            # App will download from HF Dataset on first run
            db_path = Path("data/laws.db")
            if db_path.exists():
                print(f"  Note: Local DB {db_path.stat().st_size / 1e6:.1f}MB will NOT be uploaded to Space")
                print(f"        App will download it from HF Dataset on first load")
            
            # But DO include ETag cache if present (small file)
            etag_path = Path("data/.etag_cache.json")
            if etag_path.exists():
                data_dst = staging / "data"
                data_dst.mkdir(parents=True, exist_ok=True)
                shutil.copy(etag_path, data_dst / ".etag_cache.json")
                print(f"  [OK] Including ETag cache")

            # Patch app.py sys.path hack: on Space, files are at root
            app_text = (staging / "app.py").read_text(encoding="utf-8")
            app_text = app_text.replace(
                "sys.path.insert(0, str(Path(__file__).parent.parent))\n"
                "sys.path.insert(0, str(Path(__file__).parent))\n",
                "# paths already at root in Docker container\n"
            )
            (staging / "app.py").write_text(app_text, encoding="utf-8")

            # requirements.txt for the Space
            reqs = (
                "streamlit>=1.36.0\n"
                "plotly\n"
                "pandas\n"
                "lxml\n"
                "tqdm\n"
                "requests\n"
                "huggingface_hub>=0.20.0\n"
            )
            (staging / "requirements.txt").write_text(reqs, encoding="utf-8")

            # .gitignore — do NOT exclude *.db, the DB ships with the Space
            (staging / ".gitignore").write_text(
                "__pycache__/\n*.pyc\n.DS_Store\n",
                encoding="utf-8",
            )

            print(f"  Staging contents:")
            for p in sorted(staging.rglob("*")):
                if p.is_file():
                    print(f"    {p.relative_to(staging)}")

            api.upload_folder(
                repo_id=space_id, repo_type="space",
                folder_path=str(staging),
                commit_message="Deploy OpenNormattiva (Docker/Streamlit) with full URN citations",
            )
            print(f"  Space deployed: https://huggingface.co/spaces/{space_id}")
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    # ── Upload Dataset ────────────────────────────────────────────────────
    if not args.skip_dataset:
        print("\n[2/2] Uploading HF Dataset...")
        api.create_repo(
            repo_id=dataset_id, repo_type="dataset",
            private=False, exist_ok=True,
        )

        jsonl_path = Path("data/processed/laws_vigente.jsonl")
        db_path = Path("data/laws.db")

        # Dataset card
        card = (
            "---\n"
            "license: mit\n"
            "language:\n  - it\n"
            "tags:\n  - legal\n  - italian-law\n  - normattiva\n"
            "size_categories:\n  - 100K<n<1M\n"
            "---\n\n"
            "# OpenNormattiva Dataset\n\n"
            "160,000+ Italian laws from [Normattiva](https://www.normattiva.it/) "
            "with full-text, structured citations (URN), amendment tracking, "
            "and domain classification.\n\n"
            "## Files\n\n"
            "- `data/processed/laws_vigente.jsonl` — All laws as JSONL (one per line)\n"
            "- `data/laws.db` — Pre-built SQLite database with FTS5, PageRank, domains\n\n"
            "## Schema (per law)\n\n"
            "```json\n"
            "{\n"
            '  "urn": "urn:nir:stato:legge:2006;290",\n'
            '  "title": "...",\n'
            '  "type": "legge",\n'
            '  "date": "2006-12-27",\n'
            '  "year": "2006",\n'
            '  "text": "...",\n'
            '  "citations": [\n'
            '    {"target_urn": "urn:nir:stato:decreto.legislativo:2016;50", "ref": "d.lgs. 50/2016"}\n'
            "  ]\n"
            "}\n"
            "```\n"
        )

        # Upload files
        staging = Path(tempfile.mkdtemp(prefix="normattiva_dataset_"))
        try:
            # Write dataset card
            (staging / "README.md").write_text(card, encoding="utf-8")

            # Copy data files
            data_dir = staging / "data" / "processed"
            data_dir.mkdir(parents=True)
            if jsonl_path.exists():
                shutil.copy(jsonl_path, data_dir / "laws_vigente.jsonl")
            if db_path.exists():
                shutil.copy(db_path, staging / "data" / "laws.db")

            print(f"  Uploading {sum(1 for _ in staging.rglob('*') if _.is_file())} files...")

            api.upload_folder(
                repo_id=dataset_id, repo_type="dataset",
                folder_path=str(staging),
                commit_message=f"Dataset update: 164K laws, 253K citations with full URNs",
            )
            print(f"  Dataset uploaded: https://huggingface.co/datasets/{dataset_id}")
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    print("\n[OK] Deployment complete!")
    if not args.skip_space:
        print(f"  Space: https://huggingface.co/spaces/{space_id}")
    if not args.skip_dataset:
        print(f"  Dataset: https://huggingface.co/datasets/{dataset_id}")


if __name__ == "__main__":
    main()
