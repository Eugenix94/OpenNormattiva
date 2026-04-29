#!/usr/bin/env python3
"""
Normattiva Jurisprudence Research Platform

Fully static Streamlit app - DB ships with the Space, always available.
NO automatic pipeline writes. Normattiva API is used READ-ONLY to detect
new/changed collections and show notifications.  The user decides when
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

# Setup paths for imports
_app_dir = Path(__file__).parent
_root_dir = _app_dir.parent
sys.path.insert(0, str(_root_dir))
sys.path.insert(0, str(_app_dir))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# Database connection (static - pre-built DB ships with the Space)

def get_db_paths():
    """Generate database search paths (works in Docker, local dev, and HF Spaces)."""
    _app_file = Path(__file__)
    _app_dir = _app_file.parent
    _root_dir = _app_dir.parent
    
    hf_cache_hub = Path.home() / '.cache' / 'huggingface' / 'hub'
    
    # Scan HF hub cache for any normattiva dataset snapshot that has laws.db
    hf_cached_paths = []
    if hf_cache_hub.exists():
        for snap in hf_cache_hub.glob('datasets--diatribe00--normattiva-data/snapshots/*/data/laws.db'):
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
        logger.info("Downloading database from HF Dataset (this may take ~5 min)...")
        cached = hf_hub_download(
            repo_id="diatribe00/normattiva-data",
            filename="data/laws.db",
            repo_type="dataset",
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
def _get_laws_cached(db_path: str):
    """Cached law loading — separated from session state for cache key."""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT urn, title, type, date, year, status, article_count, "
            "text_length, importance_score FROM laws ORDER BY year DESC LIMIT 100000"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Failed to cache-load laws: {e}")
        return []


def _get_laws():
    """Return list of law dicts from DB or JSONL fallback."""
    db = load_db()
    if db:
        try:
            db_path = str(db.db_path) if hasattr(db, 'db_path') else ""
            if db_path:
                return _get_laws_cached(db_path)
            # Fallback: direct query without cache
            rows = db.conn.execute(
                "SELECT urn, title, type, date, year, status, article_count, "
                "text_length, importance_score FROM laws ORDER BY year DESC LIMIT 100000"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error loading laws from DB: {e}")
    return load_laws_from_jsonl()


def _init_global_filters(laws):
    """Initialize shared cross-tab filters once per session."""
    years = [int(l.get("year")) for l in laws if l.get("year") not in (None, "")]
    types = sorted(set((l.get("type") or "unknown") for l in laws))
    statuses = sorted(set((l.get("status") or "unknown") for l in laws))

    min_year = min(years) if years else 1800
    max_year = max(years) if years else datetime.now().year

    st.session_state.setdefault("gf_all_types", types)
    st.session_state.setdefault("gf_all_statuses", statuses)
    st.session_state.setdefault("gf_year_bounds", (min_year, max_year))

    st.session_state.setdefault("gf_types", types)
    st.session_state.setdefault("gf_statuses", statuses)
    st.session_state.setdefault("gf_year_range", (min_year, max_year))
    st.session_state.setdefault("gf_keyword", "")


def _apply_global_filters(laws):
    """Apply shared sidebar filters to law lists."""
    selected_types = set(st.session_state.get("gf_types", []))
    selected_statuses = set(st.session_state.get("gf_statuses", []))
    y0, y1 = st.session_state.get("gf_year_range", st.session_state.get("gf_year_bounds", (1800, 2100)))
    keyword = (st.session_state.get("gf_keyword") or "").strip().lower()

    out = []
    for l in laws:
        typ = (l.get("type") or "unknown")
        status = (l.get("status") or "unknown")
        year = l.get("year")

        if selected_types and typ not in selected_types:
            continue
        if selected_statuses and status not in selected_statuses:
            continue
        if year not in (None, ""):
            try:
                y = int(year)
                if y < y0 or y > y1:
                    continue
            except Exception:
                pass

        if keyword:
            blob = " ".join([
                str(l.get("title") or ""),
                str(l.get("urn") or ""),
                str(l.get("type") or ""),
                str(l.get("status") or ""),
            ]).lower()
            if keyword not in blob:
                continue

        out.append(l)
    return out


def _render_filter_summary(filtered_count: int, total_count: int):
    if total_count <= 0:
        return
    if filtered_count == total_count:
        st.caption(f"Global view: {filtered_count:,}/{total_count:,} laws")
    else:
        st.info(f"Global filters active: showing {filtered_count:,} of {total_count:,} laws")


def _objective_results(db, filtered_laws):
    """Compute objective cross-domain indicators on the current filtered scope."""
    total = len(filtered_laws)
    in_force = sum(1 for l in filtered_laws if (l.get("status") == "in_force"))
    abrog = sum(1 for l in filtered_laws if (l.get("status") == "abrogated"))
    avg_articles = (sum(int(l.get("article_count") or 0) for l in filtered_laws) / total) if total else 0
    avg_text = (sum(int(l.get("text_length") or 0) for l in filtered_laws) / total) if total else 0

    citation_total = 0
    if db:
        try:
            citation_total = db.conn.execute("SELECT COUNT(*) FROM citations").fetchone()[0]
        except Exception:
            citation_total = 0

    return {
        "total": total,
        "in_force": in_force,
        "abrogated": abrog,
        "abrogation_rate": (abrog / total * 100.0) if total else 0.0,
        "avg_articles": avg_articles,
        "avg_text": avg_text,
        "citations_total": citation_total,
    }


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


def _extract_euro_amounts(text: str) -> list[float]:
    """Extract approximate euro amounts from legal text.

    Supports values like:
    - 1.234.567,89 euro
    - 250 milioni di euro
    - 3,5 miliardi euro
    """
    if not text:
        return []

    import re

    patt = re.compile(
        r"(\d{1,3}(?:\.\d{3})*(?:,\d+)?|\d+(?:,\d+)?)\s*"
        r"(miliardi?|milioni?|mila)?\s*(?:di\s+)?euro",
        re.IGNORECASE,
    )
    mult = {
        None: 1,
        "mila": 1_000,
        "milione": 1_000_000,
        "milioni": 1_000_000,
        "miliardo": 1_000_000_000,
        "miliardi": 1_000_000_000,
    }

    out: list[float] = []
    for raw_n, raw_u in patt.findall(text):
        n = raw_n.replace(".", "").replace(",", ".")
        try:
            val = float(n)
        except ValueError:
            continue
        unit = (raw_u or "").lower() or None
        out.append(val * mult.get(unit, 1))
    return out


@st.cache_data(ttl=21600, show_spinner="Computing fiscal and status-quo analytics...")
def _get_fiscal_status_snapshot(db_path: str) -> dict:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM laws GROUP BY status ORDER BY cnt DESC"
        ).fetchall()
        status_counts = {r["status"] or "unknown": r["cnt"] for r in status_rows}

        total_in_force = int(status_counts.get("in_force", 0))
        total_abrogated = int(status_counts.get("abrogated", 0))

        has_variants = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='law_variants'"
        ).fetchone() is not None

        correlated = None
        if has_variants:
            correlated_row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN l.status='in_force'
                         AND EXISTS(SELECT 1 FROM law_variants v WHERE v.urn=l.urn AND v.variant='vigente')
                         THEN 1 ELSE 0 END) AS in_force_with_vigente,
                    SUM(CASE WHEN l.status='abrogated'
                         AND EXISTS(SELECT 1 FROM law_variants v WHERE v.urn=l.urn AND v.status='abrogated')
                         THEN 1 ELSE 0 END) AS abrogated_with_variant_evidence,
                    SUM(CASE WHEN l.status='in_force'
                         AND EXISTS(SELECT 1 FROM law_variants v WHERE v.urn=l.urn AND v.variant='originale' AND v.status='abrogated')
                         THEN 1 ELSE 0 END) AS potential_status_conflicts
                FROM laws l
                """
            ).fetchone()
            correlated = dict(correlated_row) if correlated_row else None

        bilancio_rows = conn.execute(
            """
            SELECT urn, title, year, status, type, text_length, text
            FROM laws
            WHERE LOWER(title) LIKE '%bilancio%'
            ORDER BY year DESC, date DESC
            LIMIT 160
            """
        ).fetchall()

        bilancio_series = []
        bilancio_total_in_force = 0.0
        bilancio_mentions_in_force = 0
        bilancio_in_force_count = 0
        bilancio_table = []

        for r in bilancio_rows:
            row = dict(r)
            amounts = _extract_euro_amounts(row.get("text") or "")
            amount_sum = float(sum(amounts))
            mention_count = len(amounts)

            if row.get("status") == "in_force":
                bilancio_total_in_force += amount_sum
                bilancio_mentions_in_force += mention_count
                bilancio_in_force_count += 1

            yr = row.get("year")
            bilancio_series.append({
                "year": int(yr) if yr not in (None, "") else None,
                "status": row.get("status") or "unknown",
                "euro_mentions_count": mention_count,
                "euro_mentions_sum": amount_sum,
            })
            bilancio_table.append({
                "URN": row.get("urn"),
                "Title": (row.get("title") or "")[:120],
                "Year": row.get("year"),
                "Status": row.get("status"),
                "Euro Mentions": mention_count,
                "Mentioned Amount (EUR)": round(amount_sum, 2),
                "Text Length": row.get("text_length", 0),
            })

        fiscal_rows = conn.execute(
            """
            SELECT urn, title, year, status, text
            FROM laws
            WHERE status='in_force'
              AND (
                LOWER(title) LIKE '%impost%'
                OR LOWER(title) LIKE '%accis%'
                OR LOWER(title) LIKE '%tribut%'
                OR LOWER(title) LIKE '%tass%'
                OR LOWER(title) LIKE '%bilancio%'
                OR LOWER(title) LIKE '%fiscal%'
              )
            ORDER BY year DESC
            LIMIT 4000
            """
        ).fetchall()

        term_patterns = {
            "imposta": "impost",
            "tassa": "tass",
            "tributo": "tribut",
            "accisa": "accis",
            "detrazione": "detraz",
            "credito_imposta": "credito d'imposta",
        }
        laws_by_term = {k: 0 for k in term_patterns}
        mentions_by_term = {k: 0 for k in term_patterns}

        fiscal_total_amount = 0.0
        fiscal_total_mentions = 0
        fiscal_sample = []

        for r in fiscal_rows:
            row = dict(r)
            txt = ((row.get("title") or "") + " " + (row.get("text") or "")).lower()
            txt_short = txt[:120000]

            matched_any = False
            for label, needle in term_patterns.items():
                c = txt_short.count(needle)
                if c > 0:
                    matched_any = True
                    laws_by_term[label] += 1
                    mentions_by_term[label] += c

            amounts = _extract_euro_amounts(row.get("text") or "")
            fiscal_total_amount += float(sum(amounts))
            fiscal_total_mentions += len(amounts)

            if matched_any and len(fiscal_sample) < 300:
                fiscal_sample.append({
                    "URN": row.get("urn"),
                    "Title": (row.get("title") or "")[:120],
                    "Year": row.get("year"),
                    "Status": row.get("status"),
                    "Euro Mentions": len(amounts),
                    "Mentioned Amount (EUR)": round(sum(amounts), 2),
                })

        return {
            "status_counts": status_counts,
            "total_in_force": total_in_force,
            "total_abrogated": total_abrogated,
            "has_variants": has_variants,
            "correlated": correlated,
            "bilancio_in_force_count": bilancio_in_force_count,
            "bilancio_total_in_force": bilancio_total_in_force,
            "bilancio_mentions_in_force": bilancio_mentions_in_force,
            "bilancio_series": bilancio_series,
            "bilancio_table": bilancio_table,
            "fiscal_in_force_count": len(fiscal_rows),
            "fiscal_total_amount": fiscal_total_amount,
            "fiscal_total_mentions": fiscal_total_mentions,
            "laws_by_term": laws_by_term,
            "mentions_by_term": mentions_by_term,
            "fiscal_sample": fiscal_sample,
        }
    finally:
        conn.close()


