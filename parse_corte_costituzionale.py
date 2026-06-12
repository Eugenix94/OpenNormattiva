#!/usr/bin/env python3
"""Build normalized JSONL tables from Corte Costituzionale raw ZIP files on HF.

Input:
- data/corte_costituzionale/manifest.json in the dataset repo
- data/corte_costituzionale/raw/**/*.zip files referenced by the manifest

Output:
- data/corte_costituzionale/processed/<family>.jsonl
- data/corte_costituzionale/processed/summary.json

Usage:
  python parse_corte_costituzionale.py --dataset-repo diatribe00/italian-legal-lab-data --upload
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from huggingface_hub import HfApi, hf_hub_download
from lxml import etree

MANIFEST_PATH = "data/corte_costituzionale/manifest.json"
PROCESSED_DIR = "data/corte_costituzionale/processed"
SUMMARY_PATH = f"{PROCESSED_DIR}/summary.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse Corte raw ZIPs into normalized JSONL")
    parser.add_argument(
        "--dataset-repo",
        default="diatribe00/italian-legal-lab-data",
        help="HF dataset repo id (owner/name)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="HF token (optional for public read; required for upload)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload processed outputs back to dataset repo",
    )
    parser.add_argument(
        "--max-zips",
        type=int,
        default=0,
        help="Debug limit: parse at most N ZIP files (0 = all)",
    )
    return parser.parse_args()


def load_manifest(dataset_repo: str, token: Optional[str]) -> Dict:
    local_path = hf_hub_download(
        repo_id=dataset_repo,
        repo_type="dataset",
        filename=MANIFEST_PATH,
        token=token,
    )
    return json.loads(Path(local_path).read_text(encoding="utf-8"))


def infer_family_from_path(repo_path: str) -> str:
    p = repo_path.lower()
    if "/anagrafica_giudici/" in p:
        return "anagrafica_giudici"
    if "/norme_impugnate/" in p:
        return "norme_impugnate"
    if "massime" in p:
        return "massime"
    if "pronunce" in p:
        return "pronunce"
    return "other"


def _text(v: object) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _first_non_empty(d: Dict, keys: List[str]) -> str:
    lower_map = {str(k).lower(): d[k] for k in d.keys()}
    for k in keys:
        v = lower_map.get(k)
        if v is not None and _text(v):
            return _text(v)
    return ""


def _extract_year(date_value: str) -> Optional[int]:
    if not date_value:
        return None
    for token in ["/", "-", "."]:
        if token in date_value:
            parts = [p for p in date_value.split(token) if p]
            for part in parts:
                if len(part) == 4 and part.isdigit():
                    yr = int(part)
                    if 1800 <= yr <= 2100:
                        return yr
    if len(date_value) >= 4 and date_value[:4].isdigit():
        yr = int(date_value[:4])
        if 1800 <= yr <= 2100:
            return yr
    return None


def normalize_payload(payload: Dict) -> Tuple[str, str, str, Optional[int]]:
    doc_id = _first_non_empty(payload, ["id", "identificativo", "ecli", "numero", "num", "n"])  # noqa: E501
    title = _first_non_empty(payload, ["titolo", "title", "massima", "oggetto", "descrizione"])  # noqa: E501
    date_value = _first_non_empty(payload, ["data", "data_pubblicazione", "data_deposito", "date"])  # noqa: E501
    year = _extract_year(date_value)
    return doc_id, title, date_value, year


def iter_csv_rows(raw: bytes) -> Iterator[Dict]:
    for enc in ["utf-8", "utf-8-sig", "cp1252", "latin-1"]:
        try:
            stream = io.TextIOWrapper(io.BytesIO(raw), encoding=enc, newline="")
            sample = stream.read(4096)
            stream.seek(0)

            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except Exception:
                class _D(csv.Dialect):
                    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
                    quotechar = '"'
                    doublequote = True
                    skipinitialspace = False
                    lineterminator = "\n"
                    quoting = csv.QUOTE_MINIMAL

                dialect = _D

            reader = csv.DictReader(stream, dialect=dialect)
            for row in reader:
                if not row:
                    continue
                yield {str(k): (v if v is not None else "") for k, v in row.items()}
            return
        except Exception:
            continue


def iter_json_rows(raw: bytes) -> Iterator[Dict]:
    obj = json.loads(raw.decode("utf-8", errors="ignore"))
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                yield item
            else:
                yield {"value": item}
        return
    if isinstance(obj, dict):
        list_keys = [k for k, v in obj.items() if isinstance(v, list)]
        if len(list_keys) == 1:
            for item in obj[list_keys[0]]:
                if isinstance(item, dict):
                    yield item
                else:
                    yield {"value": item}
        else:
            yield obj
        return
    yield {"value": obj}


def local_name(tag: object) -> str:
    try:
        return etree.QName(tag).localname
    except Exception:
        return str(tag)


def flatten_xml_record(elem: etree._Element) -> Dict:
    out: Dict[str, object] = {"_tag": local_name(elem.tag)}

    for k, v in elem.attrib.items():
        out[f"@{local_name(k)}"] = _text(v)

    for child in elem:
        key = local_name(child.tag)
        val = _text(child.text)
        if val:
            if key in out:
                out[f"{key}_dup"] = val
            else:
                out[key] = val
        for ak, av in child.attrib.items():
            out[f"{key}@{local_name(ak)}"] = _text(av)

    if len(out) <= 1:
        txt = _text(elem.text)
        if txt:
            out["text"] = txt

    return out


def iter_xml_rows(raw: bytes) -> Iterator[Dict]:
    stream = io.BytesIO(raw)
    root = None
    context = etree.iterparse(stream, events=("start", "end"), recover=True, huge_tree=True)

    for event, elem in context:
        if event == "start" and root is None:
            root = elem
            continue

        if event == "end" and root is not None and elem.getparent() is root:
            row = flatten_xml_record(elem)
            if row:
                yield row

            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]


def iter_member_rows(member_name: str, raw: bytes) -> Iterator[Tuple[str, str, Dict]]:
    suffix = Path(member_name).suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(io.BytesIO(raw), "r") as nested:
            nested_members = [m for m in nested.namelist() if not m.endswith("/")]
            for inner in nested_members:
                inner_raw = nested.read(inner)
                inner_name = f"{member_name}!{inner}"
                yield from iter_member_rows(inner_name, inner_raw)
        return

    if suffix == ".csv":
        for row in iter_csv_rows(raw):
            yield member_name, "csv", row
    elif suffix == ".json":
        for row in iter_json_rows(raw):
            yield member_name, "json", row
    elif suffix == ".xml":
        for row in iter_xml_rows(raw):
            yield member_name, "xml", row


def write_jsonl_line(fp, obj: Dict) -> None:
    fp.write(json.dumps(obj, ensure_ascii=False) + "\n")


def parse_all(dataset_repo: str, token: Optional[str], max_zips: int) -> Tuple[Path, Dict]:
    manifest = load_manifest(dataset_repo, token)
    files_map = manifest.get("files", {}) or {}

    raw_paths = sorted(
        {
            str(meta.get("repo_path", ""))
            for meta in files_map.values()
            if str(meta.get("repo_path", "")).startswith("data/corte_costituzionale/raw/")
            and str(meta.get("repo_path", "")).endswith(".zip")
        }
    )

    if max_zips > 0:
        raw_paths = raw_paths[:max_zips]

    if not raw_paths:
        raise RuntimeError("No Corte raw ZIP files found in dataset manifest")

    temp_root = Path(tempfile.mkdtemp(prefix="corte_processed_"))
    out_dir = temp_root / PROCESSED_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    handles: Dict[str, object] = {}
    counts_family = defaultdict(int)
    counts_format = defaultdict(int)
    counts_zip = defaultdict(int)

    try:
        for idx, repo_path in enumerate(raw_paths, start=1):
            print(f"[{idx}/{len(raw_paths)}] Parsing {repo_path}")
            family = infer_family_from_path(repo_path)
            local_zip = hf_hub_download(
                repo_id=dataset_repo,
                repo_type="dataset",
                filename=repo_path,
                token=token,
            )

            if family not in handles:
                handles[family] = (out_dir / f"{family}.jsonl").open("w", encoding="utf-8")

            family_fp = handles[family]

            with zipfile.ZipFile(local_zip, "r") as zf:
                members = [m for m in zf.namelist() if not m.endswith("/")]
                for member in members:
                    try:
                        raw = zf.read(member)
                    except Exception:
                        continue

                    rec_in_member = 0
                    for source_member, fmt, payload in iter_member_rows(member, raw):
                        doc_id, title, date_value, year = normalize_payload(payload)
                        rec = {
                            "family": family,
                            "source_zip": repo_path,
                            "source_member": source_member,
                            "source_format": fmt,
                            "doc_id": doc_id,
                            "title": title,
                            "date": date_value,
                            "year": year,
                            "payload": payload,
                        }
                        write_jsonl_line(family_fp, rec)
                        rec_in_member += 1

                    counts_family[family] += rec_in_member
                    counts_format[fmt] += rec_in_member
                    counts_zip[repo_path] += rec_in_member

    finally:
        for fp in handles.values():
            fp.close()

    total_records = sum(counts_family.values())
    summary = {
        "dataset_repo": dataset_repo,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_manifest_updated_at": manifest.get("updated_at_utc"),
        "raw_zip_files": len(raw_paths),
        "total_records": total_records,
        "records_by_family": dict(sorted(counts_family.items())),
        "records_by_format": dict(sorted(counts_format.items())),
        "records_by_zip": dict(sorted(counts_zip.items())),
        "processed_files": [
            f"{PROCESSED_DIR}/{name}.jsonl" for name in sorted(counts_family.keys())
        ],
    }

    summary_path = temp_root / SUMMARY_PATH
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Parsed records: {total_records:,}")
    for fam, cnt in sorted(counts_family.items()):
        print(f"  {fam}: {cnt:,}")

    return temp_root, summary


def upload_processed(root_dir: Path, dataset_repo: str, token: str) -> None:
    api = HfApi(token=token)
    api.upload_folder(
        repo_id=dataset_repo,
        repo_type="dataset",
        folder_path=str(root_dir),
        commit_message="Corte processed tables refresh",
    )


def main() -> int:
    args = parse_args()
    token = args.token

    root_dir, _summary = parse_all(args.dataset_repo, token, args.max_zips)

    if args.upload:
        if not token:
            token = os.environ.get("HF_TOKEN")  # type: ignore[name-defined]
        if not token:
            raise RuntimeError("HF token required for upload. Set HF_TOKEN or pass --token.")
        upload_processed(root_dir, args.dataset_repo, token)
        print(f"Uploaded processed Corte tables to {args.dataset_repo}")
    else:
        print(f"Processed outputs generated at: {root_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
