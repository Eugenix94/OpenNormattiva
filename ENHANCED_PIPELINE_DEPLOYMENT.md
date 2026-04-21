# Enhanced Pipeline: Deployment & Operations Guide

## 🎯 What You're Getting

### Old: Static Pipeline (Good)
```
python static_pipeline.py --mode full      # 4 hours, full download
python static_pipeline.py --mode sync      # 30 sec, full check
Result: Smart but still downloads full collections
```

### New: Enhanced Pipeline (Better)
```
python enhanced_pipeline.py --mode incremental   # 2-10 min, NEW LAWS only
python enhanced_pipeline.py --mode verify       # Verify before going live
python enhanced_pipeline.py --mode promote      # Instant promotion (0 downtime)
Result: Incremental updates + production safety
```

---

## 📋 Three-Stage Workflow

### Stage 1: Incremental Download (2-10 minutes)

```bash
python enhanced_pipeline.py --mode incremental
```

**What happens:**
1. Checks each collection (⏱️ 30 sec)
   - Compare ETag with API
   - If same: Skip ✓
   - If different: Proceed

2. Downloads changed collections ⏱️ 1-5 min
   - Only collections that changed
   - Full ZIP (needed for parsing)

3. Extracts NEW/CHANGED laws ⏱️ 30 sec
   - For each collection, parse all laws
   - Compare with production by URN
   - Extract only new/changed
   - Ignore everything else

4. Builds staging JSONL ⏱️ 5 min
   - Read production into memory
   - Add new/changed laws
   - Write to staging file
   - Production untouched

**Output:**
```
Staging complete!
  Staging vigente: 162,394 laws (prod: 162,391)
  Staging abrogate: 124,036 laws (prod: 124,036)
  Change: +3 laws (vigente)
  Ready to verify
```

**Status during this:**
- ✓ Production LIVE (users unaffected)
- ✓ Staging being built (invisible to users)
- ✓ No reads blocked
- ✓ No writes interrupted

---

### Stage 2: Verification (2 minutes)

```bash
python enhanced_pipeline.py --mode verify
```

**What happens:**
1. Compare production vs staging
2. Check data quality
3. Display diff for review

**Output:**
```
================================================================================
STAGING VERIFICATION
================================================================================

📊 VIGENTE DATASET
  Production: 162,391 laws
  Staging:    162,394 laws
  ✓ Change: +3 laws (new/updates)

🗑️  ABROGATE DATASET
  Production: 124,036 laws
  Staging:    124,036 laws
  - No changes

🔍 DATA QUALITY CHECKS
  Vigente with NULL urn: 0
  Vigente with NULL title: 0
  ✓ No NULL values

================================================================================
Review complete. Ready to promote?
  python enhanced_pipeline.py --mode promote
================================================================================
```

**You decide:**
- ✅ Looks good → proceed to promote
- ⚠️ Concerned → block and investigate
- ❌ Major issue → rollback (old production still on disk)

---

### Stage 3: Promotion (< 1 second)

```bash
python enhanced_pipeline.py --mode promote
```

**What happens:**
1. Backup current production (3 seconds)
   ```
   Backup created: data/archives/backup_20260418_143000/
   ├─ laws_vigente.jsonl
   └─ laws_abrogate.jsonl
   ```

2. Swap staging to production (<1 second)
   ```
   data/staging/laws_vigente_staging.jsonl 
     → data/processed/laws_vigente.jsonl
   
   data/staging/laws_abrogate_staging.jsonl 
     → data/processed/laws_abrogate.jsonl
   ```

3. Update state

**Users experience:**
```
Before:
  GET /search → law #162,391 found ✓

During swap:
  (< 1 second, imperceptible)

After:
  GET /search → law #162,394 found ✓ (new!)

Result: NO DOWNTIME
```

---

## 🏗️ File Structure After Enhancement

```
data/
├── raw/
│   ├── Codici_vigente_20260418_100000.zip
│   ├── DPR_vigente_20260418_142000.zip    ← Just downloaded
│   └── ... (collection ZIPs)
│
├── processed/ (PRODUCTION - LIVE DATA)
│   ├── laws_vigente.jsonl                 ← Users search this ✓
│   ├── laws_abrogate.jsonl
│   └── laws.db
│
├── staging/ (TESTING - INVISIBLE TO USERS)
│   ├── laws_vigente_staging.jsonl         ← Built during incremental
│   └── laws_abrogate_staging.jsonl
│
├── archives/ (BACKUPS)
│   ├── backup_20260418_143000/
│   │   ├── laws_vigente.jsonl
│   │   └── laws_abrogate.jsonl
│   └── backup_20260411_120000/
│       ├── laws_vigente.jsonl
│       └── laws_abrogate.jsonl
│
├── .enhanced_state.json                   ← State persistence
├── .urns_index.json                       ← Seen URNs
└── .static_state.json                     ← Old state
```

