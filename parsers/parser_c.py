import re
from decimal import Decimal
from typing import List, Dict, Any, Optional
from core.normalizer import parse_decimal, clean_description, parse_date

DATE_REGEX = re.compile(
    r"(?:^|\s)(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})\b"
)

ROW_PATTERN = re.compile(
    r"(?:^|\s)(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})\s+(.*?)\s*([\d,]+\.?\d*)\s*(?:\((Dr|Cr)\)|(DR|CR))\s+([\d,]+\.?\d*)\s*(?:\((?:Dr|Cr)\)|(?:DR|CR))?(.*)$",
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

    transactions = []
    curr_txn: Optional[Dict[str, Any]] = None

    for line in all_lines:
        line_str = line.strip()
        if not line_str:
            continue

        match = ROW_PATTERN.search(line_str)
        if match:
            if curr_txn:
                idx = len(transactions)
                transactions.append({
                    "_index": idx,
                    "sNo": idx + 1,
                    "date": curr_txn["date"],
                    "valueDate": curr_txn["valueDate"],
                    "remarks": curr_txn["full_narration"] if curr_txn["full_narration"] else "—",
                    "description": curr_txn["full_narration"] if curr_txn["full_narration"] else "—",
                    "txnAmount": curr_txn["txnAmount"],
                    "amount": curr_txn["txnAmount"],
                    "withdrawal": curr_txn["withdrawal"],
                    "deposit": curr_txn["deposit"],
                    "balance": curr_txn["balance"],
                    "type": curr_txn["type"],
                })

            date_str, prefix_desc, amt_str, flag1, flag2, bal_str, suffix_desc = match.groups()

            flag = (flag1 or flag2 or "DR").upper()
            amt = float(parse_decimal(amt_str))
            bal = float(parse_decimal(bal_str))

            is_cr = flag == "CR"
            withdrawal = 0.0 if is_cr else amt
            deposit = amt if is_cr else 0.0
            txn_type = "CREDIT" if is_cr else "DEBIT"

            full_desc = f"{prefix_desc.strip()} {suffix_desc.strip()}".strip()
            full_desc = clean_description(full_desc)

            curr_txn = {
                "date": parse_date(date_str),
                "valueDate": parse_date(date_str),
                "full_narration": full_desc,
                "txnAmount": amt,
                "withdrawal": withdrawal,
                "deposit": deposit,
                "balance": bal,
                "type": txn_type,
            }
            continue

        # Multiline description continuation: Append only if not starting a new date
        if curr_txn and not DATE_REGEX.search(line_str):
            if not re.search(r"^(page\s*\d+|total|balance|statement|date\s+remarks)", line_str, re.I):
                cleaned_sub = clean_description(line_str)
                if cleaned_sub and len(cleaned_sub) > 2 and len(cleaned_sub) < 140:
                    curr_txn["full_narration"] += f" {cleaned_sub}"

    if curr_txn:
        idx = len(transactions)
        transactions.append({
            "_index": idx,
            "sNo": idx + 1,
            "date": curr_txn["date"],
            "valueDate": curr_txn["valueDate"],
            "remarks": curr_txn["full_narration"] if curr_txn["full_narration"] else "—",
            "description": curr_txn["full_narration"] if curr_txn["full_narration"] else "—",
            "txnAmount": curr_txn["txnAmount"],
            "amount": curr_txn["txnAmount"],
            "withdrawal": curr_txn["withdrawal"],
            "deposit": curr_txn["deposit"],
            "balance": curr_txn["balance"],
            "type": curr_txn["type"],
        })

    return transactions