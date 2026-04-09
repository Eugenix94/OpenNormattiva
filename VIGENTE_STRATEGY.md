# Vigente-Only Strategy: Complete Implementation Guide

## Executive Summary

**Status: YES, proceed with vigente-only approach**

- ✅ All 162,391 vigente laws are available
- ✅ No missing vigente laws (100% coverage)
- ✅ 124,036 missing acts are repealed (not needed)
- ✅ Live updater strategy is sound

## Current State

### Downloaded (3/22 collections)
- Codici_vigente.zip: 9.9 MB
- DL proroghe_vigente.zip: 1.4 MB
- Leggi costituzionali_vigente.zip: 0.3 MB
- **Parsed: 164 laws**

### Remaining (19/22 collections)
| Collection | Acts |
|-----------|------|
| Regi decreti | 91,346 |
| DPR | 47,756 |
| DL e leggi di conversione | 7,425 |
| Decreti Legislativi | 2,894 |
| Leggi di ratifica | 2,325 |
| Regolamenti ministeriali | 2,069 |
| Regolamenti governativi | 1,940 |
| DL decaduti | 1,735 |
| Decreti legislativi luogotenenziali | 1,215 |
| Leggi delega e relativi provvedimenti delegati | 1,125 |
| Atti di recepimento direttive UE | 1,086 |
| Regolamenti di delegificazione | 374 |
| DPCM | 357 |
| Testi Unici | 255 |
| Regi decreti legislativi | 120 |
| Leggi contenenti deleghe | 76 |
| Leggi finanziarie e di bilancio | 58 |
| Leggi costituzionali | 49 |
| Atti di attuazione Regolamenti UE | 39 |
| Leggi di delegazione europea | 32 |

**Total to download: 162,227 acts**

## Phase 1: Complete Vigente Download

**Objective:** Download all 22 vigente collections using existing `pipeline.py`

```bash
python pipeline.py --variants vigente --skip-upload
```

**Expected output:**
- 22 ZIP files in `data/raw/`
- All 162,391 laws parsed to `data/processed/laws_vigente.jsonl`
- Citation indexes built

## Phase 2: Live Updater Implementation

**Objective:** Maintain real-time currency of vigente laws via ETag-based sync

### 2a. Lightweight Update Checker
```python
# Check which collections have changed
from normattiva_api_client import NormattivaAPI
api = NormattivaAPI()

# Get catalogue and check ETags
for collection in active_collections:
    etag = api.check_collection_etag(collection, variant="V")
    stored_etag = load_from_cache(collection)
    
    if etag != stored_etag:
        # Collection changed - queue for re-download
        queue_download(collection)
```

### 2b. Incremental Download & Merge
```python
# Only re-download changed collections
for collection in changed_collections:
    data = api.get_vigente(collection)
    parse_and_merge(data)
    update_amendments_log()
```

### 2c. Amendment Timeline Tracking
```json
{
  "law_urn": "urn:nir:decreto.legge:2024-01-15;1",
  "amendments": [
    {"date": "2024-01-15", "action": "enacted", "version": 1},
    {"date": "2024-03-20", "action": "amended", "version": 2, "by": "legge:2024-03-20;15"}
  ]
}
```

## Why NOT Originale or Multivigente

| Variant | Size | Use Case | Decision |
|---------|------|----------|----------|
| **V (Vigente)** | 162K acts | Current law, jurisprudence | ✅ **USE THIS** |
| O (Originale) | 286K acts | Historical research, repealed | ❌ Skip (repealed) |
| M (Multivigente) | 162K acts | Compliance audits, versioning | ⚠️ Consider later |

**Vigente is sufficient for jurisprudence** because:
1. All amendments are already applied
2. Repealed laws don't affect current legal reasoning
3. Live updates provide historical tracking from now forward
4. Smaller dataset (easier to index, faster queries)

## Implementation Timeline

| Phase | Duration | Output |
|-------|----------|--------|
| Phase 1: Download all vigente | 1-2 hours | 162.4K laws parsed |
| Phase 2: Live updater (weekly) | 5 min/week | Amendment tracking |
| Phase 3: Citation index building | 30 min | Jurisprudence backbone |

## Files Modified

- `pipeline.py`: Already supports `--variants vigente`
- Next: Build live updater job (new file)
- Next: Citation graph extraction (existing `parse_akn.py`)

## Success Criteria

- [x] Confirmed 162,391 vigente acts available
- [x] Verified no missing vigente collections
- [x] Validated repealed acts not needed
- [ ] Download all 22 vigente collections
- [ ] Parse to JSONL
- [ ] Implement live updater
- [ ] Build citation index
- [ ] Deploy to jurisprudence system
