from datetime import datetime

def sort_transactions_by_date(transactions):
    def parse_txn_date(txn):
        date_str = txn.get("date", "")
        for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d %b %Y", "%d-%m-%Y", "%d-%m-%y"):
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue
        return datetime.min

    return sorted(transactions, key=parse_txn_date)


def parse_amount(value):
    if not value:
        return 0.0
    try:
        clean_val = str(value).replace("₹", "").replace(",", "").strip()
        return float(clean_val)
    except ValueError:
        return 0.0