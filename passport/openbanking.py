"""The applicant's bank account, as an Open Banking feed would show it.

SIMULATED. A live connection in the UK needs an FCA-authorised provider; this reads a fixed
feed in the Open Banking Read Transactions shape for the fictional Thames Bank instead.
The checks are real: they compare the feed with the PayPal history and the PDF statement.
"""
import json
from pathlib import Path

import pandas as pd

FEED = Path(__file__).resolve().parent.parent / "synthetic_demo" / "openbanking-sample.json"
TAG = "Open Banking · simulated"


def fetch():
    data = json.loads(FEED.read_text())["Data"]
    tx = pd.DataFrame([{
        "date": pd.Timestamp(item["BookingDateTime"][:10]),
        "description": item["TransactionInformation"],
        "amount": float(item["Amount"]["Amount"]) * (1 if item["CreditDebitIndicator"] == "Credit" else -1),
        "balance": float(item["Balance"]["Amount"]["Amount"]),
    } for item in data["Transaction"]])
    return data["Account"], tx


def _check(ok, good, bad, detail=""):
    return {"label": good if ok else bad, "ok": bool(ok), "detail": detail}


def check(applicant, paypal_tx, document_rows=None):
    """Name, PayPal transfers landing in the bank, and (when given) every PDF row against the feed."""
    account, bank = fetch()
    checks = [_check(account["Name"] == applicant, "Name matches PayPal", f"Bank name is {account['Name']}")]

    sent = paypal_tx[paypal_tx["description"].str.contains(account["Servicer"], case=False) & (paypal_tx["amount"] < 0)]
    arrived = sum(
        (((bank["amount"] + row.amount).abs() < 0.005) & (bank["date"] - row.date).dt.days.between(0, 3)).any()
        for row in sent.itertuples()
    )
    checks.append(_check(len(sent) > 0 and arrived == len(sent), f"{arrived} of {len(sent)} PayPal transfers arrive",
                         f"Only {arrived} of {len(sent)} PayPal transfers arrive"))

    if document_rows is not None and len(document_rows):
        mismatch = None
        for row in document_rows.itertuples():
            same_day = bank[bank["date"] == row.date]
            if row.amount and not ((same_day["amount"] - row.amount).abs() < 0.005).any():
                mismatch = row
                break
        if mismatch is None:
            checks.append(_check(True, "Statement matches the bank", ""))
        else:
            actual = bank[bank["date"] == mismatch.date]["amount"]
            shown = f"£{abs(actual.iloc[0]):,.0f}" if len(actual) else "nothing"
            checks.append(_check(False, "", f"Bank shows {shown} on {mismatch.date:%d %b}, not £{abs(mismatch.amount):,.0f}"))
    return checks
