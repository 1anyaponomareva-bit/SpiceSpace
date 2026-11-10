"""Talk about her state: support mode keeps tasks and goals out of the reply."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import safety  # noqa: E402
from tests.test_lang_paths import _main_functions  # noqa: E402

_src = (ROOT / "main.py").read_text(encoding="utf-8")
_ns: dict = {"safety": safety, "re": __import__("re")}
exec(_src[_src.index("_LOW_STATE_MARKERS = ("):_src.index("def _support_mode_rule")], _ns)
NS = _main_functions({"_support_mode_rule"}, _ns)


def _u(text: str) -> dict:
    return {"role": "user", "parts": [text]}


def _m(text: str) -> dict:
    return {"role": "model", "parts": [text]}


class SupportModeTests(unittest.TestCase):
    def test_state_in_history_keeps_mode(self) -> None:
        hist = [
            _u("Короче ощущение что сил нет вообще никаких"),
            _m("..."),
            _u("Недели 4"),
            _m("..."),
        ]
        self.assertTrue(NS["_in_support_mode"](hist, "Твоя задача сейчас какая?"))

    def test_not_my_life(self) -> None:
        self.assertTrue(NS["_in_support_mode"]([], "Ощущение что живу не свою жизнь"))

    def test_work_chat_is_not_support(self) -> None:
        hist = [_u("Собрать маркетинговую стратегию в клод"), _m("...")]
        self.assertFalse(NS["_in_support_mode"](hist, "18:00"))

    def test_rule_language(self) -> None:
        self.assertIn("твоя задача", NS["_support_mode_rule"]({"language_code": "ru"}))
        self.assertIn("What's your task", NS["_support_mode_rule"]({"language_code": "en"}))


if __name__ == "__main__":
    unittest.main()


_post = {"re": __import__("re")}
exec(_src[_src.index("_SENTENCE_RE = re.compile"):_src.index("def _support_mode_rule")], _post)


class SupportPostProcessTests(unittest.TestCase):
    def test_keeps_first_question(self) -> None:
        text = "Аня, это важно. Как давно ты так ешь? И что вообще получается съесть за день?"
        out = _post["_keep_one_question"](text)
        self.assertEqual(out.count("?"), 1)
        self.assertIn("Как давно ты так ешь?", out)
        self.assertIn("это важно.", out)
        self.assertNotIn("съесть за день", out)

    def test_questions_in_two_paragraphs(self) -> None:
        text = "Понимаю.\n\nТы сейчас дома? Муж и дети спят?"
        out = _post["_keep_one_question"](text)
        self.assertEqual(out, "Понимаю.\n\nТы сейчас дома?")

    def test_fragment_question_dropped(self) -> None:
        text = "Это тяжело.\n\nЧто конкретно даёт это ощущение? Про что-то ещё?"
        self.assertEqual(
            _post["_keep_one_question"](text),
            "Это тяжело.\n\nЧто конкретно даёт это ощущение?",
        )

    def test_single_question_untouched(self) -> None:
        text = "Тогда ложись.\n\nТы поела сегодня?"
        self.assertEqual(_post["_keep_one_question"](text), text)

    def test_strip_name(self) -> None:
        self.assertEqual(
            _post["_strip_leading_name"]("Аня, сейчас почти полночь.", "Аня"),
            "Сейчас почти полночь.",
        )
        self.assertEqual(_post["_strip_leading_name"]("Ань, слышу.", "Аня"), "Слышу.")
        self.assertEqual(_post["_strip_leading_name"]("Слышу тебя, Аня.", "Аня"), "Слышу тебя, Аня.")

    def test_new_trigger_words(self) -> None:
        self.assertTrue(NS["_in_support_mode"]([], "Ничего не хочется, наверное я просто ленивая"))
        self.assertTrue(NS["_in_support_mode"]([], "Сижу плачу"))