# PAGES

def page_dashboard():
    st.header("\U0001f4ca Dashboard")
    db = load_db()
    all_laws = _get_laws()
    laws = _apply_global_filters(all_laws)
    _render_filter_summary(len(laws), len(all_laws))
    
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
    c1.metric("Total Laws", f"{len(laws):,}")
    types = set(l.get("type", "unknown") for l in laws)
    c2.metric("Document Types", len(types))
    years = [l.get("year") for l in laws if l.get("year")]
    c3.metric("Year Range", f"{min(years)}-{max(years)}" if years else "N/A")
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
    all_laws = _get_laws()
    filtered_scope = _apply_global_filters(all_laws)
    allowed_urns = set(l.get("urn") for l in filtered_scope if l.get("urn"))
    _render_filter_summary(len(filtered_scope), len(all_laws))

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
            if allowed_urns:
                results = [r for r in results if r.get("urn") in allowed_urns]
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
                    with c2:
                        snippet = r.get("snippet", "")
                        if snippet:
                            st.markdown(f"**Matched text**: ...{snippet}...")
                        else:
                            st.text_area("Preview", r.get("text", "")[:800],
                                         height=150, disabled=True,
                                         key=f"search_{r.get('urn','')}")
        except Exception as e:
            st.error(f"Search error: {e}")
    else:
        laws = load_laws_from_jsonl()
        laws = [l for l in laws if l.get("urn") in allowed_urns] if allowed_urns else laws
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
    all_laws = _get_laws()
    laws = _apply_global_filters(all_laws)
    _render_filter_summary(len(laws), len(all_laws))
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

    st.write(f"**Showing {len(filtered)} of {len(laws)} laws**")

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


