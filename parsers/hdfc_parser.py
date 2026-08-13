import re
from utils import parse_amount, sort_transactions_by_date

def is_hdfc_header_or_footer(line):
    lower = line.lower()
    keywords = [
        "statementofaccount", "jointnoteders", "nomination:", "statementfrom:",
        "accountbranch", "phoneno.", "odlimit", "currency:", "custid",
        "accountno", "opendate", "accountstatus", "rtgs/neftifsc", "micr:",
        "branchcode", "accounttype", "date narration", "statement summary",
        "opening balance", "closing balance", "total debits", "total credits",
        "end of statement", "hdfc bank", "gstn:", "terms and conditions",
        "computer generated",
    ]
    return any(kw in lower for kw in keywords) or line.startswith("#")

def extract_hdfc_account_number(lines):
    for line in lines:
        if "accountno" in line.lower():
            match = re.search(r"accountno\s*:\s*(\d+)", line, re.IGNORECASE)
            if match:
                return match.group(1)
            # Fallback search for any long digit sequence on that line
            digits = re.findall(r"\d{9,}", line)
            if digits:
                return digits[0]
    return "Unknown"

def parse_hdfc_transactions(lines):
    transactions = []
    merged = []
    current = ""
    row_start = re.compile(r"^\d{2}/\d{2}/\d{2,4}\s+")

    for line in lines:
        line = line.strip()
        if not line or is_hdfc_header_or_footer(line):
            continue
        if row_start.match(line):
            if current:
                merged.append(current.strip())
            current = line
        else:
            if current and len(line) < 120 and "hdfc" not in line.lower():
                current += " " + line
    if current:
        merged.append(current.strip())

    index_tracker = 0
    amount_regex = re.compile(r"[₹-]?[\d,]+\.\d{2}-?")

    for row in merged:
        match = re.match(r"^(\d{2}/\d{2}/\d{2,4})\s+(.*)$", row)
        if not match:
            continue
        date, rest = match.groups()

        all_dates = re.findall(r"\b\d{2}/\d{2}/\d{2,4}\b", rest)
        value_date = all_dates[0] if all_dates else date

        raw_amounts = amount_regex.findall(rest)
        if not raw_amounts:
            continue
        amounts = [parse_amount(a) for a in raw_amounts]

        balance = amounts[-1]
        txn_amount = amounts[-2] if len(amounts) >= 2 else 0.0

        description = rest
        for amt in raw_amounts:
            description = description.replace(amt, "")
        if value_date:
            description = description.replace(value_date, "")
        description = re.sub(r"\s+", " ", description).strip()

        transactions.append({
            "_index": index_tracker,
            "date": date,
            "valueDate": value_date,
            "description": description,
            "txnAmount": txn_amount,
            "balance": balance,
        })
        index_tracker += 1

    for i, curr in enumerate(transactions):
        withdrawal, deposit = 0.0, 0.0
        if i == 0:
            withdrawal = curr["txnAmount"]
        else:
            prev_balance = transactions[i - 1]["balance"]
            diff = round(curr["balance"] - prev_balance, 2)
            if diff > 0:
                deposit = abs(diff)
                curr["txnAmount"] = deposit
            else:
                withdrawal = abs(diff)
                curr["txnAmount"] = withdrawal

        curr["withdrawal"] = withdrawal
        curr["deposit"] = deposit
        curr["amount"] = withdrawal if withdrawal > 0 else deposit
        curr["type"] = (
            "WITHDRAWAL"
            if withdrawal > 0
            else ("DEPOSIT" if deposit > 0 else None)
        )
    return sort_transactions_by_date(transactions)