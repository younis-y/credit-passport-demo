"""Turn a bank statement (CSV or PDF) into one tidy table of transactions.

Output columns: date, description, amount (credits positive, debits negative), balance.
"""
import io
import os
import csv
import math
import re

import pandas as pd

COLUMNS = ["date", "description", "amount", "balance"]
MODEL = os.environ.get("PASSPORT_MODEL", "claude-haiku-4-5-20251001")

PROMPT = (
    "Copy every row of this bank statement, including the opening balance row. "
    "Do not skip, merge, correct or invent rows.\n"
    "Reply with the ISO currency code on the first line, then one line per row and nothing else:\n"
    "YYYY-MM-DD|description|amount|balance\n"
    "Amount is positive for money in, negative for money out, 0 for a balance-only row. "
    "No thousands separators.\n\n"
)


class StatementError(ValueError):
    """The statement could not be read into rows. The message is safe to show the user."""


def _num(series, *, blank_is_zero=False):
    """Reject malformed money instead of silently turning it into zero."""
    def number(value):
        text = str(value).strip()
        if not text and blank_is_zero:
            return 0.0
        # Accept plain digits, standard grouping and Indian lakh grouping.
        if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3})(?:\.\d+)?", text):
            raise StatementError("A numeric cell is blank or invalid. Check amounts and balances.")
        result = float(text.replace(",", ""))
        if not math.isfinite(result) or abs(result) > 1e12:
            raise StatementError("Amounts and balances must be finite and no greater than 1 trillion.")
        return result
    return series.map(number)


def _tidy(df):
    df = df[COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
    if df["date"].isna().any():
        raise StatementError("Every transaction needs a readable date.")
    df["description"] = df["description"].astype(str).str.strip()
    if df["description"].eq("").any():
        raise StatementError("Every transaction needs a description.")
    df["amount"] = df["amount"].astype(float).round(2)
    df["balance"] = df["balance"].astype(float).round(2)
    if not all(math.isfinite(value) for value in df[["amount", "balance"]].to_numpy().flat):
        raise StatementError("Amounts and balances must be finite numbers.")
    return df.reset_index(drop=True)


# Banks name the same five columns differently. First match wins.
ALIASES = {
    "date": ["date", "txn date", "transaction date", "posting date", "value date"],
    "description": ["description", "narration", "details", "particulars", "transaction details"],
    "amount": ["amount"],
    "withdrawal": ["withdrawal", "withdrawals", "debit", "paid out", "dr"],
    "deposit": ["deposit", "deposits", "credit", "paid in", "cr"],
    "balance": ["balance", "running balance", "closing balance"],
}


def _column(raw, field, required=True):
    for name in ALIASES[field]:
        if name in raw.columns:
            return raw[name]
    if required:
        raise KeyError(f"no {field} column (looked for: {', '.join(ALIASES[field])})")
    return None


def _dates(values):
    """Parse date-only values before putting them into a bounded pandas series."""
    parsed = []
    for value in values:
        text = str(value).strip()
        if not text:
            raise StatementError("Every transaction needs a readable date.")
        try:
            day = pd.to_datetime(text, format="%Y-%m-%d") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else pd.to_datetime(text, dayfirst=True)
            if pd.isna(day) or day.tzinfo is not None or day != day.normalize():
                raise ValueError("not a date-only value")
            if not (1678 <= day.year <= 2261):
                raise ValueError("outside supported date range")
            parsed.append(day)
        except (ValueError, TypeError, OverflowError, AssertionError) as error:
            raise StatementError("Use calendar dates such as YYYY-MM-DD or DD/MM/YYYY, without a time or timezone, between 1678 and 2261.") from error
    return pd.Series(parsed, index=values.index, dtype="datetime64[ns]")


def parse_csv(file):
    if isinstance(file, (str, os.PathLike)):
        with open(file, "rb") as source:
            data = source.read()
    else:
        data = file.read()
    try:
        text = data.decode("utf-8-sig") if isinstance(data, bytes) else data.lstrip("\ufeff")
        rows = list(csv.reader(io.StringIO(text), strict=True))
    except (UnicodeError, csv.Error) as error:
        raise StatementError("Use a correctly quoted CSV file saved as UTF-8.") from error
    rows = [row for row in rows if row]
    if not rows:
        raise StatementError("The CSV is empty.")
    header = [name.strip().lower() for name in rows[0]]
    if any(not name for name in header) or len(set(header)) != len(header):
        raise StatementError("CSV column names must be present and unique.")
    if any(len(row) != len(header) for row in rows[1:]):
        raise StatementError("Every CSV row must have the same number of cells as the header.")
    raw = pd.DataFrame(rows[1:], columns=header)
    amount = _column(raw, "amount", required=False)
    if amount is not None:
        amount = _num(amount)
    else:
        deposits = _num(_column(raw, "deposit"), blank_is_zero=True)
        withdrawals = _num(_column(raw, "withdrawal"), blank_is_zero=True)
        if (deposits < 0).any() or (withdrawals < 0).any() or ((deposits > 0) & (withdrawals > 0)).any():
            raise StatementError("Use non-negative deposits and withdrawals, with only one non-zero value per row.")
        amount = deposits - withdrawals
    parsed_dates = _dates(_column(raw, "date"))
    table = pd.DataFrame(
        {
            "date": parsed_dates,
            "description": _column(raw, "description"),
            "amount": amount,
            "balance": _num(_column(raw, "balance")),
        }
    )
    # Many banks list the newest transaction first. The balance check needs oldest first.
    if len(table) > 1 and table["date"].is_monotonic_decreasing and table["date"].iloc[0] > table["date"].iloc[-1]:
        table = table.iloc[::-1]
    return _tidy(table)


def parse_pdf(data: bytes):
    """Read the PDF text, then have the model copy the rows out as plain lines.

    The model only transcribes. Every judgement is made later by rules, and the
    balance check in rules.py will catch a row the model copied wrongly.
    Plain lines rather than JSON roughly halve the wait (about 12s for 6 months).
    """
    import anthropic
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    if not text.strip():
        raise StatementError("This PDF has no readable text. Scanned images are not supported yet.")

    response = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=16000, messages=[{"role": "user", "content": PROMPT + text}]
    )
    if response.stop_reason != "end_turn":
        raise StatementError("The statement is too long to read in one go.")
    return parse_lines(response.content[0].text)


def parse_lines(reply: str):
    """Turn the model's reply into the table. Any line that is not a clean row is an error, never a guess."""
    currency, *lines = [line.strip() for line in reply.strip().splitlines() if line.strip()]
    rows = []
    for line in lines:
        date, _, rest = line.partition("|")
        parts = rest.rsplit("|", 2)  # a description may itself contain "|"
        if len(parts) != 3:
            raise StatementError(f"Could not read this row: {line!r}")
        rows.append([date, *parts])
    if not rows:
        raise StatementError("No transactions were found in this statement.")
    try:
        return _tidy(pd.DataFrame(rows, columns=COLUMNS)), currency.upper()
    except (ValueError, TypeError) as error:
        raise StatementError(f"A row has a date or amount that could not be read: {error}") from error
