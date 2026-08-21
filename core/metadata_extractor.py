import io
import json
import re
import logging
from typing import Dict, Any, Optional

import pdfplumber
from models import StatementMetadata, StatementPeriod
from core.normalizer import parse_decimal, parse_date

logger = logging.getLogger("BankStatementEngine")

# Standard IFSC Prefix -> Bank Name Master Mapping
IFSC_BANK_MAP = {
    "IPOS": "India Post Payments Bank",
    "SBIN": "State Bank of India",
    "HDFC": "HDFC Bank",
    "ICIC": "ICICI Bank",
    "UTIB": "Axis Bank",
    "PUNB": "Punjab National Bank",
    "BARB": "Bank of Baroda",
    "KKBK": "Kotak Mahindra Bank",
    "YESB": "Yes Bank",
    "INDB": "IndusInd Bank",
    "CNRB": "Canara Bank",
    "UBIN": "Union Bank of India",
    "BKID": "Bank of India",
    "MAHB": "Bank of Maharashtra",
    "IDIB": "Indian Bank",
    "IOBA": "Indian Overseas Bank",
}


def clean_json_markdown(text: str) -> str:
    """Strips Markdown wrappers (```json ... ```) from LLM output."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip()


def extract_header_and_footer_slice(file_bytes: bytes, password: str = None) -> tuple[str, str]:
    """
    Extracts the header block from Page 1 and the footer block from the final page
    to capture both Opening/Holder details and Closing Balances.
    """
    header_text = ""
    footer_text = ""

    with pdfplumber.open(io.BytesIO(file_bytes), password=password) as pdf:
        total_pages = len(pdf.pages)
        if total_pages > 0:
            # Page 1 top slice (metadata block)
            p1_text = pdf.pages[0].extract_text() or ""
            lines = [l.strip() for l in p1_text.split("\n") if l.strip()]
            header_text = "\n".join(lines[:45])

            # Last page bottom slice (for closing balance / summary block)
            p_last_text = pdf.pages[-1].extract_text() or ""
            last_lines = [l.strip() for l in p_last_text.split("\n") if l.strip()]
            footer_text = "\n".join(last_lines[-25:])

    return header_text, footer_text


def extract_statement_metadata_with_ai(
    file_bytes: bytes, password: str = None, openai_client=None
) -> StatementMetadata:
    header_text, footer_text = extract_header_and_footer_slice(file_bytes, password)
    combined_sample = f"=== STATEMENT HEADER ===\n{header_text}\n\n=== STATEMENT FOOTER ===\n{footer_text}"

    system_prompt = (
        "You are an expert Indian financial document metadata extractor.\n"
        "Analyze the statement header and footer text and accurately extract all customer and account metadata.\n\n"
        "Extraction Guidelines:\n"
        "1. Account Holder Name: Often appears at the very top line before 'ACCOUNT DETAILS' or 'Branch Office' (e.g., 'REKHA MITTAL').\n"
        "2. Bank Name: Identify official bank name from the header/IFSC (e.g. IFSC starting with 'IPOS' -> 'India Post Payments Bank').\n"
        "3. Branch Name vs Address: Separate the branch name (e.g. 'BIJNOR BRANCH') from the customer residential address.\n"
        "4. Account Number, IFSC, MICR, CIF/Customer ID: Extract clean numeric/alphanumeric strings without label prefixes.\n"
        "5. Dates: Standardize start/end dates to DD/MM/YYYY.\n"
        "6. Balances: Extract numeric decimal strings without 'Cr'/'Dr' flags.\n\n"
        "Return ONLY a strictly valid JSON object matching this schema:\n"
        "{\n"
        '  "bankName": "string",\n'
        '  "accountHolder": "string",\n'
        '  "accountNumber": "string",\n'
        '  "ifscCode": "string or null",\n'
        '  "micrCode": "string or null",\n'
        '  "accountType": "string",\n'
        '  "address": "string or null",\n'
        '  "branch": "string or null",\n'
        '  "panNumber": "string or null",\n'
        '  "cifNumber": "string or null",\n'
        '  "statementPeriod": {\n'
        '    "startDate": "DD/MM/YYYY or null",\n'
        '    "endDate": "DD/MM/YYYY or null"\n'
        '  },\n'
        '  "openingBalance": "string or null",\n'
        '  "closingBalance": "string or null"\n'
        "}"
    )

    user_prompt = f"### Statement Text:\n\n{combined_sample}\n\nExtract the JSON metadata:"

    # Candidate models for AI extraction
    CANDIDATE_MODELS = [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "groq/compound-mini",
        "gpt-4o-mini",
    ]

    if openai_client:
        for model_name in CANDIDATE_MODELS:
            try:
                response = openai_client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )
                raw_text = response.choices[0].message.content
                parsed = json.loads(clean_json_markdown(raw_text))
                logger.info(f"[Metadata Extractor] Successfully extracted metadata using AI model: {model_name}")
                
                # Cross-verify Bank Name against IFSC if present
                if parsed.get("ifscCode") and len(parsed["ifscCode"]) >= 4:
                    ifsc_prefix = parsed["ifscCode"][:4].upper()
                    if ifsc_prefix in IFSC_BANK_MAP:
                        parsed["bankName"] = IFSC_BANK_MAP[ifsc_prefix]

                return StatementMetadata(**parsed)
            except Exception as e:
                logger.warning(f"[Metadata Extractor] AI model `{model_name}` failed: {e}")

    # Fallback to Heuristic Regex Engine
    logger.info("[Metadata Extractor] Running deterministic regex fallback extraction.")
    return parse_metadata_heuristically(header_text, footer_text)


def parse_metadata_heuristically(header_text: str, footer_text: str) -> StatementMetadata:
    lines = [l.strip() for l in header_text.split("\n") if l.strip()]

    # 1. IFSC & Strict Bank Identification
    ifsc_match = re.search(r"\b([A-Z]{4})0[A-Z0-9]{6}\b", header_text)
    ifsc_code = ifsc_match.group(0) if ifsc_match else None

    bank_name = "Detected Bank"
    if ifsc_code and ifsc_code[:4] in IFSC_BANK_MAP:
        bank_name = IFSC_BANK_MAP[ifsc_code[:4]]
    elif re.search(r"india\s*post\s*payments\s*bank|ippb", header_text, re.I):
        bank_name = "India Post Payments Bank"
    elif re.search(r"state\s*bank\s*of\s*india", header_text, re.I):
        bank_name = "State Bank of India"
    elif re.search(r"hdfc\s*bank", header_text, re.I):
        bank_name = "HDFC Bank"
    elif re.search(r"icici\s*bank", header_text, re.I):
        bank_name = "ICICI Bank"

    # 2. Account Holder (Top line before ACCOUNT DETAILS / Branch Office)
    account_holder = "Account Holder"
    for line in lines[:4]:
        clean_line = re.sub(r"^[^\w\s]+|[^\w\s]+$", "", line).strip()
        if clean_line and not re.search(r"(account|details|statement|branch|office|bank|period|page|transaction)", clean_line, re.I):
            if len(clean_line) >= 3 and not re.search(r"\d", clean_line):
                account_holder = clean_line
                break

    # 3. Account Number
    acc_match = re.search(r"(?:account\s*(?:no|number)?|a/c\s*no)\s*[:\-]?\s*(\d{9,18})", header_text, re.I)
    account_number = acc_match.group(1) if acc_match else "N/A"

    # 4. MICR Code
    micr_match = re.search(r"(?:micr(?:\s*code)?)\s*[:\-]?\s*(\d{9})", header_text, re.I)
    micr_code = micr_match.group(1) if micr_match else None

    # 5. Customer ID / CIF
    cif_match = re.search(r"(?:customer\s*id|cif\s*(?:no|number)?|crn)\s*[:\-]?\s*([A-Za-z0-9Xx*]+)", header_text, re.I)
    cif_number = cif_match.group(1) if cif_match else None

    # 6. Branch Name & Customer Address Isolation
    branch = None
    address = None

    branch_match = re.search(r"(?:branch\s*(?:office)?\s*[:\-]?\s*(?:India Post Payments Bank\s*)?)([A-Z\s]+BRANCH)", header_text, re.I)
    if branch_match:
        branch = branch_match.group(1).strip()

    addr_match = re.search(r"Customer Address\s*:\s*(.*?)(?:Registered Mobile|Account Number|Account Type)", header_text, re.DOTALL | re.I)
    if addr_match:
        raw_addr = addr_match.group(1)
        if branch and branch in raw_addr:
            raw_addr = raw_addr.replace(branch, "")
        address = re.sub(r"\s+", " ", raw_addr).strip()
        address = re.sub(r"^[\s,.-]+|[\s,.-]+$", "", address)

    # 7. Account Type
    acc_type = "Savings Account"
    if re.search(r"current\s*account", header_text, re.I):
        acc_type = "Current Account"
    elif re.search(r"savings\s*account", header_text, re.I):
        acc_type = "Savings Account"

    # 8. Statement Period
    statement_period = None
    period_match = re.search(
        r"(?:period\s*[:\-])\s*(\d{1,2}[-\s][A-Za-z]{3}[-\s]\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+to\s+(\d{1,2}[-\s][A-Za-z]{3}[-\s]\d{2,4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        header_text,
        re.I
    )
    if period_match:
        statement_period = StatementPeriod(
            startDate=parse_date(period_match.group(1)),
            endDate=parse_date(period_match.group(2))
        )

    # 9. Opening & Closing Balances
    op_match = re.search(r"Opening Balance\s*:\s*([\d,]+\.\d{2})", header_text, re.I)
    opening_bal = str(parse_decimal(op_match.group(1))) if op_match else None

    last_amounts = re.findall(r"([\d,]+\.\d{2})\s*(?:Cr|Dr)?\.?", footer_text, re.I)
    closing_bal = str(parse_decimal(last_amounts[-1])) if last_amounts else None

    return StatementMetadata(
        bankName=bank_name,
        accountHolder=account_holder,
        accountNumber=account_number,
        ifscCode=ifsc_code,
        micrCode=micr_code,
        accountType=acc_type,
        address=address,
        branch=branch,
        cifNumber=cif_number,
        statementPeriod=statement_period,
        openingBalance=opening_bal,
        closingBalance=closing_bal
    )