#!/usr/bin/env python3
"""
Quick redeploy to HF Space with database fix.
Uses the HF_TOKEN environment variable.
"""
import os
import sys
from pathlib import Path

# Get token
token = os.environ.get('HF_TOKEN', '')
if not token:
    # Try to get from command line
    for i, arg in enumerate(sys.argv):
        if arg == '--token' and i + 1 < len(sys.argv):
            token = sys.argv[i + 1]
            break

if not token:
    print("❌ ERROR: HF_TOKEN not found")
    print()
    print("Set via:")
    print('  $env:HF_TOKEN = "hf_xxx"  (PowerShell)')
    print('  export HF_TOKEN=hf_xxx     (Linux/Mac)')
    sys.exit(1)

print("🚀 Starting quick redeploy...")
print()

# Check database
db_path = Path("data/laws.db")
if not db_path.exists():
    print(f"❌ Database not found at {db_path}")
    sys.exit(1)

print(f"✓ Database found: {db_path.stat().st_size / 1e9:.2f} GB")
print()

# Run deploy with correct parameters
import subprocess
result = subprocess.run(
    [sys.executable, "deploy_now.py"],
    env={**os.environ, "HF_TOKEN": token}
)

sys.exit(result.returncode)
