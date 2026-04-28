#!/usr/bin/env python3
"""
Normattiva Jurisprudence Research Platform

Streamlit app with resilient database loading:
- Uses local pre-downloaded DB when available
- Falls back to HF Dataset download if DB is missing
- Falls back to JSONL when DB is unavailable

NO automatic pipeline writes. Normattiva API is used READ-ONLY to detect
new/changed collections and show notifications. The user decides when
to manually pull updates into the dataset.

Pages:
  1. Dashboard         - overview stats, charts
  2. Search            - FTS5 full-text with BM25
  3. Browse            - paginated, filtered list
  4. Law Detail        - full text, citations, graph
  5. Citations         - network explorer
  6. Domains           - legal domain analysis
  7. Notifications     - API change detection (read-only)
  8. Update Log        - manual update history
  9. Export            - CSV, JSON, JSONL downloads
"""

import streamlit as st
import json
import os
import sys
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict
import plotly.express as px
import plotly.graph_objects as go
from collections import Counter
import logging
import threading
import math
import requests

# Setup paths for imports
_app_dir = Path(__file__).parent
_root_dir = _app_dir.parent
sys.path.insert(0, str(_root_dir))
sys.path.insert(0, str(_app_dir))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# Database connection and fallback configuration


def get_dataset_repo() -> str:
    """Resolve dataset repo from env vars with safe defaults."""
    owner = os.environ.get("HF_DATASET_OWNER", "diatribe00")
    name = os.environ.get("HF_DATASET_NAME", "normattiva-data")
    return f"{owner}/{name}"

def get_db_paths():
    """Generate database search paths (works in Docker, local dev, and HF Spaces)."""
    _app_file = Path(__file__)
    _app_dir = _app_file.parent
    _root_dir = _app_dir.parent
    
    hf_cache_hub = Path.home() / '.cache' / 'huggingface' / 'hub'
    
    # Scan HF hub cache for the configured dataset snapshot that has laws.db
    hf_cached_paths = []
    dataset_repo = get_dataset_repo()
    dataset_cache_name = f"datasets--{dataset_repo.replace('/', '--')}"
    if hf_cache_hub.exists():
        pattern = f"{dataset_cache_name}/snapshots/*/data/laws.db"
        for snap in hf_cache_hub.glob(pattern):
            hf_cached_paths.append(snap)
    
    base_paths = [
        # Docker container path (set by startup.sh pre-download)
        Path('/app/data/laws.db'),
        # Relative to /app/ working directory in Docker
        Path('data/laws.db'),
        # Relative to where app.py is located
        _app_dir / 'data' / 'laws.db',
        # Relative to parent of app.py directory
        _root_dir / 'data' / 'laws.db',
    ] + hf_cached_paths + [
        # Last-resort tmp fallback
        Path('/tmp/normattiva_data/laws.db'),
    ]
    return base_paths


def download_database_from_hf():
    """Download database from HF Dataset if not found locally."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        logger.error("huggingface_hub not installed — cannot download DB")
        return None
    
    try:
        repo_id = get_dataset_repo()
        token = os.environ.get("HF_TOKEN")
        logger.info("Downloading database from HF Dataset (this may take ~5 min)...")
        cached = hf_hub_download(
            repo_id=repo_id,
            filename="data/laws.db",
            repo_type="dataset",
            token=token,
        )
        logger.info(f"Downloaded to HF cache: {cached}")
        # Also copy to /app/data/laws.db so next startup is instant
        dest = Path('/app/data/laws.db')
        try:
            import shutil
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(cached, dest)
            logger.info(f"Copied to {dest}")
        except Exception:
            pass
        return Path(cached)
    except Exception as e:
        logger.warning(f"Could not download from HF Dataset: {e}")
        return None


def get_db():
    """Get database instance from local pre-built DB or download from HF Dataset."""
    from core.db import LawDatabase
    
    paths_to_check = get_db_paths()
    logger.info(f"Checking {len(paths_to_check)} database paths...")
    
    for i, p in enumerate(paths_to_check, 1):
        try:
            if p.exists():
                size_mb = p.stat().st_size / 1e6
                logger.info(f"✓ [{i}/{len(paths_to_check)}] Found database at: {p} ({size_mb:.1f} MB)")
                return LawDatabase(p)
            else:
                logger.debug(f"  [{i}/{len(paths_to_check)}] Not at: {p}")
        except Exception as e:
            logger.debug(f"  [{i}/{len(paths_to_check)}] Error checking {p}: {e}")
    
    # Try downloading from HF Dataset
    logger.warning(f"DB not found in any local path. Attempting to download from HF...")
    try:
        db_path = download_database_from_hf()
        if db_path and db_path.exists():
            size_mb = db_path.stat().st_size / 1e6
            logger.info(f"✓ Downloaded database: {db_path} ({size_mb:.1f} MB)")
            return LawDatabase(db_path)
    except Exception as e:
        logger.error(f"Failed to download: {e}")
    
    # Log detailed info for troubleshooting
    logger.error(
        f"❌ Database not found after checking {len(paths_to_check)} paths and attempting download:\n"
        + "\n".join(f"  {i}. {p} (exists: {p.exists()})" for i, p in enumerate(paths_to_check, 1))
    )
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
        Path('/app/data/processed/laws_vigente.jsonl'),
        Path(__file__).parent.parent / 'data' / 'processed' / 'laws_vigente.jsonl',
        Path('/tmp/normattiva_data/processed/laws_vigente.jsonl'),
    ]
    for p in paths:
        try:
            if p.exists():
                logger.info(f"Loading JSONL from: {p}")
                laws = []
                with open(p, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            laws.append(json.loads(line))
                return laws
        except Exception as e:
            logger.debug(f"Error loading {p}: {e}")
    return []


def _multivigente_paths() -> List[Path]:
    """Candidate paths for multivigente JSONL artifacts."""
    return [
        Path('data/processed/laws_multivigente.jsonl'),
        Path('data/processed/laws_with_amendments.jsonl'),
        Path('/app/data/processed/laws_multivigente.jsonl'),
        Path('/app/data/processed/laws_with_amendments.jsonl'),
        Path(__file__).parent.parent / 'data' / 'processed' / 'laws_multivigente.jsonl',
        Path(__file__).parent.parent / 'data' / 'processed' / 'laws_with_amendments.jsonl',
        Path('/tmp/normattiva_data/processed/laws_multivigente.jsonl'),
        Path('/tmp/normattiva_data/processed/laws_with_amendments.jsonl'),
    ]


def _find_multivigente_file() -> Path:
    """Return first available multivigente JSONL file path."""
    for p in _multivigente_paths():
        try:
            if p.exists() and p.stat().st_size > 0:
                return p
        except Exception:
            continue
    return Path("")


def _parse_iso_date_safe(value: str):
    """Best-effort parser for YYYY-MM-DD-like strings."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _mv_version_bounds(law: Dict):
    """Extract likely validity start/end fields from heterogeneous schemas."""
    start = (
        law.get("valid_from")
        or law.get("date_in_force")
        or law.get("published_date")
        or law.get("version_date")
        or law.get("date")
        or ""
    )
    end = law.get("valid_to") or law.get("date_out_of_force") or ""
    return str(start), str(end)


@st.cache_data(show_spinner=False, ttl=3600)
def _mv_find_versions(file_path: str, urn_query: str, max_matches: int = 5000) -> List[Dict]:
    """Scan multivigente JSONL and return version rows matching a URN query."""
    out = []
    q = (urn_query or "").strip().lower()
    if not file_path or not q:
        return out

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    law = json.loads(line)
                except Exception:
                    continue

                urn = str(law.get("urn") or law.get("id") or "")
                if not urn:
                    continue

                urn_l = urn.lower()
                if q not in urn_l and q != urn_l:
                    continue

                start, end = _mv_version_bounds(law)
                out.append({
                    "urn": urn,
                    "title": law.get("title", ""),
                    "type": law.get("type", ""),
                    "date": law.get("date", ""),
                    "valid_from": start,
                    "valid_to": end,
                    "is_current": law.get("is_current", False),
                    "article_count": law.get("article_count", 0),
                    "text_length": law.get("text_length", 0),
                })

                if len(out) >= max_matches:
                    break
    except Exception:
        return []

    out.sort(key=lambda r: (r.get("valid_from") or r.get("date") or "", r.get("valid_to") or ""))
    return out


def _mv_is_active_on(row: Dict, as_of_date) -> bool:
    """Return True if a version row is active at the selected date."""
    d_from = _parse_iso_date_safe(row.get("valid_from") or row.get("date") or "")
    d_to = _parse_iso_date_safe(row.get("valid_to") or "")

    if d_from and as_of_date < d_from:
        return False
    if d_to and as_of_date > d_to:
        return False
    return True


# API change monitoring (read-only, background)

_monitor_state = {
    "last_check": None,
    "pending_changes": [],
    "checking": False,
    "error": None,
}
_monitor_lock = threading.Lock()


def _run_api_check():
    """Background: poll API for collection changes via ETags (read-only)."""
    with _monitor_lock:
        if _monitor_state["checking"]:
            return
        _monitor_state["checking"] = True
        _monitor_state["error"] = None

    try:
        from normattiva_api_client import NormattivaAPI
        api = NormattivaAPI(timeout_s=15, retries=1)
        catalogue = api.get_collection_catalogue()

        changes = []
        etag_path = Path('data/.etag_cache.json')
        etag_cache = {}
        if etag_path.exists():
            try:
                etag_cache = json.loads(etag_path.read_text())
            except Exception:
                pass

        seen = set()
        for c in catalogue:
            name = c.get('nomeCollezione', c.get('nome'))
            if not name or name in seen:
                continue
            seen.add(name)
            try:
                new_etag = api.check_collection_etag(name, variant='V', format='AKN')
                if not new_etag:
                    continue
                old_etag = etag_cache.get(name)
                if old_etag and old_etag == new_etag:
                    continue
                changes.append({
                    'collection': name,
                    'old_etag': old_etag,
                    'new_etag': new_etag,
                    'detected_at': datetime.now(timezone.utc).isoformat(),
                    'is_new': old_etag is None,
                    'num_acts': c.get('numeroAtti', 0),
                })
            except Exception:
                continue

        with _monitor_lock:
            _monitor_state["pending_changes"] = changes
            _monitor_state["last_check"] = datetime.now(timezone.utc).isoformat()

    except Exception as e:
        with _monitor_lock:
            _monitor_state["error"] = str(e)
        logger.warning(f"API check failed: {e}")
    finally:
        with _monitor_lock:
            _monitor_state["checking"] = False


def trigger_api_check():
    """Start background API check (non-blocking, read-only)."""
    t = threading.Thread(target=_run_api_check, daemon=True)
    t.start()


# PAGE CONFIG

st.set_page_config(
    page_title="Normattiva Jurisprudence",
    page_icon="\u2696\ufe0f",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("\u2696\ufe0f Normattiva Jurisprudence Research Platform")
st.markdown("Explore Italian law: search, citations, domains, and legal evolution (v2.2)")


# HELPERS

@st.cache_data(ttl=3600, show_spinner="Loading laws...")
def _get_laws_cached(db_path: str, limit: int | None = None):
    """Cached law loading — separated from session state for cache key."""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        query = (
            "SELECT urn, title, type, date, year, status, article_count, "
            "text_length, importance_score FROM laws ORDER BY year DESC"
        )
        params = ()
        if limit and limit > 0:
            query += " LIMIT ?"
            params = (limit,)
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to cache-load laws: {e}")
        return []


def _get_max_laws_limit() -> int | None:
    """
    Return max number of laws to load into memory.

    Configure with env var MAX_LAWS_IN_MEMORY:
    - 0 or unset: no limit (load all)
    - >0: cap rows in memory for constrained hardware
    """
    raw = (os.environ.get("MAX_LAWS_IN_MEMORY") or "0").strip()
    try:
        value = int(raw)
        return value if value > 0 else None
    except Exception:
        logger.warning(
            "Invalid MAX_LAWS_IN_MEMORY=%r; using unlimited mode", raw
        )
        return None


def _count_total_laws(db) -> int | None:
    """Return exact law count from DB when available."""
    if not db:
        return None
    try:
        return int(db.conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0])
    except Exception:
        return None


