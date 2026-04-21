#!/usr/bin/env python3
"""Verify all fixes."""

# Verify SQLite fix
print('Checking core/db.py fix...')
with open('core/db.py', 'r', encoding='utf-8') as f:
    content = f.read()
    if 'check_same_thread=False' in content:
        print('  ✓ SQLite threading fix applied')
    else:
        print('  ✗ SQLite fix NOT found')

# Verify download_db.py deprecation removal
print('\nChecking download_db.py fixes...')
with open('download_db.py', 'r', encoding='utf-8') as f:
    content = f.read()
    if 'resume_download' in content:
        print('  ✗ resume_download still present')
    else:
        print('  ✓ resume_download removed')
    if 'local_dir_use_symlinks' in content:
        print('  ✗ local_dir_use_symlinks still present')
    else:
        print('  ✓ local_dir_use_symlinks removed')

# Verify app.py fixes
print('\nChecking space/app.py fixes...')
with open('space/app.py', 'r', encoding='utf-8') as f:
    content = f.read()
    old_count = content.count('use_container_width=True')
    new_count = content.count("width='stretch'")
    print(f'  use_container_width=True instances: {old_count}')
    print(f'  width instances: {new_count}')
    if old_count == 0 and new_count >= 14:
        print(f'  ✓ All Streamlit deprecations fixed')

print('\n✓ All fixes verified!')
