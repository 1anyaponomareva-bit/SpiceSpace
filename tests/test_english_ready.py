"""English speakers (Anya, 22.11.2026, English Threads test from ~1.12): ?start=en_… means
English, «where are you?» before the onboarding summary, dollar prices, and a 3-month
payment in dollars never counted as 1 month."""

import asyncio
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lava_pay  # noqa: E402
import main  # noqa: E402
import onboarding_flow as ob  # noqa: E402
import tz_guess  # noqa: E402


class StartLabel(unittest.TestCase):
    def test_en_labels(self):
        for arg in ("en_th_0112", "EN_a", "en", "en-1"):
            self.assertTrue(main.is_en_start_label(arg), arg)
        for arg in ("", None, "th_1311", "enjoy", "ru_en"):
            self.assertFalse(main.is_en_start_label(arg), arg)


class Cities(unittest.TestCase):
    def test_us_and_world(self):
        cases = {
            "Chicago": "America/Chicago",
            "I live in Texas": "America/Chicago",
            "NYC": "America/New_York",
            "I'm in Seattle": "America/Los_Angeles",
            "Denver, Colorado": "America/Denver",
            "Phoenix": "America/Phoenix",
            "EST": "America/New_York",
            "Pacific time": "America/Los_Angeles",
            "London": "Europe/London",
            "Toronto": "America/New_York",
            "Sydney": "Australia/Sydney",
            "GMT+3": "Etc/GMT-3",
            "I'm in UTC -5": "Etc/GMT+5",
        }
        for text, tz in cases.items():
            self.assertEqual((tz_guess.city_timezone(text) or ("",))[0], tz, text)

    def test_not_a_city_answer(self):
        for text in ("I want to move to London someday", "hello there", "gmt+30", "хочу в Москву"):
            self.assertIsNone(tz_guess.city_timezone(text), text)

    def test_russian_unchanged(self):
        self.assertEqual(tz_guess.city_timezone("Казань")[0], "Europe/Moscow")
        self.assertEqual(tz_guess.city_timezone("я в Екб")[0], "Asia/Yekaterinburg")


class TzQuestion(unittest.TestCase):
    def test_who_is_asked(self):
        self.assertTrue(ob.needs_tz_question({"lang": "en"}))
        self.assertTrue(ob.needs_tz_question({"lang": "de"}))
        self.assertFalse(ob.needs_tz_question({"lang": "ru"}))
        self.assertFalse(ob.needs_tz_question({"lang": "uk"}))
        self.assertFalse(ob.needs_tz_question({"lang": "en", "tz_set": True}))
        self.assertFalse(ob.needs_tz_question({"lang": "en", "tz_asked": True}))
        self.assertFalse(ob.needs_tz_question({"lang": "en"}, {"cycle_flags": {"tz_set": True}}))

    def _complete(self, st, existing=None):
        msg = mock.Mock(reply_text=mock.AsyncMock())
        update = SimpleNamespace(message=msg)
        with mock.patch.object(ob.db, "get_profile", return_value=existing or {}), \
             mock.patch.object(ob, "persist_profile", side_effect=AssertionError("too early")):
            asyncio.run(ob._complete_onboarding(update, None, 1, st, {1: st}, {}, {}, set(), []))
        return msg

    def test_asked_before_summary(self):
        st = {"lang": "en", "step": ob.OB_MORNING_TIME}
        msg = self._complete(st)
        self.assertEqual(st["step"], ob.OB_TIMEZONE)
        self.assertEqual(msg.reply_text.await_args.args[0], ob.TZ_ASK_EN)
        kb = msg.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, "ob_tz:et")
        self.assertEqual(kb.inline_keyboard[-1][0].callback_data, "ob_tz:other")

    def _turn(self, st, text):
        cid = 1
        msg = mock.Mock(reply_text=mock.AsyncMock(), text=text)
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=cid), message=msg,
            effective_user=SimpleNamespace(language_code="en", first_name="Anna"),
        )
        context = SimpleNamespace(bot_data={}, bot=mock.Mock())
        with mock.patch.object(ob, "_complete_onboarding", new=mock.AsyncMock()) as done:
            asyncio.run(ob.handle_onboarding_turn(update, context, text, {cid: st}, {}, {}, set()))
        return done, msg

    def test_typed_city(self):
        st = {"lang": "en", "language_code": "en", "step": ob.OB_TIMEZONE, "name": "Anna", "tz_asked": True}
        done, _ = self._turn(st, "Chicago")
        done.assert_awaited_once()
        self.assertEqual((st["timezone"], st["tz_set"]), ("America/Chicago", True))

    def test_unclear_twice_then_goes_on(self):
        st = {"lang": "en", "language_code": "en", "step": ob.OB_TIMEZONE, "name": "Anna", "tz_asked": True}
        done, msg = self._turn(st, "somewhere nice")
        done.assert_not_awaited()
        self.assertEqual(msg.reply_text.await_args.args[0], ob.TZ_UNCLEAR_EN)
        done, _ = self._turn(st, "still dunno")
        done.assert_awaited_once()
        self.assertFalse(st.get("tz_set"))

    def test_button(self):
        cid = 777003
        st = {"lang": "en", "step": ob.OB_TIMEZONE}
        main.onboarding[cid] = st
        query = mock.Mock(data="ob_tz:pt", message=mock.Mock(chat_id=cid, text=ob.TZ_ASK_EN))
        query.answer = mock.AsyncMock()
        query.edit_message_text = mock.AsyncMock()
        update = SimpleNamespace(callback_query=query, effective_chat=None, effective_user=None)
        context = SimpleNamespace(bot=mock.Mock(send_message=mock.AsyncMock()), bot_data={})
        with mock.patch.object(main, "_restore_cid_flow"), \
             mock.patch.object(main, "_touch_user_message_date"), \
             mock.patch.object(main.ob, "_complete_onboarding", new=mock.AsyncMock()) as done:
            asyncio.run(main.handle_tz_callback(update, context))
        main.onboarding.pop(cid, None)
        done.assert_awaited_once()
        self.assertEqual(st["timezone"], "America/Los_Angeles")

    def test_persist_keeps_label_zone_and_marks_answer(self):
        saved = {}
        existing = {"timezone": "America/New_York", "cycle_flags": {"tz_en_label": True}}
        with mock.patch.object(ob.db, "get_profile", return_value=existing), \
             mock.patch.object(ob.db, "upsert_profile", side_effect=lambda cid, p: saved.update(p)), \
             mock.patch.object(ob.db, "save_subscriber"), \
             mock.patch.object(ob, "save_onboarding_summary"):
            ob.persist_profile(1, {"lang": "en", "language_code": "en", "main_goal": "g"}, [])
            self.assertEqual(saved["timezone"], "America/New_York")
            ob.persist_profile(1, {"lang": "en", "language_code": "en", "timezone": "America/Denver",
                                   "tz_set": True}, [])
            self.assertEqual(saved["timezone"], "America/Denver")
            self.assertTrue(saved["cycle_flags"]["tz_set"])


