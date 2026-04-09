# Next Steps: Download & Process All Vigente Laws

## Quick Start

### 1. Download all vigente collections + parse + index

```bash
python pipeline.py --variants vigente
```

**What this does:**
- Downloads all 22 vigente collections from API
- Parses XML to JSONL
- Builds citation indexes
- Generates metrics
- Output: `data/processed/laws_vigente.jsonl` with 162,391 laws

**Expected time:** 2-4 hours (API rate limits apply)
**Output size:** ~200-300 MB JSONL

### 2. Monitor progress

Check `data/raw/` for downloaded ZIPs:
```powershell
Get-ChildItem data/raw/*.zip | Measure-Object -Property Length -Sum
```

Check `data/processed/` for parsed data:
```bash
wc -l data/processed/laws_vigente.jsonl
```

## Understanding the Citation Index

After parsing, you'll have:
- `data/indexes/laws_vigente_citations.json` — Which laws cite which other laws
- `data/indexes/laws_vigente_metrics.json` — Dataset statistics

Example citation structure:
```json
{
  "urn:nir:decreto.legge:2024-01-15;1": {
    "law": "Decreto-legge 15 gennaio 2024, n. 1",
    "citations": [
      "urn:nir:legge:1970-12-15;970",
      "urn:nir:decreto.presidente.repubblica:1972-03-05;309"
    ],
    "count": 2
  }
}
```

## Next: Live Updater

Once vigente download completes, the next phase is to build a live updater that:

1. **Weekly sync** — Check for amendments to existing laws
2. **ETag optimization** — Only re-download what changed
3. **Amendment tracking** — Record when each law was modified
4. **Incremental updates** — Merge new versions into JSONL

Example cron job (weekly):
```bash
0 0 * * SUN python live_updater.py --variant vigente
```

## Why This Works for Jurisprudence

✅ **Complete coverage:** All 162,391 current laws
✅ **Real-time accuracy:** Weekly sync keeps amendments current
✅ **Citation graph:** Know which laws reference which
✅ **No bloat:** Repealed laws already excluded
✅ **Efficient:** ETag-based sync avoids unnecessary re-downloads

## Files to Review

- `VIGENTE_STRATEGY.md` — Full strategy document
- `pipeline.py` — Updated with vigente-aware filtering
- `normattiva_api_client.py` — API client with ETag support
- `parse_akn.py` — XML parsing logic (understands citations)