def page_unified_analysis():
    st.header("📈 Unified Analysis")
    st.caption("Objective cross-tab synthesis on the exact filtered dataset scope.")

    db = load_db()
    all_laws = _get_laws()
    laws = _apply_global_filters(all_laws)
    _render_filter_summary(len(laws), len(all_laws))

    if not laws:
        st.info("No laws in current filter scope.")
        return

    obj = _objective_results(db, laws)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Laws in scope", f"{obj['total']:,}")
    c2.metric("In force", f"{obj['in_force']:,}")
    c3.metric("Abrogated", f"{obj['abrogated']:,}")
    c4.metric("Abrogation rate", f"{obj['abrogation_rate']:.2f}%")

    c5, c6, c7 = st.columns(3)
    c5.metric("Avg articles/law", f"{obj['avg_articles']:.1f}")
    c6.metric("Avg text length", f"{obj['avg_text']:.0f} chars")
    c7.metric("Citations in DB", f"{obj['citations_total']:,}")

    year_counts = Counter(int(l.get("year")) for l in laws if l.get("year") not in (None, ""))
    if year_counts:
        ys = dict(sorted(year_counts.items()))
        fig_year = px.line(
            x=list(ys.keys()), y=list(ys.values()),
            title="Objective trend: laws by year (filtered scope)",
            labels={"x": "Year", "y": "Count"},
        )
        st.plotly_chart(fig_year, width='stretch')

    status_counts = Counter((l.get("status") or "unknown") for l in laws)
    fig_status = px.bar(
        x=list(status_counts.keys()), y=list(status_counts.values()),
        title="Objective status composition",
        labels={"x": "Status", "y": "Count"},
    )
    st.plotly_chart(fig_status, width='stretch')


