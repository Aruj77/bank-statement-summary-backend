import os
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from groq import Groq

from core.pdf_processor import process_bank_statement

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Read API Key from environment variable or set your default fallback
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# Initialize Groq client safely
groq_client = None
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY)
else:
    app.logger.warning("GROQ_API_KEY not found in environment. Heuristics fallback will be used until key is set.")


@app.route("/parse-pdf", methods=["POST"])
@app.route("/api/extract-statement", methods=["POST"])
def extract_statement_api():
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded in form-data"}), 400

    file = request.files["file"]
    password = request.form.get("password", None)
    file_bytes = file.read()

    if not file_bytes:
        return jsonify({"status": "error", "message": "Uploaded file is empty"}), 400

    try:
        result = process_bank_statement(
            file_bytes=file_bytes,
            password=password,
            openai_client=groq_client,
        )
        return jsonify(result), 200
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        app.logger.error(f"Internal processing failure: {e}", exc_info=True)
        return jsonify({"status": "error", "message": "Failed to parse bank statement PDF"}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)