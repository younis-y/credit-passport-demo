"""PayPal statement mapping, deposit hold, landlord link and login, with PayPal's HTTP calls replaced by fixtures."""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from passport import paypal
from server import app

NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)
statement_csv = paypal.statement_csv


def record(when, value, balance, fee="0.00", subject=None, currency="GBP"):
    info = {
        "transaction_initiation_date": when,
        "transaction_amount": {"currency_code": currency, "value": value},
        "fee_amount": {"currency_code": currency, "value": fee},
        "ending_balance": {"currency_code": currency, "value": balance},
        "transaction_event_code": "T0006",
    }
    if subject:
        info["transaction_subject"] = subject
    return {"transaction_info": info, "payer_info": {"payer_name": {"alternate_full_name": "Acme Studio"}}}


# Six months of a freelancer paid monthly through PayPal and paying rent from it.
HISTORY = [record("2026-05-01T09:00:00+0000", "0.00", "500.00", subject="Opening balance")]
balance = 500.0
for month in range(5, 11):
    balance += 3000 - 87.30
    HISTORY.append(record(f"2026-{month:02d}-02T10:00:00+0000", "3000.00", f"{balance:.2f}", fee="-87.30"))
    balance -= 1200
    HISTORY.append(record(f"2026-{month:02d}-05T08:00:00+0000", "-1200.00", f"{balance:.2f}", subject="Rent"))


