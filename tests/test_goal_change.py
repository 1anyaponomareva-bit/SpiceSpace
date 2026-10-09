"""Goal change stays in the current chat and does not restart onboarding."""

import unittest

from goal_change import (
    ASK_RU,
    GOAL_DIALOG_STEP,
    ask_prompt,
    build_change_12w_state,
    next_step_after_weekly,
    opening_restarts_onboarding,
    patch_touches_unrelated,
    profile_patch_for_new_cycle,
)


class GoalChangeTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "name": "Аня",
            "language_code": "ru",
            "main_goal": "бегать по утрам",
            "weekly_goal": "три пробежки",
            "morning_time": "08:00",
            "evening_time": "21:30",
            "timezone": "Asia/Ho_Chi_Minh",
            "vision": "спокойные утра",
            "has_kids": False,
            "subscription_end": "2026-12-01",
            "is_premium": True,
        }
        self.history = [
            {"role": "user", "parts": ["вчера устала"]},
            {"role": "model", "parts": ["слышу"]},
        ]

    def test_a_change_request_asks_for_the_new_goal(self):
        state, opening = build_change_12w_state(self.profile)
        self.assertEqual(opening, ASK_RU)
        self.assertEqual(opening, ask_prompt("ru"))
        self.assertFalse(opening_restarts_onboarding(opening))
        self.assertNotIn("скорректировать", opening.lower())
        self.assertNotIn("привет", opening.lower())
        self.assertEqual(state["step"], GOAL_DIALOG_STEP)
        self.assertEqual(state["change_mode"], "new_12w")
        self.assertNotEqual(state["change_12w_phase"], "choice")
        self.assertEqual(self.history[0]["parts"][0], "вчера устала")

    def test_b_goal_wording_stays_in_goal_dialog(self):
        state, _opening = build_change_12w_state(self.profile)
        state["goal_turns"].append(
            {"role": "user", "content": "Пробежать полумарафон за 12 недель"}
        )
        self.assertEqual(state["step"], GOAL_DIALOG_STEP)
        self.assertNotIn(state["step"], (1, 3, 7, 8))
        self.assertEqual(state["name"], "Аня")
        self.assertEqual(state["main_goal"], "бегать по утрам")
        self.assertEqual(state["vision"], "")
        self.assertEqual(self.profile["vision"], "спокойные утра")
        self.assertEqual(next_step_after_weekly(state), "finish_12w")
        self.assertNotEqual(next_step_after_weekly(state), "weekly_to_morning")

    def test_c_finished_flow_returns_to_chat(self):
        state, _opening = build_change_12w_state(self.profile)
        flows = {7: state}
        flows.pop(7, None)
        self.assertNotIn(7, flows)
        patch = profile_patch_for_new_cycle(
            {"main_goal": "полумарафон", "weekly_goal": "первый старт", "vision": ""}
        )
        self.assertNotIn("step", patch)
        self.assertFalse(opening_restarts_onboarding(ASK_RU))

    def test_d_active_goal_updates_without_wiping_the_rest(self):
        state, _opening = build_change_12w_state(self.profile)
        state["main_goal"] = "полумарафон"
        state["weekly_goal"] = "первый старт"
        patch = profile_patch_for_new_cycle(state)
        self.assertFalse(patch_touches_unrelated(patch))
        saved = dict(self.profile)
        saved.update(patch)
        self.assertEqual(saved["main_goal"], "полумарафон")
        self.assertEqual(saved["weekly_goal"], "первый старт")
        self.assertEqual(saved["current_week"], 1)
        self.assertEqual(saved["name"], "Аня")
        self.assertEqual(saved["morning_time"], "08:00")
        self.assertEqual(saved["evening_time"], "21:30")
        self.assertEqual(saved["vision"], "спокойные утра")
        self.assertEqual(saved["subscription_end"], "2026-12-01")
        self.assertTrue(saved["is_premium"])
        self.assertEqual(self.history[0]["parts"][0], "вчера устала")

    def test_first_meeting_greeting_is_rejected(self):
        bad = "Привет, Аня! Скажи, что сейчас больше всего раздражает или давит в твоей жизни?"
        self.assertTrue(opening_restarts_onboarding(bad))
        choice = (
            "Хочешь начать новый 12-недельный цикл с новой целью — "
            "или просто скорректировать текущую?"
        )
        self.assertTrue(opening_restarts_onboarding(choice))


if __name__ == "__main__":
    unittest.main()
