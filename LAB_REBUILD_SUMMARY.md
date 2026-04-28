# OpenLaw Lab Rebuild — Complete Architecture Summary

**Status**: ✅ Ready for Deployment  
**Date**: April 26, 2026  
**Type**: Enhanced Jurisprudence Research Platform

---

## What Changed

### Before
- Lab was a simple clone of production space
- Only contained vigente laws
- Limited to basic search/browse functionality
- No jurisprudence support

### After (NEW)
- Lab is now a **comprehensive research platform**
- Contains **all normattiva data** (vigente + abrogate + multivigente)
- **Integrated Constitutional Court** (Corte Costituzionale) jurisprudence
- **Advanced analytics** for law and jurisprudence exploration
- **Separate enhanced Streamlit app** with lab-specific features
- **Sophisticated database** with normattiva + sentenze tables

---

## Files Created

### 1. Core Database Layer (`core/lab_db.py`)
**Purpose**: Enhanced database with jurisprudence support

```python
class LabDatabase:
    # Normattiva tables (inherited)
    - laws
    - citations
    - domains
    
    # NEW Jurisprudence tables
    - sentenze (Constitutional Court decisions)
    - sentenza_citations (citations from sentenze to laws)
    - sentenza_topics (constitutional principles)
    - law_jurisprudence_links (cross-references)
```

**Key Methods**:
- `insert_sentenza()` — Add a court decision
- `insert_sentenza_citations()` — Link sentenza to cited laws
- `search_sentenze()` — Full-text search jurisprudence
- `get_sentenze_for_law()` — Find jurisprudence affecting a law
- `get_stats()` — Jurisprudence statistics

### 2. Data Loader (`jurisprudence_loader.py`)
**Purpose**: Load Constitutional Court data into lab database

**Sources Supported**:
- Normattiva API (future)
- JSON files
- Python objects
- Sample data generator (for testing)

**Usage**:
```python
from jurisprudence_loader import JurisprudenceLoader

loader = JurisprudenceLoader('data/laws.db')
count = loader.create_sample_data()  # Load 66 sample sentenze
loader.load_from_json_file('sentenze.json')  # Load from file
```

### 3. Enhanced Streamlit App (`space/enhanced_lab_app.py`)
**Purpose**: Lab-specific UI with jurisprudence focus

**Pages**:
1. **🔬 Lab Dashboard** — Stats, trends, most-cited laws
2. **🔍 Integrated Search** — Search laws + sentenze together
3. **📖 Law + Jurisprudence Explorer** — Browse with connections
4. **🕰️ Multivigente Analysis** — Historical law versions
5. **📊 Advanced Analytics** — Citation networks, domains, trends

**Features**:
- Separate from production app.py
- Optimized for deep research (not just search)
- Includes lab data (vigente + abrogate + multivigente)
- Ready for jurisprudence visualization

### 4. Updated Deployment Script (`clone_to_lab.py`)
**Changes**:
- ✅ Copies `jurisprudence_loader.py` to lab space
- ✅ Uses enhanced app as default (if available)
- ✅ Updated startup.sh to load sample sentenze
- ✅ Enhanced README describing lab capabilities
- ✅ New startup.sh that initializes jurisprudence tables

**Deployment Process**:
```bash
python clone_to_lab.py
  ├─ Clone/update dataset (diatribe00/normattiva-lab-data)
  ├─ Clone/update space (diatribe00/normattiva-lab)
  ├─ Deploy enhanced app
  └─ Enable jurisprudence on first startup
```

### 5. Documentation
- **LAB_DEPLOYMENT_GUIDE.md** — Step-by-step deployment instructions
- **LAB_ARCHITECTURE_ENHANCED.md** (in memory) — Technical architecture

---

## Database Schema

### Normattiva Tables (Existing)
```sql
laws
├─ urn (PK)
├─ title
├─ year
├─ type
├─ status (in_force | abrogated)
├─ article_count
├─ text_length
├─ importance_score
└─ domain

citations
├─ citing_urn
├─ cited_urn
└─ citation_context

domains
├─ law_urn
└─ domain
```

### NEW Jurisprudence Tables
```sql
sentenze
├─ id (PK)
├─ decision_id (UNIQUE)
├─ court (e.g., "Corte Costituzionale")
├─ decision_date
├─ decision_year
├─ decision_type (sentenza | ordinanza)
├─ number
├─ urn
├─ title
├─ summary
└─ full_text

sentenza_citations
├─ id (PK)
├─ sentenza_id (FK)
├─ cited_urn (law URN)
├─ cited_title
├─ citation_type (explicit | implicit)
└─ citation_context

sentenza_topics
├─ id (PK)
├─ sentenza_id (FK)
├─ topic (constitutional principle)
└─ relevance_score

law_jurisprudence_links
├─ id (PK)
├─ law_urn
├─ sentenza_id (FK)
└─ link_type (cited_by | overridden_by | clarified_by)
```

---

## Data Features

