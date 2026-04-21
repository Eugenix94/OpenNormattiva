# OpenNormattiva - Deployment & Feature Guide

## 🚀 Current Status (April 12, 2026)

### ✅ Deployed & Live
- **Space**: https://huggingface.co/spaces/diatribe00/normattiva-search
- **Dataset**: https://huggingface.co/datasets/diatribe00/normattiva-data
- **Data**: 157,121+ Italian laws with full FTS5 search, citation graphs, amendment tracking

### Fixed Issues
1. ✅ **Database not loading** → Implemented automatic download from HF Dataset on first startup
2. ✅ **1GB storage limit** → Split deployment: Space (code only) + Dataset (database)
3. ✅ **Streamlit duplicate element errors** → Added unique keys to radio widget
4. ✅ **Path detection** → Robust multi-path database detection with retry logic

---

## 📖 How to Use the Platform

### **Page 1: Dashboard**
- Overview of all 157,121 laws
- Legal domain distribution chart
- Most important laws by PageRank
- Laws spanning 1800-2026

### **Page 2: Search**
- Full-text search using BM25 ranking
- Advanced filters: year range, law type, domain
- Click on any result to view full details

### **Page 3: Browse**
- Paginated list of all laws
- Sort by year, importance, or type
- Filter by legal domain or status

### **Page 4: Law Detail** ⭐ **(Enhanced with linking)**
When you select a law, you get:
  
  1. **📄 Full Text Tab**
     - Complete law text
     - Metadata: URN, date, type, character count
     - Legal domain classification
     - Page Rank importance score

  2. **🔗 Citation Links Tab** ⭐ **(Law-to-Law Navigation)**
     - **Incoming Citations**: Which laws cite THIS law (law is referenced by others)
       - Click expander to see details of citing law
       - Preview context where citation appears
       - "View full law" button to navigate
     
     - **Outgoing Citations**: Which laws THIS law references (law depends on)
       - See all dependencies
       - Understand legal prerequisites
       - Navigate to referenced laws

  3. **📚 Related Laws Tab**
     - **Domain Peers**: Other laws in the same legal field
       - Contextually relevant laws (e.g., all tax laws together)
       - Sorted by importance
       - Single-click navigation
     
     - **Co-Citation Network**: Laws cited together
       - Jurisprudentially related cases
       - Similar legal reasoning
       - Network-based recommendations

  4. **⚖️ Amendments Tab**
     - Complete modification history
     - Shows which laws amended or repealed this one
     - Timeline of changes

  5. **🎯 Context Graph Tab**
     - Visual citation network (depth 2)
     - Shows up to 50 connected laws
     - Interactive graph layout
     - All connected references visualized

### **Page 5: Citations**
- Network explorer showing citation patterns
- Most-cited laws in Italian legal system
- Citation frequency analysis

### **Page 6: Domains**
- Browse by legal domain (tax, civil, criminal, etc.)
- Domain statistics and trends
- Laws grouped by area of law

### **Page 7: Notifications**
- Monitors Normattiva API for new/changed laws
- Flags collections with updates
- Shows what datasets need refreshing

### **Page 8: Update Log**
- History of manual dataset updates
- Timestamps and change counts
- Version tracking

### **Page 9: Export**
- Download laws as CSV, JSON, or JSONL
- Export search results
- Bulk export to other formats

---

## 🔗 **How Law-to-Law Linking Works**

### Architecture
```
┌─────────────────────────────────────────────────┐
│  SQLite Database (laws.db - 811MB)              │
│  ├─ laws table: URN, title, text, metadata      │
│  ├─ citations table: source_urn → target_urn    │
│  ├─ amendments table: modification history      │
│  ├─ law_metadata table: domain, importance      │
│  └─ FTS5 index: full-text search (BM25)        │
└─────────────────────────────────────────────────┘
        ↓
    HF Dataset
        ↓
┌─────────────────────────────────────────────────┐
│  Streamlit App (space/app.py)                   │
│  ├─ Auto-downloads DB on first load             │
│  ├─ Caches in ~/.cache/huggingface              │
│  ├─ All pages use same DB connection            │
│  └─ Citation queries in real-time               │
└─────────────────────────────────────────────────┘
```

### Citation Navigation Flow
```
View Law A  →  Incoming Citations
               (Laws that cite A)
                    ↓
              Select citing law B
                    ↓
         View Law B (full text & all its citations)
                    ↓
            See B's dependencies
            (laws B cites)
                    ↓
              Co-citation analysis
         (laws cited together with B)
```

### Example: Navigate Constitution to Modern Laws
1. Search "Costituzione Italiana" → view Constitution
2. See "Cited by: 8,234 laws" in Citation Links tab
3. Click expander on any law (e.g., 2024 privacy law)
4. See it cites Constitution on "fundamental rights"
5. Click "View dependency" → see Constitution article
6. In same article, see other modern laws that cite it
7. Browse related laws in "Civil Rights" domain
8. View entire network in citation graph

---

## 🔧 Technical Implementation

### Database Queries Used
- **Incoming citations**: `SELECT citing_urn FROM citations WHERE cited_urn = ?`
- **Outgoing citations**: `SELECT cited_urn, context FROM citations WHERE citing_urn = ?`
- **Related laws**: Co-citation similarity algorithm (laws cited together)
- **Domain peers**: `SELECT * FROM laws WHERE domain_cluster = ?`
- **Amendment history**: `SELECT * FROM amendments WHERE original_urn = ?`
- **Citation graph**: Recursive query with depth limit (2 levels)

