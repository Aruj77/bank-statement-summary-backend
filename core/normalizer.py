import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from core.date_normalizer import parse_date_intelligent


def parse_decimal(val_str: Optional[str]) -> Decimal:
    """Safely extracts decimal amounts while removing currency symbols and commas."""
    if not val_str:
        return Decimal("0.00")
    clean = re.sub(r"[^\d.-]", "", str(val_str).replace(",", "").replace("₹", "").replace("Rs.", "").strip())
    try:
        return Decimal(clean)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def parse_date(date_str: Optional[str], default_format: str = "DMY") -> str:
    """Intelligently detects format and converts to DD-Mon-YYYY (e.g. 24-Oct-2024)."""
    return parse_date_intelligent(date_str, output_format="%d-%b-%Y", default_format=default_format)


def clean_description(desc: Optional[str]) -> str:
    """Strips table artifact keywords, page numbers, leaked disclaimers, and normalizes spaces."""
    if not desc:
        return "—"
    text = str(desc)

    text = re.sub(r"\b(DEBIT|CREDIT)\s+NA\b", "", text, flags=re.I)
    text = re.sub(r"\bNA\s*[₹Rs\.]*", "", text, flags=re.I)
    text = re.sub(r"Page\s*\d+(\s*of\s*\d+)?", "", text, flags=re.I)
    text = re.sub(r"\bNP\d{8,}\b", "", text, flags=re.I)

    text = re.split(
        r"(?:customer\s+to\s+inform|computer\s+generated|punjab\s+national\s+bank\s+is\s+integrated|empower\s+your\s+digital)",
        text,
        flags=re.I,
    )[0]

    text = re.sub(r"\s*/\s*", "/", text)

    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"^[^\w\d/]+|[^\w\d/]+$", "", cleaned).strip()

    return cleaned if cleaned else "—"