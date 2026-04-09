# Full Phase Implementation Plan: Tier 1 + Tier 2

**Timeline:** 3-4 weeks  
**Scope:** Complete jurisprudential platform MVP  
**Target:** Production-ready legal research platform

---

## Architecture Decision: Technology Stack

### Core Schema: SQLite + Python Ecosystem

**Why SQLite (not alternatives):**
- ✅ Zero infrastructure (embedded database)
- ✅ FTS5 (full-text search) built-in
- ✅ Temporal queries (amendment tracking)
- ✅ Deploys anywhere (single .db file)
- ✅ HuggingFace compatible (download as file)
- ❌ Limitations: ~1M+ queries/sec max (fine for single-instance Streamlit)

**Alternative considered: PostgreSQL**
- ✅ More scalable, JSON support, better for distributed
- ❌ Requires server, harder to deploy to HF Space
- **Decision:** SQLite for MVP, migrate if needed

---

## Data Schema: Amendment-Aware Design

```sql
-- Core laws table with FTS
laws (
  urn TEXT PRIMARY KEY,
  title TEXT,
  type TEXT,
  date TEXT,
  year INTEGER,
  text TEXT,
  text_length INTEGER,
  article_count INTEGER,
  status TEXT,  -- 'in_force', 'repealed', 'suspended'
  source_collection TEXT,
  parsed_at TEXT
)

-- FTS index for full-text search
laws_fts (
  urn, title, type, text  -- indexed fields
)

-- Citations: which laws cite which
citations (
  citing_urn TEXT,      -- law that makes the reference
  cited_urn TEXT,       -- law being referenced
  count INTEGER,        -- how many times in document
  context TEXT,         -- snippet where citation appears
  PRIMARY KEY (citing_urn, cited_urn)
)

-- Amendments: temporal tracking
amendments (
  urn TEXT,
  amending_urn TEXT,    -- law that modifies this one
  action TEXT,          -- 'amended', 'repealed', 'suspended'
  date_effective TEXT,
  article_modified TEXT,
  change_description TEXT
)

-- Metadata enrichment
law_metadata (
  urn TEXT PRIMARY KEY,
  authority TEXT,          -- Parliament, Minister, etc.
  keywords TEXT,            -- comma-separated or JSON array
  abstract TEXT,
  implementing_regulations TEXT,  -- JSON array of URNs
  implemented_by TEXT,      -- Parent URN if delegated
  amendment_count INTEGER,
  citation_count_incoming INTEGER,
  citation_count_outgoing INTEGER,
  last_modified TEXT
)
```

---

## Implementation Sequence

### Week 1: Data Foundation

#### 1.1 Build SQLite Database Layer (Day 1)

