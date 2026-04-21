#!/usr/bin/env python3
"""
Implementation Guide: Static Website + Background Updates

This architecture ensures the website is ALWAYS available while nightly
pipeline updates data safely in the background.

Files created:
1. core/changelog.py - Tracks what changed in each update
2. core/legislature.py - Extracts government/parliament metadata
3. space/app_static.py - Static Space (no live parsing)
4. record_changelog.py - Records updates for GitHub Actions

Integration steps:
"""

# STEP 1: Replace the Space app
# ============================================================================
# OLD: space/app.py (does live parsing, becomes unavailable during updates)
# NEW: space/app_static.py (always available, shows changelog)
#
# In Dockerfile or on Space:
#   STREAMLIT_APP="space/app_static.py"
#
# Or in .streamlit/config.toml:
#   [server]
#   maintenanceToken = ""
#   headless = true


# STEP 2: Update GitHub Actions workflow
# ============================================================================
# Add to .github/workflows/nightly-update.yml after "Enrich DB" step:
#
# - name: Record changelog
#   if: steps.parse.outputs.parse_done == 'true'
#   run: |
#     python record_changelog.py 2>&1 | tee logs/changelog.log
#   env:
#     HF_TOKEN: ${{ secrets.HF_TOKEN }}
#
# This ensures:
# - Changes are tracked
# - Changelog is uploaded to HF Dataset
# - Space always shows what was updated


# STEP 3: Add legislature metadata to database
# ============================================================================
# Modify core/db.py to add to schemas:
#
# ADD THIS TO init_schema():
#   self.conn.execute('''
#       ALTER TABLE laws ADD COLUMN IF NOT EXISTS legislature_id INTEGER
#   ''')
#   self.conn.execute('''
#       ALTER TABLE laws ADD COLUMN IF NOT EXISTS legislature_year INTEGER
#   ''')
#   self.conn.execute('''
#       ALTER TABLE laws ADD COLUMN IF NOT EXISTS government TEXT
#   ''')
#   self.conn.execute('''
#       ALTER TABLE laws ADD COLUMN IF NOT EXISTS parliament_session INTEGER
#   ''')


# STEP 4: Update parse_akn.py to extract legislature metadata
# ============================================================================
# In parse_akn.py, modify extract_metadata():
#
#   from core.legislature import LegislatureMetadata
#
#   # After extracting law_dict from AKN:
#   leg_meta = LegislatureMetadata.extract_from_urn(urn)
#   law_dict['legislature_id'] = leg_meta.get('legislature')
#   law_dict['legislature_year'] = leg_meta.get('year')
#   law_dict['government'] = leg_meta.get('government')
#
# This adds jurisprudential context to each law


# STEP 5: Test the static Space locally
# ============================================================================
# cd space
# streamlit run app_static.py
#
# You should see:
# - Dashboard with stats
# - Search function
# - Browse feature
# - Updates tab showing changelog
# - Sidebar with "Data Status: STATIC and always available"


# STEP 6: Deploy to HF Space
# ============================================================================
# Push code to GitHub:
#   git add core/changelog.py core/legislature.py space/app_static.py record_changelog.py
#   git commit -m "feat: static website with changelog and legislature metadata"
#   git push origin master
#
# Then in HF Space settings:
# 1. Update startup command: streamlit run space/app_static.py
# 2. Or update Dockerfile: CMD ["streamlit", "run", "space/app_static.py"]
#
# Space will restart and show the new static interface


# STEP 7: Update GitHub Actions workflow
# ============================================================================
# Edit .github/workflows/nightly-update.yml:
#
# After enrichment step, add:
#   - name: Record changelog
#     run: python record_changelog.py
#
# After uploads, add:
#   - name: Upload changelog
#     run: |
#       python -c "
#       from huggingface_hub import HfApi
#       import os
#       from pathlib import Path
#       api = HfApi(token=os.environ['HF_TOKEN'])
#       api.upload_file(
#           path_or_fileobj=str(Path('data/changelog.jsonl')),
#           path_in_repo='changelog.jsonl',
#           repo_id='diatribe00/normattiva-data',
#           repo_type='dataset',
#           commit_message='Update changelog'
#       )
#       "
#     env:
#       HF_TOKEN: ${{ secrets.HF_TOKEN }}


# BENEFITS OF THIS ARCHITECTURE:
# ============================================================================
# ✅ Website ALWAYS available (no downtime during updates)
# ✅ Shows what changed in each update (changelog)
# ✅ Tracks legislative context (government, parliament session)
# ✅ Jurisprudential evolution visible (laws over time)
# ✅ Safe background updates (no interference with active users)
# ✅ Transparent data updates (users see exactly what changed)


# COMPARISON: OLD vs NEW
# ============================================================================
#
# OLD (Live Parsing):
# - Space becomes unavailable during nightly run
# - Users can't search while parsing happens
# - No visibility into what changed
# - No legislative context
#
# NEW (Static + Background):
# - Space is ALWAYS available
# - Users can sempre search and browse
# - Changelog shows every update
# - Legislative metadata adds context
# - Nightly pipeline runs safely in background


if __name__ == "__main__":
    print(__doc__)
