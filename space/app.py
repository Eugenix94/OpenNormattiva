#!/usr/bin/env python3
"""
Normattiva Research Platform - Full Jurisprudence Visualization

Multi-page Streamlit app for exploring Italian law:
- Browse laws by type/year
- Search by keyword/URN
- Citation network analysis
- Amendment tracking
- Temporal evolution

Runs on HF Spaces, synced from GitHub.
"""

import streamlit as st
import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
import plotly.express as px
import plotly.graph_objects as go
from collections import Counter
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# PAGE CONFIG
st.set_page_config(
    page_title="Normattiva Research",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("⚖️ Normattiva Research Platform")
st.markdown("Explore Italian jurisprudence: laws, amendments, citations, and legal evolution")

# DATA LOADING (CACHED)

@st.cache_resource
def load_laws():
    """Load all laws from JSONL."""
    try:
        jsonl_files = list(Path('data/processed').glob('laws_*.jsonl'))
        if not jsonl_files:
            st.warning("No data files found. Run pipeline.py first.")
            return []
        
        laws = []
        for jsonl_file in jsonl_files:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        laws.append(json.loads(line))
        
        st.success(f"✓ Loaded {len(laws)} laws")
        return laws
    
    except Exception as e:
        st.error(f"Error loading laws: {e}")
        return []

@st.cache_resource
def load_citations():
    """Load citation index."""
    try:
        index_files = list(Path('data/indexes').glob('*_citations.json'))
        if not index_files:
            return {}
        
        with open(index_files[0], 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error loading citations: {e}")
        return {}

@st.cache_resource
def load_metrics():
    """Load metrics."""
    try:
        metric_files = list(Path('data/indexes').glob('*_metrics.json'))
        if not metric_files:
            return {}
        
        with open(metric_files[0], 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error loading metrics: {e}")
        return {}

# HELPER FUNCTIONS

def build_laws_index(laws: List[Dict]) -> Dict[str, Dict]:
    """Build lookup index for fast searching."""
    index = {}
    for law in laws:
        urn = law.get('urn')
        if urn:
            index[urn] = law
    return index

def search_laws(laws: List[Dict], query: str) -> List[Dict]:
    """Search laws by keyword/URN."""
    query_lower = query.lower()
    results = []
    
    for law in laws:
        if query_lower in law.get('urn', '').lower() or \
           query_lower in law.get('title', '').lower() or \
           query_lower in law.get('text', '').lower()[:500]:
            results.append(law)
    
    return results[:100]

def get_law_type_distribution(laws: List[Dict]) -> Dict[str, int]:
    """Count laws by type."""
    types = Counter()
    for law in laws:
        law_type = law.get('type', 'unknown')
        types[law_type] += 1
    return dict(types)

def get_law_year_distribution(laws: List[Dict]) -> Dict[str, int]:
    """Count laws by year."""
    years = Counter()
    for law in laws:
        year = law.get('year')
        if year:
            years[str(year)] += 1
    return dict(sorted(years.items()))

# PAGES

def page_dashboard():
    """Dashboard with dataset overview."""
    st.header("📊 Dashboard")
    
    laws = load_laws()
    metrics = load_metrics()
    
    if not laws:
        st.info("No data loaded yet.")
        return
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Laws", len(laws))
    
    with col2:
        st.metric("Document Types", len(get_law_type_distribution(laws)))
    
    with col3:
        st.metric("Year Span", f"{min(laws, key=lambda x: x.get('year', '9999')).get('year', 'N/A')}-{max(laws, key=lambda x: x.get('year', '0')).get('year', 'N/A')}")
    
    with col4:
        total_articles = sum(law.get('article_count', 0) for law in laws)
        st.metric("Total Articles", total_articles)
    
    # Type distribution
    col1, col2 = st.columns(2)
    
    with col1:
        type_dist = get_law_type_distribution(laws)
        fig = px.bar(
            x=list(type_dist.keys()),
            y=list(type_dist.values()),
            title="Laws by Type",
            labels={'x': 'Type', 'y': 'Count'}
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        year_dist = get_law_year_distribution(laws)
        fig = px.line(
            x=list(year_dist.keys()),
            y=list(year_dist.values()),
            title="Laws by Year",
            labels={'x': 'Year', 'y': 'Count'},
            markers=True
        )
        st.plotly_chart(fig, use_container_width=True)


def page_browse():
    """Browse laws by filters."""
    st.header("📋 Browse Laws")
    
    laws = load_laws()
    
    if not laws:
        st.info("No data loaded.")
        return
    
    # Filters
    col1, col2, col3 = st.columns(3)
    
    with col1:
        selected_type = st.selectbox(
            "Filter by Type",
            ['All'] + list(set(law.get('type', 'unknown') for law in laws))
        )
    
    with col2:
        years = sorted(set(law.get('year') for law in laws if law.get('year')))
        selected_year = st.selectbox(
            "Filter by Year",
            ['All'] + years
        )
    
    with col3:
        sort_by = st.radio("Sort by", ['Year ↓', 'Year ↑', 'Title'])
    
    # Filter laws
    filtered = laws
    if selected_type != 'All':
        filtered = [l for l in filtered if l.get('type') == selected_type]
    if selected_year != 'All':
        filtered = [l for l in filtered if l.get('year') == selected_year]
    
    # Sort
    if sort_by == 'Year ↓':
        filtered = sorted(filtered, key=lambda x: x.get('year', '0'), reverse=True)
    elif sort_by == 'Year ↑':
        filtered = sorted(filtered, key=lambda x: x.get('year', '0'))
    else:
        filtered = sorted(filtered, key=lambda x: x.get('title', ''))
    
    st.write(f"**Showing {len(filtered)} of {len(laws)} laws**")
    
    # Paginated display
    page_size = 20
    page_num = st.number_input("Page", 1, max(1, (len(filtered) // page_size) + 1))
    start_idx = (page_num - 1) * page_size
    end_idx = start_idx + page_size
    
    for law in filtered[start_idx:end_idx]:
        with st.expander(f"{law.get('title', 'Untitled')} ({law.get('year', '?')})"):
            col1, col2 = st.columns([1, 2])
            
            with col1:
                st.write(f"**URN**: `{law.get('urn', 'N/A')}`")
                st.write(f"**Type**: {law.get('type', 'N/A')}")
                st.write(f"**Date**: {law.get('date', 'N/A')}")
                st.write(f"**Articles**: {law.get('article_count', 0)}")
            
            with col2:
                st.text_area("Preview", law.get('text', '')[:1000], height=200, disabled=True)


def page_search():
    """Search laws."""
    st.header("🔍 Search")
    
    laws = load_laws()
    
    query = st.text_input("Search by URN, title, or content:")
    
    if query and len(query) > 2:
        results = search_laws(laws, query)
        st.write(f"**Found {len(results)} results**")
        
        for law in results[:20]:
            with st.expander(f"{law.get('title', 'Untitled')} ({law.get('year', '?')})"):
                st.write(f"**URN**: `{law.get('urn')}`")
                st.write(f"**Type**: {law.get('type')}")
                if law.get('citations'):
                    st.write(f"**Citations**: {len(law.get('citations', []))} laws referenced")
                st.text_area("Text", law.get('text', ''), height=200, disabled=True)


def page_citations():
    """Analyze citation networks."""
    st.header("🔗 Citation Network")
    
    laws = load_laws()
    citations_idx = load_citations()
    
    if not citations_idx or not laws:
        st.info("No citation data available.")
        return
    
    citations = citations_idx.get('citations', {})
    
    # Most cited laws
    st.subheader("Most Referenced Laws")
    
    top_cited = sorted(
        [(urn, data.get('count', 0)) for urn, data in citations.items()],
        key=lambda x: x[1],
        reverse=True
    )[:20]
    
    df = pd.DataFrame(
        [(urn, count) for urn, count in top_cited],
        columns=['URN', 'Citations']
    )
    
    if df.shape[0] > 0:
        fig = px.bar(df, x='URN', y='Citations', title="Most Referenced Laws (Top 20)")
        st.plotly_chart(fig, use_container_width=True)


def page_amendments():
    """Show amendment data."""
    st.header("📜 Amendment Tracking")
    
    laws = load_laws()
    
    st.subheader("Most Complex Laws (by articles)")
    
    complex_laws = sorted(
        laws,
        key=lambda x: x.get('article_count', 0),
        reverse=True
    )[:15]
    
    df = pd.DataFrame([
        {
            'Title': law.get('title', '')[:50],
            'Type': law.get('type'),
            'Year': law.get('year'),
            'Articles': law.get('article_count', 0)
        }
        for law in complex_laws
    ])
    
    st.dataframe(df, use_container_width=True)


# NAVIGATION

def main():
    st.sidebar.write("### Navigation")
    
    page = st.sidebar.radio(
        "Go to",
        ["📊 Dashboard", "📋 Browse", "🔍 Search", "🔗 Citations", "📜 Amendments"],
        label_visibility="collapsed"
    )
    
    st.sidebar.divider()
    
    st.sidebar.info("""
    **About this platform:**
    
    Real-time explorer of Italian legal code
    
    - Citation network analysis
    - Full text search
    - Law browse & filter
    - Updated nightly
    
    **Data source**: Normattiva API  
    **Updated**: Every 24 hours
    """)
    
    if page == "📊 Dashboard":
        page_dashboard()
    elif page == "📋 Browse":
        page_browse()
    elif page == "🔍 Search":
        page_search()
    elif page == "🔗 Citations":
        page_citations()
    elif page == "📜 Amendments":
        page_amendments()


if __name__ == "__main__":
    main()
    """Load citation index."""
    try:
        index_path = Path("data/indexes/citations.json")
        if index_path.exists():
            with open(index_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        st.error(f"Error loading citation index: {e}")
        return {}


@st.cache_resource
def load_metrics():
    """Load dataset metrics."""
    try:
        metrics_path = Path("data/indexes/metrics.json")
        if metrics_path.exists():
            with open(metrics_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        st.error(f"Error loading metrics: {e}")
        return {}


def search_laws(laws: List[Dict], query: str, field: str = 'all') -> List[Dict]:
    """Search laws by keyword."""
    query_lower = query.lower()
    results = []
    
    for law in laws:
        if field == 'all':
            searchable = f"{law.get('title', '')} {law.get('urn', '')} {law.get('text', '')}"
        else:
            searchable = law.get(field, '')
        
        if query_lower in searchable.lower():
            results.append(law)
    
    return results


def display_law_detail(law: Dict, citations: Dict):
    """Display detailed view of a single law."""
    st.header(law.get('title', 'Untitled') or 'Untitled')
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("📋 Information")
        st.write(f"**URN**: {law.get('urn', 'N/A')}")
        st.write(f"**Type**: {law.get('type', 'N/A')}")
        st.write(f"**Date**: {law.get('published_date', 'N/A')}")
        if law.get('is_current'):
            st.success("✅ Currently in force")
        else:
            st.info("⏱️ Historical version")
    
    with col2:
        st.subheader("🔗 Related Laws")
        if law.get('amendments'):
            st.write(f"**Amended by**: {len(law['amendments'])} law(s)")
            for amendment in law['amendments'][:5]:
                st.write(f"- {amendment.get('title', amendment['law_id'])}")
        
        if law.get('modifies'):
            st.write(f"**Modifies**: {len(law['modifies'])} law(s)")
    
    # Text content
    if law.get('text'):
        st.subheader("📄 Text")
        st.text_area(
            "Law text:",
            value=law['text'][:5000],
            height=300,
            disabled=True
        )
        if len(law['text']) > 5000:
            st.info(f"Full text is {len(law['text'])} characters")


def display_dashboard():
    """Display dataset dashboard."""
    metrics = load_metrics()
    
    if not metrics:
        st.warning("Metrics not available yet. Run generate_metrics.py first.")
        return
    
    st.header("📊 Dataset Dashboard")
    
    # Key metrics
    basic = metrics.get('basic_stats', {})
    amendments = metrics.get('amendment_statistics', {})
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Laws", basic.get('total_laws', 0))
    
    with col2:
        st.metric(
            "Amendment Relationships",
            amendments.get('total_amendment_relationships', 0)
        )
    
    with col3:
        st.metric(
            "Laws with Amendments",
            amendments.get('laws_with_amendments', 0)
        )
    
    with col4:
        st.metric(
            "Dataset Size (MB)",
            f"{basic.get('file_size_mb', 0):.2f}"
        )
    
    # Type distribution
    type_dist = metrics.get('type_distribution', {})
    if type_dist:
        st.subheader("📑 Laws by Type")
        st.bar_chart(type_dist)
    
    # Temporal distribution
    temporal = metrics.get('temporal_distribution', {})
    if temporal.get('by_year'):
        st.subheader("📅 Laws by Year")
        st.line_chart(temporal['by_year'])
    
    # Coverage
    coverage = metrics.get('coverage', {})
    if coverage.get('percentages'):
        st.subheader("✅ Data Completeness")
        st.json(coverage['percentages'])


def display_amendments_browser():
    """Browse amendment chains."""
    laws = load_laws_data()
    
    if not laws:
        st.warning("Laws data not loaded.")
        return
    
    st.header("📜 Amendment Chains")
    
    # Filter laws with amendments
    amended_laws = [l for l in laws if l.get('amendments')]
    
    st.write(f"Found {len(amended_laws)} laws with amendments")
    
    if amended_laws:
        selected = st.selectbox(
            "Select a law:",
            options=amended_laws,
            format_func=lambda x: f"{x.get('title', x.get('urn', 'Unknown'))}"
        )
        
        if selected:
            st.subheader(f"Amendments to: {selected.get('title', 'Unknown')}")
            
            amendments = selected.get('amendments', [])
            if amendments:
                for i, amendment in enumerate(amendments, 1):
                    col1, col2 = st.columns([1, 4])
                    with col1:
                        st.write(f"**#{i}**")
                    with col2:
                        st.write(
                            f"{amendment.get('title', amendment['law_id'])} "
                            f"({amendment.get('date', 'N/A')})"
                        )


def display_citations_browser():
    """Browse citations network."""
    laws = load_laws_data()
    citations = load_citation_index()
    
    if not laws or not citations:
        st.warning("Data not loaded yet.")
        return
    
    st.header("🔗 Citation Network")
    
    # Find highly cited laws
    citation_counts = [
        (urn, len(v.get('cited_laws', [])))
        for urn, v in citations.get('citations', {}).items()
    ]
    citation_counts.sort(key=lambda x: x[1], reverse=True)
    
    st.subheader("Most Cited Laws")
    top_cited = citation_counts[:10]
    
    for urn, count in top_cited:
        law = next((l for l in laws if (l.get('urn') == urn or l.get('id') == urn)), None)
        st.write(f"{law.get('title', urn) if law else urn}: **{count}** citations")


def main():
    """Main app entry point."""
    st.title("⚖️ Normattiva Research Interface")
    st.markdown(
        """
    Explore Italian law (Normattiva):
    - Search legislation
    - Browse amendment chains
    - Analyze citation networks
    - View historical evolution
    """
    )
    
    # Sidebar navigation
    page = st.sidebar.radio(
        "Navigation",
        ["🏠 Home", "🔍 Search", "📊 Dashboard", "📜 Amendments", "🔗 Citations"]
    )
    
    if page == "🏠 Home":
        st.header("Welcome to Normattiva Research")
        st.markdown("""
        This interface provides access to comprehensive Italian legislation data:
        
        - **Search**: Find laws by keyword or identifier
        - **Dashboard**: View dataset statistics and trends
        - **Amendments**: Explore how laws evolve over time
        - **Citations**: See which laws reference each other
        
        ### Getting Started
        1. Use the **Search** page to find specific laws
        2. Check the **Dashboard** for overall dataset statistics
        3. Browse **Amendments** to understand legal evolution
        4. Analyze **Citations** to see legal relationships
        """)
    
    elif page == "🔍 Search":
        st.header("Search Laws")
        laws = load_laws_data()
        
        if laws:
            col1, col2 = st.columns([3, 1])
            
            with col1:
                query = st.text_input("Search laws by title or content:")
            with col2:
                search_field = st.selectbox("Search in:", ["all", "title", "urn"])
            
            if query:
                results = search_laws(laws, query, search_field)
                
                st.write(f"Found **{len(results)}** results")
                
                if results:
                    selected = st.selectbox(
                        "Select law to view details:",
                        options=results,
                        format_func=lambda x: x.get('title', x.get('urn', 'Unknown'))
                    )
                    
                    if selected:
                        citations = load_citation_index()
                        display_law_detail(selected, citations)
    
    elif page == "📊 Dashboard":
        display_dashboard()
    
    elif page == "📜 Amendments":
        display_amendments_browser()
    
    elif page == "🔗 Citations":
        display_citations_browser()
    
    # Footer
    st.divider()
    st.markdown("""
    **Normattiva Research Interface**  
    Data source: Normattiva API | Updated: Daily at 2 AM UTC  
    [GitHub](https://github.com/your-user/normattiva-research) | 
    [Dataset](https://huggingface.co/datasets/your-user/normattiva-data-raw)
    """)


if __name__ == "__main__":
    main()
