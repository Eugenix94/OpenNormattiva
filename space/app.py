#!/usr/bin/env python3
"""
Normattiva Research Platform

Streamlit app with self-populating pipeline:
- On startup, a background thread downloads ALL collections from Normattiva API,
  parses AKN XML, and uploads the results to the HF Dataset.
- The UI loads data from the HF Dataset and lets users browse/search/analyze.
- Progress is shown in a dedicated Pipeline Status page.
"""

import streamlit as st
import json
import os
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import List, Dict
import plotly.express as px
from collections import Counter
import logging
import threading
import time

from huggingface_hub import hf_hub_download, HfApi
from normattiva_api_client import NormattivaAPI
from parse_akn import AKNParser

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

HF_DATASET_REPO = "diatribe00/normattiva-data-raw"
HF_TOKEN = os.environ.get("HF_TOKEN", "")

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
    """Download ALL collections, parse, build indexes, upload to HF Dataset."""
    with _pipeline_lock:
        _pipeline_state["status"] = "running"
        _pipeline_state["started_at"] = datetime.now().isoformat()
        _pipeline_state["errors"] = []
        _pipeline_state["log"] = []

    data_dir = Path("/tmp/normattiva_data")
    raw_dir = data_dir / "raw"
    processed_dir = data_dir / "processed"
    indexes_dir = data_dir / "indexes"
    for d in [raw_dir, processed_dir, indexes_dir]:
        d.mkdir(parents=True, exist_ok=True)

    api = NormattivaAPI(timeout_s=120, retries=3)
    parser = AKNParser()

    # 1. Get all collection names
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

    # 2. Download & parse each collection (vigente variant)
    all_laws = []
    for i, coll_name in enumerate(collections):
        with _pipeline_lock:
            _pipeline_state["current_collection"] = coll_name
            _pipeline_state["collections_done"] = i

        _log(f"[{i+1}/{len(collections)}] Downloading {coll_name}...")
        
        # Try AKN first, then fall back to XML
        data = None
        format_used = None
        for fmt in ["AKN", "XML"]:
            try:
                data, etag, ct = api.get_collection(coll_name, variant="V", format=fmt)
                format_used = fmt
                _log(f"  Downloaded via {fmt}: {len(data)/1e6:.1f} MB")
                break
            except Exception as fmt_err:
                _log(f"  {fmt} unavailable: {fmt_err}")
                continue
        
        if not data:
            _log(f"  ERROR: Neither AKN nor XML available")
            with _pipeline_lock:
                _pipeline_state["errors"].append(f"{coll_name}: No AKN or XML format available")
            continue
        
        try:
            zip_path = raw_dir / f"{coll_name}_vigente_{format_used.lower()}.zip"
            with open(zip_path, "wb") as f:
                f.write(data)

            laws = parser.parse_zip_file(zip_path)
            for law in laws:
                law = parser.enrich_with_metadata(law)
            all_laws.extend(laws)
            with _pipeline_lock:
                _pipeline_state["laws_parsed"] = len(all_laws)
            _log(f"  Parsed {len(laws)} laws ({format_used}) (total: {len(all_laws)})")

            # Clean up raw ZIP to save disk
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

    # 3. Save JSONL
    jsonl_file = processed_dir / "laws_vigente.jsonl"
    _log(f"Saving {len(all_laws)} laws to JSONL...")
    with open(jsonl_file, "w", encoding="utf-8") as f:
        for law in all_laws:
            f.write(json.dumps(law, ensure_ascii=False) + "\n")

    # 4. Build citation index
    _log("Building citation index...")
    citations = {}
    total_cit = 0
    for law in all_laws:
        urn = law.get("urn")
        law_cit = law.get("citations", [])
        if law_cit:
            citations[urn] = {"law": law.get("title"), "citations": law_cit, "count": len(law_cit)}
            total_cit += len(law_cit)

    cit_file = indexes_dir / "laws_vigente_citations.json"
    with open(cit_file, "w", encoding="utf-8") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "total_laws_with_citations": len(citations),
            "total_citations": total_cit,
            "citations": citations,
        }, f, ensure_ascii=False, indent=2)

    # 5. Build metrics
    _log("Building metrics...")
    metrics = {
        "generated": datetime.now().isoformat(),
        "total_laws": len(all_laws),
        "by_type": {},
        "by_year": {},
        "text_stats": {"total_chars": 0, "avg_chars": 0},
        "article_stats": {"total": 0, "avg": 0},
    }
    for law in all_laws:
        t = law.get("type", "unknown")
        metrics["by_type"][t] = metrics["by_type"].get(t, 0) + 1
        y = law.get("year")
        if y:
            metrics["by_year"][y] = metrics["by_year"].get(y, 0) + 1
        metrics["text_stats"]["total_chars"] += law.get("text_length", 0)
        metrics["article_stats"]["total"] += law.get("article_count", 0)
    if all_laws:
        metrics["text_stats"]["avg_chars"] = metrics["text_stats"]["total_chars"] / len(all_laws)
        metrics["article_stats"]["avg"] = metrics["article_stats"]["total"] / len(all_laws)

    met_file = indexes_dir / "laws_vigente_metrics.json"
    with open(met_file, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # 6. Upload to HF Dataset
    if not HF_TOKEN:
        _log("WARNING: No HF_TOKEN set — skipping upload")
    else:
        _log("Uploading to HF Dataset...")
        hf_api = HfApi(token=HF_TOKEN)
        try:
            hf_api.upload_file(
                path_or_fileobj=str(jsonl_file),
                path_in_repo="data/processed/laws_vigente.jsonl",
                repo_id=HF_DATASET_REPO, repo_type="dataset",
                commit_message=f"Pipeline: {len(all_laws)} laws from {len(collections)} collections",
            )
            _log("  Uploaded JSONL")
            hf_api.upload_file(
                path_or_fileobj=str(cit_file),
                path_in_repo="data/indexes/laws_vigente_citations.json",
                repo_id=HF_DATASET_REPO, repo_type="dataset",
                commit_message="Pipeline: citation index",
            )
            _log("  Uploaded citations")
            hf_api.upload_file(
                path_or_fileobj=str(met_file),
                path_in_repo="data/indexes/laws_vigente_metrics.json",
                repo_id=HF_DATASET_REPO, repo_type="dataset",
                commit_message="Pipeline: metrics",
            )
            _log("  Uploaded metrics")
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
    page_title="Normattiva Research",
    page_icon="\u2696\ufe0f",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("\u2696\ufe0f Normattiva Research Platform")
st.markdown("Explore Italian jurisprudence: laws, amendments, citations, and legal evolution")


# ── DATA LOADING ─────────────────────────────────────────────────────────────
@st.cache_resource
def load_laws():
    try:
        local_path = hf_hub_download(
            repo_id=HF_DATASET_REPO,
            filename="data/processed/laws_vigente.jsonl",
            repo_type="dataset",
        )
        laws = []
        with open(local_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    laws.append(json.loads(line))
        return laws
    except Exception as e:
        logger.warning(f"load_laws: {e}")
        return []

@st.cache_resource
def load_citations():
    try:
        p = hf_hub_download(repo_id=HF_DATASET_REPO, filename="data/indexes/laws_vigente_citations.json", repo_type="dataset")
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

@st.cache_resource
def load_metrics():
    try:
        p = hf_hub_download(repo_id=HF_DATASET_REPO, filename="data/indexes/laws_vigente_metrics.json", repo_type="dataset")
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── HELPERS ──────────────────────────────────────────────────────────────────
def search_laws(laws, query):
    q = query.lower()
    return [l for l in laws if q in l.get("urn", "").lower() or q in l.get("title", "").lower() or q in l.get("text", "").lower()[:500]][:100]

def type_dist(laws):
    c = Counter(l.get("type", "unknown") for l in laws)
    return dict(c)

def year_dist(laws):
    c = Counter(str(l["year"]) for l in laws if l.get("year"))
    return dict(sorted(c.items()))


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
        time.sleep(0)  # yield
        st.rerun()
    elif status == "completed":
        st.success(f"Pipeline completed at {state['finished_at']}")
        st.metric("Total laws", state["laws_parsed"])
        st.metric("Errors", len(state["errors"]))
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
    laws = load_laws()
    if not laws:
        st.info("No data loaded yet. Check Pipeline Status page.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Laws", len(laws))
    c2.metric("Document Types", len(type_dist(laws)))
    years = [l.get("year") for l in laws if l.get("year")]
    c3.metric("Year Span", f"{min(years)}-{max(years)}" if years else "N/A")
    c4.metric("Total Articles", sum(l.get("article_count", 0) for l in laws))

    col1, col2 = st.columns(2)
    with col1:
        td = type_dist(laws)
        fig = px.bar(x=list(td.keys()), y=list(td.values()), title="Laws by Type", labels={"x": "Type", "y": "Count"})
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        yd = year_dist(laws)
        fig = px.line(x=list(yd.keys()), y=list(yd.values()), title="Laws by Year", labels={"x": "Year", "y": "Count"}, markers=True)
        st.plotly_chart(fig, use_container_width=True)


def page_browse():
    st.header("\U0001f4cb Browse Laws")
    laws = load_laws()
    if not laws:
        st.info("No data loaded.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        sel_type = st.selectbox("Filter by Type", ["All"] + sorted(set(l.get("type", "unknown") for l in laws)))
    with c2:
        yrs = sorted(set(l.get("year") for l in laws if l.get("year")))
        sel_year = st.selectbox("Filter by Year", ["All"] + yrs)
    with c3:
        sort_by = st.radio("Sort by", ["Year desc", "Year asc", "Title"])

    filtered = laws
    if sel_type != "All":
        filtered = [l for l in filtered if l.get("type") == sel_type]
    if sel_year != "All":
        filtered = [l for l in filtered if l.get("year") == sel_year]

    if sort_by == "Year desc":
        filtered.sort(key=lambda x: x.get("year", "0"), reverse=True)
    elif sort_by == "Year asc":
        filtered.sort(key=lambda x: x.get("year", "0"))
    else:
        filtered.sort(key=lambda x: x.get("title", ""))

    st.write(f"**Showing {len(filtered)} of {len(laws)} laws**")
    page_size = 20
    page_num = st.number_input("Page", 1, max(1, (len(filtered) // page_size) + 1))
    start = (page_num - 1) * page_size

    for law in filtered[start:start + page_size]:
        with st.expander(f"{law.get('title', 'Untitled')} ({law.get('year', '?')})"):
            c1, c2 = st.columns([1, 2])
            with c1:
                st.write(f"**URN**: `{law.get('urn', 'N/A')}`")
                st.write(f"**Type**: {law.get('type', 'N/A')}")
                st.write(f"**Date**: {law.get('date', 'N/A')}")
                st.write(f"**Articles**: {law.get('article_count', 0)}")
            with c2:
                st.text_area("Preview", law.get("text", "")[:1000], height=200, disabled=True)


def page_search():
    st.header("\U0001f50d Search")
    laws = load_laws()
    query = st.text_input("Search by URN, title, or content:")
    if query and len(query) > 2:
        results = search_laws(laws, query)
        st.write(f"**Found {len(results)} results**")
        for law in results[:20]:
            with st.expander(f"{law.get('title', 'Untitled')} ({law.get('year', '?')})"):
                st.write(f"**URN**: `{law.get('urn')}`")
                st.write(f"**Type**: {law.get('type')}")
                if law.get("citations"):
                    st.write(f"**Citations**: {len(law['citations'])} laws referenced")
                st.text_area("Text", law.get("text", ""), height=200, disabled=True)


def page_citations():
    st.header("\U0001f517 Citation Network")
    laws = load_laws()
    cit_idx = load_citations()
    if not cit_idx or not laws:
        st.info("No citation data available.")
        return
    cit = cit_idx.get("citations", {})
    top = sorted([(u, d.get("count", 0)) for u, d in cit.items()], key=lambda x: x[1], reverse=True)[:20]
    df = pd.DataFrame(top, columns=["URN", "Citations"])
    if df.shape[0] > 0:
        fig = px.bar(df, x="URN", y="Citations", title="Most Referenced Laws (Top 20)")
        st.plotly_chart(fig, use_container_width=True)


def page_amendments():
    st.header("\U0001f4dc Amendment Tracking")
    laws = load_laws()
    if not laws:
        return
    complex_laws = sorted(laws, key=lambda x: x.get("article_count", 0), reverse=True)[:15]
    df = pd.DataFrame([
        {"Title": l.get("title", "")[:50], "Type": l.get("type"), "Year": l.get("year"), "Articles": l.get("article_count", 0)}
        for l in complex_laws
    ])
    st.dataframe(df, use_container_width=True)


# ── NAVIGATION ───────────────────────────────────────────────────────────────
def main():
    pages = ["\U0001f6e0 Pipeline", "\U0001f4ca Dashboard", "\U0001f4cb Browse", "\U0001f50d Search", "\U0001f517 Citations", "\U0001f4dc Amendments"]
    st.sidebar.write("### Navigation")
    page = st.sidebar.radio("Go to", pages, label_visibility="collapsed")

    st.sidebar.divider()
    with _pipeline_lock:
        ps = _pipeline_state["status"]
    if ps == "running":
        st.sidebar.warning(f"\U0001f504 Pipeline running...")
    elif ps == "completed":
        st.sidebar.success("\u2705 Pipeline done")
    elif ps == "error":
        st.sidebar.error("\u274c Pipeline error")

    st.sidebar.divider()
    st.sidebar.info("**Data source**: Normattiva API\n\nPipeline auto-starts on boot and populates the HF Dataset.")

    if page == pages[0]:
        page_pipeline_status()
    elif page == pages[1]:
        page_dashboard()
    elif page == pages[2]:
        page_browse()
    elif page == pages[3]:
        page_search()
    elif page == pages[4]:
        page_citations()
    elif page == pages[5]:
        page_amendments()


if __name__ == "__main__":
    main()
