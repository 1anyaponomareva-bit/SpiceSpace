"""Tone and extra food / training knowledge (Anya, 24.11.2026)."""

import unittest

import lang_context
import main
import prompts
import safety
import specialty_blocks_en as en


class Knowledge(unittest.TestCase):
    def test_only_for_food_and_training(self):
        self.assertIn("ЕДА НА КАЖДЫЙ ДЕНЬ", prompts.specialty_instructions("Как похудеть?", lang="ru"))
        self.assertEqual(prompts.specialty_instructions("Как выучить английский?", lang="ru"), "")

    def test_eating_disorder_rule_comes_first(self):
        for t, rule, new in (
            (prompts.FITNESS_NUTRITION_INSTRUCTION, "ПРЕЖДЕ ВСЕГО", "ЕДА НА КАЖДЫЙ ДЕНЬ"),
            (en.FITNESS_NUTRITION_INSTRUCTION_EN, "FIRST: with eating-disorder", "EVERYDAY FOOD"),
        ):
            self.assertLess(t.index(rule), t.index(new))
            self.assertIn("1200", t)
            self.assertIn("0.4-0.9 kg" if "EVERYDAY" in t else "0,4-0,9 кг", t)

    def test_limits_stay_last(self):
        t = prompts.FITNESS_NUTRITION_INSTRUCTION
        self.assertGreater(t.index("НЕЛЬЗЯ:"), t.index("ЕДА НА КАЖДЫЙ ДЕНЬ"))


class Tone(unittest.TestCase):
    def test_rules_in_both_languages(self):
        self.assertIn("можно начать с «нет»", main.COACH_STYLE_INSTRUCTION)
        self.assertIn('start with "no"', lang_context.COACH_STYLE_INSTRUCTION_EN)
        self.assertIn("Исключения", main.COACH_STYLE_INSTRUCTION)

    def test_no_strength_stays_in_support(self):
        self.assertTrue(main._in_support_mode([], "нет сил совсем"))
        self.assertFalse(main._in_support_mode([], "мне лень идти на тренировку"))


if __name__ == "__main__":
    unittest.main()
