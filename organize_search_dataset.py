#!/usr/bin/env python3
"""
Organize OpenNormattiva search dataset into strict track files.

Goals:
- Keep vigente and abrogated laws separated.
- Produce canonical files used by Space and dataset jobs.
- Emit a manifest with counts and quality checks.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List


def iter_jsonl(path: Path) -> Iterable[Dict]:
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def normalize_status(status: str | None) -> str:
    s = (status or "").strip().lower()
    if s in {"in_force", "vigente", "v", "in force", "active"}:
        return "in_force"
    if s in {"abrogated", "abrogato", "abrogata", "a", "repealed"}:
        return "abrogated"
    return s or "unknown"


def is_abrogated(law: Dict) -> bool:
    src = str(law.get("source_collection") or "").lower()
    return "abrogat" in src or normalize_status(law.get("status")) == "abrogated"


def write_jsonl(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def dedupe_by_urn(rows: Iterable[Dict]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for r in rows:
        urn = r.get("urn")
        if not urn:
            continue
        out[urn] = r
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Organize search dataset tracks")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    processed = args.data_dir / "processed"
    vigente_path = processed / "laws_vigente.jsonl"
    # Support both historic and canonical names
    abrogated_src_candidates = [
        processed / "laws_abrogated.jsonl",
        processed / "laws_abrogate.jsonl",
    ]

    vigente_rows = list(iter_jsonl(vigente_path) or [])
    abrogated_rows: List[Dict] = []
    for p in abrogated_src_candidates:
        if p.exists():
            abrogated_rows.extend(list(iter_jsonl(p) or []))

    vigente_map = dedupe_by_urn(vigente_rows)
    abrogated_map = dedupe_by_urn(abrogated_rows)

    # Enforce strict separation by status/source markers.
    move_to_abrogated = []
    for urn, law in list(vigente_map.items()):
        if is_abrogated(law):
            law["status"] = "abrogated"
            move_to_abrogated.append(urn)
            abrogated_map[urn] = law

    for urn in move_to_abrogated:
        vigente_map.pop(urn, None)

    move_to_vigente = []
    for urn, law in list(abrogated_map.items()):
        if not is_abrogated(law):
            law["status"] = "in_force"
            move_to_vigente.append(urn)
            vigente_map[urn] = law

    for urn in move_to_vigente:
        abrogated_map.pop(urn, None)

    vigente_out = sorted(vigente_map.values(), key=lambda x: (str(x.get("year") or ""), str(x.get("urn") or "")), reverse=True)
    abrogated_out = sorted(abrogated_map.values(), key=lambda x: (str(x.get("year") or ""), str(x.get("urn") or "")), reverse=True)

    canonical_abrogated = processed / "laws_abrogated.jsonl"
    compat_abrogate = processed / "laws_abrogate.jsonl"

    write_jsonl(vigente_path, vigente_out)
    write_jsonl(canonical_abrogated, abrogated_out)
    write_jsonl(compat_abrogate, abrogated_out)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vigente_count": len(vigente_out),
        "abrogated_count": len(abrogated_out),
        "moved_vigente_to_abrogated": len(move_to_abrogated),
        "moved_abrogated_to_vigente": len(move_to_vigente),
        "files": {
            "vigente": str(vigente_path),
            "abrogated": str(canonical_abrogated),
            "abrogate_compat": str(compat_abrogate),
        },
    }

    manifest_path = processed / "dataset_tracks_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Track organization complete")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
