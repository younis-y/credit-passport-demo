"""Write synthetic_demo/paypal-sample.json: six months of a fictional freelancer's PayPal account,
in the exact shape the Transaction Search API returns, so it runs through the same mapping code."""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "synthetic_demo" / "paypal-sample.json"
rows, balance = [], 1840.00


def add(day, value, subject=None, payer=None, fee=0.0, code="T0000"):
    global balance
    balance = round(balance + value + fee, 2)
    info = {
        "transaction_initiation_date": f"{day}T09:00:00+0000",
        "transaction_amount": {"currency_code": "GBP", "value": f"{value:.2f}"},
        "fee_amount": {"currency_code": "GBP", "value": f"{fee:.2f}"},
        "ending_balance": {"currency_code": "GBP", "value": f"{balance:.2f}"},
        "transaction_event_code": code,
    }
    if subject:
        info["transaction_subject"] = subject
    rows.append({"transaction_info": info, "payer_info": {"payer_name": {"alternate_full_name": payer}} if payer else {}})


add("2026-04-30", 0.0, subject="Opening balance")
pay = [3200, 3150, 3250, 3200, 3180, 3220]
for i, month in enumerate(range(5, 11)):
    m = f"2026-{month:02d}"
    add(f"{m}-01", -950.00, subject="Rent - Flat 4, Mare Street", code="T0001")
    add(f"{m}-02", pay[i], payer="Northwind Studio Ltd", fee=-round(pay[i] * 0.029 + 0.30, 2), code="T0006")
    add(f"{m}-08", -68.40, subject="Octopus Energy", code="T0003")
    add(f"{m}-12", -18.00, subject="Three Mobile", code="T0003")
    add(f"{m}-15", -1200.00, subject="Transfer to Monzo", code="T0400")
    if month in (6, 9):
        add(f"{m}-20", 480.00, payer="Atlas Media", fee=-round(480 * 0.029 + 0.30, 2), code="T0006")
    add(f"{m}-24", -11.99, subject="Spotify", code="T0003")

OUT.write_text(json.dumps({"transaction_details": rows}, indent=1) + "\n")
print(len(rows), "rows, closing balance", balance)
