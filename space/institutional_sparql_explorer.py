"""Institutional SPARQL explorer page for Streamlit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import pandas as pd
import plotly.express as px
import streamlit as st
from huggingface_hub import hf_hub_download

MANIFEST_PATH = "data/institutional_sparql/manifest.json"

CAMERA_LEGISLATURES_QUERY = (
    "PREFIX ocd: <http://dati.camera.it/ocd/>\n"
    "PREFIX dc: <http://purl.org/dc/elements/1.1/>\n"
    "SELECT DISTINCT ?leg ?title ?start ?end WHERE {\n"
    "  ?leg a ocd:legislatura ;\n"
    "       dc:title ?title ;\n"
    "       ocd:startDate ?start .\n"
    "  OPTIONAL { ?leg ocd:endDate ?end }\n"
    "}\n"
    "ORDER BY ?start"
)

CAMERA_GOVERNMENTS_QUERY = (
    "PREFIX ocd: <http://dati.camera.it/ocd/>\n"
    "PREFIX dc: <http://purl.org/dc/elements/1.1/>\n"
    "SELECT DISTINCT ?gov ?title ?start ?end WHERE {\n"
    "  ?gov a ocd:governo ;\n"
    "       dc:title ?title ;\n"
    "       ocd:startDate ?start .\n"
    "  OPTIONAL { ?gov ocd:endDate ?end }\n"
    "}\n"
    "ORDER BY ?start"
)


def _load_manifest(dataset_repo: str) -> Dict:
    candidates = [dataset_repo]
    if "/" not in dataset_repo:
        candidates.append(f"diatribe00/{dataset_repo}")

    last_error = None
    for repo_id in candidates:
        try:
            path = hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=MANIFEST_PATH,
            )
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            last_error = exc

    return {"error": str(last_error) if last_error else "Manifest not found"}


def _load_jsonl(dataset_repo: str, filename: str, limit: int = 2000) -> pd.DataFrame:
    candidates = [dataset_repo]
    if "/" not in dataset_repo:
        candidates.append(f"diatribe00/{dataset_repo}")

    last_error = None
    for repo_id in candidates:
        try:
            local_path = hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=filename,
            )
            rows: List[Dict] = []
            with Path(local_path).open("r", encoding="utf-8") as fh:
                for idx, line in enumerate(fh):
                    if idx >= limit:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    rows.append(json.loads(line))
            return pd.DataFrame(rows)
        except Exception as exc:
            last_error = exc

    raise RuntimeError(str(last_error) if last_error else f"Could not load {filename}")


def _format_compact_date(value: str | None) -> str:
    if not value:
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[6:8]}/{digits[4:6]}/{digits[:4]}"
    return str(value)


def _compact_date_to_ts(value: str | None):
    if not value:
        return pd.NaT
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) == 8:
        return pd.to_datetime(digits, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(value, errors="coerce")


def _render_history_preview(dataset_repo: str, files: Dict) -> None:
    st.subheader("Historical succession")
    st.caption(
        "Sequenza storica di legislature e governi ricavata dagli endpoint SPARQL ufficiali della Camera."
    )

    legislature_meta = files.get("camera_legislatures", {})
    government_meta = files.get("camera_governments", {})

    history_section = st.radio(
        "Historical succession view",
        ["Legislatures", "Governments", "Live queries"],
        horizontal=True,
        key="sparql-history-section",
    )

    if history_section == "Legislatures":
        if legislature_meta.get("jsonl_path"):
            try:
                frame = _load_jsonl(dataset_repo, legislature_meta["jsonl_path"], limit=200)
            except Exception as exc:
                st.error(f"Could not load legislature history: {exc}")
            else:
                if not frame.empty:
                    frame = frame.copy()
                    frame["Inizio"] = frame["start"].apply(_format_compact_date)
                    frame["Fine"] = frame["end"].apply(_format_compact_date).replace("", "In corso")
                    frame["start_ts"] = frame["start"].apply(_compact_date_to_ts)
                    frame["end_plot"] = frame["end"].apply(_compact_date_to_ts)
                    frame["end_plot"] = frame["end_plot"].where(frame["end_plot"].notna(), pd.Timestamp.now().normalize())
                    fig = px.timeline(
                        frame,
                        x_start="start_ts",
                        x_end="end_plot",
                        y="title",
                        hover_data=["Inizio", "Fine", "leg"],
                        title="Successione delle legislature",
                    )
                    fig.update_yaxes(autorange="reversed")
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(
                        frame[["title", "Inizio", "Fine", "leg"]].rename(
                            columns={"title": "Legislatura", "leg": "URI SPARQL"}
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
        else:
            st.info("Il dataset SPARQL non contiene ancora la successione delle legislature.")

    elif history_section == "Governments":
        if government_meta.get("jsonl_path"):
            try:
                frame = _load_jsonl(dataset_repo, government_meta["jsonl_path"], limit=400)
            except Exception as exc:
                st.error(f"Could not load government history: {exc}")
            else:
                if not frame.empty:
                    frame = frame.copy()
                    frame["Inizio"] = frame["start"].apply(_format_compact_date)
                    frame["Fine"] = frame["end"].apply(_format_compact_date).replace("", "In corso")
                    frame["start_ts"] = frame["start"].apply(_compact_date_to_ts)
                    frame["end_plot"] = frame["end"].apply(_compact_date_to_ts)
                    frame["end_plot"] = frame["end_plot"].where(frame["end_plot"].notna(), pd.Timestamp.now().normalize())
                    fig = px.timeline(
                        frame,
                        x_start="start_ts",
                        x_end="end_plot",
                        y="title",
                        hover_data=["Inizio", "Fine", "gov"],
                        title="Successione dei governi",
                    )
                    fig.update_yaxes(autorange="reversed")
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(
                        frame[["title", "Inizio", "Fine", "gov"]].rename(
                            columns={"title": "Governo", "gov": "URI SPARQL"}
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
        else:
            st.info("Il dataset SPARQL non contiene ancora la successione dei governi.")

    else:
        st.markdown("**Camera SPARQL endpoint**: https://dati.camera.it/sparql")
        st.caption("Query stabili per ricostruire la successione storica di legislature e governi anche fuori dallo snapshot dataset.")
        st.code(CAMERA_LEGISLATURES_QUERY, language="sparql")
        st.code(CAMERA_GOVERNMENTS_QUERY, language="sparql")


def render_institutional_sparql_page(dataset_repo: str) -> None:
    st.title("🏛️ Institutional SPARQL")
    st.caption("Cross-institution RDF/SPARQL snapshots from major Italian public endpoints.")

    manifest = _load_manifest(dataset_repo)
    if manifest.get("error"):
        st.info(
            "SPARQL snapshots are not available yet. Run sync_institutional_sparql.py "
            "or wait for the weekly workflow."
        )
        st.caption(f"Details: {manifest.get('error')}")
        st.markdown(
            "- Camera: https://dati.camera.it/sparql\n"
            "- Senato: https://dati.senato.it/sparql\n"
            "- Dati.gov.it: https://www.dati.gov.it/sviluppatori/sparqlclient\n"
            "- Corte: https://dati.cortecostituzionale.it/sparql"
        )
        st.code(CAMERA_LEGISLATURES_QUERY, language="sparql")
        st.code(CAMERA_GOVERNMENTS_QUERY, language="sparql")
        return

    files = manifest.get("files", {}) or {}
    total_sources = int(manifest.get("total_sources", 0))
    ok_sources = int(manifest.get("ok_sources", 0))
    total_records = sum(int(v.get("record_count", 0)) for v in files.values())

    c1, c2, c3 = st.columns(3)
    c1.metric("Sources", f"{total_sources:,}")
    c2.metric("Reachable", f"{ok_sources:,}")
    c3.metric("Records", f"{total_records:,}")

    _render_history_preview(dataset_repo, files)

    rows = []
    for key, meta in sorted(files.items()):
        rows.append(
            {
                "source_key": key,
                "label": meta.get("label"),
                "status": "ok" if meta.get("ok") else "error",
                "http_status": meta.get("http_status"),
                "records": meta.get("record_count", 0),
                "endpoint": meta.get("endpoint"),
                "updated_at_utc": meta.get("updated_at_utc"),
                "jsonl_path": meta.get("jsonl_path"),
                "error": meta.get("error"),
            }
        )

    status_df = pd.DataFrame(rows)
    st.subheader("Source status")
    st.dataframe(status_df, width="stretch", hide_index=True)

    selectable = [r for r in rows if r.get("jsonl_path")]
    if not selectable:
        return

    left, right = st.columns([3, 1])
    with left:
        pick = st.selectbox(
            "Source dataset",
            selectable,
            format_func=lambda x: f"{x['source_key']} ({x['records']} rows)",
        )
    with right:
        limit = st.slider("Preview rows", 100, 5000, 1000, 100)

    try:
        df = _load_jsonl(dataset_repo, pick["jsonl_path"], limit=limit)
    except Exception as exc:
        st.error(f"Could not load preview: {exc}")
        return

    if df.empty:
        st.warning("No records available for this source snapshot.")
        return

    st.subheader("Preview")
    st.dataframe(df, width="stretch", hide_index=True)
