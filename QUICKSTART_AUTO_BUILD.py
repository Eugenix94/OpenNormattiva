#!/usr/bin/env python3
"""
Quick Start: Automatic Data Building
=====================================

This guide explains how to use auto_build_data.py to complete the dataset.

STATUS: You currently have 3/22 collections (164 laws)
GOAL:   Download all 22 collections (162,391 laws)
TIME:   ~2-3 hours total

STAGES:
1. Download all 22 vigente collections (1-2 hours)
2. Parse to JSONL (30-45 min)
3. Load to SQLite database (30-45 min)
4. Build indexes (5-10 min)
5. Generate report (1-2 min)
"""

# ============================================================================
# STEP 1: RUN THE AUTOMATIC BUILDER
# ============================================================================

"""
Open terminal and run:

    python scripts/auto_build_data.py --resume

This will:
- Resume from where the previous download left off (3/22 collections)
- Continue downloading remaining 19 collections
- Automatically parse each as it finishes
- Load into SQLite progressively
- Build indexes at the end

The script CHECKPOINTS after each collection, so if it crashes:
- You won't lose progress
- Just run again: python scripts/auto_build_data.py --resume
- It will skip already-completed collections
"""

# ============================================================================
# STEP 2: MONITOR PROGRESS
# ============================================================================

"""
In another terminal, check progress every 30 seconds:

    watch -n 30 'python scripts/auto_build_data.py --status'

This shows:
- Stage: downloading / parsing / database / indexes / complete
- Downloaded: X/22 collections
- Parsed: X/22 collections
- Laws in DB: X/162,391
- JSONL stats: lines count, file size
"""

# ============================================================================
# STEP 3: OPTIONS & CUSTOMIZATION
# ============================================================================

"""
--resume     : Continue from checkpoint (recommended first time)
--full       : Start completely fresh, delete all checkpoints
--force      : Re-process everything (ignores checkpoints)
--status     : Check status without running

Examples:

# Monitor only (no processing)
python scripts/auto_build_data.py --status

# Start fresh (dangerous - deletes progress)
python scripts/auto_build_data.py --full

# Force re-download all (not recommended, slow)
python scripts/auto_build_data.py --force

# Normal resume (recommended)
python scripts/auto_build_data.py --resume
"""

# ============================================================================
# STEP 4: WHAT GETS CREATED
# ============================================================================

"""
After build completes, you'll have:

📦 data/
  ├─ raw/
  │  ├─ Codici_vigente.zip ✓ (9.9 MB)
  │  ├─ DL_proroghe_vigente.zip ✓ (1.4 MB)
  │  ├─ ... (19 more ZIP files)
  │  └─ [Total: ~700 MB]
  │
  ├─ processed/
  │  └─ laws_vigente.jsonl [162K lines, ~800 MB]
  │
  ├─ indexes/
  │  ├─ citations_index.json [all citations]
  │  ├─ search_index.json [top search terms]
  │  └─ build_report.json [statistics]
  │
  ├─ laws.db [SQLite, ~1.5 GB with full-text index]
  │
  └─ .build_checkpoint.json [checkpoint state]

📋 Statistics after build:
  - 162,391 total laws
  - ~5M citation relationships
  - All searchable via SQLite FTS5
  - Amendment history ready for tracking
"""

# ============================================================================
# STEP 5: VERIFY BUILD SUCCESS
# ============================================================================

"""
After build completes, verify:

# 1. Check database size
ls -lh data/laws.db
# Should be ~1.5 GB

# 2. Check JSONL lines
wc -l data/processed/laws_vigente.jsonl
# Should be ~162,391

# 3. Test database
python -c "
from core.db import LawDatabase
db = LawDatabase()
count = db.conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
print(f'Laws in DB: {count:,}')
citations = db.conn.execute('SELECT COUNT(*) FROM citations').fetchone()[0]
print(f'Citations: {citations:,}')
db.close()
"
# Should show: 162,391 laws + ~5M citations

# 4. Test search
python -c "
from core.db import LawDatabase
db = LawDatabase()
results = db.search_fts('protezione civile', limit=3)
for r in results:
    print(f\"  - {r['title'][:60]}\")
db.close()
"
# Should show search results

# 5. Check reports
cat data/indexes/build_report.json
# Shows final statistics
"""

