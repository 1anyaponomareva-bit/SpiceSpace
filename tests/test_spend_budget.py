"""Hidden daily budget: past it the chat switches to the cheaper model."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import spend_budget  # noqa: E402
from tests.test_lang_paths import _main_functions  # noqa: E402


class SpendBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        spend_budget.reset()
        self.p = mock.patch("db.claude_usage_spent_since", return_value=None)
        self.p.start()

    def tearDown(self) -> None:
        self.p.stop()
        spend_budget.reset()

    def test_counts_and_limit(self) -> None:
        with mock.patch.dict("os.environ", {"CHAT_DAILY_BUDGET_USD": "0.10"}):
            spend_budget.record(7, 0.06)
            self.assertFalse(spend_budget.over_budget(7))
            spend_budget.record(7, 0.05)
            self.assertTrue(spend_budget.over_budget(7))
            self.assertFalse(spend_budget.over_budget(8))
        spend_budget.record("model-compare", 1.0)  # not a user id: ignored
        self.assertEqual(spend_budget.spent_today("model-compare"), 0.0)

    def test_restart_reads_day_total_from_db(self) -> None:
        with mock.patch("db.claude_usage_spent_since", return_value=0.2):
            self.assertTrue(spend_budget.over_budget(9))

    def test_default_budget(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertAlmostEqual(spend_budget.daily_budget_usd(), 0.17)

    def test_model_switch(self) -> None:
        ns = _main_functions({"_budget_models"}, {
            "spend_budget": spend_budget,
            "SCHEDULE_MODEL": "claude-haiku-5-5",
            "log": __import__("logging").getLogger("t"),
        })
        chain = ["claude-sonnet-5-5", "claude-haiku-5-5"]
        self.assertEqual(ns["_budget_models"](5, chain), chain)
        spend_budget.record(5, 1.0)
        self.assertEqual(ns["_budget_models"](5, chain), ["claude-haiku-5-5", "claude-sonnet-5-5"])


if __name__ == "__main__":
    unittest.main()
