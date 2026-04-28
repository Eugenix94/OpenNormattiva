#!/usr/bin/env python3
"""
SQLite Database Layer for Normattiva Legal Research

Provides FTS5 full-text search with BM25 ranking, citation graph analysis,
amendment tracking, and relationship discovery for 162K Italian laws.

Usage:
    db = LawDatabase()
    results = db.search_fts("protezione civile")
    law = db.get_law("urn:nir:decreto.legge:2024-01-15;1")
    citations_to = db.get_citations_incoming(law['urn'])
    graph = db.get_citation_neighborhood("urn:...", depth=2)
    timeline = db.get_amendment_timeline("urn:...")
"""

import sqlite3
import math
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from collections import defaultdict
import json
import logging
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LawDatabase:
    """SQLite database with FTS5 for law research."""
    
    def __init__(self, db_path: Path = Path('data/laws.db')):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False allows Streamlit's multi-threaded execution
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init_schema()
    
    def init_schema(self):
        """Create tables and indexes."""
        
        # Main laws table
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS laws (
                urn TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                type TEXT,
                date TEXT,
                year INTEGER,
                text TEXT,
                text_length INTEGER DEFAULT 0,
                article_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'in_force',
                source_collection TEXT,
                parsed_at TEXT,
                importance_score REAL DEFAULT 0.0,
                subject_tags TEXT DEFAULT '[]',
                legislature_id INTEGER,
                government TEXT,
                era TEXT
            )
        ''')
        
        # Migrate: add legislature columns if missing (for existing DBs)
        try:
            self.conn.execute('SELECT legislature_id FROM laws LIMIT 1')
        except sqlite3.OperationalError:
            self.conn.execute('ALTER TABLE laws ADD COLUMN legislature_id INTEGER')
            self.conn.execute('ALTER TABLE laws ADD COLUMN government TEXT')
            self.conn.execute('ALTER TABLE laws ADD COLUMN era TEXT')
        
        # FTS5 virtual table for full-text search with BM25
        self.conn.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS laws_fts 
            USING fts5(urn UNINDEXED, title, type, text,
                      content=laws, content_rowid=rowid,
                      tokenize='unicode61 remove_diacritics 2')
        ''')
        
        # Citations: which laws reference which
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS citations (
                citing_urn TEXT,
                cited_urn TEXT,
                count INTEGER DEFAULT 1,
                context TEXT,
                PRIMARY KEY (citing_urn, cited_urn),
                FOREIGN KEY (citing_urn) REFERENCES laws(urn),
                FOREIGN KEY (cited_urn) REFERENCES laws(urn)
            )
        ''')
        
        # Amendment tracking
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS amendments (
                urn TEXT,
                amending_urn TEXT,
                action TEXT,
                date_effective TEXT,
                article_modified TEXT,
                change_description TEXT,
                detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (urn, amending_urn, action),
                FOREIGN KEY (urn) REFERENCES laws(urn),
                FOREIGN KEY (amending_urn) REFERENCES laws(urn)
            )
        ''')
        
        # Metadata enrichment
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS law_metadata (
                urn TEXT PRIMARY KEY,
                authority TEXT,
                keywords TEXT,
                abstract TEXT,
                implementing_regulations TEXT,
                implemented_by TEXT,
                amendment_count INTEGER DEFAULT 0,
                citation_count_incoming INTEGER DEFAULT 0,
                citation_count_outgoing INTEGER DEFAULT 0,
                pagerank REAL DEFAULT 0.0,
                domain_cluster TEXT,
                last_modified TEXT,
                FOREIGN KEY (urn) REFERENCES laws(urn)
            )
        ''')
        
        # Snapshot tracking for live updates
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS update_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection TEXT NOT NULL,
                etag TEXT,
                laws_count INTEGER,
                laws_added INTEGER DEFAULT 0,
                laws_updated INTEGER DEFAULT 0,
                laws_removed INTEGER DEFAULT 0,
                checked_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # API change detection tracking
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS api_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection TEXT NOT NULL,
                old_etag TEXT,
                new_etag TEXT,
                detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                preview_data TEXT,
                processed_at TEXT
            )
        ''')

        # Manual update log — tracks when user manually updates the dataset
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS update_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                action TEXT NOT NULL,
                description TEXT,
                laws_before INTEGER,
                laws_after INTEGER,
                collections_affected TEXT,
                user_note TEXT
            )
        ''')
        
        # Create indexes for performance
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_laws_year ON laws(year)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_laws_type ON laws(type)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_laws_status ON laws(status)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_laws_importance ON laws(importance_score)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_citations_citing ON citations(citing_urn)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_citations_cited ON citations(cited_urn)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_amendments_urn ON amendments(urn)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_amendments_date ON amendments(date_effective)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_metadata_pagerank ON law_metadata(pagerank)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_laws_legislature ON laws(legislature_id)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_laws_government ON laws(government)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_api_changes_status ON api_changes(status)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_api_changes_collection ON api_changes(collection)')

        # Articles table (structured article-level breakdown of law text)
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                law_urn TEXT NOT NULL,
                article_num TEXT,
                heading TEXT,
                text TEXT,
                char_count INTEGER DEFAULT 0,
                FOREIGN KEY (law_urn) REFERENCES laws(urn)
            )
        ''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_articles_law_urn ON articles(law_urn)')

        # Corte Costituzionale decisions (sentenze & ordinanze)
        self.conn.execute('''
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
        ''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_sentenze_anno ON sentenze(anno)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_sentenze_tipo ON sentenze(tipo)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_sentenze_esito ON sentenze(esito)')

        # CC massime (headnotes) linked to sentenze
        self.conn.execute('''
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
        ''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_massime_ecli ON sentenze_massime(ecli)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_massime_anno ON sentenze_massime(anno)')

        # OpenGA – dataset catalog (one row per CKAN package)
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS openga_catalog (
                package_id TEXT PRIMARY KEY,
                title TEXT,
                court TEXT,
                dataset_type TEXT,
                resource_url TEXT,
                resource_format TEXT,
                record_count INTEGER DEFAULT 0,
                last_updated TEXT,
                license TEXT,
                fetched_at TEXT
            )
        ''')

        # OpenGA – administrative court decisions
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS openga_sentenze (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                package_id TEXT,
                court TEXT,
                anno INTEGER,
                numero TEXT,
                data_deposito TEXT,
                sezione TEXT,
                oggetto TEXT,
                esito TEXT,
                source_url TEXT,
                raw_json TEXT,
                imported_at TEXT
            )
        ''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_openga_court ON openga_sentenze(court)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_openga_anno ON openga_sentenze(anno)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_openga_pkg ON openga_sentenze(package_id)')

        # Migrate citations table: add article-level columns if missing
        try:
            self.conn.execute('SELECT citing_article FROM citations LIMIT 1')
        except sqlite3.OperationalError:
            self.conn.execute('ALTER TABLE citations ADD COLUMN citing_article TEXT')
            self.conn.execute('ALTER TABLE citations ADD COLUMN cited_article TEXT')

        self.conn.commit()
        logger.info("Schema initialized")
    
    def insert_law(self, law: Dict) -> bool:
        """Insert or update single law."""
        try:
            # Auto-compute legislature metadata from year if not provided
            legislature_id = law.get('legislature_id')
            government = law.get('government')
            era = law.get('era')
            year = law.get('year')
            if year and not legislature_id:
                try:
                    from core.legislature import LegislatureMetadata
                    legislature_id = LegislatureMetadata.get_legislature_from_year(year)
                    government = LegislatureMetadata.get_government_from_year(year)
                    era = LegislatureMetadata.get_era_from_year(year)
                except Exception:
                    pass
            
            self.conn.execute('''
                INSERT OR REPLACE INTO laws 
                (urn, title, type, date, year, text, text_length, article_count, 
                 source_collection, parsed_at, legislature_id, government, era)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                law.get('urn'),
                law.get('title'),
                law.get('type'),
                law.get('date'),
                law.get('year'),
                law.get('text', ''),
                law.get('text_length', 0),
                law.get('article_count', 0),
                law.get('source_collection', ''),
                datetime.now().isoformat(),
                legislature_id,
                government,
                era,
            ))
            
            # Index for FTS
            text_for_fts = law.get('text', '')[:50000]
            self.conn.execute('''
                INSERT OR REPLACE INTO laws_fts (urn, title, type, text)
                VALUES (?, ?, ?, ?)
            ''', (
                law.get('urn'),
                law.get('title', ''),
                law.get('type', ''),
                text_for_fts
            ))
            
            # Insert citations (targets may reference laws not yet in DB)
            for cit in law.get('citations', []):
                if isinstance(cit, dict):
                    citation_urn = cit.get('target_urn', cit.get('cited_urn', ''))
                    context = cit.get('ref', '')
                    if cit.get('article'):
                        context = f"art. {cit['article']} | {context}"
                else:
                    citation_urn = cit
                    context = ''
                if citation_urn:
                    try:
                        self.conn.execute('''
                            INSERT OR IGNORE INTO citations (citing_urn, cited_urn, count, context)
                            VALUES (?, ?, 1, ?)
                        ''', (law.get('urn'), citation_urn, context))
                    except Exception:
                        pass  # FK violation when target law not in DB yet
            
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error inserting law {law.get('urn')}: {e}")
            return False
    
    def insert_laws_from_jsonl(self, jsonl_file: Path, batch_size: int = 500) -> int:
        """Bulk insert from JSONL file with batched commits."""
        jsonl_file = Path(jsonl_file)
        if not jsonl_file.exists():
            logger.error(f"File not found: {jsonl_file}")
            return 0
        
        inserted = 0
        try:
            with open(jsonl_file, 'r', encoding='utf-8') as f:
                for i, line in enumerate(f):
                    if not line.strip():
                        continue
                    try:
                        law = json.loads(line)
                        if self.insert_law(law):
                            inserted += 1
                        
                        if (i + 1) % batch_size == 0:
                            self.conn.commit()
                            logger.info(f"Processed {i + 1} laws...")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Invalid JSON on line {i}: {e}")
                        continue
            
            self.conn.commit()
            logger.info(f"Successfully inserted {inserted} laws")
            return inserted
        except Exception as e:
            logger.error(f"Error loading JSONL: {e}")
            return inserted
    
    # ── SEARCH ───────────────────────────────────────────────────────────────
    
    def search_fts(self, query: str, limit: int = 50) -> List[Dict]:
        """Full-text search using FTS5 BM25 ranking with snippet generation."""
        try:
            # Sanitize query for FTS5
            safe_query = self._sanitize_fts_query(query)
            if not safe_query:
                return []
            
            results = self.conn.execute('''
                SELECT 
                    l.urn, l.title, l.type, l.year, l.date,
                    l.article_count, l.text_length, l.importance_score,
                    rank * (-1) as relevance_score,
                    snippet(laws_fts, 3, '<b>', '</b>', '...', 40) as snippet
                FROM laws_fts f
                JOIN laws l ON l.urn = f.urn
                WHERE laws_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            ''', (safe_query, limit)).fetchall()
            
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Search error for '{query}': {e}")
            # Fallback to LIKE search
            return self._search_like(query, limit)
    
    def _sanitize_fts_query(self, query: str) -> str:
        """Safely format user input for FTS5 MATCH."""
        # Remove FTS5 special characters that could cause errors
        cleaned = re.sub(r'[^\w\s]', ' ', query, flags=re.UNICODE)
        terms = cleaned.split()
        if not terms:
            return ''
        # Quote each term for safety
        return ' '.join(f'"{t}"' for t in terms if len(t) > 1)
    
    def _search_like(self, query: str, limit: int) -> List[Dict]:
        """Fallback LIKE search when FTS fails."""
        pattern = f'%{query}%'
        results = self.conn.execute('''
            SELECT urn, title, type, year, date, article_count, 
                   text_length, importance_score, 0.0 as relevance_score,
                   '' as snippet
            FROM laws
            WHERE title LIKE ? OR text LIKE ?
            ORDER BY importance_score DESC
            LIMIT ?
        ''', (pattern, pattern, limit)).fetchall()
        return [dict(r) for r in results]
    
    def search_with_filters(self, 
                          query: str = '',
                          law_type: Optional[str] = None,
                          year_min: int = 0,
                          year_max: int = 9999,
                          article_min: int = 0,
                          article_max: int = 99999,
                          cite_count_min: int = 0,
                          status: Optional[str] = None,
                          sort_by: str = 'relevance',
                          limit: int = 50) -> List[Dict]:
        """Advanced search with multiple filters and sort options."""
        try:
            params = []
            
            # Base query with citation counts
            if query:
                safe_query = self._sanitize_fts_query(query)
                if not safe_query:
                    return []
                sql = '''
                    SELECT l.*, 
                           COALESCE(ci.cnt, 0) as citation_count_in,
                           COALESCE(co.cnt, 0) as citation_count_out,
                           rank * (-1) as relevance_score
                    FROM laws_fts f
                    JOIN laws l ON l.urn = f.urn
                    LEFT JOIN (SELECT cited_urn, COUNT(*) cnt FROM citations GROUP BY cited_urn) ci ON ci.cited_urn = l.urn
                    LEFT JOIN (SELECT citing_urn, COUNT(*) cnt FROM citations GROUP BY citing_urn) co ON co.citing_urn = l.urn
                    WHERE laws_fts MATCH ?
                '''
                params.append(safe_query)
            else:
                sql = '''
                    SELECT l.*,
                           COALESCE(ci.cnt, 0) as citation_count_in,
                           COALESCE(co.cnt, 0) as citation_count_out,
                           0.0 as relevance_score
                    FROM laws l
                    LEFT JOIN (SELECT cited_urn, COUNT(*) cnt FROM citations GROUP BY cited_urn) ci ON ci.cited_urn = l.urn
                    LEFT JOIN (SELECT citing_urn, COUNT(*) cnt FROM citations GROUP BY citing_urn) co ON co.citing_urn = l.urn
                    WHERE 1=1
                '''
            
            if law_type:
                sql += ' AND l.type = ?'
                params.append(law_type)
            
            if year_min > 0:
                sql += ' AND l.year >= ?'
                params.append(year_min)
            if year_max < 9999:
                sql += ' AND l.year <= ?'
                params.append(year_max)
            
            if article_min > 0:
                sql += ' AND l.article_count >= ?'
                params.append(article_min)
            if article_max < 99999:
                sql += ' AND l.article_count <= ?'
                params.append(article_max)
            
            if status:
                sql += ' AND l.status = ?'
                params.append(status)
            
            if cite_count_min > 0:
                sql += ' AND COALESCE(ci.cnt, 0) >= ?'
                params.append(cite_count_min)
            
            # Sort options
            sort_map = {
                'relevance': 'relevance_score DESC' if query else 'citation_count_in DESC',
                'citations': 'citation_count_in DESC',
                'year_desc': 'l.year DESC',
                'year_asc': 'l.year ASC',
                'importance': 'l.importance_score DESC',
                'articles': 'l.article_count DESC',
                'title': 'l.title ASC',
            }
            sql += f' ORDER BY {sort_map.get(sort_by, "citation_count_in DESC")}'
            sql += ' LIMIT ?'
            params.append(limit)
            
            results = self.conn.execute(sql, params).fetchall()
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Advanced search error: {e}")
            return []
    
    def get_law_types(self) -> List[str]:
        """Get all distinct law types."""
        rows = self.conn.execute(
            'SELECT DISTINCT type FROM laws WHERE type IS NOT NULL ORDER BY type'
        ).fetchall()
        return [r['type'] for r in rows]
    
    def get_year_range(self) -> Tuple[int, int]:
        """Get min and max year."""
        row = self.conn.execute(
            'SELECT MIN(year) as min_y, MAX(year) as max_y FROM laws WHERE year > 0'
        ).fetchone()
        return (row['min_y'] or 1861, row['max_y'] or 2026)
    
    # ── LAW RETRIEVAL ────────────────────────────────────────────────────────
    
    def get_law(self, urn: str) -> Optional[Dict]:
        """Get complete law details by URN with metadata and citation counts."""
        try:
            law = self.conn.execute('''
                SELECT l.*,
                       COALESCE(ci.cnt, 0) as citations_in,
                       COALESCE(co.cnt, 0) as citations_out,
                       COALESCE(am.cnt, 0) as amendment_count
                FROM laws l
                LEFT JOIN (SELECT cited_urn, COUNT(*) cnt FROM citations GROUP BY cited_urn) ci ON ci.cited_urn = l.urn
                LEFT JOIN (SELECT citing_urn, COUNT(*) cnt FROM citations GROUP BY citing_urn) co ON co.citing_urn = l.urn
                LEFT JOIN (SELECT urn, COUNT(*) cnt FROM amendments GROUP BY urn) am ON am.urn = l.urn
                WHERE l.urn = ?
            ''', (urn,)).fetchone()
            
            if not law:
                return None
            
            law_dict = dict(law)
            
            # Get metadata if exists
            meta = self.conn.execute(
                'SELECT * FROM law_metadata WHERE urn = ?', (urn,)
            ).fetchone()
            if meta:
                law_dict['metadata'] = dict(meta)
            
            return law_dict
        except Exception as e:
            logger.error(f"Error retrieving law {urn}: {e}")
            return None
    
    # ── CITATIONS ────────────────────────────────────────────────────────────
    
    def get_citations_outgoing(self, urn: str, limit: int = None) -> List[Dict]:
        """Get laws that this law cites."""
        try:
            sql = '''
                SELECT c.cited_urn as urn, l.title, l.year, l.type, c.count, c.context,
                       c.citing_article, c.cited_article
                FROM citations c
                LEFT JOIN laws l ON c.cited_urn = l.urn
                WHERE c.citing_urn = ?
                ORDER BY c.count DESC
            '''
            params = [urn]
            if limit:
                sql += ' LIMIT ?'
                params.append(limit)
            
            results = self.conn.execute(sql, params).fetchall()
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Error getting outgoing citations: {e}")
            return []
    
    def get_citations_incoming(self, urn: str, limit: int = None) -> List[Dict]:
        """Get laws that cite this law."""
        try:
            sql = '''
                SELECT c.citing_urn as urn, l.title, l.year, l.type, c.count, c.context,
                       c.citing_article, c.cited_article
                FROM citations c
                LEFT JOIN laws l ON c.citing_urn = l.urn
                WHERE c.cited_urn = ?
                ORDER BY c.count DESC
            '''
            params = [urn]
            if limit:
                sql += ' LIMIT ?'
                params.append(limit)
            
            results = self.conn.execute(sql, params).fetchall()
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Error getting incoming citations: {e}")
            return []

    # ── ARTICLES ─────────────────────────────────────────────────────────────

    def get_articles(self, law_urn: str) -> List[Dict]:
        """Get structured articles for a law, ordered by article number."""
        try:
            rows = self.conn.execute(
                'SELECT id, article_num, heading, text, char_count FROM articles '
                'WHERE law_urn = ? ORDER BY id',
                (law_urn,)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error getting articles for {law_urn}: {e}")
            return []

    def insert_article(self, law_urn: str, article_num: str, heading: str, text: str) -> bool:
        """Insert a structured article record."""
        try:
            self.conn.execute(
                'INSERT INTO articles (law_urn, article_num, heading, text, char_count) '
                'VALUES (?, ?, ?, ?, ?)',
                (law_urn, article_num, heading, text, len(text or ''))
            )
            return True
        except Exception as e:
            logger.error(f"Error inserting article: {e}")
            return False

    def parse_and_insert_articles(self, law_urn: str, text: str) -> int:
        """Parse law text into structured articles and store them. Returns count inserted."""
        if not text or not law_urn:
            return 0
        # Keep per-law parsing bounded for predictable runtime.
        text = text[:300000]
        # Delete existing articles for this law first (idempotent)
        self.conn.execute('DELETE FROM articles WHERE law_urn = ?', (law_urn,))

        # Fast line-based parser: avoids pathological regex backtracking
        # on very large law texts.
        header_re = re.compile(
            r'^\s*Art(?:icolo)?\.?\s*(\d+(?:[a-z]|-\w+)?)\b[.\s\-]*(.{0,200})',
            re.IGNORECASE,
        )

        current_num = None
        current_heading = ""
        current_lines = []
        count = 0

        for line in text.splitlines():
            m = header_re.match(line)
            if m:
                if current_num is not None:
                    body = '\n'.join(current_lines).strip()
                    self.insert_article(law_urn, current_num, current_heading, body)
                    count += 1
                current_num = m.group(1).strip()
                current_heading = (m.group(2) or "").strip()[:200]
                current_lines = []
            elif current_num is not None:
                current_lines.append(line)

        if current_num is not None:
            body = '\n'.join(current_lines).strip()
            self.insert_article(law_urn, current_num, current_heading, body)
            count += 1

        self.conn.commit()
        return count

    def backfill_article_columns_from_context(self) -> int:
        """Parse existing context field in citations to extract article references.

        Context is stored as "art. X | ref_text" — extract X into citing_article.
        Returns number of rows updated.
        """
        art_pattern = re.compile(r'^art\.\s*(\w+)', re.IGNORECASE)
        rows = self.conn.execute(
            "SELECT citing_urn, cited_urn, context FROM citations "
            "WHERE context IS NOT NULL AND context != '' "
            "AND (citing_article IS NULL OR citing_article = '')"
        ).fetchall()
        updated = 0
        for row in rows:
            ctx = row['context'] or ''
            m = art_pattern.match(ctx)
            if m:
                art = m.group(1)
                self.conn.execute(
                    "UPDATE citations SET citing_article = ? "
                    "WHERE citing_urn = ? AND cited_urn = ?",
                    (art, row['citing_urn'], row['cited_urn'])
                )
                updated += 1
        if updated:
            self.conn.commit()
        return updated

    def get_most_cited_laws(self, limit: int = 50) -> List[Dict]:
        """Get the most influential laws by incoming citation count."""
        results = self.conn.execute('''
            SELECT l.urn, l.title, l.type, l.year, l.date,
                   COUNT(c.citing_urn) as citation_count,
                   l.importance_score
            FROM laws l
            JOIN citations c ON c.cited_urn = l.urn
            GROUP BY l.urn
            ORDER BY citation_count DESC
            LIMIT ?
        ''', (limit,)).fetchall()
        return [dict(r) for r in results]
    
    def get_citation_neighborhood(self, urn: str, depth: int = 1, max_nodes: int = 100) -> Dict:
        """Get the citation graph neighborhood around a law.
        
        Returns nodes and edges for visualization.
        """
        visited = set()
        nodes = []
        edges = []
        queue = [(urn, 0)]
        
        while queue and len(visited) < max_nodes:
            current_urn, current_depth = queue.pop(0)
            if current_urn in visited or current_depth > depth:
                continue
            visited.add(current_urn)
            
            # Get law info
            law = self.conn.execute(
                'SELECT urn, title, type, year FROM laws WHERE urn = ?', (current_urn,)
            ).fetchone()
            
            if law:
                nodes.append({
                    'id': current_urn,
                    'title': law['title'][:80] if law['title'] else current_urn,
                    'type': law['type'],
                    'year': law['year'],
                    'is_center': current_urn == urn,
                })
            
            if current_depth < depth:
                # Outgoing
                outgoing = self.conn.execute(
                    'SELECT cited_urn FROM citations WHERE citing_urn = ? LIMIT 20',
                    (current_urn,)
                ).fetchall()
                for r in outgoing:
                    edges.append({'source': current_urn, 'target': r['cited_urn'], 'type': 'cites'})
                    queue.append((r['cited_urn'], current_depth + 1))
                
                # Incoming
                incoming = self.conn.execute(
                    'SELECT citing_urn FROM citations WHERE cited_urn = ? LIMIT 20',
                    (current_urn,)
                ).fetchall()
                for r in incoming:
                    edges.append({'source': r['citing_urn'], 'target': current_urn, 'type': 'cites'})
                    queue.append((r['citing_urn'], current_depth + 1))
        
        return {'nodes': nodes, 'edges': edges}
    
    # ── AMENDMENTS ───────────────────────────────────────────────────────────
    
    def record_amendment(self, urn: str, amending_urn: str, action: str,
                        date_effective: str = None, article_modified: str = None,
                        description: str = None):
        """Record an amendment relationship."""
        self.conn.execute('''
            INSERT OR REPLACE INTO amendments 
            (urn, amending_urn, action, date_effective, article_modified, change_description)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (urn, amending_urn, action, date_effective, article_modified, description))
        self.conn.commit()
    
    def get_amendment_timeline(self, urn: str) -> List[Dict]:
        """Get full amendment history for a law, chronologically."""
        results = self.conn.execute('''
            SELECT a.*, l.title as amending_title
            FROM amendments a
            LEFT JOIN laws l ON l.urn = a.amending_urn
            WHERE a.urn = ?
            ORDER BY a.date_effective ASC
        ''', (urn,)).fetchall()
        return [dict(r) for r in results]
    
    def get_most_amended_laws(self, limit: int = 30) -> List[Dict]:
        """Laws with the most amendments."""
        results = self.conn.execute('''
            SELECT l.urn, l.title, l.type, l.year,
                   COUNT(a.amending_urn) as amendment_count,
                   MIN(a.date_effective) as first_amendment,
                   MAX(a.date_effective) as last_amendment
            FROM laws l
            JOIN amendments a ON a.urn = l.urn
            GROUP BY l.urn
            ORDER BY amendment_count DESC
            LIMIT ?
        ''', (limit,)).fetchall()
        return [dict(r) for r in results]
    
    def get_recent_amendments(self, days: int = 30, limit: int = 50) -> List[Dict]:
        """Get amendments detected in the last N days."""
        results = self.conn.execute('''
            SELECT a.*, 
                   l1.title as law_title,
                   l2.title as amending_title
            FROM amendments a
            LEFT JOIN laws l1 ON l1.urn = a.urn
            LEFT JOIN laws l2 ON l2.urn = a.amending_urn
            WHERE a.detected_at >= datetime('now', ?)
            ORDER BY a.detected_at DESC
            LIMIT ?
        ''', (f'-{days} days', limit)).fetchall()
        return [dict(r) for r in results]
    
    # ── IMPORTANCE & PAGERANK ────────────────────────────────────────────────
    
    def compute_importance_scores(self, iterations: int = 20, damping: float = 0.85):
        """Compute PageRank-like importance scores for all laws.
        
        Laws cited by many important laws get higher scores.
        """
        logger.info("Computing importance scores (PageRank)...")
        
        # Get all citation edges
        edges = self.conn.execute('SELECT citing_urn, cited_urn FROM citations').fetchall()
        
        # Build adjacency lists
        outgoing = defaultdict(list)  # urn -> [urns it cites]
        incoming = defaultdict(list)  # urn -> [urns that cite it]
        all_urns = set()
        
        for e in edges:
            outgoing[e['citing_urn']].append(e['cited_urn'])
            incoming[e['cited_urn']].append(e['citing_urn'])
            all_urns.add(e['citing_urn'])
            all_urns.add(e['cited_urn'])
        
        if not all_urns:
            logger.info("No citations found, skipping PageRank")
            return
        
        # Only score URNs that actually exist in laws table (avoid FK violations)
        existing_urns = {
            r[0] for r in self.conn.execute('SELECT urn FROM laws').fetchall()
        }
        all_urns = all_urns & existing_urns
        if not all_urns:
            logger.info("No citation targets match known laws, skipping PageRank")
            return
        N = len(all_urns)
        scores = {urn: 1.0 / N for urn in all_urns}
        
        for i in range(iterations):
            new_scores = {}
            for urn in all_urns:
                rank = (1 - damping) / N
                for source in incoming.get(urn, []):
                    out_count = len(outgoing.get(source, []))
                    if out_count > 0:
                        rank += damping * scores[source] / out_count
                new_scores[urn] = rank
            scores = new_scores
        
        # Normalize to 0-100 scale
        max_score = max(scores.values()) if scores else 1
        
        # Update database
        for urn, score in scores.items():
            normalized = (score / max_score) * 100
            self.conn.execute(
                'UPDATE laws SET importance_score = ? WHERE urn = ?',
                (round(normalized, 2), urn)
            )
            self.conn.execute('''
                INSERT OR REPLACE INTO law_metadata (urn, pagerank, citation_count_incoming, citation_count_outgoing)
                VALUES (?, ?, 
                    COALESCE((SELECT citation_count_incoming FROM law_metadata WHERE urn = ?), ?),
                    COALESCE((SELECT citation_count_outgoing FROM law_metadata WHERE urn = ?), ?))
            ''', (urn, round(score, 8),
                  urn, len(incoming.get(urn, [])),
                  urn, len(outgoing.get(urn, []))))
        
        self.conn.commit()
        logger.info(f"Computed importance scores for {len(scores)} laws")
    
    def compute_citation_counts(self):
        """Update citation counts in law_metadata."""
        logger.info("Computing citation counts...")
        
        self.conn.execute('''
            INSERT OR REPLACE INTO law_metadata (urn, citation_count_incoming, citation_count_outgoing)
            SELECT l.urn,
                   COALESCE(ci.cnt, 0),
                   COALESCE(co.cnt, 0)
            FROM laws l
            LEFT JOIN (SELECT cited_urn, COUNT(*) cnt FROM citations GROUP BY cited_urn) ci ON ci.cited_urn = l.urn
            LEFT JOIN (SELECT citing_urn, COUNT(*) cnt FROM citations GROUP BY citing_urn) co ON co.citing_urn = l.urn
        ''')
        self.conn.commit()
        logger.info("Citation counts updated")
    
    # ── DOMAIN DETECTION ─────────────────────────────────────────────────────
    
    DOMAIN_KEYWORDS = {
        'diritto_penale': ['reato', 'pena', 'reclusione', 'delitto', 'contravvenzione', 'penale'],
        'diritto_civile': ['contratto', 'obbligazione', 'proprietà', 'possesso', 'civile'],
        'diritto_lavoro': ['lavoratore', 'datore', 'rapporto di lavoro', 'licenziamento', 'retribuzione'],
        'diritto_amministrativo': ['pubblica amministrazione', 'procedimento', 'provvedimento', 'autorità'],
        'diritto_tributario': ['imposta', 'tributo', 'fiscale', 'contribuente', 'reddito', 'iva'],
        'diritto_sanitario': ['salute', 'sanitario', 'medico', 'ospedale', 'farmaco', 'vaccino'],
        'diritto_ambientale': ['ambiente', 'inquinamento', 'rifiuti', 'ecologia', 'emissioni'],
        'diritto_europeo': ['unione europea', 'direttiva', 'regolamento ue', 'trattato'],
        'diritto_costituzionale': ['costituzione', 'costituzionale', 'diritti fondamentali', 'referendum'],
        'protezione_civile': ['protezione civile', 'emergenza', 'calamità', 'stato di emergenza'],
        'edilizia_urbanistica': ['edilizia', 'urbanistica', 'costruzione', 'concessione edilizia'],
        'trasporti': ['trasporto', 'circolazione', 'stradale', 'navigazione', 'aviazione'],
    }
    
    def detect_law_domains(self, batch_size: int = 1000):
        """Detect legal domains for all laws based on keyword matching."""
        logger.info("Detecting law domains...")
        
        offset = 0
        total = 0
        while True:
            rows = self.conn.execute(
                'SELECT urn, title, text FROM laws LIMIT ? OFFSET ?',
                (batch_size, offset)
            ).fetchall()
            
            if not rows:
                break
            
            for row in rows:
                text = (row['title'] or '') + ' ' + (row['text'] or '')[:2000]
                text_lower = text.lower()
                
                domains = []
                for domain, keywords in self.DOMAIN_KEYWORDS.items():
                    for kw in keywords:
                        if kw in text_lower:
                            domains.append(domain)
                            break
                
                if domains:
                    tags = json.dumps(domains[:5])
                    self.conn.execute(
                        'UPDATE laws SET subject_tags = ? WHERE urn = ?',
                        (tags, row['urn'])
                    )
                    self.conn.execute('''
                        INSERT OR REPLACE INTO law_metadata (urn, domain_cluster, keywords)
                        VALUES (?, ?, ?)
                    ''', (row['urn'], domains[0], json.dumps(domains)))
                    total += 1
            
            offset += batch_size
            self.conn.commit()
        
        logger.info(f"Detected domains for {total} laws")
    
    # ── STATISTICS ───────────────────────────────────────────────────────────
    
    def get_statistics(self) -> Dict:
        """Get comprehensive dataset statistics."""
        try:
            stats = {}
            
            stats['total_laws'] = self.conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
            stats['total_citations'] = self.conn.execute('SELECT COUNT(*) FROM citations').fetchone()[0]
            stats['total_amendments'] = self.conn.execute('SELECT COUNT(*) FROM amendments').fetchone()[0]
            
            # By type
            types = self.conn.execute(
                'SELECT type, COUNT(*) as count FROM laws GROUP BY type ORDER BY count DESC'
            ).fetchall()
            stats['by_type'] = {row['type']: row['count'] for row in types}
            
            # By year (all years)
            years = self.conn.execute(
                'SELECT year, COUNT(*) as count FROM laws WHERE year > 0 GROUP BY year ORDER BY year'
            ).fetchall()
            stats['by_year'] = {row['year']: row['count'] for row in years}
            
            # Most cited laws
            most_cited = self.conn.execute('''
                SELECT l.urn, l.title, l.type, l.year, COUNT(c.citing_urn) as citation_count
                FROM laws l
                JOIN citations c ON c.cited_urn = l.urn
                GROUP BY l.urn
                ORDER BY citation_count DESC
                LIMIT 20
            ''').fetchall()
            stats['most_cited'] = [dict(row) for row in most_cited]
            
            # Domain distribution 
            domains = self.conn.execute('''
                SELECT domain_cluster, COUNT(*) as count 
                FROM law_metadata 
                WHERE domain_cluster IS NOT NULL
                GROUP BY domain_cluster
                ORDER BY count DESC
            ''').fetchall()
            stats['by_domain'] = {row['domain_cluster']: row['count'] for row in domains}
            
            # Text stats
            text_stats = self.conn.execute('''
                SELECT AVG(text_length) as avg_len, 
                       SUM(text_length) as total_len,
                       AVG(article_count) as avg_articles,
                       SUM(article_count) as total_articles
                FROM laws
            ''').fetchone()
            stats['text_stats'] = {
                'avg_length': round(text_stats['avg_len'] or 0),
                'total_length': text_stats['total_len'] or 0,
                'avg_articles': round(text_stats['avg_articles'] or 0, 1),
                'total_articles': text_stats['total_articles'] or 0,
            }
            
            # Year range
            yr = self.conn.execute(
                'SELECT MIN(year) as y_min, MAX(year) as y_max FROM laws WHERE year > 0'
            ).fetchone()
            stats['year_range'] = {'min': yr['y_min'], 'max': yr['y_max']}
            
            return stats
        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            return {}
    
    # ── RELATIONSHIP DISCOVERY ───────────────────────────────────────────────
    
    def find_related_laws(self, urn: str, limit: int = 10) -> List[Dict]:
        """Find related laws through shared citations (co-citation analysis).
        
        If law A and law B both cite the same set of laws, they're likely related.
        """
        results = self.conn.execute('''
            SELECT l.urn, l.title, l.type, l.year, COUNT(*) as shared_citations
            FROM citations c1
            JOIN citations c2 ON c1.cited_urn = c2.cited_urn AND c2.citing_urn != c1.citing_urn
            JOIN laws l ON l.urn = c2.citing_urn
            WHERE c1.citing_urn = ?
            GROUP BY l.urn
            ORDER BY shared_citations DESC
            LIMIT ?
        ''', (urn, limit)).fetchall()
        return [dict(r) for r in results]
    
    def find_laws_by_domain(self, domain: str, limit: int = 50) -> List[Dict]:
        """Find laws belonging to a specific legal domain."""
        results = self.conn.execute('''
            SELECT l.urn, l.title, l.type, l.year, l.importance_score,
                   m.domain_cluster
            FROM laws l
            JOIN law_metadata m ON m.urn = l.urn
            WHERE m.domain_cluster = ?
            ORDER BY l.importance_score DESC
            LIMIT ?
        ''', (domain, limit)).fetchall()
        return [dict(r) for r in results]
    
    # ── EXPORT ───────────────────────────────────────────────────────────────
    
    def export_csv(self, output_file: Path, limit: int = None):
        """Export laws to CSV."""
        import csv
        
        sql = '''
            SELECT l.urn, l.title, l.type, l.date, l.year, 
                   l.article_count, l.text_length, l.status, l.importance_score,
                   COALESCE(ci.cnt, 0) as citations_in,
                   COALESCE(co.cnt, 0) as citations_out
            FROM laws l
            LEFT JOIN (SELECT cited_urn, COUNT(*) cnt FROM citations GROUP BY cited_urn) ci ON ci.cited_urn = l.urn
            LEFT JOIN (SELECT citing_urn, COUNT(*) cnt FROM citations GROUP BY citing_urn) co ON co.citing_urn = l.urn
            ORDER BY l.year DESC, l.title
        '''
        if limit:
            sql += f' LIMIT {int(limit)}'
        
        rows = self.conn.execute(sql).fetchall()
        
        with open(output_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['URN', 'Title', 'Type', 'Date', 'Year', 
                           'Articles', 'Text_Length', 'Status', 'Importance',
                           'Citations_In', 'Citations_Out'])
            for row in rows:
                writer.writerow(list(row))
        
        logger.info(f"Exported {len(rows)} laws to {output_file}")
        return str(output_file)
    
    def export_graph_json(self, output_file: Path, min_citations: int = 2):
        """Export citation graph as JSON for visualization."""
        nodes = self.conn.execute('''
            SELECT l.urn as id, l.title as label, l.type, l.year,
                   l.importance_score, COALESCE(ci.cnt, 0) as size
            FROM laws l
            LEFT JOIN (SELECT cited_urn, COUNT(*) cnt FROM citations GROUP BY cited_urn) ci ON ci.cited_urn = l.urn
            WHERE COALESCE(ci.cnt, 0) >= ?
            ORDER BY size DESC
        ''', (min_citations,)).fetchall()
        
        node_ids = {n['id'] for n in nodes}
        
        edges = self.conn.execute('''
            SELECT citing_urn as source, cited_urn as target, count as weight
            FROM citations
            WHERE citing_urn IN ({ids}) AND cited_urn IN ({ids})
        '''.format(ids=','.join('?' * len(node_ids))),
            list(node_ids) + list(node_ids)
        ).fetchall() if node_ids else []
        
        graph = {
            'generated': datetime.now().isoformat(),
            'nodes': [dict(n) for n in nodes],
            'edges': [dict(e) for e in edges],
            'stats': {
                'nodes': len(nodes),
                'edges': len(edges),
                'min_citations': min_citations,
            }
        }
        
        with open(output_file, 'w') as f:
            json.dump(graph, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Exported graph: {len(nodes)} nodes, {len(edges)} edges")
        return str(output_file)
    
    # ── VALIDATION ───────────────────────────────────────────────────────────
    
    def validate_data(self) -> Dict:
        """Run data quality checks."""
        report = {'checks': {}, 'issues': []}
        
        # Check: no duplicate URNs
        dups = self.conn.execute(
            'SELECT urn, COUNT(*) c FROM laws GROUP BY urn HAVING c > 1'
        ).fetchall()
        report['checks']['duplicate_urns'] = len(dups) == 0
        if dups:
            report['issues'].append(f'{len(dups)} duplicate URNs')
        
        # Check: no empty titles
        empty_titles = self.conn.execute(
            "SELECT COUNT(*) FROM laws WHERE title IS NULL OR title = ''"
        ).fetchone()[0]
        report['checks']['titles_present'] = empty_titles == 0
        if empty_titles:
            report['issues'].append(f'{empty_titles} laws with empty titles')
        
        # Check: valid years
        bad_years = self.conn.execute(
            'SELECT COUNT(*) FROM laws WHERE year IS NOT NULL AND (year < 1800 OR year > 2030)'
        ).fetchone()[0]
        report['checks']['valid_years'] = bad_years == 0
        if bad_years:
            report['issues'].append(f'{bad_years} laws with invalid years')
        
        # Check: orphan citations (cited URN doesn't exist)
        orphans = self.conn.execute('''
            SELECT COUNT(DISTINCT c.cited_urn) 
            FROM citations c
            LEFT JOIN laws l ON l.urn = c.cited_urn
            WHERE l.urn IS NULL
        ''').fetchone()[0]
        report['checks']['citation_integrity'] = True
        report['orphan_citations'] = orphans
        
        # Check: missing text
        no_text = self.conn.execute(
            "SELECT COUNT(*) FROM laws WHERE text IS NULL OR text = ''"
        ).fetchone()[0]
        report['checks']['text_present'] = no_text == 0
        if no_text:
            report['issues'].append(f'{no_text} laws with no text')
        
        total = self.conn.execute('SELECT COUNT(*) FROM laws').fetchone()[0]
        report['total_laws'] = total
        report['status'] = 'passed' if not report['issues'] else 'warnings'
        
        return report
    
    def close(self):
        """Close database connection."""
        self.conn.close()
        logger.info("Database connection closed")


if __name__ == '__main__':
    db = LawDatabase()
    stats = db.get_statistics()
    print(f"Database loaded: {stats.get('total_laws', 0)} laws")
    
    # Run validation
    report = db.validate_data()
    print(f"Validation: {report['status']}")
    for issue in report.get('issues', []):
        print(f"  - {issue}")
    
    db.close()
