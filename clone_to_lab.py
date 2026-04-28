#!/usr/bin/env python3
"""
Clone the OpenNormattiva HuggingFace Space and Dataset to a new experimental repo.

This creates isolated copies of:
  - diatribe00/normattiva-search  →  diatribe00/<lab-space-name>
  - diatribe00/normattiva-data    →  diatribe00/<lab-dataset-name>

The cloned Space is configured to pull its DB from the cloned Dataset,
so the two environments are completely independent.

Usage:
    # Clone with defaults (normattiva-lab-space + normattiva-lab-data)
    python clone_to_lab.py

    # Custom names
    python clone_to_lab.py --space normattiva-multivigente --dataset normattiva-lab-data

    # Dry run (show what would be done, no HF writes)
    python clone_to_lab.py --dry-run
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Clone OpenNormattiva to Italian Legal Lab")
    parser.add_argument("--token", default=os.environ.get("HF_TOKEN", ""))
    parser.add_argument(
        "--space", default="italian-legal-lab",
        help="Name for the cloned Space (default: italian-legal-lab)"
    )
    parser.add_argument(
        "--dataset", default="italian-legal-lab-data",
        help="Name for the cloned Dataset (default: italian-legal-lab-data)"
    )
    parser.add_argument(
        "--skip-dataset", action="store_true",
        help="Skip dataset clone (Space only). Dataset must already exist."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without writing to HF"
    )
    args = parser.parse_args()

    if not args.token and not args.dry_run:
        print("ERROR: No HF token. Set HF_TOKEN env var or use --token")
        sys.exit(1)

    from huggingface_hub import HfApi
    api = HfApi(token=args.token) if not args.dry_run else None
    username = api.whoami()["name"] if api else "diatribe00"

    src_space_id   = f"{username}/normattiva-search"
    src_dataset_id = f"{username}/normattiva-data"
    lab_space_id   = f"{username}/{args.space}"
    lab_dataset_id = f"{username}/{args.dataset}"

    print(f"Source Space:   https://huggingface.co/spaces/{src_space_id}")
    print(f"Source Dataset: https://huggingface.co/datasets/{src_dataset_id}")
    print(f"Lab Space:      https://huggingface.co/spaces/{lab_space_id}")
    print(f"Lab Dataset:    https://huggingface.co/datasets/{lab_dataset_id}")
    print()

    if args.dry_run:
        print("[DRY RUN] No changes will be made to HuggingFace.")
        print()

    # ── 1. Clone Dataset ──────────────────────────────────────────────────
    if not args.skip_dataset:
        print("[1/2] Cloning Dataset...")

        if args.dry_run:
            print(f"  Would create dataset: {lab_dataset_id}")
            print(f"  Would upload: data/laws.db ({_db_size_str()})")
            print(f"  Would upload: data/processed/laws_vigente.jsonl")
        else:
            api.create_repo(
                repo_id=lab_dataset_id, repo_type="dataset",
                private=False, exist_ok=True,
            )
            print(f"  Dataset created: {lab_dataset_id}")

            # Dataset card
            card = (
                "---\n"
                "license: mit\n"
                "language:\n  - it\n"
                "tags:\n  - legal\n  - italian-law\n  - normattiva\n  - constitutional-court\n  - multi-source\n"
                "size_categories:\n  - 1M<n<10M\n"
                "---\n\n"
                f"# Italian Legal Lab Dataset\n\n"
                f"Comprehensive Italian legal research dataset integrating:\n"
                f"- **Normattiva**: {src_dataset_id} (190k+ laws)\n"
                "- **Corte Costituzionale**: official decisions scraped from cortecostituzionale.it and stored in the lab database\n"
                f"- **Multi-vigente**: Historical law versions\n\n"
                "## Files\n\n"
                "- `data/processed/laws_vigente.jsonl` — All vigente laws as JSONL\n"
                "- `data/laws.db` — SQLite database with laws, citations, metadata, and Corte Costituzionale decisions\n"
                "- `data/processed/laws_multivigente.jsonl` — Historical law versions\n"
            )
            api.upload_file(
                repo_id=lab_dataset_id, repo_type="dataset",
                path_in_repo="README.md",
                path_or_fileobj=card.encode(),
                commit_message="Add dataset card",
            )

            uploads = []
            jsonl_path = Path("data/processed/laws_vigente.jsonl")
            db_path    = Path("data/laws.db")

            if jsonl_path.exists():
                uploads.append((str(jsonl_path), "data/processed/laws_vigente.jsonl"))
            else:
                print(f"  WARNING: {jsonl_path} not found — skipping JSONL upload")

            if db_path.exists():
                uploads.append((str(db_path), "data/laws.db"))
            else:
                print(f"  WARNING: {db_path} not found — skipping DB upload")

            if uploads:
                print(f"  Uploading {len(uploads)} files…")
                api.upload_folder(
                    repo_id=lab_dataset_id, repo_type="dataset",
                    folder_path="data",
                    path_in_repo="data",
                    commit_message="Clone from normattiva-data",
                    ignore_patterns=["*.etag*", ".etag*"],
                )
                print(f"  Dataset cloned: https://huggingface.co/datasets/{lab_dataset_id}")
    else:
        print("[1/2] Skipping Dataset clone (--skip-dataset)")

    # ── 2. Clone Space ────────────────────────────────────────────────────
    print("\n[2/2] Cloning Space...")

    staging = Path(tempfile.mkdtemp(prefix="normattiva_lab_"))
    try:
        # Copy all Space source files from the local workspace
        space_src = Path("space")
        if not space_src.exists():
            print("  ERROR: space/ directory not found")
            sys.exit(1)

        _SKIP_PATTERNS = {
            "__pycache__", ".pyc", ".DS_Store",
            "app_old_backup.py", "app_static.py", "app_v2_backup.py",
        }

        for src_file in space_src.rglob("*"):
            if src_file.is_file():
                rel = src_file.relative_to(space_src)
                # Skip cache, backups
                if any(part in str(rel) for part in _SKIP_PATTERNS):
                    continue
                dst = staging / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst)

        # Also copy root-level files needed by the Space container
        for fname in ["download_db.py", "download_sentenze.py", "normattiva_api_client.py", "parse_akn.py"]:
            src = Path(fname)
            if src.exists():
                shutil.copy2(src, staging / fname)

        # Copy shared Python packages used by app.py (e.g., core.db)
        shared_dirs = ["core"]
        for dname in shared_dirs:
            src_dir = Path(dname)
            if not src_dir.exists() or not src_dir.is_dir():
                continue
            for src_file in src_dir.rglob("*"):
                if not src_file.is_file():
                    continue
                rel = src_file.relative_to(Path("."))
                if "__pycache__" in rel.parts or src_file.suffix == ".pyc":
                    continue
                dst = staging / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst)

        # ── Patch app.py: update title + banner to identify this as the lab ──
        # For lab, use the enhanced app instead of production app
        app_path = staging / "enhanced_lab_app.py"
        
        # Copy enhanced lab app as main app
        enhanced_src = Path("space/enhanced_lab_app.py")
        if enhanced_src.exists():
            shutil.copy2(enhanced_src, staging / "enhanced_lab_app.py")
            print(f"  Using enhanced lab app for full jurisprudence support")
        else:
            # Fallback: patch the production app
            app_path = staging / "app.py"
            if app_path.exists():
                app_text = app_path.read_text(encoding="utf-8")

                # sys.path fix for Docker
                app_text = app_text.replace(
                    "sys.path.insert(0, str(Path(__file__).parent.parent))\n"
                    "sys.path.insert(0, str(Path(__file__).parent))\n",
                    "# paths already at root in Docker container\n"
                )
                # Mark as lab in sidebar caption
                app_text = app_text.replace(
                    "⚖️ **OpenNormattiva** — Piattaforma di ricerca giuridica italiana",
                    "🧪 **OpenNormattiva Lab** — Ambiente sperimentale",
                )
                app_path.write_text(app_text, encoding="utf-8")

        # ── README for the lab Space ──────────────────────────────────────
        readme = staging / "README.md"
        readme.write_text(
            "---\n"
            "title: Italian Legal Lab\n"
            "emoji: ⚖️\n"
            "colorFrom: blue\n"
            "colorTo: green\n"
            "sdk: docker\n"
            "pinned: false\n"
            "license: mit\n"
            "app_port: 8501\n"
            "---\n\n"
            f"# Italian Legal Lab — Integrated Legal Research Platform\n\n"
            f"Comprehensive research platform integrating multiple Italian legal sources:\n\n"
            "## Data Sources\n\n"
            "- **Normattiva (190k+ laws)**: Complete Italian legislative corpus (vigente + abrogate + multivigente)\n"
            "- **Corte Costituzionale**: official decisions embedded in the lab database\n"
            "- **Planned**: Corte di Cassazione, Administrative Courts, EU law, regional legislation\n\n"
            "## Features\n\n"
            "- **🔍 Unified Search**: Search across laws and Constitutional Court decisions\n"
            "- **⚖️ Jurisprudence Integration**: Explore real Corte Costituzionale decisions from the official site\n"
            "- **📖 Law Explorer**: Browse with filters, historical versions, and metadata\n"
            "- **🕰️ Historical Analysis**: Track law modifications over time (multivigente)\n"
            "- **📊 Advanced Analytics**: Citation networks, constitutional principles, impact analysis\n\n"
            "## Architecture\n\n"
            "- **Database**: SQLite with laws, citations, metadata, and Constitutional Court decisions\n"
            f"- **Dataset**: [{lab_dataset_id}](https://huggingface.co/datasets/{lab_dataset_id})\n"
            "- **App**: Enhanced Streamlit with multi-source integration\n\n"
            "## Multi-Source Expansion Roadmap\n\n"
            "- Phase 1 (Current): Normattiva + Constitutional Court\n"
            "- Phase 2 (Week 2): Corte di Cassazione (Supreme Court)\n"
            "- Phase 3 (Month 1): Administrative Courts, Regional Laws\n"
            "- Phase 4 (Month 2): EU Law, International Treaties\n\n"
            "---\n"
            "Built on [OpenNormattiva](https://huggingface.co/spaces/{src_space_id})\n",
            encoding="utf-8",
            newline="\n",
        )

        # ── Dockerfile ────────────────────────────────────────────────────
        (staging / "Dockerfile").write_text(
            "FROM python:3.11-slim\n"
            "WORKDIR /app\n"
            "ENV PYTHONUNBUFFERED=1 \\\n"
            "    PYTHONDONTWRITEBYTECODE=1 \\\n"
            "    PIP_NO_CACHE_DIR=1\n"
            # Point the DB downloader at the LAB dataset, not the production one
            f"ENV HF_DATASET_OWNER={username} \\\n"
            f"    HF_DATASET_NAME={args.dataset}\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
            "    libxml2-dev libxslt-dev gcc git && rm -rf /var/lib/apt/lists/*\n"
            "COPY requirements.txt .\n"
            "RUN pip install -r requirements.txt\n"
            "COPY . .\n"
            "RUN sed -i 's/\\r$//' startup.sh && chmod +x startup.sh download_db.py\n"
            "EXPOSE 8501\n"
            'CMD ["/bin/bash", "-c", "exec ./startup.sh"]\n',
            encoding="utf-8",
            newline="\n",
        )

        # ── startup.sh (enhanced for lab with jurisprudence) ────────────────────────────────
        startup_sh = (
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "DB_PATH=\"/app/data/laws.db\"\n"
            "MIN_DB_SIZE=100000000\n"
            "MAX_RETRIES=3\n"
            "RETRY_DELAY=5\n"
            "mkdir -p /app/data /app/data/processed\n"
            "cd /app\n"
            "check_db() {\n"
            "  [ -f \"$DB_PATH\" ] && [ $(stat -c%s \"$DB_PATH\" 2>/dev/null || echo 0) -ge $MIN_DB_SIZE ]\n"
            "}\n"
            "download_db() {\n"
            "  local attempt=1\n"
            "  while [ $attempt -le $MAX_RETRIES ]; do\n"
            "    [ $attempt -gt 1 ] && sleep $RETRY_DELAY\n"
            "    echo \"[startup] Download attempt $attempt/$MAX_RETRIES...\"\n"
            "    if python3 /app/download_db.py \"$DB_PATH\"; then\n"
            "      check_db && return 0\n"
            "      rm -f \"$DB_PATH\"\n"
            "    fi\n"
            "    attempt=$((attempt + 1))\n"
            "  done\n"
            "  return 1\n"
            "}\n"
            "if ! check_db; then\n"
            "  download_db || { echo '[startup] FATAL: DB download failed' >&2; exit 1; }\n"
            "else\n"
            "  echo '[startup] DB already present'\n"
            "fi\n"
            "echo '[startup] Checking Corte Costituzionale data shipped with the lab DB...'\n"
            "python3 - << 'PYEOF'\n"
            "import sqlite3\n"
            "conn = sqlite3.connect('/app/data/laws.db')\n"
            "try:\n"
            "    count = conn.execute('SELECT COUNT(*) FROM sentenze').fetchone()[0]\n"
            "    print('Corte Costituzionale decisions in DB:', count)\n"
            "    m = conn.execute('SELECT COUNT(*) FROM sentenze_massime').fetchone()[0]\n"
            "    print('Massime in DB:', m)\n"
            "    og = conn.execute('SELECT COUNT(*) FROM openga_sentenze').fetchone()[0]\n"
            "    print('OpenGA decisions in DB:', og)\n"
            "except Exception as e:\n"
            "    print('DB check note:', e)\n"
            "conn.close()\n"
            "PYEOF\n"
            "echo '[startup] Starting Streamlit...'\n"
            "if [ -f enhanced_lab_app.py ]; then\n"
            "  exec streamlit run enhanced_lab_app.py --server.port=8501 --server.address=0.0.0.0\n"
            "else\n"
            "  exec streamlit run app.py --server.port=8501 --server.address=0.0.0.0\n"
            "fi\n"
        )
        (staging / "startup.sh").write_text(startup_sh, encoding="utf-8", newline="\n")

        # ── requirements.txt ──────────────────────────────────────────────
        (staging / "requirements.txt").write_text(
            "streamlit>=1.36.0\n"
            "plotly\n"
            "pandas\n"
            "lxml\n"
            "tqdm\n"
            "requests\n"
            "huggingface_hub>=0.20.0\n"
            "beautifulsoup4\n",
            encoding="utf-8",
            newline="\n",
        )

        (staging / ".gitignore").write_text("__pycache__/\n*.pyc\n.DS_Store\n", encoding="utf-8", newline="\n")

        print(f"  Staged files:")
        for p in sorted(staging.rglob("*")):
            if p.is_file():
                rel = p.relative_to(staging)
                print(f"    {rel}")

        if args.dry_run:
            print(f"\n[DRY RUN] Would deploy Space to: https://huggingface.co/spaces/{lab_space_id}")
            print(f"[DRY RUN] Dockerfile sets HF_DATASET_NAME={args.dataset}")
        else:
            api.create_repo(
                repo_id=lab_space_id, repo_type="space",
                space_sdk="docker", private=False, exist_ok=True,
            )
            api.upload_folder(
                repo_id=lab_space_id, repo_type="space",
                folder_path=str(staging),
                commit_message="Clone from normattiva-search (lab fork)",
            )
            print(f"\n  Space deployed: https://huggingface.co/spaces/{lab_space_id}")

    finally:
        shutil.rmtree(staging, ignore_errors=True)

    print("\nDone! Lab rebuild complete -- Enhanced Jurisprudence Research Platform")
    print(f"  Production:   https://huggingface.co/spaces/{src_space_id}")
    print(f"  Lab Space:    https://huggingface.co/spaces/{lab_space_id}")
    print(f"  Lab Dataset:  https://huggingface.co/datasets/{lab_dataset_id}")
    print()
    print("Lab Features:")
    print("  [OK] Full normattiva dataset (vigente + abrogate + multivigente)")
    print("  [OK] Constitutional Court (Corte Costituzionale) jurisprudence")
    print("  [OK] Giustizia Amministrativa (OpenGA) - CdS + all TAR courts")
    print("  [OK] Integrated search across laws and sentenze")
    print("  [OK] Advanced analytics and citation networks")
    if args.dry_run:
        print("\n  (DRY RUN -- no changes written)")


def _db_size_str() -> str:
    db = Path("data/laws.db")
    if db.exists():
        return f"{db.stat().st_size / 1e6:.0f}MB"
    return "not found locally"


if __name__ == "__main__":
    main()
