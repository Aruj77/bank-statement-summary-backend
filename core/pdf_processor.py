import io
import json
import re
import logging
from decimal import Decimal
from typing import Tuple, List, Dict, Any

import pdfplumber

from core.bank_detector import detect_bank_and_metadata
from core.sample_builder import build_ai_table_sample
from core.ai_classifier import classify_table_layout_with_llm
from core.validator import validate_and_score_transactions
from core.safe_llm_fallback import parse_with_chunked_llm_fallback
from parsers import PARSER_REGISTRY

logger = logging.getLogger("BankStatementEngine")


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def reassemble_split_lines(text: str) -> str:
    """
    Reconstructs 3-tier vertically wrapped PDF lines into single unified transaction rows.
    Handles Union Bank multi-line layouts with split dates, amounts, descriptions, and Cr/Dr flags.
    """
    if not text:
        return ""
    print(text)
    
    # Pre-clean trailing newlines between amounts and isolated flags
    text = re.sub(r"([\d,]+\.\d{2})\s*\n\s*\(([CcRrDdEeBbIitT]{2,6})\)", r"\1(\2)", text)
    text = re.sub(r"([\d,]+\.\d{2})\s*\n\s*([Cc][Rr]|[Dd][Rr])\b", r"\1(\2)", text)

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    merged = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Match start of a split date row (e.g., "19-06-" or "19-06- 1005000.00 1011867.59")
        m_date_prefix = re.match(r"^(\d{1,2}[/-]\d{1,2}[/-])\s*(.*)$", line)

        if m_date_prefix:
            prefix_date = m_date_prefix.group(1).rstrip("-/")
            top_rest = m_date_prefix.group(2).strip()

            # Look ahead up to 3 lines for the closing year '20XX'
            matched_k = None
            year_val = None
            rest_k = ""

            for k in range(1, min(4, len(lines) - i)):
                next_line = lines[i + k]
                m_year = re.match(r"^(\d{4})\b\s*(.*)$", next_line)
                if m_year:
                    matched_k = k
                    year_val = m_year.group(1)
                    rest_k = m_year.group(2).strip()
                    break

            if matched_k is not None:
                # Collect all intermediate description body lines
                body_lines = [lines[i + j] for j in range(1, matched_k)]
                body_text = " ".join(body_lines).strip()
                reconstructed_date = f"{prefix_date}-{year_val}"

                top_amts = re.findall(r"[\d,]+\.\d{2}", top_rest)
                top_flags = re.findall(r"\((?:Cr|Dr|CR|DR|DEBIT|CREDIT)\)", top_rest, re.I)
                k_flags = re.findall(r"\((?:Cr|Dr|CR|DR|DEBIT|CREDIT)\)", rest_k, re.I)
                body_amts = re.findall(r"[\d,]+\.\d{2}", body_text)

                # Tier Case 1: Top has 2 amounts, bottom has 2 flags e.g. "(Cr) (Cr)", middle has description
                # Produces: "19-06-2025 A197230 FRM KCC 5031290 1005000.00(Cr) 1011867.59(Cr)"
                if len(top_amts) == 2 and not top_flags and len(k_flags) >= 2:
                    flag1 = k_flags[0]
                    flag2 = k_flags[1]
                    merged.append(f"{reconstructed_date} {body_text} {top_amts[0]}{flag1} {top_amts[1]}{flag2}")

                # Tier Case 2: Top has Balance, middle has Txn Amount with flag, bottom has Balance flag "(Cr)"
                # Produces: "19-06-2025 U3680388 UPIAR/... 2000.00(Dr) 1009867.59(Cr)"
                elif len(top_amts) == 1 and not top_flags and len(body_amts) >= 1 and len(k_flags) >= 1:
                    bal_amt = top_amts[0]
                    bal_flag = k_flags[0]
                    merged.append(f"{reconstructed_date} {body_text} {bal_amt}{bal_flag}")

                # Tier Case 3: Standard wrap assembly
                else:
                    full_row = f"{reconstructed_date} {body_text} {top_rest} {rest_k}".strip()
                    full_row = re.sub(r"\s+", " ", full_row)
                    merged.append(full_row)

                i += matched_k + 1
                continue

        merged.append(line)
        i += 1

    return "\n".join(merged)


