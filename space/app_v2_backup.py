#!/usr/bin/env python3
"""
Normattiva Jurisprudence Research Platform

Static-first Streamlit app — always available, loads pre-built DB.
Live API monitoring detects changes and shows previews before parsing.

Pages:
- What's New (live API changes + changelog)
- Dashboard, Search, Browse, Law Detail, Citations, Amendments,
  Domains, Export
"""

import streamlit as st
import json
import os
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import List, Dict
import plotly.express as px
import plotly.graph_objects as go
from collections import Counter
import logging
import threading
import math

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from normattiva_api_client import NormattivaAPI

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

HF_DATASET_REPO = "diatribe00/normattiva-data"
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# ── Database connection ──────────────────────────────────────────────────────

def get_db():
    """Get database instance, trying local first then HF."""
    from core.db import LawDatabase
    for p in [Path('data/laws.db'), Path('/tmp/normattiva_data/laws.db')]:
        if p.exists():
            return LawDatabase(p)
    # Try downloading pre-built DB from HF Dataset
    try:
        from huggingface_hub import hf_hub_download
        local_path = hf_hub_download(
            repo_id=HF_DATASET_REPO,
            filename="data/laws.db",
            repo_type="dataset",
        )
        return LawDatabase(Path(local_path))
    except Exception:
        pass
    return None


@st.cache_resource
def load_db():
    """Cached database connection."""
    return get_db()


@st.cache_resource
def load_laws_from_jsonl():
    """Fallback: load laws from JSONL if no database."""
    paths = [
        Path('data/processed/laws_vigente.jsonl'),
        Path('/tmp/normattiva_data/processed/laws_vigente.jsonl'),
    ]
    for p in paths:
        if p.exists():
            laws = []
            with open(p, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        laws.append(json.loads(line))
            return laws
    try:
        from huggingface_hub import hf_hub_download
        local_path = hf_hub_download(
            repo_id=HF_DATASET_REPO,
            filename="data/processed/laws_vigente.jsonl",
            repo_type="dataset",
        )
        laws = []
        with open(local_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    laws.append(json.loads(line))
        return laws
    except Exception:
        return []


# ── Live API change monitoring (background) ──────────────────────────────────

_monitor_state = {
    "last_check": None,
    "pending_changes": [],
    "checking": False,
}
_monitor_lock = threading.Lock()


def _run_api_check():
    """Background: poll API for collection changes via ETags."""
    with _monitor_lock:
        if _monitor_state["checking"]:
            return
        _monitor_state["checking"] = True

    try:
        from law_monitor import LawMonitor
        db = load_db()
        monitor = LawMonitor(db=db)
        changes = monitor.check_all_collections()

        with _monitor_lock:
            _monitor_state["pending_changes"] = changes
            _monitor_state["last_check"] = datetime.utcnow().isoformat() + "Z"
    except Exception as e:
        logger.warning(f"API check failed: {e}")
    finally:
        with _monitor_lock:
            _monitor_state["checking"] = False


def trigger_api_check():
    """Start background API check (non-blocking)."""
    t = threading.Thread(target=_run_api_check, daemon=True)
    t.start()


# Auto-check on first load (lightweight ETag poll, not full download)
if "monitor_started" not in st.session_state:
    st.session_state.monitor_started = True
    trigger_api_check()


# ── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Normattiva Jurisprudence",
    page_icon="\u2696\ufe0f",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("\u2696\ufe0f Normattiva Jurisprudence Research Platform")
st.markdown("Explore Italian law: search, citations, amendments, domains, and legal evolution")


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _get_laws():
    """Return list of law dicts from DB or JSONL fallback."""
    db = load_db()
    if db:
        try:
            rows = db.conn.execute(
                "SELECT urn, title, type, date, year, status, article_count, "
                "text_length, importance_score, legislature_id, government, era "
                "FROM laws ORDER BY year DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            try:
                rows = db.conn.execute(
                    "SELECT urn, title, type, date, year, status, article_count, "
                    "text_length, importance_score FROM laws ORDER BY year DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            except Exception:
                pass
    return load_laws_from_jsonl()


def _render_graph_plotly(nodes, edges, title="Citation Graph"):
    """Render a citation graph using Plotly scatter (no networkx needed)."""
    if not nodes or not edges:
        st.info("No graph data to display.")
        return

    # Simple circular layout
    n = len(nodes)
    node_map = {nd["id"]: i for i, nd in enumerate(nodes)}
    angles = [2 * math.pi * i / n for i in range(n)]
    xs = [math.cos(a) for a in angles]
    ys = [math.sin(a) for a in angles]

    edge_x, edge_y = [], []
    for e in edges:
        si = node_map.get(e.get("source"))
        ti = node_map.get(e.get("target"))
        if si is not None and ti is not None:
            edge_x += [xs[si], xs[ti], None]
            edge_y += [ys[si], ys[ti], None]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode='lines',
                             line=dict(width=0.5, color='#888'), hoverinfo='none'))
    sizes = [max(8, min(30, nd.get("size", 10))) for nd in nodes]
    labels = [nd.get("label", nd["id"])[:40] for nd in nodes]
    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode='markers+text', text=labels, textposition="top center",
        marker=dict(size=sizes, color=[nd.get("color", "#1f77b4") for nd in nodes],
                    line=dict(width=1, color='#333')),
        hovertext=[nd.get("label", nd["id"]) for nd in nodes],
    ))
    fig.update_layout(title=title, showlegend=False, hovermode='closest',
                      xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                      yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                      height=600)
    st.plotly_chart(fig, use_container_width=True)


