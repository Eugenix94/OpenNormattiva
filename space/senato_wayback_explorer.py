"""Senato Wayback Explorer — Streamlit page.

Lets users browse the AkomaNtoso bulk-data index and stream
XML documents from any historical legislature on demand.
"""

from __future__ import annotations

import gzip
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import requests
import streamlit as st

INDEX_PATH = "data/senato_akomantoso/index/files.jsonl"
SUMMARY_PATH = "data/senato_akomantoso/index/summary.json"

LEGISLATURES = ["Leg13", "Leg14", "Leg15", "Leg16", "Leg17", "Leg18", "Leg19"]
FAMILY_LABELS = {
    "ddlpres": "Testo presentato (ddlpres)",
    "ddlcomm": "Relazione commissione (ddlcomm)",
    "ddlmess": "Messaggio trasmesso (ddlmess)",
    "emend": "Emendamenti assemblea (emend)",
    "emendc": "Emendamenti commissione (emendc)",
    "resaula": "Resoconto stenografico aula (resaula)",
    "sommcomm": "Resoconto sommario commissione (sommcomm)",
}

LEG_YEARS: Dict[str, str] = {
    "Leg13": "1994–1996",
    "Leg14": "2001–2006",
    "Leg15": "2006–2008",
    "Leg16": "2008–2013",
    "Leg17": "2013–2018",
    "Leg18": "2018–2022",
    "Leg19": "2022–oggi",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@st.cache_data(ttl=3600, show_spinner="Caricamento indice Senato…")
def _load_summary(dataset_repo: str) -> Dict:
    from huggingface_hub import hf_hub_download

    try:
        p = hf_hub_download(repo_id=dataset_repo, repo_type="dataset", filename=SUMMARY_PATH)
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


@st.cache_data(ttl=3600, show_spinner="Caricamento indice file Senato (163 MB, una sola volta)…")
def _load_full_index(dataset_repo: str) -> pd.DataFrame:
    from huggingface_hub import hf_hub_download

    p = hf_hub_download(repo_id=dataset_repo, repo_type="dataset", filename=INDEX_PATH)
    rows = []
    with Path(p).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return pd.DataFrame(rows)


def _fetch_xml(url: str, timeout: int = 45, retries: int = 3) -> str:
    session = requests.Session()
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(0.4 * attempt)
    raise RuntimeError(str(last_err))


def _stream_docs(df: pd.DataFrame, max_docs: int) -> Iterable[Dict]:
    rows = df.head(max_docs).to_dict("records")
    for idx, row in enumerate(rows, 1):
        url = str(row.get("url", ""))
        yield idx, len(rows), row, url


def _jsonl_gz_bytes(records: List[Dict]) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for rec in records:
            gz.write((json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
    return buf.getvalue()


def render_senato_wayback_page(dataset_repo: str) -> None:
    st.title("🏛️ Senato – Archivio Storico (Wayback)")
    st.caption(
        "Esplora e scarica i testi parlamentari in formato Akoma Ntoso "
        "dal XIII alla XIX Legislatura. "
        "Fonte: [SenatoDellaRepubblica/AkomaNtosoBulkData](https://github.com/SenatoDellaRepubblica/AkomaNtosoBulkData) "
        "— aggiornamento automatico ogni notte."
    )

    # ── Summary metrics ─────────────────────────────────────────
    summary = _load_summary(dataset_repo)
    if summary.get("error"):
        st.warning("Indice non ancora disponibile. Verrà caricato al prossimo ciclo di sync.")
    else:
        leg_stats = summary.get("legislatures", []) or []
        total = int(summary.get("total_xml_files", 0))
        total_mb = round(summary.get("total_size_bytes", 0) / 1e6)
        c1, c2, c3 = st.columns(3)
        c1.metric("Legislature indicizzate", len(leg_stats))
        c2.metric("Atti XML totali", f"{total:,}")
        c3.metric("Dimensione indice", f"~{total_mb:,} MB")

    st.divider()

    # ── Legislature selector ─────────────────────────────────────
    st.subheader("🗓️ Scegli Legislatura")
    col_leg, col_fam = st.columns([2, 2])

    with col_leg:
        selected_legs = st.multiselect(
            "Legislatura(e)",
            options=LEGISLATURES,
            default=["Leg19"],
            format_func=lambda x: f"{x} ({LEG_YEARS.get(x, '')})",
            help="Seleziona una o più legislature da esplorare.",
        )
    with col_fam:
        selected_fams = st.multiselect(
            "Tipologia documento",
            options=list(FAMILY_LABELS.keys()),
            default=[],
            format_func=lambda x: FAMILY_LABELS.get(x, x),
            help="Lascia vuoto per includere tutti i tipi.",
        )

    max_docs = st.slider(
        "Numero massimo di documenti da sfogliare / scaricare",
        min_value=10,
        max_value=5000,
        value=100,
        step=10,
        help="I documenti vengono scaricati in streaming direttamente dal repository Senato.",
    )

    if not selected_legs:
        st.info("Seleziona almeno una legislatura per continuare.")
        return

    st.divider()

    # ── Index preview ────────────────────────────────────────────
    st.subheader("📋 Indice file disponibili")

    with st.spinner("Caricamento indice…"):
        try:
            full_df = _load_full_index(dataset_repo)
        except Exception as exc:
            st.error(f"Impossibile caricare l'indice: {exc}")
            return

    mask = full_df["legislature"].isin(selected_legs)
    if selected_fams:
        mask &= full_df["family"].isin(selected_fams)
    filtered = full_df[mask].reset_index(drop=True)

    st.caption(
        f"Trovati **{len(filtered):,}** file corrispondenti "
        f"({'tutti i tipi' if not selected_fams else ', '.join(selected_fams)}). "
        f"Verranno processati i primi **{min(max_docs, len(filtered)):,}**."
    )

    preview = filtered.head(500)[["legislature", "atto_id", "family", "filename", "size", "url"]]
    st.dataframe(preview, use_container_width=True, hide_index=True)

    if filtered.empty:
        st.warning("Nessun documento trovato con i filtri selezionati.")
        return

    st.divider()

    # ── Streaming & download ─────────────────────────────────────
    st.subheader("⬇️ Streaming & Download")
    st.markdown(
        "Premi il pulsante per scaricare i testi XML in streaming direttamente dal Senato. "
        "Il risultato è un file **JSONL compresso** (`.jsonl.gz`) contenente metadati + XML."
    )

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        run_stream = st.button("🚀 Avvia streaming", type="primary", use_container_width=True)
    with col_info:
        st.caption(
            f"Scaricherà fino a {min(max_docs, len(filtered)):,} documenti XML "
            f"da `github.com/SenatoDellaRepubblica`."
        )

    if run_stream:
        subset = filtered.head(max_docs)
        records: List[Dict] = []
        ok = 0
        failed = 0
        started = time.time()

        progress = st.progress(0, text="Avvio…")
        status = st.empty()

        for idx, row in enumerate(subset.itertuples(index=False), 1):
            url = str(getattr(row, "url", ""))
            try:
                xml_text = _fetch_xml(url, timeout=45, retries=2)
                records.append(
                    {
                        "snapshot_at_utc": _now_iso(),
                        "source": "SenatoDellaRepubblica/AkomaNtosoBulkData",
                        "legislature": getattr(row, "legislature", None),
                        "atto_id": getattr(row, "atto_id", None),
                        "family": getattr(row, "family", None),
                        "filename": getattr(row, "filename", None),
                        "path": getattr(row, "path", None),
                        "sha": getattr(row, "sha", None),
                        "url": url,
                        "xml": xml_text,
                    }
                )
                ok += 1
            except Exception as exc:
                records.append(
                    {
                        "snapshot_at_utc": _now_iso(),
                        "source": "SenatoDellaRepubblica/AkomaNtosoBulkData",
                        "legislature": getattr(row, "legislature", None),
                        "path": getattr(row, "path", None),
                        "url": url,
                        "error": str(exc),
                    }
                )
                failed += 1

            pct = idx / len(subset)
            progress.progress(pct, text=f"{idx}/{len(subset)} — ok={ok} errori={failed}")
            if idx % 50 == 0:
                elapsed = time.time() - started
                status.caption(f"Velocità: {idx/elapsed:.1f} doc/s | Elapsed: {elapsed:.0f}s")

        progress.progress(1.0, text="Completato!")
        elapsed = time.time() - started
        status.caption(f"Completato in {elapsed:.1f}s — {ok} ok, {failed} errori.")

        if records:
            compressed = _jsonl_gz_bytes(records)
            legs_tag = "-".join(selected_legs)
            fam_tag = "all" if not selected_fams else "-".join(selected_fams)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            fname = f"senato-wayback-{legs_tag}-{fam_tag}-{ts}.jsonl.gz"

            st.download_button(
                label=f"💾 Scarica snapshot ({len(records):,} atti, {len(compressed)/1e6:.2f} MB)",
                data=compressed,
                file_name=fname,
                mime="application/gzip",
                use_container_width=True,
            )

            # Quick XML preview of first ok record
            first_ok = next((r for r in records if "xml" in r), None)
            if first_ok:
                with st.expander("👁️ Anteprima XML – primo documento"):
                    st.caption(f"`{first_ok.get('path')}`")
                    st.code(first_ok["xml"][:4000], language="xml")

    st.divider()

    # ── Static dataset info ──────────────────────────────────────
    st.subheader("ℹ️ Informazioni sulla fonte")
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(
            "**Repository Senato**\n"
            "[SenatoDellaRepubblica/AkomaNtosoBulkData](https://github.com/SenatoDellaRepubblica/AkomaNtosoBulkData)\n\n"
            "Aggiornamento automatico: ogni notte alle 23:00\n\n"
            "Licenza: **CC BY 3.0**"
        )
    with col_b:
        leg_rows = []
        if not summary.get("error"):
            for ls in summary.get("legislatures", []):
                leg_rows.append(
                    {
                        "Legislatura": ls.get("legislature"),
                        "Anni": LEG_YEARS.get(str(ls.get("legislature")), ""),
                        "File XML": f"{int(ls.get('xml_files', 0)):,}",
                        "Troncato?": "⚠️ Sì" if ls.get("truncated") else "✅ No",
                    }
                )
        if leg_rows:
            st.dataframe(pd.DataFrame(leg_rows), use_container_width=True, hide_index=True)