### Performance Optimization
- ✅ Database caching in `~/.cache/huggingface/` (one-time download)
- ✅ Streamlit `@st.cache_resource` for DB connection
- ✅ Limit displayed results (20 shown, more available)
- ✅ Graph limited to 50 nodes (depth 2) for rendering
- ✅ FTS5 indexes for fast full-text search

### File Structure
```
OpenNormattiva/
├── space/
│   └── app.py              # Main Streamlit app (9 pages)
├── core/
│   ├── db.py               # Database layer + queries
│   ├── legislature.py      # Parliament tracking
│   └── changelog.py        # Update tracking
├── data/
│   ├── laws.db             # (811MB - in HF Dataset, not in Space)
│   ├── .etag_cache.json    # Collection ETags for API monitoring
│   └── processed/
│       └── laws_vigente.jsonl  # (1.46GB JSONL backup - also in Dataset)
├── deploy_hf.py            # Deployment automation
├── normattiva_api_client.py # Normattiva API wrapper
├── parse_akn.py            # URN parsing
├── Dockerfile              # Docker build (Space)
└── requirements.txt        # Python dependencies
```

---

## 📊 What Makes It "Jurisprudentially Precise"

1. **Citation Context**: Shows snippets of text WHERE law A cites law B
   - Understand legal reasoning
   - Not just "these laws are related"
   
2. **Amendment Tracking**: See evolution of laws
   - Original law → Modified by → Repealed by
   - Understand current legal landscape
   
3. **Domain Classification**: Laws grouped by legal field
   - Tax law, civil law, criminal law, etc.
   - Find analogous cases in same domain
   
4. **Network Analysis**: Co-citation patterns
   - Laws cited together = jurisprudentially related
   - Discover precedent chains
   - Understand legal dependencies
   
5. **PageRank Scoring**: Importance by citation count
   - Most cited = most fundamental laws
   - Identify cornerstone legislation

---

## 🚀 Performance on First Load

| Phase | Time | Notes |
|-------|------|-------|
| Space boot-up | 30-60s | Docker container building |
| Database download | 3-5 min | 811MB from HF Dataset (~200MB/s typical) |
| Cache creation | <1s | SQLite WAL + indexes |
| App ready | **4-6 min** | Can then search 157k laws instantly |

**After first load**: All subsequent loads <5s (cached database)

---

## 🔄 How to Update the Platform

### Adding New Laws
1. Run `download_normattiva.py` to fetch updates
2. Merge into `data/laws.db`
3. Rebuild citation graph (automatic)
4. Run `deploy_hf.py` to update Dataset

### Refreshing the Platform
```bash
# Locally
python download_normattiva.py
python deploy_hf.py

# Redeploy to Space takes: ~2-3 min
# Database download on Space: ~4-5 min
```

---

## 🎯 Next Steps for Enhancement

### Feature Priorities
1. **URL Query Parameters** - Deep linking: `?urn=urn:nir:stato:legge:2023;100`
2. **Full-Text Context Links** - Click any URN in law text to navigate
3. **Amendment Visualization** - Timeline showing law evolution
4. **Advanced Filters** - Citation count ranges, importance thresholds
5. **Export to Legal Tools** - Integration with Westlaw/LexisNexis formats

### Performance Improvements
1. **LRU Cache** - Cache citation queries
2. **Pre-computed Networks** - Pre-calculate most-used citation paths
3. **Search Autocomplete** - Suggest URNs while typing

### Data Enhancements
1. **CELEX Cross-References** - Link to EU directives
2. **Court Case Integration** - Link to constitutional court decisions
3. **Legal Commentary** - Attach doctrine/scholarship
4. **Legislative History** - Track proposed → passed → modified

---

## 📞 Support & Troubleshooting

### "Database not loading" Error
1. Wait 5-10 minutes for Space to fully initialize
2. Refresh page (Space rebuilds container)
3. Check: https://huggingface.co/spaces/diatribe00/normattiva-search/logs

### Searching Returns No Results
- Try: "costituzione" (simple terms work better)
- Use Advanced Filters to narrow year range
- FTS5 search is case-insensitive and supports wildcards

### Slow Citation Queries
- First run caches results
- Closing/reopening page may show cached data
- Graph visualization limits to 50 nodes for performance

---

## 📄 License & Attribution

- **Data**: Italian laws from Normattiva (Public domain)
- **Platform**: MIT License
- **Dataset**: CC-BY-4.0 (HF platform requirement)
- **Code**: Available in this repository

**Citation**:
```bibtex
@dataset{normattiva2026,
  title={OpenNormattiva: Italian Law Dataset},
  author={OpenNormattiva Contributors},
  url={https://huggingface.co/datasets/diatribe00/normattiva-data},
  year={2026}
}
```

---

## 🎉 You're Ready!

Visit: https://huggingface.co/spaces/diatribe00/normattiva-search

Start by:
1. Click **Dashboard** to see overview
2. Search "costituzione" to find Constitution
3. Click into Constitution detail page
4. Explore **Citation Links** tab to see all dependent modern laws
5. Navigate through the citation network

Enjoy exploring Italian jurisprudence! ⚖️

