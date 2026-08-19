from typing import List, Dict, Any
from parsers.parser_b import parse_parser_b

def parse_parser_e(pages_text: List[str], pages_layout: List[str] = None, metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    return parse_parser_b(pages_text, pages_layout, metadata)