Create `db.py`:
```python
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import json

class LawDatabase:
    def __init__(self, db_path: Path = Path('data/laws.db')):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.init_schema()
    
    def init_schema(self):
        """Create tables and indexes."""
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS laws (
                urn TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                type TEXT,
                date TEXT,
                year INTEGER,
                text TEXT,
                text_length INTEGER,
                article_count INTEGER,
                status TEXT DEFAULT 'in_force',
                source_collection TEXT,
                parsed_at TEXT
            )
        ''')
        
        # FTS5 table for full-text search
        self.conn.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS laws_fts 
            USING fts5(urn UNINDEXED, title, type, text)
        ''')
        
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS citations (
                citing_urn TEXT,
                cited_urn TEXT,
                count INTEGER DEFAULT 1,
                context TEXT,
                PRIMARY KEY (citing_urn, cited_urn),
                FOREIGN KEY (citing_urn) REFERENCES laws(urn),
                FOREIGN KEY (cited_urn) REFERENCES laws(urn)
            )
        ''')
        
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS amendments (
                urn TEXT,
                amending_urn TEXT,
                action TEXT,  -- 'amended', 'repealed', 'suspended'
                date_effective TEXT,
                article_modified TEXT,
                change_description TEXT,
                PRIMARY KEY (urn, amending_urn, action),
                FOREIGN KEY (urn) REFERENCES laws(urn),
                FOREIGN KEY (amending_urn) REFERENCES laws(urn)
            )
        ''')
        
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS law_metadata (
                urn TEXT PRIMARY KEY,
                authority TEXT,
                keywords TEXT,
                abstract TEXT,
                implementing_regulations TEXT,
                implemented_by TEXT,
                amendment_count INTEGER DEFAULT 0,
                citation_count_incoming INTEGER DEFAULT 0,
                citation_count_outgoing INTEGER DEFAULT 0,
                last_modified TEXT
            )
        ''')
        
        self.conn.commit()
    
    def insert_laws_from_jsonl(self, jsonl_file: Path):
        """Bulk insert from JSONL (from pipeline output)."""
        import json
        inserted = 0
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                law = json.loads(line)
                self.insert_law(law)
                inserted += 1
        self.conn.commit()
        return inserted
    
    def insert_law(self, law: Dict):
        """Insert or update single law."""
        self.conn.execute('''
            INSERT OR REPLACE INTO laws 
            (urn, title, type, date, year, text, text_length, article_count, parsed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            law.get('urn'),
            law.get('title'),
            law.get('type'),
            law.get('date'),
            law.get('year'),
            law.get('text'),
            law.get('text_length', 0),
            law.get('article_count', 0),
            datetime.now().isoformat()
        ))
        
        # Index for FTS
        self.conn.execute('''
            INSERT OR REPLACE INTO laws_fts (urn, title, type, text)
            VALUES (?, ?, ?, ?)
        ''', (
            law.get('urn'),
            law.get('title'),
            law.get('type'),
            law.get('text', '')[:2000]  # Truncate for FTS
        ))
        
        # Insert citations
        for citation_urn in law.get('citations', []):
            self.conn.execute('''
                INSERT OR IGNORE INTO citations (citing_urn, cited_urn, count)
                VALUES (?, ?, 1)
            ''', (law.get('urn'), citation_urn))
        
        self.conn.commit()
    
    def search_fts(self, query: str, limit: int = 50) -> List[Dict]:
        """Full-text search."""
        # BM25 ranking in FTS5
        results = self.conn.execute('''
            SELECT urn, title, type, year, rank
            FROM laws_fts
            WHERE laws_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        ''', (query, limit)).fetchall()
        return [dict(r) for r in results]
    
    def get_law(self, urn: str) -> Optional[Dict]:
        """Get full law details."""
        law = self.conn.execute(
            'SELECT * FROM laws WHERE urn = ?', (urn,)
        ).fetchone()
        if not law:
            return None
        law_dict = dict(law)
        
        # Get metadata
        meta = self.conn.execute(
            'SELECT * FROM law_metadata WHERE urn = ?', (urn,)
        ).fetchone()
        if meta:
            law_dict['metadata'] = dict(meta)
        
        return law_dict
    
    def get_citations_outgoing(self, urn: str) -> List[Dict]:
        """Laws that this law cites."""
        results = self.conn.execute('''
            SELECT c.cited_urn, l.title, l.year, c.count
            FROM citations c
            JOIN laws l ON c.cited_urn = l.urn
            WHERE c.citing_urn = ?
            ORDER BY c.count DESC
        ''', (urn,)).fetchall()
        return [dict(r) for r in results]
    
    def get_citations_incoming(self, urn: str) -> List[Dict]:
        """Laws that cite this law."""
        results = self.conn.execute('''
            SELECT c.citing_urn, l.title, l.year, c.count
            FROM citations c
            JOIN laws l ON c.citing_urn = l.urn
            WHERE c.cited_urn = ?
            ORDER BY c.count DESC
        ''', (urn,)).fetchall()
        return [dict(r) for r in results]
    
    def search_with_filters(self, 
                          query: str = '',
                          law_type: str = '',
                          year_min: int = 0,
                          year_max: int = 9999,
                          article_min: int = 0,
                          article_max: int = 9999,
                          cite_count_min: int = 0,
                          limit: int = 50) -> List[Dict]:
        """Advanced search with filters."""
        sql = '''
            SELECT l.*, 
                   COUNT(c.citing_urn) as citation_count
            FROM laws l
            LEFT JOIN citations c ON c.cited_urn = l.urn
            WHERE 1 = 1
        '''
        params = []
        
        if query:
            sql += ' AND l.urn IN (SELECT urn FROM laws_fts WHERE laws_fts MATCH ?)'
            params.append(query)
        
        if law_type:
            sql += ' AND l.type = ?'
            params.append(law_type)
        
        if year_min or year_max:
            sql += ' AND l.year BETWEEN ? AND ?'
            params.extend([year_min, year_max])
        
        if article_min or article_max:
            sql += ' AND l.article_count BETWEEN ? AND ?'
            params.extend([article_min, article_max])
        
        sql += ' GROUP BY l.urn'
        
        if cite_count_min:
            sql += ' HAVING COUNT(c.citing_urn) >= ?'
            params.append(cite_count_min)
        
        sql += ' ORDER BY citation_count DESC LIMIT ?'
        params.append(limit)
        
        results = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in results]
```