@st.cache_data(ttl=1800)
def _get_db_integrity_snapshot(db_path: str) -> Dict:
    """Compute integrity metrics directly from SQLite base tables."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        out: Dict[str, float | int] = {}
        out["total_laws"] = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
        out["total_citations"] = conn.execute("SELECT COUNT(*) FROM citations").fetchone()[0]
        out["total_articles"] = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        out["total_amendments"] = conn.execute("SELECT COUNT(*) FROM amendments").fetchone()[0]

        out["laws_with_any_citation"] = conn.execute(
            """
            SELECT COUNT(DISTINCT u.urn)
            FROM (
                SELECT citing_urn AS urn FROM citations
                UNION
                SELECT cited_urn AS urn FROM citations
            ) u
            JOIN laws l ON l.urn = u.urn
            """
        ).fetchone()[0]

        out["valid_citation_edges"] = conn.execute(
            """
            SELECT COUNT(*)
            FROM citations c
            JOIN laws l1 ON l1.urn = c.citing_urn
            JOIN laws l2 ON l2.urn = c.cited_urn
            """
        ).fetchone()[0]

        out["orphan_citing"] = conn.execute(
            """
            SELECT COUNT(*)
            FROM citations c
            LEFT JOIN laws l ON l.urn = c.citing_urn
            WHERE l.urn IS NULL
            """
        ).fetchone()[0]

        out["orphan_cited"] = conn.execute(
            """
            SELECT COUNT(*)
            FROM citations c
            LEFT JOIN laws l ON l.urn = c.cited_urn
            WHERE l.urn IS NULL
            """
        ).fetchone()[0]

        out["domains_nonempty"] = conn.execute(
            """
            SELECT COUNT(*)
            FROM law_metadata
            WHERE domain_cluster IS NOT NULL AND domain_cluster != ''
            """
        ).fetchone()[0]

        out["refs_detected_in_text_laws"] = conn.execute(
            "SELECT COUNT(*) FROM laws WHERE text LIKE '%urn:nir:%'"
        ).fetchone()[0]

        total_laws = out["total_laws"] or 0
        total_citations = out["total_citations"] or 0
        out["pct_laws_with_any_citation"] = (
            round((out["laws_with_any_citation"] / total_laws) * 100, 2)
            if total_laws else 0.0
        )
        out["pct_valid_citation_edges"] = (
            round((out["valid_citation_edges"] / total_citations) * 100, 2)
            if total_citations else 0.0
        )
        out["domain_coverage_pct"] = (
            round((out["domains_nonempty"] / total_laws) * 100, 2)
            if total_laws else 0.0
        )
        return out
    finally:
        conn.close()


def _get_laws():
    """Return list of law dicts from DB or JSONL fallback."""
    db = load_db()
    if db:
        try:
            limit = _get_max_laws_limit()
            db_path = str(db.db_path) if hasattr(db, 'db_path') else ""
            if db_path:
                return _get_laws_cached(db_path, limit)
            # Fallback: direct query without cache
            query = (
                "SELECT urn, title, type, date, year, status, article_count, "
                "text_length, importance_score FROM laws ORDER BY year DESC"
            )
            params = ()
            if limit and limit > 0:
                query += " LIMIT ?"
                params = (limit,)
            rows = db.conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error loading laws from DB: {e}")
    return load_laws_from_jsonl()


def _render_graph_plotly(nodes, edges, title="Citation Graph"):
    """Render a citation graph using Plotly scatter."""
    if not nodes or not edges:
        st.info("No graph data to display.")
        return

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
    st.plotly_chart(fig, width='stretch')


def linkify_law_text(text: str, db) -> dict:
    """
    Extract URN references from text and return a dict of URN -> title.
    Used to build reference tables for law text.
    """
    if not text or not db:
        return {}
    
    import re
    urn_pattern = r'urn:nir:[a-zA-Z0-9.:;\-]+'
    
    urns = list(dict.fromkeys(re.findall(urn_pattern, text)))
    if not urns:
        return {}
    
    urn_titles = {}
    for urn in urns[:30]:  # Limit lookups for performance
        try:
            law = db.get_law(urn)
            if law:
                urn_titles[urn] = law.get('title', 'N/A')[:60]
        except Exception:
            pass
    
    return urn_titles


def _get_update_log(db):
    """Get manual update log entries from DB."""
    if not db:
        return []
    try:
        rows = db.conn.execute(
            "SELECT * FROM update_log ORDER BY timestamp DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _record_update_log(db, action, description, laws_before=None, laws_after=None,
                       collections=None, user_note=None):
    """Record a manual update log entry."""
    if not db:
        return
    try:
        db.conn.execute('''
            INSERT INTO update_log (timestamp, action, description, laws_before,
                                    laws_after, collections_affected, user_note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now(timezone.utc).isoformat(),
            action,
            description,
            laws_before,
            laws_after,
            json.dumps(collections) if collections else None,
            user_note,
        ))
        db.conn.commit()
    except Exception as e:
        logger.warning(f"Failed to record update log: {e}")


@st.cache_data(ttl=900)
def _get_nightly_pipeline_runs(limit: int = 8) -> List[Dict]:
    """Fetch recent GitHub nightly workflow runs with lightweight failure details."""
    runs_url = (
        "https://api.github.com/repos/Eugenix94/OpenNormattiva/"
        "actions/workflows/nightly-update.yml/runs"
    )
    try:
        r = requests.get(runs_url, params={"per_page": max(1, min(limit, 20))}, timeout=10)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        logger.warning("Could not fetch nightly workflow runs: %s", e)
        return []

    results: List[Dict] = []
    for run in payload.get("workflow_runs", [])[:limit]:
        row = {
            "created_at": run.get("created_at", ""),
            "event": run.get("event", ""),
            "status": run.get("status", ""),
            "conclusion": run.get("conclusion", ""),
            "run_id": run.get("id"),
            "run_number": run.get("run_number"),
            "html_url": run.get("html_url", ""),
            "failed_job": "",
            "failed_step": "",
        }

        # Pull compact failure details only for failed runs.
        if row["conclusion"] == "failure" and row["run_id"]:
            jobs_url = (
                f"https://api.github.com/repos/Eugenix94/OpenNormattiva/"
                f"actions/runs/{row['run_id']}/jobs"
            )
            try:
                jr = requests.get(jobs_url, timeout=10)
                if jr.status_code == 200:
                    jobs = jr.json().get("jobs", [])
                    failed_jobs = [j for j in jobs if j.get("conclusion") == "failure"]
                    if failed_jobs:
                        fj = failed_jobs[0]
                        row["failed_job"] = fj.get("name", "")
                        for step in fj.get("steps", []):
                            if step.get("conclusion") == "failure":
                                row["failed_step"] = step.get("name", "")
                                break
            except Exception:
                pass

        results.append(row)

    return results


# PAGES

def page_dashboard():
    st.header("\U0001f4ca Dashboard")
    db = load_db()
    laws = _get_laws()
    total_laws = _count_total_laws(db)
    
    if not laws:
        st.error(
            "No data loaded. The pre-built database could not be found. "
            "The Space may not have been deployed with the database included."
        )
        st.info(
            "This is expected if the Space was just deployed. "
            "Database paths searched:\n" +
            "\n".join(f"- {p}" for p in get_db_paths())
        )
        return

    # Notification badge
    with _monitor_lock:
        pending = list(_monitor_state["pending_changes"])
    if pending:
        st.info(
            f"\U0001f514 **{len(pending)} collection(s) changed** on Normattiva API "
            f"-- see **Notifications** page to review."
        )

    # Top metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Laws", f"{(total_laws if total_laws is not None else len(laws)):,}")
    types = set(l.get("type", "unknown") for l in laws)
    c2.metric("Document Types", len(types))
    years = [l.get("year") for l in laws if l.get("year")]
    c3.metric("Year Range", f"{min(years)}-{max(years)}" if years else "N/A")
    if db:
        try:
            total_articles = db.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        except Exception:
            total_articles = sum(l.get("article_count", 0) for l in laws)
    else:
        total_articles = sum(l.get("article_count", 0) for l in laws)
    c4.metric("Total Articles", f"{total_articles:,}")

    # DB info
    if db:
        try:
            cit_count = db.conn.execute("SELECT COUNT(*) FROM citations").fetchone()[0]
            scored = db.conn.execute(
                "SELECT COUNT(*) FROM laws WHERE importance_score > 0"
            ).fetchone()[0]
            st.caption(
                f"Citations: {cit_count:,} | PageRank-scored: {scored:,} | "
                f"DB: static pre-built"
            )
        except Exception:
            pass

    if db and hasattr(db, "db_path"):
        try:
            snap = _get_db_integrity_snapshot(str(db.db_path))
            with st.expander("🔎 Data Integrity Snapshot"):
                s1, s2, s3, s4 = st.columns(4)
                s1.metric("Laws Linked", f"{snap['laws_with_any_citation']:,}")
                s2.metric("Linked %", f"{snap['pct_laws_with_any_citation']:.2f}%")
                s3.metric("Valid Citation Edges", f"{snap['valid_citation_edges']:,}")
                s4.metric("Valid Edge %", f"{snap['pct_valid_citation_edges']:.2f}%")
                st.caption(
                    f"Orphan citing: {snap['orphan_citing']:,} | "
                    f"Orphan cited: {snap['orphan_cited']:,} | "
                    f"Domain coverage: {snap['domain_coverage_pct']:.2f}% | "
                    f"Laws with URN refs in text: {snap['refs_detected_in_text_laws']:,}"
                )
        except Exception as e:
            logger.warning("Could not compute integrity snapshot: %s", e)

    col1, col2 = st.columns(2)
    with col1:
        type_counts = Counter(l.get("type", "unknown") for l in laws)
        fig = px.pie(names=list(type_counts.keys()), values=list(type_counts.values()),
                     title="Laws by Type", hole=0.4)
        st.plotly_chart(fig, width='stretch')
    with col2:
        year_counts = Counter(str(l.get("year", "?")) for l in laws if l.get("year"))
        yd = dict(sorted(year_counts.items()))
        fig = px.area(x=list(yd.keys()), y=list(yd.values()),
                      title="Laws by Year", labels={"x": "Year", "y": "Count"})
        st.plotly_chart(fig, width='stretch')

    # Most important laws
    if db:
        st.subheader("Most Important Laws (PageRank)")
        try:
            top = db.conn.execute(
                "SELECT urn, title, year, type, importance_score "
                "FROM laws WHERE importance_score > 0 "
                "ORDER BY importance_score DESC LIMIT 15"
            ).fetchall()
            if top:
                df = pd.DataFrame([dict(r) for r in top])
                df.columns = ["URN", "Title", "Year", "Type", "Importance"]
                df["Title"] = df["Title"].str[:60]
                df["Importance"] = df["Importance"].round(4)
                st.dataframe(df, width='stretch', hide_index=True)
                st.caption("Quick open from dashboard")
                for law_d in [dict(r) for r in top[:5]]:
                    _render_law_card(law_d, db, key_prefix="dashboard-top")
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
                st.plotly_chart(fig, width='stretch')
        except Exception:
            pass


def page_search():
    st.header("\U0001f50d Advanced Search")
    db = load_db()

    query = st.text_input(
        "Search Italian law (full-text with BM25 ranking):",
        placeholder="costituzione diritti fondamentali"
    )

    with st.expander("Advanced Filters"):
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            filter_type = st.text_input("Law type (e.g. legge, decreto)")
        with fc2:
            filter_year_from = st.number_input("Year from", min_value=1800,
                                                max_value=2100, value=1800)
        with fc3:
            filter_year_to = st.number_input("Year to", min_value=1800,
                                              max_value=2100, value=2100)

    if not query or len(query) < 2:
        st.info("Enter at least 2 characters to search.")
        return

    if db:
        try:
            results = db.search_fts(query, limit=50)
            st.write(f"**Found {len(results)} results** (ranked by relevance)")
            for r in results:
                year = r.get("year", "?")
                if filter_type and filter_type.lower() not in r.get("type", "").lower():
                    continue
                if year != "?" and (int(year) < filter_year_from or int(year) > filter_year_to):
                    continue
                score_str = ""
                if r.get("rank"):
                    score_str = f" · Score: {r['rank']:.2f}"
                status = r.get("status", "in_force")
                status_badge = " 🚫 *ABROGATO*" if status == "abrogated" else ""
                with st.expander(f"{r.get('title', 'Untitled')} ({year}){score_str}{status_badge}"):
                    c1, c2 = st.columns([1, 3])
                    with c1:
                        st.write(f"**URN**: `{r.get('urn', 'N/A')}`")
                        st.write(f"**Type**: {r.get('type', 'N/A')}")
                        st.write(f"**Date**: {r.get('date', 'N/A')}")
                        st.write(f"**Status**: {'⚡ In vigore' if status == 'in_force' else '🚫 Abrogato'}")
                        if r.get("importance_score"):
                            st.write(f"**Importance**: {r['importance_score']:.4f}")
                        if st.button("📖 Apri legge →", key=f"search-open-{r.get('urn','')}"):
                            _open_law(r.get("urn"), source_tab="search", source_context=query)
                    with c2:
                        snippet = r.get("snippet", "")
                        if snippet:
                            st.markdown(f"**Matched text**: ...{snippet}...")
                        else:
                            st.text_area("Preview", r.get("text", "")[:800],
                                         height=150, disabled=True,
                                         key=f"search_{r.get('urn','')}")
                        _render_law_linkage_summary(db, r.get("urn", ""), key_prefix="search")
        except Exception as e:
            st.error(f"Search error: {e}")
    else:
        laws = load_laws_from_jsonl()
        q = query.lower()
        results = [l for l in laws
                   if q in l.get("title", "").lower()
                   or q in l.get("text", "").lower()[:500]][:50]
        st.write(f"**Found {len(results)} results** (simple text match)")
        for law in results[:20]:
            with st.expander(
                f"{law.get('title', 'Untitled')} ({law.get('year', '?')})"
            ):
                st.write(f"**URN**: `{law.get('urn')}`")
                st.write(f"**Type**: {law.get('type')}")
                st.text_area("Text", law.get("text", "")[:800], height=150,
                             disabled=True, key=f"srch_jl_{law.get('urn','')}")


def page_browse():
    st.header("\U0001f4cb Browse Laws")
    db = load_db()
    laws = _get_laws()
    total_laws = _count_total_laws(db)
    if not laws:
        st.info("No data loaded.")
        return

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
        sort_by = st.selectbox("Sort by", [
            "Year (newest)", "Year (oldest)", "Title A-Z", "Importance", "Articles"
        ])

    filtered = laws
    if sel_type != "All":
        filtered = [l for l in filtered if l.get("type") == sel_type]
    if sel_year != "All":
        filtered = [l for l in filtered if l.get("year") == sel_year]
    if sel_status != "All":
        filtered = [l for l in filtered if l.get("status") == sel_status]

    if sort_by == "Year (newest)":
        filtered.sort(key=lambda x: x.get("year", 0), reverse=True)
    elif sort_by == "Year (oldest)":
        filtered.sort(key=lambda x: x.get("year", 0))
    elif sort_by == "Title A-Z":
        filtered.sort(key=lambda x: x.get("title", ""))
    elif sort_by == "Importance":
        filtered.sort(key=lambda x: x.get("importance_score") or 0, reverse=True)
    elif sort_by == "Articles":
        filtered.sort(key=lambda x: x.get("article_count", 0), reverse=True)

    shown_total = total_laws if total_laws is not None else len(laws)
    st.write(f"**Showing {len(filtered)} of {shown_total} laws**")

    effective_limit = _get_max_laws_limit()
    if effective_limit:
        st.caption(
            f"In-memory analysis cap active: {effective_limit:,} rows "
            "(set MAX_LAWS_IN_MEMORY=0 for full dataset)."
        )

    page_size = 25
    total_pages = max(1, math.ceil(len(filtered) / page_size))
    page_num = st.number_input("Page", 1, total_pages, 1)
    start = (page_num - 1) * page_size

    for law in filtered[start:start + page_size]:
        imp = law.get("importance_score")
        imp_badge = f" * {imp:.4f}" if imp else ""
        with st.expander(
            f"{law.get('title', 'Untitled')} ({law.get('year', '?')}){imp_badge}"
        ):
            c1, c2 = st.columns([1, 2])
            with c1:
                st.write(f"**URN**: `{law.get('urn', 'N/A')}`")
                st.write(f"**Type**: {law.get('type', 'N/A')}")
                st.write(f"**Date**: {law.get('date', 'N/A')}")
                st.write(f"**Status**: {law.get('status', 'N/A')}")
                st.write(f"**Articles**: {law.get('article_count', 0)}")
                if imp:
                    st.write(f"**Importance**: {imp:.6f}")
                if st.button("📖 Apri legge", key=f"browse-open-{law.get('urn','')}"):
                    _open_law(law.get("urn", ""), source_tab="browse")
            with c2:
                db = load_db()
                if db:
                    try:
                        row = db.conn.execute(
                            "SELECT text FROM laws WHERE urn = ?",
                            (law["urn"],)
                        ).fetchone()
                        txt = row[0][:2000] if row else "No text"
                    except Exception:
                        txt = "Error loading text"
                else:
                    txt = (law.get("text", "")[:2000]
                           if isinstance(law.get("text"), str)
                           else "No text")
                st.text_area("Text preview", txt, height=250, disabled=True,
                             key=f"browse_{law.get('urn', start)}")
                if db:
                    _render_law_linkage_summary(db, law.get("urn", ""), key_prefix="browse")


def page_law_detail():
    st.header("📖 Law Detail - Full Context & Relationships")
    db = load_db()
    if not db:
        st.info("Database required for detailed law view.")
        return

    # Navigation priority: session_state (card/citation buttons) > query_params > UI inputs
    nav_urn = st.session_state.pop("detail_urn", None)
    if not nav_urn:
        nav_urn = st.query_params.get("urn")
    if nav_urn:
        # Pre-fill the text input via session_state key
        st.session_state["law-detail-urn-input"] = nav_urn

    c_nav1, c_nav2 = st.columns([2, 3])
    with c_nav1:
        direct_urn = st.text_input(
            "URN diretto:",
            placeholder="urn:nir:stato:legge:2020-01-01;1",
            key="law-detail-urn-input",
        )
    with c_nav2:
        laws = _get_laws()
        urn_options = [
            f"{l.get('title', '')[:60]} ({l.get('urn', '')})"
            for l in laws[:500]
        ]
        selected = st.selectbox(
            "Oppure seleziona dalla lista (prime 500):",
            [""] + (urn_options if urn_options else ["No laws available"]),
            index=0,
            key="law-detail-select",
        )

    # Resolve active URN: direct input takes priority over selectbox
    urn = direct_urn.strip() if direct_urn and direct_urn.strip() else ""
    if not urn and selected and selected not in ("", "No laws available"):
        urn = selected.split("(")[-1].rstrip(")")

    if not urn:
        st.info("Inserisci un URN diretto oppure seleziona una legge dalla lista.")
        return

    law_row = db.conn.execute(
        "SELECT * FROM laws WHERE urn = ?", (urn,)
    ).fetchone()
    if not law_row:
        st.warning(f"Legge non trovata per URN: `{urn}`")
        return

    law = dict(law_row)
    status_label = " 🚫 *ABROGATO*" if law.get("status") == "abrogated" else ""
    st.subheader(f"{law.get('title', 'Untitled')}{status_label}")
    _render_law_linkage_summary(db, urn, key_prefix="detail")

    # Quick metadata in columns
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Type", law.get("type", "N/A"))
    col2.metric("Year", law.get("year", "N/A"))
    col3.metric("Articles", law.get("article_count", 0))
    if law.get("importance_score"):
        col4.metric("Importance (PageRank)", f"{law['importance_score']:.4f}")

    # Main tabs
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📄 Full Text", 
        "🔗 Citation Links", 
        "📚 Related Laws",
        "⚖️ Amendments",
        "🎯 Context Graph",
        "§ Articles"
    ])

    with tab1:
        """Full text with metadata"""
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.subheader("Metadata")
            st.write(f"**URN**: `{law.get('urn')}`")
            st.write(f"**Date**: {law.get('date', 'N/A')}")
            st.write(f"**Status**: {law.get('status', 'N/A')}")
            st.write(f"**Characters**: {law.get('text_length', 0):,}")
            
            meta = db.conn.execute(
                "SELECT * FROM law_metadata WHERE urn = ?", (urn,)
            ).fetchone()
            if meta:
                meta = dict(meta)
                if meta.get("domain_cluster"):
                    st.write(f"**Legal Domain**: {meta['domain_cluster']}")
        
        with col2:
            st.subheader("Full Text")
            text = law.get("text", "")
            if text:
                st.text_area(
                    "Content:", text, height=400, 
                    disabled=True, key="law-text"
                )
                # Show clickable URN reference panel below text
                with st.expander("📎 Leggi citate nel testo (URN references)"):
                    found_refs = _urn_inline_links(text, db, key_prefix="lawdetail")
                    if not found_refs:
                        st.caption("Nessun URN normativo rilevato nel testo.")
            else:
                st.info("No text content available.")

    with tab2:
        """Citations: who cites this law, and what it cites"""
        st.subheader("🔗 Citation Network")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**Laws that CITE this law** (incoming citations)")
            cited_by = db.get_citations_incoming(urn, limit=50)
            if cited_by:
                st.write(f"✓ Cited by **{len(cited_by)}** laws")
                for cit in cited_by[:20]:
                    cited_urn = cit.get("citing_urn") or cit.get("urn")
                    context = cit.get("context", "")
                    context_preview = f" _{context[:80]}..._" if context else ""
                    with st.expander(f"📌 {cited_urn[:50]}{context_preview}"):
                        try:
                            cited_law = db.get_law(cited_urn)
                            if cited_law:
                                status_badge = " 🚫 ABROGATO" if cited_law.get("status") == "abrogated" else ""
                                st.write(f"**{cited_law.get('title', 'N/A')}**{status_badge}")
                                st.write(f"Type: {cited_law.get('type')}")
                                st.write(f"Year: {cited_law.get('year')}")
                            else:
                                st.write("Dettagli non disponibili nel DB locale.")
                            if cit.get("citing_article"):
                                st.caption(f"Articolo citante: {cit.get('citing_article')}")
                            if context:
                                st.caption(f"Contesto: {context[:250]}")
                            if st.button(
                                "View full law →",
                                key=f"btn-view-{cited_urn}",
                            ):
                                _open_law(cited_urn, source_tab="detail_citations", source_context="incoming")
                        except Exception as e:
                            logger.warning("Failed rendering incoming citation %s: %s", cited_urn, e)
                            st.write(f"Details not available")
                if len(cited_by) > 20:
                    st.caption(f"... and {len(cited_by) - 20} more")
            else:
                st.info("No incoming citations found.")
        
        with col2:
            st.write("**Laws that THIS LAW CITES** (outgoing citations)")
            cites = db.get_citations_outgoing(urn, limit=50)
            if cites:
                st.write(f"✓ Cites **{len(cites)}** laws")
                for cit in cites[:20]:
                    cited_urn = cit.get("cited_urn") or cit.get("urn")
                    context = cit.get("context", "")
                    context_preview = f" _{context[:80]}..._" if context else ""
                    with st.expander(f"📌 {cited_urn[:50]}{context_preview}"):
                        try:
                            ref_law = db.get_law(cited_urn)
                            if ref_law:
                                status_badge = " 🚫 ABROGATO" if ref_law.get("status") == "abrogated" else ""
                                st.write(f"**{ref_law.get('title', 'N/A')}**{status_badge}")
                                st.write(f"Type: {ref_law.get('type')}")
                                st.write(f"Year: {ref_law.get('year')}")
                            else:
                                st.write("Dettagli non disponibili nel DB locale.")
                            if cit.get("cited_article"):
                                st.caption(f"Articolo citato: {cit.get('cited_article')}")
                            if context:
                                st.caption(f"Contesto: {context[:250]}")
                            if st.button(
                                "View dependency →",
                                key=f"btn-dep-{cited_urn}",
                            ):
                                _open_law(cited_urn, source_tab="detail_citations", source_context="outgoing")
                        except Exception as e:
                            logger.warning("Failed rendering outgoing citation %s: %s", cited_urn, e)
                            st.write(f"Details not available")
                if len(cites) > 20:
                    st.caption(f"... and {len(cites) - 20} more")
            else:
                st.info("No outgoing citations found.")

    with tab3:
        """Related laws via co-citation and domain"""
        st.subheader("📚 Related Laws (Contextually Relevant)")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.write("**Laws in the Same Legal Domain**")
            try:
                meta = db.conn.execute(
                    "SELECT domain_cluster FROM law_metadata WHERE urn = ?", (urn,)
                ).fetchone()
                if meta and meta[0]:
                    domain = meta[0]
                    same_domain = db.conn.execute(
                        "SELECT l.urn, l.title, l.year, m.domain_cluster FROM laws l "
                        "LEFT JOIN law_metadata m ON l.urn = m.urn "
                        "WHERE m.domain_cluster = ? AND l.urn != ? "
                        "ORDER BY l.importance_score DESC LIMIT 15",
                        (domain, urn)
                    ).fetchall()
                    if same_domain:
                        for law_ref in same_domain:
                            if col1.button(
                                f"📖 {law_ref[1][:50]} ({law_ref[2]})",
                                key=f"domain-{law_ref[0]}",
                            ):
                                _open_law(law_ref[0], source_tab="detail_related", source_context="domain")
                    else:
                        st.info("No other laws in this domain.")
                else:
                    st.info("Domain classification not available.")
            except Exception as e:
                st.info(f"Domain analysis unavailable: {e}")
        
        with col2:
            st.write("**Co-Citation Network (Laws Frequently Cited Together)**")
            try:
                related = db.find_related_laws(urn, limit=15)
                if related:
                    for r in related[:10]:
                        if col2.button(
                            f"📖 {r.get('title', 'N/A')[:50]}",
                            key=f"related-{r['urn']}",
                        ):
                            _open_law(r['urn'], source_tab="detail_related", source_context="co_citation")
                    if len(related) > 10:
                        st.caption(f"... and {len(related) - 10} more co-cited laws")
                else:
                    st.info("No related laws found via co-citation.")
            except Exception as e:
                logger.warning("Co-citation analysis failed for %s: %s", urn, e)
                st.info("Co-citation analysis not available yet.")

    with tab4:
        """Amendment history and evolution"""
        st.subheader("⚖️ Amendment & Modification History")
        try:
            amendments = db.get_amendment_timeline(urn)
            if amendments:
                st.write(f"**{len(amendments)}** modifications recorded:")
                for amend in amendments:
                    with st.expander(f"📝 {amend.get('amendment_date', 'Unknown')} - {amend.get('amendment_type', 'modified')}"):
                        st.write(f"**Modifying law**: {amend.get('amending_urn')}")
                        st.write(f"**Type**: {amend.get('amendment_type')}")
                        if amend.get('description'):
                            st.write(f"**Details**: {amend['description']}")
            else:
                st.info("No amendment history recorded for this law.")
        except Exception as e:
            st.info(f"Amendment history not available: {e}")

    with tab5:
        """Citation graph visualization"""
        st.subheader("🎯 Citation Context Graph (Connected Laws)")
        try:
            neighborhood = db.get_citation_neighborhood(urn, depth=2, max_nodes=50)
            if neighborhood and neighborhood.get("nodes") and len(neighborhood["nodes"]) > 0:
                _render_graph_plotly(
                    neighborhood["nodes"], 
                    neighborhood["edges"],
                    title=(f"Citation network of {law.get('title', urn)[:50]}")
                )
                st.caption(
                    f"Graph shows {len(neighborhood['nodes'])} laws connected via citations "
                    f"(depth 2, up to 50 nodes)"
                )
            else:
                st.info("No citation graph data available for this law.")
        except Exception as e:
            st.info(f"Graph visualization not available: {e}")

    with tab6:
        """Structured article view"""
        st.subheader("§ Articoli Estratti")
        try:
            articles = db.get_articles(urn)
        except Exception:
            articles = []

        if not articles:
            st.info("Nessun articolo strutturato disponibile per questa legge.")
            if st.button("Estrai articoli ora", key=f"extract-articles-{urn}"):
                extracted = db.parse_and_insert_articles(urn, law.get("text", "") or "")
                st.success(f"Articoli estratti: {extracted}")
                st.rerun()
        else:
            st.caption(f"Articoli strutturati disponibili: {len(articles)}")
            search_article = st.text_input(
                "Filtra per numero articolo (es. 1, 2-bis):",
                key=f"article-filter-{urn}",
                placeholder="Inserisci numero articolo"
            ).strip().lower()
            visible = articles
            if search_article:
                visible = [
                    a for a in articles
                    if str(a.get("article_num", "")).lower() == search_article
                ]
            if not visible:
                st.info("Nessun articolo corrisponde al filtro.")
            for a in visible[:80]:
                art_num = a.get("article_num", "?")
                heading = a.get("heading", "")
                label = f"Art. {art_num}"
                if heading:
                    label += f" - {heading[:80]}"
                with st.expander(label):
                    body = a.get("text", "")
                    if body:
                        st.text_area(
                            f"Art. {art_num}",
                            body,
                            height=220,
                            disabled=True,
                            key=f"article-body-{urn}-{a.get('id')}"
                        )
                    else:
                        st.caption("Contenuto articolo non disponibile.")
            if len(visible) > 80:
                st.caption(f"Mostrati primi 80 articoli su {len(visible)}.")


def page_citations():
    st.header("\U0001f517 Citation Network")
    db = load_db()

    if db:
        st.subheader("Most Cited Laws")
        try:
            top = db.conn.execute(
                "SELECT l.urn, l.title, l.year, COUNT(*) AS cited_by "
                "FROM citations c "
                "JOIN laws l ON l.urn = c.cited_urn "
                "GROUP BY l.urn, l.title, l.year "
                "ORDER BY cited_by DESC LIMIT 25"
            ).fetchall()
            if top:
                df = pd.DataFrame([dict(r) for r in top])
                df.columns = ["URN", "Title", "Year", "Cited By"]
                df["Title"] = df["Title"].str[:50]
                fig = px.bar(df, x="Title", y="Cited By",
                             title="Top 25 Most Cited Laws",
                             hover_data=["URN", "Year"])
                st.plotly_chart(fig, width='stretch')
                st.dataframe(df, width='stretch', hide_index=True)
                top_urn = st.selectbox(
                    "Apri una legge citata:",
                    [r["URN"] for r in df.to_dict("records")],
                    key="cit-top-open-urn"
                )
                if st.button("📖 Open selected cited law", key="cit-top-open-btn"):
                    _open_law(top_urn, source_tab="citations", source_context="top_cited")
        except Exception as e:
            st.warning(f"Error loading citation data: {e}")

        st.subheader("Citation Graph Explorer")
        urn_input = st.text_input(
            "Enter a law URN to explore its citation neighborhood:"
        )
        depth = st.slider("Graph depth", 1, 3, 2)
        max_n = st.slider("Max nodes", 10, 100, 40)
        if urn_input:
            try:
                neighborhood = db.get_citation_neighborhood(
                    urn_input, depth=depth, max_nodes=max_n
                )
                if neighborhood and neighborhood.get("nodes"):
                    _render_graph_plotly(
                        neighborhood["nodes"], neighborhood["edges"],
                        title=f"Neighborhood of {urn_input}"
                    )
                else:
                    st.warning("No graph data for this URN.")
            except Exception as e:
                st.error(f"Graph error: {e}")

        st.subheader("Cross-Domain Citations")
        try:
            cross = db.conn.execute("""
                SELECT m1.domain_cluster as from_domain,
                       m2.domain_cluster as to_domain,
                       COUNT(*) as cnt
                FROM citations c
                JOIN law_metadata m1 ON c.citing_urn = m1.urn
                JOIN law_metadata m2 ON c.cited_urn = m2.urn
                WHERE m1.domain_cluster IS NOT NULL
                  AND m2.domain_cluster IS NOT NULL
                  AND m1.domain_cluster != ''
                  AND m2.domain_cluster != ''
                GROUP BY m1.domain_cluster, m2.domain_cluster
                ORDER BY cnt DESC LIMIT 30
            """).fetchall()
            if cross:
                df = pd.DataFrame([dict(r) for r in cross])
                fig = px.treemap(
                    df, path=["from_domain", "to_domain"], values="cnt",
                    title="How Legal Domains Reference Each Other"
                )
                st.plotly_chart(fig, width='stretch')
        except Exception as e:
            logger.warning("Cross-domain citation view failed: %s", e)
            st.info("Cross-domain citation view temporarily unavailable.")
    else:
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
            fig = px.bar(df, x="URN", y="Times Cited",
                         title="Most Referenced Laws")
            st.plotly_chart(fig, width='stretch')


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
        st.info("Domain data not available.")
        return

    if not domains:
        st.info("No domain data available.")
        return

    domain_names = [d[0] for d in domains]
    domain_counts = [d[1] for d in domains]

    total_laws = db.conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    covered = sum(domain_counts)
    coverage_pct = (covered / total_laws * 100) if total_laws else 0
    m1, m2, m3 = st.columns(3)
    m1.metric("Classified Laws", f"{covered:,}")
    m2.metric("Total Laws", f"{total_laws:,}")
    m3.metric("Domain Coverage", f"{coverage_pct:.2f}%")
    if coverage_pct < 95:
        st.warning(
            "Domain coverage is below expected range. Recompute domains to align "
            "labels with current DB text/title content."
        )
    if st.button("♻️ Recompute Domains From DB"):
        with st.spinner("Recomputing domains from DB text/title..."):
            db.detect_law_domains()
        st.success("Domain recomputation completed.")
        st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        fig = px.pie(names=domain_names, values=domain_counts,
                     title="Distribution of Legal Domains", hole=0.3)
        st.plotly_chart(fig, width='stretch')
    with col2:
        fig = px.bar(x=domain_names, y=domain_counts, title="Laws per Domain",
                     labels={"x": "Domain", "y": "Count"})
        st.plotly_chart(fig, width='stretch')

    selected_domain = st.selectbox("Explore domain:", domain_names)
    if selected_domain:
        total_domain = db.conn.execute(
            "SELECT COUNT(*) FROM law_metadata WHERE domain_cluster = ?",
            (selected_domain,),
        ).fetchone()[0]
        laws_in_domain = db.conn.execute("""
            SELECT l.urn, l.title, l.year, l.type, l.importance_score
            FROM laws l JOIN law_metadata m ON l.urn = m.urn
            WHERE m.domain_cluster = ?
            ORDER BY l.importance_score DESC NULLS LAST
            LIMIT 50
        """, (selected_domain,)).fetchall()
        if laws_in_domain:
            df = pd.DataFrame([dict(r) for r in laws_in_domain])
            df.columns = ["URN", "Title", "Year", "Type", "Importance"]
            df["Title"] = df["Title"].str[:60]
            df["Importance"] = df["Importance"].apply(
                lambda x: f"{x:.4f}" if x else "N/A"
            )
            st.write(
                f"**Showing {len(laws_in_domain)} of {total_domain} laws** in domain "
                f"_{selected_domain}_:"
            )
            st.dataframe(df, width='stretch', hide_index=True)
            st.markdown("**Top linked laws in this domain**")
            for law_d in [dict(r) for r in laws_in_domain[:8]]:
                _render_law_card(law_d, db, key_prefix=f"domain-{selected_domain}")


def page_notifications():
    """API change notifications -- read-only monitoring of Normattiva updates."""
    st.header("\U0001f514 Notifications")
    st.caption(
        "Monitors the Normattiva API for new or updated collections using "
        "ETags. This is **read-only** -- no data is modified. When you "
        "see changes, you can manually update the dataset and log it in "
        "the Update Log."
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
            error = _monitor_state["error"]

        if checking:
            st.info("\u23f3 Checking API for changes...")
        elif error:
            st.error(f"Last check failed: {error}")
        elif last:
            st.success(f"Last checked: {last}")
        else:
            st.info(
                "No check performed yet. Click the button above to poll "
                "the Normattiva API for collection changes."
            )

    if pending:
        st.warning(
            f"\u26a0\ufe0f **{len(pending)} collection(s) have changed** "
            f"since the last dataset build!"
        )
        st.markdown(
            "Review the changes below. When ready, manually download the "
            "updated collections, rebuild the DB, and record it in the "
            "**Update Log**."
        )
        for ch in pending:
            is_new = ch.get('is_new', False)
            icon = "\U0001f195" if is_new else "\U0001f504"
            with st.expander(f"{icon} {ch['collection']}"):
                c1, c2 = st.columns(2)
                with c1:
                    st.write(f"**Collection**: {ch['collection']}")
                    st.write(f"**Detected**: {ch['detected_at']}")
                    if ch.get('num_acts'):
                        st.write(
                            f"**Acts in collection**: {ch['num_acts']}"
                        )
                with c2:
                    if is_new:
                        st.info("First time seeing this collection")
                    else:
                        old = ch.get('old_etag', 'N/A')
                        new = ch.get('new_etag', 'N/A')
                        st.write(f"**Previous ETag**: `{old[:20]}...`")
                        st.write(f"**New ETag**: `{new[:20]}...`")
    elif last:
        st.success(
            "\u2705 All collections up to date -- no changes detected."
        )

    # How to update instructions
    st.divider()
    st.subheader("How to Update the Dataset")
    st.markdown("""
