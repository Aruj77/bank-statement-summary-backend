import re
from typing import List, Dict, Any, Optional
from core.normalizer import clean_description, parse_date

DATE_REGEX = re.compile(
    r"(?:^|\s)(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})\b"
)
AMOUNT_PATTERN = r"-?[\d,]+\.?\d*"

HEADER_FOOTER_REGEX = re.compile(
    r"(statement\s+of\s+account|for\s+period|date\s+amount\s+type|date\s*instrument\s*id|generated\s+through|unless\s+constituent|please\s+do\s+not\s+accept|abbreviations\s+are|date:\s*\d{1,2}/\d{1,2}/\d{2,4}|page\s*\d+\s+of|page\s*\d+)",
    re.I,
)

# Pattern 1: Date  [₹]Amount  (DEBIT|CREDIT|DR|CR)  [Instrument No]  [₹]Balance  Remarks
PATTERN_FORMAT_A = re.compile(
    rf"(?:^|\s)(\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}})\s+[₹Rs.\s]*({AMOUNT_PATTERN})\s+(DEBIT|CREDIT|DR|CR)\s+(?:[A-Za-z0-9_/-]+\s+)?[₹Rs.\s]*({AMOUNT_PATTERN})\s*(.*)$",
    re.I,
)

# Pattern 2: Date  [Instrument ID]  Amount  (CR|DR|DEBIT|CREDIT)  Balance  Remarks
# Handles: 06/05/2025 396063 300000.0 DR -247630.13 TO SELF
PATTERN_FORMAT_B = re.compile(
    rf"(?:^|\s)(\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}})\s+(?:[A-Za-z0-9_/-]+\s+)?([₹Rs.\s]*{AMOUNT_PATTERN})\s+(CR|DR|DEBIT|CREDIT)\s+[₹Rs.\s]*({AMOUNT_PATTERN})\s*(.*)$",
    re.I,
)

# Pattern 3: Date  Remarks  Amount(Dr/Cr)  Balance(Dr/Cr)
PATTERN_FORMAT_C = re.compile(
    rf"(?:^|\s)(\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}|\d{{1,2}}\s+[A-Za-z]{{3}}\s+\d{{2,4}})\s+(.*?)\s*[₹Rs.\s]*({AMOUNT_PATTERN})\s*(?:\((Dr|Cr)\)|(DR|CR|DEBIT|CREDIT))\s+[₹Rs.\s]*({AMOUNT_PATTERN})\s*(?:\((Dr|Cr)\)|(DR|CR|DEBIT|CREDIT))?\s*(.*)$",
    re.I,
)


def _safe_float(val_str: str) -> float:
    """Safely converts string to float preserving negative signs."""
    if not val_str:
        return 0.0
    clean = str(val_str).replace(",", "").replace("₹", "").replace("Rs.", "").strip()
    is_neg = clean.startswith("-") or "(-" in clean
    num_part = re.sub(r"[^\d.]", "", clean)
    if not num_part:
        return 0.0
    val = float(num_part)
    return -val if is_neg else val


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

        # Match Format A: Date -> Amount -> Type -> Instrument -> Balance -> Remarks
        m_a = PATTERN_FORMAT_A.search(line_str)
        if m_a:
            if curr_txn:
                raw_txns.append(curr_txn)

            date_str, amt_str, flag_str, bal_str, rem_str = m_a.groups()
            flag = flag_str.upper()
            amt = abs(_safe_float(amt_str))
            bal = _safe_float(bal_str)
            is_cr = flag in ("CR", "CREDIT")

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

        # Match Format B: Date -> [Instrument] -> Amount -> Type -> Balance -> Remarks
        m_b = PATTERN_FORMAT_B.search(line_str)
        if m_b:
            if curr_txn:
                raw_txns.append(curr_txn)

            date_str, amt_str, flag_str, bal_str, rem_str = m_b.groups()
            flag = flag_str.upper()
            amt = abs(_safe_float(amt_str))
            bal = _safe_float(bal_str)
            is_cr = flag in ("CR", "CREDIT")

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

        # Match Format C: Date -> Remarks -> Amount(Dr/Cr) -> Balance(Dr/Cr)
        m_c = PATTERN_FORMAT_C.search(line_str)
        if m_c:
            if curr_txn:
                raw_txns.append(curr_txn)

            date_str, prefix_desc, amt_str, f1, f2, bal_str, f3, f4, suffix_desc = m_c.groups()
            flag = (f1 or f2 or f3 or f4 or "DR").upper()
            amt = abs(_safe_float(amt_str))
            bal = _safe_float(bal_str)
            is_cr = flag in ("CR", "CREDIT")

            full_desc = f"{prefix_desc.strip()} {suffix_desc.strip()}".strip()
            full_desc = re.sub(r"\(?(Dr|Cr|DR|CR|DEBIT|CREDIT)\)?$", "", full_desc, flags=re.I).strip()

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

        # Multiline remarks continuation
        if curr_txn and not DATE_REGEX.search(line_str):
            if not HEADER_FOOTER_REGEX.search(line_str):
                cleaned_sub = clean_description(line_str)
                if cleaned_sub and len(cleaned_sub) > 1 and not re.match(r"^[-₹Rs.\d,]+\.?\d*\s*\(?(?:Cr|Dr|CR|DR|DEBIT|CREDIT)?\)?\.?$", cleaned_sub, re.I):
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