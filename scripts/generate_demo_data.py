"""Regenerate fictional, reproducible demo CSVs without external services or keys.

Run from the repository: python scripts/generate_demo_data.py
Only synthetic_demo/ is written; the original samples/ are preserved.
"""

import csv
import io
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "synthetic_demo"
FIELDS = ["Date", "Description", "Amount", "Balance"]


def statement(*, opening_cents=500_000, skip_rent=None, income_cents=None):
    """Use integer cents so fixture generation never depends on float rounding."""
    incomes = income_cents or [480_000] * 6
    transactions = []
    for index, month in enumerate(range(4, 10)):
        events = [
            (10, "FICTIONAL DEMO UTILITIES", -12_000),
            (18, "FICTIONAL DEMO MARKET", -23_550),
            (22, "FICTIONAL DEMO TRAVEL", -6_500),
            (25, "FICTIONAL DEMO EMPLOYER SALARY", incomes[index]),
            (28, "FICTIONAL DEMO CAFE", -1_875),
        ]
        if month != skip_rent:
            events.append((5, "FICTIONAL DEMO HOME RENT", -160_000))
        for day, description, amount in sorted(events):
            transactions.append((f"2026-{month:02}-{day:02}", description, amount))
    balance = opening_cents
    rows = [["2026-04-01", "FICTIONAL DEMO OPENING BALANCE", "0.00", money(balance)]]
    for date, description, amount in transactions:
        balance += amount
        rows.append([date, description, money(amount), money(balance)])
    rows.append(["2026-09-30", "FICTIONAL DEMO CLOSING BALANCE", "0.00", money(balance)])
    return rows


def money(cents):
    return f"{'-' if cents < 0 else ''}{abs(cents) // 100}.{abs(cents) % 100:02}"


