#!/usr/bin/env python3
"""
test_assistant.py — Comprehensive AI chatbot test battery
Run directly against local DB (no Streamlit needed) to discover gaps.
"""
import sys, os, json, time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "space")

# Patch Streamlit so imports work without a running session
import types
st_mock = types.ModuleType("streamlit")
st_mock.cache_data = lambda *args, **kwargs: (lambda f: f)
st_mock.secrets = {}
st_mock.session_state = {}
sys.modules["streamlit"] = st_mock

# Also patch requests/huggingface_hub for offline
import unittest.mock as mock

import sqlite3
from core.db import LawDatabase

DB_PATH = "data/laws.db"
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")

# --- Load assistant modules ---
from space.global_ai_copilot import (
    _get_dataset_statistics,
    _detect_query_intent,
    _answer_meta_query,
    _retrieve_evidence,
    _is_kingdom_query,
)

db = LawDatabase(DB_PATH)
stats = _get_dataset_statistics(db)


# ---- Helpers ----
def fts_search(query, limit=8):
    try:
        # Use the real retrieval pipeline (includes canonical pinning + expansions + reranking)
        return _retrieve_evidence(db, query, top_k=limit)
    except Exception as e:
        return []


def test_case(label, query, expected_in_results=None, expected_urns=None, expected_intent=None):
    """Run a single test case and return result dict."""
    intent = _detect_query_intent(query, stats)
    results = []
    found_urns = set()

    if intent == "meta" or _is_kingdom_query(query):
        try:
            meta_ans = _answer_meta_query(query, stats, db)
            results = [{"type": "meta_answer", "content": meta_ans[:400]}]
        except Exception as e:
            results = [{"type": "error", "content": str(e)}]
    else:
        rows = fts_search(query)
        for r in rows:
            found_urns.add(str(r.get("urn", "")))
            results.append({
                "urn": r.get("urn"),
                "title": (r.get("title") or "")[:80],
                "full_title": (r.get("title") or ""),
                "status": r.get("status"),
                "score": round(float(r.get("relevance_score") or 0), 3),
                "snippet": (r.get("snippet") or ""),
            })

    # Check expectations
    pass_expected_content = True
    pass_expected_urns = True
    notes = []

    if expected_in_results and intent != "meta":
        for kw in expected_in_results:
            found = any(kw.lower() in (r.get("full_title") or r.get("title") or "").lower() or
                        kw.lower() in (r.get("urn") or "").lower() or
                        kw.lower() in (r.get("snippet") or "").lower()
                        for r in results)
            if not found:
                pass_expected_content = False
                notes.append(f"MISSING_TERM: {kw}")

    if expected_urns and intent != "meta":
        for urn in expected_urns:
            if urn not in found_urns:
                pass_expected_urns = False
                notes.append(f"MISSING_URN: {urn}")

    if expected_intent and intent != expected_intent:
        notes.append(f"INTENT_WRONG: got={intent} expected={expected_intent}")

    overall = "PASS" if (pass_expected_content and pass_expected_urns and not any("INTENT_WRONG" in n for n in notes)) else "FAIL"
    return {
        "label": label,
        "query": query,
        "intent": intent,
        "result_count": len(results),
        "top_results": results[:3],
        "status": overall,
        "notes": notes,
    }


# ============================================================
# TEST CASES
# ============================================================
TESTS = [
    # --- META / DATASET AWARENESS ---
    ("META-1: Total count", "quante leggi ci sono nel dataset?", None, None, "meta"),
    ("META-2: Kingdom count", "quante leggi del regno sono ancora in vigore?", None, None, "meta"),
    ("META-3: Type distribution", "quali tipi di atti ci sono nel dataset?", None, None, "meta"),
    ("META-4: Latest laws", "qual è la norma più recente nel database?", None, None, "meta"),
    ("META-5: Abrogated count", "quante norme sono abrogate?", None, None, "meta"),

    # --- CANONICAL LAW LOOKUP ---
    ("CANON-1: Codice Civile by name", "codice civile articoli obbligazioni contratto", ["codice civile"], ["urn:nir:stato:regio.decreto:1942-03-16;262"], "legal"),
    ("CANON-2: Codice Penale by name", "codice penale reato omicidio sanzione", ["codice penale"], ["urn:nir:stato:regio.decreto:1930-10-19;1398"], "legal"),
    ("CANON-3: TULPS by name", "testo unico leggi pubblica sicurezza TULPS", ["773"], None, "legal"),
    ("CANON-4: Legge fallimentare", "legge fallimentare concordato preventivo", ["fallimento", "267"], None, "legal"),
    ("CANON-5: Diritto d'autore", "protezione diritto d autore opere musicali", ["autore", "633"], None, "legal"),
    ("CANON-6: L.2248/1865", "unificazione amministrativa regno italia PA", ["unificazione", "2248"], None, "legal"),
    ("CANON-7: Codice della strada", "codice della strada infrazioni multa", ["strada"], None, "legal"),

    # --- DOMAIN QUERIES ---
    ("DOMAIN-1: Lavoro - ferie", "quanti giorni di ferie spettano ai lavoratori per legge", ["lavor", "ferie"], None, "legal"),
    ("DOMAIN-2: Lavoro - licenziamento", "disciplina del licenziamento illegittimo reintegra", ["lavor", "licenziament"], None, "legal"),
    ("DOMAIN-3: Fiscale - IVA", "aliquota IVA beni alimentari disciplina", ["iva", "imposta"], None, "legal"),
    ("DOMAIN-4: Penale - diffamazione", "diffamazione social media reato pena detentiva", ["diffamaz", "penal"], None, "legal"),
    ("DOMAIN-5: Salute - SSN", "diritto alla salute servizio sanitario nazionale accesso", ["sanit", "salute"], None, "legal"),
    ("DOMAIN-6: Istruzione - università", "accesso università requisiti immatricolazione laurea triennale", ["univers", "istruzion", "ammission"], None, "legal"),
    ("DOMAIN-7: Ambiente - VIA", "valutazione impatto ambientale procedure autorizzazione", ["ambient"], None, "legal"),
    ("DOMAIN-8: Privacy - GDPR", "protezione dati personali GDPR consenso utente", ["dati personali", "196"], None, "legal"),

    # --- HISTORICAL / COMPARATIVE ---
    ("HIST-1: Era kingdom vs republic", "quante norme del periodo repubblicano sono nel database", None, None, "meta"),
    ("HIST-2: Constitution search", "costituzione italiana diritti fondamentali libertà", ["costituzion"], None, "legal"),
    ("HIST-3: EU law reception", "recepimento direttive europee decreto legislativo", ["direttiv", "UE"], None, "legal"),

    # --- CROSS-REFERENCE / CITATION ---
    ("CITE-1: Most cited laws", "quali sono le norme più citate nel sistema", None, None, "meta"),
    ("CITE-2: Law citing codice civile", "decreti che citano o modificano il codice civile", ["civile"], None, "legal"),

    # --- EDGE CASES ---
    ("EDGE-1: Gibberish", "xyzqwqwqwz norma inesistente abc123", None, None, "legal"),
    ("EDGE-2: Very short query", "lavoro", ["lavor"], None, "legal"),
    ("EDGE-3: English query", "how many laws are in the database", None, None, "meta"),
    ("EDGE-4: Ambiguous query", "chi ha scritto la costituzione", None, None, "legal"),
]