class Dollars(unittest.TestCase):
    OFFERS = {"1m": {"id": "a", "rub": 590, "usd": True, "usd_amount": 7.08},
              "3m": {"id": "b", "rub": 1490, "usd": True, "usd_amount": 17.9}}

    def test_shown_price(self):
        with mock.patch.dict(lava_pay._found, self.OFFERS, clear=True), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(lava_pay.USD_PRICES_ENV, None)
            self.assertEqual(lava_pay.shown_price("1m", "en"), "$7.08")
            self.assertEqual(lava_pay.shown_price("1m", "ru"), "590 ₽")
            with mock.patch.dict(os.environ, {lava_pay.USD_PRICES_ENV: "1m=9.99, 3m=24.99"}):
                self.assertEqual(lava_pay.shown_price("3m", "en"), "$24.99")
        with mock.patch.dict(lava_pay._found, {"1m": {"id": "a", "rub": 590, "usd": False}}, clear=True):
            os.environ.pop(lava_pay.USD_PRICES_ENV, None)
            self.assertEqual(lava_pay.shown_price("1m", "en"), "590 ₽")  # no dollar price: rubles

    def test_offer_keeps_usd_amount(self):
        products = [{"data": {"offers": [{"id": "x", "prices": [
            {"currency": "RUB", "amount": 590, "periodicity": "MONTHLY"},
            {"currency": "USD", "amount": 7.08, "periodicity": "MONTHLY"},
        ]}]}}]
        self.assertEqual(lava_pay.pick_offers(products)["1m"]["usd_amount"], 7.08)

    def test_buttons_and_trial_texts(self):
        os.environ.pop(lava_pay.USD_PRICES_ENV, None)
        with mock.patch.object(lava_pay, "api_key", return_value="k"), \
             mock.patch.dict(lava_pay._found, self.OFFERS, clear=True):
            kb = main._subscription_offer_keyboard(1, "en")
            self.assertEqual([r[0].text for r in kb.inline_keyboard], ["1 month - $7.08", "3 months - $17.90"])
            prices = main._trial_prices("en", date(2026, 11, 25))
            self.assertEqual((prices["p1"], prices["lock"]), ("$7.08", ""))  # no ruble lock line
            self.assertIn("590 ₽", main._trial_prices("ru", date(2026, 11, 25))["lock"])


class ThreeMonthsInDollars(unittest.TestCase):
    def test_period_from_lava_dates(self):
        ev = {"timestamp": "2026-12-02T10:00:00Z", "will_expire": "2027-03-02T10:00:00Z",
              "amount": 17.9, "currency": "USD"}
        self.assertEqual(lava_pay.plan_from_event(ev), "3m")
        ev["will_expire"] = "2027-01-02T10:00:00Z"
        self.assertEqual(lava_pay.plan_from_event(ev), "1m")

    def test_amount_in_dollars(self):
        with mock.patch.dict(lava_pay._found, Dollars.OFFERS, clear=True):
            os.environ.pop(lava_pay.USD_PRICES_ENV, None)
            self.assertEqual(lava_pay.plan_from_amount(17.9, "USD"), "3m")
            self.assertEqual(lava_pay.plan_from_amount(7.08, "USD"), "1m")
            self.assertEqual(lava_pay.plan_from_event({"amount": 17.9, "currency": "USD"}), "3m")


if __name__ == "__main__":
    unittest.main()