When new laws are detected:

1. **Download** the updated collections locally:
   ```
   python download_normattiva.py --collections "CollectionName"
   ```
2. **Rebuild** the database:
   ```
   python production_build.py --enrich
   ```
3. **Record** the update in the Update Log page
4. **Redeploy** the Space with the new DB:
   ```
   python deploy_hf.py
   ```

This keeps you in full control of what enters the dataset.
    """)


def page_update_log():
    """Manual update log -- tracks when the dataset was updated."""
    st.header("\U0001f4dd Update Log")
    st.caption(
        "Track when the dataset was manually updated. Each entry records "
        "what changed, how many laws were added, and any notes."
    )

    db = load_db()
    if not db:
        st.warning("Database required for update log.")
        return

    # Nightly automation status (GitHub Actions)
    st.subheader("🌙 Nightly Pipeline Status")
    cbtn, csum = st.columns([1, 3])
    with cbtn:
        if st.button("Refresh nightly status"):
            _get_nightly_pipeline_runs.clear()
            st.rerun()

    nightly = _get_nightly_pipeline_runs(limit=8)
    if nightly:
        failed = sum(1 for r in nightly if r.get("conclusion") == "failure")
        success = sum(1 for r in nightly if r.get("conclusion") == "success")
        with csum:
            st.caption(
                f"Recent runs: {len(nightly)} | ✅ {success} success | ❌ {failed} failure"
            )

        nrows = []
        for r in nightly:
            failure_detail = ""
            if r.get("failed_step"):
                failure_detail = f"{r.get('failed_job','')} -> {r.get('failed_step','')}"
            elif r.get("failed_job"):
                failure_detail = r.get("failed_job", "")
            nrows.append({
                "Date": r.get("created_at", ""),
                "Event": r.get("event", ""),
                "Status": r.get("status", ""),
                "Conclusion": r.get("conclusion", ""),
                "Failure Detail": failure_detail,
                "Run": r.get("html_url", ""),
            })
        st.dataframe(pd.DataFrame(nrows), width='stretch', hide_index=True)
        latest = nightly[0]
        if latest.get("conclusion") == "failure":
            detail = ""
            if latest.get("failed_step"):
                detail = f" (step: {latest.get('failed_step')})"
            st.warning(
                f"Latest nightly run failed: {latest.get('failed_job', 'unknown job')}{detail}"
            )
    else:
        st.info("Nightly pipeline status temporarily unavailable.")

    # Show existing log entries
    log_entries = _get_update_log(db)

    if log_entries:
        st.subheader("Update History")
        rows = []
        for entry in log_entries:
            rows.append({
                "Date": entry.get("timestamp", "?"),
                "Action": entry.get("action", "?"),
                "Description": entry.get("description", ""),
                "Laws Before": entry.get("laws_before") or "",
                "Laws After": entry.get("laws_after") or "",
                "Note": entry.get("user_note") or "",
            })
        st.dataframe(
            pd.DataFrame(rows), width='stretch', hide_index=True
        )

        # Summary
        total_updates = len(log_entries)
        last_update = log_entries[0].get("timestamp", "Never")
        c1, c2 = st.columns(2)
        c1.metric("Total Updates", total_updates)
        c2.metric(
            "Last Update",
            last_update[:10] if last_update != "Never" else "Never"
        )
    else:
        st.info(
            "No updates recorded yet. Use the form below to record "
            "your first update."
        )

    # Record new update
    st.divider()
    st.subheader("Record a New Update")
    with st.form("record_update"):
        action = st.selectbox("Action", [
            "initial_load",
            "incremental_update",
            "full_rebuild",
            "collection_added",
            "enrichment_rerun",
            "bug_fix",
            "other",
        ])
        description = st.text_input(
            "Description",
            placeholder="e.g. Added 150 new laws from Leggi collection"
        )
        col1, col2 = st.columns(2)
        with col1:
            laws_before = st.number_input(
                "Laws before update", min_value=0, value=0
            )
        with col2:
            laws_after = st.number_input(
                "Laws after update", min_value=0, value=0
            )
        collections = st.text_input(
            "Collections affected (comma-separated)",
            placeholder="e.g. Leggi, DPR, Decreti Legislativi"
        )
        user_note = st.text_area(
            "Notes", placeholder="Any additional context..."
        )

        submitted = st.form_submit_button("Record Update")
        if submitted:
            if not description:
                st.error("Please provide a description.")
            else:
                coll_list = (
                    [c.strip() for c in collections.split(",") if c.strip()]
                    if collections else None
                )
                _record_update_log(
                    db, action, description,
                    laws_before=laws_before if laws_before > 0 else None,
                    laws_after=laws_after if laws_after > 0 else None,
                    collections=coll_list,
                    user_note=user_note if user_note else None,
                )
                st.success("Update recorded!")
                st.rerun()


def page_export():
    st.header("\U0001f4e5 Export Data")
    db = load_db()

    st.subheader("Export Options")

    col1, col2 = st.columns(2)
    with col1:
        st.write("**CSV Export** -- Spreadsheet of all laws with metadata")
        if st.button("Generate CSV"):
            if db:
                try:
                    csv_path = db.export_csv(Path("/tmp/normattiva_export.csv"))
                    with open(csv_path, "r", encoding="utf-8") as f:
                        csv_data = f.read()
                    st.download_button(
                        "Download CSV", csv_data,
                        "normattiva_laws.csv", "text/csv"
                    )
                except Exception as e:
                    st.error(f"Export error: {e}")
            else:
                laws = load_laws_from_jsonl()
                if laws:
                    df = pd.DataFrame(laws)
                    st.download_button(
                        "Download CSV", df.to_csv(index=False),
                        "normattiva_laws.csv", "text/csv"
                    )

    with col2:
        st.write(
            "**Citation Graph JSON** -- Network data for visualization tools"
        )
        if st.button("Generate Graph JSON"):
            if db:
                try:
                    json_path = db.export_graph_json(
                        Path("/tmp/normattiva_graph.json")
                    )
                    with open(json_path, "r", encoding="utf-8") as f:
                        json_data = f.read()
                    st.download_button(
                        "Download Graph JSON", json_data,
                        "citation_graph.json", "application/json"
                    )
                except Exception as e:
                    st.error(f"Export error: {e}")
            else:
                st.info("Database required for graph export.")

    # JSONL download
    st.subheader("Raw Data")
    paths = [
        Path('data/processed/laws_vigente.jsonl'),
        Path('/app/data/processed/laws_vigente.jsonl'),
        Path(__file__).parent.parent / 'data' / 'processed' / 'laws_vigente.jsonl',
        Path('/tmp/normattiva_data/processed/laws_vigente.jsonl'),
    ]
    for p in paths:
        try:
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = f.read()
                st.download_button(
                    "Download JSONL (raw)", data,
                    "laws_vigente.jsonl", "application/jsonlines"
                )
                st.write(f"File size: {len(data)/1e6:.1f} MB")
                break
        except Exception:
            pass

    # Data quality
    if db:
        st.subheader("Data Quality Report")
        if st.button("Run Validation"):
            try:
                report = db.validate_data()
                st.json(report)
            except Exception as e:
                st.error(f"Validation error: {e}")


# ─────────────────────────────────────────────────────────────────
# COSTITUZIONE & CODICI — Jurisprudential Framework Pages
# ─────────────────────────────────────────────────────────────────

# Known URN patterns for cornerstone Italian law documents
_CONST_URN_CANDIDATES = [
    "urn:nir:stato:costituzione:1947-12-27",
    "urn:nir:stato:costituzione:1948-01-01",
    "urn:nir:stato:costituzione:1947",
    "urn:nir:stato:costituzione:1947;0000",
]

_CODICI = {
    "Codice Civile": {
        "urns": ["urn:nir:stato:regio.decreto:1942-03-16;262"],
        "desc": "Il fondamento del diritto privato italiano: persone, famiglia, proprietà, contratti, obbligazioni, successioni.",
        "emoji": "⚖️",
    },
    "Codice Penale": {
        "urns": ["urn:nir:stato:regio.decreto:1930-10-19;1398"],
        "desc": "Definisce reati e pene in Italia. Contiene la parte generale e i singoli delitti.",
        "emoji": "🔒",
    },
    "Codice di Procedura Civile": {
        "urns": ["urn:nir:stato:regio.decreto:1940-10-28;1443"],
        "desc": "Regola il processo civile italiano: giurisdizione, competenza, atti, sentenze, esecuzione.",
        "emoji": "📋",
    },
    "Codice di Procedura Penale": {
        "urns": ["urn:nir:stato:decreto.del.presidente.della.repubblica:1988-09-22;447"],
        "desc": "Disciplina il processo penale italiano dal 1988: accusa, difesa, dibattimento, appello.",
        "emoji": "🏛️",
    },
    "Testo Unico Bancario": {
        "urns": ["urn:nir:stato:decreto.legislativo:1993-09-01;385"],
        "desc": "Disciplina le banche, i servizi finanziari e la vigilanza bancaria in Italia.",
        "emoji": "🏦",
    },
    "Codice del Consumo": {
        "urns": ["urn:nir:stato:decreto.legislativo:2005-09-06;206"],
        "desc": "Tutela i consumatori: contratti, garanzie, pratiche commerciali scorrette.",
        "emoji": "🛒",
    },
    "Statuto dei Lavoratori": {
        "urns": ["urn:nir:stato:legge:1970-05-20;300"],
        "desc": "Norma fondamentale del diritto del lavoro italiano: rapporto di lavoro, sindacati, licenziamenti.",
        "emoji": "👷",
    },
    "Codice Privacy (GDPR Nazionale)": {
        "urns": ["urn:nir:stato:decreto.legislativo:2003-06-30;196"],
        "desc": "Disciplina il trattamento dei dati personali in Italia, coordinato con il GDPR 2016/679.",
        "emoji": "🔐",
    },
}


@st.cache_data(ttl=3600)
def _find_constitution_urn(_db_path: str) -> str | None:
    """Search for the Constitution URN in the database."""
    db = load_db()
    if not db:
        return None
    # First try: direct type match (most reliable after our download)
    row = db.conn.execute(
        "SELECT urn FROM laws WHERE type = 'COSTITUZIONE' LIMIT 1"
    ).fetchone()
    if row:
        return row[0]
    # Second: exact URN prefix candidates
    for urn in _CONST_URN_CANDIDATES:
        row = db.conn.execute(
            "SELECT urn FROM laws WHERE urn LIKE ? LIMIT 1", (f"{urn}%",)
        ).fetchone()
        if row:
            return row[0]
    # Fallback: search by title
    row = db.conn.execute(
        "SELECT urn FROM laws WHERE LOWER(title) LIKE '%costituzione%italiana%' LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _open_law(urn: str, source_tab: str = "", source_context: str = ""):
    """Global router: open a law in the canonical Law Detail hub."""
    if not urn:
        return
    st.session_state["detail_urn"] = urn
    st.session_state["goto_page"] = "📖 Law Detail"
    if source_tab:
        st.session_state["law_nav_source_tab"] = source_tab
    if source_context:
        st.session_state["law_nav_source_context"] = source_context
    try:
        st.query_params.update({"urn": urn})
    except Exception:
        pass
    st.rerun()


def _render_law_linkage_summary(db, urn: str, key_prefix: str = "linkage"):
    """Small reusable linkage summary for any law across tabs."""
    if not db or not urn:
        return
    try:
        incoming = db.conn.execute(
            "SELECT COUNT(*) FROM citations WHERE cited_urn = ?", (urn,)
        ).fetchone()[0]
        outgoing = db.conn.execute(
            "SELECT COUNT(*) FROM citations WHERE citing_urn = ?", (urn,)
        ).fetchone()[0]
        amendments = db.conn.execute(
            "SELECT COUNT(*) FROM amendments WHERE urn = ?", (urn,)
        ).fetchone()[0]
        articles = db.conn.execute(
            "SELECT COUNT(*) FROM articles WHERE law_urn = ?", (urn,)
        ).fetchone()[0]
    except Exception as e:
        logger.warning("Linkage summary failed for %s: %s", urn, e)
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Cited by", f"{incoming:,}")
    m2.metric("Cites", f"{outgoing:,}")
    m3.metric("Amendments", f"{amendments:,}")
    m4.metric("Articles", f"{articles:,}")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("↗ Explore Incoming", key=f"{key_prefix}-incoming-{urn}"):
            _open_law(urn, source_tab="linkage", source_context="incoming")
    with c2:
        if st.button("↘ Explore Outgoing", key=f"{key_prefix}-outgoing-{urn}"):
            _open_law(urn, source_tab="linkage", source_context="outgoing")


def _render_law_card(law: dict, db, key_prefix: str = ""):
    """Render a compact law card with nav button and citation count."""
    urn = law.get("urn", "")
    title = law.get("title", "N/A")
    year = law.get("year", "")
    law_type = law.get("type", "")
    importance = law.get("importance_score", 0) or 0
    status = law.get("status", "")
    status_badge = " 🚫 *ABROGATO*" if status == "abrogated" else ""

    try:
        incoming = db.conn.execute(
            "SELECT COUNT(*) FROM citations WHERE cited_urn = ?", (urn,)
        ).fetchone()[0]
    except Exception:
        incoming = 0

    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        with c1:
            st.write(f"**{title}**{status_badge}")
            st.caption(f"`{urn}` | {law_type} | {year}")
            if incoming:
                st.caption(f"📎 Citata da {incoming:,} leggi")
        with c2:
            if st.button("Apri →", key=f"{key_prefix}-open-{urn}"):
                _open_law(urn, source_tab="card", source_context=key_prefix)


def _urn_inline_links(text: str, db, max_links: int = 30, key_prefix: str = "urnref") -> bool:
    """
    Find all URN references in law text and render a navigable reference panel
    with a clickable button for each cited law.
    Returns True if any references were found and rendered.
    """
    import re
    pattern = r'urn:nir:[a-zA-Z0-9\.\:\;\-]+'
    found = list(dict.fromkeys(re.findall(pattern, text)))[:max_links]
    if not found:
        return False
    rows = []
    for ref_urn in found:
        try:
            row = db.conn.execute(
                "SELECT title, year, type, status FROM laws WHERE urn = ?", (ref_urn,)
            ).fetchone()
            if row:
                rows.append((ref_urn, row[0], row[1], row[2], row[3]))
        except Exception:
            pass
    if not rows:
        return False
    st.write(f"**Leggi citate nel testo** ({len(rows)} trovate):")
    for ref_urn, title, year, ltype, ref_status in rows:
        abr = " 🚫" if ref_status == "abrogated" else ""
        c1, c2 = st.columns([5, 1])
        with c1:
            st.caption(f"`{ref_urn}` — **{title[:65]}**{abr} | {ltype} | {year}")
        with c2:
            if st.button("Apri", key=f"{key_prefix}-{ref_urn}"):
                _open_law(ref_urn, source_tab="inline_refs", source_context=key_prefix)
    return True


def page_costituzione():
    """Constitution-centric jurisprudential framework explorer."""
    st.header("🇮🇹 Costituzione della Repubblica Italiana")
    db = load_db()
    if not db:
        st.error("Database non disponibile.")
        return

    db_path = str(db.db_path) if hasattr(db, 'db_path') else ""
    const_urn = _find_constitution_urn(db_path)

    if const_urn:
        law_row = db.conn.execute(
            "SELECT * FROM laws WHERE urn = ?", (const_urn,)
        ).fetchone()
        law = dict(law_row) if law_row else {}
    else:
        law = {}

    col1, col2, col3 = st.columns(3)
    col1.metric("Anno di adozione", "1948")
    col2.metric("Articoli", law.get("article_count", "139"))
    if const_urn:
        try:
            citing = db.conn.execute(
                "SELECT COUNT(*) FROM citations WHERE cited_urn = ?", (const_urn,)
            ).fetchone()[0]
            col3.metric("Leggi che la citano", f"{citing:,}")
        except Exception:
            col3.metric("Basamento dell'ordinamento", "Fondamentale")
    else:
        col3.metric("Basamento dell'ordinamento", "Fondamentale")

    st.info(
        "La Costituzione è il vertice della gerarchia delle fonti del diritto italiano. "
        "Tutte le leggi ordinarie, i decreti e i regolamenti devono conformarsi ai suoi principi. "
        "Dalla Costituzione discende l'intero ordinamento giuridico: dalle libertà fondamentali "
        "agli organi dello Stato, dal diritto di difesa alla tutela del lavoro."
    )

    tab_cost, tab_hier, tab_codici, tab_implement, tab_leggi_cost = st.tabs([
        "📜 Testo & Citazioni",
        "🏛️ Gerarchia delle Fonti",
        "📚 I Principali Codici",
        "🔗 Leggi di Attuazione",
        "⚖️ Leggi Costituzionali",
    ])

    with tab_cost:
        if law:
            text = law.get("text", "")
            st.subheader(law.get("title", "Costituzione Italiana"))
            c1, c2 = st.columns([3, 1])
            with c1:
                if text:
                    st.text_area("Testo completo", text, height=500, disabled=True, key="const-text")
                    # Show clickable URN reference panel
                    with st.expander("📎 Leggi richiamate nel testo della Costituzione"):
                        _urn_inline_links(text, db, key_prefix="costituzione")
                else:
                    st.info("Testo non disponibile nel database.")
            with c2:
                st.subheader("Metadati")
                st.write(f"**URN**: `{law.get('urn')}`")
                st.write(f"**Tipo**: {law.get('type')}")
                st.write(f"**Data**: {law.get('date')}")
                st.write(f"**Importanza (PageRank)**: {law.get('importance_score', 0):.4f}")
                if law.get("urn") and st.button("📖 Apri in Law Detail", key="open-constitution-detail"):
                    _open_law(law.get("urn"), source_tab="costituzione", source_context="main")
                _render_law_linkage_summary(db, law.get("urn", ""), key_prefix="costituzione-main")

                st.subheader("Principali Parti")
                st.markdown("""
