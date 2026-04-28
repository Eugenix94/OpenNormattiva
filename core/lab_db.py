#!/usr/bin/env python3
"""
Enhanced lab database with jurisprudence support.
Extends core/db.py with tables for Constitutional Court decisions.
"""

import sqlite3
from pathlib import Path
from typing import List, Dict, Optional
import json
import logging

logger = logging.getLogger(__name__)


class LabDatabase:
    """Lab database with support for normattiva laws + constitutional court decisions."""

    def __init__(self, db_path: str = 'data/laws.db'):
        self.db_path = Path(db_path)
        self.conn = None
        self.init_db()

    def init_db(self):
        """Initialize or open existing database with law + jurisprudence schema."""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        
        # Keep existing normattiva tables untouched
        # Add jurisprudence tables
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS sentenze (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT UNIQUE NOT NULL,
                court TEXT NOT NULL,  -- "Corte Costituzionale", etc.
                decision_date DATE NOT NULL,
                decision_year INTEGER NOT NULL,
                decision_type TEXT,  -- "sentenza", "ordinanza", etc.
                number TEXT NOT NULL,  -- Decision number
                urn TEXT UNIQUE,  -- Standard URN format if applicable
                title TEXT,
                summary TEXT,
                full_text TEXT,
                inserted_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS sentenza_citations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sentenza_id INTEGER NOT NULL,
                cited_urn TEXT,  -- URN of cited law
                cited_title TEXT,
                citation_type TEXT,  -- "explicit", "implicit", etc.
                citation_context TEXT,  -- snippet of context
                FOREIGN KEY (sentenza_id) REFERENCES sentenze(id)
            )
        ''')
        
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS sentenza_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sentenza_id INTEGER NOT NULL,
                topic TEXT NOT NULL,  -- constitutional principle, etc.
                relevance_score REAL,
                FOREIGN KEY (sentenza_id) REFERENCES sentenze(id)
            )
        ''')
        
        # Cross-reference table: laws cited by jurisprudence
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS law_jurisprudence_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                law_urn TEXT NOT NULL,
                sentenza_id INTEGER NOT NULL,
                link_type TEXT,  -- "cited_by", "overridden_by", "clarified_by", etc.
                UNIQUE(law_urn, sentenza_id, link_type),
                FOREIGN KEY (sentenza_id) REFERENCES sentenze(id)
            )
        ''')
        
        # Create indexes
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_sentenze_date ON sentenze(decision_year, decision_date)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_sentenza_citations_urn ON sentenza_citations(cited_urn)')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_law_jur_links_urn ON law_jurisprudence_links(law_urn)')
        
        self.conn.commit()
        logger.info(f"Lab database initialized: {self.db_path}")

    def insert_sentenza(self, sentenza: Dict) -> int:
        """Insert or update a Constitutional Court decision."""
        try:
            self.conn.execute('''
                INSERT OR REPLACE INTO sentenze 
                (decision_id, court, decision_date, decision_year, decision_type, number, urn, title, summary, full_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                sentenza['decision_id'],
                sentenza.get('court', 'Corte Costituzionale'),
                sentenza['decision_date'],
                sentenza['decision_year'],
                sentenza.get('decision_type'),
                sentenza['number'],
                sentenza.get('urn'),
                sentenza.get('title'),
                sentenza.get('summary'),
                sentenza.get('full_text'),
            ))
            self.conn.commit()
            return self.conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        except Exception as e:
            logger.error(f"Error inserting sentenza: {e}")
            return None

    def insert_sentenza_citations(self, sentenza_id: int, citations: List[Dict]):
        """Insert citations from a sentenza to laws."""
        try:
            for cit in citations:
                self.conn.execute('''
                    INSERT OR IGNORE INTO sentenza_citations
                    (sentenza_id, cited_urn, cited_title, citation_type, citation_context)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    sentenza_id,
                    cit.get('cited_urn'),
                    cit.get('cited_title'),
                    cit.get('citation_type'),
                    cit.get('citation_context'),
                ))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error inserting sentenza citations: {e}")

    def link_law_to_jurisprudence(self, law_urn: str, sentenza_id: int, link_type: str = 'cited_by'):
        """Link a law to jurisprudence that cites or affects it."""
        try:
            self.conn.execute('''
                INSERT OR IGNORE INTO law_jurisprudence_links
                (law_urn, sentenza_id, link_type)
                VALUES (?, ?, ?)
            ''', (law_urn, sentenza_id, link_type))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error linking law to jurisprudence: {e}")

    def search_sentenze(self, query: str, limit: int = 100) -> List[Dict]:
        """Full-text search in sentenze."""
        try:
            results = self.conn.execute('''
                SELECT s.id, s.decision_id, s.court, s.decision_date, s.decision_year, 
                       s.decision_type, s.number, s.title, s.summary
                FROM sentenze s
                WHERE s.title LIKE ? OR s.summary LIKE ? OR s.full_text LIKE ?
                ORDER BY s.decision_date DESC
                LIMIT ?
            ''', (f'%{query}%', f'%{query}%', f'%{query}%', limit)).fetchall()
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Error searching sentenze: {e}")
            return []

    def get_sentenze_by_year(self, year: int) -> List[Dict]:
        """Get all sentenze from a specific year."""
        try:
            results = self.conn.execute('''
                SELECT * FROM sentenze
                WHERE decision_year = ?
                ORDER BY decision_date DESC
            ''', (year,)).fetchall()
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Error getting sentenze by year: {e}")
            return []

    def get_sentenze_for_law(self, law_urn: str) -> List[Dict]:
        """Get all sentenze that cite or affect a specific law."""
        try:
            results = self.conn.execute('''
                SELECT DISTINCT s.* FROM sentenze s
                JOIN sentenza_citations sc ON s.id = sc.sentenza_id
                WHERE sc.cited_urn = ?
                ORDER BY s.decision_date DESC
            ''', (law_urn,)).fetchall()
            return [dict(r) for r in results]
        except Exception as e:
            logger.error(f"Error getting sentenze for law: {e}")
            return []

    def get_sentenza_details(self, sentenza_id: int) -> Optional[Dict]:
        """Get full sentenza with its citations and topics."""
        try:
            sentenza = self.conn.execute(
                'SELECT * FROM sentenze WHERE id = ?', (sentenza_id,)
            ).fetchone()
            
            if not sentenza:
                return None
            
            result = dict(sentenza)
            
            # Get citations
            citations = self.conn.execute(
                'SELECT * FROM sentenza_citations WHERE sentenza_id = ?', (sentenza_id,)
            ).fetchall()
            result['citations'] = [dict(c) for c in citations]
            
            # Get topics
            topics = self.conn.execute(
                'SELECT * FROM sentenza_topics WHERE sentenza_id = ?', (sentenza_id,)
            ).fetchall()
            result['topics'] = [dict(t) for t in topics]
            
            return result
        except Exception as e:
            logger.error(f"Error getting sentenza details: {e}")
            return None

    def get_stats(self) -> Dict:
        """Get jurisprudence statistics."""
        try:
            sentenze_count = self.conn.execute('SELECT COUNT(*) FROM sentenze').fetchone()[0]
            year_range = self.conn.execute(
                'SELECT MIN(decision_year), MAX(decision_year) FROM sentenze'
            ).fetchone()
            
            citations_count = self.conn.execute(
                'SELECT COUNT(*) FROM sentenza_citations'
            ).fetchone()[0]
            
            topics_count = self.conn.execute(
                'SELECT COUNT(DISTINCT topic) FROM sentenza_topics'
            ).fetchone()[0]
            
            return {
                'sentenze_count': sentenze_count,
                'year_min': year_range[0],
                'year_max': year_range[1],
                'citations_count': citations_count,
                'topics_count': topics_count,
            }
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return {}

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
