"""The day of a reminder is understood or asked again, never silently today (25.11.2026)."""

import unittest
from datetime import date, datetime, timezone
from unittest import mock

import main
import reminder_dates as rd


class FakeDT(datetime):
    """Wednesday 25 November 2026, 12:00 in Moscow."""

    @classmethod
    def now(cls, tz=None):
        base = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)
        return base.astimezone(tz) if tz else base.replace(tzinfo=None)


def parse(text, tz="Europe/Moscow"):
    with mock.patch.object(main, "datetime", FakeDT):
        return main._parse_natural_reminder(text, {"language_code": "ru", "timezone": tz})


class Dates(unittest.TestCase):
    def ok(self, text, day, hhmm, title=None, tz="Europe/Moscow"):
        with self.subTest(text=text):
            out = parse(text, tz)
            self.assertIsNotNone(out)
            self.assertEqual((out.get("date"), out.get("time")), (day, hhmm), out)
            if title:
                self.assertEqual(out["title"], title)

    def test_russian(self):
        self.ok("напомни 3 декабря в 10:00 про запись к врачу", "2026-12-03", "10:00", "запись к врачу")
        self.ok("напомни в 10:00 3-го декабря про врача", "2026-12-03", "10:00", "врача")
        self.ok("напомни в пятницу в 12 взять вопросы к приёму", "2026-11-27", "12:00", "взять вопросы к приёму")
        self.ok("напомни во вторник в 18:00 про зал", "2026-12-01", "18:00", "зал")
        self.ok("напомни в следующую пятницу в 9:00 про отчёт", "2026-12-04", "09:00", "отчёт")
        self.ok("напомни через неделю в 9 про приём", "2026-12-02", "09:00", "приём")
        self.ok("напомни через 2 недели в 9:00 про приём", "2026-12-09", "09:00", "приём")
        self.ok("напомни через 3 дня в 20:00 про воду", "2026-11-28", "20:00", "воду")
        self.ok("напомни через месяц в 10:00 про врача", "2026-12-25", "10:00", "врача")
        self.ok("напомни 5 числа в 11:00 про оплату", "2026-12-05", "11:00", "оплату")
        self.ok("напомни 03.12.2026 в 10:00 про врача", "2026-12-03", "10:00", "врача")
        self.ok("напомни послезавтра в 10:00 про врача", "2026-11-27", "10:00", "врача")
        self.ok("напомни завтра в 9:00 про приём 3 декабря", "2026-11-26", "09:00", "приём 3 декабря")

    def test_english(self):
        self.ok("remind me on December 3 at 10am to call the doctor", "2026-12-03", "10:00", "call the doctor")
        self.ok("remind me on friday at 6pm to call mom", "2026-11-27", "18:00", "call mom")
        self.ok("remind me in 2 weeks at 9am to book a visit", "2026-12-09", "09:00", "book a visit")
        self.ok("remind me next monday at 9am to run", "2026-11-30", "09:00", "run")

    def test_past(self):
        self.ok("напомни 3 ноября в 10:00 про врача", "2027-11-03", "10:00")  # no year: the next one
        self.ok("напомни в 10:00 про воду", "2026-11-26", "10:00")  # 12:00 already: tomorrow
        self.ok("напомни в 18:00 про воду", "2026-11-25", "18:00")
        self.ok("напомни в среду в 10:00 про зал", "2026-12-02", "10:00")
        self.ok("напомни в среду в 18:00 про зал", "2026-11-25", "18:00")
        for t in ("напомни сегодня в 10:00 про воду", "напомни 3 ноября 2026 в 10:00 про врача",
                  "напомни вчера в 10:00 про врача", "remind me today at 9am to run"):
            self.assertEqual(parse(t), {"_day_problem": "past"}, t)

    def test_not_understood_is_asked(self):
        for t in ("напомни 31 февраля в 10:00 про врача", "напомни на следующей неделе в 10:00 про врача",
                  "напомни в конце месяца в 10:00 про врача", "напомни на выходных в 10:00 про зал",
                  "напомни 3/12/2026 в 10:00 про врача", "напомни в среду или в пятницу в 10:00 про зал",
                  "напомни 03.12 в 10:00 про врача", "напомни в декабре в 10:00 про врача"):
            self.assertEqual(parse(t), {"_day_problem": "unknown"}, t)

    def test_time_still_asked_when_missing(self):
        for t in ("напомни через неделю про приём", "напомни в пятницу про зал", "напомни в пятницу в 5 про зал"):
            self.assertIsNone(parse(t), t)

    def test_her_time_zone(self):
        # 09:00 UTC is 16:00 in Nha Trang: 15:00 is over there, but not in Moscow
        self.ok("напомни в 15:00 про воду", "2026-11-26", "15:00", tz="Asia/Ho_Chi_Minh")
        self.ok("напомни в 15:00 про воду", "2026-11-25", "15:00")

    def test_repeats(self):
        out = parse("напомни каждую пятницу в 10:00 про отчёт")
        self.assertEqual((out["repeat"], out["days_of_week"]), ("weekly", ["fri"]))
        self.assertEqual(parse("напоминай каждый день в 10:00 пить воду")["repeat"], "daily")

    def test_words_that_are_not_days(self):
        self.ok("напомни в 18:00 купить молоко и марлю", "2026-11-25", "18:00", "купить молоко и марлю")
        self.ok("напомни в 18:00 посмотреть среднюю цену", "2026-11-25", "18:00")

    def test_leap_day(self):
        self.assertEqual(rd.find("29 февраля", date(2026, 11, 25))[1], date(2028, 2, 29))


if __name__ == "__main__":
    unittest.main()
