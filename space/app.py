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
    7. Fiscal Lab        - taxes registry + citizen simulation
    8. Notifications     - API change detection (read-only)
    9. Update Log        - manual update history
    10. Export           - CSV, JSON, JSONL downloads
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
import re

# Setup paths for imports
_app_dir = Path(__file__).parent
_root_dir = _app_dir.parent
sys.path.insert(0, str(_root_dir))
sys.path.insert(0, str(_app_dir))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

STATUS_ALIASES = {
    "in_force": "in_force",
    "vigente": "in_force",
    "v": "in_force",
    "abrogated": "abrogated",
    "abrogato": "abrogated",
    "abrogata": "abrogated",
    "a": "abrogated",
}

TAX_KEYWORDS = {
    "IVA": ["iva", "imposta sul valore aggiunto"],
    "IRPEF": ["irpef", "imposta sul reddito delle persone fisiche"],
    "IRES": ["ires", "imposta sul reddito delle societa", "imposta sul reddito delle società"],
    "IMU": ["imu", "imposta municipale propria"],
    "TARI": ["tari", "tassa sui rifiuti"],
    "TASI": ["tasi", "tributo per i servizi indivisibili"],
    "Bollo": ["imposta di bollo", "bollo auto", "bollo"],
    "Registro": ["imposta di registro"],
    "Accise": ["accisa", "accise"],
    "Canone RAI": ["canone rai", "canone televisivo"],
    "Contributi Previdenziali": ["contributi previdenziali", "contributo inps", "inps"],
    "Addizionale Regionale": ["addizionale regionale"],
    "Addizionale Comunale": ["addizionale comunale"],
    "Imposta di Successione": ["imposta sulle successioni", "imposta di successione"],
    "Imposta Ipotecaria/Catastale": ["imposta ipotecaria", "imposta catastale"],
}

TAX_CONTEXT = {
    "IVA": "Colpisce consumi quotidiani: spesa, beni e servizi. Aliquote ridotte su beni essenziali.",
    "IRPEF": "Tassa principale sul reddito delle persone fisiche, applicata per scaglioni.",
    "IRES": "Imposta sul reddito delle societa; incide sui prezzi finali tramite costi d'impresa.",
    "IMU": "Imposta locale sugli immobili diversi dall'abitazione principale (con eccezioni).",
    "TARI": "Copre i costi del servizio rifiuti del Comune.",
    "TASI": "Tributo locale sui servizi comunali indivisibili (storicamente variabile).",
    "Bollo": "Imposta su atti/documenti e, in casi specifici, veicoli.",
    "Registro": "Imposta su registrazione di atti (es. locazioni, compravendite).",
    "Accise": "Imposte indirette su prodotti specifici (es. carburanti, energia, tabacchi).",
    "Canone RAI": "Contributo destinato al servizio radiotelevisivo pubblico.",
    "Contributi Previdenziali": "Prelievo finalizzato a pensioni e tutele previdenziali.",
    "Addizionale Regionale": "Quota aggiuntiva regionale sul reddito personale.",
    "Addizionale Comunale": "Quota aggiuntiva comunale sul reddito personale.",
    "Imposta di Successione": "Imposta sul trasferimento di patrimonio per successione.",
    "Imposta Ipotecaria/Catastale": "Imposte collegate a formalita immobiliari e catasto.",
}


def _normalize_status(raw_status: str | None) -> str:
    if not raw_status:
        return "unknown"
    key = str(raw_status).strip().lower()
    return STATUS_ALIASES.get(key, key)


def _status_label(raw_status: str | None) -> str:
    norm = _normalize_status(raw_status)
    if norm == "in_force":
        return "⚡ In vigore"
    if norm == "abrogated":
        return "🚫 Abrogato"
    return f"❓ {raw_status or 'N/A'}"


def _full_laws_query() -> str:
    # No hard cap by default: load full dataset for complete visualization.
    return (
        "SELECT urn, title, type, date, year, status, article_count, "
        "text_length, importance_score FROM laws ORDER BY year DESC"
    )


def _extract_euro_amounts(text: str) -> List[float]:
    if not text:
        return []
    matches = re.findall(r"(?:€\s*|eur\s*)(\d{1,3}(?:[\.,]\d{3})*(?:[\.,]\d{1,2})?|\d+(?:[\.,]\d{1,2})?)", text.lower())
    values = []
    for m in matches:
        val = m.replace('.', '').replace(',', '.')
        try:
            values.append(float(val))
        except Exception:
            continue
    return values


def _extract_tax_labels(text: str) -> List[str]:
    txt = (text or "").lower()
    labels = []
    for label, kws in TAX_KEYWORDS.items():
        if any(kw in txt for kw in kws):
            labels.append(label)
    return labels


def _short_context(text: str, tax_label: str) -> str:
    txt = (text or "")
    low = txt.lower()
    for kw in TAX_KEYWORDS.get(tax_label, []):
        idx = low.find(kw)
        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(txt), idx + 220)
            excerpt = txt[start:end].replace("\n", " ").strip()
            return excerpt[:280]
    return ""


@st.cache_data(ttl=7200, show_spinner="Building fiscal registry from full dataset...")
def _get_fiscal_registry(db_path: str):
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    where_terms = []
    params = []
    for kws in TAX_KEYWORDS.values():
        for kw in kws:
            where_terms.append("LOWER(text) LIKE ?")
            params.append(f"%{kw}%")
    sql = (
        "SELECT urn, title, type, year, date, status, text "
        "FROM laws WHERE " + " OR ".join(where_terms) + " ORDER BY year DESC"
    )
    rows = conn.execute(sql, tuple(params)).fetchall()
    conn.close()

    per_law = []
    tax_rows = []
    for r in rows:
        item = dict(r)
        labels = _extract_tax_labels(item.get("text", ""))
        if not labels:
            continue
        nstatus = _normalize_status(item.get("status"))
        amounts = _extract_euro_amounts(item.get("text", ""))
        per_law.append({
            "urn": item.get("urn"),
            "title": item.get("title"),
            "type": item.get("type"),
            "year": item.get("year"),
            "date": item.get("date"),
            "status": nstatus,
            "taxes": labels,
            "amount_mentions": len(amounts),
            "max_amount_mentioned": max(amounts) if amounts else None,
        })
        for lab in labels:
            tax_rows.append({
                "tax": lab,
                "urn": item.get("urn"),
                "title": item.get("title"),
                "year": item.get("year"),
                "status": nstatus,
                "context": _short_context(item.get("text", ""), lab),
                "amount_mentions": len(amounts),
            })

    return per_law, tax_rows

