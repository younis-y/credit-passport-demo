"""Signs that a PDF statement was edited after the bank issued it.

Three checks, each from the file itself: which software last wrote it, whether it was saved
again after issue (an appended update or a later modified date), and whether its rows still
add up. Metadata can be forged too, so a clean result here is supporting evidence only;
the bank feed is what confirms the numbers.
"""
import io
import re

import pandas as pd
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .rules import check_balances

# Software the issuing banks use to produce statements. Demo: the fictional Thames Bank only.
BANK_GENERATORS = ("Thames Bank Statement Service",)
EDITORS = {"acrobat": "Adobe Acrobat", "word": "Microsoft Word", "quartz": "Apple Preview", "ilovepdf": "iLovePDF",
           "smallpdf": "Smallpdf", "sejda": "Sejda", "foxit": "Foxit", "nitro": "Nitro", "libreoffice": "LibreOffice",
           "pdfescape": "PDFescape", "canva": "Canva"}
ROW = re.compile(r"^(\d{2} \w{3} \d{4})\s{2,}(.+?)\s{2,}(?:([+-][\d,]+\.\d{2})\s+)?([\d,]+\.\d{2})$")


def _check(ok, good, bad, detail=""):
    return {"label": good if ok else bad, "ok": bool(ok), "detail": detail}


def rows(reader):
    """Statement rows as a table: lines of date, description, signed amount, balance."""
    found = []
    for page in reader.pages:
        for line in page.extract_text(extraction_mode="layout").splitlines():
            match = ROW.match(line.strip())
            if match:
                day, text, amount, balance = match.groups()
                found.append({"date": pd.to_datetime(day, format="%d %b %Y"), "description": text.strip(),
                              "amount": float((amount or "0").replace(",", "")), "balance": float(balance.replace(",", ""))})
    return pd.DataFrame(found, columns=["date", "description", "amount", "balance"])


def check(data):
    """Returns {"checks": [...], "rows": table}. Each check reads as a fact when it passes and names the problem when it fails."""
    try:
        reader = PdfReader(io.BytesIO(data))
        meta = reader.metadata or {}
    except PdfReadError:
        return {"checks": [_check(False, "", "Not a readable PDF")], "rows": pd.DataFrame()}
    software = str(meta.get("/Producer") or meta.get("/Creator") or "unknown software")
    editor = next((name for key, name in EDITORS.items() if key in software.lower()), None)
    made = _check(software.startswith(BANK_GENERATORS), "Made by the bank's system",
                  f"Saved again in {editor}" if editor else f"Made by {software[:40]}", software)

    created, modified = getattr(reader.metadata, "creation_date", None), getattr(reader.metadata, "modification_date", None)
    updates = data.count(b"%%EOF") - 1
    later = created and modified and (modified - created).total_seconds() > 60
    if later and (modified - created).days >= 1:
        problem = f"Edited {(modified - created).days} days after issue"
    else:
        problem = "Saved again after issue"
    saved = _check(not later and updates == 0, "Not saved again after issue", problem,
                   f"{updates} appended update(s); created {created}, modified {modified}")

    table = rows(reader)
    if len(table) < 2:
        adds = _check(False, "", "No statement rows found")
    else:
        finding = check_balances(table)
        bad = "Rows out of order"
        if not finding.ok and not finding.evidence.empty:
            first = finding.evidence.iloc[0]
            bad = f"Row {int(first.name) + 1} off by £{abs(first['balance'] - first['expected_balance']):,.0f}"
        adds = _check(finding.ok, "Balances add up", bad, finding.detail)
    return {"checks": [made, saved, adds], "rows": table}
