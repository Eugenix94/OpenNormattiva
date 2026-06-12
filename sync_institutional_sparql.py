#!/usr/bin/env python3
"""Sync selected Italian institutional SPARQL sources into the HF dataset.

This script executes a small set of stable discovery queries against institutional
SPARQL endpoints and stores results as JSONL files plus a manifest.

Default sources:
- Camera dei Deputati: https://dati.camera.it/sparql
- Senato della Repubblica: https://dati.senato.it/sparql
- Dati.gov.it LOD endpoint: https://lod.dati.gov.it/sparql/
- Corte Costituzionale: https://dati.cortecostituzionale.it/sparql

Usage:
  python sync_institutional_sparql.py --check-only
  python sync_institutional_sparql.py --dataset-repo diatribe00/italian-legal-lab-data
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
from pathlib import Path
from typing import Dict, List, Optional

import requests
from huggingface_hub import HfApi, hf_hub_download

MANIFEST_PATH = "data/institutional_sparql/manifest.json"
REPORT_PATH = "logs/institutional_sparql_sync_report.json"


@dataclass(frozen=True)
class SparqlSource:
    key: str
    label: str
    endpoint: str
    query: str


SOURCES: List[SparqlSource] = [
    SparqlSource(
        key="camera_types",
        label="Camera dei Deputati - concept scan",
        endpoint="https://dati.camera.it/sparql",
        query="select distinct ?concept where { [] a ?concept } limit 500",
    ),
    SparqlSource(
        key="camera_legislatures",
        label="Camera dei Deputati - legislatures succession",
        endpoint="https://dati.camera.it/sparql",
        query=(
            "PREFIX ocd: <http://dati.camera.it/ocd/> "
            "PREFIX dc: <http://purl.org/dc/elements/1.1/> "
            "SELECT DISTINCT ?leg ?title ?start ?end WHERE { "
            "?leg a ocd:legislatura ; dc:title ?title ; ocd:startDate ?start . "
            "OPTIONAL { ?leg ocd:endDate ?end } "
            "} ORDER BY ?start"
        ),
    ),
    SparqlSource(
        key="camera_governments",
        label="Camera dei Deputati - governments succession",
        endpoint="https://dati.camera.it/sparql",
        query=(
            "PREFIX ocd: <http://dati.camera.it/ocd/> "
            "PREFIX dc: <http://purl.org/dc/elements/1.1/> "
            "SELECT DISTINCT ?gov ?title ?start ?end WHERE { "
            "?gov a ocd:governo ; dc:title ?title ; ocd:startDate ?start . "
            "OPTIONAL { ?gov ocd:endDate ?end } "
            "} ORDER BY ?start"
        ),
    ),
    SparqlSource(
        key="senato_types",
        label="Senato della Repubblica - concept scan",
        endpoint="https://dati.senato.it/sparql",
        query="select distinct ?concept where { [] a ?concept } limit 500",
    ),
    SparqlSource(
        key="dati_gov_legal_datasets",
        label="dati.gov.it - legal-themed dataset metadata",
        endpoint="https://lod.dati.gov.it/sparql/",
        query=(
            "PREFIX dcat: <http://www.w3.org/ns/dcat#> "
            "PREFIX dct: <http://purl.org/dc/terms/> "
            "SELECT DISTINCT ?dataset ?title ?theme WHERE { "
            "?dataset a dcat:Dataset ; dct:title ?title ; dcat:theme ?theme . "
            "FILTER( regex(str(?title), 'legge|giustizia|tribunale|parlamento|decreto', 'i') ) "
            "} LIMIT 1000"
        ),
    ),
    SparqlSource(
        key="corte_probe",
        label="Corte Costituzionale - probe",
        endpoint="https://dati.cortecostituzionale.it/sparql",
        query="SELECT * WHERE { ?s ?p ?o } LIMIT 50",
    ),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/sparql-results+json, application/json;q=0.9, */*;q=0.5",
        "User-Agent": "Mozilla/5.0 (compatible; OpenNormattivaSPARQLSync/1.0)",
        "Referer": "https://www.dati.gov.it/sviluppatori/sparqlclient",
    }


def execute_query(session: requests.Session, src: SparqlSource, timeout: int = 45) -> Dict:
    params = {
        "query": src.query,
        "format": "application/sparql-results+json",
        "timeout": "0",
    }

    response = session.get(
        src.endpoint,
        params=params,
        headers=_headers(),
        timeout=timeout,
        allow_redirects=True,
    )

    result: Dict[str, object] = {
        "source_key": src.key,
        "label": src.label,
        "endpoint": src.endpoint,
        "query": src.query,
        "checked_at_utc": _now_iso(),
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "ok": False,
        "warning": None,
        "error": None,
        "records": [],
    }

    if response.status_code >= 400:
        result["error"] = f"HTTP {response.status_code}"
        return result

    # Some endpoints may return an empty 200 response temporarily.
    # We mark this as reachable with warning so downstream UIs do not treat it
    # as a hard parser failure.
    if not response.content:
        result["ok"] = True
        result["warning"] = "Empty response body from endpoint"
        return result

    try:
        payload = response.json()
    except Exception:
        text_preview = response.text[:500].replace("\n", " ").strip()
        result["error"] = f"Non-JSON response: {text_preview}"
        return result

    bindings = payload.get("results", {}).get("bindings", [])
    records = []
    for b in bindings:
        row = {k: v.get("value") for k, v in b.items()}
        records.append(row)

    result["records"] = records
    result["ok"] = True
    return result


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


def build_manifest(dataset_repo: str, runs: List[Dict]) -> Dict:
    files = {}
    for run in runs:
        key = str(run["source_key"])
        files[key] = {
            "label": run.get("label"),
            "endpoint": run.get("endpoint"),
            "ok": bool(run.get("ok", False)),
            "http_status": run.get("http_status"),
            "content_type": run.get("content_type"),
            "record_count": len(run.get("records", [])),
            "warning": run.get("warning"),
            "error": run.get("error"),
            "query": run.get("query"),
            "jsonl_path": f"data/institutional_sparql/raw/{key}.jsonl",
            "updated_at_utc": run.get("checked_at_utc"),
            "records_hash": _stable_hash(run.get("records", [])),
        }

    return {
        "source": "Italian institutional SPARQL harvest",
        "dataset_repo": dataset_repo,
        "updated_at_utc": _now_iso(),
        "total_sources": len(runs),
        "ok_sources": sum(1 for r in runs if r.get("ok")),
        "files": files,
    }


def write_report(report: Dict) -> None:
    report_path = Path(REPORT_PATH)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def changed_sources(previous_manifest: Dict, current_manifest: Dict) -> List[str]:
    prev = previous_manifest.get("files", {})
    curr = current_manifest.get("files", {})
    changed = []

    for key, meta in curr.items():
        p = prev.get(key)
        if not p:
            changed.append(key)
            continue

        if p.get("records_hash") != meta.get("records_hash"):
            changed.append(key)
            continue

        if bool(p.get("ok", False)) != bool(meta.get("ok", False)):
            changed.append(key)
            continue

        if p.get("http_status") != meta.get("http_status"):
            changed.append(key)
            continue

    return changed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync institutional SPARQL extracts to HF dataset")
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
    runs: List[Dict] = []

    for src in SOURCES:
        print(f"Checking {src.key} -> {src.endpoint}")
        try:
            run = execute_query(session, src)
        except Exception as exc:
            run = {
                "source_key": src.key,
                "label": src.label,
                "endpoint": src.endpoint,
                "query": src.query,
                "checked_at_utc": _now_iso(),
                "http_status": None,
                "content_type": "",
                "ok": False,
                "error": str(exc),
                "records": [],
            }
        runs.append(run)
        print(
            f"  status={run.get('http_status')} ok={run.get('ok')} "
            f"records={len(run.get('records', []))}"
        )

    current_manifest = build_manifest(args.dataset_repo, runs)
    previous_manifest = load_remote_manifest(args.dataset_repo, token)
    changed = changed_sources(previous_manifest, current_manifest)

    report = {
        "dataset_repo": args.dataset_repo,
        "checked_at_utc": _now_iso(),
        "changed_sources": changed,
        "sources": [
            {
                "source_key": r.get("source_key"),
                "ok": r.get("ok"),
                "http_status": r.get("http_status"),
                "record_count": len(r.get("records", [])),
                "warning": r.get("warning"),
                "error": r.get("error"),
            }
            for r in runs
        ],
        "check_only": args.check_only,
    }
    write_report(report)

    print(f"Sources checked: {len(runs)}")
    print(f"Changed sources: {len(changed)}")

    if args.check_only:
        return 0

    token = token or os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: HF token required for upload. Set HF_TOKEN or pass --token.", file=sys.stderr)
        return 2

    if not changed:
        print("No changes detected in SPARQL extracts. Nothing to upload.")
        return 0

    api = HfApi(token=token)
    with tempfile.TemporaryDirectory(prefix="institutional_sparql_") as tmp:
        root = Path(tmp)

        for run in runs:
            key = str(run["source_key"])
            if key not in changed:
                continue
            rows = run.get("records", [])
            out = root / f"data/institutional_sparql/raw/{key}.jsonl"
            write_jsonl(out, rows)

        manifest_path = root / MANIFEST_PATH
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(current_manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        api.upload_folder(
            repo_id=args.dataset_repo,
            repo_type="dataset",
            folder_path=str(root),
            commit_message=f"Institutional SPARQL sync: {len(changed)} source(s) updated",
        )

    print(f"Uploaded {len(changed)} changed source file(s) + manifest to {args.dataset_repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
