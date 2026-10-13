"""Acceptance checks for the downloadable fictional scenarios, through the real pipeline."""

import csv
import importlib.util
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from passport.parse import StatementError
from passport.pipeline import run

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "synthetic_demo"
MANIFEST = json.loads((DEMO / "manifest.json").read_text())
SCENARIOS = MANIFEST["scenarios"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario["id"])
def test_scenario_matches_expected_pipeline_outcome(scenario):
    expected = scenario["expected"]
    data = (DEMO / scenario["file"]).read_bytes()
    if expected["error"]:
        with pytest.raises(StatementError):
            run(scenario["file"], data, scenario["currency"])
        return

    result = run(scenario["file"], data, scenario["currency"])
    passport = result.passport
    assert result.currency == "SGD"
    assert bool(passport["verified"]) is expected["verified"]
    assert passport["monthly_income"] == expected["income"]
    assert passport["monthly_rent"] == expected["rent"]
    assert [bool(finding.ok) for finding in passport["findings"]] == [
        expected["verified"], expected["income_ok"], expected["rent_ok"], expected["buffer_ok"]
    ]
    assert tuple(date.isoformat() for date in passport["period"]) == ("2026-04-01", "2026-09-30")

    if scenario["id"] == "steady-income":
        assert passport["rent_to_income"] == pytest.approx(1 / 3)
        assert result.transactions.iloc[-1]["balance"] == 21564.50
    elif scenario["id"] == "edited-balances":
        assert len(passport["findings"][0].evidence) == 2
    elif scenario["id"] == "missed-rent":
        assert "5 of 6 months paid" in passport["findings"][2].detail
        assert "Jul 2026" in passport["findings"][2].detail
    elif scenario["id"] == "overdraft":
        evidence = passport["findings"][3].evidence
        assert len(evidence) == 4
        assert evidence["balance"].min() == -1620.50
    elif scenario["id"] == "irregular-income":
        assert passport["rent_to_income"] is None
        assert (result.transactions["amount"] > 0).sum() == 6
    elif scenario["id"] == "out-of-order":
        assert "Dates are out of order" in passport["findings"][0].detail
        assert len(passport["findings"][0].evidence) == 3


def test_every_fixture_is_explicitly_fictional_and_within_the_advertised_period():
    assert MANIFEST["synthetic"] is True
    for scenario in SCENARIOS:
        assert scenario["filename"] == scenario["file"]
        rows = list(csv.DictReader(io.StringIO((DEMO / scenario["file"]).read_text())))
        assert all(row["Description"].startswith("FICTIONAL DEMO ") for row in rows)
        assert all("2026-04-01" <= row["Date"] <= "2026-09-30" for row in rows)


def test_download_bundle_contains_exactly_the_published_files():
    expected_names = {scenario["file"] for scenario in SCENARIOS} | {"manifest.json", "README.md"}
    with ZipFile(DEMO / "credit-passport-synthetic-demo.zip") as archive:
        assert set(archive.namelist()) == expected_names
        for filename in expected_names:
            assert archive.read(filename) == (DEMO / filename).read_bytes()


def test_demo_report_preserves_findings_and_refuses_inconsistent_statements():
    import pdfplumber

    from passport.report import build_pdf

    for scenario in SCENARIOS:
        if scenario["expected"]["error"]:
            continue
        passport = run(scenario["file"], (DEMO / scenario["file"]).read_bytes()).passport
        if not scenario["expected"]["verified"]:
            with pytest.raises(ValueError):
                build_pdf(passport, "SGD", None, "", demo=True)
            continue
        with pdfplumber.open(io.BytesIO(build_pdf(passport, "SGD", None, "", demo=True))) as pdf:
            assert len(pdf.pages) == 1
            content = pdf.pages[0].extract_text()
        assert "SYNTHETIC DEMO" in content
        assert "SGD 1,600" in content
        assert "FICTIONAL DEMO MARKET" not in content
        if scenario["id"] == "missed-rent":
            assert "Jul 2026" in content
        elif scenario["id"] == "overdraft":
            assert "1,620.50" in content
        elif scenario["id"] == "irregular-income":
            assert "No regular income found" in content


def test_generator_reproduces_all_fixtures_and_bundle(tmp_path):
    spec = importlib.util.spec_from_file_location("generate_demo_data", ROOT / "scripts/generate_demo_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.write_files(tmp_path)
    for generated in tmp_path.iterdir():
        assert generated.read_bytes() == (DEMO / generated.name).read_bytes()
