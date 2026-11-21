"""Eating-disorder signs: caught before the model, no false alarms (21.11.2026)."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ed_guard  # noqa: E402
import main  # noqa: E402
import onboarding_flow as ob  # noqa: E402
import prompts  # noqa: E402
import safety  # noqa: E402


class Detect(unittest.TestCase):
    def test_signs(self):
        for t in (
            "Иногда вызываю рвоту после еды, чтобы не поправиться",
            "Я вчера переела, сегодня не ем вообще и пойду на 2 часа в зал, чтобы сжечь",
            "пью слабительное чтобы не поправиться",
            "сорвалась и съела пачку печенья, ненавижу себя",
            "голодаю уже два дня",
            "не ем уже 3 дня",
            "I make myself throw up after dinner",
        ):
            self.assertEqual(ed_guard.detect(t), "signs", t)

    def test_acute(self):
        self.assertEqual(ed_guard.detect("Не ем уже 4 дня, кружится голова, вчера чуть не упала в обморок"), "acute")

    def test_no_false_alarms(self):
        for t in (
            "съела торт, было вкусно",
            "пропустила обед, была занята",
            "не ем сладкое уже 3 дня",
            "хочу сжечь жир на животе",
            "врач выписал мочегонное от давления",
            "у сына рвота с утра",
            "хочу похудеть, ем после 6",
            "не могу остановиться, когда начинаю есть сладкое",
            "",
        ):
            self.assertEqual(ed_guard.detect(t), "", t)


class WeightGoal(unittest.TestCase):
    def test_concerns(self):
        cases = (
            ("вешу 62, рост 165, хочу минус 15 кг", "bmi"),
            ("рост 170, хочу весить 50 кг", "bmi"),
            ("хочу похудеть с 70 до 55", "fast"),
            ("вешу 90 хочу сбросить 8 кг", ""),
            ("минус 8 кг за 12 недель", ""),
        )
        for text, want in cases:
            self.assertEqual(ed_guard.weight_goal_concern(ed_guard.parse_body(text)), want, text)

    def test_fast_reply_numbers(self):
        text = ed_guard.reply("fast", "ru", {"weight": 70, "target": 55})
        self.assertIn("минус 15 кг", text)
        self.assertIn("4-7 кг", text)
        self.assertNotIn("—", text)


class Wiring(unittest.TestCase):
    CID = 7701

    def tearDown(self):
        ed_guard._last_reply.pop(self.CID, None)
        dict.pop(main.onboarding, self.CID, None)

    def _run(self, raw, prof):
        sent = []

        async def reply(msg, text, **kw):
            sent.append(text)

        with mock.patch.object(main, "_bot_reply", reply), \
             mock.patch.object(main, "_append_history_turn"), \
             mock.patch.object(main.db_store, "mark_cycle_flag"):
            used = asyncio.run(main._handle_eating_disorder_signs(mock.Mock(), self.CID, raw, prof))
        return used, sent

    def test_fixed_reply_once_a_day(self):
        prof = {"language_code": "ru", "main_goal": "Выучить английский"}
        used, sent = self._run("голодаю уже два дня", prof)
        self.assertTrue(used)
        self.assertIn("специалисту по пищевому поведению", sent[0])
        self.assertFalse(self._run("голодаю уже три дня", prof)[0])  # then the model goes on

    def test_goal_numbers_in_onboarding(self):
        dict.__setitem__(main.onboarding, self.CID, {"step": ob.OB_VISION_DIALOG, "name": "A"})
        self.assertFalse(self._run("вешу 62, рост 165", {"language_code": "ru"})[0])
        used, sent = self._run("хочу минус 15 кг", {"language_code": "ru"})
        self.assertTrue(used)
        self.assertIn("ниже здоровой нормы", sent[0])

    def test_ordinary_message_passes(self):
        self.assertFalse(self._run("съела торт, было вкусно", {"language_code": "ru"})[0])


class Prompts(unittest.TestCase):
    def test_rule_everywhere(self):
        self.assertIn("EATING-DISORDER SAFETY", safety.SAFETY_RULE_RU)
        self.assertIn("EATING-DISORDER SAFETY", safety.SAFETY_RULE_EN)
        self.assertIn("EATING-DISORDER SAFETY", ob._system_with_lang("x", "ru"))

    def test_food_block_only_for_food(self):
        self.assertTrue(prompts.specialty_instructions("как дела", "ru", goal_text="Минус 6 кг к марту"))
        self.assertEqual(prompts.specialty_instructions("как дела", "ru", goal_text="Выучить английский до B1"), "")
        block = prompts.specialty_instructions("сколько белка есть?", "ru")
        self.assertIn("7 000 шагов", block)
        self.assertNotIn("нутрициолог", block.split("Не называешь себя")[0])

    def test_no_free_word_in_trial_texts(self):
        ru = " ".join(main._TRIAL_TEXTS["ru"].values())
        self.assertNotIn("бесплатн", ru.lower())
        self.assertNotIn("—", ru)
        self.assertIn("последний день пробного периода", main._TRIAL_TEXTS["ru"]["day3"])


if __name__ == "__main__":
    unittest.main()
