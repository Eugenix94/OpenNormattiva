#!/usr/bin/env python3
"""Fix Streamlit deprecation warnings."""

# Read space/app.py
with open('space/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace all use_container_width=True with width='stretch'
content = content.replace('use_container_width=True', "width='stretch'")

# Write back
with open('space/app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('✓ Fixed Streamlit deprecations in app.py')
print('✓ All use_container_width=True → width=\'stretch\'')
