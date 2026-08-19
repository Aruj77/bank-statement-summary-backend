import re
from decimal import Decimal
from typing import List, Dict, Any, Optional
from core.normalizer import parse_decimal, clean_description, parse_date

DATE_REGEX = re.compile(
    r"^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4})"
)
AMOUNT_PATTERN = r"[\d,]+\.\d{2}(?:\s*(?:Cr|Dr)\.?)?"


def parse_parser_a(
    pages_text: List[str],
    pages_layout: List[str] = None,
    metadata: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    all_lines = []
    for page in pages_text:
        all_lines.extend(page.split("\n"))

    raw_items = []
    for line in all_lines:
        line_str = line.strip()
        date_match = DATE_REGEX.match(line_str)
        if date_match:
            amounts = re.findall(rf"\b{AMOUNT_PATTERN}\b", line_str, re.I)
            if len(amounts) >= 2:
                date_str = parse_date(date_match.group(1))
                txn_amount = float(parse_decimal(amounts[-2]))
                balance_val = float(parse_decimal(amounts[-1]))

                narration = line_str[len(date_match.group(1)) :].strip()
                for a in amounts[-2:]:
                    narration = narration.replace(a, "")
                full_narration = clean_description(narration)

                raw_items.append({
                    "date": date_str,
                    "narration": full_narration if full_narration else "—",
                    "txnAmount": txn_amount,
                    "balance": balance_val,
                })

    # Deduce DEBIT vs CREDIT using balance movement
    transactions = []
    for i, item in enumerate(raw_items):
        txn_amt = item["txnAmount"]
        balance_val = item["balance"]

        if i > 0:
            prev_bal = raw_items[i - 1]["balance"]
            diff = balance_val - prev_bal
            if diff > 0.001:
                txn_type = "CREDIT"
                withdrawal = 0.0
                deposit = txn_amt
            else:
                txn_type = "DEBIT"
                withdrawal = txn_amt
                deposit = 0.0
        else:
            # First transaction: estimate via metadata opening balance if present
            op_bal = float(metadata.get("openingBalance") or 0.0) if metadata else 0.0
            if op_bal > 0:
                diff = balance_val - op_bal
                if diff > 0.001:
                    txn_type = "CREDIT"
                    withdrawal = 0.0
                    deposit = txn_amt
                else:
                    txn_type = "DEBIT"
                    withdrawal = txn_amt
                    deposit = 0.0
            else:
                txn_type = "DEBIT"
                withdrawal = txn_amt
                deposit = 0.0

        transactions.append({
            "_index": i,
            "sNo": i + 1,
            "date": item["date"],
            "valueDate": item["date"],
            "remarks": item["narration"],
            "description": item["narration"],
            "txnAmount": txn_amt,
            "amount": txn_amt,
            "withdrawal": withdrawal,
            "deposit": deposit,
            "balance": balance_val,
            "type": txn_type,
        })

    return transactions