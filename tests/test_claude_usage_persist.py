"""Every Claude call is saved with its cost, so spend survives deploys."""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import claude_budget
import claude_client
import db


def _fake_response(model="claude-sonnet-4-5"):
    return SimpleNamespace(
        model=model,
        stop_reason="end_turn",
        content=[SimpleNamespace(type="text", text="Привет!")],
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=100,
            cache_read_input_tokens=2000,
            cache_creation_input_tokens=0,
        ),
    )


class UsageCostTests(unittest.TestCase):
    def test_cost_formula_sonnet_4_5(self):
        cost = claude_budget.usage_cost_usd(
            "claude-sonnet-4-5",
            input_tokens=1000,
            output_tokens=100,
            cache_read_input_tokens=2000,
            cache_creation_input_tokens=1000,
        )
        # 1000*3 + 100*15 + 2000*0.30 + 1000*3.75 = 8850 per 1M
        self.assertAlmostEqual(cost, 0.00885)

    def test_dated_model_id_is_priced(self):
        cost = claude_budget.usage_cost_usd("claude-haiku-4-5-20251001", input_tokens=419, output_tokens=99)
        self.assertAlmostEqual(cost, (419 * 1.0 + 99 * 5.0) / 1_000_000)

    def test_unknown_model_costs_zero(self):
        self.assertEqual(claude_budget.usage_cost_usd("mystery", input_tokens=10), 0.0)


class PersistUsageTests(unittest.TestCase):
    def test_generate_persists_row_with_cost(self):
        saved: list[dict] = []
        fake_client = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kw: _fake_response())
        )
        with mock.patch.object(claude_client, "get_client", return_value=fake_client), \
             mock.patch.object(claude_client, "persist_usage", saved.append), \
             mock.patch.object(claude_client, "_billing_stopped", False):
            text = claude_client.generate(
                "claude-sonnet-4-5",
                [{"role": "user", "content": "привет"}],
                system="system",
                feature="chat",
                user_id=42,
            )
        self.assertEqual(text, "Привет!")
        self.assertEqual(len(saved), 1)
        row = saved[0]
        self.assertEqual(row["feature"], "chat")
        self.assertEqual(row["model"], "claude-sonnet-4-5")
        self.assertEqual(row["user_id"], 42)
        self.assertEqual(row["cache_read_tokens"], 2000)
        self.assertAlmostEqual(row["cost_usd"], 0.0051)  # 3000 + 1500 + 600 per 1M

    def test_local_fallback_appends_jsonl(self):
        with mock.patch.object(db, "_use_supabase", False), \
             mock.patch.object(db, "CLAUDE_USAGE_PATH") as path:
            written: list[str] = []
            path.open.return_value.__enter__.return_value.write.side_effect = written.append
            db.save_claude_usage({"user_id": "7", "feature": "chat", "model": "m", "cost_usd": 0.01})
        record = json.loads(written[0])
        self.assertEqual(record["user_id"], 7)
        self.assertEqual(record["feature"], "chat")
        self.assertIn("created_at", record)

    def test_supabase_failure_never_raises(self):
        with mock.patch.object(db, "_use_supabase", True), \
             mock.patch.object(db, "_request", side_effect=RuntimeError("down")):
            db.save_claude_usage({"user_id": 1, "feature": "chat", "model": "m"})


if __name__ == "__main__":
    unittest.main()
