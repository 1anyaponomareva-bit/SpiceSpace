"""Conversion fixes (Anya, 22.11.2026): the name in one tap, tariffs right under the
message, method by Telegram language, checkout that survives a restart, the 590 ₽ price
until 1 December, a soft pause on days 5-7 and payment steps in the stats."""

import asyncio
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lava_pay  # noqa: E402
import main  # noqa: E402
import onboarding_flow as ob  # noqa: E402
import spice_stats  # noqa: E402


class NameButton(unittest.TestCase):
    def test_names(self):
        self.assertEqual(ob.telegram_name("Маша"), "Маша")
        self.assertEqual(ob.telegram_name("маша"), "Маша")
        self.assertEqual(ob.telegram_name("Anna Smith"), "Anna")
        self.assertEqual(ob.telegram_name("Анна-Мария"), "Анна-Мария")
        for bad in ("", None, "🌸", "Маша🌸", "M", "user123", "Admin", "A B C", "КОТИК"):
            self.assertEqual(ob.telegram_name(bad), "", bad)

    def test_keyboard(self):
        kb = ob.name_keyboard("Маша", "ru")
        self.assertEqual([r[0].text for r in kb.inline_keyboard], ["Я Маша 👋", "Зови меня иначе"])
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, "ob_name:tg")
        self.assertIsNone(ob.name_keyboard("123", "ru"))
        self.assertEqual(ob.name_keyboard("Anna", "en").inline_keyboard[0][0].text, "I'm Anna 👋")

    def _tap(self, data, st, first_name="Маша"):
        cid = 777002
        main.onboarding[cid] = st
        msg = mock.Mock(chat_id=cid, text="Как тебя зовут?")
        query = mock.Mock(
            data=data, message=msg,
            from_user=SimpleNamespace(language_code="ru", first_name=first_name),
        )
        query.answer = mock.AsyncMock()
        query.edit_message_text = mock.AsyncMock()
        update = SimpleNamespace(callback_query=query, effective_chat=None, effective_user=None)
        context = SimpleNamespace(bot=mock.Mock(send_message=mock.AsyncMock()), bot_data={})
        with mock.patch.object(main, "_restore_cid_flow"), \
             mock.patch.object(main, "_touch_user_message_date"), \
             mock.patch.object(main.ob, "handle_onboarding_turn", new=mock.AsyncMock()) as turn:
            asyncio.run(main.handle_name_callback(update, context))
        main.onboarding.pop(cid, None)
        return turn, context

    def test_tap_feeds_name(self):
        turn, _ = self._tap("ob_name:tg", {"step": ob.OB_NAME, "lang": "ru"})
        turn.assert_awaited_once()
        self.assertEqual(turn.await_args.args[2], "Маша")

    def test_other_asks(self):
        turn, context = self._tap("ob_name:other", {"step": ob.OB_NAME, "lang": "ru"})
        turn.assert_not_awaited()
        self.assertEqual(context.bot.send_message.await_args.kwargs["text"], ob.NAME_OTHER_ASK_RU)

    def test_stale(self):
        turn, context = self._tap("ob_name:tg", {"step": ob.OB_GOAL_DIALOG, "lang": "ru", "name": "Оля"})
        turn.assert_not_awaited()
        context.bot.send_message.assert_not_awaited()


class Pricing(unittest.TestCase):
    def test_price_until_december(self):
        self.assertTrue(lava_pay.price_locked_now(date(2026, 11, 30)))
        self.assertFalse(lava_pay.price_locked_now(date(2026, 12, 1)))
        self.assertEqual(lava_pay.our_rub("1m", date(2026, 11, 30)), 590)
        self.assertEqual(lava_pay.our_rub("1m", date(2026, 12, 1)), 990)
        self.assertEqual(lava_pay.our_rub("3m", date(2026, 11, 30)), 1490)
        self.assertEqual(lava_pay.our_rub("3m", date(2026, 12, 1)), 2490)

    def test_lock_line_goes_away(self):
        self.assertIn("до 1 декабря", main._trial_prices("ru", date(2026, 11, 25))["lock"])
        self.assertEqual(main._trial_prices("ru", date(2026, 12, 1))["lock"], "")

    def test_method_by_language(self):
        with mock.patch.dict(lava_pay._found, {"1m": {"id": "x", "rub": 590, "usd": True}}, clear=True):
            self.assertEqual(lava_pay.default_method("ru", "1m"), "ru")
            self.assertEqual(lava_pay.default_method("en", "1m"), "intl")
        with mock.patch.dict(lava_pay._found, {"1m": {"id": "x", "rub": 590, "usd": False}}, clear=True):
            self.assertEqual(lava_pay.default_method("en", "1m"), "ru")

    def test_no_long_dash(self):
        for table in lava_pay._T.values():
            for v in table.values():
                self.assertNotIn("—", v)
        for table in main._TRIAL_TEXTS.values():
            for v in table.values():
                self.assertNotIn("—", v)
                self.assertNotIn("бесплатн", v)

    def test_tariff_buttons_under_message(self):
        with mock.patch.object(lava_pay, "api_key", return_value="k"), \
             mock.patch.dict(lava_pay._found, {
                 "1m": {"id": "a", "rub": 590, "usd": True},
                 "3m": {"id": "b", "rub": 1490, "usd": True},
             }, clear=True):
            kb = main._subscription_offer_keyboard(1, "ru")
        self.assertEqual([r[0].text for r in kb.inline_keyboard], ["1 месяц - 590 ₽", "3 месяца - 1490 ₽"])
        self.assertEqual(kb.inline_keyboard[0][0].callback_data, "lava:buy:1m")


