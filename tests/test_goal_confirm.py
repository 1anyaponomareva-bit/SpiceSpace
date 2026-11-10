"""Goal confirm: "да" with an add-on, weekly-only change, no refusal saved as a goal."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import onboarding_flow as ob  # noqa: E402


class ConfirmYesTests(unittest.TestCase):
    def test_yes_with_short_addon(self) -> None:
        self.assertTrue(ob._is_goal_confirm_yes("Да и смайлик ракеты"))
        self.assertTrue(ob._is_goal_confirm_yes("да, верно"))
        self.assertTrue(ob._is_goal_confirm_yes("Да"))

    def test_corrections_are_not_yes(self) -> None:
        self.assertFalse(ob._is_goal_confirm_yes("да, но поменяй срок"))
        self.assertFalse(ob._is_goal_confirm_yes("да давай другое"))
        self.assertFalse(ob._is_goal_confirm_yes("давай подумаем"))
        self.assertFalse(ob._is_goal_confirm_yes("да измени формулировку"))

    def test_requested_emoji(self) -> None:
        self.assertEqual(ob._requested_emoji("Да и смайлик ракеты"), "\U0001F680")
        self.assertEqual(ob._requested_emoji("да"), "")
        self.assertEqual(ob._requested_emoji("ракета"), "")


class WeeklyOnlyTests(unittest.TestCase):
    def test_keep_goal_change_week(self) -> None:
        self.assertTrue(ob.wants_weekly_only_change(
            "У нас же есть цель ее и оставим скорректируем только цель на неделю"
        ))
        self.assertTrue(ob.wants_weekly_only_change("keep the goal, change this week"))

    def test_new_goal_is_not_weekly_only(self) -> None:
        self.assertFalse(ob.wants_weekly_only_change("Я решила поменять цель"))
        self.assertFalse(ob.wants_weekly_only_change("на этой неделе оформить соцсети"))


class PolishMetaTests(unittest.TestCase):
    def test_refusal_detected(self) -> None:
        self.assertTrue(ob._polish_looks_like_meta(
            "Формулировка «Да и смайлик ракеты» не содержит самой цели, поэтому шлифовать "
            "здесь нечего. Напиши, чего ты хочешь достичь за 12 недель."
        ))

    def test_real_goal_passes(self) -> None:
        self.assertFalse(ob._polish_looks_like_meta(
            "К 12-й неделе продать своего Telegram-бота и получить первых платящих клиентов."
        ))
        self.assertFalse(ob._polish_looks_like_meta("Оформить соцсети для бота"))


class SameGoalTests(unittest.TestCase):
    def test_emoji_and_punctuation_ignored(self) -> None:
        a = "К 12-й неделе продать своего Telegram-бота и получить первых платящих клиентов."
        self.assertTrue(ob.same_goal_text(a, a.rstrip(".") + " \U0001F680"))

    def test_different_goal(self) -> None:
        self.assertFalse(ob.same_goal_text("Продать бота", "Оформить соцсети"))
        self.assertFalse(ob.same_goal_text("", ""))


if __name__ == "__main__":
    unittest.main()
