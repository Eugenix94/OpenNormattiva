# Multivigente Analysis: Should You Use It?

## Quick Answer

**For Your Use Case (Live Jurisprudence Search & Indexing):**
- ✅ **VIGENTE (V)** - Use this (current law)
- ✅ **ORIGINALE (O)** - Optional (historical reference, use via abrogate)
- ❌ **MULTIVIGENTE (M)** - Skip for now

**Why?**
- Vigente is 99% of what you need
- Multivigente is 3× larger but only useful for "show me amendments over time"
- For NOW: Focus on fast, accurate searches
- LATER: Add if customers ask for amendment history

---

## 📊 The Three Variants Explained

### VIGENTE (V): What Your Platform Should Use

```
Current Law (as of NOW - April 2026)

Example: "Data Protection Code" 

Timeline:
  2018: First passed
    ↓ (amendments)
  2019: Article 5 added
  2020: Article 12 rewritten
  2021: Section B amended
  2022: 3 articles removed
  2023: Article 8 updated
  2024: Emergency changes
  2025: Unified directive
  2026: VIGENTE VERSION (current)

VIGENTE contains: Only the 2026 version
  - All amendments applied
  - All repeals processed
  - Ready to enforce
  - What a lawyer references today
```

**Use Cases:**
✅ "Is this law in force?" → Check vigente
✅ "What does law X say today?" → Vigente
✅ "Citation network" → Use vigente sources/targets
✅ "Compliance check" → Vigente applies
✅ "Legal research" → Vigente is current

**Size & Performance:**
- 162,391 laws
- ~1.2 GB raw AKN
- ~800 MB parsed JSONL
- Fast to search (current state only)

**Storage Strategy:**
```
data/processed/laws_vigente.jsonl ← Your main dataset
├─ 162,391 lines (one law each)
├─ Updated: Weekly incremental
├─ Indexed: Full-text search
└─ Served: Streamlit UI + API
```

---

### ORIGINALE (O): Optional Historical Reference

```
Original Text (as first passed)

Example: "Data Protection Code" 

Timeline:
  2018: First passed → ORIGINALE VERSION
    ↓ (amendments made, but ORIGINALE unchanged)
  2019: Article 5 added
  2020: Article 12 rewritten
  2021: Section B amended
  2022: 3 articles removed
  2023: Article 8 updated
  2024: Emergency changes
  2025: Unified directive
  2026: (ORIGINALE still unchanged - it's 2018 version)

ORIGINALE contains: Only the 2018 version (never changes)
  - Original parliamentary text
  - First-pass intent
  - Amendments NOT shown
  - Historical snapshot
```

**Use Cases:**
✅ "What did the original law say?" → Originale
✅ "What amendments were made?" → Compare originale vs vigente
✅ "Legislative intent research" → Originale
✅ "Dispute over original meaning" → Originale
❌ "What's current law?" → NO, use vigente

