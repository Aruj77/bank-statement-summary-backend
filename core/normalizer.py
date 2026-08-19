import re
from decimal import Decimal, InvalidOperation
from typing import Optional
from datetime import datetime


def parse_decimal(val_str: Optional[str]) -> Decimal:
    if not val_str:
        return Decimal("0.00")
    clean = re.sub(r"[^\d.-]", "", str(val_str).replace(",", "").strip())
    try:
        return Decimal(clean)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def parse_date(date_str: Optional[str]) -> str:
    if not date_str:
        return ""
    clean = date_str.strip()

    formats = [
        "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y",
        "%d %b %Y", "%d-%b-%Y", "%d %B %Y",
        "%Y-%m-%d", "%Y/%m/%d"
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(clean, fmt)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue
    return clean


def clean_description(desc: Optional[str]) -> str:
    if not desc:
        return ""
    # Strip unnecessary spaces and repeating non-alphanumeric noise
    cleaned = re.sub(r"\s+", " ", desc)
    cleaned = re.sub(r"^[^\w\d]+|[^\w\d]+$", "", cleaned)
    return cleaned.strip()