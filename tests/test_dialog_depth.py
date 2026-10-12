"""Live-chat depth: adaptive length, one Claude call, specialty blocks on topic only."""

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
sys.modules.setdefault("anthropic", types.ModuleType("anthropic"))

from claude_budget import (  # noqa: E402
    CHAT_HISTORY_LIMIT,
    CHAT_OUTPUT_RULE_RU,
    DYNAMIC_MARKER,
    PLAIN_CHAT_CLAUDE_CALLS,
    parse_structured_reply,
    recent_history,
)
from claude_client import estimate_tokens  # noqa: E402
from prompts import (  # noqa: E402
    FITNESS_NUTRITION_INSTRUCTION,
    HOOK_FORMULA_INSTRUCTION,
    REELS_SCRIPT_STRUCTURE,
    build_chat_system,
    specialty_instructions,
)

PROFILE = {
    "language_code": "ru",
    "name": "Аня",
    "main_goal": "запустить свой продукт",
    "weekly_goal": "дописать онбординг",
    "current_week": 3,
}
YESTERDAY = {"summary": "Вчера застряла на текстах", "key_detail": "субъективные клиенты"}
TODAY = {
    "task": "написать три экрана онбординга",
    "task_completed": "false",
    "summary": "Утром села за экраны и бросила на втором",
}


def _coach_style() -> str:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "COACH_STYLE_INSTRUCTION":
                    return ast.literal_eval(node.value)
    raise AssertionError("COACH_STYLE_INSTRUCTION missing")


def _system(user_text: str) -> str:
    extra = specialty_instructions(user_text)
    return build_chat_system(
        PROFILE,
        YESTERDAY,
        TODAY,
        extra=extra,
        coach_style=_coach_style(),
    )


def _static(system: str) -> str:
    return system.split(DYNAMIC_MARKER, 1)[0]


def _dynamic(system: str) -> str:
    return system.split(DYNAMIC_MARKER, 1)[1]


class DialogDepthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.style = _coach_style()
        cls.ordinary = _system("Привет, сделала черновик экрана")
        cls.static = _static(cls.ordinary)

    def test_budget_and_generator_stay_put(self) -> None:
        self.assertEqual(PLAIN_CHAT_CLAUDE_CALLS, 1)
        self.assertEqual(CHAT_HISTORY_LIMIT, 12)
        client = (ROOT / "claude_client.py").read_text(encoding="utf-8")
        self.assertIn("max_tokens: int = 1024", client)
        self.assertIn('return "claude-sonnet-4-5"', client)

    def test_length_caps_are_gone_and_depth_rules_stay_in_the_stable_prefix(self) -> None:
        self.assertNotIn("2-3 предложения", CHAT_OUTPUT_RULE_RU)
        self.assertNotIn("2-3 sentences", self.static)
        self.assertNotIn("1–4 предложения", self.style)
        self.assertNotIn("один шаг, не десять", self.style)
        self.assertNotIn("Спроси как она", self.style)
        self.assertNotIn("НЕ давай задачи", self.style)
        self.assertIn("Длина по ситуации", self.static)
        self.assertIn("Минимальной длины нет", self.static)
        self.assertIn("не предлагай продуктивность", self.static)
        self.assertIn("помоги разобраться", self.static)
        self.assertIn("как наблюдение, не как факт", self.static)
        self.assertIn("дежурного сочувствия", self.static)
        self.assertNotIn("ТРИГГЕРНЫЙ ХУК", self.static)
        self.assertNotIn("ПИТАНИЕ:", self.static)

    def test_context_priority_without_a_second_call(self) -> None:
        dynamic = _dynamic(self.ordinary)
        self.assertIn("Цель на 12 недель: запустить свой продукт", dynamic)
        self.assertIn("Цель недели: дописать онбординг", dynamic)
        self.assertIn("Задача сегодня: написать три экрана онбординга (не сделана)", dynamic)
        self.assertIn("Сегодня: Утром села за экраны", dynamic)
        self.assertIn("Вчера застряла на текстах", dynamic)
        self.assertNotIn("ТРИГГЕРНЫЙ ХУК", dynamic)
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("load_user_facts, chat_id, 3", source)
        self.assertIn("старый фон", source)
        self.assertNotIn("update_personality_profile(", source)

    def test_stable_prefix_is_shared_and_still_cacheable(self) -> None:
        reels = _system("Напиши сценарий рилса про онбординг")
        self.assertEqual(_static(reels), self.static)
        specialty = "\n\n".join(
            (
                REELS_SCRIPT_STRUCTURE,
                HOOK_FORMULA_INSTRUCTION,
                FITNESS_NUTRITION_INSTRUCTION,
            )
        )
        before = estimate_tokens(self.static + "\n\n" + specialty)
        after = estimate_tokens(self.static)
        self.assertGreaterEqual(after, 1024)
        self.assertLess(after, before)
        self.assertGreater(before - after, 1000)

    def test_fifteen_situations(self) -> None:
        cases = [
            ("simple_result", "Сделала черновик первого экрана", False, False, False),
            ("simple_question", "Во сколько лучше писать тексты?", False, False, False),
            (
                "complex_problem",
                "Клиенты пропадают после созвона, и я не понимаю, где теряю их",
                False,
                False,
                False,
            ),
            ("tired", "Я устала и сегодня вообще ничего не хочу делать", False, False, False),
            (
                "third_postpone",
                "Опять не села за онбординг, третий день одно и то же",
                False,
                False,
                False,
            ),
            (
                "no_progress",
                "Не понимаю, почему продукт не двигается",
                False,
                False,
                False,
            ),
            ("reels", "Напиши сценарий рилса про онбординг", True, False, False),
            ("food", "Помоги с питанием, вес 60, хочу похудеть", False, False, True),
            ("change_goal", "Хочу поменять цель на 12 недель", False, False, False),
            ("task_done", "Я сделала задачу на сегодня, три экрана готовы", False, False, False),
            ("task_musing", "Может, завтра допишу экраны, пока просто думаю", False, False, False),
            ("back_after_days", "Пропала на четыре дня, только вернулась", False, False, False),
            ("short_burst", "ок", False, False, False),
            ("concrete_plan", "Составь конкретный план на эту неделю по онбордингу", False, False, False),
            ("just_talk", "Просто хочу поговорить, без задачи", False, False, False),
        ]
        self.assertEqual(len(cases), 15)
        for name, text, reels, hook, food in cases:
            with self.subTest(name=name):
                system = _system(text)
                self.assertEqual(_static(system), self.static)
                dynamic = _dynamic(system)
                self.assertIn("Цель на 12 недель", dynamic)
                self.assertEqual("ТРИГГЕРНЫЙ ХУК" in dynamic, reels)
                self.assertEqual("ФОРМУЛА ХУКА" in dynamic, hook)
                self.assertEqual("ПИТАНИЕ:" in dynamic, food)
                self.assertIn("только если пользователь явно сказал", self.static)
                self.assertIn("new_goal остаётся null", self.static)
                self.assertIn("Вопрос необязателен", self.static)

    def test_reels_and_food_prompts_finish_the_work(self) -> None:
        reels = specialty_instructions("сценарий для рилса, тема уже есть")
        self.assertIn("сразу пиши готовый сценарий", reels)
        food = specialty_instructions("составь меню на похудение")
        self.assertIn("сразу составляй меню", food)
        hook = specialty_instructions("напиши хук для видео")
        self.assertIn("сразу пиши хуки", hook)
        self.assertEqual(specialty_instructions("просто хочу поговорить"), "")

    def test_state_does_not_move_without_an_explicit_model_field(self) -> None:
        mused = parse_structured_reply(
            '{"reply":"Похоже, экраны снова ждут.","state_updates":'
            '{"task_completed":null,"new_task":null,"new_goal":null,'
            '"weekly_goal_update":null,"important_fact":null}}'
        )
        self.assertIsNone(mused["state_updates"]["task_completed"])
        self.assertIsNone(mused["state_updates"]["new_goal"])
        done = parse_structured_reply(
            '{"reply":"Три экрана готовы.","state_updates":'
            '{"task_completed":true,"new_task":null,"new_goal":null,'
            '"weekly_goal_update":null,"important_fact":null}}'
        )
        self.assertIs(done["state_updates"]["task_completed"], True)

    def test_short_burst_still_fits_in_twelve_turns(self) -> None:
        hist = [{"role": "user", "parts": [str(i)]} for i in range(20)]
        self.assertEqual(len(recent_history(hist)), 12)


if __name__ == "__main__":
    unittest.main()