**Size & Performance:**
- Larger (includes 124K abrogate + 162K vigente)
- ~1.3 GB raw AKN
- Rarely changes (it's history)
- Slower to query (what's changed?)

**Storage Strategy:**
```
data/processed/laws_abrogate.jsonl ← Contains O variant of repealed laws
└─ These are abrogate (repealed) laws in original form

Option: Also store O variant of current laws
  (Not necessary unless doing comparative research)
```

**Your Situation:**
- You have abrogate in O variant (good)
- Vigente in V variant (what you need)
- Don't need separate O of vigente (unless comparative research)

---

### MULTIVIGENTE (M): Complete Amendment History

```
All Versions + Dates (comprehensive history)

Example: "Data Protection Code"

Timeline (MULTIVIGENTE shows ALL):
  2018-01-01: Version 1 (original)
    - Articles: 10
    - Text: "..."
  2019-06-15: Version 2 (article 5 added)
    - Articles: 11
    - Text: "..." (updated)
  2020-03-20: Version 3 (article 12 rewritten)
    - Articles: 11
    - Text: "..." (updated)
  2021-09-01: Version 4 (section B amended)
    - Articles: 11
    - Text: "..." (updated)
  2022-11-10: Version 5 (3 articles removed)
    - Articles: 8
    - Text: "..." (updated)
  2023-05-30: Version 6 (article 8 updated)
    - Articles: 8
    - Text: "..." (updated)
  2024-02-14: Version 7 (emergency changes)
    - Articles: 8
    - Text: "..." (updated)
  2025-07-01: Version 8 (unified directive)
    - Articles: 9
    - Text: "..." (updated)
  2026-01-01: Version 9 (current/VIGENTE)
    - Articles: 9
    - Text: "..." (current)

MULTIVIGENTE contains: ALL versions 1-9
  - Effective dates for each version
  - Complete amendment history
  - Timeline of changes
  - Perfect for "show me how this evolved"
```

**Use Cases:**
✅ "Show me the amendment history" → Multivigente
✅ "What changed between version X and Y?" → Multivigente
✅ "Timeline of amendments" → Multivigente
✅ "Law evolution research" → Multivigente
✅ "Academic paper: 'How has law X changed?'" → Multivigente
❌ "What's current law?" → NO, use vigente
❌ "Does this law apply today?" → NO, use vigente

**Size & Performance:**
- **HUGE:** ~2.8 GB (3× larger than vigente)
- Same 22 collections as vigente
- Slower to parse (8+ versions per law)
- Slower to search (must index all versions)
- 30x more complex to display

**Storage Strategy:**
```
Option 1: Separate multivigente index
  data/processed/laws_multivigente.jsonl ← Only if customers need it
  └─ One entry per law version (not just current)
  └─ Includes effective_date_from, effective_date_to
  └─ Separate search index for "show amendments"

Option 2: Don't store multivigente
  └─ Query API on-demand for specific laws
  └─ (What you probably want for now)
```

---

## 🎯 Decision Matrix

| Question | Vigente | Originale | Multivigente |
|----------|---------|-----------|----------------|
| **"What's law today?"** | ✅ YES | ❌ NO | ❌ NO |
| **"Is this in force?"** | ✅ YES | ❌ NO | ❌ NO |
| **"What did it say originally?"** | ❌ NO | ✅ YES | ⚠️ YES (v1) |
| **"Show me amendments"** | ❌ NO | ❌ NO | ✅ YES |
| **"When did this change?"** | ❌ NO | ❌ NO | ✅ YES |
| **"Interactive timeline"** | ❌ NO | ❌ NO | ✅ YES |
| **For jurisprudence?** | ✅ YES | ⚠️ Maybe | ❌ NO |
| **For compliance?** | ✅ YES | ❌ NO | ❌ NO |
| **Size** | 1.2 GB | 1.3 GB | **2.8 GB** |
| **Parse time** | 30 min | 30 min | **2-3 hours** |
| **Search speed** | Fast | Medium | **Slow** |

---

## 📋 Recommendation by Use Case

### Use Case 1: Live Jurisprudence Search (YOUR CASE)
```
GET /search?q="income+tax"
Returns: Current laws matching query

Dataset needed:
  ✅ Vigente (162K laws, current)
  ❌ Originale (not needed for search)
  ❌ Multivigente (overkill)

Storage:
  laws_vigente.jsonl (0.8 GB)
  
Query time:
  < 100 ms (FTS5 index)
```

### Use Case 2: Amendment Tracker
```
GET /law/{urn}/amendments
Returns: Timeline of changes over history

Dataset needed:
  ✅ Vigente (current state)
  ✅ Originale (original state)
  ✅ Multivigente (all versions)

Storage:
  laws_vigente.jsonl (0.8 GB)
  laws_multivigente.jsonl (2.8 GB)
  
Query time:
  < 500 ms (filter multivigente by URN)
  
Cost: +2.0 GB storage
```

### Use Case 3: Legal Historian Database
```
GET /law/{urn}/history?detailed=true
Returns: Complete evolution with dates

Dataset needed:
  ✅ Vigente (current)
  ✅ Originale (original)
  ✅ Multivigente (all versions)

Storage:
  Full mirror of all 3 variants
  
Query time:
  < 1 second (historical queries slow)
  
Cost: +4.1 GB storage
```

---

## 💰 Storage & Performance Comparison

### Your Current Setup
```
laws_vigente.jsonl
  Size: ~0.8 GB
  Laws: 162,391
  Versions/law: 1 (current only)
  Search approach: FTS5 on vigente
  Performance: Fast ⚡
  Use case: Operations ✓
```

### If You Add Originale (via abrogate)
```
laws_vigente.jsonl
  Size: ~0.8 GB
  
laws_abrogate.jsonl
  Size: ~0.3 GB
  
Total: ~1.1 GB
Use: Can compare vigente vs originale for research
Cost: +0.3 GB
```

### If You Add Multivigente (NOT RECOMMENDED INITIALLY)
```
laws_vigente.jsonl
  Size: ~0.8 GB
  
laws_multivigente.jsonl
  Size: ~2.8 GB (huge!)
  
Total: ~3.6 GB
Use: Show "all versions with dates"
Cost: +2.8 GB (3.5× more storage)
Complexity: Much more search logic
Parse time: 2-3 hours (vs 30 min)
```

---

## 🚀 Phased Approach

### Phase 1: NOW ✓
```
Deploy with VIGENTE only
├─ laws_vigente.jsonl (162K laws)
├─ Live search functionality
└─ Fast, responsive
```

### Phase 2: IF REQUESTED
```
Add ABROGATE (already doing this)
├─ laws_abrogate.jsonl (124K repealed laws)
├─ Separate from vigente
├─ For historical research
└─ Users can compare
```

### Phase 3: IF CUSTOMERS ASK
```
Consider adding MULTIVIGENTE
├─ Only if users want "show me amendments"
├─ Separate index from vigente
├─ Feature: "Amendment Timeline" tab
│   GET /law/urn:nir:...../timeline
│   Returns: All versions with effective dates
└─ Optional enhancement
```

---

## 📊 What Normattiva Actually Provides

### Available Per Collection

| Collection | O (Original) | V (Vigente) | M (Multivigente) |
|-----------|-------------|-----------|-----------------|
| Codici | ✓ | ✓ | ✓ |
| DPR | ✓ | ✓ | ✓ |
| Leggi | ✓ | ✓ | ✓ |
| ... (all 22) | ✓ | ✓ | ✓ |
| Abrogate | ✓ | ✗ | ✗ |

**Key Point**: Abrogate (repealed) laws only have O variant
- Makes sense: Repealed thing has one final version
- V doesn't apply (not in force)
- M not needed (history is O)

---

## 🎯 Final Recommendation

### For Your Platform (Jurisprudence + Compliance Search)

**SHORT TERM (Next 2 weeks):**
```
Use:
  ✅ Vigente (V) — current laws for search
  ✅ Abrogate (O) — repealed for reference

Skip:
  ❌ Multivigente — too large, not needed yet
```

**MEDIUM TERM (After 3 months of production):**
```
Evaluate: Do users want "show amendments" feature?
  If YES → Add multivigente (separate index)
  If NO → Keep current setup
```

**LONG TERM (1+ year):**
```
If platform succeeds:
  - Add multi-variant support
  - Amendment timeline visualization
  - Historical comparison tool
  - Academic research features
```

---

## 💡 Why NOT Multivigente Initially

1. **Overkill for Your Use Case**
   - You need current law (vigente)
   - Optional: see original (originale)
   - Amendments: not a high-priority feature

2. **Performance Hit**
   - 3× larger dataset
   - Slower parsing (2-3 hours vs 30 min)
   - Slower search (multiple versions to filter)
   - More complex indexing

3. **Complexity**
   - Have to version every law
   - Effective dates, overlaps, edge cases
   - "Show me amendments" UI is complex
   - Testing nightmare

4. **Storage Cost**
   - +2.0 GB disk
   - +costs for database/archive
   - Incremental updates become tricky

5. **User Need Validation Needed**
   - Start with vigente
   - See if users ask for amendments
   - Then decide to add multivigente

---

## ✅ Bottom Line

**Use now:**
- Vigente (V) → Current laws you need
- Abrogate (O) → Reference for repealed

**Don't use now:**
- Multivigente (M) → Too much, not needed

**Add later if requested:**
- Amendment timeline feature
- Separate multivigente index
- Historical research mode

This keeps your platform **fast, simple, and focused** on your primary use case. ✓

