# Developer Quick Reference: Why These Choices Work

## Technology Decision Matrix

### Why SQLite + Python ecosystem?

**Comparison Table:**

| Criteria | SQLite | PostgreSQL | Elasticsearch |
|----------|--------|-----------|----------------|
| **Setup complexity** | 0 (embedded) | High (server) | Very High |
| **FTS capability** | ✅ FTS5 native | ✅ Full-text | ✅ Best-in-class |
| **Temporal queries** | ✅ Good | ✅ Excellent | ⚠️ Limited |
| **Citation graph** | ✅ SQL joins | ✅ Excellent | ⚠️ Workaround |
| **HF Space deploy** | ✅ Single file | ❌ External DB | ❌ External service |
| **Scalability** | ⚠️ ~100K concurrent | ✅ Millions | ✅ Unlimited |
| **Development speed** | ✅ Fastest | ⏱️ Medium | ❌ Slowest |
| **Cost** | ✅ Free | ⚠️ Hosting fee | ⚠️ Hosting fee |

**Decision:** SQLite for MVP (weeks 1-4), migrate if traffic demands it

---

### Why Pyvis for citation graphs?

**Graph Visualization Options:**

| Tool | Ease | Interactivity | Visualization | Streaming |
|------|------|---------------|---------------|-----------|
| **Pyvis** | ✅ Easy API | ⭐⭐⭐⭐ | ✅ Pretty | ✅ In Streamlit |
| **Plotly** | ✅ Very easy | ⭐⭐⭐ | ⭐⭐ | ✅ Native |
| **D3.js** | ❌ Hard | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⚠️ Complex |
| **Cytoscape** | ⏱️ Medium | ⭐⭐⭐⭐ | ⭐⭐⭐ | ✅ Component |
| **igraph/graphviz** | ❌ Hard | ⭐ | ⭐⭐ | ❌ Static only |

**Decision:** Pyvis (one-liner in Streamlit, interactive, pretty)

---

### Why FastAPI not Flask?

| Aspect | FastAPI | Flask |
|--------|---------|-------|
| **Async support** | ✅ Native | ⚠️ Requires plugin |
| **Auto docs** | ✅ Built-in Swagger | ❌ Manual setup |
| **Type hints** | ✅ Leveraged | ⚠️ Optional |
| **Performance** | ✅ Better | ⭐ Good |
| **Learning curve** | ⏱️ Medium | ✅ Easy |

**Decision:** FastAPI (modern, auto-docs, performance)

---

## Implementation Phases: Detailed Breakdown

### Phase 1a: Data Foundation (Day 1)

**Goal:** Load JSONL → SQLite

```python
# This is your START point
from pathlib import Path
import json
from db import LawDatabase

db = LawDatabase()
db.insert_laws_from_jsonl(Path('data/processed/laws_vigente.jsonl'))
# Result: laws.db created with 162K laws indexed
```

**What to test:**
```python
# Verify load
count = db.conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
assert count == 162391, f"Expected 162391 laws, got {count}"

# Test FTS
results = db.search_fts("protezione civile")
assert len(results) > 0, "FTS not working"

# Test citations
db.conn.execute('INSERT INTO citations (citing_urn, cited_urn) VALUES (?, ?)',
               ('urn:test:1', 'urn:test:2'))
```

**Time:** 1-2 hours  
**Output:** `data/laws.db` (~500-800 MB)

---

### Phase 1b: Amendment Extraction (Days 2-3)

**Goal:** Extract amendment relationships from law text

**Approach:**
1. Scan laws for amendment patterns (regex)
2. Store in `amendments` table
3. Populate `law_metadata.amendment_count`

```python
# Example: What we're extracting from law text:
text = """
Articolo 5 è modificato dal decreto legge 15 gennaio 2024, n. 1

Articolo 10 è abrogato dalla legge 20 maggio 2024, n. 45

La presente legge entra in vigore il 1 febbraio 2024
"""

# We want to extract:
# - Article 5: amended by DL 15-01-2024, n. 1
# - Article 10: repealed by L 20-05-2024, n. 45
# - Effective: 2024-02-01
```

**Validation strategy:**
- Run on 100 random laws
- Spot-check results manually
- Log false positives
- Adjust patterns iteratively

**Time:** 2-3 days  
**Accuracy target:** 80%+ (perfect is impossible due to varied formatting)

---

### Phase 2a: Search Interface (Day 1)

**Simplest version first:**

```python
# space/pages/search.py

import streamlit as st
from db import LawDatabase

st.title("🔍 Search")

db = LawDatabase()

# Mode 1: Quick search (FTS)
query = st.text_input("Search:")
if query:
    results = db.search_fts(query, limit=20)
    st.write(f"{len(results)} results")
    for r in results:
        st.write(f"- {r['title']} ({r['year']})")

# Mode 2: Advanced (filters)
with st.expander("Filters"):
    law_type = st.selectbox("Type:", ["Any", "Legge", "Decreto"])
    year_min = st.slider("Year from:", 1970, 2026, 2000)
```

