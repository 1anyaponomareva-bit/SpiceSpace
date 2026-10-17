"""Natural-language reminders in Russian and English, handled without Claude."""

from __future__ import annotations

import __future__
import ast
import asyncio
import re
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot_ui  # noqa: E402

TZ = "Europe/London"
TODAY = date(2026, 10, 17)
TOMORROW = TODAY + timedelta(days=1)

_NAMES = {
    "_WEEKDAY_RU",
    "_RU_DAYPART_HOURS",
    "_looks_like_reminder_command",
    "_to_24h",
    "_reminder_time_match",
    "_shift_calendar_day",
    "_weekday_mentioned",
    "_has_weekly_repeat_intent",
    "_parse_natural_reminder",
    "_looks_like_reminder_capability_question",
    "_reminder_capability_reply",
    "_reminder_created_message",
    "_try_handle_natural_reminder",
}


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 17, 12, 0, tzinfo=tz)


def _load(**overrides) -> dict:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    nodes: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _NAMES:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in _NAMES for t in node.targets
        ):
            nodes.append(node)
    ns: dict = {
        "re": re,
        "date": date,
        "datetime": _FixedDatetime,
        "timedelta": timedelta,
        "ui_text": bot_ui.ui_text,
        "ui_lang": bot_ui.ui_lang,
        "_profile_timezone_name": lambda _p: TZ,
        "_zone_or_default": ZoneInfo,
        "_profile_has_daily_time": lambda _p: False,
        "log": MagicMock(),
    }
    ns.update(overrides)
    code = compile(
        ast.Module(body=nodes, type_ignores=[]),
        "main.py",
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )
    exec(code, ns)
    missing = _NAMES - ns.keys()
    if missing:
        raise AssertionError(f"missing in main.py: {sorted(missing)}")
    return ns


class ParseReminderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ns = _load()

    def _parse(self, text: str, lang: str) -> dict:
        out = self.ns["_parse_natural_reminder"](text, {"language_code": lang})
        self.assertIsNotNone(out, text)
        return out

    def test_russian_phrases(self) -> None:
        cases = {
            "напомни завтра в 18:00 позвонить маме": (TOMORROW, "18:00", "позвонить маме"),
            "напомни завтра в 6 вечера позвонить маме": (TOMORROW, "18:00", "позвонить маме"),
            "напомни в 8 утра выпить таблетки": (TODAY, "08:00", "выпить таблетки"),
            "напомни завтра в 7 часов вечера купить хлеб": (TOMORROW, "19:00", "купить хлеб"),
            "напомни позвонить маме завтра в 18:00": (TOMORROW, "18:00", "позвонить маме"),
            "напомни в 18:00 продукты купить": (TODAY, "18:00", "продукты купить"),
        }
        for text, (day, hhmm, title) in cases.items():
            with self.subTest(text=text):
                out = self._parse(text, "ru")
                self.assertEqual(out["date"], day.isoformat())
                self.assertEqual(out["time"], hhmm)
                self.assertEqual(out["title"], title)
                self.assertEqual(out["timezone"], TZ)
                self.assertNotIn("_need_title", out)

    def test_english_phrases(self) -> None:
        cases = {
            "Remind me to call mom tomorrow at 6pm": (TOMORROW, "18:00", "call mom"),
            "remind me tomorrow at 6pm to call mom": (TOMORROW, "18:00", "call mom"),
            "Remind me today at 6:30pm to send the invoice": (TODAY, "18:30", "send the invoice"),
            "remind me at 6 pm to stretch": (TODAY, "18:00", "stretch"),
            "Can you remind me tomorrow at 18:00 to buy tomatoes?": (
                TOMORROW, "18:00", "buy tomatoes",
            ),
            "remind me at 7am to drink water": (TODAY, "07:00", "drink water"),
            "remind me at 12am to sleep": (TODAY, "00:00", "sleep"),
        }
        for text, (day, hhmm, title) in cases.items():
            with self.subTest(text=text):
                out = self._parse(text, "en")
                self.assertEqual(out["date"], day.isoformat())
                self.assertEqual(out["time"], hhmm)
                self.assertEqual(out["title"], title)

    def test_no_time_is_not_parsed(self) -> None:
        for text in ("remind me to call mom", "напомни позвонить маме", "set a reminder"):
            with self.subTest(text=text):
                self.assertTrue(self.ns["_looks_like_reminder_command"](text))
                self.assertIsNone(
                    self.ns["_parse_natural_reminder"](text, {"language_code": "en"})
                )

    def test_ordinary_text_is_not_a_reminder(self) -> None:
        for text in ("I did the task!", "What time is it?", "я сделала задачу"):
            with self.subTest(text=text):
                self.assertFalse(self.ns["_looks_like_reminder_command"](text))


