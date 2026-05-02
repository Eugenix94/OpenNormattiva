#!/usr/bin/env python3
"""
Expand normattiva-lab dataset:
1) Check live Normattiva API catalogue for deltas.
2) Build/refresh multivigente JSONL.
3) Optionally build a context-engineering RAG corpus JSONL.

This script is offline-friendly in the sense that it writes artifacts locally.
Use existing deploy workflows to publish dataset/space updates.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

from normattiva_api_client import NormattivaAPI
from parse_akn import AKNParser

VIGENTE_COLLECTIONS = [
    "Codici", "DL proroghe", "Leggi costituzionali", "Regi decreti", "DPR",
    "DL e leggi di conversione", "Decreti Legislativi", "Leggi di ratifica",
    "Regolamenti ministeriali", "Regolamenti governativi", "DL decaduti",
    "Decreti legislativi luogotenenziali", "Leggi delega e relativi provvedimenti delegati",
    "Atti di recepimento direttive UE", "Regolamenti di delegificazione", "DPCM",
    "Testi Unici", "Regi decreti legislativi", "Leggi contenenti deleghe",
    "Leggi finanziarie e di bilancio", "Leggi di delegazione europea",
    "Atti di attuazione Regolamenti UE",
]

ABROGATED_COLLECTION = "Atti normativi abrogati (in originale)"


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


def write_jsonl(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 180) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    out = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size)
        out.append(text[start:end])
        if end >= n:
            break
        start = max(start + 1, end - overlap)
    return out


def normalize_status(status: str | None) -> str:
    s = (status or "").lower().strip()
    if s in {"in_force", "vigente", "v", "active"}:
        return "in_force"
    if s in {"abrogated", "abrogato", "abrogata", "a", "repealed"}:
        return "abrogated"
    if s in {"multi_version", "multivigente", "m"}:
        return "multi_version"
    return s or "unknown"


def track_of(law: Dict) -> str:
    src = str(law.get("source_collection") or "").lower()
    status = normalize_status(law.get("status"))
    if "abrogat" in src or status == "abrogated":
        return "abrogato"
    if "multivigente" in src or status == "multi_version":
        return "multivigente"
    return "vigente"


def fetch_api_summary(api: NormattivaAPI) -> Dict:
    cat = api.get_collection_catalogue()
    sums = {"vigente": 0, "multivigente": 0, "abrogato": 0}
    for c in cat:
        name = str(c.get("nomeCollezione", "")).lower()
        variant = str(c.get("formatoCollezione", "")).upper()
        n = int(c.get("numeroAtti") or 0)
        if "abrogat" in name and variant == "O":
            sums["abrogato"] += n
        elif variant == "V":
            sums["vigente"] += n
        elif variant == "M":
            sums["multivigente"] += n
    return {"catalogue_count": len(cat), "by_track": sums, "raw": cat}


def build_multivigente_jsonl(api: NormattivaAPI, parser: AKNParser, processed: Path, collection_limit: int | None) -> Path:
    out_path = processed / "laws_multivigente.jsonl"
    existing = {x.get("urn"): x for x in iter_jsonl(out_path) or [] if x.get("urn")}

    collections = VIGENTE_COLLECTIONS[:collection_limit] if collection_limit else VIGENTE_COLLECTIONS
    for name in collections:
        data, _etag, _ct = api.get_collection(name, variant="M", format="AKN")
        zip_path = processed / f"_tmp_{name.replace(' ', '_')}_M.zip"
        with open(zip_path, "wb") as f:
            f.write(data)

        parsed = parser.parse_zip_file(zip_path)
        for law in parsed:
            urn = law.get("urn")
            if not urn:
                continue
            law["status"] = law.get("status") or "multi_version"
            law["source_collection"] = law.get("source_collection") or f"{name} [multivigente]"
            existing[urn] = law
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass

    rows = sorted(existing.values(), key=lambda x: (str(x.get("year") or ""), str(x.get("urn") or "")), reverse=True)
    write_jsonl(out_path, rows)
    return out_path


def build_rag_corpus(processed: Path, chunk_size: int, overlap: int) -> Path:
    sources = [
        (processed / "laws_vigente.jsonl", "vigente"),
        (processed / "laws_multivigente.jsonl", "multivigente"),
        (processed / "laws_abrogated.jsonl", "abrogato"),
        (processed / "laws_abrogate.jsonl", "abrogato"),
    ]

    seen = set()
    docs = []

    for path, fallback_track in sources:
        if not path.exists():
            continue
        for law in iter_jsonl(path) or []:
            urn = law.get("urn")
            if not urn:
                continue
            base_track = track_of(law)
            if base_track == "vigente" and fallback_track != "vigente":
                base_track = fallback_track
            chunks = chunk_text(str(law.get("text") or ""), chunk_size=chunk_size, overlap=overlap)
            for i, chunk in enumerate(chunks, 1):
                chunk_id = f"{urn}::c{i}"
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                docs.append(
                    {
                        "chunk_id": chunk_id,
                        "urn": urn,
                        "title": law.get("title"),
                        "type": law.get("type"),
                        "year": law.get("year"),
                        "status": normalize_status(law.get("status")),
                        "track": base_track,
                        "source_collection": law.get("source_collection"),
                        "chunk_index": i,
                        "text": chunk,
                    }
                )

    out_path = processed / "rag_context_corpus.jsonl"
    write_jsonl(out_path, docs)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Expand normattiva-lab with multivigente + RAG corpus")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--collection-limit", type=int, default=None,
                        help="Optional limit for multivigente collections (for quick runs)")
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=180)
    parser.add_argument(
        "--build-rag-corpus",
        action="store_true",
        help="Also generate rag_context_corpus.jsonl (disabled by default for dataset-first runs)",
    )
    args = parser.parse_args()

    processed = args.data_dir / "processed"
    processed.mkdir(parents=True, exist_ok=True)

    api = NormattivaAPI(timeout_s=60, retries=2)
    parser_akn = AKNParser()

    print("[1/3] Fetching live API catalogue summary...")
    api_summary = fetch_api_summary(api)

    print("[2/3] Building multivigente dataset...")
    multivig_path = build_multivigente_jsonl(
        api,
        parser_akn,
        processed,
        collection_limit=args.collection_limit,
    )

    rag_path = None
    if args.build_rag_corpus:
        print("[3/3] Building RAG context corpus...")
        rag_path = build_rag_corpus(processed, chunk_size=args.chunk_size, overlap=args.chunk_overlap)
    else:
        print("[3/3] Skipping RAG corpus build (use --build-rag-corpus to enable)")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_catalogue_collections": api_summary["catalogue_count"],
        "api_counts_by_track": api_summary["by_track"],
        "outputs": {
            "multivigente": str(multivig_path),
            "rag_corpus": str(rag_path) if rag_path else None,
        },
    }

    manifest_path = processed / "lab_expansion_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Lab expansion complete")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
