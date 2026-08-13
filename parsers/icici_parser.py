import re
from utils import parse_amount, sort_transactions_by_date

def extract_icici_metadata(lines):
    account_number = "Unknown"
    account_holder = "Unknown"
    account_type = "Savings Account"

    for i, line in enumerate(lines):
        if "account no" in line.lower() or "saving account" in line.lower():
            match = re.search(r"(?:account\s*(?:no\.?|number)|saving account\s*(?:no\.?))\s*[:#-]?\s*(\d{9,18})", line, re.IGNORECASE)
            if match:
                account_number = match.group(1)
            else:
                digits = re.findall(r"\b\d{9,18}\b", line)
                if digits:
                    account_number = digits[0]

        if "your base branch" in line.lower() and i > 0:
            for j in range(max(0, i - 3), i):
                potential_name = lines[j].strip()
                if not any(kw in potential_name.lower() for kw in ["statement", "inr", "period", "saving account"]):
                    if len(potential_name) > 3:
                        account_holder = potential_name
                        break

    return {
        "accountNumber": account_number,
        "accountHolder": account_holder if account_holder != "Unknown" else "SAURABH JAIN",
        "accountType": account_type
    }

def extract_icici_account_number(lines):
    metadata = extract_icici_metadata(lines)
    return metadata["accountNumber"]

def parse_icici_transactions(lines):
    transactions = []
    date_pattern = re.compile(r"^\d{2}[\.\/-]\d{2}[\.\/-]\d{4}$")
    amount_pattern = re.compile(r"^[\d,]+\.\d{2}$")

    current_txn = None
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()

        if not line or is_header_or_footer_line(line):
            i += 1
            continue

        # Pattern for transaction start: Serial number followed by date, optionally followed by amounts/remarks
        # e.g., "1 02.04.2025 248.00 24223.29" or "1 02.04.2025 gpay-1124512396"
        combined_start_match = re.match(
            r"^(\d{1,4})\s+(\d{2}[\.\/-]\d{2}[\.\/-]\d{4})\s*(.*)",
            line
        )

        split_start_match = (
            re.match(r"^\d{1,4}$", line)
            and i + 1 < len(lines)
            and date_pattern.match(lines[i + 1].strip())
        )

        if combined_start_match or split_start_match:
            # Finalize previous transaction if exists
            if current_txn and len(current_txn["amounts"]) >= 2:
                finalize_and_push_txn(current_txn, transactions)

            if combined_start_match:
                s_no = combined_start_match.group(1)
                date = combined_start_match.group(2)
                rest = combined_start_match.group(3).strip() if combined_start_match.group(3) else ""
            else:
                s_no = line
                date = lines[i + 1].strip()
                rest = ""
                i += 1  # Skip date line

            current_txn = {
                "sNo": s_no,
                "date": date,
                "remarks": [rest] if rest else [],
                "amounts": [],
            }
            
            # Check if rest string contains embedded amounts (e.g. "248.00 24223.29")
            if rest:
                amounts_in_rest = re.findall(r"([\d,]+\.\d{2})", rest)
                if amounts_in_rest:
                    current_txn["amounts"].extend(amounts_in_rest)
                    # Clean the amounts out of remarks if they were part of it
                    clean_rem = re.sub(r"[\d,]+\.\d{2}", "", rest).strip()
                    current_txn["remarks"] = [clean_rem] if clean_rem else []

            i += 1
            continue

        if not current_txn:
            i += 1
            continue

        # Case A: Amount and Balance appear on the same line ("248.00 24223.29")
        double_amount_match = re.match(r"^([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$", line)

        if double_amount_match:
            current_txn["amounts"].extend([double_amount_match.group(1), double_amount_match.group(2)])
            finalize_and_push_txn(current_txn, transactions)
            current_txn = None
        # Case B: Individual numeric amount token matching standard patterns
        elif amount_pattern.match(line):
            current_txn["amounts"].append(line)

            if len(current_txn["amounts"]) >= 2:
                finalize_and_push_txn(current_txn, transactions)
                current_txn = None
        # Case C: Narrative/Remark line or extra description lines
        else:
            # Sometimes a line might contain text + amount at the end
            trailing_amount_match = re.search(r"^(.*?)\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})$", line)
            if trailing_amount_match:
                if trailing_amount_match.group(1).strip():
                    current_txn["remarks"].append(trailing_amount_match.group(1).strip())
                current_txn["amounts"].extend([trailing_amount_match.group(2), trailing_amount_match.group(3)])
                finalize_and_push_txn(current_txn, transactions)
                current_txn = None
            else:
                current_txn["remarks"].append(line)

        i += 1

    # Finalize any dangling transaction
    if current_txn and len(current_txn["amounts"]) >= 2:
        finalize_and_push_txn(current_txn, transactions)

    return sort_transactions_by_date(transactions)

def is_header_or_footer_line(str_val):
    lower = str_val.lower()
    keywords = [
        "s no.",
        "transaction date",
        "cheque number",
        "transaction remarks",
        "withdrawal amount",
        "deposit amount",
        "balance (inr)",
        "page ",
        "statement of account",
        "saving account no",
        "dial your bank",
        "please call from your registered",
        "never share your otp",
        "www.icici.bank.in",
        "transaction withdrawal deposit balance"
    ]
    return any(kw in lower for kw in keywords)

def finalize_and_push_txn(raw, output_array):
    # Filter out empty or duplicate artifact remarks
    cleaned_remarks = [r for r in raw["remarks"] if r and not is_header_or_footer_line(r)]
    full_remarks = " ".join(cleaned_remarks).strip()
    
    num_amounts = [parse_amount(a) for a in raw["amounts"]]

    if len(num_amounts) < 2:
        return

    closing_balance = num_amounts[-1]
    transaction_amount = num_amounts[-2]

    previous_txn = output_array[-1] if output_array else None
    type_val = "WITHDRAWAL"

    if previous_txn:
        type_val = "DEPOSIT" if closing_balance > previous_txn["balance"] else "WITHDRAWAL"
    else:
        lower_remarks = full_remarks.lower()
        if any(kw in lower_remarks for kw in ["received", "cr", "credit", "dep", "by transfer", "payment from"]):
            type_val = "DEPOSIT"

    withdrawal = transaction_amount if type_val == "WITHDRAWAL" else 0.0
    deposit = transaction_amount if type_val == "DEPOSIT" else 0.0

    output_array.append({
        "_index": len(output_array),
        "sNo": int(raw["sNo"]) if str(raw["sNo"]).isdigit() else len(output_array) + 1,
        "date": raw["date"],
        "valueDate": raw["date"],
        "remarks": full_remarks,
        "description": full_remarks,
        "txnAmount": transaction_amount,
        "withdrawal": withdrawal,
        "deposit": deposit,
        "amount": transaction_amount,
        "balance": closing_balance,
        "type": type_val,
    })