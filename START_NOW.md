# START NOW: First 24 Hours

## What You're Building

A production-scale jurisprudential research platform where users can:
- 🔍 Search 162K laws by keywords + filters
- 🔗 Explore citation networks interactively  
- 📅 See amendment history over time
- 💾 Export for external use
- 🔌 Access via REST API

**Timeline:** 4 weeks  
**Complexity:** Medium  
**Prerequisite:** Your vigente JSONL ready

---

## Hour 1: Create Database Layer

### 1. Create `core/db.py`

```python
# Copy full db.py from FULL_PHASE_IMPLEMENTATION.md
# File: OpenNormattiva/core/db.py
```

### 2. Create `core/__init__.py`
```python
# Empty file to make it a package
```

### 3. Test the database
```bash
cd c:\Users\Dell\Documents\VSC Projects\OpenNormattiva

# Activate venv
.\.venv\Scripts\activate

# Test import
python -c "from core.db import LawDatabase; print('✓ Import works')"
```

**Expected output:**
```
✓ Import works
```

**Troubleshooting:**
- If import fails: Ensure `core/` folder exists with `__init__.py`
- If SQLite missing: It's built-in, shouldn't happen

---

## Hour 2: Load Data into SQLite

### 1. Create `scripts/load_db.py`

```python
#!/usr/bin/env python3
"""Load JSONL into SQLite database."""

from pathlib import Path
from core.db import LawDatabase

def main():
    db = LawDatabase(Path('data/laws.db'))
    
    jsonl_file = Path('data/processed/laws_vigente.jsonl')
    
    if not jsonl_file.exists():
        print(f"ERROR: {jsonl_file} not found")
        return 1
    
    print(f"Loading laws from {jsonl_file}...")
    count = db.insert_laws_from_jsonl(jsonl_file)
    
    print(f"\n✓ Loaded {count} laws into database")
    
    # Verify
    total = db.conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
    print(f"✓ Database now contains {total} laws")
    
    return 0

if __name__ == '__main__':
    exit(main())
```

### 2. Run the loader
```bash
python scripts/load_db.py
```

**Expected output:**
```
Loading laws from data/processed/laws_vigente.jsonl...
✓ Loaded 164 laws into database
✓ Database now contains 164 laws
```

(Once you run full pipeline, it will be 162,391 laws)

**Time required:** ~30 seconds for 164 laws, ~5-10 minutes for 162K laws

---

## Hour 3: Test Core Functionality

### 1. Create `scripts/test_features.py`

```python
#!/usr/bin/env python3
"""Quick test of database features."""

from core.db import LawDatabase
from pathlib import Path

db = LawDatabase(Path('data/laws.db'))

print("=" * 60)
print("DATABASE FEATURE TEST")
print("=" * 60)

# Test 1: Count
count = db.conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
print(f"\n✓ Total laws: {count}")

# Test 2: FTS Search
print("\n✓ Testing full-text search...")
results = db.search_fts("protezione")
print(f"  Found {len(results)} results for 'protezione'")
if results:
    print(f"  Example: {results[0]['title'][:60]}")

# Test 3: Get a law
print("\n✓ Testing law retrieval...")
law = db.conn.execute('SELECT * FROM laws LIMIT 1').fetchone()
if law:
    print(f"  Law: {law['title'][:60]}")
    print(f"  URN: {law['urn']}")

# Test 4: Citation index
print("\n✓ Testing citations table...")
cit_count = db.conn.execute('SELECT COUNT(*) FROM citations').fetchone()[0]
print(f"  Total citations indexed: {cit_count}")

print("\n" + "=" * 60)
print("✅ ALL TESTS PASSED")
print("=" * 60)
print("\nDatabase is ready for Streamlit UI!")
```

### 2. Run tests
```bash
python scripts/test_features.py
```

**Expected output:**
```
============================================================
DATABASE FEATURE TEST
============================================================

✓ Total laws: 164
✓ Testing full-text search...
  Found 5 results for 'protezione'
  Example: Codice della protezione civile
✓ Testing law retrieval...
  Law: Codice della protezione civile. (18G00011)
  URN: urn:nir:decreto.legge:2024-01-15;1
✓ Testing citations table...
  Total citations indexed: 45

============================================================
✅ ALL TESTS PASSED
============================================================

Database is ready for Streamlit UI!
```

