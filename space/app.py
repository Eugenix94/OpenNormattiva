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
import time

# Setup paths for imports
_app_dir = Path(__file__).parent
_root_dir = _app_dir.parent
sys.path.insert(0, str(_root_dir))
sys.path.insert(0, str(_app_dir))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)
# Rebuild marker: keep runtime in sync with latest pushed source.

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


# ── Legal hierarchy tier badges ─────────────────────────────────────
_SOURCE_TIER: dict[str, tuple[str, str, str]] = {
    # (tier_roman, bg_color, short_label)
    "Leggi costituzionali":                           ("I",   "#6d28d9", "Costituzionale"),
    "Codici":                                         ("II",  "#1d4ed8", "Codice"),
    "Testi Unici":                                    ("II",  "#1d4ed8", "Testo Unico"),
    "DL e leggi di conversione":                      ("III", "#0369a1", "Legge"),
    "Leggi delega e relativi provvedimenti delegati": ("III", "#0369a1", "L.Delega"),
    "Leggi contenenti deleghe":                       ("III", "#0369a1", "L.Delega"),
    "Leggi finanziarie e di bilancio":                ("III", "#0369a1", "L.Bilancio"),
    "Leggi di ratifica":                              ("III", "#0369a1", "L.Ratifica"),
    "Leggi di delegazione europea":                   ("III", "#0369a1", "Del.EU"),
    "Regi decreti":                                   ("III", "#92400e", "R.D."),
    "Regi decreti legislativi":                       ("III", "#92400e", "R.D.L."),
    "Decreti legislativi luogotenenziali":            ("III", "#92400e", "D.L.Luog."),
    "Decreti Legislativi":                            ("IV",  "#0f766e", "D.Lgs."),
    "Atti di recepimento direttive UE":               ("IV",  "#1e40af", "Recep.UE"),
    "Atti di attuazione Regolamenti UE":              ("IV",  "#1e40af", "Att.UE"),
    "DPR":                                            ("V",   "#475569", "DPR"),
    "Regolamenti ministeriali":                       ("V",   "#475569", "Reg.Min."),
    "DPCM":                                           ("V",   "#475569", "DPCM"),
    "Atti normativi abrogati (in originale)":         ("✕",  "#9ca3af", "Abrogato"),
    "DL decaduti":                                    ("✕",  "#9ca3af", "DL Decad."),
    "DL proroghe":                                    ("✕",  "#9ca3af", "DL Proroga"),
}


