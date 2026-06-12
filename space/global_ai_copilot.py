"""Persistent sidebar AI copilot using Groq Chat Completions API."""

from __future__ import annotations

import json
import re
import logging
from typing import Dict, List
from html import escape

import requests
import streamlit as st

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "Sei un assistente giuridico italiano esperto. "
    "Hai accesso a: (1) oltre 190.000 atti normativi italiani da Normattiva (leggi, decreti, codici, dalla legge unificazione 1865 ad oggi); "
    "(2) feed live della Gazzetta Ufficiale - Serie Generale con gli atti piu recenti; "
    "(3) disegni di legge del Senato della Repubblica (XIX legislatura); "
    "(4) feed RSS istituzionali (GU, Governo, Ministeri). "
    "Rispondi in modo chiaro ma tecnicamente rigoroso, distinguendo sempre cio che e vigente "
    "da cio che non e vigente. "
    "Usa le evidenze fornite nel contesto e cita URN/titolo delle norme quando disponibili. "
    "Per atti molto recenti (ultimi 30 giorni) segnala il feed GU come fonte aggiuntiva."
)

ITALIAN_STOPWORDS = {
    "a", "ad", "ai", "al", "alla", "alle", "allo", "anche", "che", "chi", "con", "come", "cosa",
    "da", "dal", "dalla", "dalle", "dei", "del", "della", "delle", "di", "dove", "e", "ed", "gli",
    "ha", "hanno", "i", "il", "in", "io", "la", "le", "lo", "ma", "mi", "ne", "nei", "nel", "nella",
    "nelle", "non", "o", "per", "piu", "poi", "quale", "quali", "quello", "questa", "questo", "se", "si",
    "sua", "sue", "sul", "sulla", "sulle", "tra", "un", "una", "uno", "vi", "vigente", "dice",
}

DOMAIN_HINTS = {
    "lavoro": ["lavor", "conged", "ferie", "contratt", "stipend", "dipendent"],
    "fiscale": ["iva", "imposta", "tribut", "fiscal", "irpef", "ires", "accis"],
    "salute": ["salute", "sanit", "ospedal", "ssn", "cura", "farmac"],
    "costituzionale": ["costituz", "diritti", "libert", "parlament", "costituzione"],
    "civile": ["civile", "obbligaz", "contratto", "responsabilit"],
    "penale": ["penale", "reat", "sanzion", "pena", "detenz"],
    "amministrativo": ["amministr", "provved", "tar", "procediment", "ente"],
    "istruzione": ["universit", "ateneo", "laure", "immatricol", "istruzion", "scuol", "student"],
}

QUERY_EXPANSIONS = {
    "university": ["universita", "ateneo", "accesso universita", "immatricolazione"],
    "college": ["universita", "ateneo", "corso di laurea"],
    "access": ["accesso", "ammissione", "requisiti"],
    "education": ["istruzione", "universita", "scuola"],
    "student": ["studente", "studenti", "diritto allo studio"],
    "health": ["salute", "sanitario"],
    "work": ["lavoro", "lavoratore", "occupazione"],
    "tax": ["imposta", "tributo", "iva", "irpef"],
    "law": ["legge", "norma", "disciplina"],
    "constitution": ["costituzione", "costituzionale"],
    # Labour
    "licenziamento": ["licenziamento", "lavoro", "lavoratore", "jobs act", "reintegra"],
    "licenziament": ["licenziamento", "lavoro", "reintegra"],
    "reintegra": ["reintegra", "licenziamento", "lavoro"],
    "ferie": ["ferie", "lavoro", "riposo", "congedo"],
    "stipend": ["stipendio", "retribuzione", "salario"],
    # Privacy / GDPR
    "gdpr": ["protezione dati personali", "dati personali", "privacy", "196"],
    "privacy": ["protezione dati personali", "dati personali", "gdpr"],
    "dati": ["dati personali", "protezione dati"],
    # Criminal / civil
    "diffamazione": ["diffamazione", "codice penale", "reato"],
    "reato": ["reato", "codice penale", "sanzione penale"],
    "omicidio": ["omicidio", "codice penale"],
    "obbligaz": ["obbligazioni", "codice civile", "contratto"],
    "contratto": ["contratto", "obbligazioni", "codice civile"],
    # Constitution
    "costituzione": ["costituzione", "principi fondamentali", "diritti fondamentali"],
    "diritti": ["diritti fondamentali", "costituzione", "liberta"],
    # IP
    "autore": ["diritto d autore", "opere dell ingegno", "copyright"],
    "copyright": ["diritto d autore", "633", "opere"],
}


def _get_dataset_statistics(db) -> Dict:
    """Fetch comprehensive dataset statistics for AI awareness."""
    if db is None or not hasattr(db, 'conn'):
        return {}
    
    try:
        stats = {}
        # Total laws
        total = db.conn.execute("SELECT COUNT(*) as cnt FROM laws").fetchone()
        stats['total_laws'] = total['cnt'] if total else 0
        
        # By status
        status_counts = db.conn.execute(
            "SELECT status, COUNT(*) as cnt FROM laws GROUP BY status"
        ).fetchall()
        stats['by_status'] = {row['status']: row['cnt'] for row in status_counts}
        
        # By type
        type_counts = db.conn.execute(
            "SELECT type, COUNT(*) as cnt FROM laws WHERE type IS NOT NULL GROUP BY type ORDER BY cnt DESC LIMIT 15"
        ).fetchall()
        stats['by_type'] = {row['type']: row['cnt'] for row in type_counts}
        
        # Year range
        year_stats = db.conn.execute(
            "SELECT MIN(year) as min_year, MAX(year) as max_year, COUNT(DISTINCT year) as year_count FROM laws WHERE year > 0"
        ).fetchone()
        stats['year_min'] = year_stats['min_year'] or 0
        stats['year_max'] = year_stats['max_year'] or 0
        stats['year_range'] = year_stats['year_count'] or 0
        
        # Latest additions
        latest = db.conn.execute(
            "SELECT urn, title, date, year FROM laws WHERE date IS NOT NULL ORDER BY date DESC, year DESC LIMIT 5"
        ).fetchall()
        stats['latest_laws'] = [
            {'urn': row['urn'], 'title': row['title'], 'date': row['date'], 'year': row['year']}
            for row in latest
        ]
        
        # Most cited
        most_cited = db.conn.execute(
            "SELECT cited_urn, COUNT(*) as citation_count FROM citations GROUP BY cited_urn ORDER BY citation_count DESC LIMIT 8"
        ).fetchall()
        stats['most_cited_urns'] = [row['cited_urn'] for row in most_cited]
        
        # Average citations per law
        citation_stats = db.conn.execute(
            "SELECT COUNT(DISTINCT citing_urn) as citing_count, COUNT(DISTINCT cited_urn) as cited_count FROM citations"
        ).fetchone()
        stats['total_citations'] = citation_stats['citing_count'] if citation_stats else 0
        
        return stats
    except Exception as e:
        logger.error(f"Error fetching dataset stats: {e}")
        return {}


