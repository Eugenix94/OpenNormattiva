#!/usr/bin/env python
"""Check for HF token and deploy if available."""

import os
import sys
from pathlib import Path

# Check for token in multiple places
token = os.environ.get('HF_TOKEN', '')

if not token:
    # Check if it's in .huggingface
    hf_config = Path.home() / '.huggingface' / 'token'
    if hf_config.exists():
        token = hf_config.read_text().strip()
        print(f'Found token in {hf_config}')

if not token:
    print('ERROR: No HF_TOKEN found')
    print('')
    print('To deploy, you need to:')
    print('1. Get token from: https://huggingface.co/settings/tokens')
    print('2. Set it: $env:HF_TOKEN = "hf_xxx"')
    print('3. Run: python check_deploy.py')
    sys.exit(1)

print('Token found! Ready to deploy.')
print('Starting deployment...')
print('')

# Try to import and run deployment
try:
    from deploy_hf import deploy_dataset_to_huggingface, setup_space_environment
    deploy_dataset_to_huggingface(token)
except Exception as e:
    print(f'Deployment error: {e}')
    sys.exit(1)
