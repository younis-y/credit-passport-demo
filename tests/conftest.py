import pytest

import server


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """Tests never call Anthropic; the AI tests install their own fake."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    server.ai_reading.cache_clear()
    server.ask_claude.cache_clear()
