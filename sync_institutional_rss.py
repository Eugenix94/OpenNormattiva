#!/usr/bin/env python3
"""Sync institutional Italian legal RSS feeds into HF dataset snapshots.

This job writes:
- data/legal_rss/manifest.json
- data/legal_rss/latest.jsonl
- data/legal_rss/snapshots/YYYY-MM-DD.jsonl

It stores normalized RSS items and source health, enabling day-by-day diffs
and historical analysis in the Streamlit assistant.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

import requests
from huggingface_hub import HfApi, hf_hub_download

MANIFEST_PATH = "data/legal_rss/manifest.json"
LATEST_PATH = "data/legal_rss/latest.jsonl"
SNAPSHOT_PREFIX = "data/legal_rss/snapshots"
REPORT_PATH = "logs/institutional_rss_sync_report.json"


@dataclass(frozen=True)
class RSSSource:
    key: str
    name: str
    institution: str
    category: str
    url: str


SOURCES: List[RSSSource] = [
    RSSSource(
        key="gazzetta_serie_generale",
        name="Gazzetta Ufficiale - Serie Generale",
        institution="Istituto Poligrafico e Zecca dello Stato",
        category="Normativa",
        url="https://www.gazzettaufficiale.it/rss/SG",
    ),
    RSSSource(
        key="gazzetta_concorsi",
        name="Gazzetta Ufficiale - Concorsi",
        institution="Istituto Poligrafico e Zecca dello Stato",
        category="Concorsi",
        url="https://www.gazzettaufficiale.it/rss/C",
    ),
    RSSSource(
        key="normattiva_news",
        name="Normattiva - News",
        institution="Normattiva",
        category="Portale normativo",
        url="https://www.normattiva.it/it/feed/",
    ),
    RSSSource(
        key="corte_news",
        name="Corte Costituzionale - News",
        institution="Corte Costituzionale",
        category="Giurisprudenza",
        url="https://www.cortecostituzionale.it/rss.xml",
    ),
    RSSSource(
        key="senato_news",
        name="Senato della Repubblica - Notizie",
        institution="Senato della Repubblica",
        category="Parlamento",
        url="https://www.senato.it/rss.xml",
    ),
    RSSSource(
        key="camera_news",
        name="Camera dei Deputati - Notizie",
        institution="Camera dei Deputati",
        category="Parlamento",
        url="https://www.camera.it/application/xmanager/projects/leg18/attachments/rss_feed/rss_feed.xml",
    ),
    RSSSource(
        key="governo_news",
        name="Governo Italiano - Comunicati",
        institution="Presidenza del Consiglio",
        category="Esecutivo",
        url="https://www.governo.it/it/rss",
    ),
    RSSSource(
        key="giustizia_news",
        name="Ministero della Giustizia - News",
        institution="Ministero della Giustizia",
        category="Ministeri",
        url="https://www.giustizia.it/giustizia/it/rss.page",
    ),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _stable_hash(obj: object) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_dt(value: str) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return ""


def _xml_local(tag: str) -> str:
    if not tag:
        return ""
    if "}" in tag:
        return tag.split("}", 1)[1].lower()
    return tag.lower()


def _looks_like_xml(response: requests.Response) -> bool:
    ctype = str(response.headers.get("content-type", "")).lower()
    if "xml" in ctype:
        return True
    text = (response.text or "").lstrip()
    return text.startswith("<?xml") or text.startswith("<rss") or text.startswith("<feed")


def _parse_feed_items(xml_text: str, src: RSSSource) -> List[Dict]:
    root = ET.fromstring(xml_text)

    entries: List[ET.Element] = []
    for node in root.iter():
        local = _xml_local(node.tag)
        if local in {"item", "entry"}:
            entries.append(node)

    rows: List[Dict] = []
    for item in entries:
        data: Dict[str, str] = {}
        for child in list(item):
            local = _xml_local(child.tag)
            txt = (child.text or "").strip()
            if local == "link":
                link_attr = (child.attrib.get("href") or "").strip()
                data["link"] = txt or link_attr
            elif local in {"title", "description", "summary", "pubdate", "date", "updated", "published"}:
                data[local] = txt

        pub = data.get("pubdate") or data.get("published") or data.get("updated") or data.get("date") or ""
        desc = data.get("description") or data.get("summary") or ""
        rows.append(
            {
                "source_key": src.key,
                "source_name": src.name,
                "institution": src.institution,
                "category": src.category,
                "title": data.get("title", ""),
                "link": data.get("link", ""),
                "published": pub,
                "published_utc": _safe_dt(pub),
                "description": desc[:1000],
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
    dedup = list(dict.fromkeys(out))
    return dedup


def _fetch_gazzetta_fallback(session: requests.Session, src: RSSSource, timeout: int) -> List[Dict]:
    homepage = session.get(
        "https://www.gazzettaufficiale.it",
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    homepage.raise_for_status()

    candidates = _extract_gu_rss_paths(homepage.text)
    if not candidates:
        return []

    parsed: List[tuple[str, List[Dict]]] = []
    for path in candidates:
        url = f"https://www.gazzettaufficiale.it/{path}"
        try:
            resp = session.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            if not _looks_like_xml(resp):
                continue
            items = _parse_feed_items(resp.text, src)
            if items:
                parsed.append((url.lower(), items))
        except Exception:
            continue

    if not parsed:
        return []

    # Prefer path by source kind, then fallback to first feed with items.
    wanted = "conc" if "concorsi" in src.key else "sg"
    for url, items in parsed:
        if wanted in url:
            return items
    return parsed[0][1]


def _fetch_source(session: requests.Session, src: RSSSource, timeout: int = 25) -> Dict:
    out = {
        "source_key": src.key,
        "source_name": src.name,
        "institution": src.institution,
        "category": src.category,
        "url": src.url,
        "checked_at_utc": _now_iso(),
        "ok": False,
        "http_status": None,
        "warning": None,
        "error": None,
        "items": [],
    }

    try:
        r = session.get(src.url, timeout=timeout, headers={"User-Agent": "OpenNormattivaRSSSync/1.0"})
        out["http_status"] = r.status_code
        r.raise_for_status()
        items: List[Dict] = []
        if _looks_like_xml(r):
            items = _parse_feed_items(r.text, src)
        elif "gazzettaufficiale.it" in src.url:
            # GU can return HTML in server contexts for direct feed URLs.
            items = _fetch_gazzetta_fallback(session, src, timeout=timeout)
            if items:
                out["warning"] = "Recovered via Gazzetta homepage RSS discovery fallback"

        out["items"] = items
        out["ok"] = bool(items)
        if not items and not out.get("error"):
            out["error"] = "No feed items parsed from response"
        return out
    except Exception as exc:
        out["error"] = str(exc)
        return out


def _normalize_items(per_source: List[Dict], max_per_source: int) -> List[Dict]:
    out: List[Dict] = []
    for src in per_source:
        subset = src.get("items", [])[:max_per_source]
        for item in subset:
            payload = {
                "snapshot_at_utc": _now_iso(),
                **item,
            }
            payload["item_hash"] = _stable_hash(
                {
                    "source_key": payload.get("source_key"),
                    "title": payload.get("title"),
                    "link": payload.get("link"),
                    "published": payload.get("published"),
                }
            )
            out.append(payload)

    out.sort(key=lambda x: (x.get("published_utc") or "", x.get("source_key") or ""), reverse=True)
    return out


def _build_manifest(dataset_repo: str, snapshot_date: str, per_source: List[Dict], items: List[Dict]) -> Dict:
    files = {
        "latest_jsonl": LATEST_PATH,
        "snapshot_jsonl": f"{SNAPSHOT_PREFIX}/{snapshot_date}.jsonl",
    }
    source_meta = []
    for src in per_source:
        source_meta.append(
            {
                "source_key": src.get("source_key"),
                "source_name": src.get("source_name"),
                "institution": src.get("institution"),
                "category": src.get("category"),
                "url": src.get("url"),
                "ok": bool(src.get("ok", False)),
                "http_status": src.get("http_status"),
                "error": src.get("error"),
                "item_count": len(src.get("items", [])),
            }
        )

    return {
        "source": "Italian institutional legal RSS sync",
        "dataset_repo": dataset_repo,
        "updated_at_utc": _now_iso(),
        "snapshot_date": snapshot_date,
        "total_sources": len(per_source),
        "ok_sources": sum(1 for s in per_source if s.get("ok")),
        "total_items": len(items),
        "items_hash": _stable_hash(items),
        "files": files,
        "sources": source_meta,
    }


def _load_remote_manifest(dataset_repo: str, token: Optional[str]) -> Dict:
    try:
        p = hf_hub_download(
            repo_id=dataset_repo,
            repo_type="dataset",
            filename=MANIFEST_PATH,
            token=token,
        )
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_jsonl(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_report(report: Dict) -> None:
    p = Path(REPORT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync institutional RSS feeds to HF dataset")
    parser.add_argument(
        "--dataset-repo",
        default="diatribe00/italian-legal-lab-data",
        help="HF dataset repo id (owner/name)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="HF token (defaults to HF_TOKEN env var if omitted)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check for changes and write logs/report",
    )
    parser.add_argument(
        "--max-items-per-source",
        type=int,
        default=120,
        help="Maximum items persisted per source for each snapshot",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = args.token if args.token is not None else None

    if args.max_items_per_source <= 0:
        print("ERROR: --max-items-per-source must be > 0", file=sys.stderr)
        return 2

    session = requests.Session()
    per_source: List[Dict] = []
    for src in SOURCES:
        print(f"Fetching {src.key} -> {src.url}")
        row = _fetch_source(session, src)
        per_source.append(row)
        print(f"  ok={row.get('ok')} status={row.get('http_status')} items={len(row.get('items', []))}")

    items = _normalize_items(per_source, max_per_source=args.max_items_per_source)
    snapshot_date = _today_utc()
    manifest = _build_manifest(args.dataset_repo, snapshot_date, per_source, items)
    previous = _load_remote_manifest(args.dataset_repo, token)

    changed = (
        previous.get("items_hash") != manifest.get("items_hash")
        or previous.get("ok_sources") != manifest.get("ok_sources")
        or previous.get("snapshot_date") != manifest.get("snapshot_date")
    )

    report = {
        "dataset_repo": args.dataset_repo,
        "checked_at_utc": _now_iso(),
        "check_only": args.check_only,
        "changed": changed,
        "snapshot_date": snapshot_date,
        "total_items": len(items),
        "ok_sources": manifest.get("ok_sources", 0),
        "total_sources": manifest.get("total_sources", 0),
        "sources": [
            {
                "source_key": s.get("source_key"),
                "ok": s.get("ok"),
                "http_status": s.get("http_status"),
                "warning": s.get("warning"),
                "warning": s.get("warning"),
                "item_count": len(s.get("items", [])),
                "error": s.get("error"),
            }
            for s in per_source
        ],
    }
    _write_report(report)

    print(f"Collected sources: {len(per_source)}")
    print(f"Normalized items: {len(items)}")
    print(f"Changes detected: {changed}")

    if args.check_only:
        return 0

    token = token or os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: HF token required for upload. Set HF_TOKEN or pass --token.", file=sys.stderr)
        return 2

    if not changed:
        print("No RSS changes detected. Nothing to upload.")
        return 0

    with tempfile.TemporaryDirectory(prefix="institutional_rss_") as tmp:
        root = Path(tmp)
        latest_path = root / LATEST_PATH
        snap_path = root / f"{SNAPSHOT_PREFIX}/{snapshot_date}.jsonl"
        manifest_path = root / MANIFEST_PATH

        _write_jsonl(latest_path, items)
        _write_jsonl(snap_path, items)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        api = HfApi(token=token)
        api.upload_folder(
            repo_id=args.dataset_repo,
            repo_type="dataset",
            folder_path=str(root),
            commit_message=f"Institutional RSS sync: {len(items)} items ({snapshot_date})",
        )

    print(f"Uploaded RSS snapshot + manifest to {args.dataset_repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
