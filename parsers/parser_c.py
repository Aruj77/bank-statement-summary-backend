import re
from typing import List, Dict, Any, Optional
from core.normalizer import clean_description, parse_date

DATE_REGEX = re.compile(
    r"(?:^|\s)(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})\b"
)
AMOUNT_PATTERN = r"[\d,]+\.\d+"

HEADER_FOOTER_REGEX = re.compile(
    r"(statement\s+of\s+account|for\s+period|date\s+amount\s+type|date\s*instrument\s*id|generated\s+through|unless\s+constituent|please\s+do\s+not\s+accept|abbreviations\s+are|date:\s*\d{1,2}/\d{1,2}/\d{2,4}|page\s*\d+\s+of|page\s*\d+|id\s*remarks\s*amount)",
    re.I,
)


def _safe_float(val_str: str) -> float:
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

    blocks = []
    curr_block = []
    ignore_until_date = False

    # 1. Group Multiline Wrap Rows into Blocks by matching Date Anchors
    for line in all_lines:
        line_str = line.strip()
        if not line_str:
            continue

        # Page breaks and headers signal the end of a transaction block.
        # Set ignore flag to flush the HDFC customer metadata block.
        if re.search(r"^(page\s*no|statement\s*summary|opening\s*balance|closing\s*balance|generated\s*on|this\s*is\s*a\s*computer|savings\s+account|date\s*transaction)", line_str, re.I):
            if curr_block:
                blocks.append(" ".join(curr_block))
                curr_block = []
            ignore_until_date = True
            continue

        if HEADER_FOOTER_REGEX.search(line_str):
            continue

        if re.match(r"^(date|particulars|chq|withdrawals|deposits|balance|remarks|amount|type|instrument)\b", line_str, re.I):
            continue

        date_match = DATE_REGEX.search(line_str)
        if date_match and date_match.start() <= 5:
            # A new valid date resets the ignore block
            ignore_until_date = False
            if curr_block:
                blocks.append(" ".join(curr_block))
            curr_block = [line_str]
        elif not ignore_until_date and curr_block:
            curr_block.append(line_str)

    if curr_block:
        blocks.append(" ".join(curr_block))

    raw_txns = []

    # Format A: Date  [₹]Amount  (DEBIT|CREDIT|DR|CR)  [Instrument No]  [₹]Balance  Remarks
    PATTERN_FORMAT_A = re.compile(
        rf"(?:^|\s)(\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}})\s+[₹Rs.\s]*({AMOUNT_PATTERN})\s+(DEBIT|CREDIT|DR|CR)\s+(?:[A-Za-z0-9_/-]+\s+)?[₹Rs.\s]*({AMOUNT_PATTERN})\s*(.*)$",
        re.I,
    )
    # Format B: Date  [Instrument ID]  Amount  (CR|DR|DEBIT|CREDIT)  Balance  Remarks
    PATTERN_FORMAT_B = re.compile(
        rf"(?:^|\s)(\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}})\s+(?:[A-Za-z0-9_/-]+\s+)?([₹Rs.\s]*{AMOUNT_PATTERN})\s+(CR|DR|DEBIT|CREDIT)\s+[₹Rs.\s]*({AMOUNT_PATTERN})\s*(.*)$",
        re.I,
    )
    # Format C: Date  Remarks  Amount(Dr/Cr)  Balance(Dr/Cr)
    PATTERN_FORMAT_C = re.compile(
        rf"(?:^|\s)(\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}|\d{{1,2}}\s+[A-Za-z]{{3}}\s+\d{{2,4}})\s+(.*?)\s*[₹Rs.\s]*({AMOUNT_PATTERN})\s*\(?(Dr|Cr|DR|CR|DEBIT|CREDIT)\)?\s+[₹Rs.\s]*({AMOUNT_PATTERN})\s*\(?(Dr|Cr|DR|CR|DEBIT|CREDIT)?\)?\s*(.*)$",
        re.I,
    )

    # 2. Extract Data using combined block regex
    for block in blocks:
        m_a = PATTERN_FORMAT_A.search(block)
        m_b = PATTERN_FORMAT_B.search(block)
        m_c = PATTERN_FORMAT_C.search(block)

        if m_a:
            date_str, amt_str, flag_str, bal_str, rem_str = m_a.groups()
            flag = flag_str.upper()
            amt = abs(_safe_float(amt_str))
            bal = _safe_float(bal_str)
            is_cr = flag in ("CR", "CREDIT")

            raw_txns.append({
                "date": parse_date(date_str),
                "valueDate": parse_date(date_str),
                "txnAmount": amt,
                "amount": amt,
                "withdrawal": 0.0 if is_cr else amt,
                "deposit": amt if is_cr else 0.0,
                "balance": bal,
                "type": "CREDIT" if is_cr else "DEBIT",
                "narration_parts": [rem_str.strip()] if rem_str.strip() else [],
            })
        elif m_b:
            date_str, amt_str, flag_str, bal_str, rem_str = m_b.groups()
            flag = flag_str.upper()
            amt = abs(_safe_float(amt_str))
            bal = _safe_float(bal_str)
            is_cr = flag in ("CR", "CREDIT")

            raw_txns.append({
                "date": parse_date(date_str),
                "valueDate": parse_date(date_str),
                "txnAmount": amt,
                "amount": amt,
                "withdrawal": 0.0 if is_cr else amt,
                "deposit": amt if is_cr else 0.0,
                "balance": bal,
                "type": "CREDIT" if is_cr else "DEBIT",
                "narration_parts": [rem_str.strip()] if rem_str.strip() else [],
            })
        elif m_c:
            date_str, prefix_desc, amt_str, flag_str1, bal_str, flag_str2, suffix_desc = m_c.groups()
            flag = (flag_str1 or flag_str2 or "DR").upper()
            amt = abs(_safe_float(amt_str))
            bal = _safe_float(bal_str)
            is_cr = flag in ("CR", "CREDIT")

            full_desc = f"{prefix_desc.strip()} {suffix_desc.strip()}".strip()
            full_desc = re.sub(r"\(?(Dr|Cr|DR|CR|DEBIT|CREDIT)\)?$", "", full_desc, flags=re.I).strip()

            raw_txns.append({
                "date": parse_date(date_str),
                "valueDate": parse_date(date_str),
                "txnAmount": amt,
                "amount": amt,
                "withdrawal": 0.0 if is_cr else amt,
                "deposit": amt if is_cr else 0.0,
                "balance": bal,
                "type": "CREDIT" if is_cr else "DEBIT",
                "narration_parts": [full_desc] if full_desc else [],
            })

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