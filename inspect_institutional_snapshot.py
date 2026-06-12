#!/usr/bin/env python3
"""Inspect a previously generated institutional snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.institutional import DEFAULT_SNAPSHOT_PATH, load_snapshot



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        default=str(DEFAULT_SNAPSHOT_PATH),
        help="Path to the JSON snapshot to inspect.",
    )
    parser.add_argument(
        "--section",
        choices=["camera", "senato", "governo", "quirinale", "corte", "endpoint_status"],
        help="Print a specific section as JSON.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum number of rows to show when printing a section.",
    )
    return parser



def main() -> int:
    args = _build_parser().parse_args()
    path = Path(args.path)
    if not path.exists():
        print(f"Snapshot non trovato: {path}")
        return 1

    snapshot = load_snapshot(path)
    print(f"Snapshot: {path}")
    print(f"Generato: {snapshot.get('generated_at', 'N/D')}")
    print(f"Registry fonti: {len(snapshot.get('registry', []))}")
    print(f"Camera deputati: {len(snapshot.get('camera', {}).get('current_deputies', []))}")
    print(f"Senato senatori: {len(snapshot.get('senato', {}).get('current_senators', []))}")
    print(f"Governo membri: {len(snapshot.get('governo', {}).get('current_members', []))}")
    print(f"Quirinale presidenti: {len(snapshot.get('quirinale', {}).get('presidents', []))}")
    print(f"Corte giudici: {len(snapshot.get('corte', {}).get('current_judges', []))}")

    errors = snapshot.get("errors", {})
    if errors:
        print("Errori presenti:")
        for key in sorted(errors):
            print(f"- {key}: {errors[key]}")

    if args.section:
        section = snapshot.get(args.section, {})
        if isinstance(section, list):
            payload = section[: args.limit]
        elif isinstance(section, dict):
            payload = {}
            for key, value in section.items():
                if isinstance(value, list):
                    payload[key] = value[: args.limit]
                else:
                    payload[key] = value
        else:
            payload = section
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
