"""Write the fictional bank side of the sample applicant, Amira Haddad, at Thames Bank (not a real bank):

- synthetic_demo/openbanking-sample.json: what an Open Banking feed would return (simulated).
- synthetic_demo/bank-statement.pdf: the statement as the bank's system would issue it.
- synthetic_demo/bank-statement-edited.pdf: the same file after someone raised one PayPal transfer
  by 2,500 in Adobe Acrobat. Like a real Acrobat save, the edit is appended to the original file.
"""
import io
import json
from datetime import date, datetime, timezone
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, NameObject
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "synthetic_demo"
GENERATOR = "Thames Bank Statement Service 3.1"
ISSUED = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
EDITED = datetime(2026, 11, 9, 21, 14, tzinfo=timezone.utc)
EDITED_ROW, EDIT = 18, 2500.00  # row 18 is the August PayPal transfer

rows, balance = [], 420.00
rows.append((date(2026, 4, 30), "Opening balance", 0.0, balance))
for month in range(5, 11):
    for day, text, amount in [(5, "Tesco Stores", -64.20), (15, "PayPal transfer", 1200.00), (18, "TfL Travel", -86.00),
                              (22, "Pret A Manger", -9.85), (27, "Hackney Council Tax", -148.00)]:
        balance = round(balance + amount, 2)
        rows.append((date(2026, month, day), text, amount, balance))


def feed():
    """The Open Banking Read Transactions shape (OBIE v3.1), trimmed to the fields the checks use."""
    return {"Data": {
        "Account": {"AccountId": "tb-0001", "Nickname": "Current account", "Name": "Amira Haddad", "Servicer": "Thames Bank"},
        "Transaction": [{
            "BookingDateTime": f"{day.isoformat()}T09:00:00+00:00",
            "CreditDebitIndicator": "Credit" if amount >= 0 else "Debit",
            "Amount": {"Amount": f"{abs(amount):.2f}", "Currency": "GBP"},
            "TransactionInformation": text,
            "Balance": {"Amount": {"Amount": f"{bal:.2f}", "Currency": "GBP"}, "CreditDebitIndicator": "Credit"},
        } for day, text, amount, bal in rows[1:]],
    }}


def page(statement_rows):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, 790, "Thames Bank")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, 772, "Current account statement · 30 Apr 2026 to 31 Oct 2026")
    pdf.drawString(50, 758, "Amira Haddad · Account 0001")
    pdf.setFont("Helvetica-Bold", 9)
    y = 726
    for x, label in [(50, "Date"), (130, "Description"), (400, "Amount"), (480, "Balance")]:
        pdf.drawString(x, y, label)
    pdf.setFont("Helvetica", 9)
    for day, text, amount, bal in statement_rows:
        y -= 18
        pdf.drawString(50, y, day.strftime("%d %b %Y"))
        pdf.drawString(130, y, text)
        pdf.drawRightString(450, y, f"{amount:+,.2f}" if amount else "")
        pdf.drawRightString(540, y, f"{bal:,.2f}")
    pdf.drawString(50, 60, "Thames Bank is fictional. Sample statement for the Credit Passport demo.")
    pdf.save()
    return buffer.getvalue()


def stamp(moment):
    return moment.strftime("D:%Y%m%d%H%M%S+00'00'")


def genuine():
    writer = PdfWriter(clone_from=io.BytesIO(page(rows)))
    writer.add_metadata({"/Producer": GENERATOR, "/Creator": GENERATOR,
                         "/CreationDate": stamp(ISSUED), "/ModDate": stamp(ISSUED)})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def edited(original):
    changed = [(day, text, amount + EDIT, bal) if n == EDITED_ROW else (day, text, amount, bal)
               for n, (day, text, amount, bal) in enumerate(rows, start=1)]
    new = PdfReader(io.BytesIO(page(changed))).pages[0]
    writer = PdfWriter(io.BytesIO(original), incremental=True)
    target = writer.pages[0]
    target.replace_contents(ContentStream(new.get_contents(), writer))
    target[NameObject("/Resources")] = new["/Resources"].clone(writer)
    writer.add_metadata({"/Producer": "Adobe Acrobat Pro (64-bit) 25.1.20432", "/ModDate": stamp(EDITED)})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


if __name__ == "__main__":
    (OUT / "openbanking-sample.json").write_text(json.dumps(feed(), indent=1) + "\n")
    first = genuine()
    (OUT / "bank-statement.pdf").write_bytes(first)
    (OUT / "bank-statement-edited.pdf").write_bytes(edited(first))
    print(len(rows), "rows, closing balance", balance)