def _tier_badge(source_collection: str | None) -> str:
    """Return an inline HTML tier badge for a law's source collection."""
    if not source_collection:
        return ""
    tier, color, label = _SOURCE_TIER.get(source_collection, ("?", "#94a3b8", (source_collection or "")[:15]))
    return (
        f"<span style='display:inline-block;font-size:0.67rem;font-weight:700;"
        f"background:{color};color:#fff;border-radius:3px;padding:1px 5px;"
        f"margin-left:4px;vertical-align:middle;'>T{tier} {label}</span>"
    )


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
    # Only load vigenti — the app is vigente-only; abrogated laws are not shown.
    # This cuts the dataset from ~190K → ~67K rows (65% memory reduction).
    return (
        "SELECT urn, title, type, date, year, status, article_count, "
        "text_length, importance_score FROM laws "
        "WHERE status = 'in_force' ORDER BY year DESC"
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
    """Global base styles — applies to all profiles. Citizen profile overrides via _CITIZEN_CSS."""
    st.markdown(
        """
        <style>
        h1, h2, h3 { letter-spacing: 0.01em; }
        p, li, label, .stCaption { line-height: 1.55; }
        .stMetric {
            background: #f8fafc;
            border-radius: 12px;
            padding: 0.6rem 0.8rem;
            border: 1px solid #e2e8f0;
        }
        [data-testid="stSidebar"] .stRadio > div { gap: 0.2rem; }
        .stButton > button { border-radius: 10px; font-weight: 600; }
        @media (max-width: 900px) {
            .block-container { padding-left: 0.9rem; padding-right: 0.9rem; }
            .stMetric { padding: 0.5rem 0.65rem; }
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
    st.markdown("VOOM corpus — **~67K vigenti** + **~123K abrogati** nel corpus totale. Full-text search, citations, legislative history.")
# IS_SEARCH (citizen) — hero is rendered inside _citizen_mvp, skip module-level title

IS_SEARCH = APP_PROFILE == "search"
IS_LAB = APP_PROFILE == "lab"
IS_ITALIAN_LAB = APP_PROFILE == "italianlab"
ACTIVE_DATASET_REPO = HF_DATASET_NAME or _default_dataset_repo(APP_PROFILE)

# Show active profile in the sidebar (lab profiles only — citizen has its own sidebar)
if IS_LAB or IS_ITALIAN_LAB:
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
    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────────────────────────
# GROQ RAG HELPER
# ─────────────────────────────────────────────────────────────────

GROQ_MODELS = {
    "expert-vigente":             "Expert Vigente — GPT OSS 120B (migliore qualità + velocità)",
    "auto-balanced":              "Auto bilanciato (economico → potente se necessario)",
    "openai/gpt-oss-120b":        "GPT OSS 120B (massima qualità, 500 t/s)",
    "llama-3.3-70b-versatile":    "Llama 3.3 70B (ottimo, 280 t/s)",
    "llama-3.1-8b-instant":       "Llama 3.1 8B Instant (veloce, domande semplici)",
}
GROQ_DEFAULT_MODEL = "expert-vigente"

_GROQ_SYSTEM_PROMPT = """\
Sei NormattivaAI — giurista italiano di livello accademico al servizio del cittadino.
Possiedi una profonda conoscenza enciclopedica del diritto italiano: costituzionale, civile, penale, amministrativo, tributario e del lavoro.

══════════════════════════════════════════════
METODOLOGIA — RAGIONA NELL'ORDINE
══════════════════════════════════════════════
1. COMPRENDI la domanda: qual è la situazione concreta? Chi è il soggetto? Quale diritto o obbligo è in gioco?
2. INQUADRA nel sistema delle fonti (dall'alto verso il basso):
   Costituzione → Leggi statali (TU, D.Lgs., L.) → Decreti (DPR, DPCM, D.M.) → Normativa regionale/locale → CCNL se pertinente
3. IDENTIFICA le norme chiave dalla tua conoscenza giuridica enciclopedica
4. VERIFICA nel CONTESTO DATASET: se la norma è presente, usa il suo testo letterale come prova diretta
5. SINTETIZZA in modo strutturato e comprensibile — con distinzioni tecniche quando rilevanti

══════════════════════════════════════════════
DUE FONTI — USALE ENTRAMBE
══════════════════════════════════════════════
📚 CONOSCENZA ENCICLOPEDICA (la tua formazione giuridica completa)
 → Usala per il framework, la gerarchia delle fonti, i principi generali, le distinzioni tecniche
 → Quando citi norme da questa fonte, aggiungi: *(enc.)*
 → Puoi citare articoli specifici (es. Art. 74 D.Lgs. 297/1994) anche se non nel dataset

📌 DATASET NORMATTIVA (norme vigenti verificate — nel CONTESTO qui sotto)
 → Se una norma è nel contesto, usa il suo testo letterale tra virgolette — è prova primaria
 → Aggiungi: *(✓ dataset)* per ogni norma confermata
 → Queste norme sono garantite VIGENTI nel dataset (67.000+ leggi)

══════════════════════════════════════════════
STRUTTURA OBBLIGATORIA
══════════════════════════════════════════════

### 📌 Risposta diretta
[1-2 frasi sintetiche. Sì / No / Dipende da... — la risposta netta alla domanda.]

### 🏛️ Quadro costituzionale
[L'articolo/i della Costituzione applicabile/i. Perché è rilevante? Come orienta l'interpretazione delle leggi ordinarie?]

### 📐 Gerarchia normativa applicabile
[Top-down — per ogni livello pertinente:
 - Nome e numero della norma + anno
 - Cosa stabilisce in merito alla domanda
 - URN se disponibile nel dataset (tra backtick)]

### 📜 Analisi delle norme specifiche
[Per ogni norma rilevante:
 **Nome norma** (Anno) `URN-se-disponibile` *(enc.)* oppure *(✓ dataset)*
 → **Contenuto**: "citazione letterale dal dataset" oppure sintesi precisa
 → **Applicazione**: cosa significa concretamente per chi fa la domanda
 → **Distinzioni tecniche**: eccezioni, condizioni, fattispecie diverse]

### ✅ Cosa fare concretamente
[Passi pratici: chi contattare, quali scadenze, quali documenti, a chi rivolgersi (INPS, CAF, avvocato, patronato, prefettura)]

### ⚠️ Variabili locali e nota legale
[Cosa può variare per regione/CCNL/contratto individuale. Quando serve un professionista. Questa analisi non costituisce consulenza legale personalizzata.]

══════════════════════════════════════════════
STANDARD DI QUALITÀ
══════════════════════════════════════════════
- Vai IN PROFONDITÀ: il cittadino merita un'analisi completa, non superficiale
- Fai distinzioni tecniche quando rilevanti (es. anno scolastico vs anno didattico; licenziamento per giusta causa vs giustificato motivo)
- Cita sempre: titolo + anno + URN tra backtick se dal dataset
- Per importi e scadenze: cita sempre l'anno della norma (aggiornabili da leggi di bilancio successive)
- Usa linguaggio tecnico ma comprensibile — come un buon avvocato che spiega a un cliente
- Se la domanda ha risvolti regionali: segnalalo esplicitamente
- Completa SEMPRE tutte le sezioni — non troncare il ragionamento

══════════════════════════════════════════════
NORME ESTRATTE DAL DATABASE NORMATTIVA — VIGENTI VERIFICATI:
══════════════════════════════════════════════
{context}
"""


def _build_groq_context(laws: list, max_chars_per_law: int = 2200) -> str:
    """Build a structured context string from retrieved vigente law records.
    Laws are sorted by importance_score descending so the most cited appear first.
    """
    try:
        cit_cache = _live_citation_counts()
    except Exception:
        cit_cache = {}

    # Sort by importance_score descending (most fundamental laws first)
    sorted_laws = sorted(
        laws,
        key=lambda r: float(r.get("importance_score") or 0),
        reverse=True,
    )

    parts = []
    for i, law in enumerate(sorted_laws, 1):
        if _normalize_status(law.get("status")) != "in_force":
            continue
        text = (law.get("text") or law.get("snippet") or "").strip()
        excerpt = text[:max_chars_per_law] + ("…" if len(text) > max_chars_per_law else "")
        urn = law.get("urn", "")
        cit_n = cit_cache.get(urn, 0) or int(law.get("citation_count_incoming") or 0)
        imp_score = float(law.get("importance_score") or 0)
        importance_str = ""
        if cit_n >= 10:
            importance_str = f" | Importanza: ALTA (citata da {cit_n} norme)"
        elif imp_score >= 0.7:
            importance_str = f" | Importanza: alta (score {imp_score:.2f})"
        elif cit_n > 0:
            importance_str = f" | Citata da: {cit_n} norme"
        parts.append(
            f"[NORMA {i} — {'✓ DATASET' if urn else 'enc.'}]\n"
            f"Titolo: {law.get('title', 'N/A')}\n"
            f"URN: {urn or 'N/D'}\n"
            f"Tipo: {law.get('type', 'N/A')} | Anno: {law.get('year', 'N/A')} | Stato: VIGENTE ✓{importance_str}\n"
            f"Testo:\n{excerpt}"
        )
    return "\n\n---\n\n".join(parts)


@st.cache_data(ttl=7200, show_spinner=False)
def _live_citation_counts():
    """
    Compute incoming-citation counts by matching citations.cited_urn
    (year-only format: YYYY;N) against laws.urn (full-date: YYYY-MM-DD;N).
    Cached for 2 h so it doesn't slow down every page load.
    """
    import re as _re_cit
    from collections import Counter
    db = load_db()
    if not db:
        return {}
    try:
        def _canon(urn):
            return _re_cit.sub(r':(\d{4})-\d{2}-\d{2};', r':\1;', urn or '')
        # Build canonical map from vigenti only (65% fewer URNs to process)
        canon_map = {}
        for (urn,) in db.conn.execute(
            "SELECT urn FROM laws WHERE urn IS NOT NULL AND status = 'in_force'"
        ):
            c = _canon(urn)
            if c and c not in canon_map:
                canon_map[c] = urn
        # Count incoming citations per full URN
        counts: Counter = Counter()
        for cited_urn, cnt in db.conn.execute(
            "SELECT cited_urn, count FROM citations WHERE cited_urn IS NOT NULL"
        ):
            full_urn = canon_map.get(cited_urn)
            if full_urn:
                counts[full_urn] += max(int(cnt or 0), 1)
        return dict(counts)
    except Exception:
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_db_counts(_db_path: str) -> dict:
    """Cache the per-status law counts so sidebar doesn't re-query every page render."""
    try:
        import sqlite3
        conn = sqlite3.connect(_db_path, check_same_thread=False)
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM laws GROUP BY status"
        ).fetchall()
        conn.close()
        counts = {r[0]: r[1] for r in rows}
        in_f  = counts.get("in_force", 0)
        ab    = counts.get("abrogated", 0)
        total = sum(counts.values())
        return {"in_force": in_f, "abrogated": ab, "total": total}
    except Exception:
        return {"in_force": 0, "abrogated": 0, "total": 0}


def _select_balanced_groq_model(question: str, context_laws: list) -> str:
    """Choose the best Groq model, escalating on legal complexity.

    Tiers:
    • Simple / short → llama-3.1-8b-instant  (560 t/s, fast)
    • Standard legal  → llama-3.3-70b-versatile  (280 t/s, strong reasoning)
    • Expert / multi-law → openai/gpt-oss-120b  (500 t/s, highest quality)
    """
    q = (question or "").lower()
    q_len = len(q)
    law_count = len(context_laws or [])

    expert_markers = [
        "costituzione", "giurisprudenza", "responsabilita", "contratto",
        "retroattivo", "ricorso", "tribunale", "cassazione", "appello",
        "risarcimento", "nullita", "invalidita", "impugnare",
    ]
    complex_markers = [
        "articolo", "art.", "comma", "decreto", "abrog", "prescrizion",
        "sanzion", "amminist", "procedura", "regolamento", "direttiva",
    ]

    expert_hits  = sum(1 for m in expert_markers  if m in q)
    complex_hits = sum(1 for m in complex_markers if m in q)

    # Expert tier: constitutional/litigation questions or very large context
    if expert_hits >= 1 or law_count >= 14 or (q_len > 250 and complex_hits >= 2):
        return "openai/gpt-oss-120b"
    # Standard legal questions
    if q_len > 120 or law_count >= 5 or complex_hits >= 1:
        return "llama-3.3-70b-versatile"
    # Simple / short questions
    return "llama-3.1-8b-instant"


def _extract_urns_from_text(text: str) -> set:
    import re
    if not text:
        return set()
    return set(re.findall(r"urn:nir:[a-zA-Z0-9.:;\-]+", text, flags=re.IGNORECASE))


def _has_legal_references(answer: str) -> bool:
    """Check if answer contains any meaningful legal references (URN, law number, article)."""
    if not answer:
        return False
    urns = _extract_urns_from_text(answer)
    if urns:
        return True
    # Also accept encyclopedic references: D.Lgs., Art., L., DPR, D.M., Testo Unico, etc.
    return bool(re.search(
        r'(D\.Lgs\.|D\.P\.R\.|D\.M\.|D\.L\.|Art\.|Legge\s+n\.|Testo\s+Unico|DPCM|Cost\.|'
        r'urn:|art\.\s*\d+|\d+/\d{4})',
        answer, flags=re.IGNORECASE
    ))


def _record_ai_telemetry(event: dict) -> None:
    """Store compact AI runtime diagnostics in session state for auditing."""
    try:
        logs = st.session_state.get("ai_telemetry")
        if not isinstance(logs, list):
            logs = []
        logs.append(event)
        st.session_state["ai_telemetry"] = logs[-100:]
    except Exception:
        pass


def _call_groq(
    question: str,
    context_laws: list,
    model: str = GROQ_DEFAULT_MODEL,
    max_tokens: int = 2500,
    temperature: float = 0.1,
    chat_history: list | None = None,
) -> tuple[str | None, str | None]:
    """
    Call Groq API with the retrieved law context (RAG pattern) and optional conversation history.
    Returns (answer_text, error_message). One of them will be None.
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return None, "GROQ_API_KEY non configurata. Imposta il secret `GROQ_API_KEY` nelle impostazioni dello Space."

    try:
        from groq import Groq
    except ImportError:
        return None, "Libreria `groq` non installata. Aggiungi `groq>=0.9.0` a requirements.txt."

    context = _build_groq_context(context_laws)
    system_prompt = _GROQ_SYSTEM_PROMPT.format(context=context)

    started = time.time()
    try:
        chosen_model = model
        if model == "auto-balanced":
            chosen_model = _select_balanced_groq_model(question, context_laws)
        elif model == "expert-vigente":
            chosen_model = "openai/gpt-oss-120b"

        # Larger output budget for 120B (supports 65K completion)
        effective_max_tokens = 3500 if "120b" in chosen_model.lower() else max_tokens

        st.session_state["last_groq_model_used"] = chosen_model

        # Build messages: system + optional conversation history + current question
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        for turn in (chat_history or []):
            if turn.get("q") and turn.get("a"):
                messages.append({"role": "user", "content": turn["q"]})
                messages.append({"role": "assistant", "content": turn["a"]})
        messages.append({"role": "user", "content": question})

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=chosen_model,
            messages=messages,
            max_tokens=effective_max_tokens,
            temperature=temperature,
            timeout=55,
        )
        answer = (response.choices[0].message.content or "").strip()

        has_refs = _has_legal_references(answer)
        _record_ai_telemetry({
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": chosen_model,
            "elapsed_s": round(time.time() - started, 3),
            "ok": bool(answer),
            "has_refs": has_refs,
            "error": None,
        })

        if not answer:
            return None, "Il modello non ha restituito una risposta. Prova a riformulare la domanda."
        return answer, None

    except Exception as e:
        _record_ai_telemetry({
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": st.session_state.get("last_groq_model_used", model),
            "elapsed_s": round(time.time() - started, 3),
            "ok": False,
            "has_refs": False,
            "error": str(e),
        })
        err_str = str(e)
        if "model_not_found" in err_str or "does not exist" in err_str.lower():
            # Graceful fallback if gpt-oss-120b is unavailable
            try:
                fb = Groq(api_key=api_key)
                fb_resp = fb.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=45,
                )
                answer = (fb_resp.choices[0].message.content or "").strip()
                st.session_state["last_groq_model_used"] = "llama-3.3-70b-versatile"
                return answer, None
            except Exception as e2:
                return None, f"Errore Groq API: {e2}"
        return None, f"Errore Groq API: {e}"


def _call_hf_model(
    question: str,
    context_laws: list,
    model: str = "mistralai/Mistral-7B-Instruct-v0.1",
    max_tokens: int = 1200,
    temperature: float = 0.1,
) -> tuple[str | None, str | None]:
    """
    Call HuggingFace Inference API with retrieved law context (simple RAG wrapper).
    Returns (answer_text, error_message).
    """
    token = (
        os.environ.get("HUGGINGFACEHUB_API_TOKEN")
        or os.environ.get("HF_API_KEY")
        or os.environ.get("HUGGINGFACE_TOKEN")
    )
    if not token:
        return None, "HUGGINGFACEHUB_API_TOKEN non configurato. Imposta il secret nello Space."

    try:
        import requests
    except Exception:
        return None, "Libreria `requests` non disponibile. Aggiungi `requests` a requirements.txt."

    context = _build_groq_context(context_laws)
    # Reuse the GROQ system prompt as a basis; it's designed to anchor answers to context.
    system_prompt = _GROQ_SYSTEM_PROMPT.format(context=context)
    payload = {
        "inputs": system_prompt + "\n\n" + question,
        "parameters": {
            "max_new_tokens": max_tokens,
            "temperature": temperature,
            "top_k": 50,
            "top_p": 0.95,
        },
        "options": {"wait_for_model": True},
    }
    url = f"https://api-inference.huggingface.co/models/{model}"
    started = time.time()
    try:
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        try:
            resp = r.json()
        except Exception as e:
            return None, f"HF response parse error: {e}"

        if isinstance(resp, dict) and resp.get("error"):
            return None, f"HF API error: {resp.get('error') }"

        # Response may be a list of dicts with 'generated_text' or a dict with 'generated_text'
        out_text = ""
        if isinstance(resp, list) and resp:
            out_text = resp[0].get("generated_text") or resp[0].get("text") or ""
        elif isinstance(resp, dict):
            out_text = resp.get("generated_text") or resp.get("text") or ""

        answer = out_text.strip() if out_text else None
        if not answer:
            return None, "Nessuna risposta dal modello HF."

        # Validate citations
        if not _has_strong_citations(answer, context_laws, min_count=2):
            _record_ai_telemetry({
                "ts": datetime.now(timezone.utc).isoformat(),
                "model": model,
                "elapsed_s": round(time.time() - started, 3),
                "ok": False,
                "citations_ok": False,
                "error": "VALIDATION_FAILED: risposta senza citazioni URN sufficienti",
            })
            return None, "VALIDATION_FAILED (HF): risposta senza citazioni URN sufficienti dal contesto"

        _record_ai_telemetry({
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "elapsed_s": round(time.time() - started, 3),
            "ok": True,
            "citations_ok": True,
            "error": None,
        })
        return answer, None
    except Exception as e:
        _record_ai_telemetry({
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "elapsed_s": round(time.time() - started, 3),
            "ok": False,
            "citations_ok": False,
            "error": str(e),
        })
        return None, f"Errore HF API: {e}"




def _call_rag(
    question: str,
    context_laws: list,
    model: str | None = None,
    max_tokens: int = 1200,
    temperature: float = 0.1,
) -> tuple[str | None, str | None]:
    """Unified RAG call: prefer Groq if configured, fallback to HuggingFace inference.
    Returns (answer, error)."""
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    hf_key = (
        os.environ.get("HUGGINGFACEHUB_API_TOKEN")
        or os.environ.get("HF_API_KEY")
        or os.environ.get("HUGGINGFACE_TOKEN")
    )

    # Try Groq first (preserves existing telemetry and model selection)
    if groq_key:
        try:
            chosen = model or GROQ_DEFAULT_MODEL
            ans, err = _call_groq(question, context_laws, model=chosen, max_tokens=max_tokens, temperature=temperature)
            if ans:
                return ans, None
            # if Groq explicitly failed validation, fall back if HF available
        except Exception:
            pass

    # Fallback to HuggingFace Inference
    if hf_key:
        chosen = model or "mistralai/Mistral-7B-Instruct-v0.1"
        ans, err = _call_hf_model(question, context_laws, model=chosen, max_tokens=max_tokens, temperature=temperature)
        if ans:
            # record chosen model for UI display parity
            st.session_state["last_groq_model_used"] = chosen
            return ans, None
        return None, err

    return None, "Nessun backend LLM disponibile: configura GROQ_API_KEY o HUGGINGFACEHUB_API_TOKEN."


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
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        year_counts = Counter(str(l.get("year", "?")) for l in laws if l.get("year"))
        yd = dict(sorted(year_counts.items()))
        fig = px.area(x=list(yd.keys()), y=list(yd.values()),
                      title="Laws by Year", labels={"x": "Year", "y": "Count"})
        st.plotly_chart(fig, use_container_width=True)

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
                st.dataframe(df, use_container_width=True, hide_index=True)
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

    # Use cached DB counts to avoid re-running COUNT on every page render
    try:
        db_path = str(db.db_path) if hasattr(db, "db_path") else ""
        if db_path:
            in_f = _cached_db_counts(db_path).get("in_force", in_f)
    except Exception:
        pass

    # Count laws mentioning major courts — cached via FTS search
    try:
        court_q = "corte costituzionale OR cassazione OR consiglio stato OR tribunale amministrativo"
        court_hits = len(db.search_fts(court_q, limit=10000))
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
                st.plotly_chart(fig, use_container_width=True)
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
                st.dataframe(quick_df, use_container_width=True, hide_index=True)

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
        st.plotly_chart(fig, use_container_width=True)
    with v2:
        yd = dict(sorted(y_counter.items()))
        fig = px.line(
            x=list(yd.keys()),
            y=list(yd.values()),
            title="Trend temporale",
            labels={"x": "Anno", "y": "Norme"},
        )
        st.plotly_chart(fig, use_container_width=True)

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
                st.dataframe(df, use_container_width=True, hide_index=True)


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
                st.dataframe(df, use_container_width=True, hide_index=True)
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
            st.plotly_chart(fig, use_container_width=True)

            sel_track = st.selectbox(
                "Explore track",
                ["vigente", "multivigente", "abrogato"],
                key="italian-lab-track-select",
            )
            view = df[df["track"] == sel_track].copy().sort_values("year", ascending=False)
            st.write(f"Showing {len(view):,} laws in track: {sel_track}")
            st.dataframe(
                view[["year", "type", "title", "status", "source_collection", "urn"]].head(200), use_container_width=True,
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
                        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
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
            st.dataframe(pd.DataFrame(snaps), use_container_width=True, hide_index=True)
        else:
            st.info("No snapshots captured yet.")

        transitions = _load_status_transitions(db, limit=2000)
        if transitions:
            df_t = pd.DataFrame(transitions)
            st.write(f"Recent transitions: {len(df_t):,}")
            st.dataframe(
                df_t[["detected_at", "year", "title", "from_status", "to_status", "from_track", "to_track", "urn"]], use_container_width=True,
                hide_index=True,
            )

            by_snap = (
                df_t.groupby(["snapshot_to"]).size().reset_index(name="transition_count").sort_values("snapshot_to")
            )
            fig = px.bar(by_snap, x="snapshot_to", y="transition_count", title="Transitions per snapshot")
            st.plotly_chart(fig, use_container_width=True)
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
                    st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)
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
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
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
        st.dataframe(df_cat, use_container_width=True, hide_index=True)

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
                ]), use_container_width=True, hide_index=True)
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
                        _cit = _live_citation_counts()
                        for r in related[:10]:
                            _render_law_card(r, db, key_prefix="detail-related-search",
                                             _cit_cache=_cit)
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
                        st.dataframe(df, use_container_width=True, hide_index=True)
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
                        _cit = _live_citation_counts()
                        for r in related[:10]:
                            _render_law_card(r, db, key_prefix="detail-related",
                                             _cit_cache=_cit)
                except Exception:
                    pass


def page_citations():
    """Enhanced citation network explorer."""
    st.header("🔗 Rete Citazioni")
    st.caption(
        "Analisi del grafo delle citazioni tra le norme del dataset. "
        "Identifica le leggi-pilastro che strutturano l'intero ordinamento."
    )
    db = load_db()
    if not db:
        st.info("Database richiesto.")
        return

    tab_top, tab_cross, tab_explore = st.tabs([
        "🌟 Più citate",
        "🔀 Citazioni cross-dominio",
        "🔍 Esplora norma",
    ])

    with tab_top:
        st.subheader("Top 30 norme più citate")
        try:
            top = db.conn.execute("""
                SELECT l.urn, l.title, l.year, l.type, l.status, l.source_collection,
                       m.citation_count_incoming, m.citation_count_outgoing, m.domain_cluster
                FROM laws l JOIN law_metadata m ON l.urn = m.urn
                WHERE m.citation_count_incoming > 0
                ORDER BY m.citation_count_incoming DESC LIMIT 30
            """).fetchall()
        except Exception as e:
            st.warning(f"Errore: {e}")
            top = []

        # Fallback to live citation counts if precomputed values are all 0
        if not top:
            cit_counts_live = _live_citation_counts()
            if cit_counts_live:
                try:
                    top_urns = sorted(cit_counts_live, key=lambda u: -cit_counts_live[u])[:30]
                    raw = db.conn.execute(f"""
                        SELECT l.urn, l.title, l.year, l.type, l.status, l.source_collection,
                               m.domain_cluster
                        FROM laws l LEFT JOIN law_metadata m ON l.urn = m.urn
                        WHERE l.urn IN ({",".join("?"*len(top_urns))})
                    """, top_urns).fetchall()
                    urn_to_row = {dict(r)["urn"]: dict(r) for r in raw}
                    top_live = []
                    for urn in top_urns:
                        r = urn_to_row.get(urn)
                        if r:
                            r["citation_count_incoming"] = cit_counts_live[urn]
                            r["citation_count_outgoing"] = 0
                            top_live.append(r)
                    top = top_live
                except Exception:
                    pass

        if top:
            df_top = pd.DataFrame([dict(r) for r in top])
            df_top["short"] = df_top["title"].str[:50]
            df_top["vigor"] = df_top["status"].apply(
                lambda s: "Vigente" if _normalize_status(s) == "in_force" else "Abrogata"
            )
            fig = px.bar(
                df_top, x="citation_count_incoming", y="short",
                orientation="h", color="vigor",
                color_discrete_map={"Vigente": "#0a7a5a", "Abrogata": "#c0392b"},
                title="Norme per citazioni in entrata",
                labels={"citation_count_incoming": "Citazioni", "short": ""},
            )
            fig.update_layout(yaxis={"autorange": "reversed"}, height=520)
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Schede norma")
            for i, r in enumerate(top[:10], 1):
                r = dict(r)
                cit_in  = int(r.get("citation_count_incoming") or 0)
                cit_out = int(r.get("citation_count_outgoing") or 0)
                urn     = r.get("urn") or ""
                norm_u  = f"https://www.normattiva.it/uri-res/N2Ls?{urn}" if urn else "#"
                tier_h  = _tier_badge(r.get("source_collection"))
                st.markdown(
                    f"<div style='border:1px solid #e2e8f0;border-radius:8px;"
                    f"padding:0.7rem 1rem;margin-bottom:0.5rem;background:#f8fafc;'>"
                    f"<strong>#{i} {(r.get('title') or '')[:75]}</strong>{tier_h}<br>"
                    f"<span style='font-size:0.77rem;color:#64748b;'>{r.get('type','')} {r.get('year','')} "
                    f"· Area: {r.get('domain_cluster') or '—'}</span><br>"
                    f"<span style='color:#0f766e;font-weight:700;'>📥 {cit_in:,} citazioni in entrata</span>"
                    f" &nbsp;·&nbsp; <span style='color:#0369a1;'>📤 {cit_out} cita altre</span><br>"
                    f"<a href='{norm_u}' target='_blank' style='font-size:0.73rem;color:#1d4ed8;'>{urn[:70]}</a>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("Dati citazioni non disponibili.")

    with tab_cross:
        st.subheader("Citazioni tra aree del diritto")
        st.caption(
            "ℹ️ Le citazioni sono collegate per corrispondenza tra domini tramite il formato canonico delle URN. "
            "I risultati coprono i collegamenti verificabili all'interno del dataset vigente."
        )
        try:
            # The citations.cited_urn uses year-only format (e.g. :1988;400)
            # while laws.urn uses full-date format (e.g. :1988-08-23;400).
            # Normalize cited_urn using SQLite REGEXP_REPLACE substitute:
            # strip the day/month part by matching the pattern in the JOIN.
            cross = db.conn.execute("""
                SELECT m1.domain_cluster as from_domain,
                       m2.domain_cluster as to_domain,
                       COUNT(*) as cnt
                FROM citations c
                JOIN law_metadata m1 ON c.citing_urn = m1.urn
                JOIN law_metadata m2 ON (
                    -- Try exact match first; then year-normalised match
                    c.cited_urn = m2.urn
                    OR REPLACE(c.cited_urn, SUBSTR(c.cited_urn,
                        INSTR(c.cited_urn, ':' || SUBSTR(c.cited_urn,
                            INSTR(c.cited_urn,':', INSTR(c.cited_urn,':')+1)+1,4)) + 5,
                        7), '') = m2.urn
                )
                WHERE m1.domain_cluster IS NOT NULL AND m1.domain_cluster != ''
                  AND m2.domain_cluster IS NOT NULL AND m2.domain_cluster != ''
                GROUP BY m1.domain_cluster, m2.domain_cluster
                ORDER BY cnt DESC LIMIT 30
            """).fetchall()
        except Exception:
            cross = []

        if not cross:
            # Fallback: build cross-domain from _live_citation_counts which already
            # normalized URNs. Group laws by domain then count cross-domain hits.
            try:
                cit_cache = _live_citation_counts()
                domain_map = {
                    row["urn"]: row["domain_cluster"]
                    for row in db.conn.execute(
                        "SELECT urn, domain_cluster FROM law_metadata WHERE domain_cluster != '' AND domain_cluster IS NOT NULL"
                    ).fetchall()
                }
                citing_rows = db.conn.execute(
                    "SELECT citing_urn, cited_urn FROM citations"
                ).fetchall()
                from collections import Counter
                cross_counter: Counter = Counter()
                for citing_urn, cited_urn in citing_rows:
                    d1 = domain_map.get(citing_urn)
                    d2 = domain_map.get(cited_urn)
                    if d1 and d2 and d1 != d2:
                        cross_counter[(d1, d2)] += 1
                if cross_counter:
                    cross = [
                        {"from_domain": k[0], "to_domain": k[1], "cnt": v}
                        for k, v in cross_counter.most_common(30)
                    ]
            except Exception:
                cross = []

        if cross:
            df_cross = pd.DataFrame(cross if isinstance(cross[0], dict) else [dict(r) for r in cross])
            fig2 = px.treemap(
                df_cross, path=["from_domain", "to_domain"], values="cnt",
                title="Come le aree del diritto si richiamano a vicenda",
            )
            st.plotly_chart(fig2, use_container_width=True)

            st.subheader("Tabella citazioni cross-dominio")
            df_cross.columns = ["Da", "A", "Citazioni"]
            st.dataframe(df_cross.head(20), use_container_width=True, hide_index=True)
        else:
            st.info(
                "Dati cross-dominio non disponibili nel formato corrente. "
                "Il formato URN nelle citazioni (anno-only) differisce dal formato nelle norme (data completa). "
                "Aggiornare la pipeline di inserimento citazioni per risolvere il problema strutturale."
            )

    with tab_explore:
        st.subheader("🔍 Esplora la rete di una norma specifica")
        urn_input = st.text_input(
            "Inserisci l'URN della norma (es. urn:nir:stato:legge:1988-08-23;400)",
            key="cit-urn-input",
            placeholder="urn:nir:stato:...",
        )
        if urn_input.strip():
            urn_clean = urn_input.strip()
            try:
                meta = db.conn.execute(
                    "SELECT citation_count_incoming, citation_count_outgoing, domain_cluster "
                    "FROM law_metadata WHERE urn = ?", (urn_clean,)
                ).fetchone()
                law_row = db.conn.execute(
                    "SELECT title, type, year, status FROM laws WHERE urn = ?", (urn_clean,)
                ).fetchone()
                cited_by = db.conn.execute(
                    "SELECT c.citing_urn, l.title, l.year, l.type "
                    "FROM citations c LEFT JOIN laws l ON c.citing_urn = l.urn "
                    "WHERE c.cited_urn = ? ORDER BY l.year DESC LIMIT 20", (urn_clean,)
                ).fetchall()
                cites = db.conn.execute(
                    "SELECT c.cited_urn, l.title, l.year, l.type "
                    "FROM citations c LEFT JOIN laws l ON c.cited_urn = l.urn "
                    "WHERE c.citing_urn = ? ORDER BY l.year DESC LIMIT 20", (urn_clean,)
                ).fetchall()
            except Exception as e:
                st.error(f"Errore: {e}")
                meta, law_row, cited_by, cites = None, None, [], []

            if law_row:
                st.success(f"**{law_row['title']}** ({law_row['type']} {law_row['year']}) — {_status_chip(law_row['status'])}")
                if meta:
                    m1, m2 = st.columns(2)
                    # Precomputed citation_count_incoming is always 0 (pipeline issue)
                    # Fall back to live citation count from the citations table
                    _cit_cache = _live_citation_counts()
                    live_incoming = _cit_cache.get(law_row.get("urn", ""), meta.get("citation_count_incoming") or 0)
                    m1.metric("📥 Citata da", f"{live_incoming:,} norme")
                    m2.metric("📤 Cita", f"{meta['citation_count_outgoing'] or 0:,} norme")
                c1, c2 = st.columns(2)
                with c1:
                    st.subheader(f"📥 Citata da ({len(cited_by)} norme mostrate)")
                    for r in cited_by:
                        r = dict(r)
                        st.markdown(f"- **{(r.get('title') or r.get('citing_urn'))[:60]}** `{r.get('year') or ''}`")
                with c2:
                    st.subheader(f"📤 Cita ({len(cites)} norme mostrate)")
                    for r in cites:
                        r = dict(r)
                        st.markdown(f"- **{(r.get('title') or r.get('cited_urn'))[:60]}** `{r.get('year') or ''}`")
            elif urn_input.strip():
                st.warning("URN non trovato nel database. Verifica il formato.")
                st.caption("Esempio: `urn:nir:stato:legge:1988-08-23;400`")


def page_domains():
    """Enhanced domain cluster browser with citation-ranked laws."""
    st.header("🏛️ Aree Giuridiche del Diritto Italiano")
    st.caption(
        "Ogni area del diritto raccoglie le norme classificate per materia. "
        "Le leggi sono ordinate per numero di citazioni in entrata — un indicatore di autorevolezza."
    )
    db = load_db()
    if not db:
        st.info("Database richiesto per questa sezione.")
        return

    try:
        domains = db.conn.execute("""
            SELECT m.domain_cluster,
                   COUNT(*) as cnt,
                   SUM(m.citation_count_incoming) as total_cit,
                   AVG(m.citation_count_incoming) as avg_cit
            FROM law_metadata m
            WHERE m.domain_cluster IS NOT NULL AND m.domain_cluster != ''
            GROUP BY m.domain_cluster
            ORDER BY cnt DESC
        """).fetchall()
    except Exception:
        st.info("Dati di dominio non disponibili.")
        return

    if not domains:
        st.info("Nessun dato disponibile.")
        return

    # Summary metrics
    domain_names  = [d["domain_cluster"] for d in domains]
    domain_counts = [d["cnt"] for d in domains]

    # Precomputed citation_count_incoming is all 0 due to pipeline issue.
    # Use live citation cache grouped by domain from law_metadata.
    _cit_cache = _live_citation_counts()
    try:
        urn_domain_rows = db.conn.execute(
            "SELECT urn, domain_cluster FROM law_metadata WHERE domain_cluster IS NOT NULL AND domain_cluster != ''"
        ).fetchall()
        domain_live_cit: dict = {}
        for row in urn_domain_rows:
            d = row["domain_cluster"]
            domain_live_cit[d] = domain_live_cit.get(d, 0) + _cit_cache.get(row["urn"], 0)
        domain_cits = [domain_live_cit.get(d, 0) for d in domain_names]
    except Exception:
        domain_cits = [int(d["total_cit"] or 0) for d in domains]

    col1, col2 = st.columns(2)
    with col1:
        fig = px.pie(
            names=domain_names, values=domain_counts,
            title="Distribuzione norme per area del diritto", hole=0.3,
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        st.plotly_chart(fig, use_container_width=True)
    with col2:
        fig2 = px.bar(
            x=domain_names, y=domain_cits,
            title="Citazioni totali per area",
            labels={"x": "Area", "y": "Citazioni totali"},
            color=domain_cits,
            color_continuous_scale="Blues",
        )
        st.plotly_chart(fig2, use_container_width=True)

    st.divider()
    st.subheader("🔍 Esplora un'area giuridica")
    selected_domain = st.selectbox("Seleziona area:", domain_names, key="dom-sel")

    if selected_domain:
        status_where = "AND l.status = 'in_force'"  # Always vigente only
        cit_counts_live = _live_citation_counts()
        try:
            laws_in_domain = db.conn.execute(f"""
                SELECT l.urn, l.title, l.year, l.type, l.status, l.source_collection,
                       COALESCE(m.citation_count_incoming, 0) as citation_count_incoming,
                       COALESCE(m.citation_count_outgoing, 0) as citation_count_outgoing
                FROM laws l JOIN law_metadata m ON l.urn = m.urn
                WHERE m.domain_cluster = ? {status_where}
                ORDER BY m.citation_count_incoming DESC NULLS LAST
                LIMIT 60
            """, (selected_domain,)).fetchall()
        except Exception as e:
            st.error(f"Errore: {e}")
            return

        # Re-sort by live citation counts if precomputed values are 0
        if laws_in_domain and cit_counts_live:
            laws_in_domain = sorted(
                [dict(r) for r in laws_in_domain],
                key=lambda r: -cit_counts_live.get(r["urn"], 0)
            )
            for r in laws_in_domain:
                r["citation_count_incoming"] = cit_counts_live.get(r["urn"], 0)

        if laws_in_domain:
            st.success(f"**{len(laws_in_domain)} norme** nell'area _{selected_domain}_ (ordinate per citazioni)")
            for i, r in enumerate(laws_in_domain[:30], 1):
                r = dict(r)
                cit_in  = int(r.get("citation_count_incoming") or 0)
                status  = _normalize_status(r.get("status"))
                sb      = "🟢" if status == "in_force" else "🔴"
                tier_h  = _tier_badge(r.get("source_collection"))
                urn     = r.get("urn") or ""
                norm_u  = f"https://www.normattiva.it/uri-res/N2Ls?{urn}" if urn else "#"
                cit_b   = (
                    f"<span style='background:#dcfce7;color:#166534;border-radius:3px;"
                    f"padding:1px 5px;font-size:0.7rem;font-weight:700;margin-left:4px;'>"
                    f"📥 {cit_in:,}</span>"
                ) if cit_in >= 5 else ""
                st.markdown(
                    f"<div style='border:1px solid #e2e8f0;border-radius:6px;"
                    f"padding:0.55rem 0.8rem;margin-bottom:0.35rem;background:#f8fafc;'>"
                    f"<strong style='font-size:0.88rem;'>#{i} {sb} {(r.get('title') or '')[:72]}</strong>"
                    f"{tier_h}{cit_b}<br>"
                    f"<span style='font-size:0.75rem;color:#64748b;'>{r.get('type','')} {r.get('year','')}</span>"
                    f" &nbsp;·&nbsp; "
                    f"<a href='{norm_u}' target='_blank' style='font-size:0.73rem;color:#1d4ed8;'>{urn[:60]}</a>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            if len(laws_in_domain) > 30:
                st.caption(f"Visualizzate 30 su {len(laws_in_domain)} norme. Raffina i filtri per vedere altre.")


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
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Citizen Context by Tax")
        context_rows = []
        for tax_name in sorted(tax_df["tax"].unique()):
            context_rows.append({
                "Tax": tax_name,
                "Citizen Context": TAX_CONTEXT.get(tax_name, "Contesto non classificato."),
            })
        st.dataframe(pd.DataFrame(context_rows), use_container_width=True, hide_index=True)

    with t2:
        st.write("Full registry of taxes detected across the corpus (with harmonized status labels).")

        agg = tax_df.groupby("tax").agg(
            laws=("urn", "nunique"),
            mentions=("tax", "count")
        ).reset_index().sort_values(["laws", "mentions"], ascending=False)
        st.dataframe(agg, use_container_width=True, hide_index=True)

        sel_tax = st.selectbox("Inspect tax", sorted(tax_df["tax"].unique()))
        sel_status = st.selectbox("Status filter", ["All", "in_force", "abrogated", "unknown"])

        view = tax_df[tax_df["tax"] == sel_tax]
        if sel_status != "All":
            view = view[view["status"] == sel_status]
        view = view.sort_values(["year", "title"], ascending=[False, True])

        st.write(f"{len(view):,} law rows for {sel_tax}.")
        st.dataframe(
            view[["year", "status", "title", "urn", "context", "amount_mentions"]], use_container_width=True,
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
            pd.DataFrame(rows), use_container_width=True, hide_index=True
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
            "Nessun aggiornamento registrato manualmente. "
            "Il database è incluso staticamente nello Space e riflette l'ultimo build della pipeline. "
            "Usa il modulo qui sotto per registrare aggiornamenti manuali o note."
        )
        # Show last indexed law as a proxy for last update
        try:
            latest = db.conn.execute(
                "SELECT MAX(parsed_at) FROM laws WHERE parsed_at IS NOT NULL"
            ).fetchone()[0]
            if latest:
                st.caption(f"🤖 Ultima indicizzazione automatica rilevata: `{latest[:19]}`")
        except Exception:
            pass

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


def _render_law_card(law: dict, db, key_prefix: str = "",
                     _cit_cache: dict | None = None):
    """Render a compact law card with nav button and citation count."""
    urn = law.get("urn", "")
    title = law.get("title", "N/A")
    year = law.get("year", "")
    law_type = law.get("type", "")

    # Use pre-computed citation dict when available to avoid per-card DB query
    if _cit_cache is not None:
        incoming = _cit_cache.get(urn, 0)
    else:
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
                _cit = _live_citation_counts()
                for row in cited_by:
                    _render_law_card(dict(row), db, key_prefix="const-cited", _cit_cache=_cit)
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
                st.plotly_chart(fig, use_container_width=True)
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
                        _cit = _live_citation_counts()
                        for r in rows:
                            _render_law_card(dict(r), db, key_prefix=f"impl-{year_from}",
                                             _cit_cache=_cit)
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
            top_k = st.slider("Norme da consultare (top-k)", 5, 30, 15, key="groq-topk")
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
                # ── Stopword filter ────────────────────────────────────────────
                _groq_sw = {
                    "il","lo","la","le","i","gli","un","una","uno","di","da","in",
                    "con","su","per","tra","fra","che","non","è","si","ha","ho","hai",
                    "del","della","dello","degli","delle","al","alla","allo","agli",
                    "alle","dal","dalla","dallo","dagli","dalle","nel","nella","nello",
                    "negli","nelle","sul","sulla","sullo","sugli","sulle","questo",
                    "questa","questi","queste","qual","quale","come","quando","dove",
                    "chi","cosa","posso","devo","voglio","sapere","avere","essere",
                    "sono","fare","mio","mia","miei","mie","suo","sua","suoi","sue",
                }
                raw_words = question.strip().split()
                meaningful = [w for w in raw_words if w.lower() not in _groq_sw and len(w) > 2]
                search_q = " ".join(meaningful) if meaningful else question.strip()

                # ── Phase 1: Primary FTS on the full cleaned query ─────────────
                primary = db.search_fts(search_q, limit=50)
                seen_urns = {r.get("urn") for r in primary}

                # ── Phase 2: Individual sub-term searches ──────────────────────
                # Each key concept searched separately to broaden coverage
                secondary = []
                for term in meaningful[:5]:
                    if len(term) > 3:
                        try:
                            sub = db.search_fts(term, limit=20)
                            for r in sub:
                                if r.get("urn") not in seen_urns:
                                    secondary.append(r)
                                    seen_urns.add(r.get("urn"))
                        except Exception:
                            pass

                # Secondary results ranked by importance_score (no FTS score)
                secondary.sort(
                    key=lambda r: float(r.get("importance_score") or 0), reverse=True
                )

                # ── Phase 3: Fallback on original question ─────────────────────
                all_results = primary + secondary
                if len(all_results) < 5:
                    try:
                        fb = db.search_fts(question.strip(), limit=30)
                        for r in fb:
                            if r.get("urn") not in seen_urns:
                                all_results.append(r)
                    except Exception:
                        pass

                # ── Filter vigenti ─────────────────────────────────────────────
                if only_vigenti:
                    all_results = [
                        r for r in all_results
                        if _normalize_status(r.get("status")) == "in_force"
                    ]
                evidence = all_results[:top_k]
            except Exception as e:
                st.error(f"Errore nella ricerca: {e}")
                evidence = []

        if not evidence:
            st.warning("Nessuna norma rilevante trovata nel dataset. Prova termini più specifici (es. 'licenziamento', 'affitto', 'scuola').")
        else:
            answer_text = None
            error_msg = None
            if has_groq:
                # Pass last 2 turns as conversation memory (only turns with a valid AI answer)
                history_turns = [
                    t for t in st.session_state.get("groq_chat", []) if t.get("a")
                ][:2]
                with st.spinner("🤖 Analizzando le norme con Groq AI…"):
                    answer_text, error_msg = _call_groq(
                        question=question.strip(),
                        context_laws=evidence,
                        model=model,
                        temperature=temperature,
                        chat_history=history_turns,
                    )
            chosen_model_display = st.session_state.get("last_groq_model_used", model)
            st.session_state["groq_chat"].insert(0, {
                "q": question.strip(),
                "a": answer_text,
                "err": error_msg,
                "evidence": evidence,
                "model_used": chosen_model_display,
            })

    # ── Chat history ─────────────────────────────────────────────
    if st.session_state["groq_chat"]:
        for idx, item in enumerate(st.session_state["groq_chat"]):
            is_latest = idx == 0
            with st.expander(f"{'🔵' if is_latest else '⚫'} Q: {item['q'][:100]}", expanded=is_latest):
                # AI answer
                if item.get("a"):
                    model_used = item.get("model_used", "")
                    model_label = GROQ_MODELS.get(model_used, model_used.split("/")[-1] if model_used else "AI")
                    st.caption(f"🤖 Risposta generata da: **{model_label}**")
                    st.markdown(item["a"])
                    st.caption(
                        "⚠️ Analisi basata su dataset Normattiva (67.000+ vigenti) + conoscenza giuridica enciclopedica. "
                        "Per decisioni legali rilevanti consulta sempre un avvocato o un CAF."
                    )
                    # Follow-up suggestion
                    if is_latest:
                        followup_q = f"Approfondisci: {item['q'][:60]}... — altri aspetti pratici"
                        if st.button("💬 Fai una domanda di approfondimento", key=f"followup-{idx}", use_container_width=False):
                            st.session_state["groq_prefill"] = followup_q
                            st.rerun()
                elif item.get("err"):
                    st.error(f"AI non disponibile: {item['err']}")
                    st.info("Le norme trovate nel dataset sono elencate di seguito — consultale direttamente.")

                # Evidence cards
                st.markdown("---")
                ev_list = item.get("evidence", [])
                st.markdown(f"### 📚 Norme nel dataset consultate ({len(ev_list)})")
                for ev_idx, ev in enumerate(ev_list):
                    status_chip = _status_chip(ev.get("status"))
                    imp = float(ev.get("importance_score") or 0)
                    imp_badge = " ⭐" if imp >= 0.7 else ""
                    with st.container(border=True):
                        c1, c3 = st.columns([6, 1])
                        with c1:
                            st.markdown(f"**{ev.get('title', 'N/A')}**{imp_badge}")
                            st.caption(f"`{ev.get('urn', 'N/A')}` | {ev.get('type', '')} | {ev.get('year', 'N/A')} | {status_chip}")
                            snippet = (ev.get("snippet") or ev.get("text") or "")[:350].strip()
                            if snippet:
                                st.caption(f"…{snippet}…")
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
                ["Solo vigenti", "Tutte"],
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
            df["stato"] = df["status"].apply(_status_label)
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
    import re as _re
    safe_key = _re.sub(r'[^a-z0-9]', '-', (law.get("urn") or "x").lower())[:80]
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
                import re as _re
                safe_key = _re.sub(r'[^a-z0-9]', '-', (law.get("urn") or "x").lower())[:80]
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
                    _mvp_law_card(law, f"mvp-c-res-{j}", g1 if j % 2 == 0 else g2, "mvp_c_open_urn")
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
                    import re as _re
                    safe = _re.sub(r'[^a-z0-9]', '-', (r.get("urn") or "x").lower())[:80]
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
                    _mvp_law_card(law, f"mvp-c-arch-{j}", g1 if j % 2 == 0 else g2, "mvp_c_open_urn")
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
                    "- 🔍 Cerco norme rilevanti nel dataset (67.000+ leggi vigenti)\n"
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

    user_input = st.chat_message("assistant") if False else None  # placeholder guard
    user_input = st.chat_input("Scrivi liberamente — cerca, chiedi, esplora…", key="mvp-d-input")
    if user_input:
        st.session_state["mvp_d_messages"].append({"role": "user", "content": user_input})
        st.session_state["mvp_d_pending"] = user_input
        st.rerun()


# ─────────────────────────────────────────────────────────────────
# CITIZEN MVP — clean single-experience interface
# ─────────────────────────────────────────────────────────────────

_CITIZEN_CSS = """<style>
/* ═══════════════════════════════════════════════════════════
   NormattivaVigente — Design System
   WCAG AA compliant color palette
   ═══════════════════════════════════════════════════════════ */

/* ── CSS Variables ────────────────────────────────────────── */
:root {
    --nv-blue:       #1d4ed8;   /* primary action */
    --nv-blue-dark:  #1e3a8a;   /* headings/hover */
    --nv-blue-light: #eff6ff;   /* subtle bg tint */
    --nv-blue-mid:   #bfdbfe;   /* border accents */
    --nv-green:      #15803d;   /* vigente — 7.2:1 on white */
    --nv-red:        #b91c1c;   /* abrogata — 7.0:1 on white */
    --nv-text:       #0f172a;   /* slate-900 — 19:1 on white */
    --nv-text-2:     #334155;   /* slate-700 — 9.4:1 on white */
    --nv-text-3:     #64748b;   /* slate-500 — 4.9:1 on white */
    --nv-bg:         #f8fafc;   /* page bg */
    --nv-card:       #ffffff;   /* card bg */
    --nv-border:     #e2e8f0;   /* card border */
    --nv-shadow:     0 1px 4px rgba(0,0,0,0.08);
}

/* ── Global layout ─────────────────────────────────────────── */
[data-testid="stAppViewContainer"] > .main {
    background: var(--nv-bg) !important;
}
.block-container {
    padding: 0 1.8rem 4rem !important;
    max-width: 920px !important;
}

/* ── Sidebar ───────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: #1e3a8a !important;
    border-right: none !important;
}
[data-testid="stSidebar"] * {
    color: #e0eaff !important;
}
[data-testid="stSidebar"] .stCheckbox label {
    color: #e0eaff !important;
    font-weight: 600;
}
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #ffffff !important;
    font-size: 1rem !important;
}

/* ── Tabs ──────────────────────────────────────────────────── */
[data-testid="stTabs"] [role="tablist"] {
    border-bottom: 2px solid var(--nv-border);
    gap: 0.2rem;
}
[data-testid="stTabs"] [role="tab"] {
    color: var(--nv-text-2) !important;
    font-weight: 600;
    font-size: 0.92rem;
    border-radius: 8px 8px 0 0;
    padding: 0.5rem 1rem;
    border: none;
    transition: background 0.15s, color 0.15s;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    color: var(--nv-blue) !important;
    border-bottom: 3px solid var(--nv-blue) !important;
    background: var(--nv-blue-light) !important;
}
[data-testid="stTabs"] [role="tab"]:hover {
    background: var(--nv-blue-light) !important;
    color: var(--nv-blue-dark) !important;
}

/* ── Hero banner ────────────────────────────────────────────── */
.nv-hero {
    background: linear-gradient(135deg, #1e3a8a 0%, #1d4ed8 55%, #3b82f6 100%);
    border-radius: 16px;
    padding: 2rem 2.5rem 1.8rem;
    margin-bottom: 1.4rem;
    text-align: center;
    box-shadow: 0 6px 28px rgba(29,78,216,0.28);
}
.nv-hero h1 {
    color: #ffffff !important;
    font-size: 2rem !important;
    font-weight: 800 !important;
    margin: 0 0 0.5rem !important;
    letter-spacing: -0.3px;
    text-shadow: 0 2px 4px rgba(0,0,0,0.35);
}
.nv-hero p {
    color: #dbeafe !important;    /* blue-100 — 7.5:1 on hero bg */
    font-size: 1.05rem !important;
    margin: 0 0 0.4rem !important;
}
.nv-hero small {
    color: #bfdbfe !important;    /* blue-200 — 5.5:1 on hero bg */
    font-size: 0.88rem !important;
}

/* ── Law card (expander-based) ──────────────────────────────── */
[data-testid="stExpander"] {
    background: var(--nv-card) !important;
    border: 1px solid var(--nv-border) !important;
    border-radius: 10px !important;
    margin-bottom: 0.55rem;
    box-shadow: var(--nv-shadow);
}
[data-testid="stExpander"] summary {
    color: var(--nv-text) !important;
    font-size: 0.91rem !important;
    font-weight: 600 !important;
    padding: 0.6rem 0.8rem !important;
}
[data-testid="stExpander"] summary:hover {
    background: var(--nv-blue-light) !important;
    border-radius: 10px;
}
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
    padding: 0.2rem 0.9rem 0.8rem !important;
}

/* ── Inline law (inside expanders — no nesting) ─────────────── */
.nv-inline-law {
    border-left: 3px solid var(--nv-blue-mid);
    padding: 0.5rem 0.75rem;
    margin-bottom: 0.55rem;
    background: var(--nv-blue-light);
    border-radius: 0 8px 8px 0;
}
.nv-inline-law strong {
    color: var(--nv-text) !important;
    font-size: 0.89rem;
    display: block;
    margin-bottom: 0.15rem;
}
.nv-inline-law code {
    font-size: 0.76rem;
    color: var(--nv-text-2) !important;
    background: rgba(0,0,0,0.04);
    padding: 0 3px;
    border-radius: 3px;
}

/* ── Buttons ────────────────────────────────────────────────── */
.stButton > button {
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
    border: 1.5px solid var(--nv-blue) !important;
    color: var(--nv-blue) !important;
    background: transparent !important;
    padding: 0.3rem 0.9rem !important;
    transition: all 0.15s !important;
}
.stButton > button:hover {
    background: var(--nv-blue) !important;
    color: #ffffff !important;
}
/* Primary call-to-action button */
.stButton[data-testid*="go"] > button,
.stButton > button[kind="primary"] {
    background: var(--nv-blue) !important;
    color: #ffffff !important;
}

/* ── Chat ───────────────────────────────────────────────────── */
[data-testid="stChatInput"] textarea {
    font-size: 0.96rem !important;
    color: var(--nv-text) !important;
    border: 2px solid var(--nv-border) !important;
    border-radius: 12px !important;
    background: var(--nv-card) !important;
}
[data-testid="stChatInput"] textarea:focus {
    border-color: var(--nv-blue) !important;
    box-shadow: 0 0 0 3px rgba(29,78,216,0.12) !important;
}
[data-testid="stChatMessage"] {
    background: var(--nv-card) !important;
    border-radius: 12px !important;
    margin-bottom: 0.6rem;
    border: 1px solid var(--nv-border) !important;
}
[data-testid="stChatMessageContent"] {
    font-size: 0.95rem !important;
    line-height: 1.75 !important;
    color: var(--nv-text) !important;
}

/* ── Metrics ────────────────────────────────────────────────── */
[data-testid="stMetric"] {
    background: var(--nv-card) !important;
    border: 1px solid var(--nv-border) !important;
    border-radius: 12px !important;
    padding: 0.75rem 1rem !important;
    box-shadow: var(--nv-shadow);
}
[data-testid="stMetricLabel"] { color: var(--nv-text-2) !important; font-size: 0.83rem !important; }
[data-testid="stMetricValue"] { color: var(--nv-text) !important; font-weight: 800 !important; }
[data-testid="stMetricDelta"] { font-size: 0.82rem !important; }

/* ── Status badges ──────────────────────────────────────────── */
.nv-vigente  { color: var(--nv-green) !important; font-weight: 700; }
.nv-abrogata { color: var(--nv-red)   !important; font-weight: 700; }

/* ── Text inputs & selects ──────────────────────────────────── */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stSelectbox"] select {
    border: 1.5px solid var(--nv-border) !important;
    border-radius: 8px !important;
    color: var(--nv-text) !important;
    font-size: 0.94rem !important;
    background: var(--nv-card) !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stNumberInput"] input:focus {
    border-color: var(--nv-blue) !important;
    box-shadow: 0 0 0 3px rgba(29,78,216,0.1) !important;
    outline: none !important;
}
.stTextInput label, .stSelectbox label,
.stNumberInput label, .stCheckbox label {
    color: var(--nv-text-2) !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
}

/* ── Captions & helper text ─────────────────────────────────── */
.stCaption, [data-testid="stCaptionContainer"] {
    color: var(--nv-text-3) !important;
    font-size: 0.83rem !important;
}

/* ── Info / Warning / Error boxes ───────────────────────────── */
[data-testid="stInfo"] {
    background: var(--nv-blue-light) !important;
    border-left: 4px solid var(--nv-blue) !important;
    color: var(--nv-text) !important;
    border-radius: 0 8px 8px 0 !important;
}
[data-testid="stWarning"] {
    border-radius: 0 8px 8px 0 !important;
}

/* ── Bar chart / plotly container ───────────────────────────── */
[data-testid="stArrowVegaLiteChart"],
[data-testid="stPlotlyChart"] {
    background: var(--nv-card) !important;
    border: 1px solid var(--nv-border) !important;
    border-radius: 12px !important;
    padding: 0.5rem !important;
}

/* ── Mobile ─────────────────────────────────────────────────── */
@media (max-width: 768px) {
    [data-testid="stSidebar"] { display: none !important; }
    .block-container { padding: 0 0.6rem 5rem !important; }
    .nv-hero { padding: 1.4rem 1.2rem 1.2rem !important; border-radius: 12px !important; }
    .nv-hero h1 { font-size: 1.4rem !important; }
    .nv-hero p  { font-size: 0.9rem !important; }
    [data-testid="stTabs"] [role="tab"] { font-size: 0.78rem !important; padding: 0.4rem 0.6rem !important; }
    .stButton > button { width: 100% !important; }
}
</style>"""


def _card_chat(law, key_suffix, col=None):
    """Render a single law card in chat/EU context. Module-level so it's accessible everywhere."""
    urn = law.get("urn") or ""
    title = (law.get("title") or "N/A")[:72]
    status = _normalize_status(law.get("status"))
    if status != "in_force":
        return
    typ = law.get("type") or ""
    year = law.get("year") or ""
    safe = re.sub(r"[^a-z0-9]", "-", urn.lower())[:80]
    target = col if col is not None else st

    norm_url = f"https://www.normattiva.it/uri-res/N2Ls?{urn}" if urn else "#"
    urn_html = (
        f"<a href='{norm_url}' target='_blank' rel='noopener' "
        f"style='color:#1d4ed8;font-size:0.74rem;text-decoration:underline;"
        f"word-break:break-all;'>{urn[:80]}</a>"
    ) if urn else f"<code style='font-size:0.74rem;'>{urn[:80]}</code>"

    ai_ctx = (law.get("ai_context") or law.get("relevance") or "").strip()
    ai_html = ""
    if ai_ctx:
        safe_ctx = (ai_ctx
                    .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    .replace('"', "&quot;"))
        ai_html = (
            f"<div style='margin-top:0.45rem;padding:0.35rem 0.5rem;"
            f"background:#eff6ff;border-radius:6px;border-left:3px solid #1d4ed8;'>"
            f"<span style='font-size:0.74rem;font-weight:700;color:#1e3a8a;"
            f"text-transform:uppercase;letter-spacing:0.04em;'>💬 Perché è rilevante</span><br>"
            f"<span style='font-size:0.83rem;color:#1e293b;line-height:1.5;"
            f"display:block;margin-top:0.2rem;'>{safe_ctx[:320]}</span>"
            f"</div>"
        )

    exc = (law.get("text_excerpt") or "").strip()
    exc_html = ""
    if exc:
        safe_exc = (exc
                    .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    .replace('"', "&quot;"))
        exc_html = (
            f"<div style='margin-top:0.4rem;padding:0.35rem 0.5rem;"
            f"background:#f8fafc;border-radius:6px;border-left:3px solid #94a3b8;'>"
            f"<span style='font-size:0.74rem;font-weight:700;color:#475569;"
            f"text-transform:uppercase;letter-spacing:0.04em;'>📄 Dal testo della norma</span><br>"
            f"<span style='font-size:0.81rem;color:#334155;font-style:italic;"
            f"line-height:1.5;display:block;margin-top:0.2rem;'>{safe_exc[:280]}</span>"
            f"</div>"
        )

    sc = law.get("source_collection") or ""
    tier_html = _tier_badge(sc) if sc else ""
    cit_in = int(law.get("citation_count_incoming") or 0)
    cit_badge = (
        f"<span style='font-size:0.67rem;background:#dcfce7;color:#166534;"
        f"border-radius:3px;padding:1px 5px;margin-left:4px;font-weight:700;'>"
        f"📥 {cit_in:,} cit.</span>"
    ) if cit_in >= 10 else ""
    target.markdown(
        f"<div class='nv-inline-law' style='padding:0.65rem 0.8rem;'>"
        f"<strong style='font-size:0.91rem;'>🟢 {title}</strong>{tier_html}{cit_badge}<br>"
        f"<span style='font-size:0.78rem;color:#64748b;'>{typ} {year}</span> &nbsp;·&nbsp; "
        f"{urn_html}"
        f"{ai_html}"
        f"{exc_html}"
        f"</div>",
        unsafe_allow_html=True,
    )
    if target.button(
        "\U0001f4d6 Apri testo completo",
        key=f"open-{safe}-{key_suffix}",
        use_container_width=True,
    ):
        st.session_state["citizen_open_urn"] = urn
        st.rerun()


def _citizen_mvp(db):
    import re as _re

    # ── Italian stop-words stripped before FTS (avoids AND-match failure) ──
    _STOPWORDS = {
        "a","ad","al","alla","alle","agli","allo","anche","ancora","anzi","appena",
        "che","chi","ci","col","come","con","cosa","così","cui","da","dal","dalla",
        "dalle","dagli","dallo","degli","dei","del","dell","della","delle","dello",
        "di","dì","dove","dunque","e","ed","era","essere","è","già","gli","ho","i",
        "il","in","invece","io","l","la","le","loro","lui","ma","mi","nel","nella",
        "nelle","negli","nello","no","non","o","ogni","oppure","ora","però","per",
        "poi","può","quale","quando","quanto","quasi","questo","qui","se","si","sia",
        "solo","sono","su","sua","suoi","sul","sulla","sulle","sugli","sullo","te",
        "ti","tra","tu","tutti","un","una","uno","vi","voi","dura","fare","avere",
        "questo","questa","questi","queste","quale","quali","sarà","verrà","essere",
        # Common question/action verbs that pollute FTS with irrelevant results
        "funziona","calcola","calcolare","dice","dicono","vinto","fai","puoi","devi",
        "vuoi","sanno","sai","sapete","capire","capisce","spiegami","spiega","dimmi",
        "serve","occorre","bisogna","basta","resta","rimane","cambia","succede",
        "avviene","permettono","consente","prevede","stabilisce","afferma","chiede",
        "richiede","vuol","devo","deve","dobbiamo","devono","posso","possono","potete",
        "andare","venire","uscire","entrare","farlo","farla","farne","all","dal","nell",
        "tutto","tutta","molti","molte","alcuni","alcune","altro","altra","altri","altre",
        "stesso","stessa","proprio","propria","propri","tale","tali","simile","simili",
        "hai","abbiamo","avete","hanno","avevo","aveva","ero","eri","eravamo","erano",
        "oggi","ieri","domani","adesso","presto","tardi","subito","molto","poco","tanto",
        "troppo","abbastanza","sempre","mai","forse","magari","certamente","ovviamente",
        "vinto","capito","trovato","fatto","detto","letto","scritto","messo","dati",
    }

    def _keywords(text: str) -> str:
        """Extract meaningful content words from a natural-language Italian query."""
        words = _re.sub(r"[^\w\s]", " ", text, flags=_re.UNICODE).split()
        return " ".join(w for w in words if w.lower() not in _STOPWORDS and len(w) > 2)[:120]

    def _smart_search(query: str, limit: int = 60) -> list:
        """
        3-pass retrieval:
          1. FTS on extracted keywords (strips stop-words so AND-match works)
          2. Per-keyword FTS union (catches partial matches)
          3. LIKE fallback on title + text
        """
        if not db:
            return []
        kw = _keywords(query)
        seen: set = set()
        combined: list = []

        # Pass 1 — AND FTS on all keywords (high precision)
        if kw:
            try:
                for r in db.search_fts(kw, limit=limit):
                    u = r.get("urn", "")
                    if u not in seen:
                        seen.add(u)
                        combined.append(r)
            except Exception:
                pass

        # Pass 2 — per-word FTS union (broader recall; always run to diversify candidates)
        words = (kw.split() if kw else [])[:6]
        if len(words) <= 3 or len(combined) < 10:
            # Only run per-word if short query OR Pass 1 gave too few results
            for w in words:
                try:
                    for r in db.search_fts(w, limit=25):
                        u = r.get("urn", "")
                        if u not in seen:
                            seen.add(u)
                            combined.append(r)
                except Exception:
                    pass

        if combined:
            # Only return vigente (in_force) laws
            vigente = [r for r in combined if _normalize_status(r.get("status")) == "in_force"]
            return vigente[:limit] if vigente else combined[:limit]
        # Pass 3 — LIKE fallback across multiple terms (vigente only)
        try:
            terms = (kw.split() or _re.sub(r"[^\w\s]", " ", query).split())[:4]
            terms = [t for t in terms if len(t) > 2]
            if not terms:
                return []
            like_clauses = " OR ".join(["title LIKE ? OR text LIKE ?" for _ in terms])
            params = []
            for t in terms:
                p = f"%{t}%"
                params.extend([p, p])
            params.append(limit)
            rows = db.conn.execute(
                "SELECT urn, title, type, year, date, status, '' AS snippet "
                f"FROM laws WHERE status='in_force' AND ({like_clauses}) LIMIT ?",
                tuple(params),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    _QUERY_EXPANSIONS = {
        "congedo": "congedo parentale maternita paternita testo unico 151 2001",
        "maternita": "tutela maternita paternita congedo testo unico 151 2001",
        "maternità": "tutela maternita paternita congedo testo unico 151 2001",
        "paternita": "congedo paternita maternita testo unico 151 2001",
        "paternità": "congedo paternita maternita testo unico 151 2001",
        "licenziamento": "licenziamento individuale giusta causa statuto lavoratori 300 1970",
        "affitto": "locazione immobili urbani disciplina locazioni sfratto 392 1978",
        "locazione": "disciplina locazioni immobili urbani affitto 392 1978",
        "sfratto": "sfratto locazione procedura affitto morosita 392 1978 431 1998",
        "morosità": "sfratto locazione morosita procedura 392 1978",
        "morosita": "sfratto locazione morosita procedura 392 1978",
        "pensione": "pensione previdenza INPS regime pensionistico",
        "pensioni": "pensione previdenza INPS regime pensionistico riforma",
        "iva": "imposta valore aggiunto dpr 633 1972",
        "irpef": "imposta reddito persone fisiche testo unico imposte redditi tuir 917 1986",
        "ires": "imposta reddito societa testo unico imposte redditi tuir 917 1986",
        "imu": "imposta municipale propria tributi locali decreto legislativo 23 2011",
        "inps": "previdenza sociale pensione contributi inps legge",
        "privacy": "protezione dati personali gdpr decreto legislativo 196 2003",
        "gdpr": "protezione dati personali decreto legislativo 196 2003 679 2016",
        "salute": "servizio sanitario nazionale tutela salute legge 833 1978",
        "lavoro": "statuto lavoratori diritto lavoro 300 1970",
        "lavoratori": "statuto lavoratori tutela lavoro dipendente 300 1970",
        "codice": "codice civile 262 1942",
        "penale": "codice penale reato 1398 1930",
        "eredita": "successione eredita codice civile testamento 262 1942",
        "eredità": "successione eredita codice civile testamento 262 1942",
        "divorzio": "divorzio separazione coniugi legge 898 1970",
        "separazione": "separazione coniugale divorzio matrimonio legge 898 1970",
        "sicurezza": "sicurezza lavoro decreto legislativo 81 2008",
        "studente": "istruzione scuola decreto ministeriale programma scolastico",
        "scuola": "istruzione scolastica legge norme programma studio",
        "università": "università ateneo istruzione superiore legge",
        "universita": "università ateneo istruzione superiore legge",
        "tasse": "imposta reddito irpef dpr 917 1986",
        "immigrazione": "immigrazione stranieri ingresso soggiorno decreto legislativo 286 1998",
        "stranieri": "immigrazione stranieri ingresso soggiorno decreto legislativo 286 1998",
        "reato": "codice penale reato pena reclusione 1398 1930",
        "contratto": "contratto obbligazioni codice civile 262 1942",
        "appalto": "appalto contratti pubblici codice 36 2023 50 2016",
        "fallimento": "fallimento insolvenza crisi impresa codice 14 2019 267 1942",
    }

    def _retrieve_context(question: str, limit: int = 12) -> list:
        """Retrieve context using query variants and prioritize vigente laws."""
        if not db:
            return []

        base = (question or "").strip()
        kw = _keywords(base)
        variants = []
        if base:
            variants.append(base)
        if kw and kw != base:
            variants.append(kw)

        for tok in kw.split()[:4]:
            exp = _QUERY_EXPANSIONS.get(tok.lower())
            if exp:
                variants.append(exp)

        # Remove the noisy constitutional lens variant — it pulls in irrelevant results
        # for most civic queries; only add it if the query explicitly mentions constitution
        _q_lower = (kw or base).lower()
        if any(w in _q_lower for w in ("costituzione","costituzionale","fondamentale","art","articolo")):
            variants.append(f"costituzione repubblica italiana {kw or base}".strip())

        seen = set()
        combined = []
        for qv in variants:
            for r in _smart_search(qv, limit=40):
                urn = r.get("urn", "")
                if urn and urn not in seen:
                    seen.add(urn)
                    combined.append(r)
            if len(combined) >= 100:
                break

        if not combined:
            return []

        q_tokens = set((kw or base).lower().split())

        def _score(r: dict) -> float:
            title = str(r.get("title") or "").lower()
            # Use only snippet (not full text) to avoid matching unrelated text deep in law body
            text = str(r.get("snippet") or "").lower()[:2000]
            overlap = sum(1 for t in q_tokens if t and (t in title or t in text))
            title_match = sum(1 for t in q_tokens if t and t in title)
            is_vigente = 1.0 if _normalize_status(r.get("status")) == "in_force" else 0.0
            # Bigger/more comprehensive laws should rank higher
            article_count = int(r.get("article_count") or 0)
            text_len = int(r.get("text_length") or 0)
            article_score = min(article_count / 80.0, 1.5)   # cap at 120 articles
            text_score    = min(text_len / 150_000.0, 1.0)   # cap at 150K chars
            # Bonus for testi unici (comprehensive codes / consolidated texts)
            is_testo_unico = 2.0 if "testo unico" in title else 0.0
            # Strong penalty for EU directive transpositions unless query is EU-related
            _is_eu_topic = any(w in _q_lower for w in ("direttiv","europe","ue ","union","recep","europea"))
            is_eu_xp = -3.0 if (not _is_eu_topic and "direttiva" in title and ("recepimento" in title or "attuazione" in title)) else 0.0
            # BM25 relevance from FTS (normalized)
            bm25 = float(r.get("relevance_score") or 0) / 10.0
            return (
                title_match * 12.0           # Title match is the primary relevance signal
                + overlap * 3.0              # Text/snippet match bonus
                + is_vigente * 3.0           # Vigente bonus (reduced: relevance > status)
                + article_score * 2.0
                + text_score
                + is_testo_unico
                + is_eu_xp
                + bm25
            )

        ranked = sorted(combined, key=_score, reverse=True)
        # Keep only vigente (in_force) laws — this is the scope of the citizen assistant
        ranked = [r for r in ranked if _normalize_status(r.get("status")) == "in_force"]

        # Out-of-scope detection: if substantial keywords (>4 chars) don't appear in
        # titles or snippets of top results, query is likely not about Italian law.
        if ranked and len(q_tokens) >= 2:
            substantial = [t for t in q_tokens if len(t) > 4]
            if substantial:
                max_title_match = max(
                    sum(1 for t in substantial if t in str(r.get("title") or "").lower())
                    for r in ranked[:5]
                )
                max_snip_match = max(
                    sum(1 for t in substantial if t in str(r.get("snippet") or "").lower()[:500])
                    for r in ranked[:5]
                )
                if max_title_match == 0 and max_snip_match == 0:
                    return []  # Off-topic query: no relevant Italian law found

        top_urns = [r.get("urn") for r in ranked[:limit] if r.get("urn")]
        if top_urns and db:
            try:
                placeholders = ",".join("?" * len(top_urns))
                meta_rows = db.conn.execute(
                    f"SELECT urn, citation_count_incoming, domain_cluster, source_collection "
                    f"FROM law_metadata WHERE urn IN ({placeholders})",
                    top_urns,
                ).fetchall()
                meta_map = {r[0]: dict(r) for r in meta_rows}
                for law in ranked[:limit]:
                    u = law.get("urn") or ""
                    if u in meta_map:
                        m = meta_map[u]
                        law.setdefault("citation_count_incoming", m.get("citation_count_incoming") or 0)
                        law.setdefault("domain_cluster", m.get("domain_cluster") or "")
                        law.setdefault("source_collection", m.get("source_collection") or "")
            except Exception:
                pass
        return ranked[:limit]

    def _law_proof_excerpt(law: dict, max_chars: int = 220) -> str:
        text = (law.get("snippet") or law.get("text") or "").strip()
        if not text:
            return "Estratto non disponibile nel dataset."
        text = _re.sub(r"\s+", " ", text)
        return text[:max_chars] + ("…" if len(text) > max_chars else "")

    st.markdown(_CITIZEN_CSS, unsafe_allow_html=True)
    has_groq = bool(os.environ.get("GROQ_API_KEY", "").strip())

    # ── Sidebar navigation ────────────────────────────────────────
    citizen_view = st.sidebar.radio(
        "📌 Sezione",
        ["🤖 Assistente AI"],
        key="citizen-view",
    )

    # ── Law detail overlay (full-screen, blocks chat) ─────────────
    open_urn = st.session_state.get("citizen_open_urn")
    if open_urn and db:
        try:
            r = db.conn.execute("SELECT * FROM laws WHERE urn=? LIMIT 1", (open_urn,)).fetchone()
            law = dict(r) if r else None
        except Exception:
            law = None
        if law:
            status = _normalize_status(law.get("status"))
            badge = "\U0001f7e2 Vigente" if status == "in_force" else "\U0001f534 Abrogata"
            st.markdown(f"### {law.get('title') or 'N/A'}")
            st.caption(f"{badge} \u00b7 {law.get('type','')} \u00b7 {law.get('year','')} \u00b7 `{open_urn}`")
            text = law.get("text") or ""
            if text:
                st.text_area(
                    "Testo integrale",
                    text[:9000] + ("\u2026" if len(text) > 9000 else ""),
                    height=420, disabled=True,
                    key=f"dettext-{open_urn[:24]}",
                )
            else:
                st.info("Testo non disponibile nel dataset per questa norma.")
            law_q = st.text_input(
                "Chiedi all\u2019AI su questa norma",
                key="law-detail-q",
                placeholder="Es.: Cosa prevede l\u2019articolo principale?",
            )
            if law_q.strip() and st.button("Chiedi \u2192", key="law-detail-ask"):
                if has_groq:
                    with st.spinner("AI in elaborazione\u2026"):
                        ans, err = _call_groq(
                            f"[Norma aperta: {law.get('title','')}] {law_q}",
                            [law], model=GROQ_DEFAULT_MODEL, max_tokens=700,
                        )
                    st.info(ans or f"\u26a0\ufe0f {err}")
                else:
                    st.info(f"\U0001f4d6 Estratto: {_law_proof_excerpt(law)}")
            col_link, col_close = st.columns([3, 1])
            col_link.link_button(
                "\U0001f4c4 Apri su Normattiva.it \u2197",
                f"https://www.normattiva.it/uri-res/N2Ls?{open_urn}",
            )
            if col_close.button("\u2190 Chiudi", key="citizen-close-detail"):
                st.session_state.pop("citizen_open_urn", None)
                st.rerun()
        else:
            st.warning("Norma non trovata nel database.")
            if st.button("\u2190 Indietro", key="citizen-back"):
                st.session_state.pop("citizen_open_urn", None)
                st.rerun()
        return

    # ── Hero ───────────────────────────────────────────────────────
    latest_db_date = "N/A"
    total_db_laws = 0
    if db:
        try:
            latest_db_date = db.conn.execute(
                "SELECT MAX(date) FROM laws WHERE date != ''"
            ).fetchone()[0] or "N/A"
            total_db_laws = db.conn.execute(
                "SELECT COUNT(*) FROM laws WHERE status='in_force'"
            ).fetchone()[0]
        except Exception:
            pass
    total_str = f"{total_db_laws:,}" if total_db_laws else "107.000+"
    sync_str = f"Aggiornato al {latest_db_date}" if latest_db_date != "N/A" else "Aggiornamento notturno"
    st.markdown(f"""
    <div class='nv-hero'>
      <h1>\U0001f1ee\U0001f1f9 NormattivaVigente</h1>
      <p>La legge italiana, spiegata in modo semplice \u2014 per ogni cittadino.</p>
      <small>{total_str} norme \u00b7 {sync_str} \u00b7 Powered by Groq AI</small>
    </div>
    """, unsafe_allow_html=True)

    if not has_groq:
        st.warning(
            "**GROQ_API_KEY non configurata** \u2014 risposte AI disabilitate. "
            "Configura il secret nelle impostazioni dello Space.",
            icon="\u26a0\ufe0f",
        )

    # ── Quick-action chips ────────────────────────────────────────
    chips = [
        ("\U0001f50d Cerca", "Cerca norme sul licenziamento senza preavviso"),
        ("\U0001f195 Ultime", "Mostrami le ultime leggi vigenti pubblicate"),
        ("\U0001f3eb Scuola", "Qual \u00e8 il programma scolastico previsto dalla legge italiana?"),
        ("\U0001f4dc Costituzione", "Cosa dice la Costituzione sul diritto al lavoro?"),
        ("\U0001f4ca Dataset", "Quante leggi ci sono nel dataset? Dammi le statistiche del corpus."),
    ]
    chip_cols = st.columns(len(chips))
    for i, (label, action) in enumerate(chips):
        if chip_cols[i].button(label, key=f"cit-chip-{i}", use_container_width=True):
            st.session_state.setdefault("citizen_chat", [])
            st.session_state["citizen_chat"].append({"role": "user", "content": action, "laws": []})
            st.session_state["citizen_pending"] = action
            st.rerun()

    # ── Intent detection ──────────────────────────────────────────
    def _detect_intent(text: str) -> str:
        lower = text.lower()
        if any(w in lower for w in ["ultime", "recenti", "nuove norme", "ultimi", "recente", "pubblicat", "ultime leggi"]):
            return "latest"
        if _re.search(r"costituzione|art[\.\s]+cost|diritto (fondamentale|inviolabile)", lower):
            return "constitution"
        if any(w in lower for w in ["dataset", "statistiche", "quante leggi", "corpus", "dati del",
                                     "numero di leggi", "totale leggi", "leggi nel", "quanti atti",
                                     "sincronizzat", "aggiornament", "copertura", "quante norme"]):
            return "dataset_stats"
        if any(w in lower for w in ["regno", "regio", "regia", "prerepubblican", "sabauda", "1861",
                                     "sabaud", "regi decreti", "era fascist", "fascismo", "legge 56",
                                     "abrogaz.*2025", "leggi del regno", "monarchia", "prima della repubblica"]):
            return "kingdom_era"
        return "groq_rag"

    # ── Inline law card — defined at module level as _card_chat() ──

    def _annotate_relevance(answer: str, laws: list, query: str = "") -> list:
        """For each law attach two explainer fields:
          - law["ai_context"]   : 1-3 sentences from the AI answer that cite this law
          - law["text_excerpt"] : most query-relevant passage from the law's own DB text
        """
        if not answer or not laws:
            return laws

        # Normalised query tokens (for excerpt scoring)
        q_tokens = set(
            w for w in _re.sub(r"[^\w\s]", " ", (query or "").lower()).split()
            if len(w) > 3 and w not in {
                "della", "dello", "degli", "delle", "nella", "negli", "dalle",
                "sono", "come", "cosa", "quando", "dove", "questo", "questa",
                "quali", "qual", "vuole", "vuoi", "anche", "legge", "norme",
                "norma", "atti", "atto",
            }
        )

        # Split AI answer into individual sentences
        sentences = _re.split(r"(?<=[.!?])\s+", _re.sub(r"\n+", " ", answer))

        _STOP = {"della", "dello", "degli", "delle", "nella", "negli", "dalle",
                 "sulle", "sulla", "sullo"}

        result = []
        for law in laws:
            law_copy = dict(law)
            urn = law.get("urn") or ""
            title = (law.get("title") or "").lower()
            title_words = [
                w for w in title.split()
                if len(w) > 4 and w not in _STOP
            ]

            # URN fragments: "2023;5", "1942-262", and the year alone
            urn_fragments = _re.findall(r"\d{4}[;\-]\d+", urn)
            urn_year_m = _re.search(r":(\d{4})-", urn)
            if urn_year_m:
                urn_fragments.append(urn_year_m.group(1))
            # Also law number from URN tail e.g. ";107" → "107"
            urn_num = _re.search(r";(\d+)$", urn)
            if urn_num:
                urn_fragments.append(urn_num.group(1))

            # ── Collect ALL sentences that mention this law ────────
            cited_sentences = []
            for sent in sentences:
                sent_l = sent.lower()
                score = 0
                for frag in urn_fragments:
                    if frag in sent:
                        score += 4
                for w in title_words[:6]:
                    if w in sent_l:
                        score += 1
                if score >= 2:
                    cited_sentences.append((score, sent.strip()))

            cited_sentences.sort(key=lambda x: -x[0])
            # Up to 3 sentences, deduplicated, joined naturally
            seen_s: set = set()
            picked = []
            for _, s in cited_sentences:
                key = s[:40]
                if key not in seen_s and len(s) > 20:
                    seen_s.add(key)
                    picked.append(s)
                if len(picked) >= 3:
                    break

            if picked:
                law_copy["ai_context"] = " ".join(picked)

            # ── Best-matching excerpt from DB text ────────────────
            raw_text = _re.sub(r"\s+", " ", (law.get("snippet") or law.get("text") or "").strip())
            if raw_text and len(raw_text) > 40:
                # Build overlapping windows of ~240 chars
                window, step = 280, 140
                best_exc, best_exc_score = "", 0
                for start in range(0, min(len(raw_text), 8000), step):
                    chunk = raw_text[start: start + window]
                    chunk_l = chunk.lower()
                    sc = 0
                    for tok in q_tokens:
                        if tok in chunk_l:
                            sc += 2
                    for tw in title_words[:4]:
                        if tw in chunk_l:
                            sc += 1
                    if sc > best_exc_score:
                        best_exc_score = sc
                        best_exc = chunk.strip()
                if not best_exc:
                    best_exc = raw_text[:240]
                law_copy["text_excerpt"] = best_exc[:280] + ("…" if len(best_exc) >= 280 else "")

            result.append(law_copy)
        return result

    # ── Dataset statistics helper ─────────────────────────────────
    def _dataset_stats_msg() -> tuple:
        if not db:
            return "Database non disponibile.", []
        try:
            total = db.conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
            vigenti = db.conn.execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
            abrogate = total - vigenti
            anno_min = db.conn.execute("SELECT MIN(year) FROM laws WHERE year > 0").fetchone()[0]
            anno_max = db.conn.execute("SELECT MAX(year) FROM laws WHERE year > 0").fetchone()[0]
            regno_vigenti = db.conn.execute(
                "SELECT COUNT(*) FROM laws WHERE year > 0 AND year < 1946 AND status='in_force'"
            ).fetchone()[0]
            n_2025 = db.conn.execute("SELECT COUNT(*) FROM laws WHERE year=2025").fetchone()[0]
            n_2026 = db.conn.execute("SELECT COUNT(*) FROM laws WHERE year=2026").fetchone()[0]
            latest_date = db.conn.execute(
                "SELECT MAX(date) FROM laws WHERE date != ''"
            ).fetchone()[0] or "N/A"
            latest_law = db.conn.execute(
                "SELECT title FROM laws WHERE date=? LIMIT 1", (latest_date,)
            ).fetchone()
            latest_title = (latest_law[0] or "")[:60] if latest_law else ""
            msg = (
                f"\U0001f4ca **Statistiche del dataset NormattivaVigente**\n\n"
                f"| Indicatore | Valore |\n"
                f"|---|---|\n"
                f"| \U0001f4da Totale norme | **{total:,}** |\n"
                f"| \U0001f7e2 Vigenti | **{vigenti:,}** ({vigenti/total*100:.1f}%) |\n"
                f"| \U0001f534 Abrogate | **{abrogate:,}** ({abrogate/total*100:.1f}%) |\n"
                f"| \U0001f4c5 Arco temporale | **{anno_min} \u2013 {anno_max}** |\n"
                f"| \U0001f3db\ufe0f Norme pre-1946 vigenti | **{regno_vigenti:,}** |\n"
                f"| \U0001f4c6 Norme 2025 nel dataset | **{n_2025:,}** |\n"
                f"| \U0001f4c6 Norme 2026 nel dataset | **{n_2026:,}** |\n"
                f"| \U0001f504 Ultima norma indicizzata | **{latest_date}** |\n\n"
                f"**Ultima legge:** *{latest_title}{'…' if len(latest_title)==60 else ''}*\n\n"
                f"🟢 **Stato sincronizzazione:** Il database è incluso staticamente nello Space "
                f"e riflette l'ultimo build della pipeline (collezione VIGENTE Normattiva). "
                f"Ultima norma registrata: {latest_date}. "
                f"Il dataset è allineato con lo stato vigente alla data di build.\n\n"
                f"Il corpus copre {anno_max - anno_min} anni di legislazione italiana. "
                f"Fonte: [Normattiva.it](https://www.normattiva.it) \u00b7 "
                f"Dataset: [HuggingFace](https://huggingface.co/datasets/diatribe00/normattivavigente-data)\n\n"
                f"*Vuoi esplorare un\u2019area specifica? Chiedi es.: \u2018quante leggi del 1942 sono vigenti?\u2019 "
                f"oppure \u2018mostrami le norme sul lavoro del 2020\u2019.*"
            )
            return msg, []
        except Exception as e:
            return f"Errore nel leggere le statistiche: {e}", []

    # ── Kingdom-era laws helper ────────────────────────────────────
    def _kingdom_era_msg() -> tuple:
        if not db:
            return "Database non disponibile.", []
        try:
            total_regno = db.conn.execute(
                "SELECT COUNT(*) FROM laws WHERE year > 0 AND year < 1946"
            ).fetchone()[0]
            vigenti_regno = db.conn.execute(
                "SELECT COUNT(*) FROM laws WHERE year > 0 AND year < 1946 AND status='in_force'"
            ).fetchone()[0]
            abrogate_regno = total_regno - vigenti_regno
            by_type = db.conn.execute(
                "SELECT type, COUNT(*) as n FROM laws "
                "WHERE year > 0 AND year < 1946 AND status='in_force' "
                "GROUP BY type ORDER BY n DESC LIMIT 5"
            ).fetchall()
            rows_sample = [
                dict(r) for r in db.conn.execute(
                    "SELECT urn, title, type, year, status FROM laws "
                    "WHERE year > 0 AND year < 1946 AND status='in_force' "
                    "ORDER BY year ASC LIMIT 8"
                ).fetchall()
            ]
            # Also grab some famous surviving laws for context
            famous_urns = [
                "urn:nir:stato:regio.decreto:1942-03-16;262",  # Codice Civile
                "urn:nir:stato:regio.decreto:1930-10-19;1398", # Codice Penale
                "urn:nir:stato:regio.decreto:1931-06-18;773",  # TULPS
            ]
            famous = []
            for u in famous_urns:
                r = db.conn.execute(
                    "SELECT urn, title, type, year, status FROM laws WHERE urn=? LIMIT 1", (u,)
                ).fetchone()
                if r:
                    famous.append(dict(r))
            tipo_lines = "\n".join(
                f"| {r[0] or 'N/A'} | {r[1]:,} |" for r in by_type
            )
            pct_abrogated = abrogate_regno / total_regno * 100 if total_regno else 0
            msg = (
                f"\U0001f3db\ufe0f **Norme del Regno d\u2019Italia nel dataset NormattivaVigente**\n\n"
                f"Il corpus contiene **{total_regno:,}** atti normativi emanati prima del 1946, di cui:\n"
                f"- \U0001f7e2 **{vigenti_regno:,} ancora vigenti** secondo Normattiva\n"
                f"- \U0001f534 **{abrogate_regno:,} abrogati** ({pct_abrogated:.1f}% del totale prerepubblicano)\n\n"
                f"> \U0001f4cc **Legge 56/2025 (7 aprile 2025):** Ha abrogato oltre 30.000 atti prerepubblicani "
                f"risalenti al 1861\u20131946. Il dataset \u00e8 aggiornato automaticamente ogni notte dalla collezione "
                f"**VIGENTE** di Normattiva.it, quindi i {vigenti_regno:,} conteggiati riflettono lo stato "
                f"attuale post-abrogazione registrato da Normattiva.\n\n"
                f"**Composizione per tipo (vigenti):**\n\n"
                f"| Tipo | Vigenti |\n|---|---|\n"
                f"{tipo_lines}\n\n"
                f"**Puoi chiedere:** *\u2018Cosa dice il Codice Civile del 1942?\u2019*, "
                f"*\u2018Quali regi decreti del 1931 sono ancora validi?\u2019*, "
                f"*\u2018Cosa \u00e8 rimasto in vigore dal fascismo?\u2019*\n\n"
                f"**Le pi\u00f9 antiche ancora vigenti nel dataset (prime 8):**"
            )
            return msg, (famous + rows_sample)[:8]
        except Exception as e:
            return f"Errore: {e}", []

    # ── Initialize chat session ────────────────────────────────────
    if "citizen_chat" not in st.session_state:
        st.session_state["citizen_chat"] = [
            {
                "role": "assistant",
                "content": (
                    "\U0001f1ee\U0001f1f9 **Ciao! Sono il tuo assistente giuridico.**\n\n"
                    "Scrivi qualsiasi domanda in italiano \u2014 capisco il linguaggio naturale:\n"
                    "- 🔍 Cerco norme nel dataset (67.000+ leggi vigenti italiane)\n"
                    "- \U0001f4d6 Mostro i testi integrali direttamente\n"
                    "- \U0001f916 Spiego in modo semplice cosa dice la legge\n"
                    "- \U0001f4ca Analizzo le statistiche del corpus legislativo\n\n"
                    "**Suggerimenti:** *programma scolastico*, *congedo parentale*, "
                    "*sfratto*, *pensione*, *privacy*, *codice penale*"
                ),
                "laws": [],
            }
        ]

    # ── Process pending query ─────────────────────────────────────
    pending = st.session_state.pop("citizen_pending", None)
    if pending and db:
        intent = _detect_intent(pending)
        new_msg = {"role": "assistant", "content": "", "laws": []}

        if intent == "latest":
            try:
                recent = [
                    dict(r) for r in db.conn.execute(
                        "SELECT urn, title, type, year, status, date FROM laws "
                        "WHERE status='in_force' ORDER BY date DESC LIMIT 12"
                    ).fetchall()
                ]
                new_msg["content"] = f"\U0001f4c5 Ecco le ultime **{len(recent)} norme vigenti** nel dataset:"
                new_msg["laws"] = recent
            except Exception as e:
                new_msg["content"] = f"\u26a0\ufe0f Errore nel caricare le norme recenti: {e}"

        elif intent == "constitution":
            results = _smart_search("costituzione diritti fondamentali", limit=25)
            cost_rows = [
                r for r in results
                if "costituzione" in (r.get("urn") or "").lower()
                or "costituzione" in (r.get("title") or "").lower()
            ] or results[:5]
            if has_groq and cost_rows:
                with st.spinner("Consulto la Costituzione\u2026"):
                    answer, err = _call_groq(pending, cost_rows, model=GROQ_DEFAULT_MODEL, max_tokens=700)
                if answer:
                    cost_rows = _annotate_relevance(answer, cost_rows, query=pending)
                new_msg["content"] = answer or f"\u26a0\ufe0f {err}"
            else:
                new_msg["content"] = "**Principi fondamentali della Costituzione**\n\nEcco le norme correlate:"
            new_msg["laws"] = cost_rows

        elif intent == "dataset_stats":
            content_text, laws = _dataset_stats_msg()
            new_msg["content"] = content_text
            new_msg["laws"] = laws

        elif intent == "kingdom_era":
            content_text, laws = _kingdom_era_msg()
            new_msg["content"] = content_text
            new_msg["laws"] = laws

        else:  # groq_rag — general question with FTS + Groq
            context_laws = _retrieve_context(pending, limit=12)
            if has_groq and context_laws:
                with st.spinner("\U0001f50d Ricerca nel dataset Normattiva\u2026"):
                    answer, err = _call_groq(
                        pending, context_laws,
                        model=GROQ_DEFAULT_MODEL, max_tokens=1200, temperature=0.1,
                    )
                used_model = st.session_state.get("last_groq_model_used")
                if answer:
                    reply = answer
                    if used_model:
                        reply += f"\n\n*Modello: {GROQ_MODELS.get(used_model, used_model)} ({used_model})*"
                    # Annotate each law with AI context + best text excerpt
                    context_laws = _annotate_relevance(answer, context_laws, query=pending)
                else:
                    reply = _build_accountable_fallback(pending, context_laws, err or "Errore AI")
            elif has_groq and not context_laws:
                reply = (
                    "\u26a0\ufe0f **Domanda fuori ambito legale italiano.**\n\n"
                    "Non ho trovato norme pertinenti nel dataset Normattiva per questa domanda. "
                    "Questo assistente risponde solo su **leggi e norme dell'ordinamento italiano**.\n\n"
                    "Esempi di domande utili:\n"
                    "- *Quanto dura il congedo di maternit\u00e0?*\n"
                    "- *Come funziona lo sfratto per morosit\u00e0?*\n"
                    "- *Cosa dice la legge sul licenziamento per giusta causa?*"
                )
                context_laws = []
            elif context_laws:
                titles = "\n".join(
                    f"- **{l.get('title','')[:68]}** ({l.get('year','')})"
                    for l in context_laws[:6]
                )
                reply = (
                    f"**Norme trovate nel dataset:**\n\n{titles}\n\n"
                    "*Configura `GROQ_API_KEY` per ricevere risposte in linguaggio semplice.*"
                )
            else:
                reply = "Nessuna norma trovata. Prova con parole chiave diverse."
                context_laws = []
            new_msg["content"] = reply
            new_msg["laws"] = context_laws

        st.session_state["citizen_chat"].append(new_msg)

    # ── EU laws tab is now a module-level function: _eu_laws_tab_render() ──

    # ── Render chat history ────────────────────────────────────────
    for idx, msg in enumerate(st.session_state["citizen_chat"]):
        with st.chat_message(msg["role"]):
            if msg.get("content"):
                st.markdown(msg["content"])
            laws = msg.get("laws") or []
            if laws:
                has_ctx = any(l.get("ai_context") or l.get("text_excerpt") for l in laws)
                if has_ctx:
                    st.caption(
                        f"\U0001f4da **{len(laws)} norme pertinenti** — "
                        "ogni scheda riporta **perché è rilevante** (risposta AI) "
                        "e **un estratto dal testo ufficiale**. "
                        "Il link URN apre la fonte su Normattiva.it."
                    )
                    for j, law in enumerate(laws[:8]):
                        _card_chat(law, f"h{idx}-{j}")
                else:
                    st.caption(
                        f"\U0001f4da **{len(laws)} norme** nel dataset — "
                        "il link URN porta al testo ufficiale su Normattiva.it:"
                    )
                    g1, g2 = st.columns(2)
                    for j, law in enumerate(laws[:8]):
                        _card_chat(law, f"h{idx}-{j}", g1 if j % 2 == 0 else g2)

    # ── Chat input (sticky bottom) ─────────────────────────────
    user_input = st.chat_input("Fai una domanda sulla legge italiana\u2026")
    if user_input:
        st.session_state["citizen_chat"].append({"role": "user", "content": user_input, "laws": []})
        st.session_state["citizen_pending"] = user_input
        st.rerun()

    # ── Clear button ───────────────────────────────────────────
    if len(st.session_state.get("citizen_chat", [])) > 1:
        if st.button("\U0001f5d1\ufe0f Nuova conversazione", key="clear-chat"):
            st.session_state["citizen_chat"] = []
            st.rerun()



def page_eu_laws():
    """🇪🇺 Norme UE — EU laws compliance tracker and search."""
    st.header("🇪🇺 Norme UE — Direttive e Attuazioni Nazionali")
    EU_CATEGORIES = {
        "🔒 Sicurezza digitale": ["cybersicurezza", "cybersecurity", "nis", "dora", "digitale"],
        "🔐 Dati & Privacy":     ["dati personali", "privacy", "gdpr", "protezione dei dati"],
        "👷 Lavoro":             ["lavoratori", "lavoro", "occupazione", "maternit", "paternit"],
        "🌱 Ambiente":           ["ambiente", "emissioni", "sostenibilit", "rifiuti", "clima", "energia"],
        "💰 Finanza":            ["finanziar", "bancari", "banca", "credito", "capitali", "assicuraz"],
        "🏥 Salute":             ["salute", "farmaci", "medic", "sanitari", "malattia"],
        "📦 Mercato interno":    ["mercato interno", "prodotti", "consumatori", "servizi", "concorrenza"],
        "🚗 Trasporti":          ["trasporti", "veicoli", "ferroviari", "marittimi"],
    }
    EU_WHERE = (
        "(title LIKE '%direttiva%' OR title LIKE '%(UE)%' OR "
        "title LIKE '%(CE)%' OR title LIKE '%(CEE)%' OR "
        "title LIKE '%recepimento%' OR "
        "source_collection = 'Atti di recepimento direttive UE' OR "
        "source_collection = 'Atti di attuazione Regolamenti UE' OR "
        "source_collection = 'Leggi di delegazione europea')"
    )

    def _extract_eu_ref(title: str):
        m = re.search(r"\((?:UE|CE|CEE|EURATOM)\)\s*(\d{4})/(\d+)", title or "", re.IGNORECASE)
        return m.group(0).strip() if m else None

    def _extract_eu_year(title: str):
        m = re.search(r"\((?:UE|CE|CEE|EURATOM)\)\s*(\d{4})/\d+", title or "", re.IGNORECASE)
        return int(m.group(1)) if m else None

    db = load_db()
    has_groq = bool(os.environ.get("GROQ_API_KEY", "").strip())

    if not db:
        st.warning("Database non disponibile.")
        return

    try:
        total_eu   = db.conn.execute(f"SELECT COUNT(*) FROM laws WHERE {EU_WHERE}").fetchone()[0]
        vigenti_eu = db.conn.execute(f"SELECT COUNT(*) FROM laws WHERE {EU_WHERE} AND status='in_force'").fetchone()[0]
        latest_eu  = db.conn.execute(f"SELECT MAX(date) FROM laws WHERE {EU_WHERE} AND date != ''").fetchone()[0] or "N/A"
    except Exception as e:
        st.error(f"Errore statistiche UE: {e}")
        return

    pct_v = vigenti_eu / total_eu * 100 if total_eu else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📋 Norme UE nel dataset", f"{total_eu:,}")
    c2.metric("🟢 Vigenti", f"{vigenti_eu:,}", f"{pct_v:.0f}%")
    c3.metric("🔴 Abrogate / superate", f"{total_eu - vigenti_eu:,}")
    c4.metric("📅 Ultima indicizzata", latest_eu)

    st.markdown(
        "Norme italiane che **recepiscono o attuano direttive e regolamenti UE**. "
        "Esplora lo stato di recepimento oppure cerca una norma specifica."
    )
    st.divider()

    tab_compliance, tab_cerca = st.tabs(["📊 Stato di recepimento", "🔍 Cerca norme"])

    with tab_compliance:
        st.markdown("### 🇮🇹 Come l'Italia ha recepito le direttive UE")
        st.caption(
            "Analisi basata sulle norme nel dataset. "
            "Le direttive recenti post-2023 potrebbero non essere ancora tutte recepite."
        )
        st.markdown("#### Copertura per area tematica")
        # Build all category conditions in one pass — 1 query instead of 16
        cat_rows = []
        try:
            all_rows = db.conn.execute(
                f"SELECT title FROM laws WHERE ({EU_WHERE})"
            ).fetchall()
            all_titles = [r[0] or "" for r in all_rows]
            all_rows_f = db.conn.execute(
                f"SELECT title FROM laws WHERE ({EU_WHERE}) AND status='in_force'"
            ).fetchall()
            vigenti_titles = set(r[0] or "" for r in all_rows_f)
            for cat_label, kws in EU_CATEGORIES.items():
                tot = sum(1 for t in all_titles if any(k in t.lower() for k in kws))
                vig = sum(1 for t in vigenti_titles if any(k in t.lower() for k in kws))
                cat_rows.append((cat_label, tot, vig))
        except Exception:
            for cat_label, kws in EU_CATEGORIES.items():
                kw_cond = " OR ".join(f"title LIKE '%{k}%'" for k in kws)
                try:
                    tot = db.conn.execute(
                        f"SELECT COUNT(*) FROM laws WHERE ({EU_WHERE}) AND ({kw_cond})"
                    ).fetchone()[0]
                    vig = db.conn.execute(
                        f"SELECT COUNT(*) FROM laws WHERE ({EU_WHERE}) AND ({kw_cond}) AND status='in_force'"
                    ).fetchone()[0]
                    cat_rows.append((cat_label, tot, vig))
                except Exception:
                    pass

        for cat_label, tot, vig in sorted(cat_rows, key=lambda x: -x[1]):
            if tot == 0:
                continue
            pct = vig / tot * 100
            status_icon = "🟢" if pct >= 70 else "🟡" if pct >= 40 else "🔴"
            col_l, col_r = st.columns([3, 1])
            col_l.markdown(f"**{cat_label}** — {vig}/{tot} vigenti")
            col_r.markdown(f"{status_icon} **{pct:.0f}%**")
            st.progress(int(pct))

        st.divider()
        st.markdown("#### 📈 Recepimento per anno")
        try:
            year_rows = db.conn.execute(
                f"SELECT year, COUNT(*) as cnt FROM laws WHERE ({EU_WHERE}) AND status='in_force' "
                f"AND year >= 2000 GROUP BY year ORDER BY year"
            ).fetchall()
            if year_rows:
                import altair as alt
                df_yr = pd.DataFrame(year_rows, columns=["anno", "norme"])
                chart = (
                    alt.Chart(df_yr)
                    .mark_bar(color="#1d4ed8")
                    .encode(
                        x=alt.X("anno:O", title="Anno"),
                        y=alt.Y("norme:Q", title="Norme vigenti recepite"),
                        tooltip=["anno", "norme"],
                    )
                    .properties(height=200, title="Norme UE vigenti recepite per anno (dal 2000)")
                )
                st.altair_chart(chart, use_container_width=True)
        except Exception:
            pass

        st.divider()
        st.markdown("#### 🤖 Chiedi sullo stato di recepimento")
        comp_q = st.text_input(
            "Es: 'Cosa prevede la NIS2?' o 'Come è recepita la direttiva GDPR?'",
            key="eu-comp-q",
        )
        if comp_q and has_groq:
            with st.spinner("Analisi in corso…"):
                eu_ctx = db.conn.execute(
                    f"SELECT urn, title, type, year, status, text, article_count "
                    f"FROM laws WHERE ({EU_WHERE}) AND status='in_force' "
                    f"AND (title LIKE ? OR text LIKE ?) LIMIT 8",
                    (f"%{comp_q[:30]}%", f"%{comp_q[:30]}%"),
                ).fetchall()
                eu_ctx = [dict(r) for r in eu_ctx]
                prompt = (
                    f"Sei un esperto di diritto europeo e diritto italiano. "
                    f"Analizza queste norme italiane che recepiscono direttive UE e rispondi "
                    f"in modo preciso indicando: 1) quali direttive UE sono recepite, "
                    f"2) cosa prevede la normativa italiana di attuazione, "
                    f"3) eventuali lacune o ritardi noti. "
                    f"Basati esclusivamente sulle norme del dataset fornite. Domanda: {comp_q}"
                )
                answer, err = _call_groq(prompt, eu_ctx, model=GROQ_DEFAULT_MODEL, max_tokens=900)
            st.markdown(answer or f"\u26a0\ufe0f {err}")
            if eu_ctx:
                st.caption(f"📚 {len(eu_ctx)} norme di riferimento dal dataset:")
                for j, law in enumerate(eu_ctx[:5]):
                    eu_ref = _extract_eu_ref(law.get("title") or "")
                    if eu_ref:
                        law = dict(law)
                        law["ai_context"] = f"Recepisce la direttiva {eu_ref}"
                    _card_chat(law, f"comp-ans-{j}")

    with tab_cerca:
        col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
        eu_search    = col_f1.text_input("🔍 Cerca nelle norme UE", placeholder="es. NIS2, GDPR, ambiente…", key="eu-search")
        cat_opts     = ["Tutte"] + list(EU_CATEGORIES.keys())
        eu_cat       = col_f2.selectbox("📂 Categoria", cat_opts, key="eu-cat")
        vigente_only = col_f3.checkbox("Solo vigenti", value=True, key="eu-vigente")

        conds = [EU_WHERE]
        if vigente_only:
            conds.append("status='in_force'")
        if eu_cat != "Tutte":
            kws = EU_CATEGORIES[eu_cat]
            conds.append("(" + " OR ".join(f"title LIKE '%{k}%'" for k in kws) + ")")
        if eu_search.strip():
            for w in eu_search.strip().split()[:4]:
                if len(w) > 2:
                    conds.append(f"(title LIKE '%{w}%' OR text LIKE '%{w}%')")
        where_clause = "WHERE " + " AND ".join(f"({c})" for c in conds)

        try:
            count_filtered = db.conn.execute(f"SELECT COUNT(*) FROM laws {where_clause}").fetchone()[0]
            eu_laws = [
                dict(r) for r in db.conn.execute(
                    f"SELECT urn, title, type, year, status, date, article_count "
                    f"FROM laws {where_clause} ORDER BY date DESC, year DESC LIMIT 40"
                ).fetchall()
            ]
        except Exception as e:
            st.error(f"Errore nella ricerca UE: {e}")
            return

        st.caption(f"**{count_filtered:,} norme** corrispondono ai filtri — mostrando le 40 più recenti.")

        if not eu_laws:
            st.info("Nessuna norma trovata. Prova ad allargare i filtri.")
            return

        g1, g2 = st.columns(2)
        for j, law in enumerate(eu_laws):
            col = g1 if j % 2 == 0 else g2
            urn      = law.get("urn") or ""
            title    = (law.get("title") or "N/A")[:80]
            badge    = "🟢" if _normalize_status(law.get("status")) == "in_force" else "🔴"
            typ      = law.get("type") or ""
            year     = law.get("year") or ""
            articles = law.get("article_count") or 0
            eu_ref   = _extract_eu_ref(law.get("title") or "")
            norm_url = f"https://www.normattiva.it/uri-res/N2Ls?{urn}" if urn else "#"
            safe     = re.sub(r"[^a-z0-9]", "-", urn.lower())[:60]
            eu_tag   = (
                f"<span style='font-size:0.72rem;background:#dbeafe;color:#1e40af;"
                f"border-radius:4px;padding:1px 5px;margin-left:4px;'>{eu_ref}</span>"
            ) if eu_ref else ""

            col.markdown(
                f"<div class='nv-inline-law' style='padding:0.6rem 0.75rem;'>"
                f"<strong style='font-size:0.88rem;'>{badge} {title}</strong>{eu_tag}<br>"
                f"<span style='font-size:0.77rem;color:#64748b;'>{typ} {year}"
                f"{f' · {articles} art.' if articles else ''}</span><br>"
                f"<a href='{norm_url}' target='_blank' rel='noopener' "
                f"style='font-size:0.73rem;color:#1d4ed8;text-decoration:underline;"
                f"word-break:break-all;'>{urn[:70]}</a>"
                f"</div>",
                unsafe_allow_html=True,
            )
            btn1, btn2 = col.columns(2)
            if btn1.button("📖 Apri testo", key=f"eu-open-{safe}-{j}", use_container_width=True):
                st.session_state["citizen_open_urn"] = urn
                st.rerun()
            if has_groq and btn2.button("🤖 Spiega", key=f"eu-ask-{safe}-{j}", use_container_width=True):
                with st.spinner("AI in elaborazione…"):
                    snippet, _ = _call_groq(
                        f"Spiega in italiano semplice cosa prevede questa norma, quale direttiva UE recepisce "
                        f"e cosa comporta per i cittadini: {law.get('title','')}",
                        [law], model=GROQ_DEFAULT_MODEL, max_tokens=400,
                    )
                col.info(snippet or "Risposta non disponibile.")




def page_hierarchy_visualizer():
    """5-tier legal hierarchy browser."""
    st.header("🏛️ Gerarchia delle Fonti del Diritto")
    st.caption(
        "Il sistema giuridico italiano è organizzato in 5 livelli gerarchici. "
        "Una norma di livello inferiore non può contraddire quelle superiori. "
        "Clicca su un livello per esplorare le norme di quella categoria."
    )
    db = load_db()
    if not db:
        st.error("Database non disponibile.")
        return

    TIERS = [
        ("I",   "#6d28d9", "#f5f3ff",
         "⚖️ Livello I — Leggi Costituzionali",
         "La Costituzione e le leggi costituzionali si trovano al vertice. Non possono essere derogate da norme inferiori.",
         ["Leggi costituzionali"]),
        ("II",  "#1d4ed8", "#eff6ff",
         "📚 Livello II — Codici & Testi Unici",
         "I Codici (Civile, Penale, ecc.) e i Testi Unici consolidano interi settori del diritto.",
         ["Codici", "Testi Unici"]),
        ("III", "#0369a1", "#f0f9ff",
         "📜 Livello III — Leggi Ordinarie & Regi Decreti",
         "Le leggi ordinarie approvate dal Parlamento, i Decreti Legge e la normativa dell'era regia.",
         ["DL e leggi di conversione", "Leggi delega e relativi provvedimenti delegati",
          "Leggi contenenti deleghe", "Leggi finanziarie e di bilancio", "Leggi di ratifica",
          "Leggi di delegazione europea", "Regi decreti", "Regi decreti legislativi",
          "Decreti legislativi luogotenenziali"]),
        ("IV",  "#0f766e", "#f0fdf4",
         "🔧 Livello IV — Decreti Legislativi & Atti UE",
         "Decreti legislativi emanati su delega parlamentare e atti di recepimento/attuazione del diritto UE.",
         ["Decreti Legislativi", "Atti di recepimento direttive UE", "Atti di attuazione Regolamenti UE"]),
        ("V",   "#475569", "#f8fafc",
         "📋 Livello V — Regolamenti & Decreti Esecutivi",
         "DPR, DPCM e regolamenti ministeriali. Danno attuazione concreta alle leggi primarie.",
         ["DPR", "Regolamenti ministeriali", "DPCM"]),
    ]

    try:
        coll_counts = {
            r[0]: r[1]
            for r in db.conn.execute(
                "SELECT source_collection, COUNT(*) FROM laws WHERE status='in_force' GROUP BY source_collection"
            ).fetchall()
        }
    except Exception:
        coll_counts = {}

    total_vigente = max(sum(coll_counts.values()), 1)

    tier_html = ""
    for tier_num, color, bg, label, desc, colls in TIERS:
        count = sum(coll_counts.get(c, 0) for c in colls)
        pct = round(count / total_vigente * 100, 1)
        coll_list = ", ".join(colls[:3]) + ("…" if len(colls) > 3 else "")
        tier_html += (
            f"<div style='background:{bg};border-left:5px solid {color};"
            f"border-radius:8px;margin:0.4rem 0;padding:0.75rem 1rem;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;'>"
            f"<strong style='color:{color};font-size:1rem;'>Livello {tier_num}</strong>"
            f"<span style='background:{color};color:#fff;border-radius:20px;"
            f"padding:2px 10px;font-size:0.8rem;font-weight:700;'>{count:,} norme ({pct}%)</span>"
            f"</div>"
            f"<div style='font-size:0.92rem;font-weight:700;margin:0.3rem 0;color:#1e293b;'>{label}</div>"
            f"<div style='font-size:0.82rem;color:#475569;line-height:1.5;'>{desc}</div>"
            f"<div style='font-size:0.73rem;color:#94a3b8;margin-top:0.3rem;'>Collezioni: {coll_list}</div>"
            f"</div>"
        )

    st.markdown(tier_html, unsafe_allow_html=True)
    st.divider()
    st.subheader("🔍 Esplora un livello")

    tier_opts: dict = {}
    for t in TIERS:
        key = f"Livello {t[0]} — {t[3].split('—')[1].strip()}"
        tier_opts[key] = t

    chosen = st.selectbox("Seleziona livello", list(tier_opts.keys()), key="hier-sel")
    if chosen:
        _, color, _, label, desc, colls = tier_opts[chosen]
        status_filter = True  # Always show only vigente laws
        try:
            where_colls = " OR ".join(f"source_collection=?" for _ in colls)
            status_where = " AND status='in_force'" if status_filter else ""
            rows = db.conn.execute(
                f"SELECT urn, title, type, year, date, status, article_count, source_collection "
                f"FROM laws WHERE ({where_colls}){status_where} ORDER BY date DESC LIMIT 100",
                colls,
            ).fetchall()
        except Exception as e:
            st.error(f"Errore: {e}")
            rows = []
        if rows:
            st.success(f"**{len(rows)} norme** trovate (prime 100 per data)")
            df = pd.DataFrame([dict(r) for r in rows])
            df["stato"] = df["status"].apply(_status_label)
            disp = df[["date", "type", "source_collection", "title", "stato", "article_count", "urn"]].rename(
                columns={
                    "date": "Data", "type": "Tipo", "source_collection": "Collezione",
                    "title": "Titolo", "stato": "Stato", "article_count": "Articoli", "urn": "URN",
                }
            )
            disp["Titolo"] = disp["Titolo"].str[:70]
            st.dataframe(disp, use_container_width=True, hide_index=True)
            urn_sel = {
                f"{r['title'][:65]} ({r['year']})": r['urn']
                for r in [dict(x) for x in rows[:50]] if r.get("urn")
            }
            sel = st.selectbox("Apri scheda", list(urn_sel.keys()), key="hier-open-sel")
            if sel and st.button("Apri scheda norma →", key="hier-open-btn"):
                st.session_state["detail_urn"] = urn_sel[sel]
                st.session_state["goto_page"] = "📖 Scheda Norma"
                st.rerun()
        else:
            st.info("Nessuna norma trovata per questo livello.")


def page_authoritative_laws():
    """Ranked list of most-cited / most authoritative Italian laws."""
    st.header("🌟 Leggi più Autorevoli")
    st.caption(
        "Classificazione delle norme per numero di citazioni in entrata: "
        "quante altre leggi fanno riferimento a questa norma. "
        "Un alto numero di citazioni indica una norma-cardine dell'ordinamento."
    )
    db = load_db()
    if not db:
        st.error("Database non disponibile.")
        return

    try:
        rows = db.conn.execute("""
            SELECT l.urn, l.title, l.type, l.year, l.date, l.status,
                   l.article_count, l.source_collection,
                   COALESCE(m.citation_count_incoming, 0) as citation_count_incoming,
                   COALESCE(m.citation_count_outgoing, 0) as citation_count_outgoing,
                   m.domain_cluster
            FROM law_metadata m
            JOIN laws l ON m.urn = l.urn
            WHERE m.citation_count_incoming > 0 AND l.status = 'in_force'
            ORDER BY m.citation_count_incoming DESC
            LIMIT 100
        """).fetchall()
    except Exception as e:
        st.error(f"Errore nel caricare i dati: {e}")
        return

    # Fallback: law_metadata.citation_count_incoming is 0 for all (URN format mismatch)
    # Use live computation from citations table
    cit_counts_live = _live_citation_counts()
    if not rows and cit_counts_live:
        try:
            placeholders = ",".join("?" * min(len(cit_counts_live), 200))
            top_urns = sorted(cit_counts_live, key=lambda u: -cit_counts_live[u])[:200]
            raw = db.conn.execute(f"""
                SELECT l.urn, l.title, l.type, l.year, l.date, l.status,
                       l.article_count, l.source_collection, m.domain_cluster
                FROM laws l
                LEFT JOIN law_metadata m ON l.urn = m.urn
                WHERE l.status = 'in_force' AND l.urn IN ({placeholders})
            """, top_urns).fetchall()
            # Re-sort by live count and add the live count field
            urn_to_row = {dict(r)["urn"]: dict(r) for r in raw}
            rows_live = []
            for urn in top_urns:
                r = urn_to_row.get(urn)
                if r:
                    r["citation_count_incoming"] = cit_counts_live[urn]
                    r["citation_count_outgoing"] = 0
                    rows_live.append(r)
            rows = rows_live[:100]
        except Exception:
            pass

    if not rows:
        st.info("Dati di citazione non disponibili.")
        return

    max_cit = rows[0]["citation_count_incoming"] if rows else 0
    total_vigente_auth = sum(1 for r in rows if _normalize_status(r["status"]) == "in_force")

    col1, col2, col3 = st.columns(3)
    col1.metric("Record citazioni", f"{max_cit:,}")
    col2.metric("Vigenti in top 100", f"{total_vigente_auth}")
    col3.metric("Norme analizzate", "100")

    df_top = pd.DataFrame([dict(r) for r in rows[:25]])
    df_top["short_title"] = df_top["title"].str[:45]
    df_top["vigor"] = df_top["status"].apply(
        lambda s: "Vigente" if _normalize_status(s) == "in_force" else "Abrogata"
    )
    fig = px.bar(
        df_top, x="citation_count_incoming", y="short_title",
        orientation="h", color="vigor",
        color_discrete_map={"Vigente": "#0a7a5a", "Abrogata": "#c0392b"},
        title="Top 25 norme più citate nell'ordinamento italiano",
        labels={"citation_count_incoming": "Citazioni in entrata", "short_title": ""},
    )
    fig.update_layout(yaxis={"autorange": "reversed"}, height=500)
    st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("📋 Top 100 — elenco completo")

    domain_opts = ["Tutte"] + sorted({r["domain_cluster"] for r in rows if r.get("domain_cluster")})
    col_f1, col_f2 = st.columns(2)
    domain_filter = col_f1.selectbox("Area del diritto", domain_opts, key="auth-domain")
    status_filt   = col_f2.selectbox("Stato", ["Solo vigenti", "Tutte"], key="auth-status")

    filtered = list(rows)
    if domain_filter != "Tutte":
        filtered = [r for r in filtered if r.get("domain_cluster") == domain_filter]
    if status_filt == "Solo vigenti":
        filtered = [r for r in filtered if _normalize_status(r["status"]) == "in_force"]

    for i, r in enumerate(filtered[:50], 1):
        r = dict(r)
        status = _normalize_status(r.get("status"))
        badge = "🟢" if status == "in_force" else "🔴"
        cit_in  = int(r.get("citation_count_incoming") or 0)
        cit_out = int(r.get("citation_count_outgoing") or 0)
        tier_html = _tier_badge(r.get("source_collection"))
        urn = r.get("urn") or ""
        norm_url = f"https://www.normattiva.it/uri-res/N2Ls?{urn}" if urn else "#"
        st.markdown(
            f"<div style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;"
            f"padding:0.7rem 1rem;margin-bottom:0.5rem;'>"
            f"<div style='display:flex;justify-content:space-between;align-items:flex-start;'>"
            f"<strong style='font-size:0.92rem;'>#{i} {badge} {(r.get('title') or '')[:72]}</strong>"
            f"<span style='white-space:nowrap;margin-left:0.5rem;'>{tier_html}</span></div>"
            f"<div style='display:flex;justify-content:space-between;margin-top:0.2rem;'>"
            f"<span style='font-size:0.77rem;color:#64748b;'>{r.get('type','')} {r.get('year','')} "
            f"· Area: {r.get('domain_cluster') or '—'} · Cita: {cit_out} norme</span>"
            f"<span style='font-size:0.8rem;font-weight:700;color:#0f766e;'>📥 {cit_in:,} citazioni</span></div>"
            f"<a href='{norm_url}' target='_blank' rel='noopener' "
            f"style='font-size:0.73rem;color:#1d4ed8;text-decoration:underline;'>{urn[:70]}</a>"
            f"</div>",
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        if c1.button("📖 Scheda norma", key=f"auth-det-{i}", use_container_width=True):
            st.session_state["detail_urn"] = urn
            st.session_state["goto_page"] = "📖 Scheda Norma"
            st.rerun()
        if c2.button("🔗 Esplora citazioni", key=f"auth-cit-{i}", use_container_width=True):
            st.session_state["cit_explore_urn"] = urn
            st.session_state["goto_page"] = "🧩 Rete Normativa"
            st.rerun()


def page_regional_gap():
    """Info page explaining dataset scope — state-only, no regional laws."""
    st.header("🗺️ Normativa Regionale — Guida al Sistema")
    st.info(
        "**Il dataset NormattivaVigente è esclusivamente statale.** "
        "Non contiene leggi regionali, regolamenti comunali o atti degli enti locali. "
        "Questa pagina spiega la struttura del sistema normativo italiano e dove trovare "
        "la normativa sub-statale."
    )

    st.subheader("📐 Struttura del sistema normativo italiano")
    levels = [
        ("🏛️ Stato — IN QUESTO DATASET", "#0a7a5a",
         "67.000+ atti vigenti · Costituzione, leggi ordinarie, D.Lgs., DPR, DPCM e molto altro.\n"
         "Fonte: Normattiva.it (portale ufficiale dello Stato).\n"
         "Il dataset copre il 100% della normativa primaria statale vigente."),
        ("🏘️ Regioni — NON in questo dataset", "#dc2626",
         "20 regioni con propri statuti e leggi su materie concorrenti: sanità, istruzione\n"
         "professionale, governo del territorio, commercio locale.\n"
         "Fonte: portali regionali ufficiali (BUR — Bollettino Ufficiale Regionale)."),
        ("🏙️ Province / Città metropolitane — NON in questo dataset", "#b45309",
         "Atti di indirizzo e pianificazione territoriale.\n"
         "Fonte: siti istituzionali delle province."),
        ("🏠 Comuni — NON in questo dataset", "#6b7280",
         "Regolamenti comunali (edilizio, commercio, polizia locale, tributi comunali).\n"
         "Fonte: albì pretori comunali e comuneweb.it."),
    ]
    for label, color, desc in levels:
        st.markdown(
            f"<div style='background:#f8fafc;border-left:5px solid {color};"
            f"border-radius:6px;padding:0.75rem 1rem;margin:0.4rem 0;'>"
            f"<strong style='color:{color};font-size:0.95rem;'>{label}</strong><br>"
            f"<span style='font-size:0.84rem;color:#334155;white-space:pre-line;'>{desc}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.subheader("🔗 Portali per la normativa regionale")
    portals = [
        ("Lombardia", "https://www.leggiregionali.it/lr/lombardia"),
        ("Lazio", "https://www.regione.lazio.it/normativa"),
        ("Veneto", "https://bur.regione.veneto.it"),
        ("Sicilia", "https://www.gurs.regione.sicilia.it"),
        ("Toscana", "https://raccoltanormativa.consiglio.regione.toscana.it"),
        ("EUR-Lex UE", "https://eur-lex.europa.eu"),
        ("Normattiva.it", "https://www.normattiva.it"),
        ("Giustizia.it", "https://www.giustizia.it"),
        ("Camera.it (leggi)", "https://www.camera.it/leg19/290"),
    ]
    cols = st.columns(3)
    for i, (name, url) in enumerate(portals):
        cols[i % 3].markdown(f"[🔗 {name}]({url})")

    st.divider()
    st.subheader("📊 Riepilogo delle collezioni nel dataset")
    db = load_db()
    if db:
        try:
            counts = db.conn.execute(
                "SELECT source_collection, COUNT(*) cnt FROM laws "
                "WHERE status='in_force' GROUP BY source_collection ORDER BY cnt DESC"
            ).fetchall()
            if counts:
                df_rc = pd.DataFrame([dict(r) for r in counts], columns=["Collezione", "Norme vigenti"])
                df_rc["Livello"] = df_rc["Collezione"].apply(
                    lambda c: f"T{_SOURCE_TIER.get(c, ('?','',''))[0]}" if c else "?"
                )
                st.dataframe(df_rc[["Livello", "Collezione", "Norme vigenti"]], use_container_width=True, hide_index=True)
        except Exception:
            pass


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
        "🏛️ Gerarchia delle Fonti": page_hierarchy_visualizer,
        "🌟 Leggi più Autorevoli": page_authoritative_laws,
        "🗺️ Normativa Regionale": page_regional_gap,
        "🇪🇺 Norme UE": page_eu_laws,
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
            "🇪🇺 Norme UE": all_pages["🇪🇺 Norme UE"],
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
        use_advanced = st.sidebar.checkbox(
            "🔧 Navigazione avanzata", value=False, key="adv-mode"
        )
        if not use_advanced:
            db = load_db()
            _citizen_mvp(db)
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
            "🏛️ Gerarchia delle Fonti": all_pages["🏛️ Gerarchia delle Fonti"],
            "🌟 Leggi più Autorevoli": all_pages["🌟 Leggi più Autorevoli"],
            "🗺️ Normativa Regionale": all_pages["🗺️ Normativa Regionale"],
            "🇪🇺 Norme UE": all_pages["🇪🇺 Norme UE"],
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
            db_path = str(db.db_path) if hasattr(db, "db_path") else ""
            _counts = _cached_db_counts(db_path) if db_path else {}
            in_f = _counts.get("in_force", 0)
            ab   = _counts.get("abrogated", 0)
            total = _counts.get("total", in_f + ab)
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
            "🇮🇹 **Italian Legal Lab** — Ricerca giuridica italiana\n\n"
            "67.000+ vigenti | FTS5 | Citazioni | Storia normativa"
        )
    elif IS_LAB:
        st.sidebar.markdown(
            "⚖️ **OpenNormattiva Lab** — Italian Legal Research\n\n"
            "VOOM: ~67K vigenti + ~123K abrogati nel corpus\n\n"
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

