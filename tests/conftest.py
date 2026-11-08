import pytest

import claude_client


@pytest.fixture(autouse=True)
def _no_usage_writes(monkeypatch):
    """Tests must not write Claude usage rows to Supabase or claude_usage.jsonl."""
    monkeypatch.setattr(claude_client, "persist_usage", lambda row: None)