- **Principi Fondamentali** (artt. 1–12)
- **Diritti e Doveri** (artt. 13–54)
  - *Rapporti civili* (artt. 13–28)
  - *Rapporti etico-sociali* (artt. 29–34)
  - *Rapporti economici* (artt. 35–47)
  - *Rapporti politici* (artt. 48–54)
- **Ordinamento della Repubblica** (artt. 55–139)
  - *Parlamento* (artt. 55–82)
  - *Presidente della Repubblica* (artt. 83–91)
  - *Governo* (artt. 92–100)
  - *Magistratura* (artt. 101–113)
  - *Corte Costituzionale* (artt. 134–137)
""")
        else:
            st.warning(
                "La Costituzione non è stata trovata nel database con gli URN noti. "
                "Usare la ricerca per trovarla: cerca 'Costituzione'."
            )
            if st.button("Cerca 'Costituzione' nel database"):
                results = db.conn.execute(
                    "SELECT urn, title, year FROM laws WHERE LOWER(title) LIKE '%costituzione%' LIMIT 10"
                ).fetchall()
                for r in results:
                    st.write(f"- `{r[0]}` — {r[1]} ({r[2]})")

        # Show laws that cite the constitution
        if const_urn:
            st.divider()
            st.subheader("📎 Principali leggi che citano la Costituzione")
            cited_by = db.conn.execute(
                "SELECT l.urn, l.title, l.year, l.type, l.importance_score "
                "FROM citations c JOIN laws l ON c.citing_urn = l.urn "
                "WHERE c.cited_urn = ? "
                "ORDER BY l.importance_score DESC LIMIT 20",
                (const_urn,)
            ).fetchall()
            if cited_by:
                for row in cited_by:
                    _render_law_card(dict(row), db, key_prefix="const-cited")
            else:
                st.info("Nessuna citazione diretta trovata per la Costituzione.")

    with tab_hier:
        st.subheader("🏛️ La Gerarchia delle Fonti del Diritto Italiano")
        st.markdown("""
