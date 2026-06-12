#!/usr/bin/env python3
"""Automated Streamlit page benchmark using AppTest.

This script runs the app in-process and navigates selected pages through the
sidebar radio (`page-nav`) to collect two metrics per step:
- wall_ms: end-to-end test harness runtime for the rerun
- app_ms: in-app render timing captured by `_record_page_timing`
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from streamlit.testing.v1 import AppTest


DEFAULT_PAGES = [
    "📊 Dashboard",
    "🔍 Search",
    "📋 Browse (All)",
]


def _last_app_timing(at: AppTest) -> dict[str, Any] | None:
    if "_perf_page_timings" not in at.session_state:
        return None
    history = at.session_state["_perf_page_timings"]
    if not history:
        return None
    return dict(history[-1])


def _benchmark_once(app_file: str, pages: list[str], timeout: int) -> list[dict[str, Any]]:
    at = AppTest.from_file(app_file)
    results: list[dict[str, Any]] = []

    # Initial run (default page from sidebar)
    t0 = perf_counter()
    at.run(timeout=timeout)
    wall_ms = (perf_counter() - t0) * 1000.0
    app_timing = _last_app_timing(at)
    results.append(
        {
            "step": 1,
            "target_page": app_timing.get("page") if app_timing else "<unknown>",
            "wall_ms": round(wall_ms, 1),
            "app_ms": float(app_timing.get("ms")) if app_timing and app_timing.get("ms") is not None else None,
            "app_at": app_timing.get("at") if app_timing else None,
        }
    )

    # Navigate explicitly to benchmark pages that are available in the current profile.
    nav = at.radio(key="page-nav")
    available = set(nav.options)
    benchmark_pages = [p for p in pages if p in available]

    step = 2
    for page in benchmark_pages:
        t0 = perf_counter()
        at.radio(key="page-nav").set_value(page).run(timeout=timeout)
        wall_ms = (perf_counter() - t0) * 1000.0
        app_timing = _last_app_timing(at)
        results.append(
            {
                "step": step,
                "target_page": page,
                "wall_ms": round(wall_ms, 1),
                "app_ms": float(app_timing.get("ms")) if app_timing and app_timing.get("ms") is not None else None,
                "app_at": app_timing.get("at") if app_timing else None,
            }
        )
        step += 1

    return results


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_page: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        page = str(r.get("target_page", ""))
        by_page.setdefault(page, {"wall": [], "app": []})
        wall = r.get("wall_ms")
        app = r.get("app_ms")
        if isinstance(wall, (int, float)):
            by_page[page]["wall"].append(float(wall))
        if isinstance(app, (int, float)):
            by_page[page]["app"].append(float(app))

    out: dict[str, dict[str, float]] = {}
    for page, vals in by_page.items():
        out[page] = {
            "wall_avg_ms": round(mean(vals["wall"]), 1) if vals["wall"] else 0.0,
            "app_avg_ms": round(mean(vals["app"]), 1) if vals["app"] else 0.0,
            "samples": float(max(len(vals["wall"]), len(vals["app"]))),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Streamlit page rerender timings")
    parser.add_argument("--app", default="space/app.py", help="Path to Streamlit app file")
    parser.add_argument("--out", default="benchmark_results.json", help="Output JSON file")
    parser.add_argument("--runs", type=int, default=2, help="Number of benchmark passes")
    parser.add_argument("--timeout", type=int, default=180, help="AppTest timeout per run in seconds")
    parser.add_argument(
        "--pages",
        nargs="*",
        default=DEFAULT_PAGES,
        help="Pages to navigate via sidebar 'page-nav'",
    )
    args = parser.parse_args()

    all_rows: list[dict[str, Any]] = []
    for run_idx in range(1, args.runs + 1):
        run_rows = _benchmark_once(args.app, args.pages, args.timeout)
        for r in run_rows:
            r["run"] = run_idx
        all_rows.extend(run_rows)

    payload = {
        "app": args.app,
        "runs": args.runs,
        "pages": args.pages,
        "results": all_rows,
        "aggregate": _aggregate(all_rows),
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote benchmark results to {out_path}")


if __name__ == "__main__":
    main()
