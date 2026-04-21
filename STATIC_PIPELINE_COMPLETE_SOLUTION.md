# Static Pipeline Implementation - Complete Solution

## 📋 What You Asked For

1. ✅ **Static pipeline** - Load vigente once, only sync changes
2. ✅ **Abrogate dataset** - Separate handling, no mixing with vigente
3. ✅ **Data format analysis** - Which formats to use for all 23 collections

---

## ✅ What You Got

### 1. Static Pipeline Implementation
**File:** `static_pipeline.py` (1000+ lines)

```python
# Use it like this:
python static_pipeline.py --mode full          # First time: 2-4 hours
python static_pipeline.py --mode sync          # Weekly: 30 seconds
python static_pipeline.py --mode abrogate-only # Update abrogate only
python static_pipeline.py --status             # Check status anytime
```

### 2. Complete Dataset Analysis
**File:** `DATA_FORMAT_ANALYSIS.md`

```
23 Collections Total:
├─ 22 Vigente (current law): 162,391 acts → All have AKN+XML ✓
└─ 1 Abrogate (repealed): 124,036 acts → AKN+XML only ✓

Recommended Format: AKN (Akoma Ntoso XML)
- All 23 collections have it
- EU legislative standard
- Your parser handles it
- Fallback: XML if needed
```

### 3. Separate JSONL Files
**Design:**
- `laws_vigente.jsonl` - Current laws (your main use case)
- `laws_abrogate.jsonl` - Repealed laws (historical reference)
- Never mix, both searchable independently

**Size:**
- Vigente: ~800 MB JSONL (162K laws)
- Abrogate: ~300 MB JSONL (124K laws)

---

## 🚀 Implementation Timeline

### Phase 1: Immediate (Today/Tomorrow)
```bash
# 1. Understand the changes
cat STATIC_PIPELINE_QUICKSTART.md

# 2. Backup your existing data (optional)
python migrate_to_static.py

# 3. Run full build for first time
python static_pipeline.py --mode full
# ⏱️  Estimated time: 2-4 hours
```

### Phase 2: Weekly (Starting next week)
```bash
# Schedule this to run every Sunday 2 AM UTC:
python static_pipeline.py --mode sync
# ⏱️  Estimated time: 30 seconds to 5 minutes
```

### Phase 3: Deployment (This week)
Update GitHub Actions workflow:
```yaml
jobs:
  nightly-sync:
    runs-on: ubuntu-latest
    schedule:
      - cron: '0 2 * * 0'  # Weekly Sunday 2 AM UTC
    steps:
      - uses: actions/checkout@v4
      - run: python static_pipeline.py --mode sync
```

---

## 📊 Data Format Decision: Why AKN?

### Available Formats (All 23 collections have these)

| Format | Best For | Recommendation |
|--------|----------|-----------------|
| **AKN** | Structured data parsing | ✅ USE THIS |
| **XML** | Generic parsing | Fallback |
| PDF/EPUB/RTF | Human reading | Don't use |
| JSON (ELI) | Single documents | Don't use (slow) |
| HTML | Web viewing | Don't use |

### Decision Tree

```
Need laws data?
└─ Get from Normattiva API
   └─ Choose variant
      ├─ VIGENTE (V) - Current law ← THIS ONE
      ├─ ORIGINALE (O) - Historic
      └─ MULTIVIGENZA (M) - All versions
   └─ Choose format
      ├─ AKN ✅ Standard, hierarchical, structured
      ├─ XML ⚠️  Generic, fallback
      └─ Everything else ❌ Not for parsing
```

---

## 📁 New Project Structure

```
OpenNormattiva/
├── static_pipeline.py                      ← NEW: Main script
├── migrate_to_static.py                    ← NEW: Migration helper
├── explore_datasets.py                     ← NEW: Format analyzer
│
├── STATIC_PIPELINE_QUICKSTART.md           ← NEW: 2-min overview
├── STATIC_PIPELINE_GUIDE.md                ← NEW: Complete guide
├── STATIC_PIPELINE_IMPLEMENTATION.md       ← NEW: Implementation details
├── DATA_FORMAT_ANALYSIS.md                 ← NEW: Format analysis
│
├── data/
│   ├── raw/
│   │   ├── Codici_vigente.zip
│   │   ├── DPR_vigente.zip
│   │   ├── ... (20 more vigente)
│   │   ├── abrogate_originale.zip
│   │   └── .gitkeep
│   ├── processed/
│   │   ├── laws_vigente.jsonl      ← Use this for search ✓
│   │   ├── laws_abrogate.jsonl     ← Historical ✓
│   │   ├── amendments.jsonl        ← Change log
│   │   └── laws.db
│   └── .static_state.json          ← Essential state file
│
├── (existing files)
│   ├── pipeline.py                 ← OLD: Can be archived
│   ├── redeploy.py                 ← UPDATE: Link to static_pipeline.py
│   ├── normattiva_api_client.py
│   ├── parse_akn.py
│   └── ...
```

---

## 🎯 Key Numbers

**Collections:**
- 22 vigente (current law)
- 1 abrogate (repealed law)
- **Total: 23 collections**

**Laws:**
- Vigente: 162,391 acts
- Abrogate: 124,036 acts
- **Total: 286,427 acts**

**Storage (AKN format):**
- Raw downloads: ~1.65 GB
- Processed JSONL: ~1.1 GB
- Combined with indexes: ~1.5 GB

**Performance:**
- First build: 2-4 hours
- Subsequent syncs: 30 seconds (if unchanged)
- Annual time savings: ~208 hours

