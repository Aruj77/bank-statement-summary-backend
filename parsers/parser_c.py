import re
from typing import List, Dict, Any, Optional
from core.normalizer import parse_decimal, clean_description, parse_date

DATE_REGEX = re.compile(
    r"(?:^|\s)(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})\b"
)
AMOUNT_PATTERN = r"[\d,]+\.?\d*"

HEADER_FOOTER_REGEX = re.compile(
    r"(statement\s+of\s+account|for\s+period|date\s*instrument\s*id|generated\s+through|unless\s+constituent|please\s+do\s+not\s+accept|abbreviations\s+are|date:\s*\d{1,2}/\d{1,2}/\d{2,4}|page\s*\d+)",
    re.I,
)

# Robust Regex matching Date + Description + Amount(Dr/Cr) + Balance(Dr/Cr)
ROW_PATTERN = re.compile(
    r"(?:^|\s)(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})\s+(.*?)\s*([\d,]+\.?\d*)\s*(?:\((Dr|Cr)\)|(DR|CR))\s+([\d,]+\.?\d*)\s*(?:\((Dr|Cr)\)|(DR|CR))?\s*(.*)$",
    re.I,
)

TYPE1_PATTERN = re.compile(
    r"(?:^|\s)(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})\s+(?:[A-Za-z0-9_/-]+\s+)?([\d,]+\.?\d*)\s+(CR|DR)\s+([\d,]+\.?\d*)\s*(.*)$",
    re.I,
)


def parse_parser_c(
    pages_text: List[str],
    pages_layout: List[str] = None,
    metadata: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    lines_source = pages_text if pages_text else (pages_layout or [])
    all_lines = []
    for page in lines_source:
        all_lines.extend(page.split("\n"))

    raw_txns = []
    curr_txn: Optional[Dict[str, Any]] = None

    for line in all_lines:
        line_str = line.strip()
        if not line_str or HEADER_FOOTER_REGEX.search(line_str):
            continue

        # Check for Type 1: Date [Instrument ID] Amount Type(CR/DR) Balance Remarks
        m_type1 = TYPE1_PATTERN.search(line_str)
        if m_type1:
            if curr_txn:
                raw_txns.append(curr_txn)

            date_str, amt_str, flag_str, bal_str, rem_str = m_type1.groups()
            flag = flag_str.upper()
            amt = float(parse_decimal(amt_str))
            bal = float(parse_decimal(bal_str))
            is_cr = flag == "CR"

            curr_txn = {
                "date": parse_date(date_str),
                "valueDate": parse_date(date_str),
                "txnAmount": amt,
                "amount": amt,
                "withdrawal": 0.0 if is_cr else amt,
                "deposit": amt if is_cr else 0.0,
                "balance": bal,
                "type": "CREDIT" if is_cr else "DEBIT",
                "narration_parts": [rem_str.strip()] if rem_str.strip() else [],
            }
            continue

        # Check for Type 2: Date Remarks Amount(Dr/Cr) Balance(Dr/Cr)
        m_type2 = ROW_PATTERN.search(line_str)
        if m_type2:
            if curr_txn:
                raw_txns.append(curr_txn)

            date_str, prefix_desc, amt_str, f1, f2, bal_str, f3, suffix_desc = m_type2.groups()
            flag = (f1 or f2 or "DR").upper()
            amt = float(parse_decimal(amt_str))
            bal = float(parse_decimal(bal_str))
            is_cr = flag == "CR"

            full_desc = f"{prefix_desc.strip()} {suffix_desc.strip()}".strip()
            full_desc = re.sub(r"\(?(Dr|Cr|DR|CR)\)?$", "", full_desc, flags=re.I).strip()

            curr_txn = {
                "date": parse_date(date_str),
                "valueDate": parse_date(date_str),
                "txnAmount": amt,
                "amount": amt,
                "withdrawal": 0.0 if is_cr else amt,
                "deposit": amt if is_cr else 0.0,
                "balance": bal,
                "type": "CREDIT" if is_cr else "DEBIT",
                "narration_parts": [full_desc] if full_desc else [],
            }
            continue

        # Multiline description continuation: Append only text without dates or stray balance lines
        if curr_txn and not DATE_REGEX.search(line_str):
            if not HEADER_FOOTER_REGEX.search(line_str):
                cleaned_sub = clean_description(line_str)
                if cleaned_sub and len(cleaned_sub) > 1 and not re.match(r"^[\d,]+\.?\d*\s*\(?(?:Cr|Dr)?\)?\.?$", cleaned_sub, re.I):
                    curr_txn["narration_parts"].append(cleaned_sub)

    if curr_txn:
        raw_txns.append(curr_txn)

    transactions = []
    for idx, t in enumerate(raw_txns):
        full_desc = clean_description(" ".join(t["narration_parts"]))
        transactions.append({
            "_index": idx,
            "sNo": idx + 1,
            "date": t["date"],
            "valueDate": t["valueDate"],
            "remarks": full_desc if full_desc else "—",
            "description": full_desc if full_desc else "—",
            "txnAmount": t["txnAmount"],
            "amount": t["amount"],
            "withdrawal": t["withdrawal"],
            "deposit": t["deposit"],
            "balance": t["balance"],
            "type": t["type"],
        })

    return transactions