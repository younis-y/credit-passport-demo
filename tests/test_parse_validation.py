"""Input failures must stay failures, rather than producing a plausible passport."""
import csv
import io
from pathlib import Path

import pytest

from passport.parse import StatementError, parse_csv
from passport.pipeline import run

SAMPLES = Path(__file__).parent.parent / "samples"
SIGNED_HEADER = ["Date", "Description", "Amount", "Balance"]
SIGNED_ROWS = [["01/04/2026", "OPENING BALANCE", "0", "100"],
               ["02/04/2026", "TEST CREDIT", "25", "125"]]


def csv_bytes(header=SIGNED_HEADER, rows=SIGNED_ROWS):
    out = io.StringIO()
    csv.writer(out).writerows([header, *rows])
    return out.getvalue().encode("utf-8")


@pytest.mark.parametrize("value", ["nonsense", "NaN", "inf", "-inf", "1e309", "1,2,3", "1000000000001"])
@pytest.mark.parametrize("column", [2, 3], ids=["amount", "balance"])
def test_invalid_money_is_rejected_instead_of_coerced_to_zero(value, column):
    rows = [row.copy() for row in SIGNED_ROWS]
    rows[0][column] = value
    with pytest.raises(StatementError):
        run("test.csv", csv_bytes(rows=rows))


@pytest.mark.parametrize("column", range(4), ids=["date", "description", "amount", "balance"])
@pytest.mark.parametrize("value", ["", "   "])
def test_each_required_value_must_be_present(column, value):
    rows = [row.copy() for row in SIGNED_ROWS]
    rows[0][column] = value
    with pytest.raises(StatementError):
        run("test.csv", csv_bytes(rows=rows))


@pytest.mark.parametrize("column", range(4), ids=["date", "description", "amount", "balance"])
def test_required_columns_cannot_be_omitted(column):
    header = [cell for index, cell in enumerate(SIGNED_HEADER) if index != column]
    rows = [[cell for index, cell in enumerate(row) if index != column] for row in SIGNED_ROWS]
    with pytest.raises(StatementError):
        run("test.csv", csv_bytes(header, rows))


@pytest.mark.parametrize("extra_header", ["Amount", " amount ", "AMOUNT", ""])
def test_duplicate_or_empty_headers_are_rejected(extra_header):
    with pytest.raises(StatementError):
        run("test.csv", csv_bytes([*SIGNED_HEADER, extra_header], [row + ["0"] for row in SIGNED_ROWS]))


@pytest.mark.parametrize("difference", [-1, 1])
def test_row_width_must_match_header(difference):
    rows = [row.copy() for row in SIGNED_ROWS]
    rows[0] = rows[0][:-1] if difference < 0 else rows[0] + ["extra"]
    with pytest.raises(StatementError):
        run("test.csv", csv_bytes(rows=rows))


@pytest.mark.parametrize("payload", [b"", b"Date,Description,Amount,Balance\n", csv_bytes(rows=SIGNED_ROWS[:1]),
                                     b'Date,Description,Amount,Balance\n2026-04-01,"unterminated,0,100\n',
                                     b"Date,Description,Amount,Balance\n2026-04-01,\xff,0,100\n"])
def test_incomplete_or_unreadable_csv_has_a_user_safe_error(payload):
    with pytest.raises(StatementError):
        run("test.csv", payload)


def test_split_columns_accept_blank_cells_without_losing_the_balance_check():
    data = csv_bytes(["Date", "Description", "Debit", "Credit", "Balance"], [
        ["01/04/2026", "OPENING BALANCE", "", "", "100"],
        ["02/04/2026", "TEST CREDIT", "", "25", "125"],
        ["03/04/2026", "TEST DEBIT", "10", "", "115"],
    ])
    result = run("test.csv", data)
    assert result.transactions["amount"].tolist() == [0, 25, -10]
    assert result.passport["verified"] is True
    assert "2 rows" in result.passport["findings"][0].detail


@pytest.mark.parametrize("debit,credit", [("garbage", ""), ("", "inf"), ("-1", ""),
                                         ("", "-1"), ("10", "25")])
