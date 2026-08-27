import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from core.normalizer import parse_decimal, clean_description, parse_date

DATE_ANCHOR_REGEX = re.compile(
    r"^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}[\s-][A-Za-z]{3}[\s-]\d{2,4})"
)

HEADER_NOISE_REGEX = re.compile(
    r"(date\s+mode|transaction\s*particulars|deposits\s+withdrawals|closing\s*balance|statement\s*of\s*transactions|page\s*\d+\s+of|saving\s*account\s*no|your\s*base\s*branch|dial\s*your\s*bank|never\s*share|statement\s*summary|account\s*details|ckyc\s*id|total\s*:)",
    re.I,
)

CREDIT_KEYWORDS_REGEX = re.compile(
    r"(chq\s*dep|cheque\s*dep|cash\s*dep|by\s*transfer|neft|rtgs|upiab|apbcr|deposit|salary|interest|int\.pd|\bcr\b|\(cr\))",
    re.I,
)
DEBIT_KEYWORDS_REGEX = re.compile(
    r"(\bto\b|withdrawal|wdl|atm|smschgs|charges|pos|e-com|nach|bill\s*payment|bbps|paid\s*via|\bdr\b|\(dr\)|you\s*are\s*paying)",
    re.I,
)


def _safe_date_obj(date_str: str) -> datetime:
    try:
        clean = date_str.replace("/", "-")
        parts = clean.split("-")
        if len(parts) == 3:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            if year < 100:
                year += 2000
            return datetime(year, month, day)
    except Exception:
        pass
    return datetime.min