# ============================================================
# RUN TESTS
# ============================================================
print("=" * 70)
print("ITALIAN LEGAL AI CHATBOT — COMPREHENSIVE SELF-TEST BATTERY")
print("=" * 70)
print(f"DB: {DB_PATH} | Total laws: {stats.get('total_laws', '?'):,}")
print()

results = []
pass_count = 0
fail_count = 0

for args in TESTS:
    r = test_case(*args)
    results.append(r)
    icon = "✓" if r["status"] == "PASS" else "✗"
    print(f"{icon} [{r['status']}] {r['label']}")
    print(f"  Query: {r['query'][:80]}")
    print(f"  Intent: {r['intent']} | Results: {r['result_count']}")
    if r["top_results"]:
        for tr in r["top_results"][:2]:
            if tr.get("type") == "meta_answer":
                print(f"  Meta: {tr['content'][:160]}")
            else:
                print(f"  [{tr.get('status','?')}] {tr.get('title','')[:60]} | {tr.get('urn','')[:50]}")
    if r["notes"]:
        for n in r["notes"]:
            print(f"  ⚠ {n}")
    print()

    if r["status"] == "PASS":
        pass_count += 1
    else:
        fail_count += 1

print("=" * 70)
print(f"RESULTS: {pass_count} PASS / {fail_count} FAIL / {len(results)} total")
print()

# ============================================================
# ANALYSIS & RECOMMENDATIONS
# ============================================================
print("=" * 70)
print("ANALYSIS OF GAPS AND ENHANCEMENT RECOMMENDATIONS")
print("=" * 70)

# 1. Intent detection accuracy
wrong_intent = [r for r in results if any("INTENT_WRONG" in n for n in r["notes"])]
print(f"\n[1] Intent detection misclassifications: {len(wrong_intent)}")
for r in wrong_intent:
    print(f"   {r['label']}: {r['notes']}")

# 2. Missing URNs in top results
missing_urns = [r for r in results if any("MISSING_URN" in n for n in r["notes"])]
print(f"\n[2] Cases where expected URN not retrieved: {len(missing_urns)}")
for r in missing_urns:
    print(f"   {r['label']}: {r['notes']}")

# 3. Missing keyword terms in results
missing_terms = [r for r in results if any("MISSING_TERM" in n for n in r["notes"])]
print(f"\n[3] Cases where expected term not in results: {len(missing_terms)}")
for r in missing_terms:
    print(f"   {r['label']}: {r['notes']}")

# 4. Zero-result queries (non-meta)
zero_results = [r for r in results if r["result_count"] == 0 and r["intent"] != "meta"]
print(f"\n[4] Non-meta queries with ZERO results: {len(zero_results)}")
for r in zero_results:
    print(f"   {r['label']}: '{r['query']}'")

# 5. Low result count for legal queries
low_results = [r for r in results if r["intent"] == "legal" and 0 < r["result_count"] < 3]
print(f"\n[5] Legal queries with fewer than 3 results: {len(low_results)}")
for r in low_results:
    print(f"   {r['label']}: {r['result_count']} results")

# Save full results
out_path = "logs/test_battery_results.json"
Path("logs").mkdir(exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({
        "stats": {k: v for k, v in stats.items() if k != "latest_laws"},
        "results": results,
        "summary": {"pass": pass_count, "fail": fail_count, "total": len(results)}
    }, f, ensure_ascii=False, indent=2)
print(f"\nFull results saved to {out_path}")