# Terms that — even when paired with meta triggers like "quanti" — signal a legal question
_LEGAL_OVERRIDE_TERMS = [
    "giorni", "ore", "mesi", "anni", "anni di", "ferie", "stipend", "retribuzion",
    "penale", "sanzione", "pena", "condanna", "multa", "reato",
    "contratto", "obbligaz", "responsabilit",
    "licenziamento", "reintegra", "dimission",
    "imposta dovuta", "deducibile", "detraibile",
    "diffamazione", "calunnia", "truffa",
    "spett",  # spettano, spetta
]

# Meta phrases that are unambiguously about dataset statistics (not rights/duties)
_META_STRONG_PHRASES = [
    "nel database", "nel dataset", "nella banca dati", "nel sistema",
    "citate nel", "citate dal", "totale norme", "tipi di atti",
    "quante leggi ci sono", "quante norme ci sono",
    "quante leggi del regno", "quanti atti", "quanti decreti nel",
    "distribuzione", "statistiche", "statistics",
]

def _detect_query_intent(user_q: str, stats: Dict) -> str:
    """Classify query intent: 'meta' (about dataset), 'exploration', or 'legal'."""
    q_lower = (user_q or "").lower()

    # Strong meta phrases are always meta regardless of context
    if any(phrase in q_lower for phrase in _META_STRONG_PHRASES):
        return "meta"

    # If the query contains a meta trigger but also a legal override term → legal
    weak_meta_triggers = ["quante", "quanti", "quanto", "how many", "how much"]
    has_weak_meta = any(kw in q_lower for kw in weak_meta_triggers)
    has_legal_override = any(t in q_lower for t in _LEGAL_OVERRIDE_TERMS)
    if has_weak_meta and has_legal_override:
        return "legal"

    meta_keywords = [
        "quante", "quanti", "how many", "count", "total",
        "ultimo", "latest", "recente", "recent", "nuovo", "new", "aggiunt", "added",
        "distribuzion", "distribution", "quali tipi", "what types", "quando", "when",
        "quanto", "how much", "database", "dataset", "collection", "contenuto", "contained",
        "piu citat", "più citat",
    ]

    exploration_keywords = [
        "mostrami", "show me", "dimmi", "tell me", "elenca", "list",
        "confronta", "compare", "similari", "similar", "correlati", "related", "approfondisci",
        "explore", "scopri", "discover",
    ]

    if any(kw in q_lower for kw in meta_keywords):
        return "meta"
    elif any(kw in q_lower for kw in exploration_keywords):
        return "exploration"
    else:
        return "legal"


def _generate_clarifying_questions(user_q: str, intent: str, db, stats: Dict) -> List[str]:
    """Generate follow-up questions to guide user exploration."""
    q_lower = (user_q or "").lower()
    questions = []
    
    if intent == "meta":
        if _is_kingdom_query(user_q):
            questions.append("Vuoi il conteggio di tutte le norme del Regno o solo di quelle ancora vigenti oggi?")
            questions.append("Ti interessa anche la distribuzione per tipo, come Regio Decreto o Regio Decreto-Legge?")
            return questions[:2]
        if any(w in q_lower for w in ["legge", "law", "decreto", "decreto-legge"]):
            questions.append("Vuoi sapere di un tipo specifico (leggi ordinarie, decreti, decreti-legge)?")
        if any(w in q_lower for w in ["vigente", "in force", "abrogat", "abrogated"]):
            questions.append("Ti interessa il confronto tra norme vigenti e abrogate?")
        if not questions:
            questions.append("Vuoi esplorare la distribuzione per tipo di norma o per anno?")
    
    elif intent == "exploration":
        # Suggest domain-specific exploration
        domains = ["lavoro", "istruzione", "fiscale", "salute", "costituzionale", "penale"]
        matching = [d for d in domains if d in q_lower]
        if matching:
            questions.append(f"Vuoi approfondire le leggi su {matching[0]}?")
        else:
            questions.append("In quale area del diritto stai cercando (lavoro, istruzione, fiscale, etc)?")
        questions.append("Preferisci una visione d'insieme o dettagli su specifiche norme?")
    
    else:  # legal
        questions.append("Cerchi norme vigenti o anche storiche/abrogate?")
        questions.append("Ti interessano gli atti citati o i decreti correlati?")
        if stats.get('most_cited_urns'):
            questions.append("Vuoi sapere quali sono le norme più citate nel sistema?")
    
    return questions[:2]


def _build_dataset_context_prompt(stats: Dict) -> str:
    """Build a context block about dataset contents for the AI."""
    lines = [
        "CONSAPEVOLEZZA DEL DATASET:",
        f"- Totale norme: {stats.get('total_laws', 'N/A'):,}",
        f"- Norme vigenti: {stats.get('by_status', {}).get('in_force', stats.get('by_status', {}).get('vigente', 0)):,}",
        f"- Norme abrogate: {stats.get('by_status', {}).get('abrogated', stats.get('by_status', {}).get('abrogato', 0)):,}",
        f"- Periodo coperto: {stats.get('year_min')} - {stats.get('year_max')}",
        f"- Principali tipi: {', '.join(list(stats.get('by_type', {}).keys())[:5])}",
    ]
    
    if stats.get('latest_laws'):
        lines.append("- Ultime norme aggiunte:")
        for law in stats.get('latest_laws', [])[:3]:
            lines.append(f"  • {law.get('title', 'N/A')} ({law.get('year')})")
    
    return "\n".join(lines)


