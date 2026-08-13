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

def detect_bank_from_text(edge_samples, requested_bank=""):
    if requested_bank in ["icici", "hdfc", "axis", "ippb", "sbi", "indusind", "kotak", "pnb"]:
        return "ippb_sbi" if requested_bank in ["ippb", "sbi"] else requested_bank

    combined_text = " ".join(edge_samples).lower()
    print(combined_text)

    # Prioritize specific statement signatures to avoid cross-contamination from transaction rows
    if "statement of axis account" in combined_text or "utib00" in combined_text or "axis bank ltd" in combined_text:
        return "axis"
    elif "indusind bank" in combined_text or "indb" in combined_text or "indus delite" in combined_text or "reachus@indusind.com" in combined_text:
        return "indusind"
    elif "hdfc bank" in combined_text:
        return "hdfc"
    elif "icici bank" in combined_text:
        return "icici"
    elif "ippb/sbi" in combined_text or "india post payments bank" in combined_text or "state bank of india" in combined_text:
        return "ippb_sbi"
    elif "kotak bank" in combined_text or "kotak mahindra" in combined_text:
        return "kotak"
    elif "pnb bank" in combined_text or "punb" in combined_text or "punjab national bank" in combined_text:
        return "pnb"
    
    # Fallback checks if explicit names aren't matched
    if "axis" in combined_text:
        return "axis"
    elif "indusind" in combined_text or "indus" in combined_text:
        return "indusind"
    elif "icici" in combined_text:
        return "icici"
    elif "hdfc" in combined_text:
        return "hdfc"
    elif "kotak" in combined_text:
        return "kotak"
    elif "pnb" in combined_text:
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
        edge_sample = []
        bank_type = None
        
        with pdfplumber.open(file, password=password) as pdf:
            total_pages = len(pdf.pages)
            for idx, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    page_lines = text.split('\n')
                    lines.extend(page_lines)
                    
                    # Check first page (only first 30 lines) or last page for bank detection
                    if idx == 0:
                        first_page_lines = [l.strip() for l in page_lines if l.strip()][:30]
                        edge_sample.extend(first_page_lines)
                    elif idx == total_pages - 1:
                        last_page_lines = [l.strip() for l in page_lines if l.strip()]
                        edge_sample.extend(last_page_lines)
                    
                    # Try detecting bank early if we have enough sample and break loop if found
                    if idx == 0 or idx == total_pages - 1:
                        bank_type = detect_bank_from_text(edge_sample, requested_bank)
                        if bank_type and not requested_bank:
                            pass

        clean_lines = [l.strip() for l in lines if l.strip()]
        
        # Final fallback check for bank type if not already caught
        if not bank_type:
            bank_type = detect_bank_from_text(edge_sample, requested_bank)
            
        print(f"Detected Bank: {bank_type}")
        
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
    app.run(port=5000, debug=True)