# ── PAGES ────────────────────────────────────────────────────────────────────

def page_whats_new():
    """Live API changes + changelog: what's new before and after parsing."""
    st.header("\U0001f514 What's New")

    tabs = st.tabs(["\U0001f6f0 Live API Changes", "\U0001f4cb Changelog History"])

    # ── Tab 1: Live API Changes (preview before parsing)
    with tabs[0]:
        st.subheader("Live API Change Detection")
        st.caption(
            "Monitors Normattiva API for updated collections using ETags. "
            "Changes are detected *before* the nightly pipeline runs."
        )

        col_btn, col_status = st.columns([1, 3])
        with col_btn:
            if st.button("\U0001f504 Check for changes now"):
                trigger_api_check()
                st.rerun()
        with col_status:
            with _monitor_lock:
                last = _monitor_state["last_check"]
                checking = _monitor_state["checking"]
                pending = list(_monitor_state["pending_changes"])

            if checking:
                st.info("\u23f3 Checking API for changes...")
            elif last:
                st.success(f"Last checked: {last}")
            else:
                st.info("No check performed yet.")

        # Show pending changes (preview)
        if pending:
            st.warning(f"\u26a0\ufe0f **{len(pending)} collection(s) have changed** since last pipeline run!")
            for ch in pending:
                is_new = ch.get('is_new', False)
                icon = "\U0001f195" if is_new else "\U0001f504"
                with st.expander(f"{icon} {ch['collection']}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.write(f"**Collection**: {ch['collection']}")
                        st.write(f"**Detected**: {ch['detected_at']}")
                        if ch.get('num_acts'):
                            st.write(f"**Acts in collection**: {ch['num_acts']}")
                    with c2:
                        if is_new:
                            st.info("First time seeing this collection")
                        else:
                            st.write(f"**Previous ETag**: `{ch.get('old_etag', 'N/A')[:20]}...`")
                            st.write(f"**New ETag**: `{ch.get('new_etag', 'N/A')[:20]}...`")
        else:
            if last:
                st.success("\u2705 All collections up to date \u2014 no changes detected.")

        # Show DB-tracked changes
        db = load_db()
        if db:
            st.divider()
            st.subheader("Change History (from DB)")
            try:
                from law_monitor import LawMonitor
                monitor = LawMonitor(db=db)
                recent = monitor.get_recent_changes(limit=30)
                if recent:
                    rows = []
                    for r in recent:
                        rows.append({
                            "Collection": r['collection'],
                            "Detected": r['detected_at'],
                            "Status": r['status'],
                            "Processed": r.get('processed_at', '\u2014'),
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                    summary = monitor.get_change_summary()
                    if summary:
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Total changes detected", summary.get('total_detected', 0))
                        c2.metric("Pending", summary.get('pending', 0))
                        c3.metric("Last check", summary.get('last_check', 'Never'))
                else:
                    st.info("No change history recorded yet.")
            except Exception as e:
                st.info(f"Change tracking not available: {e}")

    # ── Tab 2: Changelog (post-pipeline)
    with tabs[1]:
        st.subheader("Pipeline Update History")
        st.caption("What changed after each nightly pipeline run.")

        try:
            from core.changelog import ChangelogTracker
            changelog = ChangelogTracker()
            entries = changelog.get_latest_updates(limit=20)

            if entries:
                for entry in reversed(entries):
                    ts = entry.get('timestamp', 'Unknown')
                    with st.expander(f"\U0001f4c5 {ts}"):
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Laws added", entry.get('laws_added', 0))
                        c2.metric("Laws updated", entry.get('laws_updated', 0))
                        c3.metric("Citations added", entry.get('citations_added', 0))
                        c4.metric("Legislatures", entry.get('legislatures_updated', 0))

                        changes = entry.get('changes', {})
                        if changes:
                            st.json(changes)

                summary = changelog.get_summary()
                if summary:
                    st.divider()
                    st.subheader("Cumulative Summary")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Total laws added", summary.get('total_laws_added_history', 0))
                    c2.metric("Total updates", summary.get('total_laws_updated_history', 0))
                    c3.metric("Pipeline runs", summary.get('update_runs', 0))
            else:
                st.info("No changelog entries yet. They will appear after the first pipeline run.")
        except Exception as e:
            st.info(f"Changelog not available: {e}")


def page_dashboard():
    st.header("\U0001f4ca Dashboard")
    db = load_db()
    laws = _get_laws()
    if not laws:
        st.info("No data loaded yet. The database will be populated by the nightly pipeline.")
        return

    # Notification banner for pending API changes
    with _monitor_lock:
        pending = list(_monitor_state["pending_changes"])
    if pending:
        st.warning(
            f"\U0001f514 **{len(pending)} collection(s) updated** on Normattiva API \u2014 "
            f"see What's New page for preview."
        )

    # Top metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Laws", f"{len(laws):,}")
    types = set(l.get("type", "unknown") for l in laws)
    c2.metric("Document Types", len(types))
    years = [l.get("year") for l in laws if l.get("year")]
    c3.metric("Year Range", f"{min(years)}\u2013{max(years)}" if years else "N/A")
    total_articles = sum(l.get("article_count", 0) for l in laws)
    c4.metric("Total Articles", f"{total_articles:,}")

    col1, col2 = st.columns(2)
    with col1:
        type_counts = Counter(l.get("type", "unknown") for l in laws)
        fig = px.pie(names=list(type_counts.keys()), values=list(type_counts.values()),
                     title="Laws by Type", hole=0.4)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        year_counts = Counter(str(l.get("year", "?")) for l in laws if l.get("year"))
        yd = dict(sorted(year_counts.items()))
        fig = px.area(x=list(yd.keys()), y=list(yd.values()),
                      title="Laws by Year", labels={"x": "Year", "y": "Count"})
        st.plotly_chart(fig, use_container_width=True)

    # Legislature distribution
    leg_counts = Counter(l.get("legislature_id") for l in laws if l.get("legislature_id"))
    if leg_counts:
        st.subheader("Laws by Legislature")
        fig = px.bar(
            x=[f"Leg. {k}" for k in sorted(leg_counts.keys())],
            y=[leg_counts[k] for k in sorted(leg_counts.keys())],
            title="Legislative Output by Parliament",
            labels={"x": "Legislature", "y": "Laws"},
        )
        st.plotly_chart(fig, use_container_width=True)

    # Most important laws (if DB available)
    if db:
        st.subheader("Most Important Laws (PageRank)")
        try:
            top = db.conn.execute(
                "SELECT l.urn, l.title, l.year, l.type, m.pagerank, m.citation_count_incoming "
                "FROM laws l JOIN law_metadata m ON l.urn = m.urn "
                "WHERE m.pagerank IS NOT NULL ORDER BY m.pagerank DESC LIMIT 15"
            ).fetchall()
            if top:
                df = pd.DataFrame([dict(r) for r in top])
                df.columns = ["URN", "Title", "Year", "Type", "PageRank", "Cited By"]
                df["Title"] = df["Title"].str[:60]
                df["PageRank"] = df["PageRank"].round(6)
                st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception:
            pass

    # Domain distribution
    if db:
        st.subheader("Legal Domain Distribution")
        try:
            domains = db.conn.execute(
                "SELECT domain_cluster, COUNT(*) as cnt FROM law_metadata "
                "WHERE domain_cluster IS NOT NULL AND domain_cluster != '' "
                "GROUP BY domain_cluster ORDER BY cnt DESC"
            ).fetchall()
            if domains:
                fig = px.bar(x=[d[0] for d in domains], y=[d[1] for d in domains],
                             title="Laws by Legal Domain",
                             labels={"x": "Domain", "y": "Count"})
                st.plotly_chart(fig, use_container_width=True)
        except Exception:
            pass


def page_search():
    st.header("\U0001f50d Advanced Search")
    db = load_db()

    query = st.text_input("Search Italian law (full-text with BM25 ranking):", placeholder="costituzione diritti fondamentali")

    # Advanced filters
    with st.expander("Advanced Filters"):
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            filter_type = st.text_input("Law type (e.g. legge, decreto)")
        with fc2:
            filter_year_from = st.number_input("Year from", min_value=1800, max_value=2100, value=1800)
        with fc3:
            filter_year_to = st.number_input("Year to", min_value=1800, max_value=2100, value=2100)

    if not query or len(query) < 2:
        st.info("Enter at least 2 characters to search.")
        return

    if db:
        # Use FTS5 search with BM25 ranking
        try:
            results = db.search_fts(query, limit=50)
            st.write(f"**Found {len(results)} results** (ranked by relevance)")
            for r in results:
                year = r.get("year", "?")
                if filter_type and filter_type.lower() not in r.get("type", "").lower():
                    continue
                if year != "?" and (int(year) < filter_year_from or int(year) > filter_year_to):
                    continue

                score_str = f" \u2022 Score: {r.get('rank', 0):.2f}" if r.get("rank") else ""
                with st.expander(f"{r.get('title', 'Untitled')} ({year}){score_str}"):
                    c1, c2 = st.columns([1, 3])
                    with c1:
                        st.write(f"**URN**: `{r.get('urn', 'N/A')}`")
                        st.write(f"**Type**: {r.get('type', 'N/A')}")
                        st.write(f"**Date**: {r.get('date', 'N/A')}")
                        if r.get("importance_score"):
                            st.write(f"**Importance**: {r['importance_score']:.4f}")
                    with c2:
                        snippet = r.get("snippet", "")
                        if snippet:
                            st.markdown(f"**Matched text**: ...{snippet}...")
                        else:
                            st.text_area("Preview", r.get("text", "")[:800], height=150, disabled=True)
        except Exception as e:
            st.error(f"Search error: {e}")
    else:
        # Fallback: simple text search on JSONL
        laws = load_laws_from_jsonl()
        q = query.lower()
        results = [l for l in laws if q in l.get("title", "").lower() or q in l.get("text", "").lower()[:500]][:50]
        st.write(f"**Found {len(results)} results** (simple text match)")
        for law in results[:20]:
            with st.expander(f"{law.get('title', 'Untitled')} ({law.get('year', '?')})"):
                st.write(f"**URN**: `{law.get('urn')}`")
                st.write(f"**Type**: {law.get('type')}")
                st.text_area("Text", law.get("text", "")[:800], height=150, disabled=True)


def page_browse():
    st.header("\U0001f4cb Browse Laws")
    laws = _get_laws()
    if not laws:
        st.info("No data loaded.")
        return

    # Filters
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        all_types = sorted(set(l.get("type", "unknown") for l in laws))
        sel_type = st.selectbox("Type", ["All"] + all_types)
    with c2:
        all_years = sorted(set(l.get("year") for l in laws if l.get("year")))
        sel_year = st.selectbox("Year", ["All"] + all_years)
    with c3:
        all_statuses = sorted(set(l.get("status", "vigente") for l in laws))
        sel_status = st.selectbox("Status", ["All"] + all_statuses)
    with c4:
        sort_by = st.selectbox("Sort by", ["Year (newest)", "Year (oldest)", "Title A-Z", "Importance", "Articles"])

    filtered = laws
    if sel_type != "All":
        filtered = [l for l in filtered if l.get("type") == sel_type]
    if sel_year != "All":
        filtered = [l for l in filtered if l.get("year") == sel_year]
    if sel_status != "All":
        filtered = [l for l in filtered if l.get("status") == sel_status]

    if sort_by == "Year (newest)":
        filtered.sort(key=lambda x: x.get("year", "0"), reverse=True)
    elif sort_by == "Year (oldest)":
        filtered.sort(key=lambda x: x.get("year", "0"))
    elif sort_by == "Title A-Z":
        filtered.sort(key=lambda x: x.get("title", ""))
    elif sort_by == "Importance":
        filtered.sort(key=lambda x: x.get("importance_score") or 0, reverse=True)
    elif sort_by == "Articles":
        filtered.sort(key=lambda x: x.get("article_count", 0), reverse=True)

    st.write(f"**Showing {len(filtered)} of {len(laws)} laws**")

    page_size = 25
    total_pages = max(1, math.ceil(len(filtered) / page_size))
    page_num = st.number_input("Page", 1, total_pages, 1)
    start = (page_num - 1) * page_size

    for law in filtered[start:start + page_size]:
        imp = law.get("importance_score")
        imp_badge = f" \u2b50 {imp:.4f}" if imp else ""
        with st.expander(f"{law.get('title', 'Untitled')} ({law.get('year', '?')}){imp_badge}"):
            c1, c2 = st.columns([1, 2])
            with c1:
                st.write(f"**URN**: `{law.get('urn', 'N/A')}`")
                st.write(f"**Type**: {law.get('type', 'N/A')}")
                st.write(f"**Date**: {law.get('date', 'N/A')}")
                st.write(f"**Status**: {law.get('status', 'N/A')}")
                st.write(f"**Articles**: {law.get('article_count', 0)}")
                if imp:
                    st.write(f"**Importance**: {imp:.6f}")
                if law.get('legislature_id'):
                    st.write(f"**Legislature**: {law['legislature_id']}")
                if law.get('government'):
                    st.write(f"**Government**: {law['government']}")
            with c2:
                # Show full text from DB if available
                db = load_db()
                if db:
                    try:
                        row = db.conn.execute("SELECT text FROM laws WHERE urn = ?", (law["urn"],)).fetchone()
                        txt = row[0][:2000] if row else "No text"
                    except Exception:
                        txt = "Error loading text"
                else:
                    txt = law.get("text", "")[:2000] if isinstance(law.get("text"), str) else "No text"
                st.text_area("Text preview", txt, height=250, disabled=True, key=f"browse_{law.get('urn', start)}")


def page_law_detail():
    st.header("\U0001f4d6 Law Detail")
    db = load_db()
    if not db:
        st.info("Database required for detailed law view.")
        return

    # Select law
    laws = _get_laws()
    urn_options = [f"{l.get('title', '')[:60]} ({l.get('urn', '')})" for l in laws[:500]]
    selected = st.selectbox("Select a law:", urn_options if urn_options else ["No laws available"])
    if not selected or selected == "No laws available":
        return

    # Extract URN from selection
    urn = selected.split("(")[-1].rstrip(")")
    law_row = db.conn.execute("SELECT * FROM laws WHERE urn = ?", (urn,)).fetchone()
    if not law_row:
        st.warning("Law not found.")
        return

    law = dict(law_row)
    st.subheader(law.get("title", "Untitled"))

    # Tabs
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "\U0001f4c4 Text", "\U0001f3db Legislature", "\U0001f517 Citations",
        "\U0001f4dc Amendments", "\U0001f91d Related", "\U0001f578 Graph"
    ])

    with tab1:
        c1, c2 = st.columns([1, 2])
        with c1:
            st.write(f"**URN**: `{law.get('urn')}`")
            st.write(f"**Type**: {law.get('type')}")
            st.write(f"**Date**: {law.get('date')}")
            st.write(f"**Status**: {law.get('status')}")
            st.write(f"**Articles**: {law.get('article_count', 0)}")
            st.write(f"**Characters**: {law.get('text_length', 0):,}")
            meta = db.conn.execute("SELECT * FROM law_metadata WHERE urn = ?", (urn,)).fetchone()
            if meta:
                meta = dict(meta)
                if meta.get("pagerank"):
                    st.write(f"**PageRank**: {meta['pagerank']:.6f}")
                if meta.get("domain_cluster"):
                    st.write(f"**Domain**: {meta['domain_cluster']}")
                if meta.get("citation_count_incoming"):
                    st.write(f"**Cited by**: {meta['citation_count_incoming']} laws")
                if meta.get("citation_count_outgoing"):
                    st.write(f"**Cites**: {meta['citation_count_outgoing']} laws")
        with c2:
            st.text_area("Full text", law.get("text", ""), height=400, disabled=True)

    with tab2:
        # Legislature metadata tab
        leg_id = law.get('legislature_id')
        gov = law.get('government')
        era = law.get('era')
        year = law.get('year')

        if leg_id or gov or era:
            c1, c2, c3 = st.columns(3)
            with c1:
                if leg_id:
                    st.metric("Legislature", f"#{leg_id}")
                    try:
                        from core.legislature import LegislatureMetadata
                        rng = LegislatureMetadata.ITALIAN_LEGISLATURES.get(leg_id)
                        if rng:
                            st.caption(f"Parliament {rng[0]}\u2013{rng[1]}")
                    except Exception:
                        pass
            with c2:
                if gov:
                    st.metric("Government", gov)
            with c3:
                if era:
                    st.metric("Historical Era", era)

            # Show other laws from same legislature
            if leg_id:
                st.divider()
                st.write(f"**Other laws from Legislature {leg_id}:**")
                try:
                    same_leg = db.conn.execute(
                        "SELECT urn, title, year, type, importance_score "
                        "FROM laws WHERE legislature_id = ? AND urn != ? "
                        "ORDER BY importance_score DESC LIMIT 15",
                        (leg_id, urn)
                    ).fetchall()
                    if same_leg:
                        df = pd.DataFrame([dict(r) for r in same_leg])
                        df.columns = ["URN", "Title", "Year", "Type", "Importance"]
                        df["Title"] = df["Title"].str[:60]
                        st.dataframe(df, use_container_width=True, hide_index=True)
                except Exception:
                    pass
        else:
            if year:
                st.info(
                    "Legislature metadata not yet enriched for this law. "
                    "It will be populated in the next pipeline run."
                )
            else:
                st.info("No legislature data available (year unknown).")

    with tab3:
        cits = db.conn.execute(
            "SELECT cited_urn, context FROM citations WHERE citing_urn = ?", (urn,)
        ).fetchall()
        if cits:
            st.write(f"This law cites **{len(cits)}** other laws:")
            for c in cits:
                st.write(f"- `{c[0]}`" + (f" \u2014 _{c[1]}_" if c[1] else ""))
        else:
            st.info("No outgoing citations found.")

        # Cited by
        cited_by = db.conn.execute(
            "SELECT citing_urn FROM citations WHERE cited_urn = ?", (urn,)
        ).fetchall()
        if cited_by:
            st.write(f"This law is cited by **{len(cited_by)}** laws:")
            for c in cited_by:
                st.write(f"- `{c[0]}`")

    with tab4:
        amends = db.conn.execute(
            "SELECT * FROM amendments WHERE urn = ? ORDER BY date_effective DESC", (urn,)
        ).fetchall()
        if amends:
            df = pd.DataFrame([dict(a) for a in amends])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No amendments recorded for this law.")

    with tab5:
        try:
            related = db.find_related_laws(urn, limit=10)
            if related:
                st.write(f"**{len(related)}** related laws (by co-citation):")
                for r in related:
                    st.write(f"- `{r['urn']}` \u2014 {r.get('title', 'N/A')} (shared citations: {r.get('shared', 0)})")
            else:
                st.info("No related laws found via co-citation analysis.")
        except Exception:
            st.info("Related law analysis not available.")

    with tab6:
        try:
            neighborhood = db.get_citation_neighborhood(urn, depth=2, max_nodes=40)
            if neighborhood and neighborhood.get("nodes"):
                _render_graph_plotly(
                    neighborhood["nodes"], neighborhood["edges"],
                    title=f"Citation neighborhood of {law.get('title', urn)[:40]}"
                )
            else:
                st.info("No citation graph data available.")
        except Exception as e:
            st.info(f"Graph not available: {e}")


def page_citations():
    st.header("\U0001f517 Citation Network")
    db = load_db()

    if db:
        # Most cited laws
        st.subheader("Most Cited Laws")
        try:
            top = db.conn.execute(
                "SELECT l.urn, l.title, l.year, m.citation_count_incoming, m.pagerank "
                "FROM laws l JOIN law_metadata m ON l.urn = m.urn "
                "WHERE m.citation_count_incoming > 0 ORDER BY m.citation_count_incoming DESC LIMIT 25"
            ).fetchall()
            if top:
                df = pd.DataFrame([dict(r) for r in top])
                df.columns = ["URN", "Title", "Year", "Cited By", "PageRank"]
                df["Title"] = df["Title"].str[:50]
                df["PageRank"] = df["PageRank"].apply(lambda x: f"{x:.6f}" if x else "N/A")
                fig = px.bar(df, x="Title", y="Cited By", title="Top 25 Most Cited Laws",
                             hover_data=["URN", "Year"])
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning(f"Error loading citation data: {e}")

        # Citation graph explorer
        st.subheader("Citation Graph Explorer")
        urn_input = st.text_input("Enter a law URN to explore its citation neighborhood:")
        depth = st.slider("Graph depth", 1, 3, 2)
        max_n = st.slider("Max nodes", 10, 100, 40)
        if urn_input:
            try:
                neighborhood = db.get_citation_neighborhood(urn_input, depth=depth, max_nodes=max_n)
                if neighborhood and neighborhood.get("nodes"):
                    _render_graph_plotly(neighborhood["nodes"], neighborhood["edges"],
                                        title=f"Neighborhood of {urn_input}")
                else:
                    st.warning("No graph data for this URN.")
            except Exception as e:
                st.error(f"Graph error: {e}")

        # Domain citation map
        st.subheader("Cross-Domain Citations")
        try:
            cross = db.conn.execute("""
                SELECT m1.domain_cluster as from_domain, m2.domain_cluster as to_domain, COUNT(*) as cnt
                FROM citations c
                JOIN law_metadata m1 ON c.citing_urn = m1.urn
                JOIN law_metadata m2 ON c.cited_urn = m2.urn
                WHERE m1.domain_cluster IS NOT NULL AND m2.domain_cluster IS NOT NULL
                  AND m1.domain_cluster != '' AND m2.domain_cluster != ''
                GROUP BY m1.domain_cluster, m2.domain_cluster
                ORDER BY cnt DESC LIMIT 30
            """).fetchall()
            if cross:
                df = pd.DataFrame([dict(r) for r in cross])
                fig = px.treemap(df, path=["from_domain", "to_domain"], values="cnt",
                                 title="How Legal Domains Reference Each Other")
                st.plotly_chart(fig, use_container_width=True)
        except Exception:
            pass
    else:
        # Fallback for JSONL
        laws = load_laws_from_jsonl()
        if not laws:
            st.info("No data available.")
            return
        cit_counts = Counter()
        for law in laws:
            for c in law.get("citations", []):
                target = c.get("target_urn", c) if isinstance(c, dict) else c
                cit_counts[target] += 1
        if cit_counts:
            top = cit_counts.most_common(20)
            df = pd.DataFrame(top, columns=["URN", "Times Cited"])
            fig = px.bar(df, x="URN", y="Times Cited", title="Most Referenced Laws")
            st.plotly_chart(fig, use_container_width=True)


def page_amendments():
    st.header("\U0001f4dc Amendment Tracking")
    db = load_db()

    if db:
        # Most amended laws
        st.subheader("Most Amended Laws")
        try:
            most = db.get_most_amended_laws(limit=20)
            if most:
                df = pd.DataFrame(most)
                fig = px.bar(df, x="title", y="amendment_count",
                             title="Top 20 Most Amended Laws", hover_data=["urn"])
                df["title"] = df["title"].str[:50]
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception:
            pass

        # Recent amendments
        st.subheader("Recent Amendments")
        try:
            recent = db.get_recent_amendments(limit=30)
            if recent:
                df = pd.DataFrame(recent)
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("No amendment data recorded yet.")
        except Exception as e:
            st.info(f"Amendment data not available: {e}")

        # Amendment timeline for specific law
        st.subheader("Amendment Timeline")
        urn_input = st.text_input("Enter law URN to see its amendment history:")
        if urn_input:
            try:
                timeline = db.get_amendment_timeline(urn_input)
                if timeline:
                    df = pd.DataFrame(timeline)
                    fig = px.scatter(df, x="date_effective", y="action",
                                    title=f"Amendment timeline: {urn_input}",
                                    hover_data=["change_description"])
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.info("No amendments found for this URN.")
            except Exception as e:
                st.warning(f"Error: {e}")
    else:
        # Fallback
        laws = load_laws_from_jsonl()
        if not laws:
            return
        complex_laws = sorted(laws, key=lambda x: x.get("article_count", 0), reverse=True)[:15]
        df = pd.DataFrame([
            {"Title": l.get("title", "")[:50], "Type": l.get("type"), "Year": l.get("year"),
             "Articles": l.get("article_count", 0)}
            for l in complex_laws
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)


def page_domains():
    st.header("\U0001f3db Legal Domains")
    db = load_db()
    if not db:
        st.info("Database required for domain analysis.")
        return

    try:
        domains = db.conn.execute("""
            SELECT domain_cluster, COUNT(*) as cnt
            FROM law_metadata
            WHERE domain_cluster IS NOT NULL AND domain_cluster != ''
            GROUP BY domain_cluster ORDER BY cnt DESC
        """).fetchall()
    except Exception:
        st.info("Domain data not available. Run enrichment pipeline first.")
        return

    if not domains:
        st.info("No domain data. Run the enrichment pipeline to classify laws by legal domain.")
        return

    # Overview
    domain_names = [d[0] for d in domains]
    domain_counts = [d[1] for d in domains]

    col1, col2 = st.columns(2)
    with col1:
        fig = px.pie(names=domain_names, values=domain_counts, title="Distribution of Legal Domains", hole=0.3)
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        fig = px.bar(x=domain_names, y=domain_counts, title="Laws per Domain",
                     labels={"x": "Domain", "y": "Count"})
        st.plotly_chart(fig, use_container_width=True)

    # Explore specific domain
    selected_domain = st.selectbox("Explore domain:", domain_names)
    if selected_domain:
        laws_in_domain = db.conn.execute("""
            SELECT l.urn, l.title, l.year, l.type, m.pagerank
            FROM laws l JOIN law_metadata m ON l.urn = m.urn
            WHERE m.domain_cluster = ?
            ORDER BY m.pagerank DESC NULLS LAST
            LIMIT 50
        """, (selected_domain,)).fetchall()
        if laws_in_domain:
            df = pd.DataFrame([dict(r) for r in laws_in_domain])
            df.columns = ["URN", "Title", "Year", "Type", "PageRank"]
            df["Title"] = df["Title"].str[:60]
            df["PageRank"] = df["PageRank"].apply(lambda x: f"{x:.6f}" if x else "N/A")
            st.write(f"**{len(laws_in_domain)} laws** in domain _{selected_domain}_:")
            st.dataframe(df, use_container_width=True, hide_index=True)


def page_export():
    st.header("\U0001f4e5 Export Data")
    db = load_db()

    st.subheader("Export Options")

    col1, col2 = st.columns(2)
    with col1:
        st.write("**CSV Export** \u2014 Spreadsheet of all laws with metadata")
        if st.button("Generate CSV"):
            if db:
                try:
                    csv_path = db.export_csv(Path("/tmp/normattiva_export.csv"))
                    with open(csv_path, "r", encoding="utf-8") as f:
                        csv_data = f.read()
                    st.download_button("Download CSV", csv_data, "normattiva_laws.csv", "text/csv")
                except Exception as e:
                    st.error(f"Export error: {e}")
            else:
                laws = load_laws_from_jsonl()
                if laws:
                    df = pd.DataFrame(laws)
                    st.download_button("Download CSV", df.to_csv(index=False), "normattiva_laws.csv", "text/csv")

    with col2:
        st.write("**Citation Graph JSON** \u2014 Network data for visualization tools")
        if st.button("Generate Graph JSON"):
            if db:
                try:
                    json_path = db.export_graph_json(Path("/tmp/normattiva_graph.json"))
                    with open(json_path, "r", encoding="utf-8") as f:
                        json_data = f.read()
                    st.download_button("Download Graph JSON", json_data, "citation_graph.json", "application/json")
                except Exception as e:
                    st.error(f"Export error: {e}")
            else:
                st.info("Database required for graph export.")

    # JSONL download
    st.subheader("Raw Data")
    paths = [Path('data/processed/laws_vigente.jsonl'), Path('/tmp/normattiva_data/processed/laws_vigente.jsonl')]
    for p in paths:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = f.read()
            st.download_button("Download JSONL (raw)", data, "laws_vigente.jsonl", "application/jsonlines")
            st.write(f"File size: {len(data)/1e6:.1f} MB")
            break

    # Data quality
    if db:
        st.subheader("Data Quality Report")
        if st.button("Run Validation"):
            try:
                report = db.validate_data()
                st.json(report)
            except Exception as e:
                st.error(f"Validation error: {e}")


# ── NAVIGATION ───────────────────────────────────────────────────────────────
def main():
    pages = {
        "\U0001f514 What's New": page_whats_new,
        "\U0001f4ca Dashboard": page_dashboard,
        "\U0001f50d Search": page_search,
        "\U0001f4cb Browse": page_browse,
        "\U0001f4d6 Law Detail": page_law_detail,
        "\U0001f517 Citations": page_citations,
        "\U0001f4dc Amendments": page_amendments,
        "\U0001f3db Domains": page_domains,
        "\U0001f4e5 Export": page_export,
    }

    st.sidebar.write("### Navigation")
    page = st.sidebar.radio("Go to", list(pages.keys()), label_visibility="collapsed")

    # Notification badge in sidebar
    with _monitor_lock:
        pending_count = len(_monitor_state["pending_changes"])
    if pending_count > 0:
        st.sidebar.warning(f"\U0001f514 {pending_count} API change(s) detected!")

    st.sidebar.divider()

    # Data source info
    db = load_db()
    if db:
        try:
            count = db.conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
            st.sidebar.metric("Laws in DB", f"{count:,}")
        except Exception:
            pass
    else:
        laws = load_laws_from_jsonl()
        if laws:
            st.sidebar.metric("Laws (JSONL)", f"{len(laws):,}")

    st.sidebar.divider()
    st.sidebar.info(
        "**Data source**: Normattiva API\n\n"
        "Static architecture: site is always\n"
        "available. Data updated nightly via\n"
        "GitHub Actions pipeline.\n\n"
        "Live API monitoring detects changes\n"
        "before the pipeline runs."
    )

    pages[page]()


if __name__ == "__main__":
    main()