**Time:** 1 day  
**Testing:** Load 164 sample laws, verify search works

---

#### 1.2 Amendment Extraction Engine (Day 2-3)

Create `amendment_parser.py`:
```python
import re
from typing import List, Dict, Tuple

class AmendmentExtractor:
    """Extract amendment relationships from law text."""
    
    AMENDMENT_PATTERNS = {
        # "Article 5 is amended by law 123/2024"
        'amended': r'(?:l\')?articolo\s+(\d+).*?(?:è\s+)?modificat[ao]\s+(?:da|dalla)\s+([a-z\s\.]+?)(\d+/\d{2,4})',
        
        # "The following articles are repealed"
        'repealed': r'(?:l\')?articolo\s+(\d+).*?è\s+abrogat[ao]\s+(?:da|dalla)\s+([a-z\s\.]+?)(\d+/\d{2,4})',
        
        # "This law repeals law 456/2020"
        'repeals': r'(?:abrog|revoca).*?(?:la|il)?\s+([a-z\s\.]+?)(\d+/\d{2,4})',
        
        # "In effect as of 2024-01-01"
        'effective_date': r'(?:entr|vigen)a\s+in\s+vigore.*?([0-9]{4}-[0-9]{2}-[0-9]{2})',
    }
    
    def extract_amendments(self, law_text: str, law_urn: str) -> List[Dict]:
        """Extract all amendment references."""
        amendments = []
        
        # Find laws being amended
        for article_num, amended_by, amended_law_ref in re.finditer(
            self.AMENDMENT_PATTERNS['amended'], law_text, re.IGNORECASE
        ):
            amendments.append({
                'article': article_num,
                'action': 'amended_by',
                'ref_law': amended_law_ref,
                'type': amended_by.strip()
            })
        
        return amendments
    
    def normalize_law_reference(self, ref: str) -> Optional[str]:
        """Convert 'legge 123/2024' → 'urn:nir:legge:2024:123'."""
        # Simplified - in reality need full normalization logic
        match = re.search(r'(\d+)/(\d{2,4})', ref)
        if match:
            number, year = match.groups()
            year = '20' + year if len(year) == 2 else year
            return f"urn:nir:legge:{year}:{number}"
        return None
```

**Time:** 2 days  
**Testing:** Extract amendments from sample laws

---

### Week 2: Core Features (Tier 1)

#### 2.1 Advanced Search Page (Day 1)

