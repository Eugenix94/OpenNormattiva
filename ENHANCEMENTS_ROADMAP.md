# Data Building Complete - Remaining Enhancements

## Current Status
After running `auto_build_data.py`, you will have:
- ✅ All 22 vigente collections downloaded (162,391 laws)
- ✅ Complete JSONL dataset (laws_vigente.jsonl)
- ✅ SQLite database with FTS5 full-text search
- ✅ Citation indexes (outgoing + incoming)
- ✅ Search indexes (top terms)
- ✅ Build report with statistics

## What's NOT Yet Done (Remaining Enhancements)

### 1. **Amendment Tracking System** (Priority: High)
Track how laws have changed over time.

```python
# scripts/track_amendments.py
# Compares sequential downloads to detect:
# - New articles added
# - Articles removed
# - Text changes
# - Status changes (in_force → abrogated → reinstated)

# Output: amendments_log.jsonl
# {
#   "urn": "urn:nir:decreto.legge:2024-01-15;1",
#   "last_check": "2026-04-09",
#   "amendments": [
#     {"date": "2024-01-15", "action": "enacted"},
#     {"date": "2024-03-20", "action": "amended_by", "amended_by": "legge:2024-03-20;15"},
#     {"date": "2025-01-15", "action": "abrogated"}
#   ]
# }
```

**Why needed:**
- Jurisprudence requires knowing when laws changed
- Compliance audits need version history
- Migration tracking for law updates

**Effort:** ~4 hours

---

### 2. **Relationship Graph** (Priority: High)
Build network analysis for law citations.

```python
# scripts/build_citation_graph.py
# Creates graph structure:
# - Node: each law (URN)
# - Edge: citation relationship with metadata
# - Generate PageRank (which laws are most cited)
# - Detect law clusters (related domains)

# Output: citation_graph.json
# {
#   "nodes": [
#     {"id": "urn:nir:decreto.legge:2024-01-15;1", 
#      "title": "...", 
#      "pagerank": 0.052, 
#      "domain": "public_health"}
#   ],
#   "edges": [
#     {"source": "urn:...", "target": "urn:...", "weight": 1}
#   ]
# }

# Can visualize with Pyvis in Streamlit
```

**Why needed:**
- Understand law dependencies
- Find similar laws
- Identify flagship/core laws by citation count

**Effort:** ~6 hours

---

### 3. **Live Update Checker** (Priority: Medium)
Automated weekly updates without re-downloading everything.

```python
# scripts/live_updater.py (GitHub Actions scheduled job)
# 1. GET ETags from Normattiva API
# 2. Compare with stored ETags
# 3. Re-download only changed collections
# 4. Merge changes to database
# 5. Update amendment log
# 6. Trigger GitHub action on changes

# Run weekly via GitHub Actions:
# .github/workflows/update-laws.yml
schedule:
  - cron: '0 0 ? * SUN'  # Every Sunday at midnight
```

**Why needed:**
- Laws are updated by Italian government
- Need real-time currency for jurisprudence
- Amendment tracking depends on it

**Effort:** ~3 hours

---

### 4. **Search Optimization** (Priority: High)
Improve full-text search speed and relevance.

```python
# Enhancements needed:
# 1. Add FTS5 ranking function (current = no ranking)
# 2. Cache frequent searches (LRU cache)
# 3. Implement faceted search (by type, year, etc.)
# 4. Add fuzzy matching for typos
# 5. Create search analytics (popular terms)

# Example optimized search:
results = db.search_fts(
    query="protezione civile",
    filters={'type': 'decreto_legge', 'year_min': 2020},
    fuzzy=True,
    limit=50
)
# Returns: title, score, type, year, snippet
```

**Why needed:**
- Current search is basic (no ranking)
- Users expect Google-like relevance
- Filtering is manual post-query

**Effort:** ~5 hours

---

### 5. **Export Capabilities** (Priority: Medium)
Allow data downloads in various formats.

```python
# scripts/export_data.py

export_formats = {
    'jsonl': 'Raw JSONL for analysis',
    'csv': 'Laws table (URN, Title, Type, Year)',
    'json_graph': 'Citation graph (GraphJSON format)',
    'xml': 'AKN format (compatible with legislation.gov.uk)',
    'rdf': 'RDF Linked Data (dcterms + legal ontology)',
}

# Usage:
# python scripts/export_data.py \
#   --format csv \
#   --filter 'type=decreto_legge AND year>2020' \
#   --output exports/recent_decrees.csv
```

**Why needed:**
- Researchers need data portability
- Data reuse in other systems
- Compliance with open data principles

**Effort:** ~4 hours

---

### 6. **Relationship Inference** (Priority: Medium)
Detect implicit relationships not captured in citations.

```python
# scripts/infer_relationships.py

techniques = {
    'textual_similarity': 'Find laws with similar text (cosine similarity)',
    'domain_clustering': 'Group laws by keywords/domain',
    'temporal_proximity': 'Laws enacted close together often related',
    'hierarchical': 'Constitutional laws anchor civil law',
}

# Output example:
# Law A → "similar_to" → [Law B (89% match), Law C (76% match)]
# Law X → "implements_eu_directive" → Directive 2024/123/EU
```

**Why needed:**
- Explicit cite-based graph missing ~40% of relationships
- Helps find related but not-cited laws
- Improves jurisprudence quality

**Effort:** ~8 hours

---

### 7. **Metadata Enhancement** (Priority: Low)
Add computed metadata to each law.

