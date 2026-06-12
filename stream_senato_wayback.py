#!/usr/bin/env python3
"""Stream Senato Akoma Ntoso XML documents for Wayback-style snapshots.

This script reads the indexed file catalog from HF dataset and streams selected
XML documents directly from SenatoDellaRepubblica/AkomaNtosoBulkData raw URLs.
It writes newline-delimited JSON snapshots (optionally gzip-compressed).

Typical usage:
  python stream_senato_wayback.py --legislatures Leg13 --max-docs 500
  python stream_senato_wayback.py --legislatures Leg17,Leg18 --families ddlpres,ddlmess --max-docs 2000 --gzip
  python stream_senato_wayback.py --legislatures Leg19 --max-docs 1000 --upload
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests
from huggingface_hub import HfApi, hf_hub_download

INDEX_PATH = "data/senato_akomantoso/index/files.jsonl"
WAYBACK_ROOT = "data/senato_akomantoso/wayback"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_csv_arg(value: str) -> List[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def iter_index_rows(dataset_repo: str, token: Optional[str]) -> Iterable[Dict]:
    path = hf_hub_download(
        repo_id=dataset_repo,
        repo_type="dataset",
        filename=INDEX_PATH,
        token=token,
    )
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def filter_rows(rows: Iterable[Dict], legislatures: List[str], families: List[str]) -> List[Dict]:
    out = []
    wanted_leg = set(legislatures)
    wanted_fam = set(families) if families else set()

    for row in rows:
        leg = str(row.get("legislature", ""))
        fam = str(row.get("family", ""))
        if wanted_leg and leg not in wanted_leg:
            continue
        if wanted_fam and fam not in wanted_fam:
            continue
        out.append(row)
    return out


def fetch_text(session: requests.Session, url: str, timeout: int, retries: int) -> str:
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * attempt)
    raise RuntimeError(str(last_error))


def _open_out(path: Path, use_gzip: bool):
    if use_gzip:
        return gzip.open(path, "wt", encoding="utf-8")
    return path.open("w", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream Senato Wayback snapshot from indexed AkomaNtoso files")
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
        "--legislatures",
        required=True,
        help="Comma-separated legislature names (e.g. Leg13,Leg14)",
    )
    parser.add_argument(
        "--families",
        default="",
        help="Optional comma-separated families (ddlpres,ddlcomm,ddlmess,emend,emendc,resaula,sommcomm)",
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=1000,
        help="Maximum number of XML documents to stream",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Per-request timeout in seconds",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retries per document request",
    )
    parser.add_argument(
        "--gzip",
        action="store_true",
        help="Write output as .jsonl.gz",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload generated snapshot to HF dataset",
    )
    parser.add_argument(
        "--snapshot-name",
        default="",
        help="Optional snapshot file name (without extension)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = args.token if args.token is not None else os.environ.get("HF_TOKEN")

    legislatures = _parse_csv_arg(args.legislatures)
    families = _parse_csv_arg(args.families)
    if not legislatures:
        print("ERROR: At least one legislature is required.", file=sys.stderr)
        return 2
    if args.max_docs <= 0:
        print("ERROR: --max-docs must be > 0.", file=sys.stderr)
        return 2

    print(f"Loading index from {args.dataset_repo}...")
    rows = filter_rows(iter_index_rows(args.dataset_repo, token), legislatures, families)
    print(f"Candidate rows: {len(rows)}")
    if not rows:
        print("No rows match the requested filters.")
        return 0

    rows = rows[: args.max_docs]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.snapshot_name:
        base_name = args.snapshot_name
    else:
        legs = "-".join(legislatures)
        fam = "all" if not families else "-".join(families)
        base_name = f"senato-wayback-{legs}-{fam}-{timestamp}"

    ext = ".jsonl.gz" if args.gzip else ".jsonl"
    out_path = Path("data") / "senato_akomantoso" / "wayback_local" / f"{base_name}{ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ok = 0
    failed = 0
    session = requests.Session()
    started = time.time()

    with _open_out(out_path, args.gzip) as fh:
        for idx, row in enumerate(rows, start=1):
            url = str(row.get("url", ""))
            if not url:
                failed += 1
                continue

            try:
                xml_text = fetch_text(session, url, timeout=args.timeout, retries=args.retries)
                payload = {
                    "snapshot_at_utc": _now_iso(),
                    "source": "SenatoDellaRepubblica/AkomaNtosoBulkData",
                    "legislature": row.get("legislature"),
                    "atto_id": row.get("atto_id"),
                    "family": row.get("family"),
                    "filename": row.get("filename"),
                    "path": row.get("path"),
                    "sha": row.get("sha"),
                    "url": url,
                    "xml": xml_text,
                }
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
                ok += 1
            except Exception as exc:
                failed += 1
                err_payload = {
                    "snapshot_at_utc": _now_iso(),
                    "source": "SenatoDellaRepubblica/AkomaNtosoBulkData",
                    "legislature": row.get("legislature"),
                    "path": row.get("path"),
                    "url": url,
                    "error": str(exc),
                }
                fh.write(json.dumps(err_payload, ensure_ascii=False) + "\n")

            if idx % 100 == 0:
                print(f"Processed {idx}/{len(rows)} (ok={ok}, failed={failed})")

    elapsed = time.time() - started
    size_mb = out_path.stat().st_size / 1e6
    print(f"Snapshot written: {out_path}")
    print(f"Rows processed: {len(rows)} | ok={ok} failed={failed}")
    print(f"Output size: {size_mb:.2f} MB | elapsed: {elapsed:.1f}s")

    if not args.upload:
        return 0

    if not token:
        print("ERROR: HF token required for upload (set HF_TOKEN or pass --token).", file=sys.stderr)
        return 2

    api = HfApi(token=token)
    repo_target = f"{WAYBACK_ROOT}/snapshots/{out_path.name}"
    api.upload_file(
        repo_id=args.dataset_repo,
        repo_type="dataset",
        path_or_fileobj=str(out_path),
        path_in_repo=repo_target,
        commit_message=(
            f"Senato wayback stream: {','.join(legislatures)}"
            f" ({ok} ok / {failed} failed)"
        ),
    )
    print(f"Uploaded snapshot to {args.dataset_repo}:{repo_target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
