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
_ns: dict = {"safety": safety}
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
