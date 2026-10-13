"""Stateless, CSV-only web entry point for the Vercel synthetic demonstration.

No sessions, account store, model calls or generated reports are written to disk.
A report request submits the original input again and runs the checks again.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from passport.parse import StatementError
from passport.pipeline import run
from passport.report import build_pdf

ROOT = Path(__file__).resolve().parent
DEMO_DIR = ROOT / "synthetic_demo"
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_EVIDENCE_ROWS = 100
CURRENCIES = {
    "GBP": "GBP — British pound",
    "SGD": "SGD — Singapore dollar",
    "INR": "INR — Indian rupee",
    "USD": "USD — US dollar",
    "EUR": "EUR — Euro",
    "HKD": "HKD — Hong Kong dollar",
    "MYR": "MYR — Malaysian ringgit",
    "AUD": "AUD — Australian dollar",
    "CAD": "CAD — Canadian dollar",
    "NZD": "NZD — New Zealand dollar",
}


def scenarios():
    """Load the generated fixture manifest; IDs are the only public file selector."""
    manifest = json.loads((DEMO_DIR / "manifest.json").read_text())
    return manifest["scenarios"]


def scenario_path(item):
    path = (DEMO_DIR / item["file"]).resolve()
    if path.parent != DEMO_DIR.resolve() or path.suffix.lower() != ".csv":
        raise ValueError("Invalid bundled scenario path")
    return path


def validate_csv(data):
    if not data:
        raise StatementError("The CSV is empty. Choose a file with a header and at least two transaction rows.")
    if len(data) > MAX_CSV_BYTES:
        raise StatementError("The CSV is too large. Use a file smaller than 2 MB.")
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise StatementError("Save the CSV as UTF-8 text, then try again.") from error
    if "\x00" in decoded:
        raise StatementError("The file contains binary data. Choose a UTF-8 CSV file.")
    try:
        reader = csv.reader(io.StringIO(decoded), strict=True)
        header = next(reader, None)
        if not header:
            raise StatementError("The CSV needs a header row.")
        if len(header) > 20:
            raise StatementError("The CSV has too many columns. Use the provided four-column template.")
        count = 0
        for row in reader:
            if not row:
                continue
            count += 1
            if count > MAX_ROWS:
                raise StatementError("The CSV has more than 5,000 transaction rows. Use a smaller sample.")
            if len(row) != len(header):
                raise StatementError(f"CSV row {count + 1} has a different number of columns from the header.")
            if any(len(value) > 2000 for value in row):
                raise StatementError(f"CSV row {count + 1} has a field longer than 2,000 characters.")
    except csv.Error as error:
        raise StatementError("The CSV could not be read. Check its quoting and column separators.") from error


def read_input():
    if request.is_json:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise StatementError("Choose a sample scenario or upload a CSV file.")
        scenario_id = payload.get("scenario_id")
        if not isinstance(scenario_id, str):
            raise StatementError("Choose one of the available sample scenarios.")
    else:
        scenario_id = request.form.get("scenario_id")
    if scenario_id:
        selected = next((item for item in scenarios() if item["id"] == scenario_id), None)
        if not selected:
            raise StatementError("That sample scenario is not available.")
        data = scenario_path(selected).read_bytes()
        validate_csv(data)
        return selected["file"], data, selected["currency"], selected
    upload = request.files.get("statement")
    if upload is None or not upload.filename:
        raise StatementError("Choose a CSV file first.")
    original_name = upload.filename
    if len(original_name) > 160 or not original_name.lower().endswith(".csv"):
        raise StatementError("Choose a CSV file with a filename of no more than 160 characters. PDF reading is not enabled in this demo.")
    currency = request.form.get("currency", "")
    if currency not in CURRENCIES:
        raise StatementError("Select the currency used in your CSV. It cannot be inferred from the amounts.")
    data = upload.stream.read(MAX_CSV_BYTES + 1)
    validate_csv(data)
    name = secure_filename(original_name) or "uploaded.csv"
    return name, data, currency, None


def serialise_finding(finding):
    evidence = []
    for index, row in finding.evidence.head(MAX_EVIDENCE_ROWS).iterrows():
        record = {
            "row": int(index) + 1,
            "date": row["date"].strftime("%d %b %Y"),
            "description": str(row["description"]),
            "amount": float(row["amount"]),
            "balance": float(row["balance"]),
        }
        if "expected_balance" in row:
            record["expected_balance"] = float(row["expected_balance"])
        evidence.append(record)
    return {
        "label": finding.label,
        "ok": bool(finding.ok),
        "detail": finding.detail,
        "evidence": evidence,
        "evidence_total": len(finding.evidence),
    }


def create_app():
    app = Flask(__name__, static_folder="public", static_url_path="")
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_CSV_BYTES + 128 * 1024,
        MAX_FORM_MEMORY_SIZE=64 * 1024,
        MAX_FORM_PARTS=10,
    )

    @app.after_request
    def protect_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(StatementError)
    def input_error(error):
        return jsonify(error=str(error)[:600]), 400

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error="The request is too large. Use one CSV file smaller than 2 MB."), 413

    @app.get("/")
    def index():
        return render_template("index.html", currencies=CURRENCIES)

    @app.get("/api/scenarios")
    def list_scenarios():
        return jsonify(scenarios=scenarios())

    @app.get("/samples/<scenario_id>.csv")
    def download_sample(scenario_id):
        selected = next((item for item in scenarios() if item["id"] == scenario_id), None)
        if selected is None:
            return jsonify(error="That sample is not available."), 404
        return send_file(scenario_path(selected), mimetype="text/csv", as_attachment=True, download_name=selected["file"])

    @app.get("/samples/download.zip")
    def download_bundle():
        return send_file(DEMO_DIR / "credit-passport-synthetic-demo.zip", mimetype="application/zip", as_attachment=True)

    @app.post("/api/analyse")
    def analyse():
        name, data, currency, selected = read_input()
        result = run(name, data, currency)
        if len(result.transactions) > MAX_ROWS:
            raise StatementError("The CSV has more than 5,000 transaction rows. Use a smaller sample.")
        passport = result.passport
        balanced = bool(passport["verified"])
        findings = passport["findings"] if balanced else passport["findings"][:1]
        return jsonify(
            filename=name,
            source="Bundled synthetic sample" if selected else "Uploaded CSV · source unverified",
            scenario_id=selected["id"] if selected else None,
            currency=result.currency,
            row_count=len(result.transactions),
            balanced=balanced,
            period=[day.isoformat() for day in passport["period"]],
            metrics={
                "income": passport["monthly_income"],
                "rent": passport["monthly_rent"],
                "ratio": passport["rent_to_income"],
            } if balanced else None,
            findings=[serialise_finding(finding) for finding in findings],
        )

    @app.post("/api/report")
    def report():
        name, data, currency, selected = read_input()
        result = run(name, data, currency)
        if not result.passport["verified"]:
            return jsonify(error="A report cannot be issued because the balance or date-order check failed."), 422
        pdf = build_pdf(
            result.passport, result.currency, None, "", source=(
                "Synthetic CSV demo" if selected else "Uploaded CSV; source unverified"
            ), demo=True,
        )
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name="credit-passport-demo.pdf")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
