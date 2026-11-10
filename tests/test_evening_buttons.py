"""Evening message ends with the task question; a button tap gets a short reply."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import prompts  # noqa: E402
from tests.test_lang_paths import _main_functions  # noqa: E402

NS = _main_functions({"_task_outcome_reply_rule"}, {})


class EveningButtonsTests(unittest.TestCase):
    def test_prompt_has_no_tomorrow_question(self) -> None:
        ru = prompts.EVENING_MESSAGE_PROMPT_RU
        self.assertNotIn("Что конкретно сделаешь завтра", ru)
        self.assertNotIn("поставим задачу на завтра", ru)
        self.assertIn("ЕДИНСТВЕННЫЙ вопрос", ru)
        en = prompts.EVENING_MESSAGE_PROMPT
        self.assertNotIn("What specifically will you do tomorrow", en)
        self.assertIn("ONLY question", en)

    def test_button_rule_names_task(self) -> None:
        rule = NS["_task_outcome_reply_rule"](
            {"language_code": "ru"}, "partial", "Собрать маркетинговую стратегию в Claude"
        )
        self.assertIn("частично", rule)
        self.assertIn("Собрать маркетинговую стратегию", rule)
        self.assertIn("Не задавай вопросов", rule)
        en = NS["_task_outcome_reply_rule"]({"language_code": "en"}, "true", "")
        self.assertIn("Ask no questions", en)


if __name__ == "__main__":
    unittest.main()
