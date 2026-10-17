"""English profiles get no Russian scaffolding outside the live chat."""

from __future__ import annotations

import ast
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lang_context  # noqa: E402
import summaries  # noqa: E402
from lang_context import has_cyrillic  # noqa: E402
from prompts import onboarding_summary_prompt  # noqa: E402


def _main_functions(names: set[str], namespace: dict) -> dict:
    """Run selected top-level defs from main.py without importing the bot."""
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    missing = names - {node.name for node in nodes}
    if missing:
        raise AssertionError(f"missing in main.py: {sorted(missing)}")
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, "main.py", "exec"), namespace)
    return namespace


class _FakeStore:
    def __init__(self, facts: list[str], personality: dict | None) -> None:
        self._facts = facts
        self._personality = personality

    def load_user_facts(self, _chat_id: int, limit: int = 10) -> list[str]:
        return self._facts[:limit]

    def load_personality(self, _chat_id: int) -> dict | None:
        return self._personality

EN_PROFILE = {
    "language_code": "en",
    "name": "Anna",
    "vision": "Live by the sea",
    "main_goal": "Launch the app",
    "morning_time": "09:00",
    "evening_time": "21:00",
}


class OnboardingSummaryLanguageTests(unittest.TestCase):
    def test_english_prompt_and_system_have_no_cyrillic(self) -> None:
        prompt = onboarding_summary_prompt("en").format(
            name=EN_PROFILE["name"],
            vision=EN_PROFILE["vision"],
            main_goal=EN_PROFILE["main_goal"],
            morning_time=EN_PROFILE["morning_time"],
            evening_time=EN_PROFILE["evening_time"],
        )
        self.assertFalse(has_cyrillic(prompt), prompt)
        self.assertFalse(has_cyrillic(summaries.onboarding_summary_system("en")))
        for field in ('"summary"', '"mood"', '"key_detail"', '"task"'):
            self.assertIn(field, prompt)

    def test_english_fallback_has_no_cyrillic(self) -> None:
        fallback = summaries.onboarding_summary_fallback(EN_PROFILE)
        self.assertFalse(has_cyrillic(fallback["summary"]), fallback)
        self.assertFalse(has_cyrillic(fallback["mood"]), fallback)

    def test_russian_stays_russian(self) -> None:
        ru = {**EN_PROFILE, "language_code": "ru"}
        self.assertTrue(has_cyrillic(onboarding_summary_prompt("ru")))
        self.assertEqual(
            summaries.onboarding_summary_system("ru"),
            "Отвечай только валидным JSON.",
        )
        self.assertEqual(summaries.onboarding_summary_fallback(ru)["mood"], "начало")


PERSONALITY_ROW = {
    "communication_style": "short and direct",
    "motivation_triggers": "visible progress",
    "procrastination_patterns": "",
    "best_time_of_day": "morning",
    "response_to_pressure": None,
    "personal_values": "freedom",
    "strengths": "persistence",
    "blockers": "perfectionism",
    "raw_insights": "likes checklists",
}


class EveningBlocksLanguageTests(unittest.TestCase):
    def _blocks(self, lang: str) -> tuple[str, str]:
        ns = _main_functions(
            {"_facts_block_for_prompt", "_personality_block_for_prompt"},
            {
                "db_store": _FakeStore(["runs every morning", "has a cat"], PERSONALITY_ROW),
                "is_ru": lang_context.is_ru,
                "facts_header": lang_context.facts_header,
                "personality_intro": lang_context.personality_intro,
                "personality_text_lang": lang_context.personality_text,
            },
        )
        return ns["_facts_block_for_prompt"](1, lang), ns["_personality_block_for_prompt"](1, lang)

    def test_english_blocks_have_no_cyrillic(self) -> None:
        facts, personality = self._blocks("en")
        self.assertFalse(has_cyrillic(facts), facts)
        self.assertFalse(has_cyrillic(personality), personality)
        self.assertTrue(facts.startswith("Important facts:\n"))
        self.assertIn("Communication style: short and direct", personality)

    def test_russian_blocks_keep_old_text(self) -> None:
        facts, personality = self._blocks("ru")
        self.assertEqual(
            facts,
            "Важные факты о пользователе:\n— runs every morning\n— has a cat",
        )
        self.assertEqual(
            personality,
            "Профиль личности:\n"
            "Стиль общения: short and direct\n"
            "Что мотивирует: visible progress\n"
            "Лучшее время: morning\n"
            "Ценности: freedom\n"
            "Сильные стороны: persistence\n"
            "Блокеры: perfectionism\n"
            "Наблюдения: likes checklists",
        )


class _FakeSummaries:
    def __init__(self, by_day: dict[date, dict]) -> None:
        self._by_day = by_day

    def get_daily_summary(self, _cid: int, day: date) -> dict | None:
        return self._by_day.get(day)


class WeekRecapFactsLanguageTests(unittest.TestCase):
    START = date(2026, 10, 5)

    def _collect(self, lang: str) -> tuple[str, int, int]:
        by_day = {
            self.START: {
                "summary": "wrote two screens",
                "task": "finish onboarding",
                "key_detail": "client feedback",
                "mood": "tired",
                "task_completed": "true",
            },
            self.START + timedelta(days=1): {
                "task": "record a video",
                "task_completed": "false",
            },
        }
        ns = _main_functions(
            {"_collect_week_recap_facts"},
            {
                "db_store": _FakeSummaries(by_day),
                "is_ru": lang_context.is_ru,
                "timedelta": timedelta,
                "_week_date_range_for_recap": lambda _p, _d: (
                    self.START,
                    self.START + timedelta(days=6),
                ),
            },
        )
        return ns["_collect_week_recap_facts"](1, {"language_code": lang}, 7)

    def test_english_labels_have_no_cyrillic(self) -> None:
        text, completed, with_notes = self._collect("en")
        self.assertFalse(has_cyrillic(text), text)
        self.assertIn("task of the day: done", text)
        self.assertIn("task of the day: not done", text)
        self.assertEqual((completed, with_notes), (1, 2))

    def test_russian_labels_unchanged(self) -> None:
        text, _, _ = self._collect("ru")
        self.assertIn("задача дня: finish onboarding", text)
        self.assertIn("деталь: client feedback", text)
        self.assertIn("настроение: tired", text)
        self.assertIn("задача дня: выполнена", text)
        self.assertIn("задача дня: не выполнена", text)


if __name__ == "__main__":
    unittest.main()
