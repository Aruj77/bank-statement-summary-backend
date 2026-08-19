import json
import re
import logging
from typing import Optional
from models import ParserDetection
from parsers import get_dynamic_parser_prompt_definitions

logger = logging.getLogger("BankStatementEngine")


def clean_json_markdown(text: str) -> str:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip()


def classify_table_layout_with_llm(
    sample_text: str, openai_client=None
) -> ParserDetection:
    parser_defs = get_dynamic_parser_prompt_definitions()

    prompt = (
        "You are an expert bank statement table-format classifier.\n"
        "Analyze this small sample of a statement table and identify its layout.\n"
        "DO NOT extract all transactions. Output ONLY a valid JSON object matching the schema.\n\n"
        f"Available Parsers:\n{parser_defs}\n\n"
        "JSON Schema:\n"
        "{\n"
        '  "parser": "PARSER_A | PARSER_B | PARSER_C | PARSER_D | PARSER_E",\n'
        '  "confidence": 0.95,\n'
        '  "table_type": "string",\n'
        '  "date_column": "string",\n'
        '  "value_date_column": "string or null",\n'
        '  "description_column": "string",\n'
        '  "amount_column": "string or null",\n'
        '  "debit_column": "string or null",\n'
        '  "credit_column": "string or null",\n'
        '  "balance_column": "string",\n'
        '  "reference_column": "string or null",\n'
        '  "debit_credit_method": "EXPLICIT_COLUMNS | CR_DR_FLAG | BALANCE_DIFFERENCE | VALUE_SIGN",\n'
        '  "multiline_transactions": false,\n'
        '  "layout_required": false,\n'
        '  "reasoning": "string"\n'
        "}\n\n"
        f"### Statement Table Sample:\n{sample_text}\n"
    )

    # Models prioritized from your active Groq quota list
    ACTIVE_MODELS = [
        "groq/compound-mini",
        "openai/gpt-oss-20b",
        "qwen/qwen3.6-27b",
        "allam-2-7b",
    ]

    if openai_client:
        for model_candidate in ACTIVE_MODELS:
            try:
                response = openai_client.chat.completions.create(
                    model=model_candidate,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    response_format={"type": "json_object"},
                )
                raw_text = response.choices[0].message.content
                cleaned_json = clean_json_markdown(raw_text)
                parsed = json.loads(cleaned_json)
                logger.info(f"[AI Classifier] Classified via active model: {model_candidate}")
                return ParserDetection(**parsed)
            except Exception as e:
                logger.warning(f"[AI Classifier] Model `{model_candidate}` failed: {e}")

    # Heuristic Fallback Analysis
    sample_upper = sample_text.upper()
    if re.search(r"\b(CR|DR)\b", sample_upper) and re.search(r"\d+\.\d+", sample_text):
        return ParserDetection(
            parser="PARSER_C",
            confidence=0.92,
            table_type="DATE_AMT_CRDR_BAL_REMARKS",
            debit_credit_method="CR_DR_FLAG",
            multiline_transactions=False,
            layout_required=False,
            reasoning="Heuristic detected Date + Amount + CR/DR Flag + Balance layout",
        )

    if ("WITHDRAWAL" in sample_upper and "DEPOSIT" in sample_upper) or ("DEBIT" in sample_upper and "CREDIT" in sample_upper):
        return ParserDetection(
            parser="PARSER_B",
            confidence=0.85,
            table_type="DATE_DESC_DR_CR_BAL",
            debit_credit_method="EXPLICIT_COLUMNS",
            multiline_transactions=False,
            layout_required=True,
            reasoning="Heuristic detected separate Debit and Credit columns",
        )

    return ParserDetection(
        parser="PARSER_A",
        confidence=0.60,
        table_type="DATE_DESC_AMT_BAL",
        debit_credit_method="BALANCE_DIFFERENCE",
        multiline_transactions=False,
        layout_required=False,
        reasoning="Heuristic default fallback",
    )