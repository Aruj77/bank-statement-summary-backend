import os
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq

from core.pdf_processor import process_bank_statement
from core.metadata_extractor import extract_statement_metadata_with_ai

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BankStatementAPI")

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
groq_client = None

if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
    logger.info("Groq client initialized successfully.")
else:
    logger.warning("GROQ_API_KEY not found. Heuristics fallback active.")


@app.route("/parse-pdf", methods=["POST"])
@app.route("/api/extract-statement", methods=["POST"])
def extract_statement_api():
    if "file" not in request.files:
        logger.warning("[API] Request rejected: Missing file in form-data.")
        return jsonify({"status": "error", "message": "No file uploaded in form-data"}), 400

    file = request.files["file"]
    password = request.form.get("password", None)
    file_bytes = file.read()

    if not file_bytes:
        logger.warning("[API] Request rejected: Uploaded file is empty.")
        return jsonify({"status": "error", "message": "Uploaded file is empty"}), 400

    logger.info(f"[API] Processing '{file.filename}' ({len(file_bytes) / 1024:.1f} KB)...")

    try:
        result = process_bank_statement(
            file_bytes=file_bytes,
            password=password,
            openai_client=groq_client,
        )
        tx_count = result.get("summary", {}).get("transactionCount", len(result.get("transactions", [])))
        logger.info(f"[API] Successfully parsed '{file.filename}' -> {tx_count} transactions via {result.get('parser')}.")
        return jsonify(result), 200

    except ValueError as ve:
        err_msg = str(ve)
        logger.warning(f"[API] Auth/Validation error on '{file.filename}': {err_msg}")
        status_code = 401 if "PASSWORD" in err_msg.upper() else 400
        return jsonify({"status": "error", "message": err_msg}), status_code

    except Exception as e:
        logger.error(f"[API] Processing failed on '{file.filename}': {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to parse bank statement PDF"}), 500


@app.route("/api/extract-metadata", methods=["POST"])
def extract_metadata_api():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400

    file = request.files["file"]
    password = request.form.get("password", None)
    file_bytes = file.read()

    if not file_bytes:
        return jsonify({"status": "error", "message": "File is empty"}), 400

    try:
        metadata = extract_statement_metadata_with_ai(
            file_bytes=file_bytes,
            password=password,
            openai_client=groq_client,
        )
        return jsonify({"status": "success", "data": metadata.model_dump()}), 200
    except Exception as e:
        logger.error(f"[API] Metadata extraction error: {e}")
        return jsonify({"status": "error", "message": "Failed to extract metadata"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)