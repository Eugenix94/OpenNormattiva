#!/usr/bin/env python3
"""Benchmark dashboard subsection render timings via Streamlit AppTest.

This script navigates to dashboard and toggles `dashboard-section` values to
measure the in-app render timer (`_perf_page_timings`) for each section.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from streamlit.testing.v1 import AppTest


def _last_app_timing(at: AppTest) -> dict[str, Any] | None:
    if "_perf_page_timings" not in at.session_state:
        return None
    history = at.session_state["_perf_page_timings"]
    if not history:
        return None
    return dict(history[-1])


def _run_once(app_file: str, timeout: int) -> list[dict[str, Any]]:
    at = AppTest.from_file(app_file)
    rows: list[dict[str, Any]] = []

    # Initial run on default page (Dashboard)
    t0 = perf_counter()
    at.run(timeout=timeout)
    wall_ms = (perf_counter() - t0) * 1000.0
    timing = _last_app_timing(at)
    rows.append(
        {
            "step": "initial",
            "target": timing.get("page") if timing else "<unknown>",
            "wall_ms": round(wall_ms, 1),
            "app_ms": float(timing.get("ms")) if timing and timing.get("ms") is not None else None,
        }
    )

    # Ensure we are on dashboard page
    nav = at.radio(key="page-nav")
    if "📊 Dashboard" in nav.options:
        t0 = perf_counter()
        nav.set_value("📊 Dashboard").run(timeout=timeout)
        wall_ms = (perf_counter() - t0) * 1000.0
        timing = _last_app_timing(at)
        rows.append(
            {
                "step": "goto_dashboard",
                "target": "📊 Dashboard",
                "wall_ms": round(wall_ms, 1),
                "app_ms": float(timing.get("ms")) if timing and timing.get("ms") is not None else None,
            }
        )

    if not at.radio(key="dashboard-section"):
        return rows

    section_widget = at.radio(key="dashboard-section")
    for section in list(section_widget.options):
        t0 = perf_counter()
        at.radio(key="dashboard-section").set_value(section).run(timeout=timeout)
        wall_ms = (perf_counter() - t0) * 1000.0
        timing = _last_app_timing(at)
        rows.append(
            {
                "step": "section",
                "target": str(section),
                "wall_ms": round(wall_ms, 1),
                "app_ms": float(timing.get("ms")) if timing and timing.get("ms") is not None else None,
            }
        )

    return rows


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        if r.get("step") != "section":
            continue
        key = str(r.get("target", ""))
        grouped.setdefault(key, {"wall": [], "app": []})
        wall = r.get("wall_ms")
        app = r.get("app_ms")
        if isinstance(wall, (int, float)):
            grouped[key]["wall"].append(float(wall))
        if isinstance(app, (int, float)):
            grouped[key]["app"].append(float(app))

    out: dict[str, dict[str, float]] = {}
    for key, vals in grouped.items():
        out[key] = {
            "wall_avg_ms": round(mean(vals["wall"]), 1) if vals["wall"] else 0.0,
            "app_avg_ms": round(mean(vals["app"]), 1) if vals["app"] else 0.0,
            "samples": float(max(len(vals["wall"]), len(vals["app"]))),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark dashboard sections")
    parser.add_argument("--app", default="space/app.py")
    parser.add_argument("--out", default="benchmark_dashboard_sections.json")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    all_rows: list[dict[str, Any]] = []
    for run in range(1, args.runs + 1):
        rows = _run_once(args.app, args.timeout)
        for r in rows:
            r["run"] = run
        all_rows.extend(rows)

    payload = {
        "app": args.app,
        "runs": args.runs,
        "results": all_rows,
        "aggregate": _aggregate(all_rows),
    }

    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote dashboard section benchmark to {args.out}")


if __name__ == "__main__":
    main()
