import logging
import re
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Any, Optional

logger = logging.getLogger("BankStatementEngine")


class ValidationResult:
    def __init__(self, is_valid: bool, score: float, details: Dict[str, Any]):
        self.is_valid = is_valid
        self.score = score
        self.details = details


def _safe_decimal(val: Any) -> Decimal:
    if val is None:
        return Decimal("0.00")
    clean = re.sub(r"[^\d.-]", "", str(val).replace(",", "").replace("₹", "").replace("Rs.", "").strip())
    if not clean or clean == "-":
        return Decimal("0.00")
    try:
        return Decimal(clean)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def validate_and_score_transactions(
    transactions: List[Dict[str, Any]],
    declared_opening: Optional[Any] = None,
    declared_closing: Optional[Any] = None,
) -> ValidationResult:
    if not transactions:
        return ValidationResult(False, 0.0, {"transactionCount": 0, "reason": "Zero transactions parsed"})

    total_count = len(transactions)
    valid_dates = 0
    valid_amounts = 0
    valid_descriptions = 0
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")

    for t in transactions:
        if t.get("date") and len(str(t["date"]).strip()) >= 8:
            valid_dates += 1
        amt = _safe_decimal(t.get("amount", t.get("txnAmount", 0)))
        if amt > Decimal("0.00"):
            valid_amounts += 1

        desc = str(t.get("description", t.get("remarks", ""))).strip()
        if desc and desc != "—" and not desc.isdigit() and len(desc) > 2:
            valid_descriptions += 1

        total_debit += _safe_decimal(t.get("withdrawal", 0))
        total_credit += _safe_decimal(t.get("deposit", 0))

    date_score = valid_dates / total_count
    amount_score = valid_amounts / total_count
    desc_score = valid_descriptions / total_count

    first_bal = _safe_decimal(transactions[0].get("balance", "0.00"))
    last_bal = _safe_decimal(transactions[-1].get("balance", "0.00"))

    # Forward running balance: First Balance + Sum(subsequent credits) - Sum(subsequent debits) == Last Balance
    subsequent_credit = sum((_safe_decimal(t.get("deposit", 0)) for t in transactions[1:]), Decimal("0.00"))
    subsequent_debit = sum((_safe_decimal(t.get("withdrawal", 0)) for t in transactions[1:]), Decimal("0.00"))
    forward_diff = abs((first_bal + subsequent_credit - subsequent_debit) - last_bal)

    # Reverse running balance (newest to oldest statements)
    prior_credit = sum((_safe_decimal(t.get("deposit", 0)) for t in transactions[:-1]), Decimal("0.00"))
    prior_debit = sum((_safe_decimal(t.get("withdrawal", 0)) for t in transactions[:-1]), Decimal("0.00"))
    reverse_diff = abs((last_bal + prior_credit - prior_debit) - first_bal)

    TOLERANCE = Decimal("1.00")
    if forward_diff <= TOLERANCE:
        reconciliation_passed = True
        audit_diff = forward_diff
        calc_opening = first_bal - _safe_decimal(transactions[0].get("deposit", 0)) + _safe_decimal(transactions[0].get("withdrawal", 0))
        calc_closing = last_bal
    elif reverse_diff <= TOLERANCE:
        reconciliation_passed = True
        audit_diff = reverse_diff
        calc_opening = last_bal - _safe_decimal(transactions[-1].get("deposit", 0)) + _safe_decimal(transactions[-1].get("withdrawal", 0))
        calc_closing = first_bal
    else:
        audit_diff = min(forward_diff, reverse_diff)
        reconciliation_passed = audit_diff <= Decimal("5.00")
        calc_opening = first_bal
        calc_closing = last_bal

    # Penalize micro-results (< 5 rows) when auditing multi-page statements
    volume_factor = min(1.0, total_count / 10.0)

    reconciliation_score = 1.0 if reconciliation_passed else max(0.0, 1.0 - float(audit_diff / max(abs(calc_closing), Decimal("1.00"))))

    base_score = (
        (date_score * 0.35)
        + (amount_score * 0.35)
        + (reconciliation_score * 0.20)
        + (desc_score * 0.10)
    )

    final_score = base_score * volume_factor
    is_valid = (date_score >= 0.80 and amount_score >= 0.80 and total_count > 0)

    return ValidationResult(
        is_valid=is_valid,
        score=round(final_score, 4),
        details={
            "transactionCount": total_count,
            "totalCredit": f"{total_credit:.2f}",
            "totalDebit": f"{total_debit:.2f}",
            "openingBalance": f"{calc_opening:.2f}",
            "closingBalance": f"{calc_closing:.2f}",
            "reconciliationVerified": reconciliation_passed,
            "auditDifference": f"{audit_diff:.2f}",
        },
    )