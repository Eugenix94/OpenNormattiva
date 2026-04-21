#!/usr/bin/env python3
"""
Normattiva Jurisprudence Research Platform

Full-featured Streamlit app for exploring Italian law:
- Dashboard with comprehensive statistics
- Advanced full-text search with BM25 ranking
- Law browser with filtering and sorting
- Citation network graph visualization
- Amendment timeline tracking
- Legal domain exploration
- Related law discovery
- Data export

Loads from SQLite database (local) with HF Dataset fallback.
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
import time
import math

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from normattiva_api_client import NormattivaAPI
from parse_akn import AKNParser

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


# ── Shared pipeline state (thread-safe via GIL for simple reads) ─────────────
_pipeline_state = {
    "status": "idle",           # idle | running | completed | error
    "started_at": None,
    "finished_at": None,
    "current_collection": "",
    "collections_done": 0,
    "collections_total": 0,
    "laws_parsed": 0,
    "errors": [],
    "log": [],
}
_pipeline_lock = threading.Lock()

def _log(msg):
    with _pipeline_lock:
        _pipeline_state["log"].append(f"{datetime.now().strftime('%H:%M:%S')} {msg}")
        if len(_pipeline_state["log"]) > 200:
            _pipeline_state["log"] = _pipeline_state["log"][-200:]
    logger.info(msg)


# ── Background pipeline ─────────────────────────────────────────────────────
def _run_full_pipeline():
    """Download ALL collections, parse, build DB, upload to HF Dataset."""
    with _pipeline_lock:
        _pipeline_state["status"] = "running"
        _pipeline_state["started_at"] = datetime.now().isoformat()
        _pipeline_state["errors"] = []
        _pipeline_state["log"] = []

    data_dir = Path("/tmp/normattiva_data")
    raw_dir = data_dir / "raw"
    processed_dir = data_dir / "processed"
    for d in [raw_dir, processed_dir]:
        d.mkdir(parents=True, exist_ok=True)

    api = NormattivaAPI(timeout_s=120, retries=3)
    parser = AKNParser()

    try:
        catalogue = api.get_collection_catalogue()
        seen = set()
        collections = []
        for c in catalogue:
            name = c.get("nomeCollezione", c.get("nome"))
            if name and name not in seen:
                seen.add(name)
                collections.append(name)
    except Exception as e:
        _log(f"ERROR getting catalogue: {e}")
        with _pipeline_lock:
            _pipeline_state["status"] = "error"
            _pipeline_state["errors"].append(str(e))
        return

    with _pipeline_lock:
        _pipeline_state["collections_total"] = len(collections)
    _log(f"Found {len(collections)} collections to process")

    all_laws = []
    for i, coll_name in enumerate(collections):
        with _pipeline_lock:
            _pipeline_state["current_collection"] = coll_name
            _pipeline_state["collections_done"] = i

        _log(f"[{i+1}/{len(collections)}] Downloading {coll_name}...")
        
        data = None
        for fmt in ["AKN", "XML"]:
            try:
                data, etag, ct = api.get_collection(coll_name, variant="V", format=fmt)
                _log(f"  Downloaded via {fmt}: {len(data)/1e6:.1f} MB")
                break
            except Exception as fmt_err:
                _log(f"  {fmt} unavailable: {fmt_err}")
                continue
        
        if not data:
            _log(f"  ERROR: Neither AKN nor XML available")
            with _pipeline_lock:
                _pipeline_state["errors"].append(f"{coll_name}: No format available")
            continue
        
        try:
            zip_path = raw_dir / f"{coll_name}_vigente.zip"
            with open(zip_path, "wb") as f:
                f.write(data)
            laws = parser.parse_zip_file(zip_path)
            for law in laws:
                law = parser.enrich_with_metadata(law)
            all_laws.extend(laws)
            with _pipeline_lock:
                _pipeline_state["laws_parsed"] = len(all_laws)
            _log(f"  Parsed {len(laws)} laws (total: {len(all_laws)})")
            zip_path.unlink(missing_ok=True)
        except Exception as e:
            _log(f"  ERROR parsing {coll_name}: {e}")
            with _pipeline_lock:
                _pipeline_state["errors"].append(f"{coll_name}: {e}")
            continue

    with _pipeline_lock:
        _pipeline_state["collections_done"] = len(collections)
        _pipeline_state["current_collection"] = "saving..."

    if not all_laws:
        _log("ERROR: No laws parsed at all")
        with _pipeline_lock:
            _pipeline_state["status"] = "error"
        return

    # Save JSONL
    jsonl_file = processed_dir / "laws_vigente.jsonl"
    _log(f"Saving {len(all_laws)} laws to JSONL...")
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for law in all_laws:
            f.write(json.dumps(law, ensure_ascii=False) + "\n")

    # Build database with enrichments
    _log("Building database with enrichments...")
    try:
        from core.db import LawDatabase
        db = LawDatabase(data_dir / "laws.db")
        for law in all_laws:
            db.insert_law(law)
        _log("Computing citation counts...")
        db.compute_citation_counts()
        _log("Computing importance scores...")
        db.compute_importance_scores()
        _log("Detecting legal domains...")
        db.detect_law_domains()
        db.close()
        _log("Database ready with enrichments")
    except Exception as e:
        _log(f"DB error: {e}")

    # Build citation index
    _log("Building citation index...")
    citations = {}
    total_cit = 0
    for law in all_laws:
        urn = law.get("urn")
        law_cit = law.get("citations", [])
        if law_cit:
            citations[urn] = {"law": law.get("title"), "citations": law_cit, "count": len(law_cit)}
            total_cit += len(law_cit)

    indexes_dir = data_dir / "indexes"
    indexes_dir.mkdir(parents=True, exist_ok=True)
    cit_file = indexes_dir / "laws_vigente_citations.json"
    with open(cit_file, "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "total_laws_with_citations": len(citations),
            "total_citations": total_cit,
            "citations": citations,
        }, f, ensure_ascii=False, indent=2)

    # Upload to HF Dataset
    if HF_TOKEN:
        _log("Uploading to HF Dataset...")
        try:
            from huggingface_hub import HfApi
            hf_api = HfApi(token=HF_TOKEN)
            hf_api.upload_file(
                path_or_fileobj=str(jsonl_file),
                path_in_repo="data/processed/laws_vigente.jsonl",
                repo_id=HF_DATASET_REPO, repo_type="dataset",
                commit_message=f"Pipeline: {len(all_laws)} laws from {len(collections)} collections",
            )
            _log("  Uploaded JSONL")
        except Exception as e:
            _log(f"  Upload error: {e}")
            with _pipeline_lock:
                _pipeline_state["errors"].append(f"Upload: {e}")

    with _pipeline_lock:
        _pipeline_state["status"] = "completed"
        _pipeline_state["finished_at"] = datetime.now().isoformat()
    _log(f"Pipeline complete: {len(all_laws)} laws, {len(_pipeline_state['errors'])} errors")


def _maybe_start_pipeline():
    """Start the background pipeline if not already running."""
    with _pipeline_lock:
        if _pipeline_state["status"] in ("running",):
            return False
    t = threading.Thread(target=_run_full_pipeline, daemon=True)
    t.start()
    return True


# ── Auto-start on first import ──────────────────────────────────────────────
if "pipeline_autostarted" not in st.session_state:
    st.session_state.pipeline_autostarted = True
    _maybe_start_pipeline()


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
                "SELECT urn, title, type, date, year, status, article_count, text_length, importance_score FROM laws ORDER BY year DESC"
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

def page_pipeline_status():
    st.header("\U0001f6e0 Pipeline Status")
    with _pipeline_lock:
        state = dict(_pipeline_state)

    status = state["status"]
    if status == "idle":
        st.info("Pipeline has not started yet.")
        if st.button("Start Pipeline Now"):
            _maybe_start_pipeline()
            st.rerun()
    elif status == "running":
        pct = state["collections_done"] / max(state["collections_total"], 1)
        st.progress(pct, text=f"Processing: {state['current_collection']} ({state['collections_done']}/{state['collections_total']})")
        st.metric("Laws parsed so far", state["laws_parsed"])
        if state["errors"]:
            with st.expander(f"Errors ({len(state['errors'])})"):
                for e in state["errors"]:
                    st.warning(e)
        with st.expander("Live log"):
            st.code("\n".join(state["log"][-50:]))
        time.sleep(0)
        st.rerun()
    elif status == "completed":
        st.success(f"Pipeline completed at {state['finished_at']}")
        c1, c2 = st.columns(2)
        c1.metric("Total laws", state["laws_parsed"])
        c2.metric("Errors", len(state["errors"]))
        if state["errors"]:
            with st.expander("Errors"):
                for e in state["errors"]:
                    st.warning(e)
        with st.expander("Full log"):
            st.code("\n".join(state["log"]))
        if st.button("Re-run Pipeline"):
            _maybe_start_pipeline()
            st.rerun()
    elif status == "error":
        st.error("Pipeline failed")
        for e in state["errors"]:
            st.warning(e)
        with st.expander("Log"):
            st.code("\n".join(state["log"]))
        if st.button("Retry"):
            _maybe_start_pipeline()
            st.rerun()


def page_dashboard():
    st.header("\U0001f4ca Dashboard")
    db = load_db()
    laws = _get_laws()
    if not laws:
        st.info("No data loaded yet. Check Pipeline Status page.")
        return

    # Top metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Laws", len(laws))
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
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["\U0001f4c4 Text", "\U0001f517 Citations", "\U0001f4dc Amendments", "\U0001f91d Related", "\U0001f578 Graph"])

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

    with tab3:
        amends = db.conn.execute(
            "SELECT * FROM amendments WHERE urn = ? ORDER BY date_effective DESC", (urn,)
        ).fetchall()
        if amends:
            df = pd.DataFrame([dict(a) for a in amends])
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No amendments recorded for this law.")

    with tab4:
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

    with tab5:
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
        "\U0001f6e0 Pipeline": page_pipeline_status,
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

    st.sidebar.divider()
    with _pipeline_lock:
        ps = _pipeline_state["status"]
    if ps == "running":
        st.sidebar.warning("\U0001f504 Pipeline running...")
    elif ps == "completed":
        st.sidebar.success("\u2705 Pipeline done")
    elif ps == "error":
        st.sidebar.error("\u274c Pipeline error")

    # Data source info
    db = load_db()
    if db:
        try:
            count = db.conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
            st.sidebar.metric("Laws in DB", count)
        except Exception:
            pass
    else:
        laws = load_laws_from_jsonl()
        if laws:
            st.sidebar.metric("Laws (JSONL)", len(laws))

    st.sidebar.divider()
    st.sidebar.info(
        "**Data source**: Normattiva API\n\n"
        "Pipeline auto-starts on boot.\n"
        "DB mode enables: FTS search, PageRank,\n"
        "domains, citation graphs, exports."
    )

    pages[page]()


if __name__ == "__main__":
    main()
