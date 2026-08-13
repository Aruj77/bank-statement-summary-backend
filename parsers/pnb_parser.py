import re
from utils import parse_amount, sort_transactions_by_date

def parse_pnb_transactions(lines):
    transactions = []
    current_txn = None

    # Updated regex to optionally capture Instrument ID right after the date
    # Format: Date [Instrument ID] Amount CR/DR Balance Description
    txn_regex = re.compile(
        r"^(\d{2}/\d{2}/\d{4})\s+(?:([\w-]+)\s+)?([\d,]+(?:\.\d+)?)\s+(CR|DR)\s+([\d,]+(?:\.\d+)?)\s*(.*)$",
        re.IGNORECASE
    )

    skip_keywords = [
        "branch details",
        "branch name",
        "branch address",
        "customer details",
        "customer name",
        "customer address",
        "statement of account",
        "generated through",
        "computer generated",
        "unless constituent",
        "please ensure",
        "customers are requested",
        "please maintain",
        "please note",
        "abbreviations are as under",
        "page ",
        "date:",
        "opening balance",
        "closing balance",
        "total debit",
        "total credit",
    ]

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        match = txn_regex.match(line)
        if match:
            if current_txn:
                current_txn["description"] = re.sub(r"\s+", " ", current_txn["description"]).strip()
                current_txn["remarks"] = current_txn["description"]
                transactions.append(current_txn)

            date = match.group(1)
            instrument_id = match.group(2) or ""
            amount = parse_amount(match.group(3))
            raw_type = match.group(4).upper()
            balance = parse_amount(match.group(5))
            description = match.group(6) or ""

            if instrument_id and not description.startswith(instrument_id):
                description = f"{instrument_id} {description}".strip()

            is_withdrawal = (raw_type == "DR")
            withdrawal = amount if is_withdrawal else 0.0
            deposit = 0.0 if is_withdrawal else amount

            index_tracker = len(transactions)
            current_txn = {
                "_index": index_tracker,
                "sNo": index_tracker + 1,
                "id": f"{date}-{index_tracker + 1}",
                "date": date,
                "valueDate": date,
                "displayDate": date,
                "description": description,
                "remarks": description,
                "instrumentId": instrument_id,
                "amount": amount,
                "txnAmount": amount,
                "balance": balance,
                "type": "WITHDRAWAL" if is_withdrawal else "DEPOSIT",
                "rawType": raw_type,
                "withdrawal": withdrawal,
                "deposit": deposit,
                "Withdrawal": withdrawal,
                "Deposit": deposit,
            }
            continue

        if not current_txn:
            continue

        lower = line.lower()
        if any(k in lower for k in skip_keywords):
            continue

        current_txn["description"] += " " + line

    if current_txn:
        current_txn["description"] = re.sub(r"\s+", " ", current_txn["description"]).strip()
        current_txn["remarks"] = current_txn["description"]
        transactions.append(current_txn)

    valid_transactions = [
        txn for txn in transactions 
        if txn["description"] and (txn["withdrawal"] > 0 or txn["deposit"] > 0)
    ]

    return sort_transactions_by_date(valid_transactions)

def extract_pnb_account_number(lines):
    for line in lines:
        match = re.search(r"(?:account\s*no\.?|account\s*number|a/c\s*no\.?)\s*[:#-]?\s*(\d{8,18})", line, re.IGNORECASE)
        if match:
            return match.group(1)
        digits = re.findall(r"\b\d{8,18}\b", line)
        if digits and any(k in line.lower() for k in ["account", "a/c"]):
            return digits[0]
    return "Unknown"