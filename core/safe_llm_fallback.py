import json
import logging
from typing import List, Dict, Any
from core.normalizer import parse_decimal, clean_description, parse_date

logger = logging.getLogger("BankStatementEngine")


def parse_with_chunked_llm_fallback(
    pages_text: List[str], openai_client=None
) -> List[Dict[str, Any]]:
    if not openai_client:
        logger.error("[LLM Fallback] No client configured.")
        return []

    extracted_transactions = []
    global_index = 0

    system_prompt = (
        "You are a strict bank statement transaction extractor. "
        "Extract all transaction rows from the page text into a valid JSON array.\n"
        "Return ONLY a JSON array of objects with keys: "
        "date, description, withdrawal, deposit, balance."
    )

    for page_num, page_text in enumerate(pages_text):
        if not page_text.strip():
            continue

        try:
            response = openai_client.chat.completions.create(
                model="groq/compound-mini",
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"Extract transactions from Page {page_num + 1}:\n\n{page_text}",
                    },
                ],
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            rows = data.get("transactions", data if isinstance(data, list) else [])

            for row in rows:
                date_str = parse_date(row.get("date"))
                narration = clean_description(row.get("description"))
                withdrawal = float(parse_decimal(row.get("withdrawal")))
                deposit = float(parse_decimal(row.get("deposit")))
                balance_val = float(parse_decimal(row.get("balance")))
                txn_type = "DEBIT" if withdrawal > 0 else "CREDIT"
                txn_amt = withdrawal if txn_type == "DEBIT" else deposit

                extracted_transactions.append({
                    "_index": global_index,
                    "sNo": global_index + 1,
                    "date": date_str,
                    "valueDate": date_str,
                    "remarks": narration if narration else "—",
                    "description": narration if narration else "—",
                    "txnAmount": txn_amt,
                    "amount": txn_amt,
                    "withdrawal": withdrawal,
                    "deposit": deposit,
                    "balance": balance_val,
                    "type": txn_type,
                })
                global_index += 1

        except Exception as e:
            logger.error(f"[LLM Fallback] Failed on page {page_num + 1}: {e}")

    return extracted_transactions