Update `space/app.py`:
```python
def page_search_advanced():
    st.header("🔍 Advanced Search")
    
    db = get_database()  # Cache connection
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        query = st.text_input("Keywords (title, URN, or full text):", placeholder="protezione civile")
    
    with col2:
        search_mode = st.radio("Search mode:", ["Any word", "All words", "Phrase"])
    
    # Filters
    with st.expander("📋 Filters", expanded=False):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            law_type = st.selectbox("Type:", ["Any"] + list(get_law_types(db)))
        
        with col2:
            year_range = st.slider("Year:", 1970, 2026, (2000, 2026))
        
        with col3:
            article_range = st.slider("Articles:", 0, 500, (0, 500))
        
        col1, col2 = st.columns(2)
        with col1:
            min_citations = st.number_input("Min citations:", 0, 100, 0)
        with col2:
            status = st.selectbox("Status:", ["Any", "In force", "Repealed"])
    
    # Execute search
    if query or st.button("Search"):
        with st.spinner("Searching..."):
            results = db.search_with_filters(
                query=query if search_mode == "Any" else f'"{query}"',
                law_type=None if law_type == "Any" else law_type,
                year_min=year_range[0],
                year_max=year_range[1],
                article_min=article_range[0],
                article_max=article_range[1],
                cite_count_min=min_citations
            )
        
        st.write(f"**Found {len(results)} results**")
        
        for law in results[:50]:
            with st.expander(f"📜 {law['title'][:60]} ({law['year']})"):
                col1, col2, col3 = st.columns(3)
                col1.metric("Type", law['type'])
                col2.metric("Articles", law['article_count'])
                col3.metric("Citations", law.get('citation_count', 0))
                
                st.write(f"**URN**: `{law['urn']}`")
                st.caption(law['text'][:300] + "...")
                
                if st.button("View full law", key=law['urn']):
                    st.session_state.selected_law_urn = law['urn']
                    st.rerun()

@st.cache_resource
def get_database():
    from db import LawDatabase
    return LawDatabase(Path('data/laws.db'))

@st.cache_resource
def get_law_types(db):
    return db.conn.execute(
        'SELECT DISTINCT type FROM laws ORDER BY type'
    ).fetchall()
```

**Time:** 1 day
**Testing:** Run searches with various filters

---

#### 2.2 Citation Graph Visualization (Day 2-3)

Create `citation_graph.py`:
```python
import networkx as nx
from pyvis.network import Network
import streamlit as st
from pathlib import Path

class CitationGraph:
    def __init__(self, db):
        self.db = db
        self.G = nx.DiGraph()
    
    def build_graph_for_law(self, urn: str, depth: int = 2) -> nx.DiGraph:
        """Build citation subgraph around a law."""
        self.G.clear()
        
        # Add center law
        law = self.db.get_law(urn)
        self.G.add_node(urn, title=law['title'], type='center')
        
        # Depth 1: Direct citations
        outgoing = self.db.get_citations_outgoing(urn)
        incoming = self.db.get_citations_incoming(urn)
        
        for cit in outgoing:
            self.G.add_node(cit['cited_urn'], 
                           title=cit['title'], 
                           type='cited_by_center')
            self.G.add_edge(urn, cit['cited_urn'], 
                           weight=cit['count'], 
                           label=f"{cit['count']}x")
        
        for cit in incoming:
            self.G.add_node(cit['citing_urn'], 
                           title=cit['title'], 
                           type='citing_center')
            self.G.add_edge(cit['citing_urn'], urn, 
                           weight=cit['count'], 
                           label=f"{cit['count']}x")
        
        # Depth 2: Citations of citations (if depth > 1)
        if depth > 1:
            for node in list(self.G.nodes()):
                if node != urn:
                    related = self.db.get_citations_outgoing(node)
                    for cit in related[:5]:  # Limit to prevent explosion
                        self.G.add_node(cit['cited_urn'], 
                                       title=cit['title'],
                                       type='depth2')
                        self.G.add_edge(node, cit['cited_urn'],
                                       weight=cit['count'])
        
        return self.G
    
    def render_interactive(self, urn: str, depth: int = 2) -> str:
        """Generate Pyvis HTML."""
        self.build_graph_for_law(urn, depth)
        
        net = Network(directed=True, height=600, width=800)
        net.from_nx(self.G)
        
        # Styling
        for node in net.nodes:
            node_type = self.G.nodes[node['id']].get('type', 'default')
            if node_type == 'center':
                node['color'] = '#FF6B6B'
                node['size'] = 40
            elif node_type == 'cited_by_center':
                node['color'] = '#4ECDC4'
                node['size'] = 25
            elif node_type == 'citing_center':
                node['color'] = '#95E1D3'
                node['size'] = 25
            else:
                node['color'] = '#D3D3D3'
                node['size'] = 15
            
            node['font'] = {'size': 12}
        
        net.show(str(Path('/tmp/citation_graph.html')))
        with open('/tmp/citation_graph.html') as f:
            return f.read()

def page_citations_interactive():
    st.header("🔗 Citation Network")
    
    db = get_database()
    
    urn = st.text_input("Enter law URN:", "urn:nir:decreto.legge:2024:1")
    depth = st.slider("Citation depth:", 1, 3, 2)
    
    if st.button("Visualize"):
        with st.spinner("Building graph..."):
            graph_gen = CitationGraph(db)
            html = graph_gen.render_interactive(urn, depth)
            
            st.components.v1.html(html, height=700)
            
            # Statistics
            G = graph_gen.G
            st.metric("Laws in network", len(G.nodes()))
            st.metric("Citation links", len(G.edges()))
            
            # Find most referenced
            in_degree = dict(G.in_degree())
            most_cited = sorted(in_degree.items(), key=lambda x: x[1], reverse=True)[:5]
            
            st.subheader("Most cited in network")
            for cited_urn, count in most_cited:
                law = db.get_law(cited_urn)
                st.write(f"- {law['title']}: **{count}** citations")
```

