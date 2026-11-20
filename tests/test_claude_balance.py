"""Admin panel: approximate Claude API balance."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_balance  # noqa: E402

TZ = ZoneInfo("Asia/Saigon")
NOW = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)  # 17:00 local


def row(hours_ago: float, cost: float) -> dict:
    return {"created_at": (NOW - timedelta(hours=hours_ago)).isoformat(), "cost_usd": cost}


class Balance(unittest.TestCase):
    def test_without_stored_balance_shows_spend_only(self):
        out = claude_balance.summarize([row(1, 0.5), row(30, 1.0), row(200, 9)], None, NOW, TZ)
        self.assertEqual(out["spent_today_usd"], 0.5)
        self.assertEqual(out["spent_7d_usd"], 1.5)
        self.assertIsNone(out["balance_usd"])
        self.assertFalse(out["warn"])

    def test_balance_minus_spend_since_and_warning(self):
        rows = [row(h, 1.0) for h in range(0, 168, 24)]  # $1 a day, 7 rows over 6 days
        stored = {"balance_usd": 10, "at": (NOW - timedelta(hours=50)).isoformat()}
        out = claude_balance.summarize(rows, stored, NOW, TZ)
        self.assertEqual(out["spent_since_set_usd"], 3.0)  # 48h, 24h and now
        self.assertEqual(out["balance_usd"], 7.0)
        self.assertAlmostEqual(out["per_day_usd"], 1.17, places=2)
        self.assertTrue(out["warn"])  # about 6 days left
        out = claude_balance.summarize(rows, {"balance_usd": 50, "at": stored["at"]}, NOW, TZ)
        self.assertFalse(out["warn"])

    def test_negative_balance_warns(self):
        stored = {"balance_usd": 2, "at": (NOW - timedelta(hours=2)).isoformat()}
        out = claude_balance.summarize([row(1, 5)], stored, NOW, TZ)
        self.assertEqual(out["balance_usd"], -3.0)
        self.assertTrue(out["warn"])
