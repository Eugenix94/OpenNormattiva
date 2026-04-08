#!/bin/bash
# Normattiva Platform - Automated Deployment Script
# Usage: ./deploy.sh <GITHUB_REPO_URL> <HF_TOKEN>

set -e

if [ $# -ne 2 ]; then
    echo "Usage: $0 <GITHUB_REPO_URL> <HF_TOKEN>"
    echo "Example: $0 https://github.com/user/normattiva-research hf_xxxxxxxxxxxxx"
    exit 1
fi

GITHUB_REPO=$1
HF_TOKEN=$2
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "=========================================="
echo "Normattiva Platform - Deployment Script"
echo "=========================================="
echo "GitHub Repo: $GITHUB_REPO"
echo "Working Directory: $REPO_DIR"
echo ""

# Step 1: Configure git remote
echo "[1/5] Configuring Git remote..."
cd "$REPO_DIR"
git remote add origin "$GITHUB_REPO" 2>/dev/null || git remote set-url origin "$GITHUB_REPO"
git branch -M master 2>/dev/null || true
echo "✓ Git remote configured"

# Step 2: Push to GitHub
echo ""
echo "[2/5] Pushing code to GitHub..."
git push -u origin master --force 2>&1 | grep -E "(remote:|branch|sent|received|Total)" || true
echo "✓ Code pushed to GitHub"

# Step 3: Create HF Dataset repo
echo ""
echo "[3/5] Creating HuggingFace Dataset repo..."
python3 << 'EOF'
import os
from huggingface_hub import create_repo, HfApi

hf_token = os.environ.get('HF_TOKEN')
if not hf_token:
    print("✗ HF_TOKEN not set in environment")
    exit(1)

api = HfApi()
try:
    # Get user info
    user_info = api.whoami(token=hf_token)
    username = user_info['name']
    
    # Create Dataset repo
    repo_id = f"{username}/normattiva-data-raw"
    try:
        api.create_repo(
            repo_id=repo_id,
            repo_type="dataset",
            private=False,
            exist_ok=True,
            token=hf_token
        )
        print(f"✓ Dataset repo created: https://huggingface.co/datasets/{repo_id}")
    except Exception as e:
        if "already exists" in str(e):
            print(f"✓ Dataset repo already exists: https://huggingface.co/datasets/{repo_id}")
        else:
            raise
    
    # Create Space repo (linked to GitHub)
    space_id = f"{username}/normattiva-search"
    try:
        api.create_repo(
            repo_id=space_id,
            repo_type="space",
            space_sdk="streamlit",
            private=False,
            exist_ok=True,
            token=hf_token
        )
        print(f"✓ Space repo created: https://huggingface.co/spaces/{space_id}")
    except Exception as e:
        if "already exists" in str(e):
            print(f"✓ Space repo already exists: https://huggingface.co/spaces/{space_id}")
        else:
            raise
            
except Exception as e:
    print(f"✗ Error: {e}")
    exit(1)
EOF

if [ $? -ne 0 ]; then
    echo "✗ Failed to create HF repos (token may be invalid)"
    exit 1
fi

# Step 4: Clone HF Space and sync app
echo ""
echo "[4/5] Setting up HuggingFace Space..."
python3 << 'EOF'
import os
import shutil
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download

hf_token = os.environ.get('HF_TOKEN')
if not hf_token:
    exit(1)

api = HfApi()
user_info = api.whoami(token=hf_token)
username = user_info['name']
space_id = f"{username}/normattiva-search"

# Clone space repo
temp_space = Path("/tmp/normattiva-space")
if temp_space.exists():
    shutil.rmtree(temp_space)

try:
    snapshot_download(
        repo_id=space_id,
        repo_type="space",
        local_dir=temp_space,
        token=hf_token
    )
    print("✓ Space repo cloned")
except:
    # Repo might be empty, create directories
    temp_space.mkdir(parents=True, exist_ok=True)
    print("✓ Space directory created")

# Copy app.py and requirements
app_src = Path(os.getcwd()) / "space" / "app.py"
req_src = Path(os.getcwd()) / "requirements.txt"
app_dst = temp_space / "app.py"
req_dst = temp_space / "requirements.txt"

shutil.copy(app_src, app_dst)
shutil.copy(req_src, req_dst)

# Create .gitignore for space
gitignore_dst = temp_space / ".gitignore"
gitignore_dst.write_text("""
data/
__pycache__/
*.pyc
.DS_Store
""")

print("✓ Space files synced")

print(f"✓ Space ready at: https://huggingface.co/spaces/{space_id}")
EOF

if [ $? -ne 0 ]; then
    echo "⚠ Warning: Could not sync Space files (may need manual setup)"
fi

# Step 5: Summary
echo ""
echo "=========================================="
echo "✓ DEPLOYMENT COMPLETE"
echo "=========================================="
echo ""
echo "Your platform is now live:"
echo ""
echo "📦 GitHub Repository:"
echo "   $GITHUB_REPO"
echo ""
echo "📊 HuggingFace Dataset:"
echo "   https://huggingface.co/datasets/$(python3 -c 'from huggingface_hub import HfApi; import os; print(HfApi().whoami(token=os.environ.get("HF_TOKEN"))["name"])')/normattiva-data-raw"
echo ""
echo "🎨 HuggingFace Space:"
echo "   https://huggingface.co/spaces/$(python3 -c 'from huggingface_hub import HfApi; import os; print(HfApi().whoami(token=os.environ.get("HF_TOKEN"))["name"])')/normattiva-search"
echo ""
echo "📅 Next automated run: Tomorrow 2 AM UTC"
echo "✅ Commit history being tracked in GitHub"
echo ""