### Normattiva (Full)
- **190,920 laws** (vigente + abrogate)
- **Vigente**: 156,122 laws
- **Abrogate**: 33,798 laws
- **Multivigente**: Optional (66+ laws with historical versions)
- **Citations**: 193,910 cross-references
- **Domains**: 15 legal domains
- **Years**: 1861–2026

### Jurisprudence (NEW)
- **Sample data**: 66 Constitutional Court decisions (pre-loaded)
- **Real data**: Ready to load from Normattiva API or JSON
- **Topics**: Constitutional principles (diritti, separazione poteri, etc.)
- **Citations**: Links to affected laws

---

## Streamlit Pages Comparison

| Feature | Production | Lab |
|---------|-----------|-----|
| Dashboard | Basic stats | Enhanced + trends |
| Search | Laws only | Laws + sentenze |
| Browse | Law list | Law + jurisprudence |
| Detail | Law text | Law + affecting sentenze |
| Analytics | Domains | Citation networks + domains |
| Multivigente | Not available | Full historical analysis |
| **Total Pages** | 12 | 5 (specialized) |

---

## Deployment Architecture

```
├─ Production (unchanged)
│  ├─ Space: diatribe00/normattiva-search
│  ├─ Dataset: diatribe00/normattiva-data (vigente only)
│  └─ App: app.py (standard)
│
└─ Lab (NEW ENHANCED)
   ├─ Space: diatribe00/normattiva-lab
   ├─ Dataset: diatribe00/normattiva-lab-data (full data)
   ├─ App: enhanced_lab_app.py (advanced)
   ├─ DB: laws + sentenze + topics + citations
   └─ Features: Full normattiva + jurisprudence
```

---

## How to Deploy

### Option 1: Full Deployment (Recommended)
```bash
# Deploy everything
python clone_to_lab.py

# Time: 5-10 minutes
# Includes: dataset clone + space deployment
```

### Option 2: Space Only (Faster)
```bash
# If lab dataset already exists
python clone_to_lab.py --skip-dataset

# Time: 2-5 minutes
```

### Option 3: Dry Run (Preview)
```bash
# See what would happen without making changes
python clone_to_lab.py --dry-run
```

---

## What Happens at Deployment

1. **Dataset** (`normattiva-lab-data`)
   - Created with full data (vigente + abrogate + multivigente)
   - Includes laws.db, laws_vigente.jsonl, laws_multivigente.jsonl

2. **Space** (`normattiva-lab`)
   - Deployed with enhanced app
   - Includes jurisprudence_loader.py
   - Starts with enhanced startup.sh

3. **First Startup**
   - Downloads laws.db from dataset
   - Initializes jurisprudence tables in database
   - Loads 66 sample Constitutional Court decisions
   - Launches enhanced Streamlit UI

4. **Ready to Use**
   - Access at: https://huggingface.co/spaces/diatribe00/normattiva-lab
   - All 5 lab pages available immediately
   - Sample sentenze ready for exploration

---

## Next Steps (Optional)

After deployment, you can:

### 1. Add Real Constitutional Court Data
```bash
# Fetch from Normattiva API (when ready)
# Or load from JSON file
python -c "
from jurisprudence_loader import JurisprudenceLoader
loader = JurisprudenceLoader('/app/data/laws.db')
loader.load_from_json_file('sentenze.json')
"
```

### 2. Add Other Jurisprudence Sources
- Corte di Cassazione (Supreme Court)
- Administrative Courts
- EU Court decisions (Euro law)

### 3. Build Advanced Features
- Citation graph visualization (laws ↔ sentenze)
- Jurisprudence impact scoring
- Automated legal brief generation
- Trend analysis over time

---

## Files Summary

| File | Purpose | Status |
|------|---------|--------|
| `core/lab_db.py` | Enhanced DB with sentenze | ✅ Created |
| `space/enhanced_lab_app.py` | Lab Streamlit app | ✅ Created |
| `jurisprudence_loader.py` | Sentenze data loader | ✅ Created |
| `clone_to_lab.py` | Deployment script | ✅ Updated |
| `LAB_DEPLOYMENT_GUIDE.md` | Deployment instructions | ✅ Created |
| Production files | Unchanged | ✅ Untouched |

---

## Verification Checklist

Before deploying:
- ✅ All files compile without errors
- ✅ Imports work correctly
- ✅ Database schema is sound
- ✅ Deployment script is updated
- ✅ Documentation is complete

---

## Key Differences from Original Lab

| Aspect | Original | Enhanced |
|--------|----------|----------|
| Purpose | Simple clone | Research platform |
| Data | Vigente only | Full normattiva |
| Jurisprudence | None | Constitutional Court built-in |
| UI | Production app.py | Enhanced lab app |
| Pages | 12 standard | 5 specialized |
| Analytics | Basic | Advanced |
| Target User | General | Researchers |

---

**Status**: Ready for immediate deployment  
**Next Action**: Run `python clone_to_lab.py` to activate

