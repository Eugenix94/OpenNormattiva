#!/usr/bin/env python3
"""
Downloader for Corte Costituzionale decisions (sentenze & ordinanze).

Supports two sources from https://www.cortecostituzionale.it:
1) ECLI brute-force URLs (legacy): actionSchedaPronuncia.do?param_ecli=ECLI:IT:COST:{year}:{num}
2) Public pronunce index pages (preferred): /elenco-pronunce and /scheda-pronuncia/{year}/{num}

Usage:
    # Full historical download (1956–present), ~14 000 decisions
    python download_sentenze.py --full

    # Only current year
    python download_sentenze.py --year 2026

    # Range
    python download_sentenze.py --from-year 2020 --to-year 2026

    # Resume after interruption (skips already stored ECLIs)
    python download_sentenze.py --full --resume

    # Preferred endpoint-based import from public index pages
    python download_sentenze.py --from-index --resume

Rate limiting: 1 request/sec by default (--delay flag to change).
"""

import argparse
import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BASE_URL = "https://www.cortecostituzionale.it/actionSchedaPronuncia.do"
INDEX_URL = "https://www.cortecostituzionale.it/elenco-pronunce"
SCHEDA_URL = "https://www.cortecostituzionale.it/scheda-pronuncia/{year}/{num}"
DB_PATH = Path("data/laws.db")
START_YEAR = 1956
MAX_NUM = 400   # decisions per year are rarely above 350

ESITO_PATTERNS = [
    (r"dichiara l.illegittimit[àa] costituzionale", "illegittimità"),
    (r"dichiara inammissibil", "inammissibile"),
    (r"dichiara non fondat", "non fondata"),
    (r"dichiara fondat", "fondata"),
    (r"dichiara la cessazione della materia", "cessata materia"),
    (r"dichiara estint", "estinta"),
    (r"ordina la restituzione degli atti", "restituzione atti"),
]

BLOCK_MARKERS = [
    "radware captcha page",
    "please solve this captcha",
    "request unblock to the website",
    "made us think that you are a bot",
]


class SourceBlockedError(RuntimeError):
    """Raised when the official source returns a CAPTCHA or anti-bot block page."""


