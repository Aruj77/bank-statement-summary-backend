import re
from utils import parse_amount, sort_transactions_by_date

def is_ippb_sbi_summary_or_end_section(line):
    lower = line.lower()
    summary_keywords = [
        "account summary",
        "opening balance total withdrawals",
        "end of report",
        "disclaimer :",
        "guidelines for safe",
        "statement summary",
        "total withdrawal",
        "total deposit",
        "closing balance"
    ]
    return any(kw in lower for kw in summary_keywords)

def is_ippb_sbi_header_or_footer(line):
    lower = line.lower()
    keywords = [
        "transaction details",
        "date tran id",
        "transaction particulars",
        "withdrwal",
        "deposit",
        "balance",
        "account details",
        "transaction period",
        "branch office",
        "customer address",
        "registered mobile",
        "registered e-mail",
        "account number",
        "ifsc",
        "nomination no",
        "customer id",
        "account type",
        "micr",
        "opening balance :",
        "call us at",
        "email us at",
        "download india post",
        "google play store",
        "apple app store",
        "pass code",
        "never share",
        "frequently change",
        "confidential account information",
        "operating system",
        "jail breaking",
        "rooting",
        "granting access",
        "remote access",
        "remember that",
        "page ",
        "sbi",
        "state bank of india",
        "statement of account"
    ]
    return any(kw in lower for kw in keywords)

def process_text_tokens(line_text, txn, amount_pattern):
    # Strip out redundant table column headers if repeated across page splits
    clean_line = re.sub(
        r"DATE TRAN ID TRANSACTION PARTICULARS WITHDRWAL DEPOSIT BALANCE|Txn Date|Value Date|Description|Ref/Cheque No\.|Branch|Debit|Credit|Balance",
        "",
        line_text,
        flags=re.IGNORECASE
    ).strip()
    
    tokens = clean_line.split()

    for token in tokens:
        if not token:
            continue
        if token in ["•", "the"]:
            continue

        # Capture IPPB/SBI Transaction Ref ID (e.g., S80329065, Ref numbers)
        if not txn["tranId"] and re.match(r"^[A-Z0-9]{5,}$", token) and not amount_pattern.match(token):
            # Make sure it's not a date format token
            if not re.match(r"^\d{1,2}[\.\/-]\d{1,2}[\.\/-]\d{4}$", token):
                txn["tranId"] = token
                continue

        # Preserve internal date remarks formatted as DD-MM-YYYY
        if re.match(r"^\d{1,2}[\.\/-]\d{1,2}[\.\/-]\d{4}$", token):
            parts = re.split(r"[\.\/-]", token)
            if len(parts) == 3:
                token = f"{parts[0].zfill(2)}-{parts[1].zfill(2)}-{parts[2]}"
            txn["remarks"].append(token)
            continue

        # Capture currency amounts
        if amount_pattern.match(token) or re.match(r"^[\d,]+\.\d{2}$", token):
            txn["amounts"].append(token)
        elif token.upper() not in ["CR", "DR"]:
            txn["remarks"].append(token)

def parse_ippb_sbi_transactions(lines):
    transactions = []
    date_regex = re.compile(r"\b(\d{1,2})[\.\/-](\d{1,2})[\.\/-](\d{4})\b")
    amount_pattern = re.compile(r"^[\d,]+\.\d{2}(\s*(?:Cr|Dr))?$", re.IGNORECASE)

    current_txn = None

    for i, raw_line in enumerate(lines):
        # Clean string: strip hidden non-breaking spaces and trim
        line = re.sub(r"[\u00A0\u1680\u180e\u2000-\u200b\u202f\u205f\u3000]", " ", raw_line).strip()

        # 1. Terminate transaction parsing immediately when summary or footer section begins
        if is_ippb_sbi_summary_or_end_section(line):
            if current_txn:
                finalize_and_push_txn(current_txn, transactions)
                current_txn = None
            continue

        # 2. Skip headers, metadata, or noise lines
        if not line or is_ippb_sbi_header_or_footer(line):
            continue

        date_match = date_regex.search(line)

        # 3. Check if this line marks the beginning of a new transaction
        if date_match and line.find(date_match.group(0)) < 15:
            if current_txn:
                finalize_and_push_txn(current_txn, transactions)

            raw_match = date_match.group(0)
            day = date_match.group(1).zfill(2)
            month = date_match.group(2).zfill(2)
            year = date_match.group(3)

            iso_date = f"{year}-{month}-{day}"
            display_date = f"{day}-{month}-{year}"

            rest = line[line.find(raw_match) + len(raw_match):].strip()

            current_txn = {
                "lineNum": i + 1,
                "date": iso_date,
                "formattedDate": display_date,
                "tranId": "",
                "remarks": [],
                "amounts": [],
            }

            if rest:
                process_text_tokens(rest, current_txn, amount_pattern)
            continue

        # 4. Append wrapped/continuing text lines into the active transaction
        if current_txn:
            process_text_tokens(line, current_txn, amount_pattern)

    # Finalize last transaction if still open
    if current_txn:
        finalize_and_push_txn(current_txn, transactions)

    return sort_transactions_by_date(transactions)

def finalize_and_push_txn(raw, output_array):
    if len(raw["amounts"]) < 1:
        return

    amounts = [parse_amount(a) for a in raw["amounts"]]

    closing_balance = amounts[-1]
    transaction_amount = amounts[-2] if len(amounts) >= 2 else amounts[0]

    full_remarks = " ".join(raw["remarks"]).strip()
    lower_remarks = full_remarks.lower()

    type_val = "WITHDRAWAL"
    previous_txn = output_array[-1] if output_array else None

    if previous_txn:
        type_val = "DEPOSIT" if closing_balance >= previous_txn["balance"] else "WITHDRAWAL"
    else:
        if any(kw in lower_remarks for kw in ["cr~", "credit", "deposit", "lpg subsidy", "int.pd", "by transfer", "received"]):
            type_val = "DEPOSIT"

    withdrawal = transaction_amount if type_val == "WITHDRAWAL" else 0.0
    deposit = transaction_amount if type_val == "DEPOSIT" else 0.0

    record = {
        "_index": len(output_array),
        "sNo": len(output_array) + 1,
        "date": raw["date"],
        "valueDate": raw["date"],
        "displayDate": raw["formattedDate"],
        "tranId": raw["tranId"] if raw["tranId"] else "N/A",
        "remarks": full_remarks,
        "description": full_remarks,
        "txnAmount": transaction_amount,
        "withdrawal": withdrawal,
        "deposit": deposit,
        "amount": transaction_amount,
        "balance": closing_balance,
        "type": type_val,
    }

    output_array.append(record)

def extract_ippb_sbi_account_number(lines):
    for line in lines:
        match = re.search(r"(?:account\s*no\.?|account\s*number)\s*[:#-]?\s*(\d{9,18})", line, re.IGNORECASE)
        if match:
            return match.group(1)
        digits = re.findall(r"\b\d{9,18}\b", line)
        if digits and any(k in line.lower() for k in ["account", "a/c", "sb account"]):
            return digits[0]
    return "Unknown"