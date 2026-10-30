"""The deposit agent: Claude applies the landlord's rule and, if it is met, takes the next steps on PayPal.

Tools: hold_deposit (ours, Orders v2 with intent AUTHORIZE, so the money is held, not taken) and the
PayPal Agent Toolkit's create_recurring_series / activate_recurring_series for monthly rent invoices.
Every call is checked here before it reaches PayPal: the deposit is at most one week's rent (the legal
cap on a holding deposit in England), and the rent series must be the agreed rent, monthly, in GBP, to
the applicant. The applicant still approves the deposit on PayPal's own page.
"""
import json
import os
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal

import anthropic

from . import paypal
from .ai import MODEL
from .vendor.paypal_agent_toolkit.shared.api import PayPalAPI
from .vendor.paypal_agent_toolkit.shared.configuration import Context
from .vendor.paypal_agent_toolkit.shared.tools import tools as TOOLKIT

MAX_TURNS = 6
TOOLKIT_METHODS = ("create_recurring_series", "activate_recurring_series")

PROMPT = """You act for a UK landlord letting a flat for GBP {rent} a month. Their rule:
"{rule}"

Facts from fixed checks on the applicant's PayPal history (do not recompute them):
- Regular monthly income: GBP {income}
- Proposed rent as a share of that income: {share}%
- Rent paid on time: {on_time}

If the rule is met, do all of this, calling tools in parallel where you can:
1. hold_deposit for GBP {deposit} (one week's rent).
2. create_recurring_series: monthly rent of GBP {rent}, 12 cycles, starting {start}, to {email}, item name "Rent", invoicer business name "{landlord}".
3. activate_recurring_series for the series you created.
If the rule is not met, call no tools.
Finish with one sentence of at most twelve words saying what you did and why. The deposit is only
requested until the applicant approves it on PayPal, so say "requested", not "held"."""

HOLD_DEPOSIT = {
    "name": "hold_deposit",
    "description": "Hold a holding deposit on the applicant's PayPal account (authorised, not captured). Returns the PayPal page the applicant approves it on.",
    "input_schema": {
        "type": "object",
        "properties": {"amount": {"type": "string", "description": "GBP amount with two decimals, at most one week's rent."}},
        "required": ["amount"],
        "additionalProperties": False,
    },
    "strict": True,
}


def configured():
    return paypal.configured() and bool(os.environ.get("ANTHROPIC_API_KEY"))


def one_week(rent):
    """Holding deposit cap in England: annual rent / 52, rounded down to the penny."""
    return (Decimal(rent) * 12 / 52).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _toolkit_tool(method):
    tool = next(t for t in TOOLKIT if t["method"] == method)
    return {"name": method, "description": tool["description"], "input_schema": tool["args_schema"].model_json_schema()}


class _Guard:
    """Runs the tools Claude asks for, refusing anything outside the agreed terms."""

    def __init__(self, rent, email, return_url, cancel_url):
        self.rent, self.email = Decimal(rent).quantize(Decimal("0.01")), email
        self.urls = (return_url, cancel_url)
        self.series = set()
        self.result = {"approve_url": None, "deposit": None, "series_id": None, "series_active": False}
        self.api = PayPalAPI(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"],
                             Context(sandbox="sandbox" in paypal.API_BASE, source="credit-passport"))

    def run(self, name, args):
        if name == "hold_deposit":
            amount = Decimal(str(args.get("amount", "0"))).quantize(Decimal("0.01"))
            if not Decimal("1") <= amount <= one_week(self.rent):
                raise ValueError(f"Refused: deposit must be between GBP 1 and one week's rent (GBP {one_week(self.rent)}).")
            self.result["approve_url"] = paypal.create_order(str(amount), "GBP", *self.urls, intent="AUTHORIZE")
            self.result["deposit"] = str(amount)
            return {"status": "awaiting applicant approval on PayPal", "amount": f"GBP {amount}"}
        if name == "create_recurring_series":
            self._check_series(args)
            created = json.loads(self.api.run(name, args))
            self.series.add(created["id"])
            self.result["series_id"] = created["id"]
            return {"id": created["id"], "status": created.get("status")}
        if name == "activate_recurring_series":
            if args.get("recurring_series_id") not in self.series:
                raise ValueError("Refused: only a series created in this run can be activated.")
            json.loads(self.api.run(name, args))
            self.result["series_active"] = True
            return {"id": args["recurring_series_id"], "status": "ACTIVE"}
        raise ValueError(f"Unknown tool {name}.")

    def _check_series(self, args):
        items = args.get("items") or []
        recipients = args.get("primary_recipients") or []
        email = ((recipients[0] if recipients else {}).get("billing_info") or {}).get("email_address", "")
        total = sum(Decimal(str(i["unit_amount"]["value"])) * Decimal(str(i.get("quantity", "1"))) for i in items)
        problems = [
            args.get("currency_code") != "GBP" and "currency must be GBP",
            (args.get("interval_unit"), args.get("interval_count")) != ("MONTH", 1) and "must be monthly",
            not 1 <= (args.get("total_cycles") or 0) <= 12 and "at most 12 cycles",
            total != self.rent and f"rent must be GBP {self.rent}",
            email.lower() != self.email.lower() and f"recipient must be {self.email}",
        ]
        problems = [p for p in problems if p]
        if problems:
            raise ValueError("Refused: " + "; ".join(problems) + ".")


def run(metrics, rent, rule, email, return_url, cancel_url, landlord="Credit Passport demo landlord", today=None):
    """Return {"steps": [...], "message", "approve_url", "deposit", "series_id", "series_active"}."""
    start = (today or date.today()) + timedelta(days=7)
    prompt = PROMPT.format(
        rent=f"{Decimal(rent):.2f}", rule=rule, income=f"{metrics['income']:.2f}",
        share=round(float(Decimal(rent)) / metrics["income"] * 100) if metrics.get("income") else "unknown",
        on_time=metrics.get("on_time", "unknown"), deposit=one_week(rent), start=start.isoformat(),
        email=email, landlord=landlord,
    )
    guard = _Guard(rent, email, return_url, cancel_url)
    tools = [HOLD_DEPOSIT] + [_toolkit_tool(m) for m in TOOLKIT_METHODS]
    messages = [{"role": "user", "content": prompt}]
    steps = []
    client = anthropic.Anthropic()
    for _ in range(MAX_TURNS):
        response = client.beta.messages.create(
            model=MODEL, max_tokens=16000, tools=tools, messages=messages,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        if response.stop_reason == "end_turn":
            message = " ".join(b.text for b in response.content if b.type == "text").strip()
            return {"steps": steps, "message": message, **guard.result}
        if response.stop_reason != "tool_use":
            raise ValueError(f"Claude stopped early ({response.stop_reason}).")
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in (b for b in response.content if b.type == "tool_use"):
            try:
                output, ok = guard.run(block.name, block.input), True
            except paypal.PayPalError as error:
                output, ok = {"error": str(error)}, False
            except (ValueError, KeyError, TypeError) as error:
                output, ok = {"error": str(error)[:300]}, False
            except Exception as error:  # the toolkit raises requests errors on PayPal failures
                output, ok = {"error": f"PayPal call failed: {type(error).__name__}"}, False
            steps.append({"tool": block.name, "ok": ok, **({"result": output} if ok else output)})
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(output), "is_error": not ok})
        messages.append({"role": "user", "content": results})
    raise ValueError("The agent did not finish in time.")
