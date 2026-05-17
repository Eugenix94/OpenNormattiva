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


def _status_explainer(raw_status: str | None) -> str:
    norm = _normalize_status(raw_status)
    if norm == "in_force":
        return "V (Vigente): this law is currently in force and applicable."
    if norm == "abrogated":
        return "O (Originale/Abrogata): historical text, no longer in force."
    return "Status unavailable in current record."


def _status_chip(raw_status: str | None) -> str:
    norm = _normalize_status(raw_status)
    if norm == "in_force":
        return "🟢 VIGENTE"
    if norm == "abrogated":
        return "🔴 ABROGATA"
    return "⚪ SCONOSCIUTO"


def _plain_language_summary(law: Dict) -> str:
    """Return a short citizen-friendly explanation for a law result."""
    status = _normalize_status(law.get("status"))
    status_text = "in vigore" if status == "in_force" else "non in vigore"
    law_type = (law.get("type") or "atto normativo").replace(".", " ")
    year = law.get("year") or "n/d"
    snippet = (law.get("snippet") or "").strip()
    text = ((law.get("text") or "") + " " + snippet).lower()

    court_signals = []
    if "corte costituzionale" in text:
        court_signals.append("Corte costituzionale")
    if "corte di cassazione" in text:
        court_signals.append("Cassazione")
    if "consiglio di stato" in text:
        court_signals.append("Consiglio di Stato")

    base = f"Questa è una {law_type} del {year}, attualmente {status_text}."
    if snippet:
        base += f" Tema principale: {snippet[:180].strip()}"
    if court_signals:
        base += f" Contiene riferimenti a: {', '.join(court_signals[:2])}."
    return base


def _source_collection_for_urn(db, urn: str) -> str:
    if not db or not urn:
        return ""
    try:
        row = db.conn.execute("SELECT source_collection FROM laws WHERE urn = ? LIMIT 1", (urn,)).fetchone()
        return row[0] if row and row[0] is not None else ""
    except Exception:
        return ""


def _render_source_transparency_box(db, law: Dict, query_terms: str = "") -> None:
    source_collection = law.get("source_collection") or _source_collection_for_urn(db, law.get("urn", ""))
    status = _status_label(law.get("status"))
    st.caption("Trasparenza fonte")
    st.info(
        "\n".join([
            f"Data atto: {law.get('date', 'N/A')}",
            f"Stato: {status}",
            f"Fonte dataset: {ACTIVE_DATASET_REPO}",
            f"Source collection: {source_collection or 'N/A'}",
            f"Termini usati: {query_terms or 'N/A'}",
        ])
    )


BEGINNER_GLOSSARY = {
    "Vigente": "A law that is currently in force and applicable.",
    "Abrogata": "A law repealed by a newer legal act.",
    "Multivigente": "Historical timeline of how the same law changed over time.",
    "URN": "Unique legal identifier used to reference a specific act.",
    "FTS": "Full-text search on the complete legal text corpus.",
}

SEARCH_GLOSSARY = {
    "Vigente": "Norma attualmente in vigore e applicabile.",
    "Giurisprudenza": "Orientamenti dei giudici (es. Corte costituzionale, Cassazione) richiamati nel testo normativo.",
    "URN": "Identificatore univoco dell'atto normativo.",
    "FTS": "Ricerca full-text su titolo e testo delle norme vigenti.",
    "Citazioni": "Collegamenti tra norme che citano o sono citate da altre norme.",
}

JURISPRUDENCE_TOPICS = {
    "Corte costituzionale": "corte costituzionale sentenza illegittimita costituzionale",
    "Corte di Cassazione": "corte di cassazione sezioni unite",
    "Consiglio di Stato": "consiglio di stato giurisdizione amministrativa",
    "TAR": "tribunale amministrativo regionale tar",
    "Corte dei conti": "corte dei conti responsabilita erariale",
    "CEDU e diritti fondamentali": "corte europea diritti dell'uomo cedu",
}

SCENARIO_PRESETS = {
    "Lavoro e licenziamento": "licenziamento lavoro subordinato giusta causa statuto lavoratori",
    "Affitto e casa": "locazione sfratto condominio canone",
    "Famiglia e minori": "responsabilita genitoriale separazione minori mantenimento",
    "Privacy e dati": "protezione dati personali gdpr privacy trattamento",
    "Multe e circolazione": "codice della strada sanzioni ricorso verbale",
    "Fisco di base": "irpef detrazioni dichiarazione contribuente",
}

WIZARD_INTENTS = {
    "Capire i miei diritti": "diritti tutela obblighi garanzie",
    "Verificare un obbligo": "adempimento obbligo termini sanzioni",
    "Preparare un ricorso": "ricorso opposizione termine procedura",
    "Capire documenti e scadenze": "termine comunicazione notifica documentazione",
}

DOCUMENT_TEMPLATES = {
    "Accesso agli atti (FOIA/L.241)": {
        "subject": "Richiesta di accesso agli atti",
        "body": (
            "Il/La sottoscritto/a {citizen_name}, residente in {city}, chiede accesso agli atti "
            "ai sensi della normativa vigente, con riferimento a: {topic}.\n\n"
            "Motivazione sintetica: {reason}.\n"
            "Amministrazione destinataria: {recipient}.\n"
            "Si chiede riscontro entro i termini di legge."
        ),
    },
    "Diffida semplice": {
        "subject": "Diffida ad adempiere",
        "body": (
            "Il/La sottoscritto/a {citizen_name} diffida {recipient} ad adempiere in relazione a: {topic}.\n\n"
            "Fatti essenziali: {reason}.\n"
            "Si invita ad adempiere entro {deadline_days} giorni dal ricevimento della presente."
        ),
    },
    "Opposizione a verbale": {
        "subject": "Opposizione a verbale",
        "body": (
            "Il/La sottoscritto/a {citizen_name} propone opposizione al verbale relativo a: {topic}.\n\n"
            "Ragioni principali: {reason}.\n"
            "Autorita/ufficio destinatario: {recipient}."
        ),
    },
    "Richiesta di chiarimenti a PA": {
        "subject": "Richiesta di chiarimenti",
        "body": (
            "Il/La sottoscritto/a {citizen_name} richiede chiarimenti in merito a: {topic}.\n\n"
            "Contesto: {reason}.\n"
            "Ente destinatario: {recipient}.\n"
            "Si richiede risposta nei tempi previsti."
        ),
    },
}


LAB_LESSONS = {
    "How to read a law card": {
        "query": "codice civile",
        "goal": "Identify title, type, year, URN, and current status.",
    },
    "How abrogation works": {
        "query": "abrogazione",
        "goal": "Compare vigente and abrogata acts and understand legal replacement.",
    },
    "How amendments change laws": {
        "query": "decreto legislativo",
        "goal": "Open timeline view and inspect version progression.",
    },
    "How to use citations": {
        "query": "responsabilita civile",
        "goal": "See which laws cite and are cited by the selected act.",
    },
}


def _find_multivigente_db_path() -> Path | None:
    mv_paths = [
        Path("/app/data/multivigente.db"),
        Path(__file__).parent.parent / "data" / "multivigente.db",
        Path(__file__).parent / "data" / "multivigente.db",
    ]
    return next((p for p in mv_paths if p.exists() and p.stat().st_size > 10_000_000), None)


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
        ds_name = dataset_repo or "normattivavigente-data"
    
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
            name = dataset_repo or "normattivavigente-data"
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


# ---- App profile selection (must run BEFORE set_page_config) ----------
# Controls which pages are exposed and which dataset is used by default.
APP_PROFILE = os.environ.get("APP_PROFILE", "").lower().strip()
HF_DATASET_NAME = os.environ.get("HF_DATASET_NAME", "").strip()
_env_space = os.environ.get("HF_SPACE_ID") or os.environ.get("SPACE_NAME") or os.environ.get("SPACE") or ""
SPACE_NAME = str(_env_space).lower()


def _default_dataset_repo(profile: str) -> str:
    mapping = {
        "search": "diatribe00/normattivavigente-data",
        "lab": "diatribe00/normattiva-lab-data",
        "italianlab": "diatribe00/italian-legal-lab-data",
    }
    return mapping.get(profile, "diatribe00/normattivavigente-data")

if not APP_PROFILE:
    if "italian" in HF_DATASET_NAME or "italian" in SPACE_NAME or "legal" in HF_DATASET_NAME:
        APP_PROFILE = "italianlab"
    elif "multivigente" in HF_DATASET_NAME or "multivigente" in SPACE_NAME or ("lab" in SPACE_NAME and "normattiva" in SPACE_NAME):
        APP_PROFILE = "lab"
    else:
        APP_PROFILE = "search"

# PAGE CONFIG

_PAGE_TITLE = (
    "Italian Legal Lab" if APP_PROFILE == "italianlab"
    else "OpenNormattiva Lab" if APP_PROFILE == "lab"
    else "NormattivaVigente"
)
_PAGE_ICON = "\U0001f1ee\U0001f1f9" if APP_PROFILE == "italianlab" else "\u2696\ufe0f"

st.set_page_config(
    page_title=_PAGE_TITLE,
    page_icon=_PAGE_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)


