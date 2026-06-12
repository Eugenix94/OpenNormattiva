#!/usr/bin/env python3
"""Institutional sources, live parsers, and snapshot helpers.

This module centralizes the official institutional sources used by the Space and
provides lightweight extraction helpers for:
- parliamentary open data (Camera / Senato via SPARQL)
- Government members, history, and RSS
- Quirinale presidents portal and archive links
- Constitutional Court judges, presidents, and latest deposits
- endpoint registry / health checks
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests

DEFAULT_TIMEOUT = 30
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
}
DEFAULT_SNAPSHOT_PATH = Path("data/institutional/institutional_snapshot.json")
GOVERNO_HISTORY_URL = "https://www.governo.it/it/i-governi-dal-1943-ad-oggi/i-governi-nelle-legislature/192"

ITALIAN_MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}

CAMERA_SPARQL_ENDPOINT = "https://dati.camera.it/sparql"
SENATO_SPARQL_ENDPOINT = "http://dati.senato.it/sparql"

QUIRINALE_PRESIDENTS_FALLBACK: list[dict[str, str]] = [
    {"id": "1", "name": "Enrico De Nicola", "url": "https://presidenti.quirinale.it/Presidente/1"},
    {"id": "2", "name": "Luigi Einaudi", "url": "https://presidenti.quirinale.it/Presidente/2"},
    {"id": "3", "name": "Giovanni Gronchi", "url": "https://presidenti.quirinale.it/Presidente/3"},
    {"id": "4", "name": "Antonio Segni", "url": "https://presidenti.quirinale.it/Presidente/4"},
    {"id": "5", "name": "Giuseppe Saragat", "url": "https://presidenti.quirinale.it/Presidente/5"},
    {"id": "6", "name": "Giovanni Leone", "url": "https://presidenti.quirinale.it/Presidente/6"},
    {"id": "7", "name": "Sandro Pertini", "url": "https://presidenti.quirinale.it/Presidente/7"},
    {"id": "8", "name": "Francesco Cossiga", "url": "https://presidenti.quirinale.it/Presidente/8"},
    {"id": "9", "name": "Oscar Luigi Scalfaro", "url": "https://presidenti.quirinale.it/Presidente/9"},
    {"id": "10", "name": "Carlo Azeglio Ciampi", "url": "https://presidenti.quirinale.it/Presidente/10"},
    {"id": "11", "name": "Giorgio Napolitano", "url": "https://presidenti.quirinale.it/Presidente/11"},
    {"id": "12", "name": "Sergio Mattarella", "url": "https://presidenti.quirinale.it/Presidente/12"},
]

CORTE_JUDGES_FALLBACK: list[dict[str, str]] = [
    {
        "name": "Giovanni Amoroso",
        "biography_url": "https://www.cortecostituzionale.it/giudici/giovanni-amoroso",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/132",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=132",
    },
    {
        "name": "Francesco Viganò",
        "biography_url": "https://www.cortecostituzionale.it/giudici/francesco-vigano",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/133",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=133",
    },
    {
        "name": "Luca Antonini",
        "biography_url": "https://www.cortecostituzionale.it/giudici/luca-antonini",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/134",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=134",
    },
    {
        "name": "Stefano Petitti",
        "biography_url": "https://www.cortecostituzionale.it/giudici/stefano-petitti",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/137",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=137",
    },
    {
        "name": "Angelo Buscema",
        "biography_url": "https://www.cortecostituzionale.it/giudici/angelo-buscema",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/138",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=138",
    },
    {
        "name": "Emanuela Navarretta",
        "biography_url": "https://www.cortecostituzionale.it/giudici/emanuela-navarretta",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/139",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=139",
    },
    {
        "name": "Maria Rosaria San Giorgio",
        "biography_url": "https://www.cortecostituzionale.it/giudici/maria-rosaria-san-giorgio",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/140",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=140",
    },
    {
        "name": "Filippo Patroni Griffi",
        "biography_url": "https://www.cortecostituzionale.it/giudici/filippo-patroni-griffi",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/141",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=141",
    },
    {
        "name": "Marco D'Alberti",
        "biography_url": "https://www.cortecostituzionale.it/giudici/marco-dalberti",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/142",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=142",
    },
    {
        "name": "Giovanni Pitruzzella",
        "biography_url": "https://www.cortecostituzionale.it/giudici/giovanni-pitruzzella",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/143",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=143",
    },
    {
        "name": "Antonella Sciarrone Alibrandi",
        "biography_url": "https://www.cortecostituzionale.it/giudici/antonella-sciarrone-alibrandi",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/144",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=144",
    },
    {
        "name": "Massimo Luciani",
        "biography_url": "https://www.cortecostituzionale.it/giudici/massimo-luciani",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/150",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=150",
    },
    {
        "name": "Maria Alessandra Sandulli",
        "biography_url": "https://www.cortecostituzionale.it/giudici/maria-alessandra-sandulli",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/151",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=151",
    },
    {
        "name": "Roberto Nicola Cassinelli",
        "biography_url": "https://www.cortecostituzionale.it/giudici/roberto-nicola-cassinelli",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/152",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=152",
    },
    {
        "name": "Francesco Saverio Marini",
        "biography_url": "https://www.cortecostituzionale.it/giudici/francesco-saverio-marini",
        "drafted_decisions_url": "https://www.cortecostituzionale.it/pronunce-redatte/153",
        "interventions_url": "https://www.cortecostituzionale.it/actionInterventiGiudici.do?codice_giudice=153",
    },
]

CORTE_PRESIDENTS_FALLBACK: list[dict[str, str]] = [
    {"name": "Augusto Barbera", "url": "https://www.cortecostituzionale.it/presidente-barbera"},
    {"name": "Silvana Sciarra", "url": "https://www.cortecostituzionale.it/presidente-silvana-sciarra"},
    {"name": "Giuliano Amato", "url": "https://www.cortecostituzionale.it/presidente-amato"},
    {"name": "Giancarlo Coraggio", "url": "https://www.cortecostituzionale.it/presidente-coraggio"},
    {"name": "Mario Rosario Morelli", "url": "https://www.cortecostituzionale.it/presidente-morelli"},
    {"name": "Marta Cartabia", "url": "https://www.cortecostituzionale.it/presidente-cartabia"},
    {"name": "Giorgio Lattanzi", "url": "https://www.cortecostituzionale.it/presidente-lattanzi"},
    {"name": "Paolo Grossi", "url": "https://www.cortecostituzionale.it/presidente-grossi"},
    {"name": "Alessandro Criscuolo", "url": ""},
    {"name": "Giuseppe Tesauro", "url": ""},
    {"name": "Gaetano Silvestri", "url": ""},
    {"name": "Franco Gallo", "url": ""},
    {"name": "Alfonso Quaranta", "url": ""},
    {"name": "Ugo De Siervo", "url": ""},
    {"name": "Francesco Amirante", "url": ""},
    {"name": "Giovanni Maria Flick", "url": ""},
    {"name": "Franco Bile", "url": ""},
    {"name": "Annibale Marini", "url": ""},
    {"name": "Piero Alberto Capotosti", "url": ""},
    {"name": "Fernanda Contri", "url": ""},
    {"name": "Valerio Onida", "url": ""},
    {"name": "Gustavo Zagrebelsky", "url": ""},
    {"name": "Riccardo Chieppa", "url": ""},
    {"name": "Cesare Ruperto", "url": ""},
    {"name": "Fernando Santosuosso", "url": ""},
    {"name": "Cesare Mirabelli", "url": ""},
    {"name": "Francesco Guizzi", "url": ""},
    {"name": "Giuliano Vassalli", "url": ""},
    {"name": "Renato Granata", "url": ""},
    {"name": "Mauro Ferri", "url": ""},
    {"name": "Vincenzo Caianiello", "url": ""},
    {"name": "Antonio Baldassarre", "url": ""},
    {"name": "Francesco Paolo Casavola", "url": ""},
    {"name": "Aldo Corasaniti", "url": ""},
    {"name": "Ettore Gallo", "url": ""},
    {"name": "Giovanni Conso", "url": ""},
    {"name": "Francesco Saja", "url": ""},
    {"name": "Antonio La Pergola", "url": ""},
    {"name": "Livio Paladin", "url": ""},
    {"name": "Guglielmo Roehrssen", "url": ""},
    {"name": "Leopoldo Elia", "url": ""},
    {"name": "Giulio Gionfrida", "url": ""},
    {"name": "Leonetto Amadei", "url": ""},
    {"name": "Paolo Rossi", "url": ""},
    {"name": "Luigi Oggioni", "url": ""},
    {"name": "Francesco Paolo Bonifacio", "url": ""},
    {"name": "Giuseppe Verzi", "url": ""},
    {"name": "Giuseppe Chiarelli", "url": ""},
    {"name": "Michele Fragali", "url": ""},
    {"name": "Giuseppe Branca", "url": ""},
    {"name": "Aldo Sandulli", "url": ""},
    {"name": "Gaspare Ambrosini", "url": ""},
    {"name": "Giuseppe Cappi", "url": ""},
    {"name": "Gaetano Azzariti", "url": ""},
    {"name": "Enrico De Nicola", "url": ""},
]


@dataclass(frozen=True)
class InstitutionalEndpoint:
    key: str
    institution: str
    section: str
    label: str
    url: str
    access_type: str
    data_kind: str
    coverage: str
    join_target: str
    notes: str = ""


INSTITUTIONAL_ENDPOINTS: list[InstitutionalEndpoint] = [
    InstitutionalEndpoint(
        key="camera_home",
        institution="Camera dei deputati",
        section="Parlamento",
        label="Dati Camera Home",
        url="https://dati.camera.it/",
        access_type="html",
        data_kind="portal",
        coverage="Open data, ontology, linked data, history access",
        join_target="legislatures, deputies, acts, votes, groups, commissions",
    ),
    InstitutionalEndpoint(
        key="camera_sparql",
        institution="Camera dei deputati",
        section="Parlamento",
        label="Camera SPARQL",
        url=CAMERA_SPARQL_ENDPOINT,
        access_type="sparql",
        data_kind="rdf",
        coverage="Deputies, legislatures, governments, acts, votes",
        join_target="legislature_id, parliamentary activity, officeholders",
    ),
    InstitutionalEndpoint(
        key="camera_history",
        institution="Camera dei deputati",
        section="Parlamento",
        label="Storia Camera",
        url="https://storia.camera.it/",
        access_type="html",
        data_kind="history",
        coverage="Deputies, works, documents, legislatures, presidents",
        join_target="historical persons, parliamentary work, legislatures",
    ),
    InstitutionalEndpoint(
        key="senato_home",
        institution="Senato della Repubblica",
        section="Parlamento",
        label="Dati Senato Home",
        url="https://dati.senato.it/sito/home",
        access_type="html",
        data_kind="portal",
        coverage="Open data portal and documentation",
        join_target="senators, DDL, votes, commissions, groups",
    ),
    InstitutionalEndpoint(
        key="senato_sparql",
        institution="Senato della Repubblica",
        section="Parlamento",
        label="Senato SPARQL",
        url=SENATO_SPARQL_ENDPOINT,
        access_type="sparql",
        data_kind="rdf",
        coverage="Senators, DDL, votes, commissions, groups",
        join_target="legislatures, parliamentary actors, legislative iter",
    ),
    InstitutionalEndpoint(
        key="senato_downloads",
        institution="Senato della Repubblica",
        section="Parlamento",
        label="Scarica i dati Senato",
        url="https://dati.senato.it/sito/scarica_i_dati",
        access_type="html",
        data_kind="downloads",
        coverage="CSV, JSON, XML exports for composition, acts, votes",
        join_target="bulk ingestion and offline snapshots",
    ),
    InstitutionalEndpoint(
        key="senato_rss",
        institution="Senato della Repubblica",
        section="Parlamento",
        label="RSS Senato Open Data",
        url="https://dati.senato.it/sito/feed_rss?testo_generico=9",
        access_type="rss",
        data_kind="feed",
        coverage="Portal updates",
        join_target="source freshness monitoring",
    ),
    InstitutionalEndpoint(
        key="governo_members",
        institution="Presidenza del Consiglio dei Ministri",
        section="Governo",
        label="Ministri e Sottosegretari",
        url="https://www.governo.it/it/ministri-e-sottosegretari",
        access_type="html",
        data_kind="officeholders",
        coverage="Current PM, vice PMs, ministers, undersecretaries",
        join_target="current executive officeholders",
    ),
    InstitutionalEndpoint(
        key="governo_history",
        institution="Presidenza del Consiglio dei Ministri",
        section="Governo",
        label="Governi dal 1943 a oggi",
        url="https://www.governo.it/it/i-governi-dal-1943-ad-oggi/i-governi-nelle-legislature/192",
        access_type="html",
        data_kind="history",
        coverage="Governments by legislature from 1943 to present",
        join_target="government, legislature_id, law chronology",
    ),
    InstitutionalEndpoint(
        key="governo_cdm_archive",
        institution="Presidenza del Consiglio dei Ministri",
        section="Governo",
        label="Archivio Consiglio dei Ministri",
        url="https://www.governo.it/it/archivio-consiglio-dei-ministri",
        access_type="html",
        data_kind="archive",
        coverage="Council of Ministers meetings",
        join_target="executive decisions and law production timeline",
    ),
    InstitutionalEndpoint(
        key="governo_rss",
        institution="Presidenza del Consiglio dei Ministri",
        section="Governo",
        label="RSS Governo",
        url="https://www.governo.it/feed/rss",
        access_type="rss",
        data_kind="feed",
        coverage="News, Council of Ministers communications, government actions",
        join_target="freshness, executive activity, legal-news layer",
    ),
    InstitutionalEndpoint(
        key="quirinale_presidents",
        institution="Presidenza della Repubblica",
        section="Quirinale",
        label="I Presidenti della Repubblica",
        url="https://presidenti.quirinale.it/",
        access_type="html",
        data_kind="officeholders",
        coverage="Presidents from 1948 onward, with speeches, visits, photos, videos",
        join_target="presidential officeholders, speeches, institutional context",
    ),
    InstitutionalEndpoint(
        key="quirinale_archive",
        institution="Presidenza della Repubblica",
        section="Quirinale",
        label="Archivio storico del Quirinale",
        url="https://archivio.quirinale.it/aspr/",
        access_type="html",
        data_kind="archive",
        coverage="Historical diary, acts, speeches, archive materials, LOD",
        join_target="historical institutional context and signed acts",
    ),
    InstitutionalEndpoint(
        key="corte_judges",
        institution="Corte costituzionale",
        section="Giustizia costituzionale",
        label="Giudici costituzionali",
        url="https://www.cortecostituzionale.it/giudici",
        access_type="html",
        data_kind="officeholders",
        coverage="Current judges, biographies, interventions, drafted decisions",
        join_target="judges, composition, jurisprudence context",
    ),
    InstitutionalEndpoint(
        key="corte_latest",
        institution="Corte costituzionale",
        section="Giustizia costituzionale",
        label="Ultimo deposito",
        url="https://www.cortecostituzionale.it/ultimo-deposito",
        access_type="html",
        data_kind="decisions",
        coverage="Latest deposited decisions",
        join_target="constitutional decisions and impacted norms",
    ),
    InstitutionalEndpoint(
        key="corte_search",
        institution="Corte costituzionale",
        section="Giustizia costituzionale",
        label="Ricerca pronunce",
        url="https://www.cortecostituzionale.it/ricerca-pronunce",
        access_type="html",
        data_kind="search",
        coverage="Search portal for constitutional decisions",
        join_target="case discovery and norm impact analysis",
    ),
    InstitutionalEndpoint(
        key="corte_presidents",
        institution="Corte costituzionale",
        section="Giustizia costituzionale",
        label="Presidenti della Corte",
        url="https://www.cortecostituzionale.it/contenuti/composizione/presidenti",
        access_type="html",
        data_kind="history",
        coverage="Presidents of the Court since 1956",
        join_target="court leadership timeline",
    ),
    InstitutionalEndpoint(
        key="parlamento_laws",
        institution="Parlamento italiano",
        section="Parlamento",
        label="Parlamento - Leggi",
        url="https://www.parlamento.it/519",
        access_type="html",
        data_kind="joint-portal",
        coverage="Joint parliamentary law and bicameral information",
        join_target="approved laws and bicameral activity",
    ),
    InstitutionalEndpoint(
        key="dati_gov",
        institution="AgID / dati.gov.it",
        section="Catalogo nazionale",
        label="dati.gov.it",
        url="https://www.dati.gov.it/",
        access_type="html",
        data_kind="catalog",
        coverage="National dataset catalog with service metadata",
        join_target="dataset discovery, APIs, public-sector data enrichment",
        notes="Dataset pages expose API/service access metadata when available.",
    ),
]


class _LinksAndHeadingsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self.headings: list[dict[str, str]] = []
        self._in_anchor = False
        self._anchor_href = ""
        self._anchor_parts: list[str] = []
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "a":
            self._in_anchor = True
            self._anchor_href = attr_map.get("href") or ""
            self._anchor_parts = []
        elif tag in {"h1", "h2", "h3", "h4"}:
            self._heading_tag = tag
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._anchor_parts.append(data)
        if self._heading_tag:
            self._heading_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_anchor:
            text = _clean_text(" ".join(self._anchor_parts))
            if self._anchor_href:
                self.links.append({"text": text, "href": self._anchor_href})
            self._in_anchor = False
            self._anchor_href = ""
            self._anchor_parts = []
        elif self._heading_tag == tag:
            text = _clean_text(" ".join(self._heading_parts))
            if text:
                self.headings.append({"tag": tag, "text": text})
            self._heading_tag = None
            self._heading_parts = []


def _clean_text(value: str) -> str:
    value = unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()


def _strip_tags(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " | ", value or "", flags=re.IGNORECASE)
    value = re.sub(r"</(?:p|div|blockquote|li|dd|dt|h\d|ul|ol|dl)>", " | ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.replace("||", "|")
    return _clean_text(value)


def _parse_italian_date(value: str) -> str:
    text = _clean_text(value).lower().replace("º", "").replace("°", "")
    numeric = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if numeric:
        day, month, year = numeric.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    literal = re.search(r"(\d{1,2})\s+([a-zàéìòù]+)\s+(\d{4})", text)
    if not literal:
        return ""

    day, month_name, year = literal.groups()
    month = ITALIAN_MONTHS.get(month_name)
    if not month:
        return ""
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def _extract_dl_pairs(block_html: str) -> list[tuple[str, str]]:
    return re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", block_html or "", re.IGNORECASE | re.DOTALL)


def _extract_role_blob(detail_text: str, labels: tuple[str, ...]) -> str:
    boundary = r"(?=(?:\|\s*(?:Ministro|Vice Ministr(?:o|i)|Sottosegretar(?:io|i))\s*:)|$)"
    for label in labels:
        match = re.search(
            rf"(?:^|\|\s*){label}\s*:\s*(.*?){boundary}",
            detail_text,
            re.IGNORECASE,
        )
        if match:
            return _clean_text(match.group(1))
    return ""


def _extract_composition_notes(detail_text: str) -> list[str]:
    notes: list[str] = []
    for note in re.findall(r"\[([^\]]+)\]", detail_text):
        cleaned = _clean_text(note)
        if cleaned:
            notes.append(cleaned)
    for note in re.findall(r"\(([^)]*(?:dpr|dal|fino|dimissioni|ad interim)[^)]*)\)", detail_text, re.IGNORECASE):
        cleaned = _clean_text(note)
        if cleaned:
            notes.append(cleaned)
    for note in re.findall(r"-\s*((?:dal|fino|dimissioni|ad interim)[^|]+)", detail_text, re.IGNORECASE):
        cleaned = _clean_text(note)
        if cleaned:
            notes.append(cleaned)

    unique: list[str] = []
    seen: set[str] = set()
    for note in notes:
        note = note.strip("[] ")
        if not note or note in seen:
            continue
        seen.add(note)
        unique.append(note)
    return unique


def _clean_people_blob(value: str) -> str:
    value = re.sub(r"\[[^\]]*\]", "", value)
    value = re.sub(r"\(([^)]*(?:dpr|dal|fino|dimissioni|ad interim)[^)]*)\)", "", value, flags=re.IGNORECASE)
    value = re.sub(r"-\s*(?:dal|fino|dimissioni|ad interim)[^|,;]*", "", value, flags=re.IGNORECASE)
    return _clean_text(value)


def _split_people_blob(value: str) -> list[str]:
    cleaned = _clean_people_blob(value.replace("|", ","))
    if not cleaned:
        return []
    return [part.strip(" -*[]") for part in re.split(r"\s*[;,]\s*", cleaned) if part.strip(" -*[]")]


def _extract_governo_period(html: str, fallback_name: str) -> dict[str, str]:
    description = ""
    meta_match = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html, re.IGNORECASE)
    if meta_match:
        description = _clean_text(meta_match.group(1))
    if not description:
        blockquote_match = re.search(r"<blockquote>\s*<p>(.*?)</p>\s*</blockquote>", html, re.IGNORECASE | re.DOTALL)
        if blockquote_match:
            description = _strip_tags(blockquote_match.group(1))

    result = {
        "period_text": description,
        "start_date": "",
        "end_date": "",
        "legislature": "",
    }
    if not description:
        return result

    if fallback_name:
        lowered_description = description.lower()
        lowered_name = fallback_name.lower()
        if lowered_name in lowered_description:
            description = description[lowered_description.index(lowered_name):]

    match = re.match(r"(?P<name>.+?)\s*\((?P<period>[^)]*)\)\s*(?P<leg>.*)", description)
    if not match:
        return result

    period_label = _clean_text(match.group("period"))
    result["period_text"] = period_label
    result["legislature"] = _clean_text(match.group("leg"))

    date_tokens = re.findall(r"\d{1,2}(?:/\d{1,2}/\d{4}|\s+[A-Za-zàéìòù]+\s+\d{4})", period_label)
    if date_tokens:
        result["start_date"] = _parse_italian_date(date_tokens[0])
    if len(date_tokens) > 1:
        result["end_date"] = _parse_italian_date(date_tokens[1])
    return result


def _extract_governo_header_roles(html: str) -> tuple[str, list[str]]:
    match = re.search(r"</blockquote>\s*<dl>(.*?)</dl>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return "", []

    prime_minister = ""
    presidency_undersecretaries: list[str] = []
    for title_html, detail_html in _extract_dl_pairs(match.group(1)):
        title = _clean_text(_strip_tags(title_html)).lower()
        detail_text = _strip_tags(detail_html)
        if title.startswith("presidente del consiglio"):
            prime_minister = detail_text.replace(" | ", ", ")
        elif "sottosegretario" in title:
            presidency_undersecretaries = _split_people_blob(detail_text)
    return prime_minister, presidency_undersecretaries


def _parse_governo_ministry_block(block_html: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for title_html, detail_html in _extract_dl_pairs(block_html):
        portfolio = _clean_text(_strip_tags(title_html)).strip("[]* ")
        detail_text = _strip_tags(detail_html)
        notes = _extract_composition_notes(detail_text)
        rows.append({
            "portfolio": portfolio,
            "minister": _clean_people_blob(_extract_role_blob(detail_text, ("Ministro",))),
            "vice_ministers": _split_people_blob(_extract_role_blob(detail_text, ("Vice Ministr(?:o|i)",))),
            "undersecretaries": _split_people_blob(_extract_role_blob(detail_text, ("Sottosegretar(?:io|i)",))),
            "composition_notes": notes,
            "composition_change_count": len(notes),
            "raw": detail_text,
        })
    return rows


def _extract_governo_sections(html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    without_portfolio: list[dict[str, Any]] = []
    with_portfolio: list[dict[str, Any]] = []
    for heading_html, block_html in re.findall(
        r"<h2[^>]*>\s*(.*?)\s*</h2>\s*<dl>(.*?)</dl>",
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        heading = _clean_text(_strip_tags(heading_html)).lower()
        entries = _parse_governo_ministry_block(block_html)
        if "senza portafoglio" in heading:
            without_portfolio = entries
        elif heading.startswith("ministeri"):
            with_portfolio = entries
    return without_portfolio, with_portfolio


def _fetch_governo_page_details(url: str, fallback_name: str) -> dict[str, Any]:
    html = _fetch_text(url)
    period = _extract_governo_period(html, fallback_name)
    prime_minister, presidency_undersecretaries = _extract_governo_header_roles(html)
    without_portfolio, with_portfolio = _extract_governo_sections(html)

    composition_changes: list[str] = []
    seen_changes: set[str] = set()
    for entry in without_portfolio + with_portfolio:
        for note in entry.get("composition_notes", []):
            if note in seen_changes:
                continue
            seen_changes.add(note)
            composition_changes.append(note)

    return {
        "period_text": period.get("period_text", ""),
        "start_date": period.get("start_date", ""),
        "end_date": period.get("end_date", ""),
        "legislature": period.get("legislature", ""),
        "prime_minister": prime_minister,
        "presidency_undersecretaries": presidency_undersecretaries,
        "ministries_without_portfolio": without_portfolio,
        "ministries_with_portfolio": with_portfolio,
        "ministries_without_portfolio_count": len(without_portfolio),
        "ministries_with_portfolio_count": len(with_portfolio),
        "composition_changes": composition_changes,
        "composition_change_count": len(composition_changes),
        "composition_change_examples": composition_changes[:8],
    }


def _slug_to_title(slug: str) -> str:
    parts = [p for p in slug.replace("_", "-").split("-") if p]
    return " ".join(part.capitalize() for part in parts)


def _dedupe_dicts(rows: list[dict[str, Any]], key_fields: list[str]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _fetch_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    response.encoding = response.encoding or response.apparent_encoding or "utf-8"
    return response.text


def _fetch_xml(url: str, timeout: int = DEFAULT_TIMEOUT) -> ET.Element:
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    return ET.fromstring(response.content)


def _fetch_links_and_headings(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    html = _fetch_text(url, timeout=timeout)
    return _parse_links_and_headings(html, url)


def _parse_links_and_headings(html: str, url: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    parser = _LinksAndHeadingsParser()
    parser.feed(html)
    links = [
        {
            **item,
            "href": urljoin(url, item.get("href", "")),
        }
        for item in parser.links
        if item.get("href")
    ]
    return links, parser.headings


def _looks_like_bot_wall(html: str) -> bool:
    snippet = (html or "")[:4000].lower()
    return (
        "radware captcha page" in snippet
        or "validate.perfdrive.com" in snippet
        or "shieldsquare" in snippet
    )


def _run_sparql_query(endpoint: str, query: str, timeout: int = 60) -> list[dict[str, str]]:
    response = requests.get(
        endpoint,
        params={"query": query, "format": "application/sparql-results+json"},
        headers={
            **DEFAULT_HEADERS,
            "Accept": "application/sparql-results+json",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return [
        {key: value.get("value", "") for key, value in row.items()}
        for row in payload.get("results", {}).get("bindings", [])
    ]


def _limit_clause(limit: int | None) -> str:
    return f"LIMIT {limit}" if limit else ""


def registry_rows() -> list[dict[str, Any]]:
    return [asdict(item) for item in INSTITUTIONAL_ENDPOINTS]


def check_endpoint(url: str, timeout: int = 20) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        response = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )
        content_type = response.headers.get("content-type", "")
        sample = response.raw.read(512, decode_content=True) if response.raw else b""
        response.close()
        return {
            "url": url,
            "final_url": response.url,
            "status_code": response.status_code,
            "ok": response.ok,
            "content_type": content_type,
            "sample_size": len(sample),
            "checked_at": checked_at,
        }
    except Exception as exc:  # pragma: no cover - network failures are expected in some envs
        return {
            "url": url,
            "final_url": url,
            "status_code": None,
            "ok": False,
            "content_type": "",
            "sample_size": 0,
            "checked_at": checked_at,
            "error": str(exc),
        }


def collect_endpoint_statuses() -> list[dict[str, Any]]:
    rows = []
    for endpoint in INSTITUTIONAL_ENDPOINTS:
        status = check_endpoint(endpoint.url)
        rows.append({
            "institution": endpoint.institution,
            "section": endpoint.section,
            "label": endpoint.label,
            "access_type": endpoint.access_type,
            **status,
        })
    return rows


def fetch_camera_deputies(legislature: int = 19, limit: int | None = None) -> list[dict[str, str]]:
    query = f'''
    PREFIX ocd: <http://dati.camera.it/ocd/>
    PREFIX foaf: <http://xmlns.com/foaf/0.1/>
    PREFIX dct: <http://purl.org/dc/terms/>
    SELECT DISTINCT ?persona ?nome ?cognome ?gender ?bio
    WHERE {{
      ?persona a ocd:deputato ;
               foaf:firstName ?nome ;
               foaf:surname ?cognome ;
               ocd:rif_leg <http://dati.camera.it/ocd/legislatura.rdf/repubblica_{legislature}> .
      OPTIONAL {{ ?persona foaf:gender ?gender }}
      OPTIONAL {{ ?persona dct:isReferencedBy ?bio }}
    }}
    ORDER BY ?cognome ?nome
    {_limit_clause(limit)}
    '''
    return _run_sparql_query(CAMERA_SPARQL_ENDPOINT, query)


def fetch_camera_legislatures() -> list[dict[str, str]]:
    query = '''
    PREFIX ocd: <http://dati.camera.it/ocd/>
    PREFIX dc: <http://purl.org/dc/elements/1.1/>
    SELECT DISTINCT ?leg ?title ?start ?end
    WHERE {
      ?leg a ocd:legislatura ;
           dc:title ?title ;
           ocd:startDate ?start .
      OPTIONAL { ?leg ocd:endDate ?end }
    }
    ORDER BY ?start
    '''
    return _run_sparql_query(CAMERA_SPARQL_ENDPOINT, query)


def fetch_camera_governments(limit: int | None = None) -> list[dict[str, str]]:
    query = f'''
    PREFIX ocd: <http://dati.camera.it/ocd/>
    PREFIX dc: <http://purl.org/dc/elements/1.1/>
    SELECT DISTINCT ?gov ?title ?start ?end
    WHERE {{
      ?gov a ocd:governo ;
           dc:title ?title ;
           ocd:startDate ?start .
      OPTIONAL {{ ?gov ocd:endDate ?end }}
    }}
    ORDER BY DESC(?start)
    {_limit_clause(limit)}
    '''
    return _run_sparql_query(CAMERA_SPARQL_ENDPOINT, query)


def fetch_senato_senators(legislature: str = "19", limit: int | None = None) -> list[dict[str, str]]:
    query = f'''
    PREFIX osr: <http://dati.senato.it/osr/>
    PREFIX foaf: <http://xmlns.com/foaf/0.1/>
        SELECT DISTINCT ?persona ?nome ?cognome ?gender ?profession ?start ?end
    WHERE {{
      ?persona a osr:Senatore ;
               foaf:firstName ?nome ;
               foaf:lastName ?cognome ;
               osr:mandato ?mandato .
            ?mandato osr:legislatura ?leg .
            FILTER(str(?leg) = "{legislature}")
      OPTIONAL {{ ?persona foaf:gender ?gender }}
      OPTIONAL {{ ?persona osr:professione ?profession }}
      OPTIONAL {{ ?mandato osr:inizio ?start }}
      OPTIONAL {{ ?mandato osr:fine ?end }}
    }}
    ORDER BY ?cognome ?nome
    {_limit_clause(limit)}
    '''
    return _run_sparql_query(SENATO_SPARQL_ENDPOINT, query)


def fetch_senato_recent_ddl(limit: int = 25) -> list[dict[str, str]]:
    query = f'''
    PREFIX osr: <http://dati.senato.it/osr/>
    SELECT DISTINCT ?ddl ?id ?title ?presented ?law_date ?law_number ?leg
    WHERE {{
      ?ddl a osr:Ddl ;
           osr:idDdl ?id ;
           osr:titolo ?title ;
           osr:legislatura ?leg .
      OPTIONAL {{ ?ddl osr:dataPresentazione ?presented }}
      OPTIONAL {{ ?ddl osr:dataLegge ?law_date }}
      OPTIONAL {{ ?ddl osr:numeroLegge ?law_number }}
    }}
    ORDER BY DESC(?presented)
    LIMIT {int(limit)}
    '''
    return _run_sparql_query(SENATO_SPARQL_ENDPOINT, query)


def fetch_governo_members() -> list[dict[str, str]]:
    links, _ = _fetch_links_and_headings("https://www.governo.it/it/ministri-e-sottosegretari")
    rows = []
    for link in links:
        href = link.get("href", "")
        text = link.get("text", "")
        if "/it/governo/" not in href or not text:
            continue
        role_bucket = ""
        if "/presidente-del-consiglio/" in href:
            role_bucket = "Presidente del Consiglio"
        elif "/vice-presidente/" in href:
            role_bucket = "Vice Presidente"
        elif "/ministro/" in href:
            role_bucket = "Ministro"
        elif "/sottosegretari-pcm/" in href:
            role_bucket = "Sottosegretario PCM"
        elif "/sottosegretario/" in href:
            role_bucket = "Sottosegretario"
        if not role_bucket:
            continue
        pieces = href.rstrip("/").split("/")
        government_slug = pieces[pieces.index("governo") + 1] if "governo" in pieces else ""
        rows.append({
            "name": text,
            "role_bucket": role_bucket,
            "government_slug": government_slug,
            "url": href,
        })
    return _dedupe_dicts(rows, ["name", "role_bucket", "url"])


def fetch_governo_history() -> list[dict[str, Any]]:
    links, _ = _fetch_links_and_headings(GOVERNO_HISTORY_URL)
    rows = []
    for link in links:
        href = link.get("href", "")
        text = link.get("text", "")
        if "/i-governi-dal-1943-ad-oggi/" not in href:
            continue
        if not text.startswith("Governo"):
            continue
        row: dict[str, Any] = {
            "name": text,
            "url": href,
        }
        try:
            row.update(_fetch_governo_page_details(href, text))
        except Exception as exc:
            row["detail_error"] = str(exc)
        rows.append(row)
    return _dedupe_dicts(rows, ["name", "url"])


def fetch_governo_rss(limit: int = 10) -> list[dict[str, str]]:
    root = _fetch_xml("https://www.governo.it/feed/rss")
    items = []
    for item in root.findall(".//item")[:limit]:
        items.append({
            "title": _clean_text(item.findtext("title") or ""),
            "link": _clean_text(item.findtext("link") or ""),
            "published": _clean_text(item.findtext("pubDate") or ""),
            "description": _clean_text(item.findtext("description") or "")[:300],
        })
    return items


def fetch_quirinale_presidents() -> list[dict[str, str]]:
    try:
        links, _ = _fetch_links_and_headings("https://presidenti.quirinale.it/")
    except requests.HTTPError as exc:
        response = getattr(exc, "response", None)
        if response is not None and response.status_code == 403:
            return [dict(row) for row in QUIRINALE_PRESIDENTS_FALLBACK]
        raise
    except Exception as exc:
        if "403" in str(exc):
            return [dict(row) for row in QUIRINALE_PRESIDENTS_FALLBACK]
        raise
    rows = []
    for link in links:
        href = link.get("href", "")
        text = link.get("text", "")
        if not re.search(r"/Presidente/\d+$", href):
            continue
        if not text or text.lower() in {"home"}:
            continue
        match = re.search(r"/Presidente/(\d+)$", href)
        rows.append({
            "name": text,
            "id": match.group(1) if match else "",
            "url": href,
        })
    return _dedupe_dicts(rows, ["id", "name"])


def fetch_quirinale_archive_links() -> list[dict[str, str]]:
    return [
        {"label": "Archivio storico", "url": "https://archivio.quirinale.it/aspr/"},
        {"label": "Diario storico", "url": "https://archivio.quirinale.it/aspr/diari"},
        {"label": "Atti firmati", "url": "https://archivio.quirinale.it/aspr/atti"},
        {"label": "Discorsi e comunicati", "url": "https://archivio.quirinale.it/aspr/discorsi"},
        {"label": "Linked Open Data", "url": "https://archivio.quirinale.it/aspr/redazione/linked-open-data"},
        {"label": "Accadde oggi", "url": "https://archivio.quirinale.it/aspr/accadde-oggi/"},
    ]


def fetch_corte_latest_deposits(limit: int = 12) -> list[dict[str, str]]:
    html = _fetch_text("https://www.cortecostituzionale.it/ultimo-deposito")
    if _looks_like_bot_wall(html):
        return []
    links, _ = _parse_links_and_headings(html, "https://www.cortecostituzionale.it/ultimo-deposito")
    rows = []
    for link in links:
        href = link.get("href", "")
        text = link.get("text", "")
        if "/scheda-pronuncia/" not in href:
            continue
        match = re.search(r"/scheda-pronuncia/(\d{4})/(\d+)", href)
        rows.append({
            "title": text or (f"Sentenza {match.group(2)}/{match.group(1)}" if match else "Decisione"),
            "year": match.group(1) if match else "",
            "number": match.group(2) if match else "",
            "url": href,
        })
    return _dedupe_dicts(rows, ["url"])[:limit]


def fetch_corte_judges() -> list[dict[str, str]]:
    html = _fetch_text("https://www.cortecostituzionale.it/giudici")
    if _looks_like_bot_wall(html):
        return [dict(row) for row in CORTE_JUDGES_FALLBACK]
    links, headings = _parse_links_and_headings(html, "https://www.cortecostituzionale.it/giudici")
    names = [h["text"] for h in headings if h["tag"] == "h2"]
    bio_links = [l["href"] for l in links if re.search(r"/giudici/[^/]+$", l.get("href", ""))]
    pronunce_links = [l["href"] for l in links if "/pronunce-redatte/" in l.get("href", "")]
    intervention_links = [l["href"] for l in links if "actionInterventiGiudici.do" in l.get("href", "")]

    rows = []
    linked_count = min(len(names), len(bio_links), len(pronunce_links), len(intervention_links))
    for idx, name in enumerate(names[:linked_count] if linked_count else names):
        rows.append({
            "name": name,
            "biography_url": bio_links[idx] if idx < len(bio_links) else "",
            "drafted_decisions_url": pronunce_links[idx] if idx < len(pronunce_links) else "",
            "interventions_url": intervention_links[idx] if idx < len(intervention_links) else "",
        })
    return _dedupe_dicts(rows, ["name"])


def fetch_corte_presidents_history() -> list[dict[str, str]]:
    html = _fetch_text("https://www.cortecostituzionale.it/contenuti/composizione/presidenti")
    if _looks_like_bot_wall(html):
        return [dict(row) for row in CORTE_PRESIDENTS_FALLBACK]
    links, headings = _parse_links_and_headings(
        html,
        "https://www.cortecostituzionale.it/contenuti/composizione/presidenti",
    )
    names = [h["text"] for h in headings if h["tag"] == "h2"]
    page_links = [
        l["href"]
        for l in links
        if "/presidente" in l.get("href", "") and "cortecostituzionale" in l.get("href", "")
    ]
    rows = []
    for idx, name in enumerate(names):
        rows.append({
            "name": name,
            "url": page_links[idx] if idx < len(page_links) else "",
        })
    return _dedupe_dicts(rows, ["name"]) or [dict(row) for row in CORTE_PRESIDENTS_FALLBACK]


def load_laws_institutional_summary(db_path: str | Path = Path("data/laws.db")) -> dict[str, Any]:
    db_path = Path(db_path)
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
        by_leg = [
            dict(row)
            for row in conn.execute(
                "SELECT legislature_id, COUNT(*) AS law_count "
                "FROM laws WHERE legislature_id IS NOT NULL "
                "GROUP BY legislature_id ORDER BY legislature_id"
            ).fetchall()
        ]
        by_gov = [
            dict(row)
            for row in conn.execute(
                "SELECT government, COUNT(*) AS law_count "
                "FROM laws WHERE government IS NOT NULL AND TRIM(government) != '' "
                "GROUP BY government ORDER BY law_count DESC, government LIMIT 40"
            ).fetchall()
        ]
        recent = [
            dict(row)
            for row in conn.execute(
                "SELECT urn, title, year, date, government, legislature_id, status "
                "FROM laws ORDER BY date DESC, year DESC LIMIT 50"
            ).fetchall()
        ]
        return {
            "db_path": str(db_path),
            "laws_total": total,
            "distinct_legislatures": len(by_leg),
            "distinct_governments": len(by_gov),
            "by_legislature": by_leg,
            "by_government": by_gov,
            "recent_laws": recent,
        }
    finally:
        conn.close()


def collect_institutional_snapshot(
    db_path: str | Path = Path("data/laws.db"),
    include_endpoint_status: bool = False,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registry": registry_rows(),
        "laws_summary": load_laws_institutional_summary(db_path),
        "errors": {},
    }

    tasks: list[tuple[str, str, Any]] = [
        ("camera", "current_deputies", lambda: fetch_camera_deputies()),
        ("camera", "legislatures", fetch_camera_legislatures),
        ("camera", "governments", fetch_camera_governments),
        ("senato", "current_senators", fetch_senato_senators),
        ("senato", "recent_ddl", fetch_senato_recent_ddl),
        ("governo", "current_members", fetch_governo_members),
        ("governo", "history", fetch_governo_history),
        ("governo", "rss", fetch_governo_rss),
        ("quirinale", "presidents", fetch_quirinale_presidents),
        ("quirinale", "archive_links", fetch_quirinale_archive_links),
        ("corte", "current_judges", fetch_corte_judges),
        ("corte", "latest_deposits", fetch_corte_latest_deposits),
        ("corte", "presidents_history", fetch_corte_presidents_history),
    ]

    for section, key, fn in tasks:
        snapshot.setdefault(section, {})
        try:
            snapshot[section][key] = fn()
        except Exception as exc:  # pragma: no cover - network failures vary by env
            snapshot[section][key] = []
            snapshot["errors"][f"{section}.{key}"] = str(exc)

    if include_endpoint_status:
        snapshot["endpoint_status"] = collect_endpoint_statuses()

    return snapshot


def save_snapshot(snapshot: dict[str, Any], output_path: str | Path = DEFAULT_SNAPSHOT_PATH) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def load_snapshot(path: str | Path = DEFAULT_SNAPSHOT_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "CAMERA_SPARQL_ENDPOINT",
    "SENATO_SPARQL_ENDPOINT",
    "DEFAULT_SNAPSHOT_PATH",
    "INSTITUTIONAL_ENDPOINTS",
    "InstitutionalEndpoint",
    "check_endpoint",
    "collect_endpoint_statuses",
    "collect_institutional_snapshot",
    "fetch_camera_deputies",
    "fetch_camera_governments",
    "fetch_camera_legislatures",
    "fetch_corte_judges",
    "fetch_corte_latest_deposits",
    "fetch_corte_presidents_history",
    "fetch_governo_history",
    "fetch_governo_members",
    "fetch_governo_rss",
    "fetch_quirinale_archive_links",
    "fetch_quirinale_presidents",
    "fetch_senato_recent_ddl",
    "fetch_senato_senators",
    "load_laws_institutional_summary",
    "load_snapshot",
    "registry_rows",
    "save_snapshot",
]
