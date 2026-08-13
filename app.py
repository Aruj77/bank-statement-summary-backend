from flask import Flask, jsonify, request
from flask_cors import CORS
import pdfplumber
import traceback

from parsers.axis_parser import parse_axis_transactions, extract_axis_account_number
from parsers.hdfc_parser import parse_hdfc_transactions, extract_hdfc_account_number
from parsers.icici_parser import parse_icici_transactions, extract_icici_metadata
from parsers.ippb_sbi_parser import parse_ippb_sbi_transactions, extract_ippb_sbi_account_number
from parsers.indus_parser import parse_indusind_transactions, extract_indusind_account_number
from parsers.kotak_parser import parse_kotak_transactions, extract_kotak_account_number
from parsers.pnb_parser import parse_pnb_transactions, extract_pnb_account_number

app = Flask(__name__)
CORS(app)

def detect_bank_from_text(all_lines, requested_bank="", transaction_start_index=None):
    if requested_bank in ["icici", "hdfc", "axis", "ippb", "sbi", "indusind", "kotak", "pnb"]:
        return "ippb_sbi" if requested_bank in ["ippb", "sbi"] else requested_bank

    if transaction_start_index is None:
        transaction_start_index = len(all_lines)

    # Only look at text appearing BEFORE transactions start to avoid picking up bank names in transaction descriptions/remarks
    header_lines = all_lines[:transaction_start_index]
    combined_text = " ".join(header_lines).lower()

    if "icici bank" in combined_text or "icici" in combined_text:
        return "icici"
    elif "hdfc bank" in combined_text or "hdfc" in combined_text:
        return "hdfc"
    elif "axis bank" in combined_text or "axis" in combined_text or "utib" in combined_text:
        return "axis"
    elif "ippb/sbi" in combined_text or "india post payments bank" in combined_text or "state bank of india" in combined_text:
        return "ippb_sbi"
    elif "indusind bank" in combined_text or "indusind" in combined_text:
        return "indusind"
    elif "kotak bank" in combined_text or "kotak" in combined_text:
        return "kotak"
    elif "pnb bank" in combined_text or "punb" in combined_text or "pnb" in combined_text:
        return "pnb"

    return None

@app.route('/parse-pdf', methods=['POST'])
def parse_pdf_endpoint():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        requested_bank = request.form.get('bankType', '').lower()
        password = request.form.get('password', '')
        
        lines = []
        
        with pdfplumber.open(file, password=password) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    page_lines = text.split('\n')
                    lines.extend(page_lines)
            
        clean_lines = [l.strip() for l in lines if l.strip()]
        
        # Determine where transactions begin to ignore bank names inside transaction rows/descriptions
        transaction_start_idx = len(clean_lines)
        for idx, line in enumerate(clean_lines):
            lower_line = line.lower()
            if any(k in lower_line for k in ["opening balance", "tran date", "transaction date", "particulars", "chq no"]):
                transaction_start_idx = idx
                break

        bank_type = detect_bank_from_text(clean_lines, requested_bank, transaction_start_idx)
        print(bank_type)
        
        account_number = "Unknown"
        account_holder = "Unknown"
        account_type = "Savings Account"
        transactions = []
        
        if bank_type == 'axis':
            transactions = parse_axis_transactions(clean_lines)
            account_number = extract_axis_account_number(lines)
        elif bank_type == 'hdfc':
            transactions = parse_hdfc_transactions(clean_lines)
            account_number = extract_hdfc_account_number(lines)
        elif bank_type == 'icici':
            transactions = parse_icici_transactions(clean_lines)
            metadata = extract_icici_metadata(lines)
            account_number = metadata["accountNumber"]
            account_holder = metadata["accountHolder"]
            account_type = metadata["accountType"]
        elif bank_type == 'ippb_sbi':
            transactions = parse_ippb_sbi_transactions(clean_lines)
            account_number = extract_ippb_sbi_account_number(lines)
        elif bank_type == 'indusind':
            transactions = parse_indusind_transactions(clean_lines)
            account_number = extract_indusind_account_number(lines)
        elif bank_type == 'kotak':
            transactions = parse_kotak_transactions(clean_lines)
            account_number = extract_kotak_account_number(lines)
        elif bank_type == 'pnb':
            transactions = parse_pnb_transactions(clean_lines)
            account_number = extract_pnb_account_number(lines)
        else:
            return jsonify({'error': f'Could not automatically detect bank or "{bank_type}" bank type not implemented.'}), 400
            
        if not transactions:
            return jsonify({'error': 'No transactions found in statement.'}), 404
            
        return jsonify({
            "bank": bank_type.upper(),
            "accountNumber": account_number,
            "accountHolder": account_holder,
            "accountType": account_type,
            "transactions": transactions
        })

    except Exception as e:
        err_str = str(e).lower()
        if "password" in err_str or "pdfminer" in err_str or "incorrect" in err_str:
            return jsonify({'error': 'PDF is password protected or password is incorrect. Please provide the correct password.'}), 400
        
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == "__main__":
    app.run(port=5000, debug=False)