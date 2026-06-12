"""Rich explorer for the full Normattiva dataset."""

from __future__ import annotations

from collections import Counter
from typing import Dict, List

import pandas as pd
import plotly.express as px
import streamlit as st


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _status_label(value: str) -> str:
    v = str(value or "").strip().lower()
    if v in {"in_force", "vigente", "v"}:
        return "in_force"
    if v in {"abrogated", "abrogato", "abrogata", "a"}:
        return "abrogated"
    return v or "unknown"


def render_dataset_explorer_page(db, laws: List[Dict]):
    st.header("🗂️ Esplora Tutto il Dataset")
    st.caption(
        "Vista completa del corpus: filtri combinati, distribuzioni e campioni navigabili "
        "per aiutare cittadini, studenti e professionisti a orientarsi velocemente."
    )

    if not laws:
        st.warning("Nessun dato disponibile nel dataset.")
        return

    df = pd.DataFrame(laws)
    if df.empty:
        st.warning("Dataset vuoto.")
        return

    # Normalization for robust filtering.
    for col in ["title", "type", "urn", "status", "year", "date", "article_count", "text_length", "importance_score"]:
        if col not in df.columns:
            df[col] = None

    df["status_norm"] = df["status"].apply(_status_label)
    df["year_int"] = df["year"].apply(lambda x: _safe_int(x, 0))
    df["article_count"] = df["article_count"].apply(lambda x: _safe_int(x, 0))
    df["text_length"] = df["text_length"].apply(lambda x: _safe_int(x, 0))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Leggi totali", f"{len(df):,}")
    c2.metric("In vigore", f"{int((df['status_norm'] == 'in_force').sum()):,}")
    c3.metric("Abrogate", f"{int((df['status_norm'] == 'abrogated').sum()):,}")
    c4.metric("Tipologie", f"{df['type'].fillna('N/A').nunique():,}")

    st.divider()
    st.subheader("Filtri")

    f1, f2, f3, f4 = st.columns(4)
    with f1:
        q = st.text_input("Parole chiave titolo/URN", placeholder="es. lavoro, iva, costituzione")
    with f2:
        types = sorted(x for x in df["type"].dropna().astype(str).unique().tolist() if x.strip())
        selected_types = st.multiselect("Tipologia atto", options=types, default=[])
    with f3:
        selected_status = st.multiselect(
            "Status",
            options=["in_force", "abrogated", "unknown"],
            default=["in_force", "abrogated", "unknown"],
        )
    with f4:
        min_y = max(1800, int(df["year_int"].replace(0, pd.NA).dropna().min() or 1800))
        max_y = int(df["year_int"].max() or 2100)
        year_range = st.slider("Intervallo anni", min_value=min_y, max_value=max(max_y, min_y), value=(min_y, max(max_y, min_y)))

    mask = (df["year_int"] >= year_range[0]) & (df["year_int"] <= year_range[1])
    if q:
        ql = q.lower()
        mask &= (df["title"].fillna("").str.lower().str.contains(ql, regex=False) | df["urn"].fillna("").str.lower().str.contains(ql, regex=False))
    if selected_types:
        mask &= df["type"].astype(str).isin(selected_types)
    if selected_status:
        mask &= df["status_norm"].isin(selected_status)

    view = df[mask].copy().sort_values(["year_int", "importance_score"], ascending=[False, False])
    st.caption(f"Risultati filtrati: {len(view):,} su {len(df):,} leggi")

    st.divider()
    g1, g2 = st.columns(2)
    with g1:
        if not view.empty:
            yc = view[view["year_int"] > 0].groupby("year_int").size().reset_index(name="count")
            fig = px.area(yc, x="year_int", y="count", title="Distribuzione temporale (filtri attivi)")
            st.plotly_chart(fig, use_container_width=True)
    with g2:
        if not view.empty:
            tc = Counter(view["type"].fillna("N/A").astype(str).tolist())
            top = pd.DataFrame({"type": list(tc.keys()), "count": list(tc.values())}).sort_values("count", ascending=False).head(15)
            fig = px.bar(top, x="type", y="count", title="Top tipologie")
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("Tabella esplorabile")
    preview_cols = ["year", "date", "type", "status_norm", "title", "urn", "article_count", "text_length", "importance_score"]
    st.dataframe(
        view[preview_cols].head(1000),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Analisi rapida del campione")
    top_n = st.slider("Numero record per analisi testuale rapida", 20, 500, 120, 20)
    sample = view.head(top_n)
    if sample.empty:
        st.info("Nessun record nel campione.")
        return

    lengths = sample["text_length"].astype(int)
    a1, a2, a3 = st.columns(3)
    a1.metric("Lunghezza media testo", f"{int(lengths.mean()):,} caratteri")
    a2.metric("Articoli medi", f"{sample['article_count'].astype(int).mean():.1f}")
    a3.metric("Importanza media", f"{sample['importance_score'].fillna(0).astype(float).mean():.4f}")

    st.caption("Suggerimento: usa questa pagina per individuare cluster e periodi; poi apri Scheda Legge per il dettaglio normativo.")
