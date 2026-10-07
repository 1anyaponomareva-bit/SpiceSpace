"""Budget gates: one Claude call per ordinary turn, none for buttons and blocked users."""

import unittest

from claude_budget import (
    BUTTON_DONE_CLAUDE_CALLS,
    PHOTO_CLAUDE_CALLS,
    PLAIN_CHAT_CLAUDE_CALLS,
    REMINDER_CLAUDE_CALLS,
    claim_day_slot,
    is_billing_error,
    is_telegram_block_error,
    parse_structured_reply,
    profile_is_blocked,
    recent_history,
)


class StructuredReplyTests(unittest.TestCase):
    def test_tired_message_has_no_task_or_goal(self):
        raw = """{"reply":"Тяжёлый день, я рядом.","state_updates":{"task_completed":null,"new_task":null,"new_goal":null,"weekly_goal_update":null,"important_fact":null}}"""
        parsed = parse_structured_reply(raw)
        self.assertEqual(parsed["reply"], "Тяжёлый день, я рядом.")
        self.assertIsNone(parsed["state_updates"]["new_task"])
        self.assertIsNone(parsed["state_updates"]["new_goal"])
        self.assertEqual(PLAIN_CHAT_CLAUDE_CALLS, 1)

    def test_done_story_stays_one_call(self):
        raw = """{"reply":"Засчитала.","state_updates":{"task_completed":true,"new_task":null,"new_goal":null,"weekly_goal_update":null,"important_fact":null}}"""
        parsed = parse_structured_reply(raw)
        self.assertTrue(parsed["state_updates"]["task_completed"])
        self.assertEqual(PLAIN_CHAT_CLAUDE_CALLS, 1)
        self.assertEqual(PHOTO_CLAUDE_CALLS, 1)

    def test_plain_text_fallback(self):
        parsed = parse_structured_reply("Просто текст без JSON")
        self.assertEqual(parsed["reply"], "Просто текст без JSON")
        self.assertIsNone(parsed["state_updates"]["new_task"])


class SchedulerGateTests(unittest.TestCase):
    def test_button_done_and_reminder_do_not_call_claude(self):
        self.assertEqual(BUTTON_DONE_CLAUDE_CALLS, 0)
        self.assertEqual(REMINDER_CLAUDE_CALLS, 0)

    def test_blocked_user_is_skipped(self):
        self.assertTrue(profile_is_blocked({"telegram_blocked": True, "daily_enabled": True}))
        self.assertTrue(profile_is_blocked({"cycle_flags": {"telegram_blocked": True}}))
        self.assertFalse(profile_is_blocked({"daily_enabled": True}))

    def test_morning_slot_claimed_once_across_ten_runs(self):
        store: dict[str, str] = {}
        calls = 0
        for _ in range(10):
            if claim_day_slot(store, "morning:1", "2026-10-07"):
                calls += 1
        self.assertEqual(calls, 1)

    def test_summary_slot_claimed_once_across_ten_runs(self):
        store: dict[str, str] = {}
        calls = 0
        for _ in range(10):
            if claim_day_slot(store, "summary:1:2026-10-07", "2026-10-07"):
                calls += 1
        self.assertEqual(calls, 1)
        self.assertEqual(len(store), 1)

    def test_reengagement_slot_claimed_once_across_ten_runs(self):
        store: dict[str, str] = {}
        calls = 0
        for _ in range(10):
            if claim_day_slot(store, "reengage:1:day4", "2026-10-07"):
                calls += 1
        self.assertEqual(calls, 1)

    def test_billing_and_block_detection(self):
        self.assertTrue(is_billing_error(RuntimeError("Your credit balance is too low to access the Anthropic API.")))
        self.assertFalse(is_billing_error(RuntimeError("timeout")))
        self.assertTrue(is_telegram_block_error(RuntimeError("Forbidden: bot was blocked by the user")))
        self.assertFalse(is_telegram_block_error(RuntimeError("network down")))

    def test_history_is_capped(self):
        hist = [{"role": "user", "parts": [str(i)]} for i in range(40)]
        trimmed = recent_history(hist)
        self.assertEqual(len(trimmed), 12)
        self.assertEqual(trimmed[0]["parts"][0], "28")


if __name__ == "__main__":
    unittest.main()
