import re
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any, Optional, Union
from core.normalizer import parse_date, clean_description

# Matches PNB / Standard transaction IDs (e.g., S46657705, M1186313, S8905)
ANCHOR_PATTERN = re.compile(
    r"([SM]\d{4,10}|[A-Za-z]\d{4,10})\s+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}[\s-][A-Za-z]{3}[\s-]\d{2,4})\b",
    re.IGNORECASE,
)

AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")

PAGE_HEADER_RE = re.compile(
    r"(Page No\s*[-:]?\s*\d+\s*-?|Account Statement for Account Number \d+)",
    re.IGNORECASE,
)

HEADER_DISCARD_RE = re.compile(
    r"^(Account\s*Statement|Branch\s*Details|Customer\s*Details|Statement\s*Period|Txn\s*No\.|Branch\s*Name|Cheque\s*No|Dr\s*Amount|Cr\s*Amount|Balance|KIMS\s*Remarks|Remarks)",
    re.IGNORECASE,
)

FOOTER_DISCARD_RE = re.compile(
    r"(Unless constituent notifies the bank|COMPUTER GENERATED ENTERIES|PLEASE ENSURE THAT ALL|CUSTOMERS ARE REQUESTED|PLEASE MAINTAIN MINIMUM|Abbreviations are as under:).*",
    re.IGNORECASE | re.DOTALL,
)

CREDIT_HINTS = re.compile(
    r"(UPI/CR/|P2V|IMPS[_ -]?IN|NEFT_IN|BY TRANSFER|BY CASH|DEPOSIT|SALARY|INTEREST|INT\.PD|\bCR\b|\(CR\))",
    re.IGNORECASE,
)
DEBIT_HINTS = re.compile(
    r"(NEFT_OUT|^To:XXXX|IMPS[_ -]?OUT|SMS CHRG|WITHDRAWAL|WDL|ATM|CHARGES|POS|E-COM|NACH|BILL PAYMENT|PAID VIA|\bDR\b|\(DR\))",
    re.IGNORECASE,
)


def _to_decimal(raw: Any) -> Optional[Decimal]:
    if raw is None:
        return None
    cleaned = str(raw).replace("₹", "").replace("Rs.", "").replace(",", "").strip()
    negative = cleaned.startswith("-")
    cleaned = cleaned.lstrip("-").strip()
    try:
        val = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return -val if negative else val