def page_law_detail():
    st.header("📖 Law Detail - Full Context & Relationships")
    db = load_db()
    if not db:
        st.info("Database required for detailed law view.")
        return

    laws = _get_laws()
    urn_options = [
        f"{l.get('title', '')[:60]} ({l.get('urn', '')})"
        for l in laws[:500]
    ]
    selected = st.selectbox(
        "Select a law:", urn_options if urn_options else ["No laws available"],
        key="law-detail-select"
    )
    if not selected or selected == "No laws available":
        return

    urn = selected.split("(")[-1].rstrip(")")
    law_row = db.conn.execute(
        "SELECT * FROM laws WHERE urn = ?", (urn,)
    ).fetchone()
    if not law_row:
        st.warning("Law not found.")
        return

    law = dict(law_row)
    st.subheader(law.get("title", "Untitled"))

    # Quick metadata in columns
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Type", law.get("type", "N/A"))
    col2.metric("Year", law.get("year", "N/A"))
    col3.metric("Articles", law.get("article_count", 0))
    if law.get("importance_score"):
        col4.metric("Importance (PageRank)", f"{law['importance_score']:.4f}")

    # Main tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📄 Full Text", 
        "🔗 Citation Links", 
        "📚 Related Laws",
        "⚖️ Amendments",
        "🎯 Context Graph"
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
                # Show URN reference table below text
                ref_table = _urn_inline_links(text, db)
                if ref_table:
                    with st.expander("📎 Leggi citate nel testo (URN references)"):
                        st.markdown(ref_table)
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
                            st.write(f"**{cited_law.get('title', 'N/A')}**")
                            st.write(f"Type: {cited_law.get('type')}")
                            st.write(f"Year: {cited_law.get('year')}")
                            st.button(
                                f"View full law →", 
                                key=f"btn-view-{cited_urn}",
                                on_click=lambda u=cited_urn: st.query_params.update({"urn": u})
                            )
                        except:
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
                            st.write(f"**{ref_law.get('title', 'N/A')}**")
                            st.write(f"Type: {ref_law.get('type')}")
                            st.write(f"Year: {ref_law.get('year')}")
                            st.button(
                                f"View dependency →", 
                                key=f"btn-dep-{cited_urn}",
                                on_click=lambda u=cited_urn: st.query_params.update({"urn": u})
                            )
                        except:
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
                            col1.button(
                                f"📖 {law_ref[1][:50]} ({law_ref[2]})",
                                key=f"domain-{law_ref[0]}",
                                on_click=lambda u=law_ref[0]: st.query_params.update({"urn": u})
                            )
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
                        col2.button(
                            f"📖 {r.get('title', 'N/A')[:50]}",
                            key=f"related-{r['urn']}",
                            on_click=lambda u=r['urn']: st.query_params.update({"urn": u})
                        )
                    if len(related) > 10:
                        st.caption(f"... and {len(related) - 10} more co-cited laws")
                else:
                    st.info("No related laws found via co-citation.")
            except:
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