**Time:** 2-3 days (Pyvis integration, styling)  
**Output:** Interactive citation graph viewer

---

#### 2.3 Law Detail Page (Day 4)

```python
def page_law_detail():
    st.header("📜 Law Details")
    
    db = get_database()
    
    urn = st.text_input("Enter URN:", value=st.session_state.get('selected_law_urn', ''))
    
    if not urn:
        st.info("Enter a law URN to view details")
        return
    
    law = db.get_law(urn)
    if not law:
        st.error("Law not found")
        return
    
    # Header
    col1, col2, col3 = st.columns(3)
    col1.metric("Year", law['year'])
    col2.metric("Type", law['type'])
    col3.metric("Articles", law['article_count'])
    
    # Full text
    st.subheader("📋 Full Text")
    st.text_area("", law['text'], height=300, disabled=True)
    
    # Citations This Law Makes
    st.subheader("🔗 Citations (Laws this law references)")
    outgoing = db.get_citations_outgoing(urn)
    if outgoing:
        cit_df = pd.DataFrame([
            {
                'Law': f"{cit['title']} ({cit['year']})",
                'URN': cit['cited_urn'],
                'Times': cit['count']
            }
            for cit in outgoing
        ])
        st.dataframe(cit_df, use_container_width=True)
    else:
        st.info("No citations made by this law")
    
    # Citations To This Law
    st.subheader("👈 Referenced By (Laws that cite this one)")
    incoming = db.get_citations_incoming(urn)
    if incoming:
        cit_df = pd.DataFrame([
            {
                'Law': f"{cit['title']} ({cit['year']})",
                'URN': cit['citing_urn'],
                'Times': cit['count']
            }
            for cit in incoming
        ])
        st.dataframe(cit_df, use_container_width=True)
    else:
        st.info("No laws cite this one")
    
    # Amendments
    st.subheader("⚖️ Amendments")
    amendments = db.get_amendments_for_law(urn)
    if amendments:
        for amend in amendments:
            st.warning(f"""
            **{amend['action'].upper()}** by {amend['amending_urn']}
            
            Effective: {amend['date_effective']}
            
            {amend['change_description']}
            """)
    else:
        st.success("✅ No amendments. This law is in its original form.")
```

**Time:** 1 day

---

### Week 3: Amendment & Analytics (Tier 1-2)

#### 3.1 Amendment Tracking System (Day 1-2)