def test_split_columns_reject_invalid_or_conflicting_values(debit, credit):
    data = csv_bytes(["Date", "Description", "Debit", "Credit", "Balance"], [
        ["01/04/2026", "OPENING BALANCE", "", "", "100"],
        ["02/04/2026", "TEST TRANSFER", debit, credit, "100"],
    ])
    with pytest.raises(StatementError):
        run("test.csv", data)


def test_utf8_bom_and_quoted_description_remain_supported():
    rows = [row.copy() for row in SIGNED_ROWS]
    rows[1][1] = 'TEST CREDIT, "monthly"'
    result = run("test.csv", b"\xef\xbb\xbf" + csv_bytes(rows=rows))
    assert result.passport["verified"] is True
    assert result.transactions.iloc[1]["description"] == rows[1][1]


def test_indian_grouping_alias_columns_and_reverse_dates_remain_supported():
    result = run("india.csv", (SAMPLES / "india.csv").read_bytes(), "INR")
    assert result.passport["verified"] is True
    assert result.transactions["date"].is_monotonic_increasing
    assert result.passport["monthly_income"] == 145000
    assert result.passport["monthly_rent"] == 42000
    assert result.currency == "INR"


def test_interior_date_disorder_is_not_silently_sorted_into_a_passing_statement():
    data = csv_bytes(rows=[
        ["03/04/2026", "OPENING BALANCE", "0", "100"],
        ["01/04/2026", "TEST CREDIT", "25", "125"],
        ["02/04/2026", "TEST DEBIT", "-10", "115"],
    ])
    result = run("test.csv", data)
    assert result.passport["verified"] is False
    assert "Dates are out of order" in result.passport["findings"][0].detail


def test_iso_dates_do_not_swap_the_day_and_month():
    result = run("test.csv", csv_bytes(rows=[
        ["2026-04-01", "OPENING BALANCE", "0", "100"],
        ["2026-04-02", "TEST CREDIT", "25", "125"],
    ]))
    assert [date.date().isoformat() for date in result.transactions["date"]] == ["2026-04-01", "2026-04-02"]
    assert result.passport["verified"] is True


def test_iso_dates_can_coexist_with_day_first_bank_dates():
    result = run("test.csv", csv_bytes(rows=[
        ["2026-04-01", "OPENING BALANCE", "0", "100"],
        ["02/04/2026", "TEST CREDIT", "25", "125"],
    ]))
    assert [date.date().isoformat() for date in result.transactions["date"]] == ["2026-04-01", "2026-04-02"]
    assert result.passport["verified"] is True


@pytest.mark.parametrize("dates", [
    ("2026-04-01T12:00:00Z", "2026-04-02T12:00:00Z"),
    ("2263-01-01", "2263-01-02"),
], ids=["timezone-aware-timestamps", "out-of-bounds-calendar-dates"])
def test_unsupported_dates_raise_a_safe_input_error(dates):
    data = csv_bytes(rows=[
        [dates[0], "OPENING BALANCE", "0", "100"],
        [dates[1], "TEST CREDIT", "25", "125"],
    ])
    with pytest.raises(StatementError):
        run("test.csv", data)


@pytest.mark.parametrize("dates", [
    ("2026-04-01T12:00:00Z", "2026-04-02T12:00:00Z"),
    ("2263-01-01", "2263-01-02"),
], ids=["timezone-aware-timestamps", "out-of-bounds-calendar-dates"])
@pytest.mark.parametrize("endpoint", ["/api/analyse", "/api/report"])
def test_unsupported_dates_return_http_400_instead_of_crashing(dates, endpoint):
    from server import app

    data = csv_bytes(rows=[
        [dates[0], "OPENING BALANCE", "0", "100"],
        [dates[1], "TEST CREDIT", "25", "125"],
    ])
    with app.test_client() as client:
        response = client.post(endpoint, data={
            "statement": (io.BytesIO(data), "test.csv"), "currency": "GBP",
        })
    assert response.status_code == 400
    assert response.is_json
    assert response.json["error"]
