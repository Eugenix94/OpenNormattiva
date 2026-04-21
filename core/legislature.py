#!/usr/bin/env python3
"""
Legislature Metadata Extraction for Jurisprudential Evolution

Extracts and tracks:
- Legislative sessions (government term)
- Parliament/House information
- Enactment chronology
- Amendments by legislature
- Jurisprudential trends over time
"""

import re
from datetime import datetime
from typing import Dict, Optional, List

class LegislatureMetadata:
    """Extract and manage legislature information from laws."""
    
    # Italian legislatures (sessions of parliament)
    ITALIAN_LEGISLATURES = {
        1: (1948, 1953),
        2: (1953, 1958),
        3: (1958, 1963),
        4: (1963, 1968),
        5: (1968, 1972),
        6: (1972, 1976),
        7: (1976, 1979),
        8: (1979, 1983),
        9: (1983, 1987),
        10: (1987, 1992),
        11: (1992, 1994),
        12: (1994, 1996),
        13: (1996, 2001),
        14: (2001, 2006),
        15: (2006, 2008),
        16: (2008, 2013),
        17: (2013, 2018),
        18: (2018, 2022),
        19: (2022, 2027),
    }
    
    # Government cabinets (Governi)
    ITALIAN_GOVERNMENTS = {
        'De Gasperi': (1948, 1953),
        'Alcide De Gasperi': (1948, 1953),
        'Pella': (1953, 1954),
        'Fanfani': (1954, 1955),
        'Segni': (1955, 1957),
        'Zoli': (1957, 1958),
        'Fanfani II': (1958, 1959),
        'Segni II': (1959, 1960),
        'Tambroni': (1960, 1960),
        'Fanfani III': (1960, 1963),
        'Leone': (1963, 1968),
        'Moro': (1963, 1968),
        'Rumor': (1968, 1970),
        'Rumor II': (1970, 1972),
        'Andreotti': (1972, 1973),
        'Malfatti': (1972, 1973),
        'Rumor III': (1973, 1974),
        'Moro II': (1974, 1976),
        'Andreotti II': (1976, 1979),
        'Cossiga': (1979, 1980),
        'Cossiga II': (1980, 1981),
        'Spadolini': (1981, 1982),
        'Spadolini II': (1982, 1983),
        'Fanfani IV': (1983, 1987),
        'Craxi': (1983, 1987),
        'Andreotti III': (1989, 1992),
        'Amato': (1992, 1993),
        'Ciampi': (1993, 1994),
        'Berlusconi': (1994, 1995),
        'Berlusconi I': (1994, 1995),
        'Dini': (1995, 1996),
        'Prodi': (1996, 2000),
        'D\'Alema': (1998, 2000),
        'Amato II': (2000, 2001),
        'Berlusconi II': (2001, 2006),
        'Prodi II': (2006, 2008),
        'Berlusconi III': (2008, 2011),
        'Berlusconi IV': (2011, 2012),
        'Monti': (2011, 2012),
        'Letta': (2013, 2014),
        'Renzi': (2014, 2016),
        'Gentiloni': (2016, 2018),
        'Conte': (2018, 2021),
        'Draghi': (2021, 2022),
        'Meloni': (2022, 2024),
    }
    
    @classmethod
    def get_legislature_from_year(cls, year: int) -> Optional[int]:
        """Get parliament legislature number from year."""
        for leg_num, (start, end) in cls.ITALIAN_LEGISLATURES.items():
            if start <= year < end:
                return leg_num
        # After 2027, estimate
        if year >= 2027:
            return 20
        return None
    
    @classmethod
    def get_government_from_year(cls, year: int) -> Optional[str]:
        """Get government cabinet name from year."""
        for gov_name, (start, end) in cls.ITALIAN_GOVERNMENTS.items():
            if start <= year < end:
                return gov_name
        return None
    
    @classmethod
    def extract_from_urn(cls, urn: str) -> Dict:
        """Extract metadata from AKN URN.
        
        URN format: urn:nir:stato:legge:YYYY;NUMBER
        Example: urn:nir:stato:legge:2006;290
        """
        try:
            # Parse URN
            parts = urn.split(':')
            if len(parts) < 6:
                return {'urn': urn, 'valid_urn': False}
            
            law_type = parts[4]  # legge, decreto, etc.
            year_num = parts[5].split(';')[0]  # YYYY from YYYY;NUMBER
            number = parts[5].split(';')[1] if ';' in parts[5] else None
            
            year = int(year_num) if year_num.isdigit() else None
            
            if not year:
                return {'urn': urn, 'valid_urn': False}
            
            legislature = cls.get_legislature_from_year(year)
            government = cls.get_government_from_year(year)
            
            return {
                'urn': urn,
                'valid_urn': True,
                'law_type': law_type,
                'year': year,
                'number': number,
                'legislature': legislature,
                'legislature_range': cls.ITALIAN_LEGISLATURES.get(legislature),
                'government': government,
                'era': cls.get_era_from_year(year),
            }
        except Exception as e:
            return {'urn': urn, 'valid_urn': False, 'error': str(e)}
    
    @classmethod
    def get_era_from_year(cls, year: int) -> str:
        """Categorize year into historical era."""
        if year < 1960:
            return "Early Republic (1948-1960)"
        elif year < 1980:
            return "First Republic I (1960-1980)"
        elif year < 2000:
            return "First Republic II (1980-2000)"
        elif year < 2010:
            return "Second Republic I (2000-2010)"
        elif year < 2020:
            return "Second Republic II (2010-2020)"
        else:
            return "Recent (2020+)"
    
    @classmethod
    def get_jurisprudential_trends(cls, 
                                   laws_by_year: Dict[int, List[str]]) -> Dict:
        """Analyze trends in law production across legislatures.
        
        Args:
            laws_by_year: {year: [urns]}
        
        Returns:
            Trends analysis
        """
        trends = {}
        
        for year in sorted(laws_by_year.keys()):
            leg = cls.get_legislature_from_year(year)
            if leg not in trends:
                trends[leg] = {
                    'years': [],
                    'law_count': 0,
                    'government': None,
                }
            
            trends[leg]['years'].append(year)
            trends[leg]['law_count'] += len(laws_by_year[year])
            if not trends[leg]['government']:
                trends[leg]['government'] = cls.get_government_from_year(year)
        
        return trends
    
    @classmethod
    def format_metadata_display(cls, metadata: Dict) -> str:
        """Format metadata for display in UI."""
        if not metadata.get('valid_urn'):
            return "Invalid URN"
        
        parts = [
            f"Year: {metadata.get('year')}",
            f"Type: {metadata.get('law_type')}",
        ]
        
        if metadata.get('legislature'):
            parts.append(f"Legislature XVII: {metadata['legislature']}")
        
        if metadata.get('government'):
            parts.append(f"Government: {metadata['government']}")
        
        if metadata.get('era'):
            parts.append(f"Era: {metadata['era']}")
        
        return " | ".join(parts)
