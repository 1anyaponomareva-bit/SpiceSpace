"""Win-back messages to people who went quiet."""

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import winback  # noqa: E402

A = date(2026, 11, 1)


def day(n: int) -> date:
    return date.fromordinal(A.toordinal() + n)


class Schedule(unittest.TestCase):
    def run_days(self, kind: str, last_day: int) -> list[int]:
        st: dict = {}
        sent = []
        for d in range(0, last_day + 1):
            if winback.due(kind, st, A, day(d)):
                sent.append(d)
                st = winback.after_send(st, A, day(d))
        return sent

    def test_onboarding_1_3_7_14_then_monthly(self):
        self.assertEqual(self.run_days("onb", 80), [1, 3, 7, 14, 44, 74])

    def test_silent_weekly_after_day15(self):
        self.assertEqual(self.run_days("silent", 80)[:3], [22, 29, 36])

    def test_lapsed_weekly(self):
        self.assertEqual(self.run_days("lapsed", 30), [7, 14, 21, 28])

    def test_she_writes_again_count_restarts(self):
        st = winback.after_send({}, A, day(1))
        new_anchor = day(5)
        self.assertFalse(winback.due("onb", st, new_anchor, day(5)))
        self.assertTrue(winback.due("onb", st, new_anchor, day(6)))  # day 1 again

    def test_not_twice_a_day(self):
        st = winback.after_send({}, A, day(7))
        st["n"] = 0  # even if the count says due
        self.assertFalse(winback.due("onb", {**st, "n": 1}, A, day(7)))


class Texts(unittest.TestCase):
    def test_onboarding_texts(self):
        self.assertIn("Как тебя зовут", winback.onb_text(0, "ru"))
        t = winback.onb_text(0, "ru", "Аня", "минус 5 кг к Новому году")
        self.assertTrue(t.startswith("Аня, привет"))
        self.assertIn("«минус 5 кг к Новому году»", t)
        self.assertTrue(winback.onb_text(1, "ru", "Аня").startswith("Аня, я помню"))
        self.assertEqual(winback.onb_text(9, "ru", "Аня"), winback.onb_text(3, "ru", "Аня"))
        self.assertIn("name", winback.onb_text(0, "en"))

    def test_lapsed_texts(self):
        t = winback.lapsed_text(0, "ru", "Аня", "бегать")
        self.assertIn("Как дела с целью «бегать»?", t)
        self.assertIn("Спейс можно включить", winback.lapsed_text(1, "ru", "Аня"))
        self.assertFalse(winback.lapsed_text(0, "ru").startswith("{name}"))

    def test_evening_in_her_zone(self):
        now = datetime(2026, 11, 21, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(winback.local_now({}, "ru", now, "Asia/Ho_Chi_Minh").hour, 19)
        self.assertEqual(winback.local_now({"timezone": "Asia/Ho_Chi_Minh"}, "ru", now, "UTC").hour, 23)


if __name__ == "__main__":
    unittest.main()


class Plan(unittest.TestCase):
    def test_groups_and_skips(self):
        from unittest import mock
        import main

        profiles = {
            "1": {"created_at": "2026-11-18T10:00:00+00:00", "language_code": "ru"},          # onb, 3 days
            "2": {"created_at": "2026-11-18T10:00:00+00:00", "language_code": "ru",
                  "daily_enabled": False},                                                     # /stop
            "3": {"created_at": "2026-11-18T10:00:00+00:00", "language_code": "ru",
                  "cycle_flags": {"ob_age_no": True}},                                         # under 18
            "4": {"created_at": "2026-11-21T08:00:00+00:00", "language_code": "ru"},          # started today
        }
        now = datetime(2026, 11, 21, 17, 0, tzinfo=timezone.utc)  # 20:00 Moscow
        with mock.patch.object(main.db_store, "kv_get", return_value={}), \
             mock.patch.object(main.db_store, "load_all_profiles", return_value=profiles):
            plan = main._winback_plan(now)
            self.assertEqual([(p["cid"], p["kind"], p["n"]) for p in plan], [(1, "onb", 0)])
            night = main._winback_plan(datetime(2026, 11, 21, 21, 0, tzinfo=timezone.utc))
            self.assertEqual(night, [])
