#!/usr/bin/env python3
"""Streamlit pages for institutional people and source registry views."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from core.institutional import (
    DEFAULT_SNAPSHOT_PATH,
    collect_endpoint_statuses,
    collect_institutional_snapshot,
    load_laws_institutional_summary,
    load_snapshot,
    registry_rows,
)

SUMMARY_CSV_PATH = Path("data/laws_summary.csv")
REAL_DB_MIN_BYTES = 1024 * 1024


def _db_path(db: Any) -> str:
    return str(getattr(db, "db_path", Path("data/laws.db")))


def _usable_db(db_path: str) -> bool:
    path = Path(db_path)
    return path.exists() and path.stat().st_size >= REAL_DB_MIN_BYTES


@st.cache_data(ttl=1800, show_spinner=False)
def _load_live_snapshot(db_path: str, include_endpoint_status: bool) -> dict[str, Any]:
    return collect_institutional_snapshot(
        db_path=db_path,
        include_endpoint_status=include_endpoint_status,
    )


@st.cache_data(ttl=900, show_spinner=False)
def _load_laws_summary(db_path: str) -> dict[str, Any]:
    return load_laws_institutional_summary(db_path)


@st.cache_data(ttl=1800, show_spinner=False)
def _load_endpoint_statuses() -> list[dict[str, Any]]:
    return collect_endpoint_statuses()


@st.cache_data(ttl=1800, show_spinner=False)
def _load_summary_corpus(csv_path: str) -> pd.DataFrame:
    path = Path(csv_path)
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, usecols=["URN", "Title", "Type", "Date", "Year", "Status"])
    frame = frame.rename(columns={
        "URN": "urn",
        "Title": "title",
        "Type": "type",
        "Date": "date",
        "Year": "year",
        "Status": "status",
    })
    frame["date_ts"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["year"] = pd.to_numeric(frame["year"], errors="coerce")
    return frame


@st.cache_data(ttl=900, show_spinner=False)
def _laws_for_period(db_path: str, start_date: str, end_date: str, limit: int = 25) -> dict[str, Any]:
    start_ts = pd.to_datetime(start_date, errors="coerce")
    end_ts = pd.to_datetime(end_date, errors="coerce")
    if pd.isna(start_ts):
        return {"rows": [], "total": 0, "source": "unavailable"}
    if pd.isna(end_ts):
        end_ts = pd.Timestamp.now().normalize()

    start_iso = start_ts.strftime("%Y-%m-%d")
    end_iso = end_ts.strftime("%Y-%m-%d")
    start_year = int(start_ts.year)
    end_year = int(end_ts.year)

    if _usable_db(db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            where_clause = (
                "((date IS NOT NULL AND TRIM(date) != '' AND DATE(date) BETWEEN DATE(?) AND DATE(?)) "
                "OR ((date IS NULL OR TRIM(date) = '') AND year BETWEEN ? AND ?))"
            )
            total = conn.execute(
                f"SELECT COUNT(*) FROM laws WHERE {where_clause}",
                (start_iso, end_iso, start_year, end_year),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT urn, title, type, year, date, status FROM laws "
                f"WHERE {where_clause} "
                "ORDER BY COALESCE(NULLIF(date, ''), printf('%04d-12-31', year)) DESC, year DESC LIMIT ?",
                (start_iso, end_iso, start_year, end_year, limit),
            ).fetchall()
            return {
                "rows": [dict(row) for row in rows],
                "total": int(total),
                "source": "db",
            }
        except Exception:
            pass
        finally:
            conn.close()

    frame = _load_summary_corpus(str(SUMMARY_CSV_PATH))
    if frame.empty:
        return {"rows": [], "total": 0, "source": "unavailable"}

    mask = (
        (frame["date_ts"].notna() & frame["date_ts"].between(start_ts, end_ts))
        | (frame["date_ts"].isna() & frame["year"].between(start_year, end_year))
    )
    filtered = frame.loc[mask].sort_values(["date_ts", "year"], ascending=False)
    preview = filtered.head(limit).drop(columns=["date_ts"], errors="ignore")
    return {
        "rows": preview.to_dict("records"),
        "total": int(len(filtered)),
        "source": "summary_csv",
    }


@st.cache_data(ttl=900, show_spinner=False)
def _laws_for_legislature(db_path: str, legislature_id: int, limit: int = 50) -> list[dict[str, Any]]:
    if not _usable_db(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT urn, title, type, year, date, government, status "
            "FROM laws WHERE legislature_id = ? "
            "ORDER BY date DESC, year DESC LIMIT ?",
            (legislature_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@st.cache_data(ttl=900, show_spinner=False)
def _laws_for_government(db_path: str, government: str, limit: int = 50) -> list[dict[str, Any]]:
    if not _usable_db(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT urn, title, type, year, date, legislature_id, status "
            "FROM laws WHERE government = ? "
            "ORDER BY date DESC, year DESC LIMIT ?",
            (government, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@st.cache_data(ttl=900, show_spinner=False)
def _recent_institutional_laws(db_path: str, limit: int = 60) -> list[dict[str, Any]]:
    if not _usable_db(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT urn, title, type, year, date, government, legislature_id, era, status "
            "FROM laws "
            "WHERE legislature_id IS NOT NULL "
            "   OR (government IS NOT NULL AND TRIM(government) != '') "
            "ORDER BY date DESC, year DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@st.cache_data(ttl=600, show_spinner=False)
def _search_laws_by_institutional_term(db_path: str, term: str, limit: int = 30) -> list[dict[str, Any]]:
    if not _usable_db(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        q = f"%{term.strip()}%"
        rows = conn.execute(
            "SELECT urn, title, type, year, date, government, legislature_id, status "
            "FROM laws WHERE title LIKE ? OR text LIKE ? "
            "ORDER BY year DESC, date DESC LIMIT ?",
            (q, q, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()



def _rows_frame(rows: list[dict[str, Any]], rename: dict[str, str] | None = None) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    if rename:
        frame = frame.rename(columns=rename)
    return frame



def _snapshot_for_page(db: Any, prefer_live: bool, include_status: bool) -> tuple[dict[str, Any], str]:
    db_path = _db_path(db)
    saved = load_snapshot(DEFAULT_SNAPSHOT_PATH)
    if prefer_live or not saved:
        data = _load_live_snapshot(db_path, include_status)
        source_label = "fonti live ufficiali"
    else:
        data = saved
        if include_status and "endpoint_status" not in data:
            data = dict(data)
            data["endpoint_status"] = _load_endpoint_statuses()
        source_label = "snapshot locale"
    if not data.get("laws_summary"):
        data["laws_summary"] = _load_laws_summary(db_path)
    return data, source_label



def _government_role_chart(rows: list[dict[str, Any]]) -> None:
    frame = _rows_frame(rows)
    if frame.empty:
        return
    summary = frame.groupby("role_bucket").size().reset_index(name="count")
    fig = px.bar(
        summary,
        x="role_bucket",
        y="count",
        title="Composizione del Governo per ruolo",
        color="role_bucket",
    )
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig, use_container_width=True)



def _laws_by_legislature_chart(summary: dict[str, Any]) -> None:
    frame = _rows_frame(summary.get("by_legislature", []))
    if frame.empty:
        return
    fig = px.bar(
        frame,
        x="legislature_id",
        y="law_count",
        title="Leggi nel dataset per legislatura",
        labels={"legislature_id": "Legislatura", "law_count": "Leggi"},
        color="law_count",
        color_continuous_scale="Blues",
    )
    st.plotly_chart(fig, use_container_width=True)



def _laws_by_government_chart(summary: dict[str, Any]) -> None:
    frame = _rows_frame(summary.get("by_government", []))
    if frame.empty:
        return
    fig = px.bar(
        frame.head(15),
        x="government",
        y="law_count",
        title="Top governi per numero di leggi nel dataset",
        labels={"government": "Governo", "law_count": "Leggi"},
        color="law_count",
        color_continuous_scale="Teal",
    )
    st.plotly_chart(fig, use_container_width=True)



def _person_name(row: dict[str, Any], first_key: str, last_key: str) -> str:
    return " ".join(part for part in [row.get(first_key, ""), row.get(last_key, "")] if part).strip()


def _format_compact_date(value: str | None) -> str:
    if not value:
        return ""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 8:
        return f"{digits[6:8]}/{digits[4:6]}/{digits[:4]}"
    return str(value)


def _compact_date_to_ts(value: str | None):
    if not value:
        return pd.NaT
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 8:
        return pd.to_datetime(digits, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(value, errors="coerce")


def _prepare_legislature_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    for row in rows:
        title = row.get("title", "")
        match = re.search(r"Legislatura\s+([IVXLCDM]+)", title, re.IGNORECASE)
        label = match.group(1).upper() if match else ("Costituente" if "costituente" in title.lower() else title)
        start_ts = _compact_date_to_ts(row.get("start"))
        end_ts = _compact_date_to_ts(row.get("end"))
        start_iso = start_ts.strftime("%Y-%m-%d") if pd.notna(start_ts) else ""
        end_iso = end_ts.strftime("%Y-%m-%d") if pd.notna(end_ts) else ""
        prepared.append({
            "label": label,
            "title": title,
            "start_display": _format_compact_date(row.get("start")),
            "end_display": _format_compact_date(row.get("end")) or "In corso",
            "start_ts": start_ts,
            "end_ts": end_ts,
            "end_plot": end_ts if pd.notna(end_ts) else pd.Timestamp.now().normalize(),
            "sparql_url": row.get("leg", ""),
            "period_start": start_iso,
            "period_end": end_iso,
            "selection_key": row.get("leg", "") or title,
            "selection_label": f"{label} ({_format_compact_date(row.get('start')) or 'N/D'} - {_format_compact_date(row.get('end')) or 'In corso'})",
        })
    prepared.sort(key=lambda item: item.get("start_ts") if pd.notna(item.get("start_ts")) else pd.Timestamp.min)
    return prepared


def _prepare_camera_government_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    for row in rows:
        title = row.get("title", "")
        start_ts = _compact_date_to_ts(row.get("start"))
        end_ts = _compact_date_to_ts(row.get("end"))
        start_iso = start_ts.strftime("%Y-%m-%d") if pd.notna(start_ts) else ""
        end_iso = end_ts.strftime("%Y-%m-%d") if pd.notna(end_ts) else ""
        prepared.append({
            "government": title,
            "start_display": _format_compact_date(row.get("start")),
            "end_display": _format_compact_date(row.get("end")) or "In corso",
            "start_ts": start_ts,
            "end_ts": end_ts,
            "end_plot": end_ts if pd.notna(end_ts) else pd.Timestamp.now().normalize(),
            "sparql_url": row.get("gov", ""),
            "period_start": start_iso,
            "period_end": end_iso,
            "selection_key": row.get("gov", "") or title,
            "selection_label": f"{title} ({_format_compact_date(row.get('start')) or 'N/D'} - {_format_compact_date(row.get('end')) or 'In corso'})",
        })
    prepared.sort(key=lambda item: item.get("start_ts") if pd.notna(item.get("start_ts")) else pd.Timestamp.min)
    return prepared


def _extract_browser_legislature(url: str) -> str:
    match = re.search(r"/([xivlcdm]+)-legislatura", url or "", re.IGNORECASE)
    if match:
        return f"{match.group(1).upper()} Legislatura"
    if "governo-meloni" in (url or "").lower():
        return "XIX Legislatura"
    return ""


def _prepare_browser_government_history(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    for row in rows:
        url = (row.get("url", "") or "").strip()
        start_ts = _compact_date_to_ts(row.get("start_date"))
        end_ts = _compact_date_to_ts(row.get("end_date"))
        start_display = _format_compact_date(row.get("start_date"))
        end_display = _format_compact_date(row.get("end_date")) or "In corso"
        prepared.append({
            "government": (row.get("name", "") or "").strip(),
            "period_label": row.get("period_text", ""),
            "legislature_hint": row.get("legislature", "") or _extract_browser_legislature(url),
            "prime_minister": row.get("prime_minister", ""),
            "presidency_undersecretaries": row.get("presidency_undersecretaries", []),
            "ministries_without_portfolio": row.get("ministries_without_portfolio", []),
            "ministries_with_portfolio": row.get("ministries_with_portfolio", []),
            "ministries_without_portfolio_count": row.get("ministries_without_portfolio_count", 0),
            "ministries_with_portfolio_count": row.get("ministries_with_portfolio_count", 0),
            "composition_changes": row.get("composition_changes", []),
            "composition_change_count": row.get("composition_change_count", 0),
            "start_display": start_display,
            "end_display": end_display,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "end_plot": end_ts if pd.notna(end_ts) else pd.Timestamp.now().normalize(),
            "period_start": start_ts.strftime("%Y-%m-%d") if pd.notna(start_ts) else "",
            "period_end": end_ts.strftime("%Y-%m-%d") if pd.notna(end_ts) else "",
            "official_page": url,
            "selection_key": url or row.get("name", ""),
            "selection_label": f"{(row.get('name', '') or '').strip()} ({start_display or 'N/D'} - {end_display})",
        })
    prepared.reverse()
    return prepared


def _selected_custom_value(event: Any, custom_index: int = 0) -> str | None:
    if event is None:
        return None
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")
    if selection is None:
        return None
    points = getattr(selection, "points", None)
    if points is None and isinstance(selection, dict):
        points = selection.get("points")
    if not points:
        return None
    point = points[0]
    custom_data = point.get("customdata") if isinstance(point, dict) else getattr(point, "customdata", None)
    if isinstance(custom_data, (list, tuple)) and len(custom_data) > custom_index:
        value = custom_data[custom_index]
        return str(value) if value is not None else None
    if custom_index == 0 and custom_data is not None:
        return str(custom_data)
    return None


def _render_timeline(
    frame: pd.DataFrame,
    y_col: str,
    title: str,
    hover_cols: list[str],
    chart_key: str | None = None,
    selection_key_col: str | None = None,
) -> str | None:
    if frame.empty:
        return None
    plot_frame = frame.dropna(subset=["start_ts"]).copy()
    if plot_frame.empty:
        return None
    fig = px.timeline(
        plot_frame,
        x_start="start_ts",
        x_end="end_plot",
        y=y_col,
        hover_data=hover_cols,
        title=title,
        custom_data=[selection_key_col] if selection_key_col else None,
    )
    fig.update_layout(clickmode="event+select")
    fig.update_yaxes(autorange="reversed")
    # Keep chart rendering stable across Streamlit versions and avoid rerun loops
    # that can prevent users from completing downstream selections.
    if chart_key:
        st.plotly_chart(fig, use_container_width=True, key=chart_key)
    else:
        st.plotly_chart(fig, use_container_width=True)
    return None


def _select_history_row(rows: list[dict[str, Any]], widget_key: str, prompt: str, chart_selection: str | None) -> dict[str, Any] | None:
    options = [""] + [str(row.get("selection_key", "")) for row in rows if row.get("selection_key")]
    labels = {str(row.get("selection_key", "")): row.get("selection_label", str(row.get("selection_key", ""))) for row in rows}
    if chart_selection and chart_selection in labels:
        st.session_state[widget_key] = chart_selection
    if widget_key not in st.session_state or st.session_state.get(widget_key) not in options:
        st.session_state[widget_key] = ""
    selected_key = st.selectbox(
        prompt,
        options,
        format_func=lambda value: "Seleziona un periodo..." if not value else labels.get(value, value),
        key=widget_key,
    )
    return next((row for row in rows if str(row.get("selection_key", "")) == str(selected_key)), None)


def _render_period_law_list(rows: list[dict[str, Any]], key_prefix: str) -> None:
    for idx, row in enumerate(rows[:12]):
        urn = row.get("urn", "")
        when = row.get("date") or row.get("year") or "N/D"
        law_type = row.get("type") or "N/D"
        title = row.get("title") or "Senza titolo"
        left, mid, content, action = st.columns([1.3, 1.5, 6, 1.2])
        left.markdown(f"**{when}**")
        mid.markdown(f"`{law_type}`")
        content.markdown(title)
        if urn and action.button("Scheda", key=f"{key_prefix}_open_{idx}_{urn}", use_container_width=True):
            st.session_state["detail_urn"] = urn
            st.session_state["goto_page"] = "📖 Scheda Legge"
            st.rerun()


def _render_period_law_panel(db: Any, label: str, start_date: str, end_date: str, key_prefix: str) -> None:
    if not start_date:
        st.info("Periodo ufficiale non disponibile per filtrare il corpus Normattiva.")
        return

    result = _laws_for_period(_db_path(db), start_date, end_date, limit=25)
    source = result.get("source", "unavailable")
    source_label = "database principale" if source == "db" else "summary CSV" if source == "summary_csv" else "nessuna fonte locale"

    st.markdown(f"**Corpus Normattiva nel periodo di {label}**")
    st.caption(
        f"Filtro cronologico dal {start_date} al {end_date or pd.Timestamp.now().normalize().strftime('%Y-%m-%d')} "
        f"• Fonte: {source_label}"
    )
    st.metric("Norme nel periodo", f"{int(result.get('total', 0)):,}")
    rows = result.get("rows", [])
    if rows:
        _render_period_law_list(rows, key_prefix)
    else:
        st.info("Nessuna norma trovata nel corpus locale per questo intervallo cronologico.")


def _ministry_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    prepared = []
    for row in rows:
        prepared.append({
            "Area": row.get("portfolio", ""),
            "Ministro": row.get("minister", ""),
            "Vice ministri": ", ".join(row.get("vice_ministers", [])),
            "Sottosegretari": ", ".join(row.get("undersecretaries", [])),
            "Note": "; ".join(row.get("composition_notes", [])[:4]),
        })
    return _rows_frame(prepared)


def _render_browser_government_detail(row: dict[str, Any], db: Any) -> None:
    st.markdown(f"**{row.get('government', 'Governo')}**")
    left, right = st.columns([2, 3])
    with left:
        st.markdown(f"**Periodo ufficiale**: {row.get('start_display', 'N/D')} - {row.get('end_display', 'In corso')}")
        st.markdown(f"**Legislatura**: {row.get('legislature_hint', 'N/D')}")
        st.markdown(f"**Presidente del Consiglio**: {row.get('prime_minister', 'N/D') or 'N/D'}")
        if row.get("official_page"):
            st.markdown(f"**Pagina ufficiale**: [{row.get('official_page')}]({row.get('official_page')})")
    with right:
        c1, c2, c3 = st.columns(3)
        c1.metric("Ministri senza portafoglio", int(row.get("ministries_without_portfolio_count", 0)))
        c2.metric("Ministeri", int(row.get("ministries_with_portfolio_count", 0)))
        c3.metric("Cambi composizione", int(row.get("composition_change_count", 0)))

    if row.get("presidency_undersecretaries"):
        st.markdown("**Sottosegretari alla Presidenza del Consiglio**")
        st.write(", ".join(row.get("presidency_undersecretaries", [])))

    without_frame = _ministry_frame(row.get("ministries_without_portfolio", []))
    with_frame = _ministry_frame(row.get("ministries_with_portfolio", []))
    col_without, col_with = st.columns(2)
    with col_without:
        if not without_frame.empty:
            st.markdown("**Ministri senza portafoglio**")
            st.dataframe(without_frame, use_container_width=True, hide_index=True)
    with col_with:
        if not with_frame.empty:
            st.markdown("**Ministeri con portafoglio**")
            st.dataframe(with_frame, use_container_width=True, hide_index=True)

    if row.get("composition_changes"):
        changes_frame = pd.DataFrame({"Cambio di composizione": row.get("composition_changes", [])})
        st.markdown("**Cambi di composizione rilevati**")
        st.dataframe(changes_frame, use_container_width=True, hide_index=True)

    _render_period_law_panel(
        db,
        row.get("government", "Governo"),
        row.get("period_start", ""),
        row.get("period_end", ""),
        f"browser_period_{re.sub(r'[^a-zA-Z0-9]+', '_', row.get('government', 'gov')).strip('_').lower()}",
    )



def render_institutions_people_page(db: Any) -> None:
    st.header("🏛️ Istituzioni & Persone")
    st.caption(
        "Collega il corpus Normattiva con Camera, Senato, Governo, Quirinale e Corte costituzionale: "
        "persone, cariche, attivita istituzionale e punti di accesso ufficiali."
    )

    col_a, col_b = st.columns([1, 1])
    with col_a:
        prefer_live = st.checkbox(
            "Usa fonti live ufficiali",
            value=not DEFAULT_SNAPSHOT_PATH.exists(),
            help="Disattiva per usare lo snapshot locale se presente in data/institutional/.",
        )
    with col_b:
        include_status = st.checkbox(
            "Verifica anche la salute degli endpoint",
            value=False,
            help="Aggiunge un controllo HTTP leggero delle fonti ufficiali.",
        )

    data, source_label = _snapshot_for_page(db, prefer_live, include_status)
    summary = data.get("laws_summary", {})
    generated_at = data.get("generated_at", "N/D")
    st.caption(f"Origine: {source_label} • Aggiornato: {generated_at}")

    if data.get("errors"):
        st.warning(f"Alcune fonti non hanno risposto: {', '.join(sorted(data['errors'].keys())[:6])}")

    camera_deputies = data.get("camera", {}).get("current_deputies", [])
    camera_legislatures = _prepare_legislature_history(data.get("camera", {}).get("legislatures", []))
    camera_governments = _prepare_camera_government_history(data.get("camera", {}).get("governments", []))
    senato_senators = data.get("senato", {}).get("current_senators", [])
    governo_members = data.get("governo", {}).get("current_members", [])
    governo_history = _prepare_browser_government_history(data.get("governo", {}).get("history", []))
    quirinale_presidents = data.get("quirinale", {}).get("presidents", [])
    corte_judges = data.get("corte", {}).get("current_judges", [])

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Deputati (XIX)", len(camera_deputies))
    m2.metric("Senatori (XIX)", len(senato_senators))
    m3.metric("Membri Governo", len(governo_members))
    m4.metric("Giudici Corte", len(corte_judges))
    m5.metric("Presidenti Repubblica", len(quirinale_presidents))

    main_section = st.radio(
        "Sezione istituzionale",
        ["Parlamento", "Governo", "Quirinale", "Corte", "Join con il dataset"],
        horizontal=True,
        key="inst_main_section",
    )

    if main_section == "Parlamento":
        st.subheader("Camera dei deputati e Senato")
        st.caption("Dati correnti e successione storica via SPARQL dagli endpoint ufficiali della Camera.")

        parlamento_section = st.radio(
            "Vista Parlamento",
            ["In carica", "Successione legislature", "Successione governi (SPARQL)"],
            horizontal=True,
            key="inst_parlamento_section",
        )

        if parlamento_section == "In carica":
            camera_rows = []
            for row in camera_deputies:
                camera_rows.append({
                    "Nome": _person_name(row, "nome", "cognome"),
                    "Genere": row.get("gender", ""),
                    "Profilo": row.get("bio", row.get("persona", "")),
                })
            senato_rows = []
            for row in senato_senators:
                senato_rows.append({
                    "Nome": _person_name(row, "nome", "cognome"),
                    "Genere": row.get("gender", ""),
                    "Professione": row.get("profession", ""),
                    "Profilo": row.get("persona", ""),
                })

            left, right = st.columns(2)
            with left:
                st.markdown("**Deputati in carica (Camera)**")
                st.dataframe(_rows_frame(camera_rows).head(150), use_container_width=True, hide_index=True)
            with right:
                st.markdown("**Senatori in carica (Senato)**")
                st.dataframe(_rows_frame(senato_rows).head(150), use_container_width=True, hide_index=True)

            ddl_rows = _rows_frame(data.get("senato", {}).get("recent_ddl", []), rename={
                "id": "DDL",
                "title": "Titolo",
                "leg": "Legislatura",
                "presented": "Presentato",
                "law_number": "Legge",
                "law_date": "Data legge",
                "ddl": "URL",
            })
            if not ddl_rows.empty:
                st.markdown("**DDL recenti dal Senato**")
                ddl_display = ddl_rows.reindex(
                    columns=["DDL", "Titolo", "Legislatura", "Presentato", "Legge", "Data legge", "URL"],
                    fill_value="",
                )
                st.dataframe(ddl_display.head(20), use_container_width=True, hide_index=True)

            if summary:
                _laws_by_legislature_chart(summary)

        if parlamento_section == "Successione legislature":
            st.caption(
                "Successione completa delle legislature ricavata dallo SPARQL ufficiale della Camera dei deputati. "
                "Clicca una barra o usa il selettore per aprire il corpus Normattiva nel periodo corrispondente."
            )
            legislature_frame = _rows_frame(camera_legislatures, rename={
                "label": "Legislatura",
                "title": "Titolo",
                "start_display": "Inizio",
                "end_display": "Fine",
                "sparql_url": "URI SPARQL",
            })
            if not legislature_frame.empty:
                selected_legislature_key = _render_timeline(
                    legislature_frame,
                    "Legislatura",
                    "Successione storica delle legislature",
                    ["Titolo", "Inizio", "Fine", "URI SPARQL"],
                    chart_key="inst_legislature_timeline",
                    selection_key_col="selection_key",
                )
                selected_legislature = _select_history_row(
                    camera_legislatures,
                    "inst_legislature_period",
                    "Apri il corpus per legislatura",
                    selected_legislature_key,
                )
                st.dataframe(
                    legislature_frame[["Legislatura", "Titolo", "Inizio", "Fine", "URI SPARQL"]],
                    use_container_width=True,
                    hide_index=True,
                )
                if selected_legislature:
                    st.divider()
                    _render_period_law_panel(
                        db,
                        f"Legislatura {selected_legislature.get('label', '')}".strip(),
                        selected_legislature.get("period_start", ""),
                        selected_legislature.get("period_end", ""),
                        f"legislature_{selected_legislature.get('label', 'periodo').lower()}",
                    )

        if parlamento_section == "Successione governi (SPARQL)":
            st.caption(
                "Successione completa dei governi via SPARQL della Camera, utile per leggere la storia normativa lungo i cambi di esecutivo. "
                "La selezione apre il corpus per l'intervallo cronologico del governo scelto."
            )
            governments_frame = _rows_frame(camera_governments, rename={
                "government": "Governo",
                "start_display": "Inizio",
                "end_display": "Fine",
                "sparql_url": "URI SPARQL",
            })
            if not governments_frame.empty:
                selected_camera_government_key = _render_timeline(
                    governments_frame,
                    "Governo",
                    "Successione storica dei governi (SPARQL Camera)",
                    ["Inizio", "Fine", "URI SPARQL"],
                    chart_key="inst_camera_government_timeline",
                    selection_key_col="selection_key",
                )
                selected_camera_government = _select_history_row(
                    camera_governments,
                    "inst_camera_government_period",
                    "Apri il corpus per governo",
                    selected_camera_government_key,
                )
                st.dataframe(
                    governments_frame[["Governo", "Inizio", "Fine", "URI SPARQL"]],
                    use_container_width=True,
                    hide_index=True,
                )
                if selected_camera_government:
                    st.divider()
                    _render_period_law_panel(
                        db,
                        selected_camera_government.get("government", "Governo"),
                        selected_camera_government.get("period_start", ""),
                        selected_camera_government.get("period_end", ""),
                        f"camera_government_{re.sub(r'[^a-zA-Z0-9]+', '_', selected_camera_government.get('government', 'governo')).strip('_').lower()}",
                    )

    if main_section == "Governo":
        st.subheader("Governo e Presidenza del Consiglio")
        st.caption("Membri correnti, successione storica ufficiale dei governi via browser e feed del Governo.")

        governo_section = st.radio(
            "Vista Governo",
            ["Esecutivo attuale", "Storico ufficiale (browser)", "RSS e leggi del dataset"],
            horizontal=True,
            key="inst_governo_section",
        )

        if governo_section == "Esecutivo attuale":
            members_frame = _rows_frame(governo_members, rename={
                "name": "Nome",
                "role_bucket": "Ruolo",
                "government_slug": "Governo",
                "url": "Profilo",
            })
            if not members_frame.empty:
                st.dataframe(members_frame, use_container_width=True, hide_index=True)
                _government_role_chart(governo_members)

        if governo_section == "Storico ufficiale (browser)":
            st.caption(
                "Cronologia ufficiale dei governi dal browser di governo.it. "
                "Usala insieme alla tab SPARQL sopra per collegare i cambi di esecutivo alla timeline delle legislature. "
                "Ogni riga e ora arricchita con periodo, Presidente del Consiglio, composizione ministeriale e cambi di assetto."
            )
            gov_history_frame = _rows_frame(governo_history, rename={
                "government": "Governo",
                "legislature_hint": "Legislatura",
                "prime_minister": "Presidente del Consiglio",
                "start_display": "Inizio",
                "end_display": "Fine",
                "ministries_without_portfolio_count": "Senza portafoglio",
                "ministries_with_portfolio_count": "Ministeri",
                "composition_change_count": "Cambi",
                "official_page": "Pagina ufficiale",
            })
            if not gov_history_frame.empty:
                selected_browser_government_key = _render_timeline(
                    gov_history_frame,
                    "Governo",
                    "Successione storica dei governi (browser governo.it)",
                    ["Inizio", "Fine", "Legislatura", "Presidente del Consiglio", "Pagina ufficiale"],
                    chart_key="inst_browser_government_timeline",
                    selection_key_col="selection_key",
                )
                selected_browser_government = _select_history_row(
                    governo_history,
                    "inst_browser_government_period",
                    "Apri il dettaglio di un governo",
                    selected_browser_government_key,
                )
                st.dataframe(
                    gov_history_frame[[
                        "Governo",
                        "Inizio",
                        "Fine",
                        "Legislatura",
                        "Presidente del Consiglio",
                        "Senza portafoglio",
                        "Ministeri",
                        "Cambi",
                        "Pagina ufficiale",
                    ]],
                    use_container_width=True,
                    hide_index=True,
                )
                if selected_browser_government:
                    st.divider()
                    _render_browser_government_detail(selected_browser_government, db)

        if governo_section == "RSS e leggi del dataset":
            rss_rows = data.get("governo", {}).get("rss", [])
            left, right = st.columns(2)
            with left:
                st.markdown("**Ultimi aggiornamenti dal feed RSS del Governo**")
                for item in rss_rows[:8]:
                    st.markdown(f"- [{item.get('title', 'Aggiornamento')}]({item.get('link', '')})")
                    if item.get("published"):
                        st.caption(item.get("published", ""))
            with right:
                if summary:
                    _laws_by_government_chart(summary)

    if main_section == "Quirinale":
        st.subheader("Quirinale")
        st.caption("Presidenti della Repubblica e accesso diretto all'archivio storico del Quirinale.")

        presidents_frame = _rows_frame(quirinale_presidents, rename={
            "name": "Presidente",
            "id": "ID",
            "url": "Portale",
        })
        if not presidents_frame.empty:
            st.dataframe(presidents_frame, use_container_width=True, hide_index=True)

        st.markdown("**Accessi rapidi all'archivio**")
        for row in data.get("quirinale", {}).get("archive_links", []):
            st.markdown(f"- [{row.get('label', 'Archivio')}]({row.get('url', '')})")

    if main_section == "Corte":
        st.subheader("Corte costituzionale")
        st.caption("Giudici in carica, presidenti storici e ultime pronunce depositate.")

        judges_frame = _rows_frame(corte_judges, rename={
            "name": "Giudice",
            "biography_url": "Biografia",
            "drafted_decisions_url": "Pronunce redatte",
            "interventions_url": "Interventi",
        })
        if not judges_frame.empty:
            st.dataframe(judges_frame, use_container_width=True, hide_index=True)

        left, right = st.columns(2)
        with left:
            latest_frame = _rows_frame(data.get("corte", {}).get("latest_deposits", []), rename={
                "title": "Pronuncia",
                "year": "Anno",
                "number": "Numero",
                "url": "Scheda",
            })
            if not latest_frame.empty:
                st.markdown("**Ultimi depositi**")
                st.dataframe(latest_frame, use_container_width=True, hide_index=True)
        with right:
            presidents_history_frame = _rows_frame(data.get("corte", {}).get("presidents_history", []), rename={
                "name": "Presidente",
                "url": "Pagina",
            })
            if not presidents_history_frame.empty:
                st.markdown("**Presidenti della Corte**")
                st.dataframe(presidents_history_frame.head(20), use_container_width=True, hide_index=True)

    if main_section == "Join con il dataset":
        st.subheader("Join con il corpus Normattiva")
        st.caption(
            "Usa i campi gia presenti nel DB locale, in particolare legislature, governi ed ere, "
            "per passare dalle norme alle istituzioni e viceversa."
        )

        if not summary:
            st.info("Il database locale non e disponibile, quindi non posso mostrare le join sulle leggi.")
        else:
            col1, col2, col3 = st.columns(3)
            col1.metric("Leggi totali", f"{summary.get('laws_total', 0):,}")
            col2.metric("Legislature nel DB", summary.get("distinct_legislatures", 0))
            col3.metric("Governi nel DB", summary.get("distinct_governments", 0))

            if not summary.get("by_legislature") and not summary.get("by_government"):
                st.info(
                    "Il DB remoto non espone ancora tag diretti di legislatura o governo. "
                    "Usa le timeline storiche sopra: filtrano comunque il corpus per intervallo cronologico con fallback su summary CSV."
                )

            recent_rows = _recent_institutional_laws(_db_path(db))
            if recent_rows:
                st.markdown("**Leggi recenti con metadati istituzionali**")
                st.dataframe(_rows_frame(recent_rows).head(25), use_container_width=True, hide_index=True)
            elif summary.get("laws_total"):
                st.caption("Nessuna etichetta istituzionale diretta trovata nel DB corrente; il collegamento corpus-istituzioni avviene soprattutto per periodo e ricerca testuale.")

            leg_options = [row.get("legislature_id") for row in summary.get("by_legislature", [])]
            gov_options = [row.get("government") for row in summary.get("by_government", []) if row.get("government")]
            left, right = st.columns(2)

            with left:
                if leg_options:
                    selected_leg = st.selectbox("Filtra per legislatura", leg_options, key="inst_legislature_filter")
                    leg_rows = _laws_for_legislature(_db_path(db), int(selected_leg))
                    if leg_rows:
                        st.dataframe(_rows_frame(leg_rows), use_container_width=True, hide_index=True)

            with right:
                if gov_options:
                    selected_gov = st.selectbox("Filtra per governo", gov_options, key="inst_government_filter")
                    gov_rows = _laws_for_government(_db_path(db), selected_gov)
                    if gov_rows:
                        st.dataframe(_rows_frame(gov_rows), use_container_width=True, hide_index=True)

            search_term = st.text_input(
                "Cerca norme collegate a una persona o istituzione",
                placeholder="es. Meloni, Corte costituzionale, Parlamento",
                key="inst_term_search",
            )
            if search_term.strip():
                term_rows = _search_laws_by_institutional_term(_db_path(db), search_term.strip())
                if term_rows:
                    st.markdown(f"**Risultati per: {search_term.strip()}**")
                    st.dataframe(_rows_frame(term_rows), use_container_width=True, hide_index=True)
                else:
                    st.info("Nessuna legge trovata con questo termine nel titolo o nel testo.")



def render_source_registry_page(db: Any) -> None:
    st.header("🧭 Registro Fonti")
    st.caption(
        "Inventario operativo delle fonti istituzionali ufficiali: endpoint, tipologia, copertura, "
        "target di join e stato di disponibilita."
    )

    registry = registry_rows()
    frame = _rows_frame(registry, rename={
        "institution": "Istituzione",
        "section": "Sezione",
        "label": "Fonte",
        "url": "URL",
        "access_type": "Accesso",
        "data_kind": "Tipo dati",
        "coverage": "Copertura",
        "join_target": "Join target",
        "notes": "Note",
    })

    if frame.empty:
        st.info("Registro fonti non disponibile.")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Fonti censite", len(frame))
    m2.metric("SPARQL", int((frame["Accesso"] == "sparql").sum()))
    m3.metric("RSS", int((frame["Accesso"] == "rss").sum()))
    m4.metric("Archivi", int((frame["Tipo dati"] == "archive").sum()))

    st.dataframe(frame[["Istituzione", "Sezione", "Fonte", "Accesso", "Tipo dati", "Copertura", "Join target", "URL", "Note"]], use_container_width=True, hide_index=True)

    by_section = frame.groupby("Sezione").size().reset_index(name="Fonti")
    fig = px.bar(by_section, x="Sezione", y="Fonti", title="Copertura del registro per sezione", color="Fonti")
    st.plotly_chart(fig, use_container_width=True)

    if st.checkbox("Verifica endpoint live", value=False, key="source_registry_health"):
        status_rows = _load_endpoint_statuses()
        status_frame = _rows_frame(status_rows, rename={
            "institution": "Istituzione",
            "section": "Sezione",
            "label": "Fonte",
            "access_type": "Accesso",
            "status_code": "HTTP",
            "ok": "OK",
            "content_type": "Content-Type",
            "final_url": "URL finale",
            "checked_at": "Controllato",
            "error": "Errore",
        })
        if not status_frame.empty:
            st.markdown("**Stato endpoint**")
            st.dataframe(status_frame, use_container_width=True, hide_index=True)

    snapshot = load_snapshot(DEFAULT_SNAPSHOT_PATH)
    if snapshot:
        st.markdown("**Ultimo snapshot locale**")
        st.caption(f"Generato: {snapshot.get('generated_at', 'N/D')}")

        preview_rows = [
            {"Sezione": "Camera", "Record": len(snapshot.get("camera", {}).get("current_deputies", []))},
            {"Sezione": "Senato", "Record": len(snapshot.get("senato", {}).get("current_senators", []))},
            {"Sezione": "Governo", "Record": len(snapshot.get("governo", {}).get("current_members", []))},
            {"Sezione": "Quirinale", "Record": len(snapshot.get("quirinale", {}).get("presidents", []))},
            {"Sezione": "Corte", "Record": len(snapshot.get("corte", {}).get("current_judges", []))},
        ]
        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
        if snapshot.get("errors"):
            st.warning(f"Errori registrati nello snapshot: {', '.join(sorted(snapshot['errors'].keys()))}")

    laws_summary = _load_laws_summary(_db_path(db))
    if laws_summary:
        st.markdown("**Join gia disponibili nel DB locale**")
        join_rows = [
            {"Campo": "legislature_id", "Valori distinti": laws_summary.get("distinct_legislatures", 0), "Uso": "join con Camera e Senato"},
            {"Campo": "government", "Valori distinti": laws_summary.get("distinct_governments", 0), "Uso": "join con storia dei governi"},
            {"Campo": "era", "Valori distinti": len({row.get('era') for row in laws_summary.get('recent_laws', []) if row.get('era')}), "Uso": "join con Quirinale e periodizzazione"},
        ]
        st.dataframe(pd.DataFrame(join_rows), use_container_width=True, hide_index=True)
