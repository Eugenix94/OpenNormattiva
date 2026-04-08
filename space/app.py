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
from huggingface_hub import hf_hub_download

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HF_DATASET_REPO = "diatribe00/normattiva-data-raw"

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
    """Load all laws from HF Dataset."""
    try:
        local_path = hf_hub_download(
            repo_id=HF_DATASET_REPO,
            filename="data/processed/laws_vigente.jsonl",
            repo_type="dataset"
        )
        
        laws = []
        with open(local_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    laws.append(json.loads(line))
        
        st.success(f"Loaded {len(laws)} laws")
        return laws
    
    except Exception as e:
        st.error(f"Error loading laws: {e}")
        return []

@st.cache_resource
def load_citations():
    """Load citation index from HF Dataset."""
    try:
        local_path = hf_hub_download(
            repo_id=HF_DATASET_REPO,
            filename="data/indexes/laws_vigente_citations.json",
            repo_type="dataset"
        )
        
        with open(local_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error loading citations: {e}")
        return {}

@st.cache_resource
def load_metrics():
    """Load metrics from HF Dataset."""
    try:
        local_path = hf_hub_download(
            repo_id=HF_DATASET_REPO,
            filename="data/indexes/laws_vigente_metrics.json",
            repo_type="dataset"
        )
        
        with open(local_path, 'r', encoding='utf-8') as f:
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