# Stat-seeking words that must co-occur with kingdom terms for a true kingdom META query
_KINGDOM_STAT_TRIGGERS = [
    "quante", "quanti", "how many", "count", "totale", "total",
    "in vigore", "ancora vigent", "in force", "distribuzion",
    "quanti sono", "quante sono", "statistiche",
]

def _is_kingdom_query(user_q: str) -> bool:
    """True only when the query is asking for stats/counts ABOUT kingdom-era laws."""
    q_lower = (user_q or "").lower()
    has_kingdom_term = any(term in q_lower for term in ["regno", "regio decreto", "regi decreto", "kingdom", "kingdom-era"])
    if not has_kingdom_term:
        return False
    # Guard: also require a stat-seeking word so that specific-law lookups like
    # "unificazione regno italia" don't get routed to kingdom meta.
    return any(trigger in q_lower for trigger in _KINGDOM_STAT_TRIGGERS)


def _safe_secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default) or default)
    except Exception:
        return default


def _groq_key() -> str:
    # Env wins, then secrets fallback.
    import os

    return (
        os.environ.get("GROQ_API_KEY", "").strip()
        or _safe_secret("GROQ_API_KEY", "").strip()
    )


def _call_groq_chat(
    api_key: str,
    model: str,
    messages: List[Dict],
    temperature: float = 0.2,
    max_tokens: int = 1400,
) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_live_gu_rss() -> List[Dict]:
    """Fetch live RSS from Gazzetta Ufficiale - Serie Generale with 10-minute cache."""
    import urllib.request
    from xml.etree import ElementTree as ET
    from email.utils import parsedate_to_datetime

    url = "https://www.gazzettaufficiale.it/rss/SG"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ItalianLegalLab/1.0)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=6)
        content = resp.read().decode("utf-8", errors="replace")
        root = ET.fromstring(content)
        items = []
        for item in root.iter("item"):
            def _t(tag: str) -> str:
                el = item.find(tag)
                return (el.text or "").strip() if el is not None else ""

            title = _t("title")
            pubdate = _t("pubDate")
            link = _t("link")
            desc = _t("description")
            try:
                pub_iso = parsedate_to_datetime(pubdate).isoformat() if pubdate else ""
            except Exception:
                pub_iso = pubdate

            items.append({
                "source": "Gazzetta Ufficiale",
                "title": title,
                "published": pub_iso,
                "link": link,
                "description": desc[:300],
            })
        return items
    except Exception:
        return []


@st.cache_data(ttl=900, show_spinner=False)
def _load_dataset_rss_snapshot(dataset_repo: str) -> List[Dict]:
    from pathlib import Path
    from huggingface_hub import hf_hub_download

    if not dataset_repo:
        return []

    try:
        p = hf_hub_download(
            repo_id=dataset_repo,
            repo_type="dataset",
            filename="data/legal_rss/latest.jsonl",
        )
    except Exception:
        return []

    rows: List[Dict] = []
    with Path(p).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
            if len(rows) >= 200:
                break
    return rows


def _tokenize_query(text: str) -> List[str]:
    parts = re.findall(r"[a-zA-Z0-9_]+", (text or "").lower())
    return [p for p in parts if len(p) > 2 and p not in ITALIAN_STOPWORDS]


def _candidate_queries(user_q: str) -> List[str]:
    tokens = _tokenize_query(user_q)
    candidates: List[str] = []

    raw = (user_q or "").strip()
    if raw:
        candidates.append(raw)

    if tokens:
        candidates.append(" ".join(tokens[:8]))

    # Expand common English/legal intent terms to Italian legal vocabulary.
    expanded: List[str] = []
    for t in tokens:
        for term in QUERY_EXPANSIONS.get(t, []):
            expanded.append(term)
    if expanded:
        candidates.append(" ".join(expanded[:8]))
        candidates.extend(expanded[:8])

    # Add targeted token and bi-gram probes to avoid strict all-terms FTS misses.
    for t in tokens[:6]:
        candidates.append(t)
    for i in range(min(len(tokens) - 1, 5)):
        candidates.append(f"{tokens[i]} {tokens[i + 1]}")

    # Keep unique, non-empty queries preserving order.
    out: List[str] = []
    seen = set()
    for q in candidates:
        qn = q.strip()
        if qn and qn not in seen:
            out.append(qn)
            seen.add(qn)
    return out


def _query_domain_hints(tokens: List[str]) -> List[str]:
    labels: List[str] = []
    for domain, stems in DOMAIN_HINTS.items():
        if any(any(tok.startswith(st) for st in stems) for tok in tokens):
            labels.append(domain)
    return labels


def _token_overlap_score(tokens: List[str], row: Dict) -> float:
    hay = (
        f"{row.get('title', '')} {row.get('snippet', '')} {row.get('type', '')}".lower()
    )
    if not hay or not tokens:
        return 0.0
    hit = 0
    for tok in tokens[:10]:
        if tok in hay:
            hit += 1
    return hit / max(1, min(len(tokens), 10))


def _key_term_boost(tokens: List[str], row: Dict) -> float:
    title = str(row.get("title") or "").lower()
    snippet = str(row.get("snippet") or "").lower()
    if not title and not snippet:
        return 0.0

    boost = 0.0
    # Prioritize semantically strong terms (length >= 5) appearing in title/snippet.
    key_terms = [t for t in tokens if len(t) >= 5][:6]
    for tok in key_terms:
        if tok in title:
            boost += 0.9
        elif tok in snippet:
            boost += 0.4
    return min(2.7, boost)


