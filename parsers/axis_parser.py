import re
from utils import parse_amount, sort_transactions_by_date

def is_axis_header_or_footer(line):
    lower_line = line.lower()
    return (
        "customer id:" in lower_line or
        "ifsc code:" in lower_line or
        "micr code:" in lower_line or
        "nominee registered:" in lower_line or
        "registered mobile no:" in lower_line or
        "registered email id:" in lower_line or
        "scheme:" in lower_line or
        "currency:" in lower_line or
        "statement of axis account" in lower_line or
        "tran date" in lower_line or
        "chq no" in lower_line or
        "particulars" in lower_line or
        "debit" in lower_line or
        "credit" in lower_line or
        "balance" in lower_line or
        "opening balance" in lower_line or
        "axis bank" in lower_line or
        "registered office" in lower_line or
        "transaction total" in lower_line or
        "legends :" in lower_line or
        "iconn-transaction" in lower_line or
        "vmt-icon" in lower_line or
        "autosweep-transfer" in lower_line or
        "rev sweep" in lower_line or
        "sweep trf" in lower_line or
        "cwdr-cash" in lower_line or
        "pur-pos" in lower_line or
        "clg-cheque" in lower_line or
        "int.pd-interest" in lower_line or
        "int.coll-interest" in lower_line or
        "system generated output" in lower_line or
        "unless the constituent" in lower_line or
        "www.dicgc.org.in" in lower_line or
        lower_line.startswith("pageno.:") or
        "page " in lower_line or
        line.startswith("#")
    )

def parse_axis_transactions(lines):
    merged = []
    current = ""
    # Matches rows starting with a transaction date like "15-07-2025"
    row_start = re.compile(r"^\d{2}-\d{2}-\d{4}\b")

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if is_axis_header_or_footer(line):
            continue

        if row_start.match(line):
            if current:
                merged.append(current.strip())
            current = line
        else:
            lower_line = line.lower()
            if current and "statement of axis account" not in lower_line:
                current += " " + line

    if current:
        merged.append(current.strip())

    transactions = []
    index_tracker = 0
    # Matches amounts with decimals (e.g., 75000.00, 1.00)
    amount_regex = re.compile(r"\b\d{1,3}(?:,\d{2})*(?:,\d{3})\.\d{2}\b|\b\d+\.\d{2}\b")

    for row in merged:
        match = re.match(r"^(\d{2}-\d{2}-\d{4})\s+(.*)$", row)
        if not match:
            continue

        date = match.group(1)
        rest = match.group(2)

        amounts = amount_regex.findall(rest)
        if not amounts:
            continue

        balance = parse_amount(amounts[-1])
        txn_amount = parse_amount(amounts[-2]) if len(amounts) >= 2 else 0.0

        # Safely strip out only the trailing financial figures (Debit/Credit/Balance) 
        # while keeping the full narrative intact.
        description = rest
        for amt_str in amounts:
            # Only remove amounts from the tail end of the string to protect mid-text numbers
            pass

        # Clean description by cutting off from the exact position where trailing amounts begin
        # The last 1 or 2 amounts correspond to balance and transaction amount
        for amt_str in amounts[-2:]:
            idx = description.rfind(amt_str)
            if idx != -1:
                description = description[:idx]

        description = re.sub(r"\s+", " ", description).strip()
        # Clean up any residual trailing branch codes (e.g., 4370, 101 at the end)
        description = re.sub(r"\s+\d{3,4}$", "", description).strip()

        transactions.append({
            "_index": index_tracker,
            "sNo": index_tracker + 1,
            "date": date,
            "valueDate": date,
            "displayDate": date,
            "description": description,
            "remarks": description,
            "txnAmount": txn_amount,
            "balance": balance,
        })
        index_tracker += 1


    for i in range(len(transactions)):
        withdrawal = 0.0
        deposit = 0.0
        curr = transactions[i]

        if i == 0:
            deposit = curr["txnAmount"]
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
        curr["Withdrawal"] = withdrawal
        curr["Deposit"] = deposit
        curr["amount"] = withdrawal if withdrawal > 0 else deposit
        curr["type"] = "WITHDRAWAL" if withdrawal > 0 else ("DEPOSIT" if deposit > 0 else None)

    return sort_transactions_by_date(transactions)

def extract_axis_account_number(lines):
    for line in lines:
        match = re.search(r"(?:account\s*no\.?|account\s*number|a/c\s*no\.?)\s*[:#-]?\s*(\d{8,18})", line, re.IGNORECASE)
        if match:
            return match.group(1)
        digits = re.findall(r"\b\d{8,18}\b", line)
        if digits and any(k in line.lower() for k in ["account", "a/c"]):
            return digits[0]
    return "Unknown"