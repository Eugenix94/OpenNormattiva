#!/usr/bin/env python3
"""
Static Space Launcher - Keep website available 24/7

Ensures the Space:
1. Loads data ONCE on startup (not live parsing)
2. Shows changelog of updates
3. Displays legislature metadata
4. Never becomes unavailable for updates

The nightly pipeline updates data safely in the background,
and users always see the latest stable version.
"""

import streamlit as st
from pathlib import Path
import json
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure Streamlit for static mode
st.set_page_config(
    page_title="Normattiva - Static View",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Add this to main app.py to replace live parsing
def load_data_once():
    """Load database once on app startup (cached)."""
    @st.cache_resource
    def _load():
        from core.db import LawDatabase
        db = LawDatabase("data/laws.db")
        logger.info("✓ Database loaded and cached in memory")
        return db
    return _load()

def display_update_info():
    """Show changelog and last update info."""
    from core.changelog import ChangelogTracker
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 Update Information")
    
    tracker = ChangelogTracker()
    latest = tracker.get_latest_updates(limit=1)
    
    if latest:
        entry = latest[0]
        st.sidebar.info(
            f"**Last Updated:** {entry['timestamp']}\n\n"
            f"**Added:** {entry['laws_added']} laws\n"
            f"**Updated:** {entry['laws_updated']} laws\n"
            f"**Citations:** +{entry['citations_added']}"
        )
        
        # Show recent history
        with st.sidebar.expander("📜 Recent Updates"):
            for e in reversed(tracker.get_latest_updates(limit=5)):
                st.text(
                    f"{e['timestamp']}\n"
                    f"  +{e['laws_added']} laws | "
                    f"+{e['laws_updated']} updated\n"
                )
    else:
        st.sidebar.info("No update history yet")
    
    # Show data status
    st.sidebar.markdown("---")
    st.sidebar.markdown("### Data Status")
    st.sidebar.success("✅ Data **STATIC** and always available")
    st.sidebar.info(
        "Updates run nightly at 2 AM UTC\n"
        "You always see the latest stable version"
    )

def main():
    """Main static app entry point."""
    
    # Load data once (cached across sessions)
    db = load_data_once()
    
    # Header
    st.markdown("# ⚖️ Normattiva - Italian law research")
    st.markdown(
        """
    **Static Jurisprudence Platform** — Always available, always current
    
    _Data updates nightly without disrupting access_
    """)
    
    # Tabs
    tab1, tab2, tab3, tab4 = st.tabs([
        "Dashboard",
        "Search",
        "Browse",
        "Updates"
    ])
    
    with tab1:
        display_dashboard(db)
    
    with tab2:
        display_search(db)
    
    with tab3:
        display_browse(db)
    
    with tab4:
        display_updates()
    
    # Always show changelog in sidebar
    display_update_info()

def display_dashboard(db):
    """Main dashboard stats (static)."""
    col1, col2, col3, col4 = st.columns(4)
    
    laws_count = db.conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    citations_count = db.conn.execute("SELECT COUNT(*) FROM citations").fetchone()[0]
    unique_types = db.conn.execute(
        "SELECT COUNT(DISTINCT type) FROM laws"
    ).fetchone()[0]
    avg_citations = db.conn.execute(
        "SELECT AVG(incoming) FROM (SELECT cited_urn, COUNT(*) as incoming FROM citations GROUP BY cited_urn)"
    ).fetchone()[0] or 0
    
    with col1:
        st.metric("Total Laws", f"{laws_count:,}")
    with col2:
        st.metric("Citations", f"{citations_count:,}")
    with col3:
        st.metric("Law Types", unique_types)
    with col4:
        st.metric("Avg Citations", f"{avg_citations:.1f}")
    
    st.markdown("---")
    st.subheader("Laws by Type")
    
    type_dist = db.conn.execute(
        "SELECT type, COUNT(*) as count FROM laws GROUP BY type ORDER BY count DESC"
    ).fetchall()
    
    import pandas as pd
    df = pd.DataFrame(type_dist, columns=['Type', 'Count'])
    st.bar_chart(df.set_index('Type'))

def display_search(db):
    """Full-text search (static results)."""
    query = st.text_input("🔍 Search laws:")
    
    if query:
        results = db.search_fts(query, limit=20)
        st.markdown(f"**{len(results)} results found**")
        
        for r in results:
            with st.expander(f"{r['urn']} — {r['title'][:60]}..."):
                st.markdown(f"**Type:** {r['type']}")
                st.markdown(f"**Year:** {r['year']}")
                st.markdown(f"**Text preview:** {r['text'][:300]}...")

def display_browse(db):
    """Browse all laws with filters (static)."""
    law_type = st.selectbox("Filter by type:", 
                            ["All"] + db.conn.execute(
                                "SELECT DISTINCT type FROM laws ORDER BY type"
                            ).fetchall()[0])
    
    year_range = st.slider("Filter by year:", 1948, 2026, (2000, 2026))
    
    query = "SELECT urn, title, type, year FROM laws WHERE 1=1"
    params = []
    
    if law_type != "All":
        query += " AND type = ?"
        params.append(law_type)
    
    query += " AND year BETWEEN ? AND ?"
    params.extend(year_range)
    query += " LIMIT 100"
    
    results = db.conn.execute(query, params).fetchall()
    
    st.markdown(f"**{len(results)} laws matched**")
    for r in results:
        st.markdown(f"**{r['urn']}** ({r['year']}) — {r['title']}")

def display_updates():
    """Show detailed update changelog."""
    st.subheader("📋 Complete Update History")
    
    from core.changelog import ChangelogTracker
    tracker = ChangelogTracker()
    
    updates = tracker.get_latest_updates(limit=20)
    
    if not updates:
        st.info("No updates yet")
        return
    
    for update in reversed(updates):
        with st.expander(f"{update['timestamp']} — {update['laws_added']} new"):
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Laws Added", update['laws_added'])
            with col2:
                st.metric("Laws Updated", update['laws_updated'])
            with col3:
                st.metric("Citations Added", update['citations_added'])
            
            if update.get('changes'):
                st.json(update['changes'])

if __name__ == "__main__":
    main()
