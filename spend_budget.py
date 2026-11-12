"""Hidden daily model budget per user.

Every model call adds its cost to the user's total for the day (UTC). Once the total
passes CHAT_DAILY_BUDGET_USD, the chat answers with the cheaper model until the day
ends. The user sees nothing. After a restart the day's total is read back once from
the claude_usage table, so a deploy does not reset it.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

log = logging.getLogger("coach_bot")

# ≈ 15 ₽ at ~90 ₽ per dollar.
DEFAULT_DAILY_BUDGET_USD = 0.17

_lock = threading.Lock()
_spent: dict[tuple[str, str], float] = {}
_loaded: set[tuple[str, str]] = set()


def daily_budget_usd() -> float:
    raw = os.getenv("CHAT_DAILY_BUDGET_USD", "").strip()
    try:
        return float(raw) if raw else DEFAULT_DAILY_BUDGET_USD
    except ValueError:
        return DEFAULT_DAILY_BUDGET_USD


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _key(user_id: object, day: str | None = None) -> tuple[str, str] | None:
    uid = str(user_id or "").strip()
    if not uid.lstrip("-").isdigit():
        return None
    return uid, day or _today()


def record(user_id: object, cost_usd: float) -> None:
    key = _key(user_id)
    if key is None or not cost_usd:
        return
    with _lock:
        _spent[key] = _spent.get(key, 0.0) + float(cost_usd)


def _load(key: tuple[str, str]) -> None:
    try:
        import db as db_store

        stored = db_store.claude_usage_spent_since(key[0], f"{key[1]}T00:00:00+00:00")
    except Exception as e:
        log.warning("spend_budget load failed uid=%s: %s", key[0], e)
        stored = None
    with _lock:
        _loaded.add(key)
        if stored is not None:
            _spent[key] = max(_spent.get(key, 0.0), float(stored))


def spent_today(user_id: object) -> float:
    key = _key(user_id)
    if key is None:
        return 0.0
    if key not in _loaded:
        _load(key)
    with _lock:
        return _spent.get(key, 0.0)


def over_budget(user_id: object) -> bool:
    return spent_today(user_id) >= daily_budget_usd()


def reset() -> None:
    """Tests only."""
    with _lock:
        _spent.clear()
        _loaded.clear()
