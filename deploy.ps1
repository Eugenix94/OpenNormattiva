param(
    [Parameter(Mandatory=$true)]
    [string]$GitHubRepo,
    
    [Parameter(Mandatory=$true)]
    [string]$HFToken
)

# Normattiva Platform - Automated Deployment Script (Windows)
# Usage: .\deploy.ps1 -GitHubRepo "https://github.com/user/repo" -HFToken "hf_xxx"

$ErrorActionPreference = "Stop"
$RepoDir = Get-Location

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Normattiva Platform - Deployment Script" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "GitHub Repo: $GitHubRepo"
Write-Host "Working Directory: $RepoDir"
Write-Host ""

# Step 1: Configure git remote
Write-Host "[1/5] Configuring Git remote..." -ForegroundColor Yellow
try {
    git remote add origin $GitHubRepo 2>$null
}
catch {
    git remote set-url origin $GitHubRepo
}
git branch -M master 2>$null
Write-Host "✓ Git remote configured" -ForegroundColor Green

# Step 2: Push to GitHub
Write-Host ""
Write-Host "[2/5] Pushing code to GitHub..." -ForegroundColor Yellow
git push -u origin master --force 2>&1 | Select-String -Pattern "remote:|branch|sent|received|Total" | Write-Host
if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ Code pushed to GitHub" -ForegroundColor Green
}
else {
    Write-Host "⚠ Git push may have issues - check manually" -ForegroundColor Yellow
}

# Step 3-5: Create HF repos and sync (Python script)
Write-Host ""
Write-Host "[3/5] Creating HuggingFace Dataset repo..." -ForegroundColor Yellow

$pythonScript = @"
import os
import sys
from pathlib import Path
from huggingface_hub import create_repo, HfApi, snapshot_download, upload_folder
import shutil

hf_token = '$HFToken'
if not hf_token or hf_token.startswith('`$'):
    print('✗ HF_TOKEN not provided')
    sys.exit(1)

try:
    api = HfApi()
    user_info = api.whoami(token=hf_token)
    username = user_info['name']
    print(f'✓ Authenticated as: {username}')
    
    # Create Dataset repo
    dataset_id = f'{username}/normattiva-data-raw'
    try:
        api.create_repo(
            repo_id=dataset_id,
            repo_type='dataset',
            private=False,
            exist_ok=True,
            token=hf_token
        )
        print(f'✓ Dataset repo ready: https://huggingface.co/datasets/{dataset_id}')
    except Exception as e:
        if 'already exists' in str(e):
            print(f'✓ Dataset repo already exists: https://huggingface.co/datasets/{dataset_id}')
        else:
            raise
    
    # Create Space repo
    space_id = f'{username}/normattiva-search'
    try:
        api.create_repo(
            repo_id=space_id,
            repo_type='space',
            space_sdk='streamlit',
            private=False,
            exist_ok=True,
            token=hf_token
        )
        print(f'✓ Space repo ready: https://huggingface.co/spaces/{space_id}')
    except Exception as e:
        if 'already exists' in str(e):
            print(f'✓ Space repo already exists: https://huggingface.co/spaces/{space_id}')
        else:
            raise
    
    # Sync Streamlit app to Space
    print('[4/5] Syncing Streamlit app to Space...')
    temp_space = Path('/tmp/normattiva-space-sync')
    if temp_space.exists():
        shutil.rmtree(temp_space)
    temp_space.mkdir(parents=True, exist_ok=True)
    
    # Copy files
    shutil.copy('space/app.py', temp_space / 'app.py')
    shutil.copy('requirements.txt', temp_space / 'requirements.txt')
    (temp_space / '.gitignore').write_text('data/\n__pycache__/\n*.pyc\n.DS_Store\n')
    
    # Upload to Space
    api.upload_folder(
        repo_id=space_id,
        repo_type='space',
        folder_path=str(temp_space),
        token=hf_token
    )
    print(f'✓ Streamlit app deployed to Space')
    
    print('')
    print('==========================================')
    print('✓ DEPLOYMENT COMPLETE')
    print('==========================================')
    print('')
    print('Your platform is now live:')
    print('')
    print(f'📦 GitHub Repository:')
    print(f'   $GitHubRepo')
    print('')
    print(f'📊 HuggingFace Dataset:')
    print(f'   https://huggingface.co/datasets/{dataset_id}')
    print('')
    print(f'🎨 HuggingFace Space:')
    print(f'   https://huggingface.co/spaces/{space_id}')
    print('')
    print('📅 Next automated run: Tomorrow 2 AM UTC')
    print('✅ Commit history: https://github.com/YOUR_USER/normattiva-research (Git Actions)')
    
except Exception as e:
    print(f'✗ Error: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
"@

python -c $pythonScript
if ($LASTEXITCODE -ne 0) {
    Write-Host "✗ Failed to create HuggingFace repos" -ForegroundColor Red
    Write-Host "Check that HF_TOKEN is valid" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "✓ All deployment steps completed!" -ForegroundColor Green
