"""The deposit agent: Claude's tool calls run only inside the agreed terms."""
import json
from types import SimpleNamespace

import pytest

from passport import agent, paypal
from server import app

GOOD_SERIES = {
    "interval_unit": "MONTH", "interval_count": 1, "start_series_date": "2026-11-06", "total_cycles": 12,
    "currency_code": "GBP", "invoicer_business_name": "Landlord",
    "primary_recipients": [{"billing_info": {"email_address": "Tenant@example.com"}}],
    "items": [{"name": "Rent", "quantity": "1", "unit_amount": {"currency_code": "GBP", "value": "1050.00"}}],
}


def tool_use(id_, name, args):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("PAYPAL_DEMO_PAYER_EMAIL", "tenant@example.com")
    orders, toolkit, requests = [], [], []
    monkeypatch.setattr(paypal, "create_order", lambda *a, **k: orders.append((a, k)) or "https://www.sandbox.paypal.com/checkoutnow?token=X")

    class FakeToolkit:
        def __init__(self, *a): pass

        def run(self, method, params):
            toolkit.append((method, params))
            return json.dumps({"id": "RI-1", "status": "DRAFT"} if method == "create_recurring_series" else {})

    monkeypatch.setattr(agent, "PayPalAPI", FakeToolkit)
    script = [
        [tool_use("a", "hold_deposit", {"amount": "242.30"}), tool_use("b", "create_recurring_series", GOOD_SERIES)],
        [tool_use("c", "activate_recurring_series", {"recurring_series_id": "RI-1"})],
        [SimpleNamespace(type="text", text="Rule met: deposit requested and rent set up.")],
    ]

    class Messages:
        def create(self, **kwargs):
            requests.append({**kwargs, "messages": list(kwargs["messages"])})  # the loop keeps appending
            content = script[len(requests) - 1]
            return SimpleNamespace(stop_reason="tool_use" if content[0].type == "tool_use" else "end_turn", content=content)

    monkeypatch.setattr(agent.anthropic, "Anthropic", lambda: SimpleNamespace(beta=SimpleNamespace(messages=Messages())))
    return SimpleNamespace(orders=orders, toolkit=toolkit, requests=requests, script=script)


def test_one_week_is_the_legal_cap_rounded_down():
    assert str(agent.one_week("1050")) == "242.30"
    assert str(agent.one_week("1300")) == "300.00"


def test_agent_holds_deposit_and_sets_up_rent_through_the_toolkit(world):
    out = agent.run({"income": 3106.9, "on_time": "6/6"}, "1050", "Under 35%: go.", "tenant@example.com", "https://x/r", "https://x/c")
    assert out["approve_url"].startswith("https://www.sandbox.paypal.com/")
    assert out["deposit"] == "242.30" and out["series_id"] == "RI-1" and out["series_active"] is True
    assert [s["tool"] for s in out["steps"]] == ["hold_deposit", "create_recurring_series", "activate_recurring_series"]
    assert world.orders[0][1] == {"intent": "AUTHORIZE"}
    assert [m for m, _ in world.toolkit] == ["create_recurring_series", "activate_recurring_series"]
    prompt = world.requests[0]["messages"][0]["content"]
    assert "GBP 242.30" in prompt and "34%" in prompt and "6/6" in prompt
    assert {t["name"] for t in world.requests[0]["tools"]} == {"hold_deposit", "create_recurring_series", "activate_recurring_series"}
    # every tool result went back in one user message, after Claude's own turn
    assert [m["role"] for m in world.requests[1]["messages"]] == ["user", "assistant", "user"]
    assert len(world.requests[1]["messages"][2]["content"]) == 2


@pytest.mark.parametrize("change, reason", [
    ({"currency_code": "USD"}, "GBP"),
    ({"interval_unit": "WEEK"}, "monthly"),
    ({"total_cycles": 40}, "12 cycles"),
    ({"items": [{"name": "Rent", "quantity": "1", "unit_amount": {"currency_code": "GBP", "value": "2000.00"}}]}, "rent must be GBP 1050.00"),
    ({"primary_recipients": [{"billing_info": {"email_address": "someone@else.com"}}]}, "recipient"),
])
def test_out_of_terms_calls_are_refused_before_paypal(world, change, reason):
    world.script[0] = [tool_use("a", "hold_deposit", {"amount": "500.00"}), tool_use("b", "create_recurring_series", {**GOOD_SERIES, **change})]
    world.script[1] = [tool_use("c", "activate_recurring_series", {"recurring_series_id": "RI-9"})]
    out = agent.run({"income": 3106.9}, "1050", "go", "tenant@example.com", "r", "c")
    assert [s["ok"] for s in out["steps"]] == [False, False, False]
    assert "one week's rent" in out["steps"][0]["error"] and reason in out["steps"][1]["error"]
    assert world.orders == [] and world.toolkit == []
    assert out["approve_url"] is None and out["series_active"] is False
    results = world.requests[1]["messages"][2]["content"]
    assert all(r["is_error"] for r in results)


def test_agent_route_bounds_rent_and_remembers_the_offer(world):
    app.config.update(TESTING=True)
    with app.test_client() as client:
        assert client.post("/api/agent", json={"scenario_id": "paypal-sample", "rent": "5"}).status_code == 400
        assert client.post("/api/agent", json={"scenario_id": "steady-income", "rent": "1050"}).status_code == 400  # SGD
        response = client.post("/api/agent", json={"scenario_id": "paypal-sample", "rent": "1050"})
        assert response.status_code == 200 and response.json["deposit"] == "242.30"
        with client.session_transaction() as session:
            offer = session["offer"]
    assert offer == {"name": "Amira Haddad", "verified": False, "history": "Sample", "income": 3107,
                     "rent": "1050.00", "share": 34, "record": "6/6", "series": "RI-1", "scenario": "paypal-sample"}
