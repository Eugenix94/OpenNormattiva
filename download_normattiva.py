#!/usr/bin/env python3
"""
download_normattiva.py

Phase 1 downloader for Normattiva raw mirror (AKN ORIGINALE variant).
Produces a complete, versioned HF dataset with manifest and update log.

Usage:
  python download_normattiva.py                    # download all collections
  python download_normattiva.py --collection "DPR" # download one collection
  python download_normattiva.py --dry-run          # show what would be downloaded

Output:
  akn/originale/                    # collection ZIPs
  manifests/versions_manifest.jsonl # metadata (one JSON per line)
  metadata/                         # logs, last run state
"""

import urllib.request
import urllib.parse
import urllib.error
import json
import hashlib
import time
import os
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

BASE_API = "https://api.normattiva.it/t/normattiva.api/bff-opendata/v1"
OUTDIR_BASE = "."
OUTDIR_AKN = "akn/originale"
OUTDIR_MANIFESTS = "manifests"
OUTDIR_METADATA = "metadata"

UA = "NormattivaRawMirror/1.0 (Apache-2.0; contact redazione@normattiva.it)"
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://dati.normattiva.it/",
    "Accept": "*/*",
}

DELAY_S = 0.5
MAX_RETRIES = 3
RETRY_BACKOFF_S = 3

# ─────────────────────────────────────────────────────────────────────────────
# Global session state
# ─────────────────────────────────────────────────────────────────────────────

_session_cookies = {}


def _cookie_header():
    if _session_cookies:
        return "; ".join(f"{k}={v}" for k, v in _session_cookies.items())
    return None


def _absorb_cookies(response):
    for raw_hdr, val in response.headers.items():
        if raw_hdr.lower() == "set-cookie":
            kv = val.split(";")[0].split("=", 1)
            if len(kv) == 2:
                _session_cookies[kv[0].strip()] = kv[1].strip()


# ─────────────────────────────────────────────────────────────────────────────
# API calls
# ─────────────────────────────────────────────────────────────────────────────


def get_json(path, label=""):
    """Fetch JSON from API with retries and cookie handling."""
    url = f"{BASE_API}{path}"
    for attempt in range(1, MAX_RETRIES + 1):
        hdrs = dict(HEADERS)
        ck = _cookie_header()
        if ck:
            hdrs["Cookie"] = ck
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                _absorb_cookies(r)
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"{label} failed after {MAX_RETRIES} retries: {exc}")
            wait = RETRY_BACKOFF_S * attempt
            print(f"    [{label}] retry {attempt}/{MAX_RETRIES}, waiting {wait}s …")
            time.sleep(wait)


def download_collection(nome, formato_richiesta="O", dry_run=False):
    """Download one collection ZIP. Returns metadata dict or None on failure."""
    params = urllib.parse.urlencode({
        "nome": nome,
        "formato": "AKN",
        "formatoRichiesta": formato_richiesta,
    })
    url = f"{BASE_API}/api/v1/collections/download/collection-preconfezionata?{params}"

    # Sanitize filename
    safe_name = nome.replace(" ", "_").replace("(", "").replace(")", "").lower()
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    outfile = f"{OUTDIR_AKN}/{safe_name}_{formato_richiesta}_{date_str}.zip"

    if dry_run:
        return {"filename": os.path.basename(outfile), "path": outfile}

    os.makedirs(OUTDIR_AKN, exist_ok=True)
    if os.path.exists(outfile):
        print(f"    {outfile} exists, skip")
        return None

    for attempt in range(1, MAX_RETRIES + 1):
        hdrs = dict(HEADERS)
        ck = _cookie_header()
        if ck:
            hdrs["Cookie"] = ck
        req = urllib.request.Request(url, headers=hdrs)
        t0 = time.time()
        sha = hashlib.sha256()
        size = 0
        etag = None
        http_status = None

        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                http_status = r.status
                etag = r.headers.get("x-etag") or r.headers.get("ETag")
                _absorb_cookies(r)

                with open(outfile, "wb") as fh:
                    while True:
                        chunk = r.read(131072)
                        if not chunk:
                            break
                        sha.update(chunk)
                        size += len(chunk)
                        fh.write(chunk)

            elapsed = time.time() - t0
            return {
                "filename": os.path.basename(outfile),
                "path": outfile,
                "size_bytes": size,
                "sha256": sha.hexdigest(),
                "elapsed_s": round(elapsed, 2),
                "http_status": http_status,
                "etag": etag,
                "downloaded_at": datetime.now(timezone.utc).isoformat() + "Z",
            }

        except Exception as exc:
            if os.path.exists(outfile):
                os.remove(outfile)
            if attempt == MAX_RETRIES:
                return {"filename": os.path.basename(outfile), "error": str(exc)}
            wait = RETRY_BACKOFF_S * attempt
            print(f"    [retry {attempt}/{MAX_RETRIES}: {exc}] waiting {wait}s …")
            time.sleep(wait)


# ─────────────────────────────────────────────────────────────────────────────
# Manifest and log management
# ─────────────────────────────────────────────────────────────────────────────


