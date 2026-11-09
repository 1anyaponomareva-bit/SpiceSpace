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


class PeopleTests(unittest.TestCase):
    def test_line(self):
        self.assertEqual(mc.person_line({"relation": "мама", "name": "Ольга", "note": "живёт в Казани"}),
                         "мама — Ольга: живёт в Казани")
        self.assertEqual(mc.person_line({"relation": "брат", "name": "", "note": ""}), "брат")
        self.assertEqual(mc.person_line({"relation": "", "name": "", "note": "x"}), "")

    def test_same_person_by_name_and_relation(self):
        existing = ["мама — Ольга: живёт в Казани", "парень — Дима", "брат", "подруга — Катя"]
        self.assertEqual(mc.same_person_lines(existing, {"relation": "бывший", "name": "Дима"}),
                         ["парень — Дима"])
        self.assertEqual(mc.same_person_lines(existing, {"relation": "брат", "name": "Саша"}), ["брат"])
        self.assertEqual(mc.same_person_lines(existing, {"relation": "подруга", "name": "Лена"}), [])

    def test_block(self):
        block = mc.people_block(["мама — Ольга"], "ru")
        self.assertIn("Люди в её жизни", block)
        self.assertIn("— мама — Ольга", block)
        self.assertEqual(mc.people_block([], "ru"), "")


class PersonParseTests(unittest.TestCase):
    def test_parse(self):
        from claude_budget import parse_structured_reply as parse_chat_reply
        out = parse_chat_reply('{"reply":"ок","state_updates":{"person":{"relation":"мама","name":"Ольга","note":null}}}')
        self.assertEqual(out["state_updates"]["person"], {"relation": "мама", "name": "Ольга", "note": ""})
        out = parse_chat_reply('{"reply":"ок","state_updates":{"person":null}}')
        self.assertIsNone(out["state_updates"]["person"])


class WeeksTests(unittest.TestCase):
    def test_block_all_weeks_in_order(self):
        rows = [{"week_number": 1, "summary": "Сделала лендинг. Мешала простуда."},
                {"week_number": 2, "summary": "  Первые 3 клиента,\n брат Саша помог.  "},
                {"week_number": 3, "summary": ""}]
        block = mc.weeks_block(rows, "ru")
        self.assertIn("Неделя 1: Сделала лендинг. Мешала простуда.", block)
        self.assertIn("Неделя 2: Первые 3 клиента, брат Саша помог.", block)
        self.assertNotIn("Неделя 3", block)
        self.assertEqual(mc.weeks_block([], "en"), "")

    def test_cycle_filter(self):
        import db
        rows = [{"week_number": 2, "week_start": "2026-11-09", "summary": "b"},
                {"week_number": 1, "week_start": "2026-11-02", "summary": "a"},
                {"week_number": 5, "week_start": "2026-08-01", "summary": "old cycle"}]
        from unittest import mock
        with mock.patch.object(db, "load_weekly_summaries", return_value=rows):
            out = db.load_cycle_weekly_summaries(1, "2026-11-02")
        self.assertEqual([r["summary"] for r in out], ["a", "b"])