def parse_parser_a(
    pages_text: List[str],
    pages_layout: List[str] = None,
    metadata: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    lines_source = pages_layout if pages_layout else (pages_text or [])
    all_lines = []
    for page in lines_source:
        all_lines.extend(page.split("\n"))

    raw_parsed_rows = []
    curr_date: Optional[str] = None
    curr_narration_lines: List[str] = []
    pending_prefix_lines: List[str] = []

    for line in all_lines:
        line_str = line.strip()
        if not line_str or HEADER_NOISE_REGEX.search(line_str):
            continue

        # 1. Catch and skip Brought Forward (B/F) Opening Balance Row
        if re.search(r"\bB/F\b", line_str, re.I):
            bf_amounts = re.findall(r"[\d,]+\.\d{2}", line_str)
            if bf_amounts and metadata is not None:
                metadata["openingBalance"] = float(parse_decimal(bf_amounts[-1]))
            # Reset any in-flight accumulators so B/F never leaks into row 1
            curr_date = None
            curr_narration_lines = []
            pending_prefix_lines = []
            continue

        m_date = DATE_ANCHOR_REGEX.match(line_str)

        # 2. Date Anchor Encountered
        if m_date:
            txn_date_str = m_date.group(1).strip()
            rest_of_line = line_str[m_date.end():].strip()

            amounts = re.findall(r"[\d,]+\.\d{2}", rest_of_line)

            initial_narration = []
            if pending_prefix_lines:
                initial_narration.extend(pending_prefix_lines)
                pending_prefix_lines = []

            if len(amounts) >= 2:
                # Single-line row with amounts
                balance_val = float(parse_decimal(amounts[-1]))
                desc_line = rest_of_line

                if len(amounts) >= 3:
                    withdrawal = float(parse_decimal(amounts[-3]))
                    deposit = float(parse_decimal(amounts[-2]))
                    txn_amt = withdrawal if withdrawal > 0.0 else deposit
                    txn_type = "DEBIT" if withdrawal > 0.0 else "CREDIT"
                    for amt_s in amounts[-3:]:
                        desc_line = desc_line.replace(amt_s, " ")
                else:
                    txn_amt = float(parse_decimal(amounts[-2]))
                    withdrawal = None
                    deposit = None
                    txn_type = None
                    for amt_s in amounts[-2:]:
                        desc_line = desc_line.replace(amt_s, " ")

                if desc_line.strip():
                    initial_narration.append(desc_line.strip())

                raw_parsed_rows.append({
                    "date_raw": txn_date_str,
                    "date": parse_date(txn_date_str),
                    "valueDate": parse_date(txn_date_str),
                    "narration": " ".join(initial_narration).strip(),
                    "txnAmount": txn_amt,
                    "withdrawal": withdrawal,
                    "deposit": deposit,
                    "balance": balance_val,
                    "type": txn_type,
                })
                curr_date = None
                curr_narration_lines = []
                continue
            else:
                # Start multi-line transaction
                curr_date = txn_date_str
                if rest_of_line:
                    initial_narration.append(rest_of_line)
                curr_narration_lines = initial_narration
                continue

        # 3. Multi-line Continuation
        if curr_date is not None:
            amounts = re.findall(r"[\d,]+\.\d{2}", line_str)

            if len(amounts) >= 2:
                balance_val = float(parse_decimal(amounts[-1]))
                line_no_amts = line_str

                if len(amounts) >= 3:
                    withdrawal = float(parse_decimal(amounts[-3]))
                    deposit = float(parse_decimal(amounts[-2]))
                    txn_amt = withdrawal if withdrawal > 0.0 else deposit
                    txn_type = "DEBIT" if withdrawal > 0.0 else "CREDIT"
                    for amt_s in amounts[-3:]:
                        line_no_amts = line_no_amts.replace(amt_s, " ")
                else:
                    txn_amt = float(parse_decimal(amounts[-2]))
                    withdrawal = None
                    deposit = None
                    txn_type = None
                    for amt_s in amounts[-2:]:
                        line_no_amts = line_no_amts.replace(amt_s, " ")

                if line_no_amts.strip():
                    curr_narration_lines.append(line_no_amts.strip())

                full_desc = " ".join(curr_narration_lines).strip()

                raw_parsed_rows.append({
                    "date_raw": curr_date,
                    "date": parse_date(curr_date),
                    "valueDate": parse_date(curr_date),
                    "narration": full_desc,
                    "txnAmount": txn_amt,
                    "withdrawal": withdrawal,
                    "deposit": deposit,
                    "balance": balance_val,
                    "type": txn_type,
                })
                curr_date = None
                curr_narration_lines = []
            else:
                curr_narration_lines.append(line_str)
        else:
            pending_prefix_lines.append(line_str)

    if not raw_parsed_rows:
        return []

    # 4. Check Orientation (Newest-first vs Oldest-first)
    is_reverse_order = False
    if len(raw_parsed_rows) >= 5:
        first_dt = _safe_date_obj(raw_parsed_rows[0]["date_raw"])
        last_dt = _safe_date_obj(raw_parsed_rows[-1]["date_raw"])
        if first_dt > last_dt and first_dt != datetime.min and last_dt != datetime.min:
            is_reverse_order = True

    # 5. Exact Running Balance Math
    for i, t in enumerate(raw_parsed_rows):
        txn_amt = t["txnAmount"]
        curr_bal = t["balance"]
        desc = t["narration"]

        if t["withdrawal"] is None or t["deposit"] is None:
            if i > 0:
                prev_bal = raw_parsed_rows[i - 1]["balance"]

                if is_reverse_order:
                    if abs((prev_bal - txn_amt) - curr_bal) < 1.0:
                        t["type"] = "CREDIT"
                        t["deposit"] = txn_amt
                        t["withdrawal"] = 0.0
                    elif abs((prev_bal + txn_amt) - curr_bal) < 1.0:
                        t["type"] = "DEBIT"
                        t["withdrawal"] = txn_amt
                        t["deposit"] = 0.0
                    elif curr_bal > prev_bal:
                        t["type"] = "DEBIT"
                        t["withdrawal"] = txn_amt
                        t["deposit"] = 0.0
                    else:
                        t["type"] = "CREDIT"
                        t["deposit"] = txn_amt
                        t["withdrawal"] = 0.0
                else:
                    if abs((prev_bal + txn_amt) - curr_bal) < 1.0:
                        t["type"] = "CREDIT"
                        t["deposit"] = txn_amt
                        t["withdrawal"] = 0.0
                    elif abs((prev_bal - txn_amt) - curr_bal) < 1.0:
                        t["type"] = "DEBIT"
                        t["withdrawal"] = txn_amt
                        t["deposit"] = 0.0
                    elif curr_bal > prev_bal:
                        t["type"] = "CREDIT"
                        t["deposit"] = txn_amt
                        t["withdrawal"] = 0.0
                    else:
                        t["type"] = "DEBIT"
                        t["withdrawal"] = txn_amt
                        t["deposit"] = 0.0
            else:
                # Row 0: Compare directly with B/F opening balance if available
                op_bal = float(metadata.get("openingBalance") or 0.0) if metadata else 0.0
                if op_bal > 0:
                    diff = curr_bal - op_bal
                    if abs(diff - txn_amt) < 1.0:
                        t["type"] = "CREDIT"
                        t["deposit"] = txn_amt
                        t["withdrawal"] = 0.0
                    else:
                        t["type"] = "DEBIT"
                        t["withdrawal"] = txn_amt
                        t["deposit"] = 0.0
                elif CREDIT_KEYWORDS_REGEX.search(desc):
                    t["type"] = "CREDIT"
                    t["deposit"] = txn_amt
                    t["withdrawal"] = 0.0
                elif DEBIT_KEYWORDS_REGEX.search(desc):
                    t["type"] = "DEBIT"
                    t["withdrawal"] = txn_amt
                    t["deposit"] = 0.0
                elif len(raw_parsed_rows) > 1:
                    next_bal = raw_parsed_rows[1]["balance"]
                    if is_reverse_order:
                        if next_bal < curr_bal:
                            t["type"] = "DEBIT"
                            t["withdrawal"] = txn_amt
                            t["deposit"] = 0.0
                        else:
                            t["type"] = "CREDIT"
                            t["deposit"] = txn_amt
                            t["withdrawal"] = 0.0
                    else:
                        if next_bal > curr_bal:
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

    transactions = []
    for i, t in enumerate(raw_parsed_rows):
        clean_narration = clean_description(t["narration"])
        transactions.append({
            "_index": i,
            "sNo": i + 1,
            "date": t["date"],
            "valueDate": t["valueDate"],
            "remarks": clean_narration if clean_narration else "—",
            "description": clean_narration if clean_narration else "—",
            "txnAmount": t["txnAmount"],
            "amount": t["txnAmount"],
            "withdrawal": t["withdrawal"],
            "deposit": t["deposit"],
            "balance": t["balance"],
            "type": t["type"],
        })

    return transactions