import unittest
from datetime import date

import memory_context as mc

TZ = "Asia/Ho_Chi_Minh"
TODAY = date(2026, 11, 9)


def turn(role, text, ts):
    return {"role": role, "parts": [text], "ts": ts}


class HistoryDatesTests(unittest.TestCase):
    def test_old_user_messages_get_date(self):
        hist = [
            turn("user", "в пятницу собеседование", "2026-11-05T10:00:00+00:00"),
            turn("model", "удачи!", "2026-11-05T10:00:00+00:00"),
            turn("user", "привет", "2026-11-09T03:00:00+00:00"),
        ]
        out = mc.history_with_dates(hist, TODAY, TZ, "ru")
        self.assertEqual(out[0]["parts"][0], "(сообщение от 5 ноября) в пятницу собеседование")
        self.assertEqual(out[1]["parts"][0], "удачи!")
        self.assertEqual(out[2]["parts"][0], "привет")
        self.assertEqual(hist[0]["parts"][0], "в пятницу собеседование")

    def test_turn_without_ts_untouched(self):
        hist = [{"role": "user", "parts": ["x"]}]
        self.assertEqual(mc.history_with_dates(hist, TODAY, TZ, "ru"), hist)

    def test_supabase_ts_format(self):
        self.assertEqual(
            mc.turn_local_date({"ts": "2026-11-08T20:30:00.123456+00:00"}, TZ), date(2026, 11, 9)
        )


class GapNoteTests(unittest.TestCase):
    def test_gap_after_days(self):
        hist = [turn("user", "a", "2026-11-05T10:00:00+00:00"),
                turn("model", "morning", "2026-11-09T02:00:00+00:00")]
        note = mc.gap_note(hist, TODAY, TZ, "ru")
        self.assertIn("5 ноября", note)
        self.assertIn("4 дн.", note)
        self.assertIn("без упрёков", note)

    def test_no_gap_today(self):
        hist = [turn("user", "a", "2026-11-09T01:00:00+00:00")]
        self.assertEqual(mc.gap_note(hist, TODAY, TZ, "en"), "")

    def test_english(self):
        hist = [turn("user", "a", "2026-11-08T05:00:00+00:00")]
        note = mc.gap_note(hist, TODAY, TZ, "en")
        self.assertIn("November 8", note)
        self.assertNotIn("reproach", note)


class RecentDaysTests(unittest.TestCase):
    def test_last_three_past_days(self):
        sums = [{"date": f"2026-11-0{i}", "summary": f"день {i}"} for i in range(1, 10)]
        block = mc.recent_days_block(sums, TODAY, "ru")
        self.assertIn("8 ноября: день 8", block)
        self.assertIn("6 ноября: день 6", block)
        self.assertNotIn("день 5", block)
        self.assertNotIn("день 9", block)

    def test_empty(self):
        self.assertEqual(mc.recent_days_block([], TODAY, "ru"), "")


if __name__ == "__main__":
    unittest.main()
