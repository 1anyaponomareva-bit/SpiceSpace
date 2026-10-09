"""Regression checks for the post-cut chat quality bugs."""

from __future__ import annotations

import ast
import sys
import types
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_pytz = types.ModuleType("pytz")
_pytz.timezone = lambda _name: ZoneInfo("UTC")
_pytz.UTC = ZoneInfo("UTC")
sys.modules.setdefault("pytz", _pytz)

import goal_change as gc  # noqa: E402
from claude_budget import CHAT_HISTORY_LIMIT  # noqa: E402
from prompts import SPICESPACE_CORE_SYSTEM, SPICESPACE_CORE_SYSTEM_RU  # noqa: E402


def _coach_style() -> str:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "COACH_STYLE_INSTRUCTION":
                    return ast.literal_eval(node.value)
    raise AssertionError("COACH_STYLE_INSTRUCTION missing")


def _reengagement_new_goal_source() -> str:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_reengagement_callback":
            return ast.get_source_segment((ROOT / "main.py").read_text(encoding="utf-8"), node) or ""
    raise AssertionError("handle_reengagement_callback missing")


class RegressionQualityTests(unittest.TestCase):
    def test_live_prompt_has_no_stock_interview(self) -> None:
        style = _coach_style()
        self.assertNotIn("Диагностические вопросы", style)
        self.assertNotIn("Чем ты сейчас занимаешься?", style)
        self.assertNotIn("Есть ли уже аудитория", style)

    def test_short_core_remembers_and_has_one_direct_example(self) -> None:
        voice_ru = SPICESPACE_CORE_SYSTEM_RU.split(
            "Когда пользователь просит написать сценарий", 1
        )[0]
        voice_en = SPICESPACE_CORE_SYSTEM.split("REELS", 1)[0]
        self.assertIn("последних реплик", voice_ru)
        self.assertIn("по одному в день", voice_ru)
        self.assertIn("latest replies", voice_en)
        self.assertIn("one a day", voice_en)
        self.assertLess(len(voice_ru), 2600)

    def test_new_constraint_replaces_overlapping_old_fact(self) -> None:
        plan = gc.fact_update_plan(
            ["Хочет работать на заказ"],
            "Не хочет работать на заказ",
        )
        self.assertEqual(plan["action"], "insert")
        self.assertEqual(plan["drop"], ["Хочет работать на заказ"])

    def test_same_fact_is_not_stored_twice(self) -> None:
        plan = gc.fact_update_plan(
            ["Не хочет работать на заказ"],
            "не хочет работать на заказ",
        )
        self.assertEqual(plan["action"], "skip")
        self.assertEqual(plan["drop"], [])

    def test_more_specific_constraint_replaces_shorter_one(self) -> None:
        plan = gc.fact_update_plan(
            ["Не хочет работать на заказ"],
            "Не хочет работать на заказ из-за субъективных клиентов",
        )
        self.assertEqual(plan["action"], "insert")
        self.assertEqual(plan["drop"], ["Не хочет работать на заказ"])

    def test_unrelated_fact_is_kept(self) -> None:
        plan = gc.fact_update_plan(
            ["Живёт во Вьетнаме"],
            "Не хочет работать на заказ",
        )
        self.assertEqual(plan["action"], "insert")
        self.assertEqual(plan["drop"], [])

    def test_mood_is_not_a_durable_fact(self) -> None:
        self.assertFalse(gc.should_store_fact("Баги вгоняют в уныние"))
        self.assertTrue(gc.should_store_fact("Не хочет работать на заказ"))

    def test_explicit_refusal_is_captured_without_a_second_model_call(self) -> None:
        found = gc.constraint_from_message(
            "Много косяков и я в унынии. Я не хочу работать на заказ."
        )
        self.assertEqual(found, "Я не хочу работать на заказ")

    def test_history_stays_at_twelve_and_goal_is_not_repeated(self) -> None:
        self.assertEqual(CHAT_HISTORY_LIMIT, 12)
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        rule = ""
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_current_goal_only_rule":
                rule = ast.get_source_segment((ROOT / "main.py").read_text(encoding="utf-8"), node) or ""
        self.assertNotIn("Текущая цель пользователя:", rule)
        self.assertIn("блоке профиля", rule)
        self.assertIn("блоке фактов", rule)

    def test_new_goal_button_uses_change_flow(self) -> None:
        src = _reengagement_new_goal_source()
        button = src.split('data == "reengagement:new_goal"', 1)[1]
        self.assertIn("start_change_12w", button)
        self.assertNotIn("build_change_goal_dialog_opening", button)
        self.assertNotIn("start_reengagement_goal", button)
        self.assertNotIn("generate_first_pain_question", button)


if __name__ == "__main__":
    unittest.main()
