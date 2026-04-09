# Jurisprudential Platform: Gap Analysis & Roadmap

## Current State

✅ **Data Layer (Mostly Complete)**
- Download pipeline: vigente collections
- Parsing: AKN → JSONL
- Citation extraction: Basic regex
- Amendment tracking: Log structure
- Indexes: Citations, metrics
- Storage: HF Dataset + Streamlit app

⚠️ **Access Layer (Minimal)**
- Basic text search (simple substring matching)
- Citation bar chart (top 20 only)
- Browse page (stub)
- Dashboard (basic stats)
- No API, no advanced querying, no real graph exploration

---

## What's Missing: Tier 1 (Critical for Jurisprudence)

### 1. Advanced Citation Graph Exploration 🔗
**Current:** Bar chart of top 20 cited laws  
**Needed:** Interactive network visualization + traversal

```
Features:
- Render citation network as interactive graph (Pyvis/D3)
- Click a law → see all laws it cites
- Click law → see all laws that cite IT
- Trace citation paths (law A → B → C → D)
- Find most influential laws (centrality analysis)
- Citation clustering (related laws by topic)
```

**Why:** Core to legal research - need to understand how laws reference each other

**Tools:** Pyvis, networkx, vis.js  
**Effort:** Medium (2-3 days)  
**Impact:** High (enables semantic navigation)

---

### 2. Law Relationships & Amendments 📋
**Current:** Just counts articles  
**Needed:** Amendment chains, repealers, implementing regulations

```
Features:
- Track which laws amend which (cross-references)
- Show amendment timeline for each law
- Identify repealing/repealed relationships
- Find implementing regulations
- Show "active in effect since" date vs amendment date
```

**Why:** Essential for compliance - need to know current state and history

**Implementation:**
- Extract from law text: "abrogato da" (repealed by), "modificato da" (modified by)
- Build amendment graph
- Track temporal validity

**Tools:** Custom parser, temporal database logic  
**Effort:** Medium (2-3 days)  
**Impact:** Very High (enables temporal legal research)

---

### 3. Full-Text Search with Filters 🔍
**Current:** Simple substring search  
**Needed:** Production search engine

```
Features:
- Keyword + phrase search (not just substring)
- Filters: law type, year range, article count, status
- Boolean operators (AND, OR, NOT)
- Citation count filters
- Amendment date filters
- Type-ahead/autocomplete
- Relevance ranking
```

**Why:** Users need to find laws efficiently by criteria

**Implementation Options:**
- **A) Elasticsearch** - Professional, scalable, but complex setup
- **B) SQLite/FTS** - Simple, works locally, fast enough
- **C) Streamlit caching + client-side filtering** - Quick but limited

**Recommended:** SQLite FTS (good balance, easy to deploy)

**Tools:** sqlite3.FTS5  
**Effort:** Medium (1-2 days)  
**Impact:** High (core UX requirement)

---

### 4. Diff View for Amendments 📊
**Current:** None  
**Needed:** See before/after for amended laws

```
Features:
- Show original text vs current text
- Highlight changed sections
- Show which law modified it
- Show modification date
- Link to amending law
```

**Why:** Lawyers need to see what changed and when

**Tools:** difflib (Python), side-by-side HTML viewer  
**Effort:** Medium (1-2 days)  
**Impact:** High (essential for legal analysis)

---

## What's Missing: Tier 2 (Enhance Usability)

### 5. Law Metadata Enrichment 📝
**Current:** Basic (URN, title, date, type, articles)  
**Needed:** Enhanced fields

```
Add to each law:
- Authority (Parliament, Minister, etc.)
- Status (in force, repealed, suspended)
- Implementing regulations (child laws)
- Implemented by (parent law if delegated)
- Keywords/tags (extracted or manual)
- Official summary/abstract
- Amendment count
- Citation count
- Last modified date
```

**Effort:** Low-Medium (1 day)  
**Impact:** Medium (improves browsing)

---

### 6. Related Laws Suggestions 🤝
**Current:** None  
**Needed:** Semantic recommendations

```
When viewing law X, suggest:
- Laws that cite law X (incoming)
- Laws that law X cites (outgoing)
- Laws with similar title keywords
- Laws of same type from same year
- Laws that amend law X
- Laws amended by law X
```

**Implementation:** Hybrid approach
- Citation-based: Use existing citation index
- Keyword-based: Simple cosine similarity on titles
- Type-based: Same law type + nearby years

**Effort:** Low (1 day)  
**Impact:** Medium (improves discovery)

---

### 7. Statistics & Analytics Dashboard 📊
**Current:** Basic type/year distribution  
**Needed:** Rich analytics

```
Add:
- Amendment frequency over time (timeline)
- Most amended laws (top 20)
- Amendment sources (which laws amend which types?)
- Citation density by type (laws with most references)
- Coverage by domain (count laws by keyword clusters)
- Temporal evolution (laws per year, amendments per year)
- Correlation: law complexity vs citations
```

**Tools:** Plotly, pandas  
**Effort:** Low (1-2 days)  
**Impact:** Medium (good for understanding the corpus)

---

### 8. Export & API 🔌
**Current:** None  
**Needed:** Programmatic access

