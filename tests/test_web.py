"""HTTP contract tests for the stateless synthetic CSV demonstration."""
import io
from pathlib import Path

import pdfplumber
import pytest

from server import MAX_CSV_BYTES, app

SAMPLES = Path(__file__).resolve().parents[1] / "synthetic_demo"


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        yield client


def upload(client, contents=None, filename="statement.csv", currency="SGD", endpoint="/api/analyse"):
    if contents is None:
        contents = (SAMPLES / "steady-income.csv").read_bytes()
    return client.post(endpoint, data={"statement": (io.BytesIO(contents), filename), "currency": currency})


def test_home_and_assets_are_accessible_and_demo_labelled(client):
    home = client.get("/")
    assert home.status_code == 200
    assert b"Can it be faked?" in home.data
    assert b"not a credit score" in home.data
    assert b"Currency" in home.data
    assert client.get("/styles.css").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert "frame-ancestors 'none'" in home.headers["Content-Security-Policy"]


def test_scenario_catalog_and_download_pack_are_available(client):
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    catalog = response.json["scenarios"]
    assert len(catalog) >= 7
    assert {scenario["id"] for scenario in catalog} >= {"steady-income", "edited-balances", "malformed-values"}
    download = client.get("/samples/steady-income.csv")
    assert download.status_code == 200
    assert download.data == (SAMPLES / "steady-income.csv").read_bytes()
    bundle = client.get("/samples/download.zip")
    assert bundle.status_code == 200 and bundle.data.startswith(b"PK")
    assert client.get("/samples/not-a-scenario.csv").status_code == 404


def test_successful_analysis_returns_actual_evidence_and_correct_period(client):
    response = client.post("/api/analyse", json={"scenario_id": "steady-income"})
    assert response.status_code == 200
    data = response.json
    assert data["balanced"] is True
    assert data["metrics"] == {"income": 4800.0, "rent": 1600.0, "ratio": 1 / 3, "rent_record": "6/6"}
    assert data["rows"][0]["row"] == 1 and len(data["rows"]) == data["row_count"]
    assert data["currency"] == "SGD"
    assert data["period"] == ["2026-04-01", "2026-09-30"]
    assert data["source"] == "Bundled synthetic sample"
    assert all(item["ok"] for item in data["findings"])
    assert len(data["findings"][1]["evidence"]) == 6
    assert data["findings"][1]["evidence"][0]["amount"] == 4800
    assert response.headers["Cache-Control"] == "no-store"


def test_uploaded_data_requires_explicit_currency_and_does_not_claim_provenance(client):
    missing_currency = upload(client, currency="")
    assert missing_currency.status_code == 400
    assert "currency" in missing_currency.json["error"]
    assert upload(client, currency="XXX").status_code == 400
    response = upload(client, currency="GBP")
    assert response.status_code == 200
    assert response.json["currency"] == "GBP"
    assert response.json["source"] == "Uploaded CSV · source unverified"
    assert response.json["scenario_id"] is None


@pytest.mark.parametrize("scenario_id", ["edited-balances", "out-of-order"])
def test_failed_checks_hide_summary_and_refuse_report_even_if_client_claims_pass(client, scenario_id):
    response = client.post("/api/analyse", json={"scenario_id": scenario_id})
    assert response.status_code == 200
    assert response.json["balanced"] is False
    assert response.json["metrics"] is None
    assert len(response.json["findings"]) == 1
    report = client.post("/api/report", json={"scenario_id": scenario_id, "verified": True, "findings": [], "monthly_income": 999999})
    assert report.status_code == 422
    assert "check failed" in report.json["error"]


def test_pass_with_missed_rent_preserves_caution_and_supports_summary_pdf(client):
    response = client.post("/api/analyse", json={"scenario_id": "missed-rent"})
    assert response.status_code == 200
    assert response.json["balanced"] is True
    assert response.json["findings"][2]["ok"] is False
    report = client.post("/api/report", json={"scenario_id": "missed-rent"})
    assert report.status_code == 200
    assert report.mimetype == "application/pdf"
    with pdfplumber.open(io.BytesIO(report.data)) as pdf:
        assert len(pdf.pages) == 1
        text = pdf.pages[0].extract_text()
    assert "demo" in text.lower()
    assert "5 of 6 months" in text
    assert "Verification code" not in text
    assert "1 SGD =" not in text


def test_report_reprocesses_uploaded_rows_and_ignores_invented_results(client):
    report = upload(client, currency="GBP", endpoint="/api/report")
    assert report.status_code == 200
    with pdfplumber.open(io.BytesIO(report.data)) as pdf:
        text = pdf.pages[0].extract_text()
    assert "GBP 4,800" in text
    assert "source unverified" in text
    edited = upload(client, contents=(SAMPLES / "edited-balances.csv").read_bytes(), endpoint="/api/report")
    assert edited.status_code == 422
    without_input = client.post("/api/report", json={"verified": True, "monthly_income": 100000})
    assert without_input.status_code == 400


@pytest.mark.parametrize("scenario_id", ["../../etc/passwd", "missing", 3, None])
def test_scenarios_are_allowlisted(client, scenario_id):
    response = client.post("/api/analyse", json={"scenario_id": scenario_id})
    assert response.status_code == 400


def test_malformed_values_and_unsupported_input_are_rejected(client):
    invalid = client.post("/api/analyse", json={"scenario_id": "malformed-values"})
    assert invalid.status_code == 400
    assert upload(client, filename="statement.pdf").status_code == 400
    assert upload(client, contents=b"date,description,amount,balance\n2026-04-01,test,0,NaN\n").status_code == 400
    assert upload(client, contents=b"\x00\x01binary").status_code == 400
    assert upload(client, contents=b"\xff\xfe").status_code == 400
    assert upload(client, contents=b"").status_code == 400
    assert upload(client, contents=b"date,description,amount,balance\n2026-04-01,test,0,20,extra\n").status_code == 400
    assert upload(client, filename="a" * 161 + ".csv").status_code == 400


def test_request_and_row_limits_are_enforced(client):
    oversized = upload(client, contents=b"x" * (MAX_CSV_BYTES + 1))
    assert oversized.status_code in {400, 413}
    huge_request = upload(client, contents=b"x" * (MAX_CSV_BYTES + 256 * 1024))
    assert huge_request.status_code == 413
    many_rows = b"date,description,amount,balance\n" + b"2026-04-01,TEST,0,100\n" * 5001
    too_many = upload(client, contents=many_rows)
    assert too_many.status_code == 400 and "5,000" in too_many.json["error"]
    allowed_rows = b"date,description,amount,balance\n" + b"2026-04-01,TEST,0,100\n" * 5000
    allowed = upload(client, contents=allowed_rows)
    assert allowed.status_code == 200
    assert allowed.json["row_count"] == 5000
