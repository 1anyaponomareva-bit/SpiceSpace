"""Money on lava.top for the admin panel (Anya, 24.11.2026).

The lava API has no balance, only invoices with the amount and lava's fee. So: what was
earned (after the fee) comes from the invoices; withdrawals and refunds are not in the
list, so the owner can type the balance she sees in lava, and from that moment new
payments are added to it. Pure functions; main.py reads the invoices.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from claude_balance import parse_ts

KV_KEY = "lava_balance"
CURRENCIES = ("RUB", "USD", "EUR")


def _num(v: object) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def summarize(invoices: list[dict], stored: dict | None, now: datetime, tz: ZoneInfo) -> dict:
    """invoices: items of GET /api/v1/invoices. stored: {"RUB": {"value", "at"}, ...} or None.
    Amounts only, no buyers."""
    stored = stored if isinstance(stored, dict) else {}
    local = now.astimezone(tz)
    day = local.date()
    cur: dict[str, dict] = {}
    for inv in invoices or []:
        if not isinstance(inv, dict) or str(inv.get("status") or "").upper() != "COMPLETED":
            continue
        rec = inv.get("receipt") if isinstance(inv.get("receipt"), dict) else {}
        amount, fee = _num(rec.get("amount")), _num(rec.get("fee"))
        code = str(rec.get("currency") or "").upper()
        if amount <= 0 or not code:
            continue
        net = amount - fee
        b = cur.setdefault(code, {"count": 0, "gross": 0.0, "fee": 0.0, "net": 0.0,
                                  "net_today": 0.0, "net_month": 0.0, "since_set": 0.0})
        b["count"] += 1
        b["gross"] += amount
        b["fee"] += fee
        b["net"] += net
        t = parse_ts(inv.get("datetime"))
        if t is None:
            continue
        d = t.astimezone(tz).date()
        if d == day:
            b["net_today"] += net
        if (d.year, d.month) == (day.year, day.month):
            b["net_month"] += net
        at = parse_ts((stored.get(code) or {}).get("at")) if isinstance(stored.get(code), dict) else None
        if at is not None and t >= at:
            b["since_set"] += net
    for code in stored:
        if code in CURRENCIES and isinstance(stored[code], dict):
            cur.setdefault(code, {"count": 0, "gross": 0.0, "fee": 0.0, "net": 0.0,
                                  "net_today": 0.0, "net_month": 0.0, "since_set": 0.0})
    out = []
    for code in sorted(cur, key=lambda c: CURRENCIES.index(c) if c in CURRENCIES else 9):
        b = {k: (v if k == "count" else round(v, 2)) for k, v in cur[code].items()}
        s = stored.get(code) if isinstance(stored.get(code), dict) else {}
        is_set = bool(s) and parse_ts(s.get("at")) is not None
        out.append({
            "currency": code, **b,
            "balance": round(_num(s.get("value")) + b["since_set"], 2) if is_set else b["net"],
            "set_value": round(_num(s.get("value")), 2) if is_set else None,
            "set_at": s.get("at") if is_set else None,
        })
    return {"currencies": out}
