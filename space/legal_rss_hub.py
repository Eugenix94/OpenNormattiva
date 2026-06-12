"""Institutional legal RSS hub for Italian public sources."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List
from xml.etree import ElementTree as ET

import pandas as pd
import requests
import streamlit as st
from streamlit.components.v1 import html

RSS_SOURCES = [
    {
        "name": "Gazzetta Ufficiale - Serie Generale",
        "institution": "Istituto Poligrafico e Zecca dello Stato",
        "category": "Normativa",
        "url": "https://www.gazzettaufficiale.it/rss/SG",
    },
    {
        "name": "Gazzetta Ufficiale - Concorsi",
        "institution": "Istituto Poligrafico e Zecca dello Stato",
        "category": "Concorsi",
        "url": "https://www.gazzettaufficiale.it/rss/C",
    },
    {
        "name": "Normattiva - News",
        "institution": "Normattiva",
        "category": "Portale normativo",
        "url": "https://www.normattiva.it/it/feed/",
    },
    {
        "name": "Corte Costituzionale - News",
        "institution": "Corte Costituzionale",
        "category": "Giurisprudenza",
        "url": "https://www.cortecostituzionale.it/rss.xml",
    },
    {
        "name": "Senato della Repubblica - Notizie",
        "institution": "Senato della Repubblica",
        "category": "Parlamento",
        "url": "https://www.senato.it/rss.xml",
    },
    {
        "name": "Camera dei Deputati - Notizie",
        "institution": "Camera dei Deputati",
        "category": "Parlamento",
        "url": "https://www.camera.it/application/xmanager/projects/leg18/attachments/rss_feed/rss_feed.xml",
    },
    {
        "name": "Governo Italiano - Comunicati",
        "institution": "Presidenza del Consiglio",
        "category": "Esecutivo",
        "url": "https://www.governo.it/it/rss",
    },
    {
        "name": "Ministero della Giustizia - News",
        "institution": "Ministero della Giustizia",
        "category": "Ministeri",
        "url": "https://www.giustizia.it/giustizia/it/rss.page",
    },
]


def _safe_dt(value: str):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return None


def _xml_local(tag: str) -> str:
    if not tag:
        return ""
    if "}" in tag:
        return tag.split("}", 1)[1].lower()
    return tag.lower()


def _looks_like_xml(text: str, content_type: str) -> bool:
    ctype = (content_type or "").lower()
    if "xml" in ctype:
        return True
    t = (text or "").lstrip()
    return t.startswith("<?xml") or t.startswith("<rss") or t.startswith("<feed")


def _extract_items(xml_text: str):
    root = ET.fromstring(xml_text)
    entries = []
    for node in root.iter():
        if _xml_local(node.tag) in {"item", "entry"}:
            entries.append(node)

    rows = []
    for item in entries:
        data = {}
        for child in list(item):
            local = _xml_local(child.tag)
            txt = (child.text or "").strip()
            if local == "link":
                data["link"] = txt or (child.attrib.get("href") or "").strip()
            elif local in {"title", "description", "summary", "pubdate", "date", "updated", "published"}:
                data[local] = txt

        pub = data.get("pubdate") or data.get("published") or data.get("updated") or data.get("date") or ""
        rows.append(
            {
                "title": data.get("title", ""),
                "link": data.get("link", ""),
                "published": pub,
                "published_dt": _safe_dt(pub),
                "description": (data.get("description") or data.get("summary") or "")[:500],
            }
        )
    return rows


def _extract_gu_rss_paths(home_html: str) -> List[str]:
    marker = 'href="rss/'
    out: List[str] = []
    i = 0
    while True:
        j = home_html.find(marker, i)
        if j < 0:
            break
        k = home_html.find('"', j + len(marker))
        if k < 0:
            break
        out.append("rss/" + home_html[j + len(marker):k])
        i = k + 1
    return list(dict.fromkeys(out))


def _fetch_gazzetta_fallback(source: Dict, timeout: int = 20) -> List[Dict]:
    home = requests.get(
        "https://www.gazzettaufficiale.it",
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    home.raise_for_status()
    paths = _extract_gu_rss_paths(home.text)
    if not paths:
        return []

    parsed = []
    for p in paths:
        url = f"https://www.gazzettaufficiale.it/{p}"
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            if not _looks_like_xml(r.text, r.headers.get("content-type", "")):
                continue
            items = _extract_items(r.text)
            if items:
                parsed.append((url.lower(), items))
        except Exception:
            continue

    if not parsed:
        return []

    want = "conc" if "Concorsi" in source.get("name", "") else "sg"
    for u, items in parsed:
        if want in u:
            return items
    return parsed[0][1]


def _collect_items(selected_sources: List[Dict], max_items: int):
    results = [_fetch_rss_source(src) for src in selected_sources]

    status_rows = []
    all_items = []
    for res in results:
        src = res["source"]
        status_rows.append(
            {
                "Fonte": src["name"],
                "Istituzione": src["institution"],
                "Categoria": src["category"],
                "OK": "✅" if res["ok"] else "❌",
                "Elementi": len(res["items"]),
                "Errore": res["error"] or "",
            }
        )
        for item in res["items"][:max_items]:
            all_items.append(
                {
                    "source": src["name"],
                    "institution": src["institution"],
                    "category": src["category"],
                    "title": item["title"],
                    "published": item["published"],
                    "published_dt": item["published_dt"],
                    "link": item["link"],
                    "description": item["description"],
                }
            )

    return status_rows, all_items


@st.cache_data(ttl=900, show_spinner=False)
def _fetch_rss_source(source: Dict, timeout: int = 20) -> Dict:
    url = source["url"]
    out = {
        "source": source,
        "ok": False,
        "items": [],
        "error": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "ItalianLegalLab/1.0"})
        r.raise_for_status()
        if _looks_like_xml(r.text, r.headers.get("content-type", "")):
            items = _extract_items(r.text)
        elif "gazzettaufficiale.it" in url:
            items = _fetch_gazzetta_fallback(source, timeout=timeout)
        else:
            items = []
        out["ok"] = True
        out["items"] = items
        if not items:
            out["ok"] = False
            out["error"] = "No feed items parsed from source response"
        return out
    except Exception as exc:
        out["error"] = str(exc)
        return out


def render_legal_rss_hub_page():
    st.header("📰 RSS Ufficiali Istituzioni Giuridiche")
    st.caption(
        "Aggregatore unico delle fonti RSS istituzionali italiane. "
        "Partenza da Gazzetta Ufficiale, esteso a Parlamento, Corte e Governo."
    )

    st.info(
        "Nota: non tutte le amministrazioni pubblicano feed con formato uniforme. "
        "Le fonti non raggiungibili o non conformi vengono segnalate come warning."
    )

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        selected_names = st.multiselect(
            "Seleziona fonti da interrogare",
            options=[s["name"] for s in RSS_SOURCES],
            default=[s["name"] for s in RSS_SOURCES[:4]],
        )
    with c2:
        max_items = st.slider("Max notizie per fonte", 5, 80, 25, 5)
    with c3:
        auto_refresh = st.checkbox("Auto refresh", value=False, help="Ricarica automaticamente la pagina RSS.")

    refresh_seconds = st.slider("Intervallo refresh (secondi)", 30, 1800, 300, 30, disabled=not auto_refresh)

    selected_sources = [s for s in RSS_SOURCES if s["name"] in selected_names]
    if not selected_sources:
        st.warning("Seleziona almeno una fonte RSS.")
        return

    if auto_refresh:
        html(
            f"""
            <script>
            setTimeout(function() {{
                window.parent.location.reload();
            }}, {int(refresh_seconds * 1000)});
            </script>
            """,
            height=0,
        )
        st.caption(f"Aggiornamento automatico attivo ogni {refresh_seconds} secondi.")

    refresh_now = st.button("Aggiorna feed", type="primary")
    if refresh_now or auto_refresh:
        status_rows, all_items = _collect_items(selected_sources, max_items)
        st.subheader("Stato sorgenti")
        st.dataframe(pd.DataFrame(status_rows), use_container_width=True, hide_index=True)

        if all_items:
            df = pd.DataFrame(all_items)
            if "published_dt" in df.columns:
                df = df.sort_values("published_dt", ascending=False, na_position="last")

            st.subheader(f"Notizie aggregate ({len(df):,})")
            st.dataframe(
                df[["published", "institution", "category", "title", "link"]],
                use_container_width=True,
                hide_index=True,
            )

            st.session_state["rss_last_items"] = df[
                ["source", "institution", "category", "title", "published", "link", "description"]
            ].head(300).to_dict("records")
            st.session_state["rss_last_updated"] = datetime.now(timezone.utc).isoformat()

            export_jsonl = "\n".join(json.dumps(r, ensure_ascii=False) for r in st.session_state["rss_last_items"]).encode("utf-8")
            st.download_button(
                "Scarica RSS aggregati (JSONL)",
                data=export_jsonl,
                file_name="istituzioni_legali_rss.jsonl",
                mime="application/jsonl",
                use_container_width=True,
            )

            with st.expander("Anteprima contenuti"):
                for row in st.session_state["rss_last_items"][:20]:
                    st.markdown(f"**{row['title']}**")
                    st.caption(f"{row['institution']} • {row['published']}")
                    if row.get("description"):
                        st.write(row["description"])
                    if row.get("link"):
                        st.write(row["link"])
                    st.divider()
        else:
            st.warning("Nessuna notizia estratta dalle fonti selezionate.")