def page_citations():
    st.header("\U0001f517 Citation Network")
    db = load_db()
    all_laws = _get_laws()
    filtered_laws = _apply_global_filters(all_laws)
    allowed_urns = set(l.get("urn") for l in filtered_laws if l.get("urn"))
    _render_filter_summary(len(filtered_laws), len(all_laws))

    if db:
        st.subheader("Most Cited Laws")
        try:
            top = db.conn.execute(
                "SELECT l.urn, l.title, l.year, m.citation_count_incoming "
                "FROM laws l JOIN law_metadata m ON l.urn = m.urn "
                "WHERE m.citation_count_incoming > 0 "
                "ORDER BY m.citation_count_incoming DESC LIMIT 25"
            ).fetchall()
            if top:
                if allowed_urns:
                    top = [r for r in top if r["urn"] in allowed_urns]
                df = pd.DataFrame([dict(r) for r in top])
                df.columns = ["URN", "Title", "Year", "Cited By"]
                df["Title"] = df["Title"].str[:50]
                fig = px.bar(df, x="Title", y="Cited By",
                             title="Top 25 Most Cited Laws",
                             hover_data=["URN", "Year"])
                st.plotly_chart(fig, width='stretch')
                st.dataframe(df, width='stretch', hide_index=True)
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
        except Exception:
            pass
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
    all_laws = _get_laws()
    filtered_laws = _apply_global_filters(all_laws)
    allowed_urns = set(l.get("urn") for l in filtered_laws if l.get("urn"))
    _render_filter_summary(len(filtered_laws), len(all_laws))
    if not db:
        st.info("Database required for domain analysis.")
        return

    try:
        domain_rows = db.conn.execute(
            """
            SELECT m.domain_cluster, l.urn
            FROM law_metadata m
            JOIN laws l ON l.urn = m.urn
            WHERE m.domain_cluster IS NOT NULL AND m.domain_cluster != ''
            """
        ).fetchall()
        counts = {}
        for r in domain_rows:
            if allowed_urns and r[1] not in allowed_urns:
                continue
            counts[r[0]] = counts.get(r[0], 0) + 1
        domains = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    except Exception:
        st.info("Domain data not available.")
        return

    if not domains:
        st.info("No domain data available.")
        return

    domain_names = [d[0] for d in domains]
    domain_counts = [d[1] for d in domains]

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
        laws_in_domain = db.conn.execute("""
            SELECT l.urn, l.title, l.year, l.type, l.importance_score
            FROM laws l JOIN law_metadata m ON l.urn = m.urn
            WHERE m.domain_cluster = ?
            ORDER BY l.importance_score DESC NULLS LAST
            LIMIT 50
        """, (selected_domain,)).fetchall()
        if allowed_urns:
            laws_in_domain = [r for r in laws_in_domain if r["urn"] in allowed_urns]
        if laws_in_domain:
            df = pd.DataFrame([dict(r) for r in laws_in_domain])
            df.columns = ["URN", "Title", "Year", "Type", "Importance"]
            df["Title"] = df["Title"].str[:60]
            df["Importance"] = df["Importance"].apply(
                lambda x: f"{x:.4f}" if x else "N/A"
            )
            st.write(
                f"**{len(laws_in_domain)} laws** in domain "
                f"_{selected_domain}_:"
            )
            st.dataframe(df, width='stretch', hide_index=True)


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


