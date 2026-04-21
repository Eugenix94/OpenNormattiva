#!/usr/bin/env python3
"""
Download and verify the laws database from HF Dataset.
Called by startup.sh during container initialization.

Usage:
    python3 download_db.py /app/data/laws.db
"""
import os
import shutil
import sys
import time
from pathlib import Path

def get_dataset_repo():
    """Determine the correct dataset repo_id from environment or defaults."""
    dataset_owner = os.environ.get("HF_DATASET_OWNER", "diatribe00")
    dataset_name = os.environ.get("HF_DATASET_NAME", "normattiva-data")
    return f"{dataset_owner}/{dataset_name}"

def download_database(output_path: str) -> bool:
    """
    Download the laws database from HF Dataset with error handling.
    
    Args:
        output_path: Full path where to save the database
        
    Returns:
        True if successful, False otherwise
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("[download_db] ERROR: huggingface_hub not installed", file=sys.stderr)
        return False
    
    repo_id = get_dataset_repo()
    output_path_obj = Path(output_path)
    output_dir = str(output_path_obj.parent)
    token = os.environ.get("HF_TOKEN")
    max_retries = 3
    min_db_size = 100_000_000  # 100MB
    
    if not token:
        print(
            "[download_db] WARNING: HF_TOKEN not set. Download may be slower and rate-limited.",
            file=sys.stderr,
        )
    
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                wait_s = 2 ** attempt
                print(f"[download_db] Retry {attempt}/{max_retries} after {wait_s}s...", flush=True)
                time.sleep(wait_s)
            
            print(f"[download_db] Fetching {repo_id}/data/laws.db (attempt {attempt}/{max_retries})...", flush=True)
            
            # Force fresh metadata/file check on retries to avoid reusing a truncated cached blob
            cached_path = hf_hub_download(
                repo_id=repo_id,
                filename="data/laws.db",
                repo_type="dataset",
                cache_dir=output_dir,
                local_dir=None,
                token=token,
                force_download=(attempt > 1),
                resume_download=True,
            )
            
            # Verify cached file
            if not Path(cached_path).exists():
                raise FileNotFoundError(f"Downloaded file not found: {cached_path}")
            
            cached_size = Path(cached_path).stat().st_size
            if cached_size < min_db_size:
                raise ValueError(f"Downloaded DB too small: {cached_size} bytes")
            
            print(f"[download_db] Downloaded successfully: {cached_size / 1e9:.2f}GB", flush=True)
            
            # Copy to final location
            print(f"[download_db] Copying to {output_path}...", flush=True)
            shutil.copy2(cached_path, output_path)
            
            # Verify final file
            final_size = output_path_obj.stat().st_size
            if final_size != cached_size:
                raise ValueError(
                    f"Copy verification failed: {final_size} != {cached_size} bytes"
                )
            
            print(f"[download_db] Ready: {final_size / 1e9:.2f}GB at {output_path}", flush=True)
            return True
            
        except FileNotFoundError as e:
            print(f"[download_db] ERROR: File not found - {e}", file=sys.stderr)
            print(f"[download_db] Check if dataset {repo_id} exists and contains data/laws.db", file=sys.stderr)
            return False
            
        except ValueError as e:
            print(f"[download_db] ERROR: Validation failed - {e}", file=sys.stderr)
            if output_path_obj.exists():
                output_path_obj.unlink(missing_ok=True)
            if attempt == max_retries:
                return False
            
        except Exception as e:
            print(f"[download_db] ERROR: Download failed - {type(e).__name__}: {e}", file=sys.stderr)
            if attempt == max_retries:
                return False
    
    return False

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 download_db.py <output_path>", file=sys.stderr)
        sys.exit(1)
    
    output_path = sys.argv[1]
    
    # Ensure parent directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    # Download and verify
    success = download_database(output_path)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
