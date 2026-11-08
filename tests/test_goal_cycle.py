"""A new 12-week goal set in chat starts a new cycle and is confirmed with a card."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import re  # noqa: E402

_NAMES = {"_goal_card", "_new_goal_cycle_fields", "_same_saved_goal", "_apply_chat_state_updates"}


class FakeDB:
    def __init__(self):
        self.updates: list[dict] = []
        self.fields: list[tuple[str, object]] = []

    def update_profile(self, chat_id, fields):
        self.updates.append(dict(fields))
        return None  # force the in-memory branch

    def update_profile_field(self, chat_id, field, value):
        self.fields.append((field, value))
        return None

    def _profile_local_today(self, profile):
        return "2026-11-08"

    def save_user_fact(self, *a):
        pass


def _load(db: FakeDB) -> dict:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    nodes = [
        n for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name in _NAMES)
        or (isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_CYCLE_DAY_FLAG" for t in n.targets))
    ]
    ns: dict = {
        "re": re,
        "db_store": db,
        "user_profiles": {},
        "log": SimpleNamespace(info=lambda *a, **k: None),
        "can_lock_as_goal": lambda g: True,
        "should_store_fact": lambda f: False,
        "constraint_from_message": lambda t: "",
        "is_refusal_constraint": lambda f: False,
        "_apply_task_outcome_to_profile": lambda *a, **k: None,
        "_is_future_task": lambda t: False,
        "save_daily_task": lambda *a, **k: None,
        "_save_tomorrows_task": lambda *a, **k: None,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "main.py", "exec"), ns)
    return ns


class GoalCycleTests(unittest.TestCase):
    def test_new_goal_resets_cycle_to_week_one_and_returns_card(self):
        db = FakeDB()
        ns = _load(db)
        profile = {"main_goal": "Канал на YouTube", "current_week": 12, "cycle_start_date": "2026-05-28"}
        card = ns["_apply_chat_state_updates"](
            1, profile, "да", {"new_goal": "Набрать 15–20 тестовых пользователей Спейс"}
        )
        saved = db.updates[0]
        self.assertEqual(saved["main_goal"], "Набрать 15–20 тестовых пользователей Спейс")
        self.assertEqual(saved["current_week"], 1)
        self.assertEqual(saved["cycle_start_date"], "2026-11-08")
        self.assertEqual(saved["cycle_flags"], {})
        self.assertEqual(profile["current_week"], 1)
        self.assertIn("✅ Цель на 12 недель зафиксирована", card)
        self.assertIn("неделя 1 из 12", card)

    def test_new_cycle_keeps_date_flags_and_drops_day_flags(self):
        ns = _load(FakeDB())
        profile = {"cycle_flags": {
            "weekly_sent_day_13": True,
            "new_week_dialog_day_160": True,
            "sub_expired_2026-07-20": True,
            "trial_sub_msg3_2026-06-12": True,
            "weekly_sent_2026-11-04": True,
        }}
        fields = ns["_new_goal_cycle_fields"](profile)
        self.assertEqual(
            set(fields["cycle_flags"]),
            {"sub_expired_2026-07-20", "trial_sub_msg3_2026-06-12", "weekly_sent_2026-11-04"},
        )

    def test_same_goal_keeps_cycle_and_no_card(self):
        db = FakeDB()
        ns = _load(db)
        profile = {"main_goal": "Набрать 15 пользователей", "current_week": 5}
        card = ns["_apply_chat_state_updates"](1, profile, "ок", {"new_goal": "набрать 15 пользователей"})
        self.assertEqual(db.updates, [])
        self.assertEqual(card, "")
        self.assertEqual(profile["current_week"], 5)

    def test_weekly_goal_gets_card_only_when_changed(self):
        db = FakeDB()
        ns = _load(db)
        profile = {"main_goal": "Цель", "weekly_goal": "Записать 3 видео"}
        self.assertEqual(
            ns["_apply_chat_state_updates"](1, profile, "", {"weekly_goal_update": "записать 3 видео"}), ""
        )
        card = ns["_apply_chat_state_updates"](1, profile, "", {"weekly_goal_update": "Прописать воронку"})
        self.assertIn("📌 Цель на эту неделю:\nПрописать воронку", card)

    def test_english_card(self):
        ns = _load(FakeDB())
        card = ns["_goal_card"]({"language_code": "en"}, main_goal="Get 20 users")
        self.assertIn("week 1 of 12", card)


if __name__ == "__main__":
    unittest.main()
