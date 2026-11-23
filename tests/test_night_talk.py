"""Late in the evening Space answers and does not send people away (Anya, 24.11.2026)."""

import unittest
from datetime import datetime

import main
import prompts


def at(h):
    return datetime(2026, 11, 24, h, 30)


RU, EN = {"language_code": "ru"}, {"language_code": "en"}


class Night(unittest.TestCase):
    def test_late_question_gets_the_rule(self):
        for h in (21, 23, 0, 3, 5):
            self.assertIn("полностью", main._night_talk_rule(RU, "Как накачать кубики пресса?", at(h)), h)
        self.assertIn("Answer in full", main._night_talk_rule(EN, "How much protein?", at(23)))

    def test_daytime_nothing(self):
        for h in (6, 9, 15, 20):
            self.assertEqual(main._night_talk_rule(RU, "Как накачать пресс?", at(h)), "")

    def test_she_is_going_to_bed(self):
        for t in ("Спокойной ночи", "всё, я спать", "Ложусь, до завтра", "пошёл спать"):
            self.assertEqual(main._night_talk_rule(RU, t, at(23)), "", t)
        self.assertEqual(main._night_talk_rule(EN, "good night!", at(23)), "")

    def test_core_rules(self):
        ru, en = prompts.SPICESPACE_CORE_SYSTEM_RU, prompts.SPICESPACE_CORE_SYSTEM
        self.assertNotIn("перенеси на завтра", ru)
        self.assertNotIn("move them to tomorrow", en)
        self.assertIn("не предлагай дел на сейчас", ru)
        self.assertIn("Если ей плохо ночью", ru)
        self.assertIn("If she feels bad at night", en)
        self.assertIn("answer questions in full", en)


if __name__ == "__main__":
    unittest.main()
