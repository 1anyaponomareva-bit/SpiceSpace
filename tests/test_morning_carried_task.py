"""Morning: yesterday's open task is asked about; 'ok' confirms only a named task."""

from __future__ import annotations

import asyncio
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_lang_paths import _main_functions  # noqa: E402

TASK = "Собрать маркетинговую стратегию в Claude"


def _ns(base_text: str, carried: str) -> dict:
    async def base(chat_id, profile, model_names, carried=""):
        return base_text

    ns = {
        "re": re,
        "_yesterday_unfinished_task": lambda cid, p: carried,
        "_morning_message_text_base": base,
    }
    return _main_functions(
        {"_task_words", "_mentions_task", "_carried_task_question", "_morning_message_text"}, ns
    )


class MorningCarriedTaskTests(unittest.TestCase):
    def test_question_added_when_task_missing(self) -> None:
        ns = _ns("Аня, доброе утро 💚 Как ты сегодня? Я рядом.", TASK)
        text = asyncio.run(ns["_morning_message_text"](1, {"language_code": "ru"}, []))
        self.assertIn("Всё в силе — берём это на сегодня?", text)
        self.assertIn(TASK, text)

    def test_not_added_when_model_named_it(self) -> None:
        body = "Доброе утро! Вчерашняя маркетинговая стратегия в Claude ещё в силе?"
        ns = _ns(body, TASK)
        self.assertEqual(asyncio.run(ns["_morning_message_text"](1, {"language_code": "ru"}, [])), body)

    def test_no_task_no_question(self) -> None:
        ns = _ns("Доброе утро 💚", "")
        self.assertEqual(asyncio.run(ns["_morning_message_text"](1, {"language_code": "ru"}, [])), "Доброе утро 💚")

    def test_mentions_task(self) -> None:
        ns = _ns("", "")
        self.assertFalse(ns["_mentions_task"](
            "Аня, доброе утро 💚 Если аппетит появится, хорошо бы добавить что-то посытнее.", TASK
        ))
        self.assertTrue(ns["_mentions_task"]("Берём стратегию на сегодня?", TASK))


if __name__ == "__main__":
    unittest.main()
