"""Trial of 3 days for people who start on 24.11.2026 or later, 4 for everyone before."""

import asyncio
import unittest
from datetime import date, timedelta
from unittest import mock

import main
import prompts
import spice_stats as ss
import trial

OLD, NEW = date(2026, 11, 23), date(2026, 11, 24)


def prof(start):
    return {"name": "Аня", "language_code": "ru", "main_goal": "Запустить курс",
            "trial_start_date": start.isoformat(), "cycle_flags": {}, "daily_enabled": True}


class Length(unittest.TestCase):
    def test_days(self):
        self.assertEqual(trial.days_for("2026-11-23"), 4)
        self.assertEqual(trial.days_for("2026-11-24"), 3)
        self.assertEqual(trial.days_for(""), 3)
        self.assertEqual(trial.days_for(None), 3)

    def test_access(self):
        for start, n in ((OLD, 4), (NEW, 3)):
            p = prof(start)
            self.assertTrue(main._profile_in_trial(p, start + timedelta(days=n - 1)))
            self.assertFalse(main._profile_in_trial(p, start + timedelta(days=n)))

    def _evening(self, start, day):
        bot = mock.Mock()
        bot.send_message = mock.AsyncMock()
        p = prof(start)
        today = start + timedelta(days=day - 1)
        with mock.patch.object(main, "SUBSCRIPTIONS_ENABLED", True), \
                mock.patch.object(main.lava_pay, "enabled", return_value=False), \
                mock.patch.object(main.db_store, "mark_cycle_flag", lambda *a: None), \
                mock.patch.object(main.db_store, "get_daily_summary", lambda *a: {}), \
                mock.patch.object(main, "_subscription_offer_keyboard", lambda *a: None), \
                mock.patch.object(main, "_pay_mark", lambda *a: None):
            asyncio.run(main._maybe_send_trial_evening_message(bot, 8840, p, today, {}))
        return bot.send_message.call_args.kwargs["text"] if bot.send_message.await_count else ""

    def test_new_person(self):
        self.assertEqual(self._evening(NEW, 1), "")
        self.assertIn("первые 2 дня", self._evening(NEW, 2))
        self.assertIn("пробные 3 дня закончились", self._evening(NEW, 3))
        self.assertEqual(self._evening(NEW, 4), "")

    def test_old_person(self):
        self.assertEqual(self._evening(OLD, 2), "")
        self.assertIn("первые 3 дня", self._evening(OLD, 3))
        self.assertIn("пробные 4 дня закончились", self._evening(OLD, 4))

    def test_chat_knows_her_own_length(self):
        a = prompts.build_chat_system(prof(OLD), None)
        b = prompts.build_chat_system(prof(NEW), None)
        self.assertIn("Пробный период: 4 дня", a)
        self.assertIn("Пробный период: 3 дня", b)
        self.assertNotIn("первые 4 дня", b)
        self.assertEqual(a.split("---DYNAMIC---")[0], b.split("---DYNAMIC---")[0])


class Funnel(unittest.TestCase):
    def test_no_day_4(self):
        people = {"1": {"start": NEW, "tz": "Europe/Moscow", "label": "x", "done_at": None, "flags": {}}}
        times = {"1": ["2026-11-26T08:00:00+00:00", "2026-11-27T08:00:00+00:00"]}
        row = ss.build_trial_funnel(people, times, date(2026, 11, 28))["cohorts"][0]
        self.assertEqual(row["trial_days"], 3)
        self.assertEqual(row["reply_d3"], 1)
        self.assertIsNone(row["reply_d4"])
        self.assertIsNone(row["got_morning_d4"])


if __name__ == "__main__":
    unittest.main()