def _inject_accessibility_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --on-primary: #0b2545;
            --accent: #0a7a5a;
            --muted-bg: #f6f8fb;
        }
        .block-container {
            max-width: 1200px;
            padding-top: 1.2rem;
            padding-bottom: 2rem;
        }
        h1, h2, h3 {
            letter-spacing: 0.01em;
        }
        p, li, label, .stCaption {
            line-height: 1.5;
            font-size: 1rem;
        }
        .stMetric {
            background: var(--muted-bg);
            border-radius: 12px;
            padding: 0.6rem 0.8rem;
            border: 1px solid #e6eaf0;
        }
        [data-testid="stSidebar"] {
            border-right: 1px solid #e6eaf0;
        }
        [data-testid="stSidebar"] .stRadio > div {
            gap: 0.2rem;
        }
        .stButton > button {
            border-radius: 10px;
            font-weight: 600;
        }
        @media (max-width: 900px) {
            .block-container {
                padding-left: 0.9rem;
                padding-right: 0.9rem;
            }
            .stMetric {
                padding: 0.5rem 0.65rem;
            }
            .stButton > button {
                width: 100%;
            }
            [data-testid="stSidebar"] {
                min-width: 250px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


_inject_accessibility_styles()

if APP_PROFILE == "italianlab":
    st.title("\U0001f1ee\U0001f1f9 Italian Legal Lab")
    st.markdown("Full-spectrum Italian law intelligence: Normattiva datasets, SIOPE+ finance APIs, and institutional data sources.")
elif APP_PROFILE == "lab":
    st.title("\u2696\ufe0f OpenNormattiva Lab")
    st.markdown("VOOM corpus — **67,052 vigenti** + **123,859 abrogati** = **190,911 laws** total. Full-text search, citations, legislative history.")
else:
    st.title("\u2696\ufe0f NormattivaVigente")
    st.markdown("Ricerca sulle sole norme vigenti: full-text search sul corpus Normattiva in-force.")

IS_SEARCH = APP_PROFILE == "search"
IS_LAB = APP_PROFILE == "lab"
IS_ITALIAN_LAB = APP_PROFILE == "italianlab"
ACTIVE_DATASET_REPO = HF_DATASET_NAME or _default_dataset_repo(APP_PROFILE)

# Show active profile in the sidebar for clarity
if IS_SEARCH:
    st.sidebar.info(
        "Profilo attivo: cittadino (vigente)\n"
        f"Dataset: {ACTIVE_DATASET_REPO}"
    )
else:
    st.sidebar.info(f"Running profile: {APP_PROFILE}\nDataset: {ACTIVE_DATASET_REPO}")
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


# ─────────────────────────────────────────────────────────────────
# GROQ RAG HELPER
# ─────────────────────────────────────────────────────────────────

GROQ_MODELS = {
    "llama-3.3-70b-versatile": "Llama 3.3 70B (Migliore qualità)",
    "llama3-8b-8192": "Llama 3 8B (Veloce)",
    "mixtral-8x7b-32768": "Mixtral 8x7B (Contesto lungo)",
}
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

_GROQ_SYSTEM_PROMPT = """\
Sei un assistente giuridico italiano al servizio del cittadino.

Le tue regole fondamentali:
1. Rispondi ESCLUSIVAMENTE basandoti sui testi normativi forniti nel contesto.
2. Non inventare leggi, articoli o interpretazioni non presenti nel contesto.
3. Cita SEMPRE il titolo esatto e l'URN (identificatore univoco) di ogni norma che menzioni.
4. Usa un linguaggio chiaro e comprensibile al cittadino comune — evita tecnicismi non spiegati.
5. Indica esplicitamente se una norma è VIGENTE o ABROGATA.
6. Se la risposta non è ricavabile dal contesto fornito, dichiaralo apertamente.
7. Concludi sempre con una nota: "Questa risposta si basa sui dati del dataset Normattiva. Per decisioni legali consulta un avvocato."

NORME ESTRATTE DAL DATABASE NORMATTIVA:
{context}
"""


def _build_groq_context(laws: list, max_chars_per_law: int = 1800) -> str:
    """Build a structured context string from retrieved law records."""
    parts = []
    for i, law in enumerate(laws, 1):
        status = _normalize_status(law.get("status"))
        status_label = "VIGENTE ✓" if status == "in_force" else "ABROGATA ✗"
        text = (law.get("text") or law.get("snippet") or "").strip()
        excerpt = text[:max_chars_per_law] + ("…" if len(text) > max_chars_per_law else "")
        parts.append(
            f"[NORMA {i}]\n"
            f"Titolo: {law.get('title', 'N/A')}\n"
            f"URN: {law.get('urn', 'N/A')}\n"
            f"Tipo: {law.get('type', 'N/A')} | Anno: {law.get('year', 'N/A')} | Stato: {status_label}\n"
            f"Testo:\n{excerpt}"
        )
    return "\n\n---\n\n".join(parts)


def _call_groq(
    question: str,
    context_laws: list,
    model: str = GROQ_DEFAULT_MODEL,
    max_tokens: int = 1500,
    temperature: float = 0.1,
) -> tuple[str | None, str | None]:
    """
    Call Groq API with the retrieved law context (RAG pattern).
    Returns (answer_text, error_message). One of them will be None.
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return None, "GROQ_API_KEY non configurata. Imposta la variabile d'ambiente GROQ_API_KEY nelle impostazioni dello Space."

    try:
        from groq import Groq
    except ImportError:
        return None, "Libreria `groq` non installata. Aggiungi `groq>=0.9.0` a requirements.txt."

    context = _build_groq_context(context_laws)
    system_prompt = _GROQ_SYSTEM_PROMPT.format(context=context)

    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        answer = response.choices[0].message.content
        return answer, None
    except Exception as e:
        return None, f"Errore Groq API: {e}"


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
    if IS_SEARCH:
        sc1, sc2 = st.columns(2)
        sc1.metric("Norme vigenti", f"{in_force_count:,}")
        try:
            court_mentions = db.conn.execute(
                "SELECT COUNT(*) FROM laws WHERE status='in_force' AND ("
                "LOWER(text) LIKE '%corte costituzionale%' OR "
                "LOWER(text) LIKE '%corte di cassazione%' OR "
                "LOWER(text) LIKE '%consiglio di stato%' OR "
                "LOWER(text) LIKE '%tribunale amministrativo regionale%')"
            ).fetchone()[0]
        except Exception:
            court_mentions = 0
        sc2.metric("Con riferimenti giurisprudenziali", f"{court_mentions:,}")
        st.caption("Vista ottimizzata per il profilo vigente: solo norme in force.")
    else:
        sc1, sc2 = st.columns(2)
        sc1.metric("In vigore" if IS_ITALIAN_LAB else "In Force", f"{in_force_count:,}")
        sc2.metric("Abrogati" if IS_ITALIAN_LAB else "Abrogated", f"{abrogated_count:,}")
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


def page_citizen_hub():
    st.header("🏠 Hub Cittadini")
    st.caption("Percorso semplice per trovare risposte legali sulle norme vigenti senza perdersi tra strumenti tecnici.")

    db = load_db()
    if not db:
        st.error("Database non disponibile.")
        return

    try:
        in_f = db.conn.execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
    except Exception:
        in_f = 0

    try:
        court_hits = db.conn.execute(
            "SELECT COUNT(*) FROM laws WHERE status='in_force' AND ("
            "LOWER(text) LIKE '%corte costituzionale%' OR "
            "LOWER(text) LIKE '%corte di cassazione%' OR "
            "LOWER(text) LIKE '%consiglio di stato%' OR "
            "LOWER(text) LIKE '%tribunale amministrativo regionale%')"
        ).fetchone()[0]
    except Exception:
        court_hits = 0

    m1, m2, m3 = st.columns(3)
    m1.metric("Norme vigenti", f"{in_f:,}")
    m2.metric("Riferimenti giurisprudenziali", f"{court_hits:,}")
    m3.metric("Dataset ufficiale", "NormattivaVigente")

    st.subheader("Cosa vuoi fare?")
    i1, i2, i3, i4 = st.columns(4)
    if i1.button("Capire i miei diritti", key="hub-rights"):
        st.session_state["goto_page"] = "🧭 Rights Explorer"
        st.rerun()
    if i2.button("Cercare una norma", key="hub-search"):
        st.session_state["goto_page"] = "🔍 Search"
        st.rerun()
    if i3.button("Esplorare la giurisprudenza", key="hub-juris"):
        st.session_state["goto_page"] = "⚖️ Giurisprudence"
        st.rerun()
    if i4.button("Leggere una scheda completa", key="hub-detail"):
        st.session_state["goto_page"] = "📖 Law Detail"
        st.rerun()

    st.divider()
    st.subheader("🧭 Racconta la tua situazione")
    st.caption("Rispondi a poche domande guidate e ricevi un percorso normativo mirato alla tua situazione.")

    w1, w2, w3, w4 = st.columns(4)
    with w1:
        area = st.selectbox(
            "Area",
            [
                "Lavoro",
                "Casa",
                "Famiglia",
                "Privacy",
                "Circolazione stradale",
                "Tributi",
            ],
            key="hub-wizard-area",
        )
    with w2:
        intent = st.selectbox("Obiettivo", list(WIZARD_INTENTS.keys()), key="hub-wizard-intent")
    with w3:
        urgency = st.selectbox("Urgenza", ["Normale", "Breve termine", "Molto urgente"], key="hub-wizard-urgency")
    with w4:
        counterpart = st.selectbox(
            "Controparte",
            ["Privato", "Datore di lavoro", "Condominio", "Pubblica amministrazione", "Altro"],
            key="hub-wizard-counterpart",
        )

    has_documents = st.radio("Hai già documenti/prove?", ["Sì", "No"], horizontal=True, key="hub-wizard-docs")

    custom_words = st.text_input(
        "Parole aggiuntive (facoltative)",
        placeholder="es. contratto a termine, condominio, verbale",
        key="hub-wizard-custom",
    )

    if st.button("Trova norme per la mia situazione", key="hub-wizard-run", type="primary"):
        area_terms = {
            "Lavoro": "lavoro subordinato licenziamento contratto",
            "Casa": "locazione condominio sfratto proprieta",
            "Famiglia": "separazione minori mantenimento famiglia",
            "Privacy": "privacy dati personali trattamento",
            "Circolazione stradale": "codice della strada verbale ricorso",
            "Tributi": "tributi imposte contribuente dichiarazione",
        }
        counterpart_terms = {
            "Privato": "obbligazioni responsabilita",
            "Datore di lavoro": "datore di lavoro tutela lavoratore",
            "Condominio": "condominio assemblea amministratore",
            "Pubblica amministrazione": "procedimento amministrativo accesso atti",
            "Altro": "",
        }
        urgency_terms = {
            "Normale": "",
            "Breve termine": "termini procedura scadenza",
            "Molto urgente": "urgenza tutela cautelare provvedimento",
        }
        docs_terms = "documentazione prova allegati" if has_documents == "Sì" else "come raccogliere prove documenti"
        query = " ".join([
            area_terms.get(area, ""),
            WIZARD_INTENTS.get(intent, ""),
            counterpart_terms.get(counterpart, ""),
            urgency_terms.get(urgency, ""),
            docs_terms,
            (custom_words or "").strip(),
        ]).strip()
        st.session_state["lesson_query"] = query
        st.session_state["goto_page"] = "🔍 Search"
        st.rerun()

    t1, t2 = st.columns(2)
    if t1.button("Apri modelli documento", key="hub-go-templates"):
        st.session_state["goto_page"] = "📝 Modelli documenti"
        st.rerun()
    if t2.button("Confronta due scenari", key="hub-go-compare"):
        st.session_state["goto_page"] = "⚖️ Confronta scenari"
        st.rerun()

    st.divider()
    st.subheader("Temi frequenti")
    q1, q2, q3 = st.columns(3)
    if q1.button("Lavoro e licenziamento", key="hub-q-lavoro"):
        st.session_state["lesson_query"] = "licenziamento lavoro subordinato"
        st.session_state["goto_page"] = "🔍 Search"
        st.rerun()
    if q2.button("Casa, affitto, condominio", key="hub-q-casa"):
        st.session_state["lesson_query"] = "locazione condominio sfratto"
        st.session_state["goto_page"] = "🔍 Search"
        st.rerun()
    if q3.button("Privacy e dati personali", key="hub-q-privacy"):
        st.session_state["lesson_query"] = "protezione dati personali privacy"
        st.session_state["goto_page"] = "🔍 Search"
        st.rerun()

    if db:
        st.subheader("📊 Distribuzione per materia giuridica")
        try:
            domains = db.conn.execute(
                "SELECT domain_cluster, COUNT(*) as cnt FROM law_metadata "
                "WHERE domain_cluster IS NOT NULL AND domain_cluster != '' "
                "GROUP BY domain_cluster ORDER BY cnt DESC"
            ).fetchall()
            if domains:
                fig = px.bar(x=[d[0] for d in domains], y=[d[1] for d in domains],
                             title="Norme vigenti per materia",
                             labels={"x": "Materia", "y": "Numero di norme"})
                st.plotly_chart(fig, width='stretch')
        except Exception:
            pass


def page_start_here():
    if IS_SEARCH:
        st.header("🧭 Start Here — NormattivaVigente")
        st.caption("Percorso guidato sulle sole norme vigenti, con strumenti per esplorazione giuridica e riferimenti giurisprudenziali.")

        db = load_db()
        if db:
            try:
                in_f = db.conn.execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
                court_mentions = db.conn.execute(
                    "SELECT COUNT(*) FROM laws WHERE status='in_force' AND ("
                    "LOWER(text) LIKE '%corte costituzionale%' OR "
                    "LOWER(text) LIKE '%corte di cassazione%' OR "
                    "LOWER(text) LIKE '%consiglio di stato%' OR "
                    "LOWER(text) LIKE '%tribunale amministrativo regionale%')"
                ).fetchone()[0]
                c1, c2 = st.columns(2)
                c1.metric("Norme vigenti", f"{in_f:,}")
                c2.metric("Norme con riferimenti giurisprudenziali", f"{court_mentions:,}")
            except Exception:
                st.info("Dataset vigente caricato. Metriche temporaneamente non disponibili.")

        st.subheader("Percorso consigliato")
        s1, s2, s3, s4 = st.columns(4)
        if s1.button("1) Framework", key="start-fw-search"):
            st.session_state["goto_page"] = "⚖️ Framework"
            st.rerun()
        if s2.button("2) Ricerca", key="start-search-only"):
            st.session_state["goto_page"] = "🔍 Search"
            st.rerun()
        if s3.button("3) Giurisprudenza", key="start-juris"):
            st.session_state["goto_page"] = "⚖️ Giurisprudence"
            st.rerun()
        if s4.button("4) Scheda Legge", key="start-detail-search"):
            st.session_state["goto_page"] = "📖 Law Detail"
            st.rerun()

        st.divider()
        st.subheader("Glossario rapido")
        for term, desc in SEARCH_GLOSSARY.items():
            st.markdown(f"- **{term}**: {desc}")
        return

    st.header("🧭 Inizia da Qui")
    st.caption("Percorso guidato per capire tutto il database Normattiva VOM anche se parti da zero.")

    db = load_db()
    if db:
        try:
            in_f = db.conn.execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
            ab = db.conn.execute("SELECT COUNT(*) FROM laws WHERE status='abrogated'").fetchone()[0]
            mv = 0
            mv_path = _find_multivigente_db_path()
            if mv_path:
                import sqlite3 as _sqlite3
                mv_conn = _sqlite3.connect(str(mv_path))
                mv = mv_conn.execute("SELECT COUNT(*) FROM law_versions").fetchone()[0]
                mv_conn.close()
            c1, c2, c3 = st.columns(3)
            c1.metric("Vigenti (V)", f"{in_f:,}")
            c2.metric("Abrogate (O)", f"{ab:,}")
            c3.metric("Versioni storiche (M)", f"{mv:,}")
        except Exception:
            st.info("Dataset caricato. Metriche temporaneamente non disponibili.")

    st.subheader("Percorso di studio")
    s1, s2, s3, s4 = st.columns(4)
    if s1.button("1) Guida VOM", key="start-guide"):
        st.session_state["goto_page"] = "🧠 VOM Guide"
        st.rerun()
    if s2.button("2) Ricerca Guidata", key="start-search"):
        st.session_state["goto_page"] = "🔍 Search"
        st.rerun()
    if s3.button("3) Scheda Legge", key="start-detail"):
        st.session_state["goto_page"] = "📖 Law Detail"
        st.rerun()
    if s4.button("4) Lezioni Lab", key="start-lessons"):
        st.session_state["goto_page"] = "🧪 Lab Lessons"
        st.rerun()

    st.divider()
    st.subheader("Glossario rapido")
    for term, desc in BEGINNER_GLOSSARY.items():
        st.markdown(f"- **{term}**: {desc}")


def page_vom_guide():
    if IS_SEARCH:
        st.header("⚖️ Framework — NormattivaVigente")
        st.caption("Impostazione ufficiale: ricerca sulle sole norme vigenti con approfondimento giurisprudenziale.")

        c1, c2 = st.columns(2)
        c1.info("**Dataset ufficiale**\n\nSolo atti `in_force` per evitare ambiguità operative.")
        c2.success("**Metodo giurisprudenziale**\n\nParti dalla norma vigente e analizza citazioni e richiami alle Corti.")

        st.subheader("Workflow consigliato")
        st.markdown("1. Cerca una materia in **Search** (es. responsabilità civile, appalti, privacy).")
        st.markdown("2. Apri la **Law Detail** per leggere testo, metadati e citazioni.")
        st.markdown("3. Usa **Giurisprudence** per vedere cluster normativi con riferimenti a Corti e orientamenti.")
        st.markdown("4. Incrocia con **Citations** e **Domains** per una vista sistemica del quadro vigente.")
        return

    st.header("🧠 Guida VOM")
    st.caption("VOM = Vigente + Originale (abrogata) + Multivigente (storia delle versioni)")

    c1, c2, c3 = st.columns(3)
    c1.info("**V (Vigente)**\n\nNorma in vigore oggi. È il riferimento operativo.")
    c2.warning("**O (Originale/Abrogata)**\n\nTesto storico non più in vigore. Utile per contesto e confronto.")
    c3.success("**M (Multivigente)**\n\nCronologia delle versioni della stessa legge nel tempo.")

    st.subheader("Metodo consigliato")
    st.markdown("1. Parti da V per capire la disciplina attuale.")
    st.markdown("2. Usa O per vedere il quadro storico sostituito.")
    st.markdown("3. Usa M per capire quando e come la norma è cambiata.")


def page_lab_lessons():
    st.header("🧪 Lezioni Lab")
    st.caption("Esercizi guidati per studenti e principianti sul corpus VOM completo.")

    lesson_labels = {
        "How to read a law card": "Come leggere una scheda legge",
        "How abrogation works": "Come funziona l'abrogazione",
        "How amendments change laws": "Come leggere le modifiche nel tempo",
        "How to use citations": "Come usare la rete citazioni",
    }

    for lesson, cfg in LAB_LESSONS.items():
        with st.expander(lesson_labels.get(lesson, lesson)):
            st.write(f"**Obiettivo**: {cfg['goal']}")
            st.write(f"**Query suggerita**: {cfg['query']}")
            if st.button(f"Avvia lezione", key=f"lesson-{lesson}"):
                st.session_state["lesson_query"] = cfg["query"]
                st.session_state["goto_page"] = "🔍 Search"
                st.rerun()


def _did_you_mean(query: str, laws: List[Dict], max_candidates: int = 5000) -> List[str]:
    if not query or not laws:
        return []
    from difflib import get_close_matches

    q = query.strip().lower()
    titles = []
    title_map = {}
    for l in laws[:max_candidates]:
        t = (l.get("title") or "").strip()
        if not t:
            continue
        lt = t.lower()
        if lt not in title_map:
            title_map[lt] = t
            titles.append(lt)
    guessed = get_close_matches(q, titles, n=5, cutoff=0.72)
    return [title_map[g] for g in guessed]


def page_search():
    if IS_SEARCH:
        st.header("🔍 Cerca Norme Vigenti")
        st.caption("Scrivi parole semplici (materia, diritto, istituto). La ricerca mostra prima le norme in vigore più rilevanti.")
        st.write("**Scenari rapidi**")
        p1, p2, p3 = st.columns(3)
        preset_items = list(SCENARIO_PRESETS.items())
        for idx, (label, qtxt) in enumerate(preset_items):
            target_col = [p1, p2, p3][idx % 3]
            with target_col:
                if st.button(label, key=f"preset-{idx}"):
                    st.session_state["lesson_query"] = qtxt
                    st.rerun()
    else:
        st.header("🔍 Cerca Leggi — Ricerca Avanzata" if IS_ITALIAN_LAB else "🔍 Advanced Search")
    db = load_db()

    mode = st.radio(
        "Modalità ricerca",
        ["Guidata", "Esperta"],
        horizontal=True,
        help="La modalità guidata aiuta a trovare subito contenuti pertinenti.",
    )

    if mode == "Guidata":
        if IS_SEARCH:
            preset = st.selectbox(
                "Cosa vuoi fare?",
                [
                    "Trovare una norma vigente",
                    "Analizzare un tema giurisprudenziale",
                ],
            )
            default_scope = "in_force"
            if "giurisprudenziale" in preset.lower():
                st.caption("Suggerimento: prova query come 'corte costituzionale', 'cassazione', 'sezioni unite'.")
        else:
            preset = st.selectbox(
                "Cosa vuoi fare?",
                [
                    "Trovare la norma vigente (V)",
                    "Studiare una norma abrogata (O)",
                    "Capire come cambia una norma nel tempo (M)",
                ],
            )
            default_scope = "in_force"
            if "abrogata" in preset.lower():
                default_scope = "abrogated"
            elif "cambia" in preset.lower() or "tempo" in preset.lower():
                default_scope = "all"
    else:
        default_scope = "in_force"

    query = st.text_input(
        "Cerca nel diritto italiano (testo completo + ranking):",
        value=st.session_state.pop("lesson_query", ""),
        placeholder="es. responsabilità civile, decreto legislativo 231"
    )
    st.session_state["last_search_query"] = query

    with st.expander("Filtri avanzati", expanded=IS_SEARCH):
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            filter_type = st.text_input("Tipo atto (es. legge, decreto)")
        with fc2:
            filter_year_from = st.number_input("Anno da", min_value=1800,
                                                max_value=2100, value=1800)
        with fc3:
            filter_year_to = st.number_input("Anno a", min_value=1800,
                                              max_value=2100, value=2100)
        with fc4:
            status_options = ["in_force", "all"] if IS_SEARCH else ["in_force", "abrogated", "all"]
            status_scope = st.selectbox(
                "Stato",
                status_options,
                index=status_options.index(default_scope if default_scope in status_options else "in_force"),
                help="Per principianti è consigliato iniziare da in_force (vigenti)."
            )

    result_limit = st.slider("Numero massimo risultati", 25, 500, 100, 25)

    if not query or len(query) < 2:
        st.info("Inserisci almeno 2 caratteri per avviare la ricerca.")
        return

    if db:
        try:
            results = db.search_fts(query, limit=result_limit)
            if status_scope != "all":
                results = [r for r in results if _normalize_status(r.get("status")) == status_scope]

            filtered_results = []
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
                filtered_results.append(r)

            st.write(f"**Trovati {len(filtered_results)} risultati** (ordinati per rilevanza)")

            if IS_SEARCH and filtered_results:
                quick_df = pd.DataFrame([
                    {
                        "Anno": r.get("year"),
                        "Tipo": r.get("type"),
                        "Titolo": (r.get("title") or "")[:100],
                        "Status": _status_label(r.get("status")),
                    }
                    for r in filtered_results[:50]
                ])
                st.dataframe(quick_df, width='stretch', hide_index=True)

                quick_map = {
                    f"{(r.get('title') or '')[:100]} ({r.get('year', '?')})": r.get("urn")
                    for r in filtered_results[:50]
                    if r.get("urn")
                }
                if quick_map:
                    pick = st.selectbox("Apri rapidamente una norma", list(quick_map.keys()), key="search-quick-open")
                    if st.button("Apri scheda norma", key="search-quick-open-btn"):
                        st.session_state["detail_urn"] = quick_map[pick]
                        st.session_state["goto_page"] = "📖 Law Detail"
                        st.rerun()

            if not results:
                guesses = _did_you_mean(query, _get_laws())
                if guesses:
                    st.warning("Nessun risultato esatto. Forse cercavi:")
                    for g in guesses:
                        if st.button(g, key=f"guess-{g}"):
                            st.session_state["lesson_query"] = g
                            st.rerun()
            for r in filtered_results:
                year = r.get("year", "?")
                status = _normalize_status(r.get("status", "in_force"))
                track = _dataset_track(r)
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
                        st.write(f"**Status**: {_status_chip(status)} {_status_label(status)}")
                        st.write(f"**Track**: {track.upper()}")
                        if mode == "Guidata":
                            st.caption(_status_explainer(status))
                        if r.get("importance_score"):
                            st.write(f"**Importance**: {r['importance_score']:.4f}")
                        if IS_SEARCH and st.button("Apri scheda", key=f"open-from-search-{r.get('urn')}"):
                            st.session_state["detail_urn"] = r.get("urn")
                            st.session_state["goto_page"] = "📖 Law Detail"
                            st.rerun()
                    with c2:
                        if IS_SEARCH:
                            st.info(_plain_language_summary(r))
                        snippet = r.get("snippet", "")
                        if snippet:
                            st.markdown(f"**Matched text**: ...{snippet}...")
                        else:
                            st.text_area("Preview", r.get("text", "")[:800],
                                         height=150, disabled=True,
                                         key=f"search_{r.get('urn','')}")
                        if IS_SEARCH:
                            _render_source_transparency_box(db, r, query_terms=query)
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
        st.write(f"**Trovati {len(results)} risultati** (ricerca testuale semplice)")
        for law in results:
            with st.expander(
                f"{law.get('title', 'Untitled')} ({law.get('year', '?')})"
            ):
                st.write(f"**URN**: `{law.get('urn')}`")
                st.write(f"**Type**: {law.get('type')}")
                st.write(f"**Status**: {_status_label(law.get('status'))}")
                st.text_area("Text", law.get("text", "")[:800], height=150,
                             disabled=True, key=f"srch_jl_{law.get('urn','')}")


def page_document_templates():
    st.header("📝 Modelli documenti")
    st.caption("Modelli base in linguaggio semplice, da adattare al tuo caso concreto.")

    template_name = st.selectbox("Scegli modello", list(DOCUMENT_TEMPLATES.keys()))
    tpl = DOCUMENT_TEMPLATES[template_name]

    c1, c2 = st.columns(2)
    with c1:
        citizen_name = st.text_input("Nome e cognome", value="Nome Cognome")
        city = st.text_input("Città", value="Città")
        recipient = st.text_input("Destinatario", value="Ente/Ufficio/Controparte")
    with c2:
        topic = st.text_input("Oggetto del problema", value="Descrizione sintetica")
        reason = st.text_area("Fatti principali", value="Descrivi i fatti essenziali in modo cronologico.", height=110)
        deadline_days = st.number_input("Giorni proposti per risposta/adempimento", min_value=3, max_value=180, value=15)

    text = (
        f"Oggetto: {tpl['subject']}\n\n"
        + tpl["body"].format(
            citizen_name=citizen_name,
            city=city,
            recipient=recipient,
            topic=topic,
            reason=reason,
            deadline_days=deadline_days,
        )
        + "\n\nData: __________\nFirma: __________"
    )

    st.text_area("Bozza documento", text, height=320)
    st.download_button(
        "Scarica bozza (.txt)",
        data=text.encode("utf-8"),
        file_name="bozza_documento_normattivavigente.txt",
        mime="text/plain",
    )
    st.warning("Queste bozze sono strumenti informativi e non sostituiscono consulenza legale professionale.")


def page_scenario_compare():
    st.header("⚖️ Confronta scenari")
    st.caption("Confronta due situazioni giuridiche per vedere differenze nei risultati normativi vigenti.")

    db = load_db()
    if not db:
        st.info("Database richiesto per il confronto scenari.")
        return

    scenario_names = list(SCENARIO_PRESETS.keys())
    c1, c2 = st.columns(2)
    with c1:
        left_s = st.selectbox("Scenario A", scenario_names, key="cmp-left")
        left_custom = st.text_input("Query personalizzata A (opzionale)", key="cmp-left-custom")
    with c2:
        right_s = st.selectbox("Scenario B", scenario_names, index=min(1, len(scenario_names)-1), key="cmp-right")
        right_custom = st.text_input("Query personalizzata B (opzionale)", key="cmp-right-custom")

    if not st.button("Confronta", type="primary"):
        return

    q_left = left_custom.strip() or SCENARIO_PRESETS[left_s]
    q_right = right_custom.strip() or SCENARIO_PRESETS[right_s]

    left_rows = [r for r in db.search_fts(q_left, limit=200) if _normalize_status(r.get("status")) == "in_force"]
    right_rows = [r for r in db.search_fts(q_right, limit=200) if _normalize_status(r.get("status")) == "in_force"]

    left_urns = {r.get("urn") for r in left_rows if r.get("urn")}
    right_urns = {r.get("urn") for r in right_rows if r.get("urn")}
    overlap = left_urns.intersection(right_urns)

    m1, m2, m3 = st.columns(3)
    m1.metric("Norme scenario A", f"{len(left_rows):,}")
    m2.metric("Norme scenario B", f"{len(right_rows):,}")
    m3.metric("Norme comuni", f"{len(overlap):,}")

    lcol, rcol = st.columns(2)
    with lcol:
        st.subheader(f"A: {left_s}")
        for r in left_rows[:10]:
            with st.expander(f"{r.get('title','N/A')[:90]} ({r.get('year','?')})"):
                st.write(_plain_language_summary(r))
                _render_source_transparency_box(db, r, query_terms=q_left)
    with rcol:
        st.subheader(f"B: {right_s}")
        for r in right_rows[:10]:
            with st.expander(f"{r.get('title','N/A')[:90]} ({r.get('year','?')})"):
                st.write(_plain_language_summary(r))
                _render_source_transparency_box(db, r, query_terms=q_right)

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


def page_jurisprudence_explorer():
    st.header("⚖️ Giurisprudence Explorer")
    st.caption(
        "Esplora il dataset vigente attraverso richiami a Corte costituzionale, Cassazione e giudici amministrativi. "
        "Questa pagina analizza i riferimenti giurisprudenziali presenti nelle norme vigenti."
    )

    db = load_db()
    if not db:
        st.info("Database richiesto per l'esplorazione giurisprudenziale.")
        return

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        topic = st.selectbox("Tema giurisprudenziale", list(JURISPRUDENCE_TOPICS.keys()))
    with c2:
        year_from = st.number_input("Anno da", min_value=1800, max_value=2100, value=1948)
    with c3:
        year_to = st.number_input("Anno a", min_value=1800, max_value=2100, value=2100)

    limit = st.slider("Numero risultati", 20, 300, 80, 20)
    if not st.button("Analizza giurisprudenza"):
        st.info("Seleziona un tema e avvia l'analisi.")
        return

    query = JURISPRUDENCE_TOPICS[topic]
    try:
        rows = db.search_fts(query, limit=500)
    except Exception as e:
        st.error(f"Errore ricerca: {e}")
        return

    rows = [r for r in rows if _normalize_status(r.get("status")) == "in_force"]
    rows = [r for r in rows if year_from <= int(r.get("year") or 0) <= year_to]
    rows = rows[:limit]

    if not rows:
        st.warning("Nessun risultato per i filtri selezionati.")
        return

    st.success(f"Trovate {len(rows)} norme vigenti con segnali giurisprudenziali per: {topic}")

    t_counter = Counter((r.get("type") or "unknown") for r in rows)
    y_counter = Counter(str(r.get("year") or "?") for r in rows)
    v1, v2 = st.columns(2)
    with v1:
        fig = px.bar(
            x=list(t_counter.keys()),
            y=list(t_counter.values()),
            title="Distribuzione per tipo atto",
            labels={"x": "Tipo", "y": "Conteggio"},
        )
        st.plotly_chart(fig, width='stretch')
    with v2:
        yd = dict(sorted(y_counter.items()))
        fig = px.line(
            x=list(yd.keys()),
            y=list(yd.values()),
            title="Trend temporale",
            labels={"x": "Anno", "y": "Norme"},
        )
        st.plotly_chart(fig, width='stretch')

    st.subheader("Norme rilevanti")
    for r in rows:
        with st.expander(f"{r.get('title', 'N/A')} ({r.get('year', 'N/A')})"):
            st.write(f"**Status**: {_status_label(r.get('status'))}")
            st.write(f"**Tipo**: {r.get('type', 'N/A')}")
            st.write(f"**URN**: `{r.get('urn', 'N/A')}`")
            if r.get("snippet"):
                st.markdown(f"**Snippet**: ...{r.get('snippet')}...")
            if st.button("Apri scheda legge", key=f"jur-open-{r.get('urn')}"):
                st.session_state["detail_urn"] = r.get("urn")
                st.session_state["goto_page"] = "📖 Law Detail"
                st.rerun()


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
    _render_browse_table(laws, "📋 Sfoglia Archivio" if IS_ITALIAN_LAB else "📋 Browse Laws (All)", locked_status=None)


def page_vigenti():
    laws = [l for l in _get_laws() if _normalize_status(l.get("status")) == "in_force"]
    _render_browse_table(laws, "⚡ Vigenti Laws", locked_status="in_force")


def page_abrogated():
    st.header("\U0001f6ab Leggi Abrogate")

    db = load_db()
    if not db:
        st.error("Database non disponibile.")
        return

    # Count abrogated in DB
    try:
        n_abr = db.conn.execute(
            "SELECT COUNT(*) FROM laws WHERE status='abrogated'"
        ).fetchone()[0]
        n_total = db.conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
        n_force = n_total - n_abr
    except Exception:
        n_abr = n_force = n_total = 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Leggi in vigore", f"{n_force:,}")
    c2.metric("Leggi abrogate", f"{n_abr:,}", help="Atti normativi abrogati — fonte: track O Normattiva API")
    c3.metric("Totale corpus", f"{n_total:,}")

    if n_abr == 0:
        st.warning(
            "Nessuna legge abrogata nel database. "
            "Il database contiene solo leggi vigenti (track V). "
            "Per includere le ~124.000 leggi abrogate, eseguire: `py build_voom.py --steps abrogati`"
        )
        return

    st.info(
        f"Il corpus VOOM contiene **{n_abr:,}** leggi abrogate dalla raccolta "
        "'Atti normativi abrogati (in originale)' dell'API Normattiva (track O). "
        "La data di abrogazione non \u00e8 fornita dall'API; sono presenti titolo, tipo, "
        "data di emanazione e testo originale."
    )

    # Search within abrogated
    q = st.text_input("Cerca tra le leggi abrogate", placeholder="Es.: legge n. 183 previdenza")
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        type_filter = st.selectbox("Tipo atto", ["(tutti)", "legge", "decreto.legislativo",
                                                   "decreto.legge", "regio.decreto", "dpcm", "dpr"])
    with col_f2:
        year_from, year_to = st.slider("Anno emanazione", 1861, 2025, (1950, 2020))

    try:
        if q:
            rows = db.search_fts(q, limit=500)
            rows = [r for r in rows if _normalize_status(r.get("status")) == "abrogated"]
        else:
            rows = db.conn.execute(
                "SELECT urn, title, type, date, year, article_count "
                "FROM laws WHERE status='abrogated' ORDER BY year DESC LIMIT 5000"
            ).fetchall()
            rows = [dict(r) for r in rows]

        # Apply filters
        if type_filter != "(tutti)":
            rows = [r for r in rows if r.get("type") == type_filter]
        rows = [r for r in rows if year_from <= (r.get("year") or 0) <= year_to]

        st.caption(f"{len(rows):,} leggi trovate")
        if rows:
            df = pd.DataFrame([{
                "Anno": r.get("year"),
                "Tipo": r.get("type"),
                "Titolo": (r.get("title") or "")[:80],
                "Data": r.get("date"),
                "Articoli": r.get("article_count"),
                "URN": r.get("urn"),
            } for r in rows[:200]])
            st.dataframe(df, use_container_width=True, hide_index=True)
            if len(rows) > 200:
                st.caption("Mostrati i primi 200 risultati. Usa la ricerca per affinare.")
    except Exception as e:
        st.error(f"Errore query: {e}")


def page_multivigente():
    """Amendment history page — downloads multivigente.db on demand."""
    st.header("\U0001f4dc Storia Normativa — Versioni Multivigente")
    st.caption(
        "Consulta come una legge \u00e8 cambiata nel tempo. "
        "Ogni versione corrisponde a un intervallo di vigenza (track M dell'API Normattiva)."
    )

    # Check if multivigente.db is available
    mv_paths = [
        Path("/app/data/multivigente.db"),
        Path(__file__).parent.parent / "data" / "multivigente.db",
        Path(__file__).parent / "data" / "multivigente.db",
    ]
    mv_db_path = next((p for p in mv_paths if p.exists() and p.stat().st_size > 10_000_000), None)

    if mv_db_path is None:
        st.warning(
            "Il database delle versioni storiche (multivigente.db, ~2 GB) "
            "non \u00e8 ancora disponibile in questa istanza."
        )
        with st.expander("Come abilitare la storia normativa"):
            st.markdown(
                "Il database multivigente viene scaricato separatamente per non appesantire "
                "l'avvio dell'app. Clicca il pulsante qui sotto per avviare il download (~2 GB). "
                "Il download richiede 5-15 minuti e l'app rimane usabile durante l'operazione."
            )
            if st.button("Scarica database multivigente (~2 GB)", type="primary"):
                output_path = mv_paths[1] if not mv_paths[0].parent.exists() else mv_paths[0]
                output_path.parent.mkdir(parents=True, exist_ok=True)
                placeholder = st.empty()
                with st.spinner("Download in corso... (~2 GB, pazienta)"):
                    try:
                        sys.path.insert(0, str(Path(__file__).parent))
                        from download_db import download_database
                        ok = download_database(str(output_path), "multivigente")
                        if ok:
                            placeholder.success(f"Download completato: {output_path}")
                            st.rerun()
                        else:
                            placeholder.error(
                                "Download fallito. Controlla che il dataset HF contenga "
                                "data/multivigente.db (eseguire build_voom.py --steps multivigente)."
                            )
                    except Exception as ex:
                        placeholder.error(f"Errore: {ex}")
        return

    # Multivigente DB is available — query it
    import sqlite3 as _sqlite3

    st.success(f"Database storico caricato: {mv_db_path.stat().st_size/1e6:.0f} MB")

    urn_input = st.text_input(
        "Inserisci URN della legge",
        placeholder="urn:nir:stato:legge:1991;104",
        help="Puoi copiare l'URN dalla pagina dettaglio della legge.",
    )

    # Also allow free-text search to find a law's URN
    law_search = st.text_input(
        "...oppure cerca per titolo nella legge principale",
        placeholder="legge 104 handicap",
    )

    urn = urn_input.strip()
    if not urn and law_search:
        db = load_db()
        if db:
            try:
                res = db.search_fts(law_search, limit=10)
                if res:
                    opts = {f"{r.get('title','')[:70]} ({r.get('year')})": r.get("urn") for r in res}
                    chosen = st.selectbox("Seleziona legge", ["-- scegli --"] + list(opts))
                    if chosen != "-- scegli --":
                        urn = opts[chosen]
            except Exception:
                pass

    if not urn:
        st.info("Inserisci un URN o cerca per titolo per visualizzare la storia normativa.")
        return

    try:
        mv_conn = _sqlite3.connect(str(mv_db_path))
        mv_conn.row_factory = _sqlite3.Row
        versions = mv_conn.execute(
            "SELECT version_date, title, article_count, text_length, text "
            "FROM law_versions WHERE law_urn = ? ORDER BY version_date",
            (urn,)
        ).fetchall()
        mv_conn.close()
    except Exception as e:
        st.error(f"Errore lettura database storico: {e}")
        return

    if not versions:
        st.warning(f"Nessuna versione storica trovata per: `{urn}`")
        st.caption(
            "L'atto potrebbe non essere presente nel track M dell'API Normattiva, "
            "oppure il database non include ancora questa legge."
        )
        return

    st.subheader(f"{len(versions)} versioni trovate")
    df = pd.DataFrame([{
        "Data versione": v["version_date"],
        "Titolo": (v["title"] or "")[:80],
        "Articoli": v["article_count"],
        "Lunghezza testo": v["text_length"],
    } for v in versions])
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Show a specific version's text
    if len(versions) > 1:
        v_dates = [v["version_date"] for v in versions]
        selected = st.selectbox("Leggi il testo di una versione:", v_dates)
        v_text = next((v["text"] for v in versions if v["version_date"] == selected), "")
        if v_text:
            st.text_area("Testo della versione", v_text[:5000], height=400)
            if len(v_text) > 5000:
                st.caption(f"Testo troncato a 5.000 caratteri (totale: {len(v_text):,})")
    elif versions:
        st.text_area("Testo", (versions[0]["text"] or "")[:5000], height=400)




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


def page_chatbot():
    st.header("🤖 Chatbot — Normattiva Vigente")
    st.caption("Conversational explorer over the vigente dataset. Uses a local backend for retrieval and optional Groq generation.")

    backend = os.environ.get("LLM_BACKEND_URL", "http://127.0.0.1:8000")

    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    q = st.text_input("Ask the assistant (plain language)", value="", key="chatbot-question")
    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("Ask", key="chatbot-ask") and q.strip():
            try:
                resp = requests.post(f"{backend}/chat", json={"question": q, "top_k": 6}, timeout=30)
                if resp.status_code != 200:
                    st.error(f"Backend error: {resp.status_code} {resp.text}")
                else:
                    data = resp.json()
                    st.session_state["chat_history"].append({"q": q, "a": data.get("answer"), "e": data.get("evidence", [])})
            except Exception as e:
                st.error(f"Could not reach backend: {e}")

    if st.session_state["chat_history"]:
        for item in reversed(st.session_state["chat_history"]):
            with st.expander(f"Q: {item['q']}"):
                st.markdown(item.get("a") or "(no answer)")
                if item.get("e"):
                    st.subheader("Evidence")
                    for ev in item.get("e"):
                        cols = st.columns([5, 1])
                        cols[0].markdown(f"**{ev.get('title','N/A')}** ({ev.get('year','N/A')})\n\n{ev.get('snippet','')}")
                        with cols[1]:
                            if st.button("Open law", key=f"open-{ev.get('urn')}"):
                                st.session_state["detail_urn"] = ev.get('urn')
                                st.session_state["goto_page"] = "📖 Law Detail"
                                st.rerun()

    st.divider()
    if st.button("Check sync with Normattiva API", key="chatbot-sync"):
        try:
            resp = requests.get(f"{backend}/sync_status", timeout=40)
            if resp.status_code != 200:
                st.error(f"Sync check failed: {resp.status_code} {resp.text}")
            else:
                report = resp.json()
                st.subheader("Sync report")
                st.write(f"Collections checked: {report.get('summary_counted_collections')}")
                df = pd.DataFrame(report.get('details', []))
                st.dataframe(df, width='stretch', hide_index=True)
        except Exception as e:
            st.error(f"Sync check error: {e}")


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
    st.header("🇮🇹 Italian Legal Lab")
    st.caption(
        "Intelligence giuridica integrata: dataset Normattiva, dati finanziari SIOPE+, "
        "statistiche istituzionali e fonti parlamentari."
    )

    db = load_db()
    if not db:
        st.info("Database required for Legal Lab.")
        return

    section = st.radio(
        "Italian Legal Lab sections",
        [
            "Overview",
            "Normattiva Tracks",
            "Status Timeline",
            "SIOPE+",
            "Public Data Feeds",
        ],
        horizontal=True,
        key="italian-lab-section",
        label_visibility="collapsed",
    )

    if section == "Overview":
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
        if qa1.button("Cerca Leggi", key="lab-go-search"):
            st.session_state["goto_page"] = "🔍 Cerca Leggi"
            st.rerun()
        if qa2.button("Vigenti", key="lab-go-vigenti"):
            st.session_state["italian-lab-section"] = "Normattiva Tracks"
            st.session_state["italian-lab-track-select"] = "vigente"
            st.rerun()
        if qa3.button("Abrogati", key="lab-go-abrogati"):
            st.session_state["italian-lab-section"] = "Normattiva Tracks"
            st.session_state["italian-lab-track-select"] = "abrogato"
            st.rerun()
        if qa4.button("Rete Citazioni", key="lab-go-citations"):
            st.session_state["goto_page"] = "🔗 Rete Citazioni"
            st.rerun()

        st.info(
            "Recommended deployment mapping: \n"
            "- normattivavigente -> diatribe00/normattivavigente-data\n"
            "- opennormattiva-lab -> diatribe00/normattiva-lab-data\n"
            "- italian-legal-lab -> diatribe00/italian-legal-lab-data"
        )

        st.divider()
        st.subheader("Full Normattiva experience")
        st.caption("Direct access to the full analysis stack available in this Space.")
        r1, r2, r3 = st.columns(3)
        if r1.button("🔍 Cerca Leggi", key="lab-open-search"):
            st.session_state["goto_page"] = "🔍 Cerca Leggi"
            st.rerun()
        if r2.button("📋 Sfoglia Archivio", key="lab-open-browse"):
            st.session_state["goto_page"] = "📋 Sfoglia Archivio"
            st.rerun()
        if r3.button("📖 Scheda Legge", key="lab-open-detail"):
            st.session_state["goto_page"] = "📖 Scheda Legge"
            st.rerun()

        r4, r5, r6 = st.columns(3)
        if r4.button("🔗 Rete Citazioni", key="lab-open-cit-net"):
            st.session_state["goto_page"] = "🔗 Rete Citazioni"
            st.rerun()
        if r5.button("🏛️ Aree Giuridiche", key="lab-open-domains"):
            st.session_state["goto_page"] = "🏛️ Aree Giuridiche"
            st.rerun()
        if r6.button("📥 Esporta Dati", key="lab-open-export"):
            st.session_state["goto_page"] = "📥 Esporta"
            st.rerun()

    elif section == "Normattiva Tracks":
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

            sel_track = st.selectbox(
                "Explore track",
                ["vigente", "multivigente", "abrogato"],
                key="italian-lab-track-select",
            )
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

    elif section == "Status Timeline":
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

    elif section == "SIOPE+":
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

    elif section == "Public Data Feeds":
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

        st.divider()
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

        st.divider()
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

        st.divider()
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
    st.header("📖 Scheda Legge" if IS_ITALIAN_LAB else "📖 Law Detail")
    db = load_db()
    if not db:
        st.info("Database required for detailed law view.")
        return

    pref_urn = st.session_state.get("detail_urn")
    if IS_SEARCH:
        st.caption("Modalità cittadino: cerca prima per titolo/parola chiave, poi apri la scheda completa della norma vigente.")
        detail_query = st.text_input(
            "Trova norma per titolo, materia o numero",
            placeholder="es. codice del consumo, privacy, responsabilità civile",
            key="law-detail-query",
        )
        if detail_query and len(detail_query.strip()) >= 2:
            try:
                cand = db.search_fts(detail_query.strip(), limit=80)
                cand = [r for r in cand if _normalize_status(r.get("status")) == "in_force"]
            except Exception:
                cand = []
        else:
            cand = []

        if pref_urn and not any((c.get("urn") == pref_urn) for c in cand):
            row = db.conn.execute(
                "SELECT urn, title, year, status FROM laws WHERE urn = ? LIMIT 1",
                (pref_urn,),
            ).fetchone()
            if row:
                cand = [dict(row)] + cand

        if not cand:
            st.info("Inserisci almeno 2 caratteri per selezionare una norma.")
            return

        options = {
            f"{c.get('title','')[:90]} ({c.get('year','?')})": c.get("urn")
            for c in cand
            if c.get("urn")
        }
        selected_label = st.selectbox("Seleziona norma", list(options.keys()), key="law-detail-select-search")
        urn = options[selected_label]
    else:
        laws = _get_laws()
        urn_options = [f"{l.get('title', '')[:60]} ({l.get('urn', '')})" for l in laws[:500]]
        selected = st.selectbox(
            "Select a law:",
            urn_options if urn_options else ["No laws available"],
            key="law-detail-select",
        )
        if not selected or selected == "No laws available":
            return
        urn = selected.split("(")[-1].rstrip(")")
    law_row = db.conn.execute("SELECT * FROM laws WHERE urn = ?", (urn,)).fetchone()
    if not law_row:
        st.warning("Law not found.")
        return

    law = dict(law_row)
    st.subheader(law.get("title", "Untitled"))
    st.caption(f"{_status_chip(law.get('status'))} · {_status_explainer(law.get('status'))}")
    with st.expander("ℹ️ Cosa vedo in questa pagina?" if IS_SEARCH else "Why am I seeing this law?"):
        if IS_SEARCH:
            st.write(
                "Questa scheda mostra tutti i dettagli di una norma vigente: testo completo, "
                "analisi AI, citazioni ad altre norme e rete di collegamento. "
                "Usa la tab **🤖 Analisi AI** per fare domande in linguaggio semplice."
            )
        else:
            st.write(
                "This page shows one act in VOM context: current status (V/O) and historical versions (M)."
            )
    _render_source_transparency_box(
        db,
        law,
        query_terms=st.session_state.get("last_search_query", ""),
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tipo" if IS_SEARCH else "Type", law.get("type", "N/A"))
    col2.metric("Anno" if IS_SEARCH else "Year", law.get("year", "N/A"))
    col3.metric("Articoli" if IS_SEARCH else "Articles", law.get("article_count", 0))
    if law.get("importance_score"):
        col4.metric("Importanza" if IS_SEARCH else "Importance (PageRank)", f"{law['importance_score']:.4f}")

    if IS_SEARCH:
        top_simple, top_ai, top_text, top_links = st.tabs(["🪄 Panoramica", "🤖 Chiedi all'AI", "📄 Testo Integrale", "🔗 Citazioni e Rete"])
    else:
        top_simple, top_timeline, top_expert = st.tabs(["🪄 Simple View", "🕰️ Timeline View", "🧠 Expert View"])

    with top_simple:
        s1, s2 = st.columns([1, 2])
        with s1:
            st.write(f"**Stato**: {_status_chip(law.get('status'))} {_status_label(law.get('status'))}" if IS_SEARCH else f"**Status**: {_status_chip(law.get('status'))} {_status_label(law.get('status'))}")
        if IS_SEARCH:
            pass  # remaining detail in dedicated tabs below

    if IS_SEARCH:
        with top_ai:
            _render_law_ai_tab(law, db, urn)

        with top_text:
            text = law.get("text", "")
            if text:
                st.caption("Scorri il testo per leggere la norma. Usa la tab 🤖 per fare domande su passaggi specifici.")
            st.text_area("Testo della norma", text or "(Testo non disponibile nel dataset)", height=500, disabled=True, key="law-text-citizen")

        with top_links:
            tab2, tab3 = st.tabs(["🔗 Citazioni", "🎯 Grafo + correlate"])
            with tab2:
                c1, c2 = st.columns(2)
                with c1:
                    st.write("**Norme che citano questa**")
                    cited_by = db.get_citations_incoming(urn, limit=30)
                    st.write(f"Trovate: **{len(cited_by):,}**")
                    for cit in cited_by[:15]:
                        cited_urn = cit.get("citing_urn") or cit.get("urn")
                        st.write(f"- {cited_urn}")
                with c2:
                    st.write("**Norme citate da questa**")
                    cites = db.get_citations_outgoing(urn, limit=30)
                    st.write(f"Trovate: **{len(cites):,}**")
                    for cit in cites[:15]:
                        cited_urn = cit.get("cited_urn") or cit.get("urn")
                        st.write(f"- {cited_urn}")
            with tab3:
                try:
                    neighborhood = db.get_citation_neighborhood(urn, depth=2, max_nodes=50)
                    if neighborhood and neighborhood.get("nodes"):
                        _render_graph_plotly(
                            neighborhood["nodes"],
                            neighborhood["edges"],
                            title=(f"Rete citazioni di {law.get('title', urn)[:50]}"),
                        )
                except Exception:
                    st.info("Grafo non disponibile per questa norma.")
                try:
                    related = db.find_related_laws(urn, limit=15)
                    if related:
                        st.subheader("Norme correlate")
                        for r in related[:10]:
                            _render_law_card(r, db, key_prefix="detail-related-search")
                except Exception:
                    pass
    with top_timeline if not IS_SEARCH else st.container():
        if IS_SEARCH:
            pass
        else:
            mv_db_path = _find_multivigente_db_path()
            if not mv_db_path:
                st.info("Historical timeline DB is not available in this session.")
            else:
                try:
                    import sqlite3 as _sqlite3

                    mv_conn = _sqlite3.connect(str(mv_db_path))
                    mv_conn.row_factory = _sqlite3.Row
                    versions = mv_conn.execute(
                        "SELECT version_date, title, article_count, text_length, text "
                        "FROM law_versions WHERE law_urn = ? ORDER BY version_date",
                        (urn,),
                    ).fetchall()
                    original = mv_conn.execute(
                        "SELECT title, date, text_length, text FROM original_acts WHERE law_urn = ? LIMIT 1",
                        (urn,),
                    ).fetchone()
                    mv_conn.close()

                    st.write(f"M versions: **{len(versions):,}**")
                    st.write(f"O originale available: **{'yes' if original else 'no'}**")

                    if versions:
                        df = pd.DataFrame(
                            [
                                {
                                    "Version date": v["version_date"],
                                    "Title": (v["title"] or "")[:90],
                                    "Articles": v["article_count"],
                                    "Text length": v["text_length"],
                                }
                                for v in versions
                            ]
                        )
                        st.dataframe(df, width='stretch', hide_index=True)
                        dates = [v["version_date"] for v in versions]
                        selected_date = st.selectbox("Read specific M version", dates, key="detail-m-version")
                        selected_row = next((v for v in versions if v["version_date"] == selected_date), None)
                        if selected_row:
                            st.text_area(
                                "Selected version text",
                                (selected_row["text"] or "")[:5000],
                                height=300,
                                disabled=True,
                                key="detail-m-text",
                            )
                    elif not original:
                        st.warning("No historical records found for this URN in M/O tables.")

                    if original:
                        with st.expander("Original O-track text"):
                            st.write(f"**Date**: {original['date']}")
                            st.text_area(
                                "Original text",
                                (original["text"] or "")[:5000],
                                height=300,
                                disabled=True,
                                key="detail-o-text",
                            )
                except Exception as e:
                    st.error(f"Timeline load failed: {e}")
    with top_expert if not IS_SEARCH else st.container():
        if IS_SEARCH:
            pass
        else:
            tab1, tab2, tab3 = st.tabs(["📄 Full Text", "🔗 Citations", "🎯 Graph + Related"])

            with tab1:
                e1, e2 = st.columns([1, 2])
                with e1:
                    st.subheader("Metadata")
                    st.write(f"**URN**: `{law.get('urn')}`")
                    st.write(f"**Date**: {law.get('date', 'N/A')}")
                    st.write(f"**Status**: {_status_label(law.get('status'))}")
                    st.write(f"**Characters**: {law.get('text_length', 0):,}")
                with e2:
                    text = law.get("text", "")
                    st.text_area("Content", text, height=420, disabled=True, key="law-text")
                    ref_table = _urn_inline_links(text, db)
                    if ref_table:
                        with st.expander("📎 Leggi citate nel testo"):
                            st.markdown(ref_table)

            with tab2:
                c1, c2 = st.columns(2)
                with c1:
                    st.write("**Incoming citations**")
                    cited_by = db.get_citations_incoming(urn, limit=30)
                    st.write(f"Found: **{len(cited_by):,}**")
                    for cit in cited_by[:15]:
                        cited_urn = cit.get("citing_urn") or cit.get("urn")
                        st.write(f"- {cited_urn}")
                with c2:
                    st.write("**Outgoing citations**")
                    cites = db.get_citations_outgoing(urn, limit=30)
                    st.write(f"Found: **{len(cites):,}**")
                    for cit in cites[:15]:
                        cited_urn = cit.get("cited_urn") or cit.get("urn")
                        st.write(f"- {cited_urn}")

            with tab3:
                try:
                    neighborhood = db.get_citation_neighborhood(urn, depth=2, max_nodes=50)
                    if neighborhood and neighborhood.get("nodes"):
                        _render_graph_plotly(
                            neighborhood["nodes"],
                            neighborhood["edges"],
                            title=(f"Citation network of {law.get('title', urn)[:50]}"),
                        )
                except Exception:
                    st.info("Graph not available for this law.")
                try:
                    related = db.find_related_laws(urn, limit=15)
                    if related:
                        st.subheader("Related laws")
                        for r in related[:10]:
                            _render_law_card(r, db, key_prefix="detail-related")
                except Exception:
                    pass


def page_citations():
    st.header("🔗 Rete Citazioni")
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
    st.header("🏛️ Aree Giuridiche")
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
                st.session_state["goto_page"] = "📖 Scheda Legge"
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
                            st.session_state["goto_page"] = "📖 Scheda Legge"
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
# NEW PAGES: GROQ CITIZEN ASSISTANT + LATEST LAWS TRACKER
# ─────────────────────────────────────────────────────────────────

def page_groq_assistant():
    """Citizen AI assistant powered by Groq + dataset RAG."""
    st.header("🤖 Assistente AI — Normattiva")
    st.caption(
        "Fai una domanda in linguaggio comune. L'assistente cerca le norme pertinenti nel dataset "
        "e risponde citando solo fonti reali estratte dal database Normattiva. "
        "Le risposte sono **ancorate al dataset**: nessuna invenzione."
    )

    db = load_db()
    if not db:
        st.error("Database non disponibile. L'assistente richiede il database Normattiva.")
        return

    has_groq = bool(os.environ.get("GROQ_API_KEY", "").strip())
    if not has_groq:
        st.warning(
            "⚠️ **GROQ_API_KEY non configurata.** "
            "Imposta il secret `GROQ_API_KEY` nelle impostazioni dello Space per abilitare l'AI. "
            "L'assistente funziona anche in modalità solo-ricerca (senza AI) mostrando le norme rilevanti."
        )

    # ── Settings ────────────────────────────────────────────────
    with st.expander("⚙️ Impostazioni assistente", expanded=False):
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            model = st.selectbox(
                "Modello AI",
                list(GROQ_MODELS.keys()),
                format_func=lambda k: GROQ_MODELS[k],
                key="groq-model",
                disabled=not has_groq,
            )
        with col_b:
            top_k = st.slider("Norme da consultare (RAG top-k)", 3, 15, 7, key="groq-topk")
        with col_c:
            only_vigenti = st.checkbox("Solo norme vigenti", value=True, key="groq-vigenti")
        temperature = st.slider("Creatività risposta (0=preciso, 0.5=bilanciato)", 0.0, 0.5, 0.1, step=0.05, key="groq-temp")

    # ── Presets ──────────────────────────────────────────────────
    st.subheader("💬 Fai la tua domanda")
    st.caption("Oppure scegli un esempio per iniziare subito:")
    PRESETS = [
        "Quali sono i miei diritti in caso di licenziamento?",
        "Qual è il programma scolastico previsto dalla legge per quest'anno?",
        "Come funziona la tutela della privacy online?",
        "Quando scatta l'obbligo di pagare l'IMU?",
        "Quali norme regolano i contratti di locazione?",
        "Cosa prevede la legge sul codice della strada per le multe?",
    ]
    if "groq_prefill" not in st.session_state:
        st.session_state["groq_prefill"] = ""
    preset_row1 = st.columns(3)
    preset_row2 = st.columns(3)
    for i, p in enumerate(PRESETS):
        row = preset_row1 if i < 3 else preset_row2
        if row[i % 3].button(p, key=f"groq-preset-{i}", use_container_width=True):
            st.session_state["groq_prefill"] = p
            st.rerun()

    question = st.text_area(
        "Domanda",
        value=st.session_state.get("groq_prefill", ""),
        placeholder="Es.: Quali diritti ho se il mio datore di lavoro mi licenzia senza preavviso?",
        height=90,
        key="groq-question",
        label_visibility="collapsed",
    )

    ask_col, clear_col = st.columns([5, 1])
    with ask_col:
        ask_btn = st.button("🔍 Analizza e rispondi", key="groq-ask", type="primary", disabled=not question.strip())
    with clear_col:
        if st.button("🗑️ Cancella", key="groq-clear"):
            st.session_state["groq_chat"] = []
            st.session_state["groq_prefill"] = ""
            st.rerun()

    if "groq_chat" not in st.session_state:
        st.session_state["groq_chat"] = []

    if ask_btn and question.strip():
        st.session_state["groq_prefill"] = ""
        with st.spinner("🔍 Cercando norme rilevanti nel dataset…"):
            try:
                results = db.search_fts(question.strip(), limit=100)
                if only_vigenti:
                    results = [r for r in results if _normalize_status(r.get("status")) == "in_force"]
                evidence = results[:top_k]
            except Exception as e:
                st.error(f"Errore nella ricerca: {e}")
                evidence = []

        if not evidence:
            st.warning("Nessuna norma rilevante trovata nel dataset con i filtri attuali. Prova a rimuovere il filtro 'Solo vigenti'.")
        else:
            answer_text = None
            error_msg = None
            if has_groq:
                with st.spinner("🤖 Analizzando le norme con Groq AI…"):
                    answer_text, error_msg = _call_groq(
                        question=question.strip(),
                        context_laws=evidence,
                        model=model,
                        temperature=temperature,
                    )
            st.session_state["groq_chat"].insert(0, {
                "q": question.strip(),
                "a": answer_text,
                "err": error_msg,
                "evidence": evidence,
            })

    # ── Chat history ─────────────────────────────────────────────
    if st.session_state["groq_chat"]:
        for idx, item in enumerate(st.session_state["groq_chat"]):
            is_latest = idx == 0
            with st.expander(f"{'🔵' if is_latest else '⚫'} Q: {item['q'][:100]}", expanded=is_latest):
                # AI answer
                if item.get("a"):
                    st.markdown("### 🤖 Risposta AI")
                    st.markdown(item["a"])
                    st.caption("⚠️ Le risposte si basano sul dataset Normattiva. Per decisioni legali consulta un professionista.")
                elif item.get("err"):
                    st.error(f"AI non disponibile: {item['err']}")
                    st.info("Di seguito le norme trovate nel dataset che puoi consultare direttamente.")

                # Evidence cards
                st.markdown("---")
                st.markdown(f"### 📚 Norme consultate ({len(item['evidence'])} trovate nel dataset)")
                for ev_idx, ev in enumerate(item["evidence"]):
                    status_chip = _status_chip(ev.get("status"))
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([5, 1, 1])
                        with c1:
                            st.markdown(f"**{ev.get('title', 'N/A')}**")
                            st.caption(f"`{ev.get('urn', 'N/A')}` | {ev.get('type', '')} | {ev.get('year', 'N/A')} | {status_chip}")
                            snippet = (ev.get("snippet") or ev.get("text") or "")[:300].strip()
                            if snippet:
                                st.caption(f"…{snippet}…")
                        with c2:
                            st.markdown(status_chip)
                        with c3:
                            if st.button("Apri →", key=f"groq-open-{idx}-{ev_idx}-{ev.get('urn','')[:30]}"):
                                st.session_state["detail_urn"] = ev.get("urn")
                                st.session_state["goto_page"] = "📖 Scheda Norma"
                                st.rerun()


def page_latest_laws():
    """Track recently added/updated laws in the dataset."""
    st.header("🆕 Ultime Norme — Storico Vigente")
    st.caption(
        "Monitoraggio cronologico delle norme nel dataset: le più recenti per data di pubblicazione, "
        "con distinzione vigente/abrogata e tracking delle transizioni di stato."
    )

    db = load_db()
    if not db:
        st.error("Database non disponibile.")
        return

    tab_recent, tab_timeline, tab_transitions = st.tabs([
        "📅 Recenti per data",
        "📈 Timeline pubblicazioni",
        "🔄 Transizioni di stato",
    ])

    # ── Tab 1: Recent laws by date ─────────────────────────────
    with tab_recent:
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            status_filter = st.selectbox(
                "Stato",
                ["Tutte", "Solo vigenti", "Solo abrogate"],
                key="latest-status-filter",
            )
        with col_f2:
            type_options = ["Tutti i tipi"]
            try:
                type_rows = db.conn.execute(
                    "SELECT DISTINCT type FROM laws WHERE type IS NOT NULL ORDER BY type"
                ).fetchall()
                type_options += [r[0] for r in type_rows]
            except Exception:
                pass
            type_filter = st.selectbox("Tipo atto", type_options, key="latest-type-filter")
        with col_f3:
            limit = st.selectbox("Quante norme", [50, 100, 200, 500], key="latest-limit")

        where_parts = ["1=1"]
        params: list = []
        if status_filter == "Solo vigenti":
            where_parts.append("status = 'in_force'")
        elif status_filter == "Solo abrogate":
            where_parts.append("status = 'abrogated'")
        if type_filter != "Tutti i tipi":
            where_parts.append("type = ?")
            params.append(type_filter)

        where_clause = " AND ".join(where_parts)
        try:
            rows = db.conn.execute(
                f"SELECT urn, title, type, date, year, status, article_count, importance_score "
                f"FROM laws WHERE {where_clause} "
                f"ORDER BY date DESC, year DESC LIMIT ?",
                params + [limit],
            ).fetchall()
        except Exception as e:
            st.error(f"Query fallita: {e}")
            rows = []

        if not rows:
            st.info("Nessuna norma trovata con i filtri selezionati.")
        else:
            st.success(f"Trovate **{len(rows):,}** norme ordinate per data più recente.")
            df = pd.DataFrame([dict(r) for r in rows])
            df["stato"] = df["status"].apply(_status_chip)
            df["importanza"] = df["importance_score"].apply(
                lambda x: f"{x:.4f}" if x else "—"
            )
            disp = df[["date", "year", "type", "title", "stato", "article_count", "importanza", "urn"]].rename(
                columns={
                    "date": "Data",
                    "year": "Anno",
                    "type": "Tipo",
                    "title": "Titolo",
                    "stato": "Stato",
                    "article_count": "Articoli",
                    "importanza": "PageRank",
                    "urn": "URN",
                }
            )
            disp["Titolo"] = disp["Titolo"].str[:80]
            st.dataframe(disp, use_container_width=True, hide_index=True)

            # Quick law card for selected URN
            st.divider()
            st.subheader("🔍 Apri scheda norma")
            urn_choices = {f"{r['title'][:70]} ({r['date'] or r['year']})": r['urn'] for r in [dict(x) for x in rows] if r.get('urn')}
            sel = st.selectbox("Seleziona norma dalla lista", list(urn_choices.keys()), key="latest-urn-sel")
            if sel and st.button("Apri scheda →", key="latest-open-btn"):
                st.session_state["detail_urn"] = urn_choices[sel]
                st.session_state["goto_page"] = "📖 Scheda Norma"
                st.rerun()

    # ── Tab 2: Timeline chart ───────────────────────────────────
    with tab_timeline:
        st.subheader("📈 Distribuzione temporale pubblicazioni")
        try:
            year_rows = db.conn.execute(
                "SELECT year, status, COUNT(*) cnt FROM laws "
                "WHERE year IS NOT NULL AND year > 1800 "
                "GROUP BY year, status ORDER BY year"
            ).fetchall()
        except Exception:
            year_rows = []

        if year_rows:
            ydf = pd.DataFrame([dict(r) for r in year_rows])
            ydf["status_label"] = ydf["status"].apply(
                lambda s: "Vigente" if _normalize_status(s) == "in_force" else "Abrogata"
            )
            fig = px.bar(
                ydf,
                x="year",
                y="cnt",
                color="status_label",
                color_discrete_map={"Vigente": "#0a7a5a", "Abrogata": "#c0392b"},
                title="Norme nel dataset per anno di pubblicazione",
                labels={"year": "Anno", "cnt": "Numero norme", "status_label": "Stato"},
                barmode="stack",
            )
            fig.update_layout(height=450)
            st.plotly_chart(fig, use_container_width=True)

            # Decade summary
            st.subheader("Riepilogo per decennio")
            ydf["decade"] = (ydf["year"] // 10 * 10).astype(str) + "s"
            dec = ydf.groupby(["decade", "status_label"])["cnt"].sum().reset_index()
            fig2 = px.bar(
                dec,
                x="decade",
                y="cnt",
                color="status_label",
                color_discrete_map={"Vigente": "#0a7a5a", "Abrogata": "#c0392b"},
                title="Norme per decennio",
                labels={"decade": "Decennio", "cnt": "Norme", "status_label": "Stato"},
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("Dati temporali non disponibili.")

        # Most recent per type
        st.subheader("📋 Ultima norma vigente per tipo di atto")
        try:
            last_by_type = db.conn.execute(
                "SELECT type, MAX(date) max_date, COUNT(*) cnt "
                "FROM laws WHERE status = 'in_force' AND type IS NOT NULL "
                "GROUP BY type ORDER BY max_date DESC LIMIT 20"
            ).fetchall()
            if last_by_type:
                st.dataframe(
                    pd.DataFrame([dict(r) for r in last_by_type]).rename(
                        columns={"type": "Tipo", "max_date": "Data più recente", "cnt": "Totale vigenti"}
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
        except Exception:
            pass

    # ── Tab 3: Status transitions ───────────────────────────────
    with tab_transitions:
        st.subheader("🔄 Storico transizioni di stato")
        st.caption(
            "Ogni volta che viene effettuato uno snapshot manuale del dataset, "
            "vengono registrate le norme che hanno cambiato stato (vigente → abrogata o viceversa)."
        )

        _ensure_status_timeline_schema(db)

        snapshots = _load_status_snapshots(db)
        if not snapshots:
            st.info(
                "Nessuno snapshot di stato registrato. "
                "Vai su **🇮🇹 Lab Overview → Status Timeline** per catturare il primo snapshot."
            )
            if st.button("Cattura snapshot ora", key="latest-snap-btn"):
                try:
                    result = _capture_status_snapshot(db, note="snapshot-automatico")
                    st.success(
                        f"Snapshot #{result['snapshot_id']} acquisito: "
                        f"{result['laws_captured']:,} norme, {result['transitions']} transizioni."
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Snapshot fallito: {e}")
        else:
            st.success(f"**{len(snapshots)}** snapshot registrati.")
            snap_df = pd.DataFrame(snapshots)
            st.dataframe(
                snap_df.rename(columns={"id": "ID", "captured_at": "Acquisito il", "note": "Nota"}),
                use_container_width=True,
                hide_index=True,
            )

            transitions = _load_status_transitions(db, limit=500)
            if transitions:
                tr_df = pd.DataFrame(transitions)
                st.subheader(f"Ultime {len(transitions)} transizioni rilevate")
                tr_df["direzione"] = tr_df.apply(
                    lambda r: "➡️ Abrogata" if _normalize_status(r.get("to_status")) == "abrogated"
                    else ("✅ Vigente" if _normalize_status(r.get("to_status")) == "in_force" else "🔄 Cambio"),
                    axis=1,
                )
                st.dataframe(
                    tr_df[["detected_at", "direzione", "year", "title", "from_status", "to_status", "urn"]].rename(
                        columns={
                            "detected_at": "Rilevata il",
                            "direzione": "Direzione",
                            "year": "Anno",
                            "title": "Titolo",
                            "from_status": "Da",
                            "to_status": "A",
                            "urn": "URN",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                # Summary chart
                dir_counts = tr_df["direzione"].value_counts().reset_index()
                dir_counts.columns = ["Tipo transizione", "Conteggio"]
                fig = px.pie(dir_counts, names="Tipo transizione", values="Conteggio",
                             title="Distribuzione transizioni di stato", hole=0.4)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Nessuna transizione rilevata tra snapshot successivi.")

            if st.button("Cattura nuovo snapshot ora", key="latest-new-snap"):
                try:
                    note = f"snapshot-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"
                    result = _capture_status_snapshot(db, note=note)
                    st.success(
                        f"Snapshot #{result['snapshot_id']}: {result['laws_captured']:,} norme, "
                        f"{result['transitions']} transizioni rilevate."
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Snapshot fallito: {e}")


# ─────────────────────────────────────────────────────────────────
# AI HELPER WIDGETS (sidebar chat + law detail AI tab)
# ─────────────────────────────────────────────────────────────────

def _render_law_ai_tab(law: dict, db, urn: str):
    """Split-view AI analysis panel embedded inside the law detail page."""
    has_groq = bool(os.environ.get("GROQ_API_KEY", "").strip())

    st.caption(
        "L'assistente analizza il testo di **questa specifica norma**. "
        "Fai domande su articoli, sanzioni, requisiti, destinatari, ecc."
    )

    col_text, col_ai = st.columns([1, 1])

    text = (law.get("text") or "").strip()

    with col_text:
        st.markdown("#### 📄 Testo della Norma")
        st.caption(
            f"**{law.get('title', '')}** | "
            f"{_status_chip(law.get('status'))} | "
            f"Anno {law.get('year', 'N/A')}"
        )
        st.text_area(
            "Testo",
            text or "(Testo non disponibile nel dataset)",
            height=520,
            disabled=True,
            key=f"law-ai-text-viewer-{urn[:30]}",
            label_visibility="collapsed",
        )

    with col_ai:
        st.markdown("#### 💬 Chiedi all'AI su questa norma")

        if not has_groq:
            st.warning(
                "⚠️ **GROQ_API_KEY non configurata.** "
                "Imposta il secret nelle impostazioni dello Space per abilitare l'AI."
            )

        PRESETS_LAW = [
            "Di cosa parla questa norma in sintesi?",
            "Quali sono le sanzioni o conseguenze previste?",
            "Chi è soggetto a questa norma (destinatari)?",
            "Quali obblighi impone ai cittadini?",
            "Ci sono eccezioni o casi particolari?",
            "Quando è entrata in vigore ed è ancora attuale?",
        ]

        st.caption("Domande rapide:")
        p_cols = st.columns(2)
        if "law_ai_prefill" not in st.session_state:
            st.session_state["law_ai_prefill"] = ""
        for i, p in enumerate(PRESETS_LAW[:4]):
            if p_cols[i % 2].button(p[:38] + "…", key=f"law-ai-preset-{urn[:20]}-{i}"):
                st.session_state["law_ai_prefill"] = p
                st.rerun()

        question = st.text_area(
            "Domanda sulla norma",
            value=st.session_state.get("law_ai_prefill", ""),
            placeholder="Es.: Cosa dice l'articolo 1? Quali sanzioni sono previste?",
            height=85,
            key=f"law-ai-q-{urn[:30]}",
            label_visibility="collapsed",
        )

        ask_col, clear_col = st.columns([5, 1])
        with ask_col:
            ask_btn = st.button(
                "🔍 Analizza",
                key=f"law-ai-ask-{urn[:30]}",
                type="primary",
                disabled=not (question or "").strip(),
            )
        with clear_col:
            if st.button("🗑️", key=f"law-ai-clear-{urn[:30]}"):
                st.session_state[f"law_ai_chat_{urn[:40]}"] = []
                st.session_state["law_ai_prefill"] = ""
                st.rerun()

        chat_key = f"law_ai_chat_{urn[:40]}"
        if chat_key not in st.session_state:
            st.session_state[chat_key] = []

        if ask_btn and (question or "").strip():
            st.session_state["law_ai_prefill"] = ""
            context_laws = [law]
            try:
                related = db.find_related_laws(urn, limit=3)
                if related:
                    context_laws.extend(related[:3])
            except Exception:
                pass

            answer, err = None, None
            if has_groq:
                with st.spinner("🤖 Analizzando la norma…"):
                    answer, err = _call_groq(
                        question=question.strip(),
                        context_laws=context_laws,
                        model=GROQ_DEFAULT_MODEL,
                        max_tokens=900,
                        temperature=0.1,
                    )
            else:
                err = "Configura GROQ_API_KEY per ottenere risposte AI."

            st.session_state[chat_key].insert(0, {
                "q": question.strip(),
                "a": answer,
                "err": err,
            })
            st.rerun()

        for idx, item in enumerate(st.session_state.get(chat_key, [])):
            with st.expander(f"Q: {item['q'][:70]}", expanded=(idx == 0)):
                if item.get("a"):
                    st.markdown(item["a"])
                    st.caption(
                        "⚠️ Risposta basata sul dataset Normattiva. "
                        "Per decisioni legali consulta un professionista."
                    )
                elif item.get("err"):
                    st.error(item["err"])


def _render_groq_sidebar_chat(db):
    """Persistent compact AI chat widget shown in the sidebar on every page."""
    has_groq = bool(os.environ.get("GROQ_API_KEY", "").strip())

    with st.sidebar.expander("🤖 Chiedi all'AI", expanded=True):
        if not db:
            st.caption("Database non disponibile.")
            return

        current_urn = st.session_state.get("detail_urn")
        if current_urn:
            try:
                row = db.conn.execute(
                    "SELECT title FROM laws WHERE urn=? LIMIT 1", (current_urn,)
                ).fetchone()
                if row:
                    st.caption(f"📌 *{row['title'][:55]}*")
            except Exception:
                pass
        else:
            st.caption("Nessuna norma aperta — farò una ricerca nel dataset.")

        if not has_groq:
            st.caption("⚠️ AI non disponibile — mostro le norme trovate.")

        sb_q = st.text_input(
            "Domanda",
            key="sidebar-groq-q",
            placeholder="Es.: Cosa prevede questa norma?",
            label_visibility="collapsed",
        )

        if st.button("Chiedi →", key="sidebar-groq-btn", disabled=not (sb_q or "").strip()):
            context_laws = []
            if current_urn:
                try:
                    r = db.conn.execute(
                        "SELECT * FROM laws WHERE urn=? LIMIT 1", (current_urn,)
                    ).fetchone()
                    if r:
                        context_laws = [dict(r)]
                except Exception:
                    pass

            if not context_laws:
                try:
                    results = db.search_fts(sb_q.strip(), limit=30)
                    context_laws = [
                        r for r in results
                        if _normalize_status(r.get("status")) == "in_force"
                    ][:5]
                    if not context_laws:
                        context_laws = results[:5]
                except Exception:
                    pass

            if context_laws and has_groq:
                answer, err = _call_groq(
                    question=sb_q.strip(),
                    context_laws=context_laws,
                    model=GROQ_DEFAULT_MODEL,
                    max_tokens=600,
                    temperature=0.1,
                )
                reply = answer if answer else f"⚠️ {err}"
            elif context_laws:
                titles = "\n- ".join(l.get("title", "")[:60] for l in context_laws[:3])
                reply = f"Norme trovate nel dataset:\n- {titles}"
            else:
                reply = "Nessuna norma trovata per questa domanda."

            st.session_state["sidebar_groq_last"] = {"q": sb_q.strip(), "a": reply}

        last = st.session_state.get("sidebar_groq_last")
        if last:
            st.caption(f"**Q:** {last['q'][:60]}")
            st.info(last["a"][:450])
            if st.button("Approfondisci →", key="sidebar-groq-full"):
                st.session_state["groq_prefill"] = last["q"]
                st.session_state["goto_page"] = "🤖 Assistente AI"
                st.rerun()


# ─────────────────────────────────────────────────────────────────
# MOBILE CSS + MVP SHARED HELPERS
# ─────────────────────────────────────────────────────────────────

_MOBILE_CSS = """<style>
@media (max-width: 768px) {
    .block-container { padding: 0.4rem 0.4rem 5rem !important; }
    [data-testid="stSidebar"] { display: none !important; }
}
.nv-card {
    border: 1px solid #dde3f0; border-radius: 10px;
    padding: 10px 12px; margin-bottom: 8px;
    background: #f7f9ff; font-size: 0.9em;
}
.nv-vigente { color: #16a34a; font-weight: 600; }
.nv-abrogata { color: #dc2626; font-weight: 600; }
[data-testid="stChatMessageContent"] { font-size: 0.91em; }
</style>"""


def _mvp_law_card(law, key_prefix, col, open_key):
    """Compact law card with Open button, rendered into a column."""
    status = _normalize_status(law.get("status"))
    badge = "🟢 Vigente" if status == "in_force" else "🔴 Abrogata"
    title = (law.get("title") or "N/A")[:68]
    col.markdown(
        f"<div class='nv-card'><b>{title}</b><br>"
        f"<span class='{'nv-vigente' if status == 'in_force' else 'nv-abrogata'}'>{badge}</span>"
        f" · {law.get('type','?')} {law.get('year','')}</div>",
        unsafe_allow_html=True,
    )
    safe_key = (law.get("urn") or "x")[:18].replace(":", "-").replace("/", "-")
    if col.button("📖 Apri", key=f"{key_prefix}-{safe_key}", use_container_width=True):
        st.session_state[open_key] = law.get("urn")
        st.rerun()


def _mvp_search_and_reply(question, db, prefix="mvp"):
    """FTS search + Groq RAG. Returns (reply_text, laws)."""
    if not db:
        return "Database non disponibile.", []
    try:
        results = db.search_fts(question, limit=30)
    except Exception:
        results = []
    vigenti = [r for r in results if _normalize_status(r.get("status")) == "in_force"]
    context_laws = vigenti[:7] if vigenti else results[:7]
    if not context_laws:
        return "Non ho trovato norme correlate nel dataset. Prova a riformulare la domanda.", []
    answer, err = _call_groq(question, context_laws, model=GROQ_DEFAULT_MODEL, max_tokens=700, temperature=0.1)
    return (answer or f"⚠️ {err}"), context_laws


def _mvp_inline_law(law, db, key_suffix):
    """Inline law viewer: title strip + text area + citations expander."""
    text = law.get("text") or ""
    urn = law.get("urn", "")
    status = _normalize_status(law.get("status"))
    st.markdown(
        f"**URN:** `{urn}` &nbsp;·&nbsp; {law.get('type','?')} {law.get('year','')} &nbsp;·&nbsp; "
        f"{'🟢 **Vigente**' if status == 'in_force' else '🔴 **Abrogata**'}",
        unsafe_allow_html=True,
    )
    if text:
        st.text_area(
            "Testo integrale",
            text[:6000] + ("…" if len(text) > 6000 else ""),
            height=280,
            disabled=True,
            key=f"nv-law-text-{key_suffix}",
        )
    else:
        st.info("Testo non disponibile nel dataset per questa norma.")
    if db:
        cited_by, cites_out = [], []
        try:
            cited_by = db.get_citations_incoming(urn, limit=10)
            cites_out = db.get_citations_outgoing(urn, limit=10)
        except Exception:
            pass
        if cited_by or cites_out:
            with st.expander(f"🔗 Citazioni ({len(cited_by)} in entrata · {len(cites_out)} in uscita)"):
                c1, c2 = st.columns(2)
                with c1:
                    st.caption("Citate da questa norma:")
                    for c in cites_out[:8]:
                        st.caption(f"→ {(c.get('cited_urn',''))[:55]}")
                with c2:
                    st.caption("Norme che citano questa:")
                    for c in cited_by[:8]:
                        st.caption(f"← {(c.get('citing_urn',''))[:55]}")


def _mvp_get_law(db, urn):
    """Fetch a single law dict by URN. Returns None if not found."""
    try:
        row = db.conn.execute("SELECT * FROM laws WHERE urn=? LIMIT 1", (urn,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────
# MVP A — CHAT-FIRST
# ─────────────────────────────────────────────────────────────────

def _mvp_a_chat_first(db):
    """MVP A: Chat is the landing. Quick chips + search results inline."""
    st.caption(
        "💬 **Interfaccia A — Chat-First** &nbsp;·&nbsp; "
        "L'AI è il punto d'ingresso: fai una domanda in linguaggio naturale.",
        unsafe_allow_html=True,
    )
    qa_cols = st.columns(4)
    quick_actions = [
        ("🔍 Cerca", "Trova norme su lavoro e licenziamento"),
        ("🏫 Scuola", "Qual è il programma scolastico previsto dalla legge?"),
        ("🆕 Ultime", "Mostrami le ultime leggi pubblicate"),
        ("⚖️ Diritti", "Quali sono i miei diritti fondamentali come lavoratore?"),
    ]
    for i, (label, action) in enumerate(quick_actions):
        if qa_cols[i].button(label, key=f"mvp-a-qa-{i}", use_container_width=True):
            st.session_state.setdefault("mvp_a_messages", [])
            st.session_state["mvp_a_messages"].append({"role": "user", "content": action})
            st.session_state["mvp_a_pending"] = action
            st.rerun()

    st.divider()

    if "mvp_a_messages" not in st.session_state:
        st.session_state["mvp_a_messages"] = [
            {
                "role": "assistant",
                "content": (
                    "👋 **Ciao! Sono il tuo assistente giuridico** basato sul dataset Normattiva.\n\n"
                    "Dimmi cosa vuoi sapere — cerco le norme, le spiego in modo semplice e ti mostro "
                    "i testi originali. Prova a chiedermi:\n"
                    "- *Cosa prevede la legge sul lavoro da casa?*\n"
                    "- *Quando scatta il pagamento dell'IMU?*"
                ),
                "type": "welcome",
                "laws": [],
            }
        ]

    pending = st.session_state.pop("mvp_a_pending", None)
    if pending and db:
        with st.spinner("🔍 Cercando nel dataset Normattiva…"):
            reply_text, laws = _mvp_search_and_reply(pending, db, "mvp-a")
        st.session_state["mvp_a_messages"].append(
            {"role": "assistant", "content": reply_text, "type": "search_results", "laws": laws}
        )

    for idx, msg in enumerate(st.session_state["mvp_a_messages"]):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            laws = msg.get("laws") or []
            if laws:
                st.caption(f"📚 {len(laws)} norme consultate dal dataset — clicca per aprire:")
                g1, g2 = st.columns(2)
                for j, law in enumerate(laws[:6]):
                    _mvp_law_card(law, f"mvp-a-card-{idx}", g1 if j % 2 == 0 else g2, "mvp_a_open_urn")

    open_urn = st.session_state.get("mvp_a_open_urn")
    if open_urn and db:
        law = _mvp_get_law(db, open_urn)
        if law:
            with st.expander(f"📖 {(law.get('title') or '')[:80]}", expanded=True):
                _mvp_inline_law(law, db, f"a-{open_urn[:12]}")
                col_ask, col_close = st.columns([3, 1])
                ask_q = col_ask.text_input(
                    "Chiedi su questa norma", key="mvp-a-law-q",
                    placeholder="Es.: Cosa significa l'Art. 4?",
                )
                if col_ask.button("Chiedi →", key="mvp-a-law-ask", disabled=not ask_q.strip()):
                    full_q = f"[Norma aperta: {law.get('title','')}] {ask_q}"
                    st.session_state["mvp_a_messages"].append({"role": "user", "content": ask_q})
                    st.session_state["mvp_a_pending"] = full_q
                    st.session_state.pop("mvp_a_open_urn", None)
                    st.rerun()
                if col_close.button("✕ Chiudi", key="mvp-a-law-close"):
                    st.session_state.pop("mvp_a_open_urn", None)
                    st.rerun()

    user_input = st.chat_input("Scrivi la tua domanda giuridica…", key="mvp-a-input")
    if user_input:
        st.session_state["mvp_a_messages"].append({"role": "user", "content": user_input})
        st.session_state["mvp_a_pending"] = user_input
        st.rerun()


# ─────────────────────────────────────────────────────────────────
# MVP B — SPLIT-SCREEN
# ─────────────────────────────────────────────────────────────────

def _mvp_b_split_screen(db):
    """MVP B: Persistent split — search list left, content + AI right."""
    st.caption(
        "⚡ **Interfaccia B — Split-Screen** &nbsp;·&nbsp; "
        "Cerca a sinistra, leggi e chiedi all'AI a destra. Entrambi sempre visibili.",
        unsafe_allow_html=True,
    )
    col_left, col_right = st.columns([1, 2])

    with col_left:
        st.subheader("🔍 Cerca")
        search_q = st.text_input(
            "Cerca nel dataset", key="mvp-b-search",
            placeholder="Es.: lavoro, scuola, IMU…",
            label_visibility="collapsed",
        )
        if st.button("Cerca →", key="mvp-b-search-btn", use_container_width=True) and search_q.strip() and db:
            with st.spinner("Ricerca…"):
                results = db.search_fts(search_q.strip(), limit=20)
            vigenti = [r for r in results if _normalize_status(r.get("status")) == "in_force"]
            st.session_state["mvp_b_results"] = vigenti[:10] if vigenti else results[:10]
            st.session_state["mvp_b_query"] = search_q.strip()
            st.session_state.pop("mvp_b_open_urn", None)

        st.caption("Azioni rapide:")
        for label, q in [
            ("🏫 Scuola", "istruzione programma scolastico"),
            ("⚖️ Lavoro", "contratto lavoro licenziamento"),
            ("🏠 Affitti", "locazione affitto"),
            ("💶 IMU", "imposta municipale propria IMU"),
        ]:
            if st.button(label, key=f"mvp-b-quick-{label}", use_container_width=True) and db:
                results = db.search_fts(q, limit=15)
                vigenti = [r for r in results if _normalize_status(r.get("status")) == "in_force"]
                st.session_state["mvp_b_results"] = vigenti[:10] if vigenti else results[:10]
                st.session_state["mvp_b_query"] = q
                st.session_state.pop("mvp_b_open_urn", None)
                st.rerun()

        results = st.session_state.get("mvp_b_results", [])
        if results:
            st.caption(f"**{len(results)} norme** — clicca per aprire →")
            for law in results:
                status = _normalize_status(law.get("status"))
                badge = "🟢" if status == "in_force" else "🔴"
                title = (law.get("title") or "N/A")[:50]
                safe_key = (law.get("urn") or "x")[:18].replace(":", "-").replace("/", "-")
                if st.button(f"{badge} {title}", key=f"mvp-b-open-{safe_key}", use_container_width=True):
                    st.session_state["mvp_b_open_urn"] = law.get("urn")
                    st.session_state.pop("mvp_b_ai_reply", None)
                    st.session_state.pop("mvp_b_summary", None)
                    st.rerun()

    with col_right:
        open_urn = st.session_state.get("mvp_b_open_urn")
        if open_urn and db:
            law = _mvp_get_law(db, open_urn)
            if law:
                st.subheader((law.get("title") or "")[:75])
                _mvp_inline_law(law, db, f"b-{open_urn[:12]}")
                st.divider()
                st.subheader("🤖 Chiedi all'AI su questa norma")
                ai_q = st.text_input(
                    "Domanda", key="mvp-b-ai-q",
                    placeholder="Es.: Cosa significa questo articolo?",
                    label_visibility="collapsed",
                )
                if st.button("Analizza →", key="mvp-b-ai-ask", disabled=not ai_q.strip()):
                    with st.spinner("AI in elaborazione…"):
                        answer, err = _call_groq(
                            f"[Norma: {law.get('title','')}] {ai_q}",
                            [law], model=GROQ_DEFAULT_MODEL, max_tokens=600,
                        )
                    st.session_state["mvp_b_ai_reply"] = answer or f"⚠️ {err}"
                if st.session_state.get("mvp_b_ai_reply"):
                    st.info(st.session_state["mvp_b_ai_reply"])
        elif st.session_state.get("mvp_b_query"):
            query = st.session_state["mvp_b_query"]
            st.subheader(f"Risultati per: *{query}*")
            if st.button("🤖 Analizza con AI", key="mvp-b-ai-summary"):
                context = st.session_state.get("mvp_b_results", [])[:5]
                if context:
                    with st.spinner("Analisi AI…"):
                        answer, err = _call_groq(query, context, model=GROQ_DEFAULT_MODEL, max_tokens=700)
                    st.session_state["mvp_b_summary"] = answer or f"⚠️ {err}"
            if st.session_state.get("mvp_b_summary"):
                st.info(st.session_state["mvp_b_summary"])
            for law in st.session_state.get("mvp_b_results", [])[:5]:
                st.markdown(
                    f"**{(law.get('title') or 'N/A')[:80]}**  \n"
                    f"{'🟢 Vigente' if _normalize_status(law.get('status'))=='in_force' else '🔴 Abrogata'}"
                    f" · {law.get('type','?')} {law.get('year','')}"
                )
                st.markdown("---")
        else:
            st.info("👈 Cerca una norma a sinistra per visualizzarla qui.")
            st.caption(
                "**Come usare:**\n"
                "1. Digita una parola chiave a sinistra\n"
                "2. Clicca su un risultato per aprirlo qui\n"
                "3. Chiedi all'AI sul testo aperto"
            )


# ─────────────────────────────────────────────────────────────────
# MVP C — HUB + 4 PAGES + INLINE AI
# ─────────────────────────────────────────────────────────────────

def _mvp_c_hub_sidebar(db):
    """MVP C: 4-button top nav, single scrolling pages, AI inline."""
    st.caption(
        "🗂️ **Interfaccia C — Hub + AI** &nbsp;·&nbsp; "
        "4 sezioni chiare. AI sempre in fondo alla pagina.",
        unsafe_allow_html=True,
    )
    nav_cols = st.columns(4)
    nav_pages = ["🏠 Home", "🔍 Cerca", "🆕 Ultime", "🗂️ Archivio"]
    current = st.session_state.get("mvp_c_page", "🏠 Home")
    for i, p in enumerate(nav_pages):
        btn_type = "primary" if current == p else "secondary"
        if nav_cols[i].button(p, key=f"mvp-c-nav-{i}", use_container_width=True, type=btn_type):
            st.session_state["mvp_c_page"] = p
            st.rerun()
    st.divider()

    if current == "🏠 Home":
        st.subheader("🇮🇹 Benvenuto in NormattivaVigente")
        st.caption("Esplora le norme vigenti italiane in linguaggio semplice.")
        h1, h2, h3 = st.columns(3)
        topics = [
            (h1, "⚖️ Lavoro", "Contratti, licenziamento, diritti", "lavoro contratto licenziamento"),
            (h2, "🏠 Casa", "Affitti, proprietà, condominio", "locazione affitto proprietà"),
            (h3, "🏫 Scuola", "Programmi, iscrizioni, studenti", "istruzione programma scolastico"),
        ]
        for col, icon_title, desc, q in topics:
            with col:
                st.markdown(f"### {icon_title}")
                st.caption(desc)
                if st.button("Esplora →", key=f"mvp-c-topic-{icon_title}", use_container_width=True) and db:
                    results = db.search_fts(q, limit=10)
                    vigenti = [r for r in results if _normalize_status(r.get("status")) == "in_force"]
                    st.session_state["mvp_c_results"] = vigenti[:8] if vigenti else results[:8]
                    st.session_state["mvp_c_query"] = q
                    st.session_state["mvp_c_page"] = "🔍 Cerca"
                    st.rerun()
        if db:
            st.subheader("📅 Ultime 5 norme")
            try:
                recent = db.conn.execute(
                    "SELECT urn, title, type, year, status, date FROM laws ORDER BY date DESC LIMIT 5"
                ).fetchall()
                for r in recent:
                    r = dict(r)
                    badge = "🟢" if _normalize_status(r.get("status")) == "in_force" else "🔴"
                    rc1, rc2 = st.columns([4, 1])
                    rc1.markdown(
                        f"{badge} **{(r.get('title') or 'N/A')[:72]}**  \n"
                        f"{r.get('type','?')} {r.get('year','')} · {(r.get('date') or '')[:10]}"
                    )
                    safe = (r.get("urn") or "x")[:18].replace(":", "-").replace("/", "-")
                    if rc2.button("Apri", key=f"mvp-c-home-{safe}"):
                        st.session_state["mvp_c_open_urn"] = r["urn"]
                        st.session_state["mvp_c_page"] = "🔍 Cerca"
                        st.rerun()
            except Exception:
                st.info("Norme recenti non disponibili.")

    elif current == "🔍 Cerca":
        q = st.text_input(
            "Cerca nel dataset Normattiva", key="mvp-c-q",
            placeholder="Es.: scuola, lavoro, IMU, affitto…",
        )
        if st.button("🔍 Cerca", key="mvp-c-search-btn") and q.strip() and db:
            with st.spinner("Ricerca…"):
                results = db.search_fts(q.strip(), limit=20)
            vigenti = [r for r in results if _normalize_status(r.get("status")) == "in_force"]
            st.session_state["mvp_c_results"] = vigenti[:10] if vigenti else results[:10]
            st.session_state["mvp_c_query"] = q.strip()
            st.session_state.pop("mvp_c_open_urn", None)

        open_urn = st.session_state.get("mvp_c_open_urn")
        if open_urn and db:
            law = _mvp_get_law(db, open_urn)
            if law:
                if st.button("← Torna ai risultati", key="mvp-c-back"):
                    st.session_state.pop("mvp_c_open_urn", None)
                    st.rerun()
                st.subheader(f"📖 {(law.get('title') or '')[:78]}")
                _mvp_inline_law(law, db, f"c-{open_urn[:12]}")
        else:
            results = st.session_state.get("mvp_c_results", [])
            if results:
                st.caption(f"**{len(results)} risultati** per *{st.session_state.get('mvp_c_query','')}*")
                g1, g2 = st.columns(2)
                for j, law in enumerate(results):
                    _mvp_law_card(law, "mvp-c-res", g1 if j % 2 == 0 else g2, "mvp_c_open_urn")
            else:
                st.info("Usa la barra di ricerca sopra oppure torna alla Home e scegli un'area tematica.")

    elif current == "🆕 Ultime":
        st.subheader("🆕 Ultime norme pubblicate")
        if db:
            try:
                recent = db.conn.execute(
                    "SELECT urn, title, type, year, status, date FROM laws ORDER BY date DESC LIMIT 25"
                ).fetchall()
                for r in recent:
                    r = dict(r)
                    badge = "🟢 Vigente" if _normalize_status(r.get("status")) == "in_force" else "🔴 Abrogata"
                    safe = (r.get("urn") or "x")[:18].replace(":", "-").replace("/", "-")
                    rc1, rc2, rc3 = st.columns([4, 1, 1])
                    rc1.markdown(
                        f"**{(r.get('title') or 'N/A')[:75]}**  \n"
                        f"{badge} · {(r.get('date') or '')[:10]}"
                    )
                    if rc2.button("Apri", key=f"mvp-c-late-open-{safe}"):
                        st.session_state["mvp_c_open_urn"] = r["urn"]
                        st.session_state["mvp_c_page"] = "🔍 Cerca"
                        st.rerun()
                    if rc3.button("AI", key=f"mvp-c-late-ai-{safe}"):
                        with st.spinner("AI…"):
                            a, err = _call_groq(
                                f"Spiegami in modo semplice questa norma: {r.get('title','')}",
                                [r], model=GROQ_DEFAULT_MODEL, max_tokens=400,
                            )
                        st.session_state[f"mvp_c_late_{safe}"] = a or f"⚠️ {err}"
                    reply = st.session_state.get(f"mvp_c_late_{safe}")
                    if reply:
                        st.info(reply[:600])
                    st.markdown("---")
            except Exception as e:
                st.error(f"Errore caricamento norme recenti: {e}")

    elif current == "🗂️ Archivio":
        st.subheader("🗂️ Archivio norme vigenti")
        if db:
            f1, f2 = st.columns(2)
            year_f = f1.text_input("Anno (es. 2024)", key="mvp-c-arch-year")
            type_f = f2.text_input("Tipo (decreto, legge…)", key="mvp-c-arch-type")
            try:
                q_sql = "SELECT urn, title, type, year, status FROM laws WHERE status='in_force'"
                params = []
                if year_f.strip():
                    q_sql += " AND year=?"
                    params.append(year_f.strip())
                if type_f.strip():
                    q_sql += " AND LOWER(type) LIKE ?"
                    params.append(f"%{type_f.strip().lower()}%")
                q_sql += " ORDER BY date DESC LIMIT 30"
                rows = [dict(r) for r in db.conn.execute(q_sql, params).fetchall()]
                st.caption(f"**{len(rows)} norme** (max 30)")
                g1, g2 = st.columns(2)
                for j, law in enumerate(rows):
                    _mvp_law_card(law, "mvp-c-arch", g1 if j % 2 == 0 else g2, "mvp_c_open_urn")
                if st.session_state.get("mvp_c_open_urn"):
                    st.session_state["mvp_c_page"] = "🔍 Cerca"
                    st.rerun()
            except Exception as e:
                st.error(f"Errore archivio: {e}")

    st.divider()
    with st.expander("🤖 Assistente AI — Chiedi qualcosa", expanded=False):
        ai_q = st.text_input(
            "Domanda", key="mvp-c-ai-q",
            placeholder="Es.: Cosa prevede la legge sul telelavoro?",
            label_visibility="collapsed",
        )
        if st.button("Chiedi →", key="mvp-c-ai-btn", disabled=not ai_q.strip()) and db:
            ctx_urn = st.session_state.get("mvp_c_open_urn")
            if ctx_urn:
                law = _mvp_get_law(db, ctx_urn)
                context_laws = [law] if law else []
            else:
                results = db.search_fts(ai_q.strip(), limit=20)
                vigenti = [r for r in results if _normalize_status(r.get("status")) == "in_force"]
                context_laws = vigenti[:5] if vigenti else results[:5]
            with st.spinner("AI in elaborazione…"):
                answer, err = _call_groq(ai_q.strip(), context_laws, model=GROQ_DEFAULT_MODEL, max_tokens=600)
            st.session_state["mvp_c_ai_reply"] = answer or f"⚠️ {err}"
        if st.session_state.get("mvp_c_ai_reply"):
            st.info(st.session_state["mvp_c_ai_reply"])


# ─────────────────────────────────────────────────────────────────
# MVP D — PURE CONVERSATIONAL (INTENT DETECTION)
# ─────────────────────────────────────────────────────────────────

def _mvp_d_detect_intent(text: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ["ultime", "recenti", "nuove norme", "ultimi"]):
        return "latest"
    if re.search(r"costituzione|art[\.\s]+cost", lower):
        return "constitution"
    if any(w in lower for w in ["cerca", "trovami", "norme su", "leggi su", "dove è scritto", "cosa dice la legge su"]):
        return "search"
    return "groq_rag"


def _mvp_d_conversational(db):
    """MVP D: Pure conversational — intent detection routes to cards/law-viewer/AI."""
    st.caption(
        "🔄 **Interfaccia D — Conversazionale** &nbsp;·&nbsp; "
        "Scrivi liberamente. L'AI capisce cosa cerchi e risponde con norme e spiegazioni.",
        unsafe_allow_html=True,
    )
    chip_cols = st.columns(5)
    chips = [
        ("🔍 Cerca", "Cerca norme sul licenziamento senza preavviso"),
        ("🆕 Ultime", "Mostrami le ultime leggi pubblicate"),
        ("🏫 Scuola", "Qual è il programma scolastico previsto dalla legge?"),
        ("📜 Costituzione", "Cosa dice la Costituzione sul diritto al lavoro?"),
        ("🏠 Affitti", "Cosa dice la legge sui contratti di locazione?"),
    ]
    for i, (label, action) in enumerate(chips):
        if chip_cols[i].button(label, key=f"mvp-d-chip-{i}", use_container_width=True):
            st.session_state.setdefault("mvp_d_messages", [])
            st.session_state["mvp_d_messages"].append({"role": "user", "content": action})
            st.session_state["mvp_d_pending"] = action
            st.rerun()

    st.divider()

    if "mvp_d_messages" not in st.session_state:
        st.session_state["mvp_d_messages"] = [
            {
                "role": "assistant",
                "content": (
                    "🇮🇹 **Ciao! Sono il tuo assistente giuridico.**\n\n"
                    "Scrivi qualsiasi domanda in linguaggio naturale — capisco cosa cerchi:\n"
                    "- 🔍 Cerco norme rilevanti nel dataset (190.000+ leggi)\n"
                    "- 📖 Ti mostro i testi completi\n"
                    "- 🤖 Spiego ogni norma in modo semplice\n\n"
                    "**Prova:** *Cosa prevede la legge sul telelavoro?*"
                ),
                "type": "welcome",
                "laws": [],
            }
        ]

    pending = st.session_state.pop("mvp_d_pending", None)
    if pending and db:
        intent = _mvp_d_detect_intent(pending)
        new_msg = {"role": "assistant", "content": "", "type": intent, "laws": []}
        if intent == "latest":
            try:
                recent = [
                    dict(r) for r in db.conn.execute(
                        "SELECT urn, title, type, year, status, date FROM laws ORDER BY date DESC LIMIT 12"
                    ).fetchall()
                ]
                new_msg["content"] = f"📅 Ecco le ultime **{len(recent)} norme** pubblicate nel dataset:"
                new_msg["laws"] = recent
            except Exception as e:
                new_msg["content"] = f"⚠️ Errore: {e}"
        elif intent == "constitution":
            results = db.search_fts("costituzione diritti fondamentali", limit=10)
            cost_rows = [
                r for r in results
                if "costituzione" in (r.get("urn") or "").lower()
                or "costituzione" in (r.get("title") or "").lower()
            ] or results[:5]
            answer, err = _call_groq(pending, cost_rows, model=GROQ_DEFAULT_MODEL, max_tokens=600)
            new_msg["content"] = answer or f"⚠️ {err}"
            new_msg["laws"] = cost_rows
        else:
            with st.spinner("🔍 Ricerca nel dataset Normattiva…"):
                reply_text, laws = _mvp_search_and_reply(pending, db, "mvp-d")
            new_msg["content"] = reply_text
            new_msg["laws"] = laws
            new_msg["type"] = "search_results"
        st.session_state["mvp_d_messages"].append(new_msg)

    for idx, msg in enumerate(st.session_state["mvp_d_messages"]):
        with st.chat_message(msg["role"]):
            if msg.get("content"):
                st.markdown(msg["content"])
            laws = msg.get("laws") or []
            if laws:
                st.caption(f"📚 **{len(laws)} norme** nel dataset — clicca per aprire:")
                g1, g2 = st.columns(2)
                for j, law in enumerate(laws[:8]):
                    _mvp_law_card(law, f"mvp-d-card-{idx}", g1 if j % 2 == 0 else g2, "mvp_d_open_urn")

    open_urn = st.session_state.get("mvp_d_open_urn")
    if open_urn and db:
        law = _mvp_get_law(db, open_urn)
        if law:
            with st.chat_message("assistant"):
                st.markdown(f"📖 **Ho aperto la norma:**  \n**{(law.get('title') or '')[:80]}**")
                _mvp_inline_law(law, db, f"d-{open_urn[:12]}")
                col_ask, col_close = st.columns([3, 1])
                ask_q = col_ask.text_input(
                    "Chiedi su questa norma", key="mvp-d-law-q",
                    placeholder="Es.: Cosa significa questo articolo?",
                )
                if col_ask.button("Chiedi →", key="mvp-d-law-ask", disabled=not ask_q.strip()):
                    full_q = f"[Norma: {law.get('title','')}] {ask_q}"
                    st.session_state["mvp_d_messages"].append({"role": "user", "content": ask_q})
                    st.session_state["mvp_d_pending"] = full_q
                    st.session_state.pop("mvp_d_open_urn", None)
                    st.rerun()
                if col_close.button("✕ Chiudi norma", key="mvp-d-law-close"):
                    st.session_state.pop("mvp_d_open_urn", None)
                    st.rerun()

    user_input = st.chat_input("Scrivi liberamente — cerca, chiedi, esplora…", key="mvp-d-input")
    if user_input:
        st.session_state["mvp_d_messages"].append({"role": "user", "content": user_input})
        st.session_state["mvp_d_pending"] = user_input
        st.rerun()


# ─────────────────────────────────────────────────────────────────
# NAVIGATION
# ─────────────────────────────────────────────────────────────────

def main():
    # Build a complete registry of pages and then expose only the subset
    # appropriate for the active `APP_PROFILE` (search / lab / italianlab).
    all_pages = {
        "🏠 Hub Cittadini": page_citizen_hub,
        "📝 Modelli documenti": page_document_templates,
        "⚖️ Confronta scenari": page_scenario_compare,
        "🧭 Start Here": page_start_here,
        "🧠 VOM Guide": page_vom_guide,
        "⚖️ Framework": page_vom_guide,
        "🧪 Lab Lessons": page_lab_lessons,
        "📊 Dashboard": page_dashboard,
        "🧪 Italian Legal Lab": page_italian_legal_lab,
        "🧭 Rights Explorer": page_rights_explorer,
        "⚖️ Giurisprudence": page_jurisprudence_explorer,
        "🇮🇹 Costituzione & Codici": page_costituzione,
        "🔍 Search": page_search,
        "⚡ Vigenti": page_vigenti,
        "🚫 Abrogati": page_abrogated,
        "📜 Storia Normativa": page_multivigente,
        "📋 Browse (All)": page_browse,
        "🤖 LLM Lab": page_llm_lab,
        "🤖 Chatbot": page_chatbot,
        "🤖 Assistente AI": page_groq_assistant,
        "🆕 Ultime Norme": page_latest_laws,
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
            "🧭 Start Here": all_pages["🧭 Start Here"],
            "🧠 VOM Guide": all_pages["🧠 VOM Guide"],
            "🧪 Lab Lessons": all_pages["🧪 Lab Lessons"],
            "🇮🇹 Lab Overview": all_pages["🧪 Italian Legal Lab"],
            "🔍 Cerca Leggi": all_pages["🔍 Search"],
            "⚡ Vigenti": all_pages["⚡ Vigenti"],
            "🚫 Abrogati": all_pages["🚫 Abrogati"],
            "📜 Storia Normativa": all_pages["📜 Storia Normativa"],
            "📋 Sfoglia Archivio": all_pages["📋 Browse (All)"],
            "🇮🇹 Costituzione & Codici": all_pages["🇮🇹 Costituzione & Codici"],
            "🔗 Rete Citazioni": all_pages["🔗 Citations"],
            "🏛️ Aree Giuridiche": all_pages["🏛️ Domains"],
            "📖 Scheda Legge": all_pages["📖 Law Detail"],
            "🔔 Aggiornamenti": all_pages["🔔 Notifications"],
            "📝 Cronologia": all_pages["📝 Update Log"],
            "🆕 Ultime Norme": all_pages["🆕 Ultime Norme"],
            "🤖 Assistente AI": all_pages["🤖 Assistente AI"],
            "📥 Esporta": all_pages["📥 Export"],
        }
        st.sidebar.success("Italian Legal Lab — VOOM: Vigente + Abrogati + Multivigente")
    elif IS_LAB:
        pages = {
            "🧭 Start Here": all_pages["🧭 Start Here"],
            "🧠 VOM Guide": all_pages["🧠 VOM Guide"],
            "🧪 Lab Lessons": all_pages["🧪 Lab Lessons"],
            "📊 Dashboard": all_pages["📊 Dashboard"],
            "🔍 Search": all_pages["🔍 Search"],
            "⚡ Vigenti": all_pages["⚡ Vigenti"],
            "🚫 Abrogati": all_pages["🚫 Abrogati"],
            "📜 Storia Normativa": all_pages["📜 Storia Normativa"],
            "📋 Browse (All)": all_pages["📋 Browse (All)"],
            "🤖 LLM Lab": all_pages["🤖 LLM Lab"],
            "🤖 Assistente AI": all_pages["🤖 Assistente AI"],
            "🆕 Ultime Norme": all_pages["🆕 Ultime Norme"],
            "💶 Fiscal Burden Lab": all_pages["💶 Fiscal Burden Lab"],
            "📖 Law Detail": all_pages["📖 Law Detail"],
            "🔔 Notifications": all_pages["🔔 Notifications"],
            "📝 Update Log": all_pages["📝 Update Log"],
            "📥 Export": all_pages["📥 Export"],
        }
        st.sidebar.success("Normattiva Lab — VOOM: Vigente + Abrogati + Multivigente.")
    else:
        use_advanced = st.sidebar.toggle(
            "🔧 Navigazione classica (avanzata)", value=False, key="adv-mode"
        )
        if not use_advanced:
            # MVP showcase — 4 interface prototypes in tabs
            st.markdown(_MOBILE_CSS, unsafe_allow_html=True)
            db = load_db()
            _render_groq_sidebar_chat(db)
            st.title("🇮🇹 NormattivaVigente")
            st.caption(
                "Scegli l'interfaccia che preferisci — tutte e 4 sono collegate allo stesso "
                "dataset Normattiva con oltre 190.000 leggi."
            )
            tab_a, tab_b, tab_c, tab_d = st.tabs([
                "💬 A — Chat-First",
                "⚡ B — Split-Screen",
                "🗂️ C — Hub + AI",
                "🔄 D — Conversazionale",
            ])
            with tab_a:
                _mvp_a_chat_first(db)
            with tab_b:
                _mvp_b_split_screen(db)
            with tab_c:
                _mvp_c_hub_sidebar(db)
            with tab_d:
                _mvp_d_conversational(db)
            return  # skip classic navigation below

        mobile_simple = st.sidebar.checkbox("Modalità mobile semplificata", value=False, key="mobile-simple")
        pages = {
            "🏠 Hub Cittadini": all_pages["🏠 Hub Cittadini"],
            "🤖 Assistente AI": all_pages["🤖 Assistente AI"],
            "⚖️ Framework": all_pages["⚖️ Framework"],
            "🔍 Cerca Norme": all_pages["🔍 Search"],
            "📝 Modelli documenti": all_pages["📝 Modelli documenti"],
            "⚖️ Confronta scenari": all_pages["⚖️ Confronta scenari"],
            "⚖️ Percorsi Giurisprudenziali": all_pages["⚖️ Giurisprudence"],
            "📖 Scheda Norma": all_pages["📖 Law Detail"],
            "🧩 Rete Normativa": all_pages["🔗 Citations"],
            "🏛️ Aree del Diritto": all_pages["🏛️ Domains"],
            "🇮🇹 Costituzione & Codici": all_pages["🇮🇹 Costituzione & Codici"],
            "📋 Archivio Vigente": all_pages["📋 Browse (All)"],
            "🆕 Ultime Norme": all_pages["🆕 Ultime Norme"],
            "📊 Panoramica Dataset": all_pages["📊 Dashboard"],
            "📥 Dati & Download": all_pages["📥 Export"],
            "🔔 Aggiornamenti": all_pages["🔔 Notifications"],
            "📝 Registro Update": all_pages["📝 Update Log"],
        }
        if mobile_simple:
            pages = {
                "🏠 Hub Cittadini": all_pages["🏠 Hub Cittadini"],
                "🤖 Assistente AI": all_pages["🤖 Assistente AI"],
                "🔍 Cerca Norme": all_pages["🔍 Search"],
                "📖 Scheda Norma": all_pages["📖 Law Detail"],
                "📝 Modelli documenti": all_pages["📝 Modelli documenti"],
                "⚖️ Confronta scenari": all_pages["⚖️ Confronta scenari"],
                "🆕 Ultime Norme": all_pages["🆕 Ultime Norme"],
                "🔔 Aggiornamenti": all_pages["🔔 Notifications"],
            }
        st.sidebar.success("NormattivaVigente — focus on in-force laws.")

    # Allow in-page navigation with cross-profile aliases.
    goto_page = st.session_state.pop("goto_page", None)
    page_aliases = {
        "🔍 Search": "🔍 Cerca Leggi" if IS_ITALIAN_LAB else ("🔍 Search" if IS_LAB else "🔍 Cerca Norme"),
        "📖 Law Detail": "📖 Scheda Legge" if IS_ITALIAN_LAB else ("📖 Law Detail" if IS_LAB else "📖 Scheda Norma"),
        "📋 Browse (All)": "📋 Sfoglia Archivio" if IS_ITALIAN_LAB else ("📋 Browse (All)" if IS_LAB else "📋 Archivio Vigente"),
        "🔗 Citations": "🔗 Rete Citazioni" if IS_ITALIAN_LAB else ("🔗 Citations" if IS_LAB else "🧩 Rete Normativa"),
        "🧭 Rights Explorer": "🧭 Rights Explorer" if (IS_ITALIAN_LAB or IS_LAB) else "🏠 Hub Cittadini",
        "⚖️ Giurisprudence": "⚖️ Giurisprudence" if (IS_ITALIAN_LAB or IS_LAB) else "⚖️ Percorsi Giurisprudenziali",
        "📝 Modelli documenti": "📝 Modelli documenti",
        "⚖️ Confronta scenari": "⚖️ Confronta scenari",
        "🤖 Assistente AI": "🤖 Assistente AI",
        "🆕 Ultime Norme": "🆕 Ultime Norme",
    }
    if goto_page in page_aliases:
        goto_page = page_aliases[goto_page]
    default_page = goto_page if goto_page in pages else None

    st.sidebar.write("### Navigazione")
    if IS_SEARCH:
        st.sidebar.caption("Percorso consigliato: Hub → Cerca Norme → Scheda Norma → Rete Normativa")
    page_keys = list(pages.keys())
    default_idx = page_keys.index(default_page) if default_page else 0

    page = st.sidebar.radio(
        "Go to", page_keys, index=default_idx,
        label_visibility="collapsed", key="page-nav"
    )

    guided_steps = (
        ["🧭 Start Here", "🧠 VOM Guide", "🔍 Cerca Leggi", "📖 Scheda Legge", "🧪 Lab Lessons"]
        if IS_ITALIAN_LAB
        else ["🧭 Start Here", "🧠 VOM Guide", "🔍 Search", "📖 Law Detail", "🧪 Lab Lessons"]
        if IS_LAB
        else ["🏠 Hub Cittadini", "⚖️ Framework", "🔍 Cerca Norme", "⚖️ Percorsi Giurisprudenziali", "📖 Scheda Norma"]
    )
    visited = st.session_state.setdefault("guided_visited", [])
    if page in guided_steps and page not in visited:
        visited.append(page)
    completed = len([p for p in guided_steps if p in visited])
    progress = completed / len(guided_steps) if guided_steps else 0
    st.sidebar.caption("Percorso vigente + giurisprudenza" if IS_SEARCH else "Percorso principiante VOM")
    st.sidebar.progress(progress)
    st.sidebar.caption(f"Completati: {completed}/{len(guided_steps)}")

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
            in_f = db.conn.execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
            ab = db.conn.execute("SELECT COUNT(*) FROM laws WHERE status='abrogated'").fetchone()[0]
            total = in_f + ab
            if IS_ITALIAN_LAB:
                st.sidebar.metric("Leggi nel database", f"{total:,}")
                st.sidebar.caption(f"Vigenti: {in_f:,} | Abrogati: {ab:,}")
            elif IS_LAB:
                st.sidebar.metric("Laws in database", f"{total:,}")
                st.sidebar.caption(f"In force: {in_f:,} | Abrogated: {ab:,}")
            else:
                st.sidebar.metric("In-force laws", f"{in_f:,}")
                st.sidebar.caption("Vigente profile")
        except Exception:
            try:
                count = db.conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
                st.sidebar.metric("Laws in database", f"{count:,}")
            except Exception:
                pass
        st.sidebar.success("Database: ✓ Loaded" if not IS_ITALIAN_LAB else "Database: ✓ Caricato")
    else:
        st.sidebar.error("Database: ✗ Not found" if not IS_ITALIAN_LAB else "Database: ✗ Non trovato")
        laws = load_laws_from_jsonl()
        if laws:
            st.sidebar.metric("Laws (JSONL)", f"{len(laws):,}")

    # Last update
    if db:
        log_entries = _get_update_log(db)
        if log_entries:
            last = log_entries[0].get("timestamp", "")[:10]
            label = "Ultimo aggiornamento" if IS_ITALIAN_LAB else "Last updated"
            st.sidebar.caption(f"{label}: {last}")

    st.sidebar.divider()
    if IS_ITALIAN_LAB:
        st.sidebar.markdown(
            "\U0001f1ee\U0001f1f9 **Italian Legal Lab** — Ricerca giuridica italiana\n\n"
            "190.000+ leggi | FTS5 | Citazioni | Storia normativa"
        )
    elif IS_LAB:
        st.sidebar.markdown(
            "\u2696\ufe0f **OpenNormattiva Lab** — Italian Legal Research\n\n"
            "VOOM: 67,052 in force + 123,859 abrogated = 190,911 laws\n\n"
            "Full-text search | Citation graphs | Legislative history"
        )
    else:
        st.sidebar.markdown(
            "\u2696\ufe0f **NormattivaVigente** — Ricerca norme vigenti\n\n"
            "Solo vigente | Linguaggio semplice | Percorsi guidati | Giurisprudenza"
        )

    # Persistent AI sidebar chat (available on every page)
    _render_groq_sidebar_chat(db)

    pages[page]()


if __name__ == "__main__":
    main()

