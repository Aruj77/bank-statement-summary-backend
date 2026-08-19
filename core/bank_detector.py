import re
from decimal import Decimal
from typing import List, Dict, Any, Optional

BANK_SIGNATURES = {
    "HDFC": [r"hdfc bank", r"hdfcbank\.com"],
    "ICICI": [r"icici bank", r"icicibank\.com"],
    "SBI": [r"state bank of india", r"sbi\.co\.in"],
    "AXIS": [r"axis bank", r"axisbank\.com"],
    "KOTAK": [r"kotak mahindra", r"kotak\.com"],
    "INDUSIND": [r"indusind bank", r"indusind\.com"],
    "PNB": [r"punjab national bank", r"pnbindia\.in"],
    "IPPB": [r"india post payments bank", r"ippbonline\.com", r"ippb"],
    "BOB": [r"bank of baroda", r"bankofbaroda\.in"],
}

ACC_NUM_PATTERN = re.compile(
    r"(?:account\s*(?:no|number|a/c\s*no)?|a/c\s*[:#]?)\s*[:\-]?\s*([0-9Xx*]{8,18})",
    re.I,
)
ACC_HOLDER_PATTERN = re.compile(
    r"(?:customer\s*name|account\s*holder|name\s*[:\-])\s*[:\-]?\s*([A-Za-z\s\.]{3,35})",
    re.I,
)
ACC_TYPE_PATTERN = re.compile(
    r"(?:account\s*type|a/c\s*type)\s*[:\-]?\s*([A-Za-z\s/]{3,30})",
    re.I,
)
OPENING_BAL_PATTERN = re.compile(
    r"(?:opening\s*bal(?:ance)?|brought\s*forward|b/f)\s*[:\-]?\s*(?:inr|rs\.?)?\s*([\d,]+\.\d{2})",
    re.I,
)
CLOSING_BAL_PATTERN = re.compile(
    r"(?:closing\s*bal(?:ance)?|carried\s*forward|c/f)\s*[:\-]?\s*(?:inr|rs\.?)?\s*([\d,]+\.\d{2})",
    re.I,
)


def detect_bank_and_metadata(pages_text: List[str]) -> Dict[str, Any]:
    header_text = "\n".join(pages_text[:2]) if pages_text else ""
    full_text = "\n".join(pages_text) if pages_text else ""

    # Detect Bank
    detected_bank = "Detected Bank"
    for bank_name, patterns in BANK_SIGNATURES.items():
        if any(re.search(pat, header_text, re.I) for pat in patterns):
            detected_bank = bank_name
            break

    # Extract Account Number
    acc_match = ACC_NUM_PATTERN.search(header_text)
    account_number = acc_match.group(1).strip() if acc_match else "N/A"

    # Extract Account Holder Name
    holder_match = ACC_HOLDER_PATTERN.search(header_text)
    account_holder = holder_match.group(1).strip() if holder_match else "Account Holder"

    # Extract Account Type
    type_match = ACC_TYPE_PATTERN.search(header_text)
    account_type = (
        type_match.group(1).strip()
        if type_match
        else "Savings / Current Account"
    )

    # Balances
    op_match = OPENING_BAL_PATTERN.search(header_text) or OPENING_BAL_PATTERN.search(full_text)
    cl_match = CLOSING_BAL_PATTERN.search(full_text)

    opening_bal = (
        Decimal(op_match.group(1).replace(",", "")) if op_match else None
    )
    closing_bal = (
        Decimal(cl_match.group(1).replace(",", "")) if cl_match else None
    )

    return {
        "bank": detected_bank,
        "accountNumber": account_number,
        "accountHolder": account_holder,
        "accountType": account_type,
        "openingBalance": opening_bal,
        "closingBalance": closing_bal,
    }