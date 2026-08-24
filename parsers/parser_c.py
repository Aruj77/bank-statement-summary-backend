import re
from typing import List, Dict, Any, Optional
from core.normalizer import parse_decimal, clean_description, parse_date

DATE_REGEX = re.compile(
    r"(?:^|\s)(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}[\s-][A-Za-z]{3}[\s-]\d{2,4})\b"
)
AMOUNT_PATTERN = r"[\d,]+\.?\d*"

HEADER_NOISE_REGEX = re.compile(
    r"^(date|instrument\s*id|amount|type|balance|remarks|particulars|statement|page\s*\d+|generated\s+through|unless\s+constituent)",
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
        if not line_str or HEADER_NOISE_REGEX.match(line_str):
            continue

        if re.search(r"^(generated\s+through\s+pnb|unless\s+constituent|please\s+do\s+not\s+accept|abbreviations\s+are)", line_str, re.I):
            continue

        date_match = DATE_REGEX.search(line_str)

        if date_match:
            # Check for: Date ... Amount (CR|DR) Balance Remarks
            # Example: 20/08/2025 1130.0 CR 42207.6 NEFT_IN:...
            # Example 2: 17-Jul-2025 UPI/DR/... 824.82(Dr) 62868.43
            m_type1 = re.search(
                rf"(?:^|\s)(\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}})\s+(?:[A-Za-z0-9_/-]+\s+)?({AMOUNT_PATTERN})\s+(CR|DR)\s+({AMOUNT_PATTERN})\s*(.*)$",
                line_str,
                re.I,
            )
            m_type2 = re.search(
                rf"(?:^|\s)(\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}})\s+(.*?)\s*({AMOUNT_PATTERN})\s*(?:\((Dr|Cr)\)|(DR|CR))\s+({AMOUNT_PATTERN})\s*(.*)$",
                line_str,
                re.I,
            )

            if m_type1:
                if curr_txn:
                    raw_txns.append(curr_txn)

                date_str, amt_str, flag_str, bal_str, rem_str = m_type1.groups()
                flag = flag_str.upper()
                amt = float(parse_decimal(amt_str))
                bal = float(parse_decimal(bal_str))

                is_cr = flag == "CR"
                formatted_date = parse_date(date_str)

                curr_txn = {
                    "date": formatted_date,
                    "valueDate": formatted_date,
                    "txnAmount": amt,
                    "amount": amt,
                    "withdrawal": 0.0 if is_cr else amt,
                    "deposit": amt if is_cr else 0.0,
                    "balance": bal,
                    "type": "CREDIT" if is_cr else "DEBIT",
                    "narration_parts": [rem_str.strip()] if rem_str.strip() else [],
                }
                continue

            elif m_type2:
                if curr_txn:
                    raw_txns.append(curr_txn)

                date_str, prefix_desc, amt_str, f1, f2, bal_str, suffix_desc = m_type2.groups()
                flag = (f1 or f2 or "DR").upper()
                amt = float(parse_decimal(amt_str))
                bal = float(parse_decimal(bal_str))

                is_cr = flag == "CR"
                formatted_date = parse_date(date_str)
                full_desc = f"{prefix_desc.strip()} {suffix_desc.strip()}".strip()

                curr_txn = {
                    "date": formatted_date,
                    "valueDate": formatted_date,
                    "txnAmount": amt,
                    "amount": amt,
                    "withdrawal": 0.0 if is_cr else amt,
                    "deposit": amt if is_cr else 0.0,
                    "balance": bal,
                    "type": "CREDIT" if is_cr else "DEBIT",
                    "narration_parts": [full_desc] if full_desc else [],
                }
                continue

        # Multiline description continuation
        if curr_txn and not DATE_REGEX.search(line_str):
            if not re.search(r"^(page\s*\d+|total|balance|statement|legends|abbreviations|generated)", line_str, re.I):
                cleaned_sub = clean_description(line_str)
                if cleaned_sub and len(cleaned_sub) > 1 and not re.match(r"^\d+([,.]\d+)?\s*(Cr|Dr)?\.?$", cleaned_sub):
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