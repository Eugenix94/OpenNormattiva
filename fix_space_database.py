#!/usr/bin/env python3
"""
Force fix the Space deployment by deleting the corrupt 0-byte database
and re-uploading a proper copy.

Usage:
    python fix_space_database.py --token hf_xxx
"""
import argparse
import os
import sys
import tempfile
import shutil
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Fix Space database")
    parser.add_argument("--token", default=os.environ.get("HF_TOKEN", ""))
    args = parser.parse_args()

    if not args.token:
        print("ERROR: No HF token. Set HF_TOKEN or use --token")
        sys.exit(1)

    from huggingface_hub import HfApi
    api = HfApi(token=args.token)
    user = api.whoami()
    username = user["name"]
    space_id = f"{username}/normattiva-search"

    print(f"🔧 Fixing Space database: {space_id}\n")

    # Verify local database
    db_path = Path("data/laws.db")
    if not db_path.exists():
        print(f"❌ Local database not found at {db_path}")
        sys.exit(1)
    
    db_size_mb = db_path.stat().st_size / 1e6
    print(f"✓ Local database: {db_size_mb:.1f} MB")

    # Create a minimal staging with JUST the database
    staging = Path(tempfile.mkdtemp(prefix="normattiva_fix_"))
    print(f"📦 Staging in: {staging}")
    
    try:
        # Create data directory
        data_dir = staging / "data"
        data_dir.mkdir(parents=True)
        
        # Copy database
        print(f"📋 Copying database...")
        shutil.copy(db_path, data_dir / "laws.db")
        staged_size = (data_dir / "laws.db").stat().st_size / 1e6
        print(f"✓ Staged: {staged_size:.1f} MB")
        
        # Also copy .etag_cache.json if it exists
        etag_path = Path("data/.etag_cache.json")
        if etag_path.exists():
            shutil.copy(etag_path, data_dir / ".etag_cache.json")
            print(f"✓ Staged: .etag_cache.json")
        
        # Upload with explicit commit message
        print(f"\n📤 Uploading to Space...")
        print(f"   (This may take 2-5 minutes for an 811 MB file)")
        
        api.upload_folder(
            repo_id=space_id,
            repo_type="space",
            folder_path=str(staging),
            commit_message="Fix: Replace corrupt 0-byte database with proper 811MB database",
        )
        
        print(f"\n✓ Upload complete!")
        print(f"  Space: https://huggingface.co/spaces/{space_id}")
        print(f"  Container will rebuild (3-5 min) — check back after rebuild completes\n")
        
    finally:
        print(f"🧹 Cleaning up staging...")
        shutil.rmtree(staging, ignore_errors=True)

if __name__ == "__main__":
    main()