Create `amendments.py`:
```python
import re
from typing import List, Dict

class AmendmentTracker:
    def __init__(self, db):
        self.db = db
        self.patterns = {
            'modifica': r'(?:modificato?|integrato?|integramante?|completamante?|sostituito?)\s+(?:da|dall[a\']|del)\s+(.+?)$',
            'abrogato': r'(?:abrogato?|revocato?)\s+(?:da|dall[a\']|del)\s+(.+?)$',
            'sostituire': r'(?:sostituisce?|sostituir[e])\s+(.+?)$',
        }
    
    def extract_amendments_from_text(self, law_urn: str, text: str) -> List[Dict]:
        """Extract amendment info from law text."""
        amendments = []
        
        # Find articles modified
        article_pattern = r'(?:articolo|art\.?)\s+(\d+)'
        
        for match in re.finditer(article_pattern, text, re.IGNORECASE):
            article_num = match.group(1)
            
            # Check if followed by modification verb
            start = match.start()
            segment = text[start:min(start+500, len(text))]
            
            for action_key, pattern in self.patterns.items():
                if re.search(pattern, segment, re.IGNORECASE):
                    amendments.append({
                        'article': article_num,
                        'action': action_key,
                        'context': segment[:200]
                    })
        
        return amendments
    
    def store_amendments(self, law_urn: str, amendments: List[Dict]):
        """Persist amendment records."""
        for amend in amendments:
            self.db.conn.execute('''
                INSERT INTO amendments 
                (urn, article_modified, action, change_description)
                VALUES (?, ?, ?, ?)
            ''', (
                law_urn,
                amend.get('article'),
                amend.get('action'),
                amend.get('context', '')
            ))
        self.db.conn.commit()
    
    def get_amendment_timeline(self, urn: str) -> List[Dict]:
        """Get all amendments to a law over time."""
        return self.db.conn.execute('''
            SELECT * FROM amendments
            WHERE urn = ?
            ORDER BY date_effective DESC
        ''', (urn,)).fetchall()
```

**Time:** 2 days

---

#### 3.2 Amendment Timeline Page (Day 3)

```python
def page_amendments_timeline():
    st.header("📅 Amendment Timeline")
    
    db = get_database()
    
    # Get all amendments
    amendments = db.conn.execute('''
        SELECT DISTINCT urn, COUNT(*) as count
        FROM amendments
        GROUP BY urn
        ORDER BY count DESC
        LIMIT 100
    ''').fetchall()
    
    # Most amended laws
    st.subheader("Most Frequently Amended Laws")
    amend_df = pd.DataFrame([
        {
            'Law': db.get_law(a['urn'])['title'],
            'Amendments': a['count']
        }
        for a in amendments
    ])
    
    st.bar_chart(amend_df.set_index('Law'))
    
    # Timeline visualization
    st.subheader("Amendment Timeline by Year")
    
    timeline_data = db.conn.execute('''
        SELECT CAST(SUBSTR(date_effective, 1, 4) AS INTEGER) as year,
               COUNT(*) as amendment_count
        FROM amendments
        WHERE date_effective IS NOT NULL
        GROUP BY year
        ORDER BY year
    ''').fetchall()
    
    timeline_df = pd.DataFrame(timeline_data)
    st.line_chart(timeline_df.set_index('year'))
```

**Time:** 1 day

---

### Week 4: API & Export (Tier 2)

#### 4.1 FastAPI Backend (Day 1-2)