def page_fiscal_status_quo():
    st.header("💶 Fiscal Status Quo")
    st.caption(
        "Correlates vigente/abrogated state with fiscal laws and extracts quantitative "
        "signals from legge di bilancio and tax-policy texts."
    )

    db = load_db()
    if not db:
        st.info("Database required for fiscal analytics.")
        return

    db_path = str(db.db_path) if hasattr(db, "db_path") else ""
    if not db_path:
        st.info("Database path not available for fiscal analytics.")
        return

    snap = _get_fiscal_status_snapshot(db_path)
    all_laws = _get_laws()
    filtered_laws = _apply_global_filters(all_laws)
    _render_filter_summary(len(filtered_laws), len(all_laws))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("In force laws", f"{snap['total_in_force']:,}")
    c2.metric("Abrogated laws", f"{snap['total_abrogated']:,}")
    c3.metric("Bilancio laws (in force)", f"{snap['bilancio_in_force_count']:,}")
    c4.metric("Fiscal laws screened", f"{snap['fiscal_in_force_count']:,}")

    tabs = st.tabs(["⚖️ Present-Day Correlation", "🏦 Legge di Bilancio", "🧾 Taxes & Excises in Force"])

    with tabs[0]:
        st.subheader("Status correlation (vigente vs abrogated)")
        status_df = pd.DataFrame(
            [{"status": k, "count": v} for k, v in snap["status_counts"].items()]
        )
        if not status_df.empty:
            fig_status = px.pie(status_df, names="status", values="count", title="Current status distribution")
            st.plotly_chart(fig_status, width='stretch')
            st.dataframe(status_df.sort_values("count", ascending=False), width='stretch', hide_index=True)

        if snap["has_variants"] and snap["correlated"]:
            corr = snap["correlated"]
            cc1, cc2, cc3 = st.columns(3)
            cc1.metric("In-force with vigente evidence", f"{int(corr.get('in_force_with_vigente', 0)):,}")
            cc2.metric("Abrogated with variant evidence", f"{int(corr.get('abrogated_with_variant_evidence', 0)):,}")
            cc3.metric("Potential status conflicts", f"{int(corr.get('potential_status_conflicts', 0)):,}")
            st.caption(
                "Potential conflicts are laws marked in force in `laws` while a related originale variant is already "
                "marked abrogated and should be reviewed."
            )
        else:
            st.info(
                "Variant-level correlation table (`law_variants`) is not available in this dataset build. "
                "The present-day status view is based on `laws.status`."
            )

    with tabs[1]:
        st.subheader("Legge di bilancio quantitative extraction")
        amount_billion = snap["bilancio_total_in_force"] / 1_000_000_000
        b1, b2 = st.columns(2)
        b1.metric("Mentioned amounts (EUR, in-force bilancio)", f"€ {amount_billion:,.2f}B")
        b2.metric("Euro mentions (in-force bilancio)", f"{snap['bilancio_mentions_in_force']:,}")
        st.caption(
            "Amounts are parsed from legal text mentions and should be interpreted as textual fiscal signals, "
            "not certified accounting totals."
        )

        series_df = pd.DataFrame(snap["bilancio_series"])
        if not series_df.empty:
            series_df = series_df[series_df["year"].notna()]
            if not series_df.empty:
                yearly = series_df.groupby("year", as_index=False).agg(
                    euro_mentions_sum=("euro_mentions_sum", "sum"),
                    euro_mentions_count=("euro_mentions_count", "sum"),
                )
                fig_year = px.bar(
                    yearly,
                    x="year",
                    y="euro_mentions_sum",
                    title="Bilancio: extracted amount mentions by year",
                    labels={"euro_mentions_sum": "EUR mentioned", "year": "Year"},
                )
                st.plotly_chart(fig_year, width='stretch')
                st.dataframe(yearly.sort_values("year", ascending=False), width='stretch', hide_index=True)

        bilancio_df = pd.DataFrame(snap["bilancio_table"])
        if not bilancio_df.empty:
            st.write("Recent bilancio laws and extracted monetary signals")
            st.dataframe(bilancio_df.head(80), width='stretch', hide_index=True)

    with tabs[2]:
        st.subheader("Taxes, imposts, excises and fiscal policy signals in force")
        t1, t2 = st.columns(2)
        t1.metric("Total parsed euro mentions (fiscal corpus)", f"{snap['fiscal_total_mentions']:,}")
        t2.metric("Mentioned amounts (EUR, fiscal corpus)", f"€ {snap['fiscal_total_amount']/1_000_000_000:,.2f}B")

        by_law_df = pd.DataFrame(
            [{"term": k, "laws_in_force": v} for k, v in snap["laws_by_term"].items()]
        )
        mentions_df = pd.DataFrame(
            [{"term": k, "mentions": v} for k, v in snap["mentions_by_term"].items()]
        )
        if not by_law_df.empty:
            fig_terms = px.bar(by_law_df, x="term", y="laws_in_force", title="In-force laws by fiscal term")
            st.plotly_chart(fig_terms, width='stretch')
            st.dataframe(by_law_df.sort_values("laws_in_force", ascending=False), width='stretch', hide_index=True)

        if not mentions_df.empty:
            fig_mentions = px.bar(mentions_df, x="term", y="mentions", title="Term mentions in in-force fiscal laws")
            st.plotly_chart(fig_mentions, width='stretch')

        sample_df = pd.DataFrame(snap["fiscal_sample"])
        if not sample_df.empty:
            st.write("Sample of fiscal laws currently in force")
            st.dataframe(sample_df.head(120), width='stretch', hide_index=True)


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


