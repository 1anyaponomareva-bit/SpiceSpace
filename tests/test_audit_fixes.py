"""Mistakes found in the dialogs of 21-23.11.2026 (Anya: «надо чинить все»)."""

import asyncio
import unittest
from datetime import datetime, timezone
from unittest import mock

import db
import main
import onboarding_flow as ob
import prompts
import safety
import tz_guess
import winback

MSK = "Europe/Moscow"


def U(h, m=0):
    return datetime(2026, 11, 23, h, m, tzinfo=timezone.utc)


class Zones(unittest.TestCase):
    def z(self, text, now=U(4, 8), cur=MSK):
        hit = tz_guess.message_timezone(text, now, cur)
        return hit[0] if hit else None

    def test_places(self):
        self.assertEqual(self.z("Нет, я диву в Томске. У меня сейчас 11.08"), "Asia/Tomsk")
        self.assertEqual(self.z("Другое время , Томск"), "Asia/Tomsk")
        self.assertEqual(self.z("Настройся на моё время – я в Казахстане"), "Asia/Almaty")
        self.assertEqual(self.z("Запомни что я живу в германии"), "Europe/Berlin")
        self.assertEqual(self.z("Я в Сиэтле"), "America/Los_Angeles")
        self.assertEqual(tz_guess.city_timezone("Город Талдыкорган")[0], "Asia/Almaty")
        self.assertEqual(tz_guess.city_timezone("Норильск")[0], "Asia/Krasnoyarsk")
        self.assertEqual(tz_guess.city_timezone("Штутгарт")[0], "Europe/Berlin")
        self.assertEqual(tz_guess.city_timezone("Омск")[0], "Asia/Omsk")
        self.assertEqual(tz_guess.city_timezone("Томск")[0], "Asia/Tomsk")

    def test_clock(self):
        self.assertEqual(self.z("У нас сейчас 22.16", U(17, 15)), "Asia/Yekaterinburg")
        self.assertEqual(self.z("У меня 13.51", U(8, 51)), "Asia/Yekaterinburg")
        self.assertEqual(self.z("У меня сейчас без четверти 10", U(16, 46)), "Asia/Yekaterinburg")
        self.assertEqual(self.z("У меня сейчас 11:08", U(4, 8)), "Asia/Novosibirsk")
        self.assertIsNone(self.z("У меня сейчас 07:08", U(4, 8)))  # already right

    def test_moscow_diff(self):
        self.assertEqual(self.z("Разница с Москвой на два часа"), "Asia/Yekaterinburg")
        self.assertEqual(self.z("мск+4"), "Asia/Novosibirsk")

    def test_not_a_zone(self):
        for t in (
            "Хочу переехать в Германию", "я в отпуск поеду в Казань", "У меня в 13:30 обед",
            "я в норме", "Мама живёт в Томске", "У меня вес 71.10", "Я в центре города",
            "Обед в 14:00", "Мой вес 62, рост 164", "У меня 13.30 обед", "Живу в постоянном стрессе",
            "Я был в Москве и устал", "У меня завтрак в 9 утра", "Сейчас пойду гулять",
            "я в 12 обедаю", "я в туалет и спать", "я в восторге", "я в перманентном шоке",
            "живу в первый раз", "я в веном состоянии", "Я в римском стиле",
        ):
            self.assertIsNone(self.z(t), t)

    def _run(self, raw, prof):
        saved, sent = [], []

        async def reply(_m, text, **_k):
            sent.append(text)

        with mock.patch.object(main.db_store, "update_profile", lambda cid, p: saved.append(p)), \
                mock.patch.object(main, "_bot_reply", reply), \
                mock.patch.object(main, "_append_history_turn", lambda *a: None):
            done = asyncio.run(main._handle_city_timezone(mock.Mock(), 8831, raw, prof))
        main.user_profiles.pop("8831", None)
        return done, saved, sent

    def test_saved_even_when_confirmed_wrong(self):
        prof = {"main_goal": "g", "language_code": "ru", "timezone": MSK, "cycle_flags": {"tz_set": True}}
        done, saved, sent = self._run("я живу в Томске", prof)
        self.assertTrue(done)
        self.assertEqual(saved[0]["timezone"], "Asia/Tomsk")
        self.assertTrue(saved[0]["cycle_flags"]["tz_said_at"])
        self.assertIn("Томска", sent[0])

    def test_long_message_goes_on_to_chat(self):
        prof = {"main_goal": "g", "language_code": "ru", "timezone": MSK, "cycle_flags": {}}
        done, saved, sent = self._run("Я живу в Томске, подскажи, что съесть на обед сегодня?", prof)
        self.assertFalse(done)
        self.assertEqual(saved[0]["timezone"], "Asia/Tomsk")
        self.assertEqual(sent, [])

    def test_bare_city_later_is_not_a_zone(self):
        prof = {"main_goal": "g", "language_code": "ru", "timezone": MSK, "cycle_flags": {"tz_set": True}}
        self.assertEqual(self._run("Томск", prof)[1], [])


