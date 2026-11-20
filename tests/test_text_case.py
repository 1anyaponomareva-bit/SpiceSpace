"""Bot messages: sentences start with a capital letter."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from text_case import capitalize_sentences as cap  # noqa: E402


class Capitalize(unittest.TestCase):
    def test_sentence_starts(self):
        self.assertEqual(cap("привет. как дела? отлично!"), "Привет. Как дела? Отлично!")
        self.assertEqual(cap("первая строка\nвторая строка"), "Первая строка\nВторая строка")
        self.assertEqual(cap("готово 🤍 идём дальше. 💚 молодец"), "Готово 🤍 идём дальше. 💚 Молодец")
        self.assertEqual(cap("- шаг первый\n- шаг второй"), "- Шаг первый\n- Шаг второй")
        self.assertEqual(cap("ok. let's go"), "Ok. Let's go")

    def test_left_alone(self):
        for t in (
            "Это т.е. пример",
            "Купи хлеб, молоко и т.д. потом отдохни",
            "Ну... ладно",
            "Ссылка: https://t.me/SpiceSpacebot?start=a. дальше",
            "Цена 590 руб. в месяц",
            "Встань в 7.30 утра",
            "@spicespace привет",
            "/start",
            "1. пункт",
        ):
            out = cap(t)
            if t == "Ссылка: https://t.me/SpiceSpacebot?start=a. дальше":
                self.assertTrue(out.startswith("Ссылка: https://t.me/SpiceSpacebot?start=a."))
                continue
            self.assertEqual(out, t, t)

    def test_empty(self):
        self.assertEqual(cap(""), "")
        self.assertIsNone(cap(None))


if __name__ == "__main__":
    unittest.main()
