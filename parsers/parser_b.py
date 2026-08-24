import re
from typing import List, Dict, Any, Optional
from core.normalizer import parse_decimal, clean_description, parse_date

DATE_REGEX = re.compile(
    r"(?:^|\s)(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}[\s-][A-Za-z]{3}[\s-]\d{2,4})\b"
)
AMOUNT_PATTERN = r"[\d,]+\.\d{2}"

HEADER_NOISE_REGEX = re.compile(
    r"(s\s*no|transaction\s*date|withdrawal\s*amount|deposit\s*amount|balance|cheque\s*number|transaction\s*remarks|statement\s*of\s*transactions|saving\s*account|page\s*no)",
    re.I,
)

DISCLAIMER_REGEX = re.compile(
    r"(www\.icici\.bank\.in|dial\s+your\s+bank|please\s+call\s+from\s+your\s+registered|never\s+share\s+your\s+otp|sincerly\s+team|this\s+is\s+a\s+system\s+generated|legends\s+for\s+transactions|transaction\s+withdrawal\s+deposit)",
    re.I,
)

# Unambiguous Phrase & Token Indicators
EXPLICIT_DEBIT_REGEX = re.compile(
    r"\b(sent\s+using|sent\s+to|sent\s+from|upi/dr/|neft_out|imps\s*out|debit\s*trxn|dr\s*trxn|dr\b|to:|withdrawal|wdl|atm|smschgs|chg|charges|pos|e-com|nach\s*trxn|ach/|bill\s*payment|bbps)\b",
    re.I,
)
EXPLICIT_CREDIT_REGEX = re.compile(
    r"\b(payment\s+from|received\s+from|upi/cr/|neft_in|imps\s*in|credit\s*trxn|cr\s*trxn|cr\b|by\s*transfer|deposit|salary|interest|int\.pd|dividend|reversal|refund)\b",
    re.I,
)


