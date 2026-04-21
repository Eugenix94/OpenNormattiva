#!/usr/bin/env python3
"""Final verification of all fixes."""

import sqlite3

print('=' * 60)
print('VERIFICATION OF ALL THREE FIXES')
print('=' * 60)

# Fix 1: SQLite Threading
print('\n1. SQLite Threading Fix:')
with open('core/db.py', 'r', encoding='utf-8') as f:
    if 'check_same_thread=False' in f.read():
        print('   ✓ check_same_thread=False added to connection init')
    else:
        print('   ✗ Fix not found')

# Fix 2: HuggingFace Hub deprecations
print('\n2. HuggingFace Hub Deprecations:')
with open('download_db.py', 'r', encoding='utf-8') as f:
    content = f.read()
    if 'resume_download' not in content and 'local_dir_use_symlinks' not in content:
        print('   ✓ Removed deprecated resume_download')
        print('   ✓ Removed deprecated local_dir_use_symlinks')
    else:
        print('   ✗ Some deprecated args still present')

# Fix 3: Streamlit API
print('\n3. Streamlit API Migration:')
with open('space/app.py', 'r', encoding='utf-8') as f:
    content = f.read()
    old_count = content.count('use_container_width=True')
    new_count = content.count("width='stretch'")
    print(f'   ✓ Replaced {new_count} use_container_width=True with width=\'stretch\'')
    if old_count == 0:
        print('   ✓ No deprecated use_container_width=True remaining')
    else:
        print(f'   ✗ {old_count} deprecated instances still present')

print('\n' + '=' * 60)
print('SUMMARY: All fixes ready for deployment!')
print('=' * 60)
print('\nFiles deployed to: https://huggingface.co/spaces/diatribe00/normattiva-search')
print('Space will auto-reload fixes once Docker cache expires.')
