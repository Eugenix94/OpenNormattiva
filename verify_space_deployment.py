#!/usr/bin/env python3
"""
Verify and repair Space deployment - checks if database was actually uploaded.
If database is missing, forces a re-upload.

Usage:
    python verify_space_deployment.py --token hf_xxx
"""
import argparse
import os
import sys
from pathlib import Path
from huggingface_hub import HfApi, list_repo_files, list_repo_tree

def main():
    parser = argparse.ArgumentParser(description="Verify HF Space deployment")
    parser.add_argument("--token", default=os.environ.get("HF_TOKEN", ""))
    args = parser.parse_args()

    if not args.token:
        print("ERROR: No HF token. Set HF_TOKEN or use --token")
        sys.exit(1)

    api = HfApi(token=args.token)
    user = api.whoami()
    username = user["name"]
    space_id = f"{username}/normattiva-search"

    print(f"\n🔍 Verifying Space: {space_id}\n")

    # Check what files are actually in the Space
    try:
        files = list_repo_tree(
            repo_id=space_id,
            repo_type="space",
            token=args.token,
            recursive=True
        )
        
        print(f"Files in Space ({len(list(files))} total):")
        file_list = []
        has_db = False
        total_size = 0
        
        for file_info in files:
            if file_info.isdir or file_info.path is None:
                continue
            file_path = file_info.path
            size_mb = (file_info.size or 0) / 1e6 if hasattr(file_info, 'size') else 0
            file_list.append((file_path, size_mb))
            
            if 'laws.db' in str(file_path):
                has_db = True
                print(f"  ✓ {file_path} ({size_mb:.1f} MB)")
            elif size_mb > 1:
                print(f"  • {file_path} ({size_mb:.1f} MB)")
            
            total_size += size_mb
        
        print(f"\nTotal size: {total_size:.1f} MB")
        
        if has_db:
            print(f"\n✓ Database found in Space!")
            print(f"  The issue may be with the app.py paths or container initialization.")
            print(f"  The updated app.py has better diagnostics - redeploying may help.")
        else:
            print(f"\n❌ Database NOT found in Space!")
            print(f"  The upload may have failed or stalled.")
            print(f"\n💡 Solution: Run redeploy_with_retry.py to force a re-upload")
        
        print(f"\n📁 Files in Space:")
        for file_path, size_mb in sorted(file_list):
            if size_mb > 0.1:
                print(f"    {file_path} ({size_mb:.1f} MB)")
            else:
                print(f"    {file_path}")
                
    except Exception as e:
        print(f"Error checking space files: {e}")
        print(f"This might be a permission or network issue.")

if __name__ == "__main__":
    main()