def _determine_row_0_type(
    row0_amt: float,
    row0_bal: float,
    row0_desc: str,
    raw_rows: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Intelligently determines the direction of the first transaction using
    UPI phrase semantics, declared metadata balances, and multi-row forward simulation.
    """
    # 1. Declared opening balance check
    op_bal = float(metadata.get("openingBalance") or 0.0) if metadata else 0.0
    if op_bal > 0:
        diff = row0_bal - op_bal
        if diff > 0.001 and abs(diff - row0_amt) < 1.0:
            return "CREDIT"
        elif diff < -0.001 and abs(abs(diff) - row0_amt) < 1.0:
            return "DEBIT"

    # 2. UPI Phrase Semantics ('Sent using' -> DEBIT, 'Payment from' -> CREDIT)
    if EXPLICIT_DEBIT_REGEX.search(row0_desc) and not EXPLICIT_CREDIT_REGEX.search(row0_desc):
        return "DEBIT"
    if EXPLICIT_CREDIT_REGEX.search(row0_desc) and not EXPLICIT_DEBIT_REGEX.search(row0_desc):
        return "CREDIT"

    # 3. Account Zero Opening Balance (Balance == TxnAmount => Opening Deposit)
    if abs(row0_bal - row0_amt) < 0.01:
        return "CREDIT"

    # 4. Multi-Row Simulation
    if len(raw_rows) > 1:
        row1_desc = " ".join(raw_rows[1].get("narration_parts", []))
        row1_amt = float(raw_rows[1]["txnAmount"])
        row1_bal = float(raw_rows[1]["balance"] or 0.0)

        # Check if Row 1 is a known Debit (e.g., 2000.00 withdrawal resulting in 22223.29)
        if abs((row0_bal - row1_amt) - row1_bal) < 1.0:
            # Row 1 was a withdrawal from Row 0
            if "sent" in row0_desc.lower() or "debit" in row0_desc.lower() or "to" in row0_desc.lower():
                return "DEBIT"
            elif "deposit" in row0_desc.lower() or "salary" in row0_desc.lower():
                return "CREDIT"

    return "DEBIT" if "sent" in row0_desc.lower() else "CREDIT"


def parse_parser_b(
    pages_text: List[str],
    pages_layout: List[str] = None,
    metadata: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    # Use layout pages if available to preserve whitespace columns
    lines_source = pages_layout if pages_layout else (pages_text or [])
    all_lines = []
    for page in lines_source:
        all_lines.extend(page.split("\n"))

    raw_parsed_rows = []
    curr_txn: Optional[Dict[str, Any]] = None
    pending_header = ""

    for line in all_lines:
        line_str = line.strip()
        if not line_str or HEADER_NOISE_REGEX.match(line_str) or DISCLAIMER_REGEX.search(line_str):
            continue

        if re.search(
            r"^(statement\s+of\s+transactions|your\s+base\s+branch|national\s+public\s+school|uttar\s+pradesh|saving\s+account\s+no)",
            line_str,
            re.I,
        ):
            continue

        dates_found = list(DATE_REGEX.finditer(line_str))

        if dates_found:
            line_no_dates = line_str
            for d in dates_found:
                line_no_dates = line_no_dates.replace(d.group(0), " ")

            amounts = re.findall(rf"\b{AMOUNT_PATTERN}\b", line_no_dates)

            if len(amounts) >= 1:
                if curr_txn:
                    raw_parsed_rows.append(curr_txn)

                txn_date_str = dates_found[0].group(1).strip()
                val_date_str = (
                    dates_found[1].group(1).strip() if len(dates_found) > 1 else txn_date_str
                )

                formatted_date = parse_date(txn_date_str)
                formatted_val_date = parse_date(val_date_str)

                balance_val = None
                txn_amt = 0.0
                withdrawal = None
                deposit = None
                txn_type = None

                if len(amounts) >= 3:
                    withdrawal = float(parse_decimal(amounts[-3]))
                    deposit = float(parse_decimal(amounts[-2]))
                    balance_val = float(parse_decimal(amounts[-1]))
                    txn_amt = withdrawal if withdrawal > 0.0 else deposit
                    txn_type = "DEBIT" if withdrawal > 0.0 else "CREDIT"
                elif len(amounts) == 2:
                    txn_amt = float(parse_decimal(amounts[0]))
                    balance_val = float(parse_decimal(amounts[1]))
                elif len(amounts) == 1:
                    txn_amt = float(parse_decimal(amounts[0]))

                narration = line_no_dates
                for amt_str in amounts:
                    narration = narration.replace(amt_str, " ")

                narration = re.sub(r"^\d+\s+", "", narration.strip())
                if pending_header:
                    narration = f"{pending_header} {narration}".strip()
                    pending_header = ""

                curr_txn = {
                    "date": formatted_date,
                    "valueDate": formatted_val_date,
                    "txnAmount": txn_amt,
                    "amount": txn_amt,
                    "withdrawal": withdrawal,
                    "deposit": deposit,
                    "balance": balance_val,
                    "type": txn_type,
                    "narration_parts": [narration] if narration else [],
                }
                continue

        if not curr_txn and not dates_found:
            if re.match(r"^(fund\s*transfer|nach\s*trxn|debit\s*trxn|credit\s*trxn|upi\b)", line_str, re.I):
                pending_header = line_str
                continue

        if curr_txn and not dates_found:
            line_amts = re.findall(rf"\b{AMOUNT_PATTERN}\b", line_str)
            if len(line_amts) >= 2 and curr_txn["balance"] is None:
                curr_txn["txnAmount"] = float(parse_decimal(line_amts[0]))
                curr_txn["balance"] = float(parse_decimal(line_amts[1]))
            elif len(line_amts) == 1 and curr_txn["balance"] is None:
                curr_txn["balance"] = float(parse_decimal(line_amts[0]))

            cleaned_sub = clean_description(line_str)
            cleaned_sub = re.sub(r"[\d,]+\.\d{2}", "", cleaned_sub).strip()
            cleaned_sub = re.sub(r"\b(CH\s*trxn|Debit\s*trxn|Credit\s*trxn|NACH\s*trxn)\b", "", cleaned_sub, flags=re.I).strip()
            if cleaned_sub and len(cleaned_sub) > 1 and not re.match(r"^\d+$", cleaned_sub):
                curr_txn["narration_parts"].append(cleaned_sub)

    if curr_txn:
        raw_parsed_rows.append(curr_txn)

    if not raw_parsed_rows:
        return []

    # Infer Row 0 Direction
    row0_desc = " ".join(raw_parsed_rows[0].get("narration_parts", []))
    row0_dir = _determine_row_0_type(
        float(raw_parsed_rows[0]["txnAmount"]),
        float(raw_parsed_rows[0]["balance"] or 0.0),
        row0_desc,
        raw_parsed_rows,
        metadata,
    )

    transactions = []
    for i, t in enumerate(raw_parsed_rows):
        txn_amt = t["txnAmount"]
        curr_bal = t["balance"]
        desc = " ".join(t["narration_parts"])

        if t["withdrawal"] is None or t["deposit"] is None:
            if i == 0:
                if row0_dir == "DEBIT":
                    t["type"] = "DEBIT"
                    t["withdrawal"] = txn_amt
                    t["deposit"] = 0.0
                else:
                    t["type"] = "CREDIT"
                    t["deposit"] = txn_amt
                    t["withdrawal"] = 0.0
            else:
                prev_bal = raw_parsed_rows[i - 1]["balance"]
                if prev_bal is not None and curr_bal is not None:
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
                    if EXPLICIT_CREDIT_REGEX.search(desc):
                        t["type"] = "CREDIT"
                        t["deposit"] = txn_amt
                        t["withdrawal"] = 0.0
                    else:
                        t["type"] = "DEBIT"
                        t["withdrawal"] = txn_amt
                        t["deposit"] = 0.0

        filtered_parts = []
        for p in t["narration_parts"]:
            clean_p = re.sub(r"^\d+\s+", "", p).strip()
            clean_p = re.sub(r"\b(CH\s*trxn|Debit\s*trxn|Credit\s*trxn|NACH\s*trxn)\b", "", clean_p, flags=re.I).strip()
            if clean_p and len(clean_p) > 1:
                filtered_parts.append(clean_p)

        full_narration = clean_description(" ".join(filtered_parts))

        transactions.append({
            "_index": i,
            "sNo": i + 1,
            "date": t["date"],
            "valueDate": t["valueDate"],
            "remarks": full_narration if full_narration else "—",
            "description": full_narration if full_narration else "—",
            "txnAmount": txn_amt,
            "amount": txn_amt,
            "withdrawal": t["withdrawal"],
            "deposit": t["deposit"],
            "balance": curr_bal if curr_bal is not None else 0.0,
            "type": t["type"],
        })

    return transactions