---

## 🔄 Weekly Operations

### Option A: Fully Automated

```yaml
# .github/workflows/weekly-incremental.yml
name: Weekly Incremental Update

on:
  schedule:
    - cron: '0 2 * * 0'  # Sunday 2 AM UTC

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Incremental update
        run: python enhanced_pipeline.py --mode incremental
      
      - name: Verify staging
        run: python enhanced_pipeline.py --mode verify > verification.txt
      
      - name: Auto-promote (if safe)
        if: success()
        run: python enhanced_pipeline.py --mode promote
      
      - name: Push to HF Dataset
        run: python push_to_hf.py
      
      - name: Notify (email/slack)
        if: always()
        run: |
          curl -X POST ${{ secrets.SLACK_WEBHOOK }} \
            -d "Weekly sync complete. Files promoted."
```

**Pros:**
- Fully automatic
- No manual intervention
- Runs at optimal time

**Cons:**
- No human review
- Need safety checks in code

### Option B: Semi-Automated (RECOMMENDED)

```yaml
# .github/workflows/weekly-incremental-manual.yml
name: Weekly Incremental Update (Manual Promotion)

on:
  schedule:
    - cron: '0 2 * * 0'  # Sunday 2 AM UTC

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      
      - name: Incremental update
        run: python enhanced_pipeline.py --mode incremental
      
      - name: Verify & save report
        run: python enhanced_pipeline.py --mode verify > verification.txt
      
      - name: Upload verification report
        uses: actions/upload-artifact@v3
        with:
          name: verification-report
          path: verification.txt
      
      - name: Create pull request for review
        uses: peter-evans/create-pull-request@v5
        with:
          title: "Data Update: Weekly Increment"
          body: |
            Automated weekly update ready for review.
            See artifacts for verification report.
          
            **Next step:** Review verification, then approve PR.
      
      - name: Notify team
        run: |
          echo "Weekly update staged for review"
          # Send notification
```

**Pros:**
- Automated download/staging
- Manual verification before going live
- Safety + speed balance

**Cons:**
- Requires human approval
- Can't run 100% unattended

### Option C: Manual (If You Prefer Control)

```bash
# Every Sunday, manually run:
python enhanced_pipeline.py --mode incremental
# Review output

python enhanced_pipeline.py --mode verify
# Review staging changes

# Read through verification, then:
python enhanced_pipeline.py --mode promote
# Done!

# Or rollback if needed (keep backups):
# mv data/archives/backup_LATEST/* data/processed/
```

---

## 🛡️ Safety Features

### 1. No Data Loss

**Backups created before every promotion:**
```
archive/backup_20260418_143000/
  ├─ laws_vigente.jsonl (previous production)
  └─ laws_abrogate.jsonl (previous production)
```

**Can rollback anytime:**
```bash
# If something goes wrong:
cp data/archives/backup_20260418_143000/laws_vigente.jsonl 
   data/processed/laws_vigente.jsonl
# Production restored in 1 second
```

### 2. Staging Never Interferes

**Production always readable:**
- Users query `/processed/laws_vigente.jsonl` (production)
- Staging built in `/staging/` (hidden)
- No file locks, no conflicts
- Users unaffected during update

### 3. Verification Gate

**Manual verification before promoting:**
```
python enhanced_pipeline.py --mode verify
  ↓
Review report (3 minutes)
  ↓
  ├─ Looks good? → promote
  └─ Issues found? → investigate or block
```

### 4. Incremental & Tracked

**Each update tracked:**
```
.enhanced_state.json
├─ last_incremental_sync: timestamp
├─ collections: {name: {etag, law_count, last_check}}
├─ production_stats: {vigente_count, abrogate_count}
└─ staging_stats: {vigente_count, abrogate_count}
```

---

## 📊 Expected Performance

### Typical Weekly Update (if 2-3 collections changed)

| Stage | Time |
|-------|------|
| Check ETags | 30 seconds |
| Download changed (2-3 collections) | 2-5 minutes |
| Parse & extract new/changed | 1-2 minutes |
| Build staging JSONL | 1-2 minutes |
| **Incremental total** | **5-10 minutes** |
| Verify & review | 2-3 minutes |
| **Promotion (instant)** | **< 1 second** |

**Bandwidth:** ~50-200 MB (vs 1650 MB full download)
**User impact:** Zero downtime (production never interrupted)

### Worst Case (all collections changed - rare)