class CheckoutSurvivesRestart(unittest.TestCase):
    def test_saved_and_restored(self):
        kv: dict = {}
        cid = 55501
        with mock.patch.object(main.db_store, "kv_set", side_effect=lambda k, v: kv.__setitem__(k, v) or True), \
             mock.patch.object(main.db_store, "kv_get", side_effect=lambda k: kv.get(k)):
            lava_pay.checkout[cid] = {"plan": "1m", "method": "ru", "step": "email"}
            main._lava_co_save(cid)
            lava_pay.checkout.pop(cid)  # restart
            main._lava_co_checked.discard(cid)
            co = main._lava_co(cid)
        self.assertEqual((co["plan"], co["step"]), ("1m", "email"))
        lava_pay.checkout.pop(cid, None)


class SoftPause(unittest.TestCase):
    def _run(self, today, **prof_extra):
        prof = {"name": "Аня", "trial_start_date": "2026-11-20", "main_goal": "Бегать", **prof_extra}
        bot = mock.Mock(send_message=mock.AsyncMock())
        with mock.patch.object(main, "SUBSCRIPTIONS_ENABLED", True), \
             mock.patch.object(main, "_profile_is_premium_active", return_value=False), \
             mock.patch.object(main.db_store, "cycle_flag_sent", return_value=False), \
             mock.patch.object(main.db_store, "mark_cycle_flag", return_value=None), \
             mock.patch.object(main, "_pay_mark"), \
             mock.patch.object(main.safety, "in_crisis_window", return_value=False), \
             mock.patch.object(main, "ui_lang", return_value="ru"):
            main._trial_pause_sent.clear()
            asyncio.run(main._maybe_send_trial_pause(bot, 1, prof, today, {}))
        return bot.send_message

    def test_days_5_to_7(self):
        self.assertFalse(self._run(date(2026, 11, 23)).await_count)  # day 4
        sent = self._run(date(2026, 11, 24))  # day 5
        self.assertIn("твой шаг на сегодня готов", sent.await_args.kwargs["text"])
        self.assertIn("«Бегать»", self._run(date(2026, 11, 25)).await_args.kwargs["text"])
        self.assertTrue(self._run(date(2026, 11, 26)).await_count)  # day 7
        self.assertFalse(self._run(date(2026, 11, 27)).await_count)  # day 8: winback as before

    def test_never_after_stop_or_block(self):
        self.assertFalse(self._run(date(2026, 11, 24), daily_enabled=False).await_count)
        self.assertFalse(self._run(date(2026, 11, 24), telegram_blocked=True).await_count)


class PayStats(unittest.TestCase):
    def test_steps_by_day(self):
        t = datetime(2026, 11, 23, 5, 0, tzinfo=timezone.utc).isoformat()
        profiles = {
            "1": {"created_at": "2026-11-19T05:00:00+00:00",
                  "cycle_flags": {"pay_day3": t, "pay_open": t, "pay_link": t}},
            "2": {"created_at": "2026-11-19T05:00:00+00:00", "cycle_flags": {"pay_day3": t}},
        }
        out = spice_stats.build_spice_stats(profiles, {}, [], date(2026, 11, 23))
        tot = out["day_totals"]
        self.assertEqual((tot["pay_day3"], tot["pay_open"], tot["pay_link"], tot["pay_email"]), (2, 1, 1, 0))
        coh = [r for r in out["cohorts"] if r["start_day"] == "2026-11-19"][0]
        self.assertEqual(coh["pay_day3"], 2)


if __name__ == "__main__":
    unittest.main()


class OptionsOffer(unittest.TestCase):
    """Anya, 22.11.2026: the offer of options in every 12-week / weekly goal question."""

    def test_added_to_goal_question(self):
        q = "Хорошо, спорт и тело. Какой результат хотелось бы увидеть к концу этих 12 недель?"
        out = ob.with_options_offer(q, "ru")
        self.assertTrue(out.endswith("Если пока не знаешь, могу предложить несколько вариантов."))
        self.assertEqual(ob.with_options_offer(out, "ru"), out)  # never twice

    def test_not_under_card_or_list(self):
        card = "К 12-й неделе ты бегаешь 3 раза в неделю.\n\nПодходит такая цель?"
        self.assertEqual(ob.with_options_offer(card, "ru"), card)
        listed = "Можно так:\n1. Бег\n2. Йога\nЧто ближе?"
        self.assertEqual(ob.with_options_offer(listed, "ru"), listed)
        self.assertEqual(ob.with_options_offer("Записала.", "ru"), "Записала.")

    def test_weekly_fixed_texts(self):
        for key in ("weekly_dialog_opening", "new_week_morning_nudge"):
            self.assertIn(ob.OPTIONS_OFFER_RU, ob.ob_text(key, "ru"))
            self.assertIn(ob.OPTIONS_OFFER_EN, ob.ob_text(key, "en"))
