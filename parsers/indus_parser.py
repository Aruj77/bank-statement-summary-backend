import re
from utils import parse_amount, sort_transactions_by_date

def is_indusind_header_or_footer(line):
    lower_line = line.lower()
    return (
        "account statement" in lower_line or
        "customer details" in lower_line or
        "account summary" in lower_line or
        "transaction history" in lower_line or
        "statement period:" in lower_line or
        "branch ifsc code:" in lower_line or
        "nominee(s):" in lower_line or
        "holding status" in lower_line or
        "customer id" in lower_line or
        "account type" in lower_line or
        "lien amount" in lower_line or
        "balance" in lower_line or
        "date particulars" in lower_line or
        "chq no/ref no" in lower_line or
        "withdrawal" in lower_line or
        "deposit" in lower_line or
        "mob.no / tel.:" in lower_line or
        "period:" in lower_line or
        "indusind bank" in lower_line or
        "registered office" in lower_line or
        lower_line.startswith("pageno.:") or
        lower_line.startswith("c/o:") or
        lower_line.startswith("date:") or
        "page " in lower_line or
        line.startswith("#")
    )

def parse_indusind_transactions(lines):
    merged = []
    current = ""
    row_start = re.compile(r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{4}")

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if is_indusind_header_or_footer(line):
            continue

        if row_start.match(line):
            if current:
                merged.append(current.strip())
            current = line
        else:
            if current and len(line) < 120 and "indusind" not in line.lower():
                current += " " + line

    if current:
        merged.append(current.strip())

    transactions = []
    index_tracker = 0
    amount_regex = re.compile(r"[\d,]+\.\d{2}")

    for row in merged:
        match = re.match(r"^(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+(.*)$", row)
        if not match:
            continue

        date = match.group(1)
        rest = match.group(2)

        amounts = amount_regex.findall(rest)
        if not amounts or len(amounts) < 2:
            continue

        balance = parse_amount(amounts[-1])
        withdrawal = 0.0
        actual_deposit = 0.0

        if len(amounts) >= 3:
            withdrawal = parse_amount(amounts[-3])
            actual_deposit = parse_amount(amounts[-2])
        elif len(amounts) == 2:
            withdrawal = parse_amount(amounts[0])
            actual_deposit = parse_amount(amounts[1])

        description = rest
        for amt_str in amounts:
            description = description.replace(amt_str, "")

        description = re.sub(r"\b[A-Z]\d{8,}\b", "", description)
        description = re.sub(r"\s+", " ", description).strip()

        final_withdrawal = withdrawal
        final_deposit = actual_deposit
        amount = final_withdrawal if final_withdrawal > 0 else final_deposit

        txn_type = "WITHDRAWAL" if final_withdrawal > 0 else ("DEPOSIT" if final_deposit > 0 else None)

        transactions.append({
            "_index": index_tracker,
            "sNo": index_tracker + 1,
            "date": date,
            "valueDate": date,
            "displayDate": date,
            "description": description,
            "remarks": description,
            "withdrawal": final_withdrawal,
            "deposit": final_deposit,
            "Withdrawal": final_withdrawal,
            "Deposit": final_deposit,
            "txnAmount": amount,
            "amount": amount,
            "type": txn_type,
            "balance": balance,
        })
        index_tracker += 1

    return sort_transactions_by_date(transactions)

def extract_indusind_account_number(lines):
    for line in lines:
        match = re.search(r"(?:account\s*no\.?|account\s*number)\s*[:#-]?\s*(\d{9,18})", line, re.IGNORECASE)
        if match:
            return match.group(1)
        digits = re.findall(r"\b\d{9,18}\b", line)
        if digits and any(k in line.lower() for k in ["account", "a/c", "sb account"]):
            return digits[0]
    return "Unknown"