```python
# For each law, compute:
- 'subject_tags': ['public_health', 'emergency', ...]
- 'importance_score': 0-100 based on citations
- 'update_frequency': how often amended (days)
- 'age_days': days since enacted
- 'related_laws': [similar URNs]
- 'legal_basis': if implementing EU directive
- 'author': which ministry/office created it
- 'affected_sectors': [sectors this law impacts]

# Store in laws table as JSON field
UPDATE laws SET metadata = json_object(
  'importance', ?,
  'subjects', ?,
  'age_days', ?
)
```

**Why needed:**
- Enriches search and discovery
- Enables advanced filtering
- Provides law context

**Effort:** ~3 hours

---

### 8. **Validation & Data Quality** (Priority: Medium)
Ensure data integrity and missing values.

```python
# scripts/validate_database.py

checks = {
    'urn_uniqueness': 'No duplicate URNs',
    'citation_validity': 'All cited URNs exist in database',
    'text_completeness': 'No empty texts (flag for re-download)',
    'date_consistency': 'Date >= original enacted date',
    'type_values': 'Type in known categories',
    'missing_values': 'Report percentage of NULLs per column',
}

# Output: validation_report.json
{
  "status": "passed",
  "checks": {...},
  "issues": [
    {"severity": "warning", "count": 143, "type": "missing_text"}
  ]
}
```

**Why needed:**
- Catch parsing errors
- Identify re-download needs
- Ensure data reliability for jurisprudence

**Effort:** ~2 hours

---

### 9. **Analytics Dashboard** (Priority: Low)
Streamlit dashboard for data overview.

```python
# ui/dashboard.py (Streamlit app)

Features:
- Total laws: 162.4K ✓
- Laws by type (pie chart)
- Laws by year (line chart)
- Most cited laws (top 20)
- Amendment frequency (histogram)
- Database stats (size, FTS speed, etc.)
- Search quality (avg results per query)
- Citation network visualization (Pyvis)
- Recent amendments (time series)
```

**Why needed:**
- Non-technical stakeholders need overview
- Verify data loading succeeded
- Monitor search performance

**Effort:** ~4 hours

---

### 10. **API Endpoints** (Priority: High if deploying)
FastAPI endpoints for programmatic access.

```python
# api/endpoints.py (FastAPI)

@app.get("/laws/search")
def search(q: str, limit: int = 50):
    """Full-text search"""

@app.get("/laws/{urn}")
def get_law(urn: str):
    """Get single law with citations"""

@app.get("/laws/{urn}/citations")
def get_citations(urn: str, direction: str = "both"):
    """Get incoming/outgoing citations"""

@app.get("/laws")
def list_laws(type: str = None, year: int = None, limit: int = 50):
    """Filter and list laws"""

@app.get("/stats")
def get_stats():
    """Database statistics"""

@app.get("/graph/neighbors")
def get_neighbors(urn: str, depth: int = 1):
    """Citation graph neighbors"""
```

**Why needed:**
- Integrate with external systems
- Mobile app support
- Jurisprudence tools access

**Effort:** ~5 hours (if needed)

---

## Implementation Priority Matrix

```
Effort.
  High │  5.Relationships    10.API         3.LiveUpdater
       │  (8h)               (5h)           (3h)
       │
   Med │  4.SearchOptim      1.Amendments   6.Metadata
       │  (5h)               (4h)           (3h)
       │
  Low  │  2.Graph            8.Validation   7.Dashboard
       │  (6h)               (2h)           (4h)
       │  
       └─────────────────────────────────────────────
         Core Jurisprudence  |  System Ops  |  Nice-to-Have
```

## Recommended Implementation Plan

**Phase A - Core Jurisprudence (Week 1):**
1. Amendment Tracking (4h)
2. Search Optimization (5h)
3. Citation Graph (6h)
4. Validation (2h)
**→ 17 hours, enables jurisprudence use**

**Phase B - Operations (Week 2):**
5. Live Updater (3h)
6. Export Capabilities (4h)
7. Relationship Inference (8h)
**→ 15 hours, handles production ops**

**Phase C - Polish (Week 3):**
8. Metadata Enhancement (3h)
9. Analytics Dashboard (4h)
10. API Endpoints (5h)
**→ 12 hours, production-ready**

**Total: ~44 hours → ~1-2 weeks of work**

## Quick Start: Data Building

```bash
# Check status
python scripts/auto_build_data.py --status

# Resume from checkpoint (recommended first time)
python scripts/auto_build_data.py --resume

# Or start fresh
python scripts/auto_build_data.py --full

# Monitor progress
watch -n 30 'python scripts/auto_build_data.py --status'
```

## What's Blocking Jurisprudence Launch?

**Currently blocking:**
- Amendment tracking (don't know when laws changed) ← FIX FIRST
- Search optimization (search results not ranked) ← FIX SECOND
- Citation graph (can't visualize law relationships) ← FIX THIRD

**After those 3:**
- ✅ Can launch basic jurisprudence system
- ✅ Can show related laws by citation
- ✅ Can track amendments
- ✅ Can search effectively

**Nice-to-have before launch:**
- Live updater (delays go live, not blockers)
- Export (documentation feature)
- Dashboard (monitoring)

---

## Success Metrics After Build

```
Metric                   | Current | Target | Role
─────────────────────────────────────────────────
Total laws               | 164     | 162K   | Coverage
Parse success rate       | ?       | 99%+   | Quality
Database load time       | ?       | <30s   | Performance
Search response time     | ?       | <1s    | UX
FTS index size           | ?       | <2GB   | Scalability
Citation relationships   | ~50K    | ~5M    | Graph density
Amendment records        | 0       | ~20K   | Tracking
```