# Database connection (static - pre-built DB ships with the Space)

def get_db_paths():
    """Generate database search paths (works in Docker, local dev, and HF Spaces)."""
    _app_file = Path(__file__)
    _app_dir = _app_file.parent
    _root_dir = _app_dir.parent

    dataset_repo = os.environ.get("HF_DATASET_NAME", "").strip()
    if "/" in dataset_repo:
        ds_owner, ds_name = dataset_repo.split("/", 1)
    else:
        ds_owner = os.environ.get("HF_DATASET_OWNER", "diatribe00")
        ds_name = dataset_repo or "normattiva-data"
    
    hf_cache_hub = Path.home() / '.cache' / 'huggingface' / 'hub'
    
    # Scan HF hub cache for any normattiva dataset snapshot that has laws.db
    hf_cached_paths = []
    if hf_cache_hub.exists():
        cache_glob = f"datasets--{ds_owner}--{ds_name}/snapshots/*/data/laws.db"
        for snap in hf_cache_hub.glob(cache_glob):
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
        dataset_repo = os.environ.get("HF_DATASET_NAME", "").strip()
        if "/" in dataset_repo:
            repo_id = dataset_repo
        else:
            owner = os.environ.get("HF_DATASET_OWNER", "diatribe00")
            name = dataset_repo or "normattiva-data"
            repo_id = f"{owner}/{name}"

        logger.info(f"Downloading database from HF Dataset {repo_id} (this may take ~5 min)...")
        cached = hf_hub_download(
            repo_id=repo_id,
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

        # Persist detected API changes for auditability across restarts.
        db = load_db()
        if db and changes:
            for ch in changes:
                try:
                    exists = db.conn.execute(
                        "SELECT 1 FROM api_changes WHERE collection = ? AND old_etag IS ? AND new_etag = ? LIMIT 1",
                        (ch.get("collection"), ch.get("old_etag"), ch.get("new_etag")),
                    ).fetchone()
                    if not exists:
                        db.conn.execute(
                            "INSERT INTO api_changes (collection, old_etag, new_etag, status, preview_data) VALUES (?, ?, ?, 'pending', ?)",
                            (
                                ch.get("collection"),
                                ch.get("old_etag"),
                                ch.get("new_etag"),
                                json.dumps({"num_acts": ch.get("num_acts", 0), "detected_at": ch.get("detected_at")}),
                            ),
                        )
                except Exception:
                    continue
            try:
                db.conn.commit()
            except Exception:
                pass

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
    page_title="Italian Legal Lab",
    page_icon="\U0001f1ee\U0001f1f9",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("\U0001f1ee\U0001f1f9 Italian Legal Lab")
st.markdown("Full-spectrum Italian law intelligence: Normattiva datasets, SIOPE+ finance APIs, and institutional data sources.")

# ---- App profile selection ---------------------------------------------
# Controls which pages are exposed and which dataset is used by default.
APP_PROFILE = os.environ.get("APP_PROFILE", "").lower().strip()
HF_DATASET_NAME = os.environ.get("HF_DATASET_NAME", "").strip()
_env_space = os.environ.get("HF_SPACE_ID") or os.environ.get("SPACE_NAME") or os.environ.get("SPACE") or ""
SPACE_NAME = str(_env_space).lower()


def _default_dataset_repo(profile: str) -> str:
    mapping = {
        "search": "diatribe00/normattiva-data",
        "lab": "diatribe00/normattiva-lab-data",
        "italianlab": "diatribe00/italian-legal-lab-data",
    }
    return mapping.get(profile, "diatribe00/normattiva-data")

if not APP_PROFILE:
    if "italian" in HF_DATASET_NAME or "italian" in SPACE_NAME or "legal" in HF_DATASET_NAME:
        APP_PROFILE = "italianlab"
    elif "multivigente" in HF_DATASET_NAME or "multivigente" in SPACE_NAME or ("lab" in SPACE_NAME and "normattiva" in SPACE_NAME):
        APP_PROFILE = "lab"
    else:
        APP_PROFILE = "search"

IS_SEARCH = APP_PROFILE == "search"
IS_LAB = APP_PROFILE == "lab"
IS_ITALIAN_LAB = APP_PROFILE == "italianlab"
ACTIVE_DATASET_REPO = HF_DATASET_NAME or _default_dataset_repo(APP_PROFILE)

# Show active profile in the sidebar for clarity
st.sidebar.info(f"Running profile: `{APP_PROFILE}`\nDataset: `{ACTIVE_DATASET_REPO}`")
# ------------------------------------------------------------------------


# HELPERS

@st.cache_data(ttl=3600, show_spinner="Loading laws...")
def _get_laws_cached(db_path: str):
    """Cached law loading — separated from session state for cache key."""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            _full_laws_query()
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
                _full_laws_query()
            ).fetchall()
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


# PAGES

