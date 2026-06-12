#!/usr/bin/env python3
"""Sync Corte Costituzionale open-data ZIPs into a Hugging Face dataset repo.

Behavior:
- Discovers all ZIP links from the official Corte page.
- Reads prior sync manifest from HF dataset (if present).
- Downloads only new/changed files.
- Uploads changed files + refreshed manifest back to HF dataset.

Usage examples:
  python sync_corte_costituzionale.py --check-only
  python sync_corte_costituzionale.py --dataset-repo diatribe00/italian-legal-lab-data
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from huggingface_hub import HfApi, hf_hub_download

CORTE_PAGE_URL = "https://dati.cortecostituzionale.it/Scarica_i_dati/Scarica_i_dati"
MANIFEST_PATH = "data/corte_costituzionale/manifest.json"
REPORT_PATH = "logs/corte_sync_report.json"


@dataclass
class LinkMeta:
    url: str
    repo_path: str
    size: int
    etag: Optional[str]
    last_modified: Optional[str]
    family: str


def infer_family(url: str) -> str:
    u = url.lower()
    if "/anagrafica_giudici/" in u:
        return "anagrafica_giudici"
    if "/norme_impugnate/" in u:
        return "norme_impugnate"
    if "massime" in u:
        return "massime"
    if "pronunce" in u:
        return "pronunce"
    return "other"


def link_to_repo_path(url: str) -> str:
    parsed = urlparse(url)
    marker = "/opendata/"
    idx = parsed.path.lower().find(marker)
    if idx >= 0:
        suffix = parsed.path[idx + len(marker) :].lstrip("/")
    else:
        suffix = Path(parsed.path).name
    return f"data/corte_costituzionale/raw/{suffix}"


def discover_links(session: requests.Session) -> List[str]:
    html = session.get(CORTE_PAGE_URL, timeout=30).text
    hrefs = sorted(set(re.findall(r'href="([^\"]+\.zip)"', html, flags=re.I)))
    links = []
    for href in hrefs:
        absolute = href if href.startswith("http") else urljoin(CORTE_PAGE_URL, href)
        if "/opendata/" in absolute:
            links.append(absolute)
    return sorted(set(links))


def probe_size_and_headers(session: requests.Session, url: str) -> tuple[int, Optional[str], Optional[str]]:
    size = None
    etag = None
    last_modified = None

    try:
        resp = session.head(url, allow_redirects=True, timeout=30)
        if resp.ok:
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit():
                size = int(cl)
            etag = resp.headers.get("ETag")
            last_modified = resp.headers.get("Last-Modified")
    except requests.RequestException:
        pass

    if size is None:
        # Fallback for servers that don't implement HEAD consistently.
        resp = session.get(url, stream=True, allow_redirects=True, timeout=30)
        resp.raise_for_status()
        cl = resp.headers.get("Content-Length")
        if cl and cl.isdigit():
            size = int(cl)
        etag = etag or resp.headers.get("ETag")
        last_modified = last_modified or resp.headers.get("Last-Modified")
        resp.close()

    return size or 0, etag, last_modified


def build_current_snapshot(session: requests.Session) -> Dict[str, LinkMeta]:
    links = discover_links(session)
    snapshot: Dict[str, LinkMeta] = {}

    for url in links:
        size, etag, last_modified = probe_size_and_headers(session, url)
        snapshot[url] = LinkMeta(
            url=url,
            repo_path=link_to_repo_path(url),
            size=size,
            etag=etag,
            last_modified=last_modified,
            family=infer_family(url),
        )

    return snapshot


def load_remote_manifest(dataset_repo: str, token: Optional[str]) -> Dict:
    try:
        path = hf_hub_download(
            repo_id=dataset_repo,
            repo_type="dataset",
            filename=MANIFEST_PATH,
            token=token,
        )
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def diff_changes(previous_manifest: Dict, current: Dict[str, LinkMeta]) -> List[LinkMeta]:
    prev_files = previous_manifest.get("files", {})
    changed: List[LinkMeta] = []

    for url, meta in current.items():
        prev = prev_files.get(url)
        if not prev:
            changed.append(meta)
            continue

        # Prefer ETag when available, fallback to last-modified + size.
        if meta.etag and prev.get("etag") and meta.etag != prev.get("etag"):
            changed.append(meta)
            continue

        if meta.size != int(prev.get("size", -1)):
            changed.append(meta)
            continue

        if meta.last_modified and prev.get("last_modified") and meta.last_modified != prev.get("last_modified"):
            changed.append(meta)
            continue

    return changed


def download_file(session: requests.Session, url: str, dst_path: Path) -> None:
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with session.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with dst_path.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)


def build_manifest(current: Dict[str, LinkMeta], changed: List[LinkMeta], dataset_repo: str) -> Dict:
    families: Dict[str, int] = {}
    total_size = 0
    for meta in current.values():
        families[meta.family] = families.get(meta.family, 0) + 1
        total_size += meta.size

    files = {
        url: {
            "repo_path": meta.repo_path,
            "size": meta.size,
            "etag": meta.etag,
            "last_modified": meta.last_modified,
            "family": meta.family,
        }
        for url, meta in current.items()
    }

    return {
        "source": "Corte Costituzionale Open Data",
        "source_page": CORTE_PAGE_URL,
        "dataset_repo": dataset_repo,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_files": len(current),
        "total_size_bytes": total_size,
        "families": families,
        "changed_in_last_sync": [m.url for m in changed],
        "files": files,
    }


def write_report(report: Dict) -> None:
    report_path = Path(REPORT_PATH)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Corte Costituzionale ZIPs to HF dataset")
    parser.add_argument(
        "--dataset-repo",
        default="diatribe00/italian-legal-lab-data",
        help="Hugging Face dataset repo id (owner/name)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="HF token (defaults to HF_TOKEN env var if omitted)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only compute changes and write report, do not upload",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = args.token if args.token is not None else None

    session = requests.Session()
    current = build_current_snapshot(session)
    previous_manifest = load_remote_manifest(args.dataset_repo, token)
    changed = diff_changes(previous_manifest, current)

    total_size = sum(m.size for m in current.values())
    print(f"Discovered ZIPs: {len(current)}")
    print(f"Total size: {total_size / 1e9:.3f} GB")
    print(f"Changed/new files: {len(changed)}")

    report = {
        "dataset_repo": args.dataset_repo,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_files": len(current),
        "total_size_bytes": total_size,
        "changed_files": [m.url for m in changed],
        "check_only": args.check_only,
    }
    write_report(report)

    if args.check_only:
        if changed:
            print("Changes detected (check-only mode, no upload).")
        else:
            print("No changes detected.")
        return 0

    token = token or os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: HF token required for upload. Set HF_TOKEN or pass --token.", file=sys.stderr)
        return 2

    if not changed:
        print("No new/updated Corte files. Nothing to upload.")
        return 0

    api = HfApi(token=token)
    with tempfile.TemporaryDirectory(prefix="corte_sync_") as tmp:
        root = Path(tmp)

        # Download changed files only.
        for idx, meta in enumerate(changed, start=1):
            print(f"[{idx}/{len(changed)}] Downloading {meta.url}")
            dst = root / meta.repo_path
            download_file(session, meta.url, dst)

        manifest = build_manifest(current, changed, args.dataset_repo)
        manifest_path = root / MANIFEST_PATH
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        api.upload_folder(
            repo_id=args.dataset_repo,
            repo_type="dataset",
            folder_path=str(root),
            commit_message=f"Corte sync: {len(changed)} file(s) updated",
        )

    print(f"Uploaded {len(changed)} changed file(s) + manifest to {args.dataset_repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
