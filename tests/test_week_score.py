"""A fully done week (6 task days + recap day) shows 100 %."""

from __future__ import annotations

import unittest

from tests.test_lang_paths import _main_functions, ROOT


class WeekScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        ns = {"WEEK_CYCLE_LEN": 7}
        exec("DAY_SCORE_FULL = 100.0 / (WEEK_CYCLE_LEN - 1)\nDAY_SCORE_PARTIAL = DAY_SCORE_FULL / 2", ns)
        self.points = _main_functions({"_day_score_points"}, ns)["_day_score_points"]
        src = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("DAY_SCORE_FULL = 100.0 / (WEEK_CYCLE_LEN - 1)", src)
        js = (ROOT / "webapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const full = 100 / 6;", js)

    def _week(self, days: list[str]) -> int:
        total = sum(self.points(tc, is_recap_day=(i == 6)) for i, tc in enumerate(days))
        return min(100, int(round(total)))

    def test_full_week_is_100(self) -> None:
        self.assertEqual(self._week(["true"] * 6 + [None]), 100)

    def test_partial_and_missed(self) -> None:
        self.assertEqual(self._week(["true", "true", "true", "partial", "false", None, None]), 58)


if __name__ == "__main__":
    unittest.main()