# Map of canonical law name-triggers → URN.  Used to pin foundational acts at top of results.
_CANONICAL_PINS: List[tuple] = [
    # (list_of_trigger_terms, urn, label)
    (["codice civile", "codice civil"], "urn:nir:stato:regio.decreto:1942-03-16;262", "Codice civile"),
    (["codice penale", "codice pena"], "urn:nir:stato:regio.decreto:1930-10-19;1398", "Codice penale"),
    (["tulps", "pubblica sicurezza", "testo unico leggi sicurezza"], "urn:nir:stato:regio.decreto:1931-06-18;773", "TULPS"),
    (["codice della strada", "codice stradale"], "urn:nir:stato:decreto.legislativo:1992-04-30;285", "Codice della strada"),
    (["diritto d autore", "diritto autore", "copyright", "legge 633", "l. 633", "l.633"], "urn:nir:stato:legge:1941-04-22;633", "L. 633/1941 Dir. autore"),
    (["legge fallimentare", "fallimento", "concordato preventivo", "procedure concorsuali"], "urn:nir:stato:regio.decreto:1942-03-16;267", "Legge fallimentare"),
    (["codice crisi", "codice della crisi", "crisi d impresa", "crisi impresa", "insolvenza"], "urn:nir:stato:decreto.legislativo:2019-01-12;14", "Codice della crisi d'impresa"),
    (["protezione dati personali", "dati personali", "privacy", "gdpr", "codice privacy", "d.lgs. 196", "dlgs 196", "196/2003"], "urn:nir:stato:decreto.legislativo:2003-06-30;196", "Codice Privacy / D.Lgs. 196/2003"),
    (["unificazione amministrativa", "allegato e", "legge 2248", "l. 2248"], "urn:nir:stato:legge:1865-03-20;2248", "L. 2248/1865"),
    (["costituzione", "costituzione italiana", "principi fondamentali", "diritti fondamentali della costituzione"], "urn:nir:stato:costituzione:1947-12-22;", "Costituzione della Repubblica Italiana"),
    (["codice dei contratti pubblici", "appalti pubblici", "gara d appalto"], "urn:nir:stato:decreto.legislativo:2023-03-31;36", "Codice dei contratti pubblici"),
    (["protezione civile", "codice protezione civile"], "urn:nir:stato:decreto.legislativo:2018-01-02;1", "Codice protezione civile"),
    (["statuto dei lavoratori", "statuto lavoratori", "licenziamento illegittimo", "reintegra", "art. 18", "articolo 18", "licenziament", "disciplina del licenziamento"], "urn:nir:stato:legge:1970-05-20;300", "Statuto dei Lavoratori L.300/1970"),
    (["jobs act", "tutele crescenti", "licenziamento tutele"], "urn:nir:stato:decreto.legislativo:2015-03-04;23", "D.Lgs. 23/2015 Jobs Act - tutele crescenti"),
    (["imposta valore aggiunto", "aliquota iva", "disciplina iva", "dpr 633", "decreto iva"], "urn:nir:stato:decreto.del.presidente.della.repubblica:1972-10-26;633", "DPR 633/1972 - IVA"),
    (["diffamazione", "diffamaz", "stampa reato", "delitto stampa", "reato diffamaz"], "urn:nir:stato:legge:1948-02-08;47", "L. 47/1948 - diffamazione a mezzo stampa"),
    (["ammissione università", "immatricolazione università", "accesso università", "ammissione corsi", "laurea triennale requisiti"], "urn:nir:stato:decreto.ministeriale:2004-10-22;270", "DM 270/2004 - ammissione università"),
]


def _pin_canonical_laws(db, q_lower: str, merged: Dict[str, Dict]) -> None:
    """Inject canonical foundational laws into merged results when name-matched.
    If already present in merged (from FTS), force its rank_score to guaranteed top.
    """
    if db is None or not hasattr(db, "conn"):
        return
    for triggers, urn, label in _CANONICAL_PINS:
        if not any(t in q_lower for t in triggers):
            continue
        PIN_SCORE = 1000.0  # guaranteed above any FTS/rerank score
        if urn in merged:
            # Already retrieved — boost to guaranteed top position
            merged[urn]["rank_score"] = PIN_SCORE
            merged[urn]["relevance_score"] = max(float(merged[urn].get("relevance_score") or 0), PIN_SCORE)
            if not merged[urn].get("snippet"):
                merged[urn]["snippet"] = f"Norma fondamentale: {label}"
        else:
            # Not yet in merged — fetch from DB and inject
            try:
                row = db.conn.execute(
                    "SELECT urn, title, type, year, date, status, article_count, text_length, importance_score FROM laws WHERE urn=?",
                    (urn,)
                ).fetchone()
                if row:
                    d = dict(row)
                    d["relevance_score"] = PIN_SCORE
                    d["rank_score"] = PIN_SCORE
                    d["snippet"] = f"Norma fondamentale: {label}"
                    merged[urn] = d
            except Exception:
                pass


