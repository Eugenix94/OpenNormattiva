#!/usr/bin/env python3
"""
Italian Legal Lab: Integrated Multi-Source Legal Research Platform

Features:
- Full Normattiva dataset (190k+ laws: vigente + abrogate + multivigente)
- Constitutional Court (Corte Costituzionale) jurisprudence (5000+ sentenze)
- Integrated law <-> jurisprudence exploration
- Advanced analytics and visualization
- Historical version tracking
- Multi-source integration foundation (ready for Cassazione, Admin Courts, EU law)

This is a comprehensive legal research platform, not a simple search clone.
"""

import streamlit as st
import json
import os
import sys
import pandas as pd
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
import plotly.express as px
import plotly.graph_objects as go
from collections import Counter
import logging

# Setup paths
_app_dir = Path(__file__).parent
_root_dir = _app_dir.parent
sys.path.insert(0, str(_root_dir))
sys.path.insert(0, str(_app_dir))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# DATABASE & INITIALIZATION
# ─────────────────────────────────────────────────────────────────

@st.cache_resource
def load_enhanced_db():
    """Load enhanced lab database with normattiva + jurisprudence."""
    db_path = Path('/app/data/laws.db') if Path('/app/data/laws.db').exists() else Path('data/laws.db')
    
    if not db_path.exists():
        try:
            from huggingface_hub import hf_hub_download
            db_path = Path(hf_hub_download(
                repo_id=get_dataset_repo(),
                filename='data/laws.db',
                repo_type='dataset'
            ))
        except Exception as e:
            st.error(f"Cannot load database: {e}")
            return None
    
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        logger.info(f"Database loaded: {db_path}")
        return conn
    except Exception as e:
        logger.error(f"Error loading database: {e}")
        return None


def get_dataset_repo() -> str:
    """Get the configured dataset repo."""
    owner = os.environ.get("HF_DATASET_OWNER", "diatribe00")
    name = os.environ.get("HF_DATASET_NAME", "italian-legal-lab-data")
    return f"{owner}/{name}"


# ─────────────────────────────────────────────────────────────────
# LAB DASHBOARD - Enhanced Overview
# ─────────────────────────────────────────────────────────────────

def page_lab_dashboard():
    """Enhanced lab dashboard with normattiva + jurisprudence overview."""
    st.header("🔬 Dashboard — Italian Legal Corpus + Corte Costituzionale")
    
    conn = load_enhanced_db()
    if not conn:
        st.error("Database not available")
        return
    
    repo = get_dataset_repo()
    st.caption(f"Dataset: {repo}")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    try:
        laws_count = conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
        vigente = conn.execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
        abrogate = conn.execute("SELECT COUNT(*) FROM laws WHERE status='abrogated'").fetchone()[0]
        citations_count = conn.execute('SELECT COUNT(*) FROM citations').fetchone()[0]
        sentenze_count = 0
        if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sentenze'").fetchone()[0]:
            sentenze_count = conn.execute('SELECT COUNT(*) FROM sentenze').fetchone()[0]
        
        col1.metric("📜 Leggi Totali", f"{laws_count:,}")
        col2.metric("✅ Vigenti", f"{vigente:,}")
        col3.metric("❌ Abrogate", f"{abrogate:,}")
        col4.metric("🔗 Citazioni", f"{citations_count:,}")
        col5.metric("⚖️ Decisioni CC", f"{sentenze_count:,}")
    except Exception as e:
        st.warning(f"Error loading stats: {e}")
    
    st.divider()
    
    # Laws by decade
    st.subheader("📈 Normattiva — Evoluzione temporale")
    try:
        decades = conn.execute('''
            SELECT 
                (year / 10) * 10 AS decade,
                COUNT(*) AS count,
                SUM(CASE WHEN status='in_force' THEN 1 ELSE 0 END) AS vigenti
            FROM laws
            GROUP BY decade
            ORDER BY decade
        ''').fetchall()
        
        if decades:
            df = pd.DataFrame([dict(d) for d in decades])
            fig = px.bar(df, x='decade', y=['vigenti', 'count'],
                        title='Leggi per decennio',
                        labels={'decade': 'Decennio', 'vigenti': 'Vigenti', 'count': 'Abrogate'},
                        barmode='stack')
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.warning(f"Error loading trends: {e}")
    
    st.divider()
    
    # Most cited laws
    st.subheader("🌟 Leggi più citate")
    try:
        top_cited = conn.execute('''
            SELECT l.title, l.year, l.urn, COUNT(*) as citations
            FROM citations c
            JOIN laws l ON l.urn = c.cited_urn
            GROUP BY c.cited_urn
            ORDER BY citations DESC
            LIMIT 15
        ''').fetchall()
        
        if top_cited:
            df = pd.DataFrame([
                {
                    'Legge': f"{d[0][:50]} ({d[1]})",
                    'Citazioni': d[3],
                    'URN': d[2]
                }
                for d in top_cited
            ])
            fig = px.barh(df, x='Citazioni', y='Legge',
                         title='Top 15 leggi per citazioni',
                         color='Citazioni',
                         color_continuous_scale='Blues')
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.warning(f"Error loading citations: {e}")


# ─────────────────────────────────────────────────────────────────
# INTEGRATED SEARCH
# ─────────────────────────────────────────────────────────────────

