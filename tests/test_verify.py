"""The three evidence sources: PayPal, the (simulated) Open Banking feed and the PDF statement."""
from pathlib import Path

from passport import pdfcheck
from scripts.make_bank_sample import genuine
from server import app

DEMO = Path(__file__).resolve().parent.parent / "synthetic_demo"


def labels(source):
    return [(check["label"], check["ok"]) for check in source["checks"]]


def test_sample_is_verified_three_ways():
    data = app.test_client().post("/api/verify", json={"scenario_id": "paypal-sample"}).json
    assert [(s["name"], s["ok"]) for s in data["sources"]] == [("PayPal", True), ("Bank", True), ("Document", True)]
    assert labels(data["sources"][1]) == [("Name matches PayPal", True), ("6 of 6 PayPal transfers arrive", True),
                                          ("Statement matches the bank", True)]


def test_edited_pdf_is_caught_by_metadata_rows_and_the_bank():
    data = app.test_client().post("/api/verify", json={"scenario_id": "paypal-sample", "document": "edited"}).json
    bank, document = data["sources"][1], data["sources"][2]
    assert labels(document) == [("Saved again in Adobe Acrobat", False), ("Edited 9 days after issue", False),
                                ("Row 18 off by £2,500", False)]
    assert ("Bank shows £1,200 on 15 Aug, not £3,700", False) in labels(bank)


def test_committed_pdf_is_what_the_generator_makes():
    assert (DEMO / "bank-statement.pdf").read_bytes() == genuine()


def test_garbage_and_unknown_software_are_not_trusted():
    assert pdfcheck.check(b"not a pdf")["checks"][0] == {"label": "Not a readable PDF", "ok": False, "detail": ""}


def test_verify_needs_paypal_history_and_pdfs_are_allowlisted():
    client = app.test_client()
    assert client.post("/api/verify", json={"scenario_id": "steady-income"}).status_code == 400
    assert client.get("/samples/bank-statement-edited.pdf").status_code == 200
    assert client.get("/samples/manifest.pdf").status_code == 404