---

## Hour 4-6: Build Basic Search Page

### 1. Create `ui/pages/search.py`

```python
import streamlit as st
from pathlib import Path
from core.db import LawDatabase

st.title("🔍 Advanced Search")

db = LawDatabase(Path('data/laws.db'))

# Main search
query = st.text_input("Search keywords:")

# Filters
with st.expander("Filters"):
    col1, col2 = st.columns(2)
    with col1:
        year_min = st.number_input("Year from:", 1900, 2026, 2000)
    with col2:
        year_max = st.number_input("Year to:", 1900, 2026, 2026)
    
    law_type = st.selectbox("Type:", ["All"] + [
        "Legge", "Decreto legge", "Decreto legislativo", "Regolamento"
    ])

# Search
if query:
    results = db.search_with_filters(
        query=query if query else "",
        year_min=year_min,
        year_max=year_max,
        law_type=None if law_type == "All" else law_type
    )
    
    st.metric("Results", len(results))
    
    for law in results[:20]:
        with st.expander(f"{law['title'][:60]} ({law['year']})"):
            col1, col2, col3 = st.columns(3)
            col1.metric("Type", law['type'] or "?")
            col2.metric("Articles", law['article_count'] or 0)
            col3.metric("Length", f"{law['text_length']//1000}K chars")
            
            st.write(f"**URN**: `{law['urn']}`")
            st.caption(law['text'][:300] + "...")
```

### 2. Update main `space/app.py` to include search

```python
# Add to page selection:
pages = [
    "🔍 Search",
    "📊 Dashboard",
    # ... existing pages
]

page = st.sidebar.radio("Navigate", pages)

if page == "🔍 Search":
    from ui.pages.search import show_search
    show_search()
```

---

## Hour 6-8: Add Citation Graph

### 1. Install required packages

```bash
pip install pyvis networkx
```

### 2. Create `core/citation_graph.py`

```python
import networkx as nx
from pyvis.network import Network
from pathlib import Path

class CitationGraph:
    def __init__(self, db):
        self.db = db
        self.G = nx.DiGraph()
    
    def build(self, urn: str, depth: int = 1):
        """Build graph around a law."""
        self.G.clear()
        
        law = self.db.get_law(urn)
        if not law:
            return None
        
        # Center
        self.G.add_node(urn, title=law['title'], size=40, color='red')
        
        # Outgoing (laws this law cites)
        for cit in self.db.get_citations_outgoing(urn):
            self.G.add_node(cit['cited_urn'], 
                           title=cit['title'][:30], 
                           size=20, color='blue')
            self.G.add_edge(urn, cit['cited_urn'], 
                           label=f"{cit['count']}x")
        
        # Incoming (laws that cite this one)
        for cit in self.db.get_citations_incoming(urn):
            self.G.add_node(cit['citing_urn'], 
                           title=cit['title'][:30], 
                           size=20, color='green')
            self.G.add_edge(cit['citing_urn'], urn, 
                           label=f"{cit['count']}x")
        
        return self.G
    
    def render_html(self, urn: str) -> str:
        """Render to HTML."""
        self.build(urn)
        net = Network(directed=True, height=600, width=800)
        net.from_nx(self.G)
        
        temp_file = Path('/tmp/graph.html')
        net.show(str(temp_file))
        
        return temp_file.read_text()
```

### 3. Create `ui/pages/citations.py`

```python
import streamlit as st
from pathlib import Path
from core.db import LawDatabase
from core.citation_graph import CitationGraph

st.title("🔗 Citation Network")

db = LawDatabase(Path('data/laws.db'))
urn = st.text_input("Law URN:", "urn:nir:decreto.legge:2024-01-15;1")

if st.button("Visualize"):
    with st.spinner("Building graph..."):
        graph_gen = CitationGraph(db)
        html = graph_gen.render_html(urn)
        
        st.components.v1.html(html, height=700)
        
        # Stats
        st.metric("Laws in network", len(graph_gen.G.nodes()))
        st.metric("Citation links", len(graph_gen.G.edges()))
```

