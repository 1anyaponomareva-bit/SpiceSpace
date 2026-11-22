"""Russian address (Anya, 22.11.2026): always «ты», gender from the profile or the name,
no gendered forms when unknown. Men use the bot too."""

import unittest

import prompts


def prof(name="", gender=None, lang="ru"):
    p = {"name": name, "language_code": lang}
    if gender:
        p["gender"] = gender
    return p


class Gender(unittest.TestCase):
    def test_profile_wins(self):
        self.assertEqual(prompts.user_gender(prof("Анна", "male")), "male")

    def test_from_name(self):
        cases = {
            "Анна": "female", "Полина": "female", "Мария": "female", "Ольга Петрова": "female",
            "Никита": "male", "Илья": "male", "Дмитрий": "male", "Алексей": "male", "Лёша": "male",
            "Anna": "female", "Nikita": "male", "Ilya": "male",
            "Саша": None, "Женя": None, "Любовь": None, "Max": None, "": None, "💚": None,
        }
        for name, want in cases.items():
            self.assertEqual(prompts.user_gender(prof(name)), want, name)


class Rule(unittest.TestCase):
    def test_always_ty(self):
        for name in ("Анна", "Никита", "Саша"):
            r = prompts.address_rule(prof(name))
            self.assertIn("на «ты»", r)
            self.assertIn("никогда не на «вы»", r)

    def test_gender_forms(self):
        self.assertIn("женский род", prompts.address_rule(prof("Anna")))
        male = prompts.address_rule(prof("Никита"))
        self.assertIn("мужской род", male)
        self.assertIn("не подсказка про пол", male)
        self.assertIn("без форм рода", prompts.address_rule(prof("Саша")))

    def test_english_untouched(self):
        self.assertEqual(prompts.address_rule(prof("Anna", lang="en")), "")
        self.assertNotIn("«ты»", prompts.prepend_user_time(prof("Anna", lang="en"), "x"))

    def test_in_every_timed_system(self):
        s = prompts.prepend_user_time(prof("Никита"), "Ты — Спейс.")
        self.assertIn("мужской род", s)
        again = prompts.refresh_user_time_in_system(prof("Никита"), s)
        self.assertEqual(again.count("Обращение:"), 1)


if __name__ == "__main__":
    unittest.main()
