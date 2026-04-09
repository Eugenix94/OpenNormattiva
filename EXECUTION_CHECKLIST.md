# EXECUTION CHECKLIST - Ready to Deploy

## Pre-Execution Verification

- [x] All 162,391 vigente acts confirmed available
- [x] Zero gaps in vigente coverage verified  
- [x] Repealed acts (Originale-only) identified and excluded
- [x] Citations extraction tested and working
- [x] Pipeline enhanced for vigente filtering
- [x] Live updater implemented and tested
- [x] All modules import without errors
- [x] API connectivity verified
- [x] File structure prepared
- [x] Documentation complete

---

## Deployment Steps

### Step 1: Start Full Vigente Download

```bash
cd c:\Users\Dell\Documents\VSC Projects\OpenNormattiva
python pipeline.py --variants vigente
```

**What to do while waiting:**
- Monitor: `Get-ChildItem data/raw/*.zip`
- Check file sizes growing
- Be patient (2-4 hours due to API rate limits)

**What to monitor:**
```
2024-04-09 XX:XX:XX - INFO - Downloading [Collection1]...
2024-04-09 XX:XX:XX - INFO - ✓ Downloaded [Collection1] → X.X MB
2024-04-09 XX:XX:XX - INFO - Parsing [Collection1]...
2024-04-09 XX:XX:XX - INFO - ✓ Parsed NNN laws
...
```

**End state (expect to see):**
```
====================================================================
✓ Pipeline complete: 162391 total laws processed
====================================================================
```

**Output files:**
- `data/processed/laws_vigente.jsonl` - 162,391+ line JSONL
- `data/indexes/laws_vigente_citations.json` - Citation graph
- `data/indexes/laws_vigente_metrics.json` - Dataset stats

---

### Step 2: Verify Output

```bash
# Count laws
(Get-Content data/processed/laws_vigente.jsonl | Measure-Object -Line).Lines

# Show first law (should parse correctly)
Get-Content data/processed/laws_vigente.jsonl -First 1 | ConvertFrom-Json | Format-List

# Check indexes created
Get-ChildItem data/indexes/laws_vigente*

# Verify citation index
$citations = Get-Content data/indexes/laws_vigente_citations.json | ConvertFrom-Json
$citations.total_laws_with_citations
$citations.total_citations
```

**Expected results:**
- `laws_vigente.jsonl`: 162,391+ lines
- `laws_vigente_citations.json`: ~tens of thousands of laws with citations
- `laws_vigente_metrics.json`: Dataset statistics

---

### Step 3: Setup Weekly Updates (Choose One)

#### Option A: Windows Task Scheduler

```powershell
# Create scheduled task (run as administrator)
$action = New-ScheduledTaskAction `
  -Execute "C:\path\to\.venv\Scripts\python.exe" `
  -Argument "live_updater.py --variant vigente"

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 2am

Register-ScheduledTask `
  -TaskName "Normattiva Weekly Update" `
  -Action $action `
  -Trigger $trigger
```

#### Option B: Manual Weekly Check

```bash
# Every Sunday morning, run:
python live_updater.py --variant vigente

# Or first check what changed:
python live_updater.py --variant vigente --check-only
```

---

### Step 4: Monitor Amendment History

```bash
# View latest amendments
tail -5 data/processed/amendments.jsonl

# Get JSON formatted
Get-Content data/processed/amendments.jsonl -Last 5 | 
  ForEach-Object { $_ | ConvertFrom-Json } | 
  Format-Table timestamp, action, collection

# Count amendments by action
$amendments = Get-Content data/processed/amendments.jsonl |
  ForEach-Object { $_ | ConvertFrom-Json }

$amendments | Group-Object -Property action | Format-Table Name, Count
```

---

## Troubleshooting

### Issue: Downloads timeout after 30 minutes

**Solution:** API rate limits - this is normal. Let it continue.
- Pipeline will retry automatically
- Check logs for which collection failed
- You can resume by running pipeline again (completed collections cached)

### Issue: "Collection not found" errors

**Solution:** Check collection name in API
```bash
python -c "from normattiva_api_client import NormattivaAPI; 
api = NormattivaAPI()
catalogue = api.get_collection_catalogue()
print([c['nomeCollezione'] for c in catalogue if 'V' in c['formatoCollezione']])[:5]"
```

### Issue: Memory issues during parsing

**Solution:** Reduce batch size or split into phases
- Edit `pipeline.py` to process collections one at a time
- Or increase available RAM

### Issue: JSONL file too large for some tools

**Solution:** The file is expected to be large (~200-300 MB)
- Use streaming readers, not whole-file load
- Use `jq` or pandas for querying
- Split into smaller files if needed

---

## Success Indicators

After Step 1 completion, you should see:

✅ `laws_vigente.jsonl` exists and contains 162,391+ lines  
✅ Each law has `urn`, `title`, `citations`, `text` fields  
✅ Random sampling shows valid Italian law data  
✅ Citation index shows thousands of citations  
✅ Amendment log shows "added" entries for all laws  

Example valid law entry:
```json
{
  "urn": "urn:nir:decreto.legge:2024-01-15;1",
  "title": "Decreto-legge 15 gennaio 2024, n. 1",
  "type": "DecretoLegge",
  "date": "2024-01-15",
  "citations": ["L.123/2006", "D.L.45/2021"],
  "text": "[Full law text...]",
  "articles": 28,
  "article_count": 28,
  "text_length": 45823,
  "year": "2024"
}
```

---

## Timeline Expectation

| Phase | Duration | Action |
|-------|----------|--------|
| **Phase 1** | 2-4 hours | Download all vigente (one-time) |
| **Phase 2** | ~5 min | First amendment sync + cache setup |
| **Phase 3+** | <5 min/week | Weekly update checks and applies |

**Week 1:** Initial full download  
**Week 2+:** Only changes downloaded (much faster)

---

## Support Resources

- `VIGENTE_STRATEGY.md` - Strategic overview
- `SITUATION_VERIFIED.md` - All questions answered  
- `NEXT_STEPS.md` - Quick command reference
- `IMPLEMENTATION_STATUS.md` - Technical details
- `COMPLETE_SITUATION_ANALYSIS.md` - Full explanation

---

## Ready to Proceed?

✅ **YES - Execute Step 1:**

```bash
python pipeline.py --variants vigente
```

This will:
1. Download all 22 vigente collections
2. Parse 162,391 Italian laws
3. Extract citations
4. Build indexes
5. Setup amendment tracking

**Time investment:** 2-4 hours (initial, then <5 min weekly)

**Benefit:** Complete, current Italian legal database with real-time amendments for jurisprudence analysis.
