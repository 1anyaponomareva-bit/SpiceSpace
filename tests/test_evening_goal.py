"""Evening next step follows her weekly goal, not side topics of the chat."""

from __future__ import annotations

import unittest

from tests.test_lang_paths import _main_functions


class EveningNextStepRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = _main_functions({"_evening_next_step_rule"}, {})["_evening_next_step_rule"]

    def test_russian_names_weekly_goal(self) -> None:
        text = self.rule({"language_code": "ru", "main_goal": "запустить продукт",
                          "weekly_goal": "доделать бота"})
        self.assertIn("«доделать бота»", text)
        self.assertIn("прямо ведёт к этой цели", text)

    def test_falls_back_to_main_goal(self) -> None:
        text = self.rule({"language_code": "en", "main_goal": "Launch the app"})
        self.assertIn('"Launch the app"', text)

    def test_no_goal_still_has_rule(self) -> None:
        text = self.rule({"language_code": "ru"})
        self.assertTrue(text.startswith("Если предлагаешь шаг"))


if __name__ == "__main__":
    unittest.main()