def page_dashboard():
    st.header("\U0001f4ca Dashboard")
    db = load_db()
    laws = _get_laws()
    
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

    norm_statuses = [_normalize_status(l.get("status")) for l in laws]
    in_force_count = sum(1 for s in norm_statuses if s == "in_force")
    abrogated_count = sum(1 for s in norm_statuses if s == "abrogated")
    sc1, sc2 = st.columns(2)
    sc1.metric("In vigore", f"{in_force_count:,}")
    sc2.metric("Abrogati", f"{abrogated_count:,}")
    st.caption("Status harmonization active: vigente/in_force and abrogato/abrogated are unified.")

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

    query = st.text_input(
        "Search Italian law (full-text with BM25 ranking):",
        placeholder="costituzione diritti fondamentali"
    )

    with st.expander("Advanced Filters"):
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            filter_type = st.text_input("Law type (e.g. legge, decreto)")
        with fc2:
            filter_year_from = st.number_input("Year from", min_value=1800,
                                                max_value=2100, value=1800)
        with fc3:
            filter_year_to = st.number_input("Year to", min_value=1800,
                                              max_value=2100, value=2100)
        with fc4:
            status_scope = st.selectbox(
                "Status scope",
                ["in_force", "abrogated", "all"],
                help="Default is in_force to keep vigente laws separated from abrogated ones."
            )

    result_limit = st.slider("Max results", 25, 500, 100, 25)

    if not query or len(query) < 2:
        st.info("Enter at least 2 characters to search.")
        return

    if db:
        try:
            results = db.search_fts(query, limit=result_limit)
            if status_scope != "all":
                results = [r for r in results if _normalize_status(r.get("status")) == status_scope]
            st.write(f"**Found {len(results)} results** (ranked by relevance)")
            for r in results:
                year = r.get("year", "?")
                law_type = str(r.get("type") or "")
                if filter_type and filter_type.lower() not in law_type.lower():
                    continue
                if year not in (None, "?"):
                    try:
                        yi = int(year)
                        if yi < filter_year_from or yi > filter_year_to:
                            continue
                    except Exception:
                        pass
                status = _normalize_status(r.get("status", "in_force"))
                status_badge = " 🚫 *ABROGATO*" if status == "abrogated" else ""
                score_str = ""
                if r.get("relevance_score") is not None:
                    score_str = f" · Score: {float(r['relevance_score']):.2f}"
                with st.expander(f"{r.get('title', 'Untitled')} ({year}){score_str}{status_badge}"):
                    c1, c2 = st.columns([1, 3])
                    with c1:
                        st.write(f"**URN**: `{r.get('urn', 'N/A')}`")
                        st.write(f"**Type**: {r.get('type', 'N/A')}")
                        st.write(f"**Date**: {r.get('date', 'N/A')}")
                        st.write(f"**Status**: {_status_label(status)}")
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
        q = query.lower()
        results = [l for l in laws
                   if q in l.get("title", "").lower()
                   or q in l.get("text", "").lower()]
        if status_scope != "all":
            results = [l for l in results if _normalize_status(l.get("status")) == status_scope]
        results = results[:result_limit]
        st.write(f"**Found {len(results)} results** (simple text match)")
        for law in results:
            with st.expander(
                f"{law.get('title', 'Untitled')} ({law.get('year', '?')})"
            ):
                st.write(f"**URN**: `{law.get('urn')}`")
                st.write(f"**Type**: {law.get('type')}")
                st.write(f"**Status**: {_status_label(law.get('status'))}")
                st.text_area("Text", law.get("text", "")[:800], height=150,
                             disabled=True, key=f"srch_jl_{law.get('urn','')}")


def page_rights_explorer():
    st.header("🧭 Citizen Rights Explorer")
    st.caption(
        "Guided exploration of core rights and protections, based on the current Normattiva dataset. "
        "Use this to quickly discover vigente norms without confusion from abrogated laws."
    )

    db = load_db()
    if not db:
        st.info("Database required for rights explorer.")
        return

    topics = {
        "Diritto alla salute": "diritto alla salute servizio sanitario nazionale",
        "Diritto al lavoro": "diritto al lavoro statuto lavoratori",
        "Diritto all'istruzione": "diritto istruzione scuola universita",
        "Privacy e dati personali": "privacy protezione dati personali codice privacy",
        "Tutela del consumatore": "consumatore garanzia recesso pratiche commerciali",
        "Famiglia e minori": "famiglia minori responsabilita genitoriale",
        "Casa e proprieta": "proprieta abitazione locazione sfratto",
        "Tributi e diritti del contribuente": "contribuente statuto diritti fiscali imposta",
    }

    col1, col2 = st.columns([2, 3])
    with col1:
        topic = st.selectbox("Choose a rights topic", list(topics.keys()))
        include_abrogated = st.checkbox("Include abrogated laws", value=False)
        max_results = st.slider("Max retrieved laws", 5, 50, 15, 5)
        run = st.button("Explore topic")
    with col2:
        st.markdown(
            "**How to read results**\n"
            "- Prioritize ⚡ In vigore for current enforceable rights.\n"
            "- Use 🚫 Abrogato only for legal history/comparison."
        )

    if run:
        query = topics[topic]
        results = db.search_fts(query, limit=200)
        if not include_abrogated:
            results = [r for r in results if _normalize_status(r.get("status")) == "in_force"]
        results = results[:max_results]

        if not results:
            st.warning("No matching laws found for this topic.")
            return

        st.subheader(f"Results for: {topic}")
        vcount = sum(1 for r in results if _normalize_status(r.get("status")) == "in_force")
        acount = sum(1 for r in results if _normalize_status(r.get("status")) == "abrogated")
        c1, c2 = st.columns(2)
        c1.metric("Vigenti", vcount)
        c2.metric("Abrogati", acount)

        for r in results:
            status = _normalize_status(r.get("status"))
            badge = "⚡" if status == "in_force" else "🚫"
            with st.expander(f"{badge} {r.get('title', 'N/A')} ({r.get('year', 'N/A')})"):
                st.write(f"**Status**: {_status_label(status)}")
                st.write(f"**Type**: {r.get('type', 'N/A')}")
                st.write(f"**URN**: `{r.get('urn', 'N/A')}`")
                snippet = r.get("snippet", "")
                if snippet:
                    st.markdown(f"**Snippet**: ...{snippet}...")


def _render_browse_table(laws: List[Dict], title: str, locked_status: str | None = None):
    st.header(title)
    if not laws:
        st.info("No data loaded.")
        return

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        all_types = sorted(set(l.get("type", "unknown") for l in laws))
        sel_type = st.selectbox("Type", ["All"] + all_types, key=f"{title}-type")
    with c2:
        all_years = sorted(set(l.get("year") for l in laws if l.get("year")))
        sel_year = st.selectbox("Year", ["All"] + all_years, key=f"{title}-year")
    with c3:
        if locked_status:
            st.write("Status")
            st.info(locked_status)
            sel_status = locked_status
        else:
            all_statuses = sorted(set(_normalize_status(l.get("status", "vigente")) for l in laws))
            sel_status = st.selectbox("Status", ["All"] + all_statuses, key=f"{title}-status")
    with c4:
        sort_by = st.selectbox("Sort by", [
            "Year (newest)", "Year (oldest)", "Title A-Z", "Importance", "Articles"
        ], key=f"{title}-sort")

    filtered = laws
    if sel_type != "All":
        filtered = [l for l in filtered if l.get("type") == sel_type]
    if sel_year != "All":
        filtered = [l for l in filtered if l.get("year") == sel_year]
    if sel_status != "All":
        filtered = [l for l in filtered if _normalize_status(l.get("status")) == sel_status]

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
    page_num = st.number_input("Page", 1, total_pages, 1, key=f"{title}-page")
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
                st.write(f"**Status**: {_status_label(law.get('status'))}")
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
                             key=f"{title}_{law.get('urn', start)}")


