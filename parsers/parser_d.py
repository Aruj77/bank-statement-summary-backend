"""
parsers/parser_d.py

Parser for the 9-column layout:
    Txn No. | Txn Date | Description | Branch Name | Cheque No. |
    Dr Amount | Cr Amount | Balance | KIMS Remarks

Anchors each transaction on its Txn No ("S" + 8 digits) rather than on
amount-column position, because Branch Name / Cheque No. are almost always
blank placeholders and only one of Dr Amount / Cr Amount is populated per
row - neither is a reliable row boundary on its own, which is what caused
multiple real transactions to get merged into a single blob in the
previous parser.

Called by the parser engine as:
    parser_entry["fn"](pages_text, pages_layout, meta)
pages_layout and meta are accepted but not required by this parser (plain
text is sufficient here - no coordinate/layout reconstruction needed).
pages_text may be a single string or a list of per-page strings/dicts; it
is normalized into one string before parsing so that transactions split
across a page boundary (a "Page No N Account Statement..." header landing
mid-description) are stitched back together rather than treated as two
separate broken rows.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any, Optional, Union


TXN_NO_PATTERN = re.compile(r"S\d{8}")
DATE_NUMERIC = r"\d{2}-\d{2}-\d{4}"
DATE_MONTH = r"\d{1,2}-[A-Za-z]{3}-\d{4}"
DATE_PATTERN = re.compile(rf"({DATE_NUMERIC}|{DATE_MONTH})")

# Strictly "digits (optional grouping commas) . two decimals", with an
# optional leading currency symbol / minus sign. Deliberately will NOT
# match bare reference numbers like "6084238061" (no decimal point).
AMOUNT_PATTERN = re.compile(r"-?₹?\s?-?[\d,]+\.\d{2}")

PAGE_HEADER_PATTERN = re.compile(
    r"Page No \d+\s*Account Statement for Account Number \d+"
)

CREDIT_HINTS = re.compile(r"UPI/CR/|NEFT_IN|IMPS[_ ]?IN", re.IGNORECASE)
DEBIT_HINTS = re.compile(r"NEFT_OUT|^To:XXXX|IMPS[_ ]?OUT|SMS CHRG", re.IGNORECASE)


def _to_decimal(raw: str) -> Optional[Decimal]:
    if not raw:
        return None
    cleaned = raw.replace("₹", "").replace(",", "").strip()
    negative = cleaned.startswith("-")
    cleaned = cleaned.lstrip("-").strip()
    try:
        val = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -val if negative else val


def _normalize_pages_text(pages_text: Union[str, list, dict, None]) -> str:
    """Coerce whatever shape pages_text arrives in into one big string,
    in page order, so a transaction split across a page boundary is
    still contiguous text before we anchor-split on Txn No."""
    if pages_text is None:
        return ""
    if isinstance(pages_text, str):
        return pages_text
    if isinstance(pages_text, dict):
        # e.g. {1: "...", 2: "..."} - sort by page number if keys are int-like
        try:
            keys = sorted(pages_text.keys())
        except TypeError:
            keys = list(pages_text.keys())
        return "\n".join(str(pages_text[k]) for k in keys)
    if isinstance(pages_text, (list, tuple)):
        parts = []
        for page in pages_text:
            if isinstance(page, str):
                parts.append(page)
            elif isinstance(page, dict):
                parts.append(str(page.get("text", page.get("content", ""))))
            else:
                parts.append(str(page))
        return "\n".join(parts)
    return str(pages_text)


def parse_parser_d(
    pages_text: Union[str, list, dict, None],
    pages_layout: Any = None,
    meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    raw_text = _normalize_pages_text(pages_text)
    cleaned_text = PAGE_HEADER_PATTERN.sub(" ", raw_text)

    pieces = TXN_NO_PATTERN.split(cleaned_text)
    txn_ids = TXN_NO_PATTERN.findall(cleaned_text)
    segments = list(zip(txn_ids, pieces[1:]))  # pieces[0] is pre-first-Txn-No junk

    raw_rows = []
    for txn_no, segment in segments:
        date_match = DATE_PATTERN.search(segment)
        txn_date = date_match.group(0) if date_match else None
        desc_start = date_match.end() if date_match else 0

        amounts = list(AMOUNT_PATTERN.finditer(segment, desc_start))
        desc_end = amounts[0].start() if amounts else len(segment)
        description = segment[desc_start:desc_end].strip(" :\n\t-")

        balance = _to_decimal(amounts[-1].group()) if amounts else None
        txn_amount = _to_decimal(amounts[-2].group()) if len(amounts) >= 2 else None

        raw_rows.append({
            "txnNo": txn_no,
            "date": txn_date,
            "description": description,
            "txnAmount": abs(txn_amount) if txn_amount is not None else None,
            "balance": balance,
        })

    # Second pass: classify Dr vs Cr using balance-difference against the
    # next (older) row, cross-checked against description keywords.
    transactions = []
    for i, row in enumerate(raw_rows):
        flags = []
        direction = None  # "Dr" or "Cr"

        keyword_dir = None
        if CREDIT_HINTS.search(row["description"]):
            keyword_dir = "Cr"
        elif DEBIT_HINTS.search(row["description"]):
            keyword_dir = "Dr"

        balance_dir = None
        if row["balance"] is not None and i + 1 < len(raw_rows):
            next_balance = raw_rows[i + 1]["balance"]
            if next_balance is not None:
                if row["balance"] > next_balance:
                    balance_dir = "Cr"
                elif row["balance"] < next_balance:
                    balance_dir = "Dr"

        if balance_dir:
            direction = balance_dir
            if keyword_dir and keyword_dir != balance_dir:
                flags.append(
                    f"keyword hint suggested {keyword_dir} but balance-diff says {balance_dir}"
                )
        elif keyword_dir:
            direction = keyword_dir
            flags.append("no adjacent balance to cross-check; used keyword hint only")
        else:
            flags.append("could not determine Dr/Cr direction")

        amount = row["txnAmount"]
        dr_amount = amount if direction == "Dr" else None
        cr_amount = amount if direction == "Cr" else None
        txn_type = "DEBIT" if direction == "Dr" else "CREDIT" if direction == "Cr" else "UNKNOWN"

        if amount is None:
            flags.append("no transaction amount found (only balance, or nothing, parsed)")

        transactions.append({
            # schema matching sibling parsers (A/B/E), for a consistent
            # row shape regardless of which parser ran
            "_index": i,
            "sNo": i + 1,
            "date": row["date"],
            "valueDate": row["date"],
            "remarks": row["description"],
            "description": row["description"],
            "txnAmount": float(amount) if amount is not None else 0.0,
            "amount": float(amount) if amount is not None else 0.0,
            "withdrawal": float(dr_amount) if dr_amount is not None else 0.0,
            "deposit": float(cr_amount) if cr_amount is not None else 0.0,
            "balance": float(row["balance"]) if row["balance"] is not None else None,
            "type": txn_type,
            # format-specific extras, kept for display / audit
            "txnNo": row["txnNo"],
            "drAmount": str(dr_amount) if dr_amount is not None else None,
            "crAmount": str(cr_amount) if cr_amount is not None else None,
            "flags": flags,
        })

    return transactions


if __name__ == "__main__":
    import json
    import sys

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        text = f.read()
    result = parse_parser_d(text)
    print(json.dumps(result, indent=2, default=str))