Create `api.py`:
```python
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from typing import List, Optional
import json
from pathlib import Path

from db import LawDatabase

app = FastAPI(title="Normattiva API", version="1.0")
db = LawDatabase()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/laws/search")
def search_laws(
    q: str = Query(..., min_length=2),
    type: Optional[str] = None,
    year_min: int = Query(0),
    year_max: int = Query(9999),
    citations_min: int = Query(0),
    limit: int = Query(50, le=100),
):
    """Full-text search with filters."""
    results = db.search_with_filters(
        query=q,
        law_type=type,
        year_min=year_min,
        year_max=year_max,
        cite_count_min=citations_min,
        limit=limit
    )
    return {
        "count": len(results),
        "results": results
    }

@app.get("/laws/{urn}")
def get_law(urn: str):
    """Get full law details."""
    law = db.get_law(urn)
    if not law:
        raise HTTPException(status_code=404, detail="Law not found")
    return law

@app.get("/laws/{urn}/citations/incoming")
def citations_incoming(urn: str, limit: int = 50):
    """Laws that cite this one."""
    law = db.get_law(urn)
    if not law:
        raise HTTPException(status_code=404)
    
    results = db.get_citations_incoming(urn)
    return {"urn": urn, "count": len(results), "citations": results[:limit]}

@app.get("/laws/{urn}/citations/outgoing")
def citations_outgoing(urn: str, limit: int = 50):
    """Laws that this one cites."""
    law = db.get_law(urn)
    if not law:
        raise HTTPException(status_code=404)
    
    results = db.get_citations_outgoing(urn)
    return {"urn": urn, "count": len(results), "citations": results[:limit]}

@app.get("/laws/{urn}/amendments")
def get_amendments(urn: str):
    """Amendment history of this law."""
    law = db.get_law(urn)
    if not law:
        raise HTTPException(status_code=404)
    
    amendments = db.conn.execute('''
        SELECT * FROM amendments WHERE urn = ? ORDER BY date_effective DESC
    ''', (urn,)).fetchall()
    
    return {"urn": urn, "amendments": [dict(a) for a in amendments]}

@app.post("/laws/export")
def export_laws(
    urns: List[str],
    format: str = Query("json", regex="^(json|csv)$")
):
    """Export multiple laws."""
    laws = [db.get_law(urn) for urn in urns if db.get_law(urn)]
    
    if format == "json":
        return JSONResponse({"laws": laws})
    elif format == "csv":
        import csv
        import io
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=['urn', 'title', 'type', 'year', 'articles'])
        writer.writeheader()
        for law in laws:
            writer.writerow({
                'urn': law['urn'],
                'title': law['title'],
                'type': law['type'],
                'year': law['year'],
                'articles': law['article_count']
            })
        return {
            "status": "success",
            "csv": output.getvalue()
        }

@app.get("/stats")
def statistics():
    """Dataset statistics."""
    stats = db.conn.execute('''
        SELECT 
            COUNT(*) as total_laws,
            COUNT(DISTINCT type) as law_types,
            MIN(year) as earliest_year,
            MAX(year) as latest_year
        FROM laws
    ''').fetchone()
    
    return dict(stats)
```

**Time:** 1-2 days  
**Deploy:** Can run alongside Streamlit or separately

---

#### 4.2 Export Functionality in Streamlit (Day 3)

```python
def page_export():
    st.header("💾 Export")
    
    db = get_database()
    
    st.info("Export laws from search results or by selection")
    
    # Option 1: Export from search
    with st.expander("📋 From Query"):
        query = st.text_input("Search query:")
        if query and st.button("Search & export"):
            results = db.search_with_filters(query=query)
            st.success(f"Found {len(results)} laws")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Download as JSON"):
                    json_str = json.dumps(results, ensure_ascii=False, indent=2)
                    st.download_button(
                        label="laws.json",
                        data=json_str,
                        file_name="laws.json",
                        mime="application/json"
                    )
            
            with col2:
                if st.button("Download as CSV"):
                    df = pd.DataFrame([
                        {
                            'URN': l['urn'],
                            'Title': l['title'],
                            'Type': l['type'],
                            'Year': l['year'],
                            'Articles': l['article_count']
                        }
                        for l in results
                    ])
                    csv_str = df.to_csv(index=False)
                    st.download_button(
                        label="laws.csv",
                        data=csv_str,
                        file_name="laws.csv",
                        mime="text/csv"
                    )
```

**Time:** 1 day

---

## Architecture Diagram