def page_browse():
    laws = _get_laws()
    _render_browse_table(laws, "📋 Browse Laws (All)", locked_status=None)


def page_vigenti():
    laws = [l for l in _get_laws() if _normalize_status(l.get("status")) == "in_force"]
    _render_browse_table(laws, "⚡ Vigenti Laws", locked_status="in_force")


def page_abrogated():
    laws = [l for l in _get_laws() if _normalize_status(l.get("status")) == "abrogated"]
    _render_browse_table(laws, "🚫 Abrogated Laws", locked_status="abrogated")


def page_llm_lab():
    st.header("🤖 LLM Assistant Lab")
    st.caption(
        "Experimental legal assistant over the full Normattiva dataset. "
        "This lab retrieves the most relevant laws and builds a status-aware answer draft."
    )

    db = load_db()
    if not db:
        st.info("Database required for LLM lab.")
        return

    if "llm_chat" not in st.session_state:
        st.session_state["llm_chat"] = []

    with st.expander("Assistant setup"):
        top_k = st.slider("Top laws to retrieve", 3, 20, 8)
        include_abrogated = st.checkbox("Include abrogated laws in evidence", value=False)

    user_q = st.text_input("Ask a legal question", placeholder="Esempio: Qual e lo stato vigente della disciplina IVA?")
    if st.button("Analyze question") and user_q:
        results = db.search_fts(user_q, limit=100)
        if not include_abrogated:
            results = [r for r in results if _normalize_status(r.get("status")) == "in_force"]
        evidence = results[:top_k]

        if not evidence:
            st.warning("No matching laws found with current filters.")
        else:
            vig_count = sum(1 for r in evidence if _normalize_status(r.get("status")) == "in_force")
            abg_count = sum(1 for r in evidence if _normalize_status(r.get("status")) == "abrogated")
            answer_lines = [
                f"Query: {user_q}",
                f"Evidence considered: {len(evidence)} laws ({vig_count} vigenti, {abg_count} abrogate).",
                "",
                "Proposed status-aware answer:",
            ]
            for i, r in enumerate(evidence, 1):
                answer_lines.append(
                    f"{i}. [{_status_label(r.get('status'))}] {r.get('title', 'N/A')} ({r.get('year', 'N/A')}) - {r.get('urn', 'N/A')}"
                )

            answer_text = "\n".join(answer_lines)
            st.session_state["llm_chat"].append({"q": user_q, "a": answer_text, "e": evidence})

    if st.session_state["llm_chat"]:
        st.subheader("Conversation")
        for idx, item in enumerate(reversed(st.session_state["llm_chat"]), 1):
            with st.expander(f"Q{idx}: {item['q']}"):
                st.text(item["a"])
                df = pd.DataFrame([
                    {
                        "status": _normalize_status(r.get("status")),
                        "title": r.get("title"),
                        "year": r.get("year"),
                        "urn": r.get("urn"),
                    }
                    for r in item["e"]
                ])
                st.dataframe(df, width='stretch', hide_index=True)


@st.cache_data(ttl=1800, show_spinner=False)
def _http_get_json(url: str, params: dict | None = None):
    import requests
    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=1800, show_spinner=False)
def _http_get_text(url: str):
    import requests
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    return r.text


def _dataset_track(rec: dict) -> str:
    src = str(rec.get("source_collection") or "").lower()
    status = _normalize_status(rec.get("status"))
    if "abrogat" in src or status == "abrogated":
        return "abrogato"
    if "multivigente" in src:
        return "multivigente"
    return "vigente"


def _ensure_status_timeline_schema(db):
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS status_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT DEFAULT CURRENT_TIMESTAMP,
            note TEXT
        )
        """
    )
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS law_status_snapshot (
            snapshot_id INTEGER,
            urn TEXT,
            title TEXT,
            year INTEGER,
            status TEXT,
            track TEXT,
            source_collection TEXT,
            PRIMARY KEY (snapshot_id, urn)
        )
        """
    )
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS law_status_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            urn TEXT,
            title TEXT,
            year INTEGER,
            from_status TEXT,
            to_status TEXT,
            from_track TEXT,
            to_track TEXT,
            snapshot_from INTEGER,
            snapshot_to INTEGER,
            detected_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.conn.commit()


