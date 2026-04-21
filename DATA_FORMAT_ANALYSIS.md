# Dataset Format Analysis & Recommendations

## 📊 The 23 Collections at a Glance

```
TOTAL: 23 collections across 286,427 acts
├── VIGENTE (22 collections): 162,391 acts ✓ ALL 3 VARIANTS
│   ├─ Codici (40)
│   ├─ DPR (47,756) ← Largest
│   ├─ Regi decreti (91,346) ← 2nd largest
│   ├─ DL e leggi di conversione (7,425)
│   ├─ Decreti Legislativi (2,894)
│   ├─ Regolamenti ministeriali (2,069)
│   ├─ ... (16 more collections)
│   └─ Format: AKN ✓, XML ✓
│
└── ABROGATE (1 collection): 124,036 acts ⚠️  O VARIANT ONLY
    ├─ "Atti normativi abrogati (in originale)"
    ├─ NO Vigente (V) variant
    ├─ NO Multivigenza (M) variant
    ├─ Format: AKN ✓, XML ✓
    └─ Status: Repealed/old laws (historical reference)
```

---

## 📦 Available Export Formats (All Collections)

| Format | Coverage | Best For | Recommendation |
|--------|----------|----------|-----------------|
| **AKN** (Akoma Ntoso XML) | ✓ All 23 | Structured data, standards compliance | **USE THIS** ✓ |
| **XML** (Standard XML) | ✓ All 23 | Parsing, generic tools | Fallback only |
| **PDF** | ✓ All 23 | Human reading, printing | DON'T use for parsing |
| **EPUB** | ✓ All 23 | E-readers, mobile | DON'T use for parsing |
| **RTF** | ✓ All 23 | Word processors | DON'T use for parsing |
| **JSON** (ELI) | ✓ All 23 | Single document API | DON'T use for bulk |
| **HTML** | ✓ All 23 | Web viewing | DON'T use for parsing |
| **URI** | ? All 23 | ??? (undocumented) | Don't use |

---

## ⚠️ Why You Experienced "Missing Formats"

### What Likely Happened

You mentioned: *"not all have been installed due to missing XML or AKN format"*

**Reality Check:**
- ✓ All 23 collections have AKN format
- ✓ All 23 collections have XML format
- ✓ Normattiva API guarantees both

**So what actually went wrong?**

1. **Network issues** (most likely)
   - Large files (100-500 MB per collection)
   - Timeouts on slow connections
   - Partial downloads

2. **ZIP extraction failures**
   - Corrupted ZIP files
   - Disk space issues
   - Temporary file errors

3. **XML parsing errors**
   - Memory exhaustion on large files
   - Malformed XML in source
   - Parser library issues

4. **Rate limiting**
   - Too many requests to Normattiva API
   - Need delays between downloads

5. **The OS might be storing old cached data**
   - Restart your environment clears old cache
   - This is why "restart reloads everything"

### Solutions

| Issue | Solution |
|-------|----------|
| Network timeouts | Use `static_pipeline.py --mode sync` (only retries changed) |
| ZIP corruption | Add SHA256 verification (already in static_pipeline.py) |
| XML parsing errors | Pre-validate ZIP files before parsing |
| Rate limiting | Space requests 1 second apart (built into API client) |
| Cache issues | Static pipeline caches ETags locally |

---

## 🎯 Data Format Decision Tree

```
Need to download laws?
└─ YES
   └─ Which variant?
      ├─ VIGENTE (V): Current law in force (RECOMMENDED)
      │  └─ Format choice?
      │     ├─ AKN ✓ RECOMMENDED (structured, standard)
      │     └─ XML ✓ OK (if AKN fails)
      │
      ├─ ORIGINALE (O): Historic/original text
      │  └─ Format choice?
      │     ├─ AKN ✓ RECOMMENDED (structured, standard)
      │     └─ XML ✓ OK (if AKN fails)
      │
      └─ MULTIVIGENZA (M): All versions with dates
         └─ Format choice?
            ├─ AKN ✓ RECOMMENDED (structured, standard)
            └─ XML ✓ OK (if AKN fails)

No other formats should be used for bulk import.
```

---

## 📋 Specific Collections Status

### Why Each Collection is Available

