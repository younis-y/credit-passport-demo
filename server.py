"""Stateless, CSV-only web entry point for the Vercel synthetic demonstration.

No sessions, account store, model calls or generated reports are written to disk.
A report request submits the original input again and runs the checks again.
"""
from __future__ import annotations

import csv
import io
import json
import os
import secrets
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode

import anthropic
import pandas as pd
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from passport import agent, ai, paypal

from passport.parse import StatementError
from passport.pipeline import run
from passport.report import build_pdf

ROOT = Path(__file__).resolve().parent
DEMO_DIR = ROOT / "synthetic_demo"
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_EVIDENCE_ROWS = 100
PAYPAL_SOURCE = {"id": "paypal", "source": "PayPal sandbox account · rows fetched from PayPal", "report_source": "PayPal sandbox transaction history"}
PAYPAL_SAMPLE = {"id": "paypal-sample", "applicant": "Amira Haddad", "source": "Sample PayPal history · fictional applicant", "report_source": "Sample PayPal history (fictional)"}
DEFAULT_RULE = "Rent under 35% of income: hold one week's deposit and set up monthly rent."
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
    if scenario_id == "paypal":
        if not paypal.configured():
            raise StatementError("PayPal is not connected on this deployment.")
        data, currency = paypal.statement_csv()
        return "paypal-sandbox.csv", data, currency, PAYPAL_SOURCE
    if scenario_id == "paypal-sample":
        data, currency = paypal.sample_csv()
        return "paypal-sample.csv", data, currency, PAYPAL_SAMPLE
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


@lru_cache(maxsize=32)
def ai_reading(data, currency):
    """One Claude call per distinct statement; repeat clicks on a sample reuse it."""
    result = run("statement.csv", data, currency)
    return result.transactions, ai.read(result.transactions, result.passport["findings"], result.currency)


def ai_block(data, currency):
    try:
        tx, reading = ai_reading(data, currency)
    except (anthropic.APIError, ValueError, KeyError):
        return {"error": "Claude's reading is unavailable right now. The checks above are unaffected."}
    rows = [
        {"row": number, "date": row.date.strftime("%d %b %Y"), "description": str(row.description),
         "amount": float(row.amount), "label": reading["labels"].get(number, "unlabelled")}
        for number, row in enumerate(tx.head(ai.MAX_ROWS).itertuples(), start=1)
    ]
    return {"model": ai.MODEL, "note": reading["note"], "rows": rows, "total": len(tx)}


@lru_cache(maxsize=64)
def ask_claude(data, currency, question):
    result = run("statement.csv", data, currency)
    return ai.ask(result.transactions, result.passport["findings"], result.currency, question)


def rent_record(result):
    """'6/6': months with a rent payment out of months covered, from the rent finding's own rows."""
    rent = next((f for f in result.passport["findings"] if f.label == "Rent payments"), None)
    if rent is None or rent.evidence.empty:
        return None
    active = result.transactions[result.transactions["amount"] != 0]["date"]
    span = len(pd.period_range(active.min(), active.max(), freq="M"))
    return f"{rent.evidence['date'].dt.to_period('M').nunique()}/{span}"


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


def login_enabled():
    """Log in with PayPal must be switched on for the app in the PayPal developer dashboard first."""
    return paypal.configured() and os.environ.get("PAYPAL_LOGIN") == "1"