def _normalize_pages_text(pages_text: Union[str, list, dict, None]) -> str:
    if pages_text is None:
        return ""
    if isinstance(pages_text, str):
        return pages_text
    if isinstance(pages_text, dict):
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
    # Continuous text stream
    raw_text = _normalize_pages_text(pages_text)
    if not raw_text:
        raw_text = _normalize_pages_text(pages_layout)

    cleaned_text = PAGE_HEADER_RE.sub("", raw_text)
    cleaned_text = FOOTER_DISCARD_RE.sub("", cleaned_text)

    # Strip opening table header
    header_end = re.search(r"Remarks\s*", cleaned_text, re.IGNORECASE)
    if header_end:
        cleaned_text = cleaned_text[header_end.end():]

    matches = list(ANCHOR_PATTERN.finditer(cleaned_text))
    if not matches:
        return []

    raw_rows = []
    for k, m in enumerate(matches):
        txn_no = m.group(1).strip()
        txn_date_str = m.group(2).strip()

        prev_end = matches[k - 1].end() if k > 0 else 0
        curr_start = m.start()
        curr_end = m.end()
        next_start = matches[k + 1].start() if k + 1 < len(matches) else len(cleaned_text)

        # Region before anchor (contains top description and potential multi-line balance)
        before_seg = cleaned_text[prev_end:curr_start].strip()
        # Region from anchor to next anchor (contains same-line amounts, bottom description)
        after_seg = cleaned_text[curr_end:next_start].strip()

        after_lines = [l.strip() for l in after_seg.split("\n") if l.strip()]
        first_line = after_lines[0] if after_lines else ""

        first_line_amts = AMOUNT_RE.findall(first_line)
        after_amts = AMOUNT_RE.findall(after_seg)
        before_amts = AMOUNT_RE.findall(before_seg)

        bal_val = None
        txn_amt_val = None

        # Case 1: Same-line transaction (e.g. "S69443002 21-02-2026 - 900.00 2,631.34 Cr.")
        if len(first_line_amts) >= 2 and re.search(r"(?:Cr\.?|Dr\.?)", first_line, re.I):
            txn_amt_val = _to_decimal(first_line_amts[-2])
            bal_val = _to_decimal(first_line_amts[-1])
        # Case 2: Multi-line transaction (Balance is above anchor line, Txn Amount is on/after anchor line)
        elif after_amts and before_amts:
            txn_amt_val = _to_decimal(after_amts[0])
            bal_val = _to_decimal(before_amts[-1])
        # Case 3: Fallback within after_seg
        elif len(after_amts) >= 2:
            txn_amt_val = _to_decimal(after_amts[0])
            bal_val = _to_decimal(after_amts[-1])
        elif after_amts:
            txn_amt_val = _to_decimal(after_amts[0])
            bal_val = Decimal("0.00")
        else:
            txn_amt_val = Decimal("0.00")
            bal_val = Decimal("0.00")

        # Description Assembly
        desc_parts = []

        # Top lines from before_seg (skip the balance line)
        for b_line in before_seg.split("\n"):
            b_line = b_line.strip()
            if not b_line or HEADER_DISCARD_RE.search(b_line):
                continue
            # If line is purely a balance line (contains balance amount with Cr/Dr), skip
            if bal_val is not None and str(bal_val) in b_line.replace(",", ""):
                cl_b = AMOUNT_RE.sub("", b_line)
                cl_b = re.sub(r"\b(?:Cr|Dr)\b\.?", "", cl_b, flags=re.I).strip(" -:")
                if cl_b and not HEADER_DISCARD_RE.search(cl_b):
                    desc_parts.append(cl_b)
            else:
                desc_parts.append(b_line)

        # Lines from after_seg (cleaning off amounts and Cr/Dr)
        for a_line in after_lines:
            if HEADER_DISCARD_RE.search(a_line):
                continue
            cl_a = AMOUNT_RE.sub("", a_line)
            cl_a = re.sub(r"\b(?:Cr|Dr)\b\.?", "", cl_a, flags=re.I).strip(" -:")
            if cl_a and not re.match(r"^(?:Cr|Dr)\.?$", cl_a, re.I):
                desc_parts.append(cl_a)

        full_desc = clean_description(" ".join(desc_parts))

        raw_rows.append({
            "txnNo": txn_no,
            "date_raw": txn_date_str,
            "date": parse_date(txn_date_str) if txn_date_str else None,
            "description": full_desc if full_desc else "—",
            "txnAmount": abs(txn_amt_val) if txn_amt_val is not None else Decimal("0.00"),
            "balance": bal_val if bal_val is not None else Decimal("0.00"),
        })

    if not raw_rows:
        return []

    # Strict Reverse-Chronological Running Balance Arithmetic
    transactions = []
    for i, row in enumerate(raw_rows):
        direction = None
        amt = row["txnAmount"]
        curr_bal = row["balance"]
        desc = row["description"]

        # Primary Deterministic Rule: Compare against next older row
        if i + 1 < len(raw_rows):
            next_balance = raw_rows[i + 1]["balance"]
            # In reverse list: next_balance is older in time
            if abs((next_balance + amt) - curr_bal) < Decimal("1.00"):
                direction = "Cr"
            elif abs((next_balance - amt) - curr_bal) < Decimal("1.00"):
                direction = "Dr"
            elif curr_bal > next_balance:
                direction = "Cr"
            elif curr_bal < next_balance:
                direction = "Dr"

        # Secondary / Final Row Resolution
        if not direction:
            if CREDIT_HINTS.search(desc):
                direction = "Cr"
            elif DEBIT_HINTS.search(desc):
                direction = "Dr"
            elif i > 0:
                prev_bal = raw_rows[i - 1]["balance"]
                if abs((curr_bal - amt) - prev_bal) < Decimal("1.00"):
                    direction = "Dr"
                elif abs((curr_bal + amt) - prev_bal) < Decimal("1.00"):
                    direction = "Cr"
                else:
                    direction = "Cr"
            else:
                direction = "Cr"

        dr_amount = amt if direction == "Dr" else Decimal("0.00")
        cr_amount = amt if direction == "Cr" else Decimal("0.00")
        txn_type = "DEBIT" if direction == "Dr" else "CREDIT"

        transactions.append({
            "_index": i,
            "sNo": i + 1,
            "date": row["date"],
            "valueDate": row["date"],
            "remarks": row["description"],
            "description": row["description"],
            "txnAmount": float(amt),
            "amount": float(amt),
            "withdrawal": float(dr_amount),
            "deposit": float(cr_amount),
            "balance": float(curr_bal),
            "type": txn_type,
            "txnNo": row["txnNo"],
        })

    return transactions


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            text = f.read()
        result = parse_parser_d(text)
        print(json.dumps(result, indent=2, default=str))