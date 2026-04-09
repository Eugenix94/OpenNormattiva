#!/usr/bin/env python3
"""
Deploy OpenNormattiva to HuggingFace Spaces + create/update HF Dataset.

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
    parser = argparse.ArgumentParser(description="Deploy to HuggingFace")
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

    space_id = f"{username}/normattiva-search"
    dataset_id = f"{username}/normattiva-data"

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
                "title: OpenNormattiva\n"
                "emoji: ⚖️\n"
                "colorFrom: blue\n"
                "colorTo: indigo\n"
                "sdk: docker\n"
                "pinned: true\n"
                "license: mit\n"
                "app_port: 8501\n"
                "---\n\n"
                "# OpenNormattiva — Italian Law Research Platform\n\n"
                "Search, browse, and analyse 160,000+ Italian laws with "
                "full-text search, citation graphs, and domain classification.\n",
                encoding="utf-8",
            )

            # Dockerfile for Streamlit
            (staging / "Dockerfile").write_text(
                "FROM python:3.11-slim\n"
                "WORKDIR /app\n"
                "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
                "    libxml2-dev libxslt-dev gcc && rm -rf /var/lib/apt/lists/*\n"
                "COPY requirements.txt .\n"
                "RUN pip install --no-cache-dir -r requirements.txt\n"
                "COPY . .\n"
                "EXPOSE 8501\n"
                'CMD ["streamlit", "run", "app.py", \\\n'
                '     "--server.port=8501", \\\n'
                '     "--server.address=0.0.0.0", \\\n'
                '     "--server.headless=true", \\\n'
                '     "--browser.gatherUsageStats=false"]\n',
                encoding="utf-8",
            )

            # The main Streamlit app (flat, at root)
            shutil.copy("space/app.py", staging / "app.py")

            # Dependencies the app imports
            shutil.copy("normattiva_api_client.py", staging / "normattiva_api_client.py")
            shutil.copy("parse_akn.py", staging / "parse_akn.py")

            # core package
            core_dst = staging / "core"
            core_dst.mkdir()
            for f in Path("core").glob("*.py"):
                shutil.copy(f, core_dst / f.name)
            if not (core_dst / "__init__.py").exists():
                (core_dst / "__init__.py").write_text("")

            # Patch app.py sys.path hack: on Space, files are at root
            app_text = (staging / "app.py").read_text(encoding="utf-8")
            app_text = app_text.replace(
                "sys.path.insert(0, str(Path(__file__).parent.parent))\n"
                "sys.path.insert(0, str(Path(__file__).parent))\n",
                "# paths already at root in Docker container\n"
            )
            # Update dataset repo reference
            app_text = app_text.replace(
                'HF_DATASET_REPO = "diatribe00/normattiva-data-raw"',
                f'HF_DATASET_REPO = "{dataset_id}"',
            )
            (staging / "app.py").write_text(app_text, encoding="utf-8")

            # requirements.txt for the Space
            reqs = (
                "streamlit>=1.56.0\n"
                "plotly\n"
                "pandas\n"
                "lxml\n"
                "huggingface-hub\n"
                "python-dotenv\n"
                "tqdm\n"
                "requests\n"
            )
            (staging / "requirements.txt").write_text(reqs, encoding="utf-8")

            # .gitignore
            (staging / ".gitignore").write_text(
                "data/\n__pycache__/\n*.pyc\n.DS_Store\n*.db\n",
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

    print("\n✓ Deployment complete!")
    if not args.skip_space:
        print(f"  Space: https://huggingface.co/spaces/{space_id}")
    if not args.skip_dataset:
        print(f"  Dataset: https://huggingface.co/datasets/{dataset_id}")


if __name__ == "__main__":
    main()
