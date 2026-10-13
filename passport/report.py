"""The one-page passport a landlord or lender keeps. Summary only, never the full statement."""
import io
from datetime import date
from html import escape

from .bank import name_note

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

GREEN, AMBER, GREY = colors.HexColor("#1a7f37"), colors.HexColor("#9a6700"), colors.HexColor("#57606a")


def build_pdf(passport, currency, rate, rate_source, applicant="", code="", source="", *, demo=False):
    if not passport["verified"]:
        raise ValueError("No passport is issued for a statement that does not add up.")
    styles = getSampleStyleSheet()
    small = styles["Normal"].clone("small", fontSize=8, textColor=GREY, leading=11)
    cell = styles["Normal"].clone("cell", fontSize=9, leading=12)
    start, end = passport["period"]

    def both(value):
        if value is None:
            return "n/a"
        return f"{currency} {value:,.0f}" + (f"\n(about £{value * rate:,.0f})" if rate is not None else "")

    ratio = passport["rent_to_income"]
    figures = Table(
        [["Typical recurring credit" if demo else "Monthly income", "Typical rent payment" if demo else "Monthly rent", "Rent / recurring credit" if demo else "Rent as share of income"],
         [both(passport["monthly_income"]), both(passport["monthly_rent"]), "n/a" if ratio is None else f"{ratio:.0%}"]],
        colWidths=[58 * mm] * 3,
    )
    figures.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, 0), 9), ("TEXTCOLOR", (0, 0), (-1, 0), GREY),
                                 ("FONTSIZE", (0, 1), (-1, 1), 15), ("LEADING", (0, 1), (-1, 1), 19),
                                 ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                 ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))

    rows = [["", "Check", "Result"]] + [
        ["PASS" if f.ok else "NOTE", f.label, Paragraph(escape(f.detail), cell)] for f in passport["findings"]
    ]
    checks = Table(rows, colWidths=[16 * mm, 42 * mm, 116 * mm])
    style = [("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (-1, 0), GREY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey), ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold")]
    style += [("TEXTCOLOR", (0, i), (0, i), GREEN if f.ok else AMBER) for i, f in enumerate(passport["findings"], 1)]
    checks.setStyle(TableStyle(style))

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, topMargin=20 * mm, bottomMargin=18 * mm, title="Credit Passport").build([
        Paragraph("Credit Passport", styles["Title"]),
        *([Paragraph("SYNTHETIC DEMO - not for lending or tenancy decisions", styles["Heading3"]),
           Spacer(1, 6)] if demo else []),
        *([Paragraph(f"Prepared for <b>{escape(applicant)}</b> ({name_note(source)}).", styles["Normal"]),
           Spacer(1, 4)] if applicant else []),
        Paragraph(f"Summary of a bank statement covering {start:%d %B %Y} to {end:%d %B %Y}. "
                  f"Issued {date.today():%d %B %Y}."
                  + (f" Statement source: {escape(source.lower())}." if source else ""), styles["Normal"]),
        Spacer(1, 10), figures, Spacer(1, 14),
        Paragraph("What was checked", styles["Heading3"]), checks, Spacer(1, 14),
        Paragraph("What this does not prove", styles["Heading3"]),
        Paragraph("This is a summary of past bank activity. It is not a credit score or a lending decision. "
                  "The figures come from a statement supplied by the applicant: the check confirms the statement "
                  "adds up and dates are ordered. It can detect edits that break the running-balance arithmetic; "
                  "it cannot establish authenticity, completeness or ownership.", cell),
        *([Spacer(1, 6), Paragraph("The recurring credit is the median of a detected payment group, not total monthly income. "
                                  "Rent timing is compared with the observed usual day, not a contractual due date. "
                                  "Only the supplied balance rows are checked; the opening balance has no independent check.", cell)] if demo else []),
        Spacer(1, 10),
        *([Paragraph(f"<b>Verification code: {code}</b>", styles["Normal"]),
           Paragraph("Landlords: enter this code in Credit Passport under \"Verify a passport\" to see the summary "
                     "as it was issued. If the figures there differ from this page, this page has been altered.", cell),
           Spacer(1, 10)] if code else []),
        Paragraph((f"1 {escape(currency)} = £{rate:.4f} ({escape(rate_source)}). " if rate is not None else "No currency conversion applied. ")
                  + "Prototype built on fictional sample data.", small),
    ])
    return out.getvalue()
