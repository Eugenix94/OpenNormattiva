#!/usr/bin/env python3
"""Fetch institutional data from official sources and persist a local snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path

from core.institutional import DEFAULT_SNAPSHOT_PATH, collect_institutional_snapshot, save_snapshot



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="data/laws.db",
        help="Path to the local Normattiva SQLite database.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_SNAPSHOT_PATH),
        help="Path of the JSON snapshot to create.",
    )
    parser.add_argument(
        "--include-status",
        action="store_true",
        help="Also perform lightweight live endpoint health checks.",
    )
    return parser



def main() -> int:
    args = _build_parser().parse_args()
    snapshot = collect_institutional_snapshot(
        db_path=Path(args.db),
        include_endpoint_status=args.include_status,
    )
    output_path = save_snapshot(snapshot, Path(args.output))

    print(f"Snapshot salvato in: {output_path}")
    print(f"Generato: {snapshot.get('generated_at', 'N/D')}")
    print(f"Camera deputati: {len(snapshot.get('camera', {}).get('current_deputies', []))}")
    print(f"Senato senatori: {len(snapshot.get('senato', {}).get('current_senators', []))}")
    print(f"Governo membri correnti: {len(snapshot.get('governo', {}).get('current_members', []))}")
    print(f"Quirinale presidenti: {len(snapshot.get('quirinale', {}).get('presidents', []))}")
    print(f"Corte giudici correnti: {len(snapshot.get('corte', {}).get('current_judges', []))}")

    errors = snapshot.get("errors", {})
    if errors:
        print("Errori durante l'ingestione:")
        for key in sorted(errors):
            print(f"- {key}: {errors[key]}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
