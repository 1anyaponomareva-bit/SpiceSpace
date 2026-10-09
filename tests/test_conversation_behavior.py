"""Listening rules: feelings are not goals, limits survive a goal change."""

import ast
import sys
import types
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_pytz = types.ModuleType("pytz")
_pytz.timezone = lambda _name: ZoneInfo("UTC")
_pytz.UTC = ZoneInfo("UTC")
sys.modules.setdefault("pytz", _pytz)

from claude_budget import CHAT_OUTPUT_RULE_RU, parse_structured_reply
from goal_change import (
    build_change_12w_state,
    can_lock_as_goal,
    next_dialog_move,
)
from prompts import (
    MORNING_MESSAGE_PROMPT_RU,
    WEEKLY_RECAP_DIALOG_SYSTEM,
    WEEKLY_TACTICS_DIALOG_SYSTEM,
    build_chat_system,
)


def _assigned_string(filename: str, name: str) -> str:
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {filename}")


SADNESS = "Я вижу много багов в своём боте, и это вгоняет меня в уныние"
REFUSAL = "Я не хочу работать на заказ"
CONFUSION = "Не понимаю твоего вопроса"


class ConversationBehaviorTests(unittest.TestCase):
    def test_sadness_is_not_a_goal(self):
        self.assertFalse(can_lock_as_goal(SADNESS))
        self.assertEqual(next_dialog_move(SADNESS), "listen")

    def test_refusal_stays_a_constraint_during_goal_change(self):
        profile = {
            "name": "Аня",
            "language_code": "ru",
            "main_goal": "свой продукт для людей",
        }
        history = [
            {"role": "user", "parts": [REFUSAL + " из-за субъективных клиентов"]},
            {"role": "model", "parts": ["слышу"]},
        ]
        state, _opening = build_change_12w_state(profile, history)
        self.assertIn("не хочу работать на заказ", state["prior_dialog"])
        self.assertEqual(state["main_goal"], "свой продукт для людей")
        self.assertNotIn("не хочу", state["main_goal"])
        self.assertFalse(can_lock_as_goal(REFUSAL))

    def test_confusion_does_not_start_a_questionnaire(self):
        self.assertEqual(next_dialog_move(CONFUSION), "repair_question")
        self.assertFalse(can_lock_as_goal(CONFUSION))
        goal = _assigned_string("prompts.py", "GOAL_DIALOG_SYSTEM")
        why = _assigned_string("onboarding_flow.py", "WHY_DIG_SYSTEM")
        self.assertIn("Новую анкету не начинай", goal)
        self.assertIn("Новую анкету не начинай", why)
        self.assertNotIn("Задавай уточняющие вопросы 'зачем тебе это?'", why)
        self.assertNotIn("5 платящих клиентов", goal)

    def test_chat_may_answer_without_a_question(self):
        rule = _assigned_string("main.py", "CHAT_ONE_QUESTION_RULE")
        core = _assigned_string("prompts.py", "SPICESPACE_CORE_SYSTEM_RU")
        self.assertIn("Ноль вопросов — нормальный вариант", rule)
        self.assertIn("Вопрос необязателен", core)
        self.assertIn("ответь без вопроса", core)

    def test_live_prompt_contains_coach_style_and_listening_rules(self):
        style = _assigned_string("main.py", "COACH_STYLE_INSTRUCTION")
        rule = _assigned_string("main.py", "CHAT_ONE_QUESTION_RULE")
        system = build_chat_system(
            {"language_code": "ru", "name": "Аня", "main_goal": "свой продукт"},
            None,
            extra=rule,
            coach_style=style,
        )
        self.assertIn("РЕЖИМ \"ПОДДЕРЖКА\"", system)
        self.assertIn("Вопрос необязателен", system)
        self.assertIn("Ноль вопросов — нормальный вариант", system)
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("coach_style=COACH_STYLE_INSTRUCTION", source)

    def test_structured_scenarios_keep_their_contracts(self):
        self.assertIn('"weekly_goal"', WEEKLY_TACTICS_DIALOG_SYSTEM)
        self.assertIn('"ready"', WEEKLY_TACTICS_DIALOG_SYSTEM)
        self.assertIn('"ready"', WEEKLY_RECAP_DIALOG_SYSTEM)
        self.assertIn("Если вчера вечером пользователь сказал", MORNING_MESSAGE_PROMPT_RU)

    def test_missing_goal_stays_null(self):
        raw = (
            '{"reply":"Слышу, это выматывает.",'
            '"state_updates":{"task_completed":null,"new_task":null,'
            '"new_goal":null,"weekly_goal_update":null,"important_fact":null}}'
        )
        parsed = parse_structured_reply(raw)
        self.assertIsNone(parsed["state_updates"]["new_goal"])
        self.assertIsNone(parsed["state_updates"]["new_task"])
        self.assertIn("new_goal остаётся null", CHAT_OUTPUT_RULE_RU)
        self.assertFalse(can_lock_as_goal(SADNESS))


if __name__ == "__main__":
    unittest.main()
