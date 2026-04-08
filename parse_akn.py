#!/usr/bin/env python3
"""
AKN XML Parser for Normattiva

Converts Akoma Ntoso (AKN) XML to structured JSON/JSONL format.
Extracts articles, amendments, citations, metadata.

Usage:
    python parse_akn.py --input collection.zip --output laws.jsonl
"""

import json
import zipfile
import io
from pathlib import Path
from typing import Dict, List, Optional, Any
from lxml import etree
from datetime import datetime
import re
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# AKN namespaces
AKN_NS = {
    'akn': 'http://docs.oasis-open.org/legaldocml/ns/akn/3.0',
    'eli': 'http://data.europa.eu/eli/ontology#',
    'na': 'http://www.normattiva.it/eli/',
    'nrdfa': 'http://www.normattiva.it/rdfa/'
}


class AKNParser:
    """Parse Akoma Ntoso XML files from Normattiva."""

    def __init__(self):
        self.parser = etree.XMLParser(remove_blank_text=True)

    def extract_text_recursive(self, elem) -> str:
        """Extract all text from an element recursively."""
        text = []
        if elem.text:
            text.append(elem.text)
        for child in elem:
            text.append(self.extract_text_recursive(child))
            if child.tail:
                text.append(child.tail)
        return ''.join(text).strip()

    def parse_xml_file(self, xml_bytes: bytes) -> Optional[Dict[str, Any]]:
        """Parse single AKN XML file."""
        try:
            root = etree.fromstring(xml_bytes, self.parser)
            
            # Get act element
            act = root.find('.//akn:act', AKN_NS)
            if act is None:
                return None

            # Extract metadata
            meta = act.find('.//akn:meta', AKN_NS)
            ident = meta.find('.//akn:identification', AKN_NS) if meta is not None else None

            # URN (primary identifier)
            urn = None
            if ident is not None:
                urn_elem = ident.find(".//akn:FRBRalias[@name='urn:nir']", AKN_NS)
                if urn_elem is not None:
                    urn = urn_elem.get('value')

            # Title
            title = ""
            if ident is not None:
                title_elem = ident.find(".//nrdfa:span[@property='eli:title']", AKN_NS)
                if title_elem is not None:
                    title = title_elem.get('content', '').strip()

            # Date
            date_str = None
            if ident is not None:
                date_elem = ident.find(".//akn:FRBRdate", AKN_NS)
                if date_elem is not None:
                    date_str = date_elem.get('date')

            # Type (Decreto, Legge, etc.)
            doc_type = None
            if ident is not None:
                type_elem = ident.find(".//nrdfa:span[@property='eli:type_document']", AKN_NS)
                if type_elem is not None:
                    type_resource = type_elem.get('resource', '')
                    doc_type = type_resource.split('#')[-1] if '#' in type_resource else type_resource

            # Body (articles)
            body = act.find('.//akn:body', AKN_NS)
            articles = []
            if body is not None:
                for part in body.findall('.//akn:part', AKN_NS):
                    articles.extend(self._extract_articles_from_part(part))
                for article in body.findall('.//akn:article', AKN_NS):
                    articles.append(self._extract_article(article))

            # Full text
            full_text = ""
            if body is not None:
                full_text = self.extract_text_recursive(body)

            # Build result
            law = {
                'urn': urn,
                'title': title,
                'type': doc_type,
                'date': date_str,
                'text': full_text,
                'articles': articles,
                'article_count': len(articles),
                'parsed_at': datetime.now().isoformat()
            }

            return law

        except Exception as e:
            logger.error(f"Error parsing XML: {e}")
            return None

    def _extract_articles_from_part(self, part) -> List[Dict]:
        """Extract articles from a part element."""
        articles = []
        for article in part.findall('.//akn:article', AKN_NS):
            articles.append(self._extract_article(article))
        return articles

    def _extract_article(self, article_elem) -> Dict[str, Any]:
        """Extract single article."""
        eid = article_elem.get('eId', '')
        
        # Article number from heading
        heading = article_elem.find('.//akn:heading', AKN_NS)
        heading_text = heading.text if heading is not None else ""
        
        # Article content
        content = self.extract_text_recursive(article_elem)
        
        return {
            'num': heading_text.strip() if heading_text else eid,
            'eId': eid,
            'text': content
        }

    def parse_zip_file(self, zip_path: Path) -> List[Dict[str, Any]]:
        """Parse all XML files in a ZIP."""
        laws = []
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                xml_files = [f for f in zf.namelist() if f.endswith('.xml')]
                logger.info(f"Found {len(xml_files)} XML files in ZIP")
                
                for i, xml_file in enumerate(xml_files):
                    if (i + 1) % 10 == 0:
                        logger.info(f"Parsing {i+1}/{len(xml_files)}...")
                    
                    try:
                        xml_bytes = zf.read(xml_file)
                        law = self.parse_xml_file(xml_bytes)
                        if law:
                            laws.append(law)
                    except Exception as e:
                        logger.warning(f"Error parsing {xml_file}: {e}")
                        continue
        
        except Exception as e:
            logger.error(f"Error reading ZIP {zip_path}: {e}")

        logger.info(f"✓ Parsed {len(laws)} laws from ZIP")
        return laws

    def extract_citations(self, text: str) -> List[str]:
        """Extract law citations from text."""
        citations = []
        
        # Pattern: "articolo 1 della legge 123/2021"
        pattern = r"(?:articolo|art\.?)\s+(\d+)\s+(?:della|del|di|dei)\s+(?:legge|decreto|decr\.?|d\.lgs\.?|d\.l\.?)\s+(\d+/\d{2,4})"
        
        for match in re.finditer(pattern, text, re.IGNORECASE):
            citations.append(f"art.{match.group(1)} L.{match.group(2)}")
        
        # Also catch just law citations: "legge 123/2021"
        pattern2 = r"(?:legge|decreto|d\.lgs\.?|d\.l\.?)\s+(\d+/\d{2,4})"
        for match in re.finditer(pattern2, text, re.IGNORECASE):
            citations.append(f"L.{match.group(1)}")
        
        return list(set(citations))

    def enrich_with_metadata(self, law: Dict) -> Dict:
        """Add computed metadata."""
        law['citations'] = self.extract_citations(law.get('text', ''))
        law['text_length'] = len(law.get('text', ''))
        law['year'] = law.get('date', '')[:4] if law.get('date') else None
        return law

    def to_jsonl(self, laws: List[Dict], output_file: Path):
        """Write laws to JSONL file."""
        with open(output_file, 'w', encoding='utf-8') as f:
            for law in laws:
                law = self.enrich_with_metadata(law)
                f.write(json.dumps(law, ensure_ascii=False) + '\n')
        
        logger.info(f"✓ Wrote {len(laws)} laws to {output_file}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Parse Normattiva AKN XML to JSONL')
    parser.add_argument('--input', '-i', required=True, help='Input ZIP file')
    parser.add_argument('--output', '-o', default='laws.jsonl', help='Output JSONL file')
    
    args = parser.parse_args()
    
    input_path = Path(args.input)
    output_path = Path(args.output)
    
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return 1
    
    parser_obj = AKNParser()
    laws = parser_obj.parse_zip_file(input_path)
    parser_obj.to_jsonl(laws, output_path)
    
    return 0


if __name__ == '__main__':
    exit(main())
