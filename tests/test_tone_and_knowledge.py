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


class Calories(unittest.TestCase):
    def test_mifflin_example(self):
        # the numbers in the rule: a woman, 53 kg, 165 cm, 35
        base = 10 * 53 + 6.25 * 165 - 5 * 35 - 161
        self.assertAlmostEqual(base * 1.4, 1715, delta=5)
        self.assertAlmostEqual(base * 1.55, 1900, delta=5)
        self.assertLess(53 / 1.65 ** 2, 20)  # low normal weight: no deficit by the rule

    def test_rule_in_both_languages(self):
        ru, e = prompts.FITNESS_NUTRITION_INSTRUCTION, en.FITNESS_NUTRITION_INSTRUCTION_EN
        for part in ("- 161", "6,25", "1,55", "дефицит не предлагай", "ниже 18,5", "не меньше 1200"):
            self.assertIn(part, ru)
        for part in ("- 161", "6.25", "1.55", "do not suggest a deficit", "under 18.5", "never below 1200", "lb / 2.2"):
            self.assertIn(part, e)

    def test_old_formula_is_gone(self):
        import pathlib
        import re
        root = pathlib.Path(prompts.__file__).parent
        for f in root.glob("*.py"):
            t = f.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"(22-24|26-28)\s*(ккал|kcal)|(вес|weight)\s*[×xх]\s*2[2-8]", t), f.name)


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
