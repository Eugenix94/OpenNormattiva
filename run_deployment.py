#!/usr/bin/env python3
"""
🚀 FINAL DEPLOYMENT RUNNER
Complete deployment of OpenNormattiva to Hugging Face with one command.

BEFORE RUNNING:
1. Get token from: https://huggingface.co/settings/tokens (Create new token, check 'write' access)
2. Set token: $env:HF_TOKEN = "hf_xxxxxxxxxxxxx" (in PowerShell)
3. Then run: python run_deployment.py

WHAT HAPPENS:
- Authenticates with your HF account
- Creates/updates Space at: https://huggingface.co/spaces/YOUR_USERNAME/normattiva-search
- Uploads all files including 774 MB database
- Creates dataset at: https://huggingface.co/datasets/YOUR_USERNAME/normattiva-data
- Restarts Space (takes 3-5 minutes)

After deployment, open the Space to see:
✓ Full-text search of 157,121 Italian laws
✓ Browse by domain and category
✓ Citation graphing
✓ Notifications page (tracks API updates)
✓ Update log (see what changed)
"""

import os
import sys
import subprocess
from pathlib import Path

def main():
    print("="*70)
    print("  🚀 OPENNORMATTIVA DEPLOYMENT TO HUGGING FACE")
    print("="*70)
    print()
    
    # Check for token
    token = os.environ.get('HF_TOKEN', '')
    
    if not token:
        print("❌ ERROR: HF_TOKEN not found in environment")
        print()
        print("ACTION REQUIRED:")
        print()
        print("1. Get a token from: https://huggingface.co/settings/tokens")
        print("   - Click 'New token'")
        print("   - Give it a name (e.g., 'normattiva-deploy')")
        print("   - Select 'write' role")
        print("   - Copy the token")
        print()
        print("2. Set it in PowerShell:")
        print('   $env:HF_TOKEN = "hf_xxxxxxxxxxxxx"')
        print()
        print("3. Run this script again:")
        print("   python run_deployment.py")
        print()
        sys.exit(1)
    
    print(f"✓ HF_TOKEN found (first 20 chars: {token[:20]}...)")
    print()
    print("Starting deployment process...")
    print()
    
    # Run deploy_now.py
    try:
        result = subprocess.run(
            [sys.executable, 'deploy_now.py', '--token', token],
            check=False
        )
        
        if result.returncode == 0:
            print()
            print("="*70)
            print("✅ DEPLOYMENT SUBMITTED SUCCESSFULLY!")
            print("="*70)
            print()
            print("Space is now building. This typically takes 3-5 minutes.")
            print()
            print("Once ready, visit:")
            print("  https://huggingface.co/spaces/YOUR_USERNAME/normattiva-search")
            print()
            print("Check the Space logs if you see any errors:")
            print("  https://huggingface.co/spaces/YOUR_USERNAME/normattiva-search/logs")
            print()
        else:
            print()
            print("❌ Deployment failed. Check error messages above.")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Error running deployment: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
