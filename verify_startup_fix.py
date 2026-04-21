#!/usr/bin/env python3
"""
Verify that the startup fix is properly configured.
Run this before deploying.
"""
import sys
from pathlib import Path

def check_file_exists(path: str, name: str) -> bool:
    """Check if file exists and report status."""
    if Path(path).exists():
        size = Path(path).stat().st_size
        print(f"  ✓ {name:<40} ({size:,} bytes)")
        return True
    else:
        print(f"  ✗ {name:<40} MISSING")
        return False

def check_file_content(path: str, search_string: str, name: str) -> bool:
    """Check if file contains expected content."""
    if not Path(path).exists():
        print(f"  ✗ {name:<40} FILE NOT FOUND")
        return False
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except (UnicodeDecodeError, UnicodeError):
        # Fallback to latin-1 if utf-8 fails
        with open(path, 'r', encoding='latin-1', errors='ignore') as f:
            content = f.read()
    
    if search_string in content:
        print(f"  ✓ {name:<40}")
        return True
    else:
        print(f"  ✗ {name:<40} CONTENT NOT FOUND")
        return False

def main():
    print("=" * 70)
    print("OpenNormattiva Startup Fix Verification")
    print("=" * 70)
    
    checks = {
        "Files": [
            ("deploy_hf.py", "deploy_hf.py"),
            ("download_db.py", "download_db.py"),
            ("requirements.txt", "requirements.txt"),
            ("space/app.py", "space/app.py"),
        ],
        "Content Checks": [
            ("deploy_hf.py", "set -euo pipefail", "startup.sh: strict mode"),
            ("deploy_hf.py", "python3 /app/download_db.py", "startup.sh: calls download_db.py"),
            ("deploy_hf.py", "MAX_RETRIES=3", "startup.sh: retry logic"),
            ("deploy_hf.py", "MIN_DB_SIZE=100000000", "startup.sh: size validation"),
            ("download_db.py", "hf_hub_download", "download_db.py: HF integration"),
            ("download_db.py", "HF_DATASET_OWNER", "download_db.py: env vars"),
            ("deploy_hf.py", "PYTHONUNBUFFERED=1", "Dockerfile: logging setup"),
            ("deploy_hf.py", "chmod +x startup.sh download_db.py", "Dockerfile: executables"),
        ]
    }
    
    results = []
    
    # File existence checks
    print("\n[Files]")
    for path, name in checks["Files"]:
        results.append(check_file_exists(path, name))
    
    # Content checks
    print("\n[Implementation]")
    for path, search, name in checks["Content Checks"]:
        results.append(check_file_content(path, search, name))
    
    # Summary
    print("\n" + "=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"Status: {passed}/{total} checks passed")
    
    if passed == total:
        print("✓ All checks passed! Ready to deploy.")
        print("\nNext steps:")
        print("  1. export HF_TOKEN='hf_...'")
        print("  2. python deploy_hf.py")
        return 0
    else:
        print("✗ Some checks failed. Fix the issues above before deploying.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
