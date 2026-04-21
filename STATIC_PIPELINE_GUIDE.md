# Static Pipeline Implementation Guide

## 📋 Overview

Your pipeline is being transformed from **dynamic (reload everything)** to **static (incremental updates)**. This prevents wasteful re-downloads and enables separate handling of vigente vs abrogate data.

---

## 🎯 What Changed

### Before (Current - Wasteful)
```
Every run:
  ├─ Delete all downloaded data
  ├─ Download ALL 22 vigente collections (2-4 hours)
  ├─ Parse everything
  └─ Re-index
  
Result: SLOW + WASTEFUL
```

### After (New - Efficient)
```
Full Build (once):
  ├─ Download all 22 vigente collections (2-4 hours, cached)
  ├─ Parse to laws_vigente.jsonl
  ├─ Download abrogate collection (cached)
  ├─ Parse to laws_abrogate.jsonl (SEPARATE)
  └─ Done

Incremental Sync (weekly):
  ├─ Check EACH collection for ETag changes
  ├─ Only download if changed
  ├─ Only re-parse changed collections
  └─ Merge into existing JSONL
  
Result: FAST + SMART (checked if modified: YES → download, NO → skip)
```

---

## 📊 Dataset Format Recommendations

### The 23 Collections Breakdown

| Dataset | Acts | Variant | Format | Status |
|---------|------|---------|--------|--------|
| **22 Vigente Collections** | ~162K | O/V/M | AKN ✓ | Use **V (Vigente)** |
| Atti normativi abrogati | 124K | O only | AKN ✓ | Use **O (but separate)** |

**Why these formats?**

1. **AKN (Akoma Ntoso XML)** ✓ RECOMMENDED
   - Standard for legislative data (EU, UK, others)
   - Best structured format
   - Your parser already handles it
   - All 23 collections have it

2. XML (Alternative)
   - Generic XML format
   - All 23 collections have it
   - Less structured than AKN
   - **Use if AKN fails**

3. **NOT recommended** for bulk import:
   - PDF, EPUB, RTF: Document formats (not data)
   - JSON (ELI): Only for single documents
   - HTML: For viewing, not parsing
   - URI: Not documented

**Why not use JSON?**
- JSON format in API is per-document (not batch)
- Would require 286K individual API calls
- Not practical for bulk download
- Stick with AKN format

---

## 🚀 New Usage

### 1. First Time: Full Build
```bash
python static_pipeline.py --mode full
```
**What it does:**
- Downloads all 22 vigente collections (checks ETag, caches)
- Downloads abrogate collection
- Parses to:
  - `data/processed/laws_vigente.jsonl` (160K+ laws)
  - `data/processed/laws_abrogate.jsonl` (124K laws)
- Saves state to `.static_state.json`

**Output:**
```
data/
├── processed/
│   ├── laws_vigente.jsonl      ← Use this for your search/indexing
│   ├── laws_abrogate.jsonl     ← Separate dataset, don't mix
│   └── amendments.jsonl        ← Track changes
├── raw/
│   ├── Codici_vigente.zip
│   ├── DPR_vigente.zip
│   ├── ...
│   └── abrogate_originale.zip
└── .static_state.json          ← Pipeline state
```

**Time: ~2-4 hours** (first time only)

---

### 2. Weekly: Incremental Sync
```bash
python static_pipeline.py --mode sync
```

**What it does:**
- Checks EACH of 22 vigente collections for changes (by ETag)
- If changed → downloads only that collection
- If unchanged → skips (no download)
- Re-parses changed collections only
- Merges into existing laws_vigente.jsonl
- Same for abrogate collection

**Expected time: 30 seconds to 5 minutes** (depending on changes)

**Example output:**
```
Checking Codici... ✓ unchanged (cached)
Checking DPR... ↓ Downloading (changed!)
Checking Leggi... ✓ unchanged (cached)
...
Updates found:
  Vigente collections changed: 1 (DPR)
  Abrogate collection changed: False

Re-parsing 1 changed vigente collection...
✓ INCREMENTAL SYNC COMPLETE
```

---

### 3. Just Abrogate
```bash
python static_pipeline.py --mode abrogate-only
```
Updates only abrogate collection, leaves vigente untouched.

