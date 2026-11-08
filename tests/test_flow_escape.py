"""Escape/pause detection fires only on short commands, not on words inside real answers."""

import unittest

import main
import onboarding_flow as ob

REAL_ANSWERS = (
    "Эмиграция во Вьетнам, завишу от мужа, раньше была школа дизайнеров интерьера и свои "
    "объекты. Тут пришло полное обнуление и не понимание что делать. Дизайном не хочу "
    "заниматься выгорела. Пока для себя вижу выход в вайбкодинге",
    "Хочу наконец сказать себе стоп и начать бегать по утрам",
    "Работы у меня не сейчас, а через месяц, хочу подготовиться",
    "Стопроцентно хочу больше зарабатывать",
    "Хватит уже откладывать, хочу запустить свой продукт",
)
COMMANDS = ("стоп", "Хватит", "отмена", "выход", "давай потом", "не сейчас", "stop", "давай не сейчас")


class FlowEscapeTests(unittest.TestCase):
    def test_real_answers_are_not_escape(self):
        for text in REAL_ANSWERS:
            with self.subTest(text=text[:30]):
                self.assertFalse(main._user_wants_flow_escape(text))
                self.assertFalse(ob.user_wants_onboarding_pause(text))

    def test_short_commands_still_work(self):
        for text in COMMANDS:
            with self.subTest(text=text):
                self.assertTrue(
                    main._user_wants_flow_escape(text) or ob.user_wants_onboarding_pause(text)
                )


if __name__ == "__main__":
    unittest.main()