class HandleReminderTests(unittest.TestCase):
    def _run(self, text: str, lang: str) -> tuple[bool, list[str], list[dict]]:
        sent: list[str] = []
        created: list[dict] = []

        async def bot_reply(_message, msg, **_kw):
            sent.append(msg)

        def create_task(_cid, _prof, payload):
            task = dict(payload)
            created.append(task)
            return task

        def claude_generate(*_a, **_kw):
            raise AssertionError("Claude must not be called for reminders")

        ns = _load(
            _bot_reply=bot_reply,
            _append_history_turn=lambda *_a: None,
            _create_task_from_payload=create_task,
            pending_natural_reminder={},
            claude_generate=claude_generate,
        )
        handled = asyncio.run(
            ns["_try_handle_natural_reminder"](1, {"language_code": lang}, text, object())
        )
        return handled, sent, created

    def test_english_without_time_gets_fixed_help(self) -> None:
        handled, sent, created = self._run("remind me to call mom", "en")
        self.assertTrue(handled)
        self.assertEqual(created, [])
        self.assertEqual(sent, [bot_ui.ui_text("reminder_how", "en")])
        self.assertIn("remind me tomorrow at 6pm to call mom", sent[0])
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", sent[0]))

    def test_russian_without_time_gets_fixed_help(self) -> None:
        handled, sent, created = self._run("напомни позвонить маме", "ru")
        self.assertTrue(handled)
        self.assertEqual(created, [])
        self.assertIn("напомни завтра в 18:00 позвонить маме", sent[0])

    def test_created_confirmation_follows_language(self) -> None:
        handled, sent, created = self._run("Remind me to call mom tomorrow at 6pm", "en")
        self.assertTrue(handled)
        self.assertEqual(created[0]["time"], "18:00")
        self.assertIsNone(re.search(r"[А-Яа-яЁё]", sent[0]), sent[0])
        self.assertIn("call mom", sent[0])

        handled, sent, _ = self._run("напомни завтра в 18:00 позвонить маме", "ru")
        self.assertTrue(handled)
        self.assertEqual(sent[0], "Окей ✨ Напомню про «позвонить маме» в 18:00.")

    def test_capability_question_is_left_to_the_capability_reply(self) -> None:
        handled, sent, _ = self._run("can you remind me?", "en")
        self.assertFalse(handled)
        self.assertEqual(sent, [])


class CorePromptReminderRuleTests(unittest.TestCase):
    def test_old_confirm_rule_is_gone(self) -> None:
        source = (ROOT / "prompts.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        values: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in (
                        "SPICESPACE_CORE_SYSTEM",
                        "SPICESPACE_CORE_SYSTEM_RU",
                    ):
                        values[target.id] = ast.literal_eval(node.value)
        self.assertEqual(len(values), 2)
        self.assertNotIn("confirm it in one line", values["SPICESPACE_CORE_SYSTEM"])
        self.assertNotIn("подтверди одной строкой", values["SPICESPACE_CORE_SYSTEM_RU"])
        self.assertIn("Never promise a reminder yourself", values["SPICESPACE_CORE_SYSTEM"])
        self.assertIn("Никогда не обещай напоминание", values["SPICESPACE_CORE_SYSTEM_RU"])


if __name__ == "__main__":
    unittest.main()
