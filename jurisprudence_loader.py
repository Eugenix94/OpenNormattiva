#!/usr/bin/env python3
"""
Jurisprudence data loader for Constitutional Court (Corte Costituzionale) decisions.
Supports multiple sources: Normattiva API, JSON files, or direct input.
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.lab_db import LabDatabase
from normattiva_api_client import NormattivaAPI

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class JurisprudenceLoader:
    """Load Constitutional Court decisions into lab database."""
    
    def __init__(self, db_path: str = 'data/laws.db'):
        self.db = LabDatabase(db_path)
        self.api = NormattivaAPI()
    
    def load_from_normattiva_api(self, year_start: int = 1948, year_end: int = 2026) -> int:
        """
        Load sentenze from Normattiva API.
        
        The Normattiva API has a collection "Sentenze della Corte Costituzionale"
        Returns count of loaded sentenze.
        """
        loaded = 0
        
        try:
            logger.info(f"Fetching Constitutional Court decisions from Normattiva API ({year_start}-{year_end})...")
            
            # Normattiva API search endpoint for Constitutional Court
            # Collection ID: "Sentenze della Corte Costituzionale"
            collection = "Sentenze della Corte Costituzionale"
            
            # We would call the API here, but for now provide structure
            # In real deployment, extend normattiva_api_client.py with sentenze support
            
            logger.info(f"Loaded {loaded} sentenze from Normattiva API")
            return loaded
            
        except Exception as e:
            logger.error(f"Error loading from Normattiva API: {e}")
            return loaded
    
    def load_from_json_file(self, json_file: str) -> int:
        """Load sentenze from JSON file."""
        json_path = Path(json_file)
        
        if not json_path.exists():
            logger.warning(f"JSON file not found: {json_file}")
            return 0
        
        loaded = 0
        
        try:
            logger.info(f"Loading sentenze from {json_file}...")
            
            with open(json_path) as f:
                data = json.load(f)
            
            # Handle both list and dict formats
            sentenze = data if isinstance(data, list) else data.get('sentenze', [])
            
            for sentenza in sentenze:
                sentenza_id = self.db.insert_sentenza(sentenza)
                
                if sentenza_id:
                    # Insert citations if available
                    citations = sentenza.get('citations', [])
                    if citations:
                        self.db.insert_sentenza_citations(sentenza_id, citations)
                    
                    # Insert topics if available
                    topics = sentenza.get('topics', [])
                    for topic in topics:
                        self.db.conn.execute('''
                            INSERT OR IGNORE INTO sentenza_topics
                            (sentenza_id, topic, relevance_score)
                            VALUES (?, ?, ?)
                        ''', (sentenza_id, topic.get('topic'), topic.get('score')))
                    
                    loaded += 1
            
            self.db.conn.commit()
            logger.info(f"Loaded {loaded} sentenze from JSON")
            return loaded
            
        except Exception as e:
            logger.error(f"Error loading from JSON: {e}")
            return loaded
    
    def load_from_dict_list(self, sentenze_list: List[Dict]) -> int:
        """Load sentenze from list of dictionaries."""
        loaded = 0
        
        try:
            for sentenza in sentenze_list:
                sentenza_id = self.db.insert_sentenza(sentenza)
                
                if sentenza_id:
                    citations = sentenza.get('citations', [])
                    if citations:
                        self.db.insert_sentenza_citations(sentenza_id, citations)
                    loaded += 1
            
            self.db.conn.commit()
            logger.info(f"Loaded {loaded} sentenze from list")
            return loaded
            
        except Exception as e:
            logger.error(f"Error loading from list: {e}")
            return loaded
    
    def create_sample_data(self):
        """Create sample Constitutional Court data for demo/testing."""
        sample_sentenze = [
            {
                'decision_id': 'sent_cc_1_1956',
                'court': 'Corte Costituzionale',
                'decision_date': '1956-06-01',
                'decision_year': 1956,
                'decision_type': 'sentenza',
                'number': '1',
                'urn': 'urn:nir:corte.costituzionale:sentenza:1956-06-01;1',
                'title': 'Sentenza n. 1/1956 - Diritto di grazia e revisione',
                'summary': 'Decisione su questione di legittimità costituzionale sollevata...',
                'citations': [
                    {
                        'cited_urn': 'urn:nir:stato:costituzione:1947-12-27#art89',
                        'cited_title': 'Costituzione art. 89',
                        'citation_type': 'explicit'
                    }
                ],
                'topics': [
                    {'topic': 'Diritti fondamentali', 'score': 0.9},
                    {'topic': 'Separazione dei poteri', 'score': 0.7}
                ]
            },
            {
                'decision_id': 'sent_cc_348_1957',
                'court': 'Corte Costituzionale',
                'decision_date': '1957-12-21',
                'decision_year': 1957,
                'decision_type': 'sentenza',
                'number': '348',
                'urn': 'urn:nir:corte.costituzionale:sentenza:1957-12-21;348',
                'title': 'Sentenza n. 348/1957 - Liberta personale',
                'summary': 'La Corte ritiene necessario specificare che la libertà personale...',
                'citations': [
                    {
                        'cited_urn': 'urn:nir:stato:costituzione:1947-12-27#art13',
                        'cited_title': 'Costituzione art. 13',
                        'citation_type': 'explicit'
                    },
                    {
                        'cited_urn': 'urn:nir:stato:legge:1930-10-19;1398',
                        'cited_title': 'Codice Penale',
                        'citation_type': 'explicit'
                    }
                ]
            }
        ]
        
        return self.load_from_dict_list(sample_sentenze)
    
    def get_stats(self) -> Dict:
        """Get jurisprudence statistics."""
        return self.db.get_stats()
    
    def close(self):
        """Close database."""
        self.db.close()


if __name__ == '__main__':
    # Test: Load sample data
    loader = JurisprudenceLoader()
    
    count = loader.create_sample_data()
    logger.info(f"Sample data loaded: {count} sentenze")
    
    stats = loader.get_stats()
    logger.info(f"Database stats: {stats}")
    
    loader.close()
