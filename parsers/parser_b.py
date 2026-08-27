import re
from typing import List, Dict, Any, Optional
from core.normalizer import parse_decimal, clean_description, parse_date

DATE_REGEX = re.compile(
    r"(?:^|\s)(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}[\s-][A-Za-z]{3}[\s-]\d{2,4})\b"
)
AMOUNT_PATTERN = r"[\d,]+\.\d{2}"

HEADER_NOISE_REGEX = re.compile(
    r"(s\s*no|transaction\s*date|withdrawal\s*amt|deposit\s*amt|closing\s*balance|chq\./ref\.no|value\s*dt|narration|particulars|balance|statement\s*of\s*account|page\s*no)",
    re.I,
)

# Comprehensive filter for HDFC, ICICI, and SBI repeating metadata
METADATA_DISCARD_REGEX = re.compile(
    r"(hdfc\s*bank\s*limited|closing\s*balance\s*includes|contents\s*of\s*this\s*statement|state\s*account\s*branch|registered\s*office\s*address|joint\s*holders|nomination\s*:|statement\s*summary|opening\s*balance|dr\s*count|cr\s*count|generated\s*on|this\s*is\s*a\s*computer|cust\s*id|account\s*no|a/c\s*open\s*date|rtgs/neft|branch\s*code|account\s*type|od\s*limit|currency\s*:|email\s*:|phone\s*no|city\s*:|state\s*:|address\s*:|account\s*branch)",
    re.I,
)

EXPLICIT_DEBIT_REGEX = re.compile(
    r"\b(sent\s+using|sent\s+to|sent\s+from|upi/dr/|neft_out|imps\s*out|debit\s*trxn|dr\s*trxn|dr\b|to:|withdrawal|wdl|atm|smschgs|chg|charges|pos|e-com|nach\s*trxn|ach/|bill\s*payment|bbps|paid\s*via|payment\s*from\s*phone)\b",
    re.I,
)
EXPLICIT_CREDIT_REGEX = re.compile(
    r"\b(chq\s*dep|payment\s+from|received\s+from|upi/cr/|neft_in|imps\s*in|credit\s*trxn|cr\s*trxn|cr\b|by\s*transfer|deposit|salary|interest|int\.pd|dividend|reversal|refund|upiret)\b",
    re.I,
)


def parse_parser_b(
    pages_text: List[str],
    pages_layout: List[str] = None,
    metadata: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    lines_source = pages_text if pages_text else (pages_layout or [])
    all_lines = []
    for page in lines_source:
        all_lines.extend(page.split("\n"))

    raw_parsed_rows = []
    curr_txn: Optional[Dict[str, Any]] = None
    in_discard_block = False

    for line in all_lines:
        line_str = line.strip()
        if not line_str:
            continue

        # Detect start of page-level customer address/header blocks
        if re.search(r"^(page\s*no\s*\.?\s*:|m/s\.|statement\s+of\s+account|hdfc\s+bank\s+limited)", line_str, re.I):
            in_discard_block = True
            continue

        if METADATA_DISCARD_REGEX.search(line_str):
            continue

        dates_found = list(DATE_REGEX.finditer(line_str))

        # A valid date at the start indicates a new transaction row
        if dates_found and dates_found[0].start() <= 5:
            in_discard_block = False
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

        if not in_discard_block and curr_txn and not dates_found:
            if not HEADER_NOISE_REGEX.search(line_str) and not METADATA_DISCARD_REGEX.search(line_str):
                line_amts = re.findall(rf"\b{AMOUNT_PATTERN}\b", line_str)
                if len(line_amts) >= 2 and curr_txn["balance"] is None:
                    curr_txn["txnAmount"] = float(parse_decimal(line_amts[0]))
                    curr_txn["balance"] = float(parse_decimal(line_amts[1]))
                elif len(line_amts) == 1 and curr_txn["balance"] is None:
                    curr_txn["balance"] = float(parse_decimal(line_amts[0]))

                cleaned_sub = clean_description(line_str)
                cleaned_sub = re.sub(r"[\d,]+\.\d{2}", "", cleaned_sub).strip()
                if cleaned_sub and len(cleaned_sub) > 1 and not re.match(r"^0000\d+$", cleaned_sub):
                    curr_txn["narration_parts"].append(cleaned_sub)

    if curr_txn:
        raw_parsed_rows.append(curr_txn)

    if not raw_parsed_rows:
        return []

    # Reconstruct Debits, Credits, and Balances across the series
    transactions = []
    for i, t in enumerate(raw_parsed_rows):
        txn_amt = t["txnAmount"]
        curr_bal = t["balance"]
        desc = " ".join(t["narration_parts"])

        if t["withdrawal"] is None or t["deposit"] is None:
            if i == 0:
                # Row 0: Evaluate semantic keywords or zero-start assumption
                if EXPLICIT_CREDIT_REGEX.search(desc) or (curr_bal is not None and abs(curr_bal - txn_amt) < 1.0):
                    t["type"] = "CREDIT"
                    t["deposit"] = txn_amt
                    t["withdrawal"] = 0.0
                else:
                    t["type"] = "DEBIT"
                    t["withdrawal"] = txn_amt
                    t["deposit"] = 0.0
            else:
                prev_bal = raw_parsed_rows[i - 1]["balance"]
                if prev_bal is not None and curr_bal is not None:
                    diff = curr_bal - prev_bal
                    if diff > 0.01:
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
            clean_p = re.sub(r"\b0000\d{8,16}\b", "", clean_p).strip()
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