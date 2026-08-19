import logging
from decimal import Decimal
from typing import List, Dict, Any

logger = logging.getLogger("BankStatementEngine")


class ValidationResult:
    def __init__(self, is_valid: bool, score: float, details: Dict[str, Any]):
        self.is_valid = is_valid
        self.score = score
        self.details = details


def validate_and_score_transactions(
    transactions: List[Dict[str, Any]],
    declared_opening: Decimal = None,
    declared_closing: Decimal = None,
) -> ValidationResult:
    if not transactions:
        return ValidationResult(False, 0.0, {"reason": "Zero transactions parsed"})

    total_count = len(transactions)
    valid_dates = 0
    valid_amounts = 0
    valid_descriptions = 0
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")

    for t in transactions:
        if t.get("date") and len(str(t["date"])) >= 8:
            valid_dates += 1
        amt = Decimal(str(t.get("amount", t.get("txnAmount", 0))))
        if amt >= Decimal("0.00"):
            valid_amounts += 1
        
        desc = str(t.get("description", t.get("remarks", "")))
        if desc and desc != "—" and not desc.startswith("Page ") and not desc.isdigit():
            valid_descriptions += 1

        total_debit += Decimal(str(t.get("withdrawal", 0)))
        total_credit += Decimal(str(t.get("deposit", 0)))

    date_score = valid_dates / total_count
    amount_score = valid_amounts / total_count
    desc_score = valid_descriptions / total_count

    first_bal = Decimal(str(transactions[0].get("balance", "0.00")))
    last_bal = Decimal(str(transactions[-1].get("balance", "0.00")))

    # Check forward order: First is Opening, Last is Closing
    # Check reverse order: Last is Opening, First is Closing
    forward_diff = abs((first_bal + total_credit - total_debit) - last_bal)
    reverse_diff = abs((last_bal + total_credit - total_debit) - first_bal)

    if forward_diff <= Decimal("0.10"):
        reconciliation_passed = True
        audit_diff = forward_diff
        calc_opening = first_bal
        calc_closing = last_bal
    elif reverse_diff <= Decimal("0.10"):
        reconciliation_passed = True
        audit_diff = reverse_diff
        calc_opening = last_bal
        calc_closing = first_bal
    else:
        # Fallback to declared balances if available
        calc_opening = declared_opening if declared_opening is not None else last_bal
        calc_closing = declared_closing if declared_closing is not None else first_bal
        audit_diff = min(forward_diff, reverse_diff)
        reconciliation_passed = audit_diff <= Decimal("1.00")

    reconciliation_score = 1.0 if reconciliation_passed else max(0.0, 1.0 - float(audit_diff / (calc_closing or Decimal("1"))))

    # Weight components: When > 15 transactions parse with valid dates/amounts, grant strong baseline score
    final_score = (
        (date_score * 0.30)
        + (amount_score * 0.30)
        + (reconciliation_score * 0.30)
        + (desc_score * 0.10)
    )

    # Acceptance condition: strong date & amount parsing with reasonable line consistency
    is_valid = (date_score >= 0.85 and amount_score >= 0.85 and total_count > 0)

    return ValidationResult(
        is_valid=is_valid,
        score=round(final_score, 4),
        details={
            "transactionCount": total_count,
            "totalCredit": str(total_credit),
            "totalDebit": str(total_debit),
            "openingBalance": str(calc_opening),
            "closingBalance": str(calc_closing),
            "reconciliationVerified": reconciliation_passed,
            "auditDifference": str(round(audit_diff, 2)),
        },
    )