def _render_law_card(law: dict, db, key_prefix: str = ""):
    """Render a compact law card with nav button and citation count."""
    urn = law.get("urn", "")
    title = law.get("title", "N/A")
    year = law.get("year", "")
    law_type = law.get("type", "")
    importance = law.get("importance_score", 0) or 0

    try:
        incoming = db.conn.execute(
            "SELECT COUNT(*) FROM citations WHERE cited_urn = ?", (urn,)
        ).fetchone()[0]
    except Exception:
        incoming = 0

    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        with c1:
            st.write(f"**{title}**")
            st.caption(f"`{urn}` | {law_type} | {year}")
            if incoming:
                st.caption(f"📎 Citata da {incoming:,} leggi")
        with c2:
            if st.button("Apri →", key=f"{key_prefix}-open-{urn}"):
                st.session_state["detail_urn"] = urn
                st.session_state["goto_page"] = "📖 Law Detail"
                st.rerun()


def _urn_inline_links(text: str, db, max_links: int = 30) -> str:
    """
    Find all URN references in law text and return a Markdown string
    with a lookup table of referenced laws displayed below the text.
    """
    import re
    pattern = r'urn:nir:[a-zA-Z0-9\.\:\;\-]+'
    found = list(dict.fromkeys(re.findall(pattern, text)))[:max_links]
    if not found:
        return ""
    rows = []
    for urn in found:
        try:
            row = db.conn.execute(
                "SELECT title, year, type FROM laws WHERE urn = ?", (urn,)
            ).fetchone()
            if row:
                rows.append((urn, row[0], row[1], row[2]))
        except Exception:
            pass
    if not rows:
        return ""
    md = "**Leggi citate nel testo** (" + str(len(rows)) + " trovate):\n\n"
    md += "| URN | Titolo | Anno | Tipo |\n|---|---|---|---|\n"
    for urn, title, year, ltype in rows:
        md += f"| `{urn}` | {title[:60]} | {year} | {ltype} |\n"
    return md


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

    tab_cost, tab_hier, tab_codici, tab_implement = st.tabs([
        "📜 Testo & Citazioni",
        "🏛️ Gerarchia delle Fonti",
        "📚 I Principali Codici",
        "🔗 Leggi di Attuazione",
    ])

    with tab_cost:
        if law:
            text = law.get("text", "")
            st.subheader(law.get("title", "Costituzione Italiana"))
            c1, c2 = st.columns([3, 1])
            with c1:
                if text:
                    st.text_area("Testo completo", text, height=500, disabled=True, key="const-text")
                    # Show URN reference table
                    ref_table = _urn_inline_links(text, db)
                    if ref_table:
                        with st.expander("📎 Leggi richiamate nel testo della Costituzione"):
                            st.markdown(ref_table)
                else:
                    st.info("Testo non disponibile nel database.")
            with c2:
                st.subheader("Metadati")
                st.write(f"**URN**: `{law.get('urn')}`")
                st.write(f"**Tipo**: {law.get('type')}")
                st.write(f"**Data**: {law.get('date')}")
                st.write(f"**Importanza (PageRank)**: {law.get('importance_score', 0):.4f}")

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
                            st.session_state["detail_urn"] = law_d["urn"]
                            st.session_state["goto_page"] = "📖 Law Detail"
                            st.rerun()
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


