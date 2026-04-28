#!/usr/bin/env python3
"""
Diagnostic script to check Space status and database availability.
"""
import os
import sys
import requests
from pathlib import Path

# HF info
USERNAME = "diatribe00"
SPACE_NAME = f"{USERNAME}/normattiva-search"
DATASET_NAME = f"{USERNAME}/normattiva-data"

print("🔍 OpenNormattiva Deployment Diagnostic")
print("=" * 60)
print()

# 1. Check local database
print("1. Local Database")
print("-" * 60)
db_path = Path("data/laws.db")
if db_path.exists():
    size = db_path.stat().st_size / 1e9
    print(f"✓ Found at {db_path}: {size:.2f} GB")
else:
    print(f"✗ NOT found at {db_path}")
print()

# 2. Check HF Space
print("2. Hugging Face Space")
print("-" * 60)
space_url = f"https://huggingface.co/spaces/{SPACE_NAME}"
print(f"URL: {space_url}")
try:
    r = requests.head(space_url, timeout=5)
    if r.status_code == 200:
        print(f"✓ Space exists (HTTP {r.status_code})")
    else:
        print(f"~ Space returned HTTP {r.status_code}")
except Exception as e:
    print(f"✗ Error checking Space: {e}")
print()

# 3. Check HF Dataset
print("3. Hugging Face Dataset")
print("-" * 60)
dataset_url = f"https://huggingface.co/datasets/{DATASET_NAME}"
print(f"URL: {dataset_url}")
try:
    r = requests.head(dataset_url, timeout=5)
    if r.status_code == 200:
        print(f"✓ Dataset exists (HTTP {r.status_code})")
    else:
        print(f"~ Dataset returned HTTP {r.status_code}")
except Exception as e:
    print(f"✗ Error checking Dataset: {e}")
print()

# 4. Check for issues
print("4. Known Issues")
print("-" * 60)
issues = []

# Check if database is findable
if db_path.exists():
    size = db_path.stat().st_size
    if size < 100e6:  # Less than 100MB
        issues.append("Database seems too small (< 100MB)")
    elif size > 2e9:  # More than 2GB
        issues.append("Database seems very large (> 2GB) - deployment may be slow")
else:
    issues.append("❌ CRITICAL: Local database not found - cannot deploy!")

# Check for app.py issues
app_path = Path("space/app.py")
if app_path.exists():
    with open(app_path, 'r', encoding='utf-8') as f:
        content = f.read()
        if 'key="page-nav"' not in content:
            issues.append("⚠️ app.py may not have the radio key fix")
else:
    issues.append("app.py not found")

if issues:
    for i, issue in enumerate(issues, 1):
        print(f"{i}. {issue}")
else:
    print("✓ No obvious issues detected")
print()

print("=" * 60)
print("Next Steps:")
print("1. If database is missing: Check data/laws.db")
print("2. If Space has no data: Run quick_redeploy.py with HF_TOKEN set")
print("3. Wait 3-5 minutes for Space to rebuild after deployment")
print("4. Check Space logs: {space_url}/logs")