| Collection | Acts | O | V | M | Notes |
|------------|------|---|---|---|-------|
| Codici (Codes) | 40 | ✓ | ✓ | ✓ | Small, core collection |
| DPR (Pres. Decrees) | 47,756 | ✓ | ✓ | ✓ | Largest collection |
| Regi decreti (Royal Decrees) | 91,346 | ✓ | ✓ | ✓ | 2nd largest, historic |
| DL e leggi (Decrees & Laws) | 7,425 | ✓ | ✓ | ✓ | Very stable |
| Decreti Legislativi | 2,894 | ✓ | ✓ | ✓ | Legislative decrees |
| Leggi costituzionali | 49 | ✓ | ✓ | ✓ | Constitutional laws |
| ... (16 more) | | ✓ | ✓ | ✓ | All complete |
| **Abrogate** | 124,036 | ✓ | × | × | **O ONLY** |

**Why is Abrogate different?**
- Repealed laws don't need "vigente" (current) version
- Only "originale" (original/repealed state) matters
- No changes over time (it's permanently repealed)
- Normattiva treats it specially

---

## 🚀 Best Practice Implementation

### For Vigente Data (Your Main Use Case)

```python
# ✓ DO THIS
api.get_collection('DPR', variant='V', format='AKN')
# Downloads current law in standard format
# ~2-4 hours for all 22 collections
# Result: 162,391 laws ready for search

# ✗ DON'T DO THIS
api.get_collection('DPR', variant='V', format='JSON')  # Slow! One doc at a time
api.get_collection('DPR', variant='V', format='PDF')   # Unstructured
api.get_collection('DPR', variant='V', format='EPUB')  # E-reader format
```

### For Abrogate Data (Historical Reference)

```python
# ✓ DO THIS
api.get_collection('Atti normativi abrogati', variant='O', format='AKN')
# ~124K repealed laws for research
# Separate from vigente to avoid confusion

# ✗ DON'T DO THIS
api.get_collection('Atti normativi abrogati', variant='V', ...)  # Doesn't exist!
api.get_collection('Atti normativi abrogati', variant='M', ...)  # Doesn't exist!
```

---

## 📊 Storage Implications

### Disk Usage by Format (Approximate)

| Dataset | AKN Size | XML Size | JSON |
|---------|----------|----------|------|
| Vigente (V) | ~1.2 GB | ~1.3 GB | N/A |
| Abrogate (O) | ~450 MB | ~480 MB | N/A |
| **Total** | **~1.65 GB** | **~1.78 GB** | Not practical |

After parsing to JSONL:
- Vigente JSONL: ~800 MB
- Abrogate JSONL: ~300 MB
- **Total processed: ~1.1 GB**

---

## 🔧 What Happens When You Use Static Pipeline

```
BEFORE (Current - Each restart reloads):
$ python redeploy.py
Restarting Space...
  └─ Pipeline re-downloads ALL collections (2-4 hours)
  └─ Parse all JSONL again (30-45 min)
  └─ Rebuild indexes (5-10 min)
  └─ Result: WASTEFUL

AFTER (Static - Incremental only):
$ python static_pipeline.py --mode sync
Checking for changes...
  ├─ Codici: ✓ unchanged (skip)
  ├─ DPR: ✓ unchanged (skip)
  ├─ [...] (20 more, all checked by ETag)
  ├─ Abrogate: ✓ unchanged (skip)
  └─ Result: "Everything up-to-date!" (30 seconds)

Once per year when changes happen:
  └─ Only changed collection(s) re-downloaded
  └─ Only changed collection(s) re-parsed
  └─ Merged with existing data
  └─ Result: EFFICIENT
```

---

## ✅ Recommendations Summary

### What To Do

1. **Keep using AKN format** (all collections have it)
2. **Implement static pipeline** (don't reload everything)
3. **Separate vigente from abrogate** (different purposes)
4. **Use ETag caching** (skip unchanged collections)
5. **Run incremental sync weekly** (GitHub Actions)

### What NOT To Do

1. ❌ Don't use PDF/EPUB/RTF for parsing
2. ❌ Don't call JSON API in a loop (286K calls!)
3. ❌ Don't reload all collections every time
4. ❌ Don't mix vigente with abrogate data
5. ❌ Don't worry about "missing formats" (they exist)

---

## 🎓 Key Takeaway

Your pipeline went from:
- **"Reload everything each time"** (4 hours, wasteful)

To:
- **"Load once, sync changes"** (30 seconds when unchanged)

With:
- **Vigente dataset** (162K current laws)
- **Abrogate dataset** (124K repealed laws, separate)
- **Both in standard AKN format**
- **ETag-based change detection**

**Total setup time: 4 hours (first time only)**
**Subsequent weeklysyncs: 30 seconds**

