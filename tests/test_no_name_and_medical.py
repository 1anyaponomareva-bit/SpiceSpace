"""No «подруга» instead of a name; medical goals rule (Anya, 25.11.2026)."""

import unittest
from unittest import mock

import main
import names
import onboarding_flow as ob
import prompts
import safety
import specialty_blocks_en as en
import winback

NO = {"language_code": "ru", "name": "подруга", "main_goal": "x", "timezone": "Europe/Moscow"}


class Names(unittest.TestCase):
    def test_placeholder_is_not_a_name(self):
        for n in ("подруга", "Подруга", "friend", "", None):
            self.assertEqual(names.real_name(n), "", n)
        self.assertEqual(names.real_name(" Катя "), "Катя")

    def test_fill(self):
        self.assertEqual(names.fill("{name}, привет {d}", "подруга", d=1), "Привет 1")
        self.assertEqual(names.fill("Hey, {name} 🙂", "friend"), "Hey 🙂")
        self.assertEqual(names.fill("Nice to meet you, {name} 💚", "Ann"), "Nice to meet you, Ann 💚")
        self.assertEqual(names.fill("{name}, привет", "Катя"), "Катя, привет")

    def test_no_word_in_messages(self):
        texts = [
            ob.greeting_after_name("подруга", "ru"),
            ob.greeting_after_name("", "en"),
            prompts.morning_opening("подруга", lang="ru"),
            prompts.evening_opening(lang="ru", name="подруга", weekly="спать", has_task=False),
            main._milestone_fallback("подруга", 7, "ru"),
            main._morning_template(NO),
            main._format_task_reminder_text(NO, "вода"),
            main._trial_text("ru", "pause3", name="подруга", lock=""),
            ob.ob_text("subscription_paywall", "ru", name="подруга"),
            winback.onb_text(0, "ru", "подруга"),
        ]
        with mock.patch.object(main.db_store, "get_daily_summary", lambda *a: None):
            texts.append(main._morning_message_fallback(1, NO))
        for t in texts:
            self.assertNotIn("подруг", t.lower(), t)
            self.assertNotIn("friend", t.lower(), t)
            self.assertNotIn("{name}", t)
            self.assertFalse(t.startswith(","), t)
        self.assertTrue(texts[0].startswith("Приятно"), texts[0])

    def test_named_messages_unchanged(self):
        self.assertTrue(ob.greeting_after_name("Катя", "ru").startswith("Катя, "))
        self.assertTrue(prompts.morning_opening("Катя", lang="ru").startswith("Катя, доброе утро"))

    def test_chat_prompt(self):
        s = prompts.build_chat_system(NO, None, None, extra="", coach_style="")
        self.assertIn("Обращайся без имени", s)
        self.assertNotIn("Имя: подруга", s)

    def test_name_said(self):
        for t, n in (("Меня зовут катя", "Катя"), ("Привет, меня зовут Оля!", "Оля"), ("зови меня Ира", "Ира"),
                     ("my name is Ann", "Ann"), ("меня зовут на работу", ""), ("меня зовут подруга", ""),
                     ("дочку зовут Маша", ""), ("хочу спать", "")):
            self.assertEqual(names.said(t), n, t)

    def test_said_name_is_saved_only_without_one(self):
        calls = []
        with mock.patch.object(main.db_store, "update_profile", lambda c, f: calls.append(f)):
            main._note_name_said(1, "меня зовут Катя", dict(NO))
            main._note_name_said(1, "меня зовут Катя", {**NO, "name": "Оля"})
            main._note_name_said(1, "меня зовут Катя", {"name": "", "main_goal": ""})
        self.assertEqual(calls, [{"name": "Катя"}])


class Medical(unittest.TestCase):
    def test_in_chat_and_onboarding(self):
        self.assertIn("МЕДИЦИНСКИЕ ЦЕЛИ", safety.safety_rule("ru"))
        self.assertIn("MEDICAL GOALS", safety.safety_rule("en"))
        self.assertIn("MEDICAL GOALS", ob._system_with_lang("x", "ru"))

    def test_food_block_points_to_it(self):
        self.assertEqual(prompts.FITNESS_NUTRITION_INSTRUCTION.count("МЕДИЦИНСКИЕ ЦЕЛИ"), 2)
        self.assertEqual(en.FITNESS_NUTRITION_INSTRUCTION_EN.count("MEDICAL GOALS"), 2)

    def test_in_scheduled_calls(self):
        import asyncio
        seen = {}

        def gen(model, messages, *, system="", feature="", **k):
            seen[feature] = system
            return "текст"

        p = {**NO, "name": "Катя", "weekly_goal": "y"}
        with mock.patch.object(main, "claude_generate", gen):
            asyncio.run(main._today_task_options_message_text(p, ["m"]))
            asyncio.run(main._generate_today_task(p, ["m"]))
            asyncio.run(main._reengagement_message_text(p, ["m"], days_silent=5))
        for f in ("today_task_options", "today_task", "reengagement"):
            self.assertIn("МЕДИЦИНСКИЕ ЦЕЛИ", seen[f], f)

    def test_every_scheduled_prompt_in_source(self):
        src = open(main.__file__, encoding="utf-8").read()
        self.assertEqual(src.count("safety.medical_rule("), 8)


if __name__ == "__main__":
    unittest.main()
