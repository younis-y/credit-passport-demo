"""PayPal as a statement source and as the way to pay a holding deposit.

statement_csv() pulls the account's own transaction history from the Transaction Search API
and writes it in the four-column CSV the rest of the pipeline already reads, so the same
balance check and rules run on it. The rows come from PayPal, not from a file the applicant
could edit. create_order() and capture_order() take a deposit through PayPal Checkout.

Credentials: PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET. PAYPAL_API_BASE defaults to the sandbox.
"""
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .parse import StatementError

API_BASE = os.environ.get("PAYPAL_API_BASE", "https://api-m.sandbox.paypal.com")
WINDOW = timedelta(days=31)  # Transaction Search refuses longer ranges
ORDER_ID = re.compile(r"[A-Z0-9]{8,32}")
SAMPLE = Path(__file__).resolve().parent.parent / "synthetic_demo" / "paypal-sample.json"
WEB_BASE = API_BASE.replace("api-m.", "www.")


class PayPalError(StatementError):
    """PayPal refused or failed a call. The message is safe to show the user."""


def configured():
    return bool(os.environ.get("PAYPAL_CLIENT_ID") and os.environ.get("PAYPAL_CLIENT_SECRET"))


def _request(method, path, *, token=None, body=None, form=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        pair = f"{os.environ['PAYPAL_CLIENT_ID']}:{os.environ['PAYPAL_CLIENT_SECRET']}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API_BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read()
            return json.loads(body) if body else {}  # void answers 204 with no body
    except urllib.error.HTTPError as error:
        try:
            detail = json.load(error)
            reason = detail.get("message") or detail.get("error_description") or detail.get("name")
        except ValueError:
            reason = error.reason
        raise PayPalError(f"PayPal returned {error.code}: {reason}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise PayPalError("PayPal could not be reached. Try again in a moment.") from error


def _token():
    return _request("POST", "/v1/oauth2/token", form={"grant_type": "client_credentials"})["access_token"]


def _rows(details):
    """Map Transaction Search records to statement rows; amounts are net of PayPal's fee."""
    rows = []
    for item in details:
        info = item.get("transaction_info", {})
        if "ending_balance" not in info:
            raise PayPalError("PayPal did not return running balances, so the balance check cannot run.")
        payer = item.get("payer_info", {}).get("payer_name", {}).get("alternate_full_name")
        amount = float(info["transaction_amount"]["value"]) + float(info.get("fee_amount", {}).get("value", 0))
        rows.append({
            "when": info["transaction_initiation_date"],
            "description": info.get("transaction_subject") or payer or f"PayPal {info.get('transaction_event_code', 'transaction')}",
            "amount": round(amount, 2),
            "balance": float(info["ending_balance"]["value"]),
            "currency": info["transaction_amount"]["currency_code"],
        })
    return rows


def statement_csv(days=183, now=None):
    """Return (csv_bytes, currency) for the last `days` of balance-affecting transactions."""
    token = _token()
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = []
    while start < end:
        stop = min(start + WINDOW, end)
        page, pages = 1, 1
        while page <= pages:
            query = urllib.parse.urlencode({
                "start_date": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date": stop.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "fields": "transaction_info,payer_info",
                "balance_affecting_records_only": "Y",
                "page_size": 500,
                "page": page,
            })
            data = _request("GET", f"/v1/reporting/transactions?{query}", token=token)
            rows += _rows(data.get("transaction_details", []))
            pages = data.get("total_pages", 1)
            page += 1
        start = stop
    if len(rows) < 2:
        raise PayPalError("This PayPal account has fewer than two transactions in the last six months.")
    return _csv(rows)


def sample_csv():
    """A fictional freelancer's six months, stored in Transaction Search shape and mapped by the same code."""
    return _csv(_rows(json.loads(SAMPLE.read_text())["transaction_details"]))


def _csv(rows):
    table = pd.DataFrame(rows)
    # Running balances are per currency, so keep the account's main one.
    currency = table["currency"].mode().iloc[0]
    table = table[table["currency"] == currency]
    table["when"] = pd.to_datetime(table["when"], utc=True)
    table = table.sort_values("when", kind="stable")
    table["date"] = table["when"].dt.strftime("%Y-%m-%d")
    return table[["date", "description", "amount", "balance"]].to_csv(index=False).encode(), currency


def create_order(value, currency, return_url, cancel_url, intent="CAPTURE"):
    """Create a Checkout order and return the PayPal page the payer approves it on.

    intent="AUTHORIZE" holds the money on the payer's account until the landlord captures or voids it.
    """
    order = _request("POST", "/v2/checkout/orders", token=_token(), body={
        "intent": intent,
        "purchase_units": [{"amount": {"currency_code": currency, "value": value}, "description": "Holding deposit"}],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": "Credit Passport",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "PAY_NOW",
            "return_url": return_url,
            "cancel_url": cancel_url,
        }}},
    })
    return next(link["href"] for link in order["links"] if link["rel"] in ("payer-action", "approve"))


def authorize_order(order_id):
    """Place the hold on an approved AUTHORIZE order. Returns (authorization id, amount text)."""
    if not ORDER_ID.fullmatch(order_id or ""):
        raise PayPalError("That PayPal order reference is not valid.")
    result = _request("POST", f"/v2/checkout/orders/{order_id}/authorize", token=_token(), body={})
    hold = result["purchase_units"][0]["payments"]["authorizations"][0]
    if hold["status"] not in ("CREATED", "PENDING"):
        raise PayPalError(f"PayPal did not hold the deposit ({hold['status']}).")
    return hold["id"], f"{hold['amount']['currency_code']} {hold['amount']['value']}"


def settle_hold(authorization_id, accept):
    """Landlord's decision: capture the held deposit, or void the hold so nothing is taken."""
    if not ORDER_ID.fullmatch(authorization_id or ""):
        raise PayPalError("That PayPal authorization reference is not valid.")
    action = "capture" if accept else "void"
    result = _request("POST", f"/v2/payments/authorizations/{authorization_id}/{action}", token=_token(), body={})
    return (result or {}).get("id", authorization_id)


def login_url(redirect_uri, state):
    """Log in with PayPal: the applicant signs in so we see their own verified PayPal account."""
    query = urllib.parse.urlencode({
        "flowEntry": "static",
        "client_id": os.environ["PAYPAL_CLIENT_ID"],
        "response_type": "code",
        "scope": "openid profile email https://uri.paypal.com/services/paypalattributes",
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return f"{WEB_BASE}/signin/authorize?{query}"


def login_profile(code):
    """Exchange the login code and return the applicant's name, email and PayPal verified status."""
    token = _request("POST", "/v1/oauth2/token", form={"grant_type": "authorization_code", "code": code})["access_token"]
    info = _request("GET", "/v1/identity/openidconnect/userinfo?schema=openid", token=token)
    return {
        "name": info.get("name") or " ".join(filter(None, [info.get("given_name"), info.get("family_name")])),
        "email": info.get("email", ""),
        "verified": bool(info.get("verified_account") in (True, "true")),
    }
