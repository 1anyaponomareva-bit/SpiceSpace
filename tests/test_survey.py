"""Question after the trial (Anya, 25.11.2026): who gets it, once, off by default."""

import asyncio
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import main
import survey

NOW = datetime(2026, 11, 30, 10, 0, tzinfo=timezone.utc)  # 13:00 in Moscow


def person(**kw):
    p = {
        "name": "Катя", "language_code": "ru", "main_goal": "Цель", "weekly_goal": "Неделя",
        "cycle_start_date": "2026-11-24", "trial_start_date": "2026-11-24",
        "timezone": "Europe/Moscow", "morning_time": "09:00", "daily_enabled": True,
        "cycle_flags": {},
    }
    p.update(kw)
    return p


def plan(prof, *, replies=("2026-11-25T10:00:00+00:00",), payments=(), state=None, now=NOW, check_hour=True):
    rows = [{"user_id": 7, "created_at": t} for t in replies]
    with mock.patch.object(main, "SUBSCRIPTIONS_ENABLED", True), \
            mock.patch.object(main.db_store, "lava_payments_all", return_value=None if payments is None else list(payments)), \
            mock.patch.object(main.db_store, "kv_get_strict", return_value=state or {}), \
            mock.patch.object(main.db_store, "user_message_times_since", return_value=rows), \
            mock.patch.object(main.db_store, "load_all_profiles", return_value={"7": prof}):
        return main._survey_plan(now, check_hour=check_hour)


class Who(unittest.TestCase):
    def test_sent_off_by_default(self):
        self.assertFalse(main.SURVEY_SEND)

    def test_fits(self):
        self.assertEqual([i["cid"] for i in plan(person())], [7])

    def test_does_not_fit(self):
        for kw in (
            {"trial_start_date": "2026-11-29"},  # trial still on
            {"telegram_blocked": True},
            {"daily_enabled": False},
            {"cycle_flags": {"ob_age_no": True}},
            {"language_code": "en"},
            {"weekly_goal": ""},
            {"is_premium": True, "subscription_end": "2026-12-30"},
            {"subscription_end": "2026-11-20"},  # paid before
            {"cycle_flags": {"crisis_at": "2026-11-28T10:00:00+00:00"}},
        ):
            self.assertEqual(plan(person(**kw)), [], kw)

    def test_replied_only_on_days_2_to_4(self):
        for replies, n in (
            ((), 0), (("2026-11-24T10:00:00+00:00",), 0), (("2026-11-28T10:00:00+00:00",), 0),
            (("2026-11-27T10:00:00+00:00",), 1),
            (("2026-11-24T21:30:00+00:00",), 1),  # already the 25th in Moscow
        ):
            self.assertEqual(len(plan(person(), replies=replies)), n, replies)

    def test_paid_or_unknown_payments(self):
        self.assertEqual(plan(person(), payments=[{"telegram_id": 7, "status": "success"}]), [])
        self.assertEqual(len(plan(person(), payments=[{"telegram_id": 7, "status": "new"}])), 1)
        self.assertEqual(plan(person(), payments=None), [])

    def test_once(self):
        self.assertEqual(plan(person(), state={"7": {"sent": "2026-11-29T10:00:00+00:00"}}), [])
        self.assertEqual(plan(person(), state={"7": {"failed": True}}), [])

    def test_daytime_and_not_near_the_pause(self):
        self.assertEqual(plan(person(), now=NOW.replace(hour=6)), [])
        self.assertEqual(plan(person(), now=NOW.replace(hour=16)), [])
        self.assertEqual(plan(person(morning_time="12:30")), [])
        z = ZoneInfo("Europe/Moscow")
        self.assertTrue(survey.good_moment(datetime(2026, 11, 30, 12, 0, tzinfo=z), "09:00"))

    def test_no_price_and_no_dash(self):
        for t in (survey.TEXT, survey.THANKS, survey.THANKS_TEXT):
            self.assertNotIn("—", t)
            self.assertNotIn("₽", t)
            self.assertNotIn("бесплат", t)