def page_integrated_search():
    """Search across both normattiva laws and jurisprudence."""
    st.header("🔍 Ricerca Integrata — Leggi + Giurisprudenza")
    
    conn = load_enhanced_db()
    if not conn:
        st.error("Database not available")
        return
    
    search_type = st.radio("Tipo di ricerca:", 
                          ["📜 Leggi", "⚖️ Sentenze", "🔀 Entrambi"],
                          horizontal=True)
    
    query = st.text_input("Inserisci ricerca...", placeholder="es: diritti umani, proprietà, etc.")
    
    if not query.strip():
        st.info("Inserisci un termine di ricerca per iniziare.")
        return
    
    query_term = f"%{query}%"
    
    if search_type in ["📜 Leggi", "🔀 Entrambi"]:
        st.subheader("📜 Risultati Leggi")
        try:
            results = conn.execute('''
                SELECT title, year, urn, article_count, status
                FROM laws
                WHERE title LIKE ? OR urn LIKE ?
                LIMIT 50
            ''', (query_term, query_term)).fetchall()
            
            if results:
                df = pd.DataFrame([dict(r) for r in results])
                st.dataframe(df, use_container_width=True)
                st.caption(f"Trovate {len(results)} leggi")
            else:
                st.info("Nessuna legge trovata.")
        except Exception as e:
            st.warning(f"Errore ricerca leggi: {e}")
    
    if search_type in ["⚖️ Sentenze", "🔀 Entrambi"]:
        st.subheader("⚖️ Risultati Corte Costituzionale")
        try:
            sentenze_exists = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sentenze'"
            ).fetchone()[0]
            if not sentenze_exists:
                st.info("Tabella sentenze non disponibile nel database.")
            else:
                results = conn.execute('''
                    SELECT ecli, numero, anno, tipo, data_deposito, oggetto, esito
                    FROM sentenze
                    WHERE LOWER(COALESCE(oggetto, '')) LIKE ? OR LOWER(COALESCE(testo, '')) LIKE ?
                    ORDER BY anno DESC, numero DESC
                    LIMIT 50
                ''', (query_term.lower(), query_term.lower())).fetchall()

                if results:
                    df = pd.DataFrame([dict(r) for r in results])
                    st.dataframe(
                        df.rename(columns={
                            'numero': 'Numero',
                            'anno': 'Anno',
                            'tipo': 'Tipo',
                            'data_deposito': 'Deposito',
                            'oggetto': 'Oggetto',
                            'esito': 'Esito',
                        }),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.caption(f"Trovate {len(results)} decisioni")
                else:
                    st.info("Nessuna decisione trovata.")
        except Exception as e:
            st.warning(f"Errore ricerca sentenze: {e}")


# ─────────────────────────────────────────────────────────────────
# LAW EXPLORER WITH JURISPRUDENCE
# ─────────────────────────────────────────────────────────────────

def page_law_jurisprudence_explorer():
    """Browse laws with connected jurisprudence insights."""
    st.header("📖 Esplora Leggi + Giurisprudenza")
    
    conn = load_enhanced_db()
    if not conn:
        st.error("Database not available")
        return
    
    # Filter options
    col1, col2, col3 = st.columns(3)
    with col1:
        year_min = st.number_input("Anno min", min_value=1861, max_value=2026, value=1900)
    with col2:
        year_max = st.number_input("Anno max", min_value=1861, max_value=2026, value=2026)
    with col3:
        status_filter = st.selectbox("Stato:", ["Tutti", "Vigenti", "Abrogate"])
    
    try:
        # Build query
        where_clause = f"WHERE year BETWEEN {year_min} AND {year_max}"
        if status_filter == "Vigenti":
            where_clause += " AND status='in_force'"
        elif status_filter == "Abrogate":
            where_clause += " AND status='abrogated'"
        
        laws = conn.execute(f'''
            SELECT l.urn, l.title, l.year, l.status, l.article_count,
                   COALESCE(l.importance_score, 0) AS importance_score,
                   COALESCE(m.citation_count_incoming, 0) AS cited_by
            FROM laws l
            LEFT JOIN law_metadata m ON m.urn = l.urn
            {where_clause.replace('WHERE ', 'WHERE l.')}
            ORDER BY l.year DESC
            LIMIT 200
        ''').fetchall()
        
        if laws:
            df = pd.DataFrame([dict(l) for l in laws])
            
            # Show table
            st.dataframe(df, use_container_width=True, key="laws-explorer")
            
            # Select one for detail
            selected_urn = st.selectbox(
                "Seleziona una legge per dettagli:",
                [l['urn'] for l in laws],
                format_func=lambda u: next(
                    (f"{l['year']} — {l['title'][:70]}" for l in laws if l['urn'] == u),
                    u
                )
            )
            
            if selected_urn:
                law = conn.execute(
                    'SELECT * FROM laws WHERE urn = ?', (selected_urn,)
                ).fetchone()
                
                if law:
                    col1, col2 = st.columns([2, 1])
                    
                    with col1:
                        st.subheader(f"📄 {law['title']}")
                        st.write(f"**Anno:** {law['year']} | **Tipo:** {law['type']}")
                        st.write(f"**URN:** `{law['urn']}`")
                        st.write(f"**Stato:** {'✅ Vigente' if law['status'] == 'in_force' else '❌ Abrogata'}")
                        st.write(f"**Articoli:** {law['article_count']}")
                    
                    with col2:
                        st.metric("Importanza", f"{law.get('importance_score', 0):.2f}")
                        st.metric("Citazioni", law.get('cited_by', 0))
        else:
            st.warning("Nessuna legge trovata per i filtri selezionati.")
    except Exception as e:
        st.warning(f"Errore: {e}")


# ─────────────────────────────────────────────────────────────────
# MULTIVIGENTE HISTORICAL ANALYSIS
# ─────────────────────────────────────────────────────────────────

def page_multivigente_explorer():
    """Historical versions of laws over time."""
    st.header("🕰️ Analisi Storica — Multivigente")
    st.write("Traccia come una legge è stata modificata nel tempo")
    
    # This will load multivigente JSONL data if available
    try:
        mv_path = Path('/app/data/processed/laws_multivigente.jsonl')
        if not mv_path.exists():
            mv_path = Path('data/processed/laws_multivigente.jsonl')
        
        if mv_path.exists():
            with open(mv_path) as f:
                lines = [json.loads(line) for line in f]
            
            st.success(f"Dataset multivigente caricato: {len(lines)} versioni storiche")
            
            # Stats
            col1, col2, col3 = st.columns(3)
            col1.metric("Versioni", len(lines))
            
            years = [l.get('year') for l in lines if l.get('year')]
            if years:
                col2.metric("Range", f"{min(years)}-{max(years)}")
            
            urns = set(l.get('urn') for l in lines if l.get('urn'))
            col3.metric("Leggi uniche", len(urns))
            
            st.info("Explorer multivigente — selezionare una legge per tracciare le versioni storiche")
        else:
            st.warning("File multivigente non disponibile nel dataset.")
    except Exception as e:
        st.warning(f"Errore caricamento multivigente: {e}")


# ─────────────────────────────────────────────────────────────────
# ADVANCED ANALYTICS
# ─────────────────────────────────────────────────────────────────

def page_advanced_analytics():
    """Advanced analytics for the lab dataset."""
    st.header("📊 Analitiche Avanzate")
    
    conn = load_enhanced_db()
    if not conn:
        st.error("Database not available")
        return
    
    tab1, tab2, tab3 = st.tabs(["Citazioni", "Domini", "Reti"])
    
    with tab1:
        st.subheader("Analisi Rete Citazionale")
        try:
            # Citation graph stats
            citing_laws = conn.execute(
                'SELECT COUNT(DISTINCT citing_urn) FROM citations'
            ).fetchone()[0]
            cited_laws = conn.execute(
                'SELECT COUNT(DISTINCT cited_urn) FROM citations'
            ).fetchone()[0]
            
            col1, col2 = st.columns(2)
            col1.metric("Leggi che citano", citing_laws)
            col2.metric("Leggi citate", cited_laws)
            
            # Citation distribution
            top_cited = conn.execute('''
                SELECT l.urn, l.title, COUNT(*) as count
                FROM citations c
                JOIN laws l ON l.urn = c.cited_urn
                GROUP BY c.cited_urn
                ORDER BY count DESC
                LIMIT 20
            ''').fetchall()
            
            if top_cited:
                df = pd.DataFrame([
                    {'Legge': d[1][:60], 'Citazioni ricevute': d[2]}
                    for d in top_cited
                ])
                fig = px.bar(df, x='Citazioni ricevute', y='Legge', orientation='h',
                           color='Citazioni ricevute', color_continuous_scale='Viridis')
                st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.warning(f"Errore: {e}")
    
    with tab2:
        st.subheader("Distribuzione Domini Giuridici")
        try:
            domains = conn.execute('''
                SELECT m.domain_cluster AS domain, COUNT(*) as count
                FROM law_metadata m
                WHERE COALESCE(m.domain_cluster, '') != ''
                GROUP BY m.domain_cluster
                ORDER BY count DESC
                LIMIT 15
            ''').fetchall()
            
            if domains:
                df = pd.DataFrame([{'Dominio': d[0], 'Leggi': d[1]} for d in domains])
                fig = px.treemap(df, path=['Dominio'], values='Leggi',
                               color='Leggi', color_continuous_scale='Blues')
                st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.warning(f"Errore: {e}")
    
    with tab3:
        st.subheader("Network di Citazioni")
        st.info("Visualizzazione grafo — selezionare una legge principale")


# ─────────────────────────────────────────────────────────────────
# CORTE COSTITUZIONALE — ENHANCED JURISPRUDENCE PAGE
# ─────────────────────────────────────────────────────────────────

_DECADE_RANGES = {
    "Tutti": (0, 9999),
    "1956–1970": (1956, 1970),
    "1971–1985": (1971, 1985),
    "1986–2000": (1986, 2000),
    "2001–2010": (2001, 2010),
    "2011–2020": (2011, 2020),
    "2021–oggi":  (2021, 9999),
}

_ESITI_OPTIONS = [
    "Tutti", "illegittimità", "inammissibile", "non fondata",
    "fondata", "cessata materia", "manifesta inammissibilità",
    "manifesta infondatezza",
]


def _highlight_excerpt(text: str, query: str, window: int = 200) -> str:
    """Return a short excerpt around the first occurrence of query in text."""
    if not text or not query:
        return ""
    idx = text.lower().find(query.lower())
    if idx == -1:
        return text[:window] + "…"
    start = max(0, idx - window // 2)
    end = min(len(text), idx + window // 2)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    # Bold the match
    lo = query.lower()
    pos = snippet.lower().find(lo)
    if pos != -1:
        snippet = snippet[:pos] + f"**{snippet[pos:pos+len(query)]}**" + snippet[pos+len(query):]
    return snippet


def page_corte_cost():
    """Corte Costituzionale — enhanced jurisprudence analytics & search."""
    st.header("⚖️ Corte Costituzionale — Analisi e Ricerca Giurisprudenza")

    conn = load_enhanced_db()
    if not conn:
        st.error("Database non disponibile.")
        return

    # ── EMPTY STATE ───────────────────────────────────────────────
    try:
        count = conn.execute("SELECT COUNT(*) FROM sentenze").fetchone()[0]
    except Exception:
        count = 0

    if count == 0:
        st.warning(
            "Il database delle sentenze della Corte Costituzionale è vuoto. "
            "Importa le decisioni eseguendo localmente:"
        )
        col_a, col_b = st.columns(2)
        with col_a:
            st.code("python download_sentenze.py --from-index --resume", language="bash")
            st.info(
                "Il downloader usa gli endpoint pubblici di elenco pronunce e scheda pronuncia "
                "del sito ufficiale [cortecostituzionale.it](https://www.cortecostituzionale.it). "
                "Se l'origine blocca il traffico automatico (captcha/anti-bot), esegui da una rete "
                "consentita e poi rideploya il database su HuggingFace con `deploy_hf.py`."
            )
        with col_b:
            st.subheader("Ultime decisioni pubblicate")
            st.markdown("""
| Decisione | Tipo | Anno |
|---|---|---|
| [55/2026](https://www.cortecostituzionale.it/actionSchedaPronuncia.do?param_ecli=ECLI:IT:COST:2026:55) | Sentenza | 2026 |
| [54/2026](https://www.cortecostituzionale.it/actionSchedaPronuncia.do?param_ecli=ECLI:IT:COST:2026:54) | Sentenza | 2026 |
| [52/2026](https://www.cortecostituzionale.it/actionSchedaPronuncia.do?param_ecli=ECLI:IT:COST:2026:52) | Sentenza | 2026 |
| [50/2026](https://www.cortecostituzionale.it/actionSchedaPronuncia.do?param_ecli=ECLI:IT:COST:2026:50) | Ordinanza | 2026 |
""")
        return

    # ── KPI ROW ──────────────────────────────────────────────────
    try:
        stats = conn.execute("""
            SELECT
                COUNT(*) AS tot,
                SUM(tipo = 'Sentenza') AS sentenze,
                SUM(tipo = 'Ordinanza') AS ordinanze,
                SUM(esito LIKE 'illegittimità%') AS illegittimita,
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

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("📋 Decisioni totali", f"{tot:,}")
    k2.metric("📜 Sentenze", f"{sent:,}")
    k3.metric("📄 Ordinanze", f"{ordin:,}")
    k4.metric("🚫 Illegittimità", f"{illegit:,}")
    k5.metric("📅 Periodo", f"{first_y}–{last_y}")

    st.divider()

    # ── TABS ──────────────────────────────────────────────────────
    tab_trend, tab_esiti, tab_artt, tab_search, tab_cross = st.tabs([
        "📅 Trend temporale",
        "📊 Esiti",
        "📜 Articoli Cost.",
        "🔍 Ricerca",
        "🔗 Cross-reference Normattiva",
    ])

    # ── TAB 1: TEMPORAL TREND ─────────────────────────────────────
    with tab_trend:
        st.subheader("Attività decisionale per anno")

        decade_choice = st.selectbox(
            "Filtra periodo:", list(_DECADE_RANGES.keys()), key="cc-decade"
        )
        d_from, d_to = _DECADE_RANGES[decade_choice]

        try:
            year_data = conn.execute("""
                SELECT anno, tipo, COUNT(*) AS cnt
                FROM sentenze
                WHERE anno BETWEEN ? AND ?
                GROUP BY anno, tipo
                ORDER BY anno
            """, (d_from, d_to)).fetchall()
            if year_data:
                df_yr = pd.DataFrame([dict(r) for r in year_data])
                fig_yr = px.bar(
                    df_yr, x="anno", y="cnt", color="tipo",
                    title=f"Decisioni per anno e tipo — {decade_choice}",
                    labels={"anno": "Anno", "cnt": "Decisioni", "tipo": "Tipo"},
                    barmode="stack",
                    color_discrete_map={"Sentenza": "#1976D2", "Ordinanza": "#90CAF9"},
                )
                st.plotly_chart(fig_yr, use_container_width=True)

            # Illegittimità overlay
            illeg_yr = conn.execute("""
                SELECT anno, COUNT(*) AS cnt
                FROM sentenze
                WHERE esito LIKE 'illegittimità%' AND anno BETWEEN ? AND ?
                GROUP BY anno ORDER BY anno
            """, (d_from, d_to)).fetchall()
            if illeg_yr:
                df_il = pd.DataFrame([dict(r) for r in illeg_yr])
                fig_il = px.area(
                    df_il, x="anno", y="cnt",
                    title="Dichiarazioni di illegittimità costituzionale",
                    labels={"anno": "Anno", "cnt": "Dichiarazioni"},
                    color_discrete_sequence=["#f44336"],
                )
                st.plotly_chart(fig_il, use_container_width=True)
        except Exception as e:
            st.warning(f"Errore grafico temporale: {e}")

    # ── TAB 2: ESITI ──────────────────────────────────────────────
    with tab_esiti:
        col_e1, col_e2 = st.columns(2)
        with col_e1:
            st.subheader("Distribuzione degli esiti")
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
                    st.plotly_chart(fig_esito, use_container_width=True)
                else:
                    st.info("Dati esiti non disponibili.")
            except Exception as e:
                st.warning(f"Errore: {e}")

        with col_e2:
            st.subheader("Esiti per tipo di decisione")
            try:
                esito_tipo = conn.execute("""
                    SELECT tipo, esito, COUNT(*) AS cnt
                    FROM sentenze
                    WHERE esito IS NOT NULL AND esito != '' AND tipo IS NOT NULL
                    GROUP BY tipo, esito
                    ORDER BY tipo, cnt DESC
                """).fetchall()
                if esito_tipo:
                    df_et = pd.DataFrame([dict(r) for r in esito_tipo])
                    fig_et = px.bar(
                        df_et, x="tipo", y="cnt", color="esito",
                        title="Esiti per Sentenze vs Ordinanze",
                        labels={"tipo": "Tipo", "cnt": "Decisioni", "esito": "Esito"},
                        barmode="stack",
                    )
                    st.plotly_chart(fig_et, use_container_width=True)
            except Exception as e:
                st.warning(f"Errore: {e}")

    # ── TAB 3: CONSTITUTIONAL ARTICLES ───────────────────────────
    with tab_artt:
        st.subheader("Articoli della Costituzione più invocati")
        try:
            art_rows = conn.execute(
                "SELECT articoli_cost FROM sentenze WHERE articoli_cost IS NOT NULL AND articoli_cost != '[]'"
            ).fetchall()
            art_counter: Counter = Counter()
            for row in art_rows:
                try:
                    arts = json.loads(row[0])
                    art_counter.update(arts)
                except Exception:
                    pass

            if art_counter:
                top_n = st.slider("Numero di articoli da visualizzare:", 5, 50, 20, key="cc-arts-n")
                top_arts = art_counter.most_common(top_n)
                df_arts = pd.DataFrame(top_arts, columns=["Articolo", "Citazioni"])
                df_arts["label"] = "Art. " + df_arts["Articolo"].astype(str)
                fig_arts = px.bar(
                    df_arts, x="Citazioni", y="label", orientation="h",
                    title=f"Top {top_n} articoli costituzionali più richiamati",
                    labels={"Citazioni": "Volte citato", "label": ""},
                    color="Citazioni", color_continuous_scale="Blues",
                )
                fig_arts.update_layout(yaxis={"categoryorder": "total ascending"}, height=max(400, top_n * 22))
                st.plotly_chart(fig_arts, use_container_width=True)

                # Cluster view: group articles by constitutional Part
                st.subheader("Cluster tematico degli articoli invocati")
                _CLUSTERS = {
                    "Principi fondamentali (1–12)": range(1, 13),
                    "Diritti e doveri (13–54)": range(13, 55),
                    "Parlamento (55–82)": range(55, 83),
                    "Governo (92–100)": range(92, 101),
                    "Magistratura (101–113)": range(101, 114),
                    "Regioni e autonomie (114–133)": range(114, 134),
                    "Garanzie Costituzionali (134–139)": range(134, 140),
                }
                cluster_counts = {}
                for label, rng in _CLUSTERS.items():
                    total = sum(art_counter.get(str(i), 0) for i in rng)
                    if total > 0:
                        cluster_counts[label] = total
                if cluster_counts:
                    df_cl = pd.DataFrame(
                        [{"Cluster": k, "Citazioni": v} for k, v in cluster_counts.items()]
                    ).sort_values("Citazioni", ascending=False)
                    fig_cl = px.bar(
                        df_cl, x="Cluster", y="Citazioni",
                        color="Citazioni", color_continuous_scale="Oranges",
                        title="Citazioni per sezione della Costituzione",
                    )
                    fig_cl.update_layout(xaxis_tickangle=-30)
                    st.plotly_chart(fig_cl, use_container_width=True)
            else:
                st.info("Dati sugli articoli non ancora estratti (richiede download completo).")
        except Exception as e:
            st.warning(f"Errore articoli: {e}")

    # ── TAB 4: SEARCH ─────────────────────────────────────────────
    with tab_search:
        st.subheader("Ricerca avanzata nelle decisioni")

        sc1, sc2, sc3, sc4 = st.columns([2, 1, 1, 1])
        with sc1:
            q_text = st.text_input("Cerca nel testo / oggetto", placeholder="es. diritto al lavoro", key="cc-q")
        with sc2:
            q_tipo = st.selectbox("Tipo", ["Tutti", "Sentenza", "Ordinanza"], key="cc-tipo")
        with sc3:
            q_esito = st.selectbox("Esito", _ESITI_OPTIONS, key="cc-esito")
        with sc4:
            q_decade = st.selectbox("Periodo", list(_DECADE_RANGES.keys()), key="cc-s-decade")

        q_df, q_to = _DECADE_RANGES[q_decade]

        where_clauses = ["anno BETWEEN ? AND ?"]
        params: list = [q_df, q_to]
        if q_text.strip():
            where_clauses.append("(LOWER(COALESCE(oggetto,'')) LIKE ? OR LOWER(COALESCE(testo,'')) LIKE ?)")
            t = f"%{q_text.strip().lower()}%"
            params += [t, t]
        if q_tipo != "Tutti":
            where_clauses.append("tipo = ?")
            params.append(q_tipo)
        if q_esito != "Tutti":
            where_clauses.append("esito LIKE ?")
            params.append(f"{q_esito}%")

        try:
            results = conn.execute(f"""
                SELECT ecli, numero, anno, tipo, data_deposito, oggetto, esito, testo, comunicato_url
                FROM sentenze
                WHERE {' AND '.join(where_clauses)}
                ORDER BY anno DESC, numero DESC
                LIMIT 300
            """, params).fetchall()

            st.caption(f"**{len(results):,}** decisioni trovate")

            if results:
                df_res = pd.DataFrame([dict(r) for r in results])

                # Export button
                csv_bytes = df_res.drop(columns=["testo"], errors="ignore").to_csv(index=False).encode("utf-8")
                st.download_button(
                    "⬇️ Esporta risultati (CSV)",
                    data=csv_bytes,
                    file_name="sentenze_cc.csv",
                    mime="text/csv",
                    key="cc-export",
                )

                show_excerpts = st.checkbox("Mostra estratti del testo", value=False, key="cc-excerpts")

                for row in df_res.to_dict("records"):
                    ecli = row["ecli"]
                    url = f"https://www.cortecostituzionale.it/actionSchedaPronuncia.do?param_ecli={ecli}"
                    with st.expander(
                        f"**{row['tipo']} {row['numero']}/{row['anno']}** — {row['esito'] or 'n.d.'} — {str(row['oggetto'] or '')[:80]}"
                    ):
                        col_i1, col_i2 = st.columns(2)
                        col_i1.write(f"**ECLI:** `{ecli}`")
                        col_i1.write(f"**Deposito:** {row['data_deposito'] or 'n.d.'}")
                        col_i2.write(f"**Tipo:** {row['tipo'] or 'n.d.'}")
                        col_i2.write(f"**Esito:** {row['esito'] or 'n.d.'}")
                        if row["oggetto"]:
                            st.write(f"**Oggetto:** {row['oggetto']}")
                        if show_excerpts and q_text.strip() and row.get("testo"):
                            excerpt = _highlight_excerpt(row["testo"], q_text.strip())
                            st.markdown(f"*…{excerpt}…*")
                        st.markdown(f"🔗 [Apri sul sito ufficiale]({url})")
            else:
                st.info("Nessuna decisione trovata con questi criteri.")
        except Exception as e:
            st.error(f"Errore ricerca: {e}")

    # ── TAB 5: CROSS-REFERENCE NORMATTIVA ────────────────────────
    with tab_cross:
        st.subheader("Cross-reference: Normattiva ↔ Corte Costituzionale")
        st.write(
            "Seleziona un articolo costituzionale per vedere quali leggi Normattiva "
            "risultano correlate (tramite norme censurate estratte dalle decisioni)."
        )

        try:
            # Build article list from DB
            art_rows2 = conn.execute(
                "SELECT articoli_cost FROM sentenze WHERE articoli_cost IS NOT NULL AND articoli_cost != '[]'"
            ).fetchall()
            art_counter2: Counter = Counter()
            for row in art_rows2:
                try:
                    art_counter2.update(json.loads(row[0]))
                except Exception:
                    pass

            available_arts = [str(a) for a, _ in art_counter2.most_common(50)]
            if not available_arts:
                st.info("Articoli non disponibili — esegui prima il download completo.")
            else:
                chosen_art = st.selectbox(
                    "Articolo della Costituzione:",
                    available_arts,
                    format_func=lambda x: f"Art. {x}",
                    key="cc-cross-art",
                )

                # Decisions citing this article
                dec_rows = conn.execute("""
                    SELECT ecli, numero, anno, tipo, esito, oggetto, norme_censurate
                    FROM sentenze
                    WHERE articoli_cost LIKE ?
                    ORDER BY anno DESC
                    LIMIT 100
                """, (f'%"{chosen_art}"%',)).fetchall()

                st.markdown(f"**{len(dec_rows):,}** decisioni invocano Art. {chosen_art}")

                # Extract cited norms (URN-like strings in norme_censurate)
                norm_counter: Counter = Counter()
                for dr in dec_rows:
                    try:
                        norme = json.loads(dr["norme_censurate"] or "[]")
                        norm_counter.update(norme)
                    except Exception:
                        pass

                if norm_counter:
                    st.subheader("Norme più censurate nelle decisioni")
                    top_norme = norm_counter.most_common(15)
                    df_norme = pd.DataFrame(top_norme, columns=["Norma", "Citazioni"])
                    fig_norme = px.bar(
                        df_norme, x="Citazioni", y="Norma", orientation="h",
                        title=f"Norme censurate nelle decisioni che invocano Art. {chosen_art}",
                        color="Citazioni", color_continuous_scale="Reds",
                    )
                    fig_norme.update_layout(yaxis={"categoryorder": "total ascending"})
                    st.plotly_chart(fig_norme, use_container_width=True)

                    # Try to match norme to Normattiva laws table
                    st.subheader("Leggi Normattiva correlate")
                    matched = []
                    for norma, cnt in top_norme:
                        try:
                            hit = conn.execute(
                                "SELECT urn, title, type, date FROM laws WHERE urn LIKE ? LIMIT 1",
                                (f"%{norma}%",)
                            ).fetchone()
                            if hit:
                                matched.append({
                                    "Norma CC": norma,
                                    "Citazioni": cnt,
                                    "URN": hit["urn"],
                                    "Titolo": hit["title"],
                                    "Tipo": hit["type"],
                                    "Data": hit["date"],
                                })
                        except Exception:
                            pass
                    if matched:
                        df_match = pd.DataFrame(matched)
                        st.dataframe(df_match, use_container_width=True, hide_index=True)
                    else:
                        st.info("Nessuna corrispondenza trovata in Normattiva per le norme censurate.")

                # Show decisions list
                if dec_rows:
                    st.subheader(f"Decisioni che invocano Art. {chosen_art}")
                    df_dec = pd.DataFrame([dict(r) for r in dec_rows])
                    display_cols = ["anno", "numero", "tipo", "esito", "oggetto"]
                    df_dec_disp = df_dec[[c for c in display_cols if c in df_dec.columns]]
                    df_dec_disp = df_dec_disp.rename(columns={
                        "anno": "Anno", "numero": "N.", "tipo": "Tipo",
                        "esito": "Esito", "oggetto": "Oggetto",
                    })
                    st.dataframe(df_dec_disp, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning(f"Errore cross-reference: {e}")


# ─────────────────────────────────────────────────────────────────
# GIUSTIZIA AMMINISTRATIVA (OpenGA)
# ─────────────────────────────────────────────────────────────────

def page_openga():
    """Giustizia Amministrativa — OpenGA dataset analytics & search."""
    st.header("🏛️ Giustizia Amministrativa — OpenGA")
    st.caption(
        "Sentenze, ordinanze e pareri di Consiglio di Stato, CGA Sicilia e Tribunali Amministrativi Regionali. "
        "Fonte: [OpenGA](https://openga.giustizia-amministrativa.it) — Licenza CC-BY 4.0"
    )

    conn = load_enhanced_db()
    if not conn:
        st.error("Database non disponibile.")
        return

    # ── CHECK DATA AVAILABILITY ───────────────────────────────────
    try:
        total_sent = conn.execute("SELECT COUNT(*) FROM openga_sentenze").fetchone()[0]
        total_cat = conn.execute("SELECT COUNT(*) FROM openga_catalog").fetchone()[0]
    except Exception:
        total_sent = 0
        total_cat = 0

    if total_sent == 0:
        st.info(
            "**Dati OpenGA non ancora caricati nel database.**\n\n"
            "Per popolare il database con i dati della giustizia amministrativa, esegui localmente:\n"
            "```bash\n"
            "python download_openga.py --download --types sentenze ordinanze pareri\n"
            "```\n"
            "Oppure solo il catalogo (senza download delle decisioni):\n"
            "```bash\n"
            "python download_openga.py --catalog\n"
            "```\n\n"
            "**Fonte**: https://openga.giustizia-amministrativa.it — 436 dataset, CC-BY 4.0"
        )

        if total_cat > 0:
            st.divider()
            st.subheader(f"📋 Catalogo OpenGA ({total_cat} dataset)")
            cat_df = pd.read_sql_query(
                "SELECT court, dataset_type, COUNT(*) as n FROM openga_catalog GROUP BY court, dataset_type ORDER BY court",
                conn,
            )
            st.dataframe(cat_df, use_container_width=True, hide_index=True)
        return

    # ── KPI ROW ───────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    courts_n = conn.execute("SELECT COUNT(DISTINCT court) FROM openga_sentenze").fetchone()[0]
    datasets_n = total_cat
    years_n = conn.execute("SELECT COUNT(DISTINCT anno) FROM openga_sentenze WHERE anno IS NOT NULL").fetchone()[0]
    k1.metric("Decisioni", f"{total_sent:,}")
    k2.metric("Organi giudicanti", f"{courts_n}")
    k3.metric("Dataset OpenGA", f"{datasets_n}")
    k4.metric("Anni coperti", f"{years_n}")

    st.divider()

    tab1, tab2, tab3 = st.tabs(["📊 Analitiche", "🔍 Ricerca", "📋 Catalogo"])

    # ── TAB 1: ANALYTICS ──────────────────────────────────────────
    with tab1:
        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Decisioni per Organo Giudicante")
            by_court = pd.read_sql_query(
                "SELECT court, COUNT(*) as n FROM openga_sentenze GROUP BY court ORDER BY n DESC LIMIT 20",
                conn,
            )
            if not by_court.empty:
                fig = px.bar(
                    by_court,
                    x="n",
                    y="court",
                    orientation="h",
                    labels={"n": "Decisioni", "court": "Organo"},
                    color="n",
                    color_continuous_scale="Blues",
                )
                fig.update_layout(height=500, showlegend=False, yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.subheader("Tipo di Atto")
            try:
                by_type = pd.read_sql_query(
                    "SELECT c.dataset_type as tipo, COUNT(*) as n "
                    "FROM openga_sentenze s JOIN openga_catalog c ON s.package_id=c.package_id "
                    "GROUP BY c.dataset_type ORDER BY n DESC",
                    conn,
                )
                if not by_type.empty:
                    fig2 = px.pie(by_type, values="n", names="tipo", hole=0.4, color_discrete_sequence=px.colors.qualitative.Set2)
                    fig2.update_layout(height=400)
                    st.plotly_chart(fig2, use_container_width=True)
            except Exception:
                st.info("Dati tipo non disponibili.")

        # Temporal trend
        st.subheader("Trend Temporale")
        try:
            trend = pd.read_sql_query(
                "SELECT anno, COUNT(*) as n FROM openga_sentenze WHERE anno BETWEEN 1990 AND 2030 GROUP BY anno ORDER BY anno",
                conn,
            )
            if not trend.empty:
                fig3 = px.area(
                    trend, x="anno", y="n",
                    labels={"anno": "Anno", "n": "Decisioni"},
                    color_discrete_sequence=["#2196F3"],
                )
                fig3.update_layout(height=300)
                st.plotly_chart(fig3, use_container_width=True)
        except Exception:
            st.info("Dati temporali non disponibili.")

        # Court × year heatmap (top 10 courts)
        st.subheader("Mappa Calore — Organo × Anno")
        try:
            top_courts = [r[0] for r in conn.execute(
                "SELECT court, COUNT(*) as n FROM openga_sentenze GROUP BY court ORDER BY n DESC LIMIT 10"
            ).fetchall()]
            if top_courts and len(top_courts) > 1:
                placeholders = ",".join("?" * len(top_courts))
                heatmap_df = pd.read_sql_query(
                    f"SELECT court, anno, COUNT(*) as n FROM openga_sentenze "
                    f"WHERE court IN ({placeholders}) AND anno BETWEEN 1990 AND 2030 "
                    f"GROUP BY court, anno",
                    conn,
                    params=top_courts,
                )
                if not heatmap_df.empty:
                    pivot = heatmap_df.pivot(index="court", columns="anno", values="n").fillna(0)
                    fig4 = px.imshow(pivot, aspect="auto", color_continuous_scale="YlOrRd",
                                     labels={"x": "Anno", "y": "Organo", "color": "Decisioni"})
                    fig4.update_layout(height=400)
                    st.plotly_chart(fig4, use_container_width=True)
        except Exception as e:
            st.info(f"Heatmap non disponibile: {e}")

    # ── TAB 2: SEARCH ─────────────────────────────────────────────
    with tab2:
        st.subheader("Ricerca Decisioni Amministrative")

        scol1, scol2, scol3 = st.columns([2, 2, 1])
        with scol1:
            query_text = st.text_input("Ricerca per oggetto / materia", placeholder="appalto, esproprio, silenzio inadempimento...")
        with scol2:
            courts_list = ["Tutti"] + [r[0] for r in conn.execute(
                "SELECT DISTINCT court FROM openga_sentenze ORDER BY court"
            ).fetchall()]
            selected_court = st.selectbox("Organo", courts_list)
        with scol3:
            year_filter = st.number_input("Anno (0=tutti)", min_value=0, max_value=2030, value=0, step=1)

        where_clauses = []
        params: list = []
        if query_text.strip():
            where_clauses.append("(LOWER(oggetto) LIKE ? OR LOWER(esito) LIKE ?)")
            params += [f"%{query_text.lower()}%", f"%{query_text.lower()}%"]
        if selected_court != "Tutti":
            where_clauses.append("court = ?")
            params.append(selected_court)
        if year_filter:
            where_clauses.append("anno = ?")
            params.append(year_filter)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        try:
            results = pd.read_sql_query(
                f"SELECT court, anno, numero, data_deposito, sezione, oggetto, esito "
                f"FROM openga_sentenze {where_sql} ORDER BY anno DESC, numero LIMIT 200",
                conn,
                params=params,
            )
            st.write(f"**{len(results):,}** risultati (max 200)")
            if not results.empty:
                results.columns = ["Organo", "Anno", "Numero", "Deposito", "Sezione", "Oggetto", "Esito"]
                st.dataframe(results, use_container_width=True, hide_index=True, height=500)
        except Exception as e:
            st.error(f"Errore ricerca: {e}")

    # ── TAB 3: CATALOG ────────────────────────────────────────────
    with tab3:
        st.subheader(f"Catalogo Dataset OpenGA ({total_cat} dataset)")
        try:
            cat_df = pd.read_sql_query(
                "SELECT title, court, dataset_type, resource_format, record_count, last_updated "
                "FROM openga_catalog ORDER BY court, dataset_type",
                conn,
            )
            cat_df.columns = ["Titolo", "Organo", "Tipo", "Formato", "Records", "Aggiornato"]
            st.dataframe(cat_df, use_container_width=True, hide_index=True, height=600)
        except Exception as e:
            st.error(f"Errore caricamento catalogo: {e}")


# ─────────────────────────────────────────────────────────────────
# MAIN NAVIGATION
# ─────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Italian Legal Lab — Multi-Source Legal Research",
        page_icon="⚖️",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    pages = {
        "🔬 Lab Dashboard": page_lab_dashboard,
        "🔍 Ricerca Integrata": page_integrated_search,
        "📖 Esplora Leggi": page_law_jurisprudence_explorer,
        "🕰️ Multivigente": page_multivigente_explorer,
        "📊 Analitiche": page_advanced_analytics,
        "⚖️ Corte Costituzionale": page_corte_cost,
        "🏛️ Giustizia Amministrativa": page_openga,
    }
    
    st.sidebar.title("⚖️ Italian Legal Lab")
    st.sidebar.write("Multi-Source Italian Legal Research Platform")
    st.sidebar.divider()
    
    page = st.sidebar.radio("Naviga:", list(pages.keys()), label_visibility="collapsed")
    
    pages[page]()
    
    st.sidebar.divider()
    st.sidebar.info(
        "**Italian Legal Lab** integra il corpus Normattiva completo, "
        "le decisioni della Corte Costituzionale (1956–2026), "
        "la giurisprudenza amministrativa di Consiglio di Stato e TAR (OpenGA), "
        "e una base pronta per Cassazione, diritto UE e altre fonti."
    )


if __name__ == "__main__":
    main()