def create_app():
    app = Flask(__name__, static_folder="public", static_url_path="")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # PayPal return URLs need the public https host
    # Signs the session cookie and the landlord's link. Set SECRET_KEY on any multi-instance deployment.
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    signer = URLSafeSerializer(app.secret_key, salt="landlord-link")
    app.config.update(SESSION_COOKIE_SECURE=os.environ.get("VERCEL") == "1", SESSION_COOKIE_SAMESITE="Lax")
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

    @app.errorhandler(paypal.PayPalError)
    def paypal_error(error):
        return jsonify(error=str(error)[:600]), 502

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error="The request is too large. Use one CSV file smaller than 2 MB."), 413

    @app.get("/")
    def index():
        return render_template(
            "index.html", currencies=CURRENCIES, paypal=paypal.configured(), agent=agent.configured(),
            ask=ai.configured(), login=login_enabled(), payer=session.get("payer"), rule=DEFAULT_RULE,
        )

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
            source=selected.get("source", "Bundled synthetic sample") if selected else "Uploaded CSV · source unverified",
            scenario_id=selected["id"] if selected else None,
            currency=result.currency,
            row_count=len(result.transactions),
            balanced=balanced,
            period=[day.isoformat() for day in passport["period"]],
            metrics={
                "income": passport["monthly_income"],
                "rent": passport["monthly_rent"],
                "ratio": passport["rent_to_income"],
                "rent_record": rent_record(result),
            } if balanced else None,
            applicant=(session.get("payer") or {}).get("name") or (selected or {}).get("applicant"),
            findings=[serialise_finding(finding) for finding in findings],
            rows=[
                {"row": n, "date": row.date.strftime("%d %b"), "description": str(row.description), "amount": float(row.amount)}
                for n, row in enumerate(result.transactions.head(ai.MAX_ROWS).itertuples(), start=1)
            ],
            ai=ai_block(data, result.currency) if balanced and ai.configured() else None,
        )

    @app.post("/api/report")
    def report():
        name, data, currency, selected = read_input()
        result = run(name, data, currency)
        if not result.passport["verified"]:
            return jsonify(error="A report cannot be issued because the balance or date-order check failed."), 422
        pdf = build_pdf(
            result.passport, result.currency, None, "", source=(
                selected.get("report_source", "Synthetic CSV demo") if selected else "Uploaded CSV; source unverified"
            ), demo=True,
        )
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name="credit-passport-demo.pdf")

    @app.post("/api/ask")
    def ask():
        if not ai.configured():
            return jsonify(error="Claude is not connected on this deployment."), 404
        question = str((request.get_json(silent=True) or {}).get("question") or request.form.get("question") or "").strip()
        if not 3 <= len(question) <= 200:
            raise StatementError("Ask a question of 3 to 200 characters.")
        _name, data, currency, _selected = read_input()
        if not run("statement.csv", data, currency).passport["verified"]:
            raise StatementError("Claude only answers about a statement that adds up.")
        try:
            return jsonify(ask_claude(data, currency, question))
        except (anthropic.APIError, ValueError, KeyError):
            return jsonify(error="Claude is unavailable right now. Try again in a moment."), 502

    @app.post("/api/agent")
    def deposit_agent():
        if not agent.configured():
            return jsonify(error="The deposit agent is not connected on this deployment."), 404
        payload = request.get_json(silent=True) or request.form
        try:
            rent = Decimal(str(payload.get("rent", ""))).quantize(Decimal("0.01"))
        except InvalidOperation:
            rent = Decimal(0)
        if not Decimal("100") <= rent <= Decimal("10000"):
            raise StatementError("Enter a monthly rent between GBP 100 and GBP 10,000.")
        rule = str(payload.get("rule") or DEFAULT_RULE).strip()[:300]
        email = (session.get("payer") or {}).get("email") or os.environ.get("PAYPAL_DEMO_PAYER_EMAIL", "")
        if not email:
            raise StatementError("Log in with PayPal first so the rent invoices know where to go.")
        name, data, currency, selected = read_input()
        result = run(name, data, currency)
        if not result.passport["verified"] or not result.passport["monthly_income"] or result.currency != "GBP":
            raise StatementError("The agent needs a GBP statement that adds up and shows regular income.")
        income = result.passport["monthly_income"]
        try:
            outcome = agent.run({"income": income, "on_time": rent_record(result)}, rent, rule, email,
                                request.url_root + "paypal/return", request.url_root + "paypal/cancel")
        except (anthropic.APIError, ValueError) as error:
            return jsonify(error=f"The agent could not finish: {str(error)[:200]}"), 502
        payer = session.get("payer") or {}
        session["offer"] = {
            "name": payer.get("name") or (selected or {}).get("applicant") or "The applicant",
            "verified": bool(payer.get("verified")),
            "history": "PayPal" if (selected or {}).get("id") == "paypal" else "Sample" if selected else "Upload",
            "income": round(income), "rent": str(rent), "share": round(float(rent) / income * 100),
            "record": rent_record(result), "series": outcome["series_id"] if outcome["series_active"] else None,
        }
        return jsonify(outcome)

    @app.get("/paypal/return")
    def paypal_return():
        offer = session.get("offer")
        try:
            hold_id, amount = paypal.authorize_order(request.args.get("token", ""))
        except paypal.PayPalError:
            return redirect("/?deposit=failed")
        link = signer.dumps({**(offer or {}), "hold": hold_id, "deposit": amount})
        return redirect("/?" + urlencode({"deposit": "held", "amount": amount, "landlord": link}))

    @app.get("/paypal/cancel")
    def paypal_cancel():
        return redirect("/?deposit=cancelled")

    @app.get("/l/<token>")
    def landlord(token):
        try:
            offer = signer.loads(token)
        except BadSignature:
            abort(404)
        return render_template("landlord.html", offer=offer, token=token)

    @app.post("/api/l/<token>/<decision>")
    def landlord_decision(token, decision):
        try:
            offer = signer.loads(token)
        except BadSignature:
            abort(404)
        if decision not in ("accept", "refund"):
            abort(404)
        reference = paypal.settle_hold(offer["hold"], accept=decision == "accept")
        return jsonify(status="captured" if decision == "accept" else "voided", reference=reference)

    @app.get("/paypal/login")
    def paypal_login():
        if not login_enabled():
            abort(404)
        session["login_state"] = secrets.token_urlsafe(16)
        return redirect(paypal.login_url(request.url_root + "paypal/login/return", session["login_state"]))

    @app.get("/paypal/login/return")
    def paypal_login_return():
        state = session.pop("login_state", None)
        if not login_enabled() or not state or request.args.get("state") != state or not request.args.get("code"):
            return redirect("/?login=failed")
        try:
            session["payer"] = paypal.login_profile(request.args["code"])
        except paypal.PayPalError:
            return redirect("/?login=failed")
        return redirect("/")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
