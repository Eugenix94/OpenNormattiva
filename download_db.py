#!/usr/bin/env python3
"""
Download databases from HF Dataset.
Called by startup.sh (for laws.db) and by the app on-demand (for multivigente.db).

Usage:
    python3 download_db.py /app/data/laws.db                     # primary DB (boot)
    python3 download_db.py /app/data/multivigente.db multivigente # on-demand
"""
import os
import shutil
import sys
from pathlib import Path


def get_dataset_repo():
    """Determine the correct dataset repo_id from environment or defaults."""
    dataset_owner = os.environ.get("HF_DATASET_OWNER", "diatribe00")
    dataset_name = os.environ.get("HF_DATASET_NAME")
    if dataset_name:
        return f"{dataset_owner}/{dataset_name}"

    # Infer dataset from Space name when HF_DATASET_NAME not provided.
    space = (os.environ.get("HF_SPACE_ID") or os.environ.get("SPACE_NAME") or os.environ.get("SPACE") or "").lower()
    if "normattiva" in space or "opennormattiva" in space:
        return f"{dataset_owner}/normattiva-lab-data"
    # Fallback to previous default to avoid breaking existing deployments
    return f"{dataset_owner}/italian-legal-lab-data"


# Mapping: short name → (hf_filename, min_size_bytes, label)
_DB_FILES = {
    "laws": ("data/laws.db", 100_000_000, "~1.15 GB"),
    "multivigente": ("data/multivigente.db", 50_000_000, "~2.0 GB"),
}


def download_file(output_path: str, hf_filename: str,
                  min_size: int, label: str) -> bool:
    """
    Download a file from HF Dataset with error handling.

    Args:
        output_path: Full path where to save the file.
        hf_filename: Path within the HF dataset repo (e.g. 'data/laws.db').
        min_size: Minimum acceptable file size in bytes.
        label: Human-readable label for log messages.

    Returns:
        True if successful, False otherwise.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("[download_db] ERROR: huggingface_hub not installed", file=sys.stderr)
        return False

    repo_id = get_dataset_repo()
    output_dir = str(Path(output_path).parent)
    token = os.environ.get("HF_TOKEN")

    try:
        print(f"[download_db] Fetching {repo_id}/{hf_filename} ({label})...", flush=True)

        cached_path = hf_hub_download(
            repo_id=repo_id,
            filename=hf_filename,
            repo_type="dataset",
            local_dir=output_dir,
            token=token,
        )

        if not Path(cached_path).exists():
            raise FileNotFoundError(f"Downloaded file not found: {cached_path}")

        cached_size = Path(cached_path).stat().st_size
        if cached_size < min_size:
            raise ValueError(
                f"Downloaded file too small: {cached_size:,} bytes (expected >={min_size:,})"
            )

        print(f"[download_db] Downloaded: {cached_size / 1e9:.2f} GB", flush=True)

        # Copy only if cached_path != output_path
        if str(Path(cached_path).resolve()) != str(Path(output_path).resolve()):
            print(f"[download_db] Copying to {output_path}...", flush=True)
            shutil.copy2(cached_path, output_path)

        final_size = Path(output_path).stat().st_size
        print(f"[download_db] Ready: {final_size / 1e9:.2f} GB at {output_path}", flush=True)
        return True

    except FileNotFoundError as e:
        print(f"[download_db] ERROR: File not found — {e}", file=sys.stderr)
        print(
            f"[download_db] Check that dataset {repo_id} contains {hf_filename}",
            file=sys.stderr,
        )
        return False

    except ValueError as e:
        print(f"[download_db] ERROR: Validation failed — {e}", file=sys.stderr)
        return False

    except Exception as e:
        print(
            f"[download_db] ERROR: Download failed — {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return False


def download_database(output_path: str, db_key: str = "laws") -> bool:
    """
    Public API used by the Streamlit app for on-demand downloads.

    Args:
        output_path: Where to save the file.
        db_key: 'laws' (primary) or 'multivigente' (amendment history).

    Returns:
        True if successful.
    """
    if db_key not in _DB_FILES:
        raise ValueError(f"Unknown db_key '{db_key}'. Valid: {list(_DB_FILES)}")
    hf_filename, min_size, label = _DB_FILES[db_key]
    return download_file(output_path, hf_filename, min_size, label)


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: python3 download_db.py <output_path> [laws|multivigente]",
            file=sys.stderr,
        )
        sys.exit(1)

    output_path = sys.argv[1]
    db_key = sys.argv[2] if len(sys.argv) >= 3 else "laws"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    success = download_database(output_path, db_key)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