La gerarchia delle fonti determina quale norma prevale in caso di conflitto.
Le fonti di rango superiore prevalgono su quelle di rango inferiore.

```
┌─────────────────────────────────────────────────────────┐
│  1.  COSTITUZIONE (1948) + Principi supremi             │  ◄ Vertice
│      ↕ modifica solo con legge costituzionale (2/3)     │
├─────────────────────────────────────────────────────────┤
│  2.  FONTI COMUNITARIE / DIRITTO UE                     │
│      Regolamenti UE (diretta applicabilità)             │
│      Direttive UE (recepite con D.Lgs.)                 │
├─────────────────────────────────────────────────────────┤
│  3.  LEGGI COSTITUZIONALI                               │
│      Es: Statuti Regioni speciali, Trattati int.li      │
├─────────────────────────────────────────────────────────┤
│  4.  LEGGI ORDINARIE  |  ATTI AVENTI FORZA DI LEGGE     │
│      Legge ordinaria   Decreto legge (D.L.)             │
│      Legge delega      Decreto legislativo (D.Lgs.)     │
├─────────────────────────────────────────────────────────┤
│  5.  REGOLAMENTI DEL GOVERNO (D.P.R., D.P.C.M.)        │
├─────────────────────────────────────────────────────────┤
│  6.  ATTI LEGISLATIVI REGIONALI                         │
├─────────────────────────────────────────────────────────┤
│  7.  FONTI SECONDARIE LOCALI                            │  ◄ Base
│      Regolamenti comunali, ordinanze, circolari         │
└─────────────────────────────────────────────────────────┘
```