**NOT to do:**
- Don't try to make it perfect initially
- Don't over-optimize SQL
- Don't build complex UX

**Time:** 1 day (basic version works)

---

### Phase 2b: Citation Graph (Days 2-3)

**Simplest version first:**

```python
# space/pages/citations.py

import streamlit as st
import networkx as nx
from pyvis.network import Network
from db import LawDatabase

st.title("🔗 Citations")

db = LawDatabase()
urn = st.text_input("Enter law URN:")

if urn:
    # Build graph
    G = nx.DiGraph()
    law = db.get_law(urn)
    
    G.add_node(urn, title=law['title'])
    
    # Add outgoing
    for cit in db.get_citations_outgoing(urn):
        G.add_edge(urn, cit['cited_urn'])
    
    # Add incoming
    for cit in db.get_citations_incoming(urn):
        G.add_edge(cit['citing_urn'], urn)
    
    # Visualize
    net = Network(directed=True, height=600)
    net.from_nx(G)
    net.show('graph.html')
    
    with open('graph.html') as f:
        st.components.v1.html(f.read(), height=700)
```

**Time:** 2 days (including styling)

---

### Phase 2c: Law Detail Page (Day 4)

```python
# space/pages/detail.py

def page_detail():
    urn = st.text_input("URN:")
    
    if urn:
        law = db.get_law(urn)
        
        # Header
        st.title(law['title'])
        col1, col2, col3 = st.columns(3)
        col1.metric("Year", law['year'])
        col2.metric("Type", law['type'])
        col3.metric("Articles", law['article_count'])
        
        # Text
        st.text_area("Full Text:", law['text'], height=300, disabled=True)
        
        # Citations - incoming
        st.subheader("👈 Referenced By:")
        incoming = db.get_citations_incoming(urn)
        st.write(f"{len(incoming)} laws cite this one")
        for c in incoming[:5]:
            st.write(f"- {c['title']} ({c['year']}): {c['count']}x")
        
        # Citations - outgoing
        st.subheader("🔗 References:")
        outgoing = db.get_citations_outgoing(urn)
        st.write(f"This law cites {len(outgoing)} laws")
        for c in outgoing[:5]:
            st.write(f"- {c['title']} ({c['year']}): {c['count']}x")
```

**Time:** 1 day (straightforward)

---

### Phase 3a: Amendment System (Days 1-2)

**Goal:** Make amendments queryable

```python
# Stored in amendments table:
amendments = [
    {
        "urn": "urn:nir:legge:2024:45",
        "amending_urn": "urn:nir:decreto:2024:100",
        "action": "amended",
        "article": "5",
        "date_effective": "2024-02-01"
    }
]

# Query: "Show me all laws amended in 2024"
def get_amendments_by_year(year):
    return db.conn.execute('''
        SELECT * FROM amendments
        WHERE CAST(SUBSTR(date_effective, 1, 4) AS INTEGER) = ?
    ''', (year,)).fetchall()
```

**Validation:**
- Cross-check against official records if possible
- Accept ~70% accuracy (human review needed for compliance)

**Time:** 1-2 days

---

### Phase 3b: Timeline Viz (Day 3)

```python
# Streamlit requires just 10 lines:

amendments_df = pd.DataFrame([
    {"Year": int(a['date'][:4]), "Count": 1}
    for a in amendments
]).groupby("Year").sum()

st.line_chart(amendments_df)
```

**Time:** Few hours

---

### Phase 4a: FastAPI Endpoints (Days 1-2)

**Start minimal:**

```python
# api.py

from fastapi import FastAPI
from db import LawDatabase

app = FastAPI()
db = LawDatabase()

@app.get("/laws/{urn}")
def get_law(urn: str):
    law = db.get_law(urn)
    return law if law else {"error": "Not found"}

@app.get("/search")
def search(q: str):
    return db.search_fts(q, limit=20)
```

**Deploy via:**
```bash
uvicorn api:app --port 8000
```

**Time:** 1-2 days

---

### Phase 4b: Export (Day 3)

```python
# Streamlit button
import json

if st.button("Export as JSON"):
    json_str = json.dumps(results, default=str)
    st.download_button(
        label="Download",
        data=json_str,
        file_name="laws.json"
    )
```

**Time:** Few hours

---

## Migration Strategy: Current → New

**Current state:**
- JSONL in `data/processed/laws_vigente.jsonl`
- Basic Streamlit app (6 pages)
- Live updater ready (not yet using DB)

**Step-by-step migration:**

```
Day 1: Build SQLite, load JSONL
├── Run: python -c "from db import *; LawDatabase().insert_laws_from_jsonl(...)"
└── Test: Verify count + FTS works

Days 2-3: Build new features (search, graph, detail)
├── Don't remove old pages yet
├── Add new pages alongside
└── Test each independently

Days 4-7: Integrate with live updater
├── Update live_updater.py to use DB instead of JSONL
├── Sync amendments as laws update
└── Keep JSONL as backup export

After: Remove old JSONL-based code
```

