#!/usr/bin/env python3
"""
Compare live Normattiva API collection counts against the local laws.db.
Shows which collections/types have more acts in the API than in our DB.
Run from the normattiva-lab worktree directory.
"""
import sqlite3
import os
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from normattiva_api_client import NormattivaAPI

DB_PATHS = [
    Path(__file__).parent / "data" / "laws.db",
    Path(r"c:\Users\Dell\Documents\VSC Projects\OpenNormattiva\data\laws.db"),
    Path(r"C:\Users\Dell\.cache\huggingface\hub\datasets--diatribe00--normattiva-lab-data\snapshots\d31dda61dbba309a369c33d2cb1747ed3c2fd3ce\data\laws.db"),
    Path(r"C:\Users\Dell\.cache\huggingface\hub\datasets--diatribe00--normattiva-data\snapshots\7faad2c7deacc554d32030abe0734ca0f5a9ae2e\data\laws.db"),
]

# Map Normattiva collection names -> known type values in DB (partial match)
COLLECTION_TYPE_MAP = {
    "Regi decreti":                              "RD",
    "DPR":                                       "DPR",
    "Decreti Legislativi":                       "D.Lgs.",
    "DL e leggi di conversione":                 "D.L.",
    "DL decaduti":                               "D.L.",
    "DPCM":                                      "DPCM",
    "Codici":                                    "Codice",
    "Leggi costituzionali":                      "Legge cost.",
    "Leggi finanziarie e di bilancio":           "Legge",
    "Leggi di ratifica":                         "Legge",
    "Leggi delega e relativi provvedimenti delegati": "Legge",
    "Leggi di delegazione europea":              "Legge",
    "Leggi contenenti deleghe":                  "Legge",
    "Testi Unici":                               "D.Lgs.",
    "Regolamenti governativi":                   "DPR",
    "Regolamenti ministeriali":                  "DM",
    "Regolamenti di delegificazione":            "DPR",
    "Regi decreti legislativi":                  "R.D.L.",
    "Decreti legislativi luogotenenziali":       "D.Lgs.Lgt.",
    "Atti di recepimento direttive UE":          "D.Lgs.",
    "Atti di attuazione Regolamenti UE":         "D.Lgs.",
    "Atti normativi abrogati (in originale)":    None,  # abrogated mix
    "DL proroghe":                               "D.L.",
}

def main():
    # --- Find DB ---
    db_path = next((p for p in DB_PATHS if p.exists()), None)
    if not db_path:
        print("ERROR: No laws.db found. Searched:")
        for p in DB_PATHS:
            print(f"  {p}")
        sys.exit(1)
    print(f"Database: {db_path}  ({db_path.stat().st_size / 1e6:.0f} MB)\n")

    con = sqlite3.connect(db_path)
    total_db = con.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    print(f"Total laws in DB: {total_db:,}\n")

    db_by_type = dict(con.execute(
        "SELECT COALESCE(type,'?'), COUNT(*) FROM laws GROUP BY type"
    ).fetchall())

    # --- Fetch live catalogue ---
    print("Fetching live Normattiva catalogue...")
    api = NormattivaAPI(timeout_s=30, retries=2)
    try:
        catalogue = api.get_collection_catalogue()
    except Exception as e:
        print(f"ERROR fetching catalogue: {e}")
        sys.exit(1)
    print(f"Collections returned by API: {len(catalogue)}\n")

    # Deduplicate by (name, variant) and pick the most recently created
    best: dict[tuple, dict] = {}
    for c in catalogue:
        key = (c.get("nomeCollezione"), c.get("formatoCollezione"))
        existing = best.get(key)
        if existing is None or c.get("dataCreazione","") >= existing.get("dataCreazione",""):
            best[key] = c

    # Focus on VIGENTE + ORIGINALE variants for the gap analysis
    focus_variants = {"V", "O"}
    print(f"{'Collection':<50} {'Var':<4} {'API acts':>9}  {'DB type':<12} {'DB count':>9}  {'GAP':>9}  {'Updated'}")
    print("-" * 115)

    total_api_unique = 0
    gap_rows = []

    seen_names = set()
    for (name, variant), c in sorted(best.items()):
        if variant not in focus_variants:
            continue
        if name in seen_names and variant == "O":
            continue  # prefer V when both exist
        seen_names.add(name)

        api_count = c.get("numeroAtti", 0)
        date = c.get("dataCreazione", "?")
        db_type = COLLECTION_TYPE_MAP.get(name, "?")
        db_count = db_by_type.get(db_type, 0) if db_type else 0

        # For multi-type collections (Legge) sum all Legge variants
        if db_type == "Legge":
            db_count = sum(v for k, v in db_by_type.items() if isinstance(k, str) and "legge" in k.lower())
        if db_type == "D.L.":
            db_count = sum(v for k, v in db_by_type.items() if isinstance(k, str) and ("d.l." in k.lower() or "decreto-legge" in k.lower()))

        gap = api_count - db_count if db_count > 0 else api_count
        total_api_unique += api_count

        flag = "  ⚠ MISSING" if gap > 500 else ("  ★ new" if 0 < gap <= 500 else "")
        print(f"{name:<50} {variant:<4} {api_count:>9,}  {str(db_type):<12} {db_count:>9,}  {gap:>+9,}  {date}{flag}")

        if gap > 0:
            gap_rows.append((gap, name, variant, api_count, db_count, date))

    print("-" * 115)
    print(f"\nTotal DB laws: {total_db:,}")
    print(f"\nCollections with POSITIVE gap (API has more than DB):")
    for gap, name, variant, api_n, db_n, date in sorted(gap_rows, reverse=True)[:20]:
        print(f"  +{gap:>8,}  {name}  [{variant}]  (API={api_n:,}, DB={db_n:,}, updated {date})")

    con.close()

if __name__ == "__main__":
    main()
