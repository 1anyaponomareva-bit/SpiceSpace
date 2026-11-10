"""A day without a task is never shown as "not done"."""

from __future__ import annotations

import unittest

import db
from tests.test_lang_paths import _main_functions


class _Store:
    normalize_task_completed = staticmethod(db.normalize_task_completed)


class NoTaskDayTests(unittest.TestCase):
    def setUp(self) -> None:
        ns = _main_functions(
            {"_effective_task_completed", "_infer_task_completed_from_text", "_detect_evening_task_completed"},
            {"db_store": _Store(), "re": __import__("re")},
        )
        self.eff = ns["_effective_task_completed"]

    def test_no_task_false_is_hidden(self) -> None:
        self.assertIsNone(self.eff({"task_completed": "false", "summary": "сказала нет"}))
        self.assertIsNone(self.eff({"completed": False}))
        self.assertIsNone(self.eff({"summary": "не успела ничего"}))

    def test_no_task_done_still_counts(self) -> None:
        self.assertEqual(self.eff({"task_completed": "true"}), "true")
        self.assertEqual(self.eff({"task_completed": "partial"}), "partial")

    def test_with_task_unchanged(self) -> None:
        task = {"task": "дописать оплату в боте"}
        self.assertEqual(self.eff({**task, "task_completed": "false"}), "false")
        self.assertEqual(self.eff({**task, "completed": True}), "true")
        self.assertEqual(self.eff({**task, "summary": "не вышло"}), "false")


if __name__ == "__main__":
    unittest.main()