class Flow(unittest.TestCase):
    def setUp(self):
        self.kv = {}
        for p in (
            mock.patch.object(main, "SUBSCRIPTIONS_ENABLED", True),
            mock.patch.object(main.db_store, "kv_get", side_effect=lambda k: dict(self.kv)),
            mock.patch.object(main.db_store, "kv_get_strict", side_effect=lambda k: dict(self.kv)),
            mock.patch.object(main.db_store, "kv_set", side_effect=lambda k, v: self.kv.update(v) or True),
            mock.patch.object(main.db_store, "load_history", return_value=[]),
            mock.patch.object(main, "onboarding", {}),
            mock.patch.object(main, "histories", {}),
        ):
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(main.db_store, "save_history_turn")
        self.saved = p.start()
        self.addCleanup(p.stop)

    def test_send_marks_before_sending(self):
        seen = []

        class Bot:
            async def send_message(_s, **kw):
                seen.append(("7" in self.kv, kw["text"], len(kw["reply_markup"].inline_keyboard)))

        self.assertTrue(asyncio.run(main._survey_send(Bot(), {"cid": 7, "profile": person()})))
        self.assertEqual(seen, [(True, survey.TEXT, 5)])

    def test_unreadable_record_sends_nothing(self):
        bot = mock.MagicMock()
        with mock.patch.object(main.db_store, "kv_get_strict", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                asyncio.run(main._survey_send(bot, {"cid": 7, "profile": person()}))
        bot.send_message.assert_not_called()

    def test_failed_send_is_not_repeated(self):
        class Bot:
            async def send_message(_s, **kw):
                raise RuntimeError("x")

        self.assertFalse(asyncio.run(main._survey_send(Bot(), {"cid": 7, "profile": person()})))
        self.assertTrue(self.kv["7"]["failed"])
        self.assertEqual(survey.stats(self.kv)["sent"], 0)

    def test_button(self):
        self.kv["7"] = {"sent": NOW.isoformat()}
        u, c = mock.MagicMock(), mock.MagicMock()
        u.callback_query.data = "survey:price"
        u.callback_query.message.chat_id = 7
        u.callback_query.answer = mock.AsyncMock()
        u.callback_query.edit_message_reply_markup = mock.AsyncMock()
        c.bot.send_message = mock.AsyncMock()
        asyncio.run(main.handle_survey_callback(u, c))
        asyncio.run(main.handle_survey_callback(u, c))  # a second tap counts nothing
        c.bot.send_message.assert_awaited_once_with(chat_id=7, text=survey.THANKS)
        s = survey.stats(self.kv)
        self.assertEqual((s["sent"], s["answered"], s["answers"]["price"]), (1, 1, 1))

    def take(self, text, prof=None):
        reply = mock.AsyncMock()
        with mock.patch.object(main, "_bot_reply", reply):
            out = asyncio.run(main._survey_take_text(
                mock.MagicMock(), 7, prof or person(trial_start_date="2026-11-14"), text))
        return out, reply

    def test_words_after_the_pause_are_kept(self):
        self.kv["7"] = {"sent": datetime.now(timezone.utc).isoformat(), "answer": "steps"}
        out, reply = self.take("хотела больше про сон")
        self.assertTrue(out)
        self.assertEqual(reply.await_args.args[1], survey.THANKS_TEXT)
        self.assertIn(mock.call(7, "user", "хотела больше про сон"), self.saved.mock_calls)
        self.assertEqual(survey.stats(self.kv)["wrote_text"], 1)
        self.assertFalse(self.take("а ещё вот")[0])  # then the usual paywall

    def test_not_taken(self):
        self.assertFalse(self.take("привет")[0])  # no survey for her
        fresh = datetime.now(timezone.utc)
        self.kv["7"] = {"sent": fresh.isoformat()}
        self.assertFalse(self.take("хочу оплатить подписку")[0])
        with mock.patch.object(main.safety, "detect_distress", return_value=True):
            self.assertFalse(self.take("мне очень плохо")[0])
        self.kv["7"] = {"sent": (fresh - timedelta(days=8)).isoformat()}
        self.assertFalse(self.take("привет")[0])


class Stats(unittest.TestCase):
    def test_counts_only(self):
        s = survey.stats({"1": {"sent": "x", "answer": "many"}, "2": {"sent": "x"}})
        self.assertEqual(s["sent"], 2)
        self.assertEqual(set(s), {"sent", "answered", "wrote_text", "answers"})
        self.assertEqual(survey.stats(None)["sent"], 0)


if __name__ == "__main__":
    unittest.main()
