"""Claude's reading: request shape, response validation and how the web flow degrades."""
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from passport import ai
from server import app


def fake_response(payload, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(payload))])


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    calls = []
    state = {"response": None, "error": None}

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            if state["error"]:
                raise state["error"]
            return state["response"] or fake_response({"rows": [{"row": n, "label": "income"} for n in range(1, 400)], "note": " Steady salary. "})

    monkeypatch.setattr(ai.anthropic, "Anthropic", lambda: SimpleNamespace(beta=SimpleNamespace(messages=Messages())))
    return SimpleNamespace(calls=calls, state=state)


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        yield client


def test_analysis_carries_claudes_labels_and_note(claude, client):
    data = client.post("/api/analyse", json={"scenario_id": "steady-income"}).json
    assert data["ai"]["note"] == "Steady salary."
    assert data["ai"]["model"] == ai.MODEL
    assert len(data["ai"]["rows"]) == data["row_count"]  # rows beyond the statement are dropped
    assert data["ai"]["rows"][0]["label"] == "income"
    request = claude.calls[0]
    assert request["output_config"]["format"]["schema"] == ai.SCHEMA
    assert request["fallbacks"] == "default"
    assert "1|2026-04-01|FICTIONAL DEMO OPENING BALANCE|0.00|" in request["messages"][0]["content"]


def test_repeat_clicks_reuse_one_claude_call(claude, client):
    client.post("/api/analyse", json={"scenario_id": "steady-income"})
    client.post("/api/analyse", json={"scenario_id": "steady-income"})
    assert len(claude.calls) == 1


def test_claude_failure_never_blocks_the_checks(claude, client):
    claude.state["error"] = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    data = client.post("/api/analyse", json={"scenario_id": "steady-income"}).json
    assert data["balanced"] is True and data["metrics"]["income"] == 4800.0
    assert "unavailable" in data["ai"]["error"]


def test_refusal_or_truncation_is_reported_not_parsed(claude, client):
    claude.state["response"] = fake_response({}, stop_reason="max_tokens")
    assert "unavailable" in client.post("/api/analyse", json={"scenario_id": "steady-income"}).json["ai"]["error"]


def test_no_claude_for_failed_statements_or_without_a_key(claude, client, monkeypatch):
    assert client.post("/api/analyse", json={"scenario_id": "edited-balances"}).json["ai"] is None
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert client.post("/api/analyse", json={"scenario_id": "steady-income"}).json["ai"] is None
    assert claude.calls == []