**Zero downtime approach:**
- Both JSONL + SQLite coexist for 1 week
- Users don't notice anything
- Gradual cutover

---

## Code Organization Recommendation

```
OpenNormattiva/
├── core/
│   ├── db.py ......................... ← Start here
│   ├── amendment_parser.py
│   └── citation_graph.py
│
├── api/
│   └── api.py ........................ FastAPI endpoints
│
├── ui/
│   ├── pages/
│   │   ├── search.py
│   │   ├── citations.py
│   │   ├── detail.py
│   │   ├── amendments.py
│   │   ├── export.py
│   │   └── analytics.py
│   └── app.py ........................ Main Streamlit entry
│
├── tests/
│   ├── test_db.py
│   ├── test_amendments.py
│   └── test_api.py
│
└── data/
    ├── laws.db ....................... ← Grows to 500-800 MB
    └── processed/
        └── laws_vigente.jsonl ........ ← Source, keep as backup
```

---

## Testing Strategy

### Unit Tests (Continuous)
```python
# tests/test_db.py
def test_fts_search():
    db = LawDatabase(':memory:')  # In-memory for testing
    db.insert_law({'urn': 'test:1', 'title': 'Test Law', 'text': 'protezione'})
    results = db.search_fts('protezione')
    assert len(results) == 1

def test_citations():
    db = LawDatabase(':memory:')
    # Insert two laws, add citation
    ...
```

### Integration Tests (After each phase)
```python
# Load real data
db = LawDatabase()
db.insert_laws_from_jsonl(Path('data/processed/laws_vigente.jsonl'))

# Test searches
assert db.search_fts("decreto legge").count > 100
assert db.get_law('urn:...') is not None

# Test graph
graph_gen = CitationGraph(db)
G = graph_gen.build_graph_for_law('urn:...')
assert len(G.nodes) > 1
```

### Manual Testing (UI)
- Search for "protezione civile" → Should find laws
- Click on law → Should see full text & citations
- Export → Should download JSON

---

## Performance Optimization Roadmap

### Phase 1 (Weeks 1-2): Sufficient
- Indexes on URN, type, year ✅ (built into SQLite)
- FTS5 (built-in) ✅
- Pagination in UI ✅ (limit 50 results)

### Phase 2 (Weeks 3-4): Enhanced
- Citation count aggregation ✅ (SQL computed field)
- Sorted results by relevance ✅ (FTS BM25)

### Phase 3 (If needed): Advanced
- Caching layer (Redis) - only if load tests show need
- Query result caching (SQLite query cache) - usually sufficient
- Denormalization (pre-compute popular queries)

### Benchmarks to Track
```python
# Search speed
%timeit db.search_fts("decreto legge")  # Target: <500ms

# Citation fetch
%timeit db.get_citations_incoming('urn:nir:legge:2024:1')  # Target: <200ms

# Graph build (50 nodes)
%timeit CitationGraph(db).build_graph_for_law('urn:...')  # Target: <2s
```

---

## Success Checklist: What "Done" Looks Like

### Week 1 ✓
- [x] SQLite DB created with 162K laws indexed
- [x] FTS working
- [x] Amendment extraction parsing 80%+

### Week 2 ✓
- [x] You can search for any law
- [x] Citation graph renders interactively
- [x] Law detail page shows full info

### Week 3 ✓
- [x] Amendment timeline visualized
- [x] You can see how laws changed over time
- [x] Most-amended laws identified

### Week 4 ✓
- [x] REST API endpoints working
- [x] Export to JSON/CSV working
- [x] Analytics dashboard showing insights

### Post-implementation ✓
- [x] Deploy to HF Space (SQLite syncs via Git LFS)
- [x] Users can search publicly
- [x] Platform is production-ready

---

## Risk: Common Pitfalls to Avoid

### ❌ Don't:
1. **Optimize prematurely**
   - Use SQLite as-is, profile later if needed
   - Don't micro-optimize SQL before it's slow

2. **Scope creep early**
   - Start with search + graph + detail
   - Skip annotations/clustering/AI on first pass

3. **Perfect amendment extraction**
   - 80% is good enough
   - Mark uncertain ones for manual review

4. **Deploy without testing**
   - Always test FTS, citations, API locally first

### ✅ Do:
1. **Test incrementally**
   - Each feature complete before next
   - Daily integration tests

2. **Keep JSONL as backup**
   - SQLite is new hotness but JSONL is reliable
   - Have fallback if DB corrupts

3. **Log everything**
   - Amendment extraction confidence scores
   - API response times
   - User queries (for learning)

4. **Prioritize search early**
   - Without search, nothing else matters
   - Good search enables discovery

---

## Timeline Reality Check

**Optimistic (if no blockers):** 2.5 weeks  
**Realistic (1-2 blockers):** 3-3.5 weeks  
**Conservative (plan for issues):** 4 weeks

**Most likely blocker:** Amendment extraction patterns need manual tweaking

---

**Ready to code? Start: `python -c "from db import LawDatabase; LawDatabase().init_schema()"`**

**Next: Load your JSONL data into the database.**