# ============================================================================
# STEP 6: WHAT TO DO NEXT (After Build Complete)
# ============================================================================

"""
After automatic build finishes, you need 3 more enhancements before 
launching jurisprudence:

PRIORITY 1: Amendment Tracking (4 hours)
  - Track how laws changed over time
  - Needed for: compliance, versioning, audit trails
  - File to create: scripts/track_amendments.py

PRIORITY 2: Search Optimization (5 hours)
  - Add result ranking (current: no ranking)
  - Add filtering by type/year
  - Add fuzzy matching for typos
  - File to update: core/db.py search methods

PRIORITY 3: Citation Graph (6 hours)
  - Build network analysis
  - Find most-cited laws (PageRank)
  - Detect clusters of related laws
  - File to create: scripts/build_citation_graph.py

Full roadmap in: ENHANCEMENTS_ROADMAP.md
"""

# ============================================================================
# STEP 7: TROUBLESHOOTING
# ============================================================================

"""
Q: Build is slow - how long should it take?
A: ~2-3 hours for full 22 collections on typical internet (50 Mbps)
   Each collection: 30 min download + 10-15 min parse

Q: What if download fails on collection X?
A: Automatic! The script catches errors and skips with a warning.
   Next run will retry that collection.

Q: Can I run it in background?
A: Yes! On Windows:
   
   # Option 1: Run in PowerShell but don't close terminal
   python scripts/auto_build_data.py --resume
   
   # Option 2: Use nohup (Linux/Mac)
   nohup python scripts/auto_build_data.py --resume &
   
   # Option 3: Use screen (Linux)
   screen -S build
   python scripts/auto_build_data.py --resume
   # Ctrl+A then D to detach, 'screen -r build' to re-attach

Q: How much disk space needed?
A: ~3-4 GB total:
   - ZIP files: ~700 MB (raw/)
   - JSONL: ~800 MB (processed/)
   - SQLite DB: ~1.5 GB (laws.db)
   - Indexes: ~100 MB

Q: My internet disconnected mid-download - do I restart?
A: NO! Just run: python scripts/auto_build_data.py --resume
   It will resume from the last completed collection.

Q: Can I delete raw/ ZIP files to save space?
A: After build is complete, yes! They're archived in laws.db.
   Run: rm -rf data/raw/*.zip
   This saves ~700 MB and is safe.

Q: Where's the data coming from?
A: Official Normattiva API (normattiva.it)
   - Vigente variant (V) = current law, all amendments applied
   - No parsing ambiguity, all from authority source
   - ~3 MB average per collection
"""

# ============================================================================
# STEP 8: CHECKPOINT RECOVERY
# ============================================================================

"""
The script saves checkpoints in: data/.build_checkpoint.json

Current checkpoint status example:
{
  "stage": "parsing",
  "downloaded": [
    "Codici_vigente.zip",
    "DL proroghe_vigente.zip",
    "Leggi costituzionali_vigente.zip"
  ],
  "parsed": [
    "Codici_vigente.zip",
    "DL proroghe_vigente.zip"
  ],
  "stats": {
    "total_laws": 164,
    ...
  }
}

To MANUALLY reset and start over:
  rm data/.build_checkpoint.json
  python scripts/auto_build_data.py --resume

⚠️  WARNING: Never delete data/laws.db manually!
    Instead use: python scripts/auto_build_data.py --full
"""

# ============================================================================
# FINAL CHECKLIST BEFORE LAUNCH
# ============================================================================

"""
✓ Automatic data build complete (this script)
  
✓ Database built with 162K laws

✓ Citation index created

✓ Ready for next phase:
  - Amendment tracking
  - Search optimization  
  - Graph analysis
  - Then: jurisprudence system launch

Expected timeline:
- Data build: 2-3 hours (automatic)
- Enhancements: 1-2 weeks (manual development)
- Testing: 2-3 days
- Launch: Ready by end of week 2
"""

# ============================================================================
# START HERE
# ============================================================================

"""
Ready to begin? Execute this now:

python scripts/auto_build_data.py --resume

Monitor with:

python scripts/auto_build_data.py --status

Good luck! 🚀
"""
