#!/usr/bin/env python3
"""Streamlit helpers for exploring Corte Costituzionale open data."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import plotly.express as px
import streamlit as st

MANIFEST_PATH = "data/corte_costituzionale/manifest.json"
PROCESSED_SUMMARY_PATH = "data/corte_costituzionale/processed/summary.json"


def _repo_candidates(dataset_repo: str) -> List[str]:
    repo = (dataset_repo or "").strip()
    try:
        owner = st.secrets.get("HF_DATASET_OWNER") or ""
    except Exception:
        owner = ""
    owner = owner or "diatribe00"

    candidates: List[str] = []
    if repo:
        candidates.append(repo)
        if "/" not in repo:
            candidates.append(f"{owner}/{repo}")
    # Always keep the known production fallback for this Space.
    if "diatribe00/italian-legal-lab-data" not in candidates:
        candidates.append("diatribe00/italian-legal-lab-data")
    return list(dict.fromkeys(candidates))


def _human_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


@st.cache_data(ttl=900, show_spinner=False)
def _load_manifest(dataset_repo: str) -> Dict[str, Any]:
    # Try local first (when file is shipped with workspace), then HF dataset.
    local_path = Path(MANIFEST_PATH)
    if local_path.exists():
        return json.loads(local_path.read_text(encoding="utf-8"))

    from huggingface_hub import hf_hub_download

    errors = []
    for repo_id in _repo_candidates(dataset_repo):
        try:
            downloaded = hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=MANIFEST_PATH,
            )
            return json.loads(Path(downloaded).read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{repo_id}: {exc}")
    return {"error": " | ".join(errors)}


@st.cache_data(ttl=900, show_spinner=False)
def _load_processed_summary(dataset_repo: str) -> Dict[str, Any]:
    local_path = Path(PROCESSED_SUMMARY_PATH)
    if local_path.exists():
        return json.loads(local_path.read_text(encoding="utf-8"))

    from huggingface_hub import hf_hub_download

    errors = []
    for repo_id in _repo_candidates(dataset_repo):
        try:
            downloaded = hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=PROCESSED_SUMMARY_PATH,
            )
            return json.loads(Path(downloaded).read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{repo_id}: {exc}")
    return {"error": " | ".join(errors)}


@st.cache_data(ttl=900, show_spinner=False)
def _download_zip(dataset_repo: str, repo_path: str) -> str:
    from huggingface_hub import hf_hub_download

    last_error = None
    for repo_id in _repo_candidates(dataset_repo):
        try:
            return hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=repo_path,
            )
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(str(last_error) if last_error else "Unable to download ZIP")


@st.cache_data(ttl=900, show_spinner=False)
def _zip_members(zip_path: str) -> List[str]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        return sorted([m for m in zf.namelist() if not m.endswith("/")])


@st.cache_data(ttl=900, show_spinner=False)
def _load_processed_df(dataset_repo: str, repo_path: str, limit: int) -> pd.DataFrame:
    from huggingface_hub import hf_hub_download

    local = None
    last_error = None
    for repo_id in _repo_candidates(dataset_repo):
        try:
            local = hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=repo_path,
            )
            break
        except Exception as exc:
            last_error = exc
            continue
    if local is None:
        raise RuntimeError(str(last_error) if last_error else "Unable to load processed file")

    chunks = []
    loaded = 0
    for chunk in pd.read_json(local, lines=True, chunksize=2000):
        chunks.append(chunk)
        loaded += len(chunk)
        if loaded >= limit:
            break

    if not chunks:
        return pd.DataFrame()

    df = pd.concat(chunks, ignore_index=True)
    if len(df) > limit:
        df = df.head(limit)
    return df


def _preview_csv(raw: bytes, max_rows: int = 200) -> pd.DataFrame:
    # Try common encodings used in public administration exports.
    for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc, nrows=max_rows)
        except Exception:
            continue
    raise ValueError("Unable to parse CSV with common encodings")


def _preview_json(raw: bytes, max_rows: int = 200) -> pd.DataFrame:
    data = json.loads(raw.decode("utf-8", errors="ignore"))
    if isinstance(data, list):
        return pd.DataFrame(data[:max_rows])
    if isinstance(data, dict):
        if "results" in data and isinstance(data["results"], list):
            return pd.DataFrame(data["results"][:max_rows])
        return pd.json_normalize(data)
    return pd.DataFrame({"value": [str(data)]})


def _preview_xml(raw: bytes, max_rows: int = 100) -> pd.DataFrame:
    from lxml import etree

    root = etree.fromstring(raw)
    rows: List[Dict[str, Any]] = []

    # Generic projection: one row per leaf-like element with short text.
    for elem in root.iter():
        if len(rows) >= max_rows:
            break
        text = (elem.text or "").strip()
        if not text:
            continue
        if len(text) > 300:
            text = text[:300] + "..."
        rows.append(
            {
                "tag": etree.QName(elem).localname,
                "path": root.getroottree().getpath(elem),
                "text": text,
            }
        )

    if not rows:
        return pd.DataFrame(
            [
                {
                    "tag": etree.QName(root).localname,
                    "path": "/",
                    "text": "(No leaf text in first pass)",
                }
            ]
        )
    return pd.DataFrame(rows)


def _render_manifest_overview(manifest: Dict[str, Any]) -> None:
    st.subheader("Coverage")

    total_files = int(manifest.get("total_files", 0))
    total_size = int(manifest.get("total_size_bytes", 0))
    families = manifest.get("families", {}) or {}
    files_map = manifest.get("files", {}) or {}

    c1, c2, c3 = st.columns(3)
    c1.metric("Corte files", f"{total_files:,}")
    c2.metric("Total size", _human_bytes(total_size))
    c3.metric("Families", f"{len(families):,}")

    if families:
        fam_df = pd.DataFrame(
            [{"family": k, "files": v} for k, v in sorted(families.items())]
        )
        fig = px.bar(fam_df, x="family", y="files", title="Files by family")
        st.plotly_chart(fig, width="stretch")

    if files_map:
        records = []
        for source_url, meta in files_map.items():
            repo_path = str(meta.get("repo_path", ""))
            fmt = Path(repo_path).stem.split("_")[-1].lower() if "_" in Path(repo_path).stem else Path(repo_path).suffix.replace(".", "")
            records.append(
                {
                    "family": meta.get("family", "other"),
                    "repo_path": repo_path,
                    "size_mb": round(int(meta.get("size", 0)) / 1e6, 3),
                    "etag": meta.get("etag"),
                    "last_modified": meta.get("last_modified"),
                    "source_url": source_url,
                    "format_hint": fmt,
                }
            )
        df = pd.DataFrame(records).sort_values(["family", "repo_path"])
        st.dataframe(df, width="stretch", hide_index=True)


def _render_data_explorer(dataset_repo: str, manifest: Dict[str, Any]) -> None:
    st.subheader("Data Explorer")

    files_map = manifest.get("files", {}) or {}
    if not files_map:
        st.info("Manifest has no file entries yet.")
        return

    records = []
    for source_url, meta in files_map.items():
        records.append(
            {
                "source_url": source_url,
                "repo_path": meta.get("repo_path", ""),
                "family": meta.get("family", "other"),
                "size": int(meta.get("size", 0)),
            }
        )

    families = sorted(set(r["family"] for r in records))
    c1, c2 = st.columns([1, 2])
    with c1:
        selected_family = st.selectbox("Family", ["all"] + families)
    with c2:
        family_paths = [
            r["repo_path"]
            for r in records
            if selected_family == "all" or r["family"] == selected_family
        ]
        selected_path = st.selectbox("Dataset file", sorted(family_paths))

    selected_meta = next((r for r in records if r["repo_path"] == selected_path), None)
    if not selected_meta:
        st.warning("Could not resolve selected file metadata.")
        return

    st.caption(
        f"Path: {selected_path} | Size: {_human_bytes(selected_meta['size'])} | Family: {selected_meta['family']}"
    )

    try:
        zip_local_path = _download_zip(dataset_repo, selected_path)
        members = _zip_members(zip_local_path)
    except Exception as exc:
        st.error(f"Failed to download/read ZIP: {exc}")
        return

    if not members:
        st.info("ZIP archive has no file members.")
        return

    member = st.selectbox("Archive member", members)

    with zipfile.ZipFile(zip_local_path, "r") as zf:
        raw = zf.read(member)

    suffix = Path(member).suffix.lower()
    try:
        if suffix == ".csv":
            df = _preview_csv(raw)
            st.dataframe(df, width="stretch", hide_index=True)
        elif suffix == ".json":
            df = _preview_json(raw)
            st.dataframe(df, width="stretch", hide_index=True)
        elif suffix == ".xml":
            df = _preview_xml(raw)
            st.dataframe(df, width="stretch", hide_index=True)
            st.download_button(
                "Download raw XML member",
                data=raw,
                file_name=Path(member).name,
                mime="application/xml",
                key=f"dl-{selected_path}-{member}",
            )
        else:
            st.text(raw[:4000].decode("utf-8", errors="ignore"))
    except Exception as exc:
        st.error(f"Preview failed: {exc}")


def _render_cc_links() -> None:
    st.subheader("Corte data portals")
    st.markdown(
        "- Download datasets: https://dati.cortecostituzionale.it/Scarica_i_dati/Scarica_i_dati\n"
        "- ECLI reference: https://dati.cortecostituzionale.it/ECLI/ECLI\n"
        "- SPARQL endpoint: https://dati.cortecostituzionale.it/sparql"
    )


def _render_parsed_analytics(dataset_repo: str) -> None:
    st.subheader("Parsed Tables")
    summary = _load_processed_summary(dataset_repo)
    if summary.get("error"):
        st.info(
            "Parsed tables are not available yet. Run parse_corte_costituzionale.py with --upload "
            "or wait for the weekly auto-sync workflow."
        )
        st.caption(f"Details: {summary.get('error')}")
        return

    total_records = int(summary.get("total_records", 0))
    raw_zips = int(summary.get("raw_zip_files", 0))
    c1, c2, c3 = st.columns(3)
    c1.metric("Parsed records", f"{total_records:,}")
    c2.metric("Raw ZIP files", f"{raw_zips:,}")
    c3.metric("Families", f"{len(summary.get('records_by_family', {}) or {}):,}")

    fam_counts = summary.get("records_by_family", {}) or {}
    if fam_counts:
        fam_df = pd.DataFrame(
            [{"family": k, "records": v} for k, v in sorted(fam_counts.items())]
        )
        fig = px.bar(fam_df, x="family", y="records", title="Parsed records by family")
        st.plotly_chart(fig, width="stretch")

    processed_files = summary.get("processed_files", []) or []
    if not processed_files:
        st.info("No processed JSONL files listed in summary.")
        return

    left, right = st.columns([2, 1])
    with left:
        selected_path = st.selectbox("Processed file", processed_files)
    with right:
        preview_limit = st.slider("Preview rows", 500, 20000, 5000, 500)

    try:
        df = _load_processed_df(dataset_repo, selected_path, preview_limit)
    except Exception as exc:
        st.error(f"Could not load processed table: {exc}")
        return

    if df.empty:
        st.info("Processed file has no rows.")
        return

    if "year" in df.columns:
        yr_df = df[pd.to_numeric(df["year"], errors="coerce").notna()].copy()
        if not yr_df.empty:
            yr_df["year"] = pd.to_numeric(yr_df["year"], errors="coerce").astype(int)
            by_year = yr_df.groupby("year", as_index=False).size().rename(columns={"size": "records"})
            fig = px.area(by_year, x="year", y="records", title="Records by year (preview set)")
            st.plotly_chart(fig, width="stretch")

    query = st.text_input("Filter by title/doc id", placeholder="es. ecli, sentenza, ricorso")
    filtered = df
    if query:
        q = query.lower()
        cols = [c for c in ["title", "doc_id", "source_member"] if c in filtered.columns]
        if cols:
            mask = False
            for c in cols:
                mask = mask | filtered[c].astype(str).str.lower().str.contains(q, na=False)
            filtered = filtered[mask]

    preferred_cols = [
        "family",
        "source_format",
        "doc_id",
        "title",
        "date",
        "year",
        "source_member",
    ]
    display_cols = [c for c in preferred_cols if c in filtered.columns]
    if not display_cols:
        display_cols = list(filtered.columns)[:12]

    st.dataframe(filtered[display_cols].head(preview_limit), width="stretch", hide_index=True)


def render_corte_page(dataset_repo: str) -> None:
    st.header("⚖️ Corte Costituzionale Data Hub")
    st.caption(
        "Explore pronunce, massime, anagrafica giudici, and norme impugnate directly from the HF dataset mirror."
    )

    manifest = _load_manifest(dataset_repo)
    if manifest.get("error"):
        st.error(
            "Corte manifest not available yet. Run the Corte sync job first. "
            f"Details: {manifest.get('error')}"
        )
        _render_cc_links()
        return

    section = st.radio(
        "Corte data views",
        ["Coverage", "Raw Explorer", "Parsed Analytics", "External Links"],
        horizontal=True,
        key="corte-section",
    )

    if section == "Coverage":
        _render_manifest_overview(manifest)
    elif section == "Raw Explorer":
        _render_data_explorer(dataset_repo, manifest)
    elif section == "Parsed Analytics":
        _render_parsed_analytics(dataset_repo)
    else:
        _render_cc_links()
