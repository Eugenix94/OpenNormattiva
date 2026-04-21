# Enhanced Pipeline: Quick Reference

## 🎯 Use This When...

### "I just want to see what changed"
```bash
python enhanced_pipeline.py --mode verify
```
Output shows what's new/changed without modifying production

---

### "I want to update now"
```bash
# Step 1: Download & build staging
python enhanced_pipeline.py --mode incremental

# Step 2: Review what changed
python enhanced_pipeline.py --mode verify

# Step 3: Go live with new data
python enhanced_pipeline.py --mode promote
```
Time: 5-10 minutes total, zero downtime

---

### "I want to automate weekly updates"
Add this to `.github/workflows/weekly-update.yml`:
```yaml
name: Weekly Data Update
on:
  schedule:
    - cron: '0 2 * * 0'  # Sunday 2 AM

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: pip install -r requirements.txt
      - run: python enhanced_pipeline.py --mode incremental
      - run: python enhanced_pipeline.py --mode verify
      - run: python enhanced_pipeline.py --mode promote
      - run: |
          curl -X POST ${{ secrets.SLACK_WEBHOOK }} \
            -d "Weekly sync complete ✓"
```

---

### "Something went wrong, rollback!"
```bash
# Quickest rollback (< 1 second)
ls -la data/archives/

# Copy the most recent backup
cp data/archives/backup_20260418_143000/* data/processed/

# Verify
python enhanced_pipeline.py --status
```

---

### "I want to see current status"
```bash
python enhanced_pipeline.py --status
```
Outputs count of laws, last update time, staging status

---

### "I need to do a full reset"
```bash
# Reset everything to initial state
python enhanced_pipeline.py --mode full

# Or restart from backup
rm data/processed/* data/staging/*
python enhanced_pipeline.py --mode full
```
Time: 2-4 hours (only if absolutely necessary)

---

## 🔄 Decision Tree

```
What do you want to do?

1. Update with new data?
   ├─ Yes, check first → py enhanced_pipeline.py --verify
   ├─ Yes, update now → py enhanced_pipeline.py --incremental
   └─ Then promote    → py enhanced_pipeline.py --promote

2. See current state?
   ├─ Quick status → py enhanced_pipeline.py --status
   ├─ Full numbers → cat data/.enhanced_state.json
   └─ Backups available? → ls data/archives/

3. Rollback?
   ├─ Last backup → cp data/archives/backup_*/... data/processed/
   ├─ Which backup? → ls -la data/archives/
   ├─ Verify restored → py enhanced_pipeline.py --status
   └─ Users back online in < 1 sec

4. How much changed?
   ├─ Run verify → py enhanced_pipeline.py --verify
   ├─ Read report → less verification.txt (from GitHub Actions)
   ├─ Too much -> don't promote
   └─ Looks good → promote

5. Is it working?
   ├─ Check queries → curl http://localhost:8501/search?q=test
   ├─ Check logs → python check_db_error.py
   ├─ Full diagnosis → python enhanced_pipeline.py --diagnose
   └─ Need to reset → python enhanced_pipeline.py --full
```

---

## 🐛 Common Issues & Fixes

| Issue | Quick Fix | Full Solution |
|-------|-----------|---------------|
| "Nothing changed this week" | Expected! ✓ | ETags detect unchanged collections |
| "Staging → prod took too long" | Normal, staging file is big | 1GB+ JSON rewrite takes time |
| "Want to skip this week" | Comment out cron in GitHub Actions | Edit `.github/workflows/` |
| "Users see stale data" | Clear Streamlit cache | Or wait 5-60 sec (cached results) |
| "Not sure if it worked" | Run `--status` | Check last_sync timestamp |
| "Paranoid, want to verify first" | Always use `--verify` before `--promote` | Verification shows exact diff |

---

## 📋 File Reference

| File | Purpose | Edit? |
|------|---------|-------|
| `enhanced_pipeline.py` | Main logic | Read only (parameters via CLI) |
| `ENHANCED_PIPELINE_ARCHITECTURE.md` | How it works | Reference only |
| `ENHANCED_PIPELINE_DEPLOYMENT.md` | Setup guide | Reference only |
| `data/processed/laws_*.jsonl` | Production (live) | Never manually edit |
| `data/staging/laws_*_staging.jsonl` | Testing (invisible) | Auto-managed |
| `data/archives/backup_*/*.jsonl` | Backups (readonly) | Auto-managed |
| `data/.enhanced_state.json` | State tracking | Auto-managed |
| `.github/workflows/*.yml` | Automation | Edit to configure schedule |

---

## 🎬 Real Scenario

### Scenario: New laws added to DPR

**Your situation:**
- DPR collection updated Monday (new tax laws)
- You scheduled weekly update for Sunday
- Need to push to users ASAP without waiting

**Solution:**
```bash
# Thursday afternoon, manually trigger
python enhanced_pipeline.py --mode incremental

# Output shows:
# Staging complete!
#   Staging vigente: 162,395 laws (prod: 162,391)
#   Change: +4 laws (vigente, DPR)
#   Ready to verify

# Review the changes
python enhanced_pipeline.py --mode verify

# Output shows:
# 📊 VIGENTE DATASET
#   Production: 162,391 laws
#   Staging: 162,395 laws
#   ✓ Change: +4 laws (new legal act updates)
# 
# 🗑️ ABROGATE DATASET
#   (no change)
#
# 🔍 DATA QUALITY CHECKS
#   ✓ All checks passed

# Looks good, promote immediately
python enhanced_pipeline.py --mode promote

# Users see new data within 1 second
# Old data backed up and available for rollback
```

**Total time:** 10 minutes
**User downtime:** 0 seconds
**Risk of data loss:** 0 (backup created automatically)

---

## 🔐 Safety Principle

**Before every promotion, ask:**

1. ✓ Did incremental complete without errors?
2. ✓ Did verification show reasonable changes?
3. ✓ Are NULL checks passing?
4. ✓ Did law count stay similar (no 99% increase)?
5. ✓ Did I read the verification report?

**If ANY answer is "no" → DON'T PROMOTE**

Instead:
```bash
# Investigate
python check_db_error.py

# Or rollback
cp data/archives/backup_*/* data/processed/
```

---

## 📞 Support

**Need help? Check:**
1. `ENHANCED_PIPELINE_ARCHITECTURE.md` - Deep dive
2. `MULTIVIGENTE_ANALYSIS.md` - Format/variant questions
3. Run `python enhanced_pipeline.py --help` - Command reference
4. Check github repo issues (if using with GitHub Actions)

