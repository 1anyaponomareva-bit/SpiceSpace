"""Reminder ask: wording fits the hour; 'what time is it' is answered."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot_ui import ui_text  # noqa: E402
from tests.test_lang_paths import _main_functions  # noqa: E402

NS = _main_functions(
    {"_asks_current_time", "_suggest_reminder_time", "_reminder_ask_text"},
    {"datetime": datetime, "ui_text": ui_text, "_ASKS_TIME_MARKERS": (
        "сколько сейчас врем", "сколько времени", "который час", "котор час",
        "сколько у меня врем", "what time", "time is it",
    )},
)
RU = {"language_code": "ru"}


class ReminderTimeTests(unittest.TestCase):
    def test_asks_time(self) -> None:
        self.assertTrue(NS["_asks_current_time"]("Сколько сейчас времени у меня"))
        self.assertTrue(NS["_asks_current_time"]("который час?"))
        self.assertFalse(NS["_asks_current_time"]("18:00"))

    def test_morning_keeps_midday(self) -> None:
        text = NS["_reminder_ask_text"](RU, datetime(2026, 11, 10, 9, 0))
        self.assertIn("днём", text)
        self.assertIn("14:00", text)

    def test_evening_says_later(self) -> None:
        text = NS["_reminder_ask_text"](RU, datetime(2026, 11, 10, 17, 39))
        self.assertNotIn("днём", text)
        self.assertIn("попозже", text)
        self.assertIn("19:00", text)


if __name__ == "__main__":
    unittest.main()