**Come navigare il database:**
- Le *leggi ordinarie* iniziano con `urn:nir:stato:legge:`
- I *decreti legislativi* iniziano con `urn:nir:stato:decreto.legislativo:`
- I *decreti legge* iniziano con `urn:nir:stato:decreto.legge:`
- I *D.P.R.* iniziano con `urn:nir:stato:decreto.del.presidente.della.repubblica:`
- I *codici* (Civile, Penale, ecc.) sono storicamente *regi decreti*: `urn:nir:stato:regio.decreto:`
""")

        # Show type distribution from DB
        try:
            types = db.conn.execute(
                "SELECT type, COUNT(*) cnt FROM laws GROUP BY type ORDER BY cnt DESC LIMIT 15"
            ).fetchall()
            if types:
                st.subheader("Distribuzione per tipo nel database")
                type_df = pd.DataFrame(types, columns=["Tipo", "Conteggio"])
                fig = px.bar(type_df, x="Tipo", y="Conteggio",
                             title="Leggi per tipo di atto normativo")
                st.plotly_chart(fig, width='stretch')
        except Exception:
            pass

    with tab_codici:
        st.subheader("📚 I Principali Codici e Testi Unici")
        st.markdown(
            "I codici sono raccolte sistematiche di norme che regolano settori fondamentali del diritto. "
            "Ogni codice è collegato alle leggi speciali che ne integrano e modificano le disposizioni."
        )

        for name, info in _CODICI.items():
            with st.expander(f"{info['emoji']} {name}"):
                st.write(info["desc"])
                for urn in info["urns"]:
                    row = db.conn.execute(
                        "SELECT urn, title, year, type, article_count, importance_score "
                        "FROM laws WHERE urn = ? OR urn LIKE ?",
                        (urn, urn + "%")
                    ).fetchone()
                    if row:
                        law_d = dict(row)
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Anno", law_d.get("year"))
                        c2.metric("Articoli", law_d.get("article_count", "N/A"))
                        c3.metric("PageRank", f"{law_d.get('importance_score', 0):.4f}")

                        # Related citing laws
                        try:
                            citing_count = db.conn.execute(
                                "SELECT COUNT(*) FROM citations WHERE cited_urn = ?",
                                (law_d["urn"],)
                            ).fetchone()[0]
                            st.write(f"📎 Citato da **{citing_count:,}** leggi nel database")
                        except Exception:
                            pass

                        if st.button(f"Apri {name} →", key=f"codice-{urn}"):
                            _open_law(law_d["urn"], source_tab="costituzione_codici", source_context=name)
                    else:
                        st.warning(f"Non trovato nel database: `{urn}`")
                        # Fuzzy search
                        alt = db.conn.execute(
                            "SELECT urn, title, year FROM laws WHERE type = 'regio decreto' "
                            "AND year BETWEEN 1930 AND 1945 ORDER BY importance_score DESC LIMIT 5"
                        ).fetchall()
                        if alt and "Civile" in name or "Penale" in name:
                            st.caption("Candidati simili:")
                            for a in alt:
                                st.caption(f"  • `{a[0]}` — {a[1]} ({a[2]})")

    with tab_implement:
        st.subheader("🔗 Leggi di Attuazione Costituzionale")
        st.markdown(
            "Queste sono le principali leggi che attuano i diritti e i principi sanciti dalla Costituzione."
        )

        implementing_laws = [
            ("Diritto di voto e ordinamento elettorale", "legge", 1948, 1975),
            ("Corte Costituzionale", "legge", 1948, 1967),
            ("Statuto dei Lavoratori (art. 1, 4, 35 Cost.)", "legge", 1966, 1975),
            ("Tutela della privacy (art. 15 Cost.)", "decreto legislativo", 1996, 2018),
            ("Codice Antimafia (sicurezza pubblica)", "decreto legislativo", 2007, 2017),
            ("Riforma del diritto di famiglia", "legge", 1970, 1978),
            ("Ordinamento giudiziario", "legge", 1948, 1960),
            ("Diritto alla salute (art. 32 Cost.)", "legge", 1978, 1988),
        ]

        for domain_label, law_type, year_from, year_to in implementing_laws:
            with st.expander(f"📌 {domain_label}"):
                try:
                    rows = db.conn.execute(
                        "SELECT l.urn, l.title, l.year, l.importance_score "
                        "FROM laws l "
                        "WHERE l.type LIKE ? AND l.year BETWEEN ? AND ? "
                        "ORDER BY l.importance_score DESC LIMIT 5",
                        (f"%{law_type}%", year_from, year_to)
                    ).fetchall()
                    if rows:
                        for r in rows:
                            _render_law_card(dict(r), db, key_prefix=f"impl-{year_from}")
                    else:
                        st.info("Nessuna legge trovata con questi criteri.")
                except Exception as e:
                    st.info(f"Query non disponibile: {e}")

    with tab_leggi_cost:
        st.subheader("⚖️ Leggi Costituzionali (art. 138 Cost.)")
        st.markdown(
            "Le leggi costituzionali modificano o integrano la Costituzione e richiedono "
            "una procedura aggravata: doppia approvazione parlamentare a maggioranza assoluta "
            "(o 2/3 per evitare il referendum). Comprendono statuti delle Regioni speciali, "
            "trattati di rango costituzionale e revisioni della Carta."
        )
        try:
            lc_rows = db.conn.execute("""
                SELECT l.urn, l.title, l.year, l.date, l.article_count,
                       l.text_length, l.status, l.importance_score,
                       COALESCE(cit.cnt, 0) AS cited_by
                FROM laws l
                LEFT JOIN (
                    SELECT cited_urn, COUNT(*) AS cnt
                    FROM citations GROUP BY cited_urn
                ) cit ON cit.cited_urn = l.urn
                WHERE UPPER(l.type) = 'LEGGE COSTITUZIONALE'
                ORDER BY l.year DESC
            """).fetchall()
        except Exception as e:
            st.error(f"Errore: {e}")
            lc_rows = []

        if lc_rows:
            lc_df = pd.DataFrame([dict(r) for r in lc_rows])
            total_lc = len(lc_df)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Leggi costituzionali", total_lc)
            c2.metric("Vigenti", int((lc_df["status"] == "in_force").sum()))
            c3.metric("Citazioni ricevute", int(lc_df["cited_by"].sum()))
            c4.metric("Dalla", f"{int(lc_df['year'].min())} – {int(lc_df['year'].max())}")

            st.divider()

            # Timeline
            year_lc = lc_df.groupby("year")["urn"].count().reset_index()
            year_lc.columns = ["Anno", "Leggi"]
            fig_lc = px.bar(year_lc, x="Anno", y="Leggi",
                            title="Leggi costituzionali per anno",
                            color="Leggi", color_continuous_scale="Reds")
            st.plotly_chart(fig_lc, width='stretch')

            st.divider()

            # Citation leaders
            st.subheader("Le più citate dal corpus")
            top_lc = lc_df.nlargest(10, "cited_by")[["title", "year", "cited_by", "article_count", "status"]]
            top_lc["label"] = top_lc["title"].str[:55] + " (" + top_lc["year"].astype(str) + ")"
            top_lc["stato"] = top_lc["status"].map(
                {"in_force": "Vigente", "abrogated": "Abrogata"}
            ).fillna(top_lc["status"])
            fig_lct = px.bar(
                top_lc, x="cited_by", y="label", orientation="h",
                color="stato",
                color_discrete_map={"Vigente": "#4CAF50", "Abrogata": "#f44336"},
                title="Top 10 leggi costituzionali per citazioni ricevute",
                labels={"cited_by": "Citazioni", "label": ""},
            )
            fig_lct.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_lct, width='stretch')

            st.divider()

            # Full table
            st.subheader("Elenco completo")
            lc_display = lc_df[["title", "year", "article_count", "cited_by", "status", "urn"]].copy()
            lc_display["status"] = lc_display["status"].map(
                {"in_force": "✅ Vigente", "abrogated": "❌ Abrogata"}
            ).fillna(lc_display["status"])
            lc_display.columns = ["Titolo", "Anno", "Articoli", "Citazioni", "Stato", "URN"]
            st.dataframe(lc_display.drop(columns=["URN"]), width='stretch', hide_index=True)

            sel_lc = st.selectbox(
                "Apri in Law Detail:",
                lc_df["urn"].tolist(),
                format_func=lambda u: next(
                    (f"{r['year']} — {r['title'][:65]}" for r in lc_df.to_dict("records") if r["urn"] == u), u
                ),
                key="lc-open-sel",
            )
            if st.button("📖 Apri legge costituzionale", key="lc-open-btn"):
                _open_law(sel_lc, source_tab="costituzione_leggi_cost", source_context="table")
        else:
            st.info("Nessuna legge costituzionale trovata nel database.")


# ─────────────────────────────────────────────────────────────────
# CORTE COSTITUZIONALE — JURISPRUDENCE TAB
# ─────────────────────────────────────────────────────────────────

def page_corte_cost():
    """Corte Costituzionale jurisprudence analytics."""
    st.header("⚖️ Corte Costituzionale — Giurisprudenza Costituzionale")

    db = load_db()
    if not db:
        st.error("Database non disponibile.")
        return

    conn = db.conn

    # Check if data exists
    try:
        count = conn.execute("SELECT COUNT(*) FROM sentenze").fetchone()[0]
    except Exception:
        count = 0

    if count == 0:
        st.warning(
            "Il database delle sentenze della Corte Costituzionale è vuoto. "
            "Importa le decisioni eseguendo localmente:"
        )
        st.code("python download_sentenze.py --from-index --resume", language="bash")
        st.info(
            "Il downloader usa gli endpoint pubblici di elenco pronunce e scheda pronuncia "
            "del sito ufficiale [cortecostituzionale.it](https://www.cortecostituzionale.it). "
            "Se l'origine blocca il traffico automatico (captcha/anti-bot), esegui da una rete "
            "consentita e poi rideploya il database su HuggingFace con `deploy_hf.py`."
        )
        st.subheader("Anteprima: ultime decisioni pubblicate")
        # Show a live link to the CC website instead
        st.markdown("""