class Quiet(unittest.TestCase):
    NOW = U(16)

    def r(self, *texts, last="2026-11-23", ts="2026-11-22T10:00:00+00:00"):
        turns = [{"role": "user", "parts": [t], "ts": ts} for t in texts]
        return winback.quiet_reason(turns, self.NOW, last, safety.detect_crisis)

    def test_reasons(self):
        self.assertEqual(self.r("я просто не вижу смысла жить", "Меню составишь?"), "crisis")
        self.assertEqual(self.r("Стоп"), "stop")
        self.assertEqual(self.r("Хватит задавать вопросы"), "stop")
        self.assertEqual(self.r("давай потом"), "")
        self.assertEqual(self.r("Стоп", last="2026-11-25"), "")  # came back later
        self.assertEqual(self.r("хочу похудеть"), "")
        self.assertEqual(self.r(), "")
        self.assertEqual(self.r("я не вижу смысла жить", ts="2026-09-01T10:00:00+00:00"), "")

    def test_no_none(self):
        self.assertNotIn("None", winback.onb_text(0, "ru", "None"))
        self.assertNotIn("None", winback.lapsed_text(0, "ru", "None"))

    def _send(self, turns, flags=None):
        bot = mock.Mock()
        bot.send_message = mock.AsyncMock()
        item = {"cid": 8832, "kind": "onb", "n": 0, "lang": "ru",
                "profile": {"cycle_flags": flags or {}, "last_user_message_date": "2026-11-23"}}
        with mock.patch.object(main.db_store, "load_history", lambda *a: turns), \
                mock.patch.object(main.db_store, "load_flow_blob", lambda c: {}), \
                mock.patch.object(main.db_store, "save_history_turn", lambda *a: None):
            asyncio.run(main._winback_send(bot, item, []))
        main.histories.pop(8832, None)
        return bot.send_message.await_count

    def test_send(self):
        now = datetime.now(timezone.utc).isoformat()
        self.assertEqual(self._send([]), 1)
        self.assertEqual(self._send([], {"crisis_at": now}), 0)
        self.assertEqual(self._send([{"role": "user", "parts": ["не хочу жить"], "ts": now}]), 0)

    def test_crisis_survives_restart(self):
        safety._last_crisis_at.pop(8833, None)
        safety.restore_crisis(8833, datetime.now(timezone.utc).isoformat())
        self.assertTrue(safety.in_crisis_window(8833))
        safety._last_crisis_at.pop(8833, None)

    def test_flag_text_kept_in_db(self):
        row = db._cycle_flags_for_row({"cycle_flags": {"crisis_at": "2026-11-23T10:00:00", "x": 1}})
        self.assertEqual(row, {"crisis_at": "2026-11-23T10:00:00", "x": True})
        p = {"cycle_flags": dict(row)}
        db._apply_cycle_flags_to_profile(p)
        self.assertEqual(p["cycle_flags"], row)


