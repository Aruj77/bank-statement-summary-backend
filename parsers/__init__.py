from typing import Callable, Dict, Any
from parsers.parser_a import parse_parser_a
from parsers.parser_b import parse_parser_b
from parsers.parser_c import parse_parser_c
from parsers.parser_d import parse_parser_d
from parsers.parser_e import parse_parser_e

PARSER_REGISTRY: Dict[str, Dict[str, Any]] = {
    "PARSER_A": {
        "fn": parse_parser_a,
        "description": "Date | Description/Particulars | Single Transaction Amount | Balance",
        "features": [
            "Single transaction amount column",
            "Closing balance present",
            "Debit/Credit calculated based on running balance differences",
        ],
    },
    "PARSER_B": {
        "fn": parse_parser_b,
        "description": "Date | Description/Narration | Debit (Withdrawal) | Credit (Deposit) | Balance",
        "features": [
            "Separate Debit column",
            "Separate Credit column",
            "Closing balance present",
        ],
    },
    "PARSER_C": {
        "fn": parse_parser_c,
        "description": "Date | Particulars | Amount with (CR/DR) Flag | Balance",
        "features": [
            "Single amount column with CR/DR attached or in an adjacent flag column",
            "Closing balance present",
        ],
    },
    "PARSER_D": {
        "fn": parse_parser_d,
        "description": "Txn Date | Value Date | Chq/Ref No | Description | Debit | Credit | Balance",
        "features": [
            "Multiple date columns present (Txn Date vs Value Date)",
            "Cheque or reference number column exists",
        ],
    },
    "PARSER_E": {
        "fn": parse_parser_e,
        "description": "Two-line / Multi-line Wrapped Transaction Layout",
        "features": [
            "Transaction spans across 2-3 distinct lines consistently",
            "Date and Ref on Line 1, Description on Line 2, Amounts on Line 3",
        ],
    },
}


def get_dynamic_parser_prompt_definitions() -> str:
    output = []
    for pid, meta in PARSER_REGISTRY.items():
        feats = ", ".join(meta["features"])
        output.append(f"- **{pid}**: {meta['description']} (Features: {feats})")
    return "\n".join(output)