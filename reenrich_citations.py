#!/usr/bin/env python3
"""
Re-enrich the JSONL file with full-URN citations.

Reads laws_vigente.jsonl, re-extracts all citations using the full-URN
citation parser, and writes the result back in-place.  Does NOT rebuild
the DB here — that is handled by insert_citations.py.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_akn import AKNParser

JSONL_IN  = Path("data/processed/laws_vigente.jsonl")
JSONL_OUT = Path("data/processed/laws_vigente_enriched.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  default=str(JSONL_IN))
    ap.add_argument("--output", default=str(JSONL_OUT))
    args = ap.parse_args()

    src = Path(args.input)
    dst = Path(args.output)

    if not src.exists():
        print(f"ERROR: {src} not found")
        sys.exit(1)

    parser = AKNParser()
    total = 0
    cit_count = 0

    print(f"Reading {src} ...")
    with open(src, "r", encoding="utf-8") as fin, \
         open(dst, "w", encoding="utf-8") as fout:
        for lineno, line in enumerate(fin, 1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            laws = record if isinstance(record, list) else [record]
            enriched = []
            for law in laws:
                text = law.get("text", "")
                law["citations"] = parser.extract_citations(text)
                law["text_length"] = len(text)
                if law.get("date"):
                    law["year"] = law["date"][:4]
                cit_count += len(law["citations"])
                total += 1
                enriched.append(law)
            if isinstance(record, list):
                fout.write(json.dumps(enriched, ensure_ascii=False) + "\n")
            else:
                fout.write(json.dumps(enriched[0], ensure_ascii=False) + "\n")
            if lineno % 5000 == 0:
                print(f"  ... {lineno} lines, {total} laws, {cit_count} citations")

    print(f"Done: {total} laws, {cit_count} citations")
    print(f"Written to {dst}")

    # Replace source with enriched version
    backup = src.with_suffix(".jsonl.bak")
    os.replace(dst, src)   # atomic on same filesystem
    print(f"Replaced {src}")


if __name__ == "__main__":
    main()