class Escape(unittest.TestCase):
    CID = 8834

    def setUp(self):
        self.addCleanup(lambda: dict.pop(main.pending_morning, self.CID, None))
        self.addCleanup(lambda: dict.pop(main.onboarding, self.CID, None))
        self.addCleanup(lambda: main.flow_mismatch_streak.pop(self.CID, None))

    def esc(self, raw):
        out = []

        async def fake(update, cid, raw, prof):
            out.append(raw)

        with mock.patch.object(main, "_exit_flow_and_reply", fake):
            asyncio.run(main._try_flow_escape(mock.Mock(), self.CID, raw, {}))
        return out

    def wait(self):
        dict.__setitem__(main.pending_morning, self.CID, {"task": "t", "awaiting_reminder": True})

    def test_midday(self):
        for raw in ("Зачем ?", "А время чего?", "Ты мне меню составишь?"):
            self.wait()
            self.assertEqual(self.esc(raw), [], raw)
            self.assertNotIn(self.CID, main.pending_morning, raw)
        for raw in ("напомни в 12-30", "16:00", "Нет", "16", "сколько времени?"):
            self.wait()
            self.assertEqual(self.esc(raw), [], raw)
            self.assertIn(self.CID, main.pending_morning, raw)

    def test_no_is_an_answer_stop_is_not(self):
        dict.__setitem__(main.onboarding, self.CID, {"step": ob.OB_VISION_DIALOG})
        self.assertEqual(self.esc("Нет"), [])
        self.assertIn(self.CID, main.onboarding)
        self.assertEqual(self.esc("стоп"), ["стоп"])

    def test_goal_confirm_keeps_onboarding(self):
        dict.__setitem__(main.onboarding, self.CID, {"step": ob.OB_GOAL_12W, "goal_confirm": True})
        for raw in ("В 8 вечера", "После 22"):
            self.assertEqual(self.esc(raw), [])
        self.assertIn(self.CID, main.onboarding)

    def test_times(self):
        for raw, want in (("напомни в 12-30", "12:30"), ("в 15", "15:00"), ("9 вечера", "21:00"),
                          ("Скоро пойду", None), ("10-15 минут", None), ("12.30", "12:30")):
            self.assertEqual(main._midday_hhmm(raw), want, raw)
        self.assertEqual(main._reminder_time_match("напомни в 12-30 пойти гулять")[0], "12:30")
        self.assertIsNone(main._reminder_time_match("напомни гулять в 10-15 минутах ходьбы"))


class Small(unittest.TestCase):
    def test_not_a_task(self):
        for raw in ("давай начнем с пяти тем", "Составить план питания на неделю", "С завтрака завтра",
                    "А что мне делать?", "куплю творог завтра"):
            self.assertTrue(main._not_a_task_reply(raw), raw)
        for raw in ("сегодня схожу в зал", "Беру прогулку 30 минут"):
            self.assertFalse(main._not_a_task_reply(raw), raw)

    def test_morning_hours(self):
        self.assertEqual(main._sane_morning("02:00"), "09:00")
        self.assertEqual(main._sane_morning("04:00"), "04:00")
        self.assertTrue(ob._strange_morning("02:00"))
        self.assertTrue(ob._strange_morning("22:00"))
        self.assertFalse(ob._strange_morning("05:30"))
        self.assertFalse(ob._strange_morning("14:00"))

    def test_gender_said(self):
        self.assertEqual(prompts.user_gender({"name": "Анна", "cycle_flags": {"gender_said": "male"}}), "male")
        self.assertTrue(main._GENDER_SAID_RE.match("Я - мужчина"))
        self.assertFalse(main._GENDER_SAID_RE.match("Я мужчина мечты ищу"))

    def test_evening_opening(self):
        self.assertIn("питаться", prompts.evening_opening(has_task=False, lang="ru", weekly="питаться по режиму"))
        self.assertEqual(prompts.evening_opening(has_task=False, lang="ru"), "Как прошёл день? 🌙")
        self.assertNotIn("None", prompts.evening_opening(has_task=True, lang="ru", name="None", task="зал"))

    def test_dialog_has_days(self):
        main.histories[8835] = [{"role": "user", "parts": ["Завтра день рождения"], "ts": "2026-11-21T10:00:00+00:00"}]
        self.addCleanup(lambda: main.histories.pop(8835, None))
        text = asyncio.run(main._last_dialog_messages(8835, ru=True))
        self.assertIn("(21 ноября): Завтра", text)


if __name__ == "__main__":
    unittest.main()