@pytest.fixture
def fake_paypal(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    calls = []

    def request(method, path, *, token=None, body=None, form=None):
        calls.append((method, path, body))
        if path == "/v1/oauth2/token":
            return {"access_token": "token"}
        if path.startswith("/v1/reporting/transactions"):
            start = path.split("start_date=")[1][:10]
            end = path.split("end_date=")[1][:10]
            rows = [item for item in HISTORY if start <= item["transaction_info"]["transaction_initiation_date"][:10] < end]
            return {"transaction_details": rows, "total_pages": 1}
        if path == "/v2/checkout/orders":
            return {"id": "5O190127TN364715T", "links": [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T"}]}
        if path.endswith("/authorize"):
            return {"purchase_units": [{"payments": {"authorizations": [{"id": "8AB12345CD678901E", "status": "CREATED", "amount": {"currency_code": "GBP", "value": "242.30"}}]}}]}
        if path.endswith("/capture"):
            return {"id": "7KP11111AA2222222", "status": "COMPLETED"}
        if path.endswith("/void"):
            return {}
        if path.startswith("/v1/identity/openidconnect/userinfo"):
            return {"name": "Amira Haddad", "email": "amira@example.com", "verified_account": "true"}
        raise AssertionError(path)

    monkeypatch.setattr(paypal, "_request", request)
    return calls


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        yield client


def test_statement_is_net_of_fees_in_date_order_and_windowed(fake_paypal):
    data, currency = paypal.statement_csv(now=NOW)
    lines = data.decode().splitlines()
    assert currency == "GBP"
    assert lines[0] == "date,description,amount,balance"
    assert lines[1] == "2026-05-01,Opening balance,0.0,500.0"
    assert lines[2] == "2026-05-02,Acme Studio,2912.7,3412.7"
    assert len(lines) == 1 + len(HISTORY)
    windows = [path for _, path, _ in fake_paypal if "reporting" in path]
    assert len(windows) == 6  # 183 days in 31-day windows


def test_missing_running_balance_is_refused_not_invented(fake_paypal):
    del HISTORY[1]["transaction_info"]["ending_balance"]
    try:
        with pytest.raises(paypal.PayPalError, match="running balances"):
            paypal.statement_csv(now=NOW)
    finally:
        HISTORY[1]["transaction_info"]["ending_balance"] = {"currency_code": "GBP", "value": "3412.70"}


def test_only_the_main_currency_is_kept(fake_paypal):
    HISTORY.append(record("2026-06-10T00:00:00+0000", "5.00", "5.00", currency="USD"))
    try:
        data, currency = paypal.statement_csv(now=NOW)
    finally:
        HISTORY.pop()
    assert currency == "GBP"
    assert b"5.0,5.0" not in data


def test_paypal_history_runs_through_the_same_checks(fake_paypal, client, monkeypatch):
    monkeypatch.setattr(paypal, "statement_csv", lambda: statement_csv(now=NOW))
    response = client.post("/api/analyse", json={"scenario_id": "paypal"})
    assert response.status_code == 200
    data = response.json
    assert data["balanced"] is True
    assert data["source"].startswith("PayPal sandbox account")
    assert data["currency"] == "GBP"
    assert data["metrics"]["income"] == 2912.7
    assert data["metrics"]["rent"] == 1200.0
    report = client.post("/api/report", json={"scenario_id": "paypal"})
    assert report.status_code == 200 and report.data.startswith(b"%PDF")


def test_sample_history_is_mapped_by_the_same_code_and_passes(client):
    data = client.post("/api/analyse", json={"scenario_id": "paypal-sample"}).json
    assert data["balanced"] is True and data["currency"] == "GBP"
    assert data["applicant"] == "Amira Haddad"
    assert data["source"].startswith("Sample PayPal history")
    assert data["metrics"]["rent_record"] == "6/6"
    assert data["rows"][2] == {"row": 3, "date": "02 May", "description": "Northwind Studio Ltd", "amount": 3106.9}


def test_paypal_is_hidden_and_refused_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    assert b'data-scenario="paypal"' not in client.get("/").data
    assert client.post("/api/analyse", json={"scenario_id": "paypal"}).status_code == 400
    assert client.post("/api/agent", json={"scenario_id": "paypal-sample", "rent": "1050"}).status_code == 404
    assert client.get("/paypal/login").status_code == 404


def test_return_places_a_hold_and_signs_a_landlord_link(fake_paypal, client):
    with client.session_transaction() as session:
        session["offer"] = {"name": "Amira Haddad", "verified": False, "history": "Sample", "income": 3107,
                            "rent": "1050.00", "share": 34, "record": "6/6", "series": "RI-1"}
    done = client.get("/paypal/return?token=5O190127TN364715T")
    assert done.status_code == 302
    query = parse_qs(urlparse(done.location).query)
    assert query["deposit"] == ["held"] and query["amount"] == ["GBP 242.30"]
    page = client.get(f"/l/{query['landlord'][0]}")
    assert page.status_code == 200
    assert b"Amira Haddad" in page.data and "£242.30".encode() in page.data and "£1,050 rent".encode() in page.data and b"6/6" in page.data
    assert client.get(f"/l/{query['landlord'][0]}x").status_code == 404
    accept = client.post(f"/api/l/{query['landlord'][0]}/accept")
    assert accept.json == {"status": "captured", "reference": "7KP11111AA2222222"}
    refund = client.post(f"/api/l/{query['landlord'][0]}/refund")
    assert refund.json["status"] == "voided"
    paths = [path for _, path, _ in fake_paypal]
    assert "/v2/payments/authorizations/8AB12345CD678901E/capture" in paths
    assert "/v2/payments/authorizations/8AB12345CD678901E/void" in paths


def test_bad_order_reference_and_cancel_hold_nothing(fake_paypal, client):
    assert client.get("/paypal/return?token=../../x").location == "/?deposit=failed"
    assert client.get("/paypal/cancel").location == "/?deposit=cancelled"
    assert not any(path.endswith("/authorize") for _, path, _ in fake_paypal)


def test_login_checks_state_and_keeps_the_verified_profile(fake_paypal, client, monkeypatch):
    monkeypatch.setenv("PAYPAL_LOGIN", "1")
    start = client.get("/paypal/login")
    state = parse_qs(urlparse(start.location).query)["state"][0]
    assert start.location.startswith("https://www.sandbox.paypal.com/signin/authorize?")
    assert client.get("/paypal/login/return?code=abc&state=wrong").location == "/?login=failed"
    client.get("/paypal/login")
    with client.session_transaction() as session:
        state = session["login_state"]
    assert client.get(f"/paypal/login/return?code=abc&state={state}").location == "/"
    assert b"PayPal verified" in client.get("/").data