---

### 4. Check Status
```bash
python static_pipeline.py --status
```

**Output:**
```
================================================================================
STATIC PIPELINE STATUS
================================================================================

Mode: full_build
Last Updated: 2026-04-11T12:30:45.123456

📋 VIGENTE COLLECTIONS (22 total)
  Status: 22/22 downloaded
  Total Laws: 162,391

🗑️  ABROGATE COLLECTION (1 total)
  Status: ✓ Downloaded
  Total Laws: 124,036

📁 OUTPUT FILES
  ✓ Vigente JSONL: 1457.4 MB (162,391 lines)
  ✓ Abrogate JSONL: 485.2 MB (124,036 lines)
  - Amendments Log: not created yet

================================================================================
```

---

## 📁 What's in StatePersistence (.static_state.json)

```json
{
  "version": 1,
  "mode": "full_build",
  "vigente_completed": true,
  "abrogate_completed": true,
  "vigente_collections": {
    "Codici": {
      "etag": "\"7f1b2c3d-abc123\"",
      "sha256": "abcd1234...",
      "downloaded_at": "2026-04-11T10:15:00.123456",
      "file": "raw/Codici_vigente.zip"
    },
    "DPR": { ... },
    ...
  },
  "abrogate_etag": "\"def456-xyz789\"",
  "abrogate_file": "raw/abrogate_originale.zip",
  "abrogate_law_count": 124036,
  "vigente_law_count": 162391,
  "total_laws": 286427,
  "last_update": "2026-04-11T12:30:45.123456"
}
```

This lets you:
- Skip unchanged collections
- Track what's been done
- Resume interrupted downloads
- Know exactly when data was last synced

---

## 🔄 Integration with GitHub Actions

Replace your nightly workflow with:

```yaml
jobs:
  nightly-update:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Run incremental sync
        run: python static_pipeline.py --mode sync
      
      - name: Push updates to HF Dataset
        run: python push_to_hf.py
```

**Result:** Nightly runs that sync CHANGES ONLY, not entire dataset.

---

## 🛠️ Why Format Choices Matter

### Your Situation: Missing Format Issues

You mentioned "23 datasets not all installed due to missing XML or AKN format". Based on my analysis:

**Actually:** All 23 collections have BOTH AKN and XML formats.

**The real issue was probably:**
1. ❌ Network timeouts during large downloads
2. ❌ ZIP extraction failures (corrupt downloads)
3. ❌ Disk space issues
4. ❌ Memory issues during parsing

**NOT** missing formats.

### Solutions:
1. Use **AKN format** (recommended - all collections have it)
2. Fallback to **XML format** (all collections have it)
3. Use **incremental sync** so retries only affect changed collections
4. Add **resumable downloads** (already in static_pipeline.py)

---

## 📝 Next Steps

1. **Backup current data** (if any):
   ```bash
   cp data/processed/laws_vigente.jsonl data/processed/laws_vigente.backup.jsonl
   ```

2. **Run initial full build** (if starting fresh):
   ```bash
   python static_pipeline.py --mode full
   ```

3. **Verify output**:
   ```bash
   python static_pipeline.py --status
   ```

4. **Schedule weekly sync** via GitHub Actions (or cron):
   ```bash
   0 2 * * 0  python static_pipeline.py --mode sync
   ```
   (Sunday 2 AM UTC - customize as needed)

5. **Monitor** the separate datasets:
   - `laws_vigente.jsonl` - Use for search/jurisprudence
   - `laws_abrogate.jsonl` - Keep separate, for historical research

---

## 🎓 Key Concept: Static vs Dynamic

| Aspect | Dynamic (Old) | Static (New) |
|--------|---------------|-------------|
| First run | 4 hours | 4 hours |
| 2nd run (no changes) | 4 hours | 30 seconds |
| 2nd run (1 collection changed) | 4 hours | 2 minutes |
| Disk usage | 2× (re-downloads) | 1× (once) |
| Sync complexity | Regenerate all | Incremental merge |
| Abrogate handling | Mixed with vigente | Separate |
| Maintainability | Low | High |

**Result: Your pipeline goes from "restart everything" to "sync what changed"**

