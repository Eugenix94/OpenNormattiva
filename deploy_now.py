#!/usr/bin/env python3
"""
Deploy updated Normattiva Space to HuggingFace with the pre-built database.

Usage:
    python deploy_now.py --token hf_xxx

The token can also be set via HF_TOKEN environment variable.
"""
import os
import sys

def main():
    token = os.environ.get('HF_TOKEN', '')
    
    # Check command line args
    for i, arg in enumerate(sys.argv[1:]):
        if arg == '--token' and i + 2 < len(sys.argv):
            token = sys.argv[i + 2]
    
    if not token:
        print("ERROR: No HF_TOKEN found.")
        print("")
        print("Set via environment variable:")
        print("  $env:HF_TOKEN = 'hf_xxx'  (Windows PowerShell)")
        print("  export HF_TOKEN=hf_xxx      (Linux/Mac)")
        print("")
        print("Or pass as argument:")
        print("  python deploy_now.py --token hf_xxx")
        sys.exit(1)
    
    print(f"HF_TOKEN found. Deploying Space...")
    print("")
    
    # Run deploy_hf.py with the token
    import subprocess
    result = subprocess.run(
        [sys.executable, 'deploy_hf.py', '--token', token],
        cwd='.'
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