---

## After Hour 8: What You Have

✅ **Working database** with 162K laws indexed for full-text search  
✅ **Search page** with filters (type, year, etc.)  
✅ **Citation graph** visualization  
✅ **Law detail** accessible from search results

**Users can now:**
- Find any law by searching
- See which laws it cites
- See which laws cite it
- Explore networks interactively

---

## Next 24-48 Hours: Continue Building

### Tomorrow (Day 2): Amendment System
- Extract amendments from text
- Build amendment table
- Create timeline visualization

### Day 3: Export & API
- Add export to JSON/CSV
- Build basic FastAPI endpoints
- Test endpoints

### Day 4: Polish
- Performance optimization
- Error handling
- Documentation

---

## Immediate Requirements

**You must have:**
1. ✅ `data/processed/laws_vigente.jsonl` (already exists - 164+ laws)
2. ✅ Python 3.8+ (you have .venv)
3. ✅ Streamlit installed (check: `pip list | grep streamlit`)

**You must install:**
```bash
pip install pyvis networkx fastapi uvicorn
```

---

## File Structure After Hour 8

```
OpenNormattiva/
├── core/
│   ├── __init__.py
│   ├── db.py ........................ ✅ Created
│   └── citation_graph.py ............ ✅ Created
│
├── ui/
│   ├── pages/
│   │   ├── search.py ............... ✅ Created
│   │   └── citations.py ............ ✅ Created
│   └── app.py ....................... (Updated)
│
├── scripts/
│   ├── load_db.py .................. ✅ Created
│   └── test_features.py ............ ✅ Created
│
└── data/
    ├── laws.db ..................... ✅ Created (500MB after full load)
    └── processed/
        └── laws_vigente.jsonl ...... (your source data)
```

---

## Validation: Did It Work?

```bash
# Test 1: Database loads
python scripts/load_db.py
# Should show "✓ Loaded X laws"

# Test 2: Features work
python scripts/test_features.py
# Should show "✅ ALL TESTS PASSED"

# Test 3: Streamlit runs
streamlit run space/app.py
# Should open browser at http://localhost:8501
# Search page should work
# Citation graph should render
```

---

## Troubleshooting

**Q: "module 'core' not found"**  
A: Ensure `core/` folder exists with `__init__.py`

**Q: "laws.db" doesn't grow**  
A: Check that `data/processed/laws_vigente.jsonl` exists and is readable

**Q: "No results in search"**  
A: Make sure laws are loaded first (`python scripts/load_db.py`)

**Q: Graph shows no connections**  
A: Normal with sample data. Once you load full dataset (162K laws), connections will appear

---

## What To Code First (Priority Order)

🔴 **MUST DO (blocks everything):**
1. [ ] `core/db.py` - Database layer
2. [ ] `scripts/load_db.py` - Load your JSONL

🟡 **SHOULD DO (core features):**
3. [ ] `ui/pages/search.py` - Search interface
4. [ ] `core/citation_graph.py` + `ui/pages/citations.py` - Graph

🟢 **NICE TO DO (polish):**
5. [ ] Amendment extraction
6. [ ] Export functionality
7. [ ] API endpoints

---

## Success: First Deployable Version

After completing items 1-4 above, you have a **publicly deployable** research platform where:
- Users can search 162K Italian laws
- See how laws reference each other
- Understand legal relationships
- Export results

**That's Tier 1 MVP. Deploy it. Users love it.**

Then add Tier 2 features based on usage.

---

**Ready? Start with:**
```bash
cd OpenNormattiva
mkdir -p core ui/pages scripts
# Create db.py in core/ (copy from FULL_PHASE...)
python scripts/load_db.py
# Watch the database grow
```

**Questions? Check:**
- `FULL_PHASE_IMPLEMENTATION.md` - Full architecture
- `IMPLEMENTATION_DECISIONS.md` - Why these choices
- `JURISPRUDENCE_PLATFORM_GAPS.md` - What features do what

**You've got this. 4 weeks to production platform.** ⚖️🚀
