"""Russian speakers start on Moscow time, not the bot's Vietnam zone (21.11.2026)."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402
import onboarding_flow as ob  # noqa: E402
import prompts  # noqa: E402
import tz_guess  # noqa: E402


class Defaults(unittest.TestCase):
    def test_by_language(self):
        self.assertEqual(tz_guess.default_timezone("ru"), "Europe/Moscow")
        self.assertEqual(tz_guess.default_timezone("uk"), "Europe/Moscow")
        self.assertEqual(tz_guess.default_timezone("en"), tz_guess.bot_timezone())
        self.assertEqual(ob._default_timezone("ru"), "Europe/Moscow")
        self.assertEqual(prompts.resolve_user_timezone({"language_code": "ru"}), "Europe/Moscow")
        self.assertEqual(main._profile_timezone_name({"language_code": "ru", "timezone": ""}), "Europe/Moscow")
        self.assertEqual(main._profile_timezone_name({"language_code": "ru", "timezone": "Asia/Omsk"}), "Asia/Omsk")


class Cities(unittest.TestCase):
    def test_city_answers(self):
        for raw, tz in (
            ("Казань", "Europe/Moscow"), ("я в Казани", "Europe/Moscow"), ("живу в Екб", "Asia/Yekaterinburg"),
            ("у меня Минск", "Europe/Minsk"), ("Омск", "Asia/Omsk"), ("Томск", "Asia/Novosibirsk"),
            ("Самара", "Europe/Samara"), ("Самарканд", "Asia/Tashkent"), ("в питере", "Europe/Moscow"),
            ("Пхукет", "Asia/Bangkok"), ("я из Киева", "Europe/Kiev"),
        ):
            self.assertEqual((tz_guess.city_timezone(raw) or ("",))[0], tz, raw)

    def test_not_a_city_answer(self):
        for raw in ("хочу переехать в Москву", "Привет", "Сходить в зал во вторник", "Аня", ""):
            self.assertIsNone(tz_guess.city_timezone(raw), raw)


class CompletionNote(unittest.TestCase):
    def test_moscow_guess_is_said(self):
        note = ob.completion_tz_note({"timezone": "Europe/Moscow"}, "ru")
        self.assertIn("по Москве", note)
        text = ob.ob_text("onboarding_complete", "ru", main_goal="g", weekly_goal="w", mt="09:30", et="21:00", tz_note=note)
        self.assertIn("вечером в 21:00 по Москве.\nЕсли у тебя другое время, напиши свой город", text)

    def test_known_zone_plain(self):
        self.assertEqual(ob.completion_tz_note({"timezone": "Europe/Moscow", "cycle_flags": {"tz_set": True}}, "ru"), ".")
        self.assertEqual(ob.completion_tz_note({"timezone": "Asia/Omsk"}, "ru"), ".")
        self.assertEqual(ob.completion_tz_note({"timezone": "Asia/Ho_Chi_Minh"}, "en"), ".")


class CityHandler(unittest.TestCase):
    CID = 9901

    def _run(self, raw, prof):
        sent = []

        async def reply(msg, text, **kw):
            sent.append(text)

        with mock.patch.object(main, "_bot_reply", reply), \
             mock.patch.object(main, "_append_history_turn"), \
             mock.patch.object(main.db_store, "update_profile") as up:
            used = asyncio.run(main._handle_city_timezone(mock.Mock(), self.CID, raw, prof))
        main.user_profiles.pop(str(self.CID), None)
        return used, sent, up

    def test_city_sets_zone(self):
        prof = {"language_code": "ru", "main_goal": "g", "timezone": "Europe/Moscow", "morning_time": "09:30", "evening_time": "21:00"}
        used, sent, up = self._run("Новосибирск", prof)
        self.assertTrue(used)
        self.assertEqual(prof["timezone"], "Asia/Novosibirsk")
        self.assertTrue(prof["cycle_flags"]["tz_set"])
        self.assertIn("Новосибирска: утром в 09:30, вечером в 21:00", sent[0])
        up.assert_called_once()

    def test_ignored_when_zone_known_or_mid_onboarding(self):
        prof = {"language_code": "ru", "main_goal": "g", "cycle_flags": {"tz_set": True}}
        self.assertFalse(self._run("Новосибирск", prof)[0])
        self.assertFalse(self._run("Новосибирск", {"language_code": "ru", "main_goal": ""})[0])


class OneTimeFix(unittest.TestCase):
    def test_moves_russian_speakers_once(self):
        rows = [
            {"user_id": 1, "language_code": "ru", "timezone": "Asia/Ho_Chi_Minh"},
            {"user_id": 2, "language_code": "en", "timezone": "Asia/Ho_Chi_Minh"},
            {"user_id": 3, "language_code": "ru", "timezone": "Asia/Omsk"},
            {"user_id": main.ADMIN_TELEGRAM_ID, "language_code": "ru", "timezone": "Asia/Ho_Chi_Minh"},
            {"user_id": 5, "language_code": "uk", "timezone": "Asia/Ho_Chi_Minh", "cycle_flags": {"tz_set": True}},
            {"user_id": 6, "language_code": "uk", "timezone": None},
        ]
        with mock.patch.object(main.db_store, "kv_get", return_value=None), \
             mock.patch.object(main.db_store, "profiles_created_since", return_value=rows), \
             mock.patch.object(main.db_store, "update_profile") as up, \
             mock.patch.object(main.db_store, "kv_set") as kv:
            self.assertEqual(main._fix_guessed_timezones(), 2)
        self.assertEqual([c.args[0] for c in up.call_args_list], [1, 6])
        self.assertEqual(kv.call_args.args[1]["moved"], [1, 6])
        with mock.patch.object(main.db_store, "kv_get", return_value={"moved": [1]}), \
             mock.patch.object(main.db_store, "update_profile") as up2:
            self.assertEqual(main._fix_guessed_timezones(), 0)
        up2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