def _retrieve_evidence(db, user_q: str, top_k: int) -> List[Dict]:
    if db is None or not hasattr(db, "search_fts"):
        return []

    merged: Dict[str, Dict] = {}
    tokens = _tokenize_query(user_q)
    hinted_domains = _query_domain_hints(tokens)
    qset = set(tokens)

    for q in _candidate_queries(user_q):
        try:
            rows = db.search_fts(q, limit=max(top_k * 4, 20))
        except Exception:
            rows = []

        for row in rows:
            urn = str(row.get("urn") or "").strip()
            if not urn:
                continue
            score = float(row.get("relevance_score") or 0.0)
            prev = merged.get(urn)
            if prev is None or score > float(prev.get("relevance_score") or 0.0):
                merged[urn] = row
            elif not prev.get("snippet") and row.get("snippet"):
                prev["snippet"] = row.get("snippet")

    # Final fallback: LIKE search over main text/title using distilled terms.
    if len(merged) < top_k and hasattr(db, "conn"):
        terms = _tokenize_query(user_q)[:4]
        if terms:
            where = " OR ".join(["LOWER(title) LIKE ?", "LOWER(text) LIKE ?"] * len(terms))
            params = []
            for t in terms:
                params.extend([f"%{t}%", f"%{t}%"])
            params.append(max(top_k * 3, 20))
            try:
                rows = db.conn.execute(
                    f"""
                    SELECT urn, title, type, year, date, status, article_count, text_length,
                           importance_score, 0.0 as relevance_score, '' as snippet
                    FROM laws
                    WHERE {where}
                    ORDER BY importance_score DESC
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()
                for r in rows:
                    row = dict(r)
                    urn = str(row.get("urn") or "").strip()
                    if urn and urn not in merged:
                        merged[urn] = row
            except Exception:
                pass

    # Pin canonical foundational laws when name-matched in query
    _pin_canonical_laws(db, (user_q or "").lower(), merged)

    # Second-stage reranking: in-force first, query-domain alignment, lexical overlap.
    if merged and hasattr(db, "conn"):
        urns = [u for u in merged.keys() if u]
        placeholders = ",".join(["?"] * len(urns))
        meta_map: Dict[str, Dict] = {}
        try:
            rows = db.conn.execute(
                f"""
                SELECT l.urn,
                       l.status,
                       l.year,
                       l.importance_score,
                       COALESCE(m.domain_cluster, '') AS domain_cluster
                FROM laws l
                LEFT JOIN law_metadata m ON m.urn = l.urn
                WHERE l.urn IN ({placeholders})
                """,
                tuple(urns),
            ).fetchall()
            for r in rows:
                meta_map[str(r["urn"])] = dict(r)
        except Exception:
            meta_map = {}

        for urn, row in merged.items():
            meta = meta_map.get(urn, {})
            status = str(meta.get("status") or row.get("status") or "").lower()
            domain = str(meta.get("domain_cluster") or "").lower()
            importance = float(meta.get("importance_score") or row.get("importance_score") or 0.0)
            base = float(row.get("relevance_score") or 0.0)

            status_boost = 1.6 if status in {"in_force", "vigente", "v"} else (-0.3 if status in {"abrogated", "abrogato", "a"} else 0.0)
            domain_boost = 0.0
            if hinted_domains and domain:
                if any(h in domain for h in hinted_domains):
                    domain_boost = 1.2
            lexical_boost = 1.3 * _token_overlap_score(tokens, row)
            key_boost = _key_term_boost(tokens, row)
            importance_boost = min(0.8, importance * 0.8)

            title_low = str(row.get("title") or "").lower()
            policy_penalty = 0.0
            if "istruzione" in hinted_domains:
                # For education/access questions prefer norms about admission/requisites/courses,
                # and penalize generic university mentions (donations/contributions, etc.).
                has_policy_terms = any(
                    t in title_low
                    for t in [
                        "access", "ammission", "requisit", "immatricol", "laurea",
                        "specializz", "test", "graduatoria", "corso di laurea",
                    ]
                )
                asks_access = any(t in qset for t in {"access", "accesso", "admission", "ammissione", "university", "universita"})
                if asks_access and not has_policy_terms:
                    policy_penalty -= 1.4

            # Preserve canonical pin score — do not overwrite if pinned
            if float(row.get("rank_score") or 0.0) >= 100.0:
                pass  # canonical pin — leave at high score
            else:
                row["rank_score"] = base + status_boost + domain_boost + lexical_boost + key_boost + importance_boost + policy_penalty

    ranked = sorted(
        merged.values(),
        key=lambda x: (
            float(x.get("rank_score") or x.get("relevance_score") or 0.0),
            float(x.get("importance_score") or 0.0),
            int(x.get("year") or 0),
        ),
        reverse=True,
    )
    return ranked[:top_k]


def _build_context(db, user_q: str, dataset_repo: str, top_k: int = 8) -> Dict:
    evidence = _retrieve_evidence(db, user_q, top_k=top_k)

    # 1. Try live GU RSS first (most up-to-date, 10-min cache)
    live_gu = _fetch_live_gu_rss()

    # 2. Fallback to session-cached items then dataset snapshot
    rss_items = st.session_state.get("rss_last_items") or []
    rss_updated = st.session_state.get("rss_last_updated", "not-loaded")
    if not rss_items:
        rss_items = _load_dataset_rss_snapshot(dataset_repo)
        if rss_items:
            rss_updated = "dataset_snapshot"

    # Merge: live GU items first, then institutional RSS (deduplicated by title)
    merged_rss: List[Dict] = []
    seen_titles: set = set()
    for item in live_gu:
        key = (item.get("title") or "")[:80]
        if key and key not in seen_titles:
            merged_rss.append(item)
            seen_titles.add(key)
    for item in rss_items:
        key = (item.get("title") or "")[:80]
        if key and key not in seen_titles:
            merged_rss.append(item)
            seen_titles.add(key)

    rss_source = "live_gu+dataset" if live_gu else rss_updated

    # Filter GU items to only normative acts (laws/decrees) for the recent-laws list
    _norm_kws = ["DECRETO LEGISLATIVO", "LEGGE", "DECRETO-LEGGE", "DECRETO DEL PRESIDENTE"]
    recent_laws_gu = [
        x for x in live_gu
        if any(kw in (x.get("title") or "").upper() for kw in _norm_kws)
    ][:8]

    return {
        "dataset_evidence": [
            {
                "eid": f"L{i+1}",
                "title": r.get("title"),
                "year": r.get("year"),
                "status": r.get("status"),
                "urn": r.get("urn"),
                "snippet": r.get("snippet", ""),
            }
            for i, r in enumerate(evidence)
        ],
        "rss_evidence": [
            {
                "source": x.get("source", x.get("institution", "")),
                "title": x.get("title"),
                "published": x.get("published"),
                "link": x.get("link"),
            }
            for x in merged_rss[:20]
        ],
        "recent_laws_gu": [
            {
                "title": x.get("title"),
                "published": x.get("published"),
                "link": x.get("link"),
            }
            for x in recent_laws_gu
        ],
        "rss_updated_at": rss_source,
        "live_gu_ok": bool(live_gu),
    }


def _format_evidence_catalog(evidence: List[Dict]) -> str:
    if not evidence:
        return "Nessuna evidenza normativa disponibile."
    lines = []
    for item in evidence:
        lines.append(
            f"[{item.get('eid')}] {item.get('title')} | URN: {item.get('urn')} | "
            f"anno: {item.get('year')} | status: {item.get('status')} | snippet: {item.get('snippet','')[:260]}"
        )
    return "\n".join(lines)


def _extract_citation_ids(text: str) -> List[str]:
    ids = re.findall(r"\[(L\d+)\]", text or "")
    out = []
    seen = set()
    for cid in ids:
        if cid not in seen:
            out.append(cid)
            seen.add(cid)
    return out


def _resolve_citations(evidence: List[Dict], cited_ids: List[str]) -> List[Dict]:
    m = {str(x.get("eid")): x for x in evidence}
    out = [m[cid] for cid in cited_ids if cid in m]
    if not out:
        return evidence[:4]
    return out


def _extract_relevant_excerpt(text: str, terms: List[str], max_chars: int = 1300) -> str:
    txt = (text or "").replace("\n", " ").strip()
    if not txt:
        return ""

    pos = -1
    for t in terms[:8]:
        p = txt.lower().find(t.lower())
        if p >= 0:
            pos = p
            break

    if pos < 0:
        return txt[:max_chars]

    start = max(0, pos - 260)
    end = min(len(txt), start + max_chars)
    return txt[start:end]


def _highlight_terms_html(text: str, terms: List[str]) -> str:
    safe = escape(text or "")
    # Longest terms first to avoid nested highlights.
    sorted_terms = sorted({t for t in terms if len(t) >= 4}, key=len, reverse=True)
    for t in sorted_terms[:10]:
        pattern = re.compile(re.escape(escape(t)), re.IGNORECASE)
        safe = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", safe)
    return safe


def _build_law_cards(db, evidence: List[Dict], query: str) -> List[Dict]:
    terms = _tokenize_query(query)
    cards: List[Dict] = []
    for ev in evidence:
        urn = str(ev.get("urn") or "")
        title = str(ev.get("title") or "N/A")
        status = str(ev.get("status") or "N/A")
        year = ev.get("year")
        snippet = str(ev.get("snippet") or "")

        full_text = ""
        if db is not None and urn and hasattr(db, "get_law"):
            try:
                law = db.get_law(urn)
                if law:
                    full_text = str(law.get("text") or "")
                    title = str(law.get("title") or title)
                    status = str(law.get("status") or status)
                    year = law.get("year") or year
            except Exception:
                pass

        excerpt = _extract_relevant_excerpt(full_text or snippet, terms, max_chars=1300)
        highlighted = _highlight_terms_html(excerpt, terms)

        why_parts = []
        if status:
            why_parts.append(f"Stato norma: {status}")
        if year:
            why_parts.append(f"Anno: {year}")
        if snippet:
            why_parts.append("Contiene termini coerenti con la domanda")

        cards.append(
            {
                "eid": ev.get("eid"),
                "urn": urn,
                "title": title,
                "status": status,
                "year": year,
                "excerpt": excerpt,
                "highlighted_excerpt_html": highlighted,
                "why": why_parts,
            }
        )
    return cards


def _target_law_detail_page() -> str:
    current = str(st.session_state.get("page-nav", ""))
    italian_tokens = ["Cerca", "Scheda", "Vigenti", "Abrogati", "Aree", "Esporta"]
    if any(tok in current for tok in italian_tokens):
        return "📖 Scheda Legge"
    return "📖 Law Detail"


def _open_law_from_assistant(urn: str):
    if not urn:
        return
    st.session_state["detail_urn"] = urn
    st.session_state["goto_page"] = _target_law_detail_page()
    st.rerun()


def _render_evidence_actions(evidence: List[Dict], key_prefix: str):
    if not evidence:
        st.info("Nessuna evidenza normativa trovata per questa domanda.")
        return

    st.caption("Norme rilevanti da esplorare")
    for i, row in enumerate(evidence[:8], 1):
        eid = row.get("eid", f"L{i}")
        urn = row.get("urn", "")
        title = row.get("title", "N/A")
        year = row.get("year", "N/A")
        status = row.get("status", "N/A")
        label = f"{eid}. {title} ({year})"
        st.markdown(f"**{label}**")
        st.caption(f"URN: {urn} | status: {status}")
        if st.button("Apri scheda legge", key=f"{key_prefix}-open-{i}-{urn}"):
            _open_law_from_assistant(urn)


def _render_chat_history(chat_key: str, db, compact: bool = False):
    history = st.session_state.get(chat_key, [])
    if not history:
        return

    st.markdown("### Cronologia")
    for idx, item in enumerate(reversed(history[-8:]), 1):
        with st.expander(f"Q{idx}: {item['q']}", expanded=(idx == 1 and not compact)):
            st.write(item["a"])
            cited = item.get("cited_evidence", item.get("evidence", []))
            if item.get("intent") != "meta" and cited:
                _render_evidence_actions(cited, key_prefix=f"{chat_key}-{idx}")

            law_cards = item.get("law_cards") or _build_law_cards(db, cited, item.get("q", ""))
            if law_cards:
                st.markdown("#### Norme Citate Interattive")
                for n, card in enumerate(law_cards[:8], 1):
                    with st.expander(f"{card.get('eid', f'L{n}')}: {card.get('title','N/A')}"):
                        st.caption(f"URN: {card.get('urn','')} | Status: {card.get('status','N/A')} | Anno: {card.get('year','N/A')}")
                        if card.get("why"):
                            st.markdown("**Perché è stata citata**")
                            for w in card.get("why", []):
                                st.write(f"- {w}")
                        if card.get("highlighted_excerpt_html"):
                            st.markdown("**Estratto della norma (termini rilevanti evidenziati)**", unsafe_allow_html=False)
                            st.markdown(
                                f"<div style='border:1px solid #d8d8d8;padding:0.75rem;border-radius:0.5rem;'>{card.get('highlighted_excerpt_html')}</div>",
                                unsafe_allow_html=True,
                            )
            if not compact:
                with st.expander("Contesto usato"):
                    st.json(item.get("ctx", {}))


def _assistant_controls(db, dataset_repo: str, chat_key: str, compact: bool = False):
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    # Load heavy dataset statistics lazily only when they are actually needed.
    if "dataset_stats" not in st.session_state:
        st.session_state["dataset_stats"] = {}
    stats = st.session_state["dataset_stats"]

    model = st.selectbox(
        "Modello",
        options=["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        index=0,
        key=f"{chat_key}-model",
    )
    top_k = st.slider("Evidenze leggi (RAG locale)", 3, 20, 10, 1, key=f"{chat_key}-top-k")

    user_q = st.text_area(
        "Domanda",
        placeholder="Es: quali norme vigenti disciplinano oggi congedi parentali e tutele lavoro?",
        key=f"{chat_key}-question",
        height=120 if not compact else 100,
    )

    # Show clarifying suggestions if user has typed something
    if user_q.strip() and not compact:
        intent = _detect_query_intent(user_q, stats)
        clarifying_q = _generate_clarifying_questions(user_q, intent, db, stats)
        if clarifying_q:
            with st.expander("💡 Suggerimenti di esplorazione", expanded=False):
                for q in clarifying_q:
                    if st.button(q, key=f"{chat_key}-suggest-{q[:30]}"):
                        st.session_state[chat_key + "-question"] = user_q.strip() + " " + q
                        st.rerun()

    run = st.button("Chiedi all'assistente", key=f"{chat_key}-run", use_container_width=True)

    api_key = _groq_key()
    if not api_key:
        st.warning("GROQ_API_KEY non configurata. Impostala nei Secrets o nelle variabili ambiente del Space.")

    if run and user_q.strip():
        if not stats:
            stats = _get_dataset_statistics(db)
            st.session_state["dataset_stats"] = stats
        intent = _detect_query_intent(user_q, stats)
        
        # Special handling for meta-queries
        if intent == "meta":
            # Generate metadata answer without LLM
            meta_answer = _answer_meta_query(user_q, stats, db)
            st.session_state[chat_key].append(
                {
                    "q": user_q.strip(),
                    "a": meta_answer,
                    "ctx": {"meta_query": True, "stats": stats},
                    "evidence": [],
                    "cited_evidence": [],
                    "law_cards": [],
                    "intent": "meta",
                }
            )
        else:
            # Standard legal query with evidence retrieval
            context = _build_context(db, user_q.strip(), dataset_repo=dataset_repo, top_k=top_k)
            evidence = context.get("dataset_evidence", [])
            evidence_for_llm = evidence[:7]
            evidence_catalog = _format_evidence_catalog(evidence_for_llm)
            
            if not api_key:
                st.session_state[chat_key].append(
                    {
                        "q": user_q.strip(),
                        "a": "Impossibile chiamare Groq: manca GROQ_API_KEY.",
                        "ctx": context,
                        "evidence": evidence,
                        "cited_evidence": evidence_for_llm[:4],
                        "intent": intent,
                    }
                )
            else:
                # Build rich prompt with dataset awareness
                dataset_context = _build_dataset_context_prompt(stats)
                recent_gu_block = ""
                recent_laws = context.get("recent_laws_gu", [])
                if recent_laws:
                    recent_gu_block = (
                        "Atti normativi recenti pubblicati in GU (live feed - non citare con [Lx]):\n"
                        + "\n".join(
                            f"- {x.get('title', '')} [{x.get('published', '')[:10]}]"
                            for x in recent_laws
                        )
                        + "\n\n"
                    )
                user_prompt = (
                    "Domanda utente:\n"
                    f"{user_q.strip()}\n\n"
                    f"{dataset_context}\n\n"
                    "Evidenze normative disponibili (usa SOLO queste per citare norme con [Lx]):\n"
                    f"{evidence_catalog}\n\n"
                    f"{recent_gu_block}"
                    "Contesto RSS istituzionale (informativo, non citare con [Lx]):\n"
                    f"{json.dumps(context.get('rss_evidence', [])[:8], ensure_ascii=False)}\n\n"
                    "Linee guida risposta:\n"
                    "- rispondi in italiano naturale, dettagliato e strutturato (almeno 6-8 paragrafi)\n"
                    "- quando citi una norma, usa obbligatoriamente il tag [Lx] corrispondente all'evidenza\n"
                    "- evidenzia differenza tra vigente/non vigente se disponibile\n"
                    "- proponi percorso di approfondimento dentro il portale (ricerca, scheda legge, citazioni)\n"
                    "- se il contesto normativo non basta, proponi domande di follow-up per esplorare meglio\n"
                    "- se il contesto non basta, dichiaralo con precisione\n"
                    "- non inventare citazioni fuori catalogo\n"
                    "- includi una sezione finale 'Perch\u00e9 questa risposta' spiegando il ragionamento e i limiti"
                )
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ]
                try:
                    with st.spinner("Analisi in corso..."):
                        answer = _call_groq_chat(
                            api_key=api_key,
                            model=model,
                            messages=messages,
                            temperature=0.15,
                            max_tokens=1500,
                        )
                except Exception as exc:
                    answer = f"Errore Groq API: {exc}"

                cited_ids = _extract_citation_ids(answer)
                cited_evidence = _resolve_citations(evidence_for_llm, cited_ids)
                if cited_ids:
                    answer = answer + "\n\nFonti citate: " + ", ".join(cited_ids)
                elif evidence_for_llm:
                    fallback_ids = ", ".join(x.get("eid", "") for x in cited_evidence if x.get("eid"))
                    answer = answer + (
                        "\n\nNota citazioni: il modello non ha inserito tag [Lx] in output. "
                        f"Riferimenti mostrati in fallback: {fallback_ids}."
                    )

                law_cards = _build_law_cards(db, cited_evidence, user_q.strip())

                st.session_state[chat_key].append(
                    {
                        "q": user_q.strip(),
                        "a": answer,
                        "ctx": context,
                        "evidence": evidence,
                        "cited_evidence": cited_evidence,
                        "law_cards": law_cards,
                        "intent": intent,
                    }
                )

    return api_key


def _answer_meta_query(user_q: str, stats: Dict, db) -> str:
    """Generate direct answer to meta-queries about the dataset."""
    q_lower = (user_q or "").lower()
    
    lines = ["## Analisi del Dataset\n"]

    if _is_kingdom_query(user_q):
        kingdom_total = 0
        kingdom_vigenti = 0
        kingdom_types = []
        try:
            rows = db.conn.execute(
                """
                SELECT type, status, COUNT(*) AS cnt
                FROM laws
                WHERE LOWER(type) LIKE '%regio decreto%'
                GROUP BY type, status
                ORDER BY cnt DESC
                """
            ).fetchall()
            kingdom_total = sum(int(r['cnt']) for r in rows)
            kingdom_vigenti = sum(int(r['cnt']) for r in rows if str(r['status']).lower() == 'in_force')
            kingdom_types = sorted({str(r['type']) for r in rows if r['type']}, key=str.lower)
        except Exception:
            pass

        lines.append(f"**Norme del Regno nel dataset**: {kingdom_total:,}")
        lines.append(f"**Norme del Regno ancora vigenti oggi**: {kingdom_vigenti:,}")
        if kingdom_types:
            lines.append("\n**Tipi principali trovati**:")
            for typ in kingdom_types[:8]:
                lines.append(f"- {typ}")
        try:
            regno_text_count = db.conn.execute(
                "SELECT COUNT(*) FROM laws WHERE LOWER(title) LIKE '%regno%' OR LOWER(type) LIKE '%regno%'"
            ).fetchone()[0]
            lines.append(f"**Norme che contengono esplicitamente 'Regno' nel titolo o nel tipo**: {regno_text_count:,}")
        except Exception:
            pass
        lines.append("\n**Nota**: ho interpretato 'leggi de regno' come norme di epoca del Regno/classificate come `Regio Decreto*`.")
        lines.append("\n### Suggerimenti per l'esplorazione")
        lines.append("- Vuoi il dettaglio per singolo tipo (`Regio Decreto`, `Regio Decreto-Legge`, `Regio Decreto Legislativo`)?")
        lines.append("- Posso anche mostrarti quante di queste norme sono ancora in vigore per anno o materia")
        return "\n".join(lines)
    
    # Most cited laws
    if any(w in q_lower for w in ["citat", "citate", "citata", "pi\u00f9 citat", "piu citat", "maggiormente citat"]):
        lines.append("\n**Norme pi\u00f9 citate nel dataset**:")
        cited_urns = stats.get("most_cited_urns", [])
        if cited_urns and hasattr(db, "conn"):
            try:
                placeholders = ",".join(["?"] * len(cited_urns))
                rows = db.conn.execute(
                    f"SELECT urn, title, citation_count FROM (SELECT cited_urn AS urn, COUNT(*) as citation_count FROM citations GROUP BY cited_urn ORDER BY citation_count DESC LIMIT 10) c JOIN laws l ON l.urn = c.urn ORDER BY citation_count DESC"
                ).fetchall()
                for r in rows[:10]:
                    lines.append(f"- {r[1][:70]} — **{r[2]:,} citazioni** | {r[0]}")
            except Exception:
                for u in cited_urns[:6]:
                    lines.append(f"- {u}")
        else:
            lines.append("_(dati citazioni non disponibili)_")

    # Total laws question
    if any(w in q_lower for w in ["quante", "quanti", "how many", "count", "total", "totali"]):
        lines.append(f"**Totale norme nel dataset**: {stats.get('total_laws', 0):,}")
        if stats.get('by_status'):
            lines.append("\n**Distribuzione per status**:")
            for status, count in sorted(stats.get('by_status', {}).items(), key=lambda x: x[1], reverse=True):
                lines.append(f"- {status}: {count:,}")

    # Latest laws
    if any(w in q_lower for w in ["ultimo", "latest", "recente", "recent", "nuovo", "new", "aggiunt", "added"]):
        lines.append("\n**Ultime norme aggiunte**:")
        for law in stats.get('latest_laws', []):
            title = law.get('title', 'N/A')[:80]
            lines.append(f"- {title} ({law.get('year')})")

    # Types distribution
    if any(w in q_lower for w in ["tipo", "tipi", "type", "quali sono", "which", "distribuzion"]):
        lines.append("\n**Principali tipi di norme**:")
        for typ, count in sorted(stats.get('by_type', {}).items(), key=lambda x: x[1], reverse=True)[:8]:
            lines.append(f"- {typ}: {count:,}")

    # Year range
    if any(w in q_lower for w in ["quando", "year", "anno", "periodo", "period", "range", "tempo"]):
        lines.append(f"\n**Periodo coperto**: {stats.get('year_min')} - {stats.get('year_max')}")
        lines.append(f"**Anni rappresentati**: {stats.get('year_range')}")
    
    # Add exploration suggestions
    lines.append("\n### Suggerimenti per l'esplorazione")
    lines.append("- Usa il **Cerca Leggi** per filtri avanzati per tipo, anno, status")
    lines.append("- Accedi a **Vigenti** o **Abrogati** per visualizzazioni rapide")
    lines.append("- Esplora le **Aree Tematiche** per domande normative")
    lines.append("- Il **feed live GU** (Gazzetta Ufficiale Serie Generale) è aggiornato in tempo reale — ultime 28 pubblicazioni disponibili")
    lines.append("- Il dataset copre atti fino a maggio 2026 (87 atti del 2026, inclusi D.Lgs. 83, DL 66 Piano Casa, L.79);")
    
    return "\n".join(lines)


def render_global_ai_copilot(db, dataset_repo: str):
    with st.sidebar.expander("🤖 Assistente AI (Groq)", expanded=True):
        st.caption("Tutor sempre disponibile su dataset legale + feed RSS istituzionali.")
        _assistant_controls(db, dataset_repo=dataset_repo, chat_key="global_ai_chat_sidebar", compact=True)
        _render_chat_history("global_ai_chat_sidebar", db=db, compact=True)


def render_ai_assistant_page(db, dataset_repo: str):
    st.header("🤖 Assistente Legale AI")
    st.caption(
        "Fai qualsiasi domanda sul sistema giuridico italiano. "
        "Le risposte sono supportate da evidenze del dataset e feed istituzionali disponibili."
    )

    api_key = _assistant_controls(db, dataset_repo=dataset_repo, chat_key="global_ai_chat_page", compact=False)
    if api_key:
        st.success("Groq API attiva")

    st.info(
        "Suggerimento: dopo la risposta, usa i pulsanti 'Apri scheda legge' per passare "
        "direttamente al testo della norma e continuare l'esplorazione."
    )

    _render_chat_history("global_ai_chat_page", db=db, compact=False)