| Stage | Time |
|-------|------|
| Check all ETags | 30 seconds |
| Download all 22 collections | 30-45 minutes |
| Parse & extract | 5-10 minutes |
| Build staging | 5 minutes |
| **Total if all changed** | **45-60 minutes** |

**Still better than static_pipeline.py (2-4 hours)**

---

## 🚨 Monitoring & Alerting

### Check Status Anytime

```bash
python enhanced_pipeline.py --status
```

**Output:**
```
================================================================================
ENHANCED PIPELINE STATUS
================================================================================

Mode: incremental
Last Full Sync: 2026-04-11T10:30:45.123456
Last Incremental Sync: 2026-04-18T02:15:30.123456

📊 PRODUCTION (Live for Users)
  Vigente: 162,391 laws
  Abrogate: 124,036 laws
  Total: 286,427 laws
  Vigente file: 1457.4 MB

🚀 STAGING (Ready for Verification)
  Vigente: 162,394 laws
  Abrogate: 124,036 laws
  ✓ Ready to verify and promote

🔄 COLLECTION STATUS
  Codici: 2026-04-18T02:15:01
  DL proroghe: 2026-04-18T02:15:02
  DPR: 2026-04-18T02:15:15 ← Took longer (2 sec)
  ... and 19 more

================================================================================
```

### Integration with Monitoring

```python
# Example: Send to Slack/email if issues

def check_update_health():
    pipeline = EnhancedPipeline()
    
    # Check for unexpected changes
    prod = len(pipeline._read_jsonl_into_dict(pipeline.jsonl_vigente_prod))
    staging = len(pipeline._read_jsonl_into_dict(pipeline.jsonl_vigente_staging))
    
    if staging < prod * 0.95:  # Lost 5%+ laws
        alert("CRITICAL: Staging has 5% fewer laws! Check data quality!")
    
    if staging > prod * 1.5:   # Added 50%+ laws (unlikely)
        alert("WARNING: Staging added 50% laws. Verify data integrity.")
    
    # Parse verification report
    verify_report = subprocess.run([
        'python', 'enhanced_pipeline.py', '--mode', 'verify'
    ], capture_output=True, text=True)
    
    if "NULL" in verify_report.stdout:
        alert("Data quality issue: NULL values found!")
    else:
        ok("Data quality check passed")
```

---

## 🎓 Deployment Checklist

### Week 1: Setup
- [ ] Read ENHANCED_PIPELINE_ARCHITECTURE.md
- [ ] Read MULTIVIGENTE_ANALYSIS.md (this guide)
- [ ] Review enhanced_pipeline.py code
- [ ] Test locally: `python enhanced_pipeline.py --mode incremental`
- [ ] Test verify: `python enhanced_pipeline.py --mode verify`
- [ ] Test promote: `python enhanced_pipeline.py --mode promote`

### Week 2: Integration
- [ ] Update GitHub Actions workflow
- [ ] Set up Slack/email notifications
- [ ] Plan rollback procedure
- [ ] Document manual procedure for team

### Week 3: Goes Live
- [ ] Schedule first incremental (Sunday 2 AM UTC)
- [ ] Monitor logs
- [ ] Verify promotion succeeded
- [ ] Check user queries still work

### Ongoing
- [ ] Monitor weekly updates
- [ ] Keep 4+ backups (auto-cleanup old)
- [ ] Check for anomalies in verification reports
- [ ] Document any incidents

---

## 💥 Troubleshooting

### "Staging shows 0 laws"
```
Cause: Incremental found all collections unchanged
Solution: Expected! Nothing new to add.
Check: python enhanced_pipeline.py --status
```

### "Verification shows NULL values"
```
Cause: Data quality issue in collection
Solution: Don't promote, investigate
Debug: python check_db_error.py
```

### "Promotion succeeded but users see old data"
```
Cause: Streamlit cached old data
Solution: Clear Streamlit cache
Fix: streamlit cache clear
Or: Restart space
```

### "Want to rollback"
```
Restore backup:
  cp data/archives/backup_LATEST/* data/processed/
Check:
  python enhanced_pipeline.py --status
Users see old data again (< 1 second)
```

---

## ✅ Summary

**Old (Static Pipeline):**
- Full check & download every time
- 30 seconds to 4 hours depending on changes
- Always re-downloads even if unchanged collections

**New (Enhanced Pipeline):**
- Incremental: Only new/changed laws
- 5-10 minutes typical, < 1 second to promote
- Zero downtime during updates
- Staging/verification before going live
- Complete backup history

**For Your Use Case:**
- Daily: Nothing (automated weekly)
- Weekly: Automated or semi-automated with review
- Monthly: Monitor and archive old backups

**Ready to deploy** ✓

