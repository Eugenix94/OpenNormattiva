#!/usr/bin/env python3
"""
Fix abrogati status: update all URNs from the O-track ZIP to status='abrogated'.
Handles both:
  - URNs already in DB as in_force (UPDATE status only, keep vigente text)
  - URNs missing from DB (already inserted by build_voom.py as abrogated)
"""
import zipfile
import sqlite3
import xml.etree.ElementTree as ET
import sys
import time
from pathlib import Path

DB_PATH    = Path("data/laws.db")
ZIP_PATH   = Path("akn/originale/atti_normativi_abrogati_O.zip")
AKN_NS     = {
    "akn": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0",
    "frbr": "http://docs.oasis-open.org/legaldocml/ns/akn/3.0",
}

def extract_urn(xml_bytes: bytes) -> str | None:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return None
    # Try namespaced and plain search
    for elem in root.iter():
        if elem.tag.endswith("FRBRalias"):
            if elem.get("name") == "urn:nir":
                return elem.get("value")
    return None

def main():
    if not ZIP_PATH.exists():
        print(f"ERROR: ZIP not found at {ZIP_PATH}")
        sys.exit(1)
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        sys.exit(1)

    print("Extracting URNs from abrogati ZIP...")
    t0 = time.time()
    urns = []
    with zipfile.ZipFile(ZIP_PATH, "r") as zf:
        xml_files = [f for f in zf.namelist() if f.endswith(".xml")]
        total = len(xml_files)
        print(f"  {total:,} XML files")
        for i, fname in enumerate(xml_files):
            urn = extract_urn(zf.read(fname))
            if urn:
                urns.append((urn,))
            if (i + 1) % 10_000 == 0:
                print(f"  [{i+1:,}/{total:,}] extracted {len(urns):,} URNs", flush=True)

    print(f"  Done: {len(urns):,} URNs extracted in {time.time()-t0:.0f}s")

    print("\nUpdating status='abrogated' for all O-track URNs in DB...")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Count before
    before_abr = conn.execute("SELECT COUNT(*) FROM laws WHERE status='abrogated'").fetchone()[0]
    before_inf = conn.execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
    print(f"  BEFORE: {before_inf:,} in_force, {before_abr:,} abrogated")

    # Batch UPDATE
    BATCH = 500
    updated = 0
    t1 = time.time()
    for i in range(0, len(urns), BATCH):
        batch = urns[i:i+BATCH]
        # UPDATE each URN in the batch
        placeholders = ",".join("?" * len(batch))
        flat_urns = [u[0] for u in batch]
        cur = conn.execute(
            f"UPDATE laws SET status='abrogated' WHERE urn IN ({placeholders})",
            flat_urns
        )
        updated += cur.rowcount
        if (i // BATCH + 1) % 50 == 0:
            conn.commit()
            pct = (i + len(batch)) / len(urns) * 100
            print(f"  [{i+len(batch):,}/{len(urns):,}] updated={updated:,} ({pct:.0f}%)", flush=True)

    conn.commit()

    # Count after
    after_abr = conn.execute("SELECT COUNT(*) FROM laws WHERE status='abrogated'").fetchone()[0]
    after_inf = conn.execute("SELECT COUNT(*) FROM laws WHERE status='in_force'").fetchone()[0]
    tot = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    conn.close()

    print(f"\n  AFTER:  {after_inf:,} in_force, {after_abr:,} abrogated (total: {tot:,})")
    print(f"  Changed: {after_abr - before_abr:,} laws updated to abrogated")
    print(f"  Done in {time.time()-t1:.0f}s")

    print("\nRebuilding FTS index...")
    conn2 = sqlite3.connect(str(DB_PATH))
    conn2.execute("PRAGMA journal_mode=WAL")
    conn2.execute("INSERT INTO laws_fts(laws_fts) VALUES('rebuild')")
    conn2.commit()
    conn2.close()
    print("  FTS rebuild done")

    print("\nSUMMARY:")
    print(f"  in_force (vigente):   {after_inf:,}")
    print(f"  abrogated:            {after_abr:,}")
    print(f"  TOTAL:                {tot:,}")

if __name__ == "__main__":
    main()
