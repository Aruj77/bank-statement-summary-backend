import os
import json
import re
import logging
from typing import Optional

from models import ParserDetection
from parsers import get_dynamic_parser_prompt_definitions

logger = logging.getLogger("BankStatementEngine")


def clean_json_markdown(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def get_groq_model() -> str:
    return os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()


def classify_table_layout_with_llm(sample_text: str, openai_client=None) -> ParserDetection:
    parser_defs = get_dynamic_parser_prompt_definitions()

    prompt = (
        "You are an expert bank statement table-format classifier.\n"
        "Analyze the sample and output ONLY a valid JSON object matching the schema.\n\n"
        f"Available Parsers:\n{parser_defs}\n\n"
        "JSON Schema:\n"
        "{\n"
        '  "parser": "PARSER_A | PARSER_B | PARSER_C | PARSER_D | PARSER_E",\n'
        '  "confidence": 0.95,\n'
        '  "table_type": "string",\n'
        '  "debit_credit_method": "string",\n'
        '  "multiline_transactions": false,\n'
        '  "layout_required": false,\n'
        '  "reasoning": "short explanation"\n'
        "}\n\n"
        f"### Statement Table Sample:\n{sample_text}\n"
    )

    model = get_groq_model()

    if openai_client:
        try:
            response = openai_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You classify bank statement layouts. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            raw_text = response.choices[0].message.content
            parsed = json.loads(clean_json_markdown(raw_text))
            result = ParserDetection(**parsed)
            logger.info(f"[AI Classifier] Selected {result.parser} (Confidence: {result.confidence}) via `{model}`.")
            return result
        except Exception as e:
            logger.warning(f"[AI Classifier] LLM `{model}` failed ({e}). Switching to heuristics.")

    # Deterministic Heuristic Fallback
    sample_upper = sample_text.upper()

    # Rule 1: PARSER_D
    if ("TXN NO" in sample_upper or "KIMS" in sample_upper) or re.search(r"\bS\d{7,10}\b", sample_text, re.I):
        logger.info("[AI Classifier] Heuristic matched PARSER_D (Txn No schema).")
        return ParserDetection(
            parser="PARSER_D",
            confidence=0.95,
            table_type="TXN_NO_DATE_DESC_DR_CR_BAL",
            debit_credit_method="EXPLICIT_COLUMNS",
            multiline_transactions=False,
            layout_required=False,
            reasoning="Detected transaction IDs / Parser D header markers.",
        )

    # Rule 2: PARSER_C
    if re.search(r"\d+\.\d{2}\s*\((?:Dr|Cr|DR|CR|DEBIT|CREDIT)\)", sample_text, re.I) or (
        re.search(r"\b(CR|DR|DEBIT|CREDIT)\b", sample_upper)
        and ("INSTRUMENT" in sample_upper or "TYPE" in sample_upper or re.search(r"\d+\.?\d*\s+(?:CR|DR)\s+\d+\.?\d*", sample_text, re.I))
    ):
        logger.info("[AI Classifier] Heuristic matched PARSER_C (Cr/Dr indicator schema).")
        return ParserDetection(
            parser="PARSER_C",
            confidence=0.95,
            table_type="DATE_AMT_CRDR_BAL_REMARKS",
            debit_credit_method="CR_DR_FLAG",
            multiline_transactions=False,
            layout_required=False,
            reasoning="Detected explicit CR/DR flags on amount tokens.",
        )

    # Rule 3: PARSER_B
    if ("WITHDRAWAL" in sample_upper and "DEPOSIT" in sample_upper) or ("DEBIT" in sample_upper and "CREDIT" in sample_upper):
        logger.info("[AI Classifier] Heuristic matched PARSER_B (Dual Dr/Cr column schema).")
        return ParserDetection(
            parser="PARSER_B",
            confidence=0.85,
            table_type="DATE_DESC_DR_CR_BAL",
            debit_credit_method="EXPLICIT_COLUMNS",
            multiline_transactions=False,
            layout_required=True,
            reasoning="Detected separate Withdrawal and Deposit columns.",
        )

    # Default Rule: PARSER_A
    logger.info("[AI Classifier] Defaulted to PARSER_A (Single amount column fallback).")
    return ParserDetection(
        parser="PARSER_A",
        confidence=0.60,
        table_type="DATE_DESC_AMT_BAL",
        debit_credit_method="BALANCE_DIFFERENCE",
        multiline_transactions=False,
        layout_required=False,
        reasoning="Default fallback schema.",
    )