def csv_bytes(rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(FIELDS)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def expectation(*, verified=True, income=4800.0, rent=1600.0,
                income_ok=True, rent_ok=True, buffer_ok=True, error=False, notes):
    return dict(verified=verified, income=income, rent=rent, income_ok=income_ok,
                rent_ok=rent_ok, buffer_ok=buffer_ok, error=error, notes=notes)


def build_files():
    steady = statement()
    edited = [row.copy() for row in steady]
    # One altered reported balance must break this row and the next row.
    edited_row = next(row for row in edited if row[0] == "2026-06-25")
    edited_row[3] = money(round(float(edited_row[3]) * 100) + 250_000)
    shuffled = [row.copy() for row in steady]
    a = next(index for index, row in enumerate(shuffled) if row[0] == "2026-05-18")
    shuffled[a], shuffled[a + 1] = shuffled[a + 1], shuffled[a]
    malformed = [row.copy() for row in steady]
    # A lenient zero-coercing parser would silently accept this. It must reject it.
    malformed[0][2] = "not-a-number"

    definitions = [
        ("steady-income", "Steady income", "Fictional salary and rent in all six months; all balances add up.",
         steady, expectation(notes="All four checks pass. Typical monthly income SGD 4,800; rent SGD 1,600; rent-to-income ratio 33.3%.")),
        ("edited-balances", "Edited balance", "One fictional balance has been increased by SGD 2,500 without changing its transactions.",
         edited, expectation(verified=False, notes="Two rows fail the running-balance check. Income and rent may be calculated, but the failed statement must not receive an eligible summary PDF.")),
        ("missed-rent", "Missing July rent", "Fictional July rent is absent; the remaining running balances are recalculated correctly.",
         statement(skip_rent=7), expectation(rent_ok=False, notes="Balances pass. Rent appears in 5 of 6 months, with Jul 2026 named as missing. Typical rent remains SGD 1,600.")),
        ("overdraft", "Overdraft", "A fictional low opening balance produces four negative balances in April.",
         statement(opening_cents=40_000), expectation(buffer_ok=False, notes="Balances, income and rent pass. Four rows are below zero; the lowest balance is SGD -1,620.50.")),
        ("irregular-income", "Irregular income", "Fictional monthly receipts vary too widely for the regular-income rule.",
         statement(income_cents=[180_000, 320_000, 760_000, 140_000, 520_000, 920_000]),
         expectation(income=None, income_ok=False, notes="Balances and rent pass. No regular income is found, so the rent-to-income ratio is unavailable. This does not mean there was no income.")),
        ("out-of-order", "Out-of-order transactions", "Two fictional May transactions are swapped while their original balances remain attached.",
         shuffled, expectation(verified=False, notes="Dates are out of order and three running-balance rows fail. An eligible summary PDF must not be issued.")),
        ("malformed-values", "Unreadable amount", "The fictional opening row contains the text not-a-number in its amount column.",
         malformed, expectation(verified=None, income=None, rent=None, income_ok=None,
                                rent_ok=None, buffer_ok=None, error=True,
                                notes="Reject the upload with a readable validation error. Do not replace unreadable financial values with zero or issue a result.")),
    ]
    scenarios = []
    files = {}
    for scenario_id, title, description, rows, expected in definitions:
        filename = f"{scenario_id}.csv"
        scenarios.append(dict(id=scenario_id, title=title, description=description,
                              file=filename, filename=filename, currency="SGD", expected=expected))
        files[filename] = csv_bytes(rows)
    manifest = {
        "synthetic": True,
        "period": {"start": "2026-04-01", "end": "2026-09-30"},
        "currency": "SGD",
        "notice": "Entirely fictional test data. No real bank, account holder, account number or bank connection.",
        "scenarios": scenarios,
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    files["README.md"] = guide(scenarios).encode("utf-8")
    return files


def guide(scenarios):
    rows = "\n".join(
        f"| `{s['file']}` | {'Validation error' if s['expected']['error'] else 'Pass' if s['expected']['verified'] else 'Fail'} | {s['expected']['notes']} |"
        for s in scenarios
    )
    return f"""# Credit Passport synthetic test pack

Every row is entirely fictional. No real person, bank, account number, employer,
merchant or bank connection is represented. The default currency is **SGD**.
All seven files cover **1 April to 30 September 2026**, including opening and
closing balance rows. The malformed example intentionally cannot be analysed.

## Try the deployed demo

Choose a scenario in the app, or download its CSV and upload it with currency SGD.
Use the table below to compare the result with the intended outcome. The ZIP
contains these seven CSVs, this guide and `manifest.json`.

| File | Running-balance outcome | Expected result |
| --- | --- | --- |
{rows}

For the steady example, the opening balance is SGD 5,000 and the closing balance
is SGD 21,564.50. Salary is SGD 4,800 on the 25th of each month and rent is
SGD 1,600 on the 5th. The four findings appear in this order: running balances,
regular income, rent payments and negative balances.

## What these tests establish

The pipeline's `verified` field means only that the supplied rows are in date
order and their running balances add up. A pass does **not** authenticate a bank
statement, establish identity, prove a file is unedited, assess affordability,
or grant credit. A fully fabricated statement with internally consistent figures
can pass. The first row's balance has no preceding row against which to check it.

The income rule finds similarly labelled receipts within 15% of the median in
at least three separate months. The irregular-income example tests that rule;
it does not claim the fictional holder received no income. The rent rule looks
for recurring negative payments, preferring rent-like descriptions, and treats
payments within three days of the most common payment day as on time. A failed
rent or overdraft finding can coexist with a passing running-balance check.

No API key, live financial data, bank integration or network request is needed
to analyse these CSVs. The existing repository samples are separate and untouched.

## Reproduce and check

From the repository root, run `python scripts/generate_demo_data.py` to reproduce
the files and ZIP, then `python -m pytest tests/test_demo_data.py`. Fixtures use
integer cents and fixed dates; ZIP timestamps are fixed so outputs are deterministic.
`manifest.json` describes expected results, including the intended validation
failure. The tests compare those expectations with the real Python pipeline.
"""


def write_files(output=OUTPUT):
    output.mkdir(parents=True, exist_ok=True)
    files = build_files()
    for name, data in files.items():
        (output / name).write_bytes(data)
    with ZipFile(output / "credit-passport-synthetic-demo.zip", "w") as archive:
        for name, data in sorted(files.items()):
            info = ZipInfo(name, date_time=(2026, 10, 13, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    return files


if __name__ == "__main__":
    files = write_files()
    print(f"Wrote {len(files)} fictional fixtures/guide files and one ZIP to {OUTPUT}")