def _capture_status_snapshot(db, note: str = "manual"):
    _ensure_status_timeline_schema(db)

    db.conn.execute("INSERT INTO status_snapshots (note) VALUES (?)", (note,))
    snap_id = db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    db.conn.execute(
        """
        INSERT INTO law_status_snapshot
            (snapshot_id, urn, title, year, status, track, source_collection)
        SELECT
            ?,
            urn,
            title,
            year,
            LOWER(COALESCE(status, '')),
            CASE
                WHEN LOWER(COALESCE(source_collection, '')) LIKE '%abrogat%'
                     OR LOWER(COALESCE(status, '')) IN ('abrogated', 'abrogato', 'abrogata', 'a')
                    THEN 'abrogato'
                WHEN LOWER(COALESCE(source_collection, '')) LIKE '%multivigente%'
                    THEN 'multivigente'
                ELSE 'vigente'
            END,
            COALESCE(source_collection, '')
        FROM laws
        """,
        (snap_id,),
    )

    prev = db.conn.execute(
        "SELECT id FROM status_snapshots WHERE id < ? ORDER BY id DESC LIMIT 1",
        (snap_id,),
    ).fetchone()

    transitions = 0
    if prev:
        prev_id = prev[0]
        db.conn.execute(
            """
            INSERT INTO law_status_transitions
                (urn, title, year, from_status, to_status, from_track, to_track, snapshot_from, snapshot_to)
            SELECT
                c.urn,
                c.title,
                c.year,
                p.status,
                c.status,
                p.track,
                c.track,
                ?,
                ?
            FROM law_status_snapshot p
            JOIN law_status_snapshot c ON p.urn = c.urn
            WHERE p.snapshot_id = ?
              AND c.snapshot_id = ?
              AND (
                    COALESCE(p.status, '') <> COALESCE(c.status, '')
                 OR COALESCE(p.track, '') <> COALESCE(c.track, '')
              )
            """,
            (prev_id, snap_id, prev_id, snap_id),
        )
        transitions = db.conn.execute(
            "SELECT COUNT(*) FROM law_status_transitions WHERE snapshot_from = ? AND snapshot_to = ?",
            (prev_id, snap_id),
        ).fetchone()[0]

    db.conn.commit()

    total = db.conn.execute(
        "SELECT COUNT(*) FROM law_status_snapshot WHERE snapshot_id = ?",
        (snap_id,),
    ).fetchone()[0]
    return {"snapshot_id": snap_id, "laws_captured": total, "transitions": transitions}


def _load_status_snapshots(db):
    _ensure_status_timeline_schema(db)
    rows = db.conn.execute(
        "SELECT id, captured_at, note FROM status_snapshots ORDER BY id DESC LIMIT 50"
    ).fetchall()
    return [dict(r) for r in rows]


