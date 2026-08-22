import re
from typing import List, Dict, Any, Optional
from core.normalizer import parse_decimal, clean_description, parse_date

DATE_REGEX = re.compile(
    r"(?:^|\s)(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}[\s-][A-Za-z]{3}[\s-]\d{2,4})\b"
)
AMOUNT_PATTERN = r"[\d,]+\.\d{2}(?:\s*(?:Cr|Dr)\.?)?"
HEADER_NOISE_REGEX = re.compile(
    r"^(date|tran\s*id|transaction\s*particulars|withdrawal|deposit|balance|particulars|s\.?no)",
    re.I,
)
CREDIT_KEYWORDS_REGEX = re.compile(
    r"(chq\s*dep|cheque\s*dep|by\s*transfer|neft|rtgs|upiab|apbcr|deposit|salary|interest|int\.pd|cr\b)",
    re.I,
)


def parse_parser_a(
    pages_text: List[str],
    pages_layout: List[str] = None,
    metadata: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    all_lines = []
    for page in (pages_text or pages_layout or []):
        all_lines.extend(page.split("\n"))

    raw_parsed_rows = []
    curr_txn: Optional[Dict[str, Any]] = None

    for line in all_lines:
        line_str = line.strip()
        if not line_str or HEADER_NOISE_REGEX.match(line_str):
            continue

        dates_found = list(DATE_REGEX.finditer(line_str))
        amounts = re.findall(rf"\b{AMOUNT_PATTERN}\b", line_str, re.I)

        if dates_found and len(amounts) >= 2:
            if curr_txn:
                raw_parsed_rows.append(curr_txn)

            txn_date_str = dates_found[0].group(1).strip()
            val_date_str = (
                dates_found[1].group(1).strip() if len(dates_found) > 1 else txn_date_str
            )

            formatted_date = parse_date(txn_date_str)
            formatted_val_date = parse_date(val_date_str)

            balance_val = float(parse_decimal(amounts[-1]))

            if len(amounts) >= 3:
                withdrawal = float(parse_decimal(amounts[-3]))
                deposit = float(parse_decimal(amounts[-2]))
                txn_amt = withdrawal if withdrawal > 0.0 else deposit
                txn_type = "DEBIT" if withdrawal > 0.0 else "CREDIT"
            else:
                txn_amt = float(parse_decimal(amounts[-2]))
                withdrawal = None
                deposit = None
                txn_type = None

            narration = line_str
            for d_match in dates_found:
                narration = narration.replace(d_match.group(0), " ")
            for amt_str in amounts:
                narration = narration.replace(amt_str, " ")
            narration = clean_description(narration)

            curr_txn = {
                "date": formatted_date,
                "valueDate": formatted_val_date,
                "full_narration": narration,
                "txnAmount": txn_amt,
                "withdrawal": withdrawal,
                "deposit": deposit,
                "balance": balance_val,
                "type": txn_type,
            }
            continue

        if curr_txn and not dates_found and len(amounts) == 0:
            if not re.search(r"^(page\s*\d+|total|opening|closing)", line_str, re.I):
                cleaned_sub = clean_description(line_str)
                if cleaned_sub and len(cleaned_sub) > 1:
                    curr_txn["full_narration"] += f" {cleaned_sub}"

    if curr_txn:
        raw_parsed_rows.append(curr_txn)

    transactions = []
    for i, t in enumerate(raw_parsed_rows):
        txn_amt = t["txnAmount"]
        curr_bal = t["balance"]
        desc = t["full_narration"]

        if t["withdrawal"] is None or t["deposit"] is None:
            if i > 0:
                prev_bal = raw_parsed_rows[i - 1]["balance"]
                diff = curr_bal - prev_bal
                if diff > 0.001:
                    t["type"] = "CREDIT"
                    t["deposit"] = txn_amt
                    t["withdrawal"] = 0.0
                else:
                    t["type"] = "DEBIT"
                    t["withdrawal"] = txn_amt
                    t["deposit"] = 0.0
            else:
                # FIRST ROW DETERMINATION
                op_bal = float(metadata.get("openingBalance") or 0.0) if metadata else 0.0
                if op_bal > 0:
                    diff = curr_bal - op_bal
                    if diff > 0.001:
                        t["type"] = "CREDIT"
                        t["deposit"] = txn_amt
                        t["withdrawal"] = 0.0
                    else:
                        t["type"] = "DEBIT"
                        t["withdrawal"] = txn_amt
                        t["deposit"] = 0.0
                elif abs(curr_bal - txn_amt) < 0.01:
                    t["type"] = "CREDIT"
                    t["deposit"] = txn_amt
                    t["withdrawal"] = 0.0
                elif CREDIT_KEYWORDS_REGEX.search(desc):
                    t["type"] = "CREDIT"
                    t["deposit"] = txn_amt
                    t["withdrawal"] = 0.0
                elif len(raw_parsed_rows) > 1:
                    next_bal = raw_parsed_rows[1]["balance"]
                    next_amt = raw_parsed_rows[1]["txnAmount"]
                    if abs((curr_bal - next_amt) - next_bal) < 1.0:
                        t["type"] = "CREDIT"
                        t["deposit"] = txn_amt
                        t["withdrawal"] = 0.0
                    else:
                        t["type"] = "DEBIT"
                        t["withdrawal"] = txn_amt
                        t["deposit"] = 0.0
                else:
                    t["type"] = "DEBIT"
                    t["withdrawal"] = txn_amt
                    t["deposit"] = 0.0

        transactions.append({
            "_index": i,
            "sNo": i + 1,
            "date": t["date"],
            "valueDate": t["valueDate"],
            "remarks": t["full_narration"] if t["full_narration"] else "—",
            "description": t["full_narration"] if t["full_narration"] else "—",
            "txnAmount": txn_amt,
            "amount": txn_amt,
            "withdrawal": t["withdrawal"],
            "deposit": t["deposit"],
            "balance": curr_bal,
            "type": t["type"],
        })

    return transactions