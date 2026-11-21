"""No 18+ buttons since 21.11.2026: age said in a message, note after onboarding."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import onboarding_flow as ob  # noqa: E402
import safety  # noqa: E402


class Minor(unittest.TestCase):
    def test_first_person_under_18(self):
        for t in ("мне 15", "Аня, мне 16 лет", "мне всего 14!", "мне ещё нет 18",
                  "я школьница", "я в 9 классе", "учусь в 10-м классе",
                  "I'm 16", "i am 15 years old", "I'm under 18", "im in 9th grade"):
            self.assertTrue(safety.detect_minor(t), t)

    def test_not_minor(self):
        for t in ("мне 35", "мне 15 минут хватит", "мне 5 кг скинуть", "дочке 15",
                  "сын в 9 классе", "мне 8 раз в день хочется есть", "I'm 34",
                  "I need 15 min", "Аня", "хочу похудеть на 10 кг", "мне 7 дней на это"):
            self.assertFalse(safety.detect_minor(t), t)

    def test_adult_lifts_block(self):
        for t in ("мне есть 18", "мне 25", "мне уже 19", "18+", "I'm 22"):
            self.assertTrue(safety.detect_adult(t), t)
        self.assertFalse(safety.detect_adult("мне 15"))


class Greeting(unittest.TestCase):
    def test_new_greeting_asks_name_without_18_line(self):
        ru = ob.get_greeting_new("ru")
        self.assertTrue(ru.startswith("Привет 👋 Я Спейс 🤍"))
        self.assertTrue(ru.endswith("Как тебя зовут?"))
        self.assertNotIn("18", ru)
        self.assertIn("name", ob.get_greeting_new("en"))

    def test_new_state_skips_age_buttons_and_notes_age_later(self):
        state: dict = {}
        ob.start_new_onboarding(state, 1, "ru")
        self.assertFalse(ob.age_pending(state[1]))
        self.assertTrue(state[1]["age_note"])
        self.assertIn("18", ob.age_reply_text("note", "ru"))
        self.assertIn("18", ob.age_reply_text("note", "en"))


if __name__ == "__main__":
    unittest.main()