def _load_status_transitions(db, limit: int = 1000):
    _ensure_status_timeline_schema(db)
    rows = db.conn.execute(
        """
        SELECT id, detected_at, urn, title, year, from_status, to_status, from_track, to_track, snapshot_from, snapshot_to
        FROM law_status_transitions
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def page_italian_legal_lab():
    st.header("🧪 Italian Legal Lab")
    st.caption(
        "Unified exploration hub across full Normattiva datasets, SIOPE+ operational data, "
        "institutional statistics, and parliamentary sources."
    )

    db = load_db()
    if not db:
        st.info("Database required for Legal Lab.")
        return

    tabs = st.tabs([
        "🧭 Mission Control",
        "⚖️ Normattiva Status Hub",
        "🔎 Full Normattiva Experience",
        "🔄 Status Timeline",
        "🏦 SIOPE+ (Bank of Italy)",
        "🗞️ Gazzetta Ufficiale Daily",
        "📊 ISTAT SDMX",
        "🏛️ Senato AKN Bulk",
        "🧩 Institutional APIs",
    ])

    with tabs[0]:
        st.subheader("Italian Legal Lab control center")
        st.markdown(
            "This Space is now designed as the full legal analysis hub: "
            "Normattiva core (vigente/multivigente/abrogato), fiscal impact, institutional data, and exports."
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Active Profile", APP_PROFILE)
        c2.metric("Dataset Repo", ACTIVE_DATASET_REPO)
        c3.metric("Target Space", os.environ.get("HF_SPACE_ID") or os.environ.get("SPACE_NAME") or "N/A")

        st.caption("Quick actions")
        qa1, qa2, qa3, qa4 = st.columns(4)
        if qa1.button("Open Search", key="lab-go-search"):
            st.session_state["goto_page"] = "🔍 Search"
            st.rerun()
        if qa2.button("Open Vigenti", key="lab-go-vigenti"):
            st.session_state["goto_page"] = "⚡ Vigenti"
            st.rerun()
        if qa3.button("Open Abrogati", key="lab-go-abrogati"):
            st.session_state["goto_page"] = "🚫 Abrogati"
            st.rerun()
        if qa4.button("Open Citations", key="lab-go-citations"):
            st.session_state["goto_page"] = "🔗 Citations"
            st.rerun()

        st.info(
            "Recommended deployment mapping: \n"
            "- opennormattiva-search -> diatribe00/normattiva-data\n"
            "- opennormattiva-lab -> diatribe00/normattiva-lab-data\n"
            "- italian-legal-lab -> diatribe00/italian-legal-lab-data"
        )

    with tabs[1]:
        st.subheader("Normattiva: vigente / multivigente / abrogato")
        laws = _get_laws()
        if not laws:
            st.info("No laws loaded.")
        else:
            df = pd.DataFrame(laws)
            if "source_collection" not in df.columns:
                try:
                    rows = db.conn.execute("SELECT urn, source_collection FROM laws").fetchall()
                    sc = pd.DataFrame([dict(r) for r in rows])
                    df = df.merge(sc, on="urn", how="left")
                except Exception:
                    df["source_collection"] = ""
            df["track"] = df.apply(_dataset_track, axis=1)

            c1, c2, c3 = st.columns(3)
            c1.metric("Vigente", int((df["track"] == "vigente").sum()))
            c2.metric("Multivigente", int((df["track"] == "multivigente").sum()))
            c3.metric("Abrogato", int((df["track"] == "abrogato").sum()))

            fig = px.pie(df, names="track", title="Dataset status tracks")
            st.plotly_chart(fig, width='stretch')

            sel_track = st.selectbox("Explore track", ["vigente", "multivigente", "abrogato"])
            view = df[df["track"] == sel_track].copy().sort_values("year", ascending=False)
            st.write(f"Showing {len(view):,} laws in track: {sel_track}")
            st.dataframe(
                view[["year", "type", "title", "status", "source_collection", "urn"]].head(200),
                width='stretch',
                hide_index=True,
            )

            st.divider()
            st.subheader("Normattiva API live collection check")
            st.caption("Checks whether API collections expose vigente/multivigente/abrogato streams.")
            if st.button("Run API collection scan"):
                try:
                    from normattiva_api_client import NormattivaAPI
                    api = NormattivaAPI(timeout_s=15, retries=1)
                    cat = api.get_collection_catalogue()
                    rows = []
                    for c in cat:
                        nm = c.get("nomeCollezione", c.get("nome", ""))
                        low = str(nm).lower()
                        if any(k in low for k in ["vigent", "abrog", "multivigent"]):
                            rows.append({
                                "collection": nm,
                                "acts": c.get("numeroAtti", 0),
                                "key": "multivigente" if "multivigent" in low else ("abrogato" if "abrog" in low else "vigente"),
                            })
                    if rows:
                        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
                    else:
                        st.info("No explicit multivigente collection name returned in current catalogue.")
                except Exception as e:
                    st.error(f"API scan failed: {e}")

    with tabs[2]:
        st.subheader("Full Normattiva experience")
        st.caption("Direct access to the full analysis stack available in this Space.")
        r1, r2, r3 = st.columns(3)
        if r1.button("🔍 Advanced Search", key="lab-open-search"):
            st.session_state["goto_page"] = "🔍 Search"
            st.rerun()
        if r2.button("📋 Browse All Laws", key="lab-open-browse"):
            st.session_state["goto_page"] = "📋 Browse (All)"
            st.rerun()
        if r3.button("📖 Law Detail", key="lab-open-detail"):
            st.session_state["goto_page"] = "📖 Law Detail"
            st.rerun()

        r4, r5, r6 = st.columns(3)
        if r4.button("🔗 Citation Network", key="lab-open-cit-net"):
            st.session_state["goto_page"] = "🔗 Citations"
            st.rerun()
        if r5.button("🏛️ Domain Analytics", key="lab-open-domains"):
            st.session_state["goto_page"] = "🏛️ Domains"
            st.rerun()
        if r6.button("📥 Export Studio", key="lab-open-export"):
            st.session_state["goto_page"] = "📥 Export"
            st.rerun()

    with tabs[3]:
        st.subheader("Status transition timeline (vigente ↔ abrogato / track changes)")
        st.caption(
            "Capture periodic status snapshots and detect transitions per URN. "
            "This enables auditable change tracking over time."
        )

        note = st.text_input("Snapshot note", value="manual-check", key="timeline-note")
        if st.button("Capture new status snapshot"):
            try:
                result = _capture_status_snapshot(db, note=note)
                st.success(
                    f"Snapshot #{result['snapshot_id']} captured: {result['laws_captured']:,} laws, "
                    f"{result['transitions']:,} transitions vs previous snapshot."
                )
            except Exception as e:
                st.error(f"Snapshot capture failed: {e}")

        snaps = _load_status_snapshots(db)
        if snaps:
            st.write("Recent snapshots")
            st.dataframe(pd.DataFrame(snaps), width='stretch', hide_index=True)
        else:
            st.info("No snapshots captured yet.")

        transitions = _load_status_transitions(db, limit=2000)
        if transitions:
            df_t = pd.DataFrame(transitions)
            st.write(f"Recent transitions: {len(df_t):,}")
            st.dataframe(
                df_t[["detected_at", "year", "title", "from_status", "to_status", "from_track", "to_track", "urn"]],
                width='stretch',
                hide_index=True,
            )

            by_snap = (
                df_t.groupby(["snapshot_to"]).size().reset_index(name="transition_count").sort_values("snapshot_to")
            )
            fig = px.bar(by_snap, x="snapshot_to", y="transition_count", title="Transitions per snapshot")
            st.plotly_chart(fig, width='stretch')
        else:
            st.info("No transitions detected yet. Capture at least two snapshots to compute diffs.")

    with tabs[4]:
        st.subheader("SIOPE+ API integration (Bank of Italy)")
        st.caption(
            "SIOPE+ exposes treasury and payment-exchange operations and generally requires TLS client certificates. "
            "Use this panel to inspect docs/specs and test public metadata endpoints."
        )

        st.markdown(
            "**Operational note**\n"
            "Production SIOPE+ calls are authenticated (mTLS). In this Space, only public or user-provided reachable endpoints can be probed."
        )

        siope_spec_url = st.text_input(
            "SIOPE+ spec/document URL",
            value="https://developers.italia.it/it/api/siope",
            key="siope-spec-url"
        )
        if st.button("Fetch SIOPE+ documentation page", key="fetch-siope-doc"):
            try:
                doc_txt = _http_get_text(siope_spec_url)
                st.code(doc_txt[:12000], language="html")
            except Exception as e:
                st.error(f"SIOPE+ documentation fetch failed: {e}")

        st.subheader("SIOPE+ endpoint constructor")
        c1, c2, c3 = st.columns(3)
        id_a2a = c1.text_input("idA2A", value="DEMO", key="siope-id-a2a")
        cod_ente = c2.text_input("codEnte", value="000000", key="siope-cod-ente")
        cod_banca = c3.text_input("codBanca", value="00000", key="siope-cod-banca")
        op = st.selectbox(
            "Operation template",
            [
                "PA giornale list: /{idA2A}/PA/{codEnte}/giornale/",
                "PA flusso list: /{idA2A}/PA/{codEnte}/flusso",
                "PA disponibilita list: /{idA2A}/PA/{codEnte}/disponibilita",
                "BT flusso list: /{idA2A}/BT/{codBanca}/flusso/",
            ],
            key="siope-op-template"
        )

        built = (
            op.replace("{idA2A}", id_a2a)
            .replace("{codEnte}", cod_ente)
            .replace("{codBanca}", cod_banca)
        )
        st.code(built, language="text")
        st.caption("Use this generated path against your certified SIOPE+ base server in secure environments.")

    with tabs[5]:
        st.subheader("Daily Gazzetta Ufficiale feed")
        rss_url = st.text_input(
            "RSS URL",
            value="https://www.gazzettaufficiale.it/rss/serie_generale.xml",
            key="gu-rss-url"
        )
        if st.button("Fetch daily Gazzetta flow"):
            try:
                from xml.etree import ElementTree as ET
                xml_text = _http_get_text(rss_url)
                root = ET.fromstring(xml_text)
                items = []
                for item in root.findall(".//item"):
                    items.append({
                        "title": item.findtext("title"),
                        "pubDate": item.findtext("pubDate"),
                        "link": item.findtext("link"),
                        "description": (item.findtext("description") or "")[:240],
                    })
                if not items:
                    st.warning("No RSS items found.")
                else:
                    st.success(f"Fetched {len(items)} daily entries.")
                    st.dataframe(pd.DataFrame(items), width='stretch', hide_index=True)
            except Exception as e:
                st.error(f"Gazzetta fetch failed: {e}")

    with tabs[6]:
        st.subheader("ISTAT SDMX preview")
        istat_url = st.text_input(
            "ISTAT endpoint",
            value="https://sdmx.istat.it/SDMXWS/rest/dataflow",
            key="istat-url"
        )
        if st.button("Fetch ISTAT"):
            try:
                text = _http_get_text(istat_url)
                st.code(text[:12000], language="xml")
            except Exception as e:
                st.error(f"ISTAT fetch failed: {e}")

    with tabs[7]:
        st.subheader("Senato Akoma Ntoso bulk explorer")
        st.caption("Browsable view over SenatoDellaRepubblica/AkomaNtosoBulkData repository contents.")
        path = st.text_input("Repository path", value="", key="senato-path")
        if st.button("List AKN repository path"):
            try:
                api_url = f"https://api.github.com/repos/SenatoDellaRepubblica/AkomaNtosoBulkData/contents/{path}".rstrip("/")
                data = _http_get_json(api_url)
                if isinstance(data, dict):
                    data = [data]
                rows = [{"name": x.get("name"), "type": x.get("type"), "size": x.get("size"), "download_url": x.get("download_url")} for x in data]
                st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
            except Exception as e:
                st.error(f"Senato AKN listing failed: {e}")

    with tabs[8]:
        st.subheader("Institutional APIs catalog")
        catalog = [
            {"source": "OpenGazzetta (openGA)", "url": "https://api.gazzettaufficiale.it/"},
            {"source": "Corte Costituzionale", "url": "https://www.cortecostituzionale.it/actionSchedePronunce.do"},
            {"source": "Senato Open Data", "url": "https://dati.senato.it/"},
            {"source": "Camera Open Data", "url": "https://dati.camera.it/"},
            {"source": "Gov.it datasets", "url": "https://www.dati.gov.it/"},
            {"source": "ANAC (public officials/anticorruzione)", "url": "https://dati.anticorruzione.it/"},
            {"source": "MEF Open Data", "url": "https://www1.finanze.gov.it/finanze3/opendata/"},
        ]
        df_cat = pd.DataFrame(catalog)
        st.dataframe(df_cat, width='stretch', hide_index=True)

        st.subheader("Public officials legislation quick explorer")
        preset_q = st.selectbox(
            "Preset query",
            [
                "pubblico impiego", "incompatibilita incarichi pubblici", "anticorruzione", "trasparenza amministrativa",
                "responsabilita dirigenza pubblica", "contabilita pubblica"
            ],
            key="officials-preset"
        )
        if st.button("Search preset in Normattiva"):
            try:
                out = db.search_fts(preset_q, limit=30)
                out = [r for r in out if _normalize_status(r.get("status")) == "in_force"]
                st.dataframe(pd.DataFrame([
                    {
                        "status": _status_label(r.get("status")),
                        "year": r.get("year"),
                        "type": r.get("type"),
                        "title": r.get("title"),
                        "urn": r.get("urn"),
                    }
                    for r in out
                ]), width='stretch', hide_index=True)
            except Exception as e:
                st.error(f"Preset search failed: {e}")


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
            st.write(f"**Status**: {_status_label(law.get('status'))}")
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


def page_fiscal_citizen_tax_lab():
    st.header("💶 Fiscal Burden Lab (Experimental)")
    st.caption(
        "Full-dataset fiscal scan: extracts tax-related laws (vigente + abrogato harmonized), "
        "surfaces citizen context, and provides a conservative minimum-tax simulator."
    )

    db = load_db()
    if not db:
        st.info("Database required for fiscal analysis.")
        return

    db_path = str(db.db_path) if hasattr(db, "db_path") else ""
    if not db_path:
        st.info("Database path unavailable for fiscal analysis.")
        return

    per_law, tax_rows = _get_fiscal_registry(db_path)
    if not tax_rows:
        st.info("No fiscal/tax references detected in the current dataset.")
        return

    tax_df = pd.DataFrame(tax_rows)
    law_df = pd.DataFrame(per_law)

    t1, t2, t3 = st.tabs([
        "📊 Registry Overview",
        "🧾 Imposed Taxes Registry",
        "🧮 Minimum Daily-Life Simulation",
    ])

    with t1:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fiscal Laws Found", f"{len(law_df):,}")
        c2.metric("Tax Mentions", f"{len(tax_df):,}")
        c3.metric("Unique Tax Types", tax_df["tax"].nunique())
        c4.metric("In Vigore Laws", f"{(law_df['status'] == 'in_force').sum():,}")

        status_tax = (
            tax_df.groupby(["tax", "status"]).size().reset_index(name="mentions")
        )
        fig = px.bar(
            status_tax,
            x="tax",
            y="mentions",
            color="status",
            title="Tax Mentions by Status (Harmonized)",
            barmode="stack",
            category_orders={"status": ["in_force", "abrogated", "unknown"]},
        )
        st.plotly_chart(fig, width='stretch')

        st.subheader("Citizen Context by Tax")
        context_rows = []
        for tax_name in sorted(tax_df["tax"].unique()):
            context_rows.append({
                "Tax": tax_name,
                "Citizen Context": TAX_CONTEXT.get(tax_name, "Contesto non classificato."),
            })
        st.dataframe(pd.DataFrame(context_rows), width='stretch', hide_index=True)

    with t2:
        st.write("Full registry of taxes detected across the corpus (with harmonized status labels).")

        agg = tax_df.groupby("tax").agg(
            laws=("urn", "nunique"),
            mentions=("tax", "count")
        ).reset_index().sort_values(["laws", "mentions"], ascending=False)
        st.dataframe(agg, width='stretch', hide_index=True)

        sel_tax = st.selectbox("Inspect tax", sorted(tax_df["tax"].unique()))
        sel_status = st.selectbox("Status filter", ["All", "in_force", "abrogated", "unknown"])

        view = tax_df[tax_df["tax"] == sel_tax]
        if sel_status != "All":
            view = view[view["status"] == sel_status]
        view = view.sort_values(["year", "title"], ascending=[False, True])

        st.write(f"{len(view):,} law rows for {sel_tax}.")
        st.dataframe(
            view[["year", "status", "title", "urn", "context", "amount_mentions"]],
            width='stretch',
            hide_index=True,
        )

    with t3:
        st.warning(
            "Experimental estimator: values are conservative assumptions for citizen awareness, "
            "not legal/tax advice."
        )

        col_a, col_b = st.columns(2)
        with col_a:
            annual_income = st.number_input("Annual gross income (€)", min_value=0.0, value=25000.0, step=500.0)
            annual_essential_spend = st.number_input("Annual essential spending (€)", min_value=0.0, value=12000.0, step=250.0)
            annual_fuel_liters = st.number_input("Annual fuel consumption (liters)", min_value=0.0, value=600.0, step=25.0)
        with col_b:
            min_irpef_rate = st.slider("Minimum IRPEF assumption (%)", 0.0, 43.0, 23.0, 0.5)
            min_iva_rate = st.slider("Minimum IVA assumption (%)", 0.0, 22.0, 4.0, 0.5)
            min_excise_per_liter = st.number_input("Minimum fuel excise assumption (€/liter)", min_value=0.0, value=0.10, step=0.01)

        no_tax_area = 8500.0
        taxable_income = max(0.0, annual_income - no_tax_area)
        irpef_component = taxable_income * (min_irpef_rate / 100.0)
        iva_component = annual_essential_spend * (min_iva_rate / 100.0)
        excise_component = annual_fuel_liters * min_excise_per_liter
        annual_min_tax = irpef_component + iva_component + excise_component
        daily_min_tax = annual_min_tax / 365.0 if annual_min_tax else 0.0

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("IRPEF (min est.)", f"€ {irpef_component:,.2f}")
        r2.metric("IVA (min est.)", f"€ {iva_component:,.2f}")
        r3.metric("Accise (min est.)", f"€ {excise_component:,.2f}")
        r4.metric("Daily minimum burden", f"€ {daily_min_tax:,.2f}")

        st.caption(
            "Formula: max(Income - €8,500, 0) × IRPEF_min + Essential Spend × IVA_min + Fuel Liters × Excise_min. "
            "Tune assumptions interactively to simulate policy sensitivity."
        )


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
    # Build a complete registry of pages and then expose only the subset
    # appropriate for the active `APP_PROFILE` (search / lab / italianlab).
    all_pages = {
        "📊 Dashboard": page_dashboard,
        "🧪 Italian Legal Lab": page_italian_legal_lab,
        "🧭 Rights Explorer": page_rights_explorer,
        "🇮🇹 Costituzione & Codici": page_costituzione,
        "🔍 Search": page_search,
        "⚡ Vigenti": page_vigenti,
        "🚫 Abrogati": page_abrogated,
        "📋 Browse (All)": page_browse,
        "🤖 LLM Lab": page_llm_lab,
        "📖 Law Detail": page_law_detail,
        "🔗 Citations": page_citations,
        "🏛️ Domains": page_domains,
        "💶 Fiscal Burden Lab": page_fiscal_citizen_tax_lab,
        "🔔 Notifications": page_notifications,
        "📝 Update Log": page_update_log,
        "📥 Export": page_export,
    }

    # Select visible pages per profile
    if IS_ITALIAN_LAB:
        pages = {
            "📊 Dashboard": all_pages["📊 Dashboard"],
            "🧪 Italian Legal Lab": all_pages["🧪 Italian Legal Lab"],
            "🔍 Search": all_pages["🔍 Search"],
            "⚡ Vigenti": all_pages["⚡ Vigenti"],
            "🚫 Abrogati": all_pages["🚫 Abrogati"],
            "📋 Browse (All)": all_pages["📋 Browse (All)"],
            "🧭 Rights Explorer": all_pages["🧭 Rights Explorer"],
            "🇮🇹 Costituzione & Codici": all_pages["🇮🇹 Costituzione & Codici"],
            "🔗 Citations": all_pages["🔗 Citations"],
            "🏛️ Domains": all_pages["🏛️ Domains"],
            "💶 Fiscal Burden Lab": all_pages["💶 Fiscal Burden Lab"],
            "🤖 LLM Lab": all_pages["🤖 LLM Lab"],
            "📖 Law Detail": all_pages["📖 Law Detail"],
            "🔔 Notifications": all_pages["🔔 Notifications"],
            "📝 Update Log": all_pages["📝 Update Log"],
            "📥 Export": all_pages["📥 Export"],
        }
        st.sidebar.success("Italian Legal Lab profile active — unified research hub.")
    elif IS_LAB:
        pages = {
            "📊 Dashboard": all_pages["📊 Dashboard"],
            "🔍 Search": all_pages["🔍 Search"],
            "⚡ Vigenti": all_pages["⚡ Vigenti"],
            "🚫 Abrogati": all_pages["🚫 Abrogati"],
            "📋 Browse (All)": all_pages["📋 Browse (All)"],
            "🤖 LLM Lab": all_pages["🤖 LLM Lab"],
            "💶 Fiscal Burden Lab": all_pages["💶 Fiscal Burden Lab"],
            "📖 Law Detail": all_pages["📖 Law Detail"],
            "🔔 Notifications": all_pages["🔔 Notifications"],
            "📝 Update Log": all_pages["📝 Update Log"],
            "📥 Export": all_pages["📥 Export"],
        }
        st.sidebar.success("Normattiva Lab profile active — multivigente dataset and developer tools.")
    else:
        pages = {
            "📊 Dashboard": all_pages["📊 Dashboard"],
            "🔍 Search": all_pages["🔍 Search"],
            "⚡ Vigenti": all_pages["⚡ Vigenti"],
            "🚫 Abrogati": all_pages["🚫 Abrogati"],
            "📋 Browse (All)": all_pages["📋 Browse (All)"],
            "🧭 Rights Explorer": all_pages["🧭 Rights Explorer"],
            "📖 Law Detail": all_pages["📖 Law Detail"],
            "🔗 Citations": all_pages["🔗 Citations"],
            "🏛️ Domains": all_pages["🏛️ Domains"],
            "🔔 Notifications": all_pages["🔔 Notifications"],
            "📝 Update Log": all_pages["📝 Update Log"],
            "📥 Export": all_pages["📥 Export"],
        }
        st.sidebar.success("OpenNormattiva Search profile active — vigente/abrogato focused.")

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

