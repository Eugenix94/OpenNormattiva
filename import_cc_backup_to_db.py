#!/usr/bin/env python3
"""
Import Corte Costituzionale open-data backup into data/laws.db.

Expected extracted layout (already present in this workspace):
  tmp_cc_full/
    CC_OpenPronunce_*/Cc_Opendata_Pronunce_YYYY.zip
    CC_OpenMassime_*/Cc_OpenData_Massime_YYYY.zip

Usage examples:
  python import_cc_backup_to_db.py --analyze
  python import_cc_backup_to_db.py --write
  python import_cc_backup_to_db.py --write --source-root tmp_cc_full --db data/laws.db
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import zipfile
from datetime import datetime, UTC
from pathlib import Path
import xml.etree.ElementTree as ET

ESITO_PATTERNS = [
    (r"dichiara l.illegittimit[àa] costituzionale", "illegittimità"),
    (r"dichiara inammissibil", "inammissibile"),
    (r"dichiara non fondat", "non fondata"),
    (r"dichiara fondat", "fondata"),
    (r"dichiara la cessazione della materia", "cessata materia"),
    (r"dichiara estint", "estinta"),
    (r"ordina la restituzione degli atti", "restituzione atti"),
]


def text_of(elem: ET.Element | None) -> str:
    if elem is None:
        return ""
    return " ".join((elem.itertext())).strip()


def normalize_tipo(raw: str) -> str:
    s = (raw or "").strip().lower()
    if "ordinanza" in s or s.startswith("ord"):
        return "Ordinanza"
    if "sentenza" in s or s.startswith("sent"):
        return "Sentenza"
    return raw.strip().capitalize() if raw else "Sentenza"


def detect_esito(full_text: str) -> str:
    lower = (full_text or "").lower()
    for pattern, label in ESITO_PATTERNS:
        if re.search(pattern, lower):
            return label
    return "altro"


def extract_articoli_cost(full_text: str) -> list[str]:
    # Keep aligned with the project's existing extraction logic.
    found = re.findall(
        r"art(?:t|icol[oi])[\.\s]+(\d+(?:[,\-]\s*\d+)*)[,\s]+(?:della\s+)?Cost\.",
        full_text,
        re.IGNORECASE,
    )
    return sorted(set(found))


def parse_pronunce_zip(zpath: Path) -> list[dict]:
    records: list[dict] = []
    with zipfile.ZipFile(zpath) as zf:
        xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
        if not xml_name:
            return records
        root = ET.fromstring(zf.read(xml_name))

    for item in list(root):
        testata = item.find("pronuncia_testata")
        testo = item.find("pronuncia_testo")

        anno = text_of(testata.find("anno_pronuncia") if testata is not None else None)
        numero = text_of(testata.find("numero_pronuncia") if testata is not None else None)
        ecli = text_of(testata.find("ecli") if testata is not None else None)
        tipologia = text_of(testata.find("tipologia_pronuncia") if testata is not None else None)
        data_deposito = text_of(testata.find("data_deposito") if testata is not None else None)

        epigrafe = text_of(testo.find("epigrafe") if testo is not None else None)
        fatto = text_of(testo.find("testo/fatto") if testo is not None else None)
        diritto = text_of(testo.find("testo/diritto") if testo is not None else None)
        dispositivo = text_of(testo.find("dispositivo") if testo is not None else None)
        full_text = "\n\n".join([x for x in [epigrafe, fatto, diritto, dispositivo] if x]).strip()

        if not anno or not numero:
            continue
        anno_i = int(re.sub(r"\D+", "", anno))
        numero_i = int(re.sub(r"\D+", "", numero))
        if not ecli:
            ecli = f"ECLI:IT:COST:{anno_i}:{numero_i}"

        records.append(
            {
                "ecli": ecli,
                "numero": numero_i,
                "anno": anno_i,
                "tipo": normalize_tipo(tipologia),
                "data_deposito": data_deposito,
                "oggetto": epigrafe[:1200] if epigrafe else "",
                "esito": detect_esito(full_text),
                "articoli_cost": json.dumps(extract_articoli_cost(full_text), ensure_ascii=False),
                "norme_censurate": "[]",
                "testo": full_text[:50000],
                "comunicato_url": "",
                "scraped_at": datetime.now(UTC).isoformat(),
            }
        )
    return records


def parse_massime_zip(zpath: Path) -> list[dict]:
    rows: list[dict] = []
    with zipfile.ZipFile(zpath) as zf:
        xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
        if not xml_name:
            return rows
        root = ET.fromstring(zf.read(xml_name))

    for item in list(root):
        testata = item.find("pronuncia_testata")
        blocco_massime = item.find("massime")
        if testata is None or blocco_massime is None:
            continue

        anno = text_of(testata.find("anno_pronuncia"))
        numero = text_of(testata.find("numero_pronuncia"))
        tipologia = text_of(testata.find("tipologia_pronuncia"))
        data_deposito = text_of(testata.find("data_deposito"))

        if not anno or not numero:
            continue
        anno_i = int(re.sub(r"\D+", "", anno))
        numero_i = int(re.sub(r"\D+", "", numero))
        ecli = f"ECLI:IT:COST:{anno_i}:{numero_i}"

        for m in blocco_massime.findall("massima"):
            norme = [text_of(n) for n in m.findall("norme/norma") if text_of(n)]
            parametri = [text_of(p) for p in m.findall("parametri/parametro") if text_of(p)]
            rows.append(
                {
                    "ecli": ecli,
                    "anno": anno_i,
                    "numero_pronuncia": numero_i,
                    "tipo_pronuncia": normalize_tipo(tipologia),
                    "data_deposito": data_deposito,
                    "numero_massima": text_of(m.find("numero")),
                    "titolo_massima": text_of(m.find("titolo"))[:2000],
                    "testo_massima": text_of(m.find("testo"))[:10000],
                    "norme": json.dumps(norme, ensure_ascii=False),
                    "parametri": json.dumps(parametri, ensure_ascii=False),
                    "source_file": zpath.name,
                    "imported_at": datetime.now(UTC).isoformat(),
                }
            )
    return rows


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sentenze (
            ecli TEXT PRIMARY KEY,
            numero INTEGER NOT NULL,
            anno INTEGER NOT NULL,
            tipo TEXT,
            data_deposito TEXT,
            oggetto TEXT,
            esito TEXT,
            articoli_cost TEXT DEFAULT '[]',
            norme_censurate TEXT DEFAULT '[]',
            testo TEXT,
            comunicato_url TEXT,
            scraped_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sentenze_anno ON sentenze(anno)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sentenze_tipo ON sentenze(tipo)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sentenze_massime (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ecli TEXT NOT NULL,
            anno INTEGER NOT NULL,
            numero_pronuncia INTEGER NOT NULL,
            tipo_pronuncia TEXT,
            data_deposito TEXT,
            numero_massima TEXT,
            titolo_massima TEXT,
            testo_massima TEXT,
            norme TEXT DEFAULT '[]',
            parametri TEXT DEFAULT '[]',
            source_file TEXT,
            imported_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_massime_ecli ON sentenze_massime(ecli)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_massime_anno ON sentenze_massime(anno)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_massime_num_pron ON sentenze_massime(numero_pronuncia)")



def main() -> int:
    parser = argparse.ArgumentParser(description="Import CC backup (pronunce + massime) into laws.db")
    parser.add_argument("--source-root", default="tmp_cc_full", help="Root with extracted CC_OpenPronunce_* and CC_OpenMassime_* dirs")
    parser.add_argument("--db", default="data/laws.db", help="Target sqlite db path")
    parser.add_argument("--analyze", action="store_true", help="Analyze only, no writes")
    parser.add_argument("--write", action="store_true", help="Write into database")
    args = parser.parse_args()

    if not args.analyze and not args.write:
        parser.error("Select one mode: --analyze or --write")

    root = Path(args.source_root)
    if not root.exists():
        raise FileNotFoundError(f"Source root not found: {root}")

    pron_zips = sorted(root.glob("CC_OpenPronunce_*/*.zip"))
    mass_zips = sorted(root.glob("CC_OpenMassime_*/*.zip"))

    print(f"pronunce_year_files={len(pron_zips)}")
    print(f"massime_year_files={len(mass_zips)}")
    if not pron_zips:
        print("No pronunce zip files found.")
        return 1

    pron_total = 0
    mass_total = 0
    for z in pron_zips:
        pron_total += len(parse_pronunce_zip(z))
    for z in mass_zips:
        mass_total += len(parse_massime_zip(z))

    print(f"pronunce_records_parsed={pron_total}")
    print(f"massime_records_parsed={mass_total}")

    if args.analyze:
        return 0

    conn = sqlite3.connect(args.db)
    try:
        ensure_tables(conn)

        upsert_sql = """
            INSERT OR REPLACE INTO sentenze
            (ecli, numero, anno, tipo, data_deposito, oggetto, esito, articoli_cost, norme_censurate, testo, comunicato_url, scraped_at)
            VALUES (:ecli, :numero, :anno, :tipo, :data_deposito, :oggetto, :esito, :articoli_cost, :norme_censurate, :testo, :comunicato_url, :scraped_at)
        """
        for z in pron_zips:
            conn.executemany(upsert_sql, parse_pronunce_zip(z))

        conn.execute("DELETE FROM sentenze_massime")
        ins_massime = """
            INSERT INTO sentenze_massime
            (ecli, anno, numero_pronuncia, tipo_pronuncia, data_deposito, numero_massima, titolo_massima, testo_massima, norme, parametri, source_file, imported_at)
            VALUES (:ecli, :anno, :numero_pronuncia, :tipo_pronuncia, :data_deposito, :numero_massima, :titolo_massima, :testo_massima, :norme, :parametri, :source_file, :imported_at)
        """
        for z in mass_zips:
            rows = parse_massime_zip(z)
            if rows:
                conn.executemany(ins_massime, rows)

        conn.commit()

        c1 = conn.execute("SELECT COUNT(*) FROM sentenze").fetchone()[0]
        c2 = conn.execute("SELECT COUNT(*) FROM sentenze_massime").fetchone()[0]
        print(f"sentenze_rows_after={c1}")
        print(f"sentenze_massime_rows_after={c2}")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
