#!/usr/bin/env python3
"""Search for HF token in common locations."""

import os
import sys
from pathlib import Path

print('Searching for HF_TOKEN...')
print()

locations = [
    ('Environment Variable', lambda: os.environ.get('HF_TOKEN')),
    ('~/.huggingface/token', lambda: Path.home().joinpath('.huggingface', 'token').read_text().strip() 
     if Path.home().joinpath('.huggingface', 'token').exists() else None),
    ('.env file', lambda: (Path('.env').read_text().split('HF_TOKEN=')[1].split('\n')[0].strip()
     if Path('.env').exists() and 'HF_TOKEN=' in Path('.env').read_text() else None)),
]

token = None
found_location = None

for name, getter in locations:
    try:
        val = getter()
        if val:
            token = val
            found_location = name
            print(f'✓ Found token at: {name}')
            break
        else:
            print(f'✗ Not found at: {name}')
    except Exception as e:
        print(f'✗ Error checking {name}: {e}')

print()
if token:
    print(f'Token: {token[:20]}...')
    print()
    print('Proceeding with deployment...')
    sys.exit(0)
else:
    print('No token found in any location.')
    print()
    print('To complete deployment, provide token via:')
    print('  1. Environment: $env:HF_TOKEN = "hf_xxx"')
    print('  2. Argument: python deploy_now.py --token hf_xxx')
    print('  3. File: ~/.huggingface/token')
    print()
    print('Get token from: https://huggingface.co/settings/tokens')
    sys.exit(1)
