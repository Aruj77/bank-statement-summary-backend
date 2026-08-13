import re
from utils import parse_amount, sort_transactions_by_date

def is_kotak_header_or_footer(line):
    lower = line.lower()
    return (
        "opening balance" in lower or
        "savings account transactions" in lower or
        "description" in lower or
        "statement generated on" in lower or
        "end of statement" in lower or
        "account summary" in lower or
        "rbi mandates positive pay" in lower or
        "same-day cheque clearing" in lower or
        "complimentary insurance" in lower or
        "in order to avail tds" in lower or
        "deposits of up to" in lower or
        "keep your account active" in lower or
        "registering a nominee" in lower or
        "goods and services tax" in lower or
        "commonly used narrations" in lower or
        "branch address" in lower or
        "toll-free number" in lower or
        "registered office" in lower or
        "important information" in lower or
        "page " in lower or
        line.startswith("#")
    )

def is_kotak_bleed_line(line):
    lower = line.lower()
    return (
        lower.startswith("account statement") or
        lower.startswith("account no") or
        lower.startswith("account type") or
        lower.startswith("savings account") or
        lower.startswith("any discrepancy") or
        lower.startswith("this is a system generated") or
        lower.startswith("for assistance") or
        lower.startswith("remember!") or
        lower.startswith("scan for") or
        lower.startswith("kotak mahindra bank") or
        lower.startswith("cin:") or
        lower.startswith("salary account") or
        lower.startswith("form 15g") or
        lower.startswith("rbi") or
        lower.startswith("scheme.") or
        lower.startswith("ap -") or
        lower.startswith("atl -") or
        lower.startswith("atw -") or
        lower.startswith("bp -") or
        lower.startswith("cdm -") or
        lower.startswith("cms -") or
        lower.startswith("ib -") or
        lower.startswith("imps -") or
        lower.startswith("kb -") or
        lower.startswith("mb -") or
        lower.startswith("nach -") or
        lower.startswith("neft -") or
        lower.startswith("sweep transfer") or
        lower.startswith("int. pd.") or
        bool(re.match(r"^page\s+\d+", lower))
    )

def parse_kotak_transactions(lines):
    merged = []
    current = ""
    row_start = re.compile(r"^\d+\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4}")

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if is_kotak_header_or_footer(line):
            continue

        if row_start.match(line):
            if current:
                merged.append(current.strip())
            current = line
        else:
            if is_kotak_bleed_line(line):
                continue
            if current:
                current += " " + line

    if current:
        merged.append(current.strip())

    transactions = []
    previous_balance = 0.0
    index_tracker = 0
    amount_regex = re.compile(r"[\d,]+\.\d{2}")

    for row in merged:
        match = re.match(r"^(\d+)\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+(.*)$", row)
        if not match:
            continue

        date = match.group(2)
        rest = match.group(3)

        amounts = amount_regex.findall(rest)
        if not amounts or len(amounts) < 2:
            continue

        balance = parse_amount(amounts[-1])
        txn_amount = parse_amount(amounts[-2])

        # Remove last two amounts only from description
        description = rest
        pattern_to_remove = f"{re.escape(amounts[-2])}\\s+{re.escape(amounts[-1])}$"
        description = re.sub(pattern_to_remove, "", description).strip()

        withdrawal = 0.0
        deposit = 0.0

        if len(transactions) == 0:
            if balance >= txn_amount:
                deposit = txn_amount
            else:
                withdrawal = txn_amount
        else:
            if balance > previous_balance:
                deposit = txn_amount
            else:
                withdrawal = txn_amount

        previous_balance = balance
        amount = withdrawal if withdrawal > 0 else deposit
        txn_type = "WITHDRAWAL" if withdrawal > 0 else ("DEPOSIT" if deposit > 0 else None)

        transactions.append({
            "_index": index_tracker,
            "sNo": index_tracker + 1,
            "date": date,
            "valueDate": date,
            "displayDate": date,
            "description": re.sub(r"\s+", " ", description).strip(),
            "remarks": re.sub(r"\s+", " ", description).strip(),
            "withdrawal": withdrawal,
            "deposit": deposit,
            "Withdrawal": withdrawal,
            "Deposit": deposit,
            "txnAmount": amount,
            "amount": amount,
            "type": txn_type,
            "balance": balance,
        })
        index_tracker += 1

    return sort_transactions_by_date(transactions)

def extract_kotak_account_number(lines):
    for line in lines:
        match = re.search(r"(?:account\s*no\.?|account\s*number|a/c\s*no\.?)\s*[:#-]?\s*(\d{8,18})", line, re.IGNORECASE)
        if match:
            return match.group(1)
        digits = re.findall(r"\b\d{8,18}\b", line)
        if digits and any(k in line.lower() for k in ["account", "a/c", "crn"]):
            return digits[0]
    return "Unknown"