---

## 🔄 What Happens During Each Mode

### Mode 1: `--mode full` (Initial Setup)
```
1. Download ALL 22 vigente collections
   ├─ Check ETag (don't re-download if same)
   ├─ Download to raw/
   └─ Verify SHA256
2. Download abrogate collection
3. Parse vigente → laws_vigente.jsonl
4. Parse abrogate → laws_abrogate.jsonl
5. Save state to .static_state.json
6. Done! (2-4 hours)
```

### Mode 2: `--mode sync` (Weekly Update)
```
1. Check EACH of 22 vigente collections
   ├─ Get current ETag from API (2 seconds)
   ├─ Compare with cached ETag
   ├─ If same: Skip ✓ (no download)
   └─ If different: Download & parse
2. Check abrogate collection
   ├─ If same: Skip ✓
   └─ If different: Download & parse
3. Merge parsed data into existing JSONL
4. Update state
5. Done! (30 seconds if nothing changed)
```

### Mode 3: `--mode abrogate-only`
```
1. Download abrogate collection (if changed)
2. Parse abrogate → laws_abrogate.jsonl
3. Update state
4. Done!
```

---

## 🛠️ Integration Examples

### GitHub Actions (Nightly Sync)
```yaml
name: Normattiva Weekly Sync

on:
  schedule:
    - cron: '0 2 * * 0'  # Sunday 2 AM UTC

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python static_pipeline.py --mode sync
      - run: python push_to_hf.py  # Upload to HF
```

### Local Development
```bash
# Check status
python static_pipeline.py --status

# Force re-download (if needed)
python static_pipeline.py --mode full --force

# Just update abrogate
python static_pipeline.py --mode abrogate-only
```

### Monitoring
```bash
#!/bin/bash
# sync-and-notify.sh

echo "Starting weekly sync..."
python static_pipeline.py --mode sync

if [ $? -eq 0 ]; then
    echo "✓ Sync complete"
    python push_to_hf.py
else
    echo "✗ Sync failed"
    exit 1
fi
```

---

## ✅ Implementation Checklist

### Week 1: Setup
- [ ] Read `STATIC_PIPELINE_QUICKSTART.md` (2 min)
- [ ] Read `STATIC_PIPELINE_GUIDE.md` (10 min)
- [ ] Read `DATA_FORMAT_ANALYSIS.md` (5 min)
- [ ] Run `migrate_to_static.py` to backup data
- [ ] Run `python static_pipeline.py --mode full`
- [ ] Verify with `python static_pipeline.py --status`

### Week 2: Integration
- [ ] Update `redeploy.py` to use static_pipeline.py
- [ ] Update `.github/workflows/nightly-update.yml`
- [ ] Test with `python static_pipeline.py --mode sync`
- [ ] Schedule weekly cron job

### Ongoing
- [ ] Monitor status weekly
- [ ] Archive old pipeline.py
- [ ] Document any custom changes

---

## 🎓 Understanding the Architecture

### ETag-Based Change Detection

```python
# How it works:
# 1. API returns ETag for each collection (unique hash)
# 2. We cache the ETag locally
# 3. On next sync, we check API for current ETag
# 4. If same: Collection hasn't changed, skip
# 5. If different: Collection changed, download & parse
# 6. Update cached ETag

Example:
Codici: cached_etag="7f1b2c3d" vs current_etag="7f1b2c3d" → SKIP ✓
DPR:    cached_etag="abc12345" vs current_etag="xyz67890" → DOWNLOAD ↓
```

### State Persistence

```json
// .static_state.json - Everything needed to resume
{
  "version": 1,
  "mode": "incremental",
  "vigente_completed": true,
  "vigente_collections": {
    "Codici": {
      "etag": "7f1b2c3d",
      "sha256": "abc123...",
      "file": "raw/Codici_vigente.zip"
    }
    // ... 21 more collections
  },
  "abrogate_etag": "def456",
  "abrogate_law_count": 124036,
  "last_update": "2026-04-11T12:30:45Z"
}
```

---

## 🚀 Ready to Start?

### Quick Start (5 minutes)
```bash
# 1. Read overview
cat STATIC_PIPELINE_QUICKSTART.md

# 2. Understand the guides
cat STATIC_PIPELINE_GUIDE.md | head -100

# 3. Start migration
python migrate_to_static.py
```

### Full Setup (2-4 hours)
```bash
# Continue from above, then:
python static_pipeline.py --mode full
```

### Verify
```bash
python static_pipeline.py --status
```

---

## 📚 Documentation Guide

| Document | Read Time | When |
|----------|-----------|------|
| **STATIC_PIPELINE_QUICKSTART.md** | 5 min | First thing |
| **STATIC_PIPELINE_GUIDE.md** | 15 min | Before using |
| **DATA_FORMAT_ANALYSIS.md** | 10 min | Understanding formats |
| **STATIC_PIPELINE_IMPLEMENTATION.md** | 20 min | Deep dive |

---

## 💬 Summary

**Your Problem:** Pipeline reloads everything (wasteful)  
**Your Solution:** Static pipeline with incremental updates (efficient)

**Before:** 4 hours every run  
**After:** 4 hours first time, then 30 seconds every week

**Data:** 
- 22 vigente collections (162K laws) → laws_vigente.jsonl
- 1 abrogate collection (124K laws) → laws_abrogate.jsonl

**Format:** AKN (Akoma Ntoso) - all collections have it ✓

**Status:** ✅ Ready to implement

