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
    """Helper to cleanly serialize Decimal objects in debug logs."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def reassemble_split_lines(text: str) -> str:
    """
    Normalizes multi-line wraps where the date or amounts are broken across distinct lines.
    """
    if not text:
        return ""

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

        # Case 2: "DD-MM-" on line i, "YYYY <Rest of Row>" on line i+1
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

        # Case 3: "DD-MM- <Amounts/Rest>" on line i, "YYYY <Rest of Row>" on line i+1
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

        # Case 4: Amount on line i, and "(Cr)" or "(Dr)" on line i+1
        if (
            re.match(r"^[\d,]+\.\d{2}$", line)
            and (i + 1 < len(lines))
            and re.match(r"^\(?(?:Dr|Cr|DR|CR)\)?$", lines[i + 1], re.I)
        ):
            merged.append(f"{line}{lines[i + 1]}")
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

            # Check if pdfplumber can extract structured grid tables directly
            tables = page.extract_tables()
            if tables and any(len(t) > 2 for t in tables):
                table_lines = []
                for t in tables:
                    for row in t:
                        if not row:
                            continue
                        cleaned_cells = []
                        for cell in row:
                            if cell:
                                # Normalize intra-cell line wraps e.g. "18-06-\n2025" -> "18-06-2025"
                                c_clean = re.sub(r"(\d{1,2}[/-]\d{1,2}[/-])\s*\n\s*(\d{2,4})", r"\1\2", cell)
                                c_clean = re.sub(r"([\d,]+\.?\d*)\s*\n\s*(\((?:Dr|Cr)\)|(?:DR|CR))", r"\1\2", c_clean, flags=re.I)
                                c_clean = " ".join(c_clean.split())
                                if c_clean:
                                    cleaned_cells.append(c_clean)
                        if cleaned_cells:
                            table_lines.append(" ".join(cleaned_cells))

                if table_lines:
                    text_result = "\n".join(table_lines)
                    pages_text.append(text_result)
                    pages_layout.append(text_result)
                    continue

            # Fallback to text parsing with split line reassembly
            text = reassemble_split_lines(raw_text)
            layout_text = reassemble_split_lines(raw_layout_text)

            pages_text.append(text)
            pages_layout.append(layout_text)

    return pages_text, pages_layout, total_pages


def process_bank_statement(
    file_bytes: bytes, password: str = None, openai_client=None
) -> Dict[str, Any]:
    # 1. PDF Text & Layout Extraction
    pages_text, pages_layout, total_pages = extract_pdf_contents(file_bytes, password)
    logger.info(f"[PDF] Loaded {total_pages} pages successfully")

    # 2. Bank Detection & Account Metadata Extraction
    meta = detect_bank_and_metadata(pages_text)
    logger.info(
        f"[Bank Detection] Detected: {meta.get('bank', 'Detected Bank')} | "
        f"A/C: {meta.get('accountNumber', 'N/A')} | "
        f"Holder: {meta.get('accountHolder', 'Account Holder')} | "
        f"Type: {meta.get('accountType', 'Savings Account')} | "
        f"Opening Bal: {meta.get('openingBalance')} | Closing Bal: {meta.get('closingBalance')}"
    )

    # 3. Build Minimal Masked Table Sample
    sample_text, is_layout = build_ai_table_sample(pages_text, pages_layout)
    logger.info(
        f"\n==================== [RAW AI SAMPLE DATA] (is_layout={is_layout}) ====================\n"
        f"{sample_text}\n"
        f"===================================================================================="
    )

    # 4. Token-Efficient AI Layout Classification
    detection = classify_table_layout_with_llm(sample_text, openai_client)
    logger.info(
        f"[AI Classifier] Selected: {detection.parser} (Confidence: {detection.confidence}) | "
        f"Method: {detection.debit_credit_method} | Multiline: {detection.multiline_transactions}"
    )

    # 5. Candidate Ranking Loop
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

            formatted_txns = json.dumps(raw_txns[:5], indent=2, cls=DecimalEncoder)
            logger.info(
                f"\n--- [RAW PARSED SAMPLE - FIRST {min(5, len(raw_txns))} of {len(raw_txns)} ROWS via {parser_id}] ---\n"
                f"{formatted_txns}\n"
                f"{'-' * 60}"
            )

            val_result = validate_and_score_transactions(
                raw_txns, meta.get("openingBalance"), meta.get("closingBalance")
            )

            logger.info(
                f"[Validation] {parser_id} -> Count: {len(raw_txns)}, Score: {val_result.score}, "
                f"Reconciliation: {'PASSED' if val_result.details.get('reconciliationVerified') else 'FAILED'} | "
                f"Audit Diff: {val_result.details.get('auditDifference')}"
            )

            if val_result.score > highest_score:
                highest_score = val_result.score
                best_result = (parser_id, raw_txns, val_result)

            if val_result.is_valid and val_result.score >= 0.85:
                logger.info(f"[SUCCESS] {parser_id} passed validation threshold.")
                break
        except Exception as e:
            logger.warning(f"[Parser Engine] {parser_id} failed with error: {e}", exc_info=True)

    # 6. Safe LLM Fallback (only triggered if deterministic parsers fail)
    if not best_result or not best_result[2].is_valid:
        logger.warning("[FALLBACK] Deterministic parsers failed validation. Invoking LLM Page Fallback.")
        fallback_txns = parse_with_chunked_llm_fallback(pages_text, openai_client)
        val_result = validate_and_score_transactions(fallback_txns)
        best_result = ("AI_FALLBACK_PARSER", fallback_txns, val_result)

    final_parser_id, final_txns, final_val = best_result

    response_payload = {
        "status": "success",
        "bank": meta.get("bank", "Detected Bank"),
        "accountNumber": meta.get("accountNumber", "N/A"),
        "accountHolder": meta.get("accountHolder", "Account Holder"),
        "accountType": meta.get("accountType", "Savings / Current"),
        "parser": final_parser_id,
        "parserConfidence": (
            detection.confidence
            if final_parser_id == detection.parser
            else final_val.score
        ),
        "summary": final_val.details,
        "transactions": final_txns,
    }

    logger.info(
        f"\n==================== [FINAL OUTPUT SUMMARY] ====================\n"
        f"Status: {response_payload['status']} | Bank: {response_payload['bank']} | "
        f"A/C: {response_payload['accountNumber']} | Holder: {response_payload['accountHolder']}\n"
        f"Parser: {response_payload['parser']} | Confidence: {response_payload['parserConfidence']}\n"
        f"Summary: {json.dumps(response_payload['summary'], indent=2)}\n"
        f"Total Extracted Rows: {len(response_payload['transactions'])}\n"
        f"================================================================"
    )

    return response_payload