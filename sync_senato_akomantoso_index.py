#!/usr/bin/env python3
"""Index Senato Akoma Ntoso bulk data into HF dataset.

This script builds a file-level index for the public repository:
https://github.com/SenatoDellaRepubblica/AkomaNtosoBulkData

It does NOT mirror all XML payloads by default (very large); instead it stores
metadata rows (path, size, legislature, family) plus a summary manifest.

Usage:
  python sync_senato_akomantoso_index.py --check-only
  python sync_senato_akomantoso_index.py --dataset-repo diatribe00/italian-legal-lab-data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests
from huggingface_hub import HfApi, hf_hub_download

REPO_OWNER = "SenatoDellaRepubblica"
REPO_NAME = "AkomaNtosoBulkData"

INDEX_JSONL_PATH = "data/senato_akomantoso/index/files.jsonl"
SUMMARY_PATH = "data/senato_akomantoso/index/summary.json"
MANIFEST_PATH = "data/senato_akomantoso/manifest.json"
REPORT_PATH = "logs/senato_akomantoso_sync_report.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _gh_headers(token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OpenNormattivaSenatoSync/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_json(session: requests.Session, url: str, headers: Dict[str, str], timeout: int = 120) -> Dict:
    resp = session.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def list_legislatures(session: requests.Session, gh_token: Optional[str]) -> List[Dict]:
    headers = _gh_headers(gh_token)
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents"
    data = _get_json(session, url, headers, timeout=60)
    out = [x for x in data if x.get("type") == "dir" and str(x.get("name", "")).startswith("Leg")]
    return sorted(out, key=lambda x: x["name"])


def fetch_leg_tree(
    session: requests.Session,
    leg_name: str,
    leg_sha: str,
    gh_token: Optional[str],
) -> Dict:
    headers = _gh_headers(gh_token)
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/git/trees/{leg_sha}?recursive=1"
    data = _get_json(session, url, headers, timeout=300)
    tree = data.get("tree", []) or []
    truncated = bool(data.get("truncated", False))

    rows: List[Dict] = []
    for item in tree:
        if item.get("type") != "blob":
            continue
        rel_path = str(item.get("path", ""))
        if not rel_path.lower().endswith(".xml"):
            continue

        parts = rel_path.split("/")
        atto_id = parts[0] if len(parts) > 0 else ""
        family = parts[1] if len(parts) > 1 else ""
        filename = parts[-1] if parts else ""

        rows.append(
            {
                "source_repo": f"{REPO_OWNER}/{REPO_NAME}",
                "legislature": leg_name,
                "atto_id": atto_id,
                "family": family,
                "filename": filename,
                "path": f"{leg_name}/{rel_path}",
                "size": int(item.get("size", 0) or 0),
                "sha": item.get("sha"),
                "url": f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/master/{leg_name}/{rel_path}",
            }
        )

    return {
        "legislature": leg_name,
        "tree_entries": len(tree),
        "truncated": truncated,
        "xml_rows": rows,
    }


def write_jsonl(path: Path, rows: Iterable[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_report(report: Dict) -> None:
    p = Path(REPORT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Index Senato AkomaNtoso bulk data into HF dataset")
    parser.add_argument(
        "--dataset-repo",
        default="diatribe00/italian-legal-lab-data",
        help="Hugging Face dataset repo id (owner/name)",
    )
    parser.add_argument(
        "--legislatures",
        default="Leg19,Leg18,Leg17",
        help="Comma-separated legislature folders to index (e.g. Leg19,Leg18)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="HF token (defaults to HF_TOKEN env var if omitted)",
    )
    parser.add_argument(
        "--github-token",
        default=None,
        help="Optional GitHub token to increase API rate limits",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only compute report, do not upload",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    hf_token = args.token if args.token is not None else None
    gh_token = args.github_token if args.github_token is not None else os.environ.get("GITHUB_TOKEN")

    wanted = [x.strip() for x in args.legislatures.split(",") if x.strip()]
    if not wanted:
        print("ERROR: At least one legislature is required.", file=sys.stderr)
        return 2

    session = requests.Session()
    available = {x["name"]: x for x in list_legislatures(session, gh_token)}

    missing = [x for x in wanted if x not in available]
    if missing:
        print(f"ERROR: Missing legislatures in repository: {', '.join(missing)}", file=sys.stderr)
        return 2

    all_rows: List[Dict] = []
    per_leg = []
    for leg in wanted:
        sha = available[leg]["sha"]
        print(f"Indexing {leg} (sha={sha})")
        block = fetch_leg_tree(session, leg, sha, gh_token)
        rows = block["xml_rows"]
        all_rows.extend(rows)
        per_leg.append(
            {
                "legislature": leg,
                "truncated": block["truncated"],
                "tree_entries": block["tree_entries"],
                "xml_files": len(rows),
            }
        )
        print(
            f"  entries={block['tree_entries']} xml_files={len(rows)} "
            f"truncated={block['truncated']}"
        )

    summary = {
        "source": f"{REPO_OWNER}/{REPO_NAME}",
        "indexed_at_utc": _now_iso(),
        "legislatures": per_leg,
        "total_xml_files": len(all_rows),
        "total_size_bytes": sum(int(r.get("size", 0)) for r in all_rows),
        "coverage_note": "Git tree API may set truncated=true for very large trees; counts are best-effort snapshots.",
    }

    current_manifest = {
        "source": "Senato AkomaNtoso BulkData index",
        "dataset_repo": args.dataset_repo,
        "updated_at_utc": _now_iso(),
        "source_repo": f"{REPO_OWNER}/{REPO_NAME}",
        "target_legislatures": wanted,
        "summary_path": SUMMARY_PATH,
        "index_path": INDEX_JSONL_PATH,
        "total_xml_files": summary["total_xml_files"],
        "total_size_bytes": summary["total_size_bytes"],
        "rows_hash": _stable_hash(all_rows),
        "legislature_stats": per_leg,
    }

    previous = load_remote_manifest(args.dataset_repo, hf_token)
    changed = (
        previous.get("rows_hash") != current_manifest.get("rows_hash")
        or previous.get("target_legislatures") != current_manifest.get("target_legislatures")
    )

    report = {
        "dataset_repo": args.dataset_repo,
        "checked_at_utc": _now_iso(),
        "changed": bool(changed),
        "check_only": args.check_only,
        "summary": summary,
        "manifest": {
            "total_xml_files": current_manifest["total_xml_files"],
            "total_size_bytes": current_manifest["total_size_bytes"],
            "rows_hash": current_manifest["rows_hash"],
        },
    }
    write_report(report)

    print(f"Indexed XML rows: {len(all_rows)}")
    print(f"Changed: {changed}")

    if args.check_only:
        return 0

    hf_token = hf_token or os.environ.get("HF_TOKEN")
    if not hf_token:
        print("ERROR: HF token required for upload. Set HF_TOKEN or pass --token.", file=sys.stderr)
        return 2

    if not changed:
        print("No changes detected in Senato index. Nothing to upload.")
        return 0

    api = HfApi(token=hf_token)
    with tempfile.TemporaryDirectory(prefix="senato_akn_index_") as tmp:
        root = Path(tmp)

        write_jsonl(root / INDEX_JSONL_PATH, all_rows)
        (root / SUMMARY_PATH).parent.mkdir(parents=True, exist_ok=True)
        (root / SUMMARY_PATH).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        (root / MANIFEST_PATH).parent.mkdir(parents=True, exist_ok=True)
        (root / MANIFEST_PATH).write_text(
            json.dumps(current_manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        api.upload_folder(
            repo_id=args.dataset_repo,
            repo_type="dataset",
            folder_path=str(root),
            commit_message=(
                f"Senato AkomaNtoso index sync: {len(wanted)} legislature(s), "
                f"{len(all_rows)} xml rows"
            ),
        )

    print(f"Uploaded Senato AkomaNtoso index to {args.dataset_repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
