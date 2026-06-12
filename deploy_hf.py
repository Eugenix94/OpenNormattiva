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
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


def get_current_branch() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def enforce_branch_targets(branch: str, space_name: str, dataset_name: str, allow_unsafe: bool) -> None:
    expected = {
        "normattivavigente": ("normattivavigente", "normattivavigente-data"),
        "master": ("normattivavigente", "normattivavigente-data"),
        "italian-legal-lab": ("italian-legal-lab", "italian-legal-lab-data"),
        "normattiva-lab": ("opennormattiva-lab", "normattiva-lab-data"),
        "lab-space": ("opennormattiva-lab", "normattiva-lab-data"),
    }
    if branch not in expected or allow_unsafe:
        return

    exp_space, exp_dataset = expected[branch]
    if space_name != exp_space or dataset_name != exp_dataset:
        print("ERROR: Deploy target mismatch for current branch")
        print(f"  Branch:          {branch}")
        print(f"  Expected Space:  {exp_space}")
        print(f"  Expected Dataset:{exp_dataset}")
        print(f"  Requested Space: {space_name}")
        print(f"  Requested Dataset:{dataset_name}")
        print("Use --allow-unsafe-targets only if you intentionally need cross-target deploy.")
        sys.exit(2)

def main():
    parser = argparse.ArgumentParser(description="Deploy to HuggingFace")
    parser.add_argument("--token", default=os.environ.get("HF_TOKEN", ""))
    parser.add_argument("--space-name", default="italian-legal-lab",
                        help="HF Space repo name (default: italian-legal-lab)")
    parser.add_argument("--dataset-name", default="italian-legal-lab-data",
                        help="HF Dataset repo name (default: italian-legal-lab-data)")
    parser.add_argument("--skip-space", action="store_true")
    parser.add_argument("--skip-dataset", action="store_true")
    parser.add_argument("--allow-unsafe-targets", action="store_true",
                        help="Allow deploying to non-standard space/dataset for the current branch")
    args = parser.parse_args()

    branch = get_current_branch()
    print(f"Current git branch: {branch}")
    enforce_branch_targets(branch, args.space_name, args.dataset_name, args.allow_unsafe_targets)

    if not args.token:
        print("ERROR: No HF token. Set HF_TOKEN or use --token")
        sys.exit(1)

    from huggingface_hub import HfApi
    api = HfApi(token=args.token)
    user = api.whoami()
    username = user["name"]
    print(f"Authenticated as: {username}")

    space_id = f"{username}/{args.space_name}"
    dataset_id = f"{username}/{args.dataset_name}"

    # ── Deploy Space ──────────────────────────────────────────────────────
    if not args.skip_space:
        print("\n[1/2] Deploying HF Space...")

        # Determine per-space profile and default dataset to inject into the container
        profile_defaults = {
            "normattivavigente": ("search", "diatribe00/normattivavigente-data"),
            "opennormattiva-lab": ("lab", "diatribe00/normattiva-lab-data"),
            "italian-legal-lab": ("italianlab", "diatribe00/italian-legal-lab-data"),
            "openitalaw": ("models", "diatribe00/openitalaw-data"),
        }
        profile, ds = profile_defaults.get(args.space_name, ("search", f"{username}/{args.dataset_name}"))

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
            # Allow per-space README overrides placed under hf_readmes/README.<space>.md
            custom_readme = Path(f"hf_readmes/README.{args.space_name}.md")
            if custom_readme.exists():
                readme.write_text(custom_readme.read_text(encoding="utf-8"), encoding="utf-8")
            else:
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
                    "full-text search, citation graphs, and domain classification.\n\n"
                    "## Deployment\n\n"
                    "This Space automatically downloads the laws database (~970MB) from HF Dataset on first run.\n"
                    "Subsequent restarts use the cached copy.\n\n"
                    "### Environment Variables\n\n"
                    "- `HF_DATASET_OWNER`: Owner of the dataset repo (default: `diatribe00`)\n"
                    "- `HF_DATASET_NAME`: Name of the dataset repo (default: `normattivavigente-data`)\n"
                    "- `HF_TOKEN`: HuggingFace API token (auto-set if deploying to your Space)\n\n"
                    "### Logs\n\n"
                    "Check container logs for startup progress:\n"
                    "```\n"
                    "[startup] Downloading database (attempt 1/3, ~970MB)...\n"
                    "[download_db] Fetching diatribe00/normattivavigente-data/data/laws.db...\n"
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
                "# Injected environment for Space profile (set by deploy_hf.py)\n"
                f"export APP_PROFILE=\"{profile}\"\n"
                f"export HF_DATASET_NAME=\"{ds}\"\n"
                f"export HF_DATASET_OWNER=\"{ds.split('/')[0]}\"\n"
                f"export HF_DATASET_NAME=\"{ds.split('/')[-1]}\"\n"
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

            # Streamlit app modules (flat, at root)
            shutil.copy("space/app.py", staging / "app.py")
            if Path("space/corte_explorer.py").exists():
                shutil.copy("space/corte_explorer.py", staging / "corte_explorer.py")
            if Path("institutional_directory.py").exists():
                shutil.copy("institutional_directory.py", staging / "institutional_directory.py")
            if Path("space/institutional_sparql_explorer.py").exists():
                shutil.copy("space/institutional_sparql_explorer.py", staging / "institutional_sparql_explorer.py")
            if Path("space/senato_wayback_explorer.py").exists():
                shutil.copy("space/senato_wayback_explorer.py", staging / "senato_wayback_explorer.py")
            if Path("space/dataset_explorer.py").exists():
                shutil.copy("space/dataset_explorer.py", staging / "dataset_explorer.py")
            if Path("space/legal_rss_hub.py").exists():
                shutil.copy("space/legal_rss_hub.py", staging / "legal_rss_hub.py")
            if Path("space/global_ai_copilot.py").exists():
                shutil.copy("space/global_ai_copilot.py", staging / "global_ai_copilot.py")

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

            summary_path = Path("data/laws_summary.csv")
            if db_path.exists() and not summary_path.exists():
                try:
                    from core.db import LawDatabase

                    summary_db = LawDatabase(db_path)
                    try:
                        summary_db.export_csv(summary_path)
                    finally:
                        summary_db.close()
                    print("  [OK] Generated laws_summary.csv from local DB")
                except Exception as e:
                    print(f"  Note: could not generate laws_summary.csv automatically: {e}")
            
            # But DO include ETag cache if present (small file)
            etag_path = Path("data/.etag_cache.json")
            if etag_path.exists():
                data_dst = staging / "data"
                data_dst.mkdir(parents=True, exist_ok=True)
                shutil.copy(etag_path, data_dst / ".etag_cache.json")
                print(f"  [OK] Including ETag cache")

            institutional_snapshot_path = Path("data/institutional/institutional_snapshot.json")
            if institutional_snapshot_path.exists():
                institutional_dst = staging / "data" / "institutional"
                institutional_dst.mkdir(parents=True, exist_ok=True)
                shutil.copy(institutional_snapshot_path, institutional_dst / "institutional_snapshot.json")
                print(f"  [OK] Including institutional snapshot")

            if summary_path.exists():
                data_dst = staging / "data"
                data_dst.mkdir(parents=True, exist_ok=True)
                shutil.copy(summary_path, data_dst / "laws_summary.csv")
                print(f"  [OK] Including laws summary CSV")

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

        is_vigente_target = args.space_name == "normattivavigente" or args.dataset_name == "normattivavigente-data"

        db_path = Path("data/laws.db")
        mv_db_path = Path("data/multivigente.db")
        jsonl_path = Path("data/processed/laws_vigente.jsonl")
        abr_jsonl_path = Path("data/processed/laws_abrogati.jsonl")
        mv_jsonl_path = Path("data/processed/laws_multivigente.jsonl")

        # Count stats for the card
        vigente_count = 0
        abrogati_count = 0
        mv_count = 0
        if db_path.exists():
            import sqlite3 as _sq
            _c = _sq.connect(str(db_path))
            vigente_count = _c.execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
            abrogati_count = _c.execute("SELECT COUNT(*) FROM laws WHERE status='abrogated'").fetchone()[0]
            _c.close()
        if mv_db_path.exists():
            import sqlite3 as _sq
            _c = _sq.connect(str(mv_db_path))
            mv_count = _c.execute("SELECT COUNT(*) FROM law_versions").fetchone()[0]
            _c.close()

        total_laws = vigente_count + abrogati_count
        build_date = datetime.now().strftime("%Y-%m-%d")

        if is_vigente_target:
            card = (
                "---\n"
                "license: mit\n"
                "language:\n  - it\n"
                "tags:\n  - legal\n  - italian-law\n  - normattiva\n  - vigente\n"
                "size_categories:\n  - 100K<n<1M\n"
                "---\n\n"
                "# NormattivaVigente Dataset\n\n"
                f"Vigente-focused Italian law dataset, built {build_date}.\n\n"
                "## Contents\n\n"
                f"| Track | Laws | Description |\n"
                f"|-------|------|-------------|\n"
                f"| **Vigente** | {vigente_count:,} | Currently in-force laws (V track) |\n\n"
                "## Files\n\n"
                "| File | Description | Size |\n"
                "|------|-------------|------|\n"
                "| `data/laws.db` | Primary SQLite DB for search profile | ~1.15 GB |\n"
                "| `data/processed/laws_vigente.jsonl` | In-force laws as JSONL | ~935 MB |\n\n"
                "## Source\n\n"
                "Data sourced from the [Normattiva Open Data API](https://dati.normattiva.it/) "
                "under the Italian Open Government License (IODL 2.0).\n"
            )
        else:
            card = (
                "---\n"
                "license: mit\n"
                "language:\n  - it\n"
                "tags:\n  - legal\n  - italian-law\n  - normattiva\n  - voom\n"
                "size_categories:\n  - 100K<n<1M\n"
                "---\n\n"
                "# OpenNormattiva VOOM Dataset\n\n"
                f"**VOOM = Vigente + Originale (abrogati) + Multivigente**\n\n"
                f"Complete Italian law corpus from [Normattiva](https://www.normattiva.it/), "
                f"built {build_date}.\n\n"
                "## Contents\n\n"
                f"| Track | Laws | Description |\n"
                f"|-------|------|-------------|\n"
                f"| **Vigente** | {vigente_count:,} | Currently in-force laws (V track) |\n"
                f"| **Abrogati** | {abrogati_count:,} | Repealed/abrogated laws (O track) |\n"
                f"| **Total (laws.db)** | {total_laws:,} | Primary database |\n"
                f"| **Multivigente** | {mv_count:,} | Amendment versions (M track, separate DB) |\n\n"
                "## Files\n\n"
                "| File | Description | Size |\n"
                "|------|-------------|------|\n"
                "| `data/laws.db` | Primary SQLite DB (FTS5, vigente + abrogati) | ~1.15 GB |\n"
                "| `data/multivigente.db` | Amendment history SQLite DB (on-demand) | ~2.0 GB |\n"
                "| `data/processed/laws_vigente.jsonl` | In-force laws as JSONL | ~935 MB |\n"
                "| `data/processed/laws_abrogati.jsonl` | Abrogated laws as JSONL | ~713 MB |\n"
                "| `data/processed/laws_multivigente.jsonl` | Amendment versions as JSONL | ~2.5 GB |\n\n"
                "## Schema (laws.db — `laws` table)\n\n"
                "```\n"
                "urn TEXT PRIMARY KEY\n"
                "title TEXT\n"
                "type TEXT          -- legge / decreto.legge / etc.\n"
                "date TEXT          -- ISO date of enactment\n"
                "year INTEGER\n"
                "text TEXT          -- full body text (capped at 150KB)\n"
                "text_length INTEGER\n"
                "article_count INTEGER\n"
                "status TEXT        -- 'in_force' | 'abrogated'\n"
                "source_collection TEXT\n"
                "importance_score REAL  -- PageRank score\n"
                "```\n\n"
                "## Schema (multivigente.db — `law_versions` table)\n\n"
                "```\n"
                "law_urn TEXT       -- FK to laws.urn\n"
                "version_date TEXT  -- date of this amendment snapshot\n"
                "title TEXT\n"
                "type TEXT\n"
                "year INTEGER\n"
                "text TEXT          -- full text of this version\n"
                "text_length INTEGER\n"
                "article_count INTEGER\n"
                "```\n\n"
                "## Source\n\n"
                "Data sourced from the [Normattiva Open Data API](https://dati.normattiva.it/) "
                "under the Italian Open Government License (IODL 2.0).\n"
            )

        # Upload files — large files uploaded individually for better error handling
        staging = Path(tempfile.mkdtemp(prefix="normattiva_dataset_"))
        try:
            # Write dataset card
            (staging / "README.md").write_text(card, encoding="utf-8")

            data_dir = staging / "data"
            processed_dir = data_dir / "processed"
            processed_dir.mkdir(parents=True)

            # Primary DB (vigente + abrogati) — always upload
            if db_path.exists():
                size_mb = db_path.stat().st_size / 1e6
                print(f"  Including laws.db ({size_mb:.0f} MB)...")
                dst_db_path = data_dir / "laws.db"
                if is_vigente_target:
                    import sqlite3 as _sq
                    src_conn = _sq.connect(str(db_path))
                    dst_conn = _sq.connect(str(dst_db_path))
                    try:
                        src_conn.backup(dst_conn)
                    finally:
                        src_conn.close()
                    removed = dst_conn.execute(
                        "SELECT COUNT(*) FROM laws WHERE status IS NOT 'in_force'"
                    ).fetchone()[0]
                    dst_conn.execute("DELETE FROM laws WHERE status IS NOT 'in_force'")
                    dst_conn.commit()
                    kept = dst_conn.execute(
                        "SELECT COUNT(*) FROM laws WHERE status='in_force'"
                    ).fetchone()[0]
                    dst_conn.close()
                    print(f"  Prepared vigente-only laws.db ({kept:,} in_force rows, removed {removed:,})")
                else:
                    shutil.copy(db_path, dst_db_path)
            else:
                print("  WARNING: data/laws.db not found — run build_voom.py first")

            # Multivigente DB — upload only for non-vigente targets
            if (not is_vigente_target) and mv_db_path.exists():
                size_mb = mv_db_path.stat().st_size / 1e6
                print(f"  Including multivigente.db ({size_mb:.0f} MB)...")
                shutil.copy(mv_db_path, data_dir / "multivigente.db")
            elif not is_vigente_target:
                print("  Note: multivigente.db not built; run: py build_voom.py --steps multivigente")

            # JSONL files — include if present
            jsonl_files = [
                (jsonl_path, "laws_vigente.jsonl"),
            ]
            if not is_vigente_target:
                jsonl_files.extend([
                    (abr_jsonl_path, "laws_abrogati.jsonl"),
                    (mv_jsonl_path, "laws_multivigente.jsonl"),
                ])
            for src, dst_name in jsonl_files:
                if src.exists():
                    size_mb = src.stat().st_size / 1e6
                    print(f"  Including {dst_name} ({size_mb:.0f} MB)...")
                    shutil.copy(src, processed_dir / dst_name)

            n_files = sum(1 for _ in staging.rglob("*") if _.is_file())
            total_size_mb = sum(
                p.stat().st_size for p in staging.rglob("*") if p.is_file()
            ) / 1e6
            print(f"  Uploading {n_files} files ({total_size_mb:.0f} MB total)...")

            api.upload_folder(
                repo_id=dataset_id, repo_type="dataset",
                folder_path=str(staging),
                commit_message=(
                    f"VOOM dataset: {vigente_count:,} vigente + {abrogati_count:,} abrogati"
                    + (f" + {mv_count:,} multivigente versions" if mv_count else "")
                    + f" — built {build_date}"
                ),
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