| Decisione | Tipo | Anno |
|---|---|---|
| [55/2026](https://www.cortecostituzionale.it/actionSchedaPronuncia.do?param_ecli=ECLI:IT:COST:2026:55) | Sentenza | 2026 |
| [54/2026](https://www.cortecostituzionale.it/actionSchedaPronuncia.do?param_ecli=ECLI:IT:COST:2026:54) | Sentenza | 2026 |
| [52/2026](https://www.cortecostituzionale.it/actionSchedaPronuncia.do?param_ecli=ECLI:IT:COST:2026:52) | Sentenza | 2026 |
""")
        return

    # ── KPI ROW ──────────────────────────────────────────────────
    try:
        stats = conn.execute("""
            SELECT
                COUNT(*) AS tot,
                SUM(tipo = 'Sentenza') AS sentenze,
                SUM(tipo = 'Ordinanza') AS ordinanze,
                SUM(esito = 'illegittimità') AS illegittimita,
                MIN(anno) AS first_year,
                MAX(anno) AS last_year
            FROM sentenze
        """).fetchone()
        tot, sent, ordin, illegit, first_y, last_y = (
            stats[0], stats[1] or 0, stats[2] or 0,
            stats[3] or 0, stats[4], stats[5]
        )
    except Exception as e:
        st.error(f"Errore lettura dati: {e}")
        return

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📋 Decisioni totali", f"{tot:,}")
    c2.metric("📜 Sentenze", f"{sent:,}")
    c3.metric("📄 Ordinanze", f"{ordin:,}")
    c4.metric("🚫 Dichiarazioni illegittimità", f"{illegit:,}")
    c5.metric("📅 Periodo", f"{first_y}–{last_y}")

    st.divider()

    # ── TEMPORAL TREND ───────────────────────────────────────────
    st.subheader("📅 Attività decisionale per anno")
    try:
        year_data = conn.execute("""
            SELECT anno, tipo, COUNT(*) AS cnt
            FROM sentenze
            GROUP BY anno, tipo
            ORDER BY anno
        """).fetchall()
        if year_data:
            df_yr = pd.DataFrame([dict(r) for r in year_data])
            fig_yr = px.bar(
                df_yr, x="anno", y="cnt", color="tipo",
                title="Decisioni della Corte Costituzionale per anno e tipo",
                labels={"anno": "Anno", "cnt": "Decisioni", "tipo": "Tipo"},
                barmode="stack",
                color_discrete_map={"Sentenza": "#1976D2", "Ordinanza": "#90CAF9"},
            )
            st.plotly_chart(fig_yr, width='stretch')
    except Exception as e:
        logger.warning("CC temporal chart: %s", e)

    st.divider()

    # ── ESITO DISTRIBUTION ───────────────────────────────────────
    col_e1, col_e2 = st.columns(2)

    with col_e1:
        st.subheader("📊 Distribuzione degli esiti")
        try:
            esito_data = conn.execute("""
                SELECT esito, COUNT(*) AS cnt
                FROM sentenze
                WHERE esito IS NOT NULL AND esito != ''
                GROUP BY esito ORDER BY cnt DESC
            """).fetchall()
            if esito_data:
                df_esito = pd.DataFrame([dict(r) for r in esito_data])
                fig_esito = px.pie(
                    df_esito, names="esito", values="cnt",
                    title="Esiti delle decisioni",
                    color_discrete_sequence=px.colors.qualitative.Set2,
                )
                st.plotly_chart(fig_esito, width='stretch')
        except Exception as e:
            logger.warning("CC esito pie: %s", e)

    with col_e2:
        st.subheader("🚫 Illegittimità per anno")
        try:
            illeg_yr = conn.execute("""
                SELECT anno, COUNT(*) AS cnt
                FROM sentenze
                WHERE esito = 'illegittimità'
                GROUP BY anno ORDER BY anno
            """).fetchall()
            if illeg_yr:
                df_il = pd.DataFrame([dict(r) for r in illeg_yr])
                fig_il = px.area(
                    df_il, x="anno", y="cnt",
                    title="Dichiarazioni di illegittimità costituzionale per anno",
                    labels={"anno": "Anno", "cnt": "Dichiarazioni"},
                    color_discrete_sequence=["#f44336"],
                )
                st.plotly_chart(fig_il, width='stretch')
        except Exception as e:
            logger.warning("CC illegit chart: %s", e)

    st.divider()

    # ── CONSTITUTIONAL ARTICLES MOST CHALLENGED ──────────────────
    st.subheader("📜 Articoli della Costituzione più invocati")
    try:
        art_rows = conn.execute("SELECT articoli_cost FROM sentenze WHERE articoli_cost != '[]'").fetchall()
        from collections import Counter
        art_counter: Counter = Counter()
        for row in art_rows:
            try:
                arts = json.loads(row[0])
                art_counter.update(arts)
            except Exception:
                pass
        if art_counter:
            top_arts = art_counter.most_common(20)
            df_arts = pd.DataFrame(top_arts, columns=["Articolo", "Citazioni"])
            df_arts["label"] = "Art. " + df_arts["Articolo"]
            fig_arts = px.bar(
                df_arts, x="Citazioni", y="label", orientation="h",
                title="Articoli costituzionali più richiamati nelle decisioni CC",
                labels={"Citazioni": "Volte citato", "label": ""},
                color="Citazioni", color_continuous_scale="Blues",
            )
            fig_arts.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_arts, width='stretch')
        else:
            st.info("Dati sugli articoli non ancora estratti (richiede download completo).")
    except Exception as e:
        logger.warning("CC articles chart: %s", e)

    st.divider()

    # ── SEARCHABLE DECISION TABLE ─────────────────────────────────
    st.subheader("🔍 Ricerca nelle decisioni")
    col_s1, col_s2, col_s3 = st.columns([2, 1, 1])
    with col_s1:
        q_text = st.text_input("Cerca nel testo / oggetto", key="cc-search")
    with col_s2:
        q_tipo = st.selectbox("Tipo", ["Tutti", "Sentenza", "Ordinanza"], key="cc-tipo")
    with col_s3:
        q_esito = st.selectbox("Esito", ["Tutti", "illegittimità", "inammissibile", "non fondata", "fondata"], key="cc-esito")

    try:
        where_clauses = ["1=1"]
        params: list = []
        if q_text:
            where_clauses.append("(LOWER(oggetto) LIKE ? OR LOWER(testo) LIKE ?)")
            params += [f"%{q_text.lower()}%", f"%{q_text.lower()}%"]
        if q_tipo != "Tutti":
            where_clauses.append("tipo = ?")
            params.append(q_tipo)
        if q_esito != "Tutti":
            where_clauses.append("esito = ?")
            params.append(q_esito)

        results = conn.execute(f"""
            SELECT ecli, numero, anno, tipo, data_deposito, oggetto, esito, comunicato_url
            FROM sentenze
            WHERE {' AND '.join(where_clauses)}
            ORDER BY anno DESC, numero DESC
            LIMIT 200
        """, params).fetchall()

        if results:
            df_res = pd.DataFrame([dict(r) for r in results])
            df_res["Collegamento"] = df_res["ecli"].apply(
                lambda e: f"https://www.cortecostituzionale.it/actionSchedaPronuncia.do?param_ecli={e}"
            )
            display_cols = ["anno", "numero", "tipo", "esito", "data_deposito", "oggetto"]
            st.dataframe(df_res[display_cols].rename(columns={
                "anno": "Anno", "numero": "N.", "tipo": "Tipo",
                "esito": "Esito", "data_deposito": "Deposito", "oggetto": "Oggetto",
            }), width='stretch', hide_index=True)
            st.caption(f"{len(results):,} risultati")

            if not df_res.empty:
                sel_ecli = st.selectbox(
                    "Apri sul sito della Corte:",
                    df_res["ecli"].tolist(),
                    format_func=lambda e: f"{e.split(':')[-2]}/{e.split(':')[-1]} — {next((r['oggetto'][:60] for r in df_res.to_dict('records') if r['ecli'] == e), '')}",
                    key="cc-open-sel",
                )
                url_sel = f"https://www.cortecostituzionale.it/actionSchedaPronuncia.do?param_ecli={sel_ecli}"
                st.markdown(f"🔗 [Apri decisione sul sito ufficiale]({url_sel})")
        else:
            st.info("Nessuna decisione trovata con questi criteri.")
    except Exception as e:
        st.error(f"Errore ricerca: {e}")


# ─────────────────────────────────────────────────────────────────
# LEGGE DI BILANCIO — FISCAL / FINANCIAL LAW ANALYTICS
# ─────────────────────────────────────────────────────────────────

_BILANCIO_KEYWORDS = [
    "bilancio", "finanziaria", "legge di bilancio",
    "manovra", "stabilità", "collegato fiscale",
]

_BILANCIO_SQL_FILTER = """(
    LOWER(l.type) LIKE '%bilancio%'
    OR LOWER(l.title) LIKE '%legge di bilancio%'
    OR LOWER(l.title) LIKE '%legge finanziaria%'
    OR LOWER(l.title) LIKE '%manovra finanziaria%'
    OR LOWER(l.title) LIKE '%collegato fiscale%'
    OR LOWER(l.title) LIKE '%stabilità%'
)"""


def _bilancio_laws(conn) -> list:
    """Return all budget/financial law rows."""
    rows = conn.execute(f"""
        SELECT l.urn, l.title, l.type, l.year, l.date,
               l.article_count, l.text_length, l.status,
               COALESCE(m.domain_cluster, '') AS domain,
               COALESCE(cit.cited_by, 0) AS cited_by
        FROM laws l
        LEFT JOIN law_metadata m ON m.urn = l.urn
        LEFT JOIN (
            SELECT cited_urn, COUNT(*) AS cited_by
            FROM citations
            GROUP BY cited_urn
        ) cit ON cit.cited_urn = l.urn
        WHERE {_BILANCIO_SQL_FILTER}
        ORDER BY l.year DESC
    """).fetchall()
    return [dict(r) for r in rows]


def page_bilancio():
    """Legge di Bilancio — fiscal & financial law analytics dashboard."""
    st.header("💰 Legge di Bilancio — Analisi Fiscale e Finanziaria")
    db = load_db()
    if not db:
        st.error("Database non disponibile.")
        return

    conn = db.conn

    with st.spinner("Caricamento dati fiscali…"):
        laws = _bilancio_laws(conn)

    if not laws:
        st.warning("Nessuna legge di bilancio trovata nel database.")
        return

    df_all = pd.DataFrame(laws)

    # ── KPI ROW ──────────────────────────────────────────────────
    total = len(df_all)
    vigente = (df_all["status"] == "in_force").sum()
    abrogata = total - vigente
    tot_articles = int(df_all["article_count"].fillna(0).sum())
    tot_chars = int(df_all["text_length"].fillna(0).sum())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📜 Totale leggi", f"{total:,}")
    c2.metric("✅ Vigenti", f"{vigente:,}")
    c3.metric("❌ Abrogate", f"{abrogata:,}")
    c4.metric("📑 Articoli totali", f"{tot_articles:,}")
    c5.metric("📄 Caratteri totali", f"{tot_chars/1_000_000:.1f}M")

    st.divider()

    # ── TEMPORAL TREND ───────────────────────────────────────────
    st.subheader("📅 Produzione legislativa per anno")
    year_counts = (
        df_all.dropna(subset=["year"])
        .groupby("year")
        .agg(count=("urn", "count"), articles=("article_count", "sum"))
        .reset_index()
    )
    year_counts["year"] = year_counts["year"].astype(int)
    year_counts = year_counts[year_counts["year"] >= 1948].sort_values("year")

    if not year_counts.empty:
        tab_trend1, tab_trend2 = st.tabs(["Numero leggi", "Articoli emanati"])
        with tab_trend1:
            fig = px.bar(
                year_counts, x="year", y="count",
                title="Leggi di Bilancio / Finanziarie per anno",
                labels={"year": "Anno", "count": "Leggi emanate"},
                color="count",
                color_continuous_scale="Blues",
            )
            fig.update_layout(showlegend=False)
            st.plotly_chart(fig, width='stretch')
        with tab_trend2:
            fig2 = px.area(
                year_counts, x="year", y="articles",
                title="Articoli nelle leggi di bilancio per anno",
                labels={"year": "Anno", "articles": "Articoli"},
                color_discrete_sequence=["#2196F3"],
            )
            st.plotly_chart(fig2, width='stretch')

    st.divider()

    # ── STATUS PIE + COMPLEXITY TOP ──────────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("🟢 Vigenti vs Abrogate")
        status_df = df_all["status"].value_counts().reset_index()
        status_df.columns = ["status", "count"]
        status_df["label"] = status_df["status"].map(
            {"in_force": "Vigente", "abrogated": "Abrogata"}
        ).fillna(status_df["status"])
        fig_pie = px.pie(
            status_df, names="label", values="count",
            title="Stato delle leggi di bilancio",
            color="label",
            color_discrete_map={"Vigente": "#4CAF50", "Abrogata": "#f44336"},
        )
        st.plotly_chart(fig_pie, width='stretch')

    with col_right:
        st.subheader("📊 Leggi per complessità (articoli)")
        top_complex = (
            df_all[df_all["article_count"] > 0]
            .nlargest(15, "article_count")[["title", "year", "article_count", "status"]]
            .copy()
        )
        top_complex["label"] = top_complex["title"].str[:45] + " (" + top_complex["year"].astype(str) + ")"
        top_complex["stato"] = top_complex["status"].map(
            {"in_force": "Vigente", "abrogated": "Abrogata"}
        ).fillna(top_complex["status"])
        fig_comp = px.bar(
            top_complex, x="article_count", y="label",
            orientation="h",
            color="stato",
            color_discrete_map={"Vigente": "#4CAF50", "Abrogata": "#f44336"},
            title="Le 15 più complesse per numero di articoli",
            labels={"article_count": "Articoli", "label": ""},
        )
        fig_comp.update_layout(yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(fig_comp, width='stretch')

    st.divider()

    # ── CITATION IMPACT ──────────────────────────────────────────
    st.subheader("🔗 Impatto citazionale")
    tab_cit1, tab_cit2 = st.tabs(["Leggi di bilancio più citate", "Cosa citano le leggi di bilancio"])

    bilancio_urns = tuple(df_all["urn"].tolist())
    placeholder_str = ",".join("?" * len(bilancio_urns))

    with tab_cit1:
        try:
            top_cited = conn.execute(f"""
                SELECT l.urn, l.title, l.year, COUNT(*) AS cited_by
                FROM citations c
                JOIN laws l ON l.urn = c.cited_urn
                WHERE c.cited_urn IN ({placeholder_str})
                GROUP BY l.urn, l.title, l.year
                ORDER BY cited_by DESC
                LIMIT 20
            """, bilancio_urns).fetchall()
            if top_cited:
                df_tc = pd.DataFrame([dict(r) for r in top_cited])
                df_tc["label"] = df_tc["title"].str[:50] + " (" + df_tc["year"].astype(str) + ")"
                fig_tc = px.bar(
                    df_tc, x="cited_by", y="label", orientation="h",
                    title="Le leggi di bilancio più citate da altre leggi",
                    labels={"cited_by": "Citazioni ricevute", "label": ""},
                    color="cited_by", color_continuous_scale="Oranges",
                )
                fig_tc.update_layout(yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig_tc, width='stretch')
            else:
                st.info("Nessuna citazione trovata per le leggi di bilancio nel dataset.")
        except Exception as e:
            st.warning(f"Errore citazioni ricevute: {e}")

    with tab_cit2:
        try:
            top_citing = conn.execute(f"""
                SELECT l.urn, l.title, l.year, COUNT(*) AS citations_made
                FROM citations c
                JOIN laws l ON l.urn = c.cited_urn
                WHERE c.citing_urn IN ({placeholder_str})
                GROUP BY l.urn, l.title, l.year
                ORDER BY citations_made DESC
                LIMIT 20
            """, bilancio_urns).fetchall()
            if top_citing:
                df_cg = pd.DataFrame([dict(r) for r in top_citing])
                df_cg["label"] = df_cg["title"].str[:50] + " (" + df_cg["year"].astype(str) + ")"
                fig_cg = px.bar(
                    df_cg, x="citations_made", y="label", orientation="h",
                    title="Leggi più richiamate dalle leggi di bilancio",
                    labels={"citations_made": "Richiami effettuati", "label": ""},
                    color="citations_made", color_continuous_scale="Purples",
                )
                fig_cg.update_layout(yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig_cg, width='stretch')
            else:
                st.info("Nessun richiamo outbound trovato.")
        except Exception as e:
            st.warning(f"Errore citazioni emesse: {e}")

    st.divider()

    # ── DOMAIN CROSSOVER ─────────────────────────────────────────
    st.subheader("🏛️ Intersezione con altri domini giuridici")
    domain_counts = (
        df_all[df_all["domain"] != ""]
        .groupby("domain")["urn"]
        .count()
        .reset_index()
        .rename(columns={"urn": "count"})
        .sort_values("count", ascending=False)
    )
    if not domain_counts.empty:
        fig_dom = px.treemap(
            domain_counts, path=["domain"], values="count",
            title="Domini giuridici toccati dalle leggi di bilancio",
            color="count",
            color_continuous_scale="RdBu",
        )
        st.plotly_chart(fig_dom, width='stretch')
    else:
        st.info("Dati di dominio non ancora elaborati per queste leggi.")

    # Cross-domain citations FROM budget laws
    try:
        xd = conn.execute(f"""
            SELECT m2.domain_cluster AS domain_citato, COUNT(*) AS cnt
            FROM citations c
            JOIN law_metadata m2 ON m2.urn = c.cited_urn
            WHERE c.citing_urn IN ({placeholder_str})
              AND m2.domain_cluster IS NOT NULL
              AND m2.domain_cluster != ''
            GROUP BY domain_citato
            ORDER BY cnt DESC
            LIMIT 20
        """, bilancio_urns).fetchall()
        if xd:
            df_xd = pd.DataFrame([dict(r) for r in xd])
            fig_xd = px.bar(
                df_xd, x="cnt", y="domain_citato", orientation="h",
                title="Domini giuridici richiamati dalle leggi di bilancio",
                labels={"cnt": "Citazioni verso il dominio", "domain_citato": "Dominio"},
                color="cnt", color_continuous_scale="Teal",
            )
            fig_xd.update_layout(yaxis={"categoryorder": "total ascending"})
            st.plotly_chart(fig_xd, width='stretch')
    except Exception as e:
        logger.warning("Cross-domain bilancio: %s", e)

    st.divider()

    # ── FULL LAW TABLE ───────────────────────────────────────────
    st.subheader("📋 Elenco completo leggi di bilancio")

    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        search_term = st.text_input("🔎 Filtra per titolo", key="bilancio-search")
    with col_f2:
        status_filter = st.selectbox(
            "Stato", ["Tutti", "Vigente", "Abrogata"], key="bilancio-status"
        )

    df_table = df_all.copy()
    if search_term:
        df_table = df_table[
            df_table["title"].str.lower().str.contains(search_term.lower(), na=False)
        ]
    if status_filter == "Vigente":
        df_table = df_table[df_table["status"] == "in_force"]
    elif status_filter == "Abrogata":
        df_table = df_table[df_table["status"] != "in_force"]

    # Display columns
    df_display = df_table[["title", "year", "type", "article_count", "text_length", "status", "cited_by", "urn"]].copy()
    df_display["status"] = df_display["status"].map(
        {"in_force": "✅ Vigente", "abrogated": "❌ Abrogata"}
    ).fillna(df_display["status"])
    df_display["text_length"] = (df_display["text_length"] / 1000).round(1).astype(str) + "K"
    df_display.columns = ["Titolo", "Anno", "Tipo", "Articoli", "Testo (chars)", "Stato", "Citazioni", "URN"]

    st.dataframe(df_display.drop(columns=["URN"]), width='stretch', hide_index=True)

    st.caption(f"Mostrate {len(df_table):,} leggi su {total:,} totali")

    if not df_table.empty:
        sel_urn = st.selectbox(
            "Seleziona una legge da aprire:",
            df_table["urn"].tolist(),
            format_func=lambda u: next(
                (f"{r['year']} — {r['title'][:70]}" for r in df_table.to_dict("records") if r["urn"] == u),
                u,
            ),
            key="bilancio-open-sel",
        )
        if st.button("📖 Apri legge selezionata", key="bilancio-open-btn"):
            _open_law(sel_urn, source_tab="bilancio", source_context="table")


def page_multivigente_lab():
    """Lab-only exploration for multivigente versions."""
    st.header("🕰️ Multivigente (Lab)")
    st.caption("Analisi versioni storiche per URN con snapshot temporale")

    file_path = _find_multivigente_file()
    dataset_repo = get_dataset_repo()

    if not file_path:
        st.warning("Nessun file multivigente disponibile nel dataset corrente.")
        st.code(
            "GitHub Actions -> workflow_dispatch\n"
            "target_env = lab\n"
            "variants = 'vigente multivigente'\n"
            "full_rebuild = true"
        )
        return

    size_mb = file_path.stat().st_size / 1e6
    st.info(f"Dataset: {dataset_repo} | File: {file_path.name} ({size_mb:.1f} MB)")

    c1, c2 = st.columns([3, 1])
    with c1:
        urn_query = st.text_input(
            "URN (anche parziale)",
            placeholder="urn:nir:stato:legge:...",
            help="Inserisci URN completo o prefisso/parte per cercare tutte le versioni.",
        )
    with c2:
        max_matches = st.number_input("Max versioni", min_value=10, max_value=50000, value=5000, step=100)

    as_of = st.date_input("Snapshot alla data", value=datetime.utcnow().date())

    if not urn_query.strip():
        st.caption("Inserisci un URN per avviare l'analisi multivigente.")
        return

    rows = _mv_find_versions(str(file_path), urn_query.strip(), int(max_matches))

    if not rows:
        st.warning("Nessuna versione trovata per l'URN indicato.")
        return

    active_rows = [r for r in rows if _mv_is_active_on(r, as_of)]

    k1, k2, k3 = st.columns(3)
    k1.metric("Versioni trovate", f"{len(rows):,}")
    k2.metric("Attive alla data", f"{len(active_rows):,}")
    k3.metric("Range date", f"{(rows[0].get('valid_from') or rows[0].get('date') or '?')} → {(rows[-1].get('valid_from') or rows[-1].get('date') or '?')}")

    st.subheader("Snapshot alla data selezionata")
    if active_rows:
        df_active = pd.DataFrame(active_rows)
        df_active = df_active[["urn", "title", "type", "valid_from", "valid_to", "is_current", "article_count", "text_length"]]
        st.dataframe(df_active, width='stretch', hide_index=True)
    else:
        st.info("Nessuna versione risulta attiva alla data selezionata.")

    st.subheader("Timeline versioni")
    df_all = pd.DataFrame(rows)
    df_all = df_all[["urn", "title", "type", "date", "valid_from", "valid_to", "is_current", "article_count", "text_length"]]
    st.dataframe(df_all, width='stretch', hide_index=True)


# ─────────────────────────────────────────────────────────────────
# NAVIGATION
# ─────────────────────────────────────────────────────────────────

def main():
    pages = {
        "📊 Dashboard": page_dashboard,
        "🇮🇹 Costituzione & Codici": page_costituzione,
        "⚖️ Corte Costituzionale": page_corte_cost,
        "🔍 Search": page_search,
        "📋 Browse": page_browse,
        "📖 Law Detail": page_law_detail,
        "🔗 Citations": page_citations,
        "🏛️ Domains": page_domains,
        "💰 Legge di Bilancio": page_bilancio,
        "🔔 Notifications": page_notifications,
        "📝 Update Log": page_update_log,
        "📥 Export": page_export,
    }

    # Expose multivigente tools only in lab-like datasets.
    if "lab" in get_dataset_repo().lower():
        pages["🕰️ Multivigente (Lab)"] = page_multivigente_lab

    # Allow in-page navigation to Law Detail (from cards)
    if "goto_page" in st.session_state and st.session_state["goto_page"] in pages:
        default_page = st.session_state.pop("goto_page")
    else:
        default_page = None

    st.sidebar.write("### Navigazione")
    page_keys = list(pages.keys())
    default_idx = page_keys.index(default_page) if default_page else 0

    page = st.sidebar.radio(
        "Go to", page_keys, index=default_idx,
        label_visibility="collapsed", key="page-nav"
    )

    # Notification badge in sidebar
    with _monitor_lock:
        pending_count = len(_monitor_state["pending_changes"])
    if pending_count > 0:
        st.sidebar.warning(f"🔔 {pending_count} API change(s) detected!")

    st.sidebar.divider()

    # DB status in sidebar
    db = load_db()
    if db:
        try:
            count = db.conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
            st.sidebar.metric("Leggi nel database", f"{count:,}")
        except Exception:
            pass
        st.sidebar.success("Database: ✓ Caricato")
    else:
        st.sidebar.error("Database: ✗ Non trovato")
        laws = load_laws_from_jsonl()
        if laws:
            st.sidebar.metric("Leggi (JSONL)", f"{len(laws):,}")

    # Last update
    if db:
        log_entries = _get_update_log(db)
        if log_entries:
            last = log_entries[0].get("timestamp", "")[:10]
            st.sidebar.caption(f"Ultimo aggiornamento: {last}")

    st.sidebar.divider()
    st.sidebar.markdown(
        "⚖️ **OpenNormattiva** — Piattaforma di ricerca giuridica italiana\n\n"
        "190.000+ leggi | Ricerca FTS5 | Grafi citazioni | Cronologia modifiche"
    )

    pages[page]()


if __name__ == "__main__":
    main()