def _ensure_real_sentenze_schema(conn: sqlite3.Connection):
    """Reset incompatible placeholder jurisprudence tables to the real CC schema."""
    table_exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sentenze'"
    ).fetchone()[0]
    if table_exists:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(sentenze)").fetchall()
        }
        if "ecli" not in columns:
            logger.warning(
                "Existing sentenze table uses an incompatible placeholder schema; resetting to real Corte Costituzionale schema."
            )
            conn.execute("DROP TABLE IF EXISTS sentenza_topics")
            conn.execute("DROP TABLE IF EXISTS sentenza_citations")
            conn.execute("DROP TABLE IF EXISTS law_jurisprudence_links")
            conn.execute("DROP TABLE IF EXISTS sentenze")
            conn.commit()

    conn.execute("""
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
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sentenze_anno ON sentenze(anno)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sentenze_tipo ON sentenze(tipo)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sentenze_esito ON sentenze(esito)")
    conn.commit()


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _ensure_real_sentenze_schema(conn)
    return conn


def _existing_eclis(conn) -> set:
    rows = conn.execute("SELECT ecli FROM sentenze").fetchall()
    return {r[0] for r in rows}


def _fetch_page(year: int, num: int, session: requests.Session, delay: float) -> str | None:
    ecli = f"ECLI:IT:COST:{year}:{num}"
    url = f"{BASE_URL}?param_ecli={ecli}"
    try:
        resp = session.get(url, timeout=20)
        time.sleep(delay)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning("HTTP %s for %s", resp.status_code, ecli)
            return None
        body = resp.text
        lower_body = body.lower()
        if any(marker in lower_body for marker in BLOCK_MARKERS):
            raise SourceBlockedError(
                f"Official source blocked automated access while fetching {ecli}."
            )
        return body
    except SourceBlockedError:
        raise
    except requests.RequestException as e:
        logger.warning("Request error for %s: %s", ecli, e)
        time.sleep(delay * 3)
        return None


def _fetch_url(url: str, session: requests.Session, delay: float) -> str | None:
    """Fetch a generic CC URL and detect anti-bot pages."""
    try:
        resp = session.get(url, timeout=20)
        time.sleep(delay)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.warning("HTTP %s for %s", resp.status_code, url)
            return None
        body = resp.text
        lower_body = body.lower()
        if any(marker in lower_body for marker in BLOCK_MARKERS):
            raise SourceBlockedError(
                f"Official source blocked automated access while fetching {url}."
            )
        return body
    except SourceBlockedError:
        raise
    except requests.RequestException as e:
        logger.warning("Request error for %s: %s", url, e)
        time.sleep(delay * 3)
        return None


def _extract_scheda_links(index_html: str) -> list[tuple[int, int]]:
    """Extract (year, number) tuples from elenco-pronunce pages."""
    found = re.findall(r"/scheda-pronuncia/(\d{4})/(\d+)", index_html)
    unique: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for y, n in found:
        key = (int(y), int(n))
        if key in seen:
            continue
        seen.add(key)
        unique.append(key)
    return unique


def _detect_esito(text: str) -> str:
    lower = text.lower()
    for pattern, label in ESITO_PATTERNS:
        if re.search(pattern, lower):
            return label
    return "altro"


def _detect_tipo(text: str) -> str:
    m = re.search(r"\b(SENTENZA|ORDINANZA)\b", text[:500])
    return m.group(1).capitalize() if m else "Sentenza"


def _extract_date(text: str) -> str:
    # "Depositata in Cancelleria il 17 aprile 2026"
    m = re.search(r"[Dd]epositat[ao] in Cancelleria il (\d{1,2} \w+ \d{4})", text)
    if m:
        return m.group(1)
    # ISO fallback
    m2 = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    return m2.group(1) if m2 else ""


def _extract_oggetto(soup: BeautifulSoup) -> str:
    """Extract a short subject description from page metadata."""
    # Look for the summary paragraph near the top
    for tag in soup.find_all(["h3", "h4", "strong", "b"])[:20]:
        txt = tag.get_text(strip=True)
        if len(txt) > 20 and not any(
            kw in txt.lower()
            for kw in ["estremi", "norme", "parametri", "seguici", "contatti"]
        ):
            return txt[:400]
    return ""


def _extract_articoli_cost(text: str) -> list[str]:
    """Extract constitutional article numbers referenced (art. X Cost.)."""
    return list(set(re.findall(r"art(?:t|icol[oi])[\.\s]+(\d+(?:[,\-]\s*\d+)*)[,\s]+(?:della\s+)?Cost\.", text, re.IGNORECASE)))


def _extract_comunicato(soup: BeautifulSoup) -> str:
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "comunicato" in href.lower() or "Comunicato" in a.get_text():
            if href.startswith("http"):
                return href
            return "https://www.cortecostituzionale.it" + href
    return ""


def _parse_decision(html: str, year: int, num: int) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    lower_text = text.lower()
    if any(marker in lower_text for marker in BLOCK_MARKERS):
        raise SourceBlockedError("Official source returned a CAPTCHA page.")

    ecli_str = f"ECLI:IT:COST:{year}:{num}"
    if ecli_str not in text:
        return None
    if not re.search(rf"\b(?:SENTENZA|ORDINANZA)\s+N\.\s*{num}\b", text[:4000], re.IGNORECASE):
        return None

    tipo = _detect_tipo(text)
    data = _extract_date(text)
    esito = _detect_esito(text)
    oggetto = _extract_oggetto(soup)
    articoli = _extract_articoli_cost(text)
    comunicato = _extract_comunicato(soup)

    # Full text: trim navigation boilerplate before "SENTENZA" / "ORDINANZA"
    match = re.search(r"\b(?:SENTENZA|ORDINANZA)\s+N\.\s*\d+", text)
    testo = text[match.start():] if match else text

    return {
        "ecli": ecli_str,
        "numero": num,
        "anno": year,
        "tipo": tipo,
        "data_deposito": data,
        "oggetto": oggetto,
        "esito": esito,
        "articoli_cost": json.dumps(articoli),
        "norme_censurate": "[]",
        "testo": testo[:50000],   # cap at 50 KB per decision
        "comunicato_url": comunicato,
        "scraped_at": datetime.utcnow().isoformat(),
    }


def _insert(conn: sqlite3.Connection, decision: dict):
    conn.execute("""
        INSERT OR REPLACE INTO sentenze
        (ecli, numero, anno, tipo, data_deposito, oggetto, esito,
         articoli_cost, norme_censurate, testo, comunicato_url, scraped_at)
        VALUES (:ecli, :numero, :anno, :tipo, :data_deposito, :oggetto, :esito,
                :articoli_cost, :norme_censurate, :testo, :comunicato_url, :scraped_at)
    """, decision)
    conn.commit()


def download_range(year_from: int, year_to: int, delay: float, resume: bool):
    conn = _get_db()
    existing = _existing_eclis(conn) if resume else set()
    session = requests.Session()
    session.headers["User-Agent"] = (
        "OpenNormattiva-Research/1.0 (academic legal research; "
        "contact: github.com/Eugenix94/OpenNormattiva)"
    )

    total_new = 0
    try:
        for year in range(year_from, year_to + 1):
            consecutive_missing = 0
            logger.info("Year %d …", year)
            for num in range(1, MAX_NUM + 1):
                ecli = f"ECLI:IT:COST:{year}:{num}"
                if ecli in existing:
                    consecutive_missing = 0
                    continue

                html = _fetch_page(year, num, session, delay)
                if html is None:
                    consecutive_missing += 1
                    if consecutive_missing >= 15:
                        break
                    continue

                decision = _parse_decision(html, year, num)
                if decision is None:
                    consecutive_missing += 1
                    if consecutive_missing >= 15:
                        break
                    continue

                consecutive_missing = 0
                _insert(conn, decision)
                total_new += 1
                logger.info("  Stored %s (%s, %s)", ecli, decision["tipo"], decision["esito"])
    except SourceBlockedError as e:
        logger.error("Source blocked the scraper: %s", e)
        logger.error("No further requests will be made in this run.")
    finally:
        conn.close()

    logger.info("Done. %d new decisions stored.", total_new)


def download_from_index(delay: float, resume: bool, max_pages: int = 5000):
    """
    Crawl public index pages and fetch each scheda-pronuncia detail page.

    This is usually more robust than brute-force ECLI probing because it follows
    only published decisions.
    """
    conn = _get_db()
    existing = _existing_eclis(conn) if resume else set()
    session = requests.Session()
    session.headers["User-Agent"] = (
        "OpenNormattiva-Research/1.0 (academic legal research; "
        "contact: github.com/Eugenix94/OpenNormattiva)"
    )

    total_new = 0
    total_seen = 0
    consecutive_empty = 0

    try:
        for page_idx in range(max_pages):
            page_url = INDEX_URL if page_idx == 0 else f"{INDEX_URL}/{page_idx}"
            logger.info("Index page %d: %s", page_idx + 1, page_url)
            index_html = _fetch_url(page_url, session, delay)
            if index_html is None:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                continue

            pairs = _extract_scheda_links(index_html)
            if not pairs:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    break
                continue

            consecutive_empty = 0
            for year, num in pairs:
                total_seen += 1
                ecli = f"ECLI:IT:COST:{year}:{num}"
                if ecli in existing:
                    continue

                detail_url = SCHEDA_URL.format(year=year, num=num)
                html = _fetch_url(detail_url, session, delay)
                if html is None:
                    continue

                decision = _parse_decision(html, year, num)
                if decision is None:
                    continue

                _insert(conn, decision)
                total_new += 1
                logger.info("  Stored %s (%s, %s)", ecli, decision["tipo"], decision["esito"])
    except SourceBlockedError as e:
        logger.error("Source blocked the scraper: %s", e)
        logger.error("No further requests will be made in this run.")
    finally:
        conn.close()

    logger.info("Done. %d new decisions stored (seen %d links).", total_new, total_seen)


def main():
    parser = argparse.ArgumentParser(description="Download Corte Costituzionale decisions")
    parser.add_argument("--full", action="store_true", help=f"Download all from {START_YEAR} to today")
    parser.add_argument("--year", type=int, help="Single year to download")
    parser.add_argument("--from-year", type=int, default=START_YEAR)
    parser.add_argument("--to-year", type=int, default=datetime.now().year)
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests")
    parser.add_argument("--resume", action="store_true", help="Skip already stored ECLIs")
    parser.add_argument(
        "--from-index",
        action="store_true",
        help="Import via public index pages (/elenco-pronunce + /scheda-pronuncia).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5000,
        help="Max index pages to scan when using --from-index.",
    )
    args = parser.parse_args()

    if args.from_index:
        logger.info(
            "Downloading CC decisions from index pages (delay=%.1fs, resume=%s, max_pages=%d)",
            args.delay,
            args.resume,
            args.max_pages,
        )
        download_from_index(args.delay, args.resume, max_pages=args.max_pages)
        return

    if args.year:
        year_from = year_to = args.year
    elif args.full:
        year_from = START_YEAR
        year_to = datetime.now().year
    else:
        year_from = args.from_year
        year_to = args.to_year

    logger.info("Downloading CC decisions %d–%d (delay=%.1fs, resume=%s)",
                year_from, year_to, args.delay, args.resume)
    download_range(year_from, year_to, args.delay, args.resume)


if __name__ == "__main__":
    main()