def extract_pdf_contents(file_bytes: bytes, password: str = None) -> Tuple[List[str], List[str], int]:
    pages_text = []
    pages_layout = []

    with pdfplumber.open(io.BytesIO(file_bytes), password=password) as pdf:
        total_pages = len(pdf.pages)
        logger.info(f"=== [RAW DATA] Starting extraction for {total_pages} pages ===")

        for idx, page in enumerate(pdf.pages):
            raw_text = page.extract_text() or ""
            raw_layout_text = page.extract_text(layout=True) or ""

            text = reassemble_split_lines(raw_text)
            layout_text = reassemble_split_lines(raw_layout_text)

            pages_text.append(text)
            pages_layout.append(layout_text)

    return pages_text, pages_layout, total_pages


def process_bank_statement(
    file_bytes: bytes,
    password: str = None,
    openai_client=None,
) -> Dict[str, Any]:
    pages_text, pages_layout, total_pages = extract_pdf_contents(file_bytes, password)
    logger.info(f"[PDF Engine] Loaded {total_pages} pages successfully.")

    meta = detect_bank_and_metadata(pages_text)
    opening_balance = meta.get("openingBalance")
    closing_balance = meta.get("closingBalance")

    logger.info(f"[Bank Detection] Bank: {meta.get('bank')} | A/C: {meta.get('accountNumber')}")

    sample_text, is_layout = build_ai_table_sample(pages_text, pages_layout)
    detection = classify_table_layout_with_llm(sample_text, openai_client)
    logger.info(f"[AI Classifier] Selected: {detection.parser} (Confidence: {detection.confidence})")

    candidate_queue = [detection.parser] + [
        pid for pid in PARSER_REGISTRY.keys() if pid != detection.parser
    ]

    best_result = None
    highest_score = -1.0

    for parser_id in candidate_queue:
        if parser_id not in PARSER_REGISTRY:
            continue

        parser_entry = PARSER_REGISTRY[parser_id]

        try:
            raw_txns = parser_entry["fn"](pages_text, pages_layout, meta)
            val_result = validate_and_score_transactions(raw_txns, opening_balance, closing_balance)

            reconciled = "PASSED" if val_result.details.get("reconciliationVerified") else "FAILED"
            logger.info(
                f"[Parser Engine] {parser_id:<9} -> {len(raw_txns):>4} txns | Score: {val_result.score:.3f} | Reconciliation: {reconciled}"
            )

            if val_result.score > highest_score:
                highest_score = val_result.score
                best_result = (parser_id, raw_txns, val_result)

            if val_result.is_valid and val_result.score >= 0.85:
                logger.info(f"[Parser Engine] Validation passed on {parser_id}. Halting queue.")
                break

        except Exception as e:
            logger.warning(f"[Parser Engine] {parser_id} failed: {e}")

    if not best_result or not best_result[2].is_valid:
        logger.warning("[FALLBACK] Deterministic parsers failed. Invoking LLM Page Fallback.")
        fallback_txns = parse_with_chunked_llm_fallback(pages_text, openai_client)
        val_result = validate_and_score_transactions(fallback_txns, opening_balance, closing_balance)
        best_result = ("AI_FALLBACK_PARSER", fallback_txns, val_result)

    final_parser_id, final_txns, final_val = best_result

    return {
        "status": "success",
        "bank": meta.get("bank", "Detected Bank"),
        "accountNumber": meta.get("accountNumber", "N/A"),
        "accountHolder": meta.get("accountHolder", "Account Holder"),
        "accountType": meta.get("accountType", "Savings / Current"),
        "parser": final_parser_id,
        "parserConfidence": detection.confidence if final_parser_id == detection.parser else final_val.score,
        "summary": final_val.details,
        "transactions": final_txns,
    }