# Credit Passport synthetic test pack

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
| `steady-income.csv` | Pass | All four checks pass. Typical monthly income SGD 4,800; rent SGD 1,600; rent-to-income ratio 33.3%. |
| `edited-balances.csv` | Fail | Two rows fail the running-balance check. Income and rent may be calculated, but the failed statement must not receive an eligible summary PDF. |
| `missed-rent.csv` | Pass | Balances pass. Rent appears in 5 of 6 months, with Jul 2026 named as missing. Typical rent remains SGD 1,600. |
| `overdraft.csv` | Pass | Balances, income and rent pass. Four rows are below zero; the lowest balance is SGD -1,620.50. |
| `irregular-income.csv` | Pass | Balances and rent pass. No regular income is found, so the rent-to-income ratio is unavailable. This does not mean there was no income. |
| `out-of-order.csv` | Fail | Dates are out of order and three running-balance rows fail. An eligible summary PDF must not be issued. |
| `malformed-values.csv` | Validation error | Reject the upload with a readable validation error. Do not replace unreadable financial values with zero or issue a result. |

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
