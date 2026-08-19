import re
from typing import List, Tuple

HEADER_PATTERNS = [
    re.compile(r"(date|txn\s*date|value\s*date)", re.I),
    re.compile(r"(narration|description|particulars|remarks)", re.I),
    re.compile(r"(debit|withdrawal|dr|credit|deposit|cr|amount)", re.I),
    re.compile(r"(balance|closing\s*bal)", re.I),
]

DATE_ROW_PATTERN = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})\b"
)


def mask_sensitive_pii(text: str) -> str:
    # Mask Account Numbers (>6 consecutive digits)
    text = re.sub(r"\b\d{7,18}\b", "XXXX-ACC-MASKED", text)
    # Mask Indian PAN numbers
    text = re.sub(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b", "XXXXX0000X", text)
    # Mask Mobile numbers
    text = re.sub(r"\b[6-9]\d{9}\b", "XXXXXX9999", text)
    # Mask Email addresses
    text = re.sub(r"[\w\.-]+@[\w\.-]+\.\w+", "user@masked-domain.com", text)
    return text


def build_ai_table_sample(
    pages_text: List[str], pages_layout: List[str]
) -> Tuple[str, bool]:
    """Scans the first 2 pages, locates the transaction table header,

    extracts the header + 4-8 subsequent rows, and masks PII.
    """
    for use_layout, page_list in [(False, pages_text), (True, pages_layout)]:
        if not page_list:
            continue

        for page in page_list[:2]:
            lines = [line.strip() for line in page.split("\n") if line.strip()]
            for idx, line in enumerate(lines):
                matches = sum(
                    1 for pattern in HEADER_PATTERNS if pattern.search(line)
                )
                if matches >= 3:
                    # Found candidate header row
                    sample_slice = lines[max(0, idx) : min(len(lines), idx + 25)]

                    # Verify that at least 2 date rows follow
                    date_count = sum(
                        1
                        for row in sample_slice[1:]
                        if DATE_ROW_PATTERN.search(row)
                    )
                    if date_count >= 1:
                        sample_text = "\n".join(sample_slice)
                        return mask_sensitive_pii(sample_text), use_layout

    # Fallback: Top 30 lines of page 1
    fallback_lines = (
        pages_text[0].split("\n")[:30] if pages_text else ["No text available"]
    )
    return mask_sensitive_pii("\n".join(fallback_lines)), False