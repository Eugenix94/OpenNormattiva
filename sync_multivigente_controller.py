#!/usr/bin/env python3
"""Continuous controller for multivigente completeness, upload, and Space refresh."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

BASE_API = "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1"
HEADERS = {
    "User-Agent": "NormattivaVOOM/1.0 (sync-controller)",
    "Accept": "*/*",
}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def run_cmd(args: list[str]) -> int:
    log("RUN: " + " ".join(args))
    proc = subprocess.run(args)
    log(f"EXIT: {proc.returncode}")
    return int(proc.returncode)


def fetch_official_m() -> dict[str, int]:
    url = f"{BASE_API}/api/v1/collections/collection-predefinite"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())

    out: dict[str, int] = {}
    for item in data:
        if item.get("formatoCollezione") != "M":
            continue
        name = item.get("nomeCollezione") or item.get("nome")
        if not name:
            continue
        out[name] = int(item.get("numeroAtti") or 0)
    return out


def read_local_stats(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    total_versions = cur.execute("SELECT COUNT(*) FROM law_versions").fetchone()[0]
    total_distinct_urn = cur.execute("SELECT COUNT(DISTINCT law_urn) FROM law_versions").fetchone()[0]
    has_sources = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='law_version_sources'"
    ).fetchone()
    if has_sources:
        by_distinct = dict(
            cur.execute(
                "SELECT source_collection, COUNT(DISTINCT law_urn) FROM law_version_sources GROUP BY source_collection"
            ).fetchall()
        )
    else:
        by_distinct = dict(
            cur.execute(
                "SELECT source_collection, COUNT(DISTINCT law_urn) FROM law_versions GROUP BY source_collection"
            ).fetchall()
        )
    by_versions = dict(
        cur.execute(
            "SELECT source_collection, COUNT(*) FROM law_versions GROUP BY source_collection"
        ).fetchall()
    )
    conn.close()

    return {
        "total_versions": int(total_versions),
        "total_distinct_urn": int(total_distinct_urn),
        "by_distinct": {str(k): int(v) for k, v in by_distinct.items()},
        "by_versions": {str(k): int(v) for k, v in by_versions.items()},
    }


def coverage_report(official: dict[str, int], local: dict[str, Any],
                    db_path: Path | None = None) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    missing: list[str] = []
    partial: list[str] = []
    ok: list[str] = []

    by_distinct = local["by_distinct"]
    official_total = sum(official.values())
    local_distinct_total = local["total_distinct_urn"]
    pct = (local_distinct_total / official_total * 100.0) if official_total else 0.0

    db_conn = sqlite3.connect(str(db_path)) if db_path and db_path.exists() else None

    for name in sorted(official):
        o = int(official.get(name, 0))
        l = int(by_distinct.get(name, 0))
        cpct = (l / o * 100.0) if o else 0.0
        note = None

        if l == 0:
            status = "missing"
            missing.append(name)
        elif o and l < o:
            status = "partial"
            partial.append(name)
        else:
            status = "ok"
            ok.append(name)

        entry: dict[str, Any] = {
            "collection": name,
            "official_atti": o,
            "local_distinct_urn": int(by_distinct.get(name, 0)),
            "effective_distinct_urn": l,
            "coverage_pct": round(cpct, 2),
            "status": status,
        }
        details.append(entry)

    if db_conn:
        db_conn.close()

    return {
        "official_total_atti": official_total,
        "local_total_versions": int(local["total_versions"]),
        "local_total_distinct_urn": int(local_distinct_total),
        "overall_coverage_pct": round(pct, 2),
        "collections_total": len(official),
        "collections_ok": len(ok),
        "collections_partial": len(partial),
        "collections_missing": len(missing),
        "missing": missing,
        "partial": partial,
        "details": details,
    }


def maybe_restart_space(token: str, space_id: str) -> tuple[bool, str]:
    try:
        from huggingface_hub import HfApi

        api = HfApi(token=token)
        api.restart_space(repo_id=space_id)
        return True, "space restart requested"
    except Exception as exc:
        return False, f"space restart failed: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Continuous multivigente sync controller")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--sleep-minutes", type=int, default=10)
    parser.add_argument("--target-coverage", type=float, default=99.9)
    parser.add_argument("--space-id", default="diatribe00/opennormattiva-lab")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    logs_dir = root / "logs"
    state_path = logs_dir / "multivigente_sync_state.json"
    report_path = logs_dir / "multivigente_coverage_report.json"
    db_path = root / "data" / "multivigente.db"

    logs_dir.mkdir(parents=True, exist_ok=True)
    state = load_json(
        state_path,
        {
            "best_distinct_urn": 0,
            "best_coverage_pct": 0.0,
            "last_uploaded_distinct_urn": 0,
            "space_refresh_done": False,
            "last_cycle_at": None,
        },
    )

    hf_token = os.environ.get("HF_TOKEN", "").strip()

    log(f"Controller started. root={root} sleep={args.sleep_minutes}m target={args.target_coverage}%")

    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 10

    while True:
        try:
            state["last_cycle_at"] = now()
            save_json(state_path, state)

            build_exit = run_cmd([sys.executable, "build_voom.py", "--steps", "multivigente", "--skip-download"])

            official = fetch_official_m()
            local = read_local_stats(db_path)
            report = coverage_report(official, local, db_path=db_path)
            report["build_exit"] = build_exit
            report["generated_at"] = now()
            save_json(report_path, report)

            distinct = int(report["local_total_distinct_urn"])
            cov = float(report["overall_coverage_pct"])
            missing = int(report["collections_missing"])

            if distinct > int(state.get("best_distinct_urn", 0)):
                state["best_distinct_urn"] = distinct
            if cov > float(state.get("best_coverage_pct", 0.0)):
                state["best_coverage_pct"] = cov

            improved = distinct > int(state.get("last_uploaded_distinct_urn", 0))
            log(
                "Cycle summary: "
                f"versions={report['local_total_versions']} distinct={distinct} "
                f"coverage={cov}% missing_collections={missing} improved_for_upload={improved}"
            )

            if hf_token and improved:
                upload_exit = run_cmd([sys.executable, "upload_multivigente.py"])
                if upload_exit == 0:
                    state["last_uploaded_distinct_urn"] = distinct
                    log("Upload succeeded.")
                else:
                    log("Upload failed; will retry next improved cycle.")
            elif not hf_token:
                log("HF_TOKEN missing; upload skipped.")

            should_refresh_space = (
                hf_token
                and not bool(state.get("space_refresh_done", False))
                and missing == 0
                and cov >= float(args.target_coverage)
            )

            if should_refresh_space:
                ok, message = maybe_restart_space(hf_token, args.space_id)
                log(message)
                if ok:
                    state["space_refresh_done"] = True

            save_json(state_path, state)

            if args.once:
                return 0

            consecutive_failures = 0
            log(f"Sleeping {args.sleep_minutes} minutes before next cycle...")
            time.sleep(max(1, args.sleep_minutes) * 60)

        except KeyboardInterrupt:
            log("Interrupted by user. Exiting.")
            return 0
        except Exception as exc:
            consecutive_failures += 1
            log(f"ERROR in cycle (failure #{consecutive_failures}): {exc}")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log(f"Too many consecutive failures ({MAX_CONSECUTIVE_FAILURES}). Exiting.")
                return 1
            backoff = min(60 * consecutive_failures, 600)
            log(f"Backing off {backoff}s before retry...")
            time.sleep(backoff)


if __name__ == "__main__":
    raise SystemExit(main())