def load_manifest():
    """Load existing manifest or return empty list."""
    manifest_file = f"{OUTDIR_MANIFESTS}/versions_manifest.jsonl"
    if not os.path.exists(manifest_file):
        return {}
    
    manifest = {}
    with open(manifest_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                manifest[obj["filename"]] = obj
    return manifest


def save_manifest_entry(entry):
    """Append one entry to versions_manifest.jsonl."""
    os.makedirs(OUTDIR_MANIFESTS, exist_ok=True)
    manifest_file = f"{OUTDIR_MANIFESTS}/versions_manifest.jsonl"
    with open(manifest_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def save_update_log(results):
    """Write metadata/update_log.json with details of this run."""
    os.makedirs(OUTDIR_METADATA, exist_ok=True)
    log_file = f"{OUTDIR_METADATA}/update_log.json"
    
    # Load existing log or start fresh
    if os.path.exists(log_file):
        with open(log_file, "r", encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {"runs": []}
    
    # Add new run entry
    run_entry = {
        "run_timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        "files": results,
        "summary": {
            "total": len(results),
            "successful": sum(1 for r in results if "sha256" in r),
            "failed": sum(1 for r in results if "error" in r),
            "skipped": sum(1 for r in results if r is None),
        }
    }
    existing["runs"].append(run_entry)
    
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


def save_last_update():
    """Write metadata/last_update.txt with ISO timestamp."""
    os.makedirs(OUTDIR_METADATA, exist_ok=True)
    with open(f"{OUTDIR_METADATA}/last_update.txt", "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).isoformat() + "Z\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Download Normattiva collections and generate HF dataset.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python download_normattiva.py                              # download all
  python download_normattiva.py --collection "DPR"           # one collection
  python download_normattiva.py --dry-run                    # show plan
        """
    )
    parser.add_argument(
        "--collection",
        type=str,
        default=None,
        help="Download only this collection name (exact match)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without downloading"
    )
    args = parser.parse_args()

    print("=" * 70)
    print("NORMATTIVA RAW MIRROR — Phase 1 Downloader")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}Z")
    print("=" * 70)

    # Fetch catalogue
    print("\nFetching collection catalogue …")
    try:
        raw_collections = get_json(
            "/api/v1/collections/collection-predefinite",
            label="catalogue"
        )
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)

    # Build collection → ORIGINALE metadata
    catalogue = {}
    for c in raw_collections:
        n = c["nomeCollezione"]
        if c["formatoCollezione"] == "O":
            catalogue[n] = {
                "acts_count": c["numeroAtti"],
                "built_at": c["dataCreazione"],
            }

    collection_names = sorted(catalogue.keys())
    print(f"Found {len(collection_names)} collections with ORIGINALE variant.")

    # Filter to single collection if specified
    if args.collection:
        if args.collection not in collection_names:
            print(f"ERROR: Collection '{args.collection}' not found.")
            print(f"Available: {', '.join(collection_names)}")
            sys.exit(1)
        collection_names = [args.collection]
        print(f"Filtering to: {args.collection}")

    # Download phase
    if args.dry_run:
        print("\n─── DRY RUN: What would be downloaded ───")
    else:
        print("\n─── DOWNLOADING ───")

    results = []
    manifest_exist = load_manifest()

    for idx, nome in enumerate(collection_names, 1):
        acts = catalogue[nome]["acts_count"]
        built = catalogue[nome]["built_at"]
        print(f"[{idx:2d}/{len(collection_names)}] {nome:<50} ({acts:>8,} acts, {built})")

        result = download_collection(nome, formato_richiesta="O", dry_run=args.dry_run)
        
        if result is None:
            results.append(None)
        else:
            results.append(result)
            
            # Build and save manifest entry (only if actually downloaded)
            if not args.dry_run and "sha256" in result:
                manifest_entry = {
                    "filename": result["filename"],
                    "path": result["path"],
                    "collection_name": nome,
                    "variant": "O",
                    "format": "akn",
                    "acts_count": acts,
                    "sha256": result["sha256"],
                    "size_bytes": result["size_bytes"],
                    "url": (
                        f"{BASE_API}/api/v1/collections/download/"
                        f"collection-preconfezionata?nome={urllib.parse.quote(nome)}"
                        f"&formato=AKN&formatoRichiesta=O"
                    ),
                    "etag": result.get("etag"),
                    "built_at": built,
                    "downloaded_at": result.get("downloaded_at"),
                }
                save_manifest_entry(manifest_entry)
                
                # Print result
                mb = result["size_bytes"] / 1024 / 1024
                print(f"         → {mb:7.2f} MB  sha256={result['sha256'][:16]}…")
            elif args.dry_run:
                print(f"         → (dry-run, would create {result['path']})")
            elif "error" in result:
                print(f"         → ERROR: {result['error'][:100]}")

        if not args.dry_run:
            time.sleep(DELAY_S)

    # Summary
    if not args.dry_run:
        successful = sum(1 for r in results if r is not None and "sha256" in r)
        failed = sum(1 for r in results if r is not None and "error" in r)
        print("\n" + "=" * 70)
        print(f"Complete: {successful} downloaded, {failed} failed")
        save_update_log([r for r in results if r is not None])
        save_last_update()
        print(f"Manifest: {OUTDIR_MANIFESTS}/versions_manifest.jsonl")
        print(f"Log:      {OUTDIR_METADATA}/update_log.json")
        print(f"Last update: {OUTDIR_METADATA}/last_update.txt")
    else:
        print("\n" + "=" * 70)
        print(f"Dry-run: would download {len(collection_names)} collections")

    print("=" * 70)


if __name__ == "__main__":
    main()