```
Data Layer (SQLite)
├── laws (FTS5 indexed)
├── citations (graph)
├── amendments (temporal)
└── law_metadata (enrichment)
    ↓
Business Logic (Python)
├── db.py (SQL queries)
├── amendment_parser.py (extraction)
├── citation_graph.py (network analysis)
└── api.py (REST endpoints)
    ↓
Frontend (Multi-interface)
├── Streamlit (UI)
│   ├── Search
│   ├── Citation Graph
│   ├── Amendment Timeline
│   ├── Export
│   └── Analytics
├── FastAPI (REST API)
└── HF Space (deployment)
```

---

## Implementation Checklist

### Week 1
- [x] SQLite database with FTS5
- [x] Schema migration from JSONL
- [ ] Amendment extraction engine

### Week 2
- [ ] Advanced search page (Streamlit)
- [ ] Citation graph visualization (Pyvis)
- [ ] Law detail page with related laws

### Week 3
- [ ] Amendment tracking system
- [ ] Amendment timeline visualization
- [ ] Metadata enrichment

### Week 4
- [ ] FastAPI backend with endpoints
- [ ] Export functionality (JSON/CSV)
- [ ] Analytics dashboard
- [ ] Deploy API

---

## File Structure After Implementation

```
OpenNormattiva/
├── db.py ............................ NEW - Database layer
├── amendment_parser.py .............. NEW - Amendment extraction
├── citation_graph.py ................ NEW - Graph visualization
├── amendments.py .................... NEW - Amendment tracking
├── api.py ........................... NEW - REST API
├── space/
│   ├── app.py (UPDATED)
│   ├── pages/
│   │   ├── search.py ............... NEW
│   │   ├── citations.py ............ NEW
│   │   ├── amendments.py ........... NEW
│   │   ├── export.py ............... NEW
│   │   └── analytics.py ............ NEW
├── data/
│   ├── laws.db ..................... NEW - SQLite database
│   └── processed/
│       └── laws_vigente.jsonl ...... Source data
└── requirements.txt (UPDATED)
    └── Added: pyvis, networkx, fastapi, uvicorn
```

---

## Dependencies to Add

```txt
# Existing
streamlit>=1.28.0
pandas>=2.0.0
plotly>=5.0.0
lxml>=4.9.0
huggingface-hub>=0.16.0

# NEW - Database & Search
sqlite3  # Built-in
networkx>=3.2

# NEW - Graph Visualization
pyvis>=0.3.0

# NEW - API
fastapi>=0.104.0
uvicorn>=0.24.0

# NEW - Data Processing
python-multipart>=0.0.6
```

---

## Deployment Strategy

### Option A: Streamlit Only (Simplest)
- SQLite served from HF Space
- No separate API
- All features in Streamlit
- Deploy: One-click to HF Space

### Option B: Streamlit + FastAPI
- SQLite shared database
- FastAPI running on separate port
- Streamlit frontend
- API for external access
- Deploy: Docker container or Railway

### Option C: FastAPI Primary
- FastAPI as main backend
- React/Vue frontend (future)
- Streamlit for exploration/admin
- Deploy: Cloud Run, Railway, or self-hosted

**Recommendation:** Start with Option A (Streamlit only), migrate to B as users grow

---

## Success Metrics

After implementation, you should have:

✅ **Search:** Find any law by title/URN/keywords + filters  
✅ **Citations:** Interactive graph showing law relationships  
✅ **Amendments:** See all changes + timeline  
✅ **Export:** Share results as JSON/CSV  
✅ **API:** Programmatic access for integrations  
✅ **Analytics:** Understand corpus patterns  

**All in:** 3-4 weeks, ~700 lines of new code

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| SQLite too slow | Use indexes, limit query results, migrate to PostgreSQL if needed |
| Amendment extraction inaccurate | Validate on sample laws, manual curation possible |
| Memory issues with large graphs | Limit graph depth, pagination |
| HF Space deployment issues | Use Git LFS for database, or sync on startup |
| API rate limiting | Cache responses, add rate limiting |

---

**Ready to proceed? Start with Week 1, Day 1: Build the SQLite database layer.**
