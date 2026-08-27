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
from core.validator import validate_and_score_transactions, ValidationResult
from core.safe_llm_fallback import parse_with_chunked_llm_fallback
from parsers import PARSER_REGISTRY

logger = logging.getLogger("BankStatementEngine")


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def reassemble_split_lines(text: str) -> str:
    if not text:
        return ""

    # Pre-Pass: Fix floating (Cr)/(Dr) indicators that wrapped to a new line
    # Merges: "1005000.00 \n (Cr)" -> "1005000.00(Cr)"
    text = re.sub(r"([\d,]+\.\d{2})\s*\n\s*\((Cr|Dr|CR|DR|DEBIT|CREDIT)\)", r"\1(\2)", text, flags=re.I)

    lines = [line.strip() for line in text.split("\n") if line.strip()]
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Case 1: "DD-MM-" on line i, txn content on line i+1, "YYYY" on line i+2
        if (
            re.match(r"^\d{1,2}[/-]\d{1,2}[/-]$", line)
            and (i + 2 < len(lines))
            and re.match(r"^\d{4}$", lines[i + 2])
        ):
            prefix_date = line.rstrip("-/")
            body = lines[i + 1]
            year = lines[i + 2]
            merged.append(f"{prefix_date}-{year} {body}")
            i += 3
            continue

        # Case 2: "DD-MM-" on line i, "YYYY <Rest>" on line i+1
        if (
            re.match(r"^\d{1,2}[/-]\d{1,2}[/-]$", line)
            and (i + 1 < len(lines))
            and re.match(r"^\d{4}", lines[i + 1])
        ):
            prefix_date = line.rstrip("-/")
            next_line = lines[i + 1]
            year = next_line[:4]
            rest = next_line[4:].strip()
            merged.append(f"{prefix_date}-{year} {rest}")
            i += 2
            continue

        # Case 3: "DD-MM- <Rest>" on line i, "YYYY <Rest>" on line i+1
        m_split_date = re.match(r"^(\d{1,2}[/-]\d{1,2}[/-])\s+(.*)$", line)
        if m_split_date and (i + 1 < len(lines)) and re.match(r"^\d{4}\b", lines[i + 1]):
            prefix_date = m_split_date.group(1).rstrip("-/")
            top_rest = m_split_date.group(2)
            next_line = lines[i + 1]
            year = next_line[:4]
            bottom_rest = next_line[4:].strip()
            merged.append(f"{prefix_date}-{year} {bottom_rest} {top_rest}")
            i += 2
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

            # Standard Text Assembly
            text = reassemble_split_lines(raw_text)
            layout_text = reassemble_split_lines(raw_layout_text)

            pages_text.append(text)
            pages_layout.append(layout_text)

    return pages_text, pages_layout, total_pages


def process_bank_statement(
    file_bytes: bytes, password: str = None, openai_client=None
) -> Dict[str, Any]:
    pages_text, pages_layout, total_pages = extract_pdf_contents(file_bytes, password)
    logger.info(f"[PDF] Loaded {total_pages} pages successfully")

    meta = detect_bank_and_metadata(pages_text)
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
        logger.info(f"[Parser Engine] Executing {parser_id} on {total_pages} pages...")

        try:
            raw_txns = parser_entry["fn"](pages_text, pages_layout, meta)
            val_result = validate_and_score_transactions(
                raw_txns, meta.get("openingBalance"), meta.get("closingBalance")
            )

            if val_result.score > highest_score:
                highest_score = val_result.score
                best_result = (parser_id, raw_txns, val_result)

            if val_result.is_valid and val_result.score >= 0.85:
                logger.info(f"[SUCCESS] {parser_id} passed validation threshold.")
                break
        except Exception as e:
            logger.warning(f"[Parser Engine] {parser_id} failed with error: {e}", exc_info=True)

    if not best_result or not best_result[2].is_valid:
        logger.warning("[FALLBACK] Deterministic parsers failed validation. Invoking LLM Page Fallback.")
        fallback_txns = parse_with_chunked_llm_fallback(pages_text, openai_client)
        val_result = validate_and_score_transactions(fallback_txns)
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