```
Features:
- Export single law as JSON/XML/PDF
- Export query results as CSV/JSON
- REST API endpoints:
  GET /laws/{urn}
  GET /laws/search?q=...&type=...&year=...
  GET /laws/{urn}/citations  (incoming + outgoing)
  GET /laws/{urn}/amendments
  GET /amendments?date_from=...&date_to=...
- API documentation (Swagger/OpenAPI)
```

**Implementation:**
- Use FastAPI or Flask
- Deploy alongside Streamlit
- Cache responses

**Effort:** Medium (1-2 days)  
**Impact:** High if users are programmers, Low if not

---

## What's Missing: Tier 3 (Advanced Features)

### 9. Annotation & Notes 📌
- User bookmarks
- Personal notes on laws
- Highlight important sections
- Share notes with others
- Comment threads

**Effort:** High (requires backend DB)  
**Impact:** Medium

---

### 10. Citation Impact Analysis 📈
- H-index for laws (how many times cited by other cited laws?)
- Citation velocity (how fast did citations accumulate?)
- Citation lifespan (when did citations start/stop?)
- Citation clustering (topics that co-cite)

**Effort:** Medium (uses existing data)  
**Impact:** Low-Medium

---

### 11. Legal Reasoning Chains ⛓️
- Auto-detect "based on" relationships
- Show reasoning paths (law X justified by law Y which cites law Z)
- Build argument graphs

**Effort:** High (NLP + custom logic)  
**Impact:** High (but advanced)

---

### 12. Bulk Operations & Batch Analysis
- Compare multiple laws at once
- Batch search
- Export multiple laws
- Build custom collections

**Effort:** Low-Medium  
**Impact:** Low (niche use case)

---

## Priority-Based Roadmap

### Phase 1 (This Week): Core Search & Browse
- [ ] SQLite FTS search with filters
- [ ] Improved browse interface (pagination + sorting)
- [ ] Law detail page with related laws

**Effort:** 2-3 days  
**Impact:** High (core UX)

---

### Phase 2 (Next Week): Citation Intelligence
- [ ] Interactive citation graph (Pyvis)
- [ ] Citation path tracing
- [ ] Related laws suggestions
- [ ] Citation statistics dashboard

**Effort:** 3-4 days  
**Impact:** Very High (core to jurisprudence)

---

### Phase 3 (Week 3): Law Timeline & Amendments
- [ ] Amendment extraction from text
- [ ] Amendment timeline visualization
- [ ] Diff view for amendments
- [ ] "Last modified" metadata

**Effort:** 2-3 days  
**Impact:** Very High (essential for compliance)

---

### Phase 4 (Week 4+): Polish & API
- [ ] REST API endpoints
- [ ] Export capabilities (JSON, CSV, PDF)
- [ ] Analytics dashboard enhancements
- [ ] Performance optimization

**Effort:** 2-3 days  
**Impact:** Medium-High

---

## Implementation Priority Matrix

| Feature | Difficulty | Impact | Priority | Time |
|---------|-----------|--------|----------|------|
| **Tier 1 (Must Have)** | | | | |
| Advanced search (FTS) | Low | High | 1 | 1 day |
| Citation graph visualization | Medium | Very High | 2 | 2-3 days |
| Amendment tracking | Medium | Very High | 3 | 2-3 days |
| Diff view | Medium | High | 4 | 1-2 days |
| **Tier 2 (Should Have)** | | | | |
| Related laws suggestions | Low | Medium | 5 | 1 day |
| Enriched metadata | Low | Medium | 6 | 1 day |
| Analytics dashboard | Low | Medium | 7 | 1-2 days |
| API endpoints | Medium | Medium | 8 | 1-2 days |
| **Tier 3 (Nice to Have)** | | | | |
| Annotation/notes | High | Low | 9+ | 2-3 days |
| Citation impact analysis | Medium | Low | 10+ | 1-2 days |
| Legal reasoning chains | High | High | 11+ | 5+ days |

---

## Recommended Next Step

**Start with Phase 1 (Search):**

1. Replace current text search with SQLite FTS
2. Add filters: type, year, article count, citation count
3. Improve results display (show snippets, relevance)
4. Add sorting options

**Why:**
- Users can't find anything without good search
- Enables all downstream features
- Relatively quick to implement
- Immediate UX improvement

---

## Minimal Viable Jurisprudential Platform

To be considered "production-ready for legal research," you need:

✅ **Must Have:**
1. Full-text search with filters
2. Citation graph exploration
3. Amendment tracking
4. Law detail page with metadata
5. Browse/filter interface

⚠️ **Very Useful:**
6. Related laws suggestions
7. Diff view for amendments
8. Export capabilities
9. API for programmatic access

❌ **Nice But Not Essential:**
10. Annotations
11. Complex analytics
12. AI-powered reasoning

---

## Questions to Help Prioritize

1. **Who is your primary user?**
   - Law students/researchers → Need search + graph + amendments
   - Compliance officers → Need amendments + timeline
   - Legal tech developers → Need API + export

2. **What's the main use case?**
   - References/citations lookup → Graph first
   - Historical analysis → Amendments first
   - Research exploration → Search first

3. **Deployment target?**
   - Public web (HF Space) → Simple stack (Streamlit + SQLite)
   - Private/enterprise → Can use advanced architecture

4. **Timeline pressure?**
   - Quick MVP (1 week) → Phase 1 only
   - Full platform (1 month) → Phases 1-3
   - Production ready (ongoing) → All phases

---

**What should we build first?**
