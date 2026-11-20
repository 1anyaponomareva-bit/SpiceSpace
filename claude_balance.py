"""Approximate Claude API balance for the admin panel.

An ordinary API key cannot read the account balance, so the owner types the balance she
sees in the Anthropic console (or the top-up amount right after topping up). From that
moment on we subtract what the bot logged in claude_usage. Pure functions: main.py
reads the rows and the stored balance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

KV_KEY = "claude_balance"
WARN_DAYS = 7


def parse_ts(value: object) -> datetime | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cost(row: dict) -> float:
    try:
        return float(row.get("cost_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def summarize(rows: list[dict], stored: dict | None, now: datetime, tz: ZoneInfo) -> dict:
    """rows: claude_usage rows (cost_usd, created_at) for at least the last 7 days and
    since the stored balance moment. stored: {"balance_usd", "at"} or None."""
    day_start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    at = parse_ts((stored or {}).get("at"))
    spent_today = spent_7d = spent_since = 0.0
    first: datetime | None = None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        t = parse_ts(r.get("created_at"))
        if t is None or t > now:
            continue
        c = _cost(r)
        if t >= day_start:
            spent_today += c
        if t >= week_start:
            spent_7d += c
            first = t if first is None or t < first else first
        if at is not None and t >= at:
            spent_since += c
    # average over the days we actually have, at least one day
    span_days = 7.0
    if first is not None:
        span_days = min(7.0, max(1.0, (now - first).total_seconds() / 86400))
    per_day = spent_7d / span_days if spent_7d else 0.0

    out = {
        "spent_today_usd": round(spent_today, 2),
        "spent_7d_usd": round(spent_7d, 2),
        "per_day_usd": round(per_day, 2),
        "balance_usd": None,
        "balance_set_usd": None,
        "balance_set_at": None,
        "spent_since_set_usd": None,
        "days_left": None,
        "warn": False,
        "approx": True,
    }
    if at is not None:
        try:
            base = float((stored or {}).get("balance_usd"))
        except (TypeError, ValueError):
            base = None
        if base is not None:
            left = base - spent_since
            out.update(
                balance_usd=round(left, 2),
                balance_set_usd=round(base, 2),
                balance_set_at=at.isoformat(timespec="seconds"),
                spent_since_set_usd=round(spent_since, 2),
            )
            if per_day > 0:
                out["days_left"] = round(max(left, 0) / per_day, 1)
            out["warn"] = left <= 0 or (out["days_left"] is not None and out["days_left"] < WARN_DAYS)
    return out
