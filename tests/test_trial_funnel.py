"""Trial funnel by day (Anya, 23.11.2026): replies on trial days 1-4 of people who finished
onboarding on a day, by their own local day; onboarding answers do not count."""

import unittest
from datetime import date

import spice_stats as ss

D1 = date(2026, 11, 23)


def person(start=D1, tz="Europe/Moscow", done_at=None, flags=None, label="th_a"):
    return {"start": start, "tz": tz, "label": label, "done_at": done_at, "flags": flags or {}}


class Funnel(unittest.TestCase):
    def test_days_and_onboarding_excluded(self):
        people = {
            "1": person(done_at="2026-11-23T10:00:00+00:00"),
            "2": person(done_at="2026-11-23T10:00:00+00:00"),
        }
        times = {
            # 1: onboarding answers only on day 1, then day 2 and day 4
            "1": ["2026-11-23T09:59:00+00:00", "2026-11-24T08:00:00+00:00", "2026-11-26T08:00:00+00:00"],
            # 2: answered the final question after onboarding, nothing later
            "2": ["2026-11-23T09:00:00+00:00", "2026-11-23T10:05:00+00:00"],
        }
        out = ss.build_trial_funnel(people, times, date(2026, 11, 26))
        row = out["cohorts"][0]
        self.assertEqual(row["trial_day1"], "2026-11-23")
        self.assertEqual(row["trial_n"], 2)
        self.assertEqual([row[f"reply_d{n}"] for n in range(1, 5)], [1, 1, 0, 1])

    def test_local_day_of_the_person(self):
        # 23:30 Moscow on 23.11 is still day 1 for her, though 24.11 in Saigon
        people = {"1": person(tz="Europe/Moscow")}
        out = ss.build_trial_funnel(people, {"1": ["2026-11-23T20:30:00+00:00"]}, date(2026, 11, 24))
        self.assertEqual(out["cohorts"][0]["reply_d1"], 1)
        self.assertEqual(out["cohorts"][0]["reply_d2"], 0)

    def test_future_days_null(self):
        out = ss.build_trial_funnel({"1": person()}, {}, date(2026, 11, 24))
        row = out["cohorts"][0]
        self.assertEqual(row["reply_d2"], 0)
        self.assertIsNone(row["reply_d3"])
        self.assertIsNone(row["got_morning_d4"])

    def test_got_messages(self):
        flags = {"got_morning_d1": "x", "got_evening_d1": "x", "got_morning_d2": "x"}
        out = ss.build_trial_funnel({"1": person(flags=flags)}, {}, date(2026, 11, 24))
        row = out["cohorts"][0]
        self.assertEqual((row["got_morning_d1"], row["got_evening_d1"]), (1, 1))
        self.assertEqual((row["got_morning_d2"], row["got_evening_d2"]), (1, 0))

    def test_got_unknown_before_marks(self):
        out = ss.build_trial_funnel({"1": person(start=date(2026, 11, 20))}, {}, date(2026, 11, 24))
        row = out["cohorts"][0]
        self.assertIsNone(row["got_morning_d1"])
        self.assertEqual(row["got_morning_d4"], 0)

    def test_day_by_label(self):
        people = {"1": person(start=date(2026, 11, 22)), "2": person(start=date(2026, 11, 23))}
        times = {"1": ["2026-11-23T08:00:00+00:00"]}
        b = ss.build_trial_funnel(people, times, D1)["day_by_label"]["th_a"]
        self.assertEqual(b["trial_n"], 1)  # person 2 finished onboarding that day
        self.assertEqual(b["reply_d2"], 1)  # person 1 wrote on her day 2


if __name__ == "__main__":
    unittest.main()