# ─────────────────────────────────────────────────────────────────
# NAVIGATION
# ─────────────────────────────────────────────────────────────────

def main():
    all_laws = _get_laws()
    _init_global_filters(all_laws)

    pages = {
        "📊 Dashboard": page_dashboard,
        "📈 Unified Analysis": page_unified_analysis,
        "🇮🇹 Costituzione & Codici": page_costituzione,
        "🔍 Search": page_search,
        "📋 Browse": page_browse,
        "📖 Law Detail": page_law_detail,
        "🔗 Citations": page_citations,
        "🏛️ Domains": page_domains,
        "💶 Fiscal Status Quo": page_fiscal_status_quo,
        "🔔 Notifications": page_notifications,
        "📝 Update Log": page_update_log,
        "📥 Export": page_export,
    }

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

    # Global cross-tab analysis filters
    st.sidebar.write("### Global Analysis Filters")
    all_types = st.session_state.get("gf_all_types", [])
    all_statuses = st.session_state.get("gf_all_statuses", [])
    yb = st.session_state.get("gf_year_bounds", (1800, datetime.now().year))

    st.sidebar.multiselect("Types", options=all_types, key="gf_types")
    st.sidebar.multiselect("Statuses", options=all_statuses, key="gf_statuses")
    st.sidebar.slider("Year range", min_value=int(yb[0]), max_value=int(yb[1]), key="gf_year_range")
    st.sidebar.text_input("Keyword", key="gf_keyword", placeholder="title/urn/type")
    if st.sidebar.button("Reset filters"):
        st.session_state["gf_types"] = list(all_types)
        st.session_state["gf_statuses"] = list(all_statuses)
        st.session_state["gf_year_range"] = (int(yb[0]), int(yb[1]))
        st.session_state["gf_keyword"] = ""
